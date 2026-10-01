"""Source-specific collectors. All source and document failures remain visible."""
import json
import re
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from urllib.parse import urlencode, urljoin, urlsplit
from .core import Record, digest
from . import parsers as p

RSS = 'https://council.nola.gov/meetings/?rss=events'
CALENDAR = 'https://council.nola.gov/meetings/'
PLANNING = 'https://nola.gov/next/city-planning/meetings/'
NEWS = {
    'council_news': 'https://council.nola.gov/news/',
    'mayor_news': 'https://nola.gov/next/mayor/news/',
    'public_works_news': 'https://nola.gov/next/public-works/news/',
}


class Collector:
    def __init__(self, fetcher, config, today, emit, reconcile=None):
        self.fetch = fetcher
        self.config = config
        self.today = today
        self.emit = emit
        self.reconcile = reconcile or (lambda prefix, keys: None)
        self.errors = []
        self.counts = {}
        self.documents = set()
        self.document_cache = {}
        self.start = today - timedelta(days=config['lookback_days'])
        self.end = today + timedelta(days=config['lookahead_days'])

    def in_window(self, when):
        return self.start.isoformat() <= when[:10] <= self.end.isoformat()

    def error(self, source, url, error):
        self.errors.append({'source': source, 'url': url, 'error': str(error)})

    def safe(self, source, url, fn):
        try:
            return fn()
        except Exception as error:
            self.error(source, url, error)
            return None

    def record(self, record):
        self.emit(record)
        self.counts[record.source] = self.counts.get(record.source, 0) + 1

    def document(self, title, url, parent, depth=0):
        if depth > 2:
            self.error(parent.source, url, 'Document traversal depth exceeded')
            return
        identity = (parent.key, p.canonical(url))
        if identity in self.documents:
            return
        if len(self.documents) >= self.config['max_documents_per_run']:
            self.error(parent.source, url, 'Document limit reached; coverage incomplete. Increase max_documents_per_run.')
            return
        self.documents.add(identity)

        def read():
            data, content_type, raw_hash = self.fetch.get(url)
            if data.startswith(b'%PDF') or 'application/pdf' in content_type:
                if raw_hash not in self.document_cache:
                    # Isolate PDF parsing and enforce a wall-clock limit.
                    path = self.fetch.directory / raw_hash
                    cache_dir = self.fetch.directory.parent / 'extracted'
                    cache_dir.mkdir(exist_ok=True)
                    cache_path = cache_dir / (raw_hash + '-v3-ocr' + str(bool(self.config.get('ocr', False))) + '.json')
                    if cache_path.exists():
                        extracted_pages = json.loads(cache_path.read_text())
                    else:
                        command = [sys.executable, '-m', 'citywatch.pdf_extract', str(path), '--metadata']
                        if self.config.get('ocr', False):
                            command.append('--ocr')
                        result = subprocess.run(command, capture_output=True, text=True, timeout=200)
                        if result.returncode:
                            raise ValueError('PDF extraction failed: ' + result.stderr[-500:])
                        extracted_pages = json.loads(result.stdout)
                        if not any(any(term in page['warning'] for term in ('failed', 'budget', 'unavailable')) for page in extracted_pages):
                            cache_path.write_text(json.dumps(extracted_pages))
                    self.document_cache[raw_hash] = extracted_pages
                pages = self.document_cache[raw_hash]
                for number, extracted in enumerate(pages, 1):
                    page = extracted['text']
                    if extracted['warning']:
                        self.error(parent.source, url, 'Page %d: %s' % (number, extracted['warning']))
                    if not page.strip():
                        continue
                    self.record(replace(parent, key=parent.key + ':doc:' + digest(url) + ':p' + str(number),
                                        title=title, url=url, kind='document', text=page, page=number,
                                        parent_url=parent.url, raw_hash=raw_hash, extraction=extracted['method']))
            elif data.startswith(b'PK'):
                for part in p.office_text(data):
                    if not part['text']:
                        self.error(parent.source, url, 'No text in ' + part['part'] + '; visual review required')
                        continue
                    self.record(replace(parent, key=parent.key + ':doc:' + digest(url) + ':' + part['part'],
                                        title=title, url=url, kind='document', text=part['text'], page=part['page'],
                                        parent_url=parent.url, raw_hash=raw_hash, extraction=part['method']))
            elif 'html' in content_type or data.lstrip().startswith((b'<', b'<!')):
                entries = p.granicus_items(data, url)
                if entries:
                    for index, entry in enumerate(entries):
                        item = replace(parent, key=parent.key + ':agenda:' + digest(url) + ':' + str(index),
                                       kind='agenda_item', item=entry['number'], title=entry['text'][:180],
                                       text=entry['text'], url=url, parent_url=parent.url, raw_hash=raw_hash)
                        self.record(item)
                        for label, attachment in entry['links']:
                            self.document(label, attachment, item, depth+1)
                else:
                    node = p.soup(data)
                    links = p.document_links(node, url)
                    # Some document viewers wrap a PDF in an iframe or embed.
                    for frame in node.select('iframe[src], embed[src], object[data]'):
                        link = frame.get('src') or frame.get('data')
                        links.append((title, urljoin(url, link)))
                    links = [(label, link) for label, link in links if p.canonical(link) != p.canonical(url)]
                    if not links:
                        raise ValueError('Unrecognized document viewer; no PDF or agenda items found')
                    for label, link in links:
                        self.document(label, link, parent, depth+1)
            else:
                raise ValueError('Unsupported document type: ' + content_type)
        self.safe(parent.source, url, read)

    def council(self):
        feed, _, raw_hash = self.fetch.get(RSS)
        entries = p.council_feed(feed)
        indexed = {e['url']: e for e in entries}
        # Reconcile the visible calendar to the feed instead of assuming feed completeness.
        calendar, _, _ = self.fetch.get(CALENDAR)
        calendar_page = p.soup(calendar)
        main = calendar_page.find('main')
        if not main:
            raise ValueError('Council calendar main content missing')
        links = {p.canonical(urljoin(CALENDAR, a['href'])) for a in main.find_all('a', href=True)
                 if re.search(r'/meetings/\d{4}/', a['href'])}
        if not links:
            raise ValueError('Council calendar contains no meeting links; check source')
        for url in sorted(links - indexed.keys()):
            self.error('council', url, 'Calendar meeting absent from RSS; requires review')
        for entry in entries:
            if not self.in_window(entry['when']):
                continue
            parent = Record('council:' + (entry['id'] or digest(entry['url'])), 'council', entry['title'],
                            entry['url'], 'meeting', '', meeting=entry['title'], when=entry['when'])
            description = p.soup(entry['description'])
            # Persist the RSS text even when the meeting page temporarily fails.
            self.record(replace(parent, key=parent.key + ':feed', text=p.text(description), raw_hash=raw_hash,
                                status='cancelled' if 'cancelled' in entry['title'].lower() else 'scheduled'))

            def read(parent=parent):
                data, _, page_hash = self.fetch.get(parent.url)
                body = p.meeting_body(data)
                status = 'cancelled' if p.meeting_cancelled(body, parent.title) else 'scheduled'
                parent = replace(parent, status=status, raw_hash=page_hash)
                entries = p.agenda_items(body)
                links = p.document_links(body, parent.url)
                if entries:
                    for number, item_text in entries:
                        self.record(replace(parent, key=parent.key + ':item:' + number, kind='agenda_item',
                                            item=number, title=item_text[:180], text=item_text))
                    self.reconcile(parent.key + ':item:', {parent.key + ':item:' + number for number, _ in entries})
                else:
                    self.record(replace(parent, text=p.text(body)))
                    if not links and status != 'cancelled':
                        self.error('council', parent.url, 'Agenda not yet published or not recognized')
                for title, url in links:
                    self.document(title, url, parent)
            self.safe('council', parent.url, read)

    def legistar(self):
        base = 'https://webapi.legistar.com/v1/cityofno'
        query = { '$filter': "EventDate ge datetime'%s' and EventDate le datetime'%s'" % (self.start, self.end),
                  '$orderby': 'EventId', '$top': 100, '$skip': 0}
        while True:
            events = self.fetch.json(base + '/events?' + urlencode(query))
            if not isinstance(events, list):
                raise ValueError('Unexpected Legistar event response')
            for event in events:
                url = base + '/events/%s/eventitems?AgendaNote=1&MinutesNote=1&Attachments=1' % event['EventId']
                def read(event=event, url=url):
                    when = datetime.strptime(event['EventDate'][:10] + ' ' + event['EventTime'], '%Y-%m-%d %I:%M %p').replace(tzinfo=p.ZONE).isoformat()
                    parent = Record('legistar:' + str(event['EventId']), 'legistar', event['EventBodyName'],
                                    event['EventInSiteURL'], 'meeting', '', meeting=event['EventBodyName'], when=when,
                                    status=event.get('EventAgendaStatusName') or '')
                    items = self.fetch.json(url)
                    if not items:
                        self.error('legistar', url, 'No published agenda items')
                    for item in items:
                        content = '\n'.join(str(item.get(k) or '') for k in ('EventItemTitle', 'EventItemAgendaNote', 'EventItemMinutesNote', 'EventItemActionText'))
                        record = replace(parent, key=parent.key + ':item:' + str(item['EventItemId']), kind='agenda_item',
                                         title=(item.get('EventItemTitle') or 'Agenda item').split('\n')[0],
                                         text=p.text(p.soup(content)), item=str(item.get('EventItemAgendaNumber') or ''),
                                         status=' / '.join(filter(None, [parent.status, item.get('EventItemActionName')])) )
                        self.record(record)
                        for attachment in item.get('EventItemMatterAttachments') or []:
                            if attachment.get('MatterAttachmentHyperlink'):
                                self.document(attachment['MatterAttachmentName'], attachment['MatterAttachmentHyperlink'], record)
                    if items:
                        self.reconcile(parent.key + ':item:', {parent.key + ':item:' + str(i['EventItemId']) for i in items})
                    # Full agenda and minutes can contain content absent from item records.
                    for field, label in [('EventAgendaFile', 'Published agenda'), ('EventMinutesFile', 'Published minutes')]:
                        if event.get(field):
                            self.document(label, event[field], replace(parent, kind='minutes' if 'Minutes' in field else 'agenda'))
                self.safe('legistar', url, read)
            if len(events) < 100:
                break
            query['$skip'] += 100

    def planning(self):
        data, _, raw_hash = self.fetch.get(PLANNING)
        for entry in p.planning_meetings(data, PLANNING):
            if not self.in_window(entry['when']):
                continue
            parent = Record('planning:' + digest([entry['title'], entry['when']]), 'planning', entry['title'], PLANNING,
                            'meeting', entry['title'], meeting=entry['title'], when=entry['when'], raw_hash=raw_hash)
            self.record(parent)
            if 'T01:' in parent.when:
                self.error('planning', PLANNING, 'Suspicious source meeting time (retained): ' + parent.title + ' ' + parent.when)
            if not entry['links']:
                self.error('planning', PLANNING, 'No documents published for ' + parent.title + ' ' + parent.when)
            for title, url in entry['links']:
                self.document(title, url, parent)

    def news(self, name):
        base = NEWS[name]
        queue = [base]
        visited = set()
        articles = set()
        while queue and len(visited) < self.config['max_news_pages']:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            data, _, _ = self.fetch.get(url)
            entries, pages = p.news_links(data, base)
            for entry in entries:
                if not self.in_window(entry['when']) or entry['url'] in articles:
                    continue
                articles.add(entry['url'])
                def read(entry=entry):
                    data, _, raw_hash = self.fetch.get(entry['url'])
                    body = p.article_body(data)
                    record = Record('news:' + digest(entry['url']), name, entry['title'], entry['url'],
                                    'official_summary' if 'meeting summary' in entry['title'].lower() else 'announcement',
                                    p.text(body), when=entry['when'], raw_hash=raw_hash)
                    self.record(record)
                    for label, attachment in p.document_links(body, entry['url']):
                        self.document(label, attachment, record)
                self.safe(name, entry['url'], read)
            # Pages are chronological: stop after reaching older material.
            if min(e['when'] for e in entries) >= self.start.isoformat():
                queue.extend(x for x in pages if x not in visited and x not in queue)
        if queue:
            self.error(name, base, 'News pagination limit reached; earlier material may be unchecked')

    def run(self, sources):
        for name in sources:
            print('Collecting ' + name + '...', flush=True)
            if name in NEWS:
                self.safe(name, NEWS[name], lambda name=name: self.news(name))
            else:
                self.safe(name, name, getattr(self, name))
        return {'records': self.counts, 'document_relationships': len(self.documents), 'issues': self.errors,
                'window': [self.start.isoformat(), self.end.isoformat()]}
