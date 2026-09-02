VAPI_API_URL = "https://api.vapi.ai"
VAPI_PHONE_NUMBER_ID = "20f48d37-c193-415f-9a37-1c076c7b7956"

ASSISTANT_IDS = {
    "Josh": "97a19f43-7867-4090-91fa-6b2a0ff335f2",
    "Michael": "050b62d3-aa14-41b8-9e56-9d11a1845a05",
    "Mark": "fce1a7fb-9719-4a7f-ab95-a5e25ee5b1a6",
    "Margaret": "a60ca5c8-a542-438d-97ce-40a1f2364636",
}


def call_vapi_create_campaign(payload):
    api_key = os.getenv("VAPI_API_KEY")

    if not api_key:
        return {
            "ok": False,
            "error": "Missing VAPI_API_KEY in Render environment variables."
        }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    url = f"{VAPI_API_URL}/campaign"

    try:
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=30
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw_response": response.text}

        if response.status_code < 400:
            return {
                "ok": True,
                "endpoint_used": url,
                "data": data
            }

        return {
            "ok": False,
            "endpoint_used": url,
            "status_code": response.status_code,
            "error": data
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)
        }


def get_contact_private_data(contact_id):
    contacts_result = get_contacts_by_ids([contact_id])

    if not contacts_result["ok"]:
        return {
            "ok": False,
            "error": contacts_result.get("error")
        }

    contacts = extract_results(contacts_result["data"])

    if not contacts:
        return {
            "ok": False,
            "error": "Contact not found."
        }

    contact = contacts[0]
    phones = extract_phones_from_contact(contact)
    property_address = extract_property_address_from_job_title_only(contact)

    return {
        "ok": True,
        "phones": phones,
        "property_address": property_address
    }


def build_single_test_campaign_payload(batch_label, agent_name, lead_row):
    contact_name = lead_row.get("contact_name")
    contact_id = lead_row.get("contact_id")

    private_data = get_contact_private_data(contact_id)

    if not private_data["ok"]:
        return {
            "ok": False,
            "error": private_data["error"],
            "lead": contact_name
        }

    phones = private_data["phones"]
    property_address = private_data["property_address"]

    if not phones:
        return {
            "ok": False,
            "error": "Missing phone.",
            "lead": contact_name
        }

    if not property_address:
        return {
            "ok": False,
            "error": "Missing Job Title property address.",
            "lead": contact_name
        }

    selected_phone = phones[0]

    payload = {
        "name": f"TEST - {batch_label} - {contact_name}",
        "assistantId": ASSISTANT_IDS[agent_name],
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customers": [
            {
                "number": selected_phone,
                "name": contact_name
            }
        ],
        "assistantOverrides": {
            "variableValues": {
                "name": contact_name,
                "property_address": property_address
            }
        },
        "maxConcurrency": 1
    }

    return {
        "ok": True,
        "payload": payload,
        "safe_preview": {
            "batch_label": batch_label,
            "agent": agent_name,
            "lead_name": contact_name,
            "phone_last4": mask_phone(selected_phone),
            "address_found": True,
            "phoneNumberId": VAPI_PHONE_NUMBER_ID,
            "assistantId": ASSISTANT_IDS[agent_name]
        }
    }


@app.route("/create-test-campaigns")
def create_test_campaigns():
    approval = request.args.get("approve", "").strip()

    if approval != "FINAL":
        return jsonify({
            "mode": "create-test-campaigns",
            "status": "BLOCKED",
            "reason": "Missing safety approval.",
            "required_url_example": "/create-test-campaigns?approve=FINAL",
            "safe": "No campaigns were created. No Vapi calls were made."
        }), 403

    report = build_contact_rows()

    if not report["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "create-test-campaigns",
            "step_failed": report["step_failed"],
            "error": report["error"]
        }), 500

    josh_rows = report["suggested_josh_estate_rows"]
    michael_rows = report["suggested_michael_owner_rows"]

    if not josh_rows:
        return jsonify({
            "mode": "create-test-campaigns",
            "status": "FAILED",
            "reason": "No callable Josh Estate lead found.",
            "safe": "No campaigns were created."
        }), 400

    if not michael_rows:
        return jsonify({
            "mode": "create-test-campaigns",
            "status": "FAILED",
            "reason": "No callable Michael Owner lead found.",
            "safe": "No campaigns were created."
        }), 400

    josh_test_lead = josh_rows[0]
    michael_test_lead = michael_rows[0]

    josh_payload_result = build_single_test_campaign_payload(
        "Josh Estate",
        "Josh",
        josh_test_lead
    )

    if not josh_payload_result["ok"]:
        return jsonify({
            "mode": "create-test-campaigns",
            "status": "FAILED_BEFORE_VAPI",
            "failed_batch": "Josh Estate",
            "error": josh_payload_result,
            "safe": "No campaigns were created."
        }), 400

    michael_payload_result = build_single_test_campaign_payload(
        "Michael Owner",
        "Michael",
        michael_test_lead
    )

    if not michael_payload_result["ok"]:
        return jsonify({
            "mode": "create-test-campaigns",
            "status": "FAILED_BEFORE_VAPI",
            "failed_batch": "Michael Owner",
            "error": michael_payload_result,
            "safe": "No campaigns were created."
        }), 400

    created_campaigns = []

    josh_create_result = call_vapi_create_campaign(josh_payload_result["payload"])

    created_campaigns.append({
        "batch_label": "Josh Estate",
        "agent": "Josh",
        "requested_lead": josh_payload_result["safe_preview"],
        "vapi_result_ok": josh_create_result["ok"],
        "endpoint_used": josh_create_result.get("endpoint_used"),
        "campaign_id": (
            josh_create_result.get("data", {}).get("id")
            if josh_create_result["ok"]
            else None
        ),
        "vapi_response": josh_create_result
    })

    if not josh_create_result["ok"]:
        return jsonify({
            "mode": "create-test-campaigns",
            "status": "PARTIAL_OR_FAILED",
            "message": "Josh test campaign failed, so Michael was not created.",
            "created_campaigns": created_campaigns,
            "safe": "Stopped after first failure."
        }), 500

    michael_create_result = call_vapi_create_campaign(michael_payload_result["payload"])

    created_campaigns.append({
        "batch_label": "Michael Owner",
        "agent": "Michael",
        "requested_lead": michael_payload_result["safe_preview"],
        "vapi_result_ok": michael_create_result["ok"],
        "endpoint_used": michael_create_result.get("endpoint_used"),
        "campaign_id": (
            michael_create_result.get("data", {}).get("id")
            if michael_create_result["ok"]
            else None
        ),
        "vapi_response": michael_create_result
    })

    if not michael_create_result["ok"]:
        return jsonify({
            "mode": "create-test-campaigns",
            "status": "PARTIAL_SUCCESS",
            "message": "Josh test campaign was created, but Michael failed.",
            "created_campaigns": created_campaigns,
            "warning": "Check Vapi. Cancel Josh test campaign if needed.",
            "safe": "Only the first test campaign may have been created."
        }), 500

    return jsonify({
        "mode": "create-test-campaigns",
        "status": "SUCCESS",
        "approval_used": "FINAL",
        "test_scope": {
            "josh_estate_test_leads": 1,
            "michael_owner_test_leads": 1,
            "total_test_campaigns_requested": 2
        },
        "created_campaigns": created_campaigns,
        "next_step": "Check Vapi Campaigns page. Confirm both TEST campaigns appear and watch the calls/results.",
        "safe_note": "This endpoint created only two one-lead test campaigns."
    })
