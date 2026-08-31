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

"""The post-processing pipeline's public-reachability pass.

``MarkReachability`` is one cohesive ~490-line step: it tags each change with
public-reachability metadata *before* suppression runs, so a suppression rule
that would hide a public break can be flagged rather than silently applied
(ADR-044). It is the single largest step in the pipeline and referenced only
three module-level names from its parent, which made it the natural seam once
:mod:`abicheck.post_processing` hit the 2000-line hard cap.

Those three names now live in :mod:`abicheck.post_processing_context`, which
both modules import — so the dependency runs one way (``post_processing`` ->
this module -> ``post_processing_context``) and no cycle is possible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .checker_policy import ReachabilityState
from .post_processing_context import (
    _ENUM_MEMBER_KINDS,
    _PUBLIC_SOURCE_ABI_KINDS,
    PipelineContext,
)

if TYPE_CHECKING:
    from .checker_types import Change
    from .model import AbiSnapshot


class MarkReachability:
    """Tag each change with public-reachability metadata, before suppression runs.

    ADR-044 D1: the pipeline-order bug this step fixes is that
    ``ApplySuppression`` used to run before ``DetectInternalLeaks``, so a broad
    namespace/source_location suppression rule could remove the raw evidence
    (e.g. a ``type_size_changed`` on an internal type) before the internal-leak
    detector ever got a chance to see it — silently hiding a genuine leak
    through the public ABI with no trace in the report.

    This step computes the same public-surface reachability walk
    (:func:`internal_leak.compute_leak_paths`) that ``DetectInternalLeaks``
    uses, but up front — before any filtering — and tags every matching change
    with ``public_reachable``/``reachability_kind``/``reachability_proof_path``.
    ``compute_leak_paths`` is a pure function of the snapshot (function/
    variable/type declarations), not of the change list, so computing it here
    does not depend on pipeline position and does not need to be recomputed by
    ``DetectInternalLeaks`` later (which still runs after redundancy filtering
    to decide which *triggering* changes produce a synthetic leak finding).

    Deliberately does **not** also tag a change "reachable" merely because its
    own subject fails to look internal-namespaced (Codex review; reverted
    after landing — see ADR-044's "Post-merge review rounds" note). A
    ``source_location``/``namespace`` rule's whole reason to exist is
    compensating for `AbiSnapshot`'s visibility model marking *every* exported
    C/C++ symbol ``Visibility.PUBLIC`` regardless of whether the maintainer
    considers it part of the contract — ``AbiSnapshot`` carries no signal that
    distinguishes "a private helper the maintainer knows lives under
    ``internal/``" from "a genuinely public symbol that happens to be declared
    under a matching path" (both are ``Visibility.PUBLIC``, and neither name
    need contain an internal-namespace segment). Tagging any
    non-internal-namespaced subject reachable breaks the former, ordinary,
    already-relied-upon case (``tests/test_libabigail_parity_extended.py``'s
    own ``test_suppress_by_source_location`` encodes exactly this) in the
    course of trying to fix the latter — and no naming heuristic can tell them
    apart. Closing the latter gap for real needs actual dependency evidence
    (the L5 call-graph / consumer-import work already on this ADR's P1/P2
    roadmap), not a heuristic on the symbol's own spelling.

    ``RecordType.origin == ScopeOrigin.PUBLIC_HEADER`` (ADR-024's opt-in
    public-header scoping) is a *different*
    signal from the naming heuristic just described — an explicit, reliable
    tag, not a guess — and is consulted directly below for a change whose
    own subject type carries it, since :func:`internal_leak.compute_leak_paths`
    only ever records *internal* types reached while walking from the public
    surface, never the public seed types themselves (Codex review).

    ``Suppression.matches()`` (ADR-044 D2) consults ``public_reachable`` to
    decide whether a broad rule may apply at all — so the underlying evidence
    for a public-reachable internal change now survives ``ApplySuppression``
    by default, and ``DetectInternalLeaks`` (still running later in the
    pipeline, unchanged position) has real evidence to correlate.

    Skipped entirely when no suppression rules are configured
    (``ctx.suppression is None``, the common case, mirroring
    ``ApplySuppression``'s own no-op check): the reachability tags this step
    computes have no other consumer in this slice, and
    :func:`internal_leak.compute_leak_paths` is a full public-surface BFS —
    running it here unconditionally would duplicate the walk
    ``DetectInternalLeaks`` always performs later, roughly doubling that cost
    on every comparison even when nothing will ever read the tag (perf
    regression caught by ``benchmark_scaling.py``'s CI gate). Likewise
    skipped when a suppression *is* configured but every rule in it is
    narrow with the default (or explicit ``"any"``) reachability (Codex
    review) — :meth:`SuppressionList.needs_reachability_evidence` proves
    such a file's rules can never actually consult the tag either, which is
    the common case (a handful of exact ``symbol:`` waivers).

    ADR-052 D2 follow-up (G29 Phase 3 slice 10): once this step actually
    tags a change (the branch above did *not* early-return), it also caches
    that change's :class:`~abicheck.impact.model.ImpactAssessment` via
    :func:`impact.engine.assess_change` right at the point its own
    reachability/evidence fields become final -- see the ``_cache_assessment``
    closure below for the specific per-field safety audit. This is the
    resolution of ADR-052's own open measurement question ("is
    ``assess_change`` ever called more than once for the same ``Change``
    within one ``compare`` run"): measured directly (not assumed) via
    ``compare --format json --write sarif=abi.sarif``, which renders the
    identical ``DiffResult``/``Change`` objects twice in one process --
    ``reporter.py``'s JSON path and ``sarif.py``'s SARIF path each call
    ``assess_change`` independently. A one-off instrumented run over a
    genuinely breaking two-snapshot pair confirmed a real, non-hypothetical
    repeat call on the same ``Change`` object (2 calls recorded for 1
    finding) -- caching here avoids rebuilding ``proof_path``/``GraphProofPath``
    formatting from scratch on the second and later renders.
    """

    name = "mark_reachability"

    def __init__(self, namespaces: tuple[str, ...] | None = None) -> None:
        # Mirrors DetectInternalLeaks/DemoteUnreachableInternalChurn's own
        # constructor (Codex review, P2): those two steps already accept an
        # internal-namespace override, so MarkReachability must too, or a
        # project whose internal-namespace convention isn't in
        # DEFAULT_INTERNAL_NAMESPACES (e.g. "priv" instead of "detail") would
        # be recognized by the leak detector but invisible to the
        # reachability tag that gates suppression — reintroducing this ADR's
        # own failure mode for exactly that convention. An explicit
        # constructor argument (this parameter) always wins; absent that,
        # ``run()`` falls back to ``ctx.internal_namespaces`` — the
        # PolicyFile.internal_namespaces value DEFAULT_PIPELINE threads
        # through on every call (ADR-044 P1 item 5) — before finally
        # defaulting to DEFAULT_INTERNAL_NAMESPACES.
        self._namespaces = namespaces

    def run(self, changes: list[Change], ctx: PipelineContext) -> list[Change]:
        if ctx.suppression is None or not ctx.suppression.needs_reachability_evidence():
            return changes

        from .impact.engine import assess_change

        def _cache_assessment(c: Change) -> None:
            """ADR-052 D2 follow-up (G29 Phase 3 slice 10): cache *c*'s
            :class:`~abicheck.impact.model.ImpactAssessment` right after this
            step finalizes its reachability/evidence fields.

            Verified safe, not assumed, per the same discipline Slice 8/9 used
            for ``internal_leak.py``/``appcompat.py``: this is the *only* place
            in the codebase that mutates ``public_reachable``/
            ``reachability_state``/``reachability_kind``/
            ``reachability_proof_path`` on an already-constructed ``Change``
            (confirmed by a repo-wide grep, not assumed) -- so once this
            function returns for *c*, nothing later in
            ``post_processing.DEFAULT_PIPELINE`` (``ApplySuppression``,
            ``SuppressRenamedPairs``, ``FilterRedundant``,
            ``EnrichAffectedSymbols``, ``AttributeStdlibEmbedding``,
            ``DetectCppPatterns``, ``DetectTemplatePatterns``,
            ``DetectNamespacePatterns``, ``DetectInternalLeaks``,
            ``DemoteUnreachableInternalChurn``, ``DetectVersionedSymbolScheme``,
            ``EscalateFrozenNamespaceViolations``) touches them again.
            ``confidence``/``impact_proof_path``/``affected_public_roots``/
            ``impact_is_direct``/``impact_alternative_paths``/
            ``impact_discarded_path_count``/``impact_occurrence_id`` are all
            set (if at all) at ``Change`` construction time, before this step
            ever sees the change, so they are equally stable by this point.

            One residual field checked and found *not* to break this, rather
            than overlooked: ``Change.evidence_category`` has exactly one
            other post-construction mutator, ``diff_reconcile.
            reconcile_build_context_findings`` (``checker.py``, gated on
            ``--reconcile-build-context``) -- but it runs *after*
            ``_run_post_processing`` (this step included) and only ever sets
            ``evidence_category`` on a change it is simultaneously moving out
            of ``kept`` into ``DiffResult.reconciled_changes``.
            ``reporter._add_reconciled`` renders that list from its own
            hand-built dict and never reads ``Change.impact_assessment`` for
            it (confirmed by reading ``reporter.py``), so a change that ends
            up reconciled never has its (by-then-stale) cached
            ``evidence_category`` read through ``impact_assessment`` by any
            current report path -- the mutation is real but has no reachable
            consumer to observe it going stale.

            This also gives every source-graph finding
            (``buildsource/source_graph_findings.py``'s nine ``Change(...)``
            sites) a correctly cached assessment once its finding reaches
            this step: those builders run *before* ``_run_post_processing``
            (their output is merged into ``checker.compare``'s ``changes``
            list as ``extra_changes``, ahead of the whole pipeline -- unlike
            ``internal_leak.py``'s builders, which are themselves a
            *later* pipeline step), so caching directly at their own
            construction time would capture default/unset evidence fields
            this step hasn't tagged yet -- see the comment at each of those
            nine sites for the negative half of this same audit.
            """
            c.impact_assessment = assess_change(c)

        from .internal_leak import (
            _IDENTITY_VTABLE_KINDS,
            DEFAULT_INTERNAL_NAMESPACES,
            _format_path,
            _path_has_indirection,
            _path_is_value_propagating,
            _root_type_name_for_change,
            compute_call_graph_leak_paths,
            compute_leak_paths,
            is_internal_type,
            select_preferred_path,
        )
        from .model import ScopeOrigin

        def _public_header_names(snap: AbiSnapshot) -> set[str]:
            """Names of every declaration ADR-024's opt-in public-header
            scoping marked ``ScopeOrigin.PUBLIC_HEADER``
            — ``Function``/``Variable``/``RecordType``/``EnumType`` all carry
            this field. Without that flag every origin is ``ScopeOrigin.UNKNOWN``,
            so this returns empty and degrades to the prior behavior.

            ``Function``/``Variable`` also contribute their demangled-mangled
            qualified name (:func:`diff_filtering._qualified_by_mangled`), not
            just ``.name`` — the default CastXML backend never qualifies
            ``.name`` with namespace context, so a bare-name-only set would
            never match ``Change.qualified_name`` (itself recovered the same
            way) for a namespaced public function/variable, silently
            reproducing the exact identity gap this direct-match branch
            exists to close (Codex review, fresh evidence)."""
            from .diff_filtering import _qualified_by_mangled

            names: set[str] = set()
            names.update(f.name for f in snap.functions if f.origin == ScopeOrigin.PUBLIC_HEADER)
            names.update(v.name for v in snap.variables if v.origin == ScopeOrigin.PUBLIC_HEADER)
            names.update(t.name for t in snap.types if t.origin == ScopeOrigin.PUBLIC_HEADER)
            names.update(e.name for e in snap.enums if e.origin == ScopeOrigin.PUBLIC_HEADER)
            names.update(
                _qualified_by_mangled(
                    [
                        (f.mangled, f)
                        for f in snap.functions
                        if f.origin == ScopeOrigin.PUBLIC_HEADER
                    ]
                ).values()
            )
            names.update(
                _qualified_by_mangled(
                    [
                        (v.mangled, v)
                        for v in snap.variables
                        if v.origin == ScopeOrigin.PUBLIC_HEADER
                    ]
                ).values()
            )
            return names

        namespaces = self._namespaces or ctx.internal_namespaces or DEFAULT_INTERNAL_NAMESPACES
        old_paths = compute_leak_paths(ctx.old, namespaces)
        new_paths = compute_leak_paths(ctx.new, namespaces)
        reachable_types = set(old_paths) | set(new_paths)
        # ADR-044 P1 item 1: a second, independent reachability signal — the
        # optional L5 call graph's DECL_CALLS_DECL/DECL_REFERENCES_DECL edges
        # (--sources/--build-info, or the now-always-on L2 header-only graph). compute_leak_paths only
        # ever sees layout/type-graph reachability (inheritance, by-value
        # fields, signatures); a public inline function's *body* calling into
        # a removed/changed internal template specialization has none of
        # that evidence at all, but is real to a linker — the exact oneDAL
        # dispatcher gap the P0 slice's own "What this ADR does not fix"
        # section named. Returns {} on both sides with no embedded graph, so
        # this degrades to the prior behavior automatically for the common
        # case.
        old_call_paths = compute_call_graph_leak_paths(ctx.old, namespaces)
        new_call_paths = compute_call_graph_leak_paths(ctx.new, namespaces)
        call_reachable = set(old_call_paths) | set(new_call_paths)
        # ScopeOrigin.PUBLIC_HEADER (Codex review, fresh evidence):
        # compute_leak_paths only ever records *internal* types reached
        # while walking from the public surface — a declaration that is
        # itself the public surface (e.g. a header-only type never
        # referenced by an exported function/variable) never becomes a key
        # in its result, so a raw change on that declaration's own layout
        # got no tag at all from the walk above. ADR-024's opt-in
        # public-header scoping is the same reliable direct signal already
        # used for the late-detector findings in diff_namespaces.py/
        # diff_templates.py — apply it here too, across every declaration
        # kind that carries the field (function/variable/type/enum), not
        # just RecordType.
        public_header_names = _public_header_names(ctx.old) | _public_header_names(ctx.new)
        # Codex review, fourth pass: this used to return early here when
        # nothing at all was found reachable (no point tagging
        # public_reachable/reachability_kind — they'd all stay at their
        # False/None defaults either way). That is no longer true for
        # reachability_state: compute_leak_paths above already ran to
        # completion regardless, and its result being empty is itself
        # conclusive proof that no declared type in this comparison is
        # public-reachable — a per-change loop below still needs to run to
        # translate that into PROVEN_UNREACHABLE for every type-shaped
        # change, or a "nothing reachable anywhere" comparison would
        # wrongly leave every declared-type change at the honest-looking
        # but incorrect UNKNOWN default. The loop itself is cheap (simple
        # dict/set membership checks) — the walk it would have skipped
        # re-running already happened above, so this isn't a perf change.

        # A change whose root names a declared type is fully covered by the
        # layout/type-graph walk above (compute_leak_paths); a function/
        # variable-shaped root never was, so only a trustworthy call graph
        # can speak to it. "Trustworthy" means both producer passes ran to
        # completion (extractor_passes["call_graph"]/["type_graph"]) on both
        # sides -- not merely "some edge exists somewhere", since a
        # header-only or partially-collected graph can carry unrelated edges
        # while never examining the decl in question, and the combined walk
        # below mixes DECL_CALLS_DECL (call_graph.py) with
        # DECL_REFERENCES_DECL (type_graph.py) edges, each gated by its own
        # pass (Codex review, three passes).
        def _call_graph_fully_trusted(snap: AbiSnapshot) -> bool:
            build_source = getattr(snap, "build_source", None)
            graph = getattr(build_source, "source_graph", None) if build_source is not None else None
            if graph is None:
                return False
            if not (graph.extractor_passes.get("call_graph") and graph.extractor_passes.get("type_graph")):
                return False
            # Both passes completed is not enough on its own: the walk only
            # ever seeds from is_consumer_compiled_public_entry() nodes, not
            # merely "declared by some header" (source_graph_findings'
            # _public_decls() doesn't filter by visibility, so a
            # private-header decl would still count there) -- require the
            # walk's own actual seed predicate to find a match (Codex
            # review, two passes).
            from .buildsource.source_graph_query import (
                is_consumer_compiled_public_entry,
            )

            node_by_id = {n.id: n for n in graph.nodes}
            exported_decls = {
                e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
            }
            return any(
                is_consumer_compiled_public_entry(n.id, node_by_id, exported_decls)
                for n in graph.nodes
            )

        old_call_graph_trusted = _call_graph_fully_trusted(ctx.old)
        new_call_graph_trusted = _call_graph_fully_trusted(ctx.new)

        # Codex review, eighth pass: a ``kind.value.endswith("_removed"/"_added")``
        # heuristic also matches changed-in-place attribute toggles on a decl
        # that exists on *both* sides — e.g. FUNC_VIRTUAL_ADDED,
        # FUNC_NOEXCEPT_REMOVED, CTOR_EXPLICIT_ADDED, *_DEPRECATED_ADDED/REMOVED.
        # For those, requiring trust from only the suffix-selected side would
        # let a change on the untrusted/never-examined side slip through as
        # PROVEN_UNREACHABLE. Check the decl's *actual* presence on each
        # snapshot instead of pattern-matching the kind name, which is immune
        # to new one-sided or attribute-toggle kinds being added later.
        old_decl_names = {f.mangled for f in ctx.old.functions} | {f.name for f in ctx.old.functions}
        old_decl_names |= {v.mangled for v in ctx.old.variables} | {v.name for v in ctx.old.variables}
        new_decl_names = {f.mangled for f in ctx.new.functions} | {f.name for f in ctx.new.functions}
        new_decl_names |= {v.mangled for v in ctx.new.variables} | {v.name for v in ctx.new.variables}

        def _relevant_call_graph_trusted(change: Change, root: str) -> bool:
            """Only require trust from the side(s) *change*'s target actually
            exists on. A decl removed entirely (gone from the new snapshot)
            only ever existed on the old side, so only the old graph's
            coverage speaks to whether some old public entry called it — an
            untrusted/absent *new*-side graph (unsurprising, since the decl
            is gone there) must not turn a real old-side proof into UNKNOWN.
            Symmetric for a decl that's newly added. A decl present on both
            sides (a genuine changed-in-place attribute toggle) needs both
            sides trusted for a symmetric proof."""
            names = (root, change.qualified_name)
            existed_before = any(n is not None and n in old_decl_names for n in names)
            existed_after = any(n is not None and n in new_decl_names for n in names)
            if existed_before and not existed_after:
                return old_call_graph_trusted
            if existed_after and not existed_before:
                return new_call_graph_trusted
            return old_call_graph_trusted and new_call_graph_trusted

        # Typedef aliases (Codex review) are declared snapshot type surface
        # too — AbiSnapshot.typedefs is a flat {alias: underlying} map, not
        # a list of records/enums, so it needs its own membership check
        # alongside types/enums for TYPEDEF_REMOVED/TYPEDEF_BASE_CHANGED's
        # root (the alias name) to be recognized as layout-walk domain.
        known_type_names = (
            {t.name for t in ctx.old.types} | {e.name for e in ctx.old.enums}
            | {t.name for t in ctx.new.types} | {e.name for e in ctx.new.enums}
            | set(ctx.old.typedefs) | set(ctx.new.typedefs)
        )
        # RecordType.qualified_name (DWARF-backend only) resolves a bare name
        # like "Hidden" ("ns::detail::Hidden") for is_internal_type below --
        # only when unambiguous, else a colliding public/internal type of
        # the same bare name could leak the wrong namespace (Codex review).
        qualified_names_by_bare: dict[str, set[str]] = {}
        for t in (*ctx.old.types, *ctx.new.types):
            if t.qualified_name:
                qualified_names_by_bare.setdefault(t.name, set()).add(t.qualified_name)
        qualified_name_by_bare = {
            bare: next(iter(names))
            for bare, names in qualified_names_by_bare.items()
            if len(names) == 1
        }

        for c in changes:
            root = _root_type_name_for_change(c)
            # An enum-member finding's symbol is "EnumName::member" (diff_types.py),
            # not stripped by _root_type_name_for_change (that stripping is
            # scoped to STRUCT_FIELD_* kinds only) — peel it here so a
            # public-header-scoped EnumType's own member churn is found too.
            enum_owner = (
                root.rsplit("::", 1)[0]
                if "::" in root and c.kind in _ENUM_MEMBER_KINDS
                else None
            )
            # Codex review (fresh evidence): root is c.symbol verbatim for a
            # function/variable-shaped change, and diff_symbols.py sets that
            # to the *mangled* linker name for FUNC_REMOVED/FUNC_ADDED/etc. --
            # while _public_header_names above collects Function.name, which
            # is demangled. root == a public_header_names entry therefore
            # never matches for a real (mangled) C++ symbol, so a
            # public-header-declared C++ function/variable removal fell
            # through this direct-public-symbol check entirely, relying
            # entirely on the layout/call-graph walks below to still tag it
            # -- and a standalone public entry point that nothing else
            # references or embeds is reachable by neither, so it was
            # silently untagged and a broad suppression rule could hide it
            # with no diagnostic. c.qualified_name (EnrichSourceLocations,
            # runs before this step) is set from the demangled Function.name
            # for exactly the FUNC_REMOVED/FUNC_ADDED kinds this matters for,
            # so check it too.
            if (
                root in public_header_names
                or enum_owner in public_header_names
                or (c.qualified_name and c.qualified_name in public_header_names)
            ):
                c.public_reachable = True
                c.reachability_kind = "direct_public_symbol"
                c.reachability_state = ReachabilityState.PROVEN_REACHABLE
                _cache_assessment(c)
                continue
            if c.kind in _PUBLIC_SOURCE_ABI_KINDS:
                c.public_reachable = True
                c.reachability_kind = "public_source_abi_surface"
                c.reachability_state = ReachabilityState.PROVEN_REACHABLE
                _cache_assessment(c)
                continue
            tagged = False
            # An enum-member finding's root still carries the "::member"
            # suffix here (only stripped into enum_owner just above), so it
            # never matches a reachable_types key by itself even when the
            # owning enum genuinely was walked and found reachable —
            # compute_leak_paths records leaf types like enums under their
            # bare name (CodeRabbit review). Fall back to enum_owner so a
            # reachable enum's member change is tagged from this same walk
            # instead of only being caught by the coarser known_type_names
            # fallback below (which cannot distinguish reachable from
            # merely-declared).
            layout_key = (
                root
                if root in reachable_types
                else (enum_owner if enum_owner in reachable_types else None)
            )
            if layout_key is not None:
                old_pl = old_paths.get(layout_key, [])
                new_pl = new_paths.get(layout_key, [])
                paths = old_pl + [p for p in new_pl if p not in old_pl]
                # Mirror DetectInternalLeaks's own value/indirection judgment
                # (Codex review): a pure-layout change reached *only* through
                # a pointer/reference is not consumer-visible and
                # DetectInternalLeaks will not emit a leak finding for it
                # either — tagging it public_reachable anyway would refuse a
                # broad suppression rule and append a
                # suppression_would_hide_public_break diagnostic for churn
                # that was always going to be demoted as unreachable by
                # DemoteUnreachableInternalChurn, a false alarm.
                identity_or_vtable = c.kind in _IDENTITY_VTABLE_KINDS
                all_indirect = bool(paths) and all(
                    _path_has_indirection(p) for p in paths
                )
                if paths and not (all_indirect and not identity_or_vtable):
                    c.public_reachable = True
                    preferred_path = select_preferred_path(paths)
                    c.reachability_kind = "value_embedding" if _path_is_value_propagating(preferred_path) else "pointer_or_signature"
                    c.reachability_proof_path = _format_path(preferred_path)
                    c.reachability_state = ReachabilityState.PROVEN_REACHABLE
                    tagged = True
            # ADR-044 P1 items 1/3: independent of (and checked regardless of
            # the outcome of) the layout walk above — a change can be
            # call-graph-reachable without any layout/type-graph evidence at
            # all (e.g. func_removed on an internal decl with no field/base/
            # signature reference anywhere).
            # Codex review (fresh evidence): compute_call_graph_leak_paths's
            # mangled-symbol key only exists when the graph carries a
            # SOURCE_DECL_MAPS_TO_SYMBOL edge for the target decl — the
            # build-integrated L4/L5 path (source_graph.py) creates one, but
            # the header-only path (header_graph.py, always-on since G29
            # Phase A / the implicit dump path, no real build) never does. c.qualified_name
            # (EnrichSourceLocations, runs before this step) is set from
            # Function.name — the same demangled name a graph node's own
            # label carries in EITHER mode — so it is a reliable fallback key
            # independent of which graph provenance produced the evidence.
            call_key = (
                root if root in call_reachable
                else (c.qualified_name if c.qualified_name in call_reachable else None)
            )
            if not tagged and call_key is not None:
                call_paths = old_call_paths.get(call_key, []) + [
                    p
                    for p in new_call_paths.get(call_key, [])
                    if p not in old_call_paths.get(call_key, [])
                ]
                if call_paths:
                    c.public_reachable = True
                    c.reachability_kind = "symbol_availability"
                    c.reachability_proof_path = min(call_paths, key=len)
                    c.reachability_state = ReachabilityState.PROVEN_REACHABLE
                    tagged = True
            if not tagged:
                # Not proven reachable. A change whose root names a declared
                # type is squarely in the layout/type-graph walk's domain —
                # that walk is a complete closure over every internal type
                # reachable from the public surface, so its absence there is
                # conclusive proof regardless of call-graph coverage
                # (PROVEN_UNREACHABLE either way: whether the walk found no
                # path at all, or found only a demoted pointer-only path).
                #
                # A change whose root is *not* a declared type (a function/
                # variable-shaped change — e.g. func_removed on an internal
                # decl) was never in that walk's domain to begin with; only
                # the call graph could speak to it, so its verdict is
                # conclusive only when the side(s) its target could actually
                # exist on have a fully trusted, completed call-graph pass
                # (Codex review — neither an absent graph nor a handful of
                # incidental edges from a partial one may silently read the
                # same as a trustworthy graph that looked and found
                # nothing).
                #
                # Restricted to an *internal-namespaced* subject (Codex
                # review, sixth pass): compute_call_graph_leak_paths only
                # ever walks dependencies of consumer-compiled public
                # entries — is_consumer_compiled_public_entry() explicitly
                # excludes an ordinary out-of-line exported function — so a
                # trusted call graph can prove an *internal callee* absent,
                # but says nothing about an exported public symbol's own
                # reachability. Without this gate, a plain FUNC_REMOVED on a
                # real, directly-exported API function with no inline
                # caller would be misread as call-graph-proven-unreachable
                # and a broad proven-unreachable-only rule could suppress a
                # genuine ABI break. root is typically the *mangled* symbol
                # for a function/variable change (diff_symbols.py), which
                # has no "::" segments for is_internal_type to see — check
                # the demangled c.qualified_name too, same fallback pattern
                # used elsewhere in this walk.
                #
                # Restricted the same way for the layout walk itself
                # (Codex review, seventh pass): compute_leak_paths only ever
                # records *internal* types it finds reached from the public
                # surface — it never records the public seed types
                # themselves (see _public_header_names's own docstring
                # above). A genuinely public declared type absent from
                # reachable_types was therefore never examined by this walk
                # at all, not proven unreachable by it — treating any known
                # declared type as "layout domain" let a broad
                # `namespace: ns::*` rule suppress a real public-type
                # layout break with no diagnostic. root already having been
                # a key in reachable_types (even if later demoted to
                # pointer-only/non-value) is real positive evidence
                # regardless of naming; absence from it is only conclusive
                # for a type the walk's internal-only domain could have
                # classified in the first place.
                # An enum-member root keeps its "::member" suffix, so use
                # enum_owner (bare name) or a member literally named e.g.
                # "detail" would read as internal-namespaced (Codex review).
                internal_check_subject = enum_owner if enum_owner is not None else root
                # qualified_name_by_bare is keyed from RecordType names only
                # -- an enum's bare owner could collide with an unrelated
                # record's bare name, wrongly feeding the record's namespace
                # onto the enum (Codex review). Never resolve it for
                # enum_owner.
                type_qualified_name = (
                    qualified_name_by_bare.get(root) if enum_owner is None else None
                )
                subject_is_internal = is_internal_type(
                    internal_check_subject, namespaces
                ) or (
                    c.qualified_name is not None
                    and is_internal_type(c.qualified_name, namespaces)
                ) or (
                    type_qualified_name is not None
                    and is_internal_type(type_qualified_name, namespaces)
                )
                layout_domain = root in reachable_types or (
                    subject_is_internal
                    and (
                        root in known_type_names
                        or (enum_owner is not None and enum_owner in known_type_names)
                    )
                )
                if layout_domain or (
                    subject_is_internal and _relevant_call_graph_trusted(c, root)
                ):
                    c.reachability_state = ReachabilityState.PROVEN_UNREACHABLE
                else:
                    c.reachability_state = ReachabilityState.UNKNOWN
            _cache_assessment(c)
        return changes
