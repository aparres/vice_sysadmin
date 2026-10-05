"""Defensive LXD CLI adapter; importing this module performs no host operation."""

import ipaddress
import json
import math
import re
import shutil
from typing import Callable

from .interfaces import Clock
from .live_support import RealClock
from .processes import ProcessRunner
from .schemas import ExecutionRequest, ExecutionResult

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def validate_name(name: str, *, container: bool = True) -> str:
    """Same lexical pattern as Identifier, with the derived-container limit."""
    if not isinstance(name, str) or not NAME_PATTERN.fullmatch(name) or len(name) > (63 if container else 48):
        raise ValueError("Invalid LXD identifier")
    return name


class LxdRuntime:
    def __init__(self, transport: ProcessRunner | None = None, *,
                 clock: Clock | None = None, locator: Callable[[str], str | None] = shutil.which):
        self.binary = locator("lxc")
        if not self.binary:
            raise RuntimeError("lxc was not found on PATH; run on the validated LXD host")
        self.transport, self.clock = transport or ProcessRunner(), clock or RealClock()
        self._identities: dict[tuple[str, str], tuple[int, int]] = {}

    def _deadline(self, seconds: float) -> float:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Timeout must be positive and finite")
        return self.clock.monotonic() + seconds

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self.clock.monotonic()
        if remaining <= 0:
            raise TimeoutError("LXD operation deadline expired")
        return remaining

    def _call(self, args: list[str], deadline: float, cap: int = 1_048_576) -> ExecutionResult:
        return self.transport.run([self.binary, *args], self._remaining(deadline), cap)

    @staticmethod
    def _require(result: ExecutionResult, operation: str) -> None:
        if result.timed_out or result.output_limit_reached or result.exit_code != 0:
            # Do not echo full target output or scripts into exception messages.
            raise RuntimeError(f"LXD {operation} failed (exit={result.exit_code}, timeout={result.timed_out})")

    def _info(self, name: str, deadline: float) -> dict | None:
        validate_name(name)
        result = self._call(["list", name, "--format", "json"], deadline)
        self._require(result, "list")
        entries = json.loads(result.stdout)
        if not isinstance(entries, list):
            raise ValueError("Malformed lxc list response")
        return next((item for item in entries if isinstance(item, dict) and item.get("name") == name), None)

    def exists(self, name: str, *, timeout_seconds: float = 30.0) -> bool:
        validate_name(name)
        return self._info(name, self._deadline(timeout_seconds)) is not None

    def create(self, name: str, template: str, snapshot: str, profile: str, *,
               timeout_seconds: float = 30.0) -> None:
        validate_name(name)
        for value in (template, snapshot, profile):
            validate_name(value, container=False)
        deadline = self._deadline(timeout_seconds)
        if self._info(name, deadline) is not None:
            raise ValueError("Container already exists")
        copied = self._call(["copy", f"{template}/{snapshot}", name, "--profile", profile], deadline)
        self._require(copied, "copy")
        self._require(self._call(["start", name], deadline), "start")
        while True:
            ready = self._call(["exec", name, "--disable-stdin", "--force-noninteractive", "--", "true"], deadline)
            if ready.exit_code == 0 and not ready.timed_out:
                break
            self.clock.sleep(min(0.1, self._remaining(deadline)))
        # Exec readiness can precede systemd creating its private bus socket.
        while True:
            ready = self._call(["exec", name, "--disable-stdin", "--force-noninteractive",
                                "--", "test", "-S", "/run/systemd/private"], deadline)
            if ready.exit_code == 0 and not ready.timed_out:
                return
            self.clock.sleep(min(0.1, self._remaining(deadline)))

    def get_ip(self, name: str, *, timeout_seconds: float = 30.0) -> str:
        validate_name(name)
        deadline = self._deadline(timeout_seconds)
        while True:
            info = self._info(name, deadline)
            if info is None:
                raise ValueError("Unknown target container")
            state = info.get("state") or {}
            addresses = (state.get("network") or {}).get("eth0", {}).get("addresses", [])
            for item in addresses:
                if item.get("family") != "inet" or item.get("scope") != "global":
                    continue
                address = ipaddress.ip_address(item["address"])
                if address.version == 4 and not (address.is_link_local or address.is_loopback or address.is_unspecified or address.is_multicast):
                    return str(address)
            self.clock.sleep(min(0.1, self._remaining(deadline)))

    def destroy(self, name: str, *, timeout_seconds: float = 30.0) -> None:
        validate_name(name)
        deadline = self._deadline(timeout_seconds)
        info = self._info(name, deadline)
        if info is None:
            self._forget(name)
            return
        stopped = self._call(["stop", "--force", name], deadline)
        if stopped.exit_code != 0 or stopped.timed_out or stopped.output_limit_reached:
            info = self._info(name, deadline)
            if info is None:
                self._forget(name)
                return
            if str(info.get("status", "")).lower() != "stopped":
                self._require(stopped, "stop")
        deleted = self._call(["delete", name], deadline)
        if deleted.exit_code != 0 or deleted.timed_out or deleted.output_limit_reached:
            if self._info(name, deadline) is not None:
                self._require(deleted, "delete")
        self._forget(name)

    def _forget(self, name: str) -> None:
        self._identities = {key: value for key, value in self._identities.items() if key[0] != name}

    def _identity(self, name: str, user: str, deadline: float) -> tuple[int, int]:
        key = (name, user)
        if key not in self._identities:
            ids = []
            for flag in ("-u", "-g"):
                result = self._call(["exec", name, "--disable-stdin", "--force-noninteractive", "--", "id", flag, user], deadline)
                self._require(result, "identity lookup")
                text = result.stdout.strip()
                if not text.isascii() or not text.isdecimal() or int(text) == 0:
                    raise ValueError("Executor identity must have nonzero numeric UID and GID")
                ids.append(int(text))
            self._identities[key] = (ids[0], ids[1])
        return self._identities[key]

    def _execute(self, name: str, command: str, cwd: str, identity: tuple[int, int] | None,
                 deadline: float, cap: int) -> ExecutionResult:
        started = self.clock.monotonic()
        remaining = self._remaining(deadline)
        if remaining < 0.001:
            raise TimeoutError("Insufficient time to start a bounded remote command")
        # Reserve time for the host backstop and a force-stop of the target.
        inner_timeout, host_timeout = remaining * 0.8, remaining * 0.9
        args = ["exec", name, "--disable-stdin", "--force-noninteractive"]
        if identity is not None:
            args += ["--user", str(identity[0]), "--group", str(identity[1])]
        args += ["--env", f"HOME={cwd}", "--cwd", cwd, "--",
                 "timeout", "--signal=KILL", f"{inner_timeout:.6f}s", "bash", "-c", command]
        result = self.transport.run([self.binary, *args], host_timeout, cap)
        timed_out = result.timed_out or result.exit_code in {124, 137}
        if timed_out or result.output_limit_reached:
            # An lxc client kill cannot prove remote termination. Stop the entire
            # disposable target, ending this repetition, rather than leave work
            # running. If the stop cannot be confirmed, raise and let finally
            # attempt destroy under the separate cleanup allowance.
            self._require(self._call(["stop", "--force", name], deadline), "emergency target stop")
        return result.model_copy(update={
            "timed_out": timed_out,
            "exit_code": None if timed_out or result.output_limit_reached else result.exit_code,
            "latency_seconds": max(result.latency_seconds, self.clock.monotonic() - started),
        })

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        # Revalidate even if a caller used model_construct/model_copy unsafely.
        request = ExecutionRequest.model_validate(request.model_dump())
        validate_name(request.container_name)
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", request.user):
            raise ValueError("Invalid executor user")
        if "\x00" in request.command or "\x00" in request.working_directory:
            raise ValueError("NUL is forbidden in execution arguments")
        deadline = self._deadline(request.timeout_seconds)
        identity = None if request.elevate else self._identity(request.container_name, request.user, deadline)
        return self._execute(request.container_name, request.command, request.working_directory,
                             identity, deadline, request.output_byte_cap)

    def run_harness_script(self, name: str, script: str, *, purpose: str,
                           timeout_seconds: float, output_byte_cap: int) -> ExecutionResult:
        validate_name(name)
        if purpose not in {"setup", "verification", "snapshot"}:
            raise ValueError("Invalid harness purpose")
        if "\x00" in script or output_byte_cap <= 0:
            raise ValueError("Invalid harness script or output cap")
        # Invariant: caller supplies trusted setup/check/config scripts only,
        # never a script derived from agent output. Harness always runs as root.
        return self._execute(name, script, "/root", None,
                             self._deadline(timeout_seconds), output_byte_cap)
