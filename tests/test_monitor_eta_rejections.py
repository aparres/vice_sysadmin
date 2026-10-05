"""ETA formatting and confirmed rejection reasons from redacted logs."""

import json

import pytest

from test_iteration35_monitor import START, ORDER, feed, manifest, encode
from test_monitor_recent_commands import command_event
from vice_sysadmin.monitor import REASON_PREVIEW_CHARS, Progress, Tail, eta_clock, render


def raw(reason="Missing prerequisite", verdict="reject"):
    return json.dumps(dict(verdict=verdict, risk=1, risk_category="correctness", justification=reason))


def pending():
    return feed([manifest(), command_event("policy", command="id", policy_decision="approve")])


@pytest.mark.parametrize("value,expected", [(None, "unavailable"), (0, "00:00:00"),
    (0.1, "00:00:01"), (59.1, "00:01:00"), (3661, "01:01:01"), (90061, "25:01:01")])
def test_eta_clock(value, expected):
    assert eta_clock(value) == expected


@pytest.mark.parametrize("fenced", [False, True])
def test_reason_only_after_confirmed_rejection(fenced):
    progress = pending()
    response = raw()
    if fenced:
        response = "```json\n" + response + "\n```"
    progress.accept(command_event("auditor_attempt", raw_response=response))
    assert "Missing prerequisite" not in render(progress, START)
    progress.accept(command_event("audit", verdict="reject"))
    assert 'Auditor rejection reason: "Missing prerequisite"' in render(progress, START)


@pytest.mark.parametrize("last", ["invalid", raw("", "reject"), raw("approve reason", "approve"), None])
def test_latest_attempt_replaces_old_reason(last):
    progress = pending()
    progress.accept(command_event("auditor_attempt", raw_response=raw("STALE_REASON")))
    progress.accept(command_event("auditor_attempt", raw_response=last))
    progress.accept(command_event("audit", verdict="reject"))
    text = render(progress, START)
    assert "STALE_REASON" not in text and "unavailable (not recorded" in text


@pytest.mark.parametrize("kind", ["auditor_invalid_output", "audit_failure"])
def test_invalid_or_failed_attempt_does_not_supply_reason(kind):
    progress = pending()
    progress.accept(command_event("auditor_attempt", raw_response=raw("STALE_REASON")))
    progress.accept(command_event(kind))
    progress.accept(command_event("audit", verdict="reject"))
    assert "STALE_REASON" not in render(progress, START)


@pytest.mark.parametrize("verdict", ["approve", "escalate"])
def test_non_rejections_do_not_display_reason(verdict):
    progress = pending()
    progress.accept(command_event("auditor_attempt", raw_response=raw("HIDDEN")))
    progress.accept(command_event("audit", verdict=verdict))
    assert "Auditor rejection reason:" not in render(progress, START)
    assert "HIDDEN" not in render(progress, START)


def test_transport_error_and_missing_attempt_have_no_reason():
    for records in ([], [command_event("auditor_attempt", raw_response=raw("HIDDEN"), error="timeout")]):
        progress = pending()
        for record in records:
            progress.accept(record)
        progress.accept(command_event("audit", verdict="reject"))
        text = render(progress, START)
        assert "HIDDEN" not in text and "unavailable (not recorded" in text


def test_reasons_are_escaped_bounded_and_joined_by_identity():
    progress = pending()
    progress.accept(command_event("policy", ORDER[3], command="id", policy_decision="approve"))
    reason = "control\x1b[31m\n\r\t\u202e" + "x" * 700
    progress.accept(command_event("auditor_attempt", ORDER[2], raw_response=raw(reason)))
    progress.accept(command_event("audit", ORDER[3], verdict="reject"))
    assert progress.recent_commands[1]["rejection_reason"] is None
    progress.accept(command_event("audit", ORDER[2], verdict="reject"))
    preview = progress.recent_commands[0]["rejection_reason"]
    assert preview.endswith(" [truncated]")
    assert json.loads(preview.removesuffix(" [truncated]")) == reason[:REASON_PREVIEW_CHARS]
    assert "\x1b" not in preview and "\n" not in preview and "\u202e" not in preview


def test_partial_response_waits_for_full_line_and_audit(tmp_path):
    path = tmp_path / "events.jsonl"
    initial = encode([manifest(), command_event("policy", command="id", policy_decision="approve")])
    attempt = encode([command_event("auditor_attempt", raw_response=raw("WAIT_FOR_AUDIT"))])
    path.write_bytes(initial + attempt[:-3])
    progress, tail = Progress(), Tail(path)
    tail.poll(progress)
    assert "WAIT_FOR_AUDIT" not in render(progress, START)
    with path.open("ab") as stream:
        stream.write(attempt[-3:])
    tail.poll(progress)
    assert "WAIT_FOR_AUDIT" not in render(progress, START)
    verdict = encode([command_event("audit", verdict="reject")])
    with path.open("ab") as stream:
        stream.write(verdict)
    tail.poll(progress)
    assert "WAIT_FOR_AUDIT" in render(progress, START)
    assert path.read_bytes() == initial + attempt + verdict
