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
    end_date = today + timedelta(days=7)

    result = call_lacrm("GetTasks", {
        "StartDate": today.isoformat(),
        "EndDate": end_date.isoformat(),
        "CompletionStatus": "Incomplete",
        "MaxNumberOfResults": 50
    })

    if not result["ok"]:
        return jsonify({
            "crm_connection": "failed",
            "mode": "read-only",
            "error": result["error"]
        }), 500

    data = result["data"]
    tasks = data.get("Results", [])

    qualifying_tasks = []
    for task in tasks:
        task_name = task.get("Name", "")
        upper_name = task_name.upper()

        if "VAPI" in upper_name or "RELSPC" in upper_name:
            qualifying_tasks.append({
                "task_id": task.get("TaskId"),
                "task_name": task_name,
                "due_date": task.get("DueDate"),
                "contact_id_present": bool(task.get("ContactId")),
                "contact_name": task.get("ContactMetaData", {}).get("Name")
            })

    return jsonify({
        "crm_connection": "ok",
        "mode": "read-only",
        "date_range_checked": {
            "start": today.isoformat(),
            "end": end_date.isoformat()
        },
        "total_incomplete_tasks_checked": len(tasks),
        "qualifying_vapi_relspc_tasks_found": len(qualifying_tasks),
        "qualifying_tasks_preview": qualifying_tasks[:20],
        "safe": "No campaigns were created. No secret values are displayed."
    })
