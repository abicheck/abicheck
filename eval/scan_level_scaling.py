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

"""Scan-*level* scalability sweep over a self-contained synthetic corpus.

``eval/scaling.py`` times one knob (``ABICHECK_L4_JOBS``) on *one* real tree.
This harness sweeps the **other** axis the field eval never automated: how each
``scan`` *level* (``--depth binary|headers|build|source|full`` plus the
``--source-method s4`` graph rung) scales as a project's **complexity** grows —
TU count, per-TU symbol count, and C++ template/STL instantiation depth (the
documented L4 cliff driver, see ``docs/development/performance.md`` §"Scan level
cost model"). Unlike ``scaling.py`` it needs **no network and no real repo**: it
synthesises C++ trees of tunable size, builds them with the host C++ compiler
into a ``.so`` + ``compile_commands.json`` (the JSON-compilation-database shape a
real CMake/Bazel build emits), and runs ``abicheck scan`` at each level against a
slightly-changed baseline.

For every (size, level) it records wall time, **peak child RSS** (via
``os.wait4`` — the true per-call high-water mark, including clang's native
memory), the L4 coverage line (``parsed/total`` TUs), and the verdict. The
output is the per-level scaling curve + a tail exponent, surfacing where a level
goes super-linear or where a *cheaper-looking* level secretly pays a full-tree
cost (e.g. seedless ``--depth source`` runs the L5 call-graph pass over the whole
compile DB even though its L4 replay is scoped to one TU).

Gated on a C++ compiler + ``clang++`` (the L4/L5 source replay backend). With
neither, only the binary/headers tiers run. Manual (not in CI): a full
template-heavy sweep is minutes of clang time.

Reproduce::

    python eval/scan_level_scaling.py                       # default sweep
    python eval/scan_level_scaling.py --sizes 4,8,16 --depth 6
    python eval/scan_level_scaling.py --levels binary,build,source,full
    python eval/scan_level_scaling.py --json-out reports/perf/scan_levels.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import asdict, dataclass
from pathlib import Path

# ── synthetic corpus generator ──────────────────────────────────────────────
# A public header declaring an STL/template-heavy API + N translation units that
# define it. The "old" and "new" variants differ by a handful of additions plus a
# few return-type changes (source-level breaks) so the baseline compare is
# non-trivial. Template depth and STL use are what make clang's per-TU JSON AST
# large — i.e. what the L4 cliff is priced on.

_API_HPP = r"""
#pragma once
#include <vector>
#include <map>
#include <string>
#include <memory>
#include <functional>

namespace api {{

template <int N> struct Recur {{
    using next = Recur<N - 1>;
    typedef std::shared_ptr<typename next::value_t> value_t;
    std::map<std::string, std::vector<typename next::value_t>> table;
    long compute() const {{ return N + next().compute(); }}
}};
template <> struct Recur<0> {{
    using value_t = long;
    long compute() const {{ return 0; }}
}};

class Widget {{
public:
    explicit Widget(int seed);
    ~Widget();
    long process(const std::vector<std::string>& in) const;
    std::map<std::string, long> histogram() const;
{extra_methods}
private:
    std::unique_ptr<std::vector<long>> data_;
    int seed_;
}};

{free_decls}

}} // namespace api
"""

_TU_CPP = """
#include "api.hpp"
namespace api {{
{tu_funcs}
}} // namespace api
"""

_WIDGET_CPP = r"""
#include "api.hpp"
namespace api {{
Widget::Widget(int seed) : data_(new std::vector<long>()), seed_(seed) {{ data_->push_back(seed); }}
Widget::~Widget() {{}}
long Widget::process(const std::vector<std::string>& in) const {{
    long acc = seed_;
    for (auto& s : in) acc += s.size();
    Recur<8> r; acc += r.compute();
    return acc;
}}
std::map<std::string, long> Widget::histogram() const {{
    std::map<std::string, long> h;
    for (auto v : *data_) h[std::to_string(v)]++;
    return h;
}}
{extra_defs}
}} // namespace api
"""


def _gen_sources(
    root: Path, *, n_tus: int, funcs_per_tu: int, depth: int, variant: str
) -> None:
    """Write the header + per-TU sources for one (size, variant) tree."""
    root = root.resolve()
    inc = root / "include"
    src = root / "src"
    (root / "build").mkdir(parents=True, exist_ok=True)
    inc.mkdir(parents=True, exist_ok=True)
    src.mkdir(parents=True, exist_ok=True)

    free_decls: list[str] = []
    extra_methods: list[str] = []
    extra_defs: list[str] = []
    if variant == "new":
        extra_methods.append("    long added_method(int x) const;")
        extra_defs.append(
            "long Widget::added_method(int x) const { return x + seed_; }"
        )
        free_decls.append("int extra_free_fn(int a);")

    total_funcs = n_tus * funcs_per_tu
    for k in range(total_funcs):
        rt = "int" if (variant == "new" and k % 50 == 0) else "long"
        free_decls.append(f"{rt} free_fn_{k}(const std::vector<long>& v);")

    (inc / "api.hpp").write_text(
        _API_HPP.format(
            extra_methods="\n".join(extra_methods),
            free_decls="\n".join(free_decls),
        )
    )
    (src / "widget.cpp").write_text(
        _WIDGET_CPP.format(extra_defs="\n".join(extra_defs))
    )

    fidx = 0
    for t in range(n_tus):
        funcs = []
        for j in range(funcs_per_tu):
            k = fidx
            fidx += 1
            rt = "int" if (variant == "new" and k % 50 == 0) else "long"
            funcs.append(
                textwrap.dedent(f"""
                {rt} free_fn_{k}(const std::vector<long>& v) {{
                    Recur<{depth}> r;
                    long acc = r.compute();
                    std::map<std::string, long> m;
                    for (auto x : v) {{ m[std::to_string(x % {j + 1})] += x; acc += x; }}
                    std::vector<std::string> keys;
                    for (auto& kv : m) keys.push_back(kv.first);
                    return static_cast<{rt}>(acc + keys.size());
                }}""")
            )
        (src / f"tu{t}.cpp").write_text(_TU_CPP.format(tu_funcs="\n".join(funcs)))


def _build(root: Path, *, n_tus: int, cxx: str) -> Path:
    """Compile the tree into ``libsynth.so`` + an absolute-path compile DB."""
    root = root.resolve()
    inc, src, bld = root / "include", root / "src", root / "build"
    cpps = [src / "widget.cpp"] + [src / f"tu{t}.cpp" for t in range(n_tus)]
    cdb, objs = [], []
    for cpp in cpps:
        obj = bld / (cpp.stem + ".o")
        argv = [
            cxx,
            "-std=c++17",
            "-g",
            "-O0",
            "-fPIC",
            f"-I{inc}",
            "-c",
            str(cpp),
            "-o",
            str(obj),
        ]
        cdb.append({"directory": str(root), "arguments": argv, "file": str(cpp)})
        objs.append(obj)
    (root / "compile_commands.json").write_text(json.dumps(cdb, indent=1))
    for entry in cdb:
        proc = subprocess.run(entry["arguments"], capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stderr[:4000])
            raise SystemExit(f"compile failed: {entry['file']}")
    so = root / "libsynth.so"
    link = subprocess.run(
        [cxx, "-shared", "-o", str(so), *[str(o) for o in objs]],
        capture_output=True,
        text=True,
    )
    if link.returncode != 0:
        sys.stderr.write(link.stderr[:4000])
        raise SystemExit("link failed")
    return so


# ── measurement ─────────────────────────────────────────────────────────────
_L4_RE = re.compile(r"(\d+)/(\d+) TUs parsed.*?([\d.]+)s")
_VERDICT_RE = re.compile(r"Verdict:\s*(\w+)")

#: The user-facing levels, in cost order. ``source`` (seedless s5) and
#: ``source_seeded`` (s5 + a one-file ``--changed-path``) are split so the
#: seed's effect — and the unscoped call-graph cost of the seedless run — is
#: visible side by side.
LEVELS = ("binary", "headers", "build", "graph", "source_seeded", "source", "full")

#: Levels that need the clang source-replay backend (skip when clang++ absent).
_NEEDS_CLANG = {"headers", "graph", "source_seeded", "source", "full"}


def _level_args(level: str, seed: str) -> list[str]:
    return {
        "binary": ["--depth", "binary"],
        "headers": ["--depth", "headers"],
        "build": ["--depth", "build"],
        "graph": ["--source-method", "s4"],
        "source": ["--depth", "source"],
        "source_seeded": ["--depth", "source", "--changed-path", seed],
        "full": ["--depth", "full"],
    }[level]


@dataclass
class Point:
    level: str
    n_tus: int
    depth: int
    wall_s: float
    rss_mb: float
    exit: int
    verdict: str | None
    l4_parsed: int | None = None
    l4_total: int | None = None
    l4_secs: float | None = None


def _run_scan(new_root: Path, base_so: Path, level: str, *, jobs: int) -> Point:
    seed = "src/tu0.cpp"
    argv = [
        "abicheck",
        "scan",
        "--binary",
        str(new_root / "libsynth.so"),
        "-H",
        str(new_root / "include"),
        "--sources",
        str(new_root),
        "--baseline",
        str(base_so),
        "--ast-frontend",
        "clang",
        "--format",
        "text",
        *_level_args(level, seed),
    ]
    env = dict(os.environ, ABICHECK_L4_JOBS=str(jobs))
    t0 = time.monotonic()
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, text=True
    )
    assert proc.stdout is not None
    out = proc.stdout.read()
    _pid, status, ru = os.wait4(proc.pid, 0)
    wall = time.monotonic() - t0
    l4 = _L4_RE.search(out)
    verdict = _VERDICT_RE.search(out)
    return Point(
        level=level,
        n_tus=0,
        depth=0,
        wall_s=round(wall, 2),
        rss_mb=round(ru.ru_maxrss / 1024, 1),  # KiB -> MiB (Linux)
        exit=os.waitstatus_to_exitcode(status),
        verdict=verdict.group(1) if verdict else None,
        l4_parsed=int(l4.group(1)) if l4 else None,
        l4_total=int(l4.group(2)) if l4 else None,
        l4_secs=float(l4.group(3)) if l4 else None,
    )


def _tail_exponent(sizes: list[int], values: list[float]) -> float | None:
    """Log-log slope of the largest two points — the portable scaling signal."""
    pts = [(s, v) for s, v in zip(sizes, values) if s > 0 and v > 0]
    if len(pts) < 2:
        return None
    (s0, v0), (s1, v1) = pts[-2], pts[-1]
    if s0 == s1:
        return None
    return round(math.log(v1 / v0) / math.log(s1 / s0), 2)


# ── driver ──────────────────────────────────────────────────────────────────
def run_sweep(
    *,
    sizes: list[int],
    funcs_per_tu: int,
    depth: int,
    levels: list[str],
    jobs: int,
    cxx: str,
    have_clang: bool,
    workdir: Path,
) -> list[Point]:
    points: list[Point] = []
    for n in sizes:
        old = workdir / f"old_n{n}_f{funcs_per_tu}_d{depth}"
        new = workdir / f"new_n{n}_f{funcs_per_tu}_d{depth}"
        for root, variant in ((old, "old"), (new, "new")):
            if not (root / "libsynth.so").exists():
                _gen_sources(
                    root,
                    n_tus=n,
                    funcs_per_tu=funcs_per_tu,
                    depth=depth,
                    variant=variant,
                )
                _build(root, n_tus=n, cxx=cxx)
        print(f"\n=== n_tus={n} funcs/tu={funcs_per_tu} depth={depth} ===", flush=True)
        for level in levels:
            if level in _NEEDS_CLANG and not have_clang:
                print(f"  {level:14s} SKIP (clang++ not found)")
                continue
            p = _run_scan(
                new.resolve(), (old / "libsynth.so").resolve(), level, jobs=jobs
            )
            p.n_tus, p.depth = n, depth
            points.append(p)
            l4 = (
                f" L4={p.l4_parsed}/{p.l4_total}@{p.l4_secs}s"
                if p.l4_parsed is not None
                else ""
            )
            print(
                f"  {level:14s} wall={p.wall_s:7.2f}s rss={p.rss_mb:8.1f}MB "
                f"exit={p.exit} {p.verdict}{l4}",
                flush=True,
            )
    return points


def render_table(points: list[Point]) -> str:
    """A per-level Markdown table with the wall/RSS tail exponents."""
    sizes = sorted({p.n_tus for p in points})
    levels = [lv for lv in LEVELS if any(p.level == lv for p in points)]
    lines = [
        "| level | " + " | ".join(f"n={s}" for s in sizes) + " | wall exp | rss exp |",
        "|---|" + "---|" * (len(sizes) + 2),
    ]
    for lv in levels:
        by_size = {p.n_tus: p for p in points if p.level == lv}
        walls = [by_size[s].wall_s if s in by_size else 0.0 for s in sizes]
        rsses = [by_size[s].rss_mb if s in by_size else 0.0 for s in sizes]
        cells = [f"{w:.1f}s/{r:.0f}M" for w, r in zip(walls, rsses)]
        we = _tail_exponent(sizes, walls)
        re_ = _tail_exponent(sizes, rsses)
        lines.append(
            f"| {lv} | "
            + " | ".join(cells)
            + f" | {we if we is not None else '—'} "
            + f"| {re_ if re_ is not None else '—'} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sizes", default="4,8,16", help="comma-separated TU counts")
    ap.add_argument("--funcs-per-tu", type=int, default=8)
    ap.add_argument("--depth", type=int, default=6, help="template instantiation depth")
    ap.add_argument("--levels", default=",".join(LEVELS))
    ap.add_argument(
        "--jobs", type=int, default=0, help="ABICHECK_L4_JOBS (0 = leave unset / auto)"
    )
    ap.add_argument("--cxx", default=os.environ.get("CXX", "g++"))
    ap.add_argument("--workdir", default="", help="tree cache dir (default: tempdir)")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    if not shutil.which(args.cxx):
        print(
            f"error: C++ compiler {args.cxx!r} not found (set --cxx/CXX)",
            file=sys.stderr,
        )
        return 2
    have_clang = shutil.which("clang++") is not None
    if not have_clang:
        print(
            "warning: clang++ not found — only binary/build tiers will run",
            file=sys.stderr,
        )

    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    levels = [lv for lv in args.levels.split(",") if lv.strip()]
    jobs = args.jobs or (os.cpu_count() or 4)
    workdir = (
        Path(args.workdir)
        if args.workdir
        else Path(tempfile.mkdtemp(prefix="abicheck-scan-scaling-"))
    )

    points = run_sweep(
        sizes=sizes,
        funcs_per_tu=args.funcs_per_tu,
        depth=args.depth,
        levels=levels,
        jobs=jobs,
        cxx=args.cxx,
        have_clang=have_clang,
        workdir=workdir,
    )
    print("\n" + render_table(points))
    if args.json_out:
        Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json_out).write_text(
            json.dumps([asdict(p) for p in points], indent=2)
        )
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
