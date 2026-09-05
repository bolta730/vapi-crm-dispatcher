import os
import unittest
from unittest.mock import Mock, patch

import app as dispatcher


JOSH_ID = "376813ed-0dc0-4ee6-9ee8-724de9363ecb"
CALL_ID = "11111111-2222-4333-8444-555555555555"


class PostCallReportTests(unittest.TestCase):
    def setUp(self):
        dispatcher.app.config["TESTING"] = True
        self.client = dispatcher.app.test_client()

    @patch.object(dispatcher.requests, "post")
    @patch.object(dispatcher.requests, "get")
    def test_missing_ids_returns_helpful_error_without_network_or_writes(self, get, post):
        response = self.client.get("/post-call-report")

        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual(body["status"], "MISSING_CAMPAIGN_IDS")
        self.assertIn("josh_campaign_id", body["error"])
        self.assertEqual(body["safety"], dispatcher.POST_CALL_SAFETY)
        get.assert_not_called()
        post.assert_not_called()

    @patch.dict(os.environ, {"VAPI_API_KEY": "test-only"})
    @patch.object(dispatcher.requests, "post")
    @patch.object(dispatcher.requests, "get")
    def test_report_uses_only_get_and_masks_phone(self, get, post):
        campaign = {
            "id": JOSH_ID,
            "name": "LIVE - Josh Estate - 2026-09-03 - start 9AM",
            "status": "ended",
            "endedReason": None,
            "customers": [{"name": "Jane Lead", "number": "+12125550199"}],
            "calls": {CALL_ID: {"status": "ended"}},
            "callsCounterEnded": 1,
            "callsCounterEndedVoicemail": 0,
        }
        call = {
            "id": CALL_ID,
            "status": "ended",
            "endedReason": "customer-ended-call",
            "startedAt": "2026-09-03T13:00:00Z",
            "endedAt": "2026-09-03T13:01:30Z",
            "customer": {"name": "Jane Lead", "number": "+12125550199"},
            "artifact": {
                "transcript": "Assistant: Hello\nUser: Hi",
                "recordingUrl": "https://example.invalid/recording.wav",
                "messages": [{"role": "user", "message": "Hi"}],
            },
        }
        get.side_effect = [
            Mock(status_code=200, json=Mock(return_value=campaign)),
            Mock(status_code=200, json=Mock(return_value=call)),
        ]

        response = self.client.get(f"/post-call-report?josh_campaign_id={JOSH_ID}")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        report = body["campaigns"][0]
        self.assertEqual(report["total_calls"], 1)
        self.assertEqual(report["completed_calls"], 1)
        self.assertEqual(report["answered_human_calls"], 1)
        self.assertEqual(report["total_duration_seconds"], 90.0)
        self.assertEqual(report["calls"][0]["phone_last4"], "0199")
        self.assertTrue(report["calls"][0]["transcript_available"])
        self.assertTrue(report["calls"][0]["recording_url_available"])
        self.assertNotIn("+12125550199", response.get_data(as_text=True))
        self.assertTrue(all(call.args[0].startswith("https://api.vapi.ai/") for call in get.call_args_list))
        post.assert_not_called()

    @patch.object(dispatcher.requests, "get")
    def test_invalid_id_is_rejected_before_vapi_read(self, get):
        response = self.client.get("/post-call-report?josh_campaign_id=not-a-uuid")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "INVALID_CAMPAIGN_ID")
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
