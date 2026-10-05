"""Offline batch boundary pause/resume tests; no live experiment or subprocess."""

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_orchestrator import Scenario
from vice_sysadmin import run_experiment as runner
from vice_sysadmin.batch import BatchPaused, BatchPausedEvent, COUNTED_REASONS, checkpoint_lock
from vice_sysadmin.schemas import Event, Outcome, config_hash, container_name

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def batch(tmp_path, monkeypatch):
    scenario = Scenario(tmp_path)
    scenario.configure("experiment", repetitions=3, conditions=["A", "B", "C"])
    reasons, seen = [], []
    monkeypatch.setattr(runner, "get_git_commit", lambda: None)

    def repetition(task, condition, rep, config, proposer, auditor, runtime, host,
                   logger, clock, handler, run_id, *, prompts):
        seen.append((task.id, condition, rep))
        reason = reasons.pop(0) if reasons else "task_completed"
        context = dict(run_id=run_id, task_id=task.id, condition=condition, repetition=rep,
                       step=1, timestamp=datetime.now(timezone.utc),
                       container_name=container_name(task.id, rep), config_hash=config_hash(config))
        logger.write(Event(**context, event_type="run_started", prompt_hashes=prompts.hashes))
        logger.write(Event(**context, event_type="cleanup", cleanup_succeeded=True))
        logger.write(Event(**context, event_type="run_finished", termination_reason=reason,
                           outcome=Outcome.FAILURE if reason != "task_completed" else Outcome.SUCCESS,
                           cleanup_succeeded=True))
        return SimpleNamespace(termination_reason=reason, outcome=Outcome.FAILURE,
                               cleanup_succeeded=True, steps=1)

    monkeypatch.setattr(runner, "run_task_repetition", repetition)
    kwargs = dict(runtime_backend="fake", handler_name="deny", run_id="pause-test",
                  log_directory=tmp_path / "logs", config_directory=ROOT)
    state = tmp_path / "logs/pause-test.state.json"
    log = tmp_path / "logs/pause-test.jsonl"
    return SimpleNamespace(scenario=scenario, reasons=reasons, seen=seen, kwargs=kwargs,
                           state=state, log=log, tmp=tmp_path)


def execute(batch):
    return runner.execute_experiment(batch.scenario.config, [batch.scenario.task], **batch.kwargs)


def events(batch):
    return [json.loads(line) for line in batch.log.read_text().splitlines()]


@pytest.mark.parametrize("first", sorted(COUNTED_REASONS))
@pytest.mark.parametrize("second", sorted(COUNTED_REASONS))
def test_second_consecutive_failure_pauses_with_exact_causes(batch, first, second, capsys):
    batch.reasons[:] = [first, second]
    with pytest.raises(BatchPaused):
        execute(batch)
    assert len(batch.seen) == 2
    rows = events(batch)
    assert not any(row["event_type"] == "runner_failed" for row in rows)
    pause = rows[-1]
    assert pause["event_type"] == "batch_paused"
    BatchPausedEvent.model_validate_json(json.dumps(pause))
    assert [item["termination_reason"] for item in pause["triggering_repetitions"]] == [first, second]
    state = json.loads(batch.state.read_text())
    assert state["status"] == "paused" and state["next_index"] == 2
    assert pause["triggering_repetitions"] == state["completed"]
    assert pause["next_repetition"] == state["run_order"][2]
    assert Path(pause["checkpoint_path"]) == batch.state.resolve()
    text = capsys.readouterr().out
    assert "BATCH PAUSED" in text and first in text and second in text and "Next:" in text
    assert "--resume" in text


@pytest.mark.parametrize("reason", ["task_completed", "escalated", "step_limit", "token_limit",
                                    "cost_limit", "time_limit", "conflicting_proposal", "runtime_error",
                                    "execution_output_limit"])
def test_other_closes_reset_streak(batch, reason):
    batch.reasons[:] = ["proposer_failure", reason, "audit_failure", "task_completed"]
    execute(batch)
    assert len(batch.seen) == 9
    assert not any(row["event_type"] == "batch_paused" for row in events(batch))


def test_resume_preserves_order_and_resets_counter(batch):
    batch.reasons[:] = ["execution_timeout", "audit_failure"]
    with pytest.raises(BatchPaused):
        execute(batch)
    original = json.loads(batch.state.read_text())
    batch.reasons[:] = ["setup_failed", "task_completed"]
    runner.resume_experiment(batch.state)
    expected = [(item["task_id"], item["condition"], item["repetition"]) for item in original["run_order"]]
    assert batch.seen == expected and len(set(batch.seen)) == 9
    final = json.loads(batch.state.read_text())
    assert final["status"] == "complete" and final["next_index"] == 9
    assert final["run_order"] == original["run_order"]
    assert sum(row["event_type"] == "run_manifest" for row in events(batch)) == 1
    assert sum(row["event_type"] == "batch_paused" for row in events(batch)) == 1
    runner.resume_experiment(batch.state)
    assert batch.seen == expected


def test_repeated_pause_resume_segments_do_not_repeat_or_skip(batch):
    batch.reasons[:] = ["audit_failure"] * 9
    with pytest.raises(BatchPaused):
        execute(batch)
    for _ in range(3):
        with pytest.raises(BatchPaused):
            runner.resume_experiment(batch.state)
    runner.resume_experiment(batch.state)
    assert len(batch.seen) == len(set(batch.seen)) == 9


def test_checkpoint_crash_after_logged_close_recovers_only_complete_prefix(batch):
    batch.reasons[:] = ["proposer_failure", "audit_failure"]
    with pytest.raises(BatchPaused):
        execute(batch)
    state = json.loads(batch.state.read_text())
    state["completed"] = state["completed"][:1]
    state["next_index"] = 1
    batch.state.write_text(json.dumps(state))
    runner.resume_experiment(batch.state)
    assert len(batch.seen) == len(set(batch.seen)) == 9


@pytest.mark.parametrize("damage", ["partial", "unclosed", "duplicate", "ahead", "inputs", "cleanup", "order"])
def test_unsafe_resume_rejected_before_another_repetition(batch, damage):
    batch.reasons[:] = ["proposer_failure", "audit_failure"]
    with pytest.raises(BatchPaused):
        execute(batch)
    rows = events(batch)
    state = json.loads(batch.state.read_text())
    if damage == "partial":
        with batch.log.open("ab") as stream:
            stream.write(b'{"event_type":')
    elif damage == "unclosed":
        row = next(row.copy() for row in rows if row["event_type"] == "run_started")
        item = state["run_order"][2]
        row.update(task_id=item["task_id"], condition=item["condition"], repetition=item["repetition"],
                   container_name=container_name(item["task_id"], item["repetition"]))
        with batch.log.open("a") as stream:
            stream.write(json.dumps(row) + "\n")
    elif damage == "duplicate":
        with batch.log.open("a") as stream:
            stream.write(json.dumps(rows[0]) + "\n")
    elif damage == "ahead":
        state["completed"].append({**state["run_order"][2], "termination_reason": "task_completed"})
        state["next_index"] = 3
        batch.state.write_text(json.dumps(state))
    elif damage == "inputs":
        state["config"]["limits"]["max_steps"] += 1
        batch.state.write_text(json.dumps(state))
    else:
        row = next(row for row in rows if row["event_type"] == "run_finished")
        if damage == "cleanup":
            row["cleanup_succeeded"] = False
        else:
            row["condition"] = "C" if row["condition"] != "C" else "A"
        batch.log.write_text("".join(json.dumps(row) + "\n" for row in rows))
    with pytest.raises((ValueError, KeyError)):
        runner.resume_experiment(batch.state)
    assert len(batch.seen) == 2


def test_prompt_change_blocks_resume(batch, monkeypatch):
    batch.reasons[:] = ["proposer_failure", "audit_failure"]
    with pytest.raises(BatchPaused):
        execute(batch)
    original = runner.PromptBook
    def changed(**kwargs):
        book = original(**kwargs)
        book.hashes["proposer.v1.txt"] = "f" * 64
        return book
    monkeypatch.setattr(runner, "PromptBook", changed)
    with pytest.raises(ValueError, match="inputs differ"):
        runner.resume_experiment(batch.state)
    assert len(batch.seen) == 2


def test_exclusive_lock_prevents_parallel_resume(batch):
    batch.reasons[:] = ["proposer_failure", "audit_failure"]
    with pytest.raises(BatchPaused):
        execute(batch)
    with checkpoint_lock(batch.state.with_suffix(".lock")):
        with pytest.raises(ValueError, match="already running"):
            runner.resume_experiment(batch.state)
    assert len(batch.seen) == 2


def test_cli_pause_exit_and_resume_override_rejection(batch):
    batch.reasons[:] = ["proposer_failure", "audit_failure"]
    with pytest.raises(BatchPaused):
        execute(batch)
    assert runner.main(["--resume", str(batch.state), "--runtime", "fake"]) == 2
    assert len(batch.seen) == 2
    batch.reasons[:] = ["execution_timeout", "setup_failed"]
    assert runner.main(["--resume", str(batch.state)]) == 3
    assert len(batch.seen) == 4


def test_real_offline_repetition_failure_path_is_unchanged(tmp_path, monkeypatch):
    scenario = Scenario(tmp_path)
    scenario.configure("experiment", conditions=["A"], repetitions=3)
    fixture = tmp_path / "failed-proposer.json"
    fixture.write_text(json.dumps([{"error": "offline transport unavailable"}]))
    scenario.configure("proposer", mock_fixture=str(fixture), retries=0)
    monkeypatch.setattr(runner, "get_git_commit", lambda: None)
    with pytest.raises(BatchPaused):
        runner.execute_experiment(scenario.config, [scenario.task], runtime_backend="fake",
            handler_name="deny", run_id="actual-failure", log_directory=tmp_path / "logs", config_directory=ROOT)
    rows = [json.loads(line) for line in (tmp_path / "logs/actual-failure.jsonl").read_text().splitlines()]
    closes = [row for row in rows if row["event_type"] == "run_finished"]
    assert len(closes) == 2 and all(row["termination_reason"] == "proposer_failure" for row in closes)
    assert all(row["cleanup_succeeded"] for row in closes)
    assert not any(row["event_type"] == "execute_requested" for row in rows)


def test_multiple_tasks_resume_uses_saved_matrix(batch):
    other = batch.scenario.task.model_copy(update={"id": "second-task"})
    tasks = [batch.scenario.task, other]
    batch.reasons[:] = ["execution_timeout", "audit_failure"]
    with pytest.raises(BatchPaused):
        runner.execute_experiment(batch.scenario.config, tasks, **batch.kwargs)
    matrix = json.loads(batch.state.read_text())["run_order"]
    runner.resume_experiment(batch.state)
    expected = [(item["task_id"], item["condition"], item["repetition"]) for item in matrix]
    assert batch.seen == expected and len(set(batch.seen)) == 18


def test_cleanup_failure_takes_priority_and_blocks_resume(batch, monkeypatch):
    original = runner.run_task_repetition
    def failed_cleanup(*args, **kwargs):
        result = original(*args, **kwargs)
        result.cleanup_succeeded = False
        return result
    monkeypatch.setattr(runner, "run_task_repetition", failed_cleanup)
    with pytest.raises(RuntimeError, match="Cleanup failed"):
        execute(batch)
    assert len(batch.seen) == 1
    assert not any(row["event_type"] == "batch_paused" for row in events(batch))
    with pytest.raises(ValueError, match="Runner failure"):
        runner.resume_experiment(batch.state)
    assert len(batch.seen) == 1


def test_final_two_failures_have_no_next_repetition(batch):
    batch.reasons[:] = ["task_completed"] * 7 + ["proposer_failure", "setup_failed"]
    with pytest.raises(BatchPaused):
        execute(batch)
    assert events(batch)[-1]["next_repetition"] is None
    assert len(batch.seen) == 9
    runner.resume_experiment(batch.state)
    assert len(batch.seen) == 9


def test_copied_checkpoint_cannot_bypass_run_lock(batch):
    batch.reasons[:] = ["proposer_failure", "audit_failure"]
    with pytest.raises(BatchPaused):
        execute(batch)
    copied = batch.tmp / "copy.state.json"
    copied.write_bytes(batch.state.read_bytes())
    with pytest.raises(ValueError, match="log path differs"):
        runner.resume_experiment(copied)
    assert len(batch.seen) == 2


def test_failed_checkpoint_replace_preserves_old_state_and_starts_no_work(batch, monkeypatch):
    from vice_sysadmin import batch as persistence
    batch.reasons[:] = ["proposer_failure", "audit_failure"]
    with pytest.raises(BatchPaused):
        execute(batch)
    before = batch.state.read_bytes()
    calls = []
    def denied(*args):
        calls.append(args)
        raise PermissionError("simulated filesystem denial")
    monkeypatch.setattr(persistence.os, "replace", denied)
    monkeypatch.setattr(persistence.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        runner.resume_experiment(batch.state)
    assert batch.state.read_bytes() == before
    assert len(batch.seen) == 2 and 1 <= len(calls) <= 25
    assert not list(batch.state.parent.glob("*.tmp-*"))
