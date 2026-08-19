import os
from datetime import date

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


def get_today_tasks(today):
    all_tasks = []
    page = 1

    while True:
        result = call_lacrm("GetTasks", {
            "StartDate": today.isoformat(),
            "EndDate": today.isoformat(),
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
    """
    SAFETY RULE:
    Only the exact task NAME/TITLE controls Vapi deployment.

    These exact commands deploy:
    - VAPI
    - VAPI NEW
    - VAPI RELS PC
    - VAPI NEW RELS PC

    Plain RELS PC stays manual.
    Notes/descriptions are ignored.
    Anything else is ignored.
    """
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
    today = date.today()

    calendars_result = call_lacrm("GetCalendars")

    if not calendars_result["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "read-only",
            "step_failed": "GetCalendars",
            "error": calendars_result["error"]
        }), 500

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
        return jsonify({
            "crm_connection": "ok",
            "mode": "read-only",
            "source": "workspace_lead_tasks_due",
            "lead_tasks_calendar_found": False,
            "calendar_names_found": calendar_names_found,
            "safe": "No campaigns were created. No secret values are displayed."
        })

    lead_tasks_calendar_id = lead_tasks_calendar.get("CalendarId")

    tasks_result = get_today_tasks(today)

    if not tasks_result["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "read-only",
            "step_failed": "GetTasks",
            "error": tasks_result["error"]
        }), 500

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

        task_preview = {
            "priority_order": classification["priority_order"],
            "priority_label": classification["priority_label"],
            "bucket": classification["bucket"],
            "reason": classification["reason"],
            "task_id": task.get("TaskId"),
            "task_name": task_name,
            "due_date": task.get("DueDate"),
            "contact_id_present": bool(task.get("ContactId")),
            "contact_name": task.get("ContactMetaData", {}).get("Name")
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
        item.get("contact_name") or ""
    ))

    manual_rels_pc_tasks.sort(key=lambda item: (
        item.get("contact_name") or ""
    ))

    return jsonify({
        "crm_connection": "ok",
        "mode": "read-only",

        "source": "workspace_lead_tasks_due",
        "date_filter": "today_only",
        "date_checked": today.isoformat(),
        "calendar_filter": "LEAD TASKS",
        "classification_source": "exact_task_name_only",

        "lead_tasks_calendar_found": True,
        "lead_tasks_calendar_name": lead_tasks_calendar.get("Name"),

        "total_incomplete_tasks_checked_today": len(tasks),
        "lead_task_section_tasks_found_today": len(lead_section_tasks),

        "vapi_new_tasks_found": vapi_new_count,
        "vapi_regular_tasks_found": vapi_regular_count,
        "deploy_eligible_vapi_tasks_found": len(deploy_eligible_tasks),

        "manual_rels_pc_tasks_blacklisted_from_vapi": len(manual_rels_pc_tasks),
        "ignored_tasks_found": len(ignored_tasks),

        "allowed_deploy_task_names": [
            "VAPI",
            "VAPI NEW",
            "VAPI RELS PC",
            "VAPI NEW RELS PC"
        ],

        "rules": [
            "Only today's tasks.",
            "Only Your Workspace LEAD TASKS calendar.",
            "Only exact task name commands deploy.",
            "Task notes/descriptions do not trigger Vapi deployment.",
            "Plain RELS PC is manual SMS/broadcast only and blacklisted from Vapi.",
            "AUDIT DEPLOY VAPI, VAPI REVIEW, and other non-exact names are ignored."
        ],

        "deploy_eligible_vapi_tasks_preview": deploy_eligible_tasks[:50],
        "manual_rels_pc_blacklist_preview": manual_rels_pc_tasks[:15],
        "ignored_tasks_preview": ignored_tasks[:25],

        "safe": "No campaigns were created. No secret values are displayed."
    })
