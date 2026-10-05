# Iteration 3.30 — stop a batch after two consecutive infrastructure failure reasons

## Baseline and scope

Prepared on the local project through iteration 3.14, including monitor updates.
The local checkout does not contain the separately mentioned 3.18/3.25 changes;
this patch must be checked against the operator's newer server checkout. The
operator reports infrastructure noise in a previous run; no live batch was run
or raw remote run inspected during authoring. Date: 2026-10-02.

The current sequential batch layer is vice_sysadmin/run_experiment.py. It builds
and seed-shuffles a task/condition/repetition matrix, then invokes the injected
single-repetition orchestrator once per entry. There was no checkpoint/resume.
This patch adds vice_sysadmin/batch.py and changes only runner wiring, new tests
and this document. Existing orchestrator, schemas, logger, prompts, policy,
monitor, adjudication, client retry/timeout and configuration files are unchanged.

## Counter and classification

Count exact final termination_reason values proposer_failure, audit_failure,
execution_timeout and setup_failed, regardless of condition or outcome. Mixing
reasons also reaches the threshold. Every other final reason resets the streak,
including task_completed (even if verification fails), escalated, token_limit,
cost_limit, time_limit, step_limit, conflicting_proposal and execution_output_limit.
runtime_error and runner_error are too broad to infer connectivity automatically;
runner errors and failed cleanup retain the existing hard stop behavior.

These labels are defensive proxies, not proof of root cause. proposer/audit failure
may include malformed model output, setup_failed may be script failure, and an
execution timeout may be a slow command. The operator explicitly requested these
broad codes; logs preserve each exact reason and individual error events for
later attribution. No existing reason, outcome or adjudication is changed.

The streak is a local list on the Checkpoint object. It spans consecutive closed
repetitions in actual matrix order. Cleanup failure stops first. After a safe
close, synchronize the JSONL and save the completed prefix. At streak length two,
save paused status, emit and synchronize batch_paused, print a human message,
and raise BatchPaused. main catches that controlled condition and returns exit
code 3; ordinary completion is 0, other errors remain 2. There is no automatic
API/repetition retry. A final pair may pause with next_repetition null; explicit
resume then completes without executing further work.

## Batch event

BatchPausedEvent subclasses Event in batch.py, adding triggering_repetitions,
next_repetition and checkpoint_path only for batch_paused. This avoids adding null
fields to every existing repetition event; existing schemas.py is unchanged.
The triggering list contains the two matrix entries, each with task_id, category,
condition, repetition and exact termination_reason. Event context/timestamp refer
to the second close. The next entry is the pending matrix entry or null.
The normal logger redacts and writes this Event subclass as usual. A controlled
pause does not emit runner_failed. The current monitor can read the additive
record but does not gain a paused banner in this scope; the console and checkpoint
provide the explicit pause indication.

## Checkpoint file and operator command

A new run creates logs/<run_id>.state.json alongside the JSONL. Its fields are:
version, run_id, log_path, config, tasks, config_directory, handler_name, run_order,
prompt_hashes, inputs_hash, completed, next_index, status.

config and tasks are snapshots of the validated effective inputs, including the
CLI runtime override; they preserve the exact models, prices, limits, task scripts
and selector used. Credentials remain environment-variable references from the
existing schema, not values read from the environment. Treat the checkpoint like
the input configs/tasks: it is operational data, not an executable file. It does
not store model responses or execution output. completed holds each closed matrix
entry plus its exact reason. next_index indexes the preserved original run_order;
it is not a newly shuffled pending matrix. status is running, paused or complete.

Persistence uses an exclusively created initial checkpoint, then a unique temporary
file with flush/fsync and atomic replacement. On POSIX, the parent directory is
synchronized after replacement. Windows transient PermissionError on replacement
is retried at most 25 times with 20ms between attempts, solely for that filesystem
operation; no API call or repetition is retried. Persistent errors stop the runner,
and the old checkpoint remains intact. Log closes and pause records are fsynced
with a writable descriptor for Windows compatibility. Filesystem/power-loss
semantics still depend on the underlying filesystem; this is not a database.

Example simulated output:

```text
BATCH PAUSED: 2 consecutive infrastructure/provider failure reasons.
  example/C/rep-3: proposer_failure
  example/C/rep-1: audit_failure
Next: example/A/rep-2
Resume: python -m vice_sysadmin.run_experiment --resume logs/pause-test.state.json
```

Actual output gives an absolute, shell-quoted checkpoint path. The next matrix
entry is not started. After resolving the suspected provider/host issue, explicitly
run the printed --resume command. Resume is mutually exclusive with all run
configuration/runtime/task/log overrides; the frozen inputs are used instead.
Changing an original YAML does not modify this saved batch. API credentials can
be provided through their original environment names as usual.

## Resume integrity and limitations

A process-owned OS lock covers the whole run/resume and is released on exit.
Windows uses msvcrt byte locking; POSIX uses flock. Stale lock files alone do not
block a run. A checkpoint must remain at the canonical path derived from its
original absolute log path; a copied checkpoint cannot bypass that run lock.
The existing LXD host-wide lock remains in place. Automatic relocation/import
of paused batches is not supported.

Before any client call or container action, verify the stored input digest,
validated config/tasks, original matrix, selected prompt hashes, and complete
JSONL context. Require a single matching manifest and start/finish sequence in
matrix order, with cleanup_succeeded true on every close. Completed checkpoint
records must be an exact prefix of the log closes. If a hard crash occurred after
a fully logged close but before checkpoint save, recover that complete prefix
from the log. If a repetition started without closing, the log has a partial
line, an inconsistent identity, failed cleanup, duplicate manifest, or runner_failed,
refuse resume and require manual inspection. No ambiguous repetition is replayed
or skipped automatically. A caught persistence failure emits runner_failed and
therefore also requires manual inspection; it is not a transparent crash recovery.

The new segment starts with an empty streak, even if the prior segment paused.
Append to the same JSONL without writing another manifest. A complete checkpoint
resumes as a no-op. Current prompt changes block resume. Snapshot task/config
content is preserved; this does not assert semantic equivalence across arbitrary
code upgrades or changes to external mock fixture files. Resume is intended on
this validated code/version and original paths. Existing runs without these
checkpoints cannot automatically be resumed by this feature.

## Validation

Windows, Python 3.14.2, pytest 8.4.1, Pydantic 2.13.5 and PyYAML 6.0.2; isolated
3.12 venv, full current project copied into the validation directory. Tests run
with a unique basetemp outside the synchronized workspace. No live API/LXD
experiment was run. Earlier full runs exposed a Windows fsync descriptor issue
(corrected) and transient atomic replacement denials (now bounded explicitly).

Command: python -m pytest -q --basetemp=<unique local temporary directory>
(The review runner invokes pytest.main with exactly those arguments.)

Complete final output:

```text
........................................................................ [ 13%]
........................................................................ [ 26%]
........................s..s............................................ [ 39%]
........................................................................ [ 52%]
........................................................................ [ 65%]
........................................................................ [ 78%]
........................................................................ [ 91%]
...............................................s                         [100%]
549 passed, 3 skipped in 22.93s
```

Exit code: 0. Tests cover all 16 pairs of counted reasons, uncounted resets,
exact pause event/console fields, original order and counter reset, repeated
resume segments, multi-task batches, full-close crash recovery, corrupt/partial
logs, unsafe cleanup, copied checkpoints, changed prompts, lock contention,
CLI overrides/controlled exit, failed persistence preserving the old state,
terminal pause with no next work, and the original offline proposer-failure path.
Existing tests and individual-repetition sources remain byte-for-byte unchanged.
POSIX lock/directory-fsync branches are not exercised by Windows validation;
the operator should run the full suite on Linux before a live batch.

The deliverable is a diff, not a local integration. Existing release-manifest.json
remains the older release snapshot. No existing configuration is changed.
