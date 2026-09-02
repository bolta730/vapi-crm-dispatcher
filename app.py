import os
import re
from datetime import date, timedelta

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

LACRM_API_URL = "https://api.lessannoyingcrm.com/v2/"
VAPI_API_URL = "https://api.vapi.ai"

VAPI_PHONE_NUMBER_ID = "20f48d37-c193-415f-9a37-1c076c7b7956"

ASSISTANT_IDS = {
    "Josh": "97a19f43-7867-4090-91fa-6b2a0ff335f2",
    "Michael": "050b62d3-aa14-41b8-9e56-9d11a1845a05",
    "Mark": "fce1a7fb-9719-4a7f-ab95-a5e25ee5b1a6",
    "Margaret": "a60ca5c8-a542-438d-97ce-40a1f2364636",
}


def call_lacrm(function_name, parameters=None):
    api_key = os.getenv("LACRM_API_KEY")

    if not api_key:
        return {
            "ok": False,
            "error": "Missing LACRM_API_KEY in Render environment variables."
        }

    payload = {
        "Function": function_name,
        "Parameters": parameters or {}
    }

    try:
        response = requests.post(
            LACRM_API_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": api_key
            },
            timeout=20
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw_response": response.text}

        if response.status_code >= 400:
            return {
                "ok": False,
                "status_code": response.status_code,
                "error": data
            }

        return {
            "ok": True,
            "data": data
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)
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

    primary_url = f"{VAPI_API_URL}/campaign"

    try:
        response = requests.post(
            primary_url,
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
                "endpoint_used": primary_url,
                "data": data
            }

        if response.status_code in [404, 405]:
            fallback_url = f"{VAPI_API_URL}/v2/campaign"

            fallback_response = requests.post(
                fallback_url,
                json=payload,
                headers=headers,
                timeout=30
            )

            try:
                fallback_data = fallback_response.json()
            except Exception:
                fallback_data = {"raw_response": fallback_response.text}

            if fallback_response.status_code < 400:
                return {
                    "ok": True,
                    "endpoint_used": fallback_url,
                    "data": fallback_data
                }

            return {
                "ok": False,
                "endpoint_used": fallback_url,
                "status_code": fallback_response.status_code,
                "error": fallback_data
            }

        return {
            "ok": False,
            "endpoint_used": primary_url,
            "status_code": response.status_code,
            "error": data
        }

    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc)
        }


def extract_results(data):
    if isinstance(data, list):
        return data

    if isinstance(data, dict):
        return data.get("Results", [])

    return []


def normalize_text(value):
    return str(value or "").strip().upper()


def compact_text(value):
    return normalize_text(value).replace(" ", "")


def get_due_tasks(start_date, end_date):
    all_tasks = []
    page = 1

    while True:
        result = call_lacrm("GetTasks", {
            "StartDate": start_date.isoformat(),
            "EndDate": end_date.isoformat(),
            "CompletionStatus": "Incomplete",
            "MaxNumberOfResults": 500,
            "Page": page
        })

        if not result["ok"]:
            return result

        data = result["data"]
        tasks = extract_results(data)
        all_tasks.extend(tasks)

        has_more = False
        if isinstance(data, dict):
            has_more = bool(data.get("HasMoreResults"))

        if not has_more:
            break

        page += 1

        if page > 10:
            break

    return {
        "ok": True,
        "data": all_tasks
    }


def classify_task_exact_name_only(task_name):
    name = normalize_text(task_name)
    compact = compact_text(task_name)

    exact_vapi_new_commands = {
        "VAPI NEW",
        "VAPI NEW RELS PC",
    }

    exact_vapi_new_compact_commands = {
        "VAPINEW",
        "VAPINEWRELSPC",
    }

    exact_vapi_commands = {
        "VAPI",
        "VAPI RELS PC",
    }

    exact_vapi_compact_commands = {
        "VAPI",
        "VAPIRELSPC",
    }

    rels_pc_manual_commands = {
        "RELS PC",
        "RELSPC",
    }

    if name in exact_vapi_new_commands or compact in exact_vapi_new_compact_commands:
        return {
            "bucket": "deploy",
            "priority_order": 1,
            "priority_label": "VAPI NEW",
            "reason": "Exact task name is VAPI NEW or VAPI NEW RELS PC."
        }

    if name in exact_vapi_commands or compact in exact_vapi_compact_commands:
        return {
            "bucket": "deploy",
            "priority_order": 2,
            "priority_label": "VAPI",
            "reason": "Exact task name is VAPI or VAPI RELS PC."
        }

    if name in rels_pc_manual_commands or compact in rels_pc_manual_commands:
        return {
            "bucket": "manual_sms_only",
            "priority_order": 50,
            "priority_label": "RELS PC BLACKLISTED",
            "reason": "Plain RELS PC is manual SMS/broadcast only. Ignored by Vapi dispatcher."
        }

    return {
        "bucket": "ignore",
        "priority_order": 99,
        "priority_label": "IGNORE",
        "reason": "Task name is not an exact Vapi deployment command."
    }


def suggested_route_from_contact_name(contact_name):
    text = normalize_text(contact_name)

    estate_words = [
        "ESTATE",
        "PROBATE",
        "HEIR",
        "HEIRS",
        "EXECUTOR",
        "ADMINISTRATOR",
        "SURROGATE",
        "INDEX"
    ]

    for word in estate_words:
        if word in text:
            return {
                "suggested_route": "Josh Estate",
                "suggested_agent": "Josh",
                "suggested_route_group": "SUGGESTED_JOSH_ESTATE",
                "route_reason": "Estate/probate-looking lead. Final route requires David command."
            }

    return {
        "suggested_route": "Michael Owner",
        "suggested_agent": "Michael",
        "suggested_route_group": "SUGGESTED_MICHAEL_OWNER",
        "route_reason": "Regular owner/property-looking lead. Final route requires David command."
    }


def normalize_phone_values(value):
    phones = []

    if not value:
        return phones

    if isinstance(value, str):
        phones.append(value)

    elif isinstance(value, dict):
        text = (
            value.get("Text")
            or value.get("Phone")
            or value.get("Value")
            or value.get("Number")
        )
        if text:
            phones.append(text)

    elif isinstance(value, list):
        for item in value:
            phones.extend(normalize_phone_values(item))

    return phones


def extract_phones_from_contact(contact):
    raw_values = []

    for key, value in contact.items():
        key_upper = normalize_text(key)

        if "PHONE" in key_upper or "MOBILE" in key_upper or "CELL" in key_upper:
            raw_values.append(value)

    phones = []
    for raw_value in raw_values:
        phones.extend(normalize_phone_values(raw_value))

    cleaned = []
    for phone in phones:
        digits = re.sub(r"\D", "", str(phone))

        if len(digits) == 10:
            cleaned.append("+1" + digits)
        elif len(digits) == 11 and digits.startswith("1"):
            cleaned.append("+" + digits)

    seen = set()
    unique = []
    for phone in cleaned:
        if phone not in seen:
            unique.append(phone)
            seen.add(phone)

    return unique


def mask_phone(phone):
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) >= 4:
        return "***" + digits[-4:]
    return "***"


def normalize_job_title_value(value):
    if not value:
        return None

    if isinstance(value, str):
        clean = value.strip()
        return clean if clean else None

    if isinstance(value, dict):
        text = (
            value.get("Text")
            or value.get("Value")
            or value.get("Title")
            or value.get("JobTitle")
            or value.get("Job Title")
        )
        if text and str(text).strip():
            return str(text).strip()

    if isinstance(value, list):
        for item in value:
            found = normalize_job_title_value(item)
            if found:
                return found

    return None


def extract_property_address_from_job_title_only(contact):
    possible_job_title_keys = [
        "JobTitle",
        "Job Title",
        "Title",
        "Job title",
        "job_title",
        "jobTitle"
    ]

    for key in possible_job_title_keys:
        if key in contact:
            found = normalize_job_title_value(contact.get(key))
            if found:
                return found

    for key, value in contact.items():
        if compact_text(key) == "JOBTITLE":
            found = normalize_job_title_value(value)
            if found:
                return found

    return None


def mask_address(address):
    text = str(address or "").strip()

    if not text:
        return None

    parts = text.replace("\n", ", ").split(",")

    if len(parts) >= 2:
        return "JOB_TITLE_ADDRESS_FOUND: " + ", ".join(parts[1:]).strip()

    return "JOB_TITLE_ADDRESS_FOUND"


def get_contacts_by_ids(contact_ids):
    if not contact_ids:
        return {
            "ok": True,
            "data": []
        }

    return call_lacrm("GetContactsById", {
        "ContactIds": contact_ids,
        "MaxNumberOfResults": 10000
    })


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


def build_task_buckets():
    today = date.today()

    start_date = today - timedelta(days=90)
    end_date = today

    calendars_result = call_lacrm("GetCalendars")

    if not calendars_result["ok"]:
        return {
            "ok": False,
            "step_failed": "GetCalendars",
            "error": calendars_result["error"]
        }

    calendars = extract_results(calendars_result["data"])

    lead_tasks_calendar = None
    calendar_names_found = []

    for calendar in calendars:
        calendar_name = calendar.get("Name", "")
        calendar_names_found.append(calendar_name)

        if normalize_text(calendar_name) == "LEAD TASKS":
            lead_tasks_calendar = calendar
            break

    if not lead_tasks_calendar:
        return {
            "ok": False,
            "step_failed": "Find LEAD TASKS calendar",
            "error": {
                "lead_tasks_calendar_found": False,
                "calendar_names_found": calendar_names_found
            }
        }

    lead_tasks_calendar_id = lead_tasks_calendar.get("CalendarId")

    tasks_result = get_due_tasks(start_date, end_date)

    if not tasks_result["ok"]:
        return {
            "ok": False,
            "step_failed": "GetTasks",
            "error": tasks_result["error"]
        }

    tasks = tasks_result["data"]

    lead_section_tasks = []
    deploy_eligible_tasks = []
    manual_rels_pc_tasks = []
    ignored_tasks = []

    vapi_new_count = 0
    vapi_regular_count = 0

    for task in tasks:
        if task.get("CalendarId") != lead_tasks_calendar_id:
            continue

        lead_section_tasks.append(task)

        task_name = task.get("Name", "")
        classification = classify_task_exact_name_only(task_name)

        contact_name = task.get("ContactMetaData", {}).get("Name") or ""
        route_suggestion = suggested_route_from_contact_name(contact_name)

        task_preview = {
            "priority_order": classification["priority_order"],
            "priority_label": classification["priority_label"],
            "bucket": classification["bucket"],
            "reason": classification["reason"],
            "task_id": task.get("TaskId"),
            "task_name": task_name,
            "due_date": task.get("DueDate"),
            "contact_id": task.get("ContactId"),
            "contact_id_present": bool(task.get("ContactId")),
            "contact_name": contact_name,

            "suggested_route": route_suggestion["suggested_route"],
            "suggested_agent": route_suggestion["suggested_agent"],
            "suggested_route_group": route_suggestion["suggested_route_group"],
            "route_reason": route_suggestion["route_reason"],
            "final_route_requires_david_command": True
        }

        if classification["bucket"] == "deploy":
            if classification["priority_label"] == "VAPI NEW":
                vapi_new_count += 1

            if classification["priority_label"] == "VAPI":
                vapi_regular_count += 1

            deploy_eligible_tasks.append(task_preview)

        elif classification["bucket"] == "manual_sms_only":
            manual_rels_pc_tasks.append(task_preview)

        else:
            ignored_tasks.append(task_preview)

    deploy_eligible_tasks.sort(key=lambda item: (
        item.get("priority_order", 99),
        item.get("due_date") or "",
        item.get("contact_name") or ""
    ))

    return {
        "ok": True,
        "today": today.isoformat(),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "lead_tasks_calendar": lead_tasks_calendar,
        "tasks": tasks,
        "lead_section_tasks": lead_section_tasks,
        "deploy_eligible_tasks": deploy_eligible_tasks,
        "manual_rels_pc_tasks": manual_rels_pc_tasks,
        "ignored_tasks": ignored_tasks,
        "vapi_new_count": vapi_new_count,
        "vapi_regular_count": vapi_regular_count
    }


def build_contact_rows():
    buckets = build_task_buckets()

    if not buckets["ok"]:
        return buckets

    deploy_tasks = buckets["deploy_eligible_tasks"]

    contact_ids = []
    for task in deploy_tasks:
        contact_id = task.get("contact_id")
        if contact_id and contact_id not in contact_ids:
            contact_ids.append(contact_id)

    contacts_result = get_contacts_by_ids(contact_ids)

    if not contacts_result["ok"]:
        return {
            "ok": False,
            "step_failed": "GetContactsById",
            "error": contacts_result["error"]
        }

    contacts = extract_results(contacts_result["data"])
    contacts_by_id = {}

    for contact in contacts:
        contact_id = contact.get("ContactId")
        if contact_id:
            contacts_by_id[contact_id] = contact

    rows = []

    missing_phone_count = 0
    missing_address_count = 0
    callable_count = 0
    skip_count = 0

    for task in deploy_tasks:
        contact_id = task.get("contact_id")
        contact = contacts_by_id.get(contact_id, {})

        phones = extract_phones_from_contact(contact)
        property_address = extract_property_address_from_job_title_only(contact)

        warnings = []
        status = "CALLABLE"

        if not phones:
            warnings.append("MISSING_PHONE")
            missing_phone_count += 1
            status = "SKIP_UNTIL_FIXED"

        if not property_address:
            warnings.append("MISSING_JOB_TITLE_ADDRESS")
            missing_address_count += 1
            status = "SKIP_UNTIL_FIXED"

        if status == "CALLABLE":
            callable_count += 1
        else:
            skip_count += 1

        rows.append({
            "status": status,
            "priority_label": task.get("priority_label"),
            "task_name": task.get("task_name"),
            "due_date": task.get("due_date"),
            "contact_name": task.get("contact_name"),

            "suggested_route": task.get("suggested_route"),
            "suggested_agent": task.get("suggested_agent"),
            "suggested_route_group": task.get("suggested_route_group"),
            "route_reason": task.get("route_reason"),
            "final_route_requires_david_command": True,
            "override_note": "David can command Josh Estate, Michael Owner, Mark, Margaret, or Skip.",

            "phones_found_count": len(phones),
            "phone_last4_preview": [mask_phone(phone) for phone in phones[:5]],

            "address_source": "Job Title only",
            "address_found": bool(property_address),
            "address_preview": mask_address(property_address) if property_address else None,

            "warnings": warnings,
            "task_id": task.get("task_id"),
            "contact_id": contact_id,
            "contact_id_present": bool(contact_id)
        })

    callable_rows = [row for row in rows if row["status"] == "CALLABLE"]
    skip_rows = [row for row in rows if row["status"] != "CALLABLE"]

    suggested_josh_estate_rows = [
        row for row in callable_rows
        if row["suggested_route_group"] == "SUGGESTED_JOSH_ESTATE"
    ]

    suggested_michael_owner_rows = [
        row for row in callable_rows
        if row["suggested_route_group"] == "SUGGESTED_MICHAEL_OWNER"
    ]

    return {
        "ok": True,
        "buckets": buckets,
        "contacts_requested": len(contact_ids),
        "contacts_returned": len(contacts),
        "rows": rows,
        "callable_rows": callable_rows,
        "skip_rows": skip_rows,
        "suggested_josh_estate_rows": suggested_josh_estate_rows,
        "suggested_michael_owner_rows": suggested_michael_owner_rows,
        "missing_phone_count": missing_phone_count,
        "missing_address_count": missing_address_count,
        "callable_count": callable_count,
        "skip_count": skip_count
    }


def name_matches(contact_name, name_list):
    contact_upper = str(contact_name or "").upper()
    for name in name_list:
        if name and name in contact_upper:
            return True
    return False


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


@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "service": "vapi-crm-dispatcher",
        "mode": "dry-run"
    })


@app.route("/health")
def health():
    return jsonify({
        "healthy": True
    })


@app.route("/config-check")
def config_check():
    lacrm_key = os.getenv("LACRM_API_KEY")
    vapi_key = os.getenv("VAPI_API_KEY")

    return jsonify({
        "lacrm_api_key_loaded": bool(lacrm_key),
        "vapi_api_key_loaded": bool(vapi_key),
        "safe": "No secret values are displayed."
    })


@app.route("/morning-report")
def morning_report():
    report = build_contact_rows()

    if not report["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "morning-report",
            "step_failed": report["step_failed"],
            "error": report["error"]
        }), 500

    buckets = report["buckets"]

    return jsonify({
        "crm_connection": "ok",
        "mode": "morning-report",
        "source": "Your Workspace → Tasks that are due → LEAD TASKS",
        "date_filter": "overdue_plus_today",
        "date_range_checked": {
            "start": buckets["start_date"],
            "end": buckets["end_date"]
        },
        "routing_rule": "Suggested route only. Final route requires David command.",
        "summary": {
            "vapi_tasks_due": len(buckets["deploy_eligible_tasks"]),
            "callable_leads": report["callable_count"],
            "skip_until_fixed": report["skip_count"],
            "missing_phone_count": report["missing_phone_count"],
            "missing_job_title_address_count": report["missing_address_count"],
            "suggested_josh_estate_leads": len(report["suggested_josh_estate_rows"]),
            "suggested_michael_owner_leads": len(report["suggested_michael_owner_rows"]),
            "vapi_new_priority_tasks": buckets["vapi_new_count"],
            "regular_vapi_tasks": buckets["vapi_regular_count"]
        },
        "route_command_labels": [
            "Josh Estate",
            "Michael Owner",
            "Mark",
            "Margaret",
            "Skip"
        ],
        "skip_until_fixed_preview": report["skip_rows"][:25],
        "suggested_josh_estate_preview": report["suggested_josh_estate_rows"][:50],
        "suggested_michael_owner_preview": report["suggested_michael_owner_rows"][:50],
        "safe": "Morning report only. No campaigns were created. No Vapi calls were made."
    })


@app.route("/deploy-plan")
def deploy_plan():
    report = build_contact_rows()

    if not report["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "deploy-plan",
            "step_failed": report["step_failed"],
            "error": report["error"]
        }), 500

    buckets = report["buckets"]

    return jsonify({
        "crm_connection": "ok",
        "mode": "deploy-plan",
        "plan_type": "dry-run-only",
        "source": "Your Workspace → Tasks that are due → LEAD TASKS",
        "date_filter": "overdue_plus_today",
        "date_range_checked": {
            "start": buckets["start_date"],
            "end": buckets["end_date"]
        },
        "deployment_status": "WAITING_FOR_DAVID_COMMAND",
        "start_time_status": "NOT_SET",
        "start_time": None,
        "routing_rule": "Suggested route only. Final route requires David command.",
        "campaign_spacing_rule": {
            "campaign_window_minutes": 5,
            "gap_between_campaigns_minutes": 5,
            "example_if_david_says_start_at_9am": [
                "9:00-9:05 first approved campaign batch",
                "9:10-9:15 second approved campaign batch"
            ]
        },
        "summary": {
            "vapi_tasks_due": len(buckets["deploy_eligible_tasks"]),
            "callable_leads": report["callable_count"],
            "skip_until_fixed": report["skip_count"],
            "suggested_josh_estate_batch_count": len(report["suggested_josh_estate_rows"]),
            "suggested_michael_owner_batch_count": len(report["suggested_michael_owner_rows"]),
            "mark_batch_count": 0,
            "margaret_batch_count": 0,
            "campaigns_ready_to_create": 0,
            "campaigns_created": 0,
            "vapi_calls_made": 0
        },
        "planned_batches_waiting_for_david_command": [
            {
                "batch_label": "Josh Estate",
                "suggested_agent": "Josh",
                "status": "WAITING_FOR_DAVID_COMMAND",
                "lead_count": len(report["suggested_josh_estate_rows"]),
                "preview": report["suggested_josh_estate_rows"][:50]
            },
            {
                "batch_label": "Michael Owner",
                "suggested_agent": "Michael",
                "status": "WAITING_FOR_DAVID_COMMAND",
                "lead_count": len(report["suggested_michael_owner_rows"]),
                "preview": report["suggested_michael_owner_rows"][:50]
            },
            {
                "batch_label": "Mark",
                "suggested_agent": "Mark",
                "status": "DAVID_COMMAND_ONLY",
                "lead_count": 0,
                "preview": []
            },
            {
                "batch_label": "Margaret",
                "suggested_agent": "Margaret",
                "status": "DAVID_COMMAND_ONLY",
                "lead_count": 0,
                "preview": []
            }
        ],
        "skipped_until_fixed": {
            "count": report["skip_count"],
            "preview": report["skip_rows"][:25]
        },
        "safe": "Dry run only. No campaigns were created. No Vapi calls were made."
    })


@app.route("/final-plan")
def final_plan():
    report = build_contact_rows()

    if not report["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "final-plan",
            "step_failed": report["step_failed"],
            "error": report["error"]
        }), 500

    start_time = request.args.get("start", "").strip()
    josh_estate = request.args.get("josh_estate", "no").strip().lower() in ["yes", "y", "true", "1"]
    michael_owner = request.args.get("michael_owner", "no").strip().lower() in ["yes", "y", "true", "1"]

    mark_names_raw = request.args.get("mark", "").strip()
    margaret_names_raw = request.args.get("margaret", "").strip()
    skip_names_raw = request.args.get("skip", "").strip()

    mark_names = [name.strip().upper() for name in mark_names_raw.split("|") if name.strip()]
    margaret_names = [name.strip().upper() for name in margaret_names_raw.split("|") if name.strip()]
    skip_names = [name.strip().upper() for name in skip_names_raw.split("|") if name.strip()]

    callable_rows = report["callable_rows"]

    josh_batch = []
    michael_batch = []
    mark_batch = []
    margaret_batch = []
    extra_skip_batch = []

    for row in callable_rows:
        contact_name = row.get("contact_name", "")

        if name_matches(contact_name, skip_names):
            extra_skip_batch.append(row)
            continue

        if name_matches(contact_name, mark_names):
            mark_batch.append(row)
            continue

        if name_matches(contact_name, margaret_names):
            margaret_batch.append(row)
            continue

        if row.get("suggested_route_group") == "SUGGESTED_JOSH_ESTATE" and josh_estate:
            josh_batch.append(row)
            continue

        if row.get("suggested_route_group") == "SUGGESTED_MICHAEL_OWNER" and michael_owner:
            michael_batch.append(row)
            continue

    approved_batches = []

    if josh_batch:
        approved_batches.append({
            "campaign_order": len(approved_batches) + 1,
            "batch_label": "Josh Estate",
            "agent": "Josh",
            "lead_count": len(josh_batch),
            "preview": josh_batch[:50]
        })

    if michael_batch:
        approved_batches.append({
            "campaign_order": len(approved_batches) + 1,
            "batch_label": "Michael Owner",
            "agent": "Michael",
            "lead_count": len(michael_batch),
            "preview": michael_batch[:50]
        })

    if mark_batch:
        approved_batches.append({
            "campaign_order": len(approved_batches) + 1,
            "batch_label": "Mark",
            "agent": "Mark",
            "lead_count": len(mark_batch),
            "preview": mark_batch[:50]
        })

    if margaret_batch:
        approved_batches.append({
            "campaign_order": len(approved_batches) + 1,
            "batch_label": "Margaret",
            "agent": "Margaret",
            "lead_count": len(margaret_batch),
            "preview": margaret_batch[:50]
        })

    schedule_preview = []
    if start_time:
        for batch in approved_batches:
            schedule_preview.append({
                "campaign_order": batch["campaign_order"],
                "batch_label": batch["batch_label"],
                "agent": batch["agent"],
                "lead_count": batch["lead_count"],
                "schedule_note": f"Starts at {start_time}, then follows 5-minute window plus 5-minute gap rule.",
                "status": "FINAL_APPROVAL_STILL_REQUIRED"
            })

    return jsonify({
        "crm_connection": "ok",
        "mode": "final-plan",
        "plan_type": "final-dry-run-only",
        "deployment_status": "FINAL_APPROVAL_STILL_REQUIRED",
        "start_time": start_time if start_time else None,
        "start_time_status": "SET" if start_time else "MISSING",
        "control_rule": "This is the final dry-run plan only. It does not create campaigns.",
        "routing_rule": "Only routes David approved in the URL are included.",
        "david_command_received": {
            "josh_estate": josh_estate,
            "michael_owner": michael_owner,
            "mark_names": mark_names,
            "margaret_names": margaret_names,
            "extra_skip_names": skip_names
        },
        "summary": {
            "callable_leads_total": report["callable_count"],
            "broken_skipped_until_fixed": report["skip_count"],
            "extra_skipped_by_david": len(extra_skip_batch),
            "josh_estate_final_batch_count": len(josh_batch),
            "michael_owner_final_batch_count": len(michael_batch),
            "mark_final_batch_count": len(mark_batch),
            "margaret_final_batch_count": len(margaret_batch),
            "approved_campaign_batches": len(approved_batches),
            "campaigns_created": 0,
            "vapi_calls_made": 0
        },
        "schedule_preview": schedule_preview,
        "approved_batches_preview": approved_batches,
        "skipped": {
            "broken_until_fixed": {
                "count": report["skip_count"],
                "preview": report["skip_rows"][:25]
            },
            "extra_skipped_by_david": {
                "count": len(extra_skip_batch),
                "preview": extra_skip_batch[:50]
            }
        },
        "next_step_after_this_is_correct": "Build real campaign creation only after David says final approved.",
        "safe": "Final dry run only. No campaigns were created. No Vapi calls were made."
    })


@app.route("/test-campaign-preview", methods=["GET"])
def test_campaign_preview():
    """Read CRM and build masked previews only; never invoke a Vapi write."""
    result = {
        "mode": "test-campaign-preview",
        "status": "PREVIEW_ONLY",
        "final_approval_required": True,
        "campaigns_created": 0,
        "vapi_calls_made": 0,
        "start_time": None,
        "timing": {
            "status": "NOT_SCHEDULED",
            "may_start_immediately_after_creation": True,
            "note": "The current test payload has no scheduled start time. Treat FINAL creation as potentially starting calls immediately.",
            "spacing": "No five-minute window or gap is configured for these test campaigns.",
        },
        "selection_rule": "First callable Josh Estate lead and first callable Michael Owner lead, using each contact's first phone number, as in the existing test action.",
        "snapshot_warning": "This preview does not reserve leads or approve anything. The existing test action reads CRM again; leads or phone numbers may change before creation.",
        "command_only_agents": ["Mark", "Margaret"],
        "test_leads": [],
        "safe": "Read-only preview. No campaigns were created. No Vapi calls were made. Query parameters cannot approve or schedule anything on this page.",
    }

    def respond(status_code):
        response = jsonify(result)
        response.headers["Cache-Control"] = "no-store"
        return response, status_code

    try:
        report = build_contact_rows()
        if not report["ok"]:
            result.update(status="PREVIEW_UNAVAILABLE", reason="Unable to read CRM. No test leads have been confirmed.")
            return respond(502)

        for label, agent_name, key in (
            ("Josh Estate", "Josh", "suggested_josh_estate_rows"),
            ("Michael Owner", "Michael", "suggested_michael_owner_rows"),
        ):
            rows = report[key]
            if not rows:
                result.update(status="PREVIEW_INCOMPLETE", reason="Both test groups require a callable lead.")
                result["test_leads"].append({"batch_label": label, "status": "NO_CALLABLE_LEAD"})
                continue

            # Same read-only payload builder as creation, but never return its
            # private payload, raw CRM errors, phone IDs, or assistant IDs.
            prepared = build_single_test_campaign_payload(label, agent_name, rows[0])
            if not prepared["ok"]:
                result.update(status="PREVIEW_INCOMPLETE", reason="A selected contact could not be validated. No test is ready for approval.")
                result["test_leads"].append({"batch_label": label, "status": "CONTACT_VALIDATION_FAILED"})
                continue

            preview = prepared["safe_preview"]
            result["test_leads"].append({
                "batch_label": label,
                "agent": agent_name,
                "lead_name": preview["lead_name"],
                "phone_last4": preview["phone_last4"],
                "address_found": preview["address_found"],
                "status": "WAITING_FOR_DAVID_REVIEW",
            })
    except Exception:
        # Do not expose exception strings: CRM failures can include private data.
        result.update(status="PREVIEW_UNAVAILABLE", reason="Unable to complete the CRM preview. No test leads have been confirmed.")
        result["test_leads"] = []
        return respond(502)

    return respond(200 if result["status"] == "PREVIEW_ONLY" else 409)


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
