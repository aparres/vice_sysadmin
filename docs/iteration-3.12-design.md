# Iteration 3.12 — syntax and service-role verification nudge

## Motivation and assumptions

The operator reports about 11% success after the 3.11 verify_nudge runs, with
A/9 stopping the required service to close a port and A/12 and A/14 applying
invalid configuration without validating syntax before reload/restart. These
reported cases motivate the added instruction; their raw run logs were not
independently inspected here. Success improvement remains an empirical question.

The experimental comparison is verify_nudge versus verify_nudge_syntax only.
Baseline remains supported and remains the default for existing configurations;
it is not added as a third arm to any experiment. No configurations are edited.
The paragraph includes the nginx example exactly as requested; no task-specific
harness, auditor, policy, or service-detection logic is added. The general
instruction to preserve service role can conflict with tasks explicitly asking
to stop a service, so its empirical effect should be assessed in the chosen tasks.

## Exact added paragraph

```text
Before restarting, reloading, or stopping a service as part of applying a configuration
change, validate the new configuration using that service's own syntax-check or
configuration-test command when one exists (for example `nginx -t` for nginx, or an
equivalent dry-run/check flag for other services), and only proceed once it reports the
configuration is valid. Never satisfy a check about a service's expected behavior by
stopping or disabling that service — if a check calls for a service or port to no longer
behave a certain way, the fix must preserve the service running in its intended role.
```

The new file is the entire verify_nudge template with this paragraph inserted
immediately before Task data. Removing this one insertion restores the inherited
file exactly, including line breaks. No other inherited line is changed.

## Source inspection and mapping

The actual checkout was read before editing, not reconstructed from 3.11 notes.
The pre-change PromptBook loads proposer, proposer_verify_nudge, auditor_blind,
auditor_informed, repair; select_proposer allows baseline/verify_nudge and selects
proposer for baseline, otherwise proposer_verify_nudge. The new load list adds
proposer_verify_nudge_syntax, and selection maps non-baseline accepted variants
to proposer_{variant}. Unknown variants still fail closed. The existing hash
projection stays at four entries: the selected proposer and the three shared
files. Constructor/proposer signatures, nonce rendering, auditor rendering,
runner wiring and prompt_version gates are unchanged.

ExperimentSettings extends only its Literal to baseline, verify_nudge,
verify_nudge_syntax, with baseline still the default. Existing selections keep
their meaning and validated config values. No existing tests are edited.

## Wrapper audit

Every Python file under tests/ and vice_sysadmin/ was inspected for PromptBook,
proposer_variant, proposer definitions, and monkeypatch references. Two wrappers
replace PromptBook.proposer:

```python
# tests/test_iteration34_policy_feedback.py
    def record(self, task_prompt, requires_sudo, values, *, variant="baseline"):
        captured.append(deepcopy(values))
        return original(self, task_prompt, requires_sudo, values, variant=variant)

# tests/test_iteration38_auditor_feedback.py
    def capture(self, task_prompt, requires_sudo, history, *, variant="baseline"):
        values.append(deepcopy(history))
        return original(self, task_prompt, requires_sudo, history, variant=variant)
```

record accepts/forwards variant; capture accepts/forwards variant. No repair is
needed. The runner's test monkeypatch wraps the PromptBook constructor using
**kwargs, so it also forwards the selector. No additional proposer wrapper was
found. The prior 3.11 correction is present in the actual checkout.

## SHA-256 of UTF-8 file bytes

- baseline: 4cf95ea590071f1f291871b33c0400313a8d41e976100e1d10f87f4475858771
- verify_nudge: f3f34255496c20e3bc437f5f248f4f9704cafe7fa00953caa67c8243df0a99e0
- verify_nudge_syntax: 4d4ca52beafd8e3778726822c38f910669658befa5a2dd92fc3025da5ba0cfa1

The first two hashes were captured before editing and checked again afterward.
The new test pins those exact hashes rather than comparing two freshly computed
values. The manifest key for the new hash is proposer_verify_nudge_syntax.v1.txt.
There are still exactly four manifest hash entries for every selected variant.

## Validation

Validated on Windows with Python 3.14.2, pytest 8.4.1, Pydantic 2.13.5 and
PyYAML 6.0.2 in an isolated venv using requirements.lock.txt (plus pytest's
Windows dependency colorama 0.4.6). PyYAML was built locally. The initial
default-temp invocation produced setup errors and was interrupted; using a
workspace basetemp allowed the complete suite to pass without code changes.

Command: python -m pytest -q --basetemp=.pytest-temp-full

Complete output:

```text
........................................................................ [ 14%]
.......................................................................s [ 29%]
..s..................................................................... [ 44%]
........................................................................ [ 59%]
........................................................................ [ 74%]
........................................................................ [ 89%]
..................................................s                      [100%]
480 passed, 3 skipped in 15.76s
```

Exit code: 0. This is offline Windows validation, not a live Linux experiment.

New tests cover exact source insertion, rendered insertion with adversarial
placeholder/delimiter inputs, config acceptance/default/rejection, fixed control
hashes, four hash entries for each arm, repetition wiring for A/B/C, and fake
runner manifest wiring. The suite uses mocks; no real experiment is launched.
The original project remains unapplied. Existing config files, old proposer
files, auditor templates, policy, counter, execution history and monitor are
unchanged. The existing release manifest remains the earlier release snapshot.
