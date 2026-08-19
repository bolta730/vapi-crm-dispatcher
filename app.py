@app.route("/final-plan")
def final_plan():
    from flask import request

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

    def name_matches(contact_name, name_list):
        contact_upper = str(contact_name or "").upper()
        for name in name_list:
            if name and name in contact_upper:
                return True
        return False

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
        for index, batch in enumerate(approved_batches):
            schedule_preview.append({
                "campaign_order": batch["campaign_order"],
                "batch_label": batch["batch_label"],
                "agent": batch["agent"],
                "lead_count": batch["lead_count"],
                "schedule_note": f"Starts at {start_time}, then follows 5-minute window plus 5-minute gap rule. Exact clock-time math will be finalized before real campaign creation.",
                "status": "FINAL_APPROVAL_STILL_REQUIRED"
            })

    return jsonify({
        "crm_connection": "ok",
        "mode": "final-plan",
        "plan_type": "final-dry-run-only",

        "deployment_status": "FINAL_APPROVAL_STILL_REQUIRED",
        "start_time": start_time if start_time else None,
        "start_time_status": "SET" if start_time else "MISSING",

        "control_rule": "This is the final dry-run plan only. It does not create campaigns. David must still give final approval before any Vapi campaign creation endpoint is built or used.",
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

        "next_step_after_this_is_correct": "Build the real campaign creation endpoint only after David reviews this final dry-run and says final approved.",

        "safe": "Final dry run only. No campaigns were created. No Vapi calls were made."
    })
