"""Provider-neutral email preview. Rendering never acknowledges or sends alerts."""
import html
import json
import re
from collections import Counter, OrderedDict
from datetime import datetime
from email.message import EmailMessage
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from .core import digest, normalize


TOPICS = {'cycling': 'Cycling', 'walking': 'Walking', 'safe_streets': 'Safe streets',
          'greenways': 'Greenways', 'transit': 'Transit', 'land_use': 'Land use',
          'watched_projects': 'Watched projects', 'autonomous_vehicles': 'Autonomous vehicles'}
SOURCES = {'council': 'Council', 'legistar': 'Legistar', 'planning': 'City Planning',
           'council_news': 'Council news', 'mayor_news': 'Mayor news',
           'public_works_news': 'Public Works'}
HTML_EXCERPTS = 3
NOTE = ('An agenda listing does not establish passage. Topic matches indicate relevance, '
        'not a confirmed bicycle or pedestrian impact.')
REMOVAL_NOTE = ('Previously matching text is no longer present in this record; '
                'this does not establish cancellation.')
LINK_STYLE = 'color:#126748;text-decoration:underline;'
META_STYLE = 'margin:0 0 8px;color:#52625b;font-size:13px;line-height:1.5;'


def readable_date(value):
    if not value:
        return 'Date unavailable'
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return value
    if parsed.tzinfo:
        parsed = parsed.astimezone(ZoneInfo('America/Chicago'))
    result = '%s, %s %d, %d' % (parsed.strftime('%a'), parsed.strftime('%b'), parsed.day, parsed.year)
    if 'T' in value:
        result += ' · %d:%02d %s' % (parsed.hour % 12 or 12, parsed.minute, parsed.strftime('%p'))
        if parsed.tzinfo:
            result += ' ' + parsed.tzname()
    return result


def link(url, label):
    """Only source web URLs are clickable; text is always escaped."""
    try:
        valid = urlsplit(url).scheme in ('http', 'https') and bool(urlsplit(url).hostname)
    except ValueError:
        valid = False
    if not valid:
        return html.escape(label)
    return '<a href="%s" style="%s">%s</a>' % (html.escape(url, quote=True), LINK_STYLE, html.escape(label))


def grouped_updates(rows):
    latest = OrderedDict()
    for row in rows:
        payload = json.loads(row['payload'])
        latest[payload['key']] = (row, payload)
    groups = {}
    for row, payload in latest.values():
        name = payload['meeting'] or payload['title']
        updates = groups.setdefault((payload['when'], name), OrderedDict())
        # Pages share a heading, but different items, changes and statuses retain
        # their own context. Never merge a removal with current evidence.
        identity = (payload['url'], payload['title'], payload['kind'], payload['item'],
                    payload['status'], row['change'])
        update = updates.setdefault(identity, {'payload': payload, 'change': row['change'],
                                               'excerpts': OrderedDict(), 'evidence_keys': {}})
        matches = json.loads(row['matches']) or json.loads(row['previous_matches'])
        for match in matches:
            # Bullet markers and PDF line-break spaces after a hyphen do not
            # make new evidence. Display an unmodified quote and retain all refs.
            evidence_key = re.sub(r'(?<=\w)-\s+(?=\w)', '-', normalize(match['excerpt']).lstrip('• '))
            original = update['evidence_keys'].setdefault(evidence_key, match['excerpt'])
            excerpt = update['excerpts'].setdefault(original, {'tags': [], 'references': []})
            tag = (match['priority'], match['rule'])
            if tag not in excerpt['tags']:
                excerpt['tags'].append(tag)
            reference = (payload['page'], payload.get('extraction', 'text'))
            if reference not in excerpt['references']:
                excerpt['references'].append(reference)
    return [(key, list(updates.values())) for key, updates in sorted(groups.items())]


def topic_label(tags):
    return ' · '.join('%s (%s)' % (TOPICS.get(rule, rule.replace('_', ' ').capitalize()), priority)
                      for priority, rule in tags)


def references_label(references):
    labels = []
    for slide in (False, True):
        pages = sorted({page for page, extraction in references if page and (extraction == 'pptx_text') == slide})
        if pages:
            noun = 'Slide' if slide else 'Page'
            labels.append(noun + ('s ' if len(pages) > 1 else ' ') + ', '.join(map(str, pages)))
    if any(extraction == 'ocr' for _, extraction in references):
        labels.append('Includes OCR text; may contain recognition errors')
    return ' · '.join(labels)


def selected_excerpts(excerpts):
    """Prefer topic variety in the short HTML view; plain text retains everything."""
    selected, covered = [], set()
    for text, details in excerpts.items():
        tags = set(details['tags'])
        if tags - covered:
            selected.append((text, details))
            covered.update(tags)
        if len(selected) == HTML_EXCERPTS:
            return selected
    for text, details in excerpts.items():
        if not any(text == chosen[0] for chosen in selected):
            selected.append((text, details))
        if len(selected) == HTML_EXCERPTS:
            break
    return selected


def coverage_summary(report):
    if report is None or 'issues' not in report:
        return 'Coverage unavailable', ['No collection report is available for this digest.']
    issues = report['issues']
    if not issues:
        return 'No collection issues reported', []
    counts = Counter(SOURCES.get(issue.get('source'), issue.get('source') or 'Other') for issue in issues)
    details = [' · '.join('%s: %d' % pair for pair in counts.items())]
    examples = list(dict.fromkeys(str(issue.get('error', 'Unknown collection issue')) for issue in issues))
    details.extend(examples[:3])
    if len(examples) > 3:
        details.append('%d other types of collection issue.' % (len(examples) - 3))
    return 'Coverage incomplete · %d collection issue%s' % (len(issues), '' if len(issues) == 1 else 's'), details


def render(store, report=None):
    rows = store.pending()
    groups = grouped_updates(rows)
    count = sum(len(updates) for _, updates in groups)
    summary = '%d update%s in this digest' % (count, '' if count == 1 else 's')
    coverage, coverage_details = coverage_summary(report)
    lines = ['CityWatch — New Orleans', summary, '', coverage, '']
    blocks = []
    for (when, name), updates in groups:
        date = readable_date(when)
        lines.extend([name, date, '=' * 48, ''])
        blocks.append('<tr><td style="padding:28px 24px 12px;border-top:1px solid #dae3de;">'
                      '<h2 style="margin:0 0 6px;font-size:23px;line-height:1.3;">%s</h2>'
                      '<p style="%s">%s · %d update%s</p></td></tr>' %
                      (html.escape(name), META_STYLE, html.escape(date), len(updates), '' if len(updates) == 1 else 's'))
        for update in updates:
            payload, change, excerpts = update['payload'], update['change'], update['excerpts']
            metadata = [change.replace('_', ' ').capitalize(), payload['kind'].replace('_', ' ')]
            if payload['item']:
                metadata.append('Item ' + payload['item'].rstrip('.'))
            if payload['status']:
                metadata.append('Source status: ' + payload['status'])
            label = ' · '.join(metadata)
            tags = list(dict.fromkeys(tag for details in excerpts.values() for tag in details['tags']))
            topics = topic_label(tags)
            lines.extend([payload['title'], label, topics, payload['url']])
            blocks.append('<tr><td style="padding:12px 24px 24px;overflow-wrap:anywhere;word-break:break-word;">'
                          '<h3 style="margin:0 0 8px;font-size:18px;line-height:1.4;">%s</h3>'
                          '<p style="%s">%s</p><p style="margin:0 0 12px;color:#126748;font-size:13px;line-height:1.5;">%s</p>' %
                          (html.escape(payload['title']), META_STYLE, html.escape(label), html.escape(topics)))
            if change == 'matching_text_removed':
                lines.append(REMOVAL_NOTE)
                blocks.append('<p style="margin:0 0 12px;">%s</p>' % REMOVAL_NOTE)
            for text, details in excerpts.items():
                reference = references_label(details['references'])
                lines.extend(['[' + topic_label(details['tags']) + ']', text])
                if reference:
                    lines.append(reference)
                lines.append('')
            for text, details in selected_excerpts(excerpts):
                reference = references_label(details['references'])
                blocks.append('<blockquote style="margin:0 0 12px;padding:0;font-size:15px;line-height:1.6;">%s</blockquote>' % html.escape(text))
                if reference:
                    blocks.append('<p style="%s">%s</p>' % (META_STYLE, html.escape(reference)))
            remaining = max(0, len(excerpts) - HTML_EXCERPTS)
            if remaining:
                blocks.append('<p style="%s">%d more matching excerpt%s in this source; all excerpts are included in the plain-text version.</p>' %
                              (META_STYLE, remaining, '' if remaining == 1 else 's'))
            blocks.append('<p style="margin:8px 0 0;font-size:14px;">%s</p></td></tr>' % link(payload['url'], 'Read source document' if payload['kind'] == 'document' else 'Read source'))
            lines.append('')
    if not groups:
        empty = 'No pending topic alerts. Check coverage before interpreting this as no relevant activity.'
        lines.append(empty)
        blocks.append('<tr><td style="padding:24px;">%s</td></tr>' % empty)
    lines.extend(['Coverage', coverage] + coverage_details + ['', NOTE])
    plain = '\n'.join(lines) + '\n'
    page = '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CityWatch — New Orleans updates</title></head>
<body style="margin:0;padding:0;background-color:#f5f7f6;color:#203033;">
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;">%s</div>
<table role="presentation" width="100%%" cellspacing="0" cellpadding="0" border="0" style="background-color:#f5f7f6;">
<tr><td align="center" style="padding:20px 0;">
<!--[if mso]><table role="presentation" width="640" cellspacing="0" cellpadding="0"><tr><td><![endif]-->
<table role="presentation" width="100%%" cellspacing="0" cellpadding="0" border="0" style="max-width:640px;table-layout:fixed;overflow-wrap:anywhere;word-break:break-word;background-color:#ffffff;font-family:Arial,Helvetica,sans-serif;font-size:16px;line-height:1.6;color:#203033;text-align:left;">
<tr><td style="padding:24px;">
<h1 style="margin:0;font-size:30px;line-height:1.2;color:#126748;">CityWatch</h1>
<p style="margin:6px 0 18px;color:#52625b;font-size:14px;">New Orleans · Published topics and changes</p>
<p style="margin:0 0 10px;font-size:17px;line-height:1.5;">%s</p>
<p style="margin:0;color:#52625b;font-size:13px;line-height:1.5;">%s</p>
</td></tr>%s
<tr><td style="padding:24px;border-top:1px solid #dae3de;background-color:#f5f7f6;">
<h2 style="margin:0 0 8px;font-size:16px;line-height:1.4;">Collection coverage</h2>
<p style="%s">%s</p>%s
<p style="margin:20px 0 0;color:#52625b;font-size:13px;line-height:1.6;">%s</p>
</td></tr></table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table></body></html>''' % (
        html.escape(summary + '. ' + coverage + '.'), html.escape(summary), html.escape(coverage), ''.join(blocks),
        META_STYLE, html.escape(coverage), ''.join('<p style="%s">%s</p>' % (META_STYLE, html.escape(detail)) for detail in coverage_details), NOTE)
    out = store.directory / 'preview'
    out.mkdir(exist_ok=True)
    (out / 'digest.txt').write_text(plain)
    (out / 'digest.html').write_text(page)
    events = [dict(id=row['id'], idempotency_key=digest([row['created_at'], row['revision_id'], row['payload']]),
                   created_at=row['created_at'], change=row['change'], record=json.loads(row['payload']),
                   matches=json.loads(row['matches']), previous_matches=json.loads(row['previous_matches']))
              for row in rows]
    (out / 'outbox.json').write_text(json.dumps(events, indent=2))
    message = EmailMessage()
    message['Subject'] = 'CityWatch: %d topic update%s' % (count, '' if count == 1 else 's')
    message.set_content(plain)
    message.add_alternative(page, subtype='html')
    (out / 'digest.eml').write_bytes(message.as_bytes())
    return out
