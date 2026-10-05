"""Read-only incremental progress display; no experiment operations or imports."""

import argparse
from collections import Counter, deque
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import time

from .schemas import Audit, Outcome

POLL_SECONDS = 1.5
STALL_MULTIPLIER = 3.0
READ_BYTES = 1024 * 1024
MAX_LINE_BYTES = 16 * 1024 * 1024
RECENT_COMMANDS = 10
REASON_PREVIEW_CHARS = 600
COMMAND_PREVIEW_CHARS = 240


class MonitorError(ValueError):
    """Invalid input; messages deliberately exclude raw log content."""


def token(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", value):
        raise MonitorError("Invalid display identifier in log")
    return value


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise MonitorError("Invalid numeric field in log")
    return value


def timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.utcoffset() is None:
            raise ValueError
        return result
    except (AttributeError, TypeError, ValueError):
        raise MonitorError("Invalid timestamp in log") from None


def key(row):
    task, condition, repetition = token(row.get("task_id")), row.get("condition"), row.get("repetition")
    if condition not in ("A", "B", "C") or type(repetition) is not int or repetition < 1:
        raise MonitorError("Invalid repetition identity in log")
    return task, condition, repetition


def command_preview(value):
    """Render redacted command text as one bounded, escaped terminal line."""
    if not isinstance(value, str):
        raise MonitorError("Invalid command field in policy event")
    preview = json.dumps(value[:COMMAND_PREVIEW_CHARS], ensure_ascii=True)
    return preview + (" [truncated]" if len(value) > COMMAND_PREVIEW_CHARS else "")


def rejection_preview(row):
    """Extract only a valid rejection justification from a redacted attempt."""
    raw = row.get("raw_response")
    if row.get("error") is not None or not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, flags=re.DOTALL)
        if fence is None:
            return None
        text = fence[1]
    try:
        result = Audit.model_validate_json(text)
    except ValueError:
        return None
    if result.verdict != "reject" or not result.justification.strip():
        return None
    reason = result.justification
    preview = json.dumps(reason[:REASON_PREVIEW_CHARS], ensure_ascii=True)
    return preview + (" [truncated]" if len(reason) > REASON_PREVIEW_CHARS else "")


class Progress:
    def __init__(self):
        self.run_id = None
        self.seed = None
        self.order = []
        self.planned = set()
        self.cells = Counter()
        self.done_cells = Counter()
        self.finished = set()
        self.outcomes = Counter({outcome.value: 0 for outcome in Outcome})
        self.reasons = Counter()
        self.starts = {}
        self.duration_total = 0.0
        self.duration_count = 0
        self.latest = None
        self.active = None
        self.runner_failed = False
        self.recent_commands = deque(maxlen=RECENT_COMMANDS)

    def accept(self, row):
        if not isinstance(row, dict):
            raise MonitorError("Expected an event object")
        kind = row.get("event_type")
        if self.run_id is None:
            if kind != "run_manifest" or not isinstance(row.get("run_order"), list):
                raise MonitorError("First event must be a run_manifest with run_order")
            order = [key(item) for item in row["run_order"] if isinstance(item, dict)]
            if not order or len(order) != len(row["run_order"]) or len(set(order)) != len(order):
                raise MonitorError("Invalid or duplicate planned matrix entries")
            self.run_id = token(row.get("run_id"))
            if type(row.get("seed")) is not int:
                raise MonitorError("Missing manifest seed")
            self.seed, self.order, self.planned = row["seed"], order, set(order)
            self.cells.update((task, condition) for task, condition, _ in order)
            return
        if kind == "run_manifest" or row.get("run_id") != self.run_id:
            raise MonitorError("Duplicate manifest or mixed runs")
        identity = key(row)
        if identity not in self.planned:
            raise MonitorError("Event is outside the planned matrix")
        step = row.get("step")
        if type(step) is not int or step < 0:
            raise MonitorError("Invalid step")
        when = timestamp(row.get("timestamp"))
        self.latest = (identity, step)
        self.track_command(row, identity, step)
        if kind == "run_started":
            if identity in self.starts or identity in self.finished or self.active is not None:
                raise MonitorError("Duplicate or overlapping repetition start")
            self.starts[identity] = when
            self.active = identity
        elif kind == "run_finished":
            if identity not in self.starts or identity in self.finished:
                raise MonitorError("Missing start or duplicate completion")
            try:
                outcome = Outcome(row.get("outcome")).value
            except (TypeError, ValueError):
                raise MonitorError("Unknown outcome") from None
            reason = token(row.get("termination_reason"))
            duration = row.get("latency_seconds")
            if duration is None:
                duration = (when - self.starts[identity]).total_seconds()
            duration = number(duration)
            self.duration_total += duration
            self.duration_count += 1
            self.finished.add(identity)
            self.done_cells[identity[:2]] += 1
            self.outcomes[outcome] += 1
            self.reasons[reason] += 1
            self.active = None
        elif kind == "runner_failed":
            self.runner_failed = True

    def track_command(self, row, identity, step):
        """Join policy/audit events by repetition identity and step, never text."""
        kind = row.get("event_type")
        entry = next((item for item in self.recent_commands
                      if item["identity"] == identity and item["step"] == step), None)
        if kind == "policy":
            decision = row.get("policy_decision")
            if decision not in ("approve", "reject", "escalate", "disabled"):
                raise MonitorError("Invalid policy decision")
            if entry is None:
                auditor = ("N/A (condition A)" if identity[1] == "A" else
                           "not sent (policy rejected)" if decision == "reject" else
                           "waiting (policy escalation)" if decision == "escalate" else "pending")
                self.recent_commands.append(dict(identity=identity, step=step,
                    command=command_preview(row.get("command")), policy=decision, auditor=auditor, rejection_reason=None, candidate_reason=None))
        elif entry is not None and identity[1] != "A":
            if kind == "audit":
                verdict = row.get("verdict")
                if verdict not in ("approve", "reject", "escalate"):
                    raise MonitorError("Invalid auditor verdict")
                entry["auditor"] = verdict
                entry["rejection_reason"] = entry["candidate_reason"] if verdict == "reject" else None
                entry["candidate_reason"] = None
            elif kind == "auditor_attempt":
                entry["auditor"] = "pending"
                entry["rejection_reason"] = None
                entry["candidate_reason"] = rejection_preview(row)
            elif kind == "auditor_invalid_output":
                entry["candidate_reason"] = None
            elif kind == "audit_failure":
                entry["auditor"] = "error (no verdict)"
                entry["candidate_reason"] = None
                entry["rejection_reason"] = None
            elif kind == "escalation" and entry["auditor"] == "waiting (policy escalation)":
                entry["auditor"] = ("pending" if row.get("verdict") == "approve"
                                    else "not sent (policy escalation denied)")
        if kind in ("run_finished", "runner_failed"):
            for item in self.recent_commands:
                if item["identity"] == identity and item["auditor"] in (
                        "pending", "waiting (policy escalation)"):
                    item["auditor"] = "unavailable (no verdict logged)"

    @property
    def complete(self):
        return bool(self.planned) and self.finished == self.planned

    @property
    def average(self):
        return self.duration_total / self.duration_count if self.duration_count else None

    def elapsed(self, now):
        if self.active is None:
            return None
        return max(0.0, (now - self.starts[self.active]).total_seconds())

    def eta(self, now):
        if self.complete:
            return 0.0
        if self.average is None or self.runner_failed:
            return None
        return max(0.0, self.average * (len(self.planned) - len(self.finished))
                   - (self.elapsed(now) or 0.0))


class Tail:
    """Read new bytes only. Incomplete lines remain buffered across polls."""
    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.pending = b""
        self.identity = None
        self.lines = 0
        self.caught_up = False

    def poll(self, progress):
        with self.path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            if self.identity is not None and (identity != self.identity or stat.st_size < self.offset):
                raise MonitorError("Log was replaced or truncated; restart monitor")
            self.identity = identity
            stream.seek(self.offset)
            chunk = stream.read(READ_BYTES)
        self.offset += len(chunk)
        self.caught_up = len(chunk) < READ_BYTES
        pieces = (self.pending + chunk).split(b"\n")
        self.pending = pieces.pop()
        for line in pieces:
            if len(line) > MAX_LINE_BYTES:
                raise MonitorError("Log line exceeds monitor limit")
            try:
                row = json.loads(line)
            except (ValueError, UnicodeError):
                raise MonitorError("Malformed complete JSONL line") from None
            progress.accept(row)
            self.lines += 1
        if len(self.pending) > MAX_LINE_BYTES:
            raise MonitorError("Incomplete log line exceeds monitor limit")


def seconds(value):
    if value is None:
        return "unavailable"
    return str(round(value, 1)) + "s"


def eta_clock(value):
    """Round up to whole seconds; hours do not wrap after 24."""
    if value is None:
        return "unavailable"
    hours, remainder = divmod(math.ceil(value), 3600)
    minutes, seconds_left = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_left:02d}"


def render(progress, now, stall_multiplier=STALL_MULTIPLIER, *, caught_up=True):
    if progress.run_id is None:
        return "Waiting for a complete run_manifest line."
    lines = [f"Run: {progress.run_id} | Seed: {progress.seed}",
             f"Completed: {len(progress.finished)}/{len(progress.planned)} | "
             f"Rough ETA (not guaranteed): {eta_clock(progress.eta(now) if caught_up else None)}",
             f"Mean completed duration: {seconds(progress.average)} "
             f"({progress.duration_count} samples)", "Task | A | B | C"]
    for task in sorted({task for task, _, _ in progress.order}):
        cells = [f"{progress.done_cells[task, condition]}/{progress.cells[task, condition]}"
                 for condition in ("A", "B", "C")]
        lines.append(task + " | " + " | ".join(cells))
    lines.append("Outcomes: " + ", ".join(f"{name}={count}" for name, count in progress.outcomes.items()))
    lines.append("Termination reasons: " + (", ".join(
        f"{name}={count}" for name, count in sorted(progress.reasons.items())) or "none yet"))
    if progress.latest:
        (task, condition, repetition), step = progress.latest
        lines.append(f"Latest: {task}/{condition}/rep-{repetition} step={step}")
    if progress.active:
        task, condition, repetition = progress.active
        elapsed = progress.elapsed(now)
        stalled = caught_up and progress.average is not None and progress.average > 0 and elapsed > stall_multiplier * progress.average
        lines.append(f"In flight: {task}/{condition}/rep-{repetition} elapsed={seconds(elapsed)}"
                     + (" | MAY BE STALLED (informational only)" if stalled else ""))
    else:
        lines.append("In flight: none logged")
    if not caught_up:
        lines.append("Catching up with log bytes; ETA and stall warning are withheld.")
    if progress.runner_failed:
        lines.append("Runner failure recorded; remaining work may never complete. Ctrl+C to stop monitoring.")
    lines.append(f"Last {RECENT_COMMANDS} evaluated commands (newest first; escaped log previews):")
    lines.append("Task/condition/repetition/step | Policy | Auditor | Command")
    if not progress.recent_commands:
        lines.append("No policy-evaluated commands logged yet.")
    for entry in reversed(progress.recent_commands):
        task, condition, repetition = entry["identity"]
        lines.append(f"{task}/{condition}/rep-{repetition}/step-{entry['step']} | "
                     f"{entry['policy']} | {entry['auditor']} | {entry['command']}")
        if entry["auditor"] == "reject":
            lines.append("  Auditor rejection reason: " + (entry["rejection_reason"] or "unavailable (not recorded or invalid response)"))
    if progress.complete:
        lines.append("COMPLETE: every planned repetition has a run_finished event.")
    return "\n".join(lines)


def positive(value):
    try:
        parsed = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Expected a positive finite number") from None
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("Expected a positive finite number")
    return parsed


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only JSONL progress monitor")
    parser.add_argument("log", type=Path)
    parser.add_argument("--interval", type=positive, default=POLL_SECONDS)
    parser.add_argument("--stall-multiplier", type=positive, default=STALL_MULTIPLIER)
    args = parser.parse_args(argv)
    tail, progress = Tail(args.log), Progress()
    try:
        while True:
            try:
                tail.poll(progress)
            except FileNotFoundError:
                if tail.identity is not None:
                    raise MonitorError("Log disappeared; restart monitor") from None
            if sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(render(progress, datetime.now(timezone.utc), args.stall_multiplier, caught_up=tail.caught_up), flush=True)
            if progress.complete:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n" + render(progress, datetime.now(timezone.utc), args.stall_multiplier))
        return 0
    except (MonitorError, OSError):
        # Never echo raw records, paths, SDK output or exception payloads.
        print("Monitor stopped: unreadable, changed or invalid log. Experiment was not modified.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
