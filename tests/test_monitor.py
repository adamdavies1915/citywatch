import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from citywatch.core import Matcher, Record, Store
from citywatch import parsers as p
from citywatch.collect import Collector
from citywatch.preview import render

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config/watch.json').read_text())


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.matcher = Matcher(CONFIG['rules'])
        self.today = date(2026, 9, 30)
        self.record = Record('test', 'test', 'Project update', 'https://nola.gov/example', 'agenda_item',
                             'Add protected bike lanes.', when='2026-10-01T09:00:00-05:00', status='scheduled')

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def observe(self, record, **kwargs):
        return self.store.observe(record, self.matcher, self.today, **kwargs)

    def test_repeated_scan_does_not_duplicate_and_preview_does_not_ack(self):
        self.assertTrue(self.observe(self.record))
        self.assertFalse(self.observe(self.record))
        render(self.store)
        render(self.store)
        self.assertEqual(len(self.store.pending()), 1)

    def test_changed_same_url_and_reversion_both_alert(self):
        self.observe(self.record)
        self.assertTrue(self.observe(replace(self.record, text='Remove protected bike lanes.')))
        self.assertTrue(self.observe(self.record))
        self.assertEqual(len(self.store.pending()), 3)

    def test_unrelated_paragraph_and_raw_pdf_change_are_quiet(self):
        self.observe(self.record)
        self.assertFalse(self.observe(replace(self.record, text=self.record.text + ' Roll call completed.', raw_hash='changed')))

    def test_cancelled_and_rescheduled_relevant_record_alerts(self):
        self.observe(self.record)
        self.assertTrue(self.observe(replace(self.record, status='cancelled')))
        self.assertTrue(self.observe(replace(self.record, when='2026-10-05T09:00:00-05:00')))

    def test_removed_match_preserves_previous_evidence(self):
        self.observe(self.record)
        self.assertTrue(self.observe(replace(self.record, text='Roll call.')))
        row = self.store.pending()[-1]
        self.assertEqual(row['change'], 'matching_text_removed')
        self.assertTrue(json.loads(row['previous_matches']))

    def test_first_historical_material_quiet_later_revision_alerts(self):
        old = replace(self.record, when='2026-09-01')
        self.assertFalse(self.observe(old))
        self.assertTrue(self.observe(replace(old, text='Remove bike lanes.')))

    def test_baseline_is_explicit(self):
        self.assertFalse(self.observe(self.record, baseline=True))
        self.assertFalse(self.observe(self.record))

    def test_rule_change_is_replayed(self):
        no_matches = Matcher([])
        self.store.observe(self.record, no_matches, self.today)
        self.assertTrue(self.observe(self.record))
        self.assertEqual(self.store.pending()[0]['change'], 'rules_changed')

    def test_boundaries_and_unicode(self):
        self.assertFalse(self.matcher.match(replace(self.record, text='Councilmember Green discussed a bikeathon.')))
        self.assertTrue(self.matcher.match(replace(self.record, text='E–bike safety and shared–use paths.')))

    def test_generic_budget_and_routine_land_use_are_quiet(self):
        for text in ['Capital budget and right-of-way acquisition.',
                     'NORD capital projects director recruitment.',
                     'Permit for a mixed-use multifamily building.']:
            self.assertFalse(self.matcher.match(replace(self.record, text=text)))
        for text in ['Reduce parking minimums.', 'Zoning text amendment.',
                     'Improve pedestrian crossings.', 'Expand bus service.']:
            self.assertTrue(self.matcher.match(replace(self.record, text=text)))

    def test_narrowing_rules_does_not_report_source_removal(self):
        broad = Matcher([{'id': 'broad', 'priority': 'potential', 'pattern': 'capital budget'}])
        record = replace(self.record, text='Capital budget')
        self.store.observe(record, broad, self.today)
        self.assertFalse(self.observe(record))
        self.assertEqual(self.store.refilter_pending(self.matcher), 1)
        self.assertFalse(self.store.pending())

    def test_refilter_preserves_real_removed_match_alert(self):
        self.observe(self.record)
        self.observe(replace(self.record, text='Roll call.'))
        self.store.refilter_pending(self.matcher)
        self.assertEqual(len(self.store.pending()), 2)
        self.assertTrue(json.loads(self.store.pending()[-1]['previous_matches']))

    def test_removed_item_alerts_without_claiming_cancellation(self):
        record = replace(self.record, key='legistar:1:item:10')
        self.observe(record)
        self.assertEqual(self.store.reconcile_items('legistar:1:item:', {'legistar:1:item:11'}, self.matcher, self.today), 1)
        payload = json.loads(self.store.pending()[-1]['payload'])
        self.assertEqual(payload['status'], 'no longer listed')
        self.assertEqual(self.store.reconcile_items('legistar:1:item:', set(), self.matcher, self.today), 0)

    def test_preview_escapes_html(self):
        self.observe(replace(self.record, title='<script>alert(1)</script>', text='Bike lanes <script>alert(1)</script>'))
        preview = render(self.store)
        self.assertNotIn('<script>', (preview / 'digest.html').read_text())


class SourceTests(unittest.TestCase):
    def fixture(self, name):
        return (ROOT / 'tests' / 'fixtures' / (name + '.raw')).read_bytes()

    def test_real_council_feed_and_meeting(self):
        entries = p.council_feed(self.fixture('rss'))
        climate = next(e for e in entries if '20260923-climate' in e['url'])
        self.assertEqual(climate['when'], '2026-09-23T10:00:00-05:00')
        body = p.meeting_body(self.fixture('climate'))
        items = p.agenda_items(body)
        self.assertIn('Lafitte Greenway', dict(items)['4'])
        self.assertNotIn('Bike Tags', p.text(body))
        self.assertTrue(any('event_id=25006' in url for _, url in p.document_links(body, climate['url'])))

    def test_granicus_preserves_attachment_item_relationship(self):
        items = p.granicus_items(self.fixture('granicus-agenda'), 'https://cityofno.granicus.com/')
        item = next(i for i in items if i['number'] == '4.')
        self.assertIn('Greenway', item['text'])
        self.assertEqual(len(item['links']), 1)
        self.assertIn('821903', item['links'][0][1])

    def test_planning_document_relationships(self):
        entries = p.planning_meetings(self.fixture('planning'), 'https://nola.gov/')
        entry = next(e for e in entries if e['when'].startswith('2026-10-13'))
        self.assertIn('City Planning Commission', entry['title'])
        self.assertEqual(entry['when'], '2026-10-13T01:30:00-05:00')
        self.assertTrue(entry['links'])

    def test_old_cancellation_mentioned_in_agenda_is_not_meeting_cancellation(self):
        body = p.soup('<div><h1>Transportation</h1><ol><li>Approve minutes from cancelled meeting</li></ol></div>')
        self.assertFalse(p.meeting_cancelled(body))
        self.assertTrue(p.meeting_cancelled(body, 'Transportation - CANCELLED'))

    def test_news_has_dates_and_pagination(self):
        for fixture, url in [('council-news', 'https://council.nola.gov/news/'), ('news', 'https://nola.gov/next/mayor/news/')]:
            entries, pages = p.news_links(self.fixture(fixture), url)
            self.assertTrue(entries)
            self.assertTrue(pages)
            self.assertTrue(all(e['when'].startswith('2026-') for e in entries))

    def test_failed_source_reports_issue_not_empty_success(self):
        class Broken:
            def get(self, url):
                raise TimeoutError('source unavailable')
        collected = []
        collector = Collector(Broken(), CONFIG, date(2026, 9, 30), collected.append)
        report = collector.run(['council'])
        self.assertTrue(report['issues'])
        self.assertFalse(collected)

    def test_real_legistar_items_include_walking_subject(self):
        items = json.loads(self.fixture('eventitems'))
        item = next(i for i in items if i['EventItemId'] == 21203)
        record = Record('test', 'legistar', '', '', 'agenda_item', item['EventItemTitle'])
        self.assertIn('walking', {m['rule'] for m in Matcher(CONFIG['rules']).match(record)})
        self.assertTrue(item['EventItemMatterAttachments'])


if __name__ == '__main__':
    unittest.main()
