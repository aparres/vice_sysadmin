"""Offline regression coverage for one semantic repair per proposer step."""

import hashlib

import pytest

from test_orchestrator import Scenario, audit, complete, execution, proposal, response
from vice_sysadmin.mocks import FakeHandler
from vice_sysadmin.schemas import Config


def conflict():
    return proposal("id", question_for_operator="Which account?", task_completed=True)


def attempts(scenario):
    return [event for event in scenario.events if event["event_type"] == "proposer_attempt"]


@pytest.mark.parametrize("condition", ["A", "B", "C"])
def test_command_repair_runs_pipeline_in_same_step(tmp_path, condition):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.proposer_entries = [response(conflict()), response(proposal("whoami", task_completed=True))]
    result = scenario.run()
    assert result.steps == 1 and result.termination_reason == "task_completed"
    assert result.executed_commands == ["whoami"]
    assert len(scenario.executions()) == 1
    assert [event["attempt"] for event in attempts(scenario)] == [1, 2]
    assert {event["step"] for event in attempts(scenario)} == {1}
    names = [event["event_type"] for event in scenario.events]
    selected = ["proposal_blocked", "policy"]
    if condition != "A":
        selected += ["auditor_attempt", "audit"]
    selected += ["execute_requested", "executed", "verification"]
    assert [name for name in names if name in selected] == selected
    repair_prompt = scenario.proposer.calls[1][0]
    assert repair_prompt.startswith(scenario.proposer.calls[0][0])
    assert "cannot both be set" in repair_prompt and "previous_output" in repair_prompt
    assert attempts(scenario)[1]["prompt_hash"] == hashlib.sha256(repair_prompt.encode()).hexdigest()


@pytest.mark.parametrize("condition", ["A", "B", "C"])
@pytest.mark.parametrize("approved", [False, True])
def test_question_repair_uses_existing_escalation(tmp_path, condition, approved):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.configure("experiment", human_in_the_loop=True)
    scenario.handler = FakeHandler([approved])
    scenario.proposer_entries = [response(conflict()), response(proposal(
        "", question_for_operator="Which account?"))]
    if approved:
        scenario.proposer_entries.append(response(complete()))
    scenario.results = [execution()]
    result = scenario.run()
    assert result.escalated and not scenario.executions() and not scenario.auditor.calls
    assert result.steps == (2 if approved else 1)
    assert result.termination_reason == ("task_completed" if approved else "escalated")
    assert [event["attempt"] for event in attempts(scenario)] == ([1, 2, 1] if approved else [1, 2])
    assert not any(event["event_type"] == "policy" for event in scenario.events)


@pytest.mark.parametrize("condition", ["A", "B", "C"])
@pytest.mark.parametrize("bad,reason,blocked", [
    (response(conflict()), "conflicting_proposal", 2),
    (response("not JSON"), "proposer_failure", 1),
    (response({"command": "id"}), "proposer_failure", 1),
    ({"error": "transport failed"}, "proposer_failure", 1),
])
def test_failed_repair_never_retries_or_executes(tmp_path, condition, bad, reason, blocked):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.configure("proposer", retries=2)
    scenario.proposer_entries = [response(conflict()), bad, response(complete())]
    scenario.results = [execution(exit_code=1)]
    result = scenario.run()
    assert result.termination_reason == reason and result.steps == 1
    assert len(scenario.proposer.calls) == 2
    assert [event["attempt"] for event in attempts(scenario)] == [1, 2]
    assert not scenario.executions() and not scenario.auditor.calls
    assert sum(event["event_type"] == "proposal_blocked" for event in scenario.events) == blocked
    assert not any(event["event_type"] == "policy" for event in scenario.events)


@pytest.mark.parametrize("condition", ["A", "B", "C"])
def test_clean_proposal_has_no_extra_call(tmp_path, condition):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.proposer_entries = [response(proposal(task_completed=True))]
    result = scenario.run()
    assert result.executed_commands == ["id"] and result.steps == 1
    assert len(scenario.proposer.calls) == 1
    assert not any(event["event_type"] == "proposal_blocked" for event in scenario.events)


def test_parse_and_semantic_budgets_are_independent_and_charged(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.proposer_entries = [response("bad JSON"), response(conflict()),
                                 response(proposal(task_completed=True))]
    data = scenario.config.model_dump()
    for price in data["pricing"].values():
        price.update(input_per_million=1.0, output_per_million=2.0)
    scenario.config = Config.model_validate(data)
    result = scenario.run()
    assert result.executed_commands == ["id"] and result.steps == 1
    assert [event["attempt"] for event in attempts(scenario)] == [1, 2, 3]
    all_calls = [event for event in scenario.events if event["event_type"].endswith("_attempt")]
    assert len(all_calls) == 4  # Three proposer calls and one auditor call.
    assert result.total_tokens == 160 == sum(event["charged_tokens"] for event in all_calls)
    assert result.total_cost == pytest.approx(0.00024)
    assert result.total_cost == pytest.approx(sum(event["charged_cost"] for event in all_calls))


def test_repair_is_still_subject_to_policy_and_auditor(tmp_path):
    scenario = Scenario(tmp_path)
    scenario.configure("limits", max_steps=1)
    scenario.proposer_entries = [response(conflict()), response(proposal(task_completed=True))]
    scenario.auditor_entries = [response(audit("reject"), "auditor")]
    scenario.results = [execution(exit_code=1)]
    result = scenario.run()
    assert result.termination_reason == "step_limit" and not scenario.executions()
    assert any(event["event_type"] == "policy" for event in scenario.events)
    assert any(event["event_type"] == "audit" and event["verdict"] == "reject"
               for event in scenario.events)


def test_bc_repair_prompts_and_template_hashes_are_identical(tmp_path):
    runs = []
    for condition in ("B", "C"):
        directory = tmp_path / condition
        directory.mkdir()
        scenario = Scenario(directory)
        scenario.condition = condition
        scenario.proposer_entries = [response(conflict()), response(proposal(task_completed=True))]
        scenario.run()
        runs.append(scenario)
    assert [prompt.encode() for prompt, _ in runs[0].proposer.calls] == [
        prompt.encode() for prompt, _ in runs[1].proposer.calls]
    assert runs[0].result.prompt_hashes == runs[1].result.prompt_hashes
    assert "repair.v1.txt" in runs[0].result.prompt_hashes
