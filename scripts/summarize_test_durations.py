#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Render the per-phase test durations captured by tests/conftest.py.

The conftest ``ABICHECK_DURATIONS_JSON`` hook writes a list of
``{"nodeid", "when", "duration"}`` rows. This script aggregates them per test
(summing setup+call+teardown, matching pytest's ``--durations`` semantics) and
prints a Markdown table of the slowest N. In CI it appends to the job summary
(``GITHUB_STEP_SUMMARY``); locally it prints to stdout.

It is a *reporting* tool, not a gate — CI-runner timing varies too much for a
hard per-test wall-time threshold to be anything but flaky. The uploaded JSON
artifact is the durable record for trend tracking; a regression gate (with a
generous threshold) can be layered on later if desired.

Usage:
    python scripts/summarize_test_durations.py [path] [--top N]
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path


def aggregate(rows: list[dict[str, object]]) -> dict[str, float]:
    """Sum every phase's duration per test nodeid."""
    totals: dict[str, float] = collections.defaultdict(float)
    for row in rows:
        totals[str(row["nodeid"])] += float(row["duration"])  # type: ignore[arg-type]
    return dict(totals)


def render(totals: dict[str, float], top: int) -> str:
    """Build a Markdown report of the slowest ``top`` tests."""
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top]
    grand = sum(totals.values())
    lines = [
        f"## Slowest {len(ranked)} tests (fast lane, under coverage)",
        "",
        "| Total | Test |",
        "|------:|------|",
    ]
    lines += [f"| {dur:.2f}s | `{nodeid}` |" for nodeid, dur in ranked]
    lines += [
        "",
        f"Sum of per-test time: **{grand:.1f}s** across {len(totals)} tests "
        "(parallel wall time is lower).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default="test-durations.json",
        help="Durations JSON written by the conftest hook (default: test-durations.json).",
    )
    parser.add_argument("--top", type=int, default=25, help="How many tests to list.")
    args = parser.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"No durations file at {path}; nothing to report.", file=sys.stderr)
        return 0

    # A present-but-unreadable/malformed file is an operational failure, not the
    # benign "nothing to report" of a *missing* file above: surface it with a
    # non-zero exit (per the scripts/ convention) instead of a traceback. In CI
    # the step is continue-on-error, so this stays visible without blocking the
    # otherwise-green job.
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"Could not read durations from {path}: {exc}", file=sys.stderr)
        return 1

    report = render(aggregate(rows), args.top)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(report)
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
