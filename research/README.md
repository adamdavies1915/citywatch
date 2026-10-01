# Source research and implementation decisions

Research date: September 30, 2026. This is the pre-implementation research record; see the root README for current implementation status. This document describes the proposed application and tested source access, not a running monitoring service. Saved `.raw` responses and the audit script make the direct HTTP checks reproducible; the fetch manifest records retrieval time, status, URL, and content hash.

## Main conclusion

Build around **meetings, agenda items, and their linked documents**, with deterministic relevance rules. A single keyword search of announcement pages or a single Legistar integration would leave important gaps. Published subjects can provide useful advance notice without downloading recordings; they cannot establish everything actually spoken at a meeting.

## Verified sources

| Source | What it contributes | Integration decision |
| --- | --- | --- |
| [Council calendar](https://council.nola.gov/meetings/) and [RSS](https://council.nola.gov/meetings/?rss=events) | Full Council, committees, joint committees, dates, descriptions, stable feed GUIDs | Primary Council discovery; follow each meeting page for agenda links and changes |
| [Council committees](https://council.nola.gov/committees/) | Upcoming and historical committee meetings | Reconcile against the central feed rather than relying on committee names as a relevance filter |
| [Legistar API](https://webapi.legistar.com/v1/cityofno/events?$orderby=EventDate%20desc&$top=15) | Structured Council agenda items, status fields, identifiers, attachment references | Enrich discovered meetings; incomplete as the sole discovery source |
| [Granicus committee agenda example](https://cityofno.granicus.com/GeneratedAgendaViewer.php?event_id=25006&view_id=42) | Item text plus linked presentations and proposed legislation | Follow the meeting's agenda link and preserve item-to-attachment relationships |
| [Council news](https://council.nola.gov/news/) | Official post-meeting summaries and announcements | Separate evidence type; crawl pagination and article bodies |
| [Mayor announcements](https://nola.gov/next/mayor/news/) and [Public Works announcements](https://nola.gov/next/public-works/news/articles/) | Administrative decisions and street-project updates | Separate collectors with article-level dates, text, and attachment discovery |
| [City Planning meetings](https://nola.gov/next/city-planning/meetings/) | CPC, BZA, Design Advisory and Planning Advisory notices/materials | Preserve body/date headings when associating documents |
| [Planning Granicus archive](https://cityofno.granicus.com/ViewPublisher.php?view_id=2) | Agendas, staff reports, available vote summaries, recordings | Complement the Planning page; follow agenda links for staff reports |
| [RTA calendar](https://norta.legistar.com/) | Transit board and committee meetings | Valuable next source; public `norta` API also passed a direct read test |

Public Works was identified through its official website; its end-to-end collector has not been tested. Likewise, discovery of Planning and Granicus links does not establish complete attachment extraction coverage.

## Direct HTTP findings that change the design

**Council RSS is particularly useful.** The saved response contains 1,936 entries, including historical meetings, not merely upcoming events. Committee descriptions can include the numbered agenda. `pubDate` corresponds to the scheduled meeting time in the inspected examples, including future meetings. Track `first_seen_at`, `last_seen_at`, source meeting time, and content revision independently. Do not treat `pubDate` as announcement publication time. [Feed](https://council.nola.gov/meetings/?rss=events).

**Legistar works, but is incomplete.** Anonymous requests succeeded. The body list returned City Council and Alcoholic Beverage Control Board, not the standing committees. The 15 latest event results also omit September 17 and September 22 meetings listed on the Council site. This observation is a coverage discrepancy, not proof of why records are missing. Match sources using explicit links/IDs where possible and body/date/time otherwise, retaining unresolved discrepancies for review. [Bodies endpoint](https://webapi.legistar.com/v1/cityofno/bodies), [Council calendar](https://council.nola.gov/meetings/).

**Agenda details are available as structured data.** Event 1016, October 1, returned 132 records including headings and 90 attachment references. Event-list `EventItems: []` does not mean an empty agenda: the separate event-items request is needed. Use `AgendaNote=1&MinutesNote=1&Attachments=1`. API lists require pagination; the vendor documents a 1,000-result cap. [Item endpoint](https://webapi.legistar.com/v1/cityofno/events/1016/eventitems?AgendaNote=1&MinutesNote=1&Attachments=1), [vendor documentation](https://webapi.legistar.com/Home/Examples).

**Parent timestamps are insufficient.** In the saved sample, event 1016's last-modified timestamp precedes an item's last-modified timestamp. Recheck child items and attachment content; a parent timestamp alone must not suppress collection. The API's human-readable agenda numbers also repeat across sections, so use stable item IDs plus agenda sequence, not the printed number alone. [Item API documentation](https://webapi.legistar.com/Help/Api/GET-v1-Client-Events-EventId-EventItems_AgendaNote_MinutesNote_Attachments).

**HTML extraction needs explicit boundaries.** City pages contain sitewide menus and unrelated news, including bicycle/sidewalk navigation labels. Matching the full HTML would create false positives. Council meeting HTML includes unquoted link attributes; use an HTML parser, not a quoted-href regex. Planning pages contain draft/final/revised documents and occasional suspicious times such as 1:30 AM. Preserve source values and flag conflicts rather than silently correcting them. [Planning page](https://nola.gov/next/city-planning/meetings/).

## Real cases for validation

1. **September 23, Climate Change and Sustainability, item 4:** Lafitte Greenway improvements and completion of its final half-mile. The HTML agenda links a presentation. The subsequent official summary confirms a presentation occurred, but does not establish an adoption or funding decision. Expected: an agenda alert, followed by a separately labeled official-summary update. [Agenda](https://cityofno.granicus.com/GeneratedAgendaViewer.php?event_id=25006&view_id=42), [summary](https://council.nola.gov/news/september-2026/climate-change-and-sustainability-committee-meetin/).
2. **August 27, Transportation:** fare-free youth transit, e-bike safety, and the Connecting New Orleans East project were listed. Expected: cycling/transit matches and a named-project match. This also illustrates why the full meeting title is insufficient. [Meeting](https://council.nola.gov/meetings/2026/committees/transportation-and-airport/20260827-transportation/).
3. **October 1, Council, ordinance calendar 35,578:** the API lists an agreement amendment concerning pedestrian walkways along Andrew Higgins Drive. Expected: a walking alert with item 21203, meeting date/time, ordinance identifier, and linked document. This is a proposed agenda matter, not evidence of passage. [Meeting](https://cityofno.legistar.com/MeetingDetail.aspx?LEGID=1016&GID=954&G=F14BC644-99E6-4018-812A-B756E5E700C0).
4. **September 22, CPC:** the agenda links reports and a transfer-of-development-rights study. Expected: land-use policy coverage and document extraction even without bicycle terminology. [Agenda](https://cityofno.granicus.com/GeneratedAgendaViewer.php?clip_id=5588&view_id=2).

The illustrative audit matcher found two matching RSS meetings within 60 days on either side of September 30, and two matching records in the October 1 agenda. These are narrow-rule examples, not a completeness or recall measurement.

## Recommended alert behavior

Use versioned, editable rules with word boundaries, punctuation normalization, phrases, and explicit project aliases. Initial categories:

- Direct: bicycle/bike/e-bike/bikeway, pedestrian, sidewalk, crosswalk, greenway, shared-use path, complete streets, traffic calming, safe routes.
- Transit: bus lanes, bus stops, streetcars, transit service, fare policy, accessibility, first/last-mile connections.
- Land use: parking minimums, mixed-use, multifamily housing, density, adaptive reuse, development rights, zoning text amendments.
- Potential relevance: resurfacing, pavement markings, curb ramps, right-of-way, capital budgets, bridge or corridor reconstruction. Put these in a lower-priority digest rather than presenting inferred bike benefits as facts.
- Project watchlist: Lafitte Greenway and Connecting New Orleans East initially; expand from reviewed local project names. A street-name address alone should not imply an infrastructure project.

Each alert needs meeting/body/date/time and timezone, item or ordinance ID, evidence type, exact passage, matched rule, source URL, attachment page if applicable, and what changed. Link to the official participation information when provided; do not infer deadlines. Distinguish scheduled, revised, cancelled, minutes published, and official summary published. Treat source claims as source claims.

Recommended default: hourly discovery, a daily digest, prompt notification of newly relevant upcoming agenda items and cancellations. Start with a quiet baseline for historical records plus an explicit upcoming-meeting preview so initialization neither floods the inbox nor hides imminent meetings. These are proposed defaults, not installed schedules.

## Architecture and reliability requirements

Use Python collectors, SQLite, cached raw responses, normalized document text, a deterministic rule engine, and an SMTP outbox. No LLM relevance classifier is required. Keep the relationships `meeting -> agenda item -> document revision -> rule match -> notification`.

Store raw and normalized hashes, fetch times, parser/rule versions, and per-source health. Persist an alert before sending; acknowledge it only after successful delivery. Standard SMTP cannot guarantee exactly-once delivery after a crash between server acceptance and local acknowledgement; a stable message ID and delivery log help investigate duplicates.

Revisit recent past meetings for minutes and late attachments, and upcoming meetings for revisions. Deduplicate identical documents while retaining every meeting/item relationship. A repeated boilerplate match should not resend an unchanged alert, but a cancellation or relevant text change should.

PDF failures and image-only pages must be visible coverage failures, not empty successful scans. Add OCR where needed, retain page references, and test against actual attachments before claiming coverage. Bound download sizes, retry transient errors with backoff, limit request concurrency, and report missing expected source structures. Never use disappearance alone as proof of cancellation.

Before enabling email: replay the cases above, test a changed attachment at the same URL, a removed agenda item, a cancellation, a blank/unpublished agenda, a failed source, an image-only PDF, repeated runs, and SMTP failure/retry. Verify identical saved input and rule version reproduce identical matches.

## Existing services and remaining gaps

[NoticeMe](https://noticeme.nola.gov/about) already offers geographic land-use notifications and reports that it does not notify users of Council agendas. Council and committee pages also offer GovDelivery subscriptions. These are useful complements, but do not replace topic matching across Council, committees, administrative announcements, and document revisions.

Recording links exist in Granicus and some Council summaries. This research did not verify reliable caption availability, timestamp alignment, or transcription quality. Subjects raised only in discussion or public comment remain outside the documents-first release. The existing Whisper material can support that later phase.

No production collector, email account, recipient, deployment destination, or recurring job has been configured. The next implementation should begin with Council RSS plus meeting-page and Granicus attachment traversal, using Legistar as enrichment rather than the sole source.
