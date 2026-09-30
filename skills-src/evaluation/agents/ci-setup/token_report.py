# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Token and cost breakdown of `run_eval.py` output directories.

Where a run's money goes, per arm: cache reads (the context re-sent every
turn), cache writes (new content entering the context once), fresh input,
and output — plus which skill files the agent actually read, and the cost
per *successful* run, which is the number that matters.

    python token_report.py /tmp/ci-setup-eval/run4 /tmp/ci-setup-eval/run5

Prices are per million tokens and default to Sonnet-class list prices;
override them with ``--price``. The run's own ``total_cost_usd`` is shown
beside the recomputed figure so a stale price table is visible.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

#: $ per token. `cache_write` is the 5-minute ephemeral write price.
DEFAULT_PRICES = {"input": 3e-6, "cache_write": 3.75e-6, "cache_read": 0.30e-6, "output": 15e-6}

SKILL_FILES = (
    "SKILL.md",
    "template-release-baseline.md",
    "template-merge-base.md",
    "template-committed-snapshot.md",
    "workflow-templates.md",
    "pitfalls.md",
    "depth-toolchain-and-floors.md",
    "safety-invariants.md",
)


def _events(path: Path) -> list[dict[str, Any]]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def analyse_run(run_dir: Path, prices: dict[str, float]) -> dict[str, Any] | None:
    events = _events(run_dir / "events.jsonl")
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if not result:
        return None
    usage = result.get("usage") or {}
    reads: set[str] = set()
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                text = json.dumps(block.get("input"))
                reads.update(f for f in SKILL_FILES if f in text)
    grade_path = run_dir / "grade.json"
    grade = json.loads(grade_path.read_text(encoding="utf-8")) if grade_path.is_file() else {}
    cost = {
        "cache_read": usage.get("cache_read_input_tokens", 0) * prices["cache_read"],
        "cache_write": usage.get("cache_creation_input_tokens", 0) * prices["cache_write"],
        "input": usage.get("input_tokens", 0) * prices["input"],
        "output": usage.get("output_tokens", 0) * prices["output"],
    }
    return {
        "arm": run_dir.parent.name,
        "turns": result.get("num_turns", 0),
        "seconds": (result.get("duration_ms") or 0) / 1000,
        "reported_cost": result.get("total_cost_usd") or 0.0,
        "output_tokens": usage.get("output_tokens", 0),
        "context_tokens": usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0) + usage.get("input_tokens", 0),
        "success": bool(grade.get("success")),
        "score": grade.get("score", 0.0),
        "reads": sorted(reads),
        **{f"cost_{k}": v for k, v in cost.items()},
    }


def summarise(root: Path, prices: dict[str, float]) -> list[str]:
    rows = [r for p in sorted(root.glob("*/*/rep*")) if (r := analyse_run(p, prices))]
    lines = [f"## {root}"]
    for arm in sorted({r["arm"] for r in rows}):
        sel = [r for r in rows if r["arm"] == arm]
        mean = lambda k: statistics.mean(r[k] for r in sel)  # noqa: E731
        ok = sum(r["success"] for r in sel)
        total = sum(r["reported_cost"] for r in sel)
        lines.append(
            f"- **{arm}** n={len(sel)} · cost ${mean('reported_cost'):.3f} "
            f"(cache read ${mean('cost_cache_read'):.3f}, cache write ${mean('cost_cache_write'):.3f}, "
            f"output ${mean('cost_output'):.3f}) · context {mean('context_tokens') / 1e3:.0f}k · "
            f"output {mean('output_tokens') / 1e3:.1f}k · turns {mean('turns'):.1f} · {mean('seconds'):.0f}s · "
            f"success {ok}/{len(sel)} · score {mean('score'):.2f} · "
            f"$/success {total / ok:.3f}" if ok else f"- **{arm}** n={len(sel)} · no successful run"
        )
        counts = {f: sum(f in r["reads"] for r in sel) for f in SKILL_FILES}
        read = ", ".join(f"{f} {c}/{len(sel)}" for f, c in counts.items() if c)
        if read:
            lines.append(f"  - files read: {read}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--price", action="append", default=[], metavar="KIND=USD_PER_MTOK",
                        help="override a price, e.g. output=15")
    args = parser.parse_args(argv)
    prices = dict(DEFAULT_PRICES)
    for item in args.price:
        kind, _, value = item.partition("=")
        if kind not in prices:
            parser.error(f"unknown price kind {kind!r}; one of {sorted(prices)}")
        prices[kind] = float(value) / 1e6
    for root in args.roots:
        print("\n".join(summarise(root, prices)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
