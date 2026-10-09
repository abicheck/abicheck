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


"""A single observed ABI/API change and the pure data that travels with it.

``Change`` is the finding record every detector produces and every later
stage reads. It is model data: it carries the policy's eventual decisions
(``effective_verdict``, ``compatibility_decision``, ...) as fields, but never
computes them. Deciding a verdict belongs to ``policy``; aggregating findings
into a comparison result is :class:`abicheck.checker_types.DiffResult`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractAssurance,
    ContractRelevance,
)
from .change_catalog.kinds import ChangeKind
from .change_catalog.registry import Verdict
from .evidence_status import (
    Confidence,
    CrossSourceEvolution,
    FindingEvolution,
    ReachabilityState,
)
from .identity import EntityId
from .snapshot import AbiSnapshot

if TYPE_CHECKING:
    from ..impact.model import ImpactAssessment

# Marker appended to a ``SYMBOL_VERSION_ALIAS_CHANGED`` description when the old
# default symbol version is NOT retained as a non-default alias (so consumers of
# the old version fail to resolve). Shared between the producer
# (``diff_platform._diff_symbol_version_aliases``) and the cross-detector dedup
# (``diff_filtering._deduplicate_cross_detector``), which only collapses an
# alias-change into a co-reported node-move in this not-retained case — when the
# old alias IS retained the alias-change is compatible and must survive.
SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER = "old version NOT retained as alias"

# The public evidence-depth ladder (ADR-043 D2/ADR-047 §7): exactly the four
# user-facing rungs, matching the public CLI's ``--depth`` (the now-removed
# MCP server mirrored this as its own ``_PUBLIC_DEPTHS``, per ADR-021).
# Shared by DiffResult.requested_depth/effective_depth and ScanOutcome's
# matching fields (G30 P0.3) so both validate against the same set.
EVIDENCE_DEPTH_VALUES = frozenset({"binary", "headers", "build", "source"})


def validate_evidence_depth(field_name: str, value: str) -> None:
    """Reject a depth spelling outside EVIDENCE_DEPTH_VALUES (G30 P0.3).

    ``cli_compare_helpers._report_compare_result`` is the first real caller
    to populate ``requested_depth`` (P0.4 round 9, from the CLI's own
    Click-validated ``--depth`` string, which is already restricted to this
    exact set — see ``cli_params.DepthParam``), so a typo'd value reaching
    this field is not expected in practice today; kept as a fail-fast check
    for any future caller (G30 P1.3) that sets it from a less-validated
    source, since a bad value would otherwise only be caught by the JSON
    Schema — which production code never runs against (only opt-in tests
    do). Shared by ``reporter._add_check_identity`` (compare) and
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
# unambiguous. Same accepted-string set as the ``pattern`` in
# compare_report.schema.json's ``check_id`` property -- not the identical
# regex text, since that pattern is published for external, cross-language
# (ECMAScript) consumers and so uses a portable ``$(?!\n)`` end-assertion
# instead of this module's Python-only ``\Z`` (Codex review, fresh
# evidence: ``\Z`` is not a valid ECMAScript escape -- Ajv either rejects it
# or treats it as a literal 'Z').
#
# G42 adds two further optional, composable tail segments, in this fixed
# order: "!<environment_id>" (a named-environment qualifier -- reserved here,
# not yet produced by any generator) and "~<explicit_id>" (a project-author-
# supplied checks[].id). Absent both, this pattern accepts exactly what it
# accepted before G42. Mirrors
# ``abicheck.workflows.aggregate.contracts._CHECK_ID_RE`` -- the two must
# extend in lockstep (see that module's own comment for why).
#: Anchored with ``\Z``, not a trailing ``$`` -- without ``re.MULTILINE``,
#: ``$`` also matches just before a trailing ``\n`` (Codex review; see
#: ``project_targets._IDENTIFIER_RE``'s identical fix for the full
#: rationale), which would let a constructed check_id carrying an embedded
#: newline (e.g. from an unvalidated explicit_id) slip past this check.
CHECK_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9._-]*"
    r"#[A-Za-z0-9][A-Za-z0-9._-]*@(binary|headers|build|source)"
    r"(?:![A-Za-z0-9][A-Za-z0-9._-]*)?"
    r"(?:~[A-Za-z0-9][A-Za-z0-9._-]*)?\Z"
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
    # proof path's edges' own GraphEdge.occurrences (model.graph_facts.
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
    # since ADR-049 Phase 7 `contract_finding_relevance.evaluation_status_of` falls back
    # to `contract_relevance` when `compatibility_evaluation_status` below is
    # unset, so this field can decide whether compatibility policy and the
    # change gate score the finding at all. `None` for every finding when the
    # caller didn't opt in (the default) -- and an unstamped finding is
    # evaluated, which is what keeps that default path unchanged.
    contract_relevance: ContractRelevance | None = None
    contract_reason_code: str | None = None
    contract_assurance: ContractAssurance | None = None
    # ADR-049 Phase 3's provider-evidence ledger (plan Section 4.1): ids of
    # the `contract_evidence` provider records this finding's decision rests
    # on, or a run-level reference (`RUN_LEVEL_EVIDENCE_REFS`) for a decision
    # made outside `compare()` (the `--used-by`/`--required-symbol` stamp).
    # `None` when not requested; `()` = "consulted no provider". Flat tuple,
    # same circular-import reason as above.
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
    # -- `contract_finding_relevance.is_evaluated` reads an unstamped finding as
    # evaluated. Stamped by `contract_pipeline.ContractEvaluationStage`, in
    # `checker.compare`, *before* the verdict is computed.
    compatibility_evaluation_status: CompatibilityEvaluationStatus | None = None
    compatibility_decision: Verdict | None = None
    # Set by diff_types_vtable._diff_type_vtable on a TYPE_VTABLE_CHANGED finding
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
    # ``field(kw_only=True)`` per-field, not the whole-class ``dataclasses.
    # KW_ONLY`` sentinel (Codex review): ``Change`` is public API (CLAUDE.md:
    # "changing their public surface is a breaking change... coordinate
    # it"), and a class-wide ``KW_ONLY`` marker after ``description`` would
    # make every pre-existing optional field keyword-only too, breaking an
    # external caller that passed one positionally. Appended at the very
    # end, not mid-list, since ``kw_only=True`` alone doesn't stop a later
    # *positional* argument from shifting — only position preserves that.
    # Mirrors ``AbiSnapshot``'s identical fix in PR #582.
    vtable_covers_unverifiable_layout_gap: bool = field(default=False, kw_only=True)
    # ELF symbol linkage (Function.elf_binding / Variable.elf_binding's value
    # string — "global"/"weak"/"local"/"unique"/"other") of the removed (or
    # visibility-hidden) symbol, stamped by
    # diff_symbols._check_removed_function/_var_removed on
    # FUNC_REMOVED/FUNC_REMOVED_ELF_ONLY/VAR_REMOVED/FUNC_VISIBILITY_CHANGED
    # findings (the old side's binding), and by
    # diff_platform._diff_elf_deleted_fallback on FUNC_DELETED_ELF_FALLBACK.
    # None for every other kind, and None here too when the symbol's binding
    # was never captured. Exists so a suppression rule's ``binding:``
    # selector (suppression.py) can narrow a removal rule to the common
    # WEAK-COMDAT-inline case. Provider-side evidence only, NOT proof of
    # safety — see AGENTS.md's "Linkage-blind removal" entry. Deliberately a
    # plain string, not the ``SymbolBinding`` enum itself, mirroring
    # ``old_value``/``new_value``. Same ``field(kw_only=True)``-appended-last
    # convention as ``vtable_covers_unverifiable_layout_gap`` above, since
    # Change is public API.
    symbol_binding: str | None = field(default=None, kw_only=True)
    # Structured field identity for a field-level Change ("x" in "Widget::x"),
    # stamped by the six TYPE_FIELD_*/STRUCT_FIELD_* emitters
    # (diff_types.py/diff_platform.py) so diff_filtering._dedup_cross_kind's
    # parent-type match can require field agreement instead of dropping any
    # DWARF field finding whose *type* has an AST-tier finding, regardless of
    # *which* field changed (Codex review). None for every other kind.
    field_name: str | None = field(default=None, kw_only=True)
    # G39 Phase 0: evidence tier(s) producing this finding, same shape as
    # contract_evidence_refs. Same field(kw_only=True)-appended-last
    # convention as symbol_binding above (Change is public API).
    evidence_provenance: tuple[str, ...] | None = field(default=None, kw_only=True)
    # ADR-063 Phase 2 (finding_identity.py algorithm migration, second half):
    # the compare-time EntityId this finding is about -- the OLD side's when
    # it exists (REMOVED-shaped finding has none), else the NEW side's
    # (ADDED-shaped), mirroring symbol_binding's own old-side convention
    # above. None when unresolved; not yet read by any consumer.
    # `compare=False` like the declaration-side carriers -- identical-content
    # findings stay equal regardless of identity coverage (Codex review).
    # Same field(kw_only=True)-appended-last convention as evidence_provenance.
    entity_id: EntityId | None = field(default=None, kw_only=True, compare=False)
    disambiguator: str | None = field(default=None, kw_only=True, compare=False)
    # ADR-068 Phase 1 item 2 (`one-comparison-product.md`): where this
    # finding sits across a chain of more than one comparison -- see
    # `checker_policy.FindingEvolution`'s own docstring for the full
    # contract. `compare()` itself never sets this; it is populated by a
    # dedicated N>1-comparison consumer (`workflows/history.py` and future
    # siblings), which is also why it defaults to NOT_EVALUATED rather than
    # PERSISTENT -- a finding nobody classified is not silently assumed
    # unchanged (ADR-067 D3's `not_evaluated` convention).
    # `compare=False` like `entity_id`/`disambiguator` above: two otherwise
    # identical findings stay the "same" finding for dedup/equality purposes
    # regardless of which comparison-chain context annotated their evolution.
    evolution: FindingEvolution = field(
        default=FindingEvolution.NOT_EVALUATED, kw_only=True, compare=False
    )
    # ADR-068 D3 / plan P2 -- OLD->NEW evolution for a one-sided cross-source
    # finding *within this one compare() call* (see CrossSourceEvolution --
    # not the cross-comparison-chain `evolution` field above). None for
    # every ordinary finding.
    cross_source_evolution: CrossSourceEvolution | None = field(
        default=None, kw_only=True
    )
    # ADR-068 D3 (plan §6 Phase 2d): this finding comes from a check that is
    # meaningful only on the *candidate* (NEW) side -- today the ``--abi3``
    # stable-ABI audit (workflows/abi3_audit.py) -- so it rides the same
    # result document as the comparison it enriches, "marked as such" rather
    # than split into a second result. Never set for a two-sided finding.
    # Same field(kw_only=True)-appended-last convention as
    # `cross_source_evolution` above: it is the newest field, so it goes
    # after it, never between two existing ones
    # (`tests/test_evidence_provenance_completeness.py` pins the order).
    candidate_side_enrichment: bool = field(default=False, kw_only=True)
    # Codex review, item 8: a human-readable demangling of `symbol`/
    # `old_value`, populated only when the underlying declaration is
    # export-table-only (`Visibility.ELF_ONLY` -- no header confirmation,
    # so `Function.name`/`Variable.name` is never demangled the way a
    # header-backed declaration's already is; see
    # `extract.export_symbol_identity.itanium_export_function`'s own
    # docstring). Stamped by `diff_symbols._check_removed_function`/
    # `_var_removed` on FUNC_REMOVED_ELF_ONLY/VAR_REMOVED findings whose old
    # side is ELF-only. None for every other finding, including a
    # header-backed FUNC_REMOVED/VAR_REMOVED (whose `old_value` is already
    # demangled text) and any case where demangling the mangled name
    # produces nothing new. A machine format (JSON/SARIF/JUnit, per
    # `demangle.demangle_text`'s own docstring) is otherwise "raw symbols
    # only" by design -- this field is the one deliberate exception: a
    # reader who never opts into a human-facing render still gets a
    # readable name to look at, without the machine format losing the raw
    # `symbol`/`old_value` it needs for matching. Same
    # field(kw_only=True)-appended-last convention as
    # `candidate_side_enrichment` above.
    demangled_symbol: str | None = field(default=None, kw_only=True)
    # ``model/surface_facts.surface_fact_summary()``'s output for this
    # finding's declaration, verbatim (each value "true"/"false"/"unknown",
    # never an omitted key); ``None`` when no single declaration is behind
    # the finding. Appended last, keyword-only, like `demangled_symbol`.
    surface_facts: dict[str, str] | None = field(default=None, kw_only=True)
    #: The concrete ``ChangeEntity`` *value* for a finding whose kind is
    #: polymorphic -- one a detector emits for more than one entity type.
    #: ``None`` for every monomorphic kind. Set by the detector that knows,
    #: read via ``ChangeKindMeta.entity_from_field``, which is where the
    #: full account lives. It must be a real, persisted field: pointing that
    #: mechanism at ``make_change``'s ``detail`` *argument* (not stored)
    #: made it a no-op on every production finding.
    entity_discriminator: str | None = field(default=None, kw_only=True)
    review_evidence: dict[str, object] | None = field(
        default=None, kw_only=True, compare=False
    )
    #: The evidence providers that corroborated a cross-source (audit) finding
    #: -- ``run_crosschecks``' per-check ``providers`` list, e.g.
    #: ``("public_header_ast", "source_index")``. Lets a report state *which*
    #: evidence agreed, so dropping a provider while still emitting the
    #: finding is visible (catalog case151's provider matrix). ``None`` for
    #: every finding not produced by a cross-source check. Appended last,
    #: keyword-only, not part of equality, like `review_evidence`.
    cross_source_providers: tuple[str, ...] | None = field(
        default=None, kw_only=True, compare=False
    )


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


@dataclass(frozen=True)
class DetectorSpec:
    """Official specification for a single ABI change detector."""

    name: str
    run: Callable[[AbiSnapshot, AbiSnapshot], list[Change]]
    is_supported: (
        Callable[[AbiSnapshot, AbiSnapshot], tuple[bool, str | None]] | None
    ) = None

    def support(self, old: AbiSnapshot, new: AbiSnapshot) -> tuple[bool, str | None]:
        if self.is_supported is None:
            return True, None
        return self.is_supported(old, new)
