#!/usr/bin/env python3
"""Read-only source audit. Downloads public samples; never sends email.

Run with --offline to summarize the saved samples without network access.
This is a research tool, not the production monitoring service.
"""
import argparse
import hashlib
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {
    'events': 'https://webapi.legistar.com/v1/cityofno/events?$orderby=EventDate%20desc&$top=15',
    'bodies': 'https://webapi.legistar.com/v1/cityofno/bodies',
    'rss': 'https://council.nola.gov/meetings/?rss=events',
    'council': 'https://council.nola.gov/meetings/',
    'climate': 'https://council.nola.gov/meetings/2026/committees/climate-change-and-sustainability/20260923-climate-change-and-sustainability/',
    'news': 'https://nola.gov/next/mayor/news/',
    'planning': 'https://nola.gov/next/city-planning/meetings/',
    'eventitems': 'https://webapi.legistar.com/v1/cityofno/events/1016/eventitems?AgendaNote=1&MinutesNote=1&Attachments=1',
    'granicus-agenda': 'https://cityofno.granicus.com/GeneratedAgendaViewer.php?event_id=25006&view_id=42',
    'council-news': 'https://council.nola.gov/news/',
    'rta-events': 'https://webapi.legistar.com/v1/norta/events?$orderby=EventDate%20desc&$top=3',
}
# Illustrative exact rules, not a complete production taxonomy.
RULES = {
    'cycling': r'\b(?:bicycles?|bikes?|e-bikes?|bikeways?|cycling)\b',
    'walking': r'\b(?:pedestrians?|sidewalks?|walkways?|walkability|crosswalks?)\b',
    'greenways': r'\bgreenways?\b',
    'transit': r'\b(?:public transit|public transportation|fare-free|streetcars?)\b',
}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(value)
    return re.sub(r'\s+', ' ', ' '.join(parser.parts)).strip()


def hits(value):
    text = plain(value)
    result = []
    for name, pattern in RULES.items():
        match = re.search(pattern, text, re.I)
        if match:
            result.append({'rule': name, 'matched': match.group(),
                           'excerpt': text[max(0, match.start()-90):match.end()+150]})
    return result


def fetch(pair):
    name, url = pair
    record = {'url': url, 'checked_at': datetime.now(timezone.utc).isoformat()}
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'CityWatch-source-audit/0.1'})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(10_000_001)
            if len(data) > 10_000_000:
                raise ValueError('Source exceeded 10 MB audit limit')
            record.update(status=response.status, content_type=response.headers.get('Content-Type'),
                          final_url=response.url, bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        (ROOT / 'research' / (name + '.raw')).write_bytes(data)
    except Exception as error:
        record['error'] = str(error)
    return name, record


def summarize(as_of):
    folder = ROOT / 'research'
    bodies = json.loads((folder / 'bodies.raw').read_bytes())
    events = json.loads((folder / 'events.raw').read_bytes())
    items = json.loads((folder / 'eventitems.raw').read_bytes())
    feed = ET.fromstring((folder / 'rss.raw').read_bytes()).findall('.//item')
    examples = []
    recent_count = 0
    for item in feed:
        date = parsedate_to_datetime(item.findtext('pubDate'))
        age = (as_of - date.date()).days
        if not -60 <= age <= 60:
            continue
        recent_count += 1
        matches = hits(item.findtext('description') or '')
        if matches:
            examples.append({'title': item.findtext('title'), 'meeting_time': date.isoformat(),
                             'url': item.findtext('link'), 'matches': matches})
    return {
        'as_of': as_of.isoformat(),
        'limitations': 'Audit examples only. RSS matches cover descriptions, not linked attachments. No email or scheduler.',
        'legistar_bodies': [b['BodyName'] for b in bodies],
        'legistar_sample_events': [{'id': e['EventId'], 'date': e['EventDate'], 'body': e['EventBodyName']} for e in events],
        'october_1_event_items_including_headings': len(items),
        'october_1_attachment_references': sum(len(i.get('EventItemMatterAttachments') or []) for i in items),
        'rss_total_items': len(feed), 'rss_items_within_60_days': recent_count,
        'rss_matching_meetings': examples,
        'october_1_matching_items': [
            {'id': i['EventItemId'], 'agenda_number': i['EventItemAgendaNumber'],
             'title': i['EventItemTitle'], 'matches': hits(i['EventItemTitle'] or '')}
            for i in items if hits(i['EventItemTitle'] or '')],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--offline', action='store_true')
    parser.add_argument('--as-of', default='2026-09-30', help='YYYY-MM-DD for reproducible research window')
    args = parser.parse_args()
    folder = ROOT / 'research'
    folder.mkdir(exist_ok=True)
    if not args.offline:
        with ThreadPoolExecutor(max_workers=3) as pool:
            records = dict(pool.map(fetch, SOURCES.items()))
        (folder / 'fetch-manifest.json').write_text(json.dumps(records, indent=2) + '\n')
        errors = {k: v['error'] for k, v in records.items() if 'error' in v}
        if errors:
            print(json.dumps({'errors': errors}), file=sys.stderr)
            return 1  # Never report success using stale snapshots after a fetch failure.
    result = summarize(datetime.strptime(args.as_of, '%Y-%m-%d').date())
    (folder / 'audit-summary.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k not in ('rss_matching_meetings', 'october_1_matching_items')}, indent=2))
    print('Matching RSS meetings:', len(result['rss_matching_meetings']))
    print('Matching October 1 items:', len(result['october_1_matching_items']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
