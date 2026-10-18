# Slack digests for Bike Easy

CityWatch posts a daily summary and threaded source details to **#policy-design** in **bikeeasy.slack.com**. This version does not read Slack messages or respond to commands. It runs independently of email delivery. The delivery and formatting modules can support an interactive bot later without changing collection.

## Install the app

1. Open [Slack app creation](https://api.slack.com/apps?new_app=1), choose **From a manifest**, and select the **Bike Easy** workspace.
2. Paste the JSON from [`config/slack-app.json`](../config/slack-app.json). Review and create the app.
3. Under **OAuth & Permissions**, choose **Install to Workspace**. The only requested permission is `chat:write`.
4. In **#policy-design**, invite **CityWatch** using the channel's Integrations tab or `/invite @CityWatch`.
5. Save the **Bot User OAuth Token** (`xoxb-…`) as `SLACK_BOT_TOKEN` in CityWatch's Coolify environment variables. Do not put it in Git or chat. For local setup, the ignored project `.env` file is also supported.

Use these destination settings:

```dotenv
CITYWATCH_SLACK_TEAM_ID=T031RQ3EFAP
CITYWATCH_SLACK_CHANNEL_ID=C08M32G5XDL
CITYWATCH_SLACK_INTERVAL_SECONDS=86400
CITYWATCH_SLACK_ENABLED=true
```

The worker verifies the bot token belongs to this workspace before registering a subscription or posting. The bot must be invited to the channel; it has no channel-discovery or history-reading permission. Slack may require workspace administrator approval under the workspace's app policy.

## Verify and activate

With the token stored in `.env`, this verifies workspace identity without sending anything:

```sh
.venv/bin/python -m citywatch.slack_service check
```

In Coolify, set the settings above, save them, and redeploy the existing CityWatch application. The `citywatch-slack` service uses the existing persistent data volume. `CITYWATCH_SLACK_ENABLED` defaults to `false`, so deploying the code before installing the app is safe. No public domain, request URL, Socket Mode, signing secret, or app-level token is needed.

A new channel subscription starts with **future alert revisions**. It does not repost historical email archives. Restarting or redeploying preserves its cursor. The first digest is sent after a completed collection has new matching revisions; subsequent new digests are at least 24 hours apart. Each digest covers at most 500 queued revisions; further revisions remain for later digests. Thread replies continue even if delivery crosses the daily boundary.

The Slack queue does not use email's sent/suppressed flag. It applies the current rules independently until a batch is prepared. A prepared batch freezes its content. Source pages are grouped, excerpts are bounded, and links/page references preserve the route back to full evidence. Collection gaps are shown in every summary.

## Operations

These local commands do not post:

```sh
.venv/bin/python -m citywatch.slack_service status
.venv/bin/python -m citywatch.slack_service preview > /tmp/citywatch-slack-preview.json
```

Before activation, preview shows pending email evidence for review; it does not register a subscription. After activation it shows matching evidence in the next Slack queue window. If a batch is already prepared, preview uses its immutable message payloads. The local preview shows at most five source updates; the scheduled digest includes all updates in the batch.

Slack records each message acknowledgement separately. Confirmed messages are not reposted after restart. Explicit API refusals and HTTP 429 responses retry after a persisted delay; 429 uses Slack's `Retry-After`. Network timeouts, server errors, incomplete acknowledgements, or a process crash during posting pause that destination for operator review. This avoids assuming that Slack rejected a message when it may already have accepted it.

If `status` shows `needs_review`, inspect the configured channel and thread. Use the batch ID and position from the status output. If the message exists, record its Slack timestamp (not its permalink):

```sh
.venv/bin/python -m citywatch.slack_service resolve --batch BATCH_ID --position 0 --ts 1234567890.123456
```

Only after confirming it does not exist, allow the frozen message to be retried:

```sh
.venv/bin/python -m citywatch.slack_service resolve --batch BATCH_ID --position 0 --confirmed-absent
```

These are host/operator commands, not Slack commands. They acquire the same delivery lock as the sender. Never edit delivery rows directly or reset the subscription cursor to recover a timeout. Message IDs help diagnosis, but the service does not promise exactly-once delivery across an ambiguous provider response.

To disable Slack delivery, set `CITYWATCH_SLACK_ENABLED=false` and redeploy. Email and collection continue. Re-enable the same destination to resume its queue. Changing the destination creates a new subscription at the current tail; unresolved batches for an old destination remain recorded and require review.

Platform references: [bot posting](https://docs.slack.dev/reference/methods/chat.postMessage/), [app manifests](https://docs.slack.dev/reference/app-manifest/), and [rate limits](https://docs.slack.dev/apis/web-api/rate-limits/).
