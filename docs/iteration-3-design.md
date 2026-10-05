# Iteration 3 design and review record

The operator authorized implementation over iteration 2 and confirmed that the
only subsequent change was pydantic==2.13.5, already present here. The supplied
prompt reports 142 passing baseline tests. That statement is operator evidence;
the baseline and new tests were not executed in this authoring session.

## Assumptions disclosed before code generation

- Use lxc copy --profile, not a guessed profile option on lxc start.
- Resolve UID/GID once per container/user; no hardcoded agent IDs.
- Use the existing name pattern, allowing 63 characters for generated targets
  and 48 for template/profile identifiers. No ad hoc target-name construction.
- All SDK/model capabilities are not inferable from an arbitrary model string.
  Auto format uses plain text; explicit reviewed modes enable JSON/schema output.
  Do not issue an invisible probe/fallback request.
- OpenAI SDK 3.20.0 and google-genai 2.25.0 are fixed from public metadata.
  Wheel evidence is for CPython 3.14/Linux x86_64; architecture remains an
  operator-confirmed assumption. No installer or resolver was run.
- Killing lxc locally does not prove remote termination. On timeout/output cap,
  force-stop the disposable target, end agent execution, then finally destroy it.
- Popen/selectors is required instead of subprocess.run to enforce a live output
  cap. Both still use list arguments with shell=False.
- A run manifest needs an event context despite covering many repetitions. It
  carries the first scheduled repetition's identifiers plus the full run_order;
  the reporter excludes it from repetition statistics.
- Model SDK timeout knobs can represent transport timeouts rather than a total
  wall-clock deadline. Async SDK calls also use asyncio.timeout, without signals
  or hidden retries. Cancellation does not prove the provider stopped billing.

## Existing files changed

| File | Purpose |
| --- | --- |
| vice_sysadmin/schemas.py | Add defaulted SDK/fixture/task selection fields, telemetry fields, output-cap flag, and placeholder rejection in load_config. |
| vice_sysadmin/orchestrator.py | Add category/steps/final latency telemetry; stop when the live executor hits output cap; do not pass capped checks/setup. |
| vice_sysadmin/logger.py | Add atomic exclusive log creation without changing existing append behavior. |
| vice_sysadmin/structured.py | Reserve additional schema bytes only for the new json_schema mode, without changing transmitted prompts. |
| README.md | Document real components, versions, staged checks, semantics, and remaining manual work. |
| docs/vibe-coding.md | Append this iteration without replacing earlier evidence. |
| release-manifest.json | Refresh release-file hashes. |

requirements.txt and requirements.lock.txt are unchanged because the required
Pydantic pin already matched. Separate requirements-live.txt and
requirements-live.lock.txt add the optional SDK closure. All preexisting test
files and task YAMLs are preserved. Prior README content is archived in
docs/iteration-2-readme.md; prior changed-file copies are retained in review.

New modules: processes.py, lxd_runtime.py, live_support.py, clients.py,
run_experiment.py, reporter.py. New tests use fake transports, in-memory streams,
fake SDK shapes, and temporary files only. Additional fixtures/configs exercise
normal nginx smoke, timeout, and output cap on the operator's real host.

## Deliberate semantics

Timeouts allocate 80% of remaining execution time inside the target and 90% to
the host backstop, retaining the remainder for force-stop. A remote command
that exits 124 or 137 is conservatively classified as timed out. SIGKILL from
another cause can produce 137 too; the log cannot distinguish that cause.
Local subprocess reap may consume up to 0.25 seconds of cleanup beyond its
execution deadline. Synchronous OS/LXD behavior and cancellation still require
manual validation; this is not a hard real-time guarantee.

No process, container, package manager, model endpoint, or pytest was invoked
while generating files. Public docs, registry metadata, and source signatures
were read. Source-level comparisons and archive encoding checks are not test
execution or proof of operational correctness.

The CLI is sequential and locks real runs on one host. It aborts the remaining
matrix after cleanup failure. External manual lxc operations are outside that
lock. Existing log paths are refused atomically. Existing report paths are also
refused. Target profile, template, quota and host firewall remain operator-owned.
Target egress restrictions are likewise operator-managed; this adapter does not
add host firewall rules or claim to enforce requires_network.

Fake runtime checks always consume canned results: their outcomes measure wiring
only. The reporter separates backend groups and never computes reference-label
metrics. Completed repetitions form rate denominators; interrupted repetitions
are counted separately, and never-started planned work is not fabricated as failure.

## Remaining empirical evidence

Record Python patch/architecture, dependency installation output and freeze, new
pytest results, normal/negative LXD smoke JSONL, leftover-container checks, then
the small live pilot. No concurrency, model capability discovery, command
ground-truth protocol, automatic template rebuild, or multi-host operation is
included. Template escape and kernel security are not proven by these tests.
