"""Outbound-only Slack worker. It has no public endpoint or message listeners."""
import argparse
import json
import os
import re
import signal
import threading
from pathlib import Path
from .core import Matcher, Store
from .mail import load_env
from .slack_delivery import ensure_schema, pending_rows, register, resolve, tick
from .slack_format import digest_preview, latest_report, preview_messages

ROOT = Path(__file__).resolve().parents[1]


class ConfigurationError(ValueError):
    """Safe startup diagnostics containing setting names, never credentials."""


def destination(settings):
    team = settings.get('CITYWATCH_SLACK_TEAM_ID', '')
    channel = settings.get('CITYWATCH_SLACK_CHANNEL_ID', '')
    if not re.fullmatch(r'T[A-Z0-9]+', team) or not re.fullmatch(r'[CG][A-Z0-9]+', channel):
        raise ConfigurationError('Set CITYWATCH_SLACK_TEAM_ID and CITYWATCH_SLACK_CHANNEL_ID to Slack IDs')
    return team, channel


def make_client(settings):
    from slack_sdk import WebClient
    team, _ = destination(settings)
    token = settings.get('SLACK_BOT_TOKEN', '')
    if not token.startswith('xoxb-'):
        raise ConfigurationError('Set SLACK_BOT_TOKEN to the installed CityWatch bot token')
    client = WebClient(token=token, timeout=30, retry_handlers=[])
    response = client.auth_test()
    if not response.get('ok') or response.get('team_id') != team or not response.get('bot_id'):
        raise ConfigurationError('The Slack bot token does not belong to the configured workspace')
    return client


def status(store, target, matcher):
    ensure_schema(store.db)
    subscription = store.db.execute('SELECT * FROM slack_targets WHERE target=?', (target,)).fetchone()
    uncertain = [dict(row) for row in store.db.execute(
        "SELECT m.batch_id,m.position,m.status,m.error FROM slack_messages m JOIN slack_batches b ON b.id=m.batch_id "
        "WHERE b.target=? AND m.status IN ('uncertain','in_flight')", (target,))]
    active = [dict(row) for row in store.db.execute(
        "SELECT m.batch_id,m.position,m.status,m.retry_at,m.error FROM slack_messages m JOIN slack_batches b ON b.id=m.batch_id "
        "WHERE b.target=? AND b.status='prepared' AND m.status!='accepted' ORDER BY m.position LIMIT 10", (target,))]
    return {'registered': bool(subscription), 'target': target,
            'next_digest_at': subscription['next_digest_at'] if subscription else None,
            'pending_in_next_window': len(pending_rows(store.db, target, matcher)),
            'needs_review': uncertain, 'next_messages': active}


def main():
    parser = argparse.ArgumentParser(description='Outbound CityWatch Slack digests; preview and check never post.')
    parser.add_argument('command', choices=['run', 'check', 'preview', 'status', 'resolve'])
    parser.add_argument('--state', type=Path, default=Path(os.environ.get('CITYWATCH_STATE', ROOT / 'var')))
    parser.add_argument('--config', type=Path, default=Path(os.environ.get('CITYWATCH_CONFIG', ROOT / 'config/watch.json')))
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--batch')
    parser.add_argument('--position', type=int)
    parser.add_argument('--ts', help='Confirmed Slack message timestamp after manual inspection')
    parser.add_argument('--confirmed-absent', action='store_true', help='Explicitly permit retry only after checking the channel')
    args = parser.parse_args()
    settings = load_env(args.env_file)
    stopped = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.set())
    if args.command == 'run' and settings.get('CITYWATCH_SLACK_ENABLED', 'false').lower() != 'true':
        print('CityWatch Slack delivery is disabled; waiting for configuration.', flush=True)
        while not stopped.wait(30):
            pass
        return 0
    team, channel = destination(settings)
    target = team + ':' + channel
    matcher = Matcher(json.loads(args.config.read_text())['rules'])
    if args.command == 'check':
        make_client(settings)
        print(json.dumps({'status': 'workspace_verified', 'team': team, 'channel': channel,
                          'note': 'No message sent. Invite CityWatch to the configured channel before enabling delivery.'}))
        return 0
    store = Store(args.state)
    try:
        ensure_schema(store.db)
        if args.command == 'status':
            print(json.dumps(status(store, target, matcher), indent=2))
            return 0
        if args.command == 'preview':
            batch = store.db.execute("SELECT id FROM slack_batches WHERE target=? AND status='prepared' ORDER BY created_at LIMIT 1", (target,)).fetchone()
            if batch:
                messages = [json.loads(row['payload']) for row in store.db.execute(
                    'SELECT payload FROM slack_messages WHERE batch_id=? ORDER BY position', (batch['id'],))]
                print(json.dumps(preview_messages(messages), indent=2))
                return 0
            registered = store.db.execute('SELECT 1 FROM slack_targets WHERE target=?', (target,)).fetchone()
            # Before activation, show email-pending evidence for review; this
            # preview neither registers a destination nor acknowledges alerts.
            rows = pending_rows(store.db, target, matcher) if registered else store.pending()
            print(json.dumps(digest_preview(rows, latest_report(store.db)), indent=2))
            return 0
        if args.command == 'resolve':
            if args.batch is None or args.position is None:
                parser.error('resolve requires --batch and --position')
            batch = store.db.execute('SELECT target FROM slack_batches WHERE id=?', (args.batch,)).fetchone()
            if not batch or batch['target'] != target:
                parser.error('The batch does not belong to the configured destination')
            resolve(store, args.batch, args.position, args.ts, args.confirmed_absent)
            print(json.dumps({'status': 'resolved', 'batch_id': args.batch, 'position': args.position}))
            return 0
        client = make_client(settings)
        register(store.db, team, channel)
        interval = max(3600, int(settings.get('CITYWATCH_SLACK_INTERVAL_SECONDS', '86400')))
        print('CityWatch Slack digest worker started; team=%s channel=%s interval=%ds' % (team, channel, interval), flush=True)
        previous = None
        while not stopped.is_set():
            try:
                result = tick(store, target, matcher, client, interval)
            except Exception as error:
                # Never log a token, provider payload, or source excerpt.
                result = {'status': 'worker_error', 'error': type(error).__name__}
            if result != previous:
                print(json.dumps(result), flush=True)
                previous = result
            stopped.wait(1.2 if result['status'] == 'posted' else 30)
    finally:
        store.db.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ConfigurationError as error:
        print(str(error), flush=True)
        raise SystemExit(1)
    except Exception as error:
        # Authentication exceptions may contain the provider response; do not
        # print raw exceptions in hosted logs.
        print('Slack worker could not start: ' + type(error).__name__, flush=True)
        raise SystemExit(1)
