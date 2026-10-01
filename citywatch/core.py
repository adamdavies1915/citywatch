import hashlib
import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

PARSER_VERSION = '3'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def normalize(text):
    text = unicodedata.normalize('NFKC', text)
    text = re.sub('[\u2010-\u2015\u2212]', '-', text)
    return re.sub(r'\s+', ' ', text).strip()


@dataclass
class Record:
    key: str
    source: str
    title: str
    url: str
    kind: str
    text: str
    meeting: str = ''
    when: str = ''
    item: str = ''
    status: str = ''
    page: int = 0
    parent_url: str = ''
    # Document hash is provenance; it does not itself trigger an alert.
    raw_hash: str = ''
    extraction: str = 'text'


class Matcher:
    def __init__(self, rules):
        self.version = digest(rules)
        self.rules = [(r, re.compile(r['pattern'], re.I)) for r in rules]

    def match(self, record):
        text = normalize(record.text)
        if record.kind in ('meeting', 'announcement', 'official_summary') and normalize(record.title) not in text:
            text = normalize(record.title) + '. ' + text
        matches = []
        for rule, regex in self.rules:
            for match in regex.finditer(text):
                # Sentence evidence makes unrelated edits elsewhere quiet.
                left = text.rfind('. ', 0, match.start()) + 2
                if left == 1:
                    left = 0
                right = text.find('. ', match.end())
                if right < 0:
                    right = len(text)
                excerpt = text[max(left, match.start()-160):min(right+1, match.end()+240)].strip()
                found = {'rule': rule['id'], 'priority': rule['priority'],
                         'term': match.group(), 'excerpt': excerpt}
                if found not in matches:
                    matches.append(found)
        return matches


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / 'citywatch.sqlite3', timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS records (
                key TEXT PRIMARY KEY, payload TEXT NOT NULL, content_hash TEXT NOT NULL,
                signature TEXT NOT NULL, matches TEXT NOT NULL, rule_version TEXT NOT NULL,
                first_seen TEXT NOT NULL, last_seen TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS revisions (
                id INTEGER PRIMARY KEY, key TEXT NOT NULL, observed_at TEXT NOT NULL,
                payload TEXT NOT NULL, content_hash TEXT NOT NULL, rule_version TEXT NOT NULL,
                parser_version TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY, revision_id INTEGER UNIQUE NOT NULL,
                created_at TEXT NOT NULL, change TEXT NOT NULL, payload TEXT NOT NULL,
                matches TEXT NOT NULL, previous_matches TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending');
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                report TEXT);
        ''')

    def observe(self, record, matcher, today, baseline=False, include_history=False):
        now = datetime.now(timezone.utc).isoformat()
        payload = asdict(record)
        matches = matcher.match(record)
        content_hash = digest(payload)
        # Do not alert for changes to retrieval time, raw bytes, or unrelated paragraphs.
        signature = digest([matches, record.when, record.status, record.title, record.item])
        previous = self.db.execute('SELECT * FROM records WHERE key=?', (record.key,)).fetchone()
        old_matches = json.loads(previous['matches']) if previous else []
        changed = not previous or previous['content_hash'] != content_hash or previous['rule_version'] != matcher.version
        alert = False
        with self.db:
            if changed:
                cursor = self.db.execute('INSERT INTO revisions(key, observed_at, payload, content_hash, rule_version, parser_version) VALUES(?,?,?,?,?,?)',
                                         (record.key, now, json.dumps(payload), content_hash, matcher.version, PARSER_VERSION))
                relevant_change = (matches or old_matches) and (not previous or previous['signature'] != signature)
                # First observations of old material are a quiet baseline. Later revisions still alert.
                historical = bool(record.when and record.when[:10] < today.isoformat())
                notify = relevant_change and not baseline and (previous or include_history or not historical)
                if notify:
                    change = 'new' if not previous else ('rules_changed' if previous['rule_version'] != matcher.version else 'updated')
                    if record.status == 'cancelled':
                        change = 'cancelled'
                    elif previous and old_matches and not matches:
                        change = 'matching_text_removed'
                    self.db.execute('INSERT INTO outbox(revision_id, created_at, change, payload, matches, previous_matches) VALUES(?,?,?,?,?,?)',
                                    (cursor.lastrowid, now, change, json.dumps(payload), json.dumps(matches), json.dumps(old_matches)))
                    alert = True
            self.db.execute('INSERT INTO records VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, content_hash=excluded.content_hash, signature=excluded.signature, matches=excluded.matches, rule_version=excluded.rule_version, last_seen=excluded.last_seen',
                            (record.key, json.dumps(payload), content_hash, signature, json.dumps(matches), matcher.version,
                             previous['first_seen'] if previous else now, now))
        return alert

    def pending(self):
        return [dict(row) for row in self.db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY id")]

    def reconcile_items(self, prefix, current_keys, matcher, today):
        """Only call after a complete, successfully parsed agenda-item listing."""
        queued = 0
        for row in self.db.execute('SELECT payload FROM records WHERE key LIKE ?', (prefix + '%',)).fetchall():
            payload = json.loads(row['payload'])
            if payload['kind'] != 'agenda_item' or ':doc:' in payload['key'] or ':agenda:' in payload['key']:
                continue
            if payload['key'] not in current_keys and payload['status'] != 'no longer listed':
                payload.update(text='', status='no longer listed')
                queued += self.observe(Record(**payload), matcher, today)
        return queued
