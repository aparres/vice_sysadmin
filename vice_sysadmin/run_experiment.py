"""Sequential experiment CLI. Nothing runs merely by importing this module."""

import argparse
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import random
import re
import subprocess
import sys
import tempfile
from typing import Iterator
import uuid

from .adjudication import ADJUDICATION_VERSION
from .batch import BatchPaused, BatchPausedEvent, Checkpoint, checkpoint_lock
from .clients import GoogleClient, OpenAIClient
from .live_support import ConsoleApprovalHandler, RealClock, RealHostCheckRunner
from .logger import JsonlLogger
from .lxd_runtime import LxdRuntime
from .mocks import DenyAllHandler, FakeClock, FakeHostCheckRunner, FakeRuntime, MockClient
from .orchestrator import run_task_repetition
from .prompts import PromptBook
from .schemas import (
    Audit, Config, Event, ExecutionResult, ModelSettings, Outcome, Proposal, Task,
    config_hash, container_name, load_config, load_task,
)

ROOT = Path(__file__).resolve().parents[1]


def build_matrix(tasks: list[Task], config: Config) -> list[dict[str, str | int]]:
    """Pure, stable-input seeded shuffle with no process or file access."""
    if not tasks or len({task.id for task in tasks}) != len(tasks):
        raise ValueError("Tasks must be nonempty and have unique IDs")
    matrix = [dict(task_id=task.id, category=task.category, condition=condition, repetition=rep)
              for task in sorted(tasks, key=lambda item: item.id)
              for condition in config.experiment.conditions
              for rep in range(1, config.experiment.repetitions + 1)]
    random.Random(config.experiment.seed).shuffle(matrix)
    return matrix


def select_tasks(tasks: list[Task], ids: list[str]) -> list[Task]:
    if not ids:
        return tasks
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate requested task ID")
    available = {task.id for task in tasks}
    if set(ids) - available:
        raise ValueError("Requested task ID is not in the catalog")
    return [task for task in tasks if task.id in ids]


def get_git_commit() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, timeout=1.0, check=False, shell=False)
        commit = result.stdout.strip()
        return commit if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", commit) else None
    except (OSError, subprocess.SubprocessError):
        return None


@contextmanager
def single_host_lock() -> Iterator[None]:
    """Prevent overlapping runs by this CLI; manual lxc activity remains external."""
    import fcntl
    target = Path(tempfile.gettempdir()) / "vice-sysadmin-lxd.lock"
    fd = os.open(target, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def make_client(settings: ModelSettings, schema: type[Proposal] | type[Audit],
                config_directory: Path, clock):
    if settings.provider == "openai":
        return OpenAIClient(schema)
    if settings.provider == "google":
        return GoogleClient(schema)
    fixture = (config_directory / settings.mock_fixture if settings.mock_fixture else
               ROOT / "fixtures" / ("runner-proposer.json" if schema is Proposal else "auditor.json"))
    return MockClient(fixture.resolve(), clock=clock)


def fake_success() -> ExecutionResult:
    return ExecutionResult(stdout="SIMULATED fixture output; no script executed", stderr="",
                           exit_code=0, stdout_truncated=False, stderr_truncated=False,
                           timed_out=False, latency_seconds=0.0)


def _execute_experiment(config: Config, tasks: list[Task], *, runtime_backend: str,
                       handler_name: str, run_id: str, log_directory: Path,
                       config_directory: Path, checkpoint: Checkpoint | None = None) -> Path:
    if runtime_backend not in {"fake", "lxd"} or handler_name not in {"deny", "console"}:
        raise ValueError("Unknown runtime or handler")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("Unsafe run ID")
    # Hash/log the effective backend, including the CLI override.
    data = config.model_dump()
    data["runtime"]["backend"] = runtime_backend
    config = Config.model_validate(data)
    matrix = build_matrix(tasks, config)
    for item in matrix:
        container_name(item["task_id"], item["repetition"])
    prompts = PromptBook(proposer_variant=config.experiment.proposer_variant)
    if config.prompt_version != "v1":
        raise ValueError("Only v1 templates are available")
    names = sorted({settings.api_key_env for settings in (
        config.proposer, config.homogeneous_auditor, config.heterogeneous_auditor
    ) if settings.api_key_env})
    log_path = log_directory / f"{run_id}.jsonl"
    log_path = log_path.resolve()
    if checkpoint is not None:
        checkpoint.validate_resume(config, tasks, matrix, prompts.hashes)
        logger = JsonlLogger(log_path, secret_env_names=names)
    else:
        logger = JsonlLogger.create_exclusive(log_path, secret_env_names=names)
    first = matrix[0]
    context = dict(run_id=run_id, task_id=first["task_id"], condition=first["condition"],
                   repetition=first["repetition"], step=0,
                   container_name=container_name(first["task_id"], first["repetition"]),
                   config_hash=config_hash(config), runtime_backend=runtime_backend,
                   adjudication_version=ADJUDICATION_VERSION)
    if checkpoint is None:
        logger.write(Event(**context, event_type="run_manifest", timestamp=datetime.now(timezone.utc),
                           seed=config.experiment.seed, run_order=matrix, prompt_hashes=prompts.hashes,
                           python_version=platform.python_version(), git_commit=get_git_commit()))
        checkpoint = Checkpoint.create(
            log_path.with_suffix(".state.json"), config=config, tasks=tasks, matrix=matrix,
            prompt_hashes=prompts.hashes, run_id=run_id, log_path=log_path,
            config_directory=config_directory, handler_name=handler_name)
    tasks_by_id = {task.id: task for task in tasks}
    handler = ConsoleApprovalHandler() if handler_name == "console" else DenyAllHandler()
    all_mock = all(s.provider == "mock" for s in (
        config.proposer, config.homogeneous_auditor, config.heterogeneous_auditor))
    clock = FakeClock() if runtime_backend == "fake" and all_mock else RealClock()
    try:
        with single_host_lock() if runtime_backend == "lxd" else nullcontext():
            real_runtime = LxdRuntime(clock=clock) if runtime_backend == "lxd" else None
            for item in matrix[checkpoint.data["next_index"]:]:
                context.update(task_id=item["task_id"], condition=item["condition"],
                               repetition=item["repetition"], step=0,
                               container_name=container_name(item["task_id"], item["repetition"]),
                               task_category=item["category"])
                task = tasks_by_id[item["task_id"]]
                proposer = make_client(config.proposer, Proposal, config_directory, clock)
                settings = config.homogeneous_auditor if item["condition"] == "B" else config.heterogeneous_auditor
                auditor = None if item["condition"] == "A" else make_client(settings, Audit, config_directory, clock)
                # A fake run verifies wiring ONLY. Its canned successes do not
                # evaluate scripts and must never be presented as benchmark data.
                slots = config.limits.max_steps * (1 + len(config.experiment.state_snapshot_commands)) + len(task.verification.checks) + 1
                runtime = real_runtime or FakeRuntime([fake_success() for _ in range(slots)], clock=clock)
                host = (RealHostCheckRunner() if runtime_backend == "lxd" else
                        FakeHostCheckRunner([fake_success() for _ in task.verification.checks], clock=clock))
                result = run_task_repetition(
                    task, item["condition"], item["repetition"], config,
                    proposer, auditor, runtime, host, logger, clock, handler, run_id,
                    prompts=prompts,
                )
                print(json.dumps({"task": task.id, "condition": item["condition"],
                                  "repetition": item["repetition"], "outcome": result.outcome.value,
                                  "termination_reason": result.termination_reason,
                                  "cleanup_succeeded": result.cleanup_succeeded,
                                  "runtime": runtime_backend}))
                if not result.cleanup_succeeded:
                    context["step"] = result.steps
                    raise RuntimeError("Cleanup failed; inspect the target before starting another repetition")
                # Flush the completed close before publishing its checkpoint boundary.
                with log_path.open("r+b") as stream:
                    os.fsync(stream.fileno())
                if checkpoint.close(item, result.termination_reason):
                    context["step"] = result.steps
                    logger.write(BatchPausedEvent(
                        **context, event_type="batch_paused", timestamp=datetime.now(timezone.utc),
                        triggering_repetitions=checkpoint.streak,
                        next_repetition=checkpoint.next_item, checkpoint_path=str(checkpoint.path.resolve())))
                    with log_path.open("r+b") as stream:
                        os.fsync(stream.fileno())
                    print(checkpoint.message(), flush=True)
                    raise BatchPaused(checkpoint.path)
    except BatchPaused:
        raise
    except Exception as exc:
        logger.write(Event(**context, event_type="runner_failed", timestamp=datetime.now(timezone.utc),
                           error=f"Runner stopped ({type(exc).__name__}): {exc}", termination_reason="runner_error"))
        raise
    return log_path


def execute_experiment(config: Config, tasks: list[Task], *, runtime_backend: str,
                       handler_name: str, run_id: str, log_directory: Path,
                       config_directory: Path) -> Path:
    """Run a new batch under a process lock, publishing completed boundaries."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", run_id):
        raise ValueError("Unsafe run ID")
    checkpoint_path = log_directory.resolve() / f"{run_id}.state.json"
    with checkpoint_lock(checkpoint_path.with_suffix(".lock")):
        if checkpoint_path.exists():
            raise FileExistsError("Checkpoint already exists; use explicit resume")
        return _execute_experiment(config, tasks, runtime_backend=runtime_backend,
            handler_name=handler_name, run_id=run_id, log_directory=log_directory,
            config_directory=config_directory)


def resume_experiment(path: Path) -> Path:
    """Use the persisted inputs and original order; never consume CLI overrides."""
    path = path.resolve()
    with checkpoint_lock(path.with_suffix(".lock")):
        checkpoint = Checkpoint.load(path)
        data = checkpoint.data
        config = Config.model_validate(data["config"])
        tasks = [Task.model_validate(item) for item in data["tasks"]]
        log_path = Path(data["log_path"])
        if (log_path.stem != data["run_id"] or log_path.suffix != ".jsonl"
                or path != log_path.with_suffix(".state.json")):
            raise ValueError("Checkpoint log path differs from run ID")
        return _execute_experiment(config, tasks, runtime_backend=config.runtime.backend,
            handler_name=data["handler_name"], run_id=data["run_id"],
            log_directory=log_path.parent, config_directory=Path(data["config_directory"]),
            checkpoint=checkpoint)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sequential Vice SysAdmin repetitions", allow_abbrev=False)
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--tasks", type=Path, default=ROOT / "tasks")
    parser.add_argument("--task", action="append", default=[], help="Limit to this task ID (repeatable)")
    parser.add_argument("--runtime", choices=["fake", "lxd"], default="lxd")
    parser.add_argument("--handler", choices=["deny", "console"], default="deny")
    parser.add_argument("--logs", type=Path, default=ROOT / "logs")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--resume", type=Path, help="Continue a checkpoint after explicit operator action")
    arguments = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(arguments)
    try:
        if args.resume is not None:
            if any(arg.split("=", 1)[0] in {"--config", "--tasks", "--task", "--runtime", "--handler", "--logs", "--run-id"}
                   for arg in arguments):
                raise ValueError("Resume cannot be combined with run overrides")
            output = resume_experiment(args.resume)
            print(f"Log: {output}")
            return 0
        config = load_config(args.config)
        tasks = [load_task(file) for file in sorted(args.tasks.glob("*.yaml"))]
        tasks = select_tasks(tasks, args.task or config.experiment.task_ids)
        run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
        output = execute_experiment(config, tasks, runtime_backend=args.runtime,
                                    handler_name=args.handler, run_id=run_id,
                                    log_directory=args.logs, config_directory=args.config.resolve().parent)
        print(f"Log: {output}")
        return 0
    except BatchPaused:
        return 3
    except Exception as exc:
        # Avoid printing raw SDK/target data; details belong in redacted JSONL.
        if isinstance(exc, FileExistsError):
            print("Run log already exists. Choose a new run ID; existing data was not overwritten.")
        else:
            print(f"Experiment could not complete: {type(exc).__name__}. Check configuration and redacted JSONL.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
