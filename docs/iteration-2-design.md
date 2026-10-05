# Iteration 2 decisions and review record

The operator authorized importing Vice_SysAdmin_v2.zip and implementing the
attached iteration-2 prompt, generating files without running tests or system
commands. Files were imported/read/written with filesystem APIs and patch tools;
the Python project, task scripts, package managers, and Linux commands were not run.

## Assumptions recorded before implementation

- The ZIP's verification.checks extension is the accepted task schema baseline.
- API clients, container/host execution, and scheduling remain injected fakes.
- Fresh production nonces and deterministic comparisons coexist through an
  injectable nonce factory; B/C identity means identical content and nonce inputs.
- Model input bounds use UTF-8 byte length plus a configurable overhead. Output
  uses max_tokens. Future providers must support that reservation contract.
- Lifecycle methods need optional timeout keywords to accept remaining time.
  Synchronous interfaces cannot kill a nonconforming implementation themselves.
- Model/token/cost exhaustion does not prevent token-free verification, provided
  the global time budget remains. Expired deadlines record skipped/failed checks.
- Prompt schema and config changes are additive relative to the ZIP.

## Existing files intentionally changed

| File | Reason |
| --- | --- |
| vice_sysadmin/schemas.py | Add defaulted proposal/config/event fields and new result contracts. |
| vice_sysadmin/interfaces.py | Add clock, host, handler, logger and IP contracts; optional lifecycle timeouts; single-attempt client contract. |
| vice_sysadmin/mocks.py | Add shared logical clock, timeout simulation, host/handler fakes, IP lookup, and snapshot authority. |
| config.yaml | Select v1 prompts and expose read-only snapshots and cleanup allowance. |
| README.md | Describe the core, assumptions, limitations, dependency evidence, and iteration-3 boundary. |
| prompts/README.md | Replace the reserved-directory note with template/provenance guidance. |
| requirements.txt | Replace stale Pydantic 2.11.7 with operator-confirmed 2.13.5. |

The ZIP also replaced tasks/README.md, tests/test_foundations.py, README.md and
schemas.py relative to the earlier workspace, and supplied the 18 task YAMLs and
tests/test_tasks.py. Backup copies are under review/iteration-1-backup. Imported
tests and task YAMLs were not edited after import. logger.py needed no edits: it
serializes all Event fields dynamically, including the new optional fields.

New modules implement policy, prompts, structured handling, budgets, adjudication,
and the orchestrator. New tests, templates, a full dependency lock, this design
record, and a copy of the supplied prompt were added.

## Clarifications and material limits

The operator supplied exact dependency versions during implementation. Python is
3.14.x, patch unconfirmed. PyYAML 6.0.2 was locally built as pure Python without
libyaml; it is not claimed to have a prebuilt cp314 wheel. No versions were
looked up or installed. requirements.lock.txt preserves the supplied full freeze.

Global termination reason, scientific outcome, and cleanup status are separate.
For example, passing checks take precedence over a prior escalation; a cleanup
failure is separately visible even when checks establish success. Setup failure
always gives failure. A runtime exception after a command starts counts that
command conservatively for read-only adjudication.

The handler returns only approval, not an operator answer. Approving an empty
question permits another proposal. human_in_the_loop=false always denies an
escalation without consulting a provided handler. Logging failure propagates
after cleanup; the code cannot promise durable events when storage itself fails.

No claim is made that policy or delimiters prevent arbitrary shell or prompt
injection. No real runtime isolation, process-tree termination, provider billing,
or model capability has been validated. The configured read-only list is part of
the experimental protocol, and changes require scientific review.

The preserved 42-test result belongs to the operator's baseline. New tests are
authored but NOT RUN. Review and execution on the specified Linux environment
remain necessary before reporting any iteration-2 passing-test count.
