# Monitor: ten evaluated commands and rejection reasons

Baseline: locally applied iteration 3.10. This diff changes only the read-only
monitor, its tests and documentation. No experiment, schema or logging change.

The existing policy-event history grows from five to ten rows, newest first.
Rows continue to join by task, condition, repetition and step within the run.
A remains N/A; B/C verdicts still come exclusively from audit events. Policy
rejections are not auditor rejections. Attempts do not add rows.

ETA alone uses HH:MM:SS, rounded upward to whole seconds, with hours allowed
above 24. Missing samples/backlog/failure keep the existing unavailable state;
completed runs show 00:00:00. ETA arithmetic and stall detection are unchanged.

The audit event has no justification field. For a retained B/C row, the monitor
parses the latest auditor_attempt.raw_response using the existing strict Audit
schema and the same optional single JSON code-fence syntax as structured.py.
Only a nonempty rejection justification is retained as a candidate. Transport
errors, malformed responses, invalid-output events and failed audits clear it;
a new attempt always replaces it. It becomes visible only when the subsequent
audit event confirms reject. Approve/escalate do not display it. This is a
best-effort join of existing redacted logs, not new adjudication. If redaction
makes JSON invalid or the response is absent, show unavailable, never infer a
reason from a prior attempt or a proposer prompt.

Reasons appear under the corresponding command, as JSON-escaped previews capped
at 600 source characters with an explicit truncation suffix. Commands retain
their 240-character limit. Terminal controls, newlines and bidi controls are
escaped. Only the justification is shown, never the entire raw response, risk
metadata, prompts, stdout or stderr. Existing logger redaction remains the
source of secret protection; the monitor adds no general-purpose redactor.

Incremental reads, partial-line handling, CLI and experiment isolation remain
unchanged. The bounded deque stores only ten command/reason previews and pending
candidates. No extra scans, network calls, writes or experiment signals occur.

Tests authored, NOT RUN: ETA boundaries/over-24-hours, ten-entry eviction,
plain/fenced valid responses, confirmation gating, attempt replacement,
invalid/missing/failed responses, identity isolation, escaped/truncated reasons,
and partial-line updates without modifying input logs. Prior ETA assertion is
updated for the requested format. No passing count is claimed. The release
manifest remains the older release snapshot. This diff is not yet applied locally.
