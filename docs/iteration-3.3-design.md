# Iteration 3.3: wait for systemd's private bus socket

## Assumptions and risks

This diff targets iteration 3.2 after applying the 3.1 and 3.2 patches. The local
working files remain untouched; the previous 3.2 diff supplies the documentation
baseline in memory. The runtime and its existing tests were unchanged by 3.2.
Only runtime readiness behavior, its tests and iteration documentation change.
No tests, Python/project commands, LXD operations or model API calls were run.

The supplied prompt reports recurring setup_failed results: 6/12 in one firewall
batch, later failures in unrelated task categories, and roughly 10/~57 (~17%)
over the calibration runs. It reports this stderr with exit code 1:

```text
Failed to connect to system scope bus via local transport: No such file or directory
```

Those logs and host resource measurements were not independently reviewed here.
Source inspection confirms a readiness gap consistent with the reported race;
it does not establish every reported failure's cause empirically.

Before patching, lxd_runtime.py lines 78-82 read:

```python
        while True:
            ready = self._call(["exec", name, "--disable-stdin", "--force-noninteractive", "--", "true"], deadline)
            if ready.exit_code == 0 and not ready.timed_out:
                return
            self.clock.sleep(min(0.1, self._remaining(deadline)))
```

Successful exec does not prove that systemd has created /run/systemd/private.
Returning immediately lets setup race socket creation. This path precedes model
calls and is independent of the proposer/auditor condition.

## Change and boundaries

Replace the first loop's return with break, then poll this single command:

```text
lxc exec <name> --disable-stdin --force-noninteractive -- test -S /run/systemd/private
```

The argv is passed through the existing _call helper, without a shell or pipeline.
Exit code zero with timed_out false returns successfully. Other results sleep via
the same min(0.1, self._remaining(deadline)) pattern. Both loops share the original
deadline, including time already spent in list/copy/start and exec readiness.
Expiration raises the existing TimeoutError("LXD operation deadline expired").
There is no new timeout, dependency, configuration, signature or exception type.

The check proves socket existence only; it does not prove a successful D-Bus
transaction or that every service has finished starting. It assumes the existing
systemd-based template and a test executable inside it. A template without this
socket will exhaust the deadline rather than proceed to setup. Cleanup ownership
is unchanged: the orchestrator's existing finally path handles partial creation.
A readiness timeout follows its existing runtime_error path, not setup_failed.
No new cleanup or retries of the setup script are introduced.

## Authored tests (not run)

The existing create/profile/start/readiness test is retained and minimally updated
with one extra success result and the expected bus argv. Its original timing
comparison now references the two exec probes; an additional assertion checks that
an immediately ready bus consumes no simulated time. No test is removed.

A new test file reuses the existing Transport/runtime/result helpers and FakeClock
for ordering, a nonzero or timed-out bus probe followed by readiness, timeout under
a shared partially consumed deadline, timeout before exec readiness, and the common
case of exactly one bus check with no sleep. None starts a subprocess or LXD.
These tests and the earlier suites still require operator execution.

The diff leaves schemas, configs, CLI, event fields, adjudication and the 3.1/3.2
proposer changes untouched. release-manifest.json remains the prior release
snapshot, not an integrity manifest of the patched tree.
