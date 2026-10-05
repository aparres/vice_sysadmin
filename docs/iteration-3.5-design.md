# Iteration 3.5: read-only progress tooling

## Assumptions and authoritative signals

This is additive operator tooling, not a bug fix or methodological finding. The
baseline is local iteration 3.4, including the operator's datetime correction in
its regression test. Only this diff is generated; no project commands, tests,
installation, LXD or provider calls were executed. Existing runtime, orchestrator,
runner, schemas, reporter, policy, clients, prompts and configs are untouched.

The actual runner writes the planned matrix once:

```python
    logger.write(Event(**context, event_type="run_manifest", timestamp=datetime.now(timezone.utc),
                       seed=config.experiment.seed, run_order=matrix, prompt_hashes=prompts.hashes,
                       python_version=platform.python_version(), git_commit=get_git_commit()))
```

The actual orchestrator emits completion after verification, cleanup and
adjudication, not at a proposal's completion claim or at a cleanup event:

```python
        self.emit("run_finished", outcome=outcome, termination_reason=self.reason,
                  cleanup_succeeded=self.cleanup_ok, error=self.cleanup_error,
                  charged_tokens=self.budget.tokens, charged_cost=float(self.budget.cost),
                  steps=self.steps, latency_seconds=max(0.0, self.clock.monotonic() - self.budget.started))
```

Outcome currently declares success, appropriate_escalation and failure.
adjudicate() returns those enum members. The monitor imports Outcome to obtain
its tally labels; it does not duplicate or infer adjudication. A planned tuple
(task_id, condition, repetition) is counted exactly once, only on run_finished.
Unknown identities, mixed run IDs, duplicate completions or invalid outcomes
stop the monitor with a generic error rather than producing misleading totals.

## Concurrent reading and privacy

JsonlLogger.write() recursively redacts event.model_dump(mode="json") BEFORE
serializing it. It opens the file in append text mode, writes one JSON line and
flushes inside a threading.Lock. That lock is process-local, not an exclusive
OS file lock; an independent read-only file handle on the target Linux host can
read concurrently without acquiring it or preventing append. Atomic visibility
of the entire line is NOT assumed: buffered/multiple writes can expose a prefix.

Tail opens only the operator-supplied path in rb mode, seeks to its saved byte
offset and reads at most 1 MiB per poll. Bytes after the final newline remain in
memory, including a possibly incomplete UTF-8 character; they are decoded only
once the complete line arrives. No partial record affects progress. The monitor
never rewrites, flushes, locks or repairs the run log. It waits for a not-yet-created
file/manifest. File replacement or observed truncation stops the monitor; arbitrary
in-place rewriting is outside the append-only logger contract. Complete malformed
records fail visibly with generic text, without echoing their contents.

Redaction walks every nested string value, including run_id, run_order task IDs,
termination_reason and all other displayed textual fields. It replaces configured
secret values, bearer tokens and recognized OpenAI/Google key shapes. Numeric
progress fields do not come from credentials. This protection depends on using
the runner's configured JsonlLogger; its own documentation notes that transformed
or unregistered secrets cannot be universally detected. The monitor does not read
environment variables, configuration, API keys or task files. It never displays
prompt_sent, raw_response, commands, stdout/stderr, errors or model response data.
Display identifiers must match a bounded ASCII token format, preventing terminal
control sequences. Redacted identifiers containing brackets are rejected safely.
Do not use arbitrary unredacted logs as input; this tool is not a general redactor.

## Dependencies, timing and resource limits

Existing requirements are pydantic, PyYAML and pytest. No dependency is added:
standard-library counters, JSON, datetime and terminal escape sequences suffice.
Importing the existing schemas module obtains Outcome without importing runner,
LXD or provider adapters. There is no network client, subprocess or experiment
control interface in the monitor.

The matrix and seed come solely from run_manifest; every planned repetition is
retained, and every task/condition cell is displayed, including zero completions.
The in-flight repetition begins at run_started. The latest event supplies the
step separately, so a finished repetition is not falsely shown as still running.
The logged monotonic latency_seconds on run_finished includes cleanup and is the
preferred observed wall duration. Timestamp difference is a fallback when absent.
ETA uses this run's completed mean, remaining count and active elapsed duration;
it is clamped to zero and explicitly approximate. No ETA before a completion,
after runner_failed, or while catching up with unread bytes. Wall-clock changes
can distort live elapsed time; negative elapsed values are clamped to zero.

A MAY BE STALLED label appears above STALL_MULTIPLIER (default 3) times the mean,
only when that mean is positive and the reader is caught up. The --stall-multiplier
option adjusts it. This never changes, signals or kills the experiment. A failure
or abrupt end without every planned run_finished leaves monitoring active until
Ctrl+C. Completed matrices and Ctrl+C exit 0; invalid/unreadable logs exit 2.
A fake-runtime log still has simulated durations and cannot provide a meaningful
real-world ETA. Heterogeneous task mix, failures and host load also affect ETA.

Refresh defaults to 1.5 seconds. Parsing is bounded to 1 MiB per poll; a 16 MiB
line limit prevents unbounded buffering and is a module constant. Oversized lines
stop only the monitor. Repetition state grows with the planned matrix; raw event
payloads are not retained. A second reader necessarily consumes some I/O/CPU, so
zero overhead is not claimed; bounded incremental reads avoid repeated full scans.
When stdout is not a terminal, successive plain snapshots are printed without
screen-control sequences. No web server, port or browser is involved.

## Authored validation, not executed

Tests use synthetic manifest/start/finish records for hand-checked cell counts,
three outcome tallies, termination reasons, mean and ETA. They exercise partial
line completion across polls, byte offsets, bounded reads, exact matrix completion,
duplicate/mixed events, malformed/truncated files, payload exclusion and terminal
escape rejection, timestamp fallback, stall thresholds, and backlog suppression.
They call parser/aggregation/formatting functions only; no live refresh loop,
sleep, LXD or model API is invoked by tests. No passing count is claimed.

release-manifest.json remains the prior release snapshot and is not regenerated
for this targeted diff.
