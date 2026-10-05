"""Durable batch boundaries, explicit resume, and provider-failure streaks."""

from contextlib import contextmanager
import hashlib
import json
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path

from .schemas import Config, Event, Task, config_hash

COUNTED_REASONS = frozenset({"proposer_failure", "audit_failure", "execution_timeout", "setup_failed"})


class BatchPausedEvent(Event):
    """Batch-only fields; existing repetition event bytes are unaffected."""
    triggering_repetitions: list[dict[str, str | int]]
    next_repetition: dict[str, str | int] | None
    checkpoint_path: str


class BatchPaused(RuntimeError):
    """Controlled exit after persisting a safe repetition boundary."""
    def __init__(self, path):
        self.path = Path(path)
        super().__init__("Batch paused; explicit resume required")


def identity(item):
    return item["task_id"], item["condition"], item["repetition"]


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(raw.encode()).hexdigest()


@contextmanager
def checkpoint_lock(path):
    """OS-owned lock released on process exit; stale files do not lock a run."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            stream.seek(0, os.SEEK_END)
            if stream.tell() == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ValueError("Batch is already running") from exc
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ValueError("Batch is already running") from exc
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


class Checkpoint:
    def __init__(self, path, data):
        self.path, self.data = Path(path), data
        self.streak = []

    @classmethod
    def create(cls, path, *, config, tasks, matrix, prompt_hashes, run_id,
               log_path, config_directory, handler_name):
        data = dict(version=1, run_id=run_id, log_path=str(log_path.resolve()),
                    config=config.model_dump(mode="json"),
                    tasks=[task.model_dump(mode="json") for task in tasks],
                    config_directory=str(config_directory.resolve()), handler_name=handler_name,
                    run_order=matrix, prompt_hashes=prompt_hashes.copy(), completed=[],
                    next_index=0, status="running")
        data["inputs_hash"] = digest({key: data[key] for key in (
            "config", "tasks", "run_order", "prompt_hashes", "handler_name", "config_directory")})
        # Reserve exclusively; never replace an earlier checkpoint.
        with Path(path).open("x", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=True, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        return cls(path, data)

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        required = {"version", "run_id", "log_path", "config", "tasks", "config_directory",
                    "handler_name", "run_order", "prompt_hashes", "completed", "next_index",
                    "status", "inputs_hash"}
        if not isinstance(data, dict) or set(data) != required or data["version"] != 1:
            raise ValueError("Invalid batch checkpoint")
        if data["status"] not in {"running", "paused", "complete"}:
            raise ValueError("Invalid checkpoint status")
        if data["inputs_hash"] != digest({key: data[key] for key in (
                "config", "tasks", "run_order", "prompt_hashes", "handler_name", "config_directory")}):
            raise ValueError("Checkpoint input hash mismatch")
        if (type(data["next_index"]) is not int or not isinstance(data["completed"], list)
                or data["next_index"] != len(data["completed"])):
            raise ValueError("Invalid checkpoint progress")
        if not Path(data["log_path"]).is_absolute() or not Path(data["config_directory"]).is_absolute():
            raise ValueError("Checkpoint paths must be absolute")
        return cls(path, data)

    def save(self):
        temporary = self.path.with_name(self.path.name + ".tmp-" + uuid.uuid4().hex)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(self.data, stream, ensure_ascii=True, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            # Windows scanners can briefly hold a closed destination file.
            # Retry only the atomic file replacement, never a repetition/API call.
            attempts = 25 if os.name == "nt" else 1
            for attempt in range(attempts):
                try:
                    os.replace(temporary, self.path)
                    break
                except PermissionError:
                    if attempt == attempts - 1:
                        raise
                    time.sleep(0.02)
            if os.name != "nt":
                descriptor = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        finally:
            temporary.unlink(missing_ok=True)

    def validate_resume(self, config, tasks, matrix, prompt_hashes):
        data = self.data
        if (config_hash(config) != config_hash(Config.model_validate(data["config"]))
                or [task.model_dump(mode="json") for task in tasks] != data["tasks"]
                or matrix != data["run_order"] or prompt_hashes != data["prompt_hashes"]):
            raise ValueError("Resume inputs differ from checkpoint")
        completed, active = [], None
        index, kind = -1, None
        with Path(data["log_path"]).open("rb") as stream:
            for index, line in enumerate(stream):
                if not line.endswith(b"\n"):
                    raise ValueError("Incomplete log line; resume refused")
                event = json.loads(line)
                # Validate existing events and the additive batch event separately.
                model = BatchPausedEvent if event.get("event_type") == "batch_paused" else Event
                model.model_validate_json(line)
                if (event["run_id"] != data["run_id"] or event["config_hash"] != config_hash(config)
                        or event["runtime_backend"] not in (None, config.runtime.backend)):
                    raise ValueError("Log context differs from checkpoint")
                kind = event["event_type"]
                if index == 0:
                    if (kind != "run_manifest" or event["run_order"] != matrix
                            or event["prompt_hashes"] != prompt_hashes):
                        raise ValueError("Manifest differs from checkpoint")
                elif kind == "run_manifest":
                    raise ValueError("Duplicate manifest")
                if kind == "run_started":
                    if active is not None or len(completed) >= len(matrix):
                        raise ValueError("Invalid repetition sequence")
                    active = identity(event)
                    if active != identity(matrix[len(completed)]) or event["prompt_hashes"] != prompt_hashes:
                        raise ValueError("Repetition order or prompts differ")
                elif kind == "run_finished":
                    if active != identity(event) or event["cleanup_succeeded"] is not True:
                        raise ValueError("Missing start or unsafe cleanup")
                    completed.append({**matrix[len(completed)], "termination_reason": event["termination_reason"]})
                    active = None
                elif kind == "runner_failed":
                    raise ValueError("Runner failure requires manual inspection")
                elif kind not in {"run_manifest", "batch_paused"}:
                    if active != identity(event):
                        raise ValueError("Event is outside active repetition")
        if index < 0:
            raise ValueError("Missing manifest")
        if active is not None:
            raise ValueError("Started repetition has no completion; resume refused")
        saved = data["completed"]
        if len(saved) > len(completed) or completed[:len(saved)] != saved:
            raise ValueError("Checkpoint progress differs from log")
        # Recover only fully logged, cleaned-up closes if a crash preceded save.
        data["completed"] = completed
        data["next_index"] = len(completed)
        data["status"] = "complete" if len(completed) == len(matrix) else "running"
        self.streak = []
        self.save()

    def close(self, item, reason):
        data = self.data
        if data["next_index"] >= len(data["run_order"]) or item != data["run_order"][data["next_index"]]:
            raise ValueError("Checkpoint close is out of order")
        closed = {**item, "termination_reason": reason}
        data["completed"].append(closed)
        data["next_index"] += 1
        if reason in COUNTED_REASONS:
            self.streak.append(closed)
        else:
            self.streak = []
        paused = len(self.streak) == 2
        data["status"] = "paused" if paused else (
            "complete" if data["next_index"] == len(data["run_order"]) else "running")
        self.save()
        return paused

    @property
    def next_item(self):
        index = self.data["next_index"]
        return self.data["run_order"][index] if index < len(self.data["run_order"]) else None

    def message(self):
        lines = ["BATCH PAUSED: 2 consecutive infrastructure/provider failure reasons."]
        for item in self.streak:
            lines.append(f"  {item['task_id']}/{item['condition']}/rep-{item['repetition']}: {item['termination_reason']}")
        item = self.next_item
        lines.append("Next: " + (f"{item['task_id']}/{item['condition']}/rep-{item['repetition']}" if item else "none; all repetitions completed"))
        path = str(self.path.resolve())
        argument = subprocess.list2cmdline([path]) if os.name == "nt" else shlex.quote(path)
        lines.append("Resume: python -m vice_sysadmin.run_experiment --resume " + argument)
        return "\n".join(lines)
