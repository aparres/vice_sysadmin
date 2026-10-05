"""Policy feedback regressions using only the existing offline scenarios."""

from copy import deepcopy
from datetime import datetime
import json

import pytest

from test_orchestrator import Scenario, audit, complete, execution, proposal, response
from vice_sysadmin import orchestrator
from vice_sysadmin.mocks import FakeHandler
from vice_sysadmin.policy import evaluate
from vice_sysadmin.prompts import PromptBook
from vice_sysadmin.schemas import Event, PolicyDecision


@pytest.fixture
def observations(monkeypatch):
    captured = []
    original = PromptBook.proposer

    def record(self, task_prompt, requires_sudo, values, *, variant="baseline"):
        captured.append(deepcopy(values))
        return original(self, task_prompt, requires_sudo, values, variant=variant)

    monkeypatch.setattr(PromptBook, "proposer", record)
    return captured


@pytest.mark.parametrize("condition", ["A", "B", "C"])
@pytest.mark.parametrize("command,patterns,reason", [
    ("apt-get update && apt-get install nginx", [], "Disallowed shell composition"),
    ("reboot", [], "Forbidden executable"),
    ("id", ["^(?:id)$"], "Forbidden pattern matched"),
])
def test_rejection_feedback_and_policy_jsonl(tmp_path, observations, condition, command, patterns, reason):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.configure("policy", forbidden_patterns=patterns, high_risk_commands=["reboot", "id"])
    scenario.proposer_entries = [response(proposal(command)), response(complete())]
    scenario.results = [execution()]
    decision = evaluate(command, scenario.config.policy)
    result = scenario.run()
    assert observations == [[], [{"status": "Command blocked by policy", "reason": reason}]]
    assert not scenario.executions() and not scenario.auditor.calls
    assert result.steps == 2
    feedback = json.dumps(observations)
    assert "high_risk:" not in feedback and decision.rule not in feedback
    for pattern in patterns:
        assert pattern not in feedback
        assert pattern not in scenario.proposer.calls[1][0]
    assert reason in scenario.proposer.calls[1][0]
    # Reconstruct the complete pre-patch event contract, not just selected fields.
    started = scenario.events[0]
    context = {key: started[key] for key in (
        "run_id", "task_id", "condition", "repetition", "timestamp", "container_name",
        "config_hash", "adjudication_version", "task_category",
    )}
    context["timestamp"] = datetime.fromisoformat(context["timestamp"].replace("Z", "+00:00"))
    expected = Event(**context, step=1, event_type="policy", command=command,
                     policy_decision=decision.decision, policy_rule=decision.rule,
                     policy_reason=decision.reason, policy_flags=decision.flags)
    expected_bytes = (json.dumps(expected.model_dump(mode="json"), ensure_ascii=True,
                                allow_nan=False) + "\n").encode()
    actual = [line for line in scenario.log.read_bytes().splitlines(keepends=True)
              if json.loads(line)["event_type"] == "policy"]
    assert actual == [expected_bytes]


@pytest.mark.parametrize("missing", ["", None])
def test_missing_reason_keeps_placeholder(tmp_path, monkeypatch, observations, missing):
    decision = PolicyDecision.model_construct(decision="reject", rule="test", reason=missing, flags=[])
    monkeypatch.setattr(orchestrator, "evaluate", lambda *_: decision)
    scenario = Scenario(tmp_path)
    scenario.proposer_entries = [response(proposal()), response(complete())]
    scenario.results = [execution()]
    scenario.run()
    assert observations[1] == [{"status": "Command blocked by policy"}]


def test_invalid_regex_diagnostic_does_not_leak_into_observations(tmp_path, observations):
    scenario = Scenario(tmp_path)
    # Python's regex error includes the unknown group name from this raw pattern.
    pattern = "(?P=PRIVATE_POLICY_MARKER)"
    scenario.configure("policy", forbidden_patterns=[pattern])
    scenario.proposer_entries = [response(proposal()), response(complete())]
    scenario.results = [execution()]
    scenario.run()
    assert observations[1] == [{"status": "Command blocked by policy"}]
    assert "PRIVATE_POLICY_MARKER" not in scenario.proposer.calls[1][0]
    policy = next(event for event in scenario.events if event["event_type"] == "policy")
    assert policy["policy_rule"] == "parse_error"
    assert "PRIVATE_POLICY_MARKER" in policy["policy_reason"]


@pytest.mark.parametrize("approved", [False, True])
def test_policy_escalation_still_forwards_reason(tmp_path, monkeypatch, observations, approved):
    # evaluate currently emits no escalate branch; inject its supported decision.
    decision = PolicyDecision(decision="escalate", rule="test", reason="Operator approval required")
    monkeypatch.setattr(orchestrator, "evaluate", lambda *_: decision)
    scenario = Scenario(tmp_path)
    scenario.condition = "A"
    scenario.configure("experiment", human_in_the_loop=True)
    scenario.handler = FakeHandler([approved])
    result = scenario.run()
    assert result.escalated
    assert len(scenario.handler.calls) == 1
    request = scenario.handler.calls[0]
    assert request.source == "policy" and request.command == "id"
    assert request.reason == decision.reason
    assert len(scenario.executions()) == int(approved)
    assert result.termination_reason == ("task_completed" if approved else "escalated")
    assert all(item.get("status") != "Command blocked by policy"
               for snapshot in observations for item in snapshot)


def test_approve_observation_is_unchanged(tmp_path, observations):
    scenario = Scenario(tmp_path)
    result = scenario.run()
    assert result.executed_commands == ["id"]
    assert observations == [[], [{"command": "id", "result": execution().model_dump()}]]


@pytest.mark.parametrize("condition", ["A", "B", "C"])
def test_scripted_proposer_splits_rejected_command(tmp_path, observations, condition):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    scenario.task_update(requires_sudo=True)
    scenario.proposer_entries = [
        response(proposal("apt-get update && apt-get install nginx", needs_elevation=True)),
        response(proposal("apt-get update", needs_elevation=True)),
        response(proposal("apt-get install nginx", needs_elevation=True, task_completed=True)),
    ]
    scenario.auditor_entries = [response(audit(), "auditor"), response(audit(), "auditor")]
    scenario.results = [execution(), execution(), execution()]
    result = scenario.run()
    assert observations[1] == [{"status": "Command blocked by policy",
                                "reason": "Disallowed shell composition"}]
    assert "Disallowed shell composition" in scenario.proposer.calls[1][0]
    assert result.executed_commands == ["apt-get update", "apt-get install nginx"]
    assert result.steps == 3 and result.termination_reason == "task_completed"
    assert not result.escalated and result.checks["check"].passed
    assert len(scenario.auditor.calls) == (0 if condition == "A" else 2)
    assert [event["policy_decision"] for event in scenario.events
            if event["event_type"] == "policy"] == ["reject", "approve", "approve"]
