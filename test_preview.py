import unittest
from unittest.mock import patch

import app as dispatcher


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.client = dispatcher.app.test_client()
        self.report = {
            "ok": True,
            "suggested_josh_estate_rows": [
                {"contact_name": "Sample Estate", "contact_id": "j1"},
                {"contact_name": "Not selected", "contact_id": "j2"},
            ],
            "suggested_michael_owner_rows": [
                {"contact_name": "Sample Owner", "contact_id": "m1"},
            ],
        }
        self.private = {"ok": True, "phones": ["+12025550123", "+12025550456"],
                        "property_address": "123 Private Street"}
        self.vapi = self.enterContext(patch.object(dispatcher, "call_vapi_create_campaign"))
        self.network = self.enterContext(patch.object(
            dispatcher.requests.sessions.Session, "request",
            side_effect=AssertionError("Live network forbidden in tests")))
        self.rows = self.enterContext(patch.object(dispatcher, "build_contact_rows", return_value=self.report))
        self.contacts = self.enterContext(patch.object(dispatcher, "get_contact_private_data", return_value=self.private))

    def tearDown(self):
        self.vapi.assert_not_called()
        self.network.assert_not_called()

    def test_preview_selects_first_leads_and_masks_private_data(self):
        response = self.client.get("/test-campaign-preview")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual([r["lead_name"] for r in data["test_leads"]], ["Sample Estate", "Sample Owner"])
        self.assertEqual([c.args[0] for c in self.contacts.call_args_list], ["j1", "m1"])
        self.assertEqual(data["test_leads"][0]["phone_last4"], "***0123")
        self.assertIsNone(data["start_time"])
        self.assertTrue(data["timing"]["may_start_immediately_after_creation"])
        self.assertTrue(data["final_approval_required"])
        for private in ["+12025550123", "123 Private Street", dispatcher.VAPI_PHONE_NUMBER_ID, dispatcher.ASSISTANT_IDS["Josh"]]:
            self.assertNotIn(private, response.get_data(as_text=True))
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_approval_and_start_parameters_cannot_enable_creation(self):
        data = self.client.get("/test-campaign-preview?approve=FINAL&start=9AM").get_json()
        self.assertEqual(data["status"], "PREVIEW_ONLY")
        self.assertEqual(data["campaigns_created"], 0)
        self.assertIsNone(data["start_time"])

    def test_post_is_rejected(self):
        self.assertEqual(self.client.post("/test-campaign-preview?approve=FINAL").status_code, 405)
        self.rows.assert_not_called()

    def test_missing_group_is_incomplete(self):
        self.report["suggested_josh_estate_rows"] = []
        response = self.client.get("/test-campaign-preview")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["test_leads"][0]["status"], "NO_CALLABLE_LEAD")

    def test_crm_error_is_redacted(self):
        self.rows.return_value = {"ok": False, "error": "secret-phone-and-token"}
        response = self.client.get("/test-campaign-preview")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-phone-and-token", response.get_data(as_text=True))
        self.contacts.assert_not_called()

    def test_contact_error_is_redacted(self):
        self.contacts.return_value = {"ok": False, "error": "secret-phone-and-token"}
        response = self.client.get("/test-campaign-preview")
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("secret-phone-and-token", response.get_data(as_text=True))

    def test_missing_phone_or_address_is_incomplete(self):
        for field, value in [("phones", []), ("property_address", None)]:
            with self.subTest(field=field):
                self.contacts.return_value = dict(self.private, **{field: value})
                self.assertEqual(self.client.get("/test-campaign-preview").status_code, 409)

    def test_unexpected_error_clears_partial_preview(self):
        self.contacts.side_effect = [self.private, RuntimeError("private detail")]
        response = self.client.get("/test-campaign-preview")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["test_leads"], [])
        self.assertNotIn("private detail", response.get_data(as_text=True))

    def test_existing_creation_route_still_blocks_without_approval(self):
        self.assertEqual(self.client.get("/create-test-campaigns").status_code, 403)
        self.rows.assert_not_called()


class ProductionCampaignPreviewTests(unittest.TestCase):
    def setUp(self):
        self.client = dispatcher.app.test_client()
        self.report = {
            "ok": True,
            "buckets": {"deploy_eligible_tasks": [{}, {}, {}]},
            "callable_count": 2,
            "skip_count": 1,
            "suggested_josh_estate_rows": [{
                "contact_name": "Estate Lead", "task_name": "VAPI NEW",
                "due_date": "2026-09-03", "suggested_route": "Josh Estate",
                "phone_last4_preview": ["***0123"],
                "address_preview": "JOB_TITLE_ADDRESS_FOUND: Albany, NY",
                "warnings": [], "contact_id": "private-j", "task_id": "private-tj",
            }],
            "suggested_michael_owner_rows": [{
                "contact_name": "Owner Lead", "task_name": "VAPI",
                "due_date": "2026-09-03", "suggested_route": "Michael Owner",
                "phone_last4_preview": ["***0456"],
                "address_preview": "JOB_TITLE_ADDRESS_FOUND",
                "warnings": [], "contact_id": "private-m", "task_id": "private-tm",
            }],
            "skip_rows": [{
                "contact_name": "Incomplete Lead", "task_name": "VAPI RELS PC",
                "due_date": "2026-09-03", "suggested_route": "Michael Owner",
                "phone_last4_preview": [], "address_preview": None,
                "warnings": ["MISSING_PHONE", "MISSING_JOB_TITLE_ADDRESS"],
                "contact_id": "private-s", "task_id": "private-ts",
            }],
        }
        self.vapi = self.enterContext(patch.object(dispatcher, "call_vapi_create_campaign"))
        self.rows = self.enterContext(patch.object(dispatcher, "build_contact_rows", return_value=self.report))

    def tearDown(self):
        self.vapi.assert_not_called()

    def test_campaign_preview_is_grouped_masked_and_read_only(self):
        response = self.client.get("/campaign-preview?approve=FINAL")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "READ_ONLY_PREVIEW")
        self.assertEqual(data["summary"], {
            "total_vapi_tasks": 3, "callable_leads": 2,
            "josh_estate_count": 1, "michael_owner_count": 1,
            "skipped_count": 1,
        })
        self.assertEqual(data["route_groups"]["Mark"]["status"], "DAVID_COMMAND_ONLY")
        self.assertEqual(data["route_groups"]["Margaret"]["count"], 0)
        self.assertEqual(data["skipped_leads"][0]["warnings"], [
            "MISSING_PHONE", "MISSING_JOB_TITLE_ADDRESS"
        ])
        body = response.get_data(as_text=True)
        for private in ["private-j", "private-tj", "+12025550123", "123 Private Street"]:
            self.assertNotIn(private, body)
        self.assertIn("***0123", body)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_campaign_preview_post_is_rejected(self):
        self.assertEqual(self.client.post("/campaign-preview").status_code, 405)
        self.rows.assert_not_called()

    def test_campaign_preview_redacts_crm_errors(self):
        self.rows.return_value = {"ok": False, "error": "private CRM detail"}
        response = self.client.get("/campaign-preview")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("private CRM detail", response.get_data(as_text=True))

    def test_only_exact_vapi_task_names_are_eligible(self):
        exact_names = ["VAPI", "VAPI NEW", "VAPI RELS PC", "VAPI NEW RELS PC"]
        for name in exact_names:
            with self.subTest(name=name):
                self.assertEqual(dispatcher.classify_task_exact_name_only(name)["bucket"], "deploy")

        for name in ["VAPINEW", "VAPI RELSPC", "VAPI EXTRA", "RELS PC"]:
            with self.subTest(name=name):
                self.assertNotEqual(dispatcher.classify_task_exact_name_only(name)["bucket"], "deploy")

        self.assertEqual(
            dispatcher.classify_task_exact_name_only("RELS PC")["bucket"],
            "manual_sms_only",
        )


if __name__ == "__main__":
    unittest.main()
