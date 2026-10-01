import argparse
import fcntl
import json
from datetime import datetime
from pathlib import Path
from .collect import Collector
from .core import Matcher, Store
from .fetch import Fetcher, ReplayFetcher
from .parsers import ZONE
from .preview import render

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='New Orleans topic monitor. scan and preview never send email; send explicitly delivers the pending digest.')
    parser.add_argument('command', choices=['scan', 'preview', 'status', 'check-email', 'send'])
    parser.add_argument('--config', type=Path, default=ROOT / 'config/watch.json')
    parser.add_argument('--state', type=Path, default=ROOT / 'var')
    parser.add_argument('--env-file', type=Path, default=ROOT / '.env')
    parser.add_argument('--replay', type=Path, help='Read exact saved evidence from a prior state directory; no network')
    parser.add_argument('--as-of', help='Override local date for reproducible checks (YYYY-MM-DD)')
    parser.add_argument('--sources', help='Comma-separated subset of configured sources')
    parser.add_argument('--baseline', action='store_true', help='Observe without queuing alerts; use only for intentional initialization')
    parser.add_argument('--include-history', action='store_true', help='Queue first observations of historical material too')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    today = datetime.strptime(args.as_of, '%Y-%m-%d').date() if args.as_of else datetime.now(ZONE).date()
    args.state.mkdir(parents=True, exist_ok=True)
    # Prevent two scheduled runs from fetching and queuing the same work concurrently.
    with (args.state / 'run.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.exit(2, 'Another CityWatch process is using this state directory.\n')
        store = Store(args.state)
        coverage = args.state / 'coverage.json'
        if args.command in ('check-email', 'send'):
            from .mail import load_env, send
            try:
                result = send(store, load_env(args.env_file), sandbox=args.command == 'check-email')
            except Exception as error:
                parser.exit(1, str(error) + '\n')
            print(json.dumps(result, indent=2))
            return 0
        if args.command == 'status':
            print(json.dumps({'records': store.db.execute('SELECT count(*) FROM records').fetchone()[0],
                              'pending_alerts': len(store.pending()),
                              'coverage': json.loads(coverage.read_text()) if coverage.exists() else None}, indent=2))
            return 0
        if args.command == 'preview':
            print(render(store, json.loads(coverage.read_text()) if coverage.exists() else None))
            return 0
        sources = args.sources.split(',') if args.sources else config['sources']
        if any(s not in config['sources'] for s in sources):
            parser.error('Unknown or disabled source')
        matcher = Matcher(config['rules'])
        started = datetime.now(ZONE).isoformat()
        with store.db:
            run_id = store.db.execute('INSERT INTO runs(started_at) VALUES(?)', (started,)).lastrowid
        queued = 0
        def observe(record):
            nonlocal queued
            queued += store.observe(record, matcher, today, args.baseline, args.include_history)
        def reconcile(prefix, keys):
            nonlocal queued
            if not args.baseline:
                queued += store.reconcile_items(prefix, keys, matcher, today)
        fetcher = ReplayFetcher(args.replay) if args.replay else Fetcher(args.state, config)
        collector = Collector(fetcher, config, today, observe, reconcile)
        interrupted = False
        try:
            report = collector.run(sources)
        except KeyboardInterrupt:
            interrupted = True
            report = {'records': collector.counts, 'issues': collector.errors + [{'source': 'run', 'url': '', 'error': 'Scan interrupted; coverage incomplete'}],
                      'window': [collector.start.isoformat(), collector.end.isoformat()]}

        report.update(started_at=started, finished_at=datetime.now(ZONE).isoformat(), new_alerts=queued,
                      sources=sources, rule_version=matcher.version, baseline=args.baseline)
        coverage.write_text(json.dumps(report, indent=2))
        with store.db:
            store.db.execute('UPDATE runs SET finished_at=?, report=? WHERE id=?',
                             (report['finished_at'], json.dumps(report), run_id))
        output = render(store, report)
        print(json.dumps({'new_alerts': queued, 'pending_alerts': len(store.pending()),
                          'records': report['records'], 'coverage_issues': len(report['issues']),
                          'preview': str(output / 'digest.html')}, indent=2))
        return 130 if interrupted else (2 if report['issues'] else 0)
