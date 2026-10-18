import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import patch
from slack_sdk.errors import SlackApiError
from slack_sdk.web.slack_response import SlackResponse
from citywatch.core import Store, Matcher, Record
from citywatch.mail import send
from citywatch.slack_delivery import register, tick, resolve, pending_rows
from citywatch.slack_format import digest_messages, digest_preview, source_link
from citywatch.slack_service import destination, make_client

CONFIG = json.loads((Path(__file__).resolve().parents[1] / 'config/watch.json').read_text())


class Client:
    def __init__(self):
        self.calls = []
        self.failure = None

    def chat_postMessage(self, **payload):
        self.calls.append(payload)
        if self.failure:
            raise self.failure
        return {'ok': True, 'channel': payload['channel'], 'ts': '100.%06d' % len(self.calls)}


def api_error(status, code, headers=None):
    response = SlackResponse(client=None, http_verb='POST', api_url='https://slack.com/api/chat.postMessage',
                             req_args={}, data={'ok': False, 'error': code}, headers=headers or {}, status_code=status)
    return SlackApiError('simulated API response', response)


class SlackDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.matcher = Matcher(CONFIG['rules'])
        self.target = register(self.store.db, 'TTEST', 'CTEST')
        self.client = Client()
        self.record = Record('bike', 'council', 'Bike lanes', 'https://council.nola.gov/example', 'document',
                             'Add protected bike lanes.', page=2, meeting='City Council', when='2026-10-22')
        self.today = date(2026, 10, 17)

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def observe(self, record=None):
        self.store.observe(record or self.record, self.matcher, self.today)

    def tick(self, now=100000, matcher=None):
        return tick(self.store, self.target, matcher or self.matcher, self.client, now=now)

    def reopen(self):
        self.store.db.close()
        self.store = Store(self.tmp.name)

    def deliver(self, now=100000):
        result = self.tick(now)
        while result['status'] == 'posted':
            result = self.tick(now + 2)
        return result

    def email(self):
        settings = dict(MAILJET_API_KEY='fake', MAILJET_SECRET_KEY='fake', CITYWATCH_EMAIL_INTERVAL_SECONDS='0',
                        CITYWATCH_FROM_EMAIL='sender@example.com', CITYWATCH_TO_EMAIL='reader@example.com')
        return send(self.store, settings, False, lambda *_: {'Messages': [{'Status': 'success'}]})

    def test_email_and_slack_acknowledge_independently_in_both_orders(self):
        self.observe()
        self.assertEqual(self.email()['status'], 'accepted')
        self.assertFalse(self.store.pending())
        self.assertEqual(self.deliver()['status'], 'accepted')
        self.assertEqual(len(self.client.calls), 2)
        self.observe(replace(self.record, key='second'))
        self.assertEqual(self.deliver(200000)['status'], 'accepted')
        self.assertEqual(len(self.store.pending()), 1)
        self.assertEqual(self.email()['status'], 'accepted')
        self.assertEqual(self.tick(300000)['status'], 'waiting')

    def test_new_destinations_start_at_tail_and_restart_preserves_cursor(self):
        self.observe()
        new_target = register(self.store.db, 'TTEST', 'CNEW')
        self.assertFalse(pending_rows(self.store.db, new_target, self.matcher))
        self.observe(replace(self.record, key='new'))
        register(self.store.db, 'TTEST', 'CNEW')
        self.assertEqual(len(pending_rows(self.store.db, new_target, self.matcher)), 1)

    def test_restart_resumes_thread_without_reposting_parent(self):
        self.observe()
        self.assertEqual(self.tick()['status'], 'posted')
        parent = self.client.calls[0]
        self.reopen()
        self.assertEqual(self.tick()['status'], 'accepted')
        self.assertEqual(len(self.client.calls), 2)
        self.assertEqual(self.client.calls[1]['thread_ts'], '100.000001')
        self.assertNotIn('thread_ts', parent)
        self.assertEqual(self.tick(200000)['status'], 'waiting')

    def test_daily_cadence_survives_restart_and_does_not_consume_new_rows(self):
        self.observe()
        self.deliver()
        self.observe(replace(self.record, key='later'))
        self.reopen()
        self.assertEqual(self.tick(103600)['status'], 'waiting')
        self.assertEqual(len(pending_rows(self.store.db, self.target, self.matcher)), 1)
        self.assertEqual(self.deliver(186400)['status'], 'accepted')

    def test_timeout_pauses_and_manual_absence_retries_identical_message(self):
        self.observe()
        self.tick()
        self.client.failure = TimeoutError('unknown acceptance')
        failure = self.tick()
        self.assertEqual(failure['status'], 'needs_review')
        original = self.client.calls[-1]
        self.client.failure = None
        self.reopen()
        self.assertEqual(self.tick(200000)['status'], 'needs_review')
        self.assertEqual(len(self.client.calls), 2)
        resolve(self.store, failure['batch_id'], failure['position'], confirmed_absent=True)
        self.assertEqual(self.tick(200000)['status'], 'accepted')
        self.assertEqual(self.client.calls[-1], original)

    def test_manual_confirmation_of_parent_links_thread_without_resending(self):
        self.observe()
        self.client.failure = TimeoutError()
        failure = self.tick()
        resolve(self.store, failure['batch_id'], 0, ts='123.456789')
        self.client.failure = None
        self.assertEqual(self.tick()['status'], 'accepted')
        self.assertEqual(self.client.calls[-1]['thread_ts'], '123.456789')
        self.assertEqual(len(self.client.calls), 2)
        with self.assertRaises(ValueError):
            resolve(self.store, failure['batch_id'], 0, confirmed_absent=True)

    def test_process_crash_in_flight_requires_review(self):
        self.observe()
        first = self.tick()
        with self.store.db:
            self.store.db.execute("UPDATE slack_messages SET status='in_flight' WHERE batch_id=? AND position=1", (first['batch_id'],))
        self.reopen()
        self.assertEqual(self.tick()['status'], 'needs_review')
        self.assertEqual(len(self.client.calls), 1)

    def test_rate_limit_is_persisted_and_honors_retry_after(self):
        self.observe()
        self.client.failure = api_error(429, 'ratelimited', {'Retry-After': '120'})
        self.assertEqual(self.tick()['status'], 'retry_later')
        self.reopen()
        self.client.failure = None
        self.assertEqual(self.tick(100119)['status'], 'retry_later')
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(self.tick(100120)['status'], 'posted')
        self.assertEqual(self.client.calls[0], self.client.calls[1])

    def test_server_errors_and_invalid_acknowledgements_are_uncertain(self):
        self.observe()
        self.client.failure = api_error(503, 'internal_error')
        result = self.tick()
        self.assertEqual(result['status'], 'needs_review')
        resolve(self.store, result['batch_id'], 0, confirmed_absent=True)
        self.client.failure = None
        with patch.object(self.client, 'chat_postMessage', return_value={'ok': True, 'ts': '123.456', 'channel': 'CWRONG'}):
            self.assertEqual(self.tick()['status'], 'needs_review')

    def test_explicit_rejection_is_retried_after_delay(self):
        self.observe()
        self.client.failure = api_error(200, 'not_in_channel')
        self.assertEqual(self.tick()['status'], 'retry_later')
        self.client.failure = None
        self.assertEqual(self.tick(100299)['status'], 'retry_later')
        self.assertEqual(self.tick(100300)['status'], 'posted')

    def test_prepared_batch_is_immutable_across_rule_changes_and_email_refilter(self):
        self.observe()
        self.tick()
        frozen = self.store.db.execute('SELECT payload FROM slack_messages WHERE position=1').fetchone()[0]
        self.store.refilter_pending(Matcher([]))
        self.assertEqual(self.tick(matcher=Matcher([]))['status'], 'accepted')
        self.assertEqual(json.loads(frozen)['blocks'], self.client.calls[-1]['blocks'])

    def test_unprepared_rows_are_refiltered_without_changing_email_queue(self):
        self.observe()
        self.assertEqual(self.tick(matcher=Matcher([]))['status'], 'waiting')
        self.assertFalse(self.client.calls)
        self.assertEqual(len(self.store.pending()), 1)
        self.assertEqual(self.store.db.execute('SELECT cursor FROM slack_targets').fetchone()[0], 1)

    def test_removed_matching_text_retains_previous_evidence(self):
        self.observe()
        self.deliver()
        self.observe(replace(self.record, text='Roll call.'))
        self.deliver(200000)
        detail = json.dumps(self.client.calls[-1]['blocks'])
        self.assertIn('Previously matching text', detail)
        self.assertIn('protected bike lanes', detail)

    def test_collection_and_parallel_sender_do_not_create_partial_or_duplicate_batches(self):
        import fcntl
        self.observe()
        with self.store.db:
            self.store.db.execute("INSERT INTO runs(started_at) VALUES('now')")
        self.assertEqual(self.tick()['status'], 'waiting')
        with self.store.db:
            self.store.db.execute("UPDATE runs SET finished_at='later',report=?", (json.dumps({'issues': []}),))
        with (Path(self.tmp.name) / 'slack-send.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertEqual(self.tick()['status'], 'busy')
        self.assertEqual(self.deliver()['status'], 'accepted')
        self.assertEqual(len(self.client.calls), 2)

    def test_preview_is_bounded_and_never_acknowledges(self):
        for i in range(20):
            self.observe(replace(self.record, key=str(i), title='Bike project ' + str(i), text='Bike lanes. ' * 100, url='https://nola.gov/' + str(i)))
        before = len(self.store.pending())
        preview = digest_preview(self.store.pending(), {'issues': []})
        self.assertLessEqual(len(preview['blocks']), 50)
        self.assertIn('15 more', json.dumps(preview))
        self.assertEqual(len(self.store.pending()), before)
        self.assertFalse(self.client.calls)

    def test_source_markup_is_not_executed_and_messages_stay_within_limits(self):
        self.observe(replace(self.record, title='<!channel> *' * 100, text='Bike lanes. <!everyone> & *' * 3000))
        messages = digest_messages(self.store.pending(), {'issues': [{'source': 'council', 'error': '<!channel>' * 1000}]})
        for message in messages:
            self.assertNotIn('<!channel>', message['text'])
            self.assertLessEqual(len(message['text']), 4000)
            self.assertLessEqual(len(message['blocks']), 50)
            for block in message['blocks']:
                if block.get('text'):
                    limit = 150 if block['type'] == 'header' else 3000
                    self.assertLessEqual(len(block['text']['text']), limit)
                    if block['text']['type'] == 'mrkdwn':
                        self.assertNotIn('<!channel>', block['text']['text'])
        self.assertEqual(source_link('javascript:alert(1)')['text']['type'], 'plain_text')
        self.assertEqual(source_link('https://nola.gov/a|><!channel>')['text']['type'], 'plain_text')


class SlackSetupTests(unittest.TestCase):
    def test_workspace_verification_and_no_automatic_sdk_retries(self):
        settings = {'CITYWATCH_SLACK_TEAM_ID': 'TBIKE', 'CITYWATCH_SLACK_CHANNEL_ID': 'CPOLICY', 'SLACK_BOT_TOKEN': 'xoxb-test'}
        with patch('slack_sdk.WebClient') as web:
            web.return_value.auth_test.return_value = {'ok': True, 'team_id': 'TBIKE', 'bot_id': 'BBOT'}
            make_client(settings)
            self.assertEqual(web.call_args.kwargs['retry_handlers'], [])
            web.return_value.auth_test.return_value['team_id'] = 'TOTHER'
            with self.assertRaises(ValueError):
                make_client(settings)
        with self.assertRaises(ValueError):
            destination(dict(settings, CITYWATCH_SLACK_CHANNEL_ID='#policy-design'))

    def test_manifest_has_only_posting_scope_and_no_interactive_features(self):
        manifest = json.loads((Path(__file__).resolve().parents[1] / 'config/slack-app.json').read_text())
        self.assertEqual(manifest['oauth_config']['scopes']['bot'], ['chat:write'])
        self.assertFalse(manifest['settings']['socket_mode_enabled'])
        self.assertNotIn('slash_commands', manifest['features'])
        self.assertNotIn('event_subscriptions', manifest['settings'])


if __name__ == '__main__':
    unittest.main()
