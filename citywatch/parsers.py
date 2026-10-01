import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from .core import normalize

ZONE = ZoneInfo('America/Chicago')


def soup(data):
    return BeautifulSoup(data, 'html.parser')


def text(node):
    return normalize(node.get_text(' ', strip=True))


def canonical(url):
    p = urlsplit(url)
    query = '&'.join(x for x in p.query.split('&') if x and not x.startswith(('feed=', 'rss=')))
    return urlunsplit((p.scheme, p.netloc.lower(), p.path, query, ''))


def council_feed(data):
    root = ET.fromstring(data)
    entries = []
    for item in root.findall('./channel/item'):
        entries.append({'id': item.findtext('guid'), 'title': item.findtext('title') or '',
                        'url': canonical(item.findtext('link') or ''),
                        'when': parsedate_to_datetime(item.findtext('pubDate')).astimezone(ZONE).isoformat(),
                        'description': item.findtext('description') or ''})
    if not entries:
        raise ValueError('Council feed has no items; check source structure')
    return entries


def meeting_body(data):
    page = soup(data)
    main = page.find('main')
    heading = main.find('h1') if main else None
    if not heading:
        raise ValueError('Meeting content heading missing')
    # Actual Council meeting heading, time, agenda text, and buttons share this div.
    return heading.parent



def meeting_cancelled(node, title=''):
    heading = node.find('h1')
    if re.search(r'\bcancel(?:led|ed)\b', title + ' ' + (text(heading) if heading else ''), re.I):
        return True
    # Explicit standalone notices before the agenda, not references to old cancellations in items.
    if heading:
        for sibling in heading.next_siblings:
            if getattr(sibling, 'name', None) in ('ol', 'ul'):
                break
            value = text(sibling) if getattr(sibling, 'name', None) else str(sibling).strip()
            if re.fullmatch(r'(?:meeting\s+)?cancel(?:led|ed)[.!]?', value, re.I):
                return True
    return False


def agenda_items(node):
    entries = []
    for ol in node.find_all('ol'):
        number = 0
        for li in ol.find_all('li', recursive=False):
            number = int(li.get('value', number + 1))
            entries.append((str(number), text(li)))
    return entries


def document_links(node, base):
    links = []
    for a in node.find_all('a', href=True):
        url = urljoin(base, a['href'])
        lower = url.lower()
        if any(marker in lower for marker in ('.pdf', '/getattachment/', 'metaviewer.php', 'agendaviewer.php', 'view.ashx')):
            links.append((text(a) or 'Document', canonical(url)))
    return list(dict.fromkeys(links))


def granicus_items(data, base):
    page = soup(data)
    entries = []
    current = None
    # Documents appear between numbered tables rather than inside their item table.
    for node in page.find_all(['td', 'a']):
        if node.name == 'td' and 'numberspace' in node.get('class', []):
            body = node.find_next_sibling('td')
            if body:
                current = {'number': text(node), 'text': text(body), 'links': []}
                entries.append(current)
        elif node.name == 'a' and node.get('href') and current:
            url = urljoin(base, node['href'])
            if 'MetaViewer.php' in url or '.pdf' in url.lower():
                current['links'].append((text(node), url))
    return entries


def planning_meetings(data, base):
    page = soup(data)
    entries = []
    for pane in page.select('.tab-pane'):
        body = ''
        current = None
        for node in pane.find_all(['h3', 'strong', 'a']):
            if node.name == 'h3':
                body = text(node)
            elif node.name == 'strong':
                raw = text(node)
                try:
                    date = datetime.strptime(raw, '%B %d, %Y %I:%M %p').replace(tzinfo=ZONE)
                except ValueError:
                    continue
                current = {'title': body, 'when': date.isoformat(), 'links': []}
                entries.append(current)
            elif node.name == 'a' and current and node.get('href'):
                current['links'].append((text(node), urljoin(base, node['href'])))
    if not entries:
        raise ValueError('Planning meeting groups missing; check source structure')
    return entries


def news_links(data, base):
    page = soup(data)
    entries = []
    for card in page.select('.blog-teaser, .media-news'):
        link = card.select_one('h2 a, h3 a')
        date = card.select_one('.blog-meta, .news-meta-from')
        if link and date:
            raw_date = text(date).split('|')[0].strip()
            when = datetime.strptime(raw_date, '%B %d, %Y').date().isoformat()
            entries.append({'title': text(link), 'url': canonical(urljoin(base, link['href'])), 'when': when})
    if not entries:
        raise ValueError('News cards missing; check source structure')
    pages = []
    for a in page.find_all('a', href=True):
        url = urljoin(base, a['href'])
        if re.search(r'[?&]page=\d+', url, re.I) and urlsplit(url).path == urlsplit(base).path:
            pages.append(url)
    return entries, list(dict.fromkeys(pages))


def article_body(data):
    page = soup(data)
    body = page.select_one('.blog-content')
    if body:
        return body
    main = page.find('main')
    if not main:
        raise ValueError('Article main content missing')
    for node in main.select('#dept-nav-wrapper, nav, script, style, .lastUpdated, #section-whereyat'):
        node.decompose()
    return main


def office_text(data):
    """Extract bounded DOCX/PPTX XML even when servers mislabel it as text/plain."""
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if 'word/document.xml' in names:
            selected = ['word/document.xml']
            selected += sorted(n for n in names if re.fullmatch(r'word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml', n))
            method = 'docx_text'
        else:
            selected = sorted((n for n in names if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)),
                              key=lambda n: int(re.search(r'(\d+)\.xml', n).group(1)))
            method = 'pptx_text'
        if not selected:
            raise ValueError('Unsupported ZIP document; expected DOCX or PPTX')
        if sum(archive.getinfo(n).file_size for n in selected) > 20_000_000:
            raise ValueError('Office document XML exceeds extraction size limit')
        result = []
        for index, name in enumerate(selected, 1):
            root = ET.fromstring(archive.read(name))
            values = [''.join(node.itertext()) for node in root.iter() if node.tag.rsplit('}', 1)[-1] == 't']
            content = normalize(' '.join(values))
            if not content and method == 'docx_text' and name != 'word/document.xml':
                continue
            result.append({'text': content, 'page': index if method == 'pptx_text' else 0,
                           'method': method, 'part': name})
        return result
