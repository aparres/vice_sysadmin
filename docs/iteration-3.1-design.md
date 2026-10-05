# Iteration 3.1: completion after execution

## Assumptions and risks

The local iteration-3 source is the patch baseline. The supplied prompt reports
two live pilot reproductions; those logs were not supplied or independently
verified here. Source inspection confirms the reported control-flow defect.
Tests are authored, NOT RUN. No Python/project, installation, LXD, Git command,
or model API execution was performed for this patch.

Before modification, vice_sysadmin/orchestrator.py lines 157-159 read:

```python
            if proposal.task_completed:
                self.reason = "task_completed"
                return
```

Policy evaluation was at line 165, after this unconditional completion return.
Thus a non-empty command bundled with completion skipped review and execution.
It did not execute an unreviewed command; it omitted the proposed action, which
can confound H1/H2 comparisons by omitting the relevant policy/audit decision
and causing verification to fail independently of reviewer quality.

Existing fail-closed rules take priority: conflicting questions, unauthorized
elevation, policy/auditor rejection, escalation denial, budget exhaustion,
runtime errors, output caps and timeouts keep their existing behavior. A rejected
command cannot finish the task through its completion claim. An ordinary nonzero
exit status still permits a completion claim after observation; the independent
verification checks determine outcome. This does not make completion proof of
success or change adjudication.

## Corrected rule

For a non-empty command, task_completed only decides whether the loop continues
after its step's command has been fully policed/audited/executed and observed,
never before. Policy rejection still prevents audit; auditor review is applicable
only to B/C. Existing gates remain in their original order. Empty-string completion
still goes directly to verification; operator questions retain precedence.
Whitespace-only strings are non-empty and therefore reach the existing policy
validator instead of bypassing it through completion.

## Prompt provenance

The explicitly permitted in-place re-hash option was chosen: proposer.v1.txt and
prompt_version=v1 remain valid. PromptBook already hashes exact file bytes and
run_experiment already records those hashes in run_manifest; no loader, schema,
logging-field, CLI, or configuration changes are needed.

- Previous proposer SHA-256: 03f339b363e85b8920bc70f5193b255140224eb233d2ba6102cbd6eb54774195
- Updated proposer SHA-256: 39d8aa4d4ce9445ce6b2fd8808cc10ed466be62cc4e5894d90a61a015317e6f6

Compare hashes, not only the v1 label, when grouping pre/post-patch experiments.
The prompt now describes unconditional evaluation of non-empty commands and an
empty command plus task_completed=true for completion without another action.

## Added regression coverage (not executed)

- A/B/C: command plus completion follows the same review/execution event order
  as command without completion; verification follows execution and no extra
  proposer call occurs after completed execution.
- Policy, auditor and elevation rejection override completion; a one-step budget
  exposes continuation as step_limit, not task_completed.
- Empty completion skips the pipeline; empty questions preserve both denied and
  approved escalation behavior even with a simultaneous completion claim.
- Execution timeout/output cap take priority over completion.
- Deterministic B/C proposer and auditor prompts remain byte-identical; current
  exact-file hashes appear in repetition provenance and the runner manifest.

Existing tests, task catalog, dependencies, schemas, container lifecycle and CLI
are preserved byte-for-byte. The new tests reuse the existing Scenario helper.
Review the diff, then run the full test suite on the operator's environment before
repeating the live pilot. No passing-test count is claimed for this release.
