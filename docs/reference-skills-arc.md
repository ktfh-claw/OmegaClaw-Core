# ARC evaluation skills

`arc-read exact_proxy_url` performs a read-only GET through the fixed capability
proxy. It accepts only `/arc/v1/evaluation/tasks/next`,
`/arc/v1/evaluation/tasks`, or `/arc/v1/evaluation/tasks/<8-lowercase-hex-id>` at
`http://172.17.0.1:18080`; query strings, redirects, and other origins or paths
are rejected. Responses are capped at 256 KiB.

`arc-submit exact_submission_proxy_url json_body` POSTs only to
`/arc/v1/evaluation/tasks/<8-lowercase-hex-id>/submissions` on that same proxy. The body
must contain exactly `outputs` (a list of one to three rectangular ARC color
grids, at most 30x30 with cells 0–9) and `reasoning` (1–10,000 characters).
Requests are capped at 64 KiB. The skill supplies the proxy's fixed submission
intent marker but no bearer credential. The marker is not authorization: the
host campaign controller remains the sole authority that can permit a submit.

Both skills return `ARC_READ_OK` / `ARC_SUBMIT_OK` on success and clear
`*_FAILED` text for validation, proxy, size, or network failures.
