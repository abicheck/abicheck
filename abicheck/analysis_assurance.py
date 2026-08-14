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

"""P0.4: ``analysis_assurance`` — a first-class, orthogonal answer to "how
complete and trustworthy was the evidence behind this verdict", independent
of what the verdict itself says.

This is the third leg of a three-way split this codebase already has two
thirds of:

    compatibility_verdict   (``DiffResult.verdict`` / ``Verdict``)
    analysis_assurance      (this module -- NEW)
    policy_gate_decision    (``severity.compute_gate_decision`` / exit codes)

ADR-049's contract-relevance/evidence/coverage machinery
(``contract_evaluation.py``, ``contract_coverage_ledger.py``,
``contract_coverage_exit.py``, ...) already answers a *narrower* version of
this question -- "was there enough evidence to trust a *contract-relevance*
decision" -- and only when a caller opts into ``--contract-evaluation``. This
module answers the broader question for *every* comparison, opt-in or not:
did the analysis itself (depth, translation-unit accounting, export
accounting, header/build/source-graph evidence) come back complete, whatever
the contract-relevance question turns out to be. The two are complementary,
not competing -- ``contract_coverage_ledger`` remains the authority for its
own narrower question, and this module does not duplicate it (it reads the
same underlying evidence rollups where they overlap: ``fact_set``
comparability, header-parse-context drift, source-graph completeness).

**Deliberately a rollup, not a new probe.** Every field below is derived
from data the pipeline already computed by the time ``checker.compare()``
returns (or, for translation-unit/export/graph-completeness detail, by the
time a ``BuildSourcePack`` was embedded on the snapshot ahead of
``compare()`` -- see ``service_input_resolution.embed_side_build_source``).
Nothing here shells out, re-parses a binary, or re-runs an extractor.

**What's included in this first slice, and what's deferred:**

- Requested-vs-effective *depth* reuses ``checker_types.EVIDENCE_DEPTH_VALUES``
  (``binary``/``headers``/``build``/``source``) rather than inventing a
  parallel vocabulary. ``requested_depth`` mirrors
  ``DiffResult.requested_depth`` -- the G30 report-identity field nothing
  populates yet (see that field's own docstring) -- so until a front end
  starts setting it, this stays ``None`` and depth-completeness reduces to
  reporting ``effective_depth`` alone. ``effective_depth`` is always
  computed here, independent of that field, from what each side's snapshot
  actually carries (mirrors ``cli_dump_helpers.evidence_depth_label``'s
  logic, reimplemented locally rather than imported -- importing a CLI-layer
  module from here would reach back through ``cli.py`` into ``checker.py``
  and grow the CLI-registration import cycle the AI-readiness gate rejects).
- ``target_accounting`` (expected vs. resolved Bazel targets) is P0.2's
  root-target resolution, which does not exist yet. Modeled as a field that
  is always present but empty (``requested``/``resolved`` both ``None``)
  until P0.2 lands -- never silently omitted, so a consumer can tell "not
  yet supported" from "supported and empty".
- Translation-unit and export accounting are rollups of the L3/L4
  ``BuildSourcePack`` coverage a snapshot already carries (when one was
  embedded); both are ``None``-filled when no such pack is present, which is
  the ordinary case for a plain ELF/DWARF/header comparison.
- ``fact_set_comparability``/``header_context_status``/``graph_completeness``
  surface existing signals (``fact_set_inconsistent``, the
  ``header_parse_context_drift`` finding, ``SourceGraphSummary.
  degraded_passes``) as structured status fields rather than leaving them as
  prose-only findings/coverage keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .checker_policy import ChangeKind, EvidenceTier
from .checker_types import DiffResult
from .model import AbiSnapshot

if TYPE_CHECKING:
    from .buildsource.pack import BuildSourcePack

__all__ = [
    "ANALYSIS_ASSURANCE_SCHEMA_VERSION",
    "ASSURANCE_STATUS_VALUES",
    "AnalysisAssurance",
    "ExportAccounting",
    "TargetAccounting",
    "TranslationUnitAccounting",
    "compute_analysis_assurance",
]

#: Schema version for the ``AnalysisAssurance`` block, versioned
#: independently of ``REPORT_SCHEMA_VERSION`` (which still gains a MINOR
#: bump for the new top-level ``analysis_assurance`` report key -- see
#: ``schemas/__init__.py``'s own history comment) the same way
#: ``buildsource.model.BUILD_SOURCE_PACK_VERSION`` versions independently of
#: the ABI-snapshot schema: this is a self-contained sub-object a consumer
#: can version-check without caring about the report's own MAJOR.MINOR.
ANALYSIS_ASSURANCE_SCHEMA_VERSION = "1.0"

#: The required top-level status vocabulary.
AssuranceStatus = Literal[
    "complete", "partial", "failed", "not_comparable", "not_requested"
]
ASSURANCE_STATUS_VALUES: frozenset[str] = frozenset(
    {"complete", "partial", "failed", "not_comparable", "not_requested"}
)

# Same ladder as ``cli_dump_helpers._DEPTH_RANK`` / EVIDENCE_DEPTH_VALUES,
# duplicated rather than imported for the reason given in the module
# docstring (avoiding a CLI-layer import from this leaf-ish module).
_DEPTH_RANK: dict[str, int] = {"binary": 0, "headers": 1, "build": 2, "source": 3}


@dataclass
class TargetAccounting:
    """Expected vs. resolved build-system targets (P0.2, not yet built).

    Always present on :class:`AnalysisAssurance`, always empty in this slice
    -- ``requested``/``resolved`` stay ``None`` until P0.2's Bazel
    root-target resolution (``requested_roots``/``resolved_roots``/
    ``transitive_targets``) lands and populates them. Kept as a real,
    documented placeholder rather than omitted so a consumer parsing this
    block today already has the field name to look for once it starts
    carrying data.
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
    ``total - unaccounted`` when both are known. All fields stay ``None``
    when no L4 source-ABI surface was linked (the ordinary case for a plain
    ELF/DWARF/header comparison) -- this is a rollup of the existing L4
    linker output, not a new detector, so it has nothing to report without
    one.
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
    requested_depth: str | None = None
    effective_depth: str | None = None
    #: ``None`` when no depth was requested (nothing to be satisfied or not);
    #: otherwise whether ``effective_depth`` reaches ``requested_depth`` on
    #: the ``EVIDENCE_DEPTH_VALUES`` ladder.
    depth_satisfied: bool | None = None
    target_accounting: TargetAccounting = field(default_factory=TargetAccounting)
    translation_units: TranslationUnitAccounting = field(
        default_factory=TranslationUnitAccounting
    )
    export_accounting: ExportAccounting = field(default_factory=ExportAccounting)
    #: ``"clean"`` (header evidence present, no drift finding),
    #: ``"drift_detected"`` (a ``header_parse_context_drift`` finding was
    #: raised), or ``"not_evaluated"`` (no header evidence to judge).
    header_context_status: str = "not_evaluated"
    #: ``"comparable"``, ``"inconsistent"`` (``fact_set_inconsistent`` on
    #: either side's L4 surface), ``"unknown"`` (asymmetric -- only one side
    #: carries an L4 surface), or ``"not_applicable"`` (neither side does).
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
    #: Human-readable notes explaining any non-``complete`` status, folded
    #: from the same underlying signals rather than duplicating their wording.
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "requested_depth": self.requested_depth,
            "effective_depth": self.effective_depth,
            "depth_satisfied": self.depth_satisfied,
            "target_accounting": self.target_accounting.to_dict(),
            "translation_units": self.translation_units.to_dict(),
            "export_accounting": self.export_accounting.to_dict(),
            "header_context_status": self.header_context_status,
            "fact_set_comparability": self.fact_set_comparability,
            "graph_completeness": self.graph_completeness,
            "notes": list(self.notes),
        }


def _effective_depth_label(snap: AbiSnapshot, pack: BuildSourcePack | None) -> str:
    """One side's own effective depth -- mirrors
    ``cli_dump_helpers.evidence_depth_label`` without importing it (see the
    module docstring for why: that module reaches back into ``cli.py``,
    which imports ``checker.py``).

    *pack* is the caller-resolved ``BuildSourcePack`` for this side (see
    :func:`compute_analysis_assurance`'s own docstring for why this must not
    default to ``snap.build_source`` internally) -- ``None`` when this side
    carries no pack at all, out-of-band or embedded.
    """
    if pack is not None:
        sa = getattr(pack, "source_abi", None)
        sg = getattr(pack, "source_graph", None)
        l4_has_facts = sa is not None and any(sa.reachable_buckets().values())
        l5_has_facts = sg is not None and bool(getattr(sg, "nodes", None))
        if l4_has_facts or l5_has_facts:
            return "source"
        be = getattr(pack, "build_evidence", None)
        if be is not None and (be.targets or be.compile_units):
            return "build"
    if getattr(snap, "parsed_with_build_context", False):
        return "build"
    if getattr(snap, "from_headers", False):
        return "headers"
    return "binary"


def _weaker_depth(a: str, b: str) -> str:
    return a if _DEPTH_RANK.get(a, 0) <= _DEPTH_RANK.get(b, 0) else b


def _fact_set_comparability(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[str, list[str]]:
    notes: list[str] = []
    old_sa = getattr(old_pack, "source_abi", None)
    new_sa = getattr(new_pack, "source_abi", None)
    if old_sa is None and new_sa is None:
        return "not_applicable", notes
    if old_sa is None or new_sa is None:
        notes.append(
            "fact-set comparability unknown: only one side carries a linked "
            "L4 source-ABI surface"
        )
        return "unknown", notes
    inconsistent = bool(old_sa.coverage.get("fact_set_inconsistent")) or bool(
        new_sa.coverage.get("fact_set_inconsistent")
    )
    if inconsistent:
        notes.append(
            "fact_set_inconsistent on at least one side's L4 surface -- the "
            "compile units backing it disagreed on their own fact-set identity"
        )
        return "inconsistent", notes
    return "comparable", notes


def _header_context_status(
    result: DiffResult, old: AbiSnapshot, new: AbiSnapshot
) -> tuple[str, list[str]]:
    has_headers = bool(getattr(old, "from_headers", False)) or bool(
        getattr(new, "from_headers", False)
    )
    if not has_headers:
        return "not_evaluated", []
    drifted = any(
        c.kind == ChangeKind.HEADER_PARSE_CONTEXT_DRIFT
        for c in (
            result.changes
            + result.out_of_surface_changes
            + result.reconciled_changes
            + result.suppressed_changes
        )
    )
    if drifted:
        return "drift_detected", [
            "header_parse_context_drift: at least one header was parsed "
            "under a different build context than the compiled binary"
        ]
    return "clean", []


def _translation_units(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> TranslationUnitAccounting:
    selected_total: int | None = None
    parsed_total: int | None = None
    for pack in (old_pack, new_pack):
        sa = getattr(pack, "source_abi", None)
        if sa is None:
            continue
        cov = sa.coverage or {}
        selected = cov.get("compile_units_selected")
        parsed = cov.get("compile_units_parsed")
        if selected is not None:
            selected_total = (selected_total or 0) + int(selected)
        if parsed is not None:
            parsed_total = (parsed_total or 0) + int(parsed)
    failed = None
    if selected_total is not None and parsed_total is not None:
        failed = max(0, selected_total - parsed_total)
    return TranslationUnitAccounting(
        selected=selected_total, parsed=parsed_total, failed=failed, skipped=None
    )


def _export_accounting(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> ExportAccounting:
    # The *new* side is the accounting subject -- mirrors every other
    # "current state of the library" summary in this codebase
    # (old_symbol_count is the one deliberate exception, kept for its own
    # historical reason). Falls back to the old side only when new carries
    # no L4 surface at all but old does (a comparison against a
    # source-linked baseline where the new side wasn't re-linked).
    for pack in (new_pack, old_pack):
        sa = getattr(pack, "source_abi", None)
        if sa is None:
            continue
        total = len(sa.roots.get("exported_symbols", []))
        unaccounted = len(sa.unmatched.get("symbols_without_decl", []))
        internal = len(sa.mappings.get("non_public_symbol_to_reason", {}))
        source_linked = max(0, total - unaccounted)
        return ExportAccounting(
            total=total,
            source_linked=source_linked,
            internal=internal,
            unaccounted=unaccounted,
        )
    return ExportAccounting()


def _graph_completeness(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[str, list[str]]:
    """Graph-completeness rollup over both sides' L5 ``SourceGraphSummary``.

    Finding (review, P1): the previous implementation only ever looked at
    ``degraded_passes`` and defaulted to ``"complete"`` for every other
    state, silently treating three genuinely-incomplete states as complete:

    - a pass that ran only over ``narrowed_passes``/``narrowed_scope`` -- a
      real, distinct field from ``degraded_passes``: the pass completed
      cleanly, but only over a subset of the project, so it does not
      establish full-project coverage;
    - a graph carrying no ``extractor_passes``/``narrowed_passes`` at all
      (a hand-built graph, or one from before that bookkeeping existed) --
      unknown pass coverage, not "everything ran";
    - old/new graph asymmetry -- one side carries an L5 graph and the other
      does not, so whatever the present side's graph says, the comparison
      itself never examined the missing side's implementation at all.
    """
    notes: list[str] = []
    old_graph = getattr(old_pack, "source_graph", None)
    new_graph = getattr(new_pack, "source_graph", None)
    if old_graph is None and new_graph is None:
        return "not_collected", notes
    if old_graph is None or new_graph is None:
        missing = "old" if old_graph is None else "new"
        notes.append(
            f"graph completeness unknown: the {missing} side carries no L5 "
            "source graph -- the comparison never examined that side's "
            "implementation at all"
        )
        return "unknown", notes

    degraded = False
    narrowed = False
    unknown_pass_coverage = False
    for side, sg in (("old", old_graph), ("new", new_graph)):
        for pass_name, is_degraded in (sg.degraded_passes or {}).items():
            if is_degraded:
                degraded = True
                notes.append(
                    f"source-graph pass {pass_name!r} ran degraded on the {side} side"
                )
        for pass_name, is_narrowed in (sg.narrowed_passes or {}).items():
            if is_narrowed:
                narrowed = True
                notes.append(
                    f"source-graph pass {pass_name!r} ran narrowed-scope on "
                    f"the {side} side -- it did not examine the whole project"
                )
        if not sg.extractor_passes and not sg.narrowed_passes:
            unknown_pass_coverage = True
            notes.append(
                f"the {side} side's source graph records no "
                "extractor_passes/narrowed_passes -- which parts of the "
                "project it actually examined is unknown"
            )

    if degraded:
        return "degraded", notes
    if narrowed:
        return "narrowed", notes
    if unknown_pass_coverage:
        return "unknown", notes
    return "complete", notes


def compute_analysis_assurance(
    result: DiffResult,
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_pack: BuildSourcePack | None = None,
    new_pack: BuildSourcePack | None = None,
) -> AnalysisAssurance:
    """Roll up existing pipeline signals into an :class:`AnalysisAssurance`.

    Pure and cheap: reads fields already present on *result*, *old*, and
    *new* -- no re-parsing, no new extraction. Safe to call unconditionally
    (see ``checker.compare()``) and again later once a caller enriches
    *result* with data that only becomes available after ``compare()``
    returns (``layer_coverage`` in the native ``compare`` CLI path -- see
    ``cli_compare_helpers._report_compare_result``).

    *old_pack*/*new_pack* are the ``BuildSourcePack`` that actually backed
    this comparison's build/source findings -- **not** implicitly re-read
    from ``old.build_source``/``new.build_source`` internally (finding, P1
    review). ``checker.compare()`` only ever knows about each snapshot's own
    *embedded* payload, so it passes exactly that
    (``old.build_source``/``new.build_source``) explicitly. But an
    out-of-band ``--old/new-build-info``/``--old/new-sources`` pack
    directory the ``compare`` CLI resolves via ``_resolve_side_pack`` is
    used for the run's real findings and coverage *without* ever being
    attached back onto ``old``/``new`` -- so a caller that resolved such a
    pack must pass it here explicitly (see
    ``cli_compare_helpers._report_compare_result``'s recomputation) or this
    function silently falls back to reading whatever the bare snapshots
    happen to carry, which can read ``status="complete"`` even when the
    pack actually used was partial or failed. Defaults to ``None`` (no
    pack) rather than to ``old.build_source``/``new.build_source`` so this
    function never has to guess which of the two a caller meant.
    """
    notes: list[str] = []

    # -- not_comparable short-circuit -------------------------------------
    if result.assurance == "none":
        return AnalysisAssurance(
            status="not_comparable",
            notes=(
                "old and new snapshots were not provably comparable "
                "(ADR-050 ProfileMismatchError/ScopeMismatchError waived by "
                "--diagnostic-comparison); every other assurance axis is "
                "unreliable for this run",
            ),
        )

    # -- depth --------------------------------------------------------------
    requested_depth = result.requested_depth
    effective_depth = result.effective_depth or _weaker_depth(
        _effective_depth_label(old, old_pack), _effective_depth_label(new, new_pack)
    )
    depth_satisfied: bool | None = None
    if requested_depth is not None:
        depth_satisfied = _DEPTH_RANK.get(effective_depth, 0) >= _DEPTH_RANK.get(
            requested_depth, 0
        )
        if not depth_satisfied:
            notes.append(
                f"requested depth {requested_depth!r} not reached; effective "
                f"depth is {effective_depth!r}"
            )

    # -- fact-set comparability ---------------------------------------------
    fact_set_comparability, fs_notes = _fact_set_comparability(old_pack, new_pack)
    notes.extend(fs_notes)

    # -- header-context status ------------------------------------------------
    header_context_status, hc_notes = _header_context_status(result, old, new)
    notes.extend(hc_notes)

    # -- TU / export accounting ----------------------------------------------
    tu_accounting = _translation_units(old_pack, new_pack)
    export_accounting = _export_accounting(old_pack, new_pack)

    # -- graph completeness ---------------------------------------------------
    graph_completeness, graph_notes = _graph_completeness(old_pack, new_pack)
    notes.extend(graph_notes)

    if not result.scope_resolved:
        notes.append(
            "scope_resolved is False: --scope-public-headers was requested "
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
        requested_depth is None
        and old_pack is None
        and new_pack is None
        and result.contract_context is None
        and result.evidence_tier == EvidenceTier.ELF_ONLY
    )
    if fact_set_comparability == "inconsistent":
        status: AssuranceStatus = "failed"
    elif requested_depth is not None and depth_satisfied is False:
        status = "failed"
    elif (
        header_context_status == "drift_detected"
        or graph_completeness in ("degraded", "narrowed", "unknown")
        or (tu_accounting.failed or 0) > 0
        or not result.scope_resolved
        or result.contract_coverage == "partial"
        or fact_set_comparability == "unknown"
    ):
        status = "partial"
    elif nothing_requested:
        status = "not_requested"
    else:
        status = "complete"

    return AnalysisAssurance(
        status=status,
        requested_depth=requested_depth,
        effective_depth=effective_depth,
        depth_satisfied=depth_satisfied,
        translation_units=tu_accounting,
        export_accounting=export_accounting,
        header_context_status=header_context_status,
        fact_set_comparability=fact_set_comparability,
        graph_completeness=graph_completeness,
        notes=tuple(notes),
    )


def analysis_assurance_report_dict(result: DiffResult) -> dict[str, Any] | None:
    """``result.analysis_assurance.to_dict()``, or ``None`` when absent/wrong
    type -- the one narrowing implementation ``reporter._add_analysis_assurance``
    calls from all four JSON paths (full/leaf/root-cause/stat), so the
    ``DiffResult.analysis_assurance: object`` circular-import workaround (see
    that field's own comment in ``checker_types.py``) has exactly one place
    that unwraps it.
    """
    aa = getattr(result, "analysis_assurance", None)
    if not isinstance(aa, AnalysisAssurance):
        return None
    return aa.to_dict()


#: Exit contribution when ``--require-complete-analysis`` is set and the
#: status fell short of ``complete``. Deliberately the same value
#: ``contract_coverage_exit.py`` uses for its own orthogonal axis: both are
#: "the evidence behind this verdict is not fully trustworthy" signals, and
#: ``max``-folding several such axes onto the same floor is exactly what
#: keeps them from stacking into a make-believe higher severity than any one
#: of them actually earned.
INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION = 1


def analysis_assurance_exit_contribution(
    result: DiffResult, *, require_complete: bool
) -> int:
    """The exit floor ``--require-complete-analysis`` imposes (``0``/``1``).

    ``0`` unconditionally when *require_complete* is False (the default) --
    this is what keeps the flag purely additive: a caller that never opts in
    sees no change to any exit code, ever, regardless of what
    ``analysis_assurance.status`` says. ``0`` also when *result* carries no
    ``AnalysisAssurance`` at all (defensive; ``checker.compare()`` always
    attaches one, but a hand-built ``DiffResult`` in a test or an older
    in-memory object might not).
    """
    if not require_complete:
        return 0
    aa = getattr(result, "analysis_assurance", None)
    if not isinstance(aa, AnalysisAssurance):
        return 0
    return 0 if aa.status == "complete" else INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION


def fold_analysis_assurance_exit(
    base: int, result: DiffResult, *, require_complete: bool
) -> int:
    """*base* raised to the assurance floor -- the same ``max`` discipline
    :func:`abicheck.contract_coverage_exit.fold_coverage_exit` uses for its
    own orthogonal axis: never lowers a real ``2``/``4`` compatibility exit,
    only ever raises a clean ``0`` to ``1``.
    """
    return max(
        base,
        analysis_assurance_exit_contribution(result, require_complete=require_complete),
    )


def assurance_floor_diagnostic(
    result: DiffResult, *, require_complete: bool, base_exit: int
) -> str | None:
    """Why this run's exit code was (or wasn't) affected by
    ``--require-complete-analysis``, or ``None`` when the flag wasn't set or
    the status was already ``complete``. Mirrors
    ``contract_coverage_exit.coverage_failure_diagnostic``'s wording style so
    the two orthogonal-axis notices read as one family.
    """
    if not require_complete:
        return None
    aa = getattr(result, "analysis_assurance", None)
    if not isinstance(aa, AnalysisAssurance) or aa.status == "complete":
        return None
    floor = INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION
    if base_exit < floor:
        effect = f"Exit code floored to {floor}"
    elif base_exit == floor:
        effect = f"Contributes {floor} to an exit that was already {base_exit}"
    else:
        effect = (
            f"Contributes {floor}, below the compatibility axis's own exit "
            f"{base_exit}, which stands"
        )
    where = "; ".join(aa.notes) if aa.notes else "no further detail recorded"
    return (
        f"Analysis assurance incomplete (status={aa.status!r}) under "
        f"--require-complete-analysis: {where}. {effect} (P0.4 "
        "analysis-assurance axis). Use --format json for the full "
        "analysis_assurance block."
    )
