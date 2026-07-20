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

"""Safe old/new graph-node reconciliation — rename/move disambiguation
(G31 Phase B, ADR-048).

:func:`~abicheck.buildsource.source_graph.diff_source_graph`'s structural
delta (``GraphSummaryDiff``) already tells "this node id vanished, that one
appeared" — but a node's ``id`` today is whatever v1 identity scheme its
producer happened to hash (ADR-046 §2's "identity fragmentation"). A bare
declaration *rename* (same real-world entity, new spelling) or *move* (same
spelling, new declaring file) shows up there as an unrelated remove+add pair,
indistinguishable from a genuine delete-one/create-another. That is exactly
the ambiguity a flat, single-line finding (``struct_field_type_changed``-
style) cannot resolve on its own, and exactly what a *safe* reconciliation
step (this module) adds on top of B1's :mod:`entity_identity` canonical
identity — never in place of it.

**The one rule that does not change** (ADR-028 D3 / ADR-031 D6 / this file's
own contract): reconciliation *explains and localizes*; it never deletes,
suppresses, or downgrades an artifact-proven flat-diff finding produced
anywhere else in the pipeline (``diff_symbols.py``/``diff_types.py``/…). It
only adds *new*, distinctly-kinded, RISK-tier ``Change`` objects
(``declaration_renamed``/``declaration_moved``/
``declaration_identity_reconciled``) alongside whatever the rest of the
pipeline already found — see ``tests/test_graph_reconcile.py``'s
``test_reconciliation_never_deletes_or_downgrades_artifact_finding`` for the
regression proof.

Match outcomes, from strongest to weakest evidence (never resolves on a bare
short name alone — ADR-045's own "ambiguous fallback key must resolve to no
match, never an arbitrarily-chosen candidate" principle, generalized here to
graph nodes):

- **canonical-id match** — the two nodes' B1 canonical identities share the
  same ``primary_id`` (a real USR/mangled-name match) — the strongest
  possible evidence; effectively "not actually different ids" once B1
  identity is applied.
- **alias match** — the two nodes' B1 alias sets intersect, *and* that
  intersection uniquely pairs one removed node with one added node (both
  directions) — unambiguous, non-heuristic evidence (e.g. the same
  qualified name, or the same source-relative identity).
- **structural-context match** — neither identity nor alias resolves it
  (the common shape of a genuine rename: the qualified name itself changed,
  so B1's own alias set differs on both sides too), but the two nodes sit in
  the *identical, unique* structural position in their respective graphs —
  same declaring-parent edge kind/role, and no other removed/added node of
  the same kind shares that exact position. This is the weakest tier and is
  refused the moment more than one candidate shares a position (the
  case195-style ambiguous scenario in ``examples/``).
- **ambiguous** — more than one plausible candidate (via alias or structural
  context), or a fact that would otherwise resolve is itself ambiguous on
  the probing side. Recorded, but produces NO match — the pair stays a true
  add + true remove in the flat diff, at reduced confidence.
- **true add / true remove** — no candidate at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .entity_identity import (
    IDENTITY_TIER_CANONICAL,
    CanonicalIdentity,
    resolve_identity_for_node,
)

if TYPE_CHECKING:
    from .source_graph import GraphNode, SourceGraphSummary

#: Reconciliation outcomes (ADR-048 D2) — distinct from plain
#: node-add/node-remove, so a consumer can tell "the same entity, under a
#: new name/location" from "an unrelated add and an unrelated remove that
#: happen to be in the same diff".
OUTCOME_RENAMED = "declaration_renamed"
OUTCOME_MOVED = "declaration_moved"
OUTCOME_RECONCILED = "declaration_identity_reconciled"

_MATCH_KIND_CANONICAL_ID = "canonical_id"
_MATCH_KIND_ALIAS = "alias"
_MATCH_KIND_STRUCTURAL_CONTEXT = "structural_context"

#: Node kinds a rename/move reconciliation applies to — declarations and
#: types (the same set ``source_graph.DECL_NODE_KINDS`` already uses for
#: visibility classification); file/build/link-graph nodes are structural
#: build facts, not "the same entity renamed" candidates.
_RECONCILABLE_KINDS: frozenset[str] = frozenset(
    {"source_decl", "record_type", "enum_type", "typedef"}
)


@dataclass(frozen=True)
class ReconciledPair:
    """One old/new node pair the reconciliation matched as the same entity."""

    old_node: GraphNode
    new_node: GraphNode
    match_kind: str  # canonical_id | alias | structural_context
    outcome: str  # OUTCOME_RENAMED | OUTCOME_MOVED | OUTCOME_RECONCILED
    old_identity: CanonicalIdentity
    new_identity: CanonicalIdentity

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_node_id": self.old_node.id,
            "new_node_id": self.new_node.id,
            "old_label": self.old_node.label,
            "new_label": self.new_node.label,
            "match_kind": self.match_kind,
            "outcome": self.outcome,
        }


@dataclass
class GraphReconciliation:
    """Result of reconciling a :class:`~.source_graph.GraphSummaryDiff`'s
    added/removed nodes across a B1 canonical identity + structural-context
    match.
    """

    reconciled: list[ReconciledPair] = field(default_factory=list)
    ambiguous_old: list[GraphNode] = field(default_factory=list)
    ambiguous_new: list[GraphNode] = field(default_factory=list)
    true_added: list[GraphNode] = field(default_factory=list)
    true_removed: list[GraphNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reconciled": [p.to_dict() for p in self.reconciled],
            "ambiguous_old": [n.id for n in self.ambiguous_old],
            "ambiguous_new": [n.id for n in self.ambiguous_new],
            "true_added": [n.id for n in self.true_added],
            "true_removed": [n.id for n in self.true_removed],
        }


def _structural_context(
    node_id: str, graph: SourceGraphSummary
) -> frozenset[tuple[str, str, str]]:
    """The set of (direction, edge_kind+role, neighbor_kind) tuples this node
    participates in — its "position" in the graph, independent of its own
    id/name. ``neighbor_kind`` (not the neighbor's own id, which differs
    across old/new by construction for a genuinely renamed neighbor too) is
    deliberately coarse — this is a last-resort, weakest-tier signal, only
    trusted when the resulting position is unique among same-kind candidates
    (see the module docstring's "structural-context match" tier).
    """
    # Codex review: this must key on the neighbor's *kind*, matching the
    # docstring above -- not its raw node id. A raw id (e.src/e.dst) is
    # checkout-root-dependent for file/header-backed neighbors (e.g.
    # "header:///tmp/old/include/detail.h" vs
    # "header:///tmp/new/include/detail.h" for the identical project header),
    # and is by-construction different across old/new for a genuinely
    # renamed neighbor too -- either way, using the raw id here would make an
    # otherwise-unique structural position compare as different contexts and
    # silently fail to reconcile a real rename/move.
    kind_by_id = {n.id: n.kind for n in graph.nodes}
    ctx: set[tuple[str, str, str]] = set()
    for e in graph.edges:
        role = str(e.attrs.get("role", ""))
        tag = f"{e.kind}:{role}" if role else e.kind
        if e.dst == node_id:
            ctx.add(("in", tag, kind_by_id.get(e.src, "")))
        if e.src == node_id:
            ctx.add(("out", tag, kind_by_id.get(e.dst, "")))
    return frozenset(ctx)


def _path_segments(path: str) -> tuple[str, ...]:
    """Plain-path split into segments, ignoring the root/self markers."""
    from pathlib import PurePosixPath

    return tuple(p for p in PurePosixPath(path).parts if p not in ("/", ".", ""))


def _common_root_len(paths: list[str]) -> int:
    """Length (in path segments) of the longest common leading prefix shared
    by every declaring-file path on one side of a reconciliation pass.

    Old/new graphs collected from two independently-rooted checkouts (e.g.
    separate temp dirs in a benchmark harness, or two CI job workspaces)
    share no absolute root, so comparing raw absolute paths would classify
    every unmoved file as "moved". Stripping each side's own common root
    before comparing lets an unmoved file be recognised as unmoved
    regardless of where its tree happened to be checked out -- the same
    normalization :func:`source_graph_findings._common_prefix_len` applies
    for the sibling ``EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED`` family, adapted
    here for plain filesystem paths rather than ``scheme://``-prefixed node
    ids (Codex review).
    """
    seg_lists = [_path_segments(p) for p in paths if p]
    if not seg_lists:
        return 0
    if len(seg_lists) == 1:
        return max(0, len(seg_lists[0]) - 1)
    shortest = max(0, min(len(s) for s in seg_lists) - 1)
    n = 0
    for i in range(shortest):
        if len({s[i] for s in seg_lists}) == 1:
            n += 1
        else:
            break
    return n


def _root_relative_path(path: str, prefix_len: int) -> str:
    """Strip the first *prefix_len* segments from a plain filesystem path."""
    if prefix_len <= 0:
        return path
    segs = _path_segments(path)
    return "/".join(segs[prefix_len:])


def _classify_outcome(
    old_identity: CanonicalIdentity,
    new_identity: CanonicalIdentity,
    old_prefix_len: int,
    new_prefix_len: int,
) -> str:
    old_qn = old_identity.qualified_name
    new_qn = new_identity.qualified_name
    # source_relative encodes file#scope#name — compare just the file prefix
    # (before the first separator) to ask "did the declaring file change",
    # after stripping each side's own checkout-root prefix (see
    # _common_root_len).
    old_file = _root_relative_path(
        old_identity.source_relative.split("\x1f", 1)[0], old_prefix_len
    )
    new_file = _root_relative_path(
        new_identity.source_relative.split("\x1f", 1)[0], new_prefix_len
    )
    renamed = bool(old_qn) and bool(new_qn) and old_qn != new_qn
    moved = bool(old_file) and bool(new_file) and old_file != new_file
    if renamed and not moved:
        return OUTCOME_RENAMED
    if moved and not renamed:
        return OUTCOME_MOVED
    return OUTCOME_RECONCILED


def reconcile_added_removed(
    removed_nodes: list[GraphNode],
    added_nodes: list[GraphNode],
    old_graph: SourceGraphSummary,
    new_graph: SourceGraphSummary,
) -> GraphReconciliation:
    """Reconcile a :class:`~.source_graph.GraphSummaryDiff`'s ``removed_nodes``/
    ``added_nodes`` into rename/move/reconciled pairs, ambiguous candidates,
    and true adds/removes (ADR-048 D2). Pure function over the two full
    graphs (needed for structural-context matching) plus the pre-computed
    diff lists — callers typically pass
    ``diff.removed_nodes``/``diff.added_nodes`` straight from
    :func:`~.source_graph.diff_source_graph`.
    """
    result = GraphReconciliation()

    removed_by_kind: dict[str, list[GraphNode]] = {}
    for n in removed_nodes:
        if n.kind in _RECONCILABLE_KINDS:
            removed_by_kind.setdefault(n.kind, []).append(n)
        else:
            result.true_removed.append(n)
    added_by_kind: dict[str, list[GraphNode]] = {}
    for n in added_nodes:
        if n.kind in _RECONCILABLE_KINDS:
            added_by_kind.setdefault(n.kind, []).append(n)
        else:
            result.true_added.append(n)

    matched_old: set[str] = set()
    matched_new: set[str] = set()

    # Checkout-root normalization (see _common_root_len): computed once, up
    # front, over every reconcilable node's declaring file on each side --
    # not per-kind, since "which checkout root was this side collected
    # from" is a whole-graph property, not a per-node-kind one.
    old_prefix_len = _common_root_len(
        [
            str(n.attrs.get("def_file") or n.attrs.get("file") or "")
            for n in removed_nodes
            if n.kind in _RECONCILABLE_KINDS
        ]
    )
    new_prefix_len = _common_root_len(
        [
            str(n.attrs.get("def_file") or n.attrs.get("file") or "")
            for n in added_nodes
            if n.kind in _RECONCILABLE_KINDS
        ]
    )

    for kind in sorted(set(removed_by_kind) | set(added_by_kind)):
        old_list = removed_by_kind.get(kind, [])
        new_list = added_by_kind.get(kind, [])
        old_ident = {n.id: resolve_identity_for_node(n) for n in old_list}
        new_ident = {n.id: resolve_identity_for_node(n) for n in new_list}

        # -- Tier 1: canonical-id match (USR/mangled only — a shared
        # normalized-signature primary_id is intentionally handled by the
        # alias tier below, since "same qualified name + kind" is exactly
        # the alias-match evidence the module docstring describes, not a
        # canonical-identity match). ---------------------------------
        new_by_primary: dict[str, str] = {
            nid: ident.primary_id
            for nid, ident in new_ident.items()
            if ident.tier == IDENTITY_TIER_CANONICAL
        }
        new_primary_index: dict[str, list[str]] = {}
        for nid, primary in new_by_primary.items():
            new_primary_index.setdefault(primary, []).append(nid)
        for old_node in old_list:
            oid = old_node.id
            if oid in matched_old:
                continue
            if old_ident[oid].tier != IDENTITY_TIER_CANONICAL:
                continue
            primary = old_ident[oid].primary_id
            candidates = [
                c for c in new_primary_index.get(primary, []) if c not in matched_new
            ]
            if len(candidates) == 1:
                new_node = next(n for n in new_list if n.id == candidates[0])
                outcome = _classify_outcome(
                    old_ident[oid],
                    new_ident[candidates[0]],
                    old_prefix_len,
                    new_prefix_len,
                )
                result.reconciled.append(
                    ReconciledPair(
                        old_node,
                        new_node,
                        _MATCH_KIND_CANONICAL_ID,
                        outcome,
                        old_ident[oid],
                        new_ident[candidates[0]],
                    )
                )
                matched_old.add(oid)
                matched_new.add(candidates[0])

        # -- Tier 2: alias match (unambiguous both directions) ----------
        new_by_alias: dict[str, list[str]] = {}
        for nid, ident in new_ident.items():
            if nid in matched_new:
                continue
            for alias in ident.aliases:
                new_by_alias.setdefault(alias, []).append(nid)
        for old_node in old_list:
            oid = old_node.id
            if oid in matched_old:
                continue
            candidate_ids: set[str] = set()
            for alias in old_ident[oid].aliases:
                for nid in new_by_alias.get(alias, []):
                    if nid not in matched_new:
                        candidate_ids.add(nid)
            if len(candidate_ids) == 1:
                cand = next(iter(candidate_ids))
                # Ambiguity-safe both ways (ADR-045 principle): the candidate
                # must also see exactly one unmatched old-side alias partner.
                cand_ident = new_ident[cand]
                reverse_candidates = {
                    n.id
                    for n in old_list
                    if n.id not in matched_old
                    and set(old_ident[n.id].aliases) & set(cand_ident.aliases)
                }
                if len(reverse_candidates) == 1:
                    new_node = next(n for n in new_list if n.id == cand)
                    outcome = _classify_outcome(
                        old_ident[oid], cand_ident, old_prefix_len, new_prefix_len
                    )
                    result.reconciled.append(
                        ReconciledPair(
                            old_node,
                            new_node,
                            _MATCH_KIND_ALIAS,
                            outcome,
                            old_ident[oid],
                            cand_ident,
                        )
                    )
                    matched_old.add(oid)
                    matched_new.add(cand)
                else:
                    result.ambiguous_old.append(old_node)
            elif len(candidate_ids) > 1:
                result.ambiguous_old.append(old_node)

        # -- Tier 3: structural-context match (weakest, unique-position) -
        remaining_old = [
            n
            for n in old_list
            if n.id not in matched_old and n not in result.ambiguous_old
        ]
        remaining_new = [n for n in new_list if n.id not in matched_new]
        ctx_new: dict[frozenset[tuple[str, str, str]], list[str]] = {}
        for n in remaining_new:
            if n.id in matched_new:
                continue
            ctx = _structural_context(n.id, new_graph)
            if ctx:
                ctx_new.setdefault(ctx, []).append(n.id)
        for old_node in remaining_old:
            oid = old_node.id
            if oid in matched_old:
                continue
            ctx = _structural_context(oid, old_graph)
            if not ctx:
                continue
            candidates = [c for c in ctx_new.get(ctx, []) if c not in matched_new]
            if len(candidates) == 1:
                cand = candidates[0]
                # Uniqueness must also hold from the *old* side: no other
                # unmatched old node of this kind may share the identical
                # context (else the position itself is ambiguous).
                sibling_old_matches = [
                    n
                    for n in remaining_old
                    if n.id != oid
                    and n.id not in matched_old
                    and _structural_context(n.id, old_graph) == ctx
                ]
                if not sibling_old_matches:
                    new_node = next(n for n in new_list if n.id == cand)
                    outcome = _classify_outcome(
                        old_ident[oid], new_ident[cand], old_prefix_len, new_prefix_len
                    )
                    result.reconciled.append(
                        ReconciledPair(
                            old_node,
                            new_node,
                            _MATCH_KIND_STRUCTURAL_CONTEXT,
                            outcome,
                            old_ident[oid],
                            new_ident[cand],
                        )
                    )
                    matched_old.add(oid)
                    matched_new.add(cand)
                else:
                    result.ambiguous_old.append(old_node)
                    for sib in sibling_old_matches:
                        if sib not in result.ambiguous_old:
                            result.ambiguous_old.append(sib)
            elif len(candidates) > 1:
                result.ambiguous_old.append(old_node)

        for old_node in old_list:
            if old_node.id in matched_old:
                continue
            if old_node in result.ambiguous_old:
                continue
            result.true_removed.append(old_node)
        for new_node in new_list:
            if new_node.id in matched_new:
                continue
            ctx = _structural_context(new_node.id, new_graph)
            is_ambiguous_new = ctx in ctx_new and len(ctx_new.get(ctx, [])) > 1
            if is_ambiguous_new and new_node not in result.ambiguous_new:
                result.ambiguous_new.append(new_node)
            elif not is_ambiguous_new:
                result.true_added.append(new_node)

    return result


def reconcile_graph_diff(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> GraphReconciliation:
    """Convenience wrapper: structurally diff *old*/*new* (via
    :func:`~.source_graph.diff_source_graph`) and reconcile the resulting
    added/removed nodes in one call.
    """
    from .source_graph import diff_source_graph

    diff = diff_source_graph(old, new)
    return reconcile_added_removed(diff.removed_nodes, diff.added_nodes, old, new)


#: Human-readable outcome descriptions, keyed by :data:`OUTCOME_RENAMED`
#: et al. — used by :func:`diff_graph_reconciliation_findings` below.
_OUTCOME_PROSE: dict[str, str] = {
    OUTCOME_RENAMED: "renamed",
    OUTCOME_MOVED: "moved to a different declaring file",
    OUTCOME_RECONCILED: "identity-reconciled (both name and location evidence changed)",
}


def diff_graph_reconciliation_findings(
    reconciliation: GraphReconciliation,
) -> list[Any]:
    """Turn a :class:`GraphReconciliation` into ordinary, RISK-tier
    ``Change`` findings (ADR-048 D2) — enrichment/classification metadata,
    never a verdict override.

    Per ADR-028 D3 / ADR-031 D6 (unchanged by this module): these findings
    explain and localize a rename/move that would otherwise show up (or
    already has shown up, via a *different* detector) as an unrelated
    add+remove pair. They are additive — this function never touches, drops,
    or reclassifies any other ``Change`` the rest of the pipeline produced;
    see ``tests/test_graph_reconcile.py``'s
    ``test_reconciliation_never_deletes_or_downgrades_artifact_finding``.
    """
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    kind_by_outcome = {
        OUTCOME_RENAMED: ChangeKind.DECLARATION_RENAMED,
        OUTCOME_MOVED: ChangeKind.DECLARATION_MOVED,
        OUTCOME_RECONCILED: ChangeKind.DECLARATION_IDENTITY_RECONCILED,
    }
    findings: list[Any] = []
    for pair in reconciliation.reconciled:
        old_label = pair.old_node.label or pair.old_node.id
        new_label = pair.new_node.label or pair.new_node.id
        prose = _OUTCOME_PROSE.get(pair.outcome, "identity-reconciled")
        findings.append(
            Change(
                kind=kind_by_outcome.get(
                    pair.outcome, ChangeKind.DECLARATION_IDENTITY_RECONCILED
                ),
                symbol=new_label,
                description=(
                    f"Graph evidence reconciles {old_label!r} (old) with "
                    f"{new_label!r} (new) as the same declaration, {prose} "
                    f"(match evidence: {pair.match_kind}). This does not by "
                    "itself indicate a break — it explains what would "
                    "otherwise look like an unrelated add+remove pair in the "
                    "L5 graph diff; any artifact-level finding for either "
                    "spelling stands on its own evidence, unaffected by this "
                    "reconciliation."
                ),
                old_value=old_label,
                new_value=new_label,
                qualified_name=pair.new_identity.qualified_name or None,
            )
        )
    return findings
