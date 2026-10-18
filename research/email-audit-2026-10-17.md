# CityWatch email audit — October 17, 2026

Reviewed all 18 messages returned by the Gmail search `subject:CityWatch`, with no further result pages: 16 production digests, one selected-alert test, and one sender test. The production digests contain **360 displayed entries**. The selected-alert test contributes two more entries but is excluded from the replay counts below. Emails span September 30–October 16 in America/Chicago (October 1–16 UTC).

This is an audit of the evidence actually emailed, not a review of every underlying source document or a production-database inspection. “True positive” here means relevant to the monitor’s transport/urbanism topics; it does not establish passage, implementation, or that an entire document changed. A contextual mention is not automatically a false positive.

## True positives worth keeping

| Example | Why it is relevant | Replay result |
| --- | --- | --- |
| [M-26-438 w-Attachments](https://cityofno.legistar1.com/cityofno/attachments/d313322e-3372-47b2-9a5f-d2741d13fcaa.pdf) | Changes to off-street parking requirements for narrow residential lots and selected zoning districts. | 4 delivered entries retained. |
| [M-26-441 w-Attachments](https://cityofno.legistar1.com/cityofno/attachments/52759469-6635-48ac-ba40-48ed6278dd50.pdf) | Canal Street bike racks and pedestrian space. | 2 delivered entries retained. |
| [35601](https://cityofno.legistar1.com/cityofno/attachments/bae064f2-81b5-475b-afd0-9805191e9109.pdf) | Gravier Street improvements supporting the bicycle plan with dedicated protected lanes. | 5 delivered entries retained. |
| [35578](https://cityofno.legistar1.com/cityofno/attachments/9e31f7e9-3e9b-41ba-8ca5-a0cc32facfcf.pdf) | Extension of the agreement for Andrew Higgins Drive pedestrian improvements. | 12 delivered entries retained. |
| [Health Dept. Presentation](https://cityofno.granicus.com/MetaViewer.php?view_id=&event_id=24803&meta_id=824636) | E-bike rebate/training and roadway injury-prevention programs. | 6 delivered entries retained. |
| [Capital Budget Projects](https://cityofno.granicus.com/MetaViewer.php?view_id=&event_id=24801&meta_id=824443) | Specific sidewalk, protected bikeway, Connecting New Orleans East, and Lafitte Greenway project funding. | 10 delivered entries retained. |
| [ZD NO. 87-26](https://cityofno.legistar1.com/cityofno/attachments/cd8286d3-e1e1-4ab4-bb25-1a771bdb4916.pdf) | Project conditions for bicycle parking, parking waivers, and sidewalk improvements. | 26 delivered entries retained. |

## False positives and tuning

These are false or unsupported **match reasons** in the excerpts, not judgments that the entire source document is irrelevant.

| Noise | Why it fired | Change |
| --- | --- | --- |
| Houston energy-invoice addresses | `20 Greenway Plaza` and `20 East Greenway Plaza` matched “greenway.” | Ignore that address pattern; keep other greenway mentions on the same page. |
| Discarded-belongings inventory | A bike appears among shoes, blankets, toys, and trash. | Exclude the inventory mention, while keeping bike racks, bike parking, and separate transport text. |
| NOPD drone-flight logs | Routine event surveillance and dispatch notes mention pedestrians/sidewalks. | Exclude walking matches in documents explicitly titled as drone-flight logs. Announcements and other rules are unaffected. |
| Generic zoning-review sentence | “Any zoning text … amendment” appears in the standard legislative-discretion statement. | Exclude that procedural phrase; retain substantive parking/zoning evidence elsewhere. |
| Blank application checklist | “Greenway Corridor / Others as required” is an option on a form, not evidence that it applies. | Exclude this specific form-label sequence. |
| Roof-access details | Roof walkway pads matched public walking infrastructure. | Exclude explicit roof-walkway pad/detail phrases. |

Exclusions operate on overlapping match spans, rather than removing the rest of the page. The title exclusion is limited to document records and the walking rule. Configured exclusions participate in rule versioning, pending-alert refiltering, and previous-evidence checks. Prepared delivery batches remain immutable.

## Replay results

Each original emailed excerpt was passed through the repository rules before this task and through the tuned rules, retaining its document title and evidence type. An entry is retained if any of its delivered excerpts still matches. This is an **excerpt replay**, not a full source-page rescan; unseen text on a source page could change the result.

- **72 of 360** entries already stop matching under the existing narrowed rules (mostly the earlier broad capital-budget, right-of-way, and routine land-use matching).
- **29 additional entries** stop matching with this task’s exclusions.
- **259 entries** still match. This is not a claim that all 259 are high-value true positives.
- In the October 16 digest, the replay changes **85 entries to 73** before document grouping.
- The seven positive example families above retain all their delivered entries.

| Digest sent (UTC) | Entries | Already excluded | Newly excluded | Still matching |
| --- | ---: | ---: | ---: | ---: |
| 2026-10-01 04:18 | 81 | 59 | 0 | 22 |
| 2026-10-01 05:39 | 25 | 13 | 2 | 10 |
| 2026-10-01 06:07 | 22 | 0 | 2 | 20 |
| 2026-10-01 07:20 | 1 | 0 | 0 | 1 |
| 2026-10-01 18:28 | 5 | 0 | 0 | 5 |
| 2026-10-01 19:43 | 9 | 0 | 0 | 9 |
| 2026-10-01 20:56 | 20 | 0 | 0 | 20 |
| 2026-10-03 05:54 | 32 | 0 | 2 | 30 |
| 2026-10-04 06:47 | 5 | 0 | 0 | 5 |
| 2026-10-07 00:32 | 3 | 0 | 0 | 3 |
| 2026-10-08 22:57 | 2 | 0 | 0 | 2 |
| 2026-10-09 22:59 | 8 | 0 | 0 | 8 |
| 2026-10-13 00:08 | 1 | 0 | 0 | 1 |
| 2026-10-14 01:04 | 60 | 0 | 11 | 49 |
| 2026-10-15 20:28 | 1 | 0 | 0 | 1 |
| 2026-10-16 20:45 | 85 | 0 | 12 | 73 |

## Context-only matches still needing judgment

The following are genuine topic mentions but do not, by themselves, establish a new public transport or land-use policy change:

- Existing bus stops/streetcars near short-term-rental applications, including a stop described as 15 miles from a property.
- Master Plan walkability boilerplate and generic descriptions of existing sidewalks, private walkways, and building frontage.
- Blank drawing-submission checklists listing pedestrian improvements as required information.
- Routine accounting balances, including $0.05 in a sidewalk fund.
- Site-plan bicycle-parking requirements or sidewalk conditions. Some are useful, binding project conditions; blanket suppression would hide real accessibility and parking changes.

These have not been broadly suppressed. The email groups them by source document to reduce repetition. A future action-versus-context classification would need the full source text and a labeled validation set; a universal “must contain an action verb” filter would miss meaningful drawings, funding rows, and permit conditions.

## Waymo and collection gaps

No email in the retrieved set contains the Waymo/robotaxi proposal the user confirmed: the October 16 report about JP Morrell’s proposed fees and safety rules. The pre-task rules had no explicit Waymo, robotaxi, driverless, autonomous-vehicle, or automated-driving-system match. Those terms are now explicitly watched.

The news report is [New Orleans proposal would impose robotaxi fees and safety rules](https://techandbusiness.org/newswire/eWQh86HppgluEEdwbqkG-F). The [Council calendar’s October 27 committee page](https://council.nola.gov/meetings/2026/committees/governmental-affairs/20261027-joint-economic-dev-ga-pw/) lists a 10:00 AM meeting. At inspection, its agenda link opened a September 10 agenda, so that linked agenda does not verify the proposed October 27 hearing item.

All 16 production digests were marked coverage incomplete, reporting **156–406 collection issues**. These counts are collection issues, not counts of missed meetings. Independent read-only checks during this audit found:

- The configured Council RSS URL returned HTML instead of RSS, which the current collector cannot parse.
- The Legistar API’s October event query did not include the October 15 meeting, although the public calendar and delivered emails showed it. This observation does not prove what the API returned during earlier scans.
- The accessible October 15 agenda and Council PDF did not name Waymo or autonomous vehicles; first reading was only a generic agenda heading.

Therefore, the email history supports **no delivered Waymo alert**, but does not prove whether production collected the proposal without matching it or failed to collect it. Adding a topic rule does not repair a collection failure. A production coverage/database review and Council feed fallback remain necessary to establish reliable coverage. False negatives/recall cannot be measured from delivered emails alone.

## Formatting and validation

The revised email consolidates document pages, merges repeated evidence (including bullet/hyphen-spacing variants), keeps page and OCR provenance, formats dates in Central Time, and uses inline email styles. HTML contains up to three excerpts per update with an explicit remaining count and source link. Plain text retains the full set of distinct quoted evidence; `outbox.json` retains every original record and match.

On the October 16 content, grouping alone reduces 85 repeated headings to 25 document/item updates. Filtering and grouping together leave 20 sections. The original delivered HTML was about 90 KB; the formatted preview before rule filtering was about 44 KB, and the tuned preview about 37 KB. Desktop and 390/320-pixel phone previews had no horizontal overflow. Arial is deliberate for email-client compatibility, despite the design detector’s generic font warning. Browser checks are not an Outlook/Gmail rendering-service certification.

Tests cover false-positive examples, retained true-positive examples, mixed pages, rule-change replay, removal/refilter behavior, safe immutable retries, email evidence grouping, escaping, and MIME alternatives. The workspace lacks Poppler and Tesseract, so its PDF/OCR test cannot pass there. Running the updated code and complete suite in an isolated production-image container with both dependencies installed passed **all 45 tests**.

This audit records the evidence and pre-deployment validation. Deployment and production-data replay results are recorded separately. A rule change re-evaluates previously observed records when they are collected again; it does not recover uncollected or out-of-window material.

Subsequent verification: [release and backfill report](release-status-2026-10-17.md) records the deployed release, the 10,291-record production snapshot replay, zero additional alerts on repetition, and the narrow vehicle-location exclusion discovered by that replay.
