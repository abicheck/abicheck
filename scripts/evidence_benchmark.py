#!/usr/bin/env python3
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

"""ADR-033 Phase 7 — performance & false-positive benchmark report for evidence.

A self-contained, compiler-free report consolidating the ADR-033 D9 signals so
CI (and humans) can watch evidence-collection cost and false-positive drift in
one place:

* **Collection performance** — times the inline build-evidence path
  (``collect_inline_pack`` on a synthetic compile DB) per ADR-033 D2 collect
  mode, reporting wall-clock seconds and the layers each mode collected. No
  compiler/clang is required: ``build`` mode is pure JSON/text normalization and
  the source modes degrade to a partial L4 surface when clang is absent.
* **False-positive delta** — the public-surface FP-rate gate's D9 metrics
  (``false_positive_delta_vs_baseline`` / ``false_negative_delta_vs_baseline``),
  reused from :mod:`check_fp_rate`.

Run: ``python scripts/evidence_benchmark.py`` (text) or ``--json`` (machine).
This is a reporting tool, not a gate: it never fails the build on timing. The
FP deltas it prints are *gated separately* by ``scripts/check_fp_rate.py``.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_fp_rate  # noqa: E402

from abicheck.buildsource.inline import collect_inline_pack  # noqa: E402
from abicheck.buildsource.source_replay import collection_for_ci_mode  # noqa: E402

#: Collect modes worth timing in the report (off collects nothing, so skip).
_REPORT_MODES = ("build", "source-changed", "source-target", "graph-full")


def _synthetic_tree(root: Path, n_units: int) -> Path:
    """Write a tiny source tree + compile_commands.json with *n_units* TUs."""
    tree = root / "src"
    tree.mkdir(parents=True, exist_ok=True)
    cdb = []
    for i in range(n_units):
        src = tree / f"f{i}.cpp"
        src.write_text(f"int f{i}(int x){{return x+{i};}}\n", encoding="utf-8")
        cdb.append({
            "directory": str(tree),
            "file": f"f{i}.cpp",
            "arguments": ["c++", "-std=c++17", "-c", f"f{i}.cpp"],
        })
    (tree / "compile_commands.json").write_text(json.dumps(cdb), encoding="utf-8")
    return tree


def collection_timings(n_units: int = 25) -> list[dict[str, object]]:
    """Time inline collection per collect mode over a synthetic *n_units* tree."""
    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as td:
        tree = _synthetic_tree(Path(td), n_units)
        for mode in _REPORT_MODES:
            scope, layers = collection_for_ci_mode(mode)
            start = time.perf_counter()
            # clang is intentionally absent here so 'build' stays pure-Python and
            # the source modes degrade to a partial L4 surface (never abort).
            pack = collect_inline_pack(
                sources=tree, build_info=None, scope=scope, layers=layers,
                clang_bin="definitely-not-a-real-clang",
            )
            elapsed = time.perf_counter() - start
            collected = []
            if pack is not None:
                if pack.build_evidence is not None:
                    collected.append("L3")
                if pack.source_abi is not None:
                    collected.append("L4")
                if pack.source_graph is not None:
                    collected.append("L5")
            rows.append({
                "mode": mode,
                "duration_seconds": round(elapsed, 4),
                "layers_collected": collected,
                "compile_units": n_units,
            })
    return rows


def build_report(n_units: int = 25) -> dict[str, object]:
    """Assemble the consolidated ADR-033 evidence benchmark report."""
    return {
        "collection_performance": collection_timings(n_units),
        "false_positive": check_fp_rate.metrics(),
    }


def _print_text(report: dict[str, object]) -> None:
    print("ADR-033 evidence benchmark report")
    print("=================================")
    print("Collection performance (synthetic tree, no compiler):")
    print(f"  {'mode':<16}{'seconds':>9}  layers")
    for row in report["collection_performance"]:  # type: ignore[union-attr]
        layers = ",".join(row["layers_collected"]) or "-"  # type: ignore[index]
        print(f"  {row['mode']:<16}{row['duration_seconds']:>9}  {layers}")  # type: ignore[index]
    fp = report["false_positive"]
    print("False-positive gate (public-surface scoping):")
    print(
        f"  cases={fp['cases']} "  # type: ignore[index]
        f"fp_delta={fp['false_positive_delta_vs_baseline']} "  # type: ignore[index]
        f"fn_delta={fp['false_negative_delta_vs_baseline']}"  # type: ignore[index]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ADR-033 evidence benchmark report.")
    parser.add_argument("--json", action="store_true", help="Emit the report as JSON.")
    parser.add_argument("--units", type=int, default=25,
                        help="Synthetic compile units to time (default: 25).")
    args = parser.parse_args(argv)
    report = build_report(args.units)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
