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
decision" -- and only when a caller opts into ``--contract``. This
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
  ``DiffResult.requested_depth`` -- the G30 report-identity field. Finding
  (review, P1, round 9): this field was documented as unpopulated by any
  front end and this module's own ``depth_satisfied`` gate reduced to
  reporting ``effective_depth`` alone as a result -- so an explicit
  ``compare --depth source`` that never actually reached source evidence
  (both sides lacking a compile database, say) silently read
  ``requested_depth=None``/``depth_satisfied=None`` and could still report
  ``status="complete"``. Fixed first at the one front end that knows the
  user's real, explicit ``--depth`` request: ``cli_compare_helpers.
  _report_compare_result`` copies its own Click-validated ``--depth``
  string onto ``DiffResult.requested_depth`` before recomputing this block
  (never an *inferred* depth from bare ``--sources``/``--build-info`` or
  ``.abicheck.yml``'s ``source.method`` -- only an explicit flag is this
  comparison's stated request in the same on-the-record way). That fix was
  CLI-only, though the underlying gap was not (P2 review,
  ``discussion_r3787839902``): ``service.run_compare_request()`` -- the
  shared, typed Python-API/MCP entry point ``service_compare_pipeline.
  classify_compare_pair`` implements -- never gets this treatment, since
  the CLI's own ``compare`` command resolves its evidence through
  ``resolve_compare_request`` but classifies through a hand-rolled
  ``compare_snapshots`` call of its own (its ``--depth`` flag never even
  reaches ``CompareRequest.depth``; that field only carries a real value
  for a *direct* typed-API caller). Fixed there too:
  ``classify_compare_pair`` now applies the identical ``if request.depth
  is not None`` stamp-and-recompute, so a direct
  ``service.run_compare_request(CompareRequest(..., depth="headers"))``
  call gets the same `requested_depth`/`depth_satisfied` receipt the CLI
  already did, without disturbing a caller that never sets
  ``CompareRequest.depth`` (unaffected, same as before). The two call
  sites are not one shared helper because each recomputes from genuinely
  different inputs the pipeline made available at a different point: the
  CLI recomputes with a possibly out-of-band ``--old/new-build-info``/
  ``--old/new-sources`` pack (`_resolve_side_pack`) that never touches
  ``old``/``new`` directly, while ``classify_compare_pair`` recomputes from
  each snapshot's own already-embedded ``build_source`` (mirroring
  ``checker.compare()``'s own call) since a typed request's
  ``InputSpec.sources``/``build_info`` are embedded before resolution ever
  returns -- there is no separate out-of-band pack to fold in on that path.
  ``effective_depth`` is always computed here, independent of that field,
  from what each side's snapshot actually carries, via the shared
  ``evidence_depth.depth_label_for``. That rule used to be reimplemented
  here rather than imported, because importing it meant importing a
  CLI-layer module and reaching back through ``cli.py`` into ``checker.py``;
  ADR-061 Phase 3 moved it to a leaf, so this is a real delegation now.
- ``target_accounting`` (expected vs. resolved Bazel targets) rolls up P0.2's
  root-target resolution (``BuildEvidence.target_scope``) now that it has
  landed -- ``requested``/``resolved`` stay ``None`` only when neither side
  requested root-target scoping at all, never silently omitted, so a
  consumer can tell "not requested" from "requested and empty".
- Translation-unit and export accounting are rollups of the L3/L4
  ``BuildSourcePack`` coverage a snapshot already carries (when one was
  embedded); both are ``None``-filled when no such pack is present, which is
  the ordinary case for a plain ELF/DWARF/header comparison.
- ``fact_set_comparability``/``l0_context_status``/``header_context_status``/
  ``dwarf_context_status``/``l3_context_status``/``graph_completeness``
  surface existing signals (``fact_set_inconsistent`` plus a cross-side
  ``fact_set`` name/version comparison, each side's own binary export-table
  presence, the ``header_parse_context_drift`` finding, each side's own
  ``dwarf.has_dwarf``/``dwarf_advanced.has_dwarf``, each side's own
  ``BuildSourcePack.build_evidence`` presence, ``SourceGraphSummary.
  degraded_passes``) as structured status fields rather than leaving them as
  prose-only findings/coverage keys.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .buildsource.fact_set import check_fact_compatibility
from .buildsource.model import CoverageStatus, DataLayer
from .checker_policy import ChangeKind, EvidenceTier
from .checker_types import DiffResult
from .evidence_depth import DEPTH_RANK, depth_label_for, weaker_depth
from .model import AbiSnapshot

if TYPE_CHECKING:
    from .buildsource.pack import BuildSourcePack
    from .model.source_graph import SourceGraphSummary

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

# The ladder is owned by ``evidence_depth.DEPTH_RANK``, derived from
# ``scan_levels.USER_DEPTHS``. It used to be duplicated here to avoid a
# CLI-layer import; ADR-061 Phase 3 moved the vocabulary to a leaf, so the
# copy is gone and this is an alias.
_DEPTH_RANK = DEPTH_RANK

#: The real ``ExtractorRecord.name`` families for L5 source-graph extractors,
#: as actually constructed in ``buildsource/inline_graph_fold.py`` (grepped
#: from the real call sites, not guessed): each record is named
#: ``"<family>:<tool>"`` -- e.g. ``"call_graph:clang"``,
#: ``"type_graph:clang"``, ``"include_graph:clang"``,
#: ``"override_graph:clang"``, ``"template_graph:clang"``,
#: ``"macro_graph:clang"``, ``"callback_graph:clang"``,
#: ``"archive_graph:ar_index"``. Finding (review, P1): the previous version
#: of ``_graph_completeness`` matched on a ``"source_graph"`` prefix, which
#: is not how any real extractor is actually named, so a genuinely
#: partial/failed L5 graph extractor status was never recognized and could
#: read as ``"complete"``.
_L5_GRAPH_EXTRACTOR_FAMILIES: frozenset[str] = frozenset(
    {
        "archive_graph",
        "call_graph",
        "callback_graph",
        "include_graph",
        "macro_graph",
        "override_graph",
        "template_graph",
        "type_graph",
    }
)


def _is_l5_graph_extractor_record(name: str) -> bool:
    """Whether *name* (an ``ExtractorRecord.name``) belongs to one of the L5
    source-graph extractor families above -- the part before the first
    ``":"``."""
    family = name.split(":", 1)[0]
    return family in _L5_GRAPH_EXTRACTOR_FAMILIES


#: P0.4 (P2 review): ``fold_archive_graph`` correctly records nothing on a side with no ``static_library`` node.
_CONDITIONALLY_APPLICABLE_FAMILIES: dict[str, Callable[[SourceGraphSummary], bool]] = {
    "archive_graph": lambda sg: any(n.kind == "static_library" for n in sg.nodes),
}


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
            "l0_context_status": self.l0_context_status,
            "header_context_status": self.header_context_status,
            "dwarf_context_status": self.dwarf_context_status,
            "l3_context_status": self.l3_context_status,
            "fact_set_comparability": self.fact_set_comparability,
            "graph_completeness": self.graph_completeness,
            "notes": list(self.notes),
        }


def _effective_depth_label(snap: AbiSnapshot, pack: BuildSourcePack | None) -> str:
    """One side's own effective depth.

    Delegates to :func:`abicheck.evidence_depth.depth_label_for`, which owns
    the rule. This was a hand-copy of ``cli_dump_helpers.evidence_depth_label``
    kept in sync by comment, because importing it would have pulled a CLI-layer
    module (and through it ``cli.py``/``checker.py``) into this one; ADR-061
    Phase 3 moved the rule to a leaf, so the copy is gone.

    *pack* is the caller-resolved ``BuildSourcePack`` for this side (see
    :func:`compute_analysis_assurance`'s own docstring for why this must not
    default to ``snap.build_source`` internally) -- ``None`` when this side
    carries no pack at all, out-of-band or embedded. The leaf takes its pack
    explicitly for exactly that reason, so the no-defaulting rule is now
    enforced by the shared signature rather than by two docstrings agreeing.
    """
    return depth_label_for(snap, pack)


def _weaker_depth(a: str, b: str) -> str:
    """Compatibility alias for :func:`abicheck.evidence_depth.weaker_depth`."""
    return weaker_depth(a, b)


def _surface_fact_set(sa: Any) -> dict[str, Any]:
    """The rolled-up ``fact_set`` a ``SourceAbiSurface``'s ``coverage`` block
    carries, or ``{}``. Mirrors ``buildsource.source_diff._surface_fact_set``
    exactly (same dict-shaped ``coverage.fact_set`` field, same
    ``isinstance`` guard) rather than importing that module's private
    helper across a package boundary.
    """
    cov = sa.coverage if isinstance(sa.coverage, dict) else {}
    raw = cov.get("fact_set")
    return dict(raw) if isinstance(raw, dict) else {}


def _surface_fact_set_inconsistent(sa: Any) -> bool:
    """Whether this surface's own TUs disagreed on ``fact_set``. Mirrors
    ``buildsource.source_diff._surface_fact_set_inconsistent`` exactly,
    including its ``is True`` strictness (a hand-edited/forward-produced
    surface storing this flag as the JSON string ``"false"`` must not read
    as truthy).
    """
    cov = sa.coverage if isinstance(sa.coverage, dict) else {}
    return cov.get("fact_set_inconsistent") is True


def _fact_set_comparability(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[str, list[str]]:
    """Cross-side L4 fact-set comparability -- the ``analysis_assurance``
    analogue of ``buildsource.source_diff.diff_source_abi``'s own
    ``SOURCE_FACT_COVERAGE_INCOMPLETE`` gating.

    Finding (review, P1): the previous implementation only ever checked each
    SIDE's own ``fact_set_inconsistent`` flag independently -- it never
    compared the two sides' ``fact_set`` identities AGAINST EACH OTHER, so
    two internally-consistent but mutually-DIFFERENT fact-set identities
    (a producer/version mismatch between old and new) fell through to
    ``"comparable"`` even though ``diff_source_abi()``'s own
    ``check_fact_compatibility()`` -- the real, already-correct cross-side
    comparison this codebase already has -- identifies that exact same
    mismatch as ``fact_set_name_mismatch``/``fact_set_version_mismatch`` and
    suppresses source comparisons that cannot be trusted for it. Fixed by
    calling the identical ``buildsource.fact_set.check_fact_compatibility``
    both sides' rolled-up ``fact_set`` dicts, the same helper
    ``diff_source_abi()`` uses, instead of re-implementing a second,
    independent comparison here.

    A hard mismatch (``compat.structured_facts_comparable is False`` -- a
    ``fact_set`` name/version disagreement, or either side's own TUs
    disagreeing on ``fact_set``) reads ``"inconsistent"``, same bucket as
    the pre-existing per-side ``fact_set_inconsistent`` case. A softer
    mismatch (``compat.issues`` non-empty but ``structured_facts_comparable``
    still true -- a producer/producer_version/compiler_version/
    compiler_family difference, or an asymmetric one-sided ``fact_set``
    stamp) reads ``"unknown"``, since opaque-hash/content comparisons are
    not guaranteed trustworthy even though existence/removal detection
    still is. A symmetric, genuinely EMPTY ``fact_set`` on both sides (no
    signal at all -- a pre-C.8 producer, or two hand-edited packs) keeps
    reading ``"comparable"``, preserving the same forward-compat convention
    ``check_fact_compatibility`` itself documents for that shape.
    """
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

    old_inconsistent = _surface_fact_set_inconsistent(old_sa)
    new_inconsistent = _surface_fact_set_inconsistent(new_sa)
    old_fact_set = _surface_fact_set(old_sa)
    new_fact_set = _surface_fact_set(new_sa)
    has_signal = bool(
        old_fact_set or new_fact_set or old_inconsistent or new_inconsistent
    )
    if not has_signal:
        # Symmetric, genuinely empty on both sides -- nothing to compare,
        # same forward-compat treatment check_fact_compatibility() itself
        # gives this shape.
        return "comparable", notes

    compat = check_fact_compatibility(
        old_fact_set,
        new_fact_set,
        old_inconsistent=old_inconsistent,
        new_inconsistent=new_inconsistent,
    )
    if not compat.structured_facts_comparable:
        issue_notes = [f"{i.rule}: {i.message}" for i in compat.issues] or [
            "fact_set_inconsistent on at least one side's L4 surface -- the "
            "compile units backing it disagreed on their own fact-set identity"
        ]
        notes.extend(issue_notes)
        return "inconsistent", notes
    if compat.issues:
        notes.extend(f"{i.rule}: {i.message}" for i in compat.issues)
        return "unknown", notes
    return "comparable", notes


def _l0_context_status(old: AbiSnapshot, new: AbiSnapshot) -> tuple[str, list[str]]:
    """Per-side L0 binary/export-table presence status -- the L0 analogue of
    :func:`_header_context_status`'s/:func:`_dwarf_context_status`'s/
    :func:`_l3_context_status`'s presence-then-asymmetry shape.

    Self-audit finding (P0.4 review): a ``AbiSnapshot`` need not carry any
    binary at all -- ``cli_scan_helpers._intrinsic_coverage`` already
    documents and reports this exact state as ``"no binary export table
    (snapshot-only input)"`` (``has_binary = bool(snap.elf or snap.pe or
    snap.macho)``), the same predicate this helper uses. A comparison where
    only one side carries a real binary (the other a synthetic/
    snapshot-only, headers-or-hand-built-only input) means every L0-derived
    signal -- exported-symbol presence/removal, SONAME, binding/visibility --
    was never even attempted for the binary-lacking side, the identical
    shape of gap already closed for L1 (DWARF)/L2 (headers)/L3 (build
    evidence) above. Fixed the same way: check each side's own binary
    presence directly and report the asymmetric case distinctly from "both
    sides have a binary" and "neither does" (a pure header/source-only
    comparison, which is a legitimate, supported, symmetric shape and not
    itself an asymmetry).
    """

    def _has_binary(snap: AbiSnapshot) -> bool:
        return bool(snap.elf or snap.pe or snap.macho)

    old_binary = _has_binary(old)
    new_binary = _has_binary(new)
    if not old_binary and not new_binary:
        return "not_evaluated", []
    if old_binary != new_binary:
        missing = "new" if old_binary else "old"
        return "asymmetric", [
            f"L0 binary context asymmetric: the {missing} side carries no "
            "binary export table at all (snapshot-only input) -- symbol-table "
            "-level evidence (exports, SONAME, binding/visibility) was never "
            "examined for that side"
        ]
    return "clean", []


def _header_context_status(
    result: DiffResult, old: AbiSnapshot, new: AbiSnapshot
) -> tuple[str, list[str]]:
    """Header/API-level evidence status for this comparison.

    Finding (review, P1): the previous implementation used ``or`` to decide
    whether header evidence was "present" -- so a genuinely asymmetric
    comparison (one side header-derived, the other ELF-only/binary-only)
    read as fully evaluated, since only *one* side needs ``from_headers`` for
    the ``or`` to be true. That let a header-snapshot-vs-ELF-only comparison
    report ``header_context_status="clean"`` (no drift finding to raise,
    because the ELF-only side has no headers to drift against) even though
    only half the comparison actually has header/API-level evidence backing
    it. Fixed by checking ``old.from_headers != new.from_headers`` explicitly
    first and reporting the asymmetric case on its own, distinct from both
    "neither side has headers" (``not_evaluated``) and "both sides do, and
    they agree" (``clean``/``drift_detected``).
    """
    old_headers = bool(getattr(old, "from_headers", False))
    new_headers = bool(getattr(new, "from_headers", False))
    if not old_headers and not new_headers:
        return "not_evaluated", []
    if old_headers != new_headers:
        missing = "new" if old_headers else "old"
        return "asymmetric", [
            f"header context asymmetric: the {missing} side carries no "
            "header/API-level evidence (from_headers=False) -- only one "
            "side of this comparison was extracted from headers"
        ]
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


def _dwarf_context_status(old: AbiSnapshot, new: AbiSnapshot) -> tuple[str, list[str]]:
    """Per-side DWARF/DWARF-advanced evidence status -- the DWARF analogue of
    :func:`_header_context_status`'s presence-then-asymmetry shape.

    Finding (review, P1): ``confidence._detect_evidence_tiers()`` combines
    both sides' DWARF availability with OR semantics
    (``has_dwarf = (old.dwarf is not None and old.dwarf.has_dwarf) or
    (new.dwarf is not None and new.dwarf.has_dwarf)``) when it promotes the
    aggregate ``result.evidence_tier`` to ``DWARF_AWARE`` -- so a comparison
    where only *one* side actually carries usable debug info still reads as
    DWARF-aware overall. That made ``nothing_requested`` false (since
    ``evidence_tier != EvidenceTier.ELF_ONLY``) without recording the
    asymmetry anywhere else this rollup checked, and with no other partial
    predicate tripping, the overall status fell through to ``"complete"`` --
    even though the real DWARF-based detectors explicitly skip their own
    comparison whenever either side lacks usable DWARF
    (``diff_platform.py``'s struct/enum layout diff: "Neither side has
    DWARF -> skip silently" / "Only new has DWARF -> can't compare without
    old baseline -> skip"; ``dwarf_advanced.diff_advanced_dwarf``: "Returns
    [] gracefully if either side has no DWARF"). Struct/enum layout changes
    on the DWARF-lacking side could therefore be silently missed while
    ``--require-complete-analysis`` still exited 0. Fixed by checking each
    side's own ``dwarf.has_dwarf``/``dwarf_advanced.has_dwarf`` directly,
    independent of the OR-combined aggregate tier.

    Finding (review, P1, round 7): the fix above still folded
    ``dwarf.has_dwarf`` and ``dwarf_advanced.has_dwarf`` into ONE combined
    per-side boolean via ``or`` before comparing the two sides -- so an old
    snapshot with only basic ``dwarf`` and a new snapshot with only
    ``dwarf_advanced`` both read as "this side has SOME dwarf evidence", the
    two combined per-side booleans compare equal (``True == True``), and the
    status read ``"clean"``. But ``dwarf`` and ``dwarf_advanced`` are two
    entirely independent evidence channels, each compared by its own,
    separate detector family: ``diff_platform.py``'s struct/enum layout diff
    reads only basic ``dwarf`` and ``dwarf_advanced.diff_advanced_dwarf``
    reads only ``dwarf_advanced`` -- and each detector independently skips
    ITS OWN comparison whenever EITHER side lacks ITS channel specifically,
    regardless of what the other channel has. Old-basic-only +
    new-advanced-only therefore means BOTH detector families silently
    skipped their comparison, on opposite sides, with the OR-combined
    boolean never noticing either gap. Fixed by checking ``dwarf.has_dwarf``
    and ``dwarf_advanced.has_dwarf`` as two independent, per-channel
    asymmetry checks rather than one combined presence check -- either
    channel disagreeing between the two sides now reports ``"asymmetric"``,
    with a note naming which channel(s) specifically.
    """

    def _channel_present(meta: Any) -> bool:
        return bool(meta is not None and meta.has_dwarf)

    old_basic = _channel_present(old.dwarf)
    new_basic = _channel_present(new.dwarf)
    old_advanced = _channel_present(old.dwarf_advanced)
    new_advanced = _channel_present(new.dwarf_advanced)

    if not (old_basic or new_basic or old_advanced or new_advanced):
        return "not_evaluated", []

    notes: list[str] = []
    if old_basic != new_basic:
        missing = "new" if old_basic else "old"
        notes.append(
            f"DWARF context asymmetric (basic dwarf channel): the {missing} "
            "side carries no usable basic DWARF debug info -- "
            "diff_platform.py's DWARF-based struct/enum layout comparison "
            "was skipped for that side entirely"
        )
    if old_advanced != new_advanced:
        missing = "new" if old_advanced else "old"
        notes.append(
            f"DWARF context asymmetric (dwarf_advanced channel): the "
            f"{missing} side carries no usable DWARF-advanced debug info -- "
            "dwarf_advanced.diff_advanced_dwarf was skipped for that side "
            "entirely"
        )
    if notes:
        return "asymmetric", notes
    return "clean", []


def _l3_context_status(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[str, list[str]]:
    """Per-side L3 ``BuildEvidence`` presence status -- the L3 analogue of
    :func:`_header_context_status`'s/:func:`_dwarf_context_status`'s
    presence-then-asymmetry shape.

    Finding (review, P1): when the baseline carries L3 ``BuildEvidence`` but
    the target has none (or vice versa), ``cli_buildsource_helpers.
    prepare_embedded_build_source()`` already emits a prose-only
    ``EVIDENCE_COVERAGE_ASYMMETRIC``/``layer_coverage_asymmetric`` finding
    explaining that build-only changes were not checked for the side
    lacking L3 evidence -- but nothing in this rollup's own predicates
    examined L3 presence by side, so an otherwise-matching header-scoped
    comparison fell through to ``status="complete"`` regardless, even while
    the report itself carried that incomplete-coverage finding. Fixed by
    checking each side's own ``BuildSourcePack.build_evidence is not None``
    directly, mirroring the same presence-then-asymmetry check
    ``cli_buildsource_helpers._layer_presence`` already uses for L3.
    """
    old_l3 = getattr(old_pack, "build_evidence", None) is not None
    new_l3 = getattr(new_pack, "build_evidence", None) is not None
    if not old_l3 and not new_l3:
        return "not_evaluated", []
    if old_l3 != new_l3:
        missing = "new" if old_l3 else "old"
        return "asymmetric", [
            f"L3 build-evidence context asymmetric: the {missing} side "
            "carries no BuildEvidence at all -- build-only changes were not "
            "checked for that side (see the layer_coverage_asymmetric finding)"
        ]
    return "clean", []


def _target_accounting(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[TargetAccounting, bool, list[str]]:
    """Roll up P0.2's root-target scoping (``BuildEvidence.target_scope``)
    across both sides, plus a second signal: whether either side requested a
    root target that never resolved (Finding, P1 review -- see
    :class:`TargetAccounting`'s own docstring for the full history).

    ``requested``/``resolved`` are the union (order-preserving, deduplicated)
    of both sides' ``TargetScope.requested``/``.resolved`` label lists --
    mirroring the additive rollup :func:`_translation_units` already uses
    for numeric L4 accounting, adapted for label sets since target names
    (not counts) are what a consumer needs to see here. A side whose
    ``TargetScope`` carries an empty ``requested`` list is treated the same
    as ``target_scope is None`` -- ``BuildEvidence.target_scope``'s own
    docstring documents this as "root-target scoping was not requested",
    matching the convention ``buildsource.build_evidence.l3_coverage_fields``
    already uses for the identical field.
    """
    requested: list[str] = []
    resolved: list[str] = []
    seen_requested: set[str] = set()
    seen_resolved: set[str] = set()
    transitive_total: int | None = None
    any_unresolved = False
    notes: list[str] = []
    for side, pack in (("old", old_pack), ("new", new_pack)):
        be = getattr(pack, "build_evidence", None)
        ts = getattr(be, "target_scope", None) if be is not None else None
        if ts is None or not ts.requested:
            continue
        for t in ts.requested:
            if t not in seen_requested:
                seen_requested.add(t)
                requested.append(t)
        for t in ts.resolved:
            if t not in seen_resolved:
                seen_resolved.add(t)
                resolved.append(t)
        if ts.transitive_count is not None:
            transitive_total = (transitive_total or 0) + ts.transitive_count
        unresolved = sorted(set(ts.requested) - set(ts.resolved))
        if unresolved:
            any_unresolved = True
            notes.append(
                f"{side} side's requested root target(s) did not all resolve: "
                f"{', '.join(unresolved)}"
            )

    if not requested and not resolved and transitive_total is None:
        return TargetAccounting(), False, notes

    return (
        TargetAccounting(
            requested=tuple(requested),
            resolved=tuple(resolved),
            transitive_count=transitive_total,
        ),
        any_unresolved,
        notes,
    )


def _translation_units(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[TranslationUnitAccounting, bool]:
    """Roll up TU accounting, plus a second signal: whether a side carries an
    L4 ``source_abi`` object at all (i.e. L4 replay was actually attempted)
    while producing *no* TU accounting whatsoever (Finding, P1 review).

    ``inline._run_inline_source_abi()`` returns a bare ``SourceAbiSurface()``
    -- present, but with an empty ``coverage`` dict -- when its extractor
    (clang/castxml) isn't available; ``compile_units_selected``/
    ``compile_units_parsed`` are then both absent, not zero, so the old
    ``failed = selected - parsed`` computation below silently stayed
    ``None`` (nothing to subtract) instead of reading as a failure. The
    returned bool lets the caller treat that shape -- a present-but-empty L4
    surface -- as its own incompleteness signal, independent of whatever
    ``selected``/``parsed`` end up being when they ARE populated.
    """
    selected_total: int | None = None
    parsed_total: int | None = None
    l4_present_without_accounting = False
    for pack in (old_pack, new_pack):
        sa = getattr(pack, "source_abi", None)
        if sa is None:
            continue
        cov = sa.coverage or {}
        selected = cov.get("compile_units_selected")
        parsed = cov.get("compile_units_parsed")
        if selected is None and parsed is None:
            l4_present_without_accounting = True
        if selected is not None:
            selected_total = (selected_total or 0) + int(selected)
        if parsed is not None:
            parsed_total = (parsed_total or 0) + int(parsed)
    failed = None
    if selected_total is not None and parsed_total is not None:
        failed = max(0, selected_total - parsed_total)
    return (
        TranslationUnitAccounting(
            selected=selected_total, parsed=parsed_total, failed=failed, skipped=None
        ),
        l4_present_without_accounting,
    )


def _manifest_layer_incompleteness(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[bool, list[str]]:
    """Whether either side's pack *manifest* itself records an L3/L4 layer as
    incomplete -- ``partial`` coverage, or a ``failed``/``partial`` extractor
    -- for a layer this run's rollup otherwise treats as present (Finding, P1
    review). Deliberately does not flag a plain ``not_collected`` coverage
    row: that's the ordinary "this layer was never attempted" state every
    other helper in this module already handles correctly (``sa is None`` /
    ``pack is None`` short-circuits) -- it is specifically a layer the model
    objects say IS present that the manifest says is only ``partial`` that
    every other check here was blind to.

    This is the one place the manifest's own, authoritative D7/D8 vocabulary
    (``BuildSourceManifest.coverage``'s ``LayerCoverage.status``,
    ``BuildSourceManifest.extractors``'s ``ExtractorRecord.status``) is
    consulted -- every other helper in this module reads the pack's *model*
    objects (``source_abi``/``source_graph``/``build_evidence``) directly,
    which is exactly what missed the ``inline._run_inline_source_abi()``
    clang-unavailable case: a bare ``SourceAbiSurface()`` reads as "L4
    present" to every model-level check, while the manifest ``build_inline_
    coverage()`` already built for this same pack correctly recorded that L4
    row as ``partial`` (see that function's own ``any_entities`` check).
    Reading the manifest here closes that gap without duplicating its logic.

    Finding (review, P1, round 7): the extractor-status scan above only ever
    recognized a ``failed``/``partial`` record whose ``name`` started with
    one of a fixed ``source_abi``/``compile_``/``source_graph`` prefixes --
    but real extractor records outside that allowlist can also record a hard
    ``"failed"`` that invalidates the layer they cover, and this scan was
    silently blind to every one of them. The concrete case that surfaced
    this (Finding 3): ``inline._check_build_info_source_mismatch``
    appends an ``ExtractorRecord(name="build_info_source_tree_mismatch",
    status="failed", ...)`` when most of a compile database's own source
    files are absent from the ``--sources`` tree -- i.e. the build metadata
    and the source checkout may not even be the same codebase -- and that
    name matches none of the three prefixes, so the record was invisible
    here: if the resulting L4 surface still carried ordinary TU accounting
    and no other partial signal tripped, ``analysis_assurance.status`` read
    ``"complete"`` and ``--require-complete-analysis`` exited 0 despite the
    source facts possibly coming from a different checkout entirely. The
    same blind spot also silently missed every other non-prefixed extractor
    family that can legitimately record a hard failure --
    ``build_query``/``build_query_auto``/``bazel`` (a query subprocess
    timing out or failing to run, ``build_query.py``) and
    ``merge_layer_conflict`` (two inputs supplying irreconcilably different
    facts for one layer, ``merge_support.py``) chief among them -- none of
    which start with the old prefixes either. Fixed per the root-cause
    principle (fix the class of failure, not the one instance): a
    ``"failed"`` record ALWAYS invalidates the layer it names, regardless of
    which family produced it, so the prefix allowlist is dropped entirely
    for that status. ``"partial"`` keeps the original, narrower
    ``source_abi``/``compile_``/``source_graph`` prefix scope deliberately
    -- a real, confirmed-complete-but-edge-free L5 graph pass records its own
    ``status="partial"`` for a fully legitimate, non-incomplete reason (see
    :func:`_graph_completeness`'s own ``confirmed`` exemption for that exact
    shape), and broadening ``"partial"`` the same way ``"failed"`` was
    broadened would resurrect that already-fixed false positive here instead
    of in :func:`_graph_completeness`, which is the one place with the
    context (``extractor_passes``/``narrowed_passes``) to tell the two
    apart.
    """
    notes: list[str] = []
    incomplete = False
    watched_layers = (DataLayer.L3_BUILD.value, DataLayer.L4_SOURCE_ABI.value)
    for side, pack in (("old", old_pack), ("new", new_pack)):
        manifest = getattr(pack, "manifest", None)
        if manifest is None:
            continue
        for layer in watched_layers:
            row = manifest.coverage_for(layer)
            if row is None or row.status != CoverageStatus.PARTIAL:
                continue
            incomplete = True
            detail = f": {row.detail}" if row.detail else ""
            notes.append(
                f"{side} side's build-source pack manifest records "
                f"{layer} coverage as 'partial'{detail}"
            )
        for rec in manifest.extractors:
            if rec.status == "failed":
                incomplete = True
                detail = f": {rec.detail}" if rec.detail else ""
                notes.append(
                    f"{side} side's {rec.name!r} extractor status is 'failed'{detail}"
                )
                continue
            if rec.status != "partial":
                continue
            if not rec.name.startswith(("source_abi", "compile_", "source_graph")):
                continue
            incomplete = True
            detail = f": {rec.detail}" if rec.detail else ""
            notes.append(
                f"{side} side's {rec.name!r} extractor status is 'partial'{detail}"
            )
    return incomplete, notes


def _export_accounting(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> ExportAccounting:
    """Roll up total/source-linked/internal/unaccounted export counts across
    BOTH sides -- the export analogue of :func:`_translation_units`'s
    additive TU rollup, not a pick-one-side snapshot.

    Finding (review, P1, round 7): the previous implementation picked ONE
    side's accounting -- new first, falling back to old only when new
    carried no L4 surface at all -- so a comparison where BOTH sides link an
    L4 surface, but only the OLD side has unmatched exports (e.g. a
    baseline-only symbol-mapping gap that was fixed forward on the new
    side), silently returned the new side's clean accounting and never
    examined the old side at all: ``export_accounting.unaccounted`` read 0,
    and with no other partial signal tripping, the overall ``status`` could
    read ``"complete"`` under ``--require-complete-analysis`` despite
    genuinely incomplete baseline linking. Fixed by summing both sides'
    counts additively, mirroring :func:`_translation_units`'s own
    both-sides summation for TU accounting -- a nonzero ``unaccounted`` on
    EITHER side now always contributes to the combined ``export_accounting``
    rather than being silently shadowed by whichever side the old
    "new-first" selection happened to prefer.

    Finding (Codex review, PR #788): once ``source_link.link_source_abi``
    stopped leaving a classified ``non_public`` symbol in
    ``symbols_without_decl`` too (the double-count fix this rollup exists
    alongside), ``source_linked = total - unaccounted`` started silently
    counting every ``internal``-classified export as ALSO ``source_linked``
    -- correct arithmetically (the two no longer sum past ``total``), but
    wrong in what ``source_linked`` claims: a ``dependency:stdlib`` export
    is definitionally not linked to *this* library's own source. Fixed by
    also subtracting ``internal``, so the three categories are now a true
    partition of ``total`` (``source_linked + internal + unaccounted ==
    total``), not just two of the three kept mutually exclusive.

    Finding (Codex review, PR #788): the fix above assumed every
    ``SourceAbiSurface`` reaching this rollup was already produced by the
    fixed ``link_source_abi``/``relink_surface_exports`` (which now folds a
    classified symbol into ``all_matched``, so it can no longer appear in
    ``symbols_without_decl`` too). A *persisted* surface produced by a
    pre-fix build -- the ordinary "compare an existing baseline JSON against
    a fresh candidate" workflow -- round-trips through
    ``SourceAbiSurface.from_dict()`` with that stale overlap still intact:
    the same symbol sits in both ``symbols_without_decl`` and
    ``non_public_symbol_to_reason``. Counting ``len(...)`` of each
    independently double-subtracts that symbol (once as ``unaccounted``,
    once as ``internal``), so ``source_linked`` under-reports (or clamps to
    0) and ``unaccounted`` stays nonzero even though the symbol IS
    genuinely classified -- ``--require-complete-analysis`` still fails on
    this upgrade path. Fixed by deduplicating per side: a symbol already
    classified into ``non_public_symbol_to_reason`` no longer counts toward
    ``unaccounted`` here regardless of whether the surface that produced it
    already excluded it from ``symbols_without_decl`` -- this rollup is now
    correct for both a freshly-linked surface and a legacy persisted one.
    """
    total: int | None = None
    unaccounted: int | None = None
    internal: int | None = None
    for pack in (old_pack, new_pack):
        sa = getattr(pack, "source_abi", None)
        if sa is None:
            continue
        side_total = len(sa.roots.get("exported_symbols", []))
        non_public_keys = set(sa.mappings.get("non_public_symbol_to_reason", {}))
        # Subtract any overlap rather than trusting symbols_without_decl to
        # already be disjoint from non_public_symbol_to_reason -- true for a
        # freshly-linked surface, not guaranteed for one persisted before
        # this fix (see the Codex-review docstring paragraph above).
        side_unaccounted = len(
            set(sa.unmatched.get("symbols_without_decl", [])) - non_public_keys
        )
        side_internal = len(non_public_keys)
        total = (total or 0) + side_total
        unaccounted = (unaccounted or 0) + side_unaccounted
        internal = (internal or 0) + side_internal
    if total is None:
        return ExportAccounting()
    source_linked = max(0, total - (unaccounted or 0) - (internal or 0))
    return ExportAccounting(
        total=total,
        source_linked=source_linked,
        internal=internal,
        unaccounted=unaccounted,
    )


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

    Finding (review, P1): even after the fix above, this function only ever
    consulted ``SourceGraphSummary.degraded_passes``/``narrowed_passes``/
    ``extractor_passes`` -- boolean, per-pass-name coverage flags. It never
    consulted ``BuildSourcePack.manifest.extractors``, the reproducibility
    ledger where each real L5 graph extractor (``call_graph:clang``,
    ``type_graph:clang``, ``include_graph:clang``, ...) records its own
    ``status`` (``ok``/``partial``/``failed``/``skipped``). The two are not
    redundant: ``fold_call_graph`` (``inline_graph_fold.py``) only sets
    ``degraded_passes``/``narrowed_passes`` when the run was unnarrowed with
    diagnostics, or confirmed narrowed -- a pass that ran unnarrowed, hit no
    per-TU diagnostics, but still found zero edges records
    ``status="partial"`` on its own ``ExtractorRecord`` without setting any
    of those three booleans, so it was silently invisible here and could
    read as ``"complete"``. Fixed by also scanning each side's
    ``manifest.extractors`` for a record in one of the recognized L5 graph
    families (see :data:`_L5_GRAPH_EXTRACTOR_FAMILIES`) whose own ``status``
    is not ``"ok"``, folding it into the same ``degraded`` bucket used for
    the pre-existing signals -- a partial/failed/skipped extractor is
    exactly the kind of incomplete evidence ``degraded`` already means.

    Finding (review, P2): that fix over-corrected. A confirmed, fully-run,
    genuinely edge-free pass (e.g. a project with no call/override/template
    edges to find) also records ``status="partial"`` on its own
    ``ExtractorRecord`` -- the real producers key ``status`` off "did this
    pass add any edges", not "did this pass examine everything it was asked
    to". Folding *every* non-``"ok"`` record into ``degraded`` therefore
    treated that legitimate, fully-covered zero-edge case identically to a
    genuine shortfall (a crashed/unavailable extractor), which could fail
    ``--require-complete-analysis`` on a simple, cleanly-examined project.
    Fixed below by exempting a record whose own extractor family is
    separately confirmed complete (``SourceGraphSummary.extractor_passes``)
    or confirmed narrowed (``SourceGraphSummary.narrowed_passes``) on the
    same side -- both are the pass's own stronger "this genuinely ran"
    signal, set unconditionally on success regardless of edge count. A
    record with neither confirmation (a real "failed"/crash, or any other
    unconfirmed case) still folds into ``degraded``, unchanged.
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

    for side, pack, sg in (
        ("old", old_pack, old_graph),
        ("new", new_pack, new_graph),
    ):
        manifest = getattr(pack, "manifest", None)
        records = getattr(manifest, "extractors", None) or []
        for record in records:
            if not _is_l5_graph_extractor_record(record.name):
                continue
            if record.status == "ok":
                continue
            family = record.name.split(":", 1)[0]
            # Finding (review, P2): a real producer (``inline_graph_fold.py``'s
            # ``fold_call_graph``/``fold_type_graph``/... family) stamps
            # ``status="ok" if added else "partial"`` on its own
            # ExtractorRecord -- so a pass that ran over the *whole* project,
            # hit no per-TU diagnostics, and simply found zero edges to add
            # (a legitimately edge-free project: no calls/overrides/templates/
            # etc. to discover) still records "partial" here, even though the
            # SAME call also set ``extractor_passes[family] = True``
            # (confirmed full coverage) or, for a confirmed-narrowed run,
            # ``narrowed_passes[family] = True`` -- both are the pass's own,
            # stronger "this genuinely ran to completion" signal. Treating
            # every non-"ok" record as a shortfall overrides that signal and
            # would fail a simple, fully-examined project under
            # --require-complete-analysis for finding nothing to report. A
            # record whose family was NOT separately confirmed this way (a
            # real crash/missing-tool "failed", or any other case the two
            # confirmation dicts don't cover) still folds into ``degraded``
            # below -- only the confirmed-complete-or-confirmed-narrowed,
            # zero-edge shape is exempted.
            confirmed = sg is not None and (
                sg.extractor_passes.get(family) is True
                or sg.narrowed_passes.get(family) is True
            )
            if confirmed:
                continue
            degraded = True
            detail = f": {record.detail}" if record.detail else ""
            notes.append(
                f"L5 graph extractor {record.name!r} recorded status "
                f"{record.status!r} on the {side} side{detail}"
            )

    # Finding (review, P1, round 8): everything above checks per-side
    # confirmation in isolation -- it never asks whether old and new agree on
    # WHICH pass families they confirmed. A run comparing an old header-only
    # graph (``header_call_graph``/``header_type_graph`` confirmed complete)
    # against a new build-integrated graph (``call_graph``/``type_graph``
    # confirmed complete) has both sides individually "confirmed complete"
    # under every check above, so this function returned ``"complete"`` --
    # but the two sides confirmed *entirely different* family names. The
    # actual cross-snapshot graph diff only ever compares edges within a
    # shared family (``post_processing_reachability._call_graph_fully_trusted``
    # looks up the literal keys ``"call_graph"``/``"type_graph"`` on *each*
    # side independently, and ``source_diff``'s own family-scoped comparisons
    # are the same shape) -- when the confirmed family sets don't overlap at
    # all, there is no family left for that diff to actually compare on both
    # sides, so every edge on both sides goes unexamined against its
    # counterpart despite each side individually looking complete. Detect
    # this by comparing the two sides' own confirmed-family sets (the same
    # ``extractor_passes``/``narrowed_passes`` dicts already read above) and
    # report a total mismatch as ``"unknown"`` rather than ``"complete"`` --
    # folding into ``status="partial"`` under
    # ``--require-complete-analysis`` the same way the other coverage gaps in
    # this function already do, since neither side's individually-confirmed
    # coverage translates into anything the graph diff could actually use.
    #
    # Finding (review, P1, round 9): the fix above only fired on a
    # *disjoint* pair -- ``isdisjoint()`` is true only when the two sets
    # share NO member at all. It missed the partially-overlapping case: old
    # confirmed ``{"call_graph", "type_graph"}``, new confirmed only
    # ``{"call_graph"}``. The two sets are not disjoint (they share
    # ``call_graph``), so the old check left ``graph_completeness`` at
    # whatever the per-side loop above computed (``"complete"`` when nothing
    # else tripped) -- but ``buildsource/source_graph_findings.py`` only
    # ever trusts a family when it is confirmed on BOTH sides (see e.g.
    # ``_family_confirmed``/``_common_dependency_edge_kinds`` in that
    # module), so ``type_graph`` -- confirmed on old, never even attempted
    # on new -- is silently skipped for this comparison exactly the same way
    # a fully-disjoint pair is, just for one family instead of all of them.
    # A subset relationship is still a real coverage gap: whatever family is
    # in one side's confirmed set but not the other's was never examined on
    # both sides, and the cross-snapshot diff cannot compare it. Fixed by
    # comparing the two sets for *any* inequality (``!=``) rather than only
    # a total disjunction -- a subset, superset, or partial-overlap pair are
    # all "the two sides disagree on what they confirmed" just as much as a
    # fully disjoint pair is, and each reports which family/families are
    # confirmed on only one side.
    old_confirmed_families = {
        name for name, ok in (old_graph.extractor_passes or {}).items() if ok
    } | {name for name, ok in (old_graph.narrowed_passes or {}).items() if ok}
    new_confirmed_families = {
        name for name, ok in (new_graph.extractor_passes or {}).items() if ok
    } | {name for name, ok in (new_graph.narrowed_passes or {}).items() if ok}
    asymmetric_family_coverage = False
    if (
        old_confirmed_families or new_confirmed_families
    ) and old_confirmed_families != new_confirmed_families:
        # Finding (P2 review): exclude a family genuinely inapplicable on the side lacking confirmation.
        _cap = _CONDITIONALLY_APPLICABLE_FAMILIES

        def _gap(a: set[str], b: set[str], other: SourceGraphSummary) -> set[str]:
            return {f for f in a - b if _cap.get(f) is None or _cap[f](other)}

        only_old = _gap(old_confirmed_families, new_confirmed_families, new_graph)
        only_new = _gap(new_confirmed_families, old_confirmed_families, old_graph)
        one_sided = sorted(only_old | only_new)
        asymmetric_family_coverage = bool(one_sided)
        if one_sided:
            notes.append(
                f"graph completeness unknown: {one_sided!r} family/families are "
                f"confirmed on only one side (old={sorted(old_confirmed_families)!r}, "
                f"new={sorted(new_confirmed_families)!r}) -- only a family both sides "
                "cover is trusted (buildsource/source_graph_findings.py)"
            )

    if degraded:
        return "degraded", notes
    if asymmetric_family_coverage:
        return "unknown", notes
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

    # -- L0 binary-context status -----------------------------------------------
    l0_context_status, l0_notes = _l0_context_status(old, new)
    notes.extend(l0_notes)

    # -- header-context status ------------------------------------------------
    header_context_status, hc_notes = _header_context_status(result, old, new)
    notes.extend(hc_notes)

    # -- DWARF-context status --------------------------------------------------
    dwarf_context_status, dw_notes = _dwarf_context_status(old, new)
    notes.extend(dw_notes)

    # -- L3 build-evidence context status ---------------------------------------
    l3_context_status, l3_notes = _l3_context_status(old_pack, new_pack)
    notes.extend(l3_notes)

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

    if (export_accounting.unaccounted or 0) > 0:
        notes.append(
            "export_accounting.unaccounted is nonzero: at least one exported "
            "symbol could not be associated with a source declaration on the "
            "side its L4 surface accounts for"
        )
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
        l0_context_status == "asymmetric"
        or header_context_status in ("drift_detected", "asymmetric")
        or dwarf_context_status == "asymmetric"
        or l3_context_status == "asymmetric"
        or graph_completeness in ("degraded", "narrowed", "unknown")
        or (tu_accounting.failed or 0) > 0
        or not result.scope_resolved
        or result.contract_coverage == "partial"
        or fact_set_comparability == "unknown"
        or l4_present_without_accounting
        or manifest_layer_incomplete
        or (export_accounting.unaccounted or 0) > 0
        or target_unresolved
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
        target_accounting=target_accounting,
        translation_units=tu_accounting,
        export_accounting=export_accounting,
        l0_context_status=l0_context_status,
        header_context_status=header_context_status,
        dwarf_context_status=dwarf_context_status,
        l3_context_status=l3_context_status,
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
