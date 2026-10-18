"""Bounded Slack messages built from the same evidence groups as email."""
import html
import json
from urllib.parse import urlsplit
from .preview import coverage_summary, grouped_updates, readable_date, references_label, topic_label


def clipped(value, limit):
    value = str(value)
    return value if len(value) <= limit else value[:limit - 1] + '…'


def section(text):
    # Source text is plain_text, so documents cannot inject mentions or markup.
    return {'type': 'section', 'text': {'type': 'plain_text', 'text': clipped(text, 3000) or 'No details available.', 'emoji': False}}


def header(text):
    return {'type': 'header', 'text': {'type': 'plain_text', 'text': clipped(text, 150) or 'Source update', 'emoji': False}}


def source_link(url):
    try:
        parts = urlsplit(url)
        valid = parts.scheme in ('http', 'https') and parts.hostname and not parts.username
    except ValueError:
        valid = False
    if not valid or len(url) > 1800 or any(c in url for c in '<>|\r\n'):
        return section('Source link unavailable.')
    return {'type': 'section', 'text': {'type': 'mrkdwn', 'text': '<%s|Read official source>' % html.escape(url, quote=False),
                                      'verbatim': True}}


def coverage_text(report):
    label, details = coverage_summary(report)
    finished = report.get('finished_at') if report else None
    lines = [label, 'Last completed scan: ' + (readable_date(finished) if finished else 'unavailable')]
    lines.extend(clipped(detail, 250) for detail in details[:3])
    return '\n'.join(lines)


def digest_messages(rows, report):
    groups = grouped_updates(rows)
    count = sum(len(updates) for _, updates in groups)
    summary = '%d source update%s in this digest. Details and evidence are in this thread.' % (count, '' if count == 1 else 's')
    parent = {'text': clipped(html.escape('CityWatch — New Orleans\n' + summary + '\n' + coverage_text(report), quote=False), 3900),
              'blocks': [header('CityWatch · New Orleans'), section(summary), section(coverage_text(report)),
                         section('An agenda listing does not establish passage. Source links and page references are included below.')]}
    messages = [parent]
    for (when, meeting), updates in groups:
        for update in updates:
            p = update['payload']
            excerpts = update['excerpts']
            tags = list(dict.fromkeys(tag for details in excerpts.values() for tag in details['tags']))
            context = [clipped(meeting, 250), readable_date(when), update['change'].replace('_', ' ').capitalize()]
            if p['item']:
                context.append('Item ' + clipped(p['item'], 80))
            if p['status']:
                context.append('Source status: ' + clipped(p['status'], 200))
            context.append(clipped(topic_label(tags), 500))
            blocks = [header(p['title']), section('\n'.join(context))]
            evidence = []
            for quote, details in list(excerpts.items())[:3]:
                text = clipped(quote, 650)
                reference = clipped(references_label(details['references']), 200)
                if reference:
                    text += '\n' + reference
                evidence.append(text)
            if update['change'] == 'matching_text_removed':
                evidence.insert(0, 'Previously matching text is no longer present; this does not establish cancellation.')
            blocks.append(section('\n\n'.join(evidence) or 'No excerpt available.'))
            if len(excerpts) > 3:
                blocks.append(section('%d additional matching excerpts; see the source for full context.' % (len(excerpts) - 3)))
            blocks.append(source_link(p['url']))
            # The fallback is escaped as well: notification text may parse mrkdwn.
            fallback = '\n'.join([clipped(p['title'], 180), *context, *evidence, clipped(p['url'], 500)])
            messages.append({'text': clipped(html.escape(fallback, quote=False), 3900), 'blocks': blocks})
    return messages


def digest_preview(rows, report):
    if not rows:
        return {'text': html.escape('No pending Slack updates.\n' + coverage_text(report), quote=False),
                'blocks': [section('No pending Slack updates. This is not proof of no relevant activity.'), section(coverage_text(report))]}
    messages = digest_messages(rows, report)
    return preview_messages(messages)


def preview_messages(messages):
    parent = {'text': 'CityWatch digest preview.',
              'blocks': [header('CityWatch · Digest preview'), *messages[0]['blocks'][1:]]}
    for detail in messages[1:6]:
        parent['blocks'].extend([{'type': 'divider'}, *detail['blocks']])
    if len(messages) > 6:
        parent['blocks'].append(section('%d more source updates are queued for the daily digest.' % (len(messages) - 6)))
    return parent


def latest_report(db):
    row = db.execute('SELECT report FROM runs WHERE finished_at IS NOT NULL AND report IS NOT NULL ORDER BY id DESC LIMIT 1').fetchone()
    return json.loads(row['report']) if row else None
