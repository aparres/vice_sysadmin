"""Static proposer guidance and existing v1 rendering; no live operations."""

import hashlib
import json

import pytest

from test_orchestrator import ROOT, Scenario, complete, execution, response
from vice_sysadmin.prompts import PromptBook
from vice_sysadmin.schemas import Proposal

GUIDANCE = "When an observation reports that a command was blocked by policy or rejected by\nthe auditor, use the reason to revise it rather than repeat the same command\nunchanged. If the reason indicates shell composition or chaining, split the\ncommand into separate single-purpose commands and propose only the next one this\nstep. Use question_for_operator only when the task itself is ambiguous, not\nmerely because a command was blocked or rejected.\nRepeating an identical rejected command is not progress. If the reason says the\ncommand is incomplete, premature, or does not advance the task, propose a\nsubstantively different command addressing the missing prerequisite or action,\nnot a rephrasing or a resend. If the harness reports \"Command repeated without\nchange\", stop and reconsider the approach for this step; use question_for_operator\nonly if genuine task ambiguity makes the needed change unclear.\n"


@pytest.mark.parametrize("requires_sudo", [False, True])
def test_guidance_renders_with_all_existing_placeholders(requires_sudo):
    book = PromptBook(nonce_factory=lambda: "guidance_test")
    task = "Inspect the requested state"
    observations = [{"status": "Command blocked by policy", "reason": "Example reason"}]
    rendered = book.proposer(task, requires_sudo, observations)
    assert GUIDANCE in rendered
    assert json.dumps(Proposal.model_json_schema(), sort_keys=True) in rendered
    assert "Task permits elevation:\n" + json.dumps(requires_sudo) in rendered
    assert json.dumps(task, ensure_ascii=True) in rendered
    assert json.dumps(observations, ensure_ascii=True) in rendered
    assert rendered.count("<<<UNTRUSTED_guidance_test>>>") == 2
    assert rendered.count("<<<END_UNTRUSTED_guidance_test>>>") == 2
    for name in ("schema", "requires_sudo", "task", "observations"):
        assert "{{" + name + "}}" not in rendered


@pytest.mark.parametrize("condition", ["A", "B", "C"])
def test_existing_v1_gate_accepts_edited_template(tmp_path, condition):
    scenario = Scenario(tmp_path)
    scenario.condition = condition
    assert scenario.config.prompt_version == "v1"
    scenario.proposer_entries = [response(complete())]
    scenario.results = [execution()]
    result = scenario.run()
    assert result.termination_reason == "task_completed"
    assert GUIDANCE in scenario.proposer.calls[0][0]
    expected = hashlib.sha256((ROOT / "prompts/proposer.v1.txt").read_bytes()).hexdigest()
    assert result.prompt_hashes["proposer.v1.txt"] == expected
    assert scenario.events[0]["prompt_hashes"]["proposer.v1.txt"] == expected
