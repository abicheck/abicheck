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

"""The consumer/source-graph join half of application compatibility (G29
Phase 4, ADR-057) — split out of ``appcompat.py`` once that file crossed the
architecture gate's 2000-line hard cap (two independent PRs each raised its
reviewed ``no_growth`` baseline and landed together; see
``architecture/debt.yaml``'s entry for the fuller account).

``scope_diff_to_app`` is the only caller outside this module (three call
sites: :func:`consumer_impact_explanations`, :func:`attach_consumer_impact`,
:func:`enrich_covered_changes`) — the rest of this file's functions are
private helpers those three build on. The seam is a real one, not a line
count: this is the one self-contained subsystem in ``appcompat.py`` that
never touches the ELF/PE/Mach-O app-requirements parsing, the disposition
ledger, or the CLI-facing result types the rest of that file owns, and it
depends only on a `Change`/`AppRequirements` pair and the L5 source graph.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .impact.engine import assess_change
from .policy.evidence_status import ReachabilityState

if TYPE_CHECKING:
    from pathlib import Path

    from .checker import Change
    from .impact.consumer_graph import ConsumerImpactPath
    from .model import AbiSnapshot
    from .model.source_graph import SourceGraphSummary

    # Not `.appcompat.AppRequirements`: importing it, even under
    # TYPE_CHECKING, closes an `appcompat -> appcompat_consumer_impact ->
    # appcompat` cycle the AI-readiness import-cycle-growth check flags
    # regardless of the guard (it walks every import statement, not just the
    # ones that execute). `Any` costs nothing here -- the one caller
    # (`appcompat.scope_diff_to_app`) already has the concrete type in scope.
    AppRequirements = Any


def _library_source_graph(
    lib: Path | AbiSnapshot, snapshot: AbiSnapshot | None = None
) -> SourceGraphSummary | None:
    """The L5 source graph for the old library, or ``None``.

    Only an :class:`~abicheck.model.AbiSnapshot` can carry one (``dump
    --sources``/``--build-info``/``--old-sources``, or the always-on
    header-only graph) — a bare library ``Path`` is a real binary this module
    reads an export/version table from, with no graph attached.

    Hence *snapshot*, the ADR-057 follow-up (Codex review, fresh evidence):
    when OLD is a real binary, every caller passes the ``Path`` as *lib* even
    though it is holding the snapshot it just dumped or loaded from that same
    path (``cli_compare_helpers._apply_used_by_scoping``'s
    ``old_input if detect_binary_format(...) else old_snapshot``,
    ``mcp_server``'s identical line, ``appcompat.check_appcompat``'s own
    ``dump``). Reading the graph only off *lib* therefore made the consumer
    join fire **only** when OLD happened to be a saved JSON snapshot — the
    inverse of the primary usage, and it silently skipped exactly the runs
    that asked for the richest evidence (``--old-sources``/``--old-build-info``).
    *snapshot* is consulted first and is for graph lookup only; *lib* keeps
    owning every binary/export/version read, so the two can never disagree
    about what is exported. It must describe the same library as *lib* — at
    all three call sites it is the snapshot of that exact path.
    """
    for candidate in (snapshot, lib):
        build_source = getattr(candidate, "build_source", None)
        if build_source is None:
            continue
        graph: SourceGraphSummary | None = getattr(build_source, "source_graph", None)
        if graph is not None and graph.nodes:
            return graph
    return None


def consumer_impact_explanations(
    app_path: Path,
    app_reqs: AppRequirements,
    old_lib: Path | AbiSnapshot,
    symbols: list[str],
    old_snapshot: AbiSnapshot | None = None,
) -> tuple[SourceGraphSummary | None, dict[str, ConsumerImpactPath]]:
    """Explain each of *symbols* through the joined consumer/source graph
    (G29 Phase 4, ADR-057).

    The *old* library's graph, not the new one: the symbol is missing from the
    new library by definition, so only the old side still carries the
    declaration and call edges that say why the consumer depended on it.

    Returns ``(joined_graph, {symbol: ConsumerImpactPath})`` — both empty/
    ``None`` whenever no graph is available or nothing could be explained, in
    which case every finding keeps exactly the shape it had before this join
    existed.
    """
    library_graph = _library_source_graph(old_lib, old_snapshot)
    if library_graph is None or not symbols:
        return None, {}
    from .impact.consumer_graph import (
        build_consumer_graph,
        explain_required_symbols,
        join_consumer_graph,
    )

    # No `symbols=` narrowing needed: _scope_app_symbols_to_library already
    # reduced app_reqs.undefined_symbols to what this library actually
    # exports, which is exactly the scoping that parameter exists to apply.
    consumer_graph = build_consumer_graph(app_path.name, app_reqs)
    joined = join_consumer_graph(library_graph, consumer_graph)
    return joined, explain_required_symbols(joined, symbols, consumer=app_path.name)


def _format_consumer_impact(
    explained: ConsumerImpactPath,
    graph: SourceGraphSummary,
    *,
    name_consumer: bool = True,
) -> str:
    """The human-readable half of a consumer impact explanation — the string
    that replaces "requires missing symbol X" with why it was required.

    Reuses ``source_graph_findings._format_dependency_path`` for the chain
    itself rather than formatting edges here, so a consumer proof path reads
    identically to an internal-leak one for the same edges.

    *name_consumer* picks which of the two facts the sentence leads with, and
    the distinction is not cosmetic (ADR-057 D8). "``training-service``
    requires X" is a fact about *one* consumer; "X is reachable from public
    entry ``train``" is a fact about the *library*, true of every consumer and
    of no consumer. The first belongs on the
    ``CONSUMER_REQUIRED_SYMBOL_REMOVED`` overlay, which exists per app; the
    second is what may be written onto a shared library-diff finding that the
    unscoped report also renders.
    """
    entry = explained.public_entries[0] if explained.public_entries else "?"
    if explained.is_direct():
        if name_consumer:
            return f"{explained.consumer} requires public entry {entry} directly"
        return f"{explained.symbol} is declared by public entry {entry}"
    from .buildsource.source_graph_findings import _format_dependency_path

    chain = _format_dependency_path(graph, explained.entry_path)
    if name_consumer:
        return (
            f"{explained.consumer} requires {explained.symbol} "
            f"via public entry {entry}: {chain}"
        )
    return f"{explained.symbol} is reachable from public entry {entry}: {chain}"


def _has_impact_evidence(change: Change) -> bool:
    """Whether *change* already carries reachability/impact evidence from one
    of its own producers (``internal_leak``, ``source_graph_findings``,
    ``post_processing``).

    The guard on enriching a *shared* library-diff finding: those `Change`
    objects are the same ones in ``DiffResult.changes``, so overwriting
    evidence a producer already computed would corrupt the unscoped report,
    and ``attach_impact_metadata`` assigns its whole field set
    unconditionally (``None`` included). It also makes the multi-``--used-by``
    case first-writer-wins and therefore deterministic in app order, rather
    than silently last-writer-wins.

    ``impact_assessment.proof_path is not None`` (a *cached assessment that
    actually carries a proof path*), not merely "a cached assessment
    exists" (Codex review, fresh evidence): since ADR-052 Slice 10,
    ``post_processing.MarkReachability`` caches an ``ImpactAssessment`` on
    *every* change it tags -- including a change it leaves
    ``ReachabilityState.UNKNOWN`` with no proof path at all (an ordinary,
    otherwise-unexplained ``FUNC_REMOVED``), and one it tags
    ``PROVEN_REACHABLE`` via a direct-symbol/public-source-ABI-surface match
    with no walked path either (see that step's own two early-continue
    branches). Treating either of those as "evidence of its own" would skip
    :func:`enrich_covered_changes` for the exact common case this join
    exists to explain, silently dropping ``affected_public_roots``/
    ``impact_proof_path``/the consumer-neutral prose for a covered removed
    export. Reading ``.proof_path`` instead makes this check equivalent to
    the two flat-field checks below regardless of whether the evidence
    reached this change via the cache or directly.
    """
    assessment = getattr(change, "impact_assessment", None)
    return (
        (assessment is not None and assessment.proof_path is not None)
        or getattr(change, "impact_proof_path", None) is not None
        or getattr(change, "reachability_proof_path", None) is not None
    )


def _change_covers_symbol(change: Change, symbol: str) -> bool:
    """Does *change* already account for *symbol* (exact, demangled, or via
    ``affected_symbols``)? Mirrors ``appcompat._change_covers_symbol`` --
    duplicated rather than imported back, since the two modules must not
    import each other at runtime (only ``appcompat`` -> this module, never
    the reverse); both copies are one-line-bodied and change together."""
    if change.symbol == symbol:
        return True
    from .demangle import demangle as _demangle_symbol

    plain = _demangle_symbol(change.symbol)
    if plain and plain == symbol:
        return True
    return bool(change.affected_symbols and symbol in change.affected_symbols)


def enrich_covered_changes(
    changes: list[Change],
    explanations: dict[str, ConsumerImpactPath],
    graph: SourceGraphSummary,
) -> None:
    """Attach the graph explanation to library-diff findings that already
    cover a missing symbol (ADR-057 D8, Codex review).

    Without this the join reached only *uncovered* symbols: an ordinary
    removed export produces its own ``FUNC_REMOVED``, which
    ``appcompat.uncovered_missing_symbols`` then excludes from the overlay —
    so the common case, including the internal-dispatcher one this join was
    built for, got no proof path at all.

    These `Change` objects are shared with ``DiffResult.changes``, so the
    prose written here is deliberately consumer-neutral (see
    :func:`_format_consumer_impact`) and only findings with no evidence of
    their own are touched.

    Refreshes ``change.impact_assessment`` after attaching (Codex review,
    fresh evidence): a change reaching this point with a *cached but
    pathless* assessment (``MarkReachability``'s blanket Slice 10 cache —
    the case :func:`_has_impact_evidence` now lets through) still has that
    stale, path-less object sitting on ``change.impact_assessment`` after
    :func:`attach_consumer_impact` sets the flat proof-path fields —
    ``impact.engine.assess_change`` prefers any non-``None`` cached
    assessment over re-deriving from those flat fields, so without this
    refresh the newly attached consumer explanation would never actually
    reach a JSON/SARIF render. Mirrors the overlay-change path in
    ``appcompat.scope_diff_to_app``
    (``overlay_change.impact_assessment = assess_change(overlay_change)``),
    which already does this correctly for the *uncovered*-symbol case
    because that ``Change`` is always freshly constructed with no
    pre-existing cache to go stale — this shared-``Change`` counterpart
    must clear the stale cache *first*: ``assess_change`` reads
    ``change.impact_assessment`` as its own cache, so recomputing while the
    old object is still assigned would just hand back that same stale
    object unchanged.

    Aggregates *every* matching explanation, not just the first
    (:func:`_merge_consumer_impact_paths`) — a single shared ``Change`` can
    cover more than one missing export at once via ``affected_symbols``
    (e.g. one type-size change breaking several removed functions), and
    :func:`_change_covers_symbol` treats all of them as covered. Keeping
    only the first match's ``next(...)`` pick (Codex review, fresh
    evidence) silently discarded every other symbol's own public root and
    proof path, reporting a narrower explanation than the same logic
    already claims this finding accounts for.
    """
    for change in changes:
        if _has_impact_evidence(change):
            continue
        matches = [
            e for sym, e in explanations.items() if _change_covers_symbol(change, sym)
        ]
        if not matches:
            continue
        explained = _merge_consumer_impact_paths(matches)
        attach_consumer_impact(change, explained, graph, name_consumer=False)
        change.impact_assessment = None
        change.impact_assessment = assess_change(change)


def _merge_consumer_impact_paths(
    matches: list[ConsumerImpactPath],
) -> ConsumerImpactPath:
    """Combine every symbol-level explanation one shared ``Change`` covers
    (via ``affected_symbols`` naming more than one missing export) into a
    single :class:`~abicheck.impact.consumer_graph.ConsumerImpactPath`,
    instead of silently keeping only the first and discarding the rest
    (Codex review, fresh evidence).

    All of ``consumer``/``symbol``/``entry_path``/the primary (first)
    ``public_entries`` entry come from the **same** match — the one
    :func:`_choose_primary_match` picks — never from independently-chosen
    fields (Codex review, fresh evidence): an earlier version picked
    ``symbol``/``public_entries`` from ``matches[0]`` but ``entry_path``
    from the first match that happened to carry a non-empty (indirect)
    path, which are not necessarily the same match when the first match is
    itself *direct* (``entry_path == []``) and a later one is indirect.
    ``_format_consumer_impact`` reads ``explained.symbol``/
    ``explained.public_entries[0]`` together with ``explained.entry_path``
    as one coherent story ("*symbol* is reachable from public entry
    *entry*: *chain*"); mixing fields from two different matches produced
    an internally contradictory sentence — symbol A "reachable from" A's
    own entry, followed by a chain that actually starts at (and explains)
    a completely different symbol B's entry and target.

    ``public_entries``/``declarations`` are still the order-preserving
    deduped union across every match (so a finding covering several
    requirements reports every public root/declaration that explains it,
    not just the primary's), just with the primary's own values ordered
    first. A *same-rooted* other match's ``entry_path``/
    ``alternative_entry_paths`` is folded into the merged
    ``alternative_entry_paths`` instead of dropped outright, so no
    explanation is lost even though only one match can be primary
    (``attach_impact_metadata`` caps how many of these are actually kept).
    A *differently-rooted* other match's own path is deliberately left out
    (Codex review, fresh evidence) — ``impact.engine._build_alternative_path``
    stamps every merged ``GraphProofPath`` with the SAME single primary
    root, so including a path that actually starts at a different public
    entry would misrepresent it in JSON/SARIF as a variant of the primary's
    own root; that entry is still reported via ``public_entries``, just not
    smuggled in as a same-rooted alternative.

    Deliberately does **not** early-return ``matches[0]`` unchanged when
    ``len(matches) == 1`` (Codex review, fresh evidence): the single-symbol
    case is the *ordinary* one, not a degenerate special case that can skip
    the root filter below.
    :func:`~abicheck.impact.consumer_graph.explain_required_symbols` can
    itself return one ``ConsumerImpactPath`` whose own
    ``alternative_entry_paths`` already mixes candidates from more than one
    consumer-compiled entry (every entry that reaches the target
    declaration, not just the ``select_preferred_graph_path``-chosen one) —
    so a lone match can carry a differently-rooted alternative exactly like
    the primary-match case below, and skipping this function's filtering
    for it would let ``impact.engine._build_proof_path``'s single
    ``affected_public_roots[0]`` mislabel that alternative's root the same
    way an unfiltered multi-match merge would.
    """
    primary = _choose_primary_match(matches)
    ordered = [primary, *(m for m in matches if m is not primary)]
    entries: list[str] = []
    seen_entries: set[str] = set()
    declarations: list[str] = []
    seen_decls: set[str] = set()
    for m in ordered:
        for e in m.public_entries:
            if e not in seen_entries:
                seen_entries.add(e)
                entries.append(e)
        for d in m.declarations:
            if d not in seen_decls:
                seen_decls.add(d)
                declarations.append(d)
    # A non-primary match's own entry_path/alternative_entry_paths is only
    # folded in when it shares the primary's own root (Codex review, fresh
    # evidence): impact.engine._build_alternative_path stamps every merged
    # GraphProofPath.root from the SAME single primary root
    # (impact.engine._build_proof_path's affected_roots[0]) -- it has no
    # per-alternative root field at all. Including a differently-rooted
    # match's own path here would have it serialized in JSON/SARIF as
    # though it started at the primary's entry, when it actually starts at
    # (and explains) a different public entry entirely -- the exact
    # "confident but wrong" shape this module otherwise never allows, so a
    # differently-rooted match's own path is left out of the merged
    # alternatives rather than mislabeled.
    #
    # Compares by the actual graph node id (entry_path[0].src) whenever BOTH
    # sides have a walked path, not by display label (Codex review, fresh
    # evidence): distinct public-entry nodes can share one display label --
    # C++ overloads are the common case -- so a label match alone does not
    # prove m's path starts at the SAME node primary's does. Falls back to
    # the label comparison only when one side has no entry_path at all (a
    # *direct* match, whose declaration-is-the-entry "path" has no edge to
    # read a node id from) -- the label is the only signal available then,
    # same as before this fix.
    primary_root = primary.public_entries[0] if primary.public_entries else None

    def _same_root(m: ConsumerImpactPath) -> bool:
        if m.entry_path and primary.entry_path:
            return m.entry_path[0].src == primary.entry_path[0].src
        return bool(m.public_entries) and m.public_entries[0] == primary_root

    # A non-primary match's OWN alternative_entry_paths need the identical
    # per-alt filter the primary's own alternatives get below (Codex
    # review, fresh evidence): _same_root(m) only proves m's PREFERRED
    # entry_path shares the primary's root -- explain_required_symbols
    # builds m.alternative_entry_paths from every candidate path across
    # every consumer-compiled entry that reached the target, not just the
    # entry m's own preferred path happens to start at, so one of m's own
    # alternatives can start at yet another, third entry. Bulk-extending
    # all of them once m's preferred path passes the root check would
    # still let a differently-rooted path through and have it serialized
    # under the primary's single root. Filtered against m's own verified
    # entry_path[0].src (equal to primary's by construction once same_root
    # is True) rather than re-deriving it from primary directly, so the
    # comparison reads as "does this alt start where m's OWN already-
    # verified path starts" -- the same question the primary-side filter
    # answers about primary's own alternatives.
    alternatives = []
    for m in matches:
        if m is primary:
            continue
        same_root = _same_root(m)
        if m.entry_path and same_root:
            alternatives.append(m.entry_path)
            m_start = m.entry_path[0].src
            alternatives.extend(
                alt
                for alt in m.alternative_entry_paths
                if alt and alt[0].src == m_start
            )
    # primary's OWN alternative_entry_paths need the identical guard (Codex
    # review, fresh evidence): explain_required_symbols builds these from
    # every candidate path across every consumer-compiled entry the walk
    # reached, not just ones starting at the SAME entry select_preferred_graph_path
    # chose as primary -- so an entry in here can legitimately start at a
    # different node than primary.entry_path's own first hop. There is no
    # per-alternative root field to compare by label (unlike the
    # cross-match case above), so this compares each alternative's actual
    # first-edge source node against primary.entry_path's own first-edge
    # source directly -- the same underlying question ("does this
    # alternative start where the primary path starts"), answered without
    # needing graph/label access this function doesn't have.
    if primary.entry_path:
        primary_start = primary.entry_path[0].src
        alternatives.extend(
            alt
            for alt in primary.alternative_entry_paths
            if alt and alt[0].src == primary_start
        )
    from .impact.consumer_graph import ConsumerImpactPath as _ConsumerImpactPath

    return _ConsumerImpactPath(
        consumer=primary.consumer,
        symbol=primary.symbol,
        declarations=tuple(declarations),
        public_entries=tuple(entries),
        entry_path=primary.entry_path,
        alternative_entry_paths=alternatives,
    )


def _choose_primary_match(
    matches: list[ConsumerImpactPath],
) -> ConsumerImpactPath:
    """The one match :func:`_merge_consumer_impact_paths` derives
    ``consumer``/``symbol``/``entry_path``/the primary public entry from.

    Prefers the first match that carries an actual walked ``entry_path``
    (an indirect, graph-proven chain) over a direct one — a direct match's
    own ``entry_path`` is ``[]`` by construction (its "path" is the trivial
    zero-hop declaration-is-the-entry case), so preferring it as primary
    when a genuinely indirect match also exists would silently discard the
    only real path this merged explanation could have shown. Falls back to
    the first match when every match is direct — there is no path to
    prefer among them, so display order is the only remaining tiebreaker.
    """
    return next((m for m in matches if m.entry_path), matches[0])


def attach_consumer_impact(
    change: Change,
    explained: ConsumerImpactPath,
    graph: SourceGraphSummary,
    *,
    name_consumer: bool = True,
) -> None:
    """Attach one :class:`ConsumerImpactPath` to *change* in place.

    Enrichment only — never constructs a finding, never changes a verdict or
    a severity. The overlay call site already stamps ``PROVEN_REACHABLE``/
    ``consumer_proven`` on its freshly-built ``Change`` before calling this
    (see its own comment) — this function stamps the identical three fields
    itself too (Codex review, fresh evidence), a no-op there, but load-bearing
    for :func:`enrich_covered_changes`'s *shared*-``Change`` caller: that
    change's ``reachability_state``/``public_reachable`` are whatever
    ``MarkReachability`` left them (``UNKNOWN``/``False`` by default when no
    reachability-aware suppression rule is even configured, so that step never
    ran at all) — attaching a concrete proof path here without also raising
    those fields would otherwise leave the *same* internal contradiction the
    overlay's own comment already reasons its way out of: a real consumer
    requirement resolving to a real, walked call chain is not a "maybe
    internal, maybe not" ambiguity, by construction, for either caller.
    """
    from .buildsource.graph_impact import attach_impact_metadata

    attach_impact_metadata(
        change,
        affected_public_roots=list(explained.public_entries),
        path=explained.entry_path,
        graph=graph,
        alternative_paths=explained.alternative_entry_paths,
    )
    change.reachability_proof_path = _format_consumer_impact(
        explained, graph, name_consumer=name_consumer
    )
    change.public_reachable = True
    change.reachability_kind = "consumer_proven"
    # Unconditionally overrides whatever reachability_state MarkReachability
    # (or nothing) already left here, including a prior PROVEN_UNREACHABLE
    # from its own layout/type-graph closure walk (CodeRabbit review): that
    # walk reasons about *potential* reachability from the public API
    # surface as the library's own source declares it, while this evidence
    # is a real --used-by consumer binary's own undefined-symbol resolution
    # -- an empirically stronger, more direct signal than a structural
    # closure inference, and this function only ever runs when that real
    # requirement was found. Deliberately not treated as a recorded
    # conflict between two disagreeing verdicts: an actual observed
    # dependency is not "in tension" with an inference about the surface
    # that dependency turned out to route around (an alias, a compiler
    # intrinsic call, or any other path the closure walk doesn't model) --
    # it settles the question the walk could only approximate.
    change.reachability_state = ReachabilityState.PROVEN_REACHABLE
