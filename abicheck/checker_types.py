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

"""Core data types for checker results.

Extracted from ``checker.py`` to break the circular dependency between
``checker`` and ``suppression`` modules (architecture review Phase 1).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .checker_policy import (
    ChangeKind,
    Confidence,
    EvidenceTier,
    ReachabilityState,
    Verdict,
    policy_kind_sets as _policy_kind_sets,
)
from .contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractAssurance,
    ContractRelevance,
)
from .detectors import DetectorResult
from .impact.model import ImpactAssessment
from .model import AbiSnapshot
from .policy_file import PolicyFile

# Marker appended to a ``SYMBOL_VERSION_ALIAS_CHANGED`` description when the old
# default symbol version is NOT retained as a non-default alias (so consumers of
# the old version fail to resolve). Shared between the producer
# (``diff_platform._diff_symbol_version_aliases``) and the cross-detector dedup
# (``diff_filtering._deduplicate_cross_detector``), which only collapses an
# alias-change into a co-reported node-move in this not-retained case — when the
# old alias IS retained the alias-change is compatible and must survive.
SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER = "old version NOT retained as alias"

# The public evidence-depth ladder (ADR-043 D2/ADR-047 §7): exactly the four
# user-facing rungs, matching the public CLI's ``--depth`` and
# ``abicheck/mcp_server.py``'s own ``_PUBLIC_DEPTHS`` (kept as a separate,
# self-contained copy here rather than importing mcp_server — that module
# sits above this one in the dependency graph). Shared by
# DiffResult.requested_depth/effective_depth and ScanOutcome's matching
# fields (G30 P0.3) so both validate against the same set.
EVIDENCE_DEPTH_VALUES = frozenset({"binary", "headers", "build", "source"})


def validate_evidence_depth(field_name: str, value: str) -> None:
    """Reject a depth spelling outside EVIDENCE_DEPTH_VALUES (G30 P0.3).

    Nothing populates ``requested_depth``/``effective_depth`` yet, but a
    future caller (G30 P1.3) setting a typo'd value would otherwise only be
    caught by the JSON Schema — which production code never runs against
    (only opt-in tests do). Fail fast here instead, at the point the caller
    actually sets the field, matching
    ``mcp_server._validate_public_depth``'s same check on the same set.
    Shared by ``reporter._add_check_identity`` (compare) and
    ``ScanOutcome.to_dict`` (scan) so both validate identically.
    """
    if value not in EVIDENCE_DEPTH_VALUES:
        raise ValueError(
            f"{field_name}: unknown depth {value!r}. "
            f"Valid depths: {sorted(EVIDENCE_DEPTH_VALUES)}"
        )


# A check's full identity (ADR-047 §7): "target@profile#baseline_channel@requested_depth".
# Each of the four components is constrained to a safe identifier charset (no
# further '@'/'#' inside a component) so the delimiter-joined form stays
# unambiguous. Mirrors the ``pattern`` in compare_report.schema.json's
# ``check_id`` property.
CHECK_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9._-]*"
    r"#[A-Za-z0-9][A-Za-z0-9._-]*@(binary|headers|build|source)$"
)


def validate_check_id(value: str) -> None:
    """Reject a check_id that doesn't match CHECK_ID_PATTERN (G30 P0.3).

    Same rationale as ``validate_evidence_depth``: a future caller (G30 P1.3)
    setting a malformed ``check_id`` would otherwise only be caught by the
    JSON Schema, which production code never runs against. Fail fast here
    instead, at the point ``reporter._add_check_identity`` sets the field.
    """
    if not CHECK_ID_PATTERN.match(value):
        raise ValueError(
            f"check_id: malformed value {value!r}. Expected shape "
            "'target@profile#baseline_channel@requested_depth'."
        )


@dataclass
class Change:
    kind: ChangeKind
    symbol: str  # mangled name or type name
    description: str  # human-readable
    old_value: str | None = None
    new_value: str | None = None
    source_location: str | None = None  # "header.h:42" if available
    affected_symbols: list[str] | None = None  # exported functions using this type
    caused_by_type: str | None = None  # root type that makes this change redundant
    caused_count: int = 0  # number of derived changes collapsed into this root
    # Set by EscalateFrozenNamespaceViolations when the change's symbol /
    # caused_by_type matches a namespace declared as "frozen" in the policy
    # file (`frozen_namespaces:`). Carries the matching glob pattern so the
    # reporter can name the policy. Verdict computation blocks any
    # policy_override that would downgrade a change with this field set.
    frozen_namespace_violation: str | None = None
    # Filled in by the source-location enrichment step from the snapshot's
    # function index — the C++-qualified declared name (e.g.
    # ``mylib::detail::r1::dispatch``) for symbols whose ``symbol`` field
    # carries only the mangled/exported form. ``None`` when no matching
    # Function record was found (e.g. type-level changes). Lets namespace
    # selectors match ``extern "C"`` entries whose export name is unqualified.
    qualified_name: str | None = None
    # Set by FilterNonPublicSurface (ADR-024 §D5.1) when --scope-public-headers
    # demotes this finding off the public surface. Carries a stable reason code
    # (e.g. "not-exported", "non-public-type") for the audit ledger. None for
    # in-surface findings and when scoping is off.
    surface_exclusion_reason: str | None = None
    # Per-finding pattern-aware modulation (ADR-025 A4/D4.1). All default to
    # the no-op state, so a snapshot/diff with no modulation behaves exactly as
    # before.
    # - effective_verdict: when set, overrides this finding's *category* (the
    #   verdict it contributes) — consulted by effective_category() at every
    #   classification site. None = classify by ``kind``.
    # - modulation_reason / modulation_rule: the disclosed reason code and the
    #   rule id that produced the override, for the pattern-modulation ledger.
    # - confidence: this finding's own trust level (distinct from the
    #   verdict-level DiffResult.confidence).
    effective_verdict: Verdict | None = None
    modulation_reason: str | None = None
    modulation_rule: str | None = None
    confidence: Confidence = Confidence.HIGH
    # ADR-033 D9 — which build/source evidence bucket this finding belongs to
    # ("build_context" or "source_only"), set when it is produced from L3/L4/L5
    # evidence. Lets the metrics count *retained* (post-suppression) findings per
    # bucket so the D9 split partitions the reported findings. ``None`` for
    # ordinary artifact-backed findings.
    evidence_category: str | None = None
    # ADR-041 P0 roadmap item 2 — set by
    # source_graph_findings._internal_dependency_findings when this
    # PUBLIC_API_INTERNAL_DEPENDENCY_ADDED finding correlates with the *same*
    # public entry's own body/type-hash change this version (an
    # inline_body_changed/template_body_changed/public_typedef_target_changed
    # finding from source_diff.diff_source_abi). Carries that correlated
    # finding's ChangeKind value (e.g. "inline_body_changed") so a JSON/SARIF/
    # policy consumer can act on the correlation directly instead of parsing
    # it out of ``description`` prose. ``None`` when there is no correlated
    # change, or for every finding kind that does not compute one.
    correlated_change_kind: str | None = None
    # ADR-044 D1 — set by the MarkReachability pipeline step, which runs before
    # ApplySuppression so a broad namespace/source_location suppression rule can
    # tell a truly-unreachable internal change apart from one that is part of the
    # effective public ABI. True either when this change's own subject is not
    # internal-namespaced at all (a directly public symbol/type), or when its
    # root type (resolved the same way internal_leak.DetectInternalLeaks
    # resolves it) is an internal type reachable from the public surface in
    # either snapshot per internal_leak.compute_leak_paths.
    public_reachable: bool = False
    # "direct_public_symbol" when the change's own subject is not
    # internal-namespaced at all; "value_embedding" when at least one
    # reachability path embeds an internal type by value or inheritance
    # (internal_leak._path_is_value_propagating); "pointer_or_signature" when
    # reachable only through a pointer/reference/template-argument path. None
    # when public_reachable is False.
    reachability_kind: str | None = None
    # Human-readable rendering (internal_leak._format_path) of the shortest
    # matched reachability path, e.g. "fn:pub → base:detail::Base". None when
    # public_reachable is False.
    reachability_proof_path: str | None = None
    # Tri-state refinement of public_reachable (impact-analysis-layer P0
    # slice). Set by MarkReachability alongside public_reachable:
    # PROVEN_REACHABLE whenever public_reachable is set True;
    # PROVEN_UNREACHABLE when the walk ran and positively found this change
    # not part of the effective public ABI; UNKNOWN when no walk reached a
    # verdict at all, or the only evidence available (the optional L5
    # call/type graph) is itself flagged narrowed/degraded for the relevant
    # edge family. Suppression's default `unreachable-only` gate still keys
    # off public_reachable alone for backward compatibility — only the
    # opt-in `reachability: proven-unreachable-only` rule gate consults this
    # field, refusing to match on UNKNOWN unless the rule also sets
    # `allow_unknown_reachability: true`. Defaults to UNKNOWN, the honest
    # "no evidence" state.
    reachability_state: ReachabilityState = ReachabilityState.UNKNOWN
    # G31 Phase B B3 (ADR-048) — structured graph impact/proof-path data,
    # attached (not duplicated) alongside a finding the L5 graph has relevant
    # reachability evidence for. ``affected_public_roots``: the labels of the
    # public entry node(s) a "graph explain"-style walk found reaching this
    # change's subject. ``impact_proof_path``: the shortest such path as a
    # list of node/edge reference dicts (see
    # ``buildsource.graph_impact.structured_proof_path``) — the structured
    # counterpart of ``reachability_proof_path`` above, not a replacement for
    # it (that field stays the human-readable prose rendering).
    # ``impact_is_direct``: True when the path is a single hop, False when
    # transitive, None when no graph impact data applies to this finding.
    affected_public_roots: list[str] | None = None
    impact_proof_path: list[dict[str, object]] | None = None
    impact_is_direct: bool | None = None
    # ADR-046 D6 (G29 Phase 2 follow-up): the "alternative_paths"/
    # "discarded_path_count" half of the finding shape the ADR calls for
    # alongside the "primary_path" ``impact_proof_path`` above. Set by the
    # same ``attach_impact_metadata`` call, in the same node/edge-dict shape,
    # when more than one candidate path was available and a preference order
    # (``buildsource.graph_impact.select_preferred_graph_path``) picked one
    # as primary — the runner-up(s), capped, not every discarded candidate.
    # ``impact_discarded_path_count`` is how many candidates beyond the kept
    # cap were dropped (0 when every candidate fit).
    impact_alternative_paths: list[list[dict[str, object]]] | None = None
    impact_discarded_path_count: int = 0
    # ADR-052's "stable occurrence_id" follow-up (G29 Phase 3), now buildable
    # on top of ADR-046 D1's occurrence_id half: a hash over the primary
    # proof path's edges' own GraphEdge.occurrences (buildsource.graph_facts.
    # edge_occurrence_id), independent of description text — distinct from
    # finding_id (which deliberately still includes description, see that
    # function's docstring) and from root_cause_id (which needs full-result
    # context this per-Change field can't see, so stays out of scope here).
    # None whenever every edge on the path lacks occurrence-level attrs —
    # still the common case today, since no producer populates them yet.
    impact_occurrence_id: str | None = None
    # G29 Phase 3 slice 2 (ADR-052 follow-up) — set on a change moved into
    # ``DiffResult.suppressed_changes`` (``checker._filter_suppressed_changes``/
    # ``_filter_pattern_synthetic``, ``post_processing.ApplySuppression``) to
    # the matching ``Suppression`` rule's ``label`` (falling back to
    # ``reason`` when the rule has no label — both are free-form and either
    # may be unset, so this can still be ``None``). Feeds
    # ``impact.model.FindingDecision.suppression_rule`` — the piece ADR-052
    # slice 1 deliberately left unwired. Never set for a change that stays
    # visible, nor for the appcompat.py/cli_compare_helpers.py consumer/
    # runtime overlays a suppression rule discards outright (those never
    # reach ``suppressed_changes`` at all).
    suppression_rule: str | None = None
    # ADR-052 D2 follow-up (G29 Phase 3, scoped implementation) — a
    # producer's own directly-constructed ``ImpactAssessment``, when the
    # producer builds one itself instead of leaving ``impact.engine.
    # assess_change`` to derive it later from the flat fields above. Only
    # ``internal_leak._build_leak_change``/``_build_call_graph_leak_change``
    # set this today (see those functions' docstrings for why they are safe
    # to cache: nothing later in ``post_processing.DEFAULT_PIPELINE``
    # mutates a leak finding's own reachability/evidence fields after
    # construction). ``impact.engine.assess_change`` reuses this object's
    # *evidence* fields (reachability/proof-path/confidence/
    # evidence_category/correlated_change_kind) when present, but always
    # recomputes ``decision``/``root_cause_id`` fresh from this ``Change``'s
    # current flat-field state — those can still change after construction
    # (suppression, pattern modulation), so a cached ``decision`` would risk
    # going stale. ``None`` (the default) for every producer that has not
    # yet been migrated — the flat fields above remain the sole source of
    # truth for those, exactly as before this field existed.
    impact_assessment: ImpactAssessment | None = None
    # ADR-049's contract evaluator (opt-in `compare(...,
    # contract_evaluation=True)`) — `contract_evaluation.
    # evaluate_snapshot_pair_contract_relevance`'s per-finding decision,
    # flattened into three fields (mirroring every other producer-attached
    # enrichment above) rather than a single nested
    # `ContractEvaluationDecision` object: `checker_types.py` cannot import
    # that dataclass from `contract_evaluation.py` without a circular
    # import (`contract_evaluation.py` itself imports `Change` from this
    # module), but `ContractRelevance`/`ContractAssurance` live in the
    # dependency-free leaf module `contract_relevance_types.py`, which this
    # module can safely import. Structurally additive, but no longer inert:
    # since ADR-049 Phase 7 `contract_gating.evaluation_status_of` falls back
    # to `contract_relevance` when `compatibility_evaluation_status` below is
    # unset, so this field can decide whether compatibility policy and the
    # change gate score the finding at all. `None` for every finding when the
    # caller didn't opt in (the default) -- and an unstamped finding is
    # evaluated, which is what keeps that default path unchanged.
    contract_relevance: ContractRelevance | None = None
    contract_reason_code: str | None = None
    contract_assurance: ContractAssurance | None = None
    # ADR-049 Phase 3's provider-evidence ledger (plan Section 4.1): the ids
    # of the `contract_evidence` provider records this finding's decision
    # actually rests on (`contract_evidence_collect.evidence_refs_for_reason`),
    # or a run-level reference (`RUN_LEVEL_EVIDENCE_REFS`) for a decision made
    # outside `compare()` by a caller that holds no evidence block -- the
    # `--used-by`/`--required-symbol` scoped stamp. `None` when contract
    # evaluation was not requested; `()` is a real value, meaning "this
    # decision consulted no provider" (a non-entity finding, whose relevance
    # follows from its `ChangeKind` alone). Kept as a flat tuple for the same
    # circular-import reason as the three fields above.
    contract_evidence_refs: tuple[str, ...] | None = None
    # ADR-049 D1's other half of the canonical per-finding shape, and the one
    # that makes the contract decision *authoritative* rather than shadow:
    # whether compatibility policy ran for this finding, and what it decided.
    # `IN_CONTRACT`/`NOT_APPLICABLE` findings are EVALUATED and carry their
    # `Verdict`; the other three relevance values are NOT_EVALUATED and carry
    # `None` -- which is JSON `null` in reports, deliberately *not* a new
    # compatibility verdict meaning "fine". A NOT_EVALUATED finding also
    # contributes nothing to the change gate (`contract_gating.py`), while
    # staying fully present in `DiffResult.changes` and every audit ledger:
    # ADR-049 D9 conserves every detector fact in exactly one visible outcome.
    # Both are `None` for a run that never opted into contract evaluation (the
    # default), which is what keeps the legacy pipeline bit-for-bit unchanged
    # -- `contract_gating.is_evaluated` reads an unstamped finding as
    # evaluated. Stamped by `contract_pipeline.ContractEvaluationStage`, in
    # `checker.compare`, *before* the verdict is computed.
    compatibility_evaluation_status: CompatibilityEvaluationStatus | None = None
    compatibility_decision: Verdict | None = None
    # Set by diff_types._diff_type_vtable on a TYPE_VTABLE_CHANGED finding
    # when it rests on the identical asymmetric-layout-evidence gap
    # LAYOUT_UNVERIFIABLE (diff_layout.py) reports for the same type. Purely
    # an internal cross-detector correlation key for
    # post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged, which
    # sets the co-located LAYOUT_UNVERIFIABLE finding's own
    # ``correlated_change_kind`` from it — deliberately NOT
    # ``modulation_reason``/``modulation_rule`` (Codex review): this finding's
    # own severity is never touched, so tagging it as a "modulation" would be
    # a false audit entry (a public field reporters and
    # ``impact.engine.assess_change()`` expose as a real verdict-modulation
    # reason code) for a finding whose verdict never changed. Also
    # deliberately never used to *remove* either finding from ``changes``
    # (three earlier revisions tried variants of that and each was found
    # unsafe by review — see AGENTS.md's "Findings emitted from absent
    # evidence" entry for the full account). ``False`` for every ordinary
    # TYPE_VTABLE_CHANGED and every other finding kind.
    #
    # ``field(kw_only=True)``, per-field rather than the ``dataclasses.
    # KW_ONLY`` sentinel (Codex review, fresh evidence): ``Change`` is
    # documented as a public Python-API type (checker_types.py's own module
    # docstring; CLAUDE.md: "changing their public surface is a breaking
    # change to the Python API — coordinate it"), and a whole-class
    # ``KW_ONLY`` marker placed after ``description`` would make every
    # pre-existing optional field keyword-only too — breaking any external
    # caller that previously passed ``old_value``/``new_value``/etc.
    # positionally, which this codebase cannot audit (only its own call
    # sites, all of which already pass every field beyond the first three by
    # keyword). Appended at the very end of the dataclass instead of
    # mid-list, for the same reason `field(kw_only=True)` alone doesn't
    # already make position irrelevant: a plain field inserted anywhere
    # earlier still shifts every later *positional* argument for a
    # hypothetical caller reaching that far — appending keeps every
    # pre-existing field's position, and therefore every pre-existing
    # positional caller's behavior, completely unchanged. Mirrors
    # ``AbiSnapshot``'s identical per-field fix in PR #582 exactly (that
    # precedent predates this dataclass's own instance of the same bug).
    vtable_covers_unverifiable_layout_gap: bool = field(default=False, kw_only=True)
    # ELF symbol linkage (Function.elf_binding / Variable.elf_binding's value
    # string — "global"/"weak"/"local"/"unique"/"other") of the removed
    # symbol, stamped by diff_symbols._check_removed_function/_var_removed on
    # FUNC_REMOVED/FUNC_REMOVED_ELF_ONLY/VAR_REMOVED findings only. None for
    # every other kind, and None here too when the symbol's binding was never
    # captured (non-ELF platform, older snapshot, ELF metadata without a
    # matching .dynsym entry). Exists so a suppression rule's ``binding:``
    # selector (suppression.py) can narrow a removal rule to the common
    # WEAK-COMDAT-inline case. Provider-side evidence only, NOT proof of
    # safety: it names the *library's own build*'s linkage, not whether
    # every consumer already carries its own copy — see AGENTS.md's
    # "Linkage-blind removal" entry (the extern-template counterexample) and
    # Suppression.binding's own docstring, which carries the full caveat.
    # Deliberately a plain string, not
    # the ``SymbolBinding`` enum itself: mirrors ``old_value``/``new_value``
    # and every other string-typed selector-facing field already on this
    # class, and keeps ``model.py``'s ``elf_metadata`` dependency from
    # leaking into this dataclass's own import surface. Same
    # ``field(kw_only=True)``-appended-last convention as
    # ``vtable_covers_unverifiable_layout_gap`` immediately above, for the
    # identical reason (Change is public API; see that field's own comment).
    symbol_binding: str | None = field(default=None, kw_only=True)


@dataclass
class LibraryMetadata:
    """File-level metadata for a library artifact (path, hash, size).

    The optional ``tbb_interface_version`` field captures
    ``TBB_INTERFACE_VERSION`` from oneTBB's ``oneapi/tbb/version.h`` when
    a TBB-shaped header set is supplied to the dumper. It is reported as
    a first-class signal in ``appcompat`` so users can spot
    forward-compatibility violations (binary's
    ``TBB_runtime_interface_version()`` < headers' compile-time
    ``TBB_INTERFACE_VERSION``) without having to read the symbol table.
    None when the dumper did not see a TBB version header.
    """

    path: str  # file path as given on the CLI
    sha256: str  # hex digest
    size_bytes: int  # file size in bytes
    tbb_interface_version: int | None = None


@dataclass
class DiffResult:
    old_version: str
    new_version: str
    library: str
    changes: list[Change] = field(default_factory=list)
    verdict: Verdict = Verdict.NO_CHANGE
    suppressed_count: int = 0
    suppressed_changes: list[Change] = field(default_factory=list)  # full audit trail
    suppression_file_provided: bool = (
        False  # True when --suppress was passed, even if 0 matched
    )
    detector_results: list[DetectorResult] = field(default_factory=list)
    policy: str = (
        "strict_abi"  # active policy profile; drives breaking/source_breaks/compatible
    )
    policy_file: PolicyFile | None = None  # custom policy with overrides (Bug 4)
    old_metadata: LibraryMetadata | None = None
    new_metadata: LibraryMetadata | None = None
    redundant_changes: list[Change] = field(
        default_factory=list
    )  # hidden by redundancy filter
    redundant_count: int = 0
    old_symbol_count: int | None = None  # public exported symbol count in old library
    # Evidence tier and confidence — helps users assess how much trust to
    # place in the verdict.  "high" means multiple evidence sources agree;
    # "low" means key detectors were disabled (e.g., DWARF stripped).
    confidence: Confidence = Confidence.HIGH
    evidence_tiers: list[str] = field(
        default_factory=list
    )  # e.g. ["elf", "dwarf", "header"]
    coverage_warnings: list[str] = field(
        default_factory=list
    )  # human-readable coverage gaps
    # ADR-024: findings excluded because they are not on the public-header
    # ABI surface (only populated when scope_to_public_surface is enabled).
    # Recorded for audit — surfaced under --show-filtered — never dropped.
    out_of_surface_changes: list[Change] = field(default_factory=list)
    out_of_surface_count: int = 0
    # ADR-039 — findings suppressed as context-free header-parse artifacts once
    # build context (active ``-D`` defines + per-field ``guard`` annotations)
    # proved the field's real presence is identical across both builds. Recorded
    # for audit — surfaced under --show-filtered — never silently dropped. Only
    # populated when ``reconcile_build_context`` was enabled and build evidence
    # was present; empty otherwise. Excluded from the verdict.
    reconciled_changes: list[Change] = field(default_factory=list)
    reconciled_count: int = 0
    scope_to_public_surface: bool = False
    # False only when --scope-public-headers was requested but the public
    # surface could not be resolved, so scoping fell back to the full export
    # table. A False value means compatibility is *unconfirmed* and the result
    # needs manual review — it must never read as a confidently-clean public
    # surface (issue #235).
    scope_resolved: bool = True
    # ADR-024 §D5.3 — structured confidence in the surface resolution itself
    # (distinct from ``confidence`` above, which is the overall verdict trust).
    # "high" with no notes = clean header-scoped run; "reduced" with one or more
    # structured note codes (e.g. "mangling-fallback", "no-provenance") when the
    # surface had to be resolved less reliably. Disclosed in the JSON/SARIF
    # surface ledger so the "demote + disclose" promise stays auditable.
    surface_scope_confidence: str = "high"
    surface_scope_notes: list[str] = field(default_factory=list)
    # Canonical analysis depth (ordered): ELF_ONLY < DWARF_AWARE < HEADER_AWARE.
    # Distinct from the raw ``evidence_tiers`` list above — this is the single
    # scalar consumers should key trust decisions off of. See EvidenceTier.
    evidence_tier: EvidenceTier = EvidenceTier.ELF_ONLY
    # ADR-027 A4 — pattern-aware verdict modulation ledger. Each entry records a
    # demotion/raise the pattern pass made (symbol, original→new category, the
    # rule id, the disclosed reason, the evidence tier, and the graph edges that
    # matched). Empty unless --pattern-verdicts was enabled. Findings themselves
    # carry the override on Change.effective_verdict; this is the audit trail.
    pattern_modulations: list[dict[str, object]] = field(default_factory=list)
    # ADR-028 D7 — evidence-coverage rows (L0–L5) for the compare, when an
    # BuildSourcePack was supplied. Each entry is a serialized LayerCoverage
    # ({layer, status, confidence, detail}). Surfaced in the JSON report so
    # machine consumers can tell artifact-proven from build-context-only
    # findings; empty when no evidence was involved.
    layer_coverage: list[dict[str, object]] = field(default_factory=list)
    # ADR-033 D6/D9 — evidence-collection timing and observability metrics for
    # the compare. Populated only when build-info/source facts were involved
    # (mirrors ``layer_coverage``). Keys follow the D9 metric names (e.g.
    # ``extractor.duration_seconds``, ``findings.source_only.count``); surfaced
    # in the JSON report so CI can tune mode selection. Empty otherwise.
    evidence_metrics: dict[str, object] = field(default_factory=dict)
    # ADR-047 §7 report-identity envelope (G30 P0.3) — optional, additive.
    # Nothing in the CLI/service layer populates these yet; they exist so the
    # GitHub Actions integration-model primitives planned in G30 P1
    # (``resolve-baseline``, ``check-target``) have a report-level place to
    # record a check's identity once they're built. None means "not set by
    # this caller" and the field is omitted from the JSON report entirely —
    # never emitted as a null/empty placeholder.
    check_id: str | None = None  # "target@profile#baseline_channel@requested_depth"
    profile_id: str | None = None  # e.g. "linux-x86_64-gcc13-release"
    requested_depth: str | None = None  # one of EVIDENCE_DEPTH_VALUES
    effective_depth: str | None = None  # one of EVIDENCE_DEPTH_VALUES
    baseline_channel: str | None = None  # e.g. "accepted-main", a release tag
    # ADR-050 D2 — set when exactly one side of the compare carried an
    # ExtractionContract (a genuinely mixed pair: e.g. a fresh header-AST
    # dump compared against a pre-ADR-050 stored baseline, or a symbols-only
    # side against a full L2 side). None on the ordinary case (both or
    # neither side carries a contract) — this is report-level metadata, not
    # a ChangeKind/Change finding, so it is structurally unreachable by any
    # --severity-* promotion. "partial" is currently the only recognized
    # value.
    contract_coverage: Literal["partial"] | None = None
    # ADR-050 D2 — set to "none" only when --diagnostic-comparison forced a
    # tentative diff through past a genuine contract mismatch that would
    # otherwise have raised ProfileMismatchError/ScopeMismatchError. Applies
    # to the whole DiffResult (the gate failed for the pair as a whole
    # before any diff ran), not per-Change.
    assurance: Literal["none"] | None = None
    # ADR-049 Phase 4 — the three persisted blocks (``contract_evidence`` /
    # ``evaluation_context`` / ``decision_receipt``) for this comparison,
    # assembled by ``contract_context.build_persisted_context``. Populated
    # only under ``compare(..., contract_evaluation=True)``; ``None``
    # otherwise, and omitted from the JSON report entirely rather than
    # emitted as a null placeholder. Typed as ``object`` for the same
    # circular-import reason as ``Change.contract_relevance``'s flat fields:
    # ``contract_evidence.py`` reaches ``compatibility_evaluation_config.py``
    # -> ``checker_policy.py``, which this module also imports, and a real
    # annotation here would pull that whole chain into every consumer of
    # ``DiffResult``. Audit data as far as *compatibility* policy and the
    # change gate are concerned -- they read the per-finding fields above,
    # never this block -- but not inert: ``contract_coverage_exit.
    # fold_coverage_exit`` derives the orthogonal coverage contribution from
    # it, so a run carrying one can exit ``1`` on that axis (ADR-049 §7).
    contract_context: object | None = None

    def _effective_kind_sets(
        self,
    ) -> tuple[
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
    ]:
        """Return (breaking, api_break, compatible, risk) kind sets with overrides applied."""
        breaking, api_break, compatible, risk = _policy_kind_sets(self.policy)
        if not self.policy_file or not self.policy_file.overrides:
            return breaking, api_break, compatible, risk

        # Apply overrides: move kinds between sets
        b, a, c, r = set(breaking), set(api_break), set(compatible), set(risk)
        _VERDICT_TO_SET_IDX = {
            Verdict.BREAKING: 0,
            Verdict.API_BREAK: 1,
            Verdict.COMPATIBLE: 2,
            Verdict.COMPATIBLE_WITH_RISK: 3,
        }
        sets = [b, a, c, r]
        for kind, verdict in self.policy_file.overrides.items():
            # Remove from all sets
            for s in sets:
                s.discard(kind)
            # Add to target set
            idx = _VERDICT_TO_SET_IDX.get(verdict)
            if idx is not None:
                sets[idx].add(kind)
        return frozenset(b), frozenset(a), frozenset(c), frozenset(r)

    def _effective_verdict_for_change(self, change: Change) -> Verdict:
        """Return the per-change verdict, including frozen namespace guards."""
        from .severity import effective_verdict_for_change

        return effective_verdict_for_change(
            change,
            policy=self.policy,
            kind_sets=self._effective_kind_sets(),
            policy_file=self.policy_file,
        )

    def _evaluated_changes(self) -> list[Change]:
        """The findings compatibility policy actually classified (ADR-049 D1).

        The four verdict buckets below are the *compatibility* axis, and the
        overall ``verdict`` is computed from exactly this set — so a finding
        contract evaluation left ``NOT_EVALUATED`` must not appear in them,
        or a report would count a "breaking change" the verdict it sits next
        to deliberately did not score. Such findings are not lost: they are
        still in ``changes``, they are listed by :attr:`not_evaluated`, and
        every renderer discloses them with their relevance and reason.

        Without ``--contract-evaluation`` no finding carries a relevance at
        all, so this returns ``changes`` unchanged and every bucket is
        exactly what it was before ADR-049.
        """
        from .contract_gating import is_evaluated

        return [c for c in self.changes if is_evaluated(c)]

    @property
    def not_evaluated(self) -> list[Change]:
        """Findings compatibility policy did not score (ADR-049 D1).

        ``PROVEN_OUT_OF_CONTRACT``, ``UNKNOWN_UNPROVEN`` and
        ``UNKNOWN_UNRESOLVED`` findings, in ``changes`` order. Empty for
        every run that did not opt into ``--contract-evaluation``.
        """
        from .contract_gating import is_evaluated

        return [c for c in self.changes if not is_evaluated(c)]

    @property
    def breaking(self) -> list[Change]:
        """Changes classified as BREAKING under the active policy."""
        return [
            c
            for c in self._evaluated_changes()
            if self._effective_verdict_for_change(c) == Verdict.BREAKING
        ]

    @property
    def source_breaks(self) -> list[Change]:
        """Changes classified as API_BREAK under the active policy."""
        return [
            c
            for c in self._evaluated_changes()
            if self._effective_verdict_for_change(c) == Verdict.API_BREAK
        ]

    @property
    def compatible(self) -> list[Change]:
        """Changes classified as COMPATIBLE under the active policy."""
        return [
            c
            for c in self._evaluated_changes()
            if self._effective_verdict_for_change(c) == Verdict.COMPATIBLE
        ]

    @property
    def risk(self) -> list[Change]:
        """Changes classified as COMPATIBLE_WITH_RISK under the active policy."""
        return [
            c
            for c in self._evaluated_changes()
            if self._effective_verdict_for_change(c) == Verdict.COMPATIBLE_WITH_RISK
        ]


@dataclass(frozen=True)
class DetectorSpec:
    """Specification for a single ABI change detector.

    Renamed from ``_DetectorSpec`` during architecture review Phase 1
    to serve as the official detector interface.
    """

    name: str
    run: Callable[[AbiSnapshot, AbiSnapshot], list[Change]]
    is_supported: (
        Callable[[AbiSnapshot, AbiSnapshot], tuple[bool, str | None]] | None
    ) = None

    def support(self, old: AbiSnapshot, new: AbiSnapshot) -> tuple[bool, str | None]:
        if self.is_supported is None:
            return True, None
        return self.is_supported(old, new)
