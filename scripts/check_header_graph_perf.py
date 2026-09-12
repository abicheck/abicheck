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

* ``dump_ms`` — ``dumper.dump(so, [header], header_backend=...)``, which
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
  applied to the snapshot ``dump_ms`` already produced, *outside* that
  scope (again matching ``_run_dump_uncached``'s own call ordering), in
  isolation. Both flags are ``True``, matching production's
  ``_HEADER_GRAPH_ENABLED``/``_HEADER_GRAPH_INCLUDES_ENABLED`` (both
  unconditionally on since G31 Phase A) — the second flag drives
  ``ClangHeaderIncludeExtractor``'s own extra ``clang -M`` subprocess per
  top-level header, a real, always-on part of the attach cost this gate
  would otherwise silently exclude.

* ``total_ms`` — the **same sample's** whole ``dump`` + ``attach`` window,
  read off one ``perf_counter`` span (the ``t0``/``t3`` pair bracketing both
  phases), not reconstructed afterwards. It is deliberately *not*
  ``median(dump_ms) + median(attach_ms)``: two independent medians are two
  different samples' values, so their sum names a run that never happened and
  systematically understates the spread a reader would use to judge noise.
  Nothing extra is executed to obtain it — it reuses the timestamps the two
  phase measurements already take, so adding this metric cost zero additional
  ``dump`` calls.

  **Scope boundary, stated because the name invites over-reading:**
  ``total_ms`` is the total of *these two in-process phases only*. It excludes
  Python interpreter startup, CLI argument/config resolution, input
  resolution, snapshot serialization, comparison, and report rendering — i.e.
  it is not a full ``abicheck`` CLI wall time and must never be quoted as
  one. The full-CLI L2 figure has its own harness
  (``scripts/check_l2_cli_perf.py``); see ``docs/contribute/performance.md``
  for the three distinct measurement levels and which number belongs to
  which.

All three metrics are gated **separately** against the baseline. Gating only
``attach_ms`` (this script's original behaviour) measured the main dump on
every single sample and then threw the number away at gate time: a 2x
regression in the primary header-AST extraction path — by far the larger of
the two costs, and the one most product changes actually touch — printed a
larger ``dump_ms`` in the table and still exited ``0``. Separate gates also
distinguish *which* phase moved, which a single combined number cannot.

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
    GateThreshold,
    finite_nonnegative_float_arg,
    is_gateable,
    positive_int_arg,
    resolve_threshold,
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
DEFAULT_REGRESS_TOLERANCE = 0.5  # a gated metric may grow at most 50% vs. baseline
#: Absolute-ms floor combined with DEFAULT_REGRESS_TOLERANCE via max() — see
#: perf_measurement.combined_regression_threshold. 0.0 preserves the
#: historical pure-percentage-tolerance behaviour by default.
DEFAULT_REGRESS_MIN_DELTA_MS = 0.0

#: The three independently-gated metrics, in report order. ``dump_ms`` is the
#: primary header-AST extraction pass, ``attach_ms`` the always-on header-graph
#: attach layered on top of it, and ``total_ms`` the one ``perf_counter`` span
#: covering both (see the module docstring for why it is measured rather than
#: summed from the two medians, and for the scope it does *not* cover).
#:
#: Order matters only for presentation. Every one of them is gated: this tuple
#: replaced a hard-coded single ``"attach_ms"`` gate, and a metric being
#: *measured but ungated* is the specific defect that replacement fixes, so a
#: metric added here without a gate would reintroduce it.
METRICS: tuple[str, ...] = ("dump_ms", "attach_ms", "total_ms")

#: Legacy key a pre-schema-2 report used for what is now ``dump_ms``. Read as
#: an alias so the PR-vs-base CI job (which measures the *base* branch with the
#: base branch's own copy of this script) keeps gating the dump phase across
#: the one commit that renames it, instead of hard-failing on a baseline it
#: cannot be expected to have written in the new shape. ``total_ms`` has no
#: such alias -- a pre-schema-2 report genuinely never measured it, so it is
#: reported as ungated rather than reconstructed by summing, which is exactly
#: the bad arithmetic ``total_ms`` exists to avoid.
LEGACY_METRIC_ALIASES: dict[str, str] = {"dump_ms": "baseline_ms"}

#: Report schema. ``2`` is the three-metric shape; ``1`` (implicit, unstamped)
#: was ``baseline_ms``/``attach_ms`` with only the latter gated.
REPORT_SCHEMA = "abicheck-header-graph-perf/2"


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
            # total_ms is (t3 - t0): ONE span over this very sample's dump and
            # attach, taken from timestamps the two phase measurements already
            # produced. No third call, no extra dump -- adding the metric cost
            # nothing to execute. It is deliberately not computed later as
            # median(dump) + median(attach): those two medians generally come
            # from different samples, so their sum describes a run that never
            # occurred (and, being a sum of two robust centres, understates the
            # real joint spread a reader judges noise by).
            #
            # It also intentionally includes the small inter-phase gap (the
            # t1..t2 window) rather than excluding it: the gap is real wall time
            # a production dump also pays, and dropping it would make total_ms
            # an exact sum of the two phases, i.e. carry no information the two
            # already carry.
            #
            # _require_real_ast_attach runs *after* t3 -- correctness validation
            # is this harness's own work, not the user-facing path, so it must
            # never land inside a timed window.
            _require_real_ast_attach(attached, n, backend)
            return {
                "dump_ms": (t1 - t0) * 1000.0,
                "attach_ms": (t3 - t2) * 1000.0,
                "total_ms": (t3 - t0) * 1000.0,
            }

    _one_pair()  # untimed warmup — discarded
    samples: dict[str, list[float]] = {m: [] for m in METRICS}
    for _ in range(repeat):
        one = _one_pair()
        for metric in METRICS:
            samples[metric].append(one[metric])

    result: dict[str, Any] = {}
    for metric in METRICS:
        stats = summarize_samples(samples[metric])
        result[metric] = stats.median
        result[f"{metric}_samples"] = samples[metric]
        result[f"{metric}_cv"] = stats.cv
    return result


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


def _load_baseline(path: Path) -> dict[tuple[int, str], dict[str, float]]:
    """Read a report into ``(size, backend) -> {metric: baseline_ms}``.

    Per-metric, not a single scalar: the gate now checks three independent
    metrics, and folding them into one number at load time is what made the
    original single-``attach_ms`` gate impossible to extend without silently
    dropping the other two.

    Only :func:`is_gateable` values are retained. A ``NaN``/``Infinity``
    baseline (both of which ``json.load`` accepts and happily round-trips) is
    dropped rather than kept, because ``current > base + allowed`` is ``False``
    for either -- a single poisoned number would otherwise turn this gate into
    an unconditional pass that still prints ``OK``. Dropping it makes the point
    read as *ungated*, which :func:`main` then surfaces (and, when nothing at
    all survives, fails on) instead.

    ``baseline_ms`` is accepted as an alias for ``dump_ms`` -- see
    :data:`LEGACY_METRIC_ALIASES`.
    """
    data = json.loads(path.read_text())
    points = data if isinstance(data, list) else data.get("points", [])
    out: dict[tuple[int, str], dict[str, float]] = {}
    for p in points:
        metrics: dict[str, float] = {}
        for metric in METRICS:
            value = p.get(metric)
            if value is None:
                alias = LEGACY_METRIC_ALIASES.get(metric)
                if alias is not None:
                    value = p.get(alias)
            if is_gateable(value):
                metrics[metric] = float(value)
        out[_point_key(p)] = metrics
    return out


def gateable_metrics(
    point: dict[str, Any],
    baseline: dict[tuple[int, str], dict[str, float]],
    metrics: tuple[str, ...] = METRICS,
) -> list[str]:
    """Which of *metrics* can actually be gated for *point*.

    A metric is gateable only when **both** sides are real, finite, positive
    numbers. Requiring it of the *measured* side too is not redundant defensive
    coding: a point carrying ``attach_ms = nan`` (a malformed hand-edited
    report, a point assembled by a caller that mis-keyed a field) compares
    ``False`` against any threshold, so it would pass the gate while having
    measured nothing -- the same silent-pass shape a ``nan`` baseline causes,
    from the other direction.
    """
    base = baseline.get(_point_key(point), {})
    return [
        m for m in metrics if is_gateable(base.get(m)) and is_gateable(point.get(m))
    ]


def matched_points(
    points: list[dict[str, Any]],
    baseline: dict[tuple[int, str], dict[str, float]],
    metrics: tuple[str, ...] = METRICS,
) -> list[dict[str, Any]]:
    """The subset of *points* with at least one gateable metric.

    Used to distinguish "every point checked out fine" from "the baseline
    covered nothing this run measured" — a size/backend axis change (or a
    stale/mistargeted ``--baseline`` file) makes ``check_regressions``
    return an empty failure list either way, and printing ``OK`` for that
    case would be a false-confidence silent pass (Codex review): a
    baseline containing only size 999 would let a completely unchecked
    default 25/100/400 run report success.

    "At least one gateable metric" (rather than "key present in the
    baseline") is what keeps this aligned with what
    :func:`check_regressions` really gates, now that a point can be present
    yet carry no usable number for any metric -- a non-positive, absent or
    non-finite value on either side.
    """
    return [p for p in points if gateable_metrics(p, baseline, metrics)]


def ungated_metrics(
    points: list[dict[str, Any]],
    baseline: dict[tuple[int, str], dict[str, float]],
    metrics: tuple[str, ...] = METRICS,
) -> list[str]:
    """One message per (point, metric) pair that could *not* be gated.

    The counterpart of :func:`check_regressions`: a gate that reports only its
    failures cannot distinguish "three metrics, all fine" from "one metric
    fine, two silently unmeasurable". :func:`main` prints these, and
    ``--require-all-metrics`` promotes them to failures for a caller that
    knows the baseline should be complete.
    """
    out: list[str] = []
    for p in points:
        base = baseline.get(_point_key(p), {})
        size, backend = _point_key(p)
        for metric in metrics:
            if is_gateable(base.get(metric)) and is_gateable(p.get(metric)):
                continue
            if _point_key(p) not in baseline:
                reason = "no baseline entry for this (size, backend)"
            elif not is_gateable(base.get(metric)):
                reason = (
                    f"baseline {metric}={base.get(metric)!r} is not a gateable value"
                )
            else:
                reason = f"measured {metric}={p.get(metric)!r} is not a gateable value"
            out.append(f"size={size} backend={backend} metric={metric}: {reason}")
    return out


def check_regressions(
    points: list[dict[str, Any]],
    baseline: dict[tuple[int, str], dict[str, float]],
    thresholds: dict[str, GateThreshold],
) -> list[str]:
    """Return one message per (size, backend, metric) that regressed.

    A metric regresses once its measured median exceeds its own baseline by
    more than that metric's ``GateThreshold.allowed_delta`` — see
    ``perf_measurement.combined_regression_threshold``.

    Each metric in *thresholds* is checked independently, which is the whole
    point: a dump-only, an attach-only and a total-only slowdown each fail on
    their own metric and name it, instead of one combined number that a
    reader then has to guess the cause of. A metric absent from *thresholds*
    is not gated at all, so the caller's ``--metrics`` selection is honored
    here rather than re-derived.
    """
    failures = []
    for p in points:
        base_metrics = baseline.get(_point_key(p), {})
        size, backend = _point_key(p)
        for metric, threshold in thresholds.items():
            base = base_metrics.get(metric)
            current = p.get(metric)
            if not (is_gateable(base) and is_gateable(current)):
                # Reported by ungated_metrics() instead -- skipping silently
                # here would be the original silent-pass bug in miniature.
                continue
            base = float(base)
            current = float(current)
            allowed_delta = threshold.allowed_delta(base)
            if current > base + allowed_delta:
                pct = (current / base - 1.0) * 100.0
                failures.append(
                    f"size={size} backend={backend}: {metric} {current:.1f} > "
                    f"baseline {base:.1f} + {allowed_delta:.1f} allowed ({pct:+.0f}%) "
                    f"[tolerance={threshold.tolerance} min_delta_ms={threshold.min_delta} "
                    f"source={threshold.source}]"
                )
    return failures


def _cv_pct(p: dict[str, Any], metric: str) -> float:
    """*metric*'s coefficient of variation as a percentage, or NaN if unset."""
    cv = p.get(f"{metric}_cv")
    return cv * 100.0 if cv is not None else float("nan")


_TABLE_COLUMNS = ("size", "backend", *METRICS, *(f"{m}_cv%" for m in METRICS))


def _row_cells(p: dict[str, Any]) -> list[str]:
    cells = [str(p["size"]), str(p.get("backend", "clang"))]
    cells += [f"{float(p[m]):.1f}" if is_gateable(p.get(m)) else "n/a" for m in METRICS]
    cells += [f"{_cv_pct(p, m):.1f}" for m in METRICS]
    return cells


def _print_table(points: list[dict[str, Any]]) -> None:
    widths = [max(len(c), 11) for c in _TABLE_COLUMNS]
    print(" ".join(c.rjust(w) for c, w in zip(_TABLE_COLUMNS, widths)))
    for p in points:
        print(" ".join(c.rjust(w) for c, w in zip(_row_cells(p), widths)))


def _print_markdown(points: list[dict[str, Any]]) -> None:
    print("| " + " | ".join(_TABLE_COLUMNS) + " |")
    print("|" + "|".join(["---:"] * len(_TABLE_COLUMNS)) + "|")
    for p in points:
        print("| " + " | ".join(_row_cells(p)) + " |")


#: ``argparse`` ``type=`` for ``--sizes``/``--repeat``: reject <= 0. Now
#: shared with benchmark_scaling.py via perf_measurement.positive_int_arg
#: (the two scripts' identical --repeat-must-be-positive concern can't
#: independently drift); kept under this module's own historical name since
#: tests/test_header_graph_perf_gate.py references it directly.
_positive_int = positive_int_arg


#: ``argparse`` ``type=`` for ``--regress-tolerance``/``--regress-min-delta-ms``:
#: reject non-finite/negative. Now shared with benchmark_scaling.py via
#: perf_measurement.finite_nonnegative_float_arg (the two scripts' identical
#: regression-gate validation can't independently drift); kept under this
#: module's own historical name since tests/test_header_graph_perf_gate.py
#: references it directly.
_finite_nonnegative_float = finite_nonnegative_float_arg


def resolve_thresholds(args: argparse.Namespace) -> dict[str, GateThreshold]:
    """The effective :class:`GateThreshold` per selected metric.

    Exists as its own function, returning a value the report then records,
    because a threshold that is only ever computed inline at comparison time
    cannot be audited from the run's own output. That is not a hypothetical:
    ``benchmark_scaling.py`` carried a built-in per-scenario
    ``regress_tolerance=1.3`` that silently outranked an explicitly-passed
    stricter ``--regress-tolerance``, and no reader of its JSON report could
    see that the number they asked for was not the number that gated them.

    The precedence here admits no such case: the CLI-wide value is the
    default, a caller-stated per-metric value overrides it, and nothing else
    participates. A future built-in exception would have to arrive as a
    ``GateThreshold`` with its own ``source`` label and would therefore appear
    in the receipt.
    """
    base = GateThreshold(
        tolerance=args.regress_tolerance,
        min_delta=args.regress_min_delta_ms,
        source="explicit"
        if args.regress_tolerance != DEFAULT_REGRESS_TOLERANCE
        else "default",
    )
    return {
        metric: resolve_threshold(
            default=base,
            explicit_tolerance=getattr(args, f"tolerance_{metric}"),
            explicit_min_delta=getattr(args, f"min_delta_{metric}"),
        )
        for metric in args.metrics
    }


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
        help="Fractional growth allowed vs. baseline, for EVERY gated metric, "
        "before failing (default: %(default)s = 50%%). The actual allowed delta "
        "is max(this fraction x baseline, --regress-min-delta-ms).",
    )
    p.add_argument(
        "--regress-min-delta-ms",
        type=_finite_nonnegative_float,
        default=DEFAULT_REGRESS_MIN_DELTA_MS,
        help="Absolute-ms floor combined with --regress-tolerance via max(), for "
        "every gated metric -- protects a small baseline from flagging on "
        "run-to-run noise alone (default: %(default)s = pure percentage "
        "tolerance, the historical behaviour).",
    )
    p.add_argument(
        "--metrics",
        nargs="+",
        choices=list(METRICS),
        default=list(METRICS),
        help="Which metrics to gate (default: all three, gated independently). "
        "Narrowing this is a deliberate, visible choice recorded in the JSON "
        "report's effective_thresholds block -- it is not how a metric should "
        "ever come to be measured-but-ungated by accident.",
    )
    for metric in METRICS:
        p.add_argument(
            f"--regress-tolerance-{metric.removesuffix('_ms')}",
            type=_finite_nonnegative_float,
            default=None,
            dest=f"tolerance_{metric}",
            help=f"Per-metric override of --regress-tolerance for {metric}.",
        )
        p.add_argument(
            f"--regress-min-delta-ms-{metric.removesuffix('_ms')}",
            type=_finite_nonnegative_float,
            default=None,
            dest=f"min_delta_{metric}",
            help=f"Per-metric override of --regress-min-delta-ms for {metric}. "
            f"Useful because the three metrics differ by an order of magnitude "
            f"in absolute size, so one shared absolute floor is either useless "
            f"for the large one or noise-prone for the small one.",
        )
    p.add_argument(
        "--require-all-metrics",
        action="store_true",
        help="Fail when any selected metric could not be gated for any measured "
        "point (a missing baseline entry, or a non-finite/non-positive value "
        "on either side) instead of only reporting it. For a caller whose "
        "baseline was written by this same script version, an ungateable "
        "metric means something is wrong, not that coverage is legitimately "
        "partial.",
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
    # Resolved up front so the --json-out report records them even on a
    # report-only (no --baseline) run.
    thresholds = resolve_thresholds(args)
    metrics = tuple(args.metrics)

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
        args.json_out.write_text(
            json.dumps(
                {
                    "schema": REPORT_SCHEMA,
                    "metrics": list(METRICS),
                    # The thresholds that ACTUALLY gated this run, per metric,
                    # each with the provenance of its two numbers. Recorded
                    # unconditionally -- including on a report-only run, where
                    # they document what a later --baseline run would apply --
                    # so "the strict threshold I passed was honored" is a fact a
                    # reader can check rather than has to trust.
                    "effective_thresholds": {
                        m: t.as_dict() for m, t in thresholds.items()
                    },
                    "points": points,
                },
                indent=2,
            )
            + "\n"
        )
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

    print("\nEffective thresholds (the numbers that gated this run):")
    for metric, threshold in thresholds.items():
        print(
            f"  {metric}: tolerance={threshold.tolerance} "
            f"min_delta_ms={threshold.min_delta} source={threshold.source}"
        )

    matched = matched_points(points, baseline, metrics)
    if not matched:
        print(
            f"\nFAIL: --baseline {args.baseline} has no gateable entry for any of "
            f"this run's {len(points)} (size, backend) point(s) and selected "
            f"metric(s) {list(metrics)} — nothing was actually gated. Check that "
            "--sizes/--metrics/the backends measured match what the baseline was "
            "generated with, or regenerate it via --json-out."
        )
        return 1

    failures = check_regressions(points, baseline, thresholds)
    ungated = ungated_metrics(points, baseline, metrics)
    if args.require_all_metrics and ungated:
        failures.extend(f"--require-all-metrics: {u}" for u in ungated)
    if failures:
        print("\nFAIL: header-graph L2 perf regression:")
        for f in failures:
            print(f"  - {f}")
        return 1
    if ungated:
        print(
            f"\nNOTE: {len(ungated)} (point, metric) pair(s) were NOT gated "
            "(no baseline entry, or a non-gateable value on one side). Pass "
            "--require-all-metrics to turn this into a failure:"
        )
        for u in ungated:
            print(f"  - {u}")
    gated_pairs = sum(len(gateable_metrics(p, baseline, metrics)) for p in points)
    print(
        f"\nOK: no header-graph L2 perf regression vs. baseline "
        f"({gated_pairs} (point, metric) pair(s) checked across "
        f"{len(matched)} point(s), metrics={list(metrics)})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
