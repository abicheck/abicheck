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
"""Assembles the ``AnalysisAssurance`` block from every axis. Its public entry point is ``compute_analysis_assurance``."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from ...checker_types import DiffResult
from ...evidence_depth import (
    depth_request_source,
    reported_depth_label,
    resolve_reported_depth,
    weaker_depth,
)
from ...model import AbiSnapshot
from ...model.evidence_status import EvidenceTier
from ..analysis_assurance_declined import declined_comparison_notes
from ..analysis_assurance_l0_context import l0_context_status as _l0_context_status
from ..analysis_assurance_schema_staleness import (
    schema_staleness_status as _schema_staleness_status,
)
from ..binary_gate_marker import binary_gate_assurance_notes
from ._comparability import (
    NOT_COMPARABLE_NOTE,
    bounded_comparability_notes,
    is_comparability_bounded,
)
from ._context import _dwarf_context_status, _header_context_status, _l3_context_status
from ._export_accounting import _export_accounting
from ._fact_set import _fact_set_comparability
from ._graph_completeness import _graph_completeness
from ._layout import layout_unverified_detectors as _layout_unverified_detectors
from ._manifest_layers import _manifest_layer_incompleteness
from ._result import AnalysisAssurance, AssuranceStatus
from ._target_accounting import _target_accounting
from ._translation_units import _translation_units

if TYPE_CHECKING:
    from ...buildsource.pack import BuildSourcePack


def compute_analysis_assurance(
    result: DiffResult,
    old: AbiSnapshot | None,
    new: AbiSnapshot,
    *,
    old_pack: BuildSourcePack | None = None,
    new_pack: BuildSourcePack | None = None,
    same_content: Callable[[], bool] | None = None,
) -> AnalysisAssurance:
    """Roll up existing pipeline signals into an :class:`AnalysisAssurance`.

    Pure and cheap: reads fields already present on *result*, *old*, and
    *new* -- no re-parsing, no new extraction. Safe to call unconditionally
    (see ``checker.compare()``) and again later once a caller enriches
    *result* with data that only becomes available after ``compare()``
    returns (``layer_coverage`` -- see ``workflows.compare_classify``).

    Prefer ``workflows.gate.attach_analysis_assurance``, which wires
    *same_content* for you (see ``workflows/analysis_assurance_attach``).

    *old_pack*/*new_pack* are the ``BuildSourcePack`` that actually backed
    this comparison's build/source findings -- **not** implicitly re-read
    from ``old.build_source``/``new.build_source`` internally (finding, P1
    review). ``checker.compare()`` only ever knows about each snapshot's own
    *embedded* payload, so it passes exactly that
    (``old.build_source``/``new.build_source``) explicitly. But an
    out-of-band ``--old/new-build-info``/``--old/new-sources`` pack
    directory used for the run's real findings must reach this call too --
    the ``compare`` CLI attaches it onto ``old``/``new`` before the shared
    classification -- or this function silently falls back to reading whatever the bare snapshots
    happen to carry, which can read ``status="complete"`` even when the
    pack actually used was partial or failed. Defaults to ``None`` (no
    pack) rather than to ``old.build_source``/``new.build_source`` so this
    function never has to guess which of the two a caller meant.
    """
    notes: list[str] = []
    # ``compare --no-baseline``: the baseline is declared absent (ADR-065
    # ``declared_absent``), so every axis below that asks "do the two sides
    # agree?" has no second side to ask about. Those axes read
    # ``not_evaluated`` -- their own existing vocabulary for "this question
    # was never answered" -- rather than being answered against a copy of
    # the candidate, which would report perfect agreement about a baseline
    # nobody supplied. The candidate-side axes (depth, pack accounting,
    # scope resolution) are unaffected and stay fully computed.

    # Carrying the request keeps ``requested_depth_source`` from
    # defaulting to a claim this path never established.
    if result.assurance == "none":
        return AnalysisAssurance(
            status="not_comparable",
            requested_depth=result.requested_depth,
            requested_depth_source=depth_request_source(result.requested_depth),
            notes=(NOT_COMPARABLE_NOTE,),
        )

    # Bounded (non-fatal) comparability mismatch -- owner:
    # `_comparability.py` (Codex review, PR #1274).
    notes.extend(bounded_comparability_notes(result.comparability_assurance))

    # -- depth --------------------------------------------------------------
    requested_depth = result.requested_depth
    effective_depth = result.effective_depth or (
        weaker_depth(
            reported_depth_label(old, old_pack), reported_depth_label(new, new_pack)
        )
        if old is not None
        # One side, so the run's effective depth *is* the candidate's own --
        # not the weaker of it and a stand-in.
        else reported_depth_label(new, new_pack)
    )
    # ``evidence_depth.resolve_reported_depth`` owns what an absent
    # ``--depth`` means; every gate below reads ``explicit_depth``, so no
    # verdict, status, or exit code moves.
    explicit_depth = requested_depth
    reported = resolve_reported_depth(explicit_depth, effective_depth)
    requested_depth = reported.requested
    requested_depth_source = reported.source
    depth_satisfied: bool | None = reported.satisfied
    if reported.note is not None:
        notes.append(reported.note)

    # -- fact-set comparability ---------------------------------------------
    fact_set_comparability, fs_notes = _fact_set_comparability(old_pack, new_pack)
    notes.extend(fs_notes)

    # -- L0 binary-context status -----------------------------------------------
    l0_context_status, l0_notes = (
        _l0_context_status(old, new) if old is not None else ("not_evaluated", [])
    )
    notes.extend(l0_notes)

    # -- header-context status ------------------------------------------------
    header_context_status, hc_notes = (
        _header_context_status(result, old, new)
        if old is not None
        else ("not_evaluated", [])
    )
    notes.extend(hc_notes)

    # -- DWARF-context status --------------------------------------------------
    dwarf_context_status, dw_notes = (
        _dwarf_context_status(old, new) if old is not None else ("not_evaluated", [])
    )
    notes.extend(dw_notes)

    layout_unverified_detectors = (  # E-S1
        _layout_unverified_detectors(old, new) if old is not None else ()
    )
    if layout_unverified_detectors:
        notes.append("layout unverified: " + ", ".join(layout_unverified_detectors))

    # -- L3 build-evidence context status ---------------------------------------
    l3_context_status, l3_notes = _l3_context_status(old_pack, new_pack)
    notes.extend(l3_notes)

    # -- schema-staleness status -------------------------------------------
    schema_staleness_status, staleness_notes = (
        _schema_staleness_status(old, new, same_content=same_content)
        if old is not None
        else ("not_evaluated", [])
    )
    notes.extend(staleness_notes)

    # -- target accounting (P0.2 root-target scoping rollup) --------------------
    target_accounting, target_unresolved, target_notes = _target_accounting(
        old_pack, new_pack
    )
    notes.extend(target_notes)

    # -- TU / export accounting ----------------------------------------------
    tu_accounting, l4_present_without_accounting = _translation_units(
        old_pack, new_pack
    )
    export_accounting = _export_accounting(old_pack, new_pack)
    if l4_present_without_accounting:
        notes.append(
            "an L4 source-ABI surface is present on at least one side but "
            "carries no translation-unit accounting at all (selected/parsed "
            "both unset) -- consistent with L4 extraction failing before it "
            "produced any real facts (e.g. clang/castxml unavailable)"
        )

    # -- graph completeness ---------------------------------------------------
    graph_completeness, graph_notes = _graph_completeness(old_pack, new_pack)
    notes.extend(graph_notes)

    # -- manifest-level layer incompleteness ----------------------------------
    # The one check below that reads the pack MANIFEST directly (D7/D8's own
    # status vocabulary) rather than the pack's model objects -- see
    # _manifest_layer_incompleteness's own docstring for why the two can
    # disagree (Finding, P1 review).
    manifest_layer_incomplete, manifest_notes = _manifest_layer_incompleteness(
        old_pack, new_pack
    )
    notes.extend(manifest_notes)
    notes.extend(declined_comparison_notes(result.detector_results))
    notes.extend(binary_gate_assurance_notes(result.changes))

    if (export_accounting.unaccounted or 0) > 0:
        notes.append(
            "export_accounting.unaccounted is nonzero: at least one exported "
            "symbol could not be associated with a source declaration on the "
            "side its L4 surface accounts for"
        )
    if not result.scope_resolved:
        notes.append(
            "scope_resolved is False: public-header scoping was requested "
            "but the public surface could not be resolved, so scoping fell "
            "back to the full export table"
        )
    if result.contract_coverage == "partial":
        notes.append(
            "contract_coverage is 'partial': exactly one side carried an "
            "ExtractionContract axis, so it could never be checked"
        )

    # -- overall status -------------------------------------------------------
    nothing_requested = (
        explicit_depth is None
        and old_pack is None
        and new_pack is None
        and result.contract_context is None
        and result.evidence_tier == EvidenceTier.ELF_ONLY
    )
    if fact_set_comparability == "inconsistent":
        status: AssuranceStatus = "failed"
    elif explicit_depth is not None and depth_satisfied is False:
        status = "failed"
    elif (
        l0_context_status == "asymmetric"
        or header_context_status in ("drift_detected", "asymmetric")
        or dwarf_context_status == "asymmetric"
        or l3_context_status == "asymmetric"
        or schema_staleness_status == "degraded"
        or graph_completeness in ("degraded", "narrowed", "unknown")
        or (tu_accounting.failed or 0) > 0
        or not result.scope_resolved
        or result.contract_coverage == "partial"
        or fact_set_comparability == "unknown"
        or l4_present_without_accounting
        or manifest_layer_incomplete
        or (export_accounting.unaccounted or 0) > 0
        or target_unresolved
        or is_comparability_bounded(result.comparability_assurance)
    ):
        status = "partial"
    elif nothing_requested:
        status = "not_requested"
    else:
        status = "complete"

    return AnalysisAssurance(
        status=status,
        requested_depth=requested_depth,
        requested_depth_source=requested_depth_source,
        effective_depth=effective_depth,
        depth_satisfied=depth_satisfied,
        target_accounting=target_accounting,
        translation_units=tu_accounting,
        export_accounting=export_accounting,
        l0_context_status=l0_context_status,
        header_context_status=header_context_status,
        dwarf_context_status=dwarf_context_status,
        l3_context_status=l3_context_status,
        schema_staleness_status=schema_staleness_status,
        fact_set_comparability=fact_set_comparability,
        graph_completeness=graph_completeness,
        layout_unverified_detectors=layout_unverified_detectors,
        notes=tuple(notes),
    )
