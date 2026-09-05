import os
import unittest
from unittest.mock import Mock, patch

import app as dispatcher


CALL_ID_1 = "11111111-2222-4333-8444-555555555555"
CALL_ID_2 = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


class CallQaReportTests(unittest.TestCase):
    def setUp(self):
        dispatcher.app.config["TESTING"] = True
        self.client = dispatcher.app.test_client()

    @patch.object(dispatcher.requests, "post")
    @patch.object(dispatcher.requests, "get")
    def test_missing_ids_is_safe_and_makes_no_network_request(self, get, post):
        response = self.client.get("/call-qa-report")

        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertEqual(body["status"], "MISSING_CALL_IDS")
        self.assertIn("call_ids", body["error"])
        self.assertEqual(body["safety"], dispatcher.CALL_QA_SAFETY)
        get.assert_not_called()
        post.assert_not_called()

    @patch.dict(os.environ, {"VAPI_API_KEY": "test-only"})
    @patch.object(dispatcher.requests, "post")
    @patch.object(dispatcher.requests, "get")
    def test_report_uses_only_get_masks_phone_and_returns_qa_detail(self, get, post):
        call = {
            "id": CALL_ID_1,
            "status": "ended",
            "endedReason": "silence-timed-out",
            "endedMessage": "Call ended after prolonged silence.",
            "startedAt": "2026-09-03T13:00:00Z",
            "endedAt": "2026-09-03T13:01:10Z",
            "customer": {"name": "Jane Lead", "number": "+12125550199"},
            "artifact": {
                "transcript": "Assistant: Hello\nUser: Hi\nAssistant: Are you there?",
                "recordingUrl": "https://example.invalid/recording.wav",
                "messages": [
                    {"role": "assistant", "message": "Hello", "secondsFromStart": 1},
                    {"role": "user", "message": "Hi", "secondsFromStart": 3},
                ],
            },
            "analysis": {"summary": "The customer answered, then went silent.", "successEvaluation": "false"},
        }
        get.return_value = Mock(status_code=200, json=Mock(return_value=call))

        response = self.client.get(f"/call-qa-report?call_ids={CALL_ID_1}")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        report = body["calls"][0]
        self.assertEqual(report["phone_last4"], "0199")
        self.assertEqual(report["duration_seconds"], 70.0)
        self.assertTrue(report["answered_human"])
        self.assertTrue(report["customer_speech_detected"])
        self.assertTrue(report["assistant_speech_detected"])
        self.assertIn("User: Hi", report["transcript"])
        self.assertEqual(report["analysis"]["success_evaluation"], "false")
        self.assertNotIn("+12125550199", response.get_data(as_text=True))
        self.assertEqual(get.call_args.args[0], f"https://api.vapi.ai/call/{CALL_ID_1}")
        post.assert_not_called()

    @patch.object(dispatcher.requests, "get")
    def test_invalid_duplicate_and_unknown_parameters_are_rejected_before_read(self, get):
        cases = [
            ("/call-qa-report?call_ids=not-a-uuid", "INVALID_CALL_ID"),
            (f"/call-qa-report?call_ids={CALL_ID_1},{CALL_ID_1}", "DUPLICATE_CALL_ID"),
            (f"/call-qa-report?call_ids={CALL_ID_1}&extra=yes", "INVALID_PARAMETERS"),
            (f"/call-qa-report?call_ids={CALL_ID_1}&call_ids={CALL_ID_2}", "INVALID_PARAMETERS"),
        ]
        for url, expected_status in cases:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.get_json()["status"], expected_status)
        get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
