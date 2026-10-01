import json
import tempfile
import unittest
from datetime import date
from citywatch.core import Store, Record, Matcher
from citywatch.mail import send


class MailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.settings = dict(MAILJET_API_KEY='test-public', MAILJET_SECRET_KEY='test-secret',
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


if __name__ == '__main__':
    unittest.main()
