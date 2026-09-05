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

"""State and kind sets shared across the post-processing pipeline.

A leaf split out of :mod:`abicheck.post_processing` so the reachability pass
(:mod:`abicheck.post_processing_reachability`) can use them without importing
its own parent — which would be a genuine import cycle, not a stylistic one.

That parent had reached exactly the 2000-line hard cap the AI-readiness gate
enforces, so it could not absorb another line; splitting is what
``scripts/check_ai_readiness.py`` asks for there rather than an allowlist
entry. ``PipelineContext`` is re-exported from ``post_processing`` because 23
call sites import it from that historical path.

Deliberately imports only ``checker_policy`` (itself a leaf), so nothing here
can pull a cycle into either consumer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot
    from .policy.disposition_ledger import DispositionLedger
    from .suppression import SuppressionList
    from .surface import PublicSurface


@dataclass
class PipelineContext:
    """Shared state passed through the pipeline."""

    old: AbiSnapshot
    new: AbiSnapshot
    suppression: SuppressionList | None = None
    # Glob patterns identifying contractually frozen namespaces (e.g.
    # ``**::detail::r1``). Threaded in from PolicyFile.frozen_namespaces.
    # Consumed by EscalateFrozenNamespaceViolations to tag matching
    # findings with Change.frozen_namespace_violation.
    frozen_namespaces: list[str] = field(default_factory=list)
    # ADR-044 P1 item 5: project-configurable internal-implementation-namespace
    # convention, threaded in from PolicyFile.internal_namespaces. None means
    # "not configured" — MarkReachability/DetectInternalLeaks/
    # DemoteUnreachableInternalChurn each fall back to their own
    # DEFAULT_INTERNAL_NAMESPACES. Deliberately not consulted by
    # DetectNamespacePatterns's experimental_namespaces — a different, unrelated
    # convention (see PolicyFile.internal_namespaces's docstring).
    internal_namespaces: tuple[str, ...] | None = None
    # ADR-024 §D4: when True, FilterNonPublicSurface moves findings that are
    # not on the public-header-scoped ABI surface to ``out_of_surface``.
    scope_to_public_surface: bool = False
    # `compare --post-manifest`: an explicit committed-ABI surface (the set of
    # `pp_*`/ufunc-loop symbols a POST manifest promises). When set,
    # FilterNonPublicSurface scopes against *this* set instead of the
    # header-derived surface — an export finding whose symbol is not committed is
    # demoted, while type-level and leak findings are kept (conservative). None
    # means "not manifest-scoped".
    public_surface_allowlist: set[str] | None = None
    # G15 (opt-in): when True, DetectVersionedSymbolScheme reclassifies the
    # version-rename pairs (ICU `u_*_NN`) as compatible so the verdict reflects
    # the real delta instead of the rename churn. Off by default (authority rule).
    collapse_versioned_symbols: bool = False
    # ADR-024 §D6 widening overlay: symbol names (mangled or demangled) the
    # user *guarantees* are public even when header provenance can't see them
    # (asm stubs, .def exports, extern "C" shims, MSVC-mangling gaps). Matching
    # findings are forced to stay in-surface under scoping. Widening only ever
    # *keeps* a finding, so it cannot hide a break.
    force_public_symbols: set[str] = field(default_factory=set)
    # Set True when scoping was requested but the public surface could not be
    # resolved, so the step fell back to the full export table (keeps every
    # finding). Consumers surface this as "manual review required" — scoping
    # must never silently read as confident compatibility (issue #235).
    scope_fell_back: bool = False
    # Public surfaces computed by FilterNonPublicSurface, cached here so the
    # caller can reuse them (e.g. surface_scope_confidence) instead of repeating
    # the type-closure walk. None when scoping was not run.
    surf_old: PublicSurface | None = None
    surf_new: PublicSurface | None = None
    # Accumulated side-outputs
    opaque_filtered: list[Change] = field(default_factory=list)
    suppressed: list[Change] = field(default_factory=list)
    redundant: list[Change] = field(default_factory=list)
    kept: list[Change] = field(default_factory=list)
    # ADR-024: findings filtered out as not-public (full audit trail).
    out_of_surface: list[Change] = field(default_factory=list)
    # Set when collapsed version-rename churn was paired with an observed
    # SONAME change. The late SONAME policy should not call that bump
    # unnecessary after this step has moved the matched removals out of kept.
    versioned_scheme_soname_relink_required: bool = False
    # ADR-067 C-S1: the run's conserved policy-disposition ledger. Every
    # suppression application point in this pipeline records into it (with the
    # matched rule's provenance) as it moves a change into ``suppressed``, so
    # the raw-versus-effective totals reconcile by construction rather than
    # being recovered after the fact. ``None`` only for a caller that built a
    # context of its own without one -- recording is then skipped, never
    # faked.
    disposition_ledger: DispositionLedger | None = None


# diff_types.py builds ENUM_MEMBER_*/ENUM_LAST_MEMBER_VALUE_CHANGED's symbol
# as "EnumName::member" (unlike TYPE_FIELD_* kinds, which carry the
# containing type name directly) — MarkReachability.run needs this set to
# know when to peel the member suffix before checking the owning EnumType's
# public-header origin.
_ENUM_MEMBER_KINDS = frozenset(
    {
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_MEMBER_ADDED,
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
    }
)

# L4 (source_diff.py) / L5 (source_graph_findings.py) findings below are
# public *by construction* -- each built only from an already-proven-public
# entity, never a bare namespace-name heuristic (Codex review, many passes).
# NOT extended to SOURCE_BINARY_PROVENANCE_MISMATCH (aggregate, symbol="")
# or ODR_SOURCE_CONFLICT's sibling checks not scoped to public types.
_PUBLIC_SOURCE_ABI_KINDS = frozenset(
    {
        ChangeKind.PUBLIC_TYPEDEF_REMOVED,
        ChangeKind.PUBLIC_TYPEDEF_TARGET_CHANGED,
        ChangeKind.PUBLIC_MACRO_REMOVED,
        ChangeKind.PUBLIC_MACRO_VALUE_CHANGED,
        ChangeKind.INLINE_FUNCTION_REMOVED,
        ChangeKind.UNINSTANTIATED_TEMPLATE_REMOVED,
        ChangeKind.CONCEPT_TIGHTENED,
        ChangeKind.CONSTEXPR_VALUE_CHANGED,
        ChangeKind.DEFAULT_ARGUMENT_CHANGED,
        ChangeKind.INLINE_BODY_CHANGED,
        ChangeKind.TEMPLATE_BODY_CHANGED,
        ChangeKind.GENERATED_HEADER_CHANGED,
        ChangeKind.SOURCE_DECL_BINARY_SYMBOL_MISMATCH,
        ChangeKind.ODR_SOURCE_CONFLICT,
        # L5 (source_graph_findings.py) kinds whose subject is itself a
        # proven-public entry/decl/symbol, not just something touching one.
        # NOT extended to BUILD_OPTION_REACHES_PUBLIC_SYMBOL/TARGET_DEPENDENCY_
        # ADDED -- keyed on an option/target that merely reaches something
        # public, not a public entity itself.
        ChangeKind.PUBLIC_REACHABILITY_CHANGED,
        ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API,
        ChangeKind.CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED,
        ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
        ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT,
        ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED,
        # _mapping_drift_findings fires only on old_sym != new_sym, and a
        # SOURCE_DECL_MAPS_TO_SYMBOL edge's target is always a genuinely
        # *exported* symbol (source_link.relink_surface_exports matches only
        # against the real export set) -- so at least one side has this decl
        # actually exported whenever it fires (Codex review).
        ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
    }
)
