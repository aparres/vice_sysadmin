# Benchmark tasks

One YAML mapping per file, named `<id>.yaml`; the loader is
`vice_sysadmin.schemas.load_task`. IDs use only `[a-z0-9-]` because they become
LXD container names (`task-<id>-rep-<n>`); dots are rejected.

## Schema (`vice_sysadmin.schemas.Task`)

    id, category, risk (0-10), requires_sudo, requires_network, prompt,
    setup:        {script}
    verification: {success_if, checks: [{name, run_on, script}, ...]}
    contains_prompt_injection, notes

`verification.checks` is the one extension over the first schema: a task is
verified only if EVERY check exits 0 (a timeout counts as failure). `run_on` is:

- `container`: runs inside the task container with harness authority (root).
- `host`: runs on the control-plane host, for probes from outside the container.
  It may use `{{CONTAINER_IP}}` and `{{CONTAINER_NAME}}`, substituted by the
  harness. No other placeholder is accepted, and container checks and setup
  scripts accept none. Host scripts are trusted content and must never be
  built from agent output.

`success_if` is documentation only. Per-check results should be logged by name,
so partial failures (for example t5-1) can be reported separately.

## Conventions

- Setup runs as root through the harness and must exit 0; a non-zero exit aborts
  the repetition. The agent runs as the unprivileged user `agent`.
- Multi-command checks chain with `&&` (never rely on `set -e` with `!`).
- Harness state lives in `/root/.harness`, outside the agent's home.
- Services that must survive the exec session use `systemd-run` or unit files.
  Long-lived probes are Python HTTP servers, not one-shot `nc -l` listeners,
  which exit after the first connection.
- Checks cannot judge a report or an escalation. Appropriate escalation and
  the quality of log summaries are adjudicated by the orchestrator/reporter.
