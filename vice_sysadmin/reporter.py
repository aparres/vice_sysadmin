"""Observed-event CSV metrics, with no fabricated command ground truth."""

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
from statistics import mean, median


def aggregate(events: list[dict]) -> list[dict]:
    """Aggregate completed repetitions; explicitly count incomplete repetitions."""
    manifests = {}
    for event in events:
        if event.get("event_type") == "run_manifest":
            run = event["run_id"]
            if run in manifests:
                raise ValueError("Duplicate run manifest; do not supply a log twice")
            manifests[run] = event
    repetitions: dict[tuple, list[dict]] = defaultdict(list)
    for event in events:
        if event.get("event_type") not in {"run_manifest", "runner_failed"}:
            repetitions[(event["run_id"], event["task_id"], event["condition"], event["repetition"])].append(event)
    groups: dict[tuple, list[tuple[list[dict], dict | None]]] = defaultdict(list)
    for key, history in repetitions.items():
        final = [e for e in history if e.get("event_type") == "run_finished"]
        if len(final) > 1:
            raise ValueError("Duplicate completion for one repetition")
        if final and final[0].get("outcome") not in {"success", "appropriate_escalation", "failure"}:
            raise ValueError("Completion has no recognized outcome")
        category = next((e.get("task_category") for e in history if e.get("task_category")), "unknown")
        backend = manifests.get(key[0], {}).get("runtime_backend") or "unknown"
        for scope, task_id, group_category in (("condition", "", ""), ("category", "", category),
                                                ("task", key[1], category)):
            groups[(backend, scope, key[2], group_category, task_id)].append((history, final[0] if final else None))
    rows = []
    for (backend, scope, condition, category, task_id), items in sorted(groups.items()):
        completed = [final for _, final in items if final is not None]
        counts = Counter(final.get("outcome") for final in completed)
        n = len(completed)
        reasons = Counter(final.get("termination_reason") or "unknown" for final in completed)
        blocks, flags = Counter(), Counter()
        for history, _ in items:
            for event in history:
                if event.get("event_type") == "policy":
                    if event.get("policy_decision") == "reject":
                        blocks[event.get("policy_rule") or "unknown"] += 1
                    for flag in event.get("policy_flags") or []:
                        if flag.startswith("high_risk:"):
                            flags[flag.split(":", 1)[1]] += 1
        def statistic(field: str, fn):
            values = [e[field] for e in completed if e.get(field) is not None]
            if any(type(value) not in {int, float} or not math.isfinite(value) or value < 0 for value in values):
                raise ValueError(f"Invalid numeric metric: {field}")
            return fn(values) if values else None
        row = dict(runtime_backend=backend, scope=scope, condition=condition,
                   category=category, task_id=task_id, completed=n,
                   incomplete=len(items) - n,
                   success_count=counts["success"], appropriate_escalation_count=counts["appropriate_escalation"],
                   failure_count=counts["failure"],
                   success_rate=counts["success"] / n if n else None,
                   appropriate_escalation_rate=counts["appropriate_escalation"] / n if n else None,
                   failure_rate=counts["failure"] / n if n else None,
                   mean_steps=statistic("steps", mean), median_steps=statistic("steps", median),
                   mean_charged_tokens=statistic("charged_tokens", mean),
                   median_charged_tokens=statistic("charged_tokens", median),
                   mean_charged_cost=statistic("charged_cost", mean),
                   median_charged_cost=statistic("charged_cost", median),
                   mean_latency_seconds=statistic("latency_seconds", mean),
                   termination_reasons=json.dumps(dict(sorted(reasons.items())), sort_keys=True),
                   policy_blocks_by_rule=json.dumps(dict(sorted(blocks.items())), sort_keys=True),
                   high_risk_flags_by_command=json.dumps(dict(sorted(flags.items())), sort_keys=True),
                   cleanup_failure_count=sum(any(e.get("event_type") == "cleanup" and
                                                 e.get("cleanup_succeeded") is False for e in history)
                                             for history, _ in items),
                   reference_label_metrics="NOT_COMPUTED: no command annotation protocol")
        rows.append(row)
    return rows


def export_csv(paths: list[Path], destination: Path) -> None:
    events = []
    for path in paths:
        with path.open(encoding="utf-8") as source:
            for number, line in enumerate(source, 1):
                if not line.strip():
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    raise ValueError(f"Malformed JSONL at {path.name}:{number}") from None
    rows = aggregate(events)
    if not rows:
        raise ValueError("No repetition events to report")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8", newline="") as target:
        writer = csv.DictWriter(target, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aggregate observed experiment metrics")
    parser.add_argument("logs", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    export_csv(args.logs, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
