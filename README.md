# CityWatch

A deterministic monitor for New Orleans public meetings and announcements about cycling, walking, transit, and urban development.

**The first version collects sources, tracks changes, produces email previews, and supports Mailjet delivery.** Scanning and previewing do not send email. The explicit `send` command does. The hosted instance runs on Coolify with persistent storage and email enabled, waiting one hour between completed scans and sending at most one digest per 24 hours.

## Run

Python 3.9+ on macOS or Linux. Optional OCR uses local `pdftoppm` (Poppler) and `tesseract` executables. On a new machine, install them separately or set `"ocr": false` in the config:

```sh
uv venv .venv --python python3
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python -m citywatch scan
.venv/bin/python -m citywatch status
.venv/bin/python -m citywatch preview
```

Configuration and editable topic rules: [`config/watch.json`](config/watch.json).

Outputs in `var/preview/`:

- `digest.html`: readable email preview, grouped by meeting or article.
- `digest.txt`: plain-text version.
- `digest.eml`: multipart email draft with no recipients.
- `outbox.json`: provider-neutral pending notification records.

`var/coverage.json` lists collection failures, unreadable PDF pages, unpublished agendas, pagination limits, and source discrepancies. Exit code **0** means collection completed without reported issues; **2** means coverage is incomplete (or another scan holds the lock). Valid collected material is still saved and previewed. A zero-match result is not a coverage guarantee.

## Sources

- Council RSS plus calendar reconciliation, individual committee/full-Council pages, Granicus agendas and their attachments.
- Legistar Council agenda items, supporting documents, published agendas and minutes.
- City Planning meeting groups and their linked PDFs, including BZA and advisory bodies.
- Council news and meeting summaries, Mayor announcements, Public Works announcements.

The [research document](research/README.md) records the source investigation and design. Legistar alone is incomplete, so the Council feed is also collected. Calendar entries missing from RSS are flagged for review. Meeting and document publication gaps are retained as coverage issues.

## Alert behavior

The first scan quietly records historical material and queues relevant upcoming meetings. To preview historical matches too, use a **separate state directory**:

```sh
.venv/bin/python -m citywatch scan --state var/review --include-history
```

`--baseline` explicitly records everything without queuing alerts. It should only be used for intentional initialization; it consumes the revisions it observes. `--include-history` affects first observations, not already-baselined records.

Subsequent scans queue changes to matching evidence, dates, status, or titles. Removed matching text retains its previous evidence and is labeled as removed, not as proof of cancellation. Successfully parsed Council/Legistar item lists are checked for items no longer listed. Unchanged content and unrelated paragraphs do not create duplicate alerts. Changing the rule set re-evaluates collected records on the next scan.

Rules use exact regular expressions, with Unicode/punctuation and whitespace normalization. Excerpts quote normalized source text, not generated summaries. Direct and policy matches remain labeled separately. Generic capital budgets, right-of-way, resurfacing, mixed-use, and multifamily mentions no longer trigger alerts on their own. Land-use alerts focus on parking requirements, zoning text, development rights, density policy, and transit-oriented development. Pending alerts are rechecked against current rules before delivery; already-prepared delivery batches retain their original content for safe retries. Agenda entries do not establish passage; official summaries remain a distinct evidence type.

SQLite stores current records, revision history, matches, and the pending outbox. Raw response bytes are saved by SHA-256 with URL/time metadata. Previewing does **not** mark anything sent. Multiple observations of a pending item are condensed to its latest version in the preview; its outbox history remains available for the future delivery adapter. Cross-source evidence may still repeat where wording differs.

## Verify and replay

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Replay the same downloaded responses without network access:

```sh
.venv/bin/python -m citywatch scan --config var/validation-config.json \
  --state var/replay --replay var/validation --as-of 2026-09-30 --include-history
```

Use the same config, sources, and date as the original collection so request URLs match. Missing or modified saved evidence fails explicitly. Repeating a replay against the same state should queue zero additional alerts.

The original read-only research audit remains available:

```sh
python3 scripts/audit_sources.py
# After downloading the source snapshots:
python3 scripts/audit_sources.py --offline
```

## Scheduling and email integration

`scripts/run_monitor.sh` is a scheduler-ready entry point. For an hourly local collection, a scheduler can invoke it with the absolute project path; the hosted deployment uses the container worker instead. The process lock prevents overlap. Keep `var/` on persistent storage and review coverage failures. No server, cloud service, or subscription is needed to preview locally.

Mailjet credentials and sender/recipient settings are loaded from environment variables or an ignored `.env`; see `.env.example`. Secrets are excluded from the Docker build context.

```sh
.venv/bin/python -m citywatch check-email   # Mailjet sandbox; no delivery or acknowledgement
.venv/bin/python -m citywatch send          # Actually sends the pending digest
```

Mailjet batches are saved before sending and retried with identical content, a stable campaign ID, and Mailjet's duplicate-suppression flag. Outbox rows are acknowledged only after Mailjet accepts the request. A network timeout retains the batch; review the provider's status before retrying ambiguous failures. Acceptance is not proof of inbox delivery. Unit tests cover sandbox behavior, failure preservation, stable retries, and acknowledgement. The sender must be an active, verified address in the Mailjet account, displayed as CityWatch. API acceptance alone does not establish sender validation or delivery; verify sender status before activation.

`Dockerfile` and `compose.yaml` prepare a background worker for Coolify or Docker Compose. It has no public port. Persist `/app/var`; configure the Mailjet environment variables and set `CITYWATCH_SEND_EMAIL=true` to enable daily digest delivery. The default is scan-only. The container build and first worker startup have been verified on Coolify. The named `citywatch-data` volume retains the database, delivery batches, extracted text, and evidence across restarts.

## Current limits

- No recording transcription or spoken-only coverage.
- Sparse/image-based PDF pages are OCRed locally when tools are available. OCR excerpts are labeled and may contain recognition errors. Native text extraction prefers Poppler when installed, with pypdf as a fallback. PDF parsing is limited to 500 pages, bounded content streams, a per-document OCR budget, and a 200-second worker timeout. Unreadable or blank pages are flagged conservatively. Extracted text is cached by raw document hash, parser version, and OCR setting.
- Planning includes linked documents on the city meetings page; separate archive-only staff reports are not yet collected. RTA is researched but not enabled.
- PDF tables and multi-column layouts can produce imperfect text order. Page numbers are physical PDF pages. A match split across pages can be missed.
- Removal checks apply to successfully parsed Council and Legistar agenda-item lists. Document disappearance, page-number shifts, and cross-system meeting identity are not fully reconciled. A missing item is not interpreted as a vote or cancellation.
- The default lookback is 45 days and lookahead is 60 days. Document/news limits are explicit and produce coverage issues rather than silent truncation. Increase them for a broader initial collection.
- Downloads have a total per-attempt deadline and bounded retries. Each run re-fetches its window; there is in-run caching and content-addressed storage, but no conditional HTTP caching yet.
- The hosted worker collects changes, then waits 3,600 seconds. Delivery is limited to one attempt per 24 hours using persistent database state (`CITYWATCH_EMAIL_INTERVAL_SECONDS=86400`); restarts and ambiguous timeouts do not bypass this limit. Pending changes accumulate into the next digest. Existing installations receive a full 24-hour quiet period when this limit is first applied. Initial scans and OCR can take longer; this is not a fixed hourly deadline. A corrected test using a verified sender reached Mailjet status `sent`; the original unverified sender was replaced.
- DOCX and PPTX text is extracted, including mislabeled downloads. Images inside Office files and spreadsheet attachments are not yet analyzed; unsupported formats are reported.

`work/` retains the earlier manual research and Whisper checkout. It is not scanned by the application.
