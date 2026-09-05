# vapi-crm-dispatcher

## Controlled production launcher

`GET /launch-campaigns` is blocked unless `approve=FINAL`, a `start`, and
`josh_estate=yes` and/or `michael_owner=yes` are supplied. Never visit a fully
approved URL until David has reviewed `/campaign-preview` and approved launch.

`start=9AM` means today at 9 AM America/New_York. Optional `date=YYYY-MM-DD`
selects a future day. Optional `gap=5` controls the whole-minute spacing between
contact campaigns and defaults to five minutes. Past times, DST-ambiguous times,
and starts less than two minutes away or more than seven days away are rejected.
Each resolved timestamp is sent as `schedulePlan.earliestAt`.

Each callable CRM contact gets its own Vapi V2 campaign with every valid phone
number belonging to that contact. Campaigns remain concurrency 1 and are ordered
Josh Estate contacts first, followed by Michael Owner contacts. Per-customer
variable overrides carry the contact's name and cleaned Job Title address. All
payloads are prepared before creation. Missing data is excluded; an empty
selected route group or CRM read failure blocks the whole request.

`GET /campaign-preview` remains read-only. Its `planned_campaigns` list shows one
entry per contact, the route and assistant, all masked phone last-four values,
and campaign order. Add `start`, optional `date`, and optional `gap` to preview
the exact campaign names and scheduled timestamps without creating anything.

Safety checks (no campaign creation): omit approval; omit start with approval;
or supply approval and start with no batches. Local tests mock Vapi and block
network access: `python -m unittest -v`.

Important: FINAL is a confirmation gate, not authentication. Anyone with access
to this endpoint can supply it. Do not share complete launch URLs or refresh
them: requests are not deduplicated across launches. After a timeout or partial
result, inspect Vapi before retrying. This endpoint does not verify recipient
consent; David must approve the audience and calling time before launch.

## Read-only post-call report

`GET /post-call-report` accepts `josh_campaign_id`, `michael_campaign_id`, or
both. It reads each Vapi campaign and its call records, then returns campaign
counters plus masked per-call results. Phone numbers are limited to the last
four digits; transcripts and recording URLs are reported only as available or
unavailable.

This route contains only Vapi GET requests. It cannot create campaigns, place
calls, or update Vapi configuration. A request with no campaign IDs returns a
helpful 400 response before reading Vapi.

## Read-only call QA report

`GET /call-qa-report?call_ids=ID1,ID2,ID3` reads up to 50 Vapi call records and
returns masked customer identity, status and end reason, duration, voicemail and
human-answer indicators, recording/transcript availability, bounded transcript
and message detail, and Vapi's summary/success evaluation when available.

The endpoint uses only Vapi GET requests. It cannot create campaigns or calls,
or modify prompts, settings, transfers, or routing. Missing, malformed,
duplicate, repeated, and unknown parameters are rejected before any Vapi read.
