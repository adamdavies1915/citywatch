"""Bounded, read-only HTTP with content-addressed evidence and per-run caching."""
import hashlib
import json
import time
import signal
from contextlib import contextmanager
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

ALLOWED_HOSTS = {'council.nola.gov', 'nola.gov', 'www.nola.gov', 'cityofno.granicus.com',
                 'cityofno.legistar.com', 'cityofno.legistar1.com', 'webapi.legistar.com'}


def safe_url(url):
    parts = urlsplit(url)
    if parts.scheme not in ('http', 'https') or parts.hostname not in ALLOWED_HOSTS or parts.username or parts.port not in (None, 80, 443):
        raise ValueError('Unsupported public source URL: ' + url)
    return urlunsplit((parts.scheme, parts.netloc, quote(parts.path, safe='/%:@()!,$&\'~*+-;='),
                       quote(parts.query, safe='=&%:$,?/@()+\'~*;-'), ''))


@contextmanager
def deadline(seconds):
    def expired(signum, frame):
        raise TimeoutError('Download exceeded total time limit')
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


class Redirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return super().redirect_request(req, fp, code, msg, headers, safe_url(newurl))


class Fetcher:
    def __init__(self, directory, config):
        self.directory = Path(directory) / 'evidence'
        self.directory.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.cache = {}
        self.opener = urllib.request.build_opener(Redirects())

    def get(self, url):
        url = safe_url(url)
        if url in self.cache:
            content_type, raw_hash = self.cache[url]
            return (self.directory / raw_hash).read_bytes(), content_type, raw_hash
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={'User-Agent': 'CityWatch/0.1 public-meeting-monitor'})
                with deadline(self.config['timeout_seconds']), self.opener.open(request, timeout=self.config['timeout_seconds']) as response:
                    data = response.read(self.config['max_download_bytes'] + 1)
                    if len(data) > self.config['max_download_bytes']:
                        raise ValueError('Download exceeds configured size limit')
                    raw_hash = hashlib.sha256(data).hexdigest()
                    path = self.directory / raw_hash
                    if not path.exists():
                        path.write_bytes(data)
                    with (self.directory / 'requests.jsonl').open('a') as log:
                        log.write(json.dumps({'url': url, 'final_url': response.url, 'sha256': raw_hash,
                                              'at': datetime.now(timezone.utc).isoformat(),
                                              'content_type': response.headers.get('Content-Type')}) + '\n')
                    result = data, response.headers.get('Content-Type', ''), raw_hash
                    self.cache[url] = result[1:]
                    return result
            except urllib.error.HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
            except (urllib.error.URLError, TimeoutError):
                if attempt == 2:
                    raise
            time.sleep(0.5 * 2 ** attempt)

    def json(self, url):
        return json.loads(self.get(url)[0])


class ReplayFetcher:
    """Replays exact saved HTTP responses; missing evidence fails explicitly."""
    def __init__(self, directory):
        self.directory = Path(directory) / 'evidence'
        self.responses = {}
        for line in (self.directory / 'requests.jsonl').read_text().splitlines():
            entry = json.loads(line)
            self.responses[entry['url']] = entry

    def get(self, url):
        url = safe_url(url)
        if url not in self.responses:
            raise ValueError('URL absent from replay evidence: ' + url)
        entry = self.responses[url]
        data = (self.directory / entry['sha256']).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry['sha256']:
            raise ValueError('Replay evidence hash mismatch')
        return data, entry['content_type'] or '', entry['sha256']

    def json(self, url):
        return json.loads(self.get(url)[0])
