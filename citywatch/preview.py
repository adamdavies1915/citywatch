"""Provider-neutral email preview. Rendering never acknowledges or sends alerts."""
import html
import json
from collections import OrderedDict
from email.message import EmailMessage
from pathlib import Path
from .core import digest


def render(store, report=None):
    rows = store.pending()
    latest = OrderedDict()
    for row in rows:
        payload = json.loads(row['payload'])
        latest[payload['key']] = (row, payload)
    lines = ['CityWatch — New Orleans', '', 'Published topics and changes; an agenda listing does not establish passage.', '']
    if report:
        lines.append('Coverage: %d issue(s). See coverage.json for details.' % len(report.get('issues', [])))
        lines.append('')
    if not latest:
        lines.append('No pending topic alerts. Check coverage before interpreting this as no relevant activity.')
    groups = OrderedDict()
    for row, payload in latest.values():
        name = payload['meeting'] or payload['title']
        groups.setdefault((payload['when'], name), []).append((row, payload))
    blocks = []
    for (when, name), entries in sorted(groups.items()):
        lines.extend([name, when or 'Date unavailable', ''])
        blocks.append('<h2>%s</h2><p>%s</p>' % (html.escape(name), html.escape(when)))
        seen = set()
        for row, payload in entries:
            matches = json.loads(row['matches']) or json.loads(row['previous_matches'])
            # Same evidence may occur in the RSS feed and HTML agenda: collapse exact repeats in the email.
            evidence = tuple((m['rule'], m['excerpt']) for m in matches)
            identity = (payload['url'], payload['page'], payload['item'], evidence, row['change'])
            if identity in seen:
                continue
            seen.add(identity)
            label = '%s · %s' % (row['change'].replace('_', ' '), payload['kind'].replace('_', ' '))
            if payload['item']:
                label += ' · item ' + payload['item']
            if payload['page']:
                label += (' · slide ' if payload.get('extraction') == 'pptx_text' else ' · page ') + str(payload['page'])
            if payload.get('extraction') == 'ocr':
                label += ' · OCR text (may contain recognition errors)'
            if payload['status']:
                label += ' · ' + payload['status']
            lines.extend([label, payload['title'], payload['url']])
            blocks.append('<article><p class="meta">%s</p><h3>%s</h3><p><a href="%s">Source document</a></p>' %
                          (html.escape(label), html.escape(payload['title']), html.escape(payload['url'], quote=True)))
            if row['change'] == 'matching_text_removed':
                lines.append('Previously matching text is no longer present in this record; this does not establish cancellation.')
                blocks.append('<p>Previously matching text is no longer present; this does not establish cancellation.</p>')
            for match in matches:
                lines.extend(['[%s / %s] %s' % (match['priority'], match['rule'], match['excerpt'])])
                blocks.append('<p><strong>%s / %s</strong></p><blockquote>%s</blockquote>' %
                              (html.escape(match['priority']), html.escape(match['rule']), html.escape(match['excerpt'])))
            lines.append('')
            blocks.append('</article>')
    plain = '\n'.join(lines) + '\n'
    issue_count = len((report or {}).get('issues', []))
    page = '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>CityWatch email preview</title><style>body{font:16px/1.6 system-ui,sans-serif;max-width:850px;margin:40px auto;padding:0 24px;color:#203033;background:#f5f7f6}article{background:white;padding:20px;margin:18px 0;border:1px solid #dae3de;border-radius:8px}h1,h2,h3{line-height:1.3}h2{margin-top:36px}.meta{color:#52625b;font-size:14px}a{color:#126748}blockquote{margin:12px 0;padding:0 16px;border-left:3px solid #96bda7}</style>
<h1>CityWatch</h1><p>New Orleans · Published topics and changes</p><p>An agenda listing does not establish passage. Potential relevance means a broad rule matched, not a confirmed bicycle or pedestrian impact.</p>'''
    page += '<p>Coverage: %d issue(s). See coverage.json.</p>' % issue_count
    page += ''.join(blocks) or '<p>No pending topic alerts.</p>'
    page += '</html>'
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
    message['Subject'] = 'CityWatch: %d topic update(s)' % len(latest)
    message.set_content(plain)
    message.add_alternative(page, subtype='html')
    (out / 'digest.eml').write_bytes(message.as_bytes())
    return out
