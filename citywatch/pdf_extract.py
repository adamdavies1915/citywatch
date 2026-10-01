"""Bounded PDF worker with optional local OCR and page-level provenance."""
import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from pypdf import PdfReader


def extract(path, ocr=False, metadata=False):
    reader = PdfReader(path)
    if len(reader.pages) > 500:
        raise ValueError('PDF exceeds 500-page parsing limit')
    native_pages = None
    if shutil.which('pdftotext'):
        converted = subprocess.run(['pdftotext', '-layout', str(path), '-'], capture_output=True, text=True, timeout=30)
        if converted.returncode == 0:
            native_pages = converted.stdout.split('\f')
    pages = []
    started = time.monotonic()
    for number, page in enumerate(reader.pages, 1):
        if native_pages is not None and number <= len(native_pages):
            value = native_pages[number - 1]
        else:
            content = page.get_contents()
            if content and len(content.get_data()) > 20_000_000:
                raise ValueError('PDF page content stream exceeds parsing limit')
            value = page.extract_text() or ''
        result = {'text': value, 'method': 'pdf_text', 'warning': ''}
        # Sparse text can be an image slide with only a selectable heading.
        if ocr and len(value.strip()) < 100:
            if not shutil.which('pdftoppm') or not shutil.which('tesseract'):
                result['warning'] = 'Sparse page; OCR tools (pdftoppm and tesseract) unavailable'
            elif time.monotonic() - started > 140:
                result['warning'] = 'Sparse page; document OCR time budget reached'
            else:
                try:
                    with tempfile.TemporaryDirectory(prefix='citywatch-ocr-') as directory:
                        prefix = str(Path(directory) / 'page')
                        subprocess.run(['pdftoppm', '-f', str(number), '-l', str(number), '-singlefile',
                                        '-scale-to', '2200', '-png', str(path), prefix],
                                       check=True, capture_output=True, timeout=20)
                        env = dict(os.environ, OMP_THREAD_LIMIT='1')
                        process = subprocess.run(['tesseract', prefix + '.png', 'stdout', '-l', 'eng'],
                                                 check=True, capture_output=True, text=True, timeout=20, env=env)
                        recognized = process.stdout.strip()
                        if len(recognized) > len(value.strip()):
                            result.update(text=recognized, method='ocr')
                except (subprocess.SubprocessError, OSError) as error:
                    result['warning'] = 'OCR failed: ' + str(error)[:200]
        if not result['text'].strip():
            result['warning'] = result['warning'] or 'No extractable text after available extraction; image/blank page requires review'
        pages.append(result if metadata else result['text'])
    if not pages:
        raise ValueError('PDF has no pages')
    return pages


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path')
    parser.add_argument('--ocr', action='store_true')
    parser.add_argument('--metadata', action='store_true')
    args = parser.parse_args()
    print(json.dumps(extract(args.path, args.ocr, args.metadata)))
