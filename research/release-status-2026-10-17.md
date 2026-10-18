# Release and backfill verification — updated October 18, 2026

The email formatting and initial matching release, `791a7b8b5b9906942531ffeed21e1b845b52efe2`, is verified running on the existing Coolify application and persistent volume. The earlier host connection failure cleared after a host restart. [CI passed for that release](https://github.com/adamdavies1915/citywatch/actions/runs/39074050277).

## Production-record backfill replay

A consistent SQLite backup was taken from production and replayed in an isolated container with network access disabled. Every latest stored record was re-observed using the new matcher; the production database, delivery batches, and email acknowledgements were not changed by the replay. This is a **full stored-record replay**, not a re-fetch or raw-document parser replay.

| Check | Result |
| --- | ---: |
| Latest stored records replayed | 10,291 |
| First-pass new alerts with the final rules | 10 |
| Second-pass additional alerts | **0** |
| Pending alerts after both passes | 10 |
| Previously matched records no longer matching | 13 |
| Records containing Waymo, robotaxi, robo-taxi, or robo taxi | 0 |

The ten new alerts concern airport **Autonomous Vehicle System** capital projects, across Council and Council-news copies of the documents. They are not evidence of the Waymo proposal. The first replay also found two false matches on the fire department's “Automated Vehicle Location Dispatching.” A narrow exclusion now removes that phrase while retaining actual autonomous-vehicle and Waymo mentions on the same page. The final replay above includes this correction.

The production coverage report available with the snapshot finished October 17 at 23:23 Central and reported **146 issues**: Council 1, Legistar 44, Planning 5, Council news 92, and Mayor news 4. The Council RSS parse failure remains unresolved. Absence from stored evidence does not establish absence from official business, and this replay cannot recover material that was never collected.

The detailed local result is `var/production-replay/report.json`. The isolated remote backup and replay outputs are under `/tmp/citywatch-release-review`; these temporary files may disappear on host restart. No replay email was sent.

## Slack digest release validation

The approved first version posts daily digests to Bike Easy's **#policy-design**, keeps email delivery independent, and has no interactive replies or Slack message-reading permissions. App installation uses [`config/slack-app.json`](../config/slack-app.json) and [the setup guide](../docs/slack-setup.md).

- **All 64 tests passed** in the final container image, including PDF/OCR dependencies and the new rule correction.
- Compose configuration validated. The isolated validation intentionally did not supply production Mailjet credentials.
- Slack tests cover independent email acknowledgement, channel enrollment without historical replay, restarts, partial threads, persistent daily cadence, rate limits, ambiguous acknowledgements, manual recovery, immutable retries, source-text escaping, message limits, and workspace verification.
- The tuned October 16 email's 73 record entries render as one Slack summary plus 20 source-detail messages. All messages fit Slack's block/text limits; the longest notification fallback in this sample is 1,301 characters.
- Slack defaults to disabled until the posting-only app is installed, invited to the channel, and its bot token is stored in Coolify. A live Slack post has not been tested without that token.
