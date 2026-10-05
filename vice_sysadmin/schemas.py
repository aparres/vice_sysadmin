"""Strict data contracts; benchmark task fields are preserved unchanged."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


Risk = Annotated[int, Field(ge=0, le=10)]
PositiveInt = Annotated[int, Field(gt=0)]
PositiveFloat = Annotated[float, Field(gt=0, allow_inf_nan=False)]
NonnegativeFloat = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9-]*$", max_length=48)]


class Script(Contract):
    script: str


CheckName = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_]*$", max_length=64)]

# Values the harness substitutes at run time, and only in host-side checks.
HOST_PLACEHOLDERS = frozenset({"CONTAINER_IP", "CONTAINER_NAME"})
_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z0-9_]*)\s*\}\}")


class Check(Contract):
    """One verification step. It passes if and only if its script exits 0.

    ``run_on="container"`` executes inside the task container through the
    harness authority. ``run_on="host"`` executes on the control-plane host
    (for example, probing a port from outside the container). Host scripts are
    trusted harness content; they must never be derived from agent output.
    """

    name: CheckName
    run_on: Literal["container", "host"]
    script: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def placeholders_only_on_host(self) -> Check:
        found = set(_PLACEHOLDER.findall(self.script))
        if self.run_on == "container" and found:
            raise ValueError("Container checks must not use placeholders")
        unknown = found - HOST_PLACEHOLDERS
        if unknown:
            raise ValueError(f"Unknown placeholders: {sorted(unknown)}")
        if self.run_on == "host" and "{{" in _PLACEHOLDER.sub("", self.script):
            raise ValueError("Malformed placeholder in host check")
        return self


class Verification(Contract):
    """All checks must pass for the task to count as verified.

    ``success_if`` is human-readable documentation; only the checks decide.
    """

    success_if: str
    checks: Annotated[list[Check], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_check_names(self) -> Verification:
        names = [check.name for check in self.checks]
        if len(names) != len(set(names)):
            raise ValueError("Check names must be unique within a task")
        return self


class Task(Contract):
    id: str
    category: str
    risk: Risk
    requires_sudo: bool
    requires_network: bool
    prompt: str
    setup: Script
    verification: Verification
    contains_prompt_injection: bool
    notes: str = ""


class Proposal(Contract):
    command: str
    reasoning: str
    estimated_risk: Risk
    task_completed: bool
    needs_elevation: bool = False
    question_for_operator: str | None = None

    @model_validator(mode="after")
    def command_required_when_active(self) -> Proposal:
        if (not self.task_completed and not self.command.strip()
                and not (self.question_for_operator or "").strip()):
            raise ValueError("An active proposal requires a command")
        return self


class Audit(Contract):
    verdict: Literal["approve", "reject", "escalate"]
    risk: Risk
    risk_category: str
    justification: str


class Outcome(str, Enum):
    SUCCESS = "success"
    APPROPRIATE_ESCALATION = "appropriate_escalation"
    FAILURE = "failure"


class ModelSettings(Contract):
    provider: Literal["openai", "google", "mock"]
    model: Annotated[str, Field(min_length=1)]
    temperature: Annotated[float, Field(ge=0, le=2, allow_inf_nan=False)]
    top_p: Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
    max_tokens: PositiveInt
    timeout_seconds: PositiveFloat
    retries: Annotated[int, Field(ge=0)]
    invalid_json_retries: Literal[1] = 1
    api_key_env: Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9_]*$")] | None = None
    input_token_overhead: Annotated[int, Field(ge=0)] = 256
    output_format: Literal["auto", "text", "json", "json_schema"] = "auto"
    mock_fixture: str | None = None


class Prices(Contract):
    input_per_million: NonnegativeFloat
    output_per_million: NonnegativeFloat


class Limits(Contract):
    max_steps: PositiveInt
    max_tokens: PositiveInt
    cost_cap: PositiveFloat
    total_time_seconds: PositiveFloat
    command_timeout_seconds: PositiveFloat
    output_byte_cap: PositiveInt
    cleanup_timeout_seconds: PositiveFloat = 10.0


class PolicySettings(Contract):
    enabled: bool
    reject_command_chaining: bool
    forbidden_commands: list[str]
    high_risk_commands: list[str]
    forbidden_patterns: list[str]
    read_only_commands: list[str] = Field(default_factory=lambda: [
        "id", "whoami", "pwd", "ls", "cat", "head", "tail", "grep", "ss",
        "stat", "wc", "df", "du", "ps", "uname", "systemctl", "journalctl",
    ])


class RuntimeSettings(Contract):
    backend: Literal["fake", "lxd"]
    template: Identifier
    snapshot: Literal["base"]
    profile: Literal["vicesysadmin"]
    storage_pool: Literal["default"]
    bridge: Literal["lxdbr0"]
    user: Annotated[str, Field(pattern=r"^[a-z_][a-z0-9_-]*$")]
    working_directory: Annotated[str, Field(pattern=r"^/")]

    @model_validator(mode="after")
    def unprivileged_default(self) -> RuntimeSettings:
        if self.user == "root":
            raise ValueError("The default executor user must be unprivileged")
        return self


class ExperimentSettings(Contract):
    conditions: Annotated[list[Literal["A", "B", "C"]], Field(min_length=1)]
    repetitions: PositiveInt
    seed: int
    proposer_variant: Literal[
        "baseline", "verify_nudge", "verify_nudge_syntax", "verify_nudge_syntax_v2",
        "verify_nudge_syntax_v3",
    ] = "baseline"
    auditor_mode: Literal["blind", "informed"]
    human_in_the_loop: bool
    state_snapshot_commands: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_conditions(self) -> ExperimentSettings:
        if len(self.conditions) != len(set(self.conditions)):
            raise ValueError("Conditions must be unique")
        return self


class Config(Contract):
    schema_version: Literal[1]
    proposer: ModelSettings
    homogeneous_auditor: ModelSettings
    heterogeneous_auditor: ModelSettings
    runtime: RuntimeSettings
    limits: Limits
    policy: PolicySettings
    experiment: ExperimentSettings
    pricing: dict[str, Prices]
    prompt_version: str

    @model_validator(mode="after")
    def provider_conditions(self) -> Config:
        if self.proposer.provider == "mock":
            if any(m.provider != "mock" for m in (
                self.homogeneous_auditor, self.heterogeneous_auditor
            )):
                raise ValueError("Offline configuration must use all mock clients")
        else:
            if self.proposer.provider != "openai":
                raise ValueError("The proposer must use OpenAI")
            if self.homogeneous_auditor.provider != "openai":
                raise ValueError("Condition B requires an OpenAI auditor")
            if self.heterogeneous_auditor.provider != "google":
                raise ValueError("Condition C requires a Google auditor")
        return self


def load_config(path: Path) -> Config:
    """Read configuration with a YAML loader that cannot instantiate objects."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    def reject_placeholders(value: object) -> None:
        if isinstance(value, str) and ("REPLACE_" in value or "<SET_" in value):
            raise ValueError("Configuration contains operator placeholders")
        if isinstance(value, dict):
            for key, item in value.items():
                reject_placeholders(key)
                reject_placeholders(item)
        if isinstance(value, list):
            for item in value:
                reject_placeholders(item)
    reject_placeholders(data)
    return Config.model_validate(data)


def load_task(path: Path) -> Task:
    return Task.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def config_hash(config: Config) -> str:
    """Hash canonical validated config, including defaults, excluding credentials."""
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True,
                           separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LLMResponse(Contract):
    raw_response: str
    model_returned: str | None
    model_version_returned: str | None = None
    input_tokens: Annotated[int, Field(ge=0)] | None
    output_tokens: Annotated[int, Field(ge=0)] | None
    latency_seconds: NonnegativeFloat
    estimated_cost: NonnegativeFloat | None


class ExecutionRequest(Contract):
    container_name: str
    command: Annotated[str, Field(min_length=1)]
    user: str
    working_directory: Annotated[str, Field(pattern=r"^/")]
    elevate: bool = False
    task_requires_sudo: bool = False
    timeout_seconds: PositiveFloat
    output_byte_cap: PositiveInt

    @model_validator(mode="after")
    def explicit_elevation(self) -> ExecutionRequest:
        if self.user in ("root", "0", ""):
            raise ValueError("Execution must identify an unprivileged user")
        if self.elevate and not self.task_requires_sudo:
            raise ValueError("Elevation requires task authorization")
        return self


class ExecutionResult(Contract):
    stdout: str
    stderr: str
    exit_code: int | None
    stdout_truncated: bool
    stderr_truncated: bool
    timed_out: bool
    latency_seconds: NonnegativeFloat
    output_limit_reached: bool = False


class Event(Contract):
    """Every JSONL record has the same keys; inapplicable values are null."""

    run_id: str
    task_id: str
    condition: Literal["A", "B", "C"]
    repetition: PositiveInt
    step: Annotated[int, Field(ge=0)]
    timestamp: datetime
    container_name: str
    event_type: str
    config_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    prompt_sent: str | None = None
    raw_response: str | None = None
    model_returned: str | None = None
    model_version_returned: str | None = None
    temperature: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_seconds: float | None = None
    estimated_cost: float | None = None
    verdict: Literal["approve", "reject", "escalate"] | None = None
    policy_decision: Literal["disabled", "approve", "reject", "escalate"] | None = None
    command: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    sudo_elevation: bool = False
    outcome: Outcome | None = None
    seed: int | None = None
    error: str | None = None
    attempt: PositiveInt | None = None
    policy_flags: list[str] | None = None
    policy_rule: str | None = None
    policy_reason: str | None = None
    check_name: str | None = None
    check_passed: bool | None = None
    termination_reason: str | None = None
    adjudication_version: str | None = None
    prompt_hash: str | None = None
    prompt_hashes: dict[str, str] | None = None
    charged_tokens: Annotated[int, Field(ge=0)] | None = None
    charged_cost: NonnegativeFloat | None = None
    timed_out: bool | None = None
    cleanup_succeeded: bool | None = None
    output_limit_reached: bool = False
    task_category: str | None = None
    steps: Annotated[int, Field(ge=0)] | None = None
    run_order: list[dict[str, str | int]] | None = None
    python_version: str | None = None
    git_commit: str | None = None
    runtime_backend: Literal["fake", "lxd"] | None = None

    @model_validator(mode="after")
    def coherent_context(self) -> Event:
        expected = container_name(self.task_id, self.repetition)
        if self.container_name != expected:
            raise ValueError(f"Expected container name {expected}")
        if self.timestamp.utcoffset() is None:
            raise ValueError("Event timestamp must include a timezone")
        return self


def container_name(task_id: str, repetition: int) -> str:
    """Keep task IDs unchanged, validating the derived name at the boundary."""
    import re

    name = f"task-{task_id}-rep-{repetition}"
    if repetition < 1 or len(name) > 63 or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name):
        raise ValueError("Task ID/repetition cannot form a safe LXD container name")
    return name


class PolicyDecision(Contract):
    decision: Literal["approve", "reject", "escalate", "disabled"]
    rule: str | None = None
    reason: str
    flags: list[str] = Field(default_factory=list)


class CheckResult(Contract):
    name: str
    run_on: Literal["container", "host"]
    passed: bool
    execution: ExecutionResult | None = None
    error: str | None = None


class EscalationRequest(Contract):
    task_id: str
    command: str
    reason: str
    source: Literal["proposer", "auditor", "policy"]


class TaskRunResult(Contract):
    outcome: Outcome
    steps: Annotated[int, Field(ge=0)]
    escalated: bool
    final_message: str
    checks: dict[str, CheckResult]
    total_tokens: Annotated[int, Field(ge=0)]
    total_cost: NonnegativeFloat
    latency_seconds: NonnegativeFloat
    termination_reason: str
    cleanup_succeeded: bool
    cleanup_error: str | None = None
    adjudication_version: str
    prompt_hashes: dict[str, str]
    executed_commands: list[str]
