import os
import re
from datetime import date, timedelta

import requests
from flask import Flask, jsonify

app = Flask(__name__)

LACRM_API_URL = "https://api.lessannoyingcrm.com/v2/"


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

        data = response.json()

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

    manual_rels_pc_tasks.sort(key=lambda item: (
        item.get("due_date") or "",
        item.get("contact_name") or ""
    ))

    ignored_tasks.sort(key=lambda item: (
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
    suggested_josh_estate_count = 0
    suggested_michael_owner_count = 0
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

        if task.get("suggested_route_group") == "SUGGESTED_JOSH_ESTATE":
            suggested_josh_estate_count += 1

        if task.get("suggested_route_group") == "SUGGESTED_MICHAEL_OWNER":
            suggested_michael_owner_count += 1

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
        "suggested_josh_estate_count": suggested_josh_estate_count,
        "suggested_michael_owner_count": suggested_michael_owner_count,
        "callable_count": callable_count,
        "skip_count": skip_count
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


@app.route("/crm-test")
def crm_test():
    buckets = build_task_buckets()

    if not buckets["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "read-only",
            "step_failed": buckets["step_failed"],
            "error": buckets["error"]
        }), 500

    return jsonify({
        "crm_connection": "ok",
        "mode": "read-only",

        "source": "workspace_lead_tasks_due",
        "date_filter": "overdue_plus_today",
        "date_range_checked": {
            "start": buckets["start_date"],
            "end": buckets["end_date"]
        },
        "calendar_filter": "LEAD TASKS",
        "classification_source": "exact_task_name_only",

        "lead_tasks_calendar_found": True,
        "lead_tasks_calendar_name": buckets["lead_tasks_calendar"].get("Name"),

        "total_incomplete_tasks_checked": len(buckets["tasks"]),
        "lead_task_section_tasks_found": len(buckets["lead_section_tasks"]),

        "vapi_new_tasks_found": buckets["vapi_new_count"],
        "vapi_regular_tasks_found": buckets["vapi_regular_count"],
        "deploy_eligible_vapi_tasks_found": len(buckets["deploy_eligible_tasks"]),

        "manual_rels_pc_tasks_blacklisted_from_vapi": len(buckets["manual_rels_pc_tasks"]),
        "ignored_tasks_found": len(buckets["ignored_tasks"]),

        "allowed_deploy_task_names": [
            "VAPI",
            "VAPI NEW",
            "VAPI RELS PC",
            "VAPI NEW RELS PC"
        ],

        "rules": [
            "Reads Your Workspace LEAD TASKS due list.",
            "Date range is overdue plus today.",
            "Only exact task name commands deploy.",
            "Task notes/descriptions do not trigger Vapi deployment.",
            "Plain RELS PC is manual SMS/broadcast only and blacklisted from Vapi.",
            "AUDIT DEPLOY VAPI, VAPI REVIEW, and other non-exact names are ignored.",
            "Route labels are suggestions only until David commands final routing."
        ],

        "deploy_eligible_vapi_tasks_preview": buckets["deploy_eligible_tasks"][:75],
        "manual_rels_pc_blacklist_preview": buckets["manual_rels_pc_tasks"][:25],
        "ignored_tasks_preview": buckets["ignored_tasks"][:25],

        "safe": "No campaigns were created. No secret values are displayed."
    })


@app.route("/contact-dry-run")
def contact_dry_run():
    report = build_contact_rows()

    if not report["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "contact-dry-run",
            "step_failed": report["step_failed"],
            "error": report["error"]
        }), 500

    return jsonify({
        "crm_connection": "ok",
        "mode": "contact-dry-run",

        "source": "workspace_lead_tasks_due",
        "date_filter": "overdue_plus_today",
        "calendar_filter": "LEAD TASKS",
        "classification_source": "exact_task_name_only",

        "address_rule": "Job Title is the only property address field.",
        "routing_rule": "Suggested route only. Final route requires David command.",

        "deploy_eligible_vapi_tasks_found": len(report["buckets"]["deploy_eligible_tasks"]),
        "contacts_requested": report["contacts_requested"],
        "contacts_returned": report["contacts_returned"],

        "missing_phone_count": report["missing_phone_count"],
        "missing_job_title_address_count": report["missing_address_count"],
        "suggested_josh_estate_count": report["suggested_josh_estate_count"],
        "suggested_michael_owner_count": report["suggested_michael_owner_count"],
        "callable_count": report["callable_count"],
        "skip_count": report["skip_count"],

        "rules": [
            "Plain RELS PC remains blacklisted from Vapi.",
            "Only exact VAPI and VAPI NEW task names are included.",
            "Property address is pulled from Job Title only.",
            "Josh Estate and Michael Owner are suggestions only.",
            "Mark and Margaret are only used when David commands it.",
            "No campaigns were created and no Vapi calls were made."
        ],

        "deploy_contact_preview": report["rows"][:75],

        "safe": "No campaigns were created. No Vapi calls were made. Full phone numbers are hidden on this public page."
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

        "control_rule": "Report only. David gives the deployment time, skips, and final routing before campaigns are created.",
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

        "suggested_routing_rules": [
            "Estate/probate-looking leads are suggested as Josh Estate.",
            "Regular owner/property-looking leads are suggested as Michael Owner.",
            "Mark is only used when David commands it.",
            "Margaret is only used when David commands it.",
            "Missing phone or missing Job Title address is skipped until fixed.",
            "These are suggestions only, not final routing."
        ],

        "next_instruction_needed_from_david": [
            "What time should the campaigns start?",
            "Should Josh Estate handle the suggested estate leads?",
            "Should Michael Owner handle the suggested regular owner leads?",
            "Any specific lead overrides to Mark?",
            "Any specific lead overrides to Margaret?",
            "Any leads to Skip even if callable?"
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

        "control_rule": "This is only a deployment-plan preview. David must give start time, final routing, skips, and final approval before campaigns are created.",
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
                "meaning": "These are estate/probate-looking callable leads. They only go to Josh if David commands Josh Estate.",
                "preview": report["suggested_josh_estate_rows"][:50]
            },
            {
                "batch_label": "Michael Owner",
                "suggested_agent": "Michael",
                "status": "WAITING_FOR_DAVID_COMMAND",
                "lead_count": len(report["suggested_michael_owner_rows"]),
                "meaning": "These are regular owner/property-looking callable leads. They only go to Michael if David commands Michael Owner.",
                "preview": report["suggested_michael_owner_rows"][:50]
            },
            {
                "batch_label": "Mark",
                "suggested_agent": "Mark",
                "status": "DAVID_COMMAND_ONLY",
                "lead_count": 0,
                "meaning": "Mark is only used when David names specific leads for Mark.",
                "preview": []
            },
            {
                "batch_label": "Margaret",
                "suggested_agent": "Margaret",
                "status": "DAVID_COMMAND_ONLY",
                "lead_count": 0,
                "meaning": "Margaret is only used when David names specific leads for Margaret.",
                "preview": []
            }
        ],

        "skipped_until_fixed": {
            "count": report["skip_count"],
            "meaning": "These will not be deployed unless missing phone/address is fixed.",
            "preview": report["skip_rows"][:25]
        },

        "next_command_needed_from_david": {
            "plain_english_example": "Start at 9 AM. Josh Estate yes. Michael Owner yes. No Mark. No Margaret. Skip broken leads.",
            "available_route_commands": [
                "Josh Estate",
                "Michael Owner",
                "Mark",
                "Margaret",
                "Skip"
            ],
            "required_before_real_campaign_creation": [
                "Start time",
                "Which suggested batches to approve",
                "Any named leads to Mark",
                "Any named leads to Margaret",
                "Any extra skips",
                "Final approval"
            ]
        },

        "safe": "Dry run only. No campaigns were created. No Vapi calls were made."
    })
