"""Independent Slack delivery cursors and immutable, resumable message batches.

The legacy outbox status remains email-owned. Slack reads revisions after its
own subscription cursor, regardless of email acknowledgements or refiltering.
"""
import fcntl
import json
import re
import time
import uuid
from dataclasses import replace
from .core import Record, digest
from .slack_format import digest_messages, latest_report

MAX_BATCH_ROWS = 500


def ensure_schema(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS slack_targets (
            target TEXT PRIMARY KEY, team TEXT NOT NULL, channel TEXT NOT NULL,
            cursor INTEGER NOT NULL, next_digest_at REAL NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS slack_batches (
            id TEXT PRIMARY KEY, target TEXT NOT NULL, end_id INTEGER NOT NULL,
            ids TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS slack_messages (
            batch_id TEXT NOT NULL, position INTEGER NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', ts TEXT, retry_at REAL NOT NULL DEFAULT 0,
            error TEXT, PRIMARY KEY(batch_id, position));
        CREATE INDEX IF NOT EXISTS slack_batch_target ON slack_batches(target, status);
    ''')


def register(db, team, channel):
    """Start at the current tail once; restarts never move an existing cursor."""
    ensure_schema(db)
    target = team + ':' + channel
    with db:
        db.execute('INSERT OR IGNORE INTO slack_targets(target,team,channel,cursor) '
                   'VALUES(?,?,?,(SELECT COALESCE(MAX(id),0) FROM outbox))', (target, team, channel))
    return target


def filtered_rows(rows, matcher):
    result = []
    for original in rows:
        row = dict(original)
        record = Record(**json.loads(row['payload']))
        matches = matcher.match(record)
        previous = []
        if row['change'] == 'matching_text_removed':
            for old in json.loads(row['previous_matches']):
                previous.extend(matcher.match(replace(record, key='previous', text=old['excerpt'])))
        if matches or previous:
            row.update(matches=json.dumps(matches), previous_matches=json.dumps(previous))
            result.append(row)
    return result


def pending_rows(db, target, matcher):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='slack_targets'").fetchone():
        return []
    subscription = db.execute('SELECT * FROM slack_targets WHERE target=?', (target,)).fetchone()
    if not subscription:
        return []
    rows = db.execute('SELECT * FROM outbox WHERE id>? ORDER BY id LIMIT ?',
                      (subscription['cursor'], MAX_BATCH_ROWS)).fetchall()
    return filtered_rows(rows, matcher)


def prepare(db, target, matcher, interval, now):
    batch = db.execute("SELECT * FROM slack_batches WHERE target=? AND status='prepared' ORDER BY created_at LIMIT 1", (target,)).fetchone()
    if batch:
        return batch
    subscription = db.execute('SELECT * FROM slack_targets WHERE target=?', (target,)).fetchone()
    if not subscription:
        raise ValueError('Slack destination is not registered')
    if now < subscription['next_digest_at']:
        return None
    latest_run = db.execute('SELECT finished_at FROM runs ORDER BY id DESC LIMIT 1').fetchone()
    if latest_run and not latest_run['finished_at']:
        return None  # Wait for collection to finish before freezing a digest.
    rows = db.execute('SELECT * FROM outbox WHERE id>? ORDER BY id LIMIT ?',
                      (subscription['cursor'], MAX_BATCH_ROWS)).fetchall()
    if not rows:
        return None
    end_id = rows[-1]['id']
    selected = filtered_rows(rows, matcher)
    if not selected:
        with db:
            db.execute('UPDATE slack_targets SET cursor=? WHERE target=?', (end_id, target))
        return None
    messages = digest_messages(selected, latest_report(db))
    ids = [r['id'] for r in selected]
    batch_id = digest([target, end_id, ids, messages])
    with db:
        db.execute('INSERT INTO slack_batches VALUES(?,?,?,?,?,?)', (batch_id, target, end_id, json.dumps(ids), 'prepared', now))
        for position, message in enumerate(messages):
            message.update(channel=subscription['channel'], unfurl_links=False, unfurl_media=False,
                           parse='none', link_names=False,
                           client_msg_id=str(uuid.uuid5(uuid.NAMESPACE_URL, batch_id + ':' + str(position))))
            db.execute('INSERT INTO slack_messages(batch_id,position,payload) VALUES(?,?,?)',
                       (batch_id, position, json.dumps(message)))
        db.execute('UPDATE slack_targets SET next_digest_at=? WHERE target=?', (now + interval, target))
    return db.execute('SELECT * FROM slack_batches WHERE id=?', (batch_id,)).fetchone()


def finish_if_complete(db, batch):
    if db.execute("SELECT 1 FROM slack_messages WHERE batch_id=? AND status!='accepted'", (batch['id'],)).fetchone():
        return False
    with db:
        db.execute("UPDATE slack_batches SET status='accepted' WHERE id=?", (batch['id'],))
        db.execute('UPDATE slack_targets SET cursor=? WHERE target=?', (batch['end_id'], batch['target']))
    return True


def tick(store, target, matcher, client, interval=86400, now=None):
    """Post at most one message. Caller paces calls; a file lock prevents peers."""
    now = time.time() if now is None else now
    with (store.directory / 'slack-send.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy'}
        db = store.db
        batch = prepare(db, target, matcher, max(0, interval), now)
        if not batch:
            return {'status': 'waiting'}
        message = db.execute("SELECT * FROM slack_messages WHERE batch_id=? AND status!='accepted' ORDER BY position LIMIT 1", (batch['id'],)).fetchone()
        if not message:
            finish_if_complete(db, batch)
            return {'status': 'accepted', 'batch_id': batch['id']}
        identity = (batch['id'], message['position'])
        if message['status'] in ('in_flight', 'uncertain'):
            # A process may have died after Slack accepted the request. Never
            # blindly retry it; client_msg_id alone is not an exactly-once API.
            return {'status': 'needs_review', 'batch_id': batch['id'], 'position': message['position']}
        if now < message['retry_at']:
            return {'status': 'retry_later'}
        payload = json.loads(message['payload'])
        if message['position']:
            parent = db.execute('SELECT ts FROM slack_messages WHERE batch_id=? AND position=0', (batch['id'],)).fetchone()
            if not parent['ts']:
                raise RuntimeError('Slack parent message is not acknowledged')
            payload['thread_ts'] = parent['ts']
        with db:
            db.execute("UPDATE slack_messages SET status='in_flight',error=NULL WHERE batch_id=? AND position=?", identity)
        try:
            response = client.chat_postMessage(**payload)
            if not response.get('ok') or not response.get('ts') or response.get('channel') != payload['channel']:
                raise ValueError('Slack acknowledgement is incomplete')
        except Exception as error:
            # SlackApiError has a response; network errors and malformed replies
            # do not establish rejection. Only explicit refusals are retried.
            response = getattr(error, 'response', None)
            status = getattr(response, 'status_code', None)
            code = response.get('error', '') if response is not None else ''
            rejected = status == 429 or (status == 200 and code and code not in ('internal_error', 'fatal_error', 'request_timeout'))
            delay = 300
            if status == 429:
                try:
                    delay = max(1, int(response.headers.get('Retry-After', response.headers.get('retry-after', '60'))))
                except (ValueError, TypeError):
                    delay = 60
            safe_code = str(code)[:80] if code and str(code).replace('_', '').isalnum() else type(error).__name__
            with db:
                db.execute('UPDATE slack_messages SET status=?,retry_at=?,error=? WHERE batch_id=? AND position=?',
                           ('pending' if rejected else 'uncertain', now + delay, safe_code, *identity))
            return {'status': 'retry_later' if rejected else 'needs_review', 'batch_id': batch['id'], 'position': message['position'], 'error': safe_code}
        with db:
            db.execute("UPDATE slack_messages SET status='accepted',ts=?,error=NULL WHERE batch_id=? AND position=?", (response['ts'], *identity))
        complete = finish_if_complete(db, batch)
        return {'status': 'accepted' if complete else 'posted', 'batch_id': batch['id'], 'position': message['position']}


def resolve(store, batch_id, position, ts=None, confirmed_absent=False):
    """Operator-only resolution after checking the configured Slack channel."""
    if bool(ts) == bool(confirmed_absent):
        raise ValueError('Supply a confirmed Slack timestamp or confirm the message is absent')
    if ts and not re.fullmatch(r'\d+\.\d+', ts):
        raise ValueError('Invalid Slack message timestamp')
    with (store.directory / 'slack-send.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        row = store.db.execute('SELECT status FROM slack_messages WHERE batch_id=? AND position=?', (batch_id, position)).fetchone()
        if not row or row['status'] not in ('uncertain', 'in_flight'):
            raise ValueError('Only an uncertain message can be resolved')
        with store.db:
            store.db.execute('UPDATE slack_messages SET status=?,ts=?,retry_at=0,error=NULL WHERE batch_id=? AND position=?',
                             ('accepted' if ts else 'pending', ts, batch_id, position))
        batch = store.db.execute('SELECT * FROM slack_batches WHERE id=?', (batch_id,)).fetchone()
        finish_if_complete(store.db, batch)
