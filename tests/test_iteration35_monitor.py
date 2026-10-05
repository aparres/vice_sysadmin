"""Deterministic parser/aggregation/formatting tests; never run the refresh loop."""

from datetime import datetime, timedelta, timezone
import json

import pytest

from vice_sysadmin.monitor import MonitorError, Progress, Tail, render
from vice_sysadmin.schemas import Outcome

START = datetime(2026, 9, 29, tzinfo=timezone.utc)
ORDER = [("task-one", "A", 1), ("task-one", "A", 2),
         ("task-one", "B", 1), ("task-two", "C", 1)]


def manifest():
    return dict(event_type="run_manifest", run_id="test-run", seed=42,
                run_order=[dict(task_id=t, condition=c, repetition=r) for t, c, r in ORDER])


def event(kind, identity, seconds, **fields):
    task, condition, repetition = identity
    return dict(event_type=kind, run_id="test-run", task_id=task, condition=condition,
                repetition=repetition, step=2, timestamp=(START + timedelta(seconds=seconds)).isoformat(),
                **fields)


def rows():
    result = [manifest()]
    for identity, start, duration, outcome, reason in [
        (ORDER[0], 0, 10, Outcome.SUCCESS.value, "task_completed"),
        (ORDER[1], 10, 20, Outcome.FAILURE.value, "step_limit"),
        (ORDER[2], 30, 30, Outcome.APPROPRIATE_ESCALATION.value, "escalated"),
    ]:
        result.extend([event("run_started", identity, start),
                       event("run_finished", identity, start + duration, outcome=outcome,
                             termination_reason=reason, latency_seconds=duration)])
    return result


def encode(records):
    return b"".join((json.dumps(row) + "\n").encode() for row in records)


def feed(records):
    progress = Progress()
    for row in records:
        progress.accept(row)
    return progress


def test_matrix_progress_outcomes_and_eta():
    progress = feed(rows())
    assert progress.run_id == "test-run" and progress.seed == 42
    assert progress.order == ORDER and len(progress.finished) == 3
    assert progress.cells == {("task-one", "A"): 2, ("task-one", "B"): 1, ("task-two", "C"): 1}
    assert progress.done_cells == {("task-one", "A"): 2, ("task-one", "B"): 1}
    assert progress.outcomes == {outcome.value: 1 for outcome in Outcome}
    assert progress.reasons == {"task_completed": 1, "step_limit": 1, "escalated": 1}
    assert progress.average == 20 and progress.eta(START) == 20
    assert not progress.complete
    display = render(progress, START)
    assert "Completed: 3/4" in display and "Rough ETA (not guaranteed): 00:00:20" in display
    assert "task-one | 2/2 | 1/1 | 0/0" in display
    assert "task-two | 0/0 | 0/0 | 0/1" in display


def test_partial_line_is_retained_then_read_once(tmp_path):
    path = tmp_path / "run.jsonl"
    records = rows()
    data = encode(records)
    split = len(data) - 12
    path.write_bytes(data[:split])
    progress, tail = Progress(), Tail(path)
    tail.poll(progress)
    assert len(progress.finished) == 2 and tail.pending
    assert tail.offset == split
    offset = tail.offset
    tail.poll(progress)
    assert tail.offset == offset and len(progress.finished) == 2
    with path.open("ab") as stream:
        stream.write(data[split:])
    tail.poll(progress)
    assert len(progress.finished) == 3 and tail.pending == b""
    assert tail.lines == len(records)
    assert path.read_bytes() == data


def test_all_planned_finishes_mark_complete_without_loop():
    progress = feed(rows())
    progress.accept(event("run_started", ORDER[3], 60))
    assert not progress.complete
    progress.accept(event("cleanup", ORDER[3], 65, cleanup_succeeded=True))
    assert not progress.complete  # Cleanup or verification is not completion.
    progress.accept(event("run_finished", ORDER[3], 70, outcome="success",
                          termination_reason="task_completed", latency_seconds=10))
    assert progress.complete and progress.active is None and progress.eta(START) == 0
    assert "COMPLETE" in render(progress, START)


def test_elapsed_stall_and_runner_failure_are_informational():
    progress = feed(rows())
    progress.accept(event("run_started", ORDER[3], 60))
    now = START + timedelta(seconds=121)
    assert progress.elapsed(now) == 61
    assert "MAY BE STALLED" in render(progress, now)
    assert "MAY BE STALLED" not in render(progress, now, stall_multiplier=4)
    progress.accept(event("runner_failed", ORDER[3], 122, error="NEVER_DISPLAY_RAW_ERROR"))
    assert progress.eta(now) is None and not progress.complete
    assert "Runner failure recorded" in render(progress, now)
    assert "NEVER_DISPLAY" not in render(progress, now)


def test_no_samples_no_eta_or_stall():
    progress = feed([manifest(), event("run_started", ORDER[0], 0)])
    text = render(progress, START + timedelta(seconds=999))
    assert "unavailable" in text and "MAY BE STALLED" not in text


def test_duplicate_finish_and_unknown_identity_do_not_inflate_counts():
    progress = feed(rows())
    with pytest.raises(MonitorError):
        progress.accept(rows()[-1])
    assert len(progress.finished) == 3
    with pytest.raises(MonitorError):
        progress.accept(event("run_finished", ("unknown", "A", 1), 70, outcome="success"))
    assert not progress.complete


def test_duplicate_manifest_and_mixed_run_rejected():
    progress = feed([manifest()])
    with pytest.raises(MonitorError):
        progress.accept(manifest())
    wrong = event("run_started", ORDER[0], 0)
    wrong["run_id"] = "different"
    with pytest.raises(MonitorError):
        progress.accept(wrong)


def test_truncated_file_and_malformed_complete_line_fail_safely(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_bytes(encode([manifest()]))
    tail, progress = Tail(path), Progress()
    tail.poll(progress)
    path.write_bytes(b"")
    with pytest.raises(MonitorError, match="truncated"):
        tail.poll(progress)
    path.write_bytes(b"SECRET_RAW_INVALID_JSON\n")
    with pytest.raises(MonitorError) as caught:
        Tail(path).poll(Progress())
    assert "SECRET" not in str(caught.value)


def test_display_never_renders_arbitrary_event_payloads():
    records = rows()
    for row in records:
        row.update(prompt_sent="PRIVATE_PROMPT", raw_response="PRIVATE_RESPONSE",
                   command="PRIVATE_COMMAND", stdout="PRIVATE_STDOUT", stderr="PRIVATE_STDERR",
                   error="PRIVATE_ERROR", model_returned="PRIVATE_MODEL")
    text = render(feed(records), START)
    assert "PRIVATE_" not in text
    invalid = manifest()
    invalid["run_id"] = "escape\x1b[31m"
    with pytest.raises(MonitorError):
        feed([invalid])


def test_missing_duration_falls_back_to_timestamp_difference():
    records = rows()
    del records[-1]["latency_seconds"]
    assert feed(records).average == 20


def test_bounded_reads_resume_from_offset(tmp_path, monkeypatch):
    from vice_sysadmin import monitor
    data = encode([manifest()])
    path = tmp_path / "run.jsonl"
    path.write_bytes(data)
    monkeypatch.setattr(monitor, "READ_BYTES", len(data) - 1)
    tail, progress = Tail(path), Progress()
    tail.poll(progress)
    assert progress.run_id is None and tail.offset == len(data) - 1
    tail.poll(progress)
    assert progress.run_id == "test-run" and tail.offset == len(data)
    assert tail.caught_up and tail.lines == 1
    tail.poll(progress)
    assert tail.lines == 1


def test_backlog_suppresses_eta_and_stall_warning():
    progress = feed(rows())
    progress.accept(event("run_started", ORDER[3], 60))
    text = render(progress, START + timedelta(seconds=999), caught_up=False)
    assert "Catching up" in text and "MAY BE STALLED" not in text
    assert "Rough ETA (not guaranteed): unavailable" in text
