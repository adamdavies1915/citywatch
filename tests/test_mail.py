import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import date
from citywatch.core import Store, Record, Matcher
from citywatch.mail import send


class MailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.settings = dict(MAILJET_API_KEY='test-public', MAILJET_SECRET_KEY='test-secret',
                             CITYWATCH_EMAIL_INTERVAL_SECONDS='0',
                             CITYWATCH_FROM_EMAIL='sender@example.com', CITYWATCH_TO_EMAIL='reader@example.com')
        self.store.observe(Record('1', 'test', 'Bike lane', 'https://nola.gov/test', 'agenda_item', 'Bike lane'),
                           Matcher([{'id': 'bike', 'priority': 'direct', 'pattern': 'bike'}]), date(2026, 9, 30))

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_sandbox_validates_without_acknowledging(self):
        sent = []
        def transport(payload, settings):
            sent.append(payload)
            return {'Messages': [{'Status': 'success'}]}
        self.assertEqual(send(self.store, self.settings, True, transport)['status'], 'sandbox_validated')
        self.assertTrue(sent[0]['SandboxMode'])
        self.assertEqual(len(self.store.pending()), 1)

    def test_failure_preserves_immutable_batch_then_success_acknowledges(self):
        attempts = []
        def fail(payload, settings):
            attempts.append(payload)
            raise TimeoutError('network timeout')
        with self.assertRaises(TimeoutError):
            send(self.store, self.settings, False, fail)
        self.assertEqual(len(self.store.pending()), 1)
        def success(payload, settings):
            attempts.append(payload)
            return {'Messages': [{'Status': 'success', 'To': [{'MessageID': 123}]}]}
        self.assertEqual(send(self.store, self.settings, False, success)['status'], 'accepted')
        self.assertEqual(attempts[0], attempts[1])
        self.assertTrue(attempts[0]['Messages'][0]['DeduplicateCampaign'])
        self.assertEqual(len(self.store.pending()), 0)
        self.assertEqual(send(self.store, self.settings, False, success)['status'], 'nothing_to_send')

    def test_refilter_does_not_mutate_prepared_retry(self):
        def fail(payload, settings):
            raise TimeoutError('ambiguous provider result')
        with self.assertRaises(TimeoutError):
            send(self.store, self.settings, False, fail)
        self.assertEqual(self.store.refilter_pending(Matcher([])), 0)
        self.assertEqual(len(self.store.pending()), 1)

    def test_daily_limit_survives_restart_and_preserves_pending(self):
        self.settings.pop('CITYWATCH_EMAIL_INTERVAL_SECONDS')
        calls = []
        def success(payload, settings):
            calls.append(payload)
            return {'Messages': [{'Status': 'success'}]}
        with patch('citywatch.mail.time.time', return_value=100000):
            self.assertEqual(send(self.store, self.settings, False, success)['status'], 'accepted')
        self.store.observe(Record('2', 'test', 'Bus service', 'https://nola.gov/2', 'agenda_item', 'Bus service'),
                           Matcher([{'id': 'transit', 'priority': 'direct', 'pattern': 'Bus'}]), date(2026, 9, 30))
        self.store.db.close()
        self.store = Store(self.temp.name)
        with patch('citywatch.mail.time.time', return_value=103600):
            self.assertEqual(send(self.store, self.settings, False, success)['status'], 'digest_deferred')
        self.assertEqual(len(self.store.pending()), 1)
        self.assertEqual(len(calls), 1)
        with patch('citywatch.mail.time.time', return_value=186400):
            self.assertEqual(send(self.store, self.settings, False, success)['status'], 'accepted')

    def test_timeout_does_not_allow_hourly_retries(self):
        self.settings.pop('CITYWATCH_EMAIL_INTERVAL_SECONDS')
        def fail(payload, settings):
            raise TimeoutError('ambiguous result')
        with patch('citywatch.mail.time.time', return_value=100000):
            with self.assertRaises(TimeoutError):
                send(self.store, self.settings, False, fail)
        with patch('citywatch.mail.time.time', return_value=103600):
            self.assertEqual(send(self.store, self.settings, False, fail)['status'], 'digest_deferred')
        self.assertEqual(len(self.store.pending()), 1)

    def test_existing_installation_gets_quiet_period(self):
        def success(payload, settings):
            return {'Messages': [{'Status': 'success'}]}
        send(self.store, self.settings, False, success)
        self.store.db.execute('DROP TABLE email_cadence')
        self.store.db.commit()
        self.settings.pop('CITYWATCH_EMAIL_INTERVAL_SECONDS')
        with patch('citywatch.mail.time.time', return_value=100000):
            self.assertEqual(send(self.store, self.settings, False, success)['status'], 'digest_deferred')
        with patch('citywatch.mail.time.time', return_value=186400):
            self.assertEqual(send(self.store, self.settings, False, success)['status'], 'nothing_to_send')


if __name__ == '__main__':
    unittest.main()
