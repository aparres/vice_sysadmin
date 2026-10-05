# Iteration 3.4: policy rejection feedback

## Assumptions and risks

Base: the local project with iterations 3.1, 3.2 and 3.3 applied. This deliverable
is a diff only. No project execution, tests, installs, LXD or model calls occurred.
The supplied prompt reports t1-1-install-configure-service failing in condition B
at calibration N=15, batch 1, after three repeated chained-command rejections and
an irrelevant escalation. The live log was not supplied for independent review.
Source inspection confirms the missing feedback consistent with that report.

Current orchestrator.py lines 201-203 are:

```python
            if decision.decision == "reject":
                self.observations.append({"status": "Command blocked by policy"})
                continue
```

The policy event already records the reason. The next proposer prompt receives
only the placeholder, so it cannot use the specific rejection reason to correct
its command. This affects feedback before audit equally in A/B/C and can confound
H1/H2 comparisons. The patch does not establish that a live model will self-correct.

## Full evaluate() audit and safe handling

All explicit normal returns have non-empty reasons:

- disabled: Policy is disabled
- chaining rejection: Disallowed shell composition
- pattern rejection: Forbidden pattern matched
- executable rejection: Forbidden executable
- approval: No blocking rule matched

There is also a parse_error rejection with reason=str(exc), catching ValueError
and re.error. Contrary to the prompt's premise, this is not always a fixed generic
message. An invalid named-group reference can expose a policy pattern fragment in
its diagnostic. PolicySettings does not validate/compile forbidden_patterns.
No current return explicitly supplies None or an empty reason; PolicyDecision
requires a string. The exception-derived string is not constrained to be nonempty,
and the patch defensively omits an empty/None reason if one is ever supplied.

To honor the explicit no-raw-policy-internals requirement, parse_error observations
retain the original placeholder. This is a deliberate narrow exception to forwarding
decision.reason: forwarding that branch verbatim would risk disclosure. The rule
is inspected only to identify that exception; it is never sent to the proposer.
All other rejection reasons are added as the reason field next to the unchanged
status. decision.rule and decision.flags are excluded entirely: rules can embed
raw regexes and flags are internal annotations. No sanitized category is added.
Policy evaluation and its logged rule/reason/flags remain unchanged, including
full parse diagnostics in JSONL. Reason strings should be reviewed if future
policy implementations change what they contain.

## Scope and regression coverage

The reject branch still continues to the next step. No prompt files, schema fields,
configuration, approval/escalation paths, accounting, adjudication or lifecycle
behavior change. Feedback stays in the existing untrusted observation block.
There is no new repair call or retry mechanism. Existing tests remain unchanged.

New offline tests cover chaining/executable/pattern rejection feedback in A/B/C,
raw pattern/flag exclusion, invalid-regex diagnostics, empty/None fallback, full
policy-event JSONL bytes reconstructed from the original Event contract with a
zero-time fake clock, unchanged approval observations, and escalation reason
forwarding. evaluate() currently has no escalate return, so its supported decision
is injected for that regression. A scripted multi-step scenario verifies the next
prompt contains the reason and that split commands pass policy/audit/execution.
These tests are authored only, NOT RUN; no passing count is claimed.

release-manifest.json remains the prior release snapshot, not an integrity manifest
for the tree after applying this targeted patch.
