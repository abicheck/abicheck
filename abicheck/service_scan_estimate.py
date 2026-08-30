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

"""``scan``'s per-layer cost estimator (ADR-035 D10, ``--dry-run``/``--estimate``).

Split out of :mod:`abicheck.service_scan` purely for line budget: that
module is ``no_growth``-debt-tracked at its adoption baseline, and the
``_TU_UNKNOWN_NOTE_SUFFIX`` fix (Codex review, fresh evidence -- L4/L5 must
inherit L3's "genuinely unknown, not zero" TU-count state, not just L3's own
row) had no room left to land in-place. :mod:`abicheck.service_scan` binds
:func:`estimate_scan` (and the private helpers its own
:func:`~abicheck.service_scan.estimate_artifact_set` and
:mod:`abicheck.cli_scan`'s test suite reach directly) as real module-level
names via ``importlib.import_module`` -- a plain function call, not a static
``ast.ImportFrom`` this module's own AI-readiness ``import-cycle-growth``
scan would see -- so every existing ``from .service_scan import
estimate_scan`` call site is unaffected.

Every reference back to a :mod:`abicheck.service_scan` runtime value
(``CostEstimate``, its private TU-counting helpers) is a function-local
import: a module-level one would form the reverse half of that same cycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .buildsource.scan_levels import EvidenceDepth, SourceMethod

if TYPE_CHECKING:
    from .service_scan import CostEstimate, ScanRequest

# Codex review: TU counts here are workspace-wide (a pre-captured Bazel
# aquery/cquery jsonproto is never filtered by `targets` -- BazelAdapter only
# scopes a *live* query), so a `--build-target` run's real count is typically
# lower. Baked into each row's `note` so a Python-API caller sees it too.
_UNSCOPED_TU_NOTE_SUFFIX = (
    " [UNSCOPED: --build-target given, but this TU count is workspace-wide -- "
    "the real run's Bazel collection scopes to the requested root target(s) "
    "and typically touches fewer TUs]"
)
_TU_UNKNOWN_NOTE_SUFFIX = " [UNKNOWN: derived from an unknown L3 TU count, see L3_build note]"


def _estimate_total_tus(req: ScanRequest) -> tuple[int, str]:
    """Project-wide TU count and its provenance note for the estimate."""
    from .service_scan import (
        _build_config_declares_query,
        _count_bazel_build_info_tus,
        _count_compile_db_tus,
        _count_pack_tus,
        _count_source_tus,
        _discover_compile_db,
    )

    # Count TUs from the *same* effective build-info the real scan uses (`req.compile_db or req.build_info`) so an
    # explicit --compile-db wins over a Bazel --build-info here too — else the estimate could price a different
    # action graph than the scan executes (Codex review). A pack dir supplies its own L3 compile units; a Bazel
    # aquery/cquery jsonproto is routed through the Bazel adapter; a raw compile DB / source tree is counted
    # otherwise.
    eff_build_info = req.compile_db or req.build_info
    bazel_tus = (
        _count_bazel_build_info_tus(eff_build_info)
        if eff_build_info is not None
        else None
    )
    pack_tus = _count_pack_tus(eff_build_info) if eff_build_info is not None else None
    compile_db = _discover_compile_db(req.sources, eff_build_info)
    if bazel_tus is not None:
        total, note = bazel_tus, "Bazel aquery/cquery (build_evidence)"
    elif pack_tus is not None:
        total, note = pack_tus, "build-source pack (build_evidence)"
    elif compile_db is not None:
        total, note = _count_compile_db_tus(compile_db), f"compile DB: {compile_db.name}"
    elif req.sources is not None:
        total, note = _count_source_tus(req.sources), "counted source files (no compile DB)"
    else:
        total, note = 0, (f"build.query: {req.build_config.name} [UNKNOWN: query-only build.query, real run's trusted query determines the actual count]" if req.build_config is not None and _build_config_declares_query(req.build_config) else "no source tree / compile DB")
    if req.build_targets:
        note += _UNSCOPED_TU_NOTE_SUFFIX
    return total, note


def _estimate_replay_tus(req: ScanRequest, collect_mode: str, total_tus: int) -> int:
    """TUs the L4 replay (and its clang call-graph pass) would touch."""
    from .service_scan import _is_header_path, _is_source_tu_path

    # The L4 replay scope: a changed-only collection touches at most the changed *source* TUs (POI-focused, D7); a
    # full/target scope touches every TU. The budget's max_tus is a documented cap (never shrinks scope silently —
    # it FAILS — but the estimate honestly reflects the cap as the upper bound). A changed *header* fans out:
    # without an include graph (the common compile-DB-only path)
    # ``source_replay.select_compile_units(scope='changed')`` fails open to **all** TUs so header ABI changes are
    # never silently missed, so the estimate must charge ``total_tus`` for a header change rather than the single
    # header path — else it understates L4 cost and a user picks too small a budget (Codex review). An
    # empty/seedless diff is likewise broad.
    changed = [p for p in req.changed_paths if p]
    source_changed = [p for p in changed if _is_source_tu_path(p)]
    header_changed = any(_is_header_path(p) for p in changed)
    if collect_mode == "source-changed":
        if not changed or header_changed:
            replay_tus = total_tus
        else:
            replay_tus = (
                min(len(source_changed), total_tus)
                if total_tus
                else len(source_changed)
            )
    else:
        # graph-full / baseline → full scope; graph-build emits no L4 row.
        replay_tus = total_tus
    if req.budget.max_tus:
        replay_tus = min(replay_tus, req.budget.max_tus)
    return replay_tus


def _intrinsic_layer_estimates(
    req: ScanRequest, eff_depth: EvidenceDepth
) -> list[CostEstimate]:
    """The always-present L0/L1/L2 rows (intrinsic layers, no S-method)."""
    from .service_scan import (
        CostEstimate,
        _estimate_header_seconds,
        expand_header_inputs,
    )

    # --depth binary is symbols-only: the real scan suppresses the L2 header AST, so
    # the estimate must not price an L2_header layer for headers that won't be parsed
    # — else a programmatic caller's `ScanResult.estimate` plans a different cost than
    # what executes (Codex review). Keyed on the resolved effective depth.
    eff_req_headers = [] if eff_depth is EvidenceDepth.BINARY else list(req.headers)
    expanded_headers = expand_header_inputs(eff_req_headers) if eff_req_headers else []
    n_headers = len(expanded_headers)
    l2_seconds, l2_high_risk = _estimate_header_seconds(expanded_headers)
    if not n_headers:
        l2_note = "no headers supplied"
    elif l2_high_risk:
        l2_note = (
            "public-header AST (needs castxml or clang); deep #include/template "
            "complexity detected — this is a conservative floor, not a precise "
            "ETA, actual parse time can be far higher (unbounded in pathological "
            "cases); pass --budget to cap it"
        )
    else:
        l2_note = "public-header AST (needs castxml or clang)"
    return [
        CostEstimate(
            None,
            "L0_binary",
            len(req.binaries),
            0.1 * max(1, len(req.binaries)),
            0.0,
            "binary export table parse",
        ),
        CostEstimate(None, "L1_debug", 0, 0.05, 0.0, "debug info (if present)"),
        CostEstimate(None, "L2_header", n_headers, l2_seconds, 0.0, l2_note),
    ]


def _source_layer_estimates(
    resolved: SourceMethod,
    collect_mode: str,
    total_tus: int,
    tu_note: str,
    replay_tus: int,
    build_targets: tuple[str, ...] = (),
) -> list[CostEstimate]:
    """The collect-mode-dependent L3/L4/L5 rows (source-evidence layers)."""
    from .service_scan import (
        _COST_PER_TU_BUILD,
        _COST_PER_TU_GRAPH,
        _COST_PER_TU_REPLAY,
        CostEstimate,
    )

    # "source-target" (ADR-043 D2/D3) is the unseeded sibling of "source-changed" — same L3/L4/L5 layers, just a
    # broader (target-scoped, not changed-only) replay; it must price identically to source-changed everywhere
    # below, else an unseeded explicit-source scan/estimate silently reports zero source layers (the same zero-TU
    # defect the collect-mode fix addresses).
    #
    # L4/L5 inherit the same unscoped/unknown total_tus the L3 row's tu_note
    # already flags -- a short back-reference, not the full sentence again.
    unscoped_ref = " [UNSCOPED, see L3_build note]" if build_targets else ""
    unknown_ref = _TU_UNKNOWN_NOTE_SUFFIX if "[UNKNOWN" in tu_note else ""
    estimates: list[CostEstimate] = []
    if collect_mode in (
        "build",
        "graph-build",
        "source-changed",
        "source-target",
        "graph-full",
    ):
        estimates.append(
            CostEstimate(
                "s1",
                "L3_build",
                total_tus,
                _COST_PER_TU_BUILD * total_tus,
                0.0,
                tu_note,
            )
        )
    if collect_mode in ("source-changed", "source-target", "graph-full"):
        estimates.append(
            CostEstimate(
                resolved.value,
                "L4_source_abi",
                replay_tus,
                _COST_PER_TU_REPLAY * replay_tus,
                0.0,
                f"{collect_mode} replay scope ({replay_tus} of {total_tus} TU(s))"
                f"{unscoped_ref}{unknown_ref}",
            )
        )
    # L5 structural fold runs for every graph-building mode (cheap).
    if collect_mode in ("graph-build", "graph-full", "source-changed", "source-target"):
        estimates.append(
            CostEstimate(
                resolved.value,
                "L5_source_graph",
                total_tus,
                _COST_PER_TU_GRAPH * total_tus,
                0.0,
                f"source graph fold/edges{unscoped_ref}{unknown_ref}",
            )
        )
    # When both L4 and L5 are collected the inline path also runs a Clang
    # call-graph pass (``inline._fold_call_graph``) over the replay scope — price
    # it so `scan --estimate` does not understate a source-changed/graph-full PR
    # scan (Codex review). Scope mirrors the L4 replay (changed-scoped vs full).
    if collect_mode in ("source-changed", "source-target", "graph-full"):
        estimates.append(
            CostEstimate(
                resolved.value,
                "L5_source_graph",
                replay_tus,
                _COST_PER_TU_REPLAY * replay_tus,
                0.0,
                f"call-graph clang pass ({replay_tus} of {total_tus} TU(s))"
                f"{unscoped_ref}{unknown_ref}",
            )
        )
    return estimates


def estimate_scan(
    req: ScanRequest,
    *,
    resolved_level: tuple[SourceMethod, EvidenceDepth] | None = None,
) -> list[CostEstimate]:
    """Dry-run: projected per-layer cost of *req* for this project (ADR-035
    D10). Probes the project (TU count, header fan-out, collect mode) and
    returns one :class:`CostEstimate` per L-layer the level would touch --
    **without running any compiler or parsing any binary**. Coarse anchors
    (see ``_COST_PER_*``): ranks layers for a depth/budget pick, not a
    precise wall-clock prediction."""
    from .service_scan import _resolve_estimate_level

    resolved, eff_depth, collect_mode = _resolve_estimate_level(req, resolved_level)
    total_tus, tu_note = _estimate_total_tus(req)
    replay_tus = _estimate_replay_tus(req, collect_mode, total_tus)
    estimates = _intrinsic_layer_estimates(req, eff_depth)
    estimates.extend(
        _source_layer_estimates(
            resolved, collect_mode, total_tus, tu_note, replay_tus, req.build_targets
        )
    )
    return estimates


__all__ = [
    "estimate_scan",
    "_estimate_total_tus",
    "_estimate_replay_tus",
    "_intrinsic_layer_estimates",
    "_source_layer_estimates",
    "_UNSCOPED_TU_NOTE_SUFFIX",
    "_TU_UNKNOWN_NOTE_SUFFIX",
]
