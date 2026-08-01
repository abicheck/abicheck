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

"""ADR-049 Phase 4's two procedures: original-decision **replay** and
new-policy **re-evaluation**.

Plan Section 5.1 names both, and the difference between them is the whole
point of persisting two separate blocks:

- :func:`replay_original_decisions` reproduces what the original run decided.
  It reads the ``decision_receipt`` and nothing else -- no evidence is
  re-walked, no live file is re-probed, and *this build's* own provider
  defaults, evaluator version, or reason vocabulary cannot alter the answer
  ("current required-provider defaults cannot alter the recorded original
  decision"). Its only precondition is the D6 version check.
- :func:`reevaluate_from_evidence` answers the different question "what would
  this comparison decide under a *different* contract?" -- old observations,
  newly resolved context. It walks the persisted, policy-independent
  ``contract_evidence`` graph and never touches the receipt, so a receipt
  written under ``public`` puts no thumb on an ``exports`` re-evaluation.

Both refuse a context whose version counters exceed what this build
understands (:func:`load_replayable_context`), which is D6's fail-closed rule
made unavoidable rather than optional: reinterpreting a newer schema's data
under older rules is exactly the silent-misread failure the counters exist to
prevent. A *mixed* context -- older evidence with a newer evaluation context,
or vice versa -- is explicitly fine; it is the ordinary re-evaluation case,
not an error.

Re-evaluation is deliberately a *narrower* evaluator than the live
:mod:`abicheck.contract_evaluation`, and says so rather than pretending
otherwise. It decides membership from the persisted graph and the persisted
provider completeness alone; it has no access to the live surfaces' origin
maps, hidden-friend reasoning, or the pipeline's own
``surface_exclusion_reason`` annotations, none of which are observations the
evidence block carries. Where the live evaluator would resolve a finding
using one of those, this one answers ``UNKNOWN_UNRESOLVED`` -- an
under-claim, never a stronger claim than the evidence supports. That is also
why :func:`compare_decisions` reports directional agreement rather than plain
equality: a replay that *weakens* a decision is a coverage limit; one that
*strengthens* it (turning an unresolved finding into a proven exclusion)
would be a real defect.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from .checker_types import Change
from .contract_context import finding_key
from .contract_evidence import (
    EvidenceSearchRecord,
    PersistedContractContext,
    TypeGraphSnapshot,
    check_persisted_context_versions_supported,
)
from .contract_evidence_collect import graph_node_index
from .contract_relevance_types import (
    ContractAssurance,
    ContractMode,
    ContractRelevance,
    EvidenceCompleteness,
    EvidenceProviderStatus,
    coerce_contract_mode,
)


def load_replayable_context(ctx: PersistedContractContext) -> PersistedContractContext:
    """Return *ctx* once every version counter is known-supported (D6).

    A thin, deliberately mandatory gate: both procedures below call it, so
    there is no path that consumes a persisted context without the check.
    Raises :class:`~abicheck.contract_evidence.UnsupportedSchemaVersionError`
    for a counter newer than this build's ceiling.
    """
    check_persisted_context_versions_supported(ctx)
    return ctx


def replay_original_decisions(
    ctx: PersistedContractContext,
) -> Mapping[str, ContractRelevance]:
    """The original run's own per-finding decisions, verbatim.

    Returns the receipt's ``relevance_by_finding`` map unchanged. Nothing is
    recomputed: that is the guarantee, not a limitation.
    """
    return load_replayable_context(ctx).decision_receipt.relevance_by_finding


@dataclass(frozen=True)
class ReplayDecision:
    """One re-evaluated finding's decision, with the evidence it rests on."""

    relevance: ContractRelevance
    reason_code: str
    assurance: ContractAssurance
    evidence_refs: tuple[str, ...] = ()


def _type_scoped(change: Change) -> bool:
    from .surface import _MEMBER_LEVEL_TYPE_KIND_NAMES, _TYPE_LEVEL_KIND_NAMES

    return (
        change.kind.value in _TYPE_LEVEL_KIND_NAMES
        or change.kind.value in _MEMBER_LEVEL_TYPE_KIND_NAMES
    )


#: ``decl:``/type node-kind prefixes, as :mod:`contract_evidence_collect`
#: spells them. A spelling resolves through one flat index, so the *kind* of
#: node it lands on is what separates the two questions the live evaluator
#: asks separately -- see :func:`_entity_lookups`.
_DECL_NODE_KINDS: tuple[str, ...] = ("decl:",)
_TYPE_NODE_KINDS: tuple[str, ...] = ("record:", "enum:", "typedef:")


def _symbol_may_name_a_declaration(change: Change) -> bool:
    """Whether *change*'s own symbol may be matched against a declaration.

    Mirrors ``_symbol_matches``'s first branch: for a **type-level** kind the
    live matcher accepts the symbol as a declaration identity only when it is
    *mangled*, because a bare type spelling colliding with a real export name
    proves nothing about which of the two a finding is on. Every other kind
    (symbol-level, and the member-level families whose ``symbol`` is the
    owning type) is matched by plain membership there, so it is here too.
    """
    from .contract_evaluation import _is_mangled_identity
    from .surface import _TYPE_LEVEL_KIND_NAMES

    if change.kind.value not in _TYPE_LEVEL_KIND_NAMES:
        return True
    return _is_mangled_identity(change.symbol or "")


def _type_identity_is_ambiguous(
    domain: _PersistedDomain, side: str, spelling: str
) -> bool:
    """Both clauses ``_confirmed_type_matches`` rejects a match on.

    It refuses when "the candidate itself is flagged in *ambiguous*" **or**
    "when the candidate is a *qualified* name whose own trailing tail is
    ambiguous" -- the second because ``_walk_type_closure`` reaches an
    ambiguous bare tail from a signature by adding *every* matching record,
    so the qualified candidate's presence in the closure does not show that
    *it*, rather than its ambiguous sibling, is what the signature actually
    reached.

    Both are answered from the provider's own persisted
    ``ambiguous_identities`` -- the same set live consults -- rather than
    re-derived from the graph. Two earlier attempts inferred it from the
    resolution instead, and each missed a case the set states outright: a
    qualified candidate whose *tail* collides, and then two records sharing
    one canonical identity, which are one node in the graph but two entries
    in ``_index_surface_types``'s own count (Codex review, twice). An
    observation this module needs is cheaper to persist than to reconstruct.
    """
    ambiguous = domain.ambiguous_identities(side)
    if not ambiguous:
        return False
    if spelling in ambiguous:
        return True
    return "::" in spelling and spelling.rsplit("::", 1)[1] in ambiguous


def _entity_lookups(
    change: Change, mode: ContractMode
) -> list[tuple[str, tuple[str, ...]]]:
    """``(spelling, admissible node kinds)`` to try against a persisted graph.

    Symbol, its bare ``::`` tail, and ``caused_by_type`` -- reduced to plain
    spellings, because the persisted graph is keyed by spelling rather than
    by a live model object. Which of the three apply is **per mode**, because
    the live evaluator's own matching differs by mode and a looser resolver
    here is a strengthening path (Codex review, fresh evidence):

    - the bare tail is `public`-only. ``_symbol_matches`` takes
      ``allow_tail_fallback=False`` on the ``exports`` path, precisely so an
      unexported ``ns::foo`` cannot borrow an exported C ``foo``'s identity;
      offering the tail here reintroduced exactly that collision, one layer
      down.
    - ``caused_by_type`` is consulted for a type- or member-level finding
      only, mirroring ``_exports_mode_decision``'s own ``type_scoped`` gate
      and its stated reason: closure membership answers a *type*-level
      question, so a symbol-level finding on an unexported helper must not
      be placed in contract merely because its ``caused_by_type`` is one
      some *other* exported signature reaches. Applied in both modes --
      `public` reaches the same closure through the same node set, and
      being stricter than the live matcher can only weaken.

    The node *kind* each spelling may land on is the same distinction one
    level down, and it needs stating separately because this module resolves
    every spelling through one flat index while the live evaluator asks two
    separate questions -- "is this symbol an export root" (declarations only)
    and "is this type in the closure" (types only). One spelling can answer
    to both: with an exported ``api(foo *)``, a reachable ``struct foo`` and
    an unexported function ``foo``, the spelling ``foo`` resolved to
    ``{decl:foo, record:foo}``, and the reachable *record* placed the
    *function* removal ``IN_CONTRACT`` against a live
    ``PROVEN_OUT_OF_CONTRACT`` (Codex review, fresh evidence, confirmed with
    a minimal snapshot). So a symbol may reach a declaration node only under
    the rule ``_symbol_matches`` uses (:func:`_symbol_may_name_a_declaration`)
    and a type node only when the finding is type-scoped at all, and
    ``caused_by_type`` -- a type name by construction -- reaches type nodes
    alone.
    """
    out: list[tuple[str, tuple[str, ...]]] = []
    symbol = change.symbol or ""
    type_scoped = _type_scoped(change)
    if symbol:
        kinds: tuple[str, ...] = ()
        if _symbol_may_name_a_declaration(change):
            kinds += _DECL_NODE_KINDS
        if type_scoped:
            kinds += _TYPE_NODE_KINDS
        if kinds:
            out.append((symbol, kinds))
            if mode is not ContractMode.EXPORTS and "::" in symbol:
                out.append((symbol.rsplit("::", 1)[1], kinds))
    if change.caused_by_type and type_scoped:
        out.append((change.caused_by_type, _TYPE_NODE_KINDS))
    return out


def _side_of(change: Change) -> str:
    from .contract_evaluation import authoritative_side

    return authoritative_side(change)


def _not_applicable(change: Change) -> bool:
    from .contract_evaluation import _NOT_APPLICABLE_KIND_SLUGS

    return change.kind.value in _NOT_APPLICABLE_KIND_SLUGS


class _PersistedDomain:
    """One side's roots, closure, and completeness, read off the block.

    Built from :func:`~abicheck.contract_context.persisted_domain_view`, the
    one implementation of "which providers supply this mode's roots, which
    entry carries the type graph, and what does the closure from those roots
    look like" -- shared with the receipt builder so a re-evaluated closure
    cannot drift from the persisted one (CodeRabbit review).
    """

    def __init__(self, ctx: PersistedContractContext, mode: ContractMode) -> None:
        from .contract_context import persisted_domain_view

        view = persisted_domain_view(ctx.contract_evidence, mode)
        self.mode = mode
        self.roots: dict[str, set[str]] = view.roots_by_side
        self.graph_by_side: dict[str, TypeGraphSnapshot] = view.graph_by_side
        self.record_by_side: dict[str, EvidenceSearchRecord] = view.root_record_by_side
        self.header_record_by_side: dict[str, EvidenceSearchRecord] = (
            view.header_record_by_side
        )
        self.closure: dict[str, frozenset[str]] = view.closure_by_side
        self.overlay_roots: dict[str, dict[str, set[str]]] = view.overlay_roots_by_side
        self.root_nodes: dict[str, set[str]] = view.closure_seeds_by_side
        self.post_manifest: dict[str, tuple[EvidenceSearchRecord, frozenset[str]]] = (
            view.post_manifest_by_side
        )
        # One spelling index per side, built once rather than per finding:
        # `resolve_graph_node` alone rescans every node and edge on each
        # call, which is O(findings x graph) over a graph the collector
        # documents as whole-snapshot (CodeRabbit review).
        self.node_index: dict[str, dict[str, set[str]]] = {
            side: graph_node_index(graph) for side, graph in self.graph_by_side.items()
        }
        # Only `exports` needs the alias-free view (see `resolve`), and it
        # costs a second pass over every node -- so it is built for that mode
        # alone rather than for every re-evaluation.
        self.exact_node_index: dict[str, dict[str, set[str]]] = (
            {
                side: graph_node_index(graph, follow_aliases=False)
                for side, graph in self.graph_by_side.items()
            }
            if mode is ContractMode.EXPORTS
            else {}
        )

    def resolve(self, side: str, spelling: str) -> set[str]:
        nodes = set(self.node_index.get(side, {}).get(spelling, ()))
        if self.mode is not ContractMode.EXPORTS:
            return nodes
        return self._without_shared_export_aliases(side, spelling, nodes)

    def _without_shared_export_aliases(
        self, side: str, spelling: str, nodes: set[str]
    ) -> set[str]:
        """*nodes*, minus export roots reached only through a shared alias.

        ``compute_export_surface`` prunes its own root set the same way, and
        for the same reason: with an exported ``ns::foo`` and an unrelated,
        unexported C ``foo`` both declared, ``_symbol_keys`` gives the C++
        root the bare alias ``foo`` that the C declaration also answers to,
        and "an alias shared with a non-root proves nothing about which
        declaration a finding names". Live therefore answers
        ``PROVEN_OUT_OF_CONTRACT`` for a finding on the unexported ``foo``;
        resolving that spelling here through the persisted graph's own alias
        edges reached the exported node and replayed ``IN_CONTRACT`` -- the
        inverse collision of the tail-fallback one ``_entity_lookups``
        already closed, and the same strengthening (Codex review, fresh
        evidence).

        Only *declaration* nodes count as the sharing non-root: live's
        ``nonroot_keys`` is built from declarations alone, so a record or
        enum answering to the same spelling must not prune a genuine export
        root. A spelling that is itself a root's own canonical node identity
        is exempt for the same reason it is exempt live -- only the derived
        aliases around an identity can be shared.
        """
        roots = self.root_nodes.get(side, set())
        rooted = nodes & roots
        if not rooted:
            return nodes
        sharing = {n for n in nodes if n.startswith("decl:")} - roots
        if not sharing:
            return nodes
        if self.exact_node_index.get(side, {}).get(spelling, set()) & roots:
            return nodes
        return nodes - rooted

    def overlay_names(self, side: str, nodes: set[str]) -> bool:
        """Whether an explicit overlay names one of *nodes* itself.

        The persisted counterpart of the live evaluator's two per-finding
        overlay overrides (``force_public_symbols`` and
        ``public_surface_allowlist``), which return ``IN_CONTRACT`` before its
        own resolvability gate and independently of any closure. Membership
        here is direct only -- what an overlaid declaration's signature
        happens to reference is not itself overlaid.
        """
        return any(nodes & roots for roots in self.overlay_roots.get(side, {}).values())

    def domain_is_available(self, side: str) -> bool:
        """Whether this side's root provider observed its domain *at all*.

        Weaker than :meth:`domain_is_closed` (which asks whether the search
        was *complete*) and checked before any conclusion, positive or
        negative. An ``UNAVAILABLE`` record is the ledger's spelling of the
        live evaluator's own ``not auth.resolvable`` gate -- one-to-one, since
        ``contract_evidence_collect`` writes ``UNAVAILABLE`` exactly when
        ``PublicSurface.resolvable``/``ExportSurface.resolvable`` is ``False``
        -- and that gate returns ``UNKNOWN_UNRESOLVED`` live.

        Without this check a replay could claim membership from evidence the
        producing run had already declared unusable (Codex review, fresh
        evidence): an ``elf_only_mode`` snapshot leaves the header surface
        unresolvable, yet the provider entry still carries the declarations
        and the type graph it collected before bailing out, so the closure
        walk happily reached the entity and answered ``IN_CONTRACT`` with
        ``COMPLETE`` assurance against a live ``UNKNOWN_UNRESOLVED`` -- a
        strengthening :func:`compare_decisions` rejects.
        """
        record = self.record_by_side.get(side)
        return record is not None and record.status is EvidenceProviderStatus.AVAILABLE

    def ambiguous_identities(self, side: str) -> frozenset[str]:
        """The spellings this side's provider recorded as ambiguous.

        Taken from the domain's own root provider, falling back to the
        header record -- ``all`` has no root provider (ADR-049 D2) but its
        findings are still placed against the declaration parse, which is
        the search that observed the collision.
        """
        record = self.record_by_side.get(side) or self.header_record_by_side.get(side)
        return (
            frozenset(record.ambiguous_identities)
            if record is not None
            else frozenset()
        )

    def domain_is_closed(self, side: str) -> bool:
        """Whether this side's root provider searched its domain completely.

        Both completeness facets Section 4.2 separates are required: a
        ``PARTIAL`` search proves no absence, and ambiguous identity coverage
        means a match (or a miss) may be about a different entity than the
        one being judged.
        """
        record = self.record_by_side.get(side)
        return (
            record is not None
            and record.completeness is EvidenceCompleteness.COMPLETE
            and record.identity_coverage is EvidenceCompleteness.COMPLETE
        )

    def can_prove_exclusion(self, side: str) -> bool:
        """Whether a *negative* conclusion is supportable from this block.

        Deliberately narrower than :meth:`domain_is_closed`, and only for the
        ``exports`` domain (Codex review, fresh evidence). ADR-049 Section
        4.3's ``out_of_contract_proof_complete`` requires either a terminal
        exact exclusion or *positive* out-of-contract provenance plus every
        stronger-or-equal provider having completed. The two domains differ
        in whether the persisted block can satisfy that:

        - ``exports``: the export provider's ``COMPLETE`` state *is*
          ``ExportSurface.exclusion_is_provable`` -- an observed table, a
          resolved root, every root typed, no unaccounted export, no
          unresolved type edge. Absence from that closure is the terminal
          exclusion the ADR names, so a negative conclusion is supported.
        - ``public``: the ADR's positive proof is private/system-header
          *provenance*, which this block does not carry (it records
          declarations and a type graph, not per-entity header origin), and
          ``configuration_coverage`` is ``NOT_STARTED`` for every record this
          build writes, so no compile/generated-header variant ever
          completed. Concluding ``PROVEN_OUT_OF_CONTRACT`` from graph
          non-membership alone would therefore claim more than the evidence
          supports -- and this module may only ever *weaken* the live
          decision, never strengthen it.
        """
        return self.mode is ContractMode.EXPORTS and self.domain_is_closed(side)

    def refs(self, side: str, nodes: set[str] | None = None) -> tuple[str, ...]:
        # An entity rooted by an explicit overlay rests on that overlay's own
        # record, not on the header provider that merely supplied the graph:
        # citing `public_header` for a declaration retained solely by
        # `--public-symbol` names evidence the decision never used (Codex
        # review, fresh evidence). Cited *alongside* the root provider, since
        # the closure walk that reached the entity is still the root
        # provider's own.
        overlay: tuple[str, ...] = ()
        if nodes:
            overlay = tuple(
                sorted(
                    record_id
                    for record_id, roots in self.overlay_roots.get(side, {}).items()
                    if nodes & roots
                )
            )
        record = self.record_by_side.get(side)
        if record is not None:
            return (record.id, *overlay)
        if overlay:
            return overlay
        # `all` has no root provider (ADR-049 D2), so cite the declaration
        # parse that placed the finding as an entity at all -- the same
        # record `evidence_refs_for_reason` cites for this mode on the live
        # path. An empty tuple would carry the *non-entity* meaning instead
        # (CodeRabbit review).
        header = self.header_record_by_side.get(side)
        return (header.id,) if header is not None else ()


_MODE_MEMBERSHIP_REASON: Mapping[ContractMode, str] = {
    ContractMode.PUBLIC: "public_root_membership",
    ContractMode.EXPORTS: "export_root_membership",
}


def reevaluate_from_evidence(
    ctx: PersistedContractContext,
    findings: Sequence[Change],
    *,
    mode: ContractMode | str | None = None,
    finding_id: Callable[[Change], object] | None = None,
) -> dict[str, ReplayDecision]:
    """Re-decide *findings* from persisted observations under a new context.

    *mode* defaults to the persisted ``evaluation_context``'s own contract
    mode, which reproduces the original domain; pass a different one to ask
    the new-policy question ("re-evaluation uses old observations with a
    newly resolved context"). The evidence block is untouched either way --
    that it needs no re-collection for a mode the original run never
    evaluated is exactly what "policy-independent" buys.
    """
    ctx = load_replayable_context(ctx)
    resolved_mode = coerce_contract_mode(
        mode
        if mode is not None
        else ctx.evaluation_context.resolved_config.contract.mode
    )
    domain = _PersistedDomain(ctx, resolved_mode)
    out: dict[str, ReplayDecision] = {}
    for change in findings:
        out[finding_key(change, finding_id)] = _reevaluate_one(
            change, domain, resolved_mode
        )
    return out


def _manifest_could_admit(change: Change, committed: frozenset[str]) -> bool:
    """Whether a ``--post-manifest`` run could have kept *change* in surface.

    ``post_processing.FilterNonPublicSurface._run_allowlist``'s own keep
    conditions, minus the one this module cannot reproduce. It demotes a
    finding only when the finding is symbol-level, carries a symbol, is not a
    hidden-friend finding, *and* that symbol is a concrete export of either
    snapshot -- and it matches the committed set exactly, never through the
    suffix-tolerant matcher.

    The concrete-export test is deliberately not reproduced here. The
    persisted export provider's declarations are not the same set as
    ``_snapshot_export_ids`` (which reads the raw platform tables), so
    consulting it could answer "not a concrete export, therefore kept" for a
    symbol the live run did demote -- a strengthening. Omitting the test
    weakens instead: a symbol-level finding on a non-exported symbol
    re-evaluates ``UNKNOWN_UNRESOLVED`` where live said ``IN_CONTRACT``,
    which is this module's documented direction.

    ``--public-symbol``'s widening rescue is omitted for the same reason: it
    applies live only when header scoping is *also* on, and the persisted
    context records the resolved contract mode rather than that flag, so
    honoring it unconditionally would strengthen exactly the
    ``--no-scope-public-headers --post-manifest --public-symbol`` case the
    live pipeline refuses to rescue.
    """
    from .surface import is_hidden_friend_finding, is_symbol_level_finding

    symbol = change.symbol or ""
    if (
        not symbol
        or not is_symbol_level_finding(change)
        or is_hidden_friend_finding(change)
    ):
        return True
    return symbol in committed


def _all_mode_decision(
    change: Change, domain: _PersistedDomain, side: str, refs: tuple[str, ...]
) -> ReplayDecision:
    """``all``'s decision, which an exact manifest still narrows.

    ADR-049 D2's ``all`` row drops *header-origin* scoping, not every
    provider: the live evaluator checks ``--post-manifest``'s own exclusion
    ahead of its ``all``-mode shortcut, precisely because a committed-export
    manifest is an exact, closed-domain observation rather than a
    header-origin classification. Replaying ``IN_CONTRACT`` unconditionally
    contradicted a live ``PROVEN_OUT_OF_CONTRACT`` for a concrete export the
    manifest omits -- a flip :func:`compare_decisions` rejects (Codex review,
    fresh evidence).

    The narrowed answer is ``UNKNOWN_UNRESOLVED``, not the live
    ``PROVEN_OUT_OF_CONTRACT``: this module reproduces the manifest's keep
    conditions only approximately (see :func:`_manifest_could_admit`), so it
    knows the finding is not *demonstrably* in contract without being able to
    prove the exclusion the live pipeline proved.
    """
    manifest = domain.post_manifest.get(side)
    if manifest is not None and not _manifest_could_admit(change, manifest[1]):
        return ReplayDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
            evidence_refs=(*refs, manifest[0].id),
        )
    return ReplayDecision(
        relevance=ContractRelevance.IN_CONTRACT,
        reason_code="all_mode_normalized_entity",
        assurance=ContractAssurance.COMPLETE,
        evidence_refs=refs,
    )


def _reevaluate_one(
    change: Change, domain: _PersistedDomain, mode: ContractMode
) -> ReplayDecision:
    if _not_applicable(change):
        return ReplayDecision(
            relevance=ContractRelevance.NOT_APPLICABLE,
            reason_code="non_entity_finding",
            assurance=ContractAssurance.COMPLETE,
        )
    side = _side_of(change)
    refs = domain.refs(side)
    if mode is ContractMode.ALL:
        return _all_mode_decision(change, domain, side, refs)
    graph = domain.graph_by_side.get(side)
    if graph is None:
        # A legacy or one-sided context: the facts this domain needs for the
        # authoritative side are simply absent (plan Section 5.1: "legacy
        # snapshots remain readable but become unresolved where old-side
        # facts needed by `public` are absent").
        return ReplayDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.UNAVAILABLE,
            evidence_refs=refs,
        )
    nodes: set[str] = set()
    # Kept apart from `nodes`: membership is decided only on nodes of a kind
    # this finding's own spelling may legitimately name, while the overlay
    # override and evidence attribution below stay on the full resolution,
    # since both are keyed by the *symbol* live too (`_change_matches_symbols`
    # asks nothing about node kind). See `_entity_lookups`.
    member_nodes: set[str] = set()
    ambiguous = False
    for spelling, kinds in _entity_lookups(change, mode):
        resolved = domain.resolve(side, spelling)
        nodes |= resolved
        admissible = {n for n in resolved if n.startswith(kinds)}
        member_nodes |= admissible
        if set(_TYPE_NODE_KINDS) <= set(kinds) and _type_identity_is_ambiguous(
            domain, side, spelling
        ):
            ambiguous = True
    # Re-taken now that the entity's own nodes are known, so an overlay-rooted
    # decision cites the overlay rather than only the root provider.
    refs = domain.refs(side, nodes)
    if domain.overlay_names(side, nodes):
        # The user named this entity itself. Live, that is an unconditional
        # override checked *ahead* of the resolvability gate below, so it
        # holds here too even when the root provider observed nothing.
        return ReplayDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code=_MODE_MEMBERSHIP_REASON[ContractMode.PUBLIC],
            assurance=ContractAssurance.COMPLETE,
            evidence_refs=refs,
        )
    if not domain.domain_is_available(side):
        # The root provider itself reported it never observed this domain --
        # the persisted counterpart of the live evaluator's `not
        # auth.resolvable` branch, which returns exactly this. Checked after
        # the graph rather than instead of it, because a provider can record
        # an unusable search and still have attached the declarations and
        # graph it collected on the way to that conclusion (see
        # `domain_is_available`).
        return ReplayDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.UNAVAILABLE,
            evidence_refs=refs,
        )
    if not member_nodes:
        # The entity is not in this side's graph at all -- unplaceable, which
        # is not the same as proven outside the contract. Judged on the
        # kind-admissible set: a spelling that landed only on a node kind it
        # may not name has told us nothing about *this* entity.
        return ReplayDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
            evidence_refs=refs,
        )
    closure = domain.closure.get(side, frozenset())
    # Rootness is decided before ambiguity, exactly as live: a declaration
    # that *is* a root answers its own membership by its own linker identity,
    # and `_exports_mode_decision` returns on that before it ever computes a
    # type candidate. Roots are seeds of the closure, so splitting this out
    # changes nothing for an unambiguous finding.
    if member_nodes & domain.root_nodes.get(side, set()):
        return ReplayDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code=_MODE_MEMBERSHIP_REASON[mode],
            assurance=ContractAssurance.COMPLETE,
            evidence_refs=refs,
        )
    if ambiguous:
        # Which of the colliding types the finding is about is what decides
        # membership, and that is exactly what is unknown -- so neither the
        # closure hit below nor the exclusion after it may be claimed. The
        # reason code is live's own for this shape.
        return ReplayDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="identity_ambiguous",
            assurance=ContractAssurance.PARTIAL,
            evidence_refs=refs,
        )
    if member_nodes & closure:
        return ReplayDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code=_MODE_MEMBERSHIP_REASON[mode],
            assurance=ContractAssurance.COMPLETE,
            evidence_refs=refs,
        )
    if domain.can_prove_exclusion(side):
        return ReplayDecision(
            relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
            reason_code="terminal_authoritative_exclusion",
            assurance=ContractAssurance.COMPLETE,
            evidence_refs=refs,
        )
    return ReplayDecision(
        relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
        reason_code="required_evidence_incomplete",
        assurance=ContractAssurance.PARTIAL,
        evidence_refs=refs,
    )


#: How confident each relevance value is, for the directional comparison
#: :func:`compare_decisions` makes. A *lower* rank is a weaker claim; a
#: replay may only ever weaken. ``NOT_APPLICABLE`` is deliberately absent:
#: it is not a point on this scale at all (the finding is off the entity
#: axis entirely), so a transition into or out of it is reported as a plain
#: disagreement rather than being ranked.
_CLAIM_STRENGTH: Mapping[ContractRelevance, int] = {
    ContractRelevance.UNKNOWN_UNRESOLVED: 0,
    ContractRelevance.UNKNOWN_UNPROVEN: 1,
    ContractRelevance.IN_CONTRACT: 2,
    ContractRelevance.PROVEN_OUT_OF_CONTRACT: 2,
}


@dataclass(frozen=True)
class DecisionComparison:
    """How a re-evaluation's decisions relate to an original receipt's."""

    #: Findings whose decision is identical.
    agreed: tuple[str, ...] = ()
    #: Findings the replay could only *weaken* (evidence the persisted block
    #: does not carry -- a documented coverage limit, not a defect).
    weakened: tuple[str, ...] = ()
    #: Findings the replay decided *more strongly*, or flipped between two
    #: equally-strong-but-opposite conclusions (``IN_CONTRACT`` vs.
    #: ``PROVEN_OUT_OF_CONTRACT``). Either is a real defect: persisted
    #: evidence may never out-claim the live evaluator that wrote it.
    strengthened: tuple[str, ...] = ()
    #: Findings whose two decisions are not on the same scale at all: one
    #: side says ``NOT_APPLICABLE`` (the finding is off the entity axis
    #: entirely) and the other places it on that axis. Ranking them against
    #: each other would be meaningless, so they are reported separately --
    #: but they are *not* excused: the two evaluators share one
    #: ``_NOT_APPLICABLE_KIND_SLUGS`` set, so a disagreement here means the
    #: recorded receipt and this build classify the same ``ChangeKind``
    #: differently, which a consumer correlating them must see (CodeRabbit
    #: review: the ranking comment promised this bucket, and it did not
    #: exist -- such a transition silently landed in ``strengthened``).
    disagreed: tuple[str, ...] = ()
    #: Findings present in one map and not the other.
    only_in_original: tuple[str, ...] = ()
    only_in_replay: tuple[str, ...] = ()

    @property
    def is_sound(self) -> bool:
        """True when the replay neither out-claimed nor lost a finding.

        Includes :attr:`disagreed`: an entity-axis mismatch is a real
        inconsistency between the recorded decision and this build, not a
        tolerated weakening.
        """
        return (
            not self.strengthened and not self.disagreed and not self.only_in_original
        )


def compare_decisions(
    original: Mapping[str, ContractRelevance],
    replayed: Mapping[str, ReplayDecision | ContractRelevance],
) -> DecisionComparison:
    """Directionally compare an original receipt with a re-evaluation.

    See the module docstring for why this is not equality: the persisted
    evaluator is strictly narrower than the live one, so weakening is
    expected and strengthening is the thing worth failing on.
    """
    replayed_relevance = {
        key: (value if isinstance(value, ContractRelevance) else value.relevance)
        for key, value in replayed.items()
    }
    agreed: list[str] = []
    weakened: list[str] = []
    strengthened: list[str] = []
    disagreed: list[str] = []
    for key in sorted(set(original) & set(replayed_relevance)):
        was, now = original[key], replayed_relevance[key]
        if was == now:
            agreed.append(key)
            continue
        old_rank = _CLAIM_STRENGTH.get(was)
        new_rank = _CLAIM_STRENGTH.get(now)
        if old_rank is None or new_rank is None:
            # One side is NOT_APPLICABLE, which the strength scale
            # deliberately does not rank -- see `disagreed`.
            disagreed.append(key)
        elif new_rank >= old_rank:
            strengthened.append(key)
        else:
            weakened.append(key)
    return DecisionComparison(
        agreed=tuple(agreed),
        weakened=tuple(weakened),
        strengthened=tuple(strengthened),
        disagreed=tuple(disagreed),
        only_in_original=tuple(sorted(set(original) - set(replayed_relevance))),
        only_in_replay=tuple(sorted(set(replayed_relevance) - set(original))),
    )


def unresolved_rate(decisions: Iterable[ReplayDecision | ContractRelevance]) -> float:
    """Share of *decisions* that could not be resolved, in ``[0.0, 1.0]``.

    One of the four quantities Phase 3's "Measure:" list names
    (``scripts/measure_contract_shadow.py`` reports it per provider/domain);
    exposed here so the script and any consumer compute it identically.
    Returns ``0.0`` for an empty input -- no findings means nothing
    unresolved, not a division by zero.
    """
    values = [d if isinstance(d, ContractRelevance) else d.relevance for d in decisions]
    if not values:
        return 0.0
    unresolved = sum(
        1
        for v in values
        if v
        in (ContractRelevance.UNKNOWN_UNRESOLVED, ContractRelevance.UNKNOWN_UNPROVEN)
    )
    return unresolved / len(values)
