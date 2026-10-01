import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from citywatch.collect import Collector
from citywatch.core import Store, Matcher
from citywatch.core import Record
from citywatch.fetch import ReplayFetcher, safe_url, deadline

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / 'config/watch.json').read_text())


def make_pdf(path, text=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=200)
    if text:
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        stream.set_data(('BT /F1 12 Tf 10 100 Td (%s) Tj ET' % text).encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


class DocumentTests(unittest.TestCase):
    def test_pdf_page_evidence_and_blank_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'evidence'
            folder.mkdir()
            datafile = folder / 'sample.pdf'
            make_pdf(datafile, 'Add bicycle parking.')
            class FakeFetcher:
                def __init__(self):
                    self.directory = folder
                def get(self, url):
                    data = datafile.read_bytes()
                    sha = hashlib.sha256(data).hexdigest()
                    (folder / sha).write_bytes(data)
                    return data, 'application/pdf', sha
            records = []
            parent = Record('meeting:1', 'test', 'Meeting', 'https://nola.gov/meeting', 'meeting', '', when='2026-10-01')
            collector = Collector(FakeFetcher(), CONFIG, date(2026, 9, 30), records.append)
            collector.document('Report', 'https://nola.gov/report.pdf', parent)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].page, 1)
            self.assertEqual(records[0].parent_url, parent.url)
            self.assertIn('bicycle', records[0].text)
            self.assertFalse(collector.errors)
            make_pdf(datafile)
            records.clear()
            collector = Collector(FakeFetcher(), CONFIG, date(2026, 9, 30), records.append)
            collector.document('Report', 'https://nola.gov/report.pdf', parent)
            self.assertTrue(collector.errors)
            self.assertFalse(records)  # Unreadable page must not erase a previous successful match.

    def test_office_text_without_trusting_content_type(self):
        import io, zipfile
        from citywatch.parsers import office_text
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="urn:word"><w:p><w:r><w:t>New bicycle lane</w:t></w:r></w:p></w:document>')
        parts = office_text(buffer.getvalue())
        self.assertEqual(parts[0]['text'], 'New bicycle lane')
        self.assertEqual(parts[0]['page'], 0)
        self.assertEqual(parts[0]['method'], 'docx_text')

    def test_total_download_deadline(self):
        import time
        with self.assertRaises(TimeoutError):
            with deadline(0.02):
                time.sleep(0.1)

    def test_missing_replay_and_host_restrictions(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / 'evidence'
            evidence.mkdir()
            (evidence / 'requests.jsonl').write_text('')
            with self.assertRaises(ValueError):
                ReplayFetcher(directory).get('https://nola.gov/missing')
        for url in ['file:///etc/passwd', 'http://127.0.0.1/', 'https://nola.gov.evil.example/a', 'https://nola.gov:9999/a']:
            with self.assertRaises(ValueError):
                safe_url(url)


if __name__ == '__main__':
    unittest.main()
