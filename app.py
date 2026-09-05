import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

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

    primary_url = f"{VAPI_API_URL}/v2/campaign"

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


def call_vapi_read(path):
    """Read one Vapi resource. Reporting code must never use a write method."""
    api_key = os.getenv("VAPI_API_KEY")

    if not api_key:
        return {
            "ok": False,
            "error": "Missing VAPI_API_KEY in Render environment variables."
        }

    try:
        response = requests.get(
            f"{VAPI_API_URL}/{path.lstrip('/')}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        try:
            data = response.json()
        except Exception:
            data = None

        if response.status_code >= 400:
            return {
                "ok": False,
                "status_code": response.status_code,
                "error": "Vapi could not return the requested read-only resource.",
            }

        return {"ok": True, "data": data}
    except Exception:
        return {
            "ok": False,
            "error": "Vapi could not be reached for the read-only report.",
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
    exact_vapi_new_commands = {
        "VAPI NEW",
        "VAPI NEW RELS PC",
    }

    exact_vapi_commands = {
        "VAPI",
        "VAPI RELS PC",
    }

    rels_pc_manual_commands = {
        "RELS PC",
    }

    if name in exact_vapi_new_commands:
        return {
            "bucket": "deploy",
            "priority_order": 1,
            "priority_label": "VAPI NEW",
            "reason": "Exact task name is VAPI NEW or VAPI NEW RELS PC."
        }

    if name in exact_vapi_commands:
        return {
            "bucket": "deploy",
            "priority_order": 2,
            "priority_label": "VAPI",
            "reason": "Exact task name is VAPI or VAPI RELS PC."
        }

    if name in rels_pc_manual_commands:
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


def clean_address_for_speech(address):
    """Format the spoken address without changing the source CRM value."""
    text = str(address or "").strip()
    if re.fullmatch(r"\d{5}(?:-\d{4})?", text):
        return ""
    # Preserve a leading five-digit street number; remove postal ZIP/ZIP+4.
    text = re.sub(r"(?!\A)\b\d{5}(?:-\d{4})?\b", "", text)
    text = re.sub(r"\bNY\b", "New York", text, flags=re.IGNORECASE)
    text = re.sub(r"([^,\s])\s+New York\s*$", r"\1, New York", text)
    parts = [re.sub(r"\s+", " ", part).strip()
             for part in re.split(r"[,\r\n]+", text)]
    parts = [part for part in parts if part]
    if len(parts) >= 3 or (len(parts) == 2 and parts[1].lower() != "new york"):
        return parts[0] + " in " + ", ".join(parts[1:])
    return ", ".join(parts)


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
                "property_address": clean_address_for_speech(property_address)
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


def public_campaign_preview_row(row):
    """Return only the masked fields David needs for launch review."""
    return {
        "lead_name": row.get("contact_name"),
        "task_name": row.get("task_name"),
        "due_date": row.get("due_date"),
        "suggested_route": row.get("suggested_route"),
        "phone_last4": row.get("phone_last4_preview", []),
        "address_preview": row.get("address_preview"),
        "warnings": row.get("warnings", []),
    }


@app.route("/campaign-preview", methods=["GET"])
def campaign_preview():
    """Production CRM preview. This endpoint has no Vapi write path."""
    response_body = {
        "status": "READ_ONLY_PREVIEW",
        "source": "Your Workspace → Tasks that are due → LEAD TASKS",
        "included_task_names": ["VAPI", "VAPI NEW", "VAPI RELS PC", "VAPI NEW RELS PC"],
        "blacklisted_manual_only": ["RELS PC"],
        "summary": {
            "total_vapi_tasks": 0,
            "callable_leads": 0,
            "josh_estate_count": 0,
            "michael_owner_count": 0,
            "skipped_count": 0,
        },
        "route_groups": {
            "Josh Estate": {"status": "SUGGESTED_ROUTE", "count": 0, "leads": []},
            "Michael Owner": {"status": "SUGGESTED_ROUTE", "count": 0, "leads": []},
            "Mark": {"status": "DAVID_COMMAND_ONLY", "count": 0, "leads": []},
            "Margaret": {"status": "DAVID_COMMAND_ONLY", "count": 0, "leads": []},
        },
        "skipped_leads": [],
        "safety": [
            "READ_ONLY_PREVIEW",
            "No campaigns were created.",
            "No Vapi calls were made.",
        ],
    }

    def respond(status_code):
        response = jsonify(response_body)
        response.headers["Cache-Control"] = "no-store"
        return response, status_code

    try:
        report = build_contact_rows()
        if not report["ok"]:
            response_body["status"] = "PREVIEW_UNAVAILABLE"
            response_body["reason"] = "Unable to read CRM. No launch preview is available."
            return respond(502)

        buckets = report["buckets"]
        josh_rows = report["suggested_josh_estate_rows"]
        michael_rows = report["suggested_michael_owner_rows"]

        response_body["summary"] = {
            "total_vapi_tasks": len(buckets["deploy_eligible_tasks"]),
            "callable_leads": report["callable_count"],
            "josh_estate_count": len(josh_rows),
            "michael_owner_count": len(michael_rows),
            "skipped_count": report["skip_count"],
        }
        response_body["route_groups"]["Josh Estate"].update(
            count=len(josh_rows),
            leads=[public_campaign_preview_row(row) for row in josh_rows],
        )
        response_body["route_groups"]["Michael Owner"].update(
            count=len(michael_rows),
            leads=[public_campaign_preview_row(row) for row in michael_rows],
        )
        response_body["skipped_leads"] = [
            public_campaign_preview_row(row) for row in report["skip_rows"]
        ]
    except Exception:
        # CRM exceptions can contain private values, so never return their text.
        response_body["status"] = "PREVIEW_UNAVAILABLE"
        response_body["reason"] = "Unable to complete the CRM preview."
        return respond(502)

    return respond(200)


def resolve_launch_start(start, launch_date=None):
    """Clock times mean today in New York, never an implicit next-day launch."""
    now = datetime.now(ZoneInfo("America/New_York"))
    match = re.fullmatch(r"(1[0-2]|[1-9])(?::([0-5][0-9]))?(AM|PM)", start.upper())
    if not match:
        raise ValueError("Use a start such as 9AM or 2:30PM.")
    day = date.fromisoformat(launch_date) if launch_date else now.date()
    hour = int(match[1]) % 12 + (12 if match[3] == "PM" else 0)
    scheduled = datetime(day.year, day.month, day.day, hour, int(match[2] or 0),
                         tzinfo=now.tzinfo)
    # Reject ambiguous/nonexistent DST clock times instead of guessing.
    if scheduled.utcoffset() != scheduled.replace(fold=1).utcoffset():
        raise ValueError("Choose a time outside the daylight-saving clock change.")
    if scheduled <= now + timedelta(minutes=2) or scheduled > now + timedelta(days=7):
        raise ValueError("Start must be more than two minutes ahead and within seven days; use date=YYYY-MM-DD for a future day.")
    return scheduled


@app.route("/launch-campaigns", methods=["GET"])
def launch_campaigns():
    body = {
        "status": "BLOCKED", "created_campaigns": [], "campaign_ids": [],
        "selected_batches": [], "lead_counts": {},
        "selected_start_time": request.args.get("start"),
        "timezone": "America/New_York", "scheduled_start": None,
        "skipped_count": 0, "crm_read": False,
        "safety_note": "No campaigns were created. No Vapi calls were made.",
    }

    def respond(status, code, reason=None):
        body["status"] = status
        if reason:
            body["reason"] = reason
        response = jsonify(body)
        response.headers["Cache-Control"] = "no-store"
        return response, code

    # Gate before any CRM read or Vapi write, including implicit Flask HEAD.
    if request.method != "GET" or request.args.getlist("approve") != ["FINAL"]:
        return respond("BLOCKED", 403, "Explicit FINAL approval is required.")
    start = request.args.get("start", "").strip()
    if not start:
        return respond("MISSING_START_TIME", 400)
    batches = [
        ("josh_estate", "Josh Estate", "Josh", "suggested_josh_estate_rows"),
        ("michael_owner", "Michael Owner", "Michael", "suggested_michael_owner_rows"),
    ]
    selected = [batch for batch in batches if request.args.getlist(batch[0]) == ["yes"]]
    body["selected_batches"] = [batch[1] for batch in selected]
    if not selected:
        return respond("NO_BATCH_SELECTED", 400)
    allowed = {"approve", "start", "date", "josh_estate", "michael_owner"}
    if any(key not in allowed or len(request.args.getlist(key)) != 1 for key in request.args):
        return respond("INVALID_PARAMETERS", 400, "Unsupported or repeated launch parameters.")
    if any(request.args.get(key, "no") not in ("yes", "no") for key in ("josh_estate", "michael_owner")):
        return respond("INVALID_PARAMETERS", 400, "Batch flags must be yes or no.")
    try:
        scheduled = resolve_launch_start(start, request.args.get("date"))
    except ValueError as exc:
        return respond("INVALID_START_TIME", 400, str(exc))
    body["scheduled_start"] = scheduled.isoformat()
    payloads = []
    try:
        report = build_contact_rows()
        if not report["ok"]:
            return respond("CRM_UNAVAILABLE", 502)
        body["crm_read"] = True
        body["skipped_count"] = report["skip_count"]
        for _, label, agent, rows_key in selected:
            customers = []
            for row in report[rows_key]:
                if row.get("status") != "CALLABLE":
                    continue
                private = get_contact_private_data(row["contact_id"])
                if not private["ok"]:
                    return respond("CRM_UNAVAILABLE", 502)
                address = clean_address_for_speech(private["property_address"])
                if not private["phones"] or not address:
                    body["skipped_count"] += 1
                    continue
                name = row.get("contact_name") or ""
                customers.append({
                    "number": private["phones"][0], "name": name[:40],
                    "assistantOverrides": {"variableValues": {
                        "name": name, "property_address": address,
                    }},
                })
            body["lead_counts"][label] = len(customers)
            if not customers:
                return respond("NO_CALLABLE_LEADS", 409, f"No callable leads in {label}; nothing launched.")
            if len(customers) > 10000:
                return respond("BATCH_TOO_LARGE", 400)
            payloads.append((label, {
                "name": f"LIVE - {label} - {scheduled.date().isoformat()} - start {start}",
                "assistantId": ASSISTANT_IDS[agent],
                "phoneNumberId": VAPI_PHONE_NUMBER_ID,
                "customers": customers, "maxConcurrency": 1,
                "schedulePlan": {"earliestAt": scheduled.astimezone(timezone.utc).isoformat()},
            }))
    except Exception:
        return respond("FAILED_BEFORE_VAPI", 502, "Unable to prepare all selected batches; nothing launched.")
    # Prepare every batch before the first write. Never retry an uncertain create.
    for label, payload in payloads:
        if scheduled <= datetime.now(timezone.utc) + timedelta(minutes=1):
            return respond("START_TIME_EXPIRED", 409, "Start is too close; check any already-created campaigns before retrying.")
        body["safety_note"] = "Vapi creation attempted. Do not retry this URL: check Vapi first to avoid duplicate campaigns. Mark and Margaret excluded."
        try:
            result = call_vapi_create_campaign(payload)
            campaign_id = result.get("data", {}).get("id") if result.get("ok") else None
            if not campaign_id:
                body["failed_batch"] = label
                return respond("PARTIAL_OR_UNCONFIRMED", 502, "Stopped after an unsuccessful or uncertain Vapi response; no automatic retry.")
        except Exception:
            body["failed_batch"] = label
            return respond("PARTIAL_OR_UNCONFIRMED", 502, "Creation outcome uncertain; check Vapi before retrying.")
        body["campaign_ids"].append(campaign_id)
        body["created_campaigns"].append({
            "campaign_id": campaign_id, "batch_label": label,
            "name": payload["name"], "lead_count": len(payload["customers"]),
            "selected_start_time": start, "scheduled_start": scheduled.isoformat(),
        })
    return respond("SUCCESS", 200)


POST_CALL_SAFETY = [
    "READ_ONLY_POST_CALL_REPORT",
    "No campaigns were created.",
    "No Vapi calls were made.",
]

CALL_QA_SAFETY = [
    "READ_ONLY_CALL_QA_REPORT",
    "Only Vapi call records were read.",
    "No campaigns were created.",
    "No calls were made.",
    "No Vapi prompts, settings, transfers, or routing were changed.",
]

MAX_QA_CALL_IDS = 50
MAX_QA_TRANSCRIPT_CHARS = 30000
MAX_QA_MESSAGES = 250
MAX_QA_MESSAGE_CHARS = 4000


def valid_uuid(value):
    try:
        return str(UUID(value)) == value.lower()
    except (AttributeError, TypeError, ValueError):
        return False


def last_four(number):
    digits = re.sub(r"\D", "", str(number or ""))
    return digits[-4:] if digits else None


def duration_seconds(call):
    try:
        started = datetime.fromisoformat(call["startedAt"].replace("Z", "+00:00"))
        ended = datetime.fromisoformat(call["endedAt"].replace("Z", "+00:00"))
        return max(0, round((ended - started).total_seconds(), 2))
    except (AttributeError, KeyError, TypeError, ValueError):
        return None


def campaign_call_ids(campaign):
    calls = campaign.get("calls") or {}
    if isinstance(calls, dict):
        return list(calls.keys())
    if isinstance(calls, list):
        return [item.get("id") if isinstance(item, dict) else item for item in calls]
    return []


def public_call_report(call_id, result):
    if not result.get("ok") or not isinstance(result.get("data"), dict):
        return {
            "call_id": call_id,
            "status": "READ_ERROR",
            "lead_customer_name": None,
            "phone_last4": None,
            "duration_seconds": None,
            "transcript_available": False,
            "recording_url_available": False,
            "answered_human": None,
            "error_message": result.get("error", "Unable to read call details."),
        }

    call = result["data"]
    customer = call.get("customer") if isinstance(call.get("customer"), dict) else {}
    artifact = call.get("artifact") if isinstance(call.get("artifact"), dict) else {}
    transcript = artifact.get("transcript") or call.get("transcript")
    messages = artifact.get("messages") or call.get("messages") or []
    has_customer_speech = any(
        isinstance(message, dict)
        and str(message.get("role") or message.get("speaker") or "").lower()
        in {"user", "customer"}
        and bool(message.get("message") or message.get("content") or message.get("text"))
        for message in messages
    )
    ended_reason = call.get("endedReason")
    voicemail = str(ended_reason or "").lower() == "voicemail"
    failed = call.get("status") == "failed" or any(
        marker in str(ended_reason or "").lower()
        for marker in ("error", "failed", "rejected", "blocked", "invalid", "not-found")
    )
    return {
        "call_id": call.get("id") or call_id,
        "status": call.get("status"),
        "ended_reason": ended_reason,
        "lead_customer_name": customer.get("name"),
        "phone_last4": last_four(customer.get("number")),
        "started_at": call.get("startedAt"),
        "ended_at": call.get("endedAt"),
        "duration_seconds": duration_seconds(call),
        "voicemail": voicemail,
        "answered_human": has_customer_speech if not voicemail else False,
        "failed": failed,
        "transcript_available": bool(transcript),
        "recording_url_available": bool(
            artifact.get("recordingUrl")
            or artifact.get("stereoRecordingUrl")
            or call.get("recordingUrl")
        ),
        "error_message": call.get("endedMessage"),
    }


def qa_message(message):
    """Return only conversation fields useful for QA, excluding tool/config data."""
    if not isinstance(message, dict):
        return None
    text = message.get("message") or message.get("content") or message.get("text")
    if isinstance(text, list):
        text = " ".join(
            str(part.get("text") or "") if isinstance(part, dict) else str(part)
            for part in text
        ).strip()
    if not isinstance(text, str) or not text.strip():
        return None
    return {
        "role": message.get("role") or message.get("speaker"),
        "text": text[:MAX_QA_MESSAGE_CHARS],
        "time_seconds": message.get("secondsFromStart") or message.get("time"),
        "duration_seconds": message.get("duration"),
    }


def public_call_qa_report(call_id, result):
    """Build a bounded, phone-masked diagnostic view of one Vapi call."""
    base = public_call_report(call_id, result)
    if not result.get("ok") or not isinstance(result.get("data"), dict):
        return {
            **base,
            "voicemail": None,
            "failed": True,
            "customer_speech_detected": None,
            "assistant_speech_detected": None,
            "transcript": None,
            "transcript_truncated": False,
            "messages": [],
            "analysis": {},
        }

    call = result["data"]
    artifact = call.get("artifact") if isinstance(call.get("artifact"), dict) else {}
    raw_messages = artifact.get("messages") or call.get("messages") or []
    messages = [item for item in (qa_message(message) for message in raw_messages) if item]
    transcript = artifact.get("transcript") or call.get("transcript")
    transcript = transcript if isinstance(transcript, str) else None
    analysis = call.get("analysis") if isinstance(call.get("analysis"), dict) else {}
    roles = {str(item.get("role") or "").lower() for item in messages}
    return {
        **base,
        "customer_speech_detected": bool(roles & {"user", "customer"}),
        "assistant_speech_detected": bool(roles & {"assistant", "bot"}),
        "transcript": transcript[:MAX_QA_TRANSCRIPT_CHARS] if transcript else None,
        "transcript_truncated": bool(transcript and len(transcript) > MAX_QA_TRANSCRIPT_CHARS),
        "messages": messages[:MAX_QA_MESSAGES],
        "messages_truncated": len(messages) > MAX_QA_MESSAGES,
        "analysis": {
            "summary": analysis.get("summary"),
            "success_evaluation": analysis.get("successEvaluation"),
        },
    }


def build_post_call_campaign_report(batch_label, campaign_id):
    campaign_result = call_vapi_read(f"campaign/{campaign_id}")
    if not campaign_result.get("ok") or not isinstance(campaign_result.get("data"), dict):
        return {
            "campaign_id": campaign_id,
            "batch_label": batch_label,
            "campaign_name": None,
            "campaign_status": "READ_ERROR",
            "total_calls": 0,
            "completed_calls": 0,
            "voicemail_count": None,
            "answered_human_calls": None,
            "failed_calls": 0,
            "total_duration_seconds": None,
            "call_ids": [],
            "calls": [],
            "error_messages": [campaign_result.get("error", "Unable to read campaign.")],
        }

    campaign = campaign_result["data"]
    call_ids = [call_id for call_id in campaign_call_ids(campaign) if isinstance(call_id, str)]
    calls = []
    if call_ids:
        with ThreadPoolExecutor(max_workers=min(8, len(call_ids))) as executor:
            futures = {
                executor.submit(call_vapi_read, f"call/{call_id}"): call_id
                for call_id in call_ids
            }
            for future in as_completed(futures):
                call_id = futures[future]
                try:
                    calls.append(public_call_report(call_id, future.result()))
                except Exception:
                    calls.append(public_call_report(call_id, {"ok": False}))
        order = {call_id: index for index, call_id in enumerate(call_ids)}
        calls.sort(key=lambda item: order.get(item["call_id"], len(order)))

    durations = [item["duration_seconds"] for item in calls if item["duration_seconds"] is not None]
    human_values = [item.get("answered_human") for item in calls]
    errors = [item["error_message"] for item in calls if item.get("error_message")]
    total_calls = len(call_ids) or len(campaign.get("customers") or [])
    return {
        "campaign_id": campaign.get("id") or campaign_id,
        "campaign_name": campaign.get("name"),
        "batch_label": batch_label,
        "campaign_status": campaign.get("status"),
        "total_calls": total_calls,
        "completed_calls": campaign.get("callsCounterEnded", sum(item.get("status") == "ended" for item in calls)),
        "voicemail_count": campaign.get("callsCounterEndedVoicemail"),
        "answered_human_calls": (
            sum(value is True for value in human_values)
            if any(value is not None for value in human_values) else None
        ),
        "failed_calls": sum(item.get("failed") is True or item.get("status") == "READ_ERROR" for item in calls),
        "total_duration_seconds": round(sum(durations), 2) if durations else None,
        "average_duration_seconds": round(sum(durations) / len(durations), 2) if durations else None,
        "call_ids": call_ids,
        "calls": calls,
        "error_messages": errors,
    }


@app.route("/post-call-report", methods=["GET"])
def post_call_report():
    """Read-only AM/PM campaign results with phone numbers always masked."""
    body = {
        "status": "READ_ONLY_POST_CALL_REPORT",
        "campaign_ids": {},
        "campaigns": [],
        "safety": POST_CALL_SAFETY,
    }

    def respond(code):
        response = jsonify(body)
        response.headers["Cache-Control"] = "no-store"
        return response, code

    allowed = {"josh_campaign_id", "michael_campaign_id"}
    if any(key not in allowed or len(request.args.getlist(key)) != 1 for key in request.args):
        body.update(
            status="INVALID_PARAMETERS",
            error="Use each supported campaign ID parameter at most once.",
        )
        return respond(400)

    requested = [
        ("Josh Estate", request.args.get("josh_campaign_id", "").strip()),
        ("Michael Owner", request.args.get("michael_campaign_id", "").strip()),
    ]
    requested = [(label, campaign_id) for label, campaign_id in requested if campaign_id]
    if not requested:
        body.update(
            status="MISSING_CAMPAIGN_IDS",
            error="Provide josh_campaign_id, michael_campaign_id, or both.",
            example="/post-call-report?josh_campaign_id=<campaign-uuid>&michael_campaign_id=<campaign-uuid>",
        )
        return respond(400)
    if any(not valid_uuid(campaign_id) for _, campaign_id in requested):
        body.update(status="INVALID_CAMPAIGN_ID", error="Each campaign ID must be a valid UUID.")
        return respond(400)

    body["campaign_ids"] = {
        "josh_campaign_id" if label == "Josh Estate" else "michael_campaign_id": campaign_id
        for label, campaign_id in requested
    }
    body["campaigns"] = [
        build_post_call_campaign_report(label, campaign_id)
        for label, campaign_id in requested
    ]
    if any(campaign["campaign_status"] == "READ_ERROR" for campaign in body["campaigns"]):
        body["status"] = "REPORT_PARTIAL_OR_UNAVAILABLE"
        return respond(502)
    return respond(200)


@app.route("/call-qa-report", methods=["GET"])
def call_qa_report():
    """Read-only call detail for QA; never returns a full customer phone number."""
    body = {
        "status": "READ_ONLY_CALL_QA_REPORT",
        "requested_call_ids": [],
        "calls": [],
        "safety": CALL_QA_SAFETY,
    }

    def respond(code):
        response = jsonify(body)
        response.headers["Cache-Control"] = "no-store"
        return response, code

    if set(request.args) - {"call_ids"} or len(request.args.getlist("call_ids")) > 1:
        body.update(
            status="INVALID_PARAMETERS",
            error="Provide exactly one call_ids parameter containing comma-separated call UUIDs.",
        )
        return respond(400)

    raw_ids = request.args.get("call_ids", "")
    call_ids = [value.strip() for value in raw_ids.split(",") if value.strip()]
    if not call_ids:
        body.update(
            status="MISSING_CALL_IDS",
            error="Provide one or more comma-separated Vapi call IDs in call_ids.",
            example="/call-qa-report?call_ids=<call-uuid-1>,<call-uuid-2>",
        )
        return respond(400)
    if len(call_ids) > MAX_QA_CALL_IDS:
        body.update(
            status="TOO_MANY_CALL_IDS",
            error=f"Provide no more than {MAX_QA_CALL_IDS} call IDs per report.",
        )
        return respond(400)
    if len(set(call_ids)) != len(call_ids):
        body.update(status="DUPLICATE_CALL_ID", error="Each call ID may appear only once.")
        return respond(400)
    if any(not valid_uuid(call_id) for call_id in call_ids):
        body.update(status="INVALID_CALL_ID", error="Each call ID must be a valid UUID.")
        return respond(400)

    body["requested_call_ids"] = call_ids
    with ThreadPoolExecutor(max_workers=min(8, len(call_ids))) as executor:
        futures = {
            executor.submit(call_vapi_read, f"call/{call_id}"): call_id
            for call_id in call_ids
        }
        reports = {}
        for future in as_completed(futures):
            call_id = futures[future]
            try:
                reports[call_id] = public_call_qa_report(call_id, future.result())
            except Exception:
                reports[call_id] = public_call_qa_report(call_id, {"ok": False})
    body["calls"] = [reports[call_id] for call_id in call_ids]
    if any(item["status"] == "READ_ERROR" for item in body["calls"]):
        body["status"] = "REPORT_PARTIAL_OR_UNAVAILABLE"
        return respond(502)
    return respond(200)


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
