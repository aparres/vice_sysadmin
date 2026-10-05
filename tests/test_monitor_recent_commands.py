"""Recent command/verdict aggregation, tested without the refresh loop."""

import json

import pytest

from test_iteration35_monitor import START, ORDER, encode, event, feed, manifest
from vice_sysadmin.logger import JsonlLogger
from vice_sysadmin.monitor import COMMAND_PREVIEW_CHARS, Progress, Tail, render
from vice_sysadmin.schemas import Event


def command_event(kind, identity=ORDER[2], step=1, **fields):
    row = event(kind, identity, 1, **fields)
    row["step"] = step
    return row


def test_latest_ten_are_in_evaluation_order_not_verdict_order():
    progress = feed([manifest()])
    for step in range(1, 13):
        progress.accept(command_event("policy", step=step, command=f"echo {step}", policy_decision="approve"))
    assert [entry["step"] for entry in progress.recent_commands] == list(range(3, 13))
    progress.accept(command_event("audit", step=4, verdict="reject"))
    assert len(progress.recent_commands) == 10
    assert progress.recent_commands[1]["auditor"] == "reject"
    text = render(progress, START)
    assert text.index('"echo 12"') < text.index('"echo 3"')
    assert '"echo 1"' not in text


@pytest.mark.parametrize("identity", [ORDER[2], ORDER[3]])
@pytest.mark.parametrize("verdict", ["approve", "reject", "escalate"])
def test_bc_pending_updates_with_logged_verdict(identity, verdict):
    progress = feed([manifest(), command_event("policy", identity, command="id", policy_decision="approve")])
    assert progress.recent_commands[0]["auditor"] == "pending"
    progress.accept(command_event("auditor_attempt", identity, attempt=1, raw_response="NOT_RENDERED"))
    progress.accept(command_event("auditor_attempt", identity, attempt=2, raw_response="NOT_RENDERED"))
    assert len(progress.recent_commands) == 1
    progress.accept(command_event("audit", identity, command="id", verdict=verdict))
    assert progress.recent_commands[0]["auditor"] == verdict
    assert "NOT_RENDERED" not in render(progress, START)


def test_a_is_not_applicable_and_blocked_bc_is_not_pending():
    progress = feed([manifest(),
        command_event("policy", ORDER[0], command="id", policy_decision="approve"),
        command_event("policy", ORDER[2], command="reboot", policy_decision="reject"),
    ])
    assert progress.recent_commands[0]["auditor"] == "N/A (condition A)"
    assert progress.recent_commands[1]["auditor"] == "not sent (policy rejected)"


def test_same_command_and_step_do_not_mix_repetitions_or_conditions():
    progress = feed([manifest()])
    for identity in ORDER:
        progress.accept(command_event("policy", identity, command="id", policy_decision="approve"))
    progress.accept(command_event("audit", ORDER[3], verdict="reject"))
    progress.accept(command_event("audit", ORDER[2], verdict="approve"))
    assert [entry["auditor"] for entry in progress.recent_commands] == [
        "N/A (condition A)", "N/A (condition A)", "approve", "reject"]
    assert not progress.finished and sum(progress.outcomes.values()) == 0


@pytest.mark.parametrize("approved", [False, True])
def test_policy_escalation_and_missing_verdict(approved):
    progress = feed([manifest(), command_event("policy", command="id", policy_decision="escalate")])
    assert progress.recent_commands[0]["auditor"] == "waiting (policy escalation)"
    progress.accept(command_event("escalation", verdict="approve" if approved else "escalate"))
    expected = "pending" if approved else "not sent (policy escalation denied)"
    assert progress.recent_commands[0]["auditor"] == expected
    progress.accept(command_event("runner_failed", error="DO_NOT_DISPLAY"))
    assert progress.recent_commands[0]["auditor"] == (
        "unavailable (no verdict logged)" if approved else expected)
    assert "DO_NOT_DISPLAY" not in render(progress, START)


def test_audit_failure_has_no_invented_verdict():
    progress = feed([manifest(), command_event("policy", command="id", policy_decision="approve"),
                     command_event("audit_failure", error="PRIVATE_ERROR")])
    assert progress.recent_commands[0]["auditor"] == "error (no verdict)"
    assert "PRIVATE_ERROR" not in render(progress, START)


def test_partial_audit_line_updates_existing_entry_on_next_poll(tmp_path):
    path = tmp_path / "events.jsonl"
    initial = encode([manifest(), command_event("policy", command="id", policy_decision="approve")])
    verdict = encode([command_event("audit", command="id", verdict="approve")])
    path.write_bytes(initial + verdict[:-3])
    tail, progress = Tail(path), Progress()
    tail.poll(progress)
    assert progress.recent_commands[0]["auditor"] == "pending"
    with path.open("ab") as stream:
        stream.write(verdict[-3:])
    tail.poll(progress)
    assert len(progress.recent_commands) == 1
    assert progress.recent_commands[0]["auditor"] == "approve"
    tail.poll(progress)
    assert len(progress.recent_commands) == 1
    assert path.read_bytes() == initial + verdict


def test_proposal_and_execution_events_do_not_duplicate_commands():
    progress = feed([manifest()])
    for kind in ("proposal", "proposal_blocked", "execute_requested", "executed"):
        progress.accept(command_event(kind, command="NOT_EVALUATED"))
    assert not progress.recent_commands
    row = command_event("policy", command="id", policy_decision="disabled")
    progress.accept(row)
    progress.accept(row)
    assert len(progress.recent_commands) == 1


def test_terminal_controls_and_long_commands_are_safe_previews():
    command = "echo \x1b[31m\n\r\t\u202e" + "x" * 400
    progress = feed([manifest(), command_event("policy", command=command, policy_decision="approve")])
    preview = progress.recent_commands[0]["command"]
    assert "\x1b" not in preview and "\n" not in preview and "\u202e" not in preview
    assert preview.endswith(" [truncated]")
    assert json.loads(preview.removesuffix(" [truncated]")) == command[:COMMAND_PREVIEW_CHARS]


def test_commands_use_existing_logger_redaction(tmp_path):
    path = tmp_path / "redacted.jsonl"
    path.write_bytes(encode([manifest()]))
    secret = "test-private-value-123"
    logger = JsonlLogger(path, secrets=[secret])
    logger.write(Event(run_id="test-run", task_id="task-one", condition="B", repetition=1,
        step=1, timestamp=START, container_name="task-task-one-rep-1", config_hash="a" * 64,
        event_type="policy", command="echo " + secret, policy_decision="approve"))
    progress = Progress()
    Tail(path).poll(progress)
    text = render(progress, START)
    assert secret not in text and "[REDACTED]" in text


def test_finish_closes_pending_and_retains_history():
    progress = feed([manifest(), command_event("run_started"),
        command_event("policy", command="id", policy_decision="approve"),
        command_event("run_finished", outcome="failure", termination_reason="token_limit", latency_seconds=0),
    ])
    assert progress.recent_commands[0]["auditor"] == "unavailable (no verdict logged)"
    assert len(progress.finished) == 1
