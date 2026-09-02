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


if __name__ == "__main__":
    unittest.main()
