"""Deterministic test doubles. This module never starts a process or network call."""

from collections import deque
from pathlib import Path
import json

from .interfaces import ClientError, Clock
from .schemas import (
    EscalationRequest, ExecutionRequest, ExecutionResult, LLMResponse, ModelSettings,
)


class FakeClock:
    """Monotonic logical time; sleeping never blocks the host."""

    def __init__(self, start: float = 0.0):
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Time cannot move backwards")
        self._now += seconds

    def advance(self, seconds: float) -> None:
        self.sleep(seconds)


def bound_result(result: ExecutionResult, timeout: float, cap: int,
                 clock: Clock | None = None) -> ExecutionResult:
    """Apply the shared fake timeout and combined UTF-8 byte budget."""
    if timeout <= 0 or cap <= 0:
        raise ValueError("Timeout and output cap must be positive")
    out, err = result.stdout.encode("utf-8"), result.stderr.encode("utf-8")
    remaining = max(0, cap - len(out))
    elapsed = min(result.latency_seconds, timeout)
    if clock is not None:
        clock.sleep(elapsed)
    timed_out = result.timed_out or result.latency_seconds > timeout
    return result.model_copy(update={
        "stdout": out[:cap].decode("utf-8", errors="ignore"),
        "stderr": err[:remaining].decode("utf-8", errors="ignore"),
        "stdout_truncated": result.stdout_truncated or len(out) > cap,
        "stderr_truncated": result.stderr_truncated or len(err) > remaining,
        "timed_out": timed_out,
        "exit_code": None if timed_out else result.exit_code,
        "latency_seconds": elapsed,
    })


class MockClient:
    """Consume fixture entries in order, including explicitly scripted failures."""

    def __init__(self, fixture_path: Path, *, clock: Clock | None = None):
        entries = json.loads(fixture_path.read_text(encoding="utf-8"))
        if not isinstance(entries, list):
            raise ValueError("A client fixture must contain a JSON array")
        self._entries = deque(entries)
        self.calls: list[tuple[str, ModelSettings]] = []
        self.clock = clock

    def generate(self, prompt: str, settings: ModelSettings) -> LLMResponse:
        self.calls.append((prompt, settings))
        if not self._entries:
            raise ClientError("Mock response fixture exhausted")
        entry = self._entries.popleft()
        delay = float(entry.get("delay_seconds", entry.get("latency_seconds", 0.0)))
        if delay < 0:
            raise ValueError("Fixture delay cannot be negative")
        if self.clock is not None:
            self.clock.sleep(min(delay, settings.timeout_seconds))
        if delay > settings.timeout_seconds:
            raise ClientError("Simulated provider timeout")
        if "error" in entry:
            raise ClientError(str(entry["error"]))
        return LLMResponse.model_validate({k: v for k, v in entry.items()
                                           if k != "delay_seconds"})


class FakeRuntime:
    """Record lifecycle and execution calls; return only queued results.

    This fake is not a security sandbox or a shell emulator. It cannot verify
    real permissions, network isolation, process termination, or elapsed time.
    """

    def __init__(self, results: list[ExecutionResult | Exception] | None = None, *,
                 clock: Clock | None = None, ip: str = "10.18.28.10"):
        self.containers: set[str] = set()
        self.calls: list[tuple] = []
        self._results = deque(results or [])
        self.clock = clock
        self.ip = ip

    def exists(self, name: str, *, timeout_seconds: float = 30.0) -> bool:
        self.calls.append(("exists", name))
        return name in self.containers

    def create(self, name: str, template: str, snapshot: str, profile: str, *,
               timeout_seconds: float = 30.0) -> None:
        if name in self.containers:
            raise ValueError("Container already exists")
        self.calls.append(("create", name, template, snapshot, profile))
        self.containers.add(name)

    def destroy(self, name: str, *, timeout_seconds: float = 30.0) -> None:
        self.calls.append(("destroy", name))
        self.containers.discard(name)

    def get_ip(self, name: str, *, timeout_seconds: float = 30.0) -> str:
        if name not in self.containers:
            raise ValueError("Unknown target container")
        self.calls.append(("get_ip", name))
        return self.ip

    def _consume(self, name: str, timeout: float, cap: int) -> ExecutionResult:
        if name not in self.containers:
            raise ValueError("Unknown target container")
        if timeout <= 0 or cap <= 0:
            raise ValueError("Timeout and output cap must be positive")
        if not self._results:
            raise RuntimeError("Fake execution results exhausted")
        result = self._results.popleft()
        if isinstance(result, Exception):
            raise result
        return bound_result(result, timeout, cap, self.clock)

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls.append(("execute", request))
        return self._consume(request.container_name, request.timeout_seconds,
                             request.output_byte_cap)

    def run_harness_script(self, name: str, script: str, *, purpose: str,
                           timeout_seconds: float,
                           output_byte_cap: int) -> ExecutionResult:
        if purpose not in ("setup", "verification", "snapshot"):
            raise ValueError("Unknown harness purpose")
        self.calls.append(("harness", name, purpose, script))
        return self._consume(name, timeout_seconds, output_byte_cap)


class FakeHostCheckRunner:
    def __init__(self, results: list[ExecutionResult | Exception] | None = None, *,
                 clock: Clock | None = None):
        self._results = deque(results or [])
        self.clock = clock
        self.calls: list[tuple[str, str, float, int]] = []

    def run(self, script: str, container_name: str, timeout_seconds: float,
            output_byte_cap: int) -> ExecutionResult:
        self.calls.append((script, container_name, timeout_seconds, output_byte_cap))
        if not self._results:
            raise RuntimeError("Fake host results exhausted")
        result = self._results.popleft()
        if isinstance(result, Exception):
            raise result
        return bound_result(result, timeout_seconds, output_byte_cap, self.clock)


class DenyAllHandler:
    def approve(self, request: EscalationRequest, *, timeout_seconds: float) -> bool:
        return False


class FakeHandler:
    def __init__(self, approvals: list[bool] | None = None):
        self._approvals = deque(approvals or [])
        self.calls: list[EscalationRequest] = []

    def approve(self, request: EscalationRequest, *, timeout_seconds: float) -> bool:
        self.calls.append(request)
        return bool(self._approvals) and self._approvals.popleft() is True
