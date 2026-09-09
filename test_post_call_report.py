import os
import time
import unittest
from unittest.mock import Mock, patch

import app as dispatcher


JOSH_ID = "376813ed-0dc0-4ee6-9ee8-724de9363ecb"
SECOND_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
THIRD_ID = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
FOURTH_ID = "cccccccc-dddd-4eee-8fff-aaaaaaaaaaaa"
CALL_ID = "11111111-2222-4333-8444-555555555555"


class PostCallReportTests(unittest.TestCase):
    def setUp(self):
        dispatcher.app.config["TESTING"] = True
        self.client = dispatcher.app.test_client()
        with dispatcher.REPORT_JOBS_LOCK:
            dispatcher.REPORT_JOBS.clear()

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

    @patch.object(dispatcher, "build_post_call_campaign_report")
    def test_comma_separated_campaign_ids_return_all_campaigns(self, build_report):
        ids = [JOSH_ID, SECOND_ID, THIRD_ID, FOURTH_ID]
        build_report.side_effect = lambda label, campaign_id, **kwargs: {
            "campaign_id": campaign_id,
            "batch_label": label,
            "campaign_status": "ended",
        }

        response = self.client.get(f"/post-call-report?campaign_ids={','.join(ids)}")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["requested_campaign_ids"], ids)
        self.assertEqual([item["campaign_id"] for item in body["campaigns"]], ids)
        self.assertEqual(build_report.call_count, 4)

    @patch.object(dispatcher, "call_vapi_read")
    def test_sixteen_campaigns_are_bounded_parallel_and_keep_request_order(self, vapi_read):
        ids = [f"00000000-0000-4000-8000-{index:012d}" for index in range(16)]

        def delayed_read(path):
            time.sleep(0.05)
            campaign_id = path.rsplit("/", 1)[-1]
            return {"ok": True, "data": {"id": campaign_id, "status": "ended"}}

        vapi_read.side_effect = delayed_read
        started = time.monotonic()
        response = self.client.get(f"/post-call-report?campaign_ids={','.join(ids)}")
        elapsed = time.monotonic() - started

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["campaign_id"] for item in response.get_json()["campaigns"]],
            ids,
        )
        self.assertEqual(vapi_read.call_count, 16)
        self.assertLess(elapsed, 0.6)

    @patch.object(dispatcher, "build_post_call_campaign_report")
    def test_campaign_build_exception_returns_json_partial_report(self, build_report):
        build_report.side_effect = RuntimeError("unexpected private upstream detail")

        response = self.client.get(f"/post-call-report?campaign_ids={JOSH_ID}")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.content_type, "application/json")
        body = response.get_json()
        self.assertEqual(body["status"], "REPORT_PARTIAL_OR_UNAVAILABLE")
        self.assertEqual(body["campaigns"][0]["campaign_status"], "READ_ERROR")
        self.assertNotIn("private upstream detail", response.get_data(as_text=True))

    @patch.object(dispatcher, "build_post_call_campaign_report")
    def test_repeated_and_legacy_parameters_are_supported(self, build_report):
        build_report.side_effect = lambda label, campaign_id, **kwargs: {
            "campaign_id": campaign_id,
            "batch_label": label,
            "campaign_status": "ended",
        }

        response = self.client.get(
            f"/post-call-report?josh_campaign_id={JOSH_ID}"
            f"&josh_campaign_id={SECOND_ID},{THIRD_ID}"
            f"&michael_campaign_id={FOURTH_ID}"
        )

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(
            body["requested_campaign_ids"],
            [JOSH_ID, SECOND_ID, THIRD_ID, FOURTH_ID],
        )
        self.assertEqual(body["campaign_ids"]["josh_campaign_id"], [JOSH_ID, SECOND_ID, THIRD_ID])
        self.assertEqual(body["campaign_ids"]["michael_campaign_id"], [FOURTH_ID])

    @patch.object(dispatcher, "build_post_call_campaign_report")
    def test_duplicate_campaign_id_is_rejected_before_vapi_read(self, build_report):
        response = self.client.get(
            f"/post-call-report?campaign_ids={JOSH_ID}&campaign_ids={JOSH_ID}"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["status"], "DUPLICATE_CAMPAIGN_ID")
        build_report.assert_not_called()


class PostCallReportJobTests(unittest.TestCase):
    def setUp(self):
        dispatcher.app.config["TESTING"] = True
        self.client = dispatcher.app.test_client()
        with dispatcher.REPORT_JOBS_LOCK:
            dispatcher.REPORT_JOBS.clear()

    @patch.object(dispatcher.REPORT_JOB_EXECUTOR, "submit")
    def test_start_returns_job_id_and_schedules_read_only_report(self, submit):
        response = self.client.get(
            f"/post-call-report-start?campaign_ids={JOSH_ID},{SECOND_ID}"
        )

        self.assertEqual(response.status_code, 202)
        body = response.get_json()
        self.assertEqual(body["status"], "PROCESSING")
        self.assertTrue(dispatcher.valid_uuid(body["report_job_id"]))
        self.assertEqual(body["requested_campaign_ids"], [JOSH_ID, SECOND_ID])
        self.assertEqual(
            body["result_url"],
            f"/post-call-report-result?report_job_id={body['report_job_id']}",
        )
        submit.assert_called_once()
        self.assertIs(submit.call_args.args[0], dispatcher.run_post_call_report_job)

    def test_result_returns_processing_then_complete_full_large_report(self):
        ids = [f"00000000-0000-4000-8000-{index:012d}" for index in range(16)]
        requested = [("Campaign", campaign_id) for campaign_id in ids]
        report_job_id = "dddddddd-eeee-4fff-8aaa-bbbbbbbbbbbb"
        with dispatcher.REPORT_JOBS_LOCK:
            dispatcher.REPORT_JOBS[report_job_id] = {"status": "PROCESSING"}

        processing = self.client.get(
            f"/post-call-report-result?report_job_id={report_job_id}"
        )
        self.assertEqual(processing.status_code, 202)
        self.assertEqual(processing.get_json()["status"], "PROCESSING")

        full_campaigns = [
            {
                "campaign_id": campaign_id,
                "campaign_status": "ended",
                "calls": [{"call_id": f"call-{index}"}],
            }
            for index, campaign_id in enumerate(ids)
        ]
        with patch.object(
            dispatcher, "build_post_call_campaign_reports", return_value=full_campaigns
        ) as build_reports:
            dispatcher.run_post_call_report_job(
                report_job_id,
                requested,
                {"campaign_ids": ids},
            )

        complete = self.client.get(
            f"/post-call-report-result?report_job_id={report_job_id}"
        )
        self.assertEqual(complete.status_code, 200)
        body = complete.get_json()
        self.assertEqual(body["status"], "COMPLETE")
        self.assertEqual(body["report"]["requested_campaign_ids"], ids)
        self.assertEqual(len(body["report"]["campaigns"]), 16)
        self.assertEqual(body["report"]["campaigns"], full_campaigns)
        self.assertEqual(body["report"]["safety"], dispatcher.POST_CALL_SAFETY)
        build_reports.assert_called_once_with(requested)

    @patch.object(dispatcher.requests, "post")
    @patch.object(dispatcher, "build_post_call_campaign_reports")
    def test_report_job_runner_never_uses_post(self, build_reports, post):
        report_job_id = "eeeeeeee-ffff-4000-8bbb-cccccccccccc"
        with dispatcher.REPORT_JOBS_LOCK:
            dispatcher.REPORT_JOBS[report_job_id] = {"status": "PROCESSING"}
        build_reports.return_value = []

        dispatcher.run_post_call_report_job(
            report_job_id,
            [("Campaign", JOSH_ID)],
            {"campaign_ids": [JOSH_ID]},
        )

        post.assert_not_called()
        self.assertEqual(dispatcher.REPORT_JOBS[report_job_id]["status"], "COMPLETE")

    def test_job_endpoints_reject_invalid_parameters_as_json(self):
        start = self.client.get("/post-call-report-start?campaign_ids=bad")
        result = self.client.get("/post-call-report-result?report_job_id=bad")

        self.assertEqual(start.status_code, 400)
        self.assertEqual(start.content_type, "application/json")
        self.assertEqual(start.get_json()["status"], "INVALID_CAMPAIGN_ID")
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.content_type, "application/json")
        self.assertEqual(result.get_json()["status"], "INVALID_REPORT_JOB_ID")


if __name__ == "__main__":
    unittest.main()
