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

"""Reproducible memory/time benchmark for a header-depth multi-library compare.

Why a harness rather than ``/usr/bin/time -v``: the four figures a memory
investigation needs are four different numbers (see
:mod:`abicheck.memory_trace`'s docstring), and ``time -v``'s
``Maximum resident set size`` is only one of them -- the *parent's*. A
concurrency change moves the process tree without moving it; a cgroup OOM is
decided on a third number it never reports.

So this samples, for the whole run:

* parent peak RSS (``/proc/<pid>/statm``),
* process-tree peak RSS *and* PSS (``smaps_rollup`` over the live tree --
  summed RSS double-counts shared pages, PSS does not),
* cgroup ``memory.current`` peak, the figure a nominal-16-GB runner's limit
  is enforced against,

and, in a **separate** opt-in run (``--tracemalloc``), Python allocation
totals. Those are never collected in the same run as a timing: tracemalloc
changes both the time and the RSS it would be reported next to.

Usage::

    # build a six-library C++ release fixture once, then measure variants
    python scripts/bench_release_memory.py --out bench.json
    python scripts/bench_release_memory.py --members 6 --apis 1800 \\
        --variants json,junit,bundle-facts --repeat 3 --out bench.json

The fixture is a real compiled C++ release (``g++ -shared -g`` per member,
headers parsed by the configured header-AST backend), not a synthetic
in-memory object graph: the amplification under investigation is in what the
*real* extraction retains. ``--keep`` reuses a previously built fixture so
before/after measurements at two SHAs compare identical operands.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from abicheck.workflows import memory_trace  # noqa: E402
except ImportError:  # pragma: no cover - running against a pre-instrumentation SHA
    # This harness is deliberately runnable at a *base* revision too, so a
    # before/after pair is measured by one script over one fixture rather
    # than by two scripts whose sampling could differ. Without the module
    # there is simply no trace to write and no cgroup helper to borrow.
    memory_trace = None  # type: ignore[assignment]

ENV_TRACE_PATH = getattr(memory_trace, "ENV_TRACE_PATH", "ABICHECK_MEMORY_TRACE")
ENV_TRACEMALLOC = getattr(
    memory_trace, "ENV_TRACEMALLOC", "ABICHECK_MEMORY_TRACE_TRACEMALLOC"
)


def _cgroup_memory() -> tuple[int | None, int | None]:
    if memory_trace is not None:
        return memory_trace._cgroup_memory()
    return (None, None)


_PAGE = 4096


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


def _member_sources(
    index: int, apis: int, records: int, *, new: bool
) -> tuple[str, str]:
    """Header and translation unit for one member.

    The NEW side of member 0 carries the two real changes the release must
    keep detecting, so a memory variant that silently dropped evidence fails
    the verdict check rather than only the byte comparison:

    * a **changed public record** (an inserted field shifts every following
      member's offset -- a layout break), and
    * a **removed export**.
    """
    ns = f"lib{index}"
    h = [
        "#pragma once",
        "#include <cstddef>",
        "#include <string>",
        "#include <vector>",
        f"namespace {ns} {{",
    ]
    for r in range(records):
        h.append(f"struct Rec{r} {{")
        h.append("  int a;")
        if new and index == 0 and r == 0:
            # The changed public record: an inserted field.
            h.append("  int inserted_field;")
        h.append("  double b;")
        h.append("  char pad[8];")
        h.append("};")
        h.append(f"template <typename T> struct Box{r} {{ T value; T get() const; }};")
        h.append(f"enum class E{r} {{ Zero, One, Two }};")
    for a in range(apis):
        if new and index == 0 and a == 0:
            # The removed export: declared and defined on OLD only.
            continue
        h.append(f"int api_{a}(const Rec{a % max(records, 1)}& r, int x);")
    h.append("std::string describe(const std::vector<int>& v);")
    h.append("}")
    header = "\n".join(h) + "\n"

    c = [f'#include "{ns}.hpp"', f"namespace {ns} {{"]
    for a in range(apis):
        if new and index == 0 and a == 0:
            continue
        c.append(
            f"int api_{a}(const Rec{a % max(records, 1)}& r, int x) "
            "{ return r.a + x; }"
        )
    c.append(
        "std::string describe(const std::vector<int>& v) "
        "{ return std::to_string(v.size()); }"
    )
    c.append("}")
    return header, "\n".join(c) + "\n"


def build_fixture(root: Path, members: int, apis: int, records: int) -> None:
    """Compile a two-sided, many-member C++ release under *root*."""
    for side in ("old", "new"):
        src = root / side / "src"
        inc = root / side / "include"
        lib = root / side / "lib"
        for d in (src, inc, lib):
            d.mkdir(parents=True, exist_ok=True)
        for i in range(members):
            header, unit = _member_sources(i, apis, records, new=(side == "new"))
            (inc / f"lib{i}.hpp").write_text(header, encoding="utf-8")
            (src / f"lib{i}.cpp").write_text(unit, encoding="utf-8")
            cmd = [
                os.environ.get("CXX", "g++"),
                "-std=c++17",
                "-shared",
                "-fPIC",
                "-g",
                "-O0",
                f"-I{inc}",
                str(src / f"lib{i}.cpp"),
                "-o",
                str(lib / f"libmember{i}.so"),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True)
            if proc.returncode != 0:
                raise SystemExit(
                    f"fixture compile failed for {side}/lib{i}:\n{proc.stderr}"
                )


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------


class _Sampler(threading.Thread):
    """Poll a running process tree's memory until told to stop.

    Sampling rather than a single final reading: the peak of a release
    fan-out is inside the run, not at its end, and a child compiler process
    that has already exited contributes nothing to a post-hoc measurement.
    """

    def __init__(self, pid: int, interval: float = 0.05) -> None:
        super().__init__(daemon=True)
        self.pid = pid
        self.interval = interval
        self.stop = threading.Event()
        self.parent_peak = 0
        self.tree_rss_peak = 0
        self.tree_pss_peak = 0
        self.cgroup_peak = 0
        self.max_processes = 0
        self.samples = 0

    def _tree(self) -> list[int]:
        seen: list[int] = []
        pending = [self.pid]
        while pending:
            pid = pending.pop()
            if pid in seen:
                continue
            seen.append(pid)
            try:
                for task in Path(f"/proc/{pid}/task").iterdir():
                    try:
                        raw = (task / "children").read_text(encoding="ascii")
                    except OSError:
                        continue
                    pending.extend(int(t) for t in raw.split())
            except OSError:
                continue
        return seen

    def run(self) -> None:
        while not self.stop.is_set():
            pids = self._tree()
            self.max_processes = max(self.max_processes, len(pids))
            rss_total = pss_total = 0
            for pid in pids:
                rss = pss = 0
                try:
                    with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as fh:
                        for line in fh:
                            if line.startswith("Rss:"):
                                rss = int(line.split()[1]) * 1024
                            elif line.startswith("Pss:"):
                                pss = int(line.split()[1]) * 1024
                except (OSError, ValueError, IndexError):
                    continue
                rss_total += rss
                pss_total += pss
                if pid == self.pid:
                    self.parent_peak = max(self.parent_peak, rss)
            self.tree_rss_peak = max(self.tree_rss_peak, rss_total)
            self.tree_pss_peak = max(self.tree_pss_peak, pss_total)
            cg_current, cg_peak = _cgroup_memory()
            for value in (cg_current, cg_peak):
                if value is not None:
                    self.cgroup_peak = max(self.cgroup_peak, value)
            self.samples += 1
            self.stop.wait(self.interval)


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------

#: name -> extra CLI arguments, as a function of the output directory.
VARIANTS: dict[str, object] = {
    "json": lambda out: ["-o", f"json={out / 'report.json'}"],
    "junit": lambda out: [
        "-o",
        f"json={out / 'report.json'}",
        "-o",
        f"junit={out / 'report.xml'}",
    ],
    "bundle-facts": lambda out: [
        "-o",
        f"json={out / 'report.json'}",
        "--bundle-facts-out",
        str(out / "baseline.json"),
    ],
    "junit+bundle-facts": lambda out: [
        "-o",
        f"json={out / 'report.json'}",
        "-o",
        f"junit={out / 'report.xml'}",
        "--bundle-facts-out",
        str(out / "baseline.json"),
    ],
}


def run_variant(
    root: Path,
    name: str,
    out: Path,
    *,
    trace: Path | None,
    tracemalloc: bool,
    cache_dir: Path | None,
    env_extra: dict[str, str] | None = None,
) -> dict[str, object]:
    out.mkdir(parents=True, exist_ok=True)
    args = [
        sys.executable,
        "-m",
        "abicheck",
        "compare",
        str(root / "old" / "lib"),
        str(root / "new" / "lib"),
        "-H",
        f"old={root / 'old' / 'include'}",
        "-H",
        f"new={root / 'new' / 'include'}",
    ]
    args += VARIANTS[name](out)  # type: ignore[operator]

    env = dict(os.environ)
    env.update(env_extra or {})
    if trace is not None:
        env[ENV_TRACE_PATH] = str(trace)
        if tracemalloc:
            env[ENV_TRACEMALLOC] = "1"
    else:
        env.pop(ENV_TRACE_PATH, None)
        env.pop(ENV_TRACEMALLOC, None)
    if cache_dir is not None:
        env["ABICHECK_CACHE_DIR"] = str(cache_dir)

    started = time.monotonic()
    proc = subprocess.Popen(
        args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    sampler = _Sampler(proc.pid)
    sampler.start()
    stdout, stderr = proc.communicate()
    sampler.stop.set()
    sampler.join(2)
    elapsed = time.monotonic() - started

    report_path = out / "report.json"
    verdict = None
    findings = None
    if report_path.exists():
        try:
            doc = json.loads(report_path.read_text(encoding="utf-8"))
            verdict = doc.get("verdict") or doc.get("release", {}).get("verdict")
            findings = len(doc.get("findings") or [])
        except ValueError:
            pass
    return {
        "variant": name,
        "exit_code": proc.returncode,
        "verdict": verdict,
        "findings": findings,
        "seconds": round(elapsed, 3),
        "parent_peak_rss_bytes": sampler.parent_peak,
        "tree_peak_rss_bytes": sampler.tree_rss_peak,
        "tree_peak_pss_bytes": sampler.tree_pss_peak,
        "cgroup_peak_bytes": sampler.cgroup_peak,
        "max_processes": sampler.max_processes,
        "samples": sampler.samples,
        "tracemalloc": tracemalloc,
        "stderr_tail": stderr.decode("utf-8", "replace")[-2000:],
        "stdout_tail": stdout.decode("utf-8", "replace")[-500:],
    }


def _mib(value: object) -> float | None:
    return round(int(value) / (1024 * 1024), 1) if isinstance(value, int) else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=Path("/tmp/abicheck-bench-fixture"))
    ap.add_argument("--members", type=int, default=6)
    ap.add_argument("--apis", type=int, default=300)
    ap.add_argument("--records", type=int, default=20)
    ap.add_argument("--variants", default="json,junit,bundle-facts")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument(
        "--cold",
        action="store_true",
        help="clear the AST/snapshot cache before each run (cold extraction)",
    )
    ap.add_argument("--trace", type=Path, default=None, help="write a memory trace")
    ap.add_argument(
        "--tracemalloc",
        action="store_true",
        help="Python-allocation profiling run; never combine with a timing claim",
    )
    ap.add_argument("--keep", action="store_true", help="reuse an existing fixture")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--label", default="")
    args = ap.parse_args(argv)

    root: Path = args.root
    if not args.keep or not (root / "old" / "lib").exists():
        if root.exists():
            shutil.rmtree(root)
        print(
            f"building fixture: {args.members} members x {args.apis} APIs "
            f"x {args.records} records ...",
            file=sys.stderr,
        )
        build_fixture(root, args.members, args.apis, args.records)

    cache_dir = root / "cache"
    results: list[dict[str, object]] = []
    for name in args.variants.split(","):
        name = name.strip()
        if name not in VARIANTS:
            raise SystemExit(f"unknown variant {name!r}; known: {sorted(VARIANTS)}")
        for attempt in range(args.repeat):
            if args.cold and cache_dir.exists():
                shutil.rmtree(cache_dir)
            out = root / "out" / f"{name}-{attempt}"
            if out.exists():
                shutil.rmtree(out)
            trace = args.trace
            if trace is not None:
                trace = trace.with_name(f"{trace.stem}-{name}-{attempt}{trace.suffix}")
            row = run_variant(
                root,
                name,
                out,
                trace=trace,
                tracemalloc=args.tracemalloc,
                cache_dir=cache_dir,
            )
            row["attempt"] = attempt
            row["cold"] = bool(args.cold)
            results.append(row)
            print(
                f"{name:<20} run {attempt}  exit={row['exit_code']}  "
                f"{row['seconds']:>7}s  parent={_mib(row['parent_peak_rss_bytes'])} MiB  "
                f"tree_rss={_mib(row['tree_peak_rss_bytes'])} MiB  "
                f"tree_pss={_mib(row['tree_peak_pss_bytes'])} MiB",
                file=sys.stderr,
            )

    summary: dict[str, object] = {
        "label": args.label,
        "python": sys.version.split()[0],
        "members": args.members,
        "apis": args.apis,
        "records": args.records,
        "cold": bool(args.cold),
        "runs": results,
    }
    by_variant: dict[str, object] = {}
    for name in {str(r["variant"]) for r in results}:
        rows = [r for r in results if r["variant"] == name]
        by_variant[name] = {
            "median_seconds": statistics.median(float(r["seconds"]) for r in rows),
            "median_parent_peak_mib": statistics.median(
                float(r["parent_peak_rss_bytes"]) / (1024 * 1024) for r in rows
            ),
            "median_tree_pss_peak_mib": statistics.median(
                float(r["tree_peak_pss_bytes"]) / (1024 * 1024) for r in rows
            ),
            "exit_codes": sorted({int(r["exit_code"]) for r in rows}),  # type: ignore[arg-type]
        }
    summary["by_variant"] = by_variant
    text = json.dumps(summary, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
