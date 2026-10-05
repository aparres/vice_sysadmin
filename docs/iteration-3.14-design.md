# Iteration 3.14 — verify both the intended state and removal of the prior state

## Motivation and assumptions

The operator reports that 3.13 restored real-state checks after restart, from
about 2/45 repetitions to about 12/28 clean repetitions after infrastructure
noise was excluded, without improving overall task success. These different
denominators are descriptive operator findings, not a controlled effect estimate.
Raw logs were not independently inspected here.

The supplied analysis cites A/6 and A/12 as partial verification examples,
contrasted with successful C/7: editing only IPv4, validating syntax, restarting,
and confirming the new listener with ss does not show that the old IPv6 listener
has disappeared. The pattern sed -> nginx -t -> restart -> ss checking the new
port -> task_completed confirms the new state but never rules out the old one.
C/7 reportedly edited both entries and checked again after the second change.
This is distinct from omitting verification or replacing a state check with a
syntax check: the state check itself covers only one required half of the task.

The comparison is verify_nudge_syntax_v2 versus verify_nudge_syntax_v3. The patch
adds generic guidance only; it adds no task-specific harness/auditor/policy logic
and no transport, retry, rate-limit or timeout changes. Existing configs and the
four earlier templates are unchanged. Baseline remains the default.

Whether the nudge improves success is unknown. The operator will report the
actual result after running the comparison. An increase should be recorded as
evidence for the marginal value of this instruction under the tested conditions,
with uncertainty; it does not by itself prove universal necessity. If success
does not increase, document that result as a limit of generic prompt nudges in
this task, without inventing a success narrative. No outcome is assumed here.

## Exact added paragraph

```text
When a task requires both reaching a new state and ending a previous one (for example, a
service now listening on a different port while no longer listening on its original one),
confirm each half separately and do not infer one from the other. Observing that the new
state holds does not confirm the old one ended, and confirming the old one ended does not
confirm the new one works — check explicitly for the absence of the prior state (including
across any other address family, interface, or configuration entry that could still produce
it) as well as the presence of the intended one, before treating the task as complete.
```

The new file is the entire v2 template plus this one insertion, immediately
after the 3.13 paragraph and before Task data. Removing it recovers the control
byte-for-byte. The example has no specific service name or numeric ports.

## Inspected wiring and wrappers

Actual source was read before changes. select_proposer already maps baseline
to proposer and all accepted non-baseline variants to proposer_{variant}.
Only the loader list and allowed set need extending; the schema adds the new
Literal value and preserves the default. The selected hash projection remains
exactly four entries: selected proposer, auditor_blind, auditor_informed, repair.
Constructor, proposer signatures, rendering, nonce handling and runner wiring
remain unchanged. Full source and reference output are retained in the review
pre-change-inspection.md artifact outside the diff.

The audit again inspected every Python file in tests and vice_sysadmin. Two
wrappers replace PromptBook.proposer:

- test_iteration34_policy_feedback.record(self, task_prompt, requires_sudo,
  values, *, variant="baseline") accepts and forwards variant.
- test_iteration38_auditor_feedback.capture(self, task_prompt, requires_sudo,
  history, *, variant="baseline") accepts and forwards variant.

Both were re-read and need no changes. Constructor monkeypatches forward **kwargs.
No existing tests are edited, and no new wrapper issue was found.

## SHA-256 of UTF-8 template bytes

- baseline (unchanged): 4cf95ea590071f1f291871b33c0400313a8d41e976100e1d10f87f4475858771
- verify_nudge (unchanged): f3f34255496c20e3bc437f5f248f4f9704cafe7fa00953caa67c8243df0a99e0
- verify_nudge_syntax (unchanged): 4d4ca52beafd8e3778726822c38f910669658befa5a2dd92fc3025da5ba0cfa1
- verify_nudge_syntax_v2 (unchanged): c165ab69dc1935a89930d02d317e8fca6566951e844cf35c159f573b06314c68
- verify_nudge_syntax_v3 (new): 717f47d872abf08840ef97d68b6a6a380ff34d956045dedd7db84b41abc390f9

Tests pin all five hashes explicitly; the four earlier template bytes are also
compared between the source and validation copy. The new manifest key is
proposer_verify_nudge_syntax_v3.v1.txt, with four entries still published per run.

## Validation

Windows, Python 3.14.2, pytest 8.4.1, Pydantic 2.13.5, PyYAML 6.0.2. Validation
used the existing isolated 3.12 environment and a complete copy of the current
project with only this patch applied. A workspace basetemp avoids the known
Windows default-temp permission issue. No real experiment was launched.

Command: python -m pytest -q --basetemp=.pytest-temp-full

Complete output:

```text
........................................................................ [ 14%]
........................................................................ [ 28%]
........................s..s............................................ [ 42%]
........................................................................ [ 56%]
........................................................................ [ 70%]
........................................................................ [ 85%]
........................................................................ [ 99%]
...s                                                                     [100%]
505 passed, 3 skipped in 18.35s
```

Exit code: 0. New tests cover fixed hashes of all variants, exact source and
rendered insertion, config acceptance/rejection/defaults, four published hashes,
A/B/C repetition wiring and the manifest in B/C. The three existing skips remain
reported; no new filtering or skip was introduced. The main project remains
unapplied pending operator confirmation; release-manifest.json remains the older
release snapshot. To select the experimental arm after application, set
experiment.proposer_variant to verify_nudge_syntax_v3 in the operator's chosen
comparison config. This diff edits no config.
