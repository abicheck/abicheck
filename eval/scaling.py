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

"""C1 — parallel-L4 (P06) scaling harness for the field evaluation.

Measures the wall-clock time of ``abicheck dump --sources`` at several
``ABICHECK_L4_JOBS`` levels on real source trees and emits the scaling curve the
follow-up plan asks for (``eval/FOLLOWUPS.md`` §C1). The per-TU clang AST-JSON
extraction (L4) is the embarrassingly-parallel phase; the L3 compile-DB parse,
the L5 graph build, and snapshot serialization stay serial — so the *whole-dump*
speedup is Amdahl-bounded by that serial fraction, not the worker count. The
report records exactly that: the L4 fraction parallelizes, the end-to-end dump
plateaus once the serial phases dominate.

Layout mirrors ``runner.py``: the pure helpers (:func:`speedup_rows`,
:func:`render_scaling`, :func:`amdahl_serial_fraction`) are unit-tested in
``tests/test_eval_scaling.py``; the live driver clones+configures each tree
(reusing the runner's git/cmake helpers) and times the dump. Gated on git+cmake;
clang absent only means L4 is cheaper (fewer decls), never a crash.

Reproduce::

    python eval/scaling.py                 # default trees, jobs 1/2/4, 1 rep
    python eval/scaling.py --jobs 1,2,4,8 --reps 3
    python eval/scaling.py --report-only   # re-render SCALING.md from latest
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path

# The harness lives in eval/ alongside runner.py; reuse its clone/configure
# helpers and shared paths rather than duplicating the toolchain plumbing. The
# `runner._*` helpers below are private but intentionally shared between the two
# eval entry points — keep them in sync if runner's clone/configure API changes.
_EVAL_DIR = Path(__file__).resolve().parent
if str(_EVAL_DIR) not in sys.path:
    sys.path.insert(0, str(_EVAL_DIR))

import runner  # noqa: E402  (eval/runner.py — path injected above)

SCALING_SCHEMA = "abicheck-eval-scaling/1"

#: Default trees: a small C tree (zstd) and the prior baseline (freetype, 42 TU).
#: Each is self-contained (no submodules) and configures with stock cmake.
DEFAULT_TREES: list[dict] = [
    {
        "label": "freetype",
        "repo": "https://github.com/freetype/freetype.git",
        "tag": "VER-2-13-3",
        "cmake_args": [
            "-G",
            "Ninja",
            "-DCMAKE_C_COMPILER=clang",
            "-DFT_DISABLE_HARFBUZZ=ON",
            "-DFT_DISABLE_BROTLI=ON",
            "-DFT_DISABLE_BZIP2=ON",
            "-DFT_DISABLE_PNG=ON",
            "-DFT_DISABLE_ZLIB=ON",
        ],
    },
    {
        "label": "zstd",
        "repo": "https://github.com/facebook/zstd.git",
        "tag": "v1.5.7",
        "cmake_subdir": "build/cmake",
        "cmake_args": [
            "-G",
            "Ninja",
            "-DCMAKE_C_COMPILER=clang",
            "-DZSTD_BUILD_TESTS=OFF",
            "-DZSTD_BUILD_PROGRAMS=OFF",
        ],
    },
]

DEFAULT_JOBS = (1, 2, 4)


# ── pure helpers (unit-tested) ────────────────────────────────────────────────


def speedup_rows(samples: dict[int, float]) -> list[dict]:
    """Turn ``{jobs: seconds}`` into per-job rows with speedup + efficiency.

    Speedup is relative to the serial (``jobs=1``) baseline; efficiency is
    ``speedup / jobs`` (1.0 == perfect linear scaling). Pure — the live driver
    feeds it measured medians, tests feed it fixed numbers.
    """
    if 1 not in samples:
        raise ValueError("samples must include the jobs=1 serial baseline")
    base = samples[1]
    rows: list[dict] = []
    for jobs in sorted(samples):
        secs = samples[jobs]
        speedup = (base / secs) if secs > 0 else 0.0
        rows.append(
            {
                "jobs": jobs,
                "seconds": round(secs, 2),
                "speedup": round(speedup, 2),
                "efficiency": round(speedup / jobs, 2) if jobs else 0.0,
            }
        )
    return rows


def amdahl_serial_fraction(samples: dict[int, float]) -> float | None:
    """Estimate the serial fraction *f* from the best observed speedup (Amdahl).

    Uses the job level with the highest measured speedup: from
    ``speedup = 1 / (f + (1-f)/n)`` we solve ``f = (n/speedup - 1)/(n - 1)``.
    Returns ``None`` when there is no parallel sample (only ``jobs=1``) or the
    measurement shows no speedup (degenerate/noisy). Clamped to ``[0, 1]``.
    """
    rows = [r for r in speedup_rows(samples) if r["jobs"] > 1 and r["speedup"] > 0]
    if not rows:
        return None
    best = max(rows, key=lambda r: r["speedup"])
    n, s = best["jobs"], best["speedup"]
    if n <= 1 or s <= 0:
        return None
    f = (n / s - 1.0) / (n - 1.0)
    return round(min(1.0, max(0.0, f)), 3)


def render_scaling(payload: dict) -> str:
    """Render the scaling-curve markdown report (SCALING.md)."""
    L: list[str] = []
    L.append(
        "<!-- GENERATED by eval/scaling.py — do not edit by hand. Re-run to refresh. -->"
    )
    L.append("# abicheck field-evaluation — L4 parallel-scaling (C1)\n")
    L.append(f"- generated: `{payload['generated_utc']}`")
    L.append(f"- abicheck: `{payload['abicheck_version']}`")
    L.append(
        f"- host: `{payload['host']['platform']}`, "
        f"{payload['host']['cpus']} CPUs, Python {payload['host']['python']}"
    )
    L.append(f"- reps per point: **{payload['reps']}** (median reported)")
    L.append("")
    L.append(
        "`ABICHECK_L4_JOBS` fans the per-TU clang AST extraction (L4) across a "
        "thread pool; L3 (compile-DB parse), L5 (graph), and serialization stay "
        "serial. The table is *whole-dump* wall time — what a user observes — so "
        "the speedup is Amdahl-bounded by that serial fraction, not the core count."
    )
    L.append("")
    for tree in payload["trees"]:
        if "error" in tree:
            L.append(f"## {tree['label']}\n")
            L.append(f"> skipped/errored: {tree['error']}\n")
            continue
        f = tree.get("serial_fraction")
        f_disp = f"~{f:.0%} serial" if f is not None else "n/a"
        L.append(f"## {tree['label']} — {tree['tus']} TUs ({f_disp})\n")
        L.append("| jobs | dump s | speedup | efficiency |")
        L.append("|---|---|---|---|")
        for r in tree["rows"]:
            L.append(
                f"| {r['jobs']} | {r['seconds']} | {r['speedup']}× | {r['efficiency']} |"
            )
        L.append("")
    L.append(
        "> `speedup` = serial(jobs=1) ÷ this; `efficiency` = speedup ÷ jobs "
        "(1.0 = perfect). `serial fraction` is the Amdahl estimate from the best "
        "speedup. Reproduce: `python eval/scaling.py --jobs 1,2,4`."
    )
    L.append("")
    return "\n".join(L)


# ── live driver (git + cmake + abicheck) ──────────────────────────────────────


def _configure_tree(entry: dict) -> tuple[Path, Path]:
    """Clone + configure one tree; return ``(source_tree, build_dir)``."""
    label = entry["label"]
    key = runner._checkout_key(entry["repo"], entry["tag"])
    tree = runner.SRC_DIR / f"scaling_{label}_{key}"
    runner._git_clone_tag(entry["repo"], entry["tag"], tree)
    cmake_src = tree / entry["cmake_subdir"] if entry.get("cmake_subdir") else tree
    build = runner.BUILD_DIR / f"scaling_{label}_{key}"
    if not (build / "compile_commands.json").exists():
        runner._cmake_configure(cmake_src, build, list(entry.get("cmake_args", [])))
    return tree, build


def _time_dump(
    tree: Path, build: Path, jobs: int, out: Path, executor: str = "thread"
) -> float:
    """One timed ``dump --sources`` at the given ``ABICHECK_L4_JOBS``; seconds.

    ``executor`` selects the L4 extract pool (``thread`` default, ``process`` to
    parallelize the GIL-bound AST post-processing too — see
    ``ABICHECK_L4_EXECUTOR``); compare the two curves to size the GIL win.
    """
    env = dict(os.environ, ABICHECK_L4_JOBS=str(jobs), ABICHECK_L4_EXECUTOR=executor)
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            "abicheck",
            "dump",
            "--sources",
            str(tree),
            "--build-info",
            str(build),
            "--depth",
            "full",
            "-o",
            str(out),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=1800,
    )
    secs = time.perf_counter() - t0
    if proc.returncode or not out.exists():
        raise RuntimeError(f"dump failed (jobs={jobs}): {(proc.stderr or '')[-200:]}")
    return secs


def _tu_count(build: Path) -> int:
    try:
        return len(json.loads((build / "compile_commands.json").read_text()))
    except (OSError, ValueError):
        return 0


def measure_tree(
    entry: dict, jobs_list: list[int], reps: int, executor: str = "thread"
) -> dict:
    """Configure one tree and time the dump at each job level (median of reps)."""
    label = entry["label"]
    try:
        tree, build = _configure_tree(entry)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        return {"label": label, "error": f"clone/configure failed: {str(exc)[:120]}"}
    out = runner.SNAP_DIR / f"scaling_{label}.json"
    runner.SNAP_DIR.mkdir(parents=True, exist_ok=True)
    samples: dict[int, float] = {}
    try:
        for jobs in jobs_list:
            times = [_time_dump(tree, build, jobs, out, executor) for _ in range(reps)]
            samples[jobs] = statistics.median(times)
            print(
                f"  {label:10} jobs={jobs} executor={executor} {samples[jobs]:.1f}s",
                file=sys.stderr,
            )
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        return {"label": label, "error": f"dump failed: {str(exc)[:120]}"}
    return {
        "label": label,
        "tus": _tu_count(build),
        "samples": {str(k): v for k, v in samples.items()},
        "rows": speedup_rows(samples),
        "serial_fraction": amdahl_serial_fraction(samples),
    }


def run(
    trees: list[dict], jobs_list: list[int], reps: int, executor: str = "thread"
) -> dict:
    missing = [t for t in runner._SOURCE_REQUIRED if not runner._have(t)]
    tree_rows: list[dict]
    if missing:
        print(f"  scaling skipped: missing {', '.join(missing)}", file=sys.stderr)
        tree_rows = [
            {"label": t["label"], "error": f"skipped: missing {', '.join(missing)}"}
            for t in trees
        ]
    else:
        tree_rows = [measure_tree(t, jobs_list, reps, executor) for t in trees]
    return {
        "result_schema": SCALING_SCHEMA,
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "abicheck_version": runner._abicheck_version(),
        "host": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpus": os.cpu_count() or 1,
        },
        "jobs": jobs_list,
        "reps": reps,
        "executor": executor,
        "trees": tree_rows,
    }


def _write_results(payload: dict) -> Path:
    runner.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = payload["generated_utc"].replace(":", "").replace("-", "")
    out = runner.RESULTS_DIR / f"scaling-{stamp}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    (runner.RESULTS_DIR / "scaling-latest.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="C1 parallel-L4 scaling harness")
    ap.add_argument(
        "--jobs",
        default=",".join(str(j) for j in DEFAULT_JOBS),
        help="comma-separated ABICHECK_L4_JOBS levels (must include 1)",
    )
    ap.add_argument(
        "--reps", type=int, default=1, help="timed repetitions per point (median)"
    )
    ap.add_argument("--only", help="comma-separated tree labels")
    ap.add_argument(
        "--executor",
        choices=("thread", "process"),
        default="thread",
        help="L4 extract pool: 'process' also parallelizes the GIL-bound AST "
        "post-processing (ABICHECK_L4_EXECUTOR); compare the two curves",
    )
    ap.add_argument(
        "--report-only",
        action="store_true",
        help="re-render SCALING.md from the latest results",
    )
    args = ap.parse_args()

    if args.report_only:
        payload = json.loads(
            (runner.RESULTS_DIR / "scaling-latest.json").read_text(encoding="utf-8")
        )
    else:
        jobs_list = sorted({int(j) for j in args.jobs.split(",")})
        if 1 not in jobs_list:
            ap.error("--jobs must include the serial baseline 1")
        only = set(args.only.split(",")) if args.only else None
        trees = [t for t in DEFAULT_TREES if not (only and t["label"] not in only)]
        payload = run(trees, jobs_list, args.reps, args.executor)
        out = _write_results(payload)
        print(f"results → {out}", file=sys.stderr)
    (_EVAL_DIR / "SCALING.md").write_text(render_scaling(payload), encoding="utf-8")
    print(f"report  → {_EVAL_DIR / 'SCALING.md'}", file=sys.stderr)


if __name__ == "__main__":
    main()
