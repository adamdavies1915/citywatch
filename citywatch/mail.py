"""Mailjet delivery with immutable batches and acknowledgement after acceptance."""
import base64
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from .core import digest
from .preview import render


def load_env(path):
    """Read JSON-quoted or plain KEY=value entries without executing shell code."""
    values = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, value = line.split('=', 1)
            values[key.strip()] = json.loads(value) if value.startswith('"') else value.strip("'")
    values.update(os.environ)
    return values


def mailjet(payload, settings):
    key = settings['MAILJET_API_KEY']
    secret = settings['MAILJET_SECRET_KEY']
    auth = base64.b64encode((key + ':' + secret).encode()).decode()
    request = urllib.request.Request('https://api.mailjet.com/v3.1/send', data=json.dumps(payload).encode(),
                                     headers={'Authorization': 'Basic ' + auth, 'Content-Type': 'application/json'})
    # No automatic retry: a timeout could occur after provider acceptance.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError('Unexpected Mailjet redirect; credentials were not forwarded')
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            result = json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError('Mailjet rejected the request (HTTP %d); outbox retained' % error.code) from None
    messages = result.get('Messages', [])
    if len(messages) != 1 or messages[0].get('Status') != 'success':
        codes = [e.get('ErrorCode', 'unknown') for m in messages for e in m.get('Errors', [])]
        raise RuntimeError('Mailjet did not accept the message; error codes: ' + ', '.join(codes))
    return result


def send(store, settings, sandbox=True, transport=mailjet):
    for key in ['MAILJET_API_KEY', 'MAILJET_SECRET_KEY', 'CITYWATCH_FROM_EMAIL', 'CITYWATCH_TO_EMAIL']:
        if not settings.get(key):
            raise ValueError('Missing email setting: ' + key)
    store.db.execute('''CREATE TABLE IF NOT EXISTS deliveries (
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ids TEXT NOT NULL,
        payload TEXT NOT NULL, status TEXT NOT NULL, response TEXT)''')
    store.db.commit()
    # Enforce cadence at delivery, not in the worker loop. Persist attempts so
    # restarts and ambiguous provider timeouts cannot cause hourly email bursts.
    interval = max(0, int(settings.get('CITYWATCH_EMAIL_INTERVAL_SECONDS', '86400')))
    store.db.execute('CREATE TABLE IF NOT EXISTS email_cadence (id INTEGER PRIMARY KEY, attempted_at REAL NOT NULL)')
    if not sandbox:
        with store.db:
            cadence = store.db.execute('SELECT attempted_at FROM email_cadence WHERE id=1').fetchone()
            if cadence is None and store.db.execute("SELECT 1 FROM deliveries WHERE status='accepted' LIMIT 1").fetchone():
                # On upgrading an existing installation, give the inbox a full
                # quiet period instead of sending another digest immediately.
                store.db.execute('INSERT INTO email_cadence VALUES(1,?)', (time.time(),))
                cadence = store.db.execute('SELECT attempted_at FROM email_cadence WHERE id=1').fetchone()
        if cadence and time.time() < cadence['attempted_at'] + interval:
            return {'status': 'digest_deferred', 'next_attempt_at': datetime.fromtimestamp(
                cadence['attempted_at'] + interval, timezone.utc).isoformat()}
    pending = store.db.execute("SELECT * FROM deliveries WHERE status='prepared' ORDER BY created_at LIMIT 1").fetchone()
    if pending:
        payload = json.loads(pending['payload'])
        ids = json.loads(pending['ids'])
        batch_id = pending['id']
        if payload['Messages'][0]['To'][0]['Email'] != settings['CITYWATCH_TO_EMAIL']:
            raise ValueError('An unsent batch exists for another recipient; review it before changing recipients')
    else:
        rows = store.pending()
        if not rows:
            return {'status': 'nothing_to_send'}
        ids = [r['id'] for r in rows]
        coverage = store.directory / 'coverage.json'
        report = json.loads(coverage.read_text()) if coverage.exists() else {'issues': [{'error': 'Coverage unavailable'}]}
        output = render(store, report)
        plain = (output / 'digest.txt').read_text()
        page = (output / 'digest.html').read_text()
        batch_id = digest([ids, plain, settings['CITYWATCH_FROM_EMAIL'], settings['CITYWATCH_TO_EMAIL']])
        payload = {'Messages': [{
            'From': {'Email': settings['CITYWATCH_FROM_EMAIL'], 'Name': settings.get('CITYWATCH_FROM_NAME', 'CityWatch')},
            'To': [{'Email': settings['CITYWATCH_TO_EMAIL']}],
            'Subject': 'CityWatch: New Orleans topic updates' + (' (coverage incomplete)' if report['issues'] else ''),
            'TextPart': plain, 'HTMLPart': page,
            'CustomID': batch_id, 'CustomCampaign': 'citywatch-' + batch_id, 'DeduplicateCampaign': True,
        }]}
        if not sandbox:
            with store.db:
                store.db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,NULL)',
                                 (batch_id, datetime.now(timezone.utc).isoformat(), json.dumps(ids), json.dumps(payload), 'prepared'))
    outgoing = dict(payload, SandboxMode=sandbox)
    if not sandbox:
        with store.db:
            store.db.execute('INSERT INTO email_cadence VALUES(1,?) ON CONFLICT(id) DO UPDATE SET attempted_at=excluded.attempted_at',
                             (time.time(),))
    result = transport(outgoing, settings)
    if not sandbox:
        with store.db:
            store.db.execute("UPDATE deliveries SET status='accepted', response=? WHERE id=?", (json.dumps(result), batch_id))
            store.db.executemany("UPDATE outbox SET status='sent' WHERE id=?", [(identifier,) for identifier in ids])
    return {'status': 'sandbox_validated' if sandbox else 'accepted', 'batch_id': batch_id, 'alert_revisions': len(ids)}
