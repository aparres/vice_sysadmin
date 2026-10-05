# Iteration 3.2: one repair for conflicting proposals

## Assumptions and risks

Baseline: local iteration 3.1, including its completion-ordering fix. The supplied
prompt reports pilot failures and earlier passing tests; those results were not
independently verified. No project code, tests, installation, LXD operations or
model calls were executed. This deliverable is a diff only, not an applied patch.

Before patching, orchestrator.py lines 147-152 were:

```python
            if question:
                if proposal.command.strip():
                    self.reason = "conflicting_proposal"
                    self.emit("proposal_blocked", command=proposal.command,
                              error="Clarification must have an empty command")
                    return
```

This confirms an immediate exit before policy/auditor dispatch, with no repair.
Failures from this format issue can confound H1/H2 comparisons by removing the
opportunity to review the proposed action in every condition, including A.

## Repair budget and behavior

Use one independent semantic-repair counter per step, with no new config field.
The existing invalid_json_retries field is constrained to Literal[1], and the
structured helper implements one parse/schema repair. That normal path remains
unchanged. A parse repair may therefore precede the one semantic repair.

The first conflicting proposal emits proposal and proposal_blocked as before.
The proposer is called once more in the SAME step, using the original task prompt
plus the explicit command/question rule and the existing repair.v1.txt renderer.
Rejected proposal data stays inside the existing untrusted-data delimiters.
A corrected command proceeds through the existing gates, including the 3.1
completion rule; a corrected question uses existing escalation. Another conflict
emits proposal_blocked and ends as conflicting_proposal. Invalid JSON/schema or a
transport failure on that call ends as proposer_failure, with no nested repair or
transport retry. Budget limits and logging failures retain their existing priority.

request_structured accepts internal attempt_offset and max_repairs parameters;
existing callers retain their defaults. Each call still reserves, charges and
logs through the same code, and attempt numbers continue within the step. The
semantic counter resets at each new step, while global token/cost/time limits do
not reset. One extra call increases cost and latency; it never bypasses approval.

Prompt files are unchanged. Their existing hashes remain in run_manifest; the
full appended repair prompt and its SHA-256 are logged in proposer_attempt. No
schema fields, event names/fields, CLI, dependencies, adjudication or lifecycle
are changed. All pre-existing test files remain unchanged.

## Authored validation (not executed)

New tests cover A/B/C command repair, question repair with approval or denial,
repeated conflicts, invalid JSON/schema, transport failure without another retry,
normal proposals, independent parse and semantic budgets, charged token/cost sums,
auditor rejection, and byte-identical deterministic B/C repair prompts.
The earlier suites, including test_iteration31_completion.py, must also be run by
the operator. No passing count is claimed.

The diff adds this note and a vibe-coding row. release-manifest.json remains the
prior release snapshot: this is a targeted patch, not a repackaged release. It is
not an integrity manifest for the patched tree. No configuration files are edited,
so operator-specific pilot models/prices are preserved.
