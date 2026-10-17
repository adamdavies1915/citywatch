import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from email import policy
from email.parser import BytesParser

from bs4 import BeautifulSoup

from citywatch.core import Matcher, Record, Store
from citywatch.preview import render, readable_date


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.matcher = Matcher([{'id': 'cycling', 'priority': 'direct', 'pattern': r'\bbike\b'},
                                {'id': 'walking', 'priority': 'direct', 'pattern': r'\bwalking\b'}])
        self.record = Record('page1', 'council', 'Transport report', 'https://nola.gov/report.pdf',
                             'document', 'Add bike parking and walking paths.', meeting='City Council',
                             when='2026-10-15T15:08:00+00:00', item='31.', page=1)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def observe(self, record):
        self.store.observe(record, self.matcher, date(2026, 10, 1))

    def preview(self, report=None):
        output = render(self.store, report)
        return output, BeautifulSoup((output / 'digest.html').read_text(), 'html.parser')

    def test_pages_share_heading_and_exact_evidence_retains_topics_and_provenance(self):
        self.observe(self.record)
        self.observe(replace(self.record, key='page2', page=2, extraction='ocr'))
        output, page = self.preview({'issues': []})
        self.assertEqual(len(page.find_all('h3')), 1)
        self.assertEqual(len(page.find_all('blockquote')), 1)
        self.assertIn('Cycling (direct) · Walking (direct)', page.get_text())
        self.assertIn('Pages 1, 2', page.get_text())
        self.assertIn('OCR text', page.get_text())
        self.assertIn('Thu, Oct 15, 2026 · 10:08 AM CDT', page.get_text())
        self.assertEqual(len(json.loads((output / 'outbox.json').read_text())), 2)
        self.assertEqual(len(self.store.pending()), 2)

    def test_short_html_preserves_full_plain_text_and_machine_readable_evidence(self):
        self.observe(replace(self.record, text='Bike one. Bike two. Bike three. Bike four. Walking five.'))
        output, page = self.preview()
        excerpts = [quote.get_text() for quote in page.find_all('blockquote')]
        self.assertEqual(len(excerpts), 3)
        self.assertIn('Walking five.', excerpts)
        self.assertIn('2 more matching excerpts', page.get_text())
        self.assertIn('Bike four.', (output / 'digest.txt').read_text())
        events = json.loads((output / 'outbox.json').read_text())
        self.assertEqual(len(events[0]['matches']), 5)
        message = BytesParser(policy=policy.default).parsebytes((output / 'digest.eml').read_bytes())
        self.assertEqual(message.get_body(('plain',)).get_content(), (output / 'digest.txt').read_text())
        self.assertIn('2 more matching excerpts', message.get_body(('html',)).get_content())

    def test_pdf_bullets_and_hyphen_spacing_do_not_repeat_evidence(self):
        self.observe(replace(self.record, text='• Add off- street bike parking.'))
        self.observe(replace(self.record, key='page2', page=2, text='Add off-street bike parking.', extraction='ocr'))
        output, page = self.preview()
        self.assertEqual([q.get_text() for q in page.find_all('blockquote')], ['• Add off- street bike parking.'])
        self.assertIn('Pages 1, 2', page.get_text())
        self.assertEqual(len(json.loads((output / 'outbox.json').read_text())), 2)

    def test_latest_revision_replaces_old_content_but_removals_stay_separate(self):
        self.observe(self.record)
        self.observe(replace(self.record, text='Remove bike parking.'))
        self.observe(replace(self.record, key='page2', page=2))
        self.observe(replace(self.record, key='page2', page=2, text='Roll call.'))
        _, page = self.preview()
        self.assertEqual(len(page.find_all('h3')), 2)
        self.assertIn('does not establish cancellation', page.get_text())
        self.assertEqual([q.get_text() for q in page.find_all('blockquote')],
                         ['Remove bike parking.', self.record.text])

    def test_same_document_in_different_agenda_items_stays_separate(self):
        self.observe(self.record)
        self.observe(replace(self.record, key='item32', item='32.'))
        _, page = self.preview()
        self.assertEqual(len(page.find_all('h3')), 2)

    def test_coverage_unknown_empty_and_partial_are_distinct_and_escaped(self):
        _, page = self.preview()
        self.assertIn('Coverage unavailable', page.get_text())
        self.assertIn('No pending topic alerts', page.get_text())
        _, page = self.preview({'issues': []})
        self.assertIn('No collection issues reported', page.get_text())
        _, page = self.preview({'issues': [{'source': 'council', 'error': '<script>feed failed</script>'}]})
        self.assertIn('Coverage incomplete · 1 collection issue', page.get_text())
        self.assertIn('Council: 1', page.get_text())
        self.assertFalse(page.find_all('script'))
        self.assertNotIn('coverage.json', page.get_text())

    def test_source_markup_and_non_web_links_are_not_executable(self):
        self.observe(replace(self.record, title='<img src=x onerror=alert(1)>',
                             text='Bike <script>alert(1)</script>', url='javascript:alert(1)'))
        _, page = self.preview()
        self.assertFalse(page.find_all(['img', 'script', 'a']))
        self.assertTrue(all(table.get('role') == 'presentation' for table in page.find_all('table')))

    def test_dates_keep_date_only_and_unrecognized_values(self):
        self.assertEqual(readable_date('2026-10-15'), 'Thu, Oct 15, 2026')
        self.assertEqual(readable_date('2026-12-15T16:00:00Z'), 'Tue, Dec 15, 2026 · 10:00 AM CST')
        self.assertEqual(readable_date('TBD'), 'TBD')
        self.assertEqual(readable_date(''), 'Date unavailable')


if __name__ == '__main__':
    unittest.main()
