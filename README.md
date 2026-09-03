# vapi-crm-dispatcher

## Controlled production launcher

`GET /launch-campaigns` is blocked unless `approve=FINAL`, a `start`, and
`josh_estate=yes` and/or `michael_owner=yes` are supplied. Never visit a fully
approved URL until David has reviewed `/campaign-preview` and approved launch.

`start=9AM` means today at 9 AM America/New_York. Optional `date=YYYY-MM-DD`
selects a future day. Past times, DST-ambiguous times, and starts less than two
minutes away or more than seven days away are rejected. The resolved timestamp
is sent as `schedulePlan.earliestAt`, not merely used as a name label.

Each selected batch gets one Vapi V2 campaign, concurrency 1 per campaign,
using the preview's callable groups and the existing first-phone selection.
Per-customer variable overrides carry each lead's name and cleaned Job Title
address. All payloads are prepared before creation. Missing data is excluded;
an empty selected batch or CRM read failure blocks the whole request.
The existing preview and test routes are unchanged.

Safety checks (no campaign creation): omit approval; omit start with approval;
or supply approval and start with no batches. Local tests mock Vapi and block
network access: `python -m unittest -v`.

Important: FINAL is a confirmation gate, not authentication. Anyone with access
to this endpoint can supply it. Do not share complete launch URLs or refresh
them: requests are not deduplicated across launches. After a timeout or partial
result, inspect Vapi before retrying. This endpoint does not verify recipient
consent; David must approve the audience and calling time before launch.
