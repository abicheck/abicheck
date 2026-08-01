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


def _entity_spellings(change: Change) -> list[str]:
    """Every spelling of *change*'s entity to try against a persisted graph.

    Symbol (plus its bare ``::`` tail), then ``caused_by_type`` -- the same
    two identity sources ``contract_evaluation``'s own
    ``_symbol_matches``/``_type_candidates`` consult, reduced to plain
    spellings because the persisted graph is keyed by spelling, not by a live
    model object.
    """
    out: list[str] = []
    symbol = change.symbol or ""
    if symbol:
        out.append(symbol)
        if "::" in symbol:
            out.append(symbol.rsplit("::", 1)[1])
    if change.caused_by_type:
        out.append(change.caused_by_type)
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
        # One spelling index per side, built once rather than per finding:
        # `resolve_graph_node` alone rescans every node and edge on each
        # call, which is O(findings x graph) over a graph the collector
        # documents as whole-snapshot (CodeRabbit review).
        self.node_index: dict[str, dict[str, set[str]]] = {
            side: graph_node_index(graph) for side, graph in self.graph_by_side.items()
        }

    def resolve(self, side: str, spelling: str) -> set[str]:
        return set(self.node_index.get(side, {}).get(spelling, ()))

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
        return ReplayDecision(
            relevance=ContractRelevance.IN_CONTRACT,
            reason_code="all_mode_normalized_entity",
            assurance=ContractAssurance.COMPLETE,
            evidence_refs=refs,
        )
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
    for spelling in _entity_spellings(change):
        nodes |= domain.resolve(side, spelling)
    # Re-taken now that the entity's own nodes are known, so an overlay-rooted
    # decision cites the overlay rather than only the root provider.
    refs = domain.refs(side, nodes)
    if not nodes:
        # The entity is not in this side's graph at all -- unplaceable, which
        # is not the same as proven outside the contract.
        return ReplayDecision(
            relevance=ContractRelevance.UNKNOWN_UNRESOLVED,
            reason_code="required_evidence_incomplete",
            assurance=ContractAssurance.PARTIAL,
            evidence_refs=refs,
        )
    closure = domain.closure.get(side, frozenset())
    if nodes & closure:
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
