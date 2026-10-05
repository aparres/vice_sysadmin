"""Completion claims must not bypass review, execution, or observation."""

import hashlib
import json

import pytest

from test_orchestrator import ROOT, Scenario, audit, complete, execution, proposal, response
from vice_sysadmin.mocks import FakeHandler
from vice_sysadmin import run_experiment


@pytest.mark.parametrize("condition", ["A", "B", "C"])
@pytest.mark.parametrize("completed", [False, True])
def test_nonempty_completion_runs_full_pipeline(tmp_path, condition, completed):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.proposer_entries = [response(proposal(task_completed=completed))]
    if not completed:
        scenario.proposer_entries.append(response(complete()))
    result = scenario.run()
    assert result.executed_commands == ["id"]
    assert len(scenario.executions()) == 1
    assert result.steps == (1 if completed else 2)
    assert len(scenario.proposer.calls) == result.steps
    assert result.termination_reason == "task_completed"
    names = [event["event_type"] for event in scenario.events]
    pipeline = ["policy"]
    if condition != "A":
        pipeline += ["auditor_attempt", "audit"]
    pipeline += ["execute_requested", "executed", "verification"]
    assert [name for name in names if name in pipeline] == pipeline
    assert len(scenario.auditor.calls) == (0 if condition == "A" else 1)
    if not completed:
        assert "public output" in scenario.proposer.calls[1][0]


@pytest.mark.parametrize("condition", ["A", "B", "C"])
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("source", ["policy", "auditor", "elevation"])
def test_rejection_overrides_completion(tmp_path, condition, completed, source):
    if condition == "A" and source == "auditor":
        pytest.skip("Condition A has no auditor")
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.configure("limits", max_steps=1)
    scenario.proposer_entries = [response(proposal(
        "reboot" if source == "policy" else "id",
        task_completed=completed, needs_elevation=source == "elevation"))]
    scenario.auditor_entries = [response(audit("reject"), "auditor")]
    scenario.results = [execution(exit_code=1)]
    result = scenario.run()
    assert result.termination_reason == "step_limit"
    assert not scenario.executions()
    assert len(scenario.auditor.calls) == int(source == "auditor")
    assert not any(event["event_type"] in {"execute_requested", "executed"}
                   for event in scenario.events)
    if source == "policy":
        assert any(event["policy_decision"] == "reject" for event in scenario.events)
    elif source == "auditor":
        assert any(event["event_type"] == "audit" and event["verdict"] == "reject"
                   for event in scenario.events)
    else:
        assert any(event["event_type"] == "elevation_rejected" for event in scenario.events)


@pytest.mark.parametrize("condition", ["A", "B", "C"])
def test_empty_completion_skips_pipeline(tmp_path, condition):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.proposer_entries = [response(complete())]
    scenario.results = [execution()]
    result = scenario.run()
    assert result.steps == 1 and result.termination_reason == "task_completed"
    assert not scenario.executions() and not scenario.auditor.calls
    names = [event["event_type"] for event in scenario.events]
    assert "verification" in names
    assert not set(names) & {"policy", "auditor_attempt", "audit", "execute_requested", "executed"}


@pytest.mark.parametrize("condition", ["A", "B", "C"])
@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("approved", [False, True])
def test_empty_question_preserves_escalation_precedence(tmp_path, condition, completed, approved):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.configure("experiment", human_in_the_loop=True)
    scenario.handler = FakeHandler([approved])
    scenario.proposer_entries = [response(proposal(
        "", task_completed=completed, question_for_operator="Proceed?"))]
    if approved:
        scenario.proposer_entries.append(response(complete()))
    scenario.results = [execution()]
    result = scenario.run()
    assert result.escalated
    assert result.steps == (2 if approved else 1)
    assert result.termination_reason == ("task_completed" if approved else "escalated")
    assert not scenario.executions() and not scenario.auditor.calls
    names = [event["event_type"] for event in scenario.events]
    assert "escalation" in names and "verification" in names
    assert not set(names) & {"policy", "auditor_attempt", "audit", "execute_requested", "executed"}


@pytest.mark.parametrize("flag,reason", [
    ("timed_out", "execution_timeout"),
    ("output_limit_reached", "execution_output_limit"),
])
def test_execution_limits_override_completion(tmp_path, flag, reason):
    scenario = Scenario(tmp_path)
    scenario.proposer_entries = [response(proposal(task_completed=True))]
    scenario.results = [execution(**{flag: True, "exit_code": None}), execution(exit_code=1)]
    result = scenario.run()
    assert result.termination_reason == reason
    assert result.executed_commands == ["id"]


def test_bc_prompts_are_identical_and_log_current_file_hash(tmp_path):
    scenarios = []
    expected = hashlib.sha256((ROOT / "prompts/proposer.v1.txt").read_bytes()).hexdigest()
    for condition in ("B", "C"):
        directory = tmp_path / condition
        directory.mkdir()
        scenario = Scenario(directory)
        scenario.condition = condition
        scenario.proposer_entries = [response(proposal(task_completed=True))]
        result = scenario.run()
        assert result.prompt_hashes["proposer.v1.txt"] == expected
        assert scenario.events[0]["prompt_hashes"]["proposer.v1.txt"] == expected
        scenarios.append(scenario)
    assert scenarios[0].proposer.calls[0][0].encode() == scenarios[1].proposer.calls[0][0].encode()
    assert scenarios[0].auditor.calls[0][0].encode() == scenarios[1].auditor.calls[0][0].encode()


def test_run_manifest_records_updated_proposer_hash(tmp_path, monkeypatch):
    scenario = Scenario(tmp_path)
    scenario.configure("experiment", conditions=["B", "C"], repetitions=1)
    for role in ("proposer", "homogeneous_auditor", "heterogeneous_auditor"):
        scenario.configure(role, provider="mock", mock_fixture=None)
    # Prevent even the optional Git subprocess; all clients/runtime remain fakes.
    monkeypatch.setattr(run_experiment, "get_git_commit", lambda: None)
    log = run_experiment.execute_experiment(
        scenario.config, [scenario.task], runtime_backend="fake", handler_name="deny",
        run_id="completion-hash", log_directory=tmp_path / "logs", config_directory=ROOT)
    events = [json.loads(line) for line in log.read_text().splitlines()]
    expected = hashlib.sha256((ROOT / "prompts/proposer.v1.txt").read_bytes()).hexdigest()
    assert events[0]["event_type"] == "run_manifest"
    assert events[0]["prompt_hashes"]["proposer.v1.txt"] == expected
    starts = [event for event in events if event["event_type"] == "run_started"]
    assert {event["condition"] for event in starts} == {"B", "C"}
    assert all(event["prompt_hashes"] == events[0]["prompt_hashes"] for event in starts)
