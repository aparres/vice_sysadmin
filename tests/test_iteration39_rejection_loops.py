"""Repeated-rejection nudges; scripted offline scenarios only."""

from datetime import datetime
import json

import pytest

from test_orchestrator import Scenario, audit, complete, execution, proposal, response
from test_iteration38_auditor_feedback import observations
from vice_sysadmin import orchestrator
from vice_sysadmin.schemas import Event, PolicyDecision

STATUS = "Command repeated without change"


def warnings(history):
    return [item for item in history if item.get("status") == STATUS]


def scripted(tmp_path, monkeypatch, sources, commands=None, condition="B"):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    commands = commands or ["id"] * len(sources)
    decisions = iter(sources)

    def policy(command, settings):
        source = next(decisions)
        return PolicyDecision(decision="reject" if source == "policy" else "approve",
                              rule="fixture" if source == "policy" else None,
                              reason="Policy reason" if source == "policy" else "Allowed")

    monkeypatch.setattr(orchestrator, "evaluate", policy)
    scenario.proposer_entries = [response(proposal(command)) for command in commands] + [response(complete())]
    scenario.auditor_entries = [response(audit("reject" if source == "auditor" else "approve"), "auditor")
                                for source in sources if source != "policy"]
    scenario.results = [execution() for source in sources if source == "approve"] + [execution()]
    return scenario


@pytest.mark.parametrize("sources,condition", [
    (["policy"] * 3, "A"), (["policy"] * 3, "B"),
    (["auditor"] * 3, "B"), (["auditor"] * 3, "C"),
    (["policy", "auditor", "auditor"], "B"),
])
def test_third_rejection_adds_nudge_without_replacing_feedback(tmp_path, monkeypatch, observations, sources, condition):
    scenario = scripted(tmp_path, monkeypatch, sources, condition=condition)
    result = scenario.run()
    assert orchestrator.REPEATED_REJECTION_THRESHOLD == 3
    assert all(not warnings(history) for history in observations[:3])
    assert warnings(observations[3]) == [{"status": STATUS, "command": "id", "count": 3}]
    normal = [{"status": "Command blocked by policy", "reason": "Policy reason"}
              if source == "policy" else
              {"status": "Command rejected by auditor", "reason": "reviewed"}
              for source in sources]
    assert [item for item in observations[3] if item.get("status") != STATUS] == normal
    assert not result.escalated and not scenario.executions()
    # All existing policy/audit event fields retain the prior byte representation.
    start = scenario.events[0]
    context = {name: start[name] for name in (
        "run_id", "task_id", "condition", "repetition", "timestamp", "container_name",
        "config_hash", "adjudication_version", "task_category")}
    context["timestamp"] = datetime.fromisoformat(context["timestamp"].replace("Z", "+00:00"))
    expected = []
    for step, source in enumerate(sources, 1):
        fields = dict(command="id", policy_decision="reject" if source == "policy" else "approve",
                      policy_rule="fixture" if source == "policy" else None,
                      policy_reason="Policy reason" if source == "policy" else "Allowed", policy_flags=[])
        records = [Event(**context, step=step, event_type="policy", **fields)]
        if source == "auditor":
            records.append(Event(**context, step=step, event_type="audit", command="id", verdict="reject"))
        expected.extend((json.dumps(record.model_dump(mode="json"), ensure_ascii=True,
                                    allow_nan=False) + "\n").encode() for record in records)
    actual = [line for line in scenario.log.read_bytes().splitlines(keepends=True)
              if json.loads(line)["event_type"] in {"policy", "audit", "escalation"}]
    assert actual == expected


@pytest.mark.parametrize("commands", [["id", "id", "whoami"], ["id", "id", "id "]])
def test_any_command_difference_resets_streak(tmp_path, monkeypatch, observations, commands):
    scenario = scripted(tmp_path, monkeypatch, ["auditor"] * 3, commands)
    scenario.run()
    assert not any(warnings(history) for history in observations)


def test_approval_resets_even_if_command_does_not_change(tmp_path, monkeypatch, observations):
    scenario = scripted(tmp_path, monkeypatch, ["auditor", "auditor", "approve", "auditor"])
    assert scenario.run().executed_commands == ["id"]
    assert not any(warnings(history) for history in observations)


def test_nudge_is_next_request_only_and_rearms_after_three(tmp_path, monkeypatch, observations):
    scenario = scripted(tmp_path, monkeypatch, ["auditor"] * 7)
    scenario.run()
    assert [index for index, history in enumerate(observations) if warnings(history)] == [3, 6]
    assert all(item["count"] == 3 for history in observations for item in warnings(history))


def test_revision_after_nudge_executes_without_escalation(tmp_path, monkeypatch, observations):
    scenario = scripted(tmp_path, monkeypatch, ["auditor"] * 3 + ["approve"], ["id"] * 3 + ["whoami"])
    result = scenario.run()
    assert warnings(observations[3])
    assert not warnings(observations[4])
    assert result.executed_commands == ["whoami"] and result.steps == 5
    assert result.termination_reason == "task_completed" and not result.escalated


def test_budget_is_not_extended_to_deliver_nudge(tmp_path, monkeypatch, observations):
    scenario = scripted(tmp_path, monkeypatch, ["auditor"] * 3)
    scenario.configure("limits", max_steps=3)
    result = scenario.run()
    assert result.termination_reason == "step_limit" and len(scenario.proposer.calls) == 3
    assert not result.escalated


def test_existing_question_path_remains_model_driven(tmp_path, monkeypatch, observations):
    scenario = scripted(tmp_path, monkeypatch, ["auditor"] * 3)
    scenario.proposer_entries[-1] = response(proposal("", question_for_operator="Which scope?"))
    result = scenario.run()
    assert warnings(observations[3]) and result.termination_reason == "escalated"
    assert len([event for event in scenario.events if event["event_type"] == "escalation"]) == 1


def test_repair_attempts_do_not_count_as_policy_or_auditor_rejections(tmp_path, monkeypatch, observations):
    scenario = scripted(tmp_path, monkeypatch, ["auditor"] * 3)
    scenario.proposer_entries.insert(2, response(proposal("id", question_for_operator="Conflicting question?")))
    scenario.run()
    assert not any(warnings(history) for history in observations)
