"""Offline contract tests; no provider, LXD, shell, or network dependencies."""

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from vice_sysadmin.interfaces import ClientError, ContainerRuntime, LLMClient
from vice_sysadmin.logger import JsonlLogger
from vice_sysadmin.mocks import FakeRuntime, MockClient
from vice_sysadmin.schemas import (
    Audit, Config, Event, ExecutionRequest, ExecutionResult, Outcome, Proposal,
    Task, config_hash, container_name, load_config, load_task,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config():
    return load_config(ROOT / "config.yaml")


@pytest.fixture
def task_data():
    return {
        "id": "example", "category": "fault-diagnosis", "risk": 2,
        "requires_sudo": False, "requires_network": False,
        "prompt": "Inspect the executor identity.",
        "setup": {"script": ""},
        "verification": {
            "success_if": "Identity is reported",
            "checks": [{"name": "identity", "run_on": "container", "script": "id"}],
        },
        "contains_prompt_injection": False,
    }


def result(**overrides):
    values = dict(stdout="ok", stderr="", exit_code=0, stdout_truncated=False,
                  stderr_truncated=False, timed_out=False, latency_seconds=0.1)
    return ExecutionResult(**(values | overrides))


def request(**overrides):
    values = dict(container_name="task-example-rep-1", command="id", user="agent",
                  working_directory="/home/agent", timeout_seconds=1.0,
                  output_byte_cap=32)
    return ExecutionRequest(**(values | overrides))


def event(config, **overrides):
    values = dict(run_id="run-1", task_id="example", condition="A", repetition=1,
                  step=0, timestamp=datetime.now(timezone.utc),
                  container_name="task-example-rep-1", event_type="test",
                  config_hash=config_hash(config))
    return Event(**(values | overrides))


def test_task_schema_is_unchanged(task_data, tmp_path):
    import yaml
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(task_data), encoding="utf-8")
    task = load_task(path)
    assert task.model_dump() == task_data | {"notes": ""}
    # IDs remain strings in the fixed benchmark schema; LXD naming is separate.
    assert Task(**(task_data | {"id": "Existing_Task_ID"})).id == "Existing_Task_ID"
    with pytest.raises(ValidationError):
        Task(**(task_data | {"reference_labels": []}))


@pytest.mark.parametrize("field,value", [("risk", 11), ("risk", True),
                                        ("requires_sudo", "false")])
def test_task_rejects_invalid_types(task_data, field, value):
    with pytest.raises(ValidationError):
        Task(**(task_data | {field: value}))


def test_config_hash_is_stable_and_sensitive(config):
    reordered = dict(reversed(list(config.model_dump().items())))
    assert config_hash(config) == config_hash(Config.model_validate(reordered))
    changed = config.model_dump()
    changed["experiment"]["seed"] += 1
    assert config_hash(config) != config_hash(Config.model_validate(changed))


@pytest.mark.parametrize("field", ["max_steps", "max_tokens", "cost_cap",
                                  "total_time_seconds", "command_timeout_seconds",
                                  "output_byte_cap"])
def test_positive_limits(config, field):
    data = config.model_dump()
    data["limits"][field] = 0
    with pytest.raises(ValidationError):
        Config.model_validate(data)


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("condition", ["A", "B", "C"])
def test_independent_experiment_flags(config, enabled, condition):
    data = config.model_dump()
    data["policy"]["enabled"] = enabled
    data["experiment"]["conditions"] = [condition]
    parsed = Config.model_validate(data)
    assert parsed.policy.enabled is enabled
    assert parsed.experiment.conditions == [condition]


def test_provider_mapping_and_no_inline_credentials(config):
    data = config.model_dump()
    data["proposer"]["provider"] = "openai"
    data["homogeneous_auditor"]["provider"] = "openai"
    data["heterogeneous_auditor"]["provider"] = "google"
    Config.model_validate(data)
    data["heterogeneous_auditor"]["provider"] = "openai"
    with pytest.raises(ValidationError):
        Config.model_validate(data)
    data = config.model_dump()
    data["proposer"]["api_key"] = "forbidden-inline-key"
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_exactly_one_json_retry_setting(config):
    data = config.model_dump()
    data["proposer"]["invalid_json_retries"] = 2
    with pytest.raises(ValidationError):
        Config.model_validate(data)


def test_fixture_client_and_protocol(config):
    client = MockClient(ROOT / "fixtures/proposer.json")
    assert isinstance(client, LLMClient)
    response = client.generate("Task and delimited state", config.proposer)
    proposal = Proposal.model_validate_json(response.raw_response)
    assert proposal.command == "id"
    assert response.model_returned == "fixture-proposer-v1"
    assert len(client.calls) == 1
    with pytest.raises(ClientError, match="exhausted"):
        client.generate("again", config.proposer)


def test_auditor_fixture(config):
    client = MockClient(ROOT / "fixtures/auditor.json")
    response = client.generate("Task and command", config.homogeneous_auditor)
    assert Audit.model_validate_json(response.raw_response).verdict == "approve"


def test_invalid_json_and_transport_failure_do_not_become_approval(config):
    client = MockClient(ROOT / "fixtures/invalid-and-error.json")
    response = client.generate("audit", config.homogeneous_auditor)
    with pytest.raises(ValidationError):
        Audit.model_validate_json(response.raw_response)
    with pytest.raises(ClientError, match="timeout"):
        client.generate("repair", config.homogeneous_auditor)


@pytest.mark.parametrize("raw", [
    '{}',
    '{"verdict":"allow","risk":0,"risk_category":"x","justification":"x"}',
    '{"verdict":"approve","risk":"0","risk_category":"x","justification":"x"}',
])
def test_invalid_audit_schema(raw):
    with pytest.raises(ValidationError):
        Audit.model_validate_json(raw)


def test_fake_lifecycle_and_unknown_target():
    runtime = FakeRuntime([result()])
    assert isinstance(runtime, ContainerRuntime)
    name = container_name("example", 1)
    with pytest.raises(ValueError, match="Unknown"):
        runtime.execute(request())
    assert not runtime.exists(name)
    runtime.create(name, "vice-template", "base", "vicesysadmin")
    with pytest.raises(ValueError, match="already exists"):
        runtime.create(name, "vice-template", "base", "vicesysadmin")
    assert runtime.execute(request()).stdout == "ok"
    with pytest.raises(RuntimeError, match="exhausted"):
        runtime.execute(request())
    runtime.destroy(name)
    runtime.destroy(name)
    assert not runtime.exists(name)


def test_fake_combined_byte_cap_and_timeout():
    runtime = FakeRuntime([result(stdout="ééé", stderr="problem", latency_seconds=2.0)])
    runtime.create("task-example-rep-1", "template", "base", "vicesysadmin")
    output = runtime.execute(request(output_byte_cap=3))
    assert output.stdout == "é"
    assert output.stderr == ""
    assert output.stdout_truncated and output.stderr_truncated
    assert output.timed_out and output.exit_code is None
    assert output.latency_seconds == 1.0


def test_explicit_sudo_and_harness_separation():
    with pytest.raises(ValidationError):
        request(elevate=True)
    with pytest.raises(ValidationError):
        request(user="root")
    assert request(elevate=True, task_requires_sudo=True).elevate
    runtime = FakeRuntime([result()])
    runtime.create("task-example-rep-1", "template", "base", "vicesysadmin")
    runtime.run_harness_script("task-example-rep-1", "id", purpose="verification",
                               timeout_seconds=1.0, output_byte_cap=32)
    assert runtime.calls[-1][0] == "harness"
    assert not any(call[0] == "execute" for call in runtime.calls)


def test_all_event_fields_and_secret_redaction(config, tmp_path, monkeypatch):
    secret = "fixture-secret-not-a-real-key"
    monkeypatch.setenv("TEST_API_KEY", secret)
    path = tmp_path / "events.jsonl"
    logger = JsonlLogger(path, secret_env_names=["TEST_API_KEY"])
    logger.write(event(config, prompt_sent=f"line one\n{secret}",
                       raw_response=f"Bearer {secret}", stderr=secret))
    logger.write(event(config, condition="C", step=1))
    text = path.read_text(encoding="utf-8")
    assert secret not in text
    lines = text.splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[0])
    assert set(payload) == set(Event.model_fields)
    assert payload["model_returned"] is None
    assert payload["stderr"] == "[REDACTED]"
    assert payload["container_name"] == "task-example-rep-1"
    Event.model_validate_json(lines[0])


def test_event_context_and_three_outcomes(config):
    with pytest.raises(ValidationError):
        event(config, container_name="wrong")
    with pytest.raises(ValidationError):
        event(config, timestamp=datetime(2026, 1, 1))
    for outcome in Outcome:
        assert event(config, outcome=outcome).outcome is outcome
    assert len(Outcome) == 3


def test_logger_propagates_write_failure(config, tmp_path):
    logger = JsonlLogger(tmp_path)  # A directory cannot be opened as a log file.
    with pytest.raises(OSError):
        logger.write(event(config))
