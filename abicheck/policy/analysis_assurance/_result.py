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
"""Result types for the ``analysis_assurance`` block: ``AnalysisAssurance`` and its per-axis accounting records, plus the status vocabulary and schema version."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

#: Schema version for the ``AnalysisAssurance`` block, versioned
#: independently of ``REPORT_SCHEMA_VERSION`` (which still gains a MINOR
#: bump for the new top-level ``analysis_assurance`` report key -- see
#: ``schemas/__init__.py``'s own history comment) the same way
#: ``buildsource.model.BUILD_SOURCE_PACK_VERSION`` versions independently of
#: the ABI-snapshot schema: this is a self-contained sub-object a consumer
#: can version-check without caring about the report's own MAJOR.MINOR.
#: 1.0 -> 1.1: added ``schema_staleness_status`` (additive; a 1.0-only
#: reader ignores the new key).
#: 1.1 -> 1.2: added ``requested_depth_source`` (``"explicit"``/
#: ``"implicit"``). Additive in the key set, but note two *values* move for
#: a run that gave no ``--depth``: ``requested_depth`` is now the depth the
#: run reached rather than ``null``, and ``depth_satisfied`` is ``true``
#: rather than ``null`` -- an unrequested depth is satisfied, not unknown.
#: Separately, ``effective_depth`` no longer reads ``"source"`` for a run
#: whose only L5 evidence is the always-on header-only declaration graph
#: (``evidence_depth.reported_depth_label``). No verdict, assurance
#: ``status``, or exit code moves: every gate still reads the explicit
#: request.
ANALYSIS_ASSURANCE_SCHEMA_VERSION = "1.2"

#: The required top-level status vocabulary.
AssuranceStatus = Literal[
    "complete", "partial", "failed", "not_comparable", "not_requested"
]
ASSURANCE_STATUS_VALUES: frozenset[str] = frozenset(
    {"complete", "partial", "failed", "not_comparable", "not_requested"}
)


@dataclass
class TargetAccounting:
    """Expected vs. resolved build-system targets (P0.2's root-target scoping).

    Finding (review, P1): this block was originally left permanently empty
    pending P0.2's Bazel root-target resolution
    (``BuildEvidence.target_scope`` -- ``TargetScope.requested``/
    ``.resolved``/``.transitive_count``), with a comment noting P0.2 would
    populate it. P0.2 has since landed (``buildsource/adapters/bazel.py``),
    so keeping this block empty hid a genuine partial-resolution case (two
    requested Bazel roots with only one actually resolved -- a typo'd
    target label, say) with no predicate anywhere downgrading the overall
    ``status`` for it. Now wired: :func:`_target_accounting` rolls up both
    sides' ``BuildEvidence.target_scope`` (union of ``requested``/
    ``resolved`` labels, summed ``transitive_count``), and a requested root
    absent from ``resolved`` on either side folds into
    ``AnalysisAssurance.status = "partial"`` in the overall computation,
    same as every other incompleteness signal in this module.

    ``requested``/``resolved`` stay ``None`` only when NEITHER side ever
    requested root-target scoping at all (``BuildEvidence.target_scope`` is
    ``None``, or carries an empty ``requested`` list, on both sides) -- kept
    as a real, documented "not requested" state rather than an empty tuple,
    so a consumer can still tell "root-target scoping wasn't used for this
    run" from "it was used and resolved everything" (``requested ==
    resolved``, both non-empty).
    """

    requested: tuple[str, ...] | None = None
    resolved: tuple[str, ...] | None = None
    transitive_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested": list(self.requested) if self.requested is not None else None,
            "resolved": list(self.resolved) if self.resolved is not None else None,
            "transitive_count": self.transitive_count,
        }


@dataclass
class TranslationUnitAccounting:
    """Selected / parsed / failed / skipped TU counts (rollup, not a probe).

    Sourced from the embedded ``BuildSourcePack``'s L4
    ``SourceAbiSurface.coverage`` (``compile_units_selected``/
    ``compile_units_parsed``, set by ``source_replay.py``). ``failed`` is a
    *derived* upper bound (``selected - parsed``, floored at 0) -- the pack
    does not currently track a TU-level failure count distinct from "not
    parsed", so this over-approximates rather than under-reports a real
    failure. ``skipped`` is not tracked anywhere in the pipeline today and
    stays ``None``; a future extractor emitting a genuine
    ``compile_units_skipped`` coverage key would populate it without needing
    a shape change here.
    """

    selected: int | None = None
    parsed: int | None = None
    failed: int | None = None
    skipped: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "parsed": self.parsed,
            "failed": self.failed,
            "skipped": self.skipped,
        }


@dataclass
class ExportAccounting:
    """Total / source-linked / internal / unaccounted exported symbols.

    Sourced from the embedded ``BuildSourcePack``'s L4
    ``SourceAbiSurface``: ``roots["exported_symbols"]`` (total),
    ``unmatched["symbols_without_decl"]`` (unaccounted -- an export with no
    matching source declaration found), and
    ``mappings["non_public_symbol_to_reason"]`` (internal -- an export the
    source linker itself classified as non-public). ``source_linked`` is
    ``total - unaccounted - internal`` when all three are known -- a true
    three-way partition (``source_linked + internal + unaccounted ==
    total``), not merely "not unaccounted" (Codex review: an earlier
    ``total - unaccounted`` formula silently double-counted every
    ``internal`` export as ALSO ``source_linked`` once ``unaccounted`` no
    longer overlapped it; see :func:`_export_accounting`'s own docstring for
    the full history). All fields stay ``None``
    when no L4 source-ABI surface was linked (the ordinary case for a plain
    ELF/DWARF/header comparison) -- this is a rollup of the existing L4
    linker output, not a new detector, so it has nothing to report without
    one.

    Finding (review, P1, round 7): :func:`_export_accounting` used to pick
    ONE side's accounting (new first, old only as a fallback when new had no
    L4 surface at all) rather than combining both -- see that function's own
    docstring for the full history. The counts below are therefore an
    additive rollup across BOTH sides (mirroring
    :class:`TranslationUnitAccounting`'s own both-sides summation), not a
    single side's snapshot -- a nonzero ``unaccounted`` on either side always
    contributes to the combined total.
    """

    total: int | None = None
    source_linked: int | None = None
    internal: int | None = None
    unaccounted: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "source_linked": self.source_linked,
            "internal": self.internal,
            "unaccounted": self.unaccounted,
        }


@dataclass
class AnalysisAssurance:
    """The ``analysis_assurance`` block: how complete was the evidence.

    Orthogonal to :class:`~abicheck.checker_types.DiffResult.verdict` --
    :attr:`status` says nothing about whether a break was found, only about
    how much to trust the analysis that looked for one. See the module
    docstring for the three-way split this completes.
    """

    schema_version: str = ANALYSIS_ASSURANCE_SCHEMA_VERSION
    status: AssuranceStatus = "not_requested"
    #: The depth this run was asked for; under an ``"implicit"``
    #: :attr:`requested_depth_source`, the depth it reached instead --
    #: ``evidence_depth.resolve_reported_depth`` owns that rule.
    requested_depth: str | None = None
    effective_depth: str | None = None
    #: Whether ``effective_depth`` reaches ``requested_depth`` on the
    #: ``EVIDENCE_DEPTH_VALUES`` ladder -- trivially ``True`` for an
    #: implicit request, which is the point: an unrequested depth is
    #: *satisfied*, not *unknown*, and must not share a ``null`` with it.
    #: ``None`` only where neither was resolved.
    depth_satisfied: bool | None = None
    target_accounting: TargetAccounting = field(default_factory=TargetAccounting)
    translation_units: TranslationUnitAccounting = field(
        default_factory=TranslationUnitAccounting
    )
    export_accounting: ExportAccounting = field(default_factory=ExportAccounting)
    #: ``"clean"`` (both sides carry a binary export table),
    #: ``"asymmetric"`` (only one side does -- symbol-table-level evidence
    #: was never examined for the binary-lacking, snapshot-only side), or
    #: ``"not_evaluated"`` (neither side carries a binary -- a pure
    #: header/source-only comparison, a legitimate symmetric shape).
    l0_context_status: str = "not_evaluated"
    #: ``"clean"`` (header evidence present on both sides, no drift finding),
    #: ``"drift_detected"`` (a ``header_parse_context_drift`` finding was
    #: raised), ``"asymmetric"`` (only one side carries header/API-level
    #: evidence -- the comparison never examined the other side's headers at
    #: all), or ``"not_evaluated"`` (neither side has header evidence).
    header_context_status: str = "not_evaluated"
    #: ``"clean"`` (both sides carry usable DWARF/DWARF-advanced debug info),
    #: ``"asymmetric"`` (only one side does -- the DWARF-based struct/enum
    #: layout detectors, e.g. ``diff_platform.py``'s ``_diff_dwarf_types``/
    #: ``dwarf_advanced.diff_advanced_dwarf``, explicitly skip their own
    #: comparison whenever either side lacks it, so layout evidence for the
    #: missing side was never even attempted), or ``"not_evaluated"``
    #: (neither side has DWARF -- nothing to be asymmetric about).
    dwarf_context_status: str = "not_evaluated"
    #: ``"clean"`` (both sides carry an L3 ``BuildEvidence`` payload),
    #: ``"asymmetric"`` (only one side does -- the other's build-only
    #: changes were never checked at all;
    #: ``cli_buildsource_helpers.prepare_embedded_build_source`` already
    #: emits a ``layer_coverage_asymmetric``/``EVIDENCE_COVERAGE_ASYMMETRIC``
    #: finding naming this same gap), or ``"not_evaluated"`` (neither side
    #: carries L3 build evidence -- nothing to be asymmetric about).
    l3_context_status: str = "not_evaluated"
    #: ``"comparable"``, ``"inconsistent"`` (``fact_set_inconsistent`` on
    #: either side's L4 surface, OR a hard ``fact_set`` name/version mismatch
    #: BETWEEN the two sides -- the same cross-side check ``diff_source_abi``'s
    #: ``SOURCE_FACT_COVERAGE_INCOMPLETE`` uses), ``"unknown"`` (asymmetric --
    #: only one side carries an L4 surface at all, or the two sides carry a
    #: softer fact-set mismatch: a differing producer/producer_version/
    #: compiler_version/compiler_family, or one side stamped a ``fact_set``
    #: and the other didn't), or ``"not_applicable"`` (neither side carries
    #: an L4 surface).
    fact_set_comparability: str = "not_applicable"
    #: ``"complete"`` (both sides carry an L5 graph, every observed pass ran
    #: full-project and clean); ``"degraded"`` (``SourceGraphSummary.
    #: degraded_passes`` has a ``True`` entry on either side -- per-TU
    #: diagnostics on some subset); ``"narrowed"`` (a pass ran but only over
    #: ``narrowed_passes``/``narrowed_scope`` -- a real, distinct state from
    #: ``degraded_passes``: the pass completed cleanly but never examined the
    #: whole project, so it cannot vouch for full graph coverage);
    #: ``"unknown"`` (either only one side carries an L5 graph at all --
    #: asymmetric coverage -- or a side's graph records no
    #: ``extractor_passes``/``narrowed_passes`` at all, so which parts of the
    #: project the graph actually covers cannot be determined); or
    #: ``"not_collected"`` (neither side carries an L5 graph).
    graph_completeness: str = "not_collected"
    #: E-S1: see ``_layout.py``.
    layout_unverified_detectors: tuple[str, ...] = field(default_factory=tuple)
    #: Human-readable notes explaining any non-``complete`` status, folded
    #: from the same underlying signals rather than duplicating their wording.
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: ``"clean"`` (neither side carries a fact this abicheck's tool-upgrade
    #: reliability machinery marks stale) or ``"degraded"`` (at least one
    #: side does -- either its own ``schema_version`` predates this
    #: abicheck's, or it was re-saved since without ever being regenerated;
    #: see ``policy.analysis_assurance_degraded_facts.degraded_reliability_
    #: facts``). Unlike the other context-status fields above, this one has
    #: no ``"asymmetric"`` state: a single side's stale fact already means
    #: the affected detector(s) declined to trust it for this comparison.
    #: Appended at the END of the field list, after every pre-existing
    #: field including ``notes`` -- not inserted among the other context-
    #: status fields where it conceptually belongs -- specifically so this
    #: dataclass's generated POSITIONAL constructor stays backward
    #: compatible: an external caller passing the pre-existing trailing
    #: fields (``fact_set_comparability``/``graph_completeness``/
    #: ``layout_unverified_detectors``/``notes``) positionally would
    #: otherwise have silently rebound them to the wrong fields the moment
    #: this one was inserted earlier in the list (Codex review, PR #1209
    #: round 10).
    schema_staleness_status: str = "clean"
    #: ``"explicit"`` (a front end stated ``--depth``) or ``"implicit"``.
    #: Every gate reads the explicit value only, so this records the
    #: normalization without licensing one. Appended at the END for the
    #: reason ``schema_staleness_status`` above records: written next to
    #: ``depth_satisfied`` first, it rebound every positional argument from
    #: ``target_accounting`` on (CodeRabbit review). Second field to learn
    #: that, so ``tests/test_analysis_assurance_implicit_depth.py`` now
    #: states the rule executably rather than in a third comment.
    requested_depth_source: str = "implicit"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "requested_depth": self.requested_depth,
            "requested_depth_source": self.requested_depth_source,
            "effective_depth": self.effective_depth,
            "depth_satisfied": self.depth_satisfied,
            "target_accounting": self.target_accounting.to_dict(),
            "translation_units": self.translation_units.to_dict(),
            "export_accounting": self.export_accounting.to_dict(),
            "l0_context_status": self.l0_context_status,
            "header_context_status": self.header_context_status,
            "dwarf_context_status": self.dwarf_context_status,
            "l3_context_status": self.l3_context_status,
            "schema_staleness_status": self.schema_staleness_status,
            "fact_set_comparability": self.fact_set_comparability,
            "graph_completeness": self.graph_completeness,
            "layout_unverified_detectors": list(self.layout_unverified_detectors),
            "notes": list(self.notes),
        }
