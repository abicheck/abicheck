#!/usr/bin/env python3
"""Summarize remeasurement artifacts from examples, components, and real world."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "remeasurement_summary.v1"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def count_values(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(record.get(field, "")) for record in records).items()))


def summarize_examples(path: Path) -> dict[str, Any]:
    data = load_json(path)
    records = list(data.get("results", []))
    return {
        "artifact": str(path),
        "schema_version": data.get("schema_version"),
        "component": "synthetic-example",
        "runner": data.get("runner"),
        "platform": data.get("platform"),
        "command": data.get("command"),
        "total": len(records),
        "status_counts": dict(sorted(data.get("summary", {}).items())),
        "mode_counts": count_values(records, "mode"),
        "source_layer_counts": layer_counts(records),
        "blocking_failures": sum(
            1 for record in records if record.get("status") in {"FAIL", "ERROR"}
        ),
    }


def summarize_components(path: Path) -> dict[str, Any]:
    data = load_json(path)
    records = list(data.get("records", []))
    blocked = [
        {
            "suite": record.get("suite"),
            "blocked_reasons": record.get("blocked_reasons", []),
        }
        for record in records
        if record.get("status") == "blocked"
    ]
    return {
        "artifact": str(path),
        "schema_version": data.get("schema_version"),
        "component": "component-suite",
        "runner": data.get("runner"),
        "platform": data.get("platform"),
        "command": data.get("command"),
        "total": len(records),
        "status_counts": dict(sorted(data.get("status_counts", {}).items())),
        "suite_counts": count_values(records, "suite"),
        "source_layer_counts": layer_counts(records),
        "blocked": blocked,
        "blocking_failures": sum(
            1 for record in records if record.get("status") in {"failed", "blocked"}
        ),
    }


def summarize_real_world(path: Path, meta_path: Path | None = None) -> dict[str, Any]:
    records = load_json(path)
    if not isinstance(records, list):
        raise ValueError(f"{path} must contain a list of real-world records")
    meta = load_json(meta_path) if meta_path and meta_path.exists() else {}
    return {
        "artifact": str(path),
        "metadata_artifact": str(meta_path) if meta_path else None,
        "schema_version": meta.get("schema_version")
        or next((record.get("schema_version") for record in records), None),
        "component": "real-world-matrix",
        "runner": meta.get("runner"),
        "platform": meta.get("platform"),
        "command": meta.get("command"),
        "total": len(records),
        "mode_counts": count_values(records, "mode"),
        "verdict_counts": count_values(records, "got"),
        "expected_counts": count_values(records, "expected"),
        "source_layer_counts": layer_counts(records),
        "blocking_failures": sum(
            1 for record in records if int(record.get("exit_code") or 0) != 0
        ),
    }


def layer_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for record in records:
        for layer in record.get("source_layers", []) or []:
            counts[str(layer)] += 1
    return dict(sorted(counts.items()))


def make_summary(sections: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "command": [sys.executable, *sys.argv],
        "section_count": len(sections),
        "total_records": sum(int(section.get("total", 0)) for section in sections),
        "blocking_failures": sum(
            int(section.get("blocking_failures", 0)) for section in sections
        ),
        "sections": sections,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path)
    parser.add_argument("--components", type=Path)
    parser.add_argument("--real-world", type=Path)
    parser.add_argument("--real-world-meta", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-blocking", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    sections = []
    if args.examples:
        sections.append(summarize_examples(args.examples))
    if args.components:
        sections.append(summarize_components(args.components))
    if args.real_world:
        sections.append(summarize_real_world(args.real_world, args.real_world_meta))
    summary = make_summary(sections)
    text = json.dumps(summary, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    if args.fail_on_blocking and summary["blocking_failures"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
