import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import app as dispatcher


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.client = dispatcher.app.test_client()
        self.network = self.enterContext(patch.object(
            dispatcher.requests.sessions.Session, 'request',
            side_effect=AssertionError('Live network forbidden')))
        self.report = {
            'ok': True, 'skip_count': 3,
            'suggested_josh_estate_rows': [
                {'status': 'CALLABLE', 'contact_id': 'j1', 'contact_name': 'Estate One'},
                {'status': 'CALLABLE', 'contact_id': 'j2', 'contact_name': 'Estate Two'}],
            'suggested_michael_owner_rows': [
                {'status': 'CALLABLE', 'contact_id': 'm1', 'contact_name': 'Owner'}],
        }
        self.rows = self.enterContext(patch.object(dispatcher, 'build_contact_rows', return_value=self.report))
        self.private = self.enterContext(patch.object(dispatcher, 'get_contact_private_data',
            side_effect=lambda cid: {'ok': True, 'phones': [f'+12025550{cid[-1]}1', f'+12025550{cid[-1]}2'],
                                    'property_address': f'12 {cid} Street, Albany, NY 12207'}))
        self.vapi = self.enterContext(patch.object(dispatcher, 'call_vapi_create_campaign',
            side_effect=[{'ok': True, 'data': {'id': 'fake-j1'}},
                         {'ok': True, 'data': {'id': 'fake-j2'}},
                         {'ok': True, 'data': {'id': 'fake-m1'}}]))
        self.time = self.enterContext(patch.object(dispatcher, 'resolve_launch_start',
            return_value=datetime.now(timezone.utc) + timedelta(days=1)))

    def tearDown(self):
        self.network.assert_not_called()

    def launch(self, flags='josh_estate=yes&michael_owner=yes'):
        return self.client.get('/launch-campaigns?approve=FINAL&start=9AM&' + flags)

    def test_required_gates_before_all_reads_and_writes(self):
        for url, status in [
            ('/launch-campaigns', 'BLOCKED'),
            ('/launch-campaigns?approve=final&start=9AM&josh_estate=yes', 'BLOCKED'),
            ('/launch-campaigns?approve=FINAL&josh_estate=yes&michael_owner=yes', 'MISSING_START_TIME'),
            ('/launch-campaigns?approve=FINAL&start=9AM', 'NO_BATCH_SELECTED'),
        ]:
            response = self.client.get(url)
            self.assertEqual(response.json['status'], status)
            self.assertEqual(response.json['created_campaigns'], [])
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.rows.assert_not_called()
        self.private.assert_not_called()
        self.vapi.assert_not_called()

    def test_one_campaign_per_contact_all_phones_correct_routes_and_five_minute_gap(self):
        response = self.launch()
        self.assertEqual(response.json['status'], 'SUCCESS')
        self.assertEqual(response.json['campaign_ids'], ['fake-j1', 'fake-j2', 'fake-m1'])
        self.assertEqual(response.json['lead_counts'], {'Josh Estate': 2, 'Michael Owner': 1})
        self.assertEqual(response.json['gap_minutes'], 5)
        self.assertEqual(response.json['skipped_count'], 3)
        self.assertEqual(self.vapi.call_count, 3)
        for call, agent in zip(self.vapi.call_args_list, ['Josh', 'Josh', 'Michael']):
            payload = call.args[0]
            self.assertEqual(payload['assistantId'], dispatcher.ASSISTANT_IDS[agent])
            self.assertEqual(payload['phoneNumberId'], dispatcher.VAPI_PHONE_NUMBER_ID)
            self.assertEqual(payload['maxConcurrency'], 1)
            self.assertIn('earliestAt', payload['schedulePlan'])
            self.assertTrue(payload['name'].startswith('LIVE - '))
            self.assertNotIn('assistantOverrides', payload)
            self.assertEqual(len(payload['customers']), 2)
            for customer in payload['customers']:
                address = customer['assistantOverrides']['variableValues']['property_address']
                self.assertNotIn('12207', address)
                self.assertIn('New York', address)
        starts = [datetime.fromisoformat(call.args[0]['schedulePlan']['earliestAt'])
                  for call in self.vapi.call_args_list]
        self.assertEqual([starts[i + 1] - starts[i] for i in range(2)],
                         [timedelta(minutes=5), timedelta(minutes=5)])
        self.assertEqual(
            [customer['number'] for customer in self.vapi.call_args_list[0].args[0]['customers']],
            ['+1202555011', '+1202555012'])
        self.assertNotIn('+1202555011', response.get_data(as_text=True))

    def test_only_selected_batch(self):
        self.launch('michael_owner=yes')
        self.vapi.assert_called_once()
        self.private.assert_called_once_with('m1')
        self.assertEqual(self.vapi.call_args.args[0]['assistantId'], dispatcher.ASSISTANT_IDS['Michael'])

    def test_empty_second_batch_prevents_all_writes(self):
        self.report['suggested_michael_owner_rows'] = []
        self.assertEqual(self.launch().json['status'], 'NO_CALLABLE_LEADS')
        self.vapi.assert_not_called()

    def test_missing_cleaned_address_is_skipped(self):
        self.private.side_effect = None
        self.private.return_value = {'ok': True, 'phones': ['+12025550123'], 'property_address': '12207'}
        self.assertEqual(self.launch().json['status'], 'NO_CALLABLE_LEADS')
        self.vapi.assert_not_called()

    def test_crm_failure_prevents_all_writes_and_redacts(self):
        self.private.side_effect = RuntimeError('private secret')
        response = self.launch()
        self.assertEqual(response.json['status'], 'FAILED_BEFORE_VAPI')
        self.assertNotIn('private secret', response.get_data(as_text=True))
        self.vapi.assert_not_called()

    def test_vapi_uncertainty_stops_without_retry(self):
        self.vapi.side_effect = [{'ok': False, 'error': 'private secret'}]
        response = self.launch()
        self.assertEqual(response.json['status'], 'PARTIAL_OR_UNCONFIRMED')
        self.vapi.assert_called_once()
        self.assertNotIn('private secret', response.get_data(as_text=True))

    def test_partial_success_preserves_created_id(self):
        self.vapi.side_effect = [{'ok': True, 'data': {'id': 'fake-j'}}, RuntimeError('timeout')]
        response = self.launch()
        self.assertEqual(response.json['campaign_ids'], ['fake-j'])
        self.assertEqual(response.json['status'], 'PARTIAL_OR_UNCONFIRMED')

    def test_optional_gap_is_applied(self):
        response = self.client.get(
            '/launch-campaigns?approve=FINAL&start=9AM&gap=7&josh_estate=yes&michael_owner=yes')
        self.assertEqual(response.json['gap_minutes'], 7)
        starts = [datetime.fromisoformat(call.args[0]['schedulePlan']['earliestAt'])
                  for call in self.vapi.call_args_list]
        self.assertEqual(starts[1] - starts[0], timedelta(minutes=7))

    def test_head_and_post_cannot_launch(self):
        url = '/launch-campaigns?approve=FINAL&start=9AM&josh_estate=yes'
        self.assertEqual(self.client.head(url).status_code, 403)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.vapi.assert_not_called()
        self.rows.assert_not_called()

    def test_unknown_and_repeated_parameters_fail_closed(self):
        for flags in ['josh_estate=yes&mark=yes', 'josh_estate=yes&start=10AM']:
            self.assertEqual(self.launch(flags).json['status'], 'INVALID_PARAMETERS')
        self.vapi.assert_not_called()


class StartTests(unittest.TestCase):
    def test_future_date_new_york(self):
        day = (datetime.now(dispatcher.ZoneInfo('America/New_York')) + timedelta(days=1)).date()
        resolved = dispatcher.resolve_launch_start('9AM', day.isoformat())
        self.assertEqual(resolved.hour, 9)
        self.assertEqual(str(resolved.tzinfo), 'America/New_York')

    def test_invalid_or_past_time_rejected(self):
        for start, day in [('now', None), ('25AM', None), ('9AM', '2000-01-01')]:
            with self.assertRaises(ValueError):
                dispatcher.resolve_launch_start(start, day)


if __name__ == '__main__':
    unittest.main()
