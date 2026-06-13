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
BREAKING_VERDICTS = {"BREAKING", "API_BREAK"}
COMPATIBLE_VERDICTS = {"COMPATIBLE", "COMPATIBLE_WITH_RISK", "NO_CHANGE"}
COMPARISON_STATUSES = {
    "MATCH",
    "UNCOMPARABLE",
    "ABICHECK_STRICTER",
    "ABICHECK_WEAKER",
}


def load_json(path: Path) -> Any:
    """Load JSON from a UTF-8 file."""
    return json.loads(path.read_text(encoding="utf-8"))


def count_values(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    """Count stringified values for a field across records."""
    return dict(sorted(Counter(str(record.get(field, "")) for record in records).items()))


def count_first_value(
    records: list[dict[str, Any]], primary: str, fallback: str
) -> dict[str, int]:
    """Count a primary field with fallback support for older artifacts."""
    values = [
        str(record.get(primary) or record.get(fallback) or "")
        for record in records
    ]
    return dict(sorted(Counter(values).items()))


def normalize_verdict(verdict: str | None) -> str:
    """Collapse abicheck verdicts to a compatible/breaking axis."""
    normalized = (verdict or "").strip().upper()
    if normalized in BREAKING_VERDICTS:
        return "BREAKING"
    if normalized in COMPATIBLE_VERDICTS:
        return "COMPATIBLE"
    return "UNKNOWN"


def comparison_status(record: dict[str, Any]) -> str:
    """Return canonical expected-vs-actual comparison status for a record."""
    status = record.get("comparison_status")
    if status:
        normalized_status = str(status).strip().upper()
        if normalized_status in COMPARISON_STATUSES:
            return normalized_status
    expected = normalize_verdict(record.get("expected") or record.get("expectation"))
    actual = normalize_verdict(record.get("got") or record.get("verdict"))
    if expected == "UNKNOWN" or actual == "UNKNOWN":
        return "UNCOMPARABLE"
    if expected == actual:
        return "MATCH"
    if actual == "BREAKING" and expected == "COMPATIBLE":
        return "ABICHECK_STRICTER"
    return "ABICHECK_WEAKER"


def summarize_examples(path: Path) -> dict[str, Any]:
    """Summarize a validate_examples.v2 artifact."""
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
    """Summarize a component_suites.v1 artifact."""
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
    """Summarize run_matrix real-world records."""
    records = load_json(path)
    if not isinstance(records, list):
        raise ValueError(f"{path} must contain a list of real-world records")
    meta = load_json(meta_path) if meta_path and meta_path.exists() else {}
    statuses = [comparison_status(record) for record in records]
    status_counts = dict(sorted(Counter(statuses).items()))
    run_errors = sum(
        1
        for record in records
        if comparison_status(record) == "UNCOMPARABLE"
        and not (record.get("got") or record.get("verdict"))
    )
    expectation_mismatches = sum(
        status_counts.get(status, 0)
        for status in ("ABICHECK_STRICTER", "ABICHECK_WEAKER")
    )
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
        "verdict_counts": count_first_value(records, "got", "verdict"),
        "expected_counts": count_first_value(records, "expected", "expectation"),
        "comparison_status_counts": status_counts,
        "source_layer_counts": layer_counts(records),
        "run_errors": run_errors,
        "expectation_mismatches": expectation_mismatches,
        "blocking_failures": run_errors + expectation_mismatches,
    }


def layer_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    """Count evidence/source layer mentions across records."""
    counts: Counter[str] = Counter()
    for record in records:
        for layer in record.get("source_layers", []) or []:
            counts[str(layer)] += 1
    return dict(sorted(counts.items()))


def make_summary(
    sections: list[dict[str, Any]], command_argv: list[str] | None = None
) -> dict[str, Any]:
    """Build the combined remeasurement summary payload."""
    return {
        "schema_version": SCHEMA_VERSION,
        "command": [
            sys.executable,
            *(command_argv if command_argv is not None else sys.argv[1:]),
        ],
        "section_count": len(sections),
        "total_records": sum(int(section.get("total", 0)) for section in sections),
        "blocking_failures": sum(
            int(section.get("blocking_failures", 0)) for section in sections
        ),
        "sections": sections,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--examples", type=Path)
    parser.add_argument("--components", type=Path)
    parser.add_argument("--real-world", type=Path)
    parser.add_argument("--real-world-meta", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--fail-on-blocking", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for combined remeasurement summaries."""
    command_argv = sys.argv[1:] if argv is None else argv
    args = parse_args(command_argv)
    sections = []
    if args.examples:
        sections.append(summarize_examples(args.examples))
    if args.components:
        sections.append(summarize_components(args.components))
    if args.real_world:
        sections.append(summarize_real_world(args.real_world, args.real_world_meta))
    summary = make_summary(sections, command_argv=command_argv)
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
