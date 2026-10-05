"""Linux subprocess transport with bounded concurrent stdout/stderr capture."""

from dataclasses import dataclass, field
import math
import os
import selectors
import signal
import subprocess
import time
from typing import BinaryIO

from .schemas import ExecutionResult


@dataclass
class OutputBudget:
    limit: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    reached: bool = False

    def feed(self, stream: str, chunk: bytes) -> bool:
        """Stop at the cap, conservatively flagging the stream that reached it."""
        if self.limit <= 0 or stream not in {"stdout", "stderr"}:
            raise ValueError("Invalid output budget or stream")
        target = self.stdout if stream == "stdout" else self.stderr
        remaining = self.limit - len(self.stdout) - len(self.stderr)
        target.extend(chunk[:remaining])
        if chunk and len(chunk) >= remaining:
            self.reached = True
            setattr(self, stream + "_truncated", True)
        return self.reached


def capture_stream(stream: BinaryIO, cap: int) -> OutputBudget:
    """In-memory seam for verifying incremental byte-budget behavior."""
    budget = OutputBudget(cap)
    while chunk := stream.read(min(8192, max(1, cap))):
        if budget.feed("stdout", chunk):
            break
    return budget


class ProcessRunner:
    """List arguments only. Popen is necessary for a live combined output cap."""

    def run(self, argv: list[str], timeout_seconds: float, output_byte_cap: int) -> ExecutionResult:
        if not argv or timeout_seconds <= 0 or not math.isfinite(timeout_seconds) or output_byte_cap <= 0:
            raise ValueError("Invalid process bounds")
        started = time.monotonic()
        deadline = started + timeout_seconds
        output = OutputBudget(output_byte_cap)
        timed_out = False
        process = subprocess.Popen(
            argv, shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        try:
            with selectors.DefaultSelector() as selector:
                for stream, label in ((process.stdout, "stdout"), (process.stderr, "stderr")):
                    assert stream is not None
                    os.set_blocking(stream.fileno(), False)
                    selector.register(stream, selectors.EVENT_READ, label)
                while selector.get_map():
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    for key, _ in selector.select(min(remaining, 0.1)):
                        chunk = os.read(key.fileobj.fileno(), min(8192, output_byte_cap))
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif output.feed(key.data, chunk):
                            break
                    if output.reached:
                        break
                if not timed_out and not output.reached:
                    try:
                        process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    except subprocess.TimeoutExpired:
                        timed_out = True
        finally:
            # Kill the whole local session even if the shell exited but children
            # remain. Remote container processes need LxdRuntime's separate stop.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Local process could not be reaped within cleanup allowance") from None
        return ExecutionResult(
            stdout=bytes(output.stdout).decode("utf-8", errors="ignore"),
            stderr=bytes(output.stderr).decode("utf-8", errors="ignore"),
            exit_code=None if timed_out or output.reached else process.returncode,
            stdout_truncated=output.stdout_truncated, stderr_truncated=output.stderr_truncated,
            timed_out=timed_out, output_limit_reached=output.reached,
            latency_seconds=max(0.0, time.monotonic() - started),
        )
