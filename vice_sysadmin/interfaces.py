"""Boundaries for future provider clients and the host-owned container harness."""

from typing import Protocol, runtime_checkable

from .schemas import (
    EscalationRequest, Event, ExecutionRequest, ExecutionResult, LLMResponse, ModelSettings,
)


class ClientError(RuntimeError):
    """Provider unavailable, timed out, or returned unusable data; never approve."""


@runtime_checkable
class LLMClient(Protocol):
    def generate(self, prompt: str, settings: ModelSettings) -> LLMResponse:
        """Return raw output and usage; raise ClientError on transport failure.

        Clients must enforce settings.timeout_seconds and perform ONE attempt.
        The orchestrator owns transport retries; it passes retries=0 to clients.
        The caller validates structured JSON and allows exactly one repair retry.
        No client response itself authorizes execution.
        """
        ...


@runtime_checkable
class ContainerRuntime(Protocol):
    def exists(self, name: str, *, timeout_seconds: float = 30.0) -> bool: ...

    def create(self, name: str, template: str, snapshot: str, profile: str, *,
               timeout_seconds: float = 30.0) -> None:
        """Clone a template snapshot; reject an existing destination."""
        ...

    def destroy(self, name: str, *, timeout_seconds: float = 30.0) -> None:
        """Stop and remove a target; an absent target is an idempotent no-op."""
        ...

    def get_ip(self, name: str, *, timeout_seconds: float = 30.0) -> str:
        """Return only the target IP address; honor the operation timeout."""
        ...

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        """Execute only inside the named target, with bounded capture and timeout.

        Future implementations use subprocess list arguments, never shell=True
        on the host. Timeout must stop the target process and descendants.
        output_byte_cap is a combined stdout/stderr UTF-8 budget.
        """
        ...


    def run_harness_script(self, name: str, script: str, *,
                           purpose: str, timeout_seconds: float,
                           output_byte_cap: int) -> ExecutionResult:
        """Run trusted setup, snapshot, or verification; never agent input."""
        ...


@runtime_checkable
class Clock(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


@runtime_checkable
class HostCheckRunner(Protocol):
    def run(self, script: str, container_name: str, timeout_seconds: float,
            output_byte_cap: int) -> ExecutionResult:
        """Trusted harness scripts only, never agent-derived scripts.

        A future implementation uses subprocess list arguments (bash -c script),
        never shell=True, and terminates descendants on timeout.
        """
        ...


@runtime_checkable
class EscalationHandler(Protocol):
    def approve(self, request: EscalationRequest, *, timeout_seconds: float) -> bool:
        """Only literal True authorizes continuation; honor the remaining deadline."""
        ...


class EventLogger(Protocol):
    def write(self, event: Event) -> None: ...
