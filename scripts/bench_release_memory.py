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

#: Env-var names, spelled here rather than imported. This harness is
#: deliberately runnable at a *base* revision that predates
#: `abicheck.workflows.memory_trace`, so a before/after pair is measured by
#: one script over one fixture rather than by two scripts whose sampling
#: could differ -- and an import that only sometimes resolves must not
#: decide what the harness is called with.
ENV_TRACE_PATH = "ABICHECK_MEMORY_TRACE"
ENV_TRACEMALLOC = "ABICHECK_MEMORY_TRACE_TRACEMALLOC"


def _memory_trace() -> object | None:
    """The instrumentation module, or ``None`` at a revision without it.

    Resolved on demand, never at import: this module is imported by its own
    tests, and mutating ``sys.path`` (or failing an import) at import time
    is a process-wide side effect a caller did not ask for.
    """
    try:
        from abicheck.workflows import memory_trace
    except ImportError:  # pragma: no cover - a pre-instrumentation revision
        return None
    return memory_trace


def _cgroup_memory() -> tuple[int | None, int | None]:
    module = _memory_trace()
    if module is None:
        return (None, None)
    return module._cgroup_memory()  # type: ignore[attr-defined,no-any-return]


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
    members = None
    if report_path.exists():
        try:
            doc = json.loads(report_path.read_text(encoding="utf-8"))
            verdict = doc.get("verdict")
            # A directory/package `compare` reports per member, not at the
            # top level: the release's own findings live under each library
            # entry, so counting `doc["findings"]` would read 0 on a run
            # that found every injected break. Getting this wrong in the
            # *validator* would be worse than not validating, since it
            # would reject correct runs.
            libraries = doc.get("libraries") or []
            members = len(libraries)
            findings = sum(len(lib.get("findings") or []) for lib in libraries)
        except (ValueError, AttributeError):
            pass
    return {
        "variant": name,
        "exit_code": proc.returncode,
        "verdict": verdict,
        "findings": findings,
        "members": members,
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


#: Records the parameters a kept fixture was built with, so `--keep` cannot
#: silently reuse a differently-shaped tree and label it with the new
#: numbers -- which would make a before/after pair compare two fixtures.
FIXTURE_MANIFEST = "fixture.json"


def _fixture_matches(root: Path, wanted: dict[str, int]) -> bool:
    """Whether *root* holds a complete fixture built with *wanted*."""
    if not (root / "old" / "lib").exists() or not (root / "new" / "lib").exists():
        return False
    try:
        recorded = json.loads((root / FIXTURE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if recorded != wanted:
        return False
    # Complete, not merely present: a build interrupted part-way leaves some
    # members compiled and the manifest already written on a later run.
    for side in ("old", "new"):
        built = sorted((root / side / "lib").glob("libmember*.so"))
        if len(built) != wanted["members"]:
            return False
    return True


def _require_a_measured_comparison(row: dict[str, object]) -> None:
    """Refuse a run that did not actually compare anything.

    A failed extraction, a malformed report or a crashed member produces a
    *fast, small* run -- which is exactly what a memory benchmark would
    otherwise record as an improvement. The fixture injects one changed
    public record and one removed export, so the only acceptable outcome is
    the breaking verdict and its findings.
    """
    if row["exit_code"] != 4:
        raise SystemExit(
            f"{row['variant']}: expected exit 4 (the injected break), got "
            f"{row['exit_code']}. Not recording a measurement of a run that "
            f"did not compare.\n{row['stderr_tail']}"
        )
    if row["verdict"] != "BREAKING":
        raise SystemExit(
            f"{row['variant']}: the release verdict is {row['verdict']!r}, "
            "not BREAKING. Not recording a measurement of a run that did not "
            "detect the injected break."
        )
    findings = row["findings"]
    if not isinstance(findings, int) or findings <= 0:
        raise SystemExit(
            f"{row['variant']}: the members carry {findings!r} findings in "
            "total, so the comparison did not produce the injected break. "
            "Not recording."
        )


def _mib(value: object) -> float | None:
    return round(int(value) / (1024 * 1024), 1) if isinstance(value, int) else None


def _build_parser() -> argparse.ArgumentParser:
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
    return ap


def _prepare_fixture(args: argparse.Namespace) -> None:
    """Build or validate-and-reuse the compiled fixture under ``--root``."""
    wanted = {"members": args.members, "apis": args.apis, "records": args.records}
    if args.keep and _fixture_matches(args.root, wanted):
        print(f"reusing fixture at {args.root}", file=sys.stderr)
        return
    if args.root.exists():
        shutil.rmtree(args.root)
    print(
        f"building fixture: {args.members} members x {args.apis} APIs "
        f"x {args.records} records ...",
        file=sys.stderr,
    )
    build_fixture(args.root, args.members, args.apis, args.records)
    (args.root / FIXTURE_MANIFEST).write_text(json.dumps(wanted), encoding="utf-8")


def _summarize(
    args: argparse.Namespace, results: list[dict[str, object]]
) -> dict[str, object]:
    """Fold the per-run rows into the receipt.

    ``median_seconds`` is ``None`` for a ``--tracemalloc`` run: tracemalloc
    perturbs both time and RSS, so such a run publishes no timing at all
    rather than a number a reader might compare against an ordinary one.
    """
    by_variant: dict[str, object] = {}
    for name in {str(r["variant"]) for r in results}:
        rows = [r for r in results if r["variant"] == name]
        by_variant[name] = {
            "median_seconds": None
            if args.tracemalloc
            else statistics.median(float(r["seconds"]) for r in rows),
            "median_parent_peak_mib": statistics.median(
                float(r["parent_peak_rss_bytes"]) / (1024 * 1024) for r in rows
            ),
            "median_tree_pss_peak_mib": statistics.median(
                float(r["tree_peak_pss_bytes"]) / (1024 * 1024) for r in rows
            ),
            "exit_codes": sorted({int(r["exit_code"]) for r in rows}),  # type: ignore[arg-type]
        }
    return {
        "label": args.label,
        "python": sys.version.split()[0],
        "members": args.members,
        "apis": args.apis,
        "records": args.records,
        "cold": bool(args.cold),
        "runs": results,
        "by_variant": by_variant,
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.tracemalloc and args.trace is None:
        raise SystemExit(
            "--tracemalloc needs --trace: tracemalloc is enabled inside the "
            "measured process by the trace gate, so without a trace path it "
            "records nothing while the receipt would still claim it did."
        )

    root: Path = args.root
    _prepare_fixture(args)

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
                # The trace path is deterministic and the tracer appends, so
                # re-running the same command would interleave this run's
                # samples with the previous one's under one file.
                trace.unlink(missing_ok=True)
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
            _require_a_measured_comparison(row)
            results.append(row)
            print(
                f"{name:<20} run {attempt}  exit={row['exit_code']}  "
                f"{row['seconds']:>7}s  parent={_mib(row['parent_peak_rss_bytes'])} MiB  "
                f"tree_rss={_mib(row['tree_peak_rss_bytes'])} MiB  "
                f"tree_pss={_mib(row['tree_peak_pss_bytes'])} MiB",
                file=sys.stderr,
            )

    text = json.dumps(_summarize(args, results), indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
