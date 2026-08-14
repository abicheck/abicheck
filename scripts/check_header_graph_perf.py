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

"""Header-only (L2) semantic graph performance gate (G31 Phase D).

G31 Phase A made the header-only graph (``abicheck/buildsource/header_graph.py``)
unconditional: every ``dump``/``compare`` that parses headers now pays its
attach cost on every run, not just when a user explicitly opted in via the
now-retired ``--header-graph`` flag. G31 Phase C's in-process AST memoization
(``dumper_cache.py``) closed the worst case of that cost (a second, redundant
``clang -ast-dump=json`` subprocess when the main snapshot pass already used
``--ast-frontend clang``) but the cost is not zero: the default ``castxml``
backend never populates that memo, so ``service._attach_header_graph`` still
pays a genuine second ``clang`` invocation on every dump, on top of whatever
memoized/graph-construction overhead remains on the clang-frontend path.

This script isolates and measures that attach cost directly, sweeping a
synthetic header-size axis across **both** header backends, and gates on
regression against a documented baseline — the same discipline
``check_fp_rate.py``/``check_tier_accuracy.py`` apply to correctness and
``benchmark_scaling.py`` applies to comparison scaling.

Method: for every (size, backend, repeat) combination, build a *fresh* tiny
real ELF ``.so`` + synthetic public header of size *N* declarations in its
own temp directory — never reused across repeats or backends, since the AST
disk-cache key is keyed on each header's resolved path (not its content), so
a shared fixture would let a later measurement silently hit a disk-cache
entry an earlier one already wrote for the identical path, understating the
genuine cold-invocation cost this gate exists to catch. Then time:

* ``baseline_ms`` — ``dumper.dump(so, [header], header_backend=...)``, which
  does **not** itself attach the header graph (see
  ``abicheck/buildsource/CLAUDE.md``'s ``header_graph.py`` row: the attach
  step lives in ``service.py``, not ``dumper.py``), run inside
  ``dumper_cache.ast_memoize_scope()`` — the exact scope
  ``service._run_dump_uncached`` wraps its own primary dump call in, so a
  ``clang``-backend run's in-process AST memo is actually written the same
  way it would be in production (a direct, unscoped ``dumper.dump()`` call
  never populates it — see ``dumper_cache.py``'s own
  ``_ast_memoize_scope`` docstring).
* ``attach_ms`` — ``service._attach_header_graph(snap, header_graph=True,
  header_graph_includes=True, ...)``
  applied to the snapshot ``baseline_ms`` already produced, *outside* that
  scope (again matching ``_run_dump_uncached``'s own call ordering), in
  isolation. Both flags are ``True``, matching production's
  ``_HEADER_GRAPH_ENABLED``/``_HEADER_GRAPH_INCLUDES_ENABLED`` (both
  unconditionally on since G31 Phase A) — the second flag drives
  ``ClangHeaderIncludeExtractor``'s own extra ``clang -M`` subprocess per
  top-level header, a real, always-on part of the attach cost this gate
  would otherwise silently exclude.

For the ``clang`` backend this measures the real Phase C in-process memo
handoff (should be cheap: a dict lookup + graph construction, no second
subprocess) plus the include-graph pass. For the ``castxml`` backend — the
default L2 backend, and the one most `dump`/`compare` invocations actually
use — the primary pass never writes to the memo (only ``dumper_clang.py``'s
``_clang_header_dump`` does), so ``attach_ms`` there is the genuine second
``clang -ast-dump=json`` subprocess every default-backend dump now pays,
plus the same include-graph pass. Reports both backends' points; the
``castxml`` backend's points are simply omitted (not an error) when
``castxml`` isn't installed. Every attach is verified
(``_require_real_ast_attach``) to have actually stamped a real clang-AST
pass rather than silently degrading to a declaration-only fallback graph
(``service._attach_header_graph`` never raises on a failed clang invocation
— it degrades instead, which would otherwise read as a *fast* measurement
for what is really a broken one) — a degraded attach aborts the run loudly
instead of recording a misleading data point.

Requires ``clang``/``clang++`` and ``g++`` on ``PATH`` (Linux/ELF only,
matching ``tests/test_clang_header_backend_integration.py``'s own scope);
self-skips (exit 0) when unavailable so this never blocks a host without a
C++ toolchain. ``castxml`` is optional by default — its points are just
skipped (with a printed ``SKIP:``/``NOTE:``) when it's absent or rejected by
abicheck's own version gate (``UnsupportedCastxmlVersionError``), the whole
run isn't. Pass ``--require-castxml`` to turn that off for a caller that
knows it *should* have a working pinned castxml (e.g. a CI job that just
installed one via ``action/install-castxml.sh``): there, a missing or
out-of-policy castxml means the install/policy regressed, not that the tool
is merely an optional local convenience, so the run fails loudly instead of
silently narrowing the sweep to the ``clang`` backend alone. The two failure
shapes print distinct message prefixes so a caller can tell them apart:
``FAIL: castxml version rejected by this build's policy: ...`` specifically
for ``UnsupportedCastxmlVersionError`` (a version-policy mismatch), and
``FAIL: header extraction failed: ...`` for any other extraction error (a
timeout, a crash, malformed output) -- a genuine regression, never treated
as skippable.

Usage::

    # Measure and print a table (report-only without --baseline):
    python scripts/check_header_graph_perf.py

    # Establish/refresh the committed baseline:
    python scripts/check_header_graph_perf.py --json-out reports/perf/header_graph.json

    # Gate a PR run against the committed baseline:
    python scripts/check_header_graph_perf.py --baseline reports/perf/header_graph.json \\
        --regress-tolerance 0.5

    # CI callers that installed a pinned castxml: fail rather than skip if
    # it's missing or out-of-policy:
    python scripts/check_header_graph_perf.py --require-castxml
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# Prefer an already-importable `abicheck` over the checkout sitting next to
# this script, inserting the repo root only as a fallback -- NOT
# unconditionally. This is what lets this exact script file be pointed at a
# *different* installed abicheck than the one physically next to it:
# performance.yml's `header-graph-regression` job runs THIS copy (the PR
# head's) against both the head venv's and the base venv's separately-
# installed packages, so both sides are measured with the identical
# harness/statistics (Codex review on PR #768 — see benchmark_scaling.py's
# own, more detailed comment on the identical pattern for the full
# reasoning). A bare `python scripts/check_header_graph_perf.py` with no
# install still works exactly as before.
if importlib.util.find_spec("abicheck") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Sibling module (this script's own directory) — the shared median/percentile/
# regression-threshold math this script and benchmark_scaling.py both use.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from perf_measurement import (  # noqa: E402
    combined_regression_threshold,
    summarize_samples,
)

# Unlike ``check_mutation_score.py``'s ``SURVIVOR_BASELINE`` (a single
# in-module constant), the baseline here is a per-size JSON report on disk
# (``--json-out``/``--baseline``) since it's a curve, not a scalar. A run
# with no ``--baseline`` is report-only — the same "not yet established"
# convention, just file-shaped instead of a module constant. See the module
# docstring's Usage section for how to establish and then gate against one.
DEFAULT_SIZES: tuple[int, ...] = (25, 100, 400)
DEFAULT_REPEAT = 3
DEFAULT_REGRESS_TOLERANCE = 0.5  # attach_ms may grow at most 50% vs. baseline
#: Absolute-ms floor combined with DEFAULT_REGRESS_TOLERANCE via max() — see
#: perf_measurement.combined_regression_threshold. 0.0 preserves the
#: historical pure-percentage-tolerance behaviour by default.
DEFAULT_REGRESS_MIN_DELTA_MS = 0.0


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _synthesize_header(n: int) -> str:
    """A synthetic public header with *n* structs and *n* free functions.

    Deliberately simple, self-contained declarations (no templates, no
    inheritance) — the point is to scale the *count* of declarations the
    header-graph attach step has to walk, not to exercise any one parsing
    edge case (those are covered by the correctness test suite instead).
    """
    lines = ["#pragma once", "namespace hgperf {", ""]
    for i in range(n):
        lines.append(f"struct S{i} {{ int a; double b; S{i}* next; }};")
    for i in range(n):
        lines.append(f"int fn{i}(const S{i}& in, S{i}* out);")
    lines.append("")
    lines.append("}  // namespace hgperf")
    return "\n".join(lines) + "\n"


def _synthesize_source(n: int) -> str:
    lines = ['#include "api.h"', "namespace hgperf {", ""]
    for i in range(n):
        lines.append(
            f"int fn{i}(const S{i}& in, S{i}* out) {{ *out = in; return in.a; }}"
        )
    lines.append("")
    lines.append("}  // namespace hgperf")
    return "\n".join(lines) + "\n"


def _build_fixture(tmp_dir: Path, n: int) -> tuple[Path, Path]:
    """Compile a real ELF ``.so`` + write its header for size *n*. Returns
    ``(so_path, header_path)``.

    Called once per (size, backend, repeat) inside a fresh temp directory
    (see ``_measure_one``) — the resolved header *path* is part of the AST
    disk-cache key (``dumper_ast_config._cache_key`` hashes each header's
    resolved path + mtime, not its content), so a fresh directory alone is
    enough to guarantee a cold cache on every call, with no need to vary the
    header content itself (Codex review: a shared fixture reused across
    repeats/backends let the *second* backend's/repeat's measurement hit a
    disk-cache entry the *first* one's attach step had already written for
    the identical header path, understating the genuine cold-invocation
    cost this gate exists to catch).
    """
    header = tmp_dir / "api.h"
    header.write_text(_synthesize_header(n))
    src = tmp_dir / "api.cpp"
    src.write_text(_synthesize_source(n))
    so = tmp_dir / "libhgperf.so"
    proc = subprocess.run(
        ["g++", "-shared", "-fPIC", "-O0", "-o", str(so), str(src), f"-I{tmp_dir}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # check=True alone raises CalledProcessError without printing the
        # compiler's own diagnostics, leaving a fixture-build failure as a
        # bare non-zero-exit traceback in CI logs (CodeRabbit review).
        raise RuntimeError(
            f"g++ failed to build the size={n} fixture (exit {proc.returncode}):\n"
            f"{proc.stderr}"
        )
    return so, header


#: Backends this gate can measure, in the order reported. ``castxml`` is
#: dropped from the sweep (not the whole run) when the tool isn't installed.
BACKENDS: tuple[str, ...] = ("clang", "castxml")


def _resolve_includes(
    header: Path,
) -> tuple[list[Path], tuple[str, ...], tuple[Path, ...]]:
    """Replicate ``service._dump_elf``'s own inferred-include-root resolution.

    Both the primary dump and ``service._attach_header_graph`` must resolve
    to the *identical* ``extra_includes``/``gcc_option_tokens`` for their
    respective ``_clang_header_dump`` calls to land on the same AST
    disk-cache key (and, on the ``clang`` backend, the same in-process memo
    slot) — ``resolve_inferred_header_roots`` is a pure function of
    ``headers``/``user_includes``/``gcc_options``/``gcc_option_tokens``, so
    calling it once here with the same arguments both the real ``dump()``
    call below and ``_attach_header_graph``'s own internal call use (empty
    user includes, default ``CompileContext``) gives both call sites the
    same answer without threading a shared value between them (Codex
    review: passing no ``extra_includes`` to ``dump()`` at all — the
    previous shape of this function — left the primary pass's cache key
    diverged from the attach step's own internally-resolved one, so a
    ``clang``-backend attach could never actually hit the memo the primary
    pass wrote).
    """
    from abicheck.header_utils import deferred_token_dirs, resolve_inferred_header_roots

    inc_extra, deferred = resolve_inferred_header_roots(
        [header], [], gcc_options=None, gcc_option_tokens=()
    )
    return inc_extra, tuple(deferred), tuple(deferred_token_dirs(deferred))


def _require_real_ast_attach(snap: Any, n: int, backend: str) -> None:
    """Raise unless *snap*'s attached graph reflects a genuine clang AST parse.

    ``service._attach_header_graph`` never raises on a failed/unavailable
    clang invocation — it silently degrades to a declaration-only graph
    (ADR-028 D3 "never abort collection"), which is the right call for a real
    dump, but wrong for *this* benchmark: a regression that outright breaks
    the second clang invocation would make that call return almost
    instantly, reading as a performance *improvement* rather than the
    functional break it actually is (Codex review). ``header_graph.py``'s
    own comment is explicit that a real AST-backed pass always stamps both
    ``HEADER_CALL_GRAPH_PASS``/``HEADER_TYPE_GRAPH_PASS`` together
    ("reaching this line means the whole pass ran cleanly"); the degraded
    fallback branch never stamps ``HEADER_CALL_GRAPH_PASS`` at all. Checking
    for it is therefore a precise, already-load-bearing signal — not a new
    parallel bookkeeping mechanism — that this measurement's ``attach_ms``
    reflects the real second clang invocation, for either backend.

    Also requires ``HEADER_INCLUDE_GRAPH_PASS`` to have landed in
    ``extractor_passes`` (a clean pass), not merely ``degraded_passes`` (a
    partial one) — since the attach call always requests the include-graph
    pass too (its own ``clang -M`` subprocess per top-level header), a
    regression that breaks *only* that pass while the main AST parse still
    succeeds would otherwise return a shorter, silently-degraded sample that
    reads as a performance win (Codex review, fresh evidence: this is a
    distinct failure mode from the AST-parse one above, not covered by
    checking ``HEADER_CALL_GRAPH_PASS`` alone).
    """
    from abicheck.buildsource.header_graph import (
        HEADER_CALL_GRAPH_PASS,
        HEADER_INCLUDE_GRAPH_PASS,
    )

    graph = getattr(getattr(snap, "build_source", None), "source_graph", None)
    passes = getattr(graph, "extractor_passes", {}) if graph is not None else {}
    if not passes.get(HEADER_CALL_GRAPH_PASS):
        raise RuntimeError(
            f"size={n} backend={backend}: _attach_header_graph degraded to a "
            "declaration-only graph (HEADER_CALL_GRAPH_PASS not stamped) instead "
            "of a genuine clang AST attach -- this measurement's attach_ms would "
            "understate the real cost (or mask a regression that broke the "
            "second clang invocation entirely). Aborting rather than recording "
            "a misleading data point."
        )
    if not passes.get(HEADER_INCLUDE_GRAPH_PASS):
        raise RuntimeError(
            f"size={n} backend={backend}: the include-graph pass "
            "(HEADER_INCLUDE_GRAPH_PASS) did not complete cleanly -- either it "
            "degraded (a partial `clang -M` failure) or never ran at all. This "
            "measurement's attach_ms would understate the real always-on include-"
            "graph cost. Aborting rather than recording a misleading data point."
        )


def _measure_one(n: int, backend: str, repeat: int) -> dict[str, Any]:
    """Time *repeat* (dump, attach) pairs, one freshly-built fixture apiece.

    Mirrors ``service._run_dump_uncached``'s own call shape: the primary
    dump runs inside ``dumper_cache.ast_memoize_scope()``, and
    ``_attach_header_graph`` runs after that scope exits — the in-process AST
    memo (G31 Phase C) is a per-thread slot that outlives the scope until a
    reader pops it, so this ordering is what actually lets a ``clang``-backend
    attach hit the memo instead of a disk-cache reload (Codex review: calling
    ``dumper.dump()`` with no surrounding scope, as an earlier version of this
    script did, never writes the memo at all).

    Runs one untimed warmup pair (its own fresh temp dir, same as every timed
    repeat — it doesn't skip ``_build_fixture``'s deliberate per-repeat
    AST-disk-cache-miss design) before the *repeat* timed pairs, then reports
    the **median** of the timed samples via
    ``perf_measurement.summarize_samples`` — not the minimum (see that
    module's own docstring for why "keep the fastest" hides regressions).
    """
    from abicheck import dumper_cache
    from abicheck.compile_context import CompileContext
    from abicheck.dumper import dump
    from abicheck.service import _attach_header_graph

    def _one_pair() -> tuple[float, float]:
        with tempfile.TemporaryDirectory(prefix="hgperf_") as tmp:
            so, header = _build_fixture(Path(tmp), n)
            inc_extra, deferred_tokens, extra_hash_dirs = _resolve_includes(header)

            t0 = time.perf_counter()
            with dumper_cache.ast_memoize_scope():
                snap = dump(
                    so,
                    [header],
                    extra_includes=inc_extra,
                    header_backend=backend,
                    # None, not "c++": production normalizes the default
                    # C++ request to None for both this call and the
                    # _attach_header_graph call below (service.py's
                    # `lang if lang == "c" else None`) -- lang is part of
                    # _clang_header_dump's AST cache key, so passing the
                    # unnormalized "c++" here exercised a different cache
                    # key than the one a real default-C++ dump computes
                    # (Codex review). Auto-detection (_detect_cpp_headers)
                    # still resolves this fixture as C++ correctly, since
                    # its synthesized `namespace hgperf { ... }` is one of
                    # the structural C++ patterns it matches.
                    lang=None,
                    gcc_option_tokens=deferred_tokens,
                    extra_hash_dirs=extra_hash_dirs,
                )
            t1 = time.perf_counter()

            t2 = time.perf_counter()
            attached = _attach_header_graph(
                snap,
                # Keyword, not positional -- two adjacent bare `True`s made
                # this call fragile to a silent reordering (CodeRabbit
                # review). Production (service._run_dump_uncached) always
                # passes _HEADER_GRAPH_INCLUDES_ENABLED (a module constant,
                # True since G31 Phase A) alongside header_graph, not just
                # the bare AST-attach flag -- that second flag drives
                # ClangHeaderIncludeExtractor's own extra `clang -M`
                # subprocess per top-level header, a real, always-on part of
                # the production attach cost this benchmark must include too
                # (Codex review: passing False here silently excluded it
                # from every measurement).
                header_graph=True,
                header_graph_includes=True,
                headers=[header],
                includes=[],
                # None, matching the primary dump() call above -- see its
                # own comment (Codex review).
                lang=None,
                compile=CompileContext(),
                public_headers=None,
                public_header_dirs=None,
            )
            t3 = time.perf_counter()
            _require_real_ast_attach(attached, n, backend)
            return (t1 - t0) * 1000.0, (t3 - t2) * 1000.0

    _one_pair()  # untimed warmup — discarded
    baseline_samples: list[float] = []
    attach_samples: list[float] = []
    for _ in range(repeat):
        baseline_ms, attach_ms = _one_pair()
        baseline_samples.append(baseline_ms)
        attach_samples.append(attach_ms)

    baseline_stats = summarize_samples(baseline_samples)
    attach_stats = summarize_samples(attach_samples)
    return {
        "baseline_ms": baseline_stats.median,
        "attach_ms": attach_stats.median,
        "baseline_ms_samples": baseline_samples,
        "attach_ms_samples": attach_samples,
        "baseline_ms_cv": baseline_stats.cv,
        "attach_ms_cv": attach_stats.cv,
    }


def _measure_size(
    n: int,
    repeat: int,
    backends: tuple[str, ...],
    *,
    require_castxml: bool = False,
) -> list[dict[str, Any]]:
    from abicheck.errors import SnapshotError, UnsupportedCastxmlVersionError

    points = []
    for backend in backends:
        try:
            result = _measure_one(n, backend, repeat)
        except SnapshotError as exc:
            # Only the one narrow, genuinely-optional condition self-skips:
            # a castxml build present on PATH but rejected by abicheck's own
            # version gate (measure()'s _have("castxml") filter already
            # excludes a fully-absent castxml before this point is ever
            # attempted, so reaching here with any *other* SnapshotError --
            # a timeout, a crash, malformed output -- on an otherwise-
            # supported castxml install is a real extraction regression, not
            # an expected condition (Codex review, fresh evidence: the
            # previous "any SnapshotError on castxml" catch was still too
            # broad -- it also swallowed those). Same for clang, the backend
            # main() already required before measure() was ever called:
            # silently dropping either kind of real failure would let
            # main() see only the points that did succeed and potentially
            # still report a clean "OK".
            #
            # --require-castxml (set by callers that explicitly installed a
            # pinned castxml, e.g. the header-graph-perf/-regression CI jobs
            # via action/install-castxml.sh) additionally forbids even the
            # narrow UnsupportedCastxmlVersionError skip: there, a version
            # rejection means the pinned installer or the version gate
            # itself regressed, not "castxml is an optional local tool" --
            # silently dropping the whole default-backend axis would let a
            # gating run report OK having compared nothing on it (Codex
            # review, fresh evidence).
            if (
                backend != "castxml"
                or not isinstance(exc, UnsupportedCastxmlVersionError)
                or require_castxml
            ):
                raise
            print(f"SKIP: size={n} backend={backend}: {exc}")
            continue
        points.append({"size": n, "backend": backend, **result})
    return points


def measure(
    sizes: tuple[int, ...],
    repeat: int,
    backends: tuple[str, ...] = BACKENDS,
    *,
    require_castxml: bool = False,
) -> list[dict[str, Any]]:
    # "clang" needs no external optional tool (already required by main()'s
    # own SKIP check before measure() is ever called); every other backend
    # is gated on its own name being on PATH, not hardcoded to "castxml" --
    # BACKENDS only ever holds these two today, but keying the check on the
    # actual backend name rather than a specific one keeps this correct if
    # a third backend is ever added (CodeRabbit review). require_castxml
    # bypasses this presence filter entirely for "castxml" -- if it's
    # required, a missing tool must surface as a hard failure (main()'s own
    # SKIP path only ever checks clang/clang++/g++), not a silently
    # narrowed sweep.
    active = tuple(b for b in backends if b == "clang" or require_castxml or _have(b))
    points: list[dict[str, Any]] = []
    for n in sizes:
        points.extend(_measure_size(n, repeat, active, require_castxml=require_castxml))
    return points


def _point_key(p: dict[str, Any]) -> tuple[int, str]:
    return int(p["size"]), str(p.get("backend", "clang"))


def _load_baseline(path: Path) -> dict[tuple[int, str], float]:
    data = json.loads(path.read_text())
    points = data if isinstance(data, list) else data.get("points", [])
    return {_point_key(p): float(p["attach_ms"]) for p in points}


def matched_points(
    points: list[dict[str, Any]], baseline: dict[tuple[int, str], float]
) -> list[dict[str, Any]]:
    """The subset of *points* that have a corresponding *baseline* entry.

    Used to distinguish "every point checked out fine" from "the baseline
    covered nothing this run measured" — a size/backend axis change (or a
    stale/mistargeted ``--baseline`` file) makes ``check_regressions``
    return an empty failure list either way, and printing ``OK`` for that
    case would be a false-confidence silent pass (Codex review): a
    baseline containing only size 999 would let a completely unchecked
    default 25/100/400 run report success.

    Excludes an entry whose baseline value is non-positive, matching
    ``check_regressions``'s own ``base is None or base <= 0`` skip
    condition (CodeRabbit review) -- otherwise the final "N checked" count
    in ``main()`` would include a point ``check_regressions`` never
    actually gated.
    """
    return [p for p in points if baseline.get(_point_key(p), 0.0) > 0]


def check_regressions(
    points: list[dict[str, Any]],
    baseline: dict[tuple[int, str], float],
    tolerance: float,
    *,
    min_delta_ms: float = DEFAULT_REGRESS_MIN_DELTA_MS,
) -> list[str]:
    """Return one message per (size, backend) that regressed beyond *tolerance*.

    A point regresses once ``attach_ms`` exceeds the baseline by more than
    ``max(tolerance * baseline, min_delta_ms)`` — see
    ``perf_measurement.combined_regression_threshold``. ``min_delta_ms=0``
    (the default) reduces to the historical pure-percentage-tolerance rule.
    """
    failures = []
    for p in points:
        base = baseline.get(_point_key(p))
        if base is None or base <= 0:
            continue
        current = float(p["attach_ms"])
        allowed_delta = combined_regression_threshold(base, tolerance, min_delta_ms)
        allowed = base + allowed_delta
        if current > allowed:
            pct = (current / base - 1.0) * 100.0
            size, backend = _point_key(p)
            failures.append(
                f"size={size} backend={backend}: attach_ms {current:.1f} > "
                f"baseline {base:.1f} + {allowed_delta:.1f} allowed ({pct:+.0f}%)"
            )
    return failures


def _attach_cv_pct(p: dict[str, Any]) -> float:
    """``attach_ms``'s coefficient of variation as a percentage, or NaN if unset."""
    cv = p.get("attach_ms_cv")
    return cv * 100.0 if cv is not None else float("nan")


def _print_table(points: list[dict[str, Any]]) -> None:
    print(
        f"{'size':>8} {'backend':>9} {'baseline_ms':>12} {'attach_ms':>12} "
        f"{'attach_%':>10} {'attach_cv%':>11}"
    )
    for p in points:
        baseline_ms = p["baseline_ms"]
        attach_ms = p["attach_ms"]
        pct = (attach_ms / baseline_ms * 100.0) if baseline_ms else float("nan")
        print(
            f"{p['size']:>8} {p.get('backend', 'clang'):>9} {baseline_ms:>12.1f} "
            f"{attach_ms:>12.1f} {pct:>9.1f}% {_attach_cv_pct(p):>10.1f}%"
        )


def _print_markdown(points: list[dict[str, Any]]) -> None:
    print("| size | backend | baseline_ms | attach_ms | attach_% | attach_cv% |")
    print("|---:|---|---:|---:|---:|---:|")
    for p in points:
        baseline_ms = p["baseline_ms"]
        attach_ms = p["attach_ms"]
        pct = (attach_ms / baseline_ms * 100.0) if baseline_ms else float("nan")
        print(
            f"| {p['size']} | {p.get('backend', 'clang')} | {baseline_ms:.1f} | "
            f"{attach_ms:.1f} | {pct:.1f}% | {_attach_cv_pct(p):.1f}% |"
        )


def _positive_int(value: str) -> int:
    """``argparse`` ``type=`` for ``--sizes``/``--repeat``: reject <= 0.

    A ``--repeat 0`` left ``baseline_samples``/``attach_samples`` empty,
    so ``min()`` inside ``_measure_one`` raised an unhandled ``ValueError``;
    a ``--sizes 0`` entry built a fixture with no declarations at all and
    recorded a meaningless baseline point (CodeRabbit review). Rejecting at
    parse time gives a clear ``argparse`` usage error instead of either.
    """
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value!r}")
    return parsed


def _finite_nonnegative_float(value: str) -> float:
    """``argparse`` ``type=`` for ``--regress-tolerance``: reject non-finite/negative.

    ``check_regressions()`` computes ``allowed = base * (1.0 + tolerance)`` and
    fails only when ``current > allowed`` -- a ``nan`` tolerance makes that
    comparison always ``False`` (``float`` comparisons against ``nan`` are
    never true), and an ``inf`` tolerance makes ``allowed`` infinite, so both
    let an arbitrarily regressed measurement print ``OK`` instead of failing
    (Codex review, fresh evidence). A negative tolerance is also meaningless
    here (it would demand *faster* than baseline just to pass). Rejecting all
    three at parse time gives a clear ``argparse`` usage error instead of a
    silently-neutered gate.
    """
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(
            f"must be a finite, non-negative number, got {value!r}"
        )
    return parsed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--sizes",
        type=_positive_int,
        nargs="+",
        default=list(DEFAULT_SIZES),
        help="Declaration counts to sweep (default: %(default)s)",
    )
    p.add_argument(
        "--repeat",
        type=_positive_int,
        default=DEFAULT_REPEAT,
        help="Timed repeats per size (plus one untimed warmup); the MEDIAN is "
        "reported (default: %(default)s)",
    )
    p.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Path to a previously written --json-out report to gate against",
    )
    p.add_argument(
        "--regress-tolerance",
        type=_finite_nonnegative_float,
        default=DEFAULT_REGRESS_TOLERANCE,
        help="Fractional attach_ms growth allowed vs. baseline before failing "
        "(default: %(default)s = 50%%). The actual allowed delta is "
        "max(this fraction x baseline, --regress-min-delta-ms).",
    )
    p.add_argument(
        "--regress-min-delta-ms",
        type=_finite_nonnegative_float,
        default=DEFAULT_REGRESS_MIN_DELTA_MS,
        help="Absolute-ms floor combined with --regress-tolerance via max() -- "
        "protects a small attach_ms baseline from flagging on run-to-run "
        "noise alone (default: %(default)s = pure percentage tolerance, the "
        "historical behaviour).",
    )
    p.add_argument("--json-out", type=Path, default=None, help="Write a JSON report")
    p.add_argument(
        "--markdown",
        action="store_true",
        help="Print a Markdown table instead of a plain one",
    )
    p.add_argument(
        "--require-castxml",
        action="store_true",
        help="Fail (instead of silently skipping) if castxml is missing or "
        "rejected by abicheck's version gate -- for callers (e.g. CI jobs) "
        "that explicitly installed a pinned castxml, where that condition "
        "means the install/policy regressed, not that the tool is merely "
        "an optional local convenience",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not (_have("clang") and _have("clang++") and _have("g++")):
        print(
            "SKIP: clang/clang++/g++ not all found on PATH — nothing to measure "
            "(the header-only graph attach step needs a real clang install)."
        )
        return 0
    if not sys.platform.startswith("linux"):
        print(
            "SKIP: header-graph perf gate is Linux/ELF-scoped (see module docstring)."
        )
        return 0

    if not _have("castxml") and not args.require_castxml:
        print(
            "NOTE: castxml not found on PATH — measuring the clang backend only "
            "(the default castxml backend's genuine second-clang-invocation cost "
            "is not covered by this run)."
        )

    # Every repeat deliberately forces a cache *miss* (see _build_fixture's
    # docstring), so every one of those parses writes a real, never-reused
    # entry into the persistent AST cache (~/.cache/abi_check or platform
    # equivalent) that nothing then cleans up — repeated runs, especially at
    # larger --sizes, accumulate real disk usage there for no benefit (Codex
    # review). Redirect XDG_CACHE_HOME (the same env var dumper_cache._cache_path
    # already honors) to a throwaway directory for the run's lifetime instead.
    # Windows has no equivalent env-var override in _cache_path, so this is a
    # best-effort mitigation there, matching the module's Linux/ELF scope.
    from abicheck.errors import SnapshotError, UnsupportedCastxmlVersionError

    with tempfile.TemporaryDirectory(prefix="hgperf_cache_") as cache_dir:
        old_xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
        os.environ["XDG_CACHE_HOME"] = cache_dir
        try:
            points = measure(
                tuple(args.sizes), args.repeat, require_castxml=args.require_castxml
            )
        except UnsupportedCastxmlVersionError as exc:
            # Only reachable with --require-castxml (without it, _measure_size
            # already turns this specific error into a SKIP+continue) -- a
            # clean FAIL here, not an unhandled traceback, matching this
            # script's existing --baseline-read-error convention. Kept as its
            # own except clause, with its own distinct message prefix, so a
            # caller (e.g. header-graph-regression's base-measurement step)
            # can tell a genuine version-policy rejection apart from any
            # *other* SnapshotError below -- a castxml timeout, a crash, or
            # malformed output is a real extraction regression, not an
            # optional/skippable condition, and must not be mistaken for one
            # by string-matching a shared message (Codex review, fresh
            # evidence: an earlier version of this used one shared message
            # for both, which made that distinction impossible from the
            # printed output alone).
            print(f"\nFAIL: castxml version rejected by this build's policy: {exc}")
            return 1
        except SnapshotError as exc:
            # Any other castxml/clang extraction failure under
            # --require-castxml (a timeout, a crash, malformed output) --
            # deliberately NOT the same message prefix as the version-policy
            # FAIL above, so the two are never conflated by a caller matching
            # on the printed text.
            print(f"\nFAIL: header extraction failed: {exc}")
            return 1
        finally:
            if old_xdg_cache_home is None:
                os.environ.pop("XDG_CACHE_HOME", None)
            else:
                os.environ["XDG_CACHE_HOME"] = old_xdg_cache_home

    if args.markdown:
        _print_markdown(points)
    else:
        _print_table(points)

    # Load the baseline BEFORE writing --json-out: the two may name the same
    # path (e.g. re-running the exact command used to establish the baseline
    # with --baseline added), and writing first would silently overwrite the
    # historical baseline with this run's own numbers before it's read --
    # every point then trivially matches itself and the gate reports OK
    # having destroyed the one file that could have shown a regression
    # (Codex review).
    baseline = None
    if args.baseline is not None:
        try:
            baseline = _load_baseline(args.baseline)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # An unreadable/malformed --baseline (missing file, invalid
            # JSON, a report missing "attach_ms") previously propagated as
            # an unhandled traceback -- indistinguishable in a CI log from a
            # genuine crash in this script itself, rather than a gate
            # failure with a clear cause (CodeRabbit review).
            print(f"\nFAIL: could not read --baseline {args.baseline}: {exc}")
            return 1

    # Never overwrite the file --baseline was just read from, regression or
    # not: even with the read-before-write ordering above, writing this run's
    # own (possibly regressed) numbers over the historical baseline destroys
    # the one reference a *later* run needs to catch the same regression
    # again -- the next invocation would just compare the regressed numbers
    # against themselves (Codex review, fresh evidence: this survives even
    # though the read-before-write fix makes THIS run's own verdict correct).
    same_path = (
        args.json_out is not None
        and args.baseline is not None
        and args.json_out.resolve() == args.baseline.resolve()
    )
    if args.json_out is not None and not same_path:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps({"points": points}, indent=2) + "\n")
        print(f"\nWrote {args.json_out}")
    elif same_path:
        print(
            f"\nNOTE: --json-out and --baseline both name {args.json_out} — not "
            "overwriting the baseline this run was gated against. Write the "
            "report to a different path (or omit --json-out) when refreshing "
            "the committed baseline."
        )

    if baseline is None:
        print(
            "\nNo --baseline given: report-only run. Pass a previously written "
            "--json-out report via --baseline to gate future runs against it."
        )
        return 0

    matched = matched_points(points, baseline)
    if not matched:
        print(
            f"\nFAIL: --baseline {args.baseline} has no entry matching any of this "
            f"run's {len(points)} (size, backend) point(s) — nothing was actually "
            "gated. Check that --sizes/the backends measured match what the "
            "baseline was generated with, or regenerate it via --json-out."
        )
        return 1
    failures = check_regressions(
        points,
        baseline,
        args.regress_tolerance,
        min_delta_ms=args.regress_min_delta_ms,
    )
    if failures:
        print("\nFAIL: header-graph attach-cost regression:")
        for f in failures:
            print(f"  - {f}")
        return 1
    unmatched = len(points) - len(matched)
    if unmatched:
        print(
            f"\nNOTE: {unmatched} point(s) had no matching baseline entry and were "
            "not gated (new size/backend not yet in the baseline)."
        )
    print(
        f"\nOK: no header-graph attach-cost regression vs. baseline ({len(matched)} checked)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
