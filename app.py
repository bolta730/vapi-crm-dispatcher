import os
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


def get_all_tasks(start_date, end_date):
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


def classify_task_by_name_only(task_name):
    """
    SAFETY RULE:
    Only the task NAME/TITLE controls deployment.

    Task notes/descriptions are ignored for Vapi eligibility.

    Plain RELS PC is blacklisted from Vapi unless VAPI is written
    directly in the task name.
    """
    name_text = normalize_text(task_name)
    compact_name = name_text.replace(" ", "")

    has_vapi_new = (
        "VAPI NEW" in name_text
        or "VAPINEW" in compact_name
    )

    has_vapi = (
        "VAPI" in name_text
        or "VAPI" in compact_name
    )

    has_rels_pc = (
        "RELS PC" in name_text
        or "RELSPC" in compact_name
    )

    # Highest priority Vapi queue:
    # VAPI NEW
    # vapi new
    # VAPI NEW RELS PC
    if has_vapi_new:
        return {
            "bucket": "deploy",
            "qualified_for_vapi": True,
            "priority_order": 1,
            "priority_label": "VAPI NEW",
            "reason": "Highest priority Vapi deployment task. Task name contains VAPI NEW."
        }

    # Regular Vapi queue:
    # VAPI
    # vapi
    # VAPI RELS PC
    if has_vapi:
        return {
            "bucket": "deploy",
            "qualified_for_vapi": True,
            "priority_order": 2,
            "priority_label": "VAPI",
            "reason": "Regular Vapi deployment task. Task name contains VAPI."
        }

    # Plain RELS PC:
    # manual SMS / broadcast only.
    # blacklisted from Vapi.
    if has_rels_pc:
        return {
            "bucket": "manual_sms_only",
            "qualified_for_vapi": False,
            "priority_order": 50,
            "priority_label": "RELS PC BLACKLISTED",
            "reason": "Plain RELS PC is manual SMS/broadcast only. Ignored by Vapi dispatcher."
        }

    return {
        "bucket": "ignore",
        "qualified_for_vapi": False,
        "priority_order": 99,
        "priority_label": "IGNORE",
        "reason": "Task name does not contain VAPI, VAPI NEW, or RELS PC."
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

    # Workspace "tasks that are due" can include overdue tasks,
    # so we check 90 days back plus 7 days forward.
    start_date = today - timedelta(days=90)
    end_date = today + timedelta(days=7)

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
            "lead_tasks_calendar_found": False,
            "calendar_names_found": calendar_names_found,
            "safe": "No campaigns were created. No secret values are displayed."
        })

    lead_tasks_calendar_id = lead_tasks_calendar.get("CalendarId")

    tasks_result = get_all_tasks(start_date, end_date)

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
        task_calendar_id = task.get("CalendarId")

        if task_calendar_id != lead_tasks_calendar_id:
            continue

        lead_section_tasks.append(task)

        task_name = task.get("Name", "")

        # IMPORTANT:
        # Deployment classification uses task name only.
        # Description/note is intentionally ignored for safety.
        classification = classify_task_by_name_only(task_name)

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

    # VAPI NEW comes first, then regular VAPI.
    deploy_eligible_tasks.sort(key=lambda item: (
        item.get("priority_order", 99),
        item.get("due_date") or "",
        item.get("contact_name") or ""
    ))

    manual_rels_pc_tasks.sort(key=lambda item: (
        item.get("due_date") or "",
        item.get("contact_name") or ""
    ))

    return jsonify({
        "crm_connection": "ok",
        "mode": "read-only",
        "classification_source": "task_name_only",
        "lead_tasks_calendar_found": True,
        "lead_tasks_calendar_name": lead_tasks_calendar.get("Name"),
        "date_range_checked": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat()
        },
        "total_incomplete_tasks_checked": len(tasks),
        "lead_task_section_tasks_found": len(lead_section_tasks),

        "vapi_new_tasks_found": vapi_new_count,
        "vapi_regular_tasks_found": vapi_regular_count,
        "deploy_eligible_vapi_tasks_found": len(deploy_eligible_tasks),

        "manual_rels_pc_tasks_blacklisted_from_vapi": len(manual_rels_pc_tasks),

        "rules": [
            "Only task name/title controls Vapi deployment.",
            "Task notes/descriptions do not trigger Vapi deployment.",
            "VAPI NEW = deploy first.",
            "VAPI = regular deploy.",
            "Plain RELS PC = manual SMS/broadcast only and blacklisted from Vapi.",
            "VAPI RELS PC = deploy eligible because VAPI is in the task name.",
            "VAPI NEW RELS PC = deploy eligible first because VAPI NEW is in the task name."
        ],

        "deploy_eligible_vapi_tasks_preview": deploy_eligible_tasks[:25],
        "manual_rels_pc_blacklist_preview": manual_rels_pc_tasks[:10],
        "ignored_tasks_preview": ignored_tasks[:10],

        "safe": "No campaigns were created. No secret values are displayed."
    })
