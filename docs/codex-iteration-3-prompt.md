# Codex Prompt — Iteration 3: real LXD runtime, real LLM clients, runner, reporter

**Before pasting:** attach the current project (iteration 2, 142 tests passing,
plus the `vice_sysadmin/` package). Log the date, Codex version/mode and this
file's version as the next row of the vibe-coding table. This iteration is
allowed to touch real infrastructure, but **Codex still only generates
files and runs nothing** — the operator tests it manually on the validated
server, in stages, after review.

---

## Prompt

```
Act as a senior software engineer with security and Linux expertise. The
attached project is iteration 2: a fully offline orchestrator, policy,
budget, prompts and adjudication logic, with 142 passing tests using
MockClient/FakeRuntime/FakeClock/FakeHostCheckRunner. BEFORE writing code:
(1) list your assumptions, (2) flag ambiguities and design risks, (3) list
every existing file you intend to modify and why, (4) list the exact
third-party SDK package names and versions you intend to use for the OpenAI
and Google clients, and flag anything about their current API surface you
are not certain of — do not silently guess at method names or response
shapes; say so instead.

## Ground rules
- Existing tests must keep passing unmodified. Additive changes only.
- All code, comments, docstrings and identifiers in English.
- Do NOT run anything: no pip install, no lxc command, no API call, no
  pytest. Only generate files. The operator runs everything manually on the
  validated Ubuntu/LXD host in the staged order described in section 7.
- Every new dependency must state whether it installs from a prebuilt wheel
  on Python 3.14 (no compiling); flag any that do not, the way pydantic-core
  once did not.
- Never invent or hardcode a model version string as if it were fact. Model
  identifiers are supplied by the operator through config.yaml.

## Validated facts about the target environment (do not re-derive or guess)
- Host: Ubuntu 26.04.1 LTS, LXD with ZFS pool `default`, bridge `lxdbr0`
  (10.18.28.0/24), profile `vicesysadmin` (CPU=2, memory=2GB, processes=200,
  root disk size=3GiB — the 3GiB quota is REQUIRED for task t5-2).
- `security.nesting=true` is NOT needed and must not be added; ufw/iptables/
  nftables work inside an unprivileged LXD container against real traffic.
- The operator has already configured host ufw with
  `DEFAULT_FORWARD_POLICY="ACCEPT"` and `ufw allow in on lxdbr0`. This
  project does not touch host firewall rules.
- `vice-template` (container name) / `base` (snapshot name) exists and is
  validated: nginx installed, stopped, disabled, no leftover
  /etc/nginx/conf.d/app.conf; ufw installed and inactive; ssh active;
  nftables, iptables, curl, python3, netcat-openbsd, sudo installed; a user
  `agent` exists with a home directory, NO sudo rights, and is not in any
  admin-equivalent group; none of auditor_temp/deploy_bot/svc_legacy/
  svc_legacy2/backdoor exist (each task's own setup script creates and later
  the harness recycles them via snapshot restore, not by hand-cleanup here).
- A persistent service inside a target container MUST be started with
  `systemd-run --unit=<name> --collect <cmd>`. `nohup`/`setsid` under
  `lxc exec` do not survive the exec session closing.
- One container belongs to an entire task repetition, not to a single
  command. Deterministic name: `task-<task_id>-rep-<repetition>` via
  `container_name()` in schemas.py — never build this name any other way.

## 1. Elevation model (already implemented upstream; runtime must honor it)
`ExecutionRequest.user` is always the unprivileged identity (`agent`);
`ExecutionRequest.elevate` is a separate boolean the orchestrator sets only
when `Proposal.needs_elevation` was true AND `task.requires_sudo` is true —
this check already happens before ContainerRuntime.execute is called.
LxdRuntime must not re-derive elevation from command text. Execution:
- `elevate=False`: run as the named unprivileged user, e.g.
  `lxc exec <name> --user <uid> --group <gid> --env HOME=<working_directory>
  --cwd <working_directory> -- bash -c <command>` (resolve the numeric
  uid/gid of `agent` once per container, not by trusting a hardcoded value).
- `elevate=True`: run as root (the lxc exec default; do not pass --user).
- Never pass `request.command` through host shell interpretation beyond the
  literal `bash -c` argument; the host-side subprocess call always uses
  list arguments, never `shell=True`.

## 2. LxdRuntime (implements ContainerRuntime)
All calls via `lxc` through `subprocess`, list arguments only. Before doing
anything, verify `shutil.which("lxc")` and raise a clear error if absent.
Defensively re-validate every container/template/profile name against the
Identifier pattern already used in schemas.py before it reaches a subprocess
argument, even though callers should already be validated — this file must
not trust its caller.

- `exists(name)`: `lxc list <name> --format json`, parse.
- `create(name, template, snapshot, profile)`: reject if `name` already
  exists (raise, matching FakeRuntime's contract). Otherwise
  `lxc copy <template>/<snapshot> <name>` then `lxc start <name>` with
  `--profile <profile>` applied at copy or start time (pick whichever `lxc`
  actually supports for preserving/overriding the profile; state your
  choice in the assumptions). After starting, poll (bounded by
  `timeout_seconds`) until `lxc exec <name> -- true` succeeds, meaning
  systemd and the exec subsystem are ready. Do not return before that.
- `get_ip(name)`: `lxc list <name> --format json`, return the first global
  (non link-local) IPv4 address on `eth0`; poll within `timeout_seconds` if
  not yet assigned, since DHCP can lag right after start.
- `destroy(name)`: `lxc stop --force <name>` then `lxc delete <name>`,
  each idempotent — a "not found"/"already stopped" error from `lxc` is not
  a failure here, matching the protocol's documented idempotent semantics.
- `execute(request)`: build the command per section 1. Enforce the timeout
  BOTH from inside the container (wrap the command with
  `timeout --signal=KILL <seconds> bash -c <command>`, since coreutils is
  present in the template) AND as a host-side subprocess timeout slightly
  larger as a backstop, because a client-side kill of `lxc exec` is not
  guaranteed to terminate every remote process. Enforce `output_byte_cap` as
  a combined UTF-8 budget by reading stdout/stderr incrementally and
  stopping once the cap is hit (terminate the process early in that case
  too; mark `stdout_truncated`/`stderr_truncated`). Map the container-side
  `timeout` exit behavior to `ExecutionResult.timed_out=True` and
  `exit_code=None`, consistent with FakeRuntime's contract.
- `run_harness_script(name, script, purpose, timeout_seconds,
  output_byte_cap)`: purpose in {"setup", "verification", "snapshot"}.
  Always runs as root (no --user), since setup/verification legitimately
  need privileges (useradd, apt, systemctl). Same timeout/output-cap
  handling as execute(). This is trusted harness content — assert the
  caller never derives `script` from agent output (a comment noting the
  invariant is enough; this file cannot verify caller intent).

## 3. Real HostCheckRunner (implements the HostCheckRunner protocol)
Runs a trusted harness script on the control-plane host itself (used for
T6's `run_on: host` checks, e.g. probing a port from outside the
container). `subprocess.run(["bash", "-c", script], ...)`, list arguments,
never `shell=True`. Same timeout/output-cap handling as LxdRuntime.execute.
The script has already had `{{CONTAINER_IP}}`/`{{CONTAINER_NAME}}`
substituted by the orchestrator via `shlex.quote` before reaching this
class — this class does not do substitution itself.

## 4. Real Clock
```
class RealClock:
    def monotonic(self) -> float: ...   # time.monotonic()
    def sleep(self, seconds: float) -> None: ...  # time.sleep()
```

## 5. Real LLM clients (OpenAI and Google/Gemini)
One client per provider, both implementing the existing `LLMClient`
protocol (`generate(prompt, settings) -> LLMResponse`, exactly ONE attempt,
no internal SDK retries — disable the SDK's own retry behavior explicitly
so the orchestrator's accounting stays accurate). Requirements for both:
- Read the API key from `os.environ[settings.api_key_env]`; raise
  `ClientError` with a message that never includes the key itself if the
  variable is unset or empty.
- Enforce `settings.timeout_seconds` as a hard request timeout via the SDK's
  own timeout parameter, not just a wrapping `signal.alarm`.
- Pass `settings.temperature`, `settings.top_p`, `settings.max_tokens` and
  the exact `settings.model` identifier from config; never hardcode a model
  name anywhere in this file.
- Request the strictest structured/JSON-output mode the provider's current
  API offers for the given model, as a best-effort reduction in invalid-JSON
  repairs — but do not depend on it: `LLMResponse.raw_response` still goes
  through the existing `parse_output`/repair path unchanged. If you are not
  certain the configured model supports structured output, degrade
  gracefully to plain text generation with the schema described in the
  prompt text (which prompts.py already embeds) rather than raising.
- Populate `LLMResponse.model_returned`, `model_version_returned` (if the
  API exposes a distinct build/version separately from the model name;
  otherwise null), `input_tokens`, `output_tokens`, `latency_seconds`
  (measured locally, not trusted from the API), and `estimated_cost` (use
  `config.pricing` here in the client only if you choose to fill it; leaving
  it null is also acceptable — the orchestrator's Budget already computes
  cost independently and does not trust this field over its own reservation
  math, per iteration 2's `budget.charge()`).
- Any transport error, auth error, rate limit, or malformed response at the
  SDK level becomes a `ClientError`; never let a raw SDK exception escape
  this module, and never treat any exception as an approval or a successful
  response.
- No conversation state, tool use, or function calling — plain one-shot
  text generation per call.

## 6. Experiment runner (new CLI entry point)
A script (`python -m vice_sysadmin.run_experiment ...`) that:
- Loads `config.yaml` and every `tasks/*.yaml`.
- Builds the full matrix of (task × condition × repetition) from
  `config.experiment.conditions` and `.repetitions`.
- Randomizes the run order using `random.Random(config.experiment.seed)`;
  logs the seed and the exact randomized order as one `run_manifest` event
  before the first repetition, plus: config_hash, prompt hashes, Python
  version, and (if available) the current git commit hash — never fail the
  run if git metadata is unavailable, just log it as null.
- Runs repetitions sequentially (no concurrency in this iteration; state
  this explicitly as a scope limit, not an oversight — concurrent runs on
  one host would share the LXD storage pool and CPU/memory limits).
- Instantiates one `JsonlLogger` per run at `logs/<run_id>.jsonl`, passing
  `secret_env_names` built from the three configured `api_key_env` values
  (deduplicated). REFUSE to start if that log path already exists — a
  scientific run must never silently overwrite prior data.
- Wires components by flag, independently:
  - `--runtime {fake,lxd}` (default lxd): fake uses FakeRuntime (mirrors the
    test harness, for a dry run); lxd uses the real LxdRuntime.
  - Provider selection is NOT a runner flag — it comes entirely from
    `config.yaml`'s `provider: mock|openai|google` per role, exactly as
    `Config.provider_conditions` already validates. Document that an
    all-mock config with `runtime.backend: lxd` is a valid and deliberately
    supported combination: it exercises the real container lifecycle
    without spending any API tokens.
  - `--handler {deny,console}` (default deny): `deny` uses the existing
    `DenyAllHandler`; `console` is a new `ConsoleApprovalHandler` that
    prints the `EscalationRequest` to stdout and blocks on a yes/no prompt,
    for supervised pilot runs. Both honor `config.experiment.human_in_the_loop`
    exactly as the orchestrator already does (a false flag denies without
    consulting any handler, per iteration 2's tested behavior).
- Provide two example configs alongside the existing `config.yaml`:
  `configs/smoke-lxd-mock.yaml` (runtime.backend: lxd, all providers mock,
  1 repetition, conditions [A]) for the smoke-test stage, and
  `configs/pilot-live.yaml` (runtime.backend: lxd, proposer: openai,
  homogeneous_auditor: openai, heterogeneous_auditor: google, 1-2 tasks
  worth of repetitions) for the first real-API pilot. Do not invent pricing
  or model identifiers in `pilot-live.yaml`; leave clearly marked
  placeholders the operator must fill in, and make `load_config` reject the
  file as long as placeholders remain (fail loudly, not silently).

## 7. Staged testing this iteration must support (document in the README)
This is the order the operator will actually run things in, after review —
state it explicitly so nobody skips a stage:
1. `--runtime fake` with the existing all-mock `config.yaml`: re-validates
   wiring with zero real infrastructure (equivalent to the existing test
   suite, but through the CLI).
2. `--runtime lxd` with `configs/smoke-lxd-mock.yaml`: real `vice-template`
   containers, real `lxc` lifecycle and timeouts, MOCK LLM responses — this
   is where LxdRuntime bugs surface without spending a single API token.
3. `--runtime lxd` with `configs/pilot-live.yaml` and real API keys: the
   first run that spends tokens and calls real providers, limited to 1-2
   tasks and 1 repetition per condition.
Only after stage 3 looks correct does a full 18-task × N-repetition ×
3-condition run make sense.

## 8. Reporter
A script that aggregates one or more run JSONL files into CSV. Compute only
what is objectively derivable from logged events — do NOT fabricate ground
truth. Per condition and per task/category: outcome counts and rates
(success / appropriate_escalation / failure), termination_reason
distribution, mean/median steps, mean/median charged tokens and cost, mean
latency, policy block counts by rule, `high_risk` flag counts by command
name, and cleanup-failure count. Explicitly do NOT compute auditor false
positive/negative rates or any metric requiring reference labels for
commands — iteration-1's README already flagged that a separate annotation
protocol is needed first; leave a clearly labeled placeholder section in
the reporter's output and its documentation instead of guessing at labels.

## 9. Tests
Everything from iterations 1-2 must keep passing unmodified. For this
iteration, since LxdRuntime/HostCheckRunner/real clients depend on a real
`lxc` binary, real containers and real network access that do not exist in
CI or in this generation environment, testable-offline coverage is limited
to: argument-construction correctness (assert the exact subprocess argv for
each LxdRuntime method, elevate=True vs False, purpose variants, using a
fake/mocked `subprocess.run`), the incremental output-cap reading logic
against an in-memory byte stream, the runner's matrix construction and
seeded randomization (deterministic given a seed, covered without any real
I/O), the runner's refuse-to-overwrite-existing-log behavior, config
validation of the two new example configs (smoke config validates; pilot
config with placeholders present is rejected), and the reporter's
aggregation math against a small hand-built JSONL fixture. State plainly in
the README that these tests do NOT prove LxdRuntime works against a real
LXD host — only a manual run on the validated server (stage 1-2 above)
proves that.

## 10. Documentation
Update the README with: the staged testing order (section 7), the exact
manual verification steps for stage 2 (what to look at in `lxc list` and
the JSONL log to confirm a smoke-test repetition actually worked end to
end), the elevation model as implemented here, and a clear statement of
what remains manual/未finished after this iteration (for example: no
concurrency, no auditor ground-truth annotation, no automatic template
rebuild).

## Deliverable
Assumptions, risks and the SDK-version disclosure first, then the files. Do
not run anything.
```

---

## After Codex finishes

1. Zip the project (no `.git`, `.venv`, `__pycache__`, no real API keys) and
   send it for review here first, same as iterations 1 and 2.
2. Copy to a **new** folder on the server, install, and run
   `python -m pytest -q` — this only proves the offline-testable parts
   (section 9), not the real LXD path.
3. Run stage 1 (`--runtime fake`) manually.
4. Run stage 2 (`--runtime lxd` with `configs/smoke-lxd-mock.yaml`) against
   `vice-template/base` and inspect the JSONL log and `lxc list` by hand
   before touching any real API key.
5. Only then move to stage 3 with real keys, starting with 1-2 tasks.

## Not part of this prompt

Concurrency/parallel repetitions, the auditor ground-truth annotation
protocol and its false-positive/negative metrics, automatic template
rebuilding, and anything beyond a single-host experiment.
