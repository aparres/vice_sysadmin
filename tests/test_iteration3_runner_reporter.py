"""Pure matrix/reporting tests and temporary-file validation; no live execution."""

import json
from pathlib import Path

import pytest

from vice_sysadmin.logger import JsonlLogger
from vice_sysadmin.mocks import FakeClock, FakeHostCheckRunner, FakeRuntime, MockClient
from vice_sysadmin.orchestrator import run_task_repetition
from vice_sysadmin.reporter import aggregate, export_csv
from vice_sysadmin.run_experiment import build_matrix, select_tasks
from vice_sysadmin.schemas import ExecutionResult, load_config, load_task

ROOT = Path(__file__).resolve().parents[1]


def test_seeded_matrix_is_stable_and_complete():
    tasks = [load_task(path) for path in sorted((ROOT / "tasks").glob("*.yaml"))]
    config = load_config(ROOT / "config.yaml")
    first = build_matrix(tasks, config)
    assert first == build_matrix(list(reversed(tasks)), config)
    assert len(first) == len(tasks) * len(config.experiment.conditions) * config.experiment.repetitions
    assert len({(row["task_id"], row["condition"], row["repetition"]) for row in first}) == len(first)
    changed = config.model_copy(update={"experiment": config.experiment.model_copy(update={"seed": 42})})
    assert first != build_matrix(tasks, changed)


def test_task_selection_and_unknown_id():
    task = load_task(ROOT / "tasks/t1-2-restart-failed-service.yaml")
    assert select_tasks([task], [task.id]) == [task]
    with pytest.raises(ValueError):
        select_tasks([task], ["missing"])


def test_exclusive_log_refuses_existing_data(tmp_path):
    path = tmp_path / "run.jsonl"
    path.write_text("prior-data\n")
    with pytest.raises(FileExistsError):
        JsonlLogger.create_exclusive(path)
    assert path.read_text() == "prior-data\n"
    fresh = JsonlLogger.create_exclusive(tmp_path / "new.jsonl")
    assert fresh.path.exists()


def test_smoke_validates_and_unfilled_pilot_is_rejected():
    reference = ROOT / "tests/fixtures/configs_pre311"
    config = load_config(reference / "configs/smoke-lxd-mock.yaml")
    assert config.runtime.backend == "lxd" and config.proposer.provider == "mock"
    assert config.experiment.conditions == ["A"] and config.experiment.repetitions == 1
    assert config.experiment.task_ids == ["t1-2-restart-failed-service"]
    with pytest.raises(ValueError, match="placeholders"):
        load_config(reference / "configs/pilot-live.yaml")


def reporter_fixture():
    rows = [{"run_id": "run", "event_type": "run_manifest", "runtime_backend": "lxd"}]
    for rep, outcome, steps, tokens, cost, latency in [
        (1, "success", 2, 100, 0.1, 1.0),
        (2, "appropriate_escalation", 4, 300, 0.3, 3.0),
        (3, "failure", 6, 500, 0.5, 5.0),
    ]:
        context = dict(run_id="run", task_id="t", condition="B", repetition=rep,
                       task_category="ambiguous-instructions")
        rows.extend([
            context | dict(event_type="policy", policy_decision="reject", policy_rule="forbidden",
                           policy_flags=["high_risk:rm"]),
            context | dict(event_type="cleanup", cleanup_succeeded=rep != 3),
            context | dict(event_type="run_finished", outcome=outcome, steps=steps,
                           charged_tokens=tokens, charged_cost=cost, latency_seconds=latency,
                           termination_reason="escalated" if rep == 2 else "task_completed"),
        ])
    return rows


def test_reporter_math_and_no_ground_truth_metrics():
    rows = aggregate(reporter_fixture())
    assert {row["scope"] for row in rows} == {"condition", "category", "task"}
    row = next(row for row in rows if row["scope"] == "condition")
    assert row["completed"] == 3 and row["success_rate"] == pytest.approx(1 / 3)
    assert row["appropriate_escalation_rate"] == pytest.approx(1 / 3)
    assert row["failure_rate"] == pytest.approx(1 / 3)
    assert row["mean_steps"] == row["median_steps"] == 4
    assert row["mean_charged_tokens"] == row["median_charged_tokens"] == 300
    assert row["mean_charged_cost"] == pytest.approx(0.3)
    assert row["median_charged_cost"] == pytest.approx(0.3)
    assert row["mean_latency_seconds"] == 3.0 and row["cleanup_failure_count"] == 1
    assert json.loads(row["policy_blocks_by_rule"]) == {"forbidden": 3}
    assert json.loads(row["high_risk_flags_by_command"]) == {"rm": 3}
    assert row["reference_label_metrics"].startswith("NOT_COMPUTED")


def test_incomplete_repetition_is_not_fabricated_as_failure():
    events = reporter_fixture()
    events.append(dict(run_id="run", task_id="t", condition="B", repetition=4,
                       task_category="ambiguous-instructions", event_type="run_started"))
    row = next(row for row in aggregate(events) if row["scope"] == "condition")
    assert row["completed"] == 3 and row["incomplete"] == 1 and row["failure_count"] == 1


def test_csv_export_and_duplicate_input_rejected(tmp_path):
    source = tmp_path / "fixture.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in reporter_fixture()) + "\n")
    target = tmp_path / "report.csv"
    export_csv([source], target)
    assert "NOT_COMPUTED" in target.read_text()
    with pytest.raises(ValueError, match="Duplicate"):
        export_csv([source, source], tmp_path / "duplicate.csv")
    with pytest.raises(FileExistsError):
        export_csv([source], target)


def test_output_cap_ends_agent_work_and_final_metrics_are_logged(tmp_path):
    config = load_config(ROOT / "config.yaml")
    task = load_task(ROOT / "tasks/t1-2-restart-failed-service.yaml")
    clock = FakeClock()
    success = ExecutionResult(stdout="", stderr="", exit_code=0, timed_out=False,
                              stdout_truncated=False, stderr_truncated=False, latency_seconds=0.0)
    capped = success.model_copy(update={"exit_code": None, "stdout_truncated": True,
                                       "output_limit_reached": True})
    failed = success.model_copy(update={"exit_code": 1})
    runtime = FakeRuntime([success, capped, failed, failed], clock=clock)
    log = tmp_path / "cap.jsonl"
    result = run_task_repetition(task, "A", 1, config,
        MockClient(ROOT / "fixtures/proposer.json", clock=clock), None,
        runtime, FakeHostCheckRunner(clock=clock), JsonlLogger(log), clock, run_id="cap")
    assert result.termination_reason == "execution_output_limit" and result.cleanup_succeeded
    assert len([call for call in runtime.calls if call[0] == "execute"]) == 1
    final = json.loads(log.read_text().splitlines()[-1])
    assert final["steps"] == result.steps
    assert final["latency_seconds"] == result.latency_seconds
    assert final["task_category"] == task.category
