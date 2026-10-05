"""Real host clock, trusted host probes, and supervised console approvals."""

import json
import select
import sys
import time

from .processes import ProcessRunner
from .schemas import EscalationRequest, ExecutionResult


class RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class RealHostCheckRunner:
    def __init__(self, transport: ProcessRunner | None = None):
        self.transport = transport or ProcessRunner()

    def run(self, script: str, container_name: str, timeout_seconds: float,
            output_byte_cap: int) -> ExecutionResult:
        # Invariant: script is trusted verification YAML with harness-substituted
        # placeholders. It must NEVER be built from agent commands or output.
        return self.transport.run(["bash", "-c", script], timeout_seconds, output_byte_cap)


class ConsoleApprovalHandler:
    def approve(self, request: EscalationRequest, *, timeout_seconds: float) -> bool:
        print(json.dumps(request.model_dump(), ensure_ascii=True))
        print("Approve continuation? Type yes or no: ", end="", flush=True)
        if timeout_seconds <= 0 or not sys.stdin.isatty():
            return False
        ready, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
        return bool(ready) and sys.stdin.readline().strip().lower() == "yes"
