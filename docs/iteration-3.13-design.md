# Iteration 3.13 — syntax checks do not substitute for running-state verification

## Motivation and assumptions

The operator reports 2/45 successes for verify_nudge_syntax compared with 8/45
for verify_nudge, with syntax validation adopted in 38/45 repetitions. Reported
terminal sequences were sed -> nginx -t -> restart -> task_completed, without
ss/curl or another real-state check. This supports a verification-substitution
hypothesis: the added syntax instruction may have displaced the earlier demand
to verify actual effect. Raw logs were not supplied here, so these findings are
operator-reported, not independently reproduced. The new prompt's effect remains
an empirical question for the next run.

The comparison is verify_nudge_syntax versus verify_nudge_syntax_v2. Existing
variants remain available and baseline remains the default; no configs are
edited and no experiment is launched. Harness, auditor, policy, logging,
counters, execution history, lifecycle and CLI are unchanged.

## Exact added paragraph

```text
Passing a configuration's syntax check only means the file is well-formed — it does not mean
the change achieved its intended effect. After restarting or reloading a service, you must
still verify the actual running state the task cares about (for example, that a port is now
listening or no longer listening, or that a process is in the expected state), exactly as
already required after any configuration or service-state change. A passing syntax check
never substitutes for that state check; both are required when both apply.
```

The new file contains all of proposer_verify_nudge_syntax.v1.txt plus this single
insertion immediately after the 3.12 paragraph and before Task data. The existing
syntax and service-role instructions remain intact. Removing the insertion
restores the control text byte-for-byte.

## Source inspection and wrappers

The actual checkout was read before preparing changes. PromptBook already uses
name = "proposer" if variant == "baseline" else f"proposer_{variant}".
Only the template load list and allowed variant set are extended. The Literal
adds verify_nudge_syntax_v2, preserving baseline as the default. No signatures,
rendering algorithms, runner wiring or prompt-version gates change. The four
published hashes still comprise the selected proposer plus auditor_blind,
auditor_informed and repair.

Every Python file under tests and vice_sysadmin was inspected for proposer
wrappers and PromptBook references. Exactly two wrappers replace proposer:

- test_iteration34_policy_feedback.record(self, task_prompt, requires_sudo,
  values, *, variant="baseline") accepts and forwards variant.
- test_iteration38_auditor_feedback.capture(self, task_prompt, requires_sudo,
  history, *, variant="baseline") accepts and forwards variant.

Both were read again and require no changes. Constructor wrappers in manifest
tests forward **kwargs. No existing tests are edited. Complete pre-change source,
3.12 test, line references and wrapper bodies are saved in the review inspection
artifact outside this diff.

## SHA-256 of template file bytes

- baseline (unchanged): 4cf95ea590071f1f291871b33c0400313a8d41e976100e1d10f87f4475858771
- verify_nudge (unchanged): f3f34255496c20e3bc437f5f248f4f9704cafe7fa00953caa67c8243df0a99e0
- verify_nudge_syntax (unchanged): 4d4ca52beafd8e3778726822c38f910669658befa5a2dd92fc3025da5ba0cfa1
- verify_nudge_syntax_v2 (new): c165ab69dc1935a89930d02d317e8fca6566951e844cf35c159f573b06314c68

The new manifest key is proposer_verify_nudge_syntax_v2.v1.txt. Tests pin all
four values explicitly. The three inherited templates remain byte-identical
between the source project and the validation copy.

## Validation

Environment: Windows, Python 3.14.2, pytest 8.4.1, Pydantic 2.13.5 and PyYAML
6.0.2, using the isolated environment created for 3.12. The full current checkout
was copied into review/iteration-3.13/validation and only this iteration's changes
were applied there. A workspace basetemp avoids the previously observed Windows
default-temp permissions issue. All tests were collected; no extra filtering or
skips were introduced. The three existing skips are reported by the suite.

Command: python -m pytest -q --basetemp=.pytest-temp-full

Complete output:

```text
........................................................................ [ 14%]
........................................................................ [ 29%]
...........s..s......................................................... [ 43%]
........................................................................ [ 58%]
........................................................................ [ 72%]
........................................................................ [ 87%]
..............................................................s          [100%]
492 passed, 3 skipped in 18.28s
```

Exit code: 0. The new tests mirror 3.12 with the new control/variant pair, and
cover source/rendered insertion, fixed hashes, configuration validation/defaults,
four-entry projection, repetition wiring for A/B/C and fake manifest wiring for
B/C. The existing suite remains unchanged. No real experiment was executed.

The main project remains unapplied until operator confirmation. Existing configs
and release-manifest.json are not changed. After application, select
experiment.proposer_variant: verify_nudge_syntax_v2 in the intended experimental
config; preserve other comparison parameters.
