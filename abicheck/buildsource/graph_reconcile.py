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


def _path_segments(path: str) -> tuple[str, ...]:
    """Plain-path split into segments, ignoring the root/self markers.

    Normalizes ``\\`` to ``/`` first (same as
    ``source_graph_findings._path_segments``/``source_graph.py``'s own
    caller-file normalization): ``PurePosixPath`` treats a backslash as an
    ordinary filename character, not a separator, so a Windows-style
    declaring path (``C:\\old\\include\\api.h``) would never be split at
    all -- silently defeating the project-root-marker search in
    :func:`_project_relative_path` and comparing raw checkout roots
    (Codex review).
    """
    from pathlib import PurePosixPath

    posix = path.replace("\\", "/")
    return tuple(p for p in PurePosixPath(posix).parts if p not in ("/", ".", ""))


#: Conventional project-root directory names — a superset of
#: :data:`abicheck.header_utils._INCLUDE_ROOT_NAMES` (which only needs
#: ``include``/``inc`` for its narrower include-root-inference purpose;
#: this also covers ``src``/``source``/``sources`` layouts). Used here as an
#: anchor for stripping a checkout-root prefix from a single declaring-file
#: path with no sibling to derive a shared prefix from (Codex review — see
#: :func:`_project_relative_path`).
_CONVENTIONAL_ROOT_MARKERS: frozenset[str] = frozenset(
    {"include", "inc", "src", "source", "sources"}
)


def _project_relative_path(path: str) -> str:
    """Best-effort project-relative form of a declaring-file/header path.

    Two independently-rooted checkouts of the same tree (separate temp dirs
    in a benchmark harness, or two CI job workspaces) share no absolute
    root, so comparing raw absolute paths would misclassify an unmoved file
    as "moved" purely because of where its tree happened to be checked out.

    With more than one declaring file on a side, the shared checkout-root
    prefix could in principle be derived structurally (comparing multiple
    paths against each other) — but a single sample gives no such baseline,
    and blindly reserving "everything but the basename" as an assumed
    checkout root (an earlier version of this function did that) silently
    hides a real cross-directory move that happens to keep the same
    filename (Codex review: ``/tmp/old/src/foo.h`` -> ``/tmp/new/include/foo.h``
    must not read as unmoved). Anchoring on the last conventional root-marker
    segment instead (``include``/``inc``/``src``/``source``/``sources`` — the
    same vocabulary :data:`abicheck.header_utils._INCLUDE_ROOT_NAMES` already
    uses for a similar purpose) gets both cases right without needing a
    second sample: it strips the checkout-root prefix when a recognizable
    project-layout marker is present, and falls back to comparing the full
    path (never silently "unmoved") when it isn't.
    """
    if not path:
        return path
    segs = _path_segments(path)
    for i in range(len(segs) - 1, -1, -1):
        if segs[i].lower() in _CONVENTIONAL_ROOT_MARKERS:
            return "/".join(segs[i:])
    return "/".join(segs)


#: Node kinds whose ``label``/declaring-path fields are a filesystem path,
#: not a semantic name. :func:`resolve_identity_for_node` falls back to
#: ``node.label`` for ``qualified_name`` when no explicit ``qualified_name``
#: attr is present -- for these kinds that fallback silently hands back the
#: raw (checkout-root-dependent) path, so :func:`_neighbor_identity` must
#: route them to the path-normalization branch directly rather than trust
#: that fallback (Codex review: real header_graph/source_graph header nodes
#: use full path labels). ``source``/``generated_file`` are the same
#: filesystem-path-as-label shape (``SourceGraphSummary.indexes()``/
#: ``file_node()`` already group all four kinds together) so they need the
#: same routing (CodeRabbit review).
_FILE_LIKE_KINDS: frozenset[str] = frozenset(
    {"header", "file", "source", "generated_file"}
)


def _neighbor_identity(node: GraphNode) -> str:
    """A checkout-root-independent, kind-disambiguated identity for a graph
    node used as a *neighbor* in :func:`_all_structural_contexts`.

    Neither the neighbor's raw node id (checkout-root/rename-dependent) nor
    its bare kind alone (collides two genuinely different parents of the
    same kind, e.g. two different structs each losing/gaining an unrelated
    field — Codex review) is safe here. For a file-like neighbor (see
    :data:`_FILE_LIKE_KINDS`), always uses a project-relative form of its
    declaring path/label -- never its resolved "qualified name", which for
    these kinds is only ever the raw path via label fallback. For every
    other kind, prefers the neighbor's own resolved qualified name (stable,
    not path-based); falls back to bare kind only when neither fact is
    available, so the tuple this feeds is never actually empty.
    """
    if node.kind in _FILE_LIKE_KINDS:
        path = str(node.attrs.get("def_file") or node.attrs.get("file") or node.label or "")
        return f"{node.kind}:{_project_relative_path(path)}" if path else node.kind
    ident = resolve_identity_for_node(node)
    if ident.qualified_name:
        return f"{node.kind}:{ident.qualified_name}"
    path = str(node.attrs.get("def_file") or node.attrs.get("file") or "")
    if path:
        return f"{node.kind}:{_project_relative_path(path)}"
    return node.kind


def _is_unqualified_identity(ident: CanonicalIdentity) -> bool:
    """True when *ident* carries no more disambiguating evidence than a
    bare short name: no scope-qualified name (no ``::``) and no
    arity-distinguishing param types (the normalized-signature arity
    segment is ``"0"``). Used to decide whether the "qualified:"/plain
    signature aliases are trustworthy Tier-2 evidence or are, in
    substance, exactly as weak as a "name:" alias (see
    :func:`_strong_aliases`).
    """
    if "::" in ident.qualified_name:
        return False
    sig_parts = ident.normalized_signature.split("\x1f")
    return len(sig_parts) < 3 or sig_parts[2] == "0"


def _strong_aliases(ident: CanonicalIdentity) -> set[str]:
    """The subset of *ident*'s aliases trustworthy for Tier-2 alias
    matching in :func:`reconcile_added_removed`.

    A bare "name:<short>" alias is always excluded (ADR-045: an ambiguous
    fallback key must resolve to no match). When the identity itself is
    unqualified (see :func:`_is_unqualified_identity`) -- e.g. a
    header-only-graph ``source_decl`` node seeded with only a bare
    label/name and no explicit ``qualified_name`` attr -- the
    "qualified:"/plain signature aliases carry that exact same weak
    evidence (``resolve_identity_for_node`` silently derives
    ``qualified_name`` from the bare label), so they are excluded too
    (Codex review, fresh evidence).
    """
    if _is_unqualified_identity(ident):
        return {
            a
            for a in ident.aliases
            if not a.startswith(("name:", "qualified:", "sig:"))
        }
    return {a for a in ident.aliases if not a.startswith("name:")}


def _all_structural_contexts(
    graph: SourceGraphSummary,
) -> dict[str, frozenset[tuple[str, str, str]]]:
    """Every node's structural "position" in *graph* — the set of
    (direction, edge_kind+role, neighbor_identity) tuples it participates
    in, independent of its own id/name. ``neighbor_identity`` (see
    :func:`_neighbor_identity` — not the neighbor's own raw node id, which
    is checkout-root-dependent, and not bare kind alone, which collides
    unrelated same-kind parents) is a last-resort, weakest-tier signal,
    only trusted when the resulting position is unique among same-kind
    candidates (see the module docstring's "structural-context match"
    tier).

    Computed for every node in one pass (a single scan over ``graph.edges``,
    with each node's own identity resolved exactly once) rather than
    per-node-per-call: Tier 3 below calls this once per side per kind group,
    but each of those calls previously recomputed every node's identity
    AND rescanned every edge from scratch for every single node it probed
    (including the O(candidates²) sibling-uniqueness check) — on a large
    graph (e.g. a template/SYCL-heavy header closure) that blew up into a
    real CI timeout (Codex review round; caught by CI, not a review
    comment).
    """
    identity_by_id = {n.id: _neighbor_identity(n) for n in graph.nodes}
    ctx: dict[str, set[tuple[str, str, str]]] = {n.id: set() for n in graph.nodes}
    for e in graph.edges:
        role = str(e.attrs.get("role", ""))
        tag = f"{e.kind}:{role}" if role else e.kind
        if e.dst in ctx:
            ctx[e.dst].add(("in", tag, identity_by_id.get(e.src, "")))
        if e.src in ctx:
            ctx[e.src].add(("out", tag, identity_by_id.get(e.dst, "")))
    return {nid: frozenset(c) for nid, c in ctx.items()}


def _declaring_files(graph: SourceGraphSummary) -> dict[str, str]:
    """Every node's declaring-file project-relative path, resolved via an
    incoming ``SOURCE_DECLARES`` edge.

    ``resolve_identity_for_node``'s ``source_relative`` alias needs a
    ``def_file``/``file`` attr on the declaration node itself, but real
    header-only-graph ``source_decl`` nodes (``header_graph.py``'s
    ``seed_decl``) carry no such attr -- the declaring header is only
    recorded as the source end of a ``SOURCE_DECLARES`` edge. Without this,
    :func:`_classify_outcome` never sees a declaring file for those nodes
    and a real cross-header move is misclassified as
    ``declaration_identity_reconciled`` instead of ``OUTCOME_MOVED``
    (Codex review, fresh evidence).
    """
    label_by_id = {n.id: n.label for n in graph.nodes}
    result: dict[str, str] = {}
    for e in graph.edges:
        if e.kind != "SOURCE_DECLARES":
            continue
        label = label_by_id.get(e.src)
        if label:
            result[e.dst] = _project_relative_path(str(label))
    return result


def _classify_outcome(
    old_identity: CanonicalIdentity,
    new_identity: CanonicalIdentity,
    *,
    old_declaring_file: str = "",
    new_declaring_file: str = "",
) -> str:
    old_qn = old_identity.qualified_name
    new_qn = new_identity.qualified_name
    # source_relative encodes file#scope#name — compare just the file prefix
    # (before the first separator) to ask "did the declaring file change",
    # after normalizing each side's path (see _project_relative_path).
    # Falls back to the SOURCE_DECLARES-edge-derived declaring file (see
    # _declaring_files) when the node carries no def_file/file attr of its
    # own.
    old_file = (
        _project_relative_path(old_identity.source_relative.split("\x1f", 1)[0])
        or old_declaring_file
    )
    new_file = (
        _project_relative_path(new_identity.source_relative.split("\x1f", 1)[0])
        or new_declaring_file
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

    # Computed once per side, for the whole graph -- not per kind, and not
    # per node-probed-in-Tier-3 (see _all_structural_contexts' own
    # docstring for why the latter mattered on a large graph).
    old_contexts = _all_structural_contexts(old_graph)
    new_contexts = _all_structural_contexts(new_graph)
    old_declaring_files = _declaring_files(old_graph)
    new_declaring_files = _declaring_files(new_graph)

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
                    old_declaring_file=old_declaring_files.get(oid, ""),
                    new_declaring_file=new_declaring_files.get(candidates[0], ""),
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
        # A bare "name:<short>" alias (resolve_canonical_identity() adds one
        # for every entity that has a name at all) must never be sufficient
        # evidence on its own -- that is exactly the "ambiguous fallback key
        # must resolve to no match" principle this module's own docstring
        # cites (ADR-045), generalized to aliases: two unrelated
        # declarations that merely share a short name (e.g. old `a::foo`
        # removed, unrelated new `b::foo` added) must not reconcile just
        # because "foo" is their only common alias (Codex review).
        #
        # That principle is silently defeated when the entity has no real
        # qualified_name fact at all: resolve_identity_for_node() falls
        # back to node.label for `qualified_name`, so a header-only-graph
        # source_decl node seeded with only a bare label (the common
        # production shape -- no explicit qualified_name attr) launders
        # that same bare name into "qualified:<label>" and the plain
        # signature alias, both exempt from the name: filter above. Two
        # unrelated such nodes sharing a label then reconcile on evidence
        # no stronger than a bare name (Codex review, fresh evidence).
        # _strong_aliases() treats those two aliases as equally weak
        # whenever the identity carries no scope-qualification ("::") and
        # no arity-distinguishing param types.
        strong_new_by_alias: dict[str, list[str]] = {}
        for nid, ident in new_ident.items():
            if nid in matched_new:
                continue
            for alias in _strong_aliases(ident):
                strong_new_by_alias.setdefault(alias, []).append(nid)
        for old_node in old_list:
            oid = old_node.id
            if oid in matched_old:
                continue
            candidate_ids: set[str] = set()
            for alias in _strong_aliases(old_ident[oid]):
                for nid in strong_new_by_alias.get(alias, []):
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
                    and _strong_aliases(old_ident[n.id]) & _strong_aliases(cand_ident)
                }
                if len(reverse_candidates) == 1:
                    new_node = next(n for n in new_list if n.id == cand)
                    outcome = _classify_outcome(
                        old_ident[oid],
                        cand_ident,
                        old_declaring_file=old_declaring_files.get(oid, ""),
                        new_declaring_file=new_declaring_files.get(cand, ""),
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
            ctx = new_contexts.get(n.id, frozenset())
            if ctx:
                ctx_new.setdefault(ctx, []).append(n.id)
        for old_node in remaining_old:
            oid = old_node.id
            if oid in matched_old:
                continue
            ctx = old_contexts.get(oid, frozenset())
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
                    and old_contexts.get(n.id, frozenset()) == ctx
                ]
                if not sibling_old_matches:
                    new_node = next(n for n in new_list if n.id == cand)
                    outcome = _classify_outcome(
                        old_ident[oid],
                        new_ident[cand],
                        old_declaring_file=old_declaring_files.get(oid, ""),
                        new_declaring_file=new_declaring_files.get(cand, ""),
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
            ctx = new_contexts.get(new_node.id, frozenset())
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


def _public_reachable_ids(graph: SourceGraphSummary) -> frozenset[str]:
    """Every node id reachable from a public-API entry in *graph*, via the
    identical dependency-edge closure ``PUBLIC_API_INTERNAL_DEPENDENCY_ADDED``
    already uses (``source_graph_findings._dependency_reachability``) —
    exported-symbol-mapped or public-header-visible entries, walked over
    :data:`~.source_graph.DEPENDENCY_EDGE_KINDS`. Includes the entries
    themselves. A node declared in a private header can still appear here
    if a public entry reaches it (e.g. as a private field type of a public
    struct) — "declared privately" and "not part of the public surface"
    are different questions; only the latter matters for gating a finding's
    verdict impact.
    """
    from .source_graph import DEPENDENCY_EDGE_KINDS, is_public_dependency_node

    adjacency: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.kind in DEPENDENCY_EDGE_KINDS:
            adjacency.setdefault(e.src, []).append(e.dst)
    exported_decls = {
        e.src for e in graph.edges if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL"
    }
    node_by_id = {n.id: n for n in graph.nodes}
    entries = [
        n.id
        for n in graph.nodes
        if is_public_dependency_node(n.id, node_by_id, exported_decls)
    ]
    reachable: set[str] = set(entries)
    stack = list(entries)
    while stack:
        cur = stack.pop()
        for nxt in adjacency.get(cur, []):
            if nxt not in reachable:
                reachable.add(nxt)
                stack.append(nxt)
    return frozenset(reachable)


def diff_graph_reconciliation_findings(
    reconciliation: GraphReconciliation,
    old_graph: SourceGraphSummary | None = None,
    new_graph: SourceGraphSummary | None = None,
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

    *old_graph*/*new_graph* (optional; the real production caller,
    ``source_graph_findings._reconciliation_findings``, always supplies
    them) gate a pair's RISK verdict impact on public reachability (Codex
    review, fresh evidence): a rename/move reconciled entirely within
    private, never-publicly-reached declarations must not turn an
    otherwise-clean comparison into ``COMPATIBLE_WITH_RISK`` on its own --
    that would penalize a purely internal implementation-detail refactor.
    A *declared*-private node that a public entry still transitively
    reaches (e.g. a private field type of a public struct,
    :func:`_public_reachable_ids`) is genuinely part of the public surface
    and is never suppressed here. Omitting the graphs (as the module's own
    lower-level unit tests do) skips this gate entirely, matching the
    pre-existing behavior.
    """
    from ..checker_policy import ChangeKind
    from ..checker_types import Change
    from .source_graph import EVIDENCE_TIER_L5

    kind_by_outcome = {
        OUTCOME_RENAMED: ChangeKind.DECLARATION_RENAMED,
        OUTCOME_MOVED: ChangeKind.DECLARATION_MOVED,
        OUTCOME_RECONCILED: ChangeKind.DECLARATION_IDENTITY_RECONCILED,
    }
    boundary = f"[{EVIDENCE_TIER_L5}]"
    old_reachable = _public_reachable_ids(old_graph) if old_graph is not None else None
    new_reachable = _public_reachable_ids(new_graph) if new_graph is not None else None
    findings: list[Any] = []
    for pair in reconciliation.reconciled:
        if old_reachable is not None and new_reachable is not None:
            old_reached = pair.old_node.id in old_reachable
            new_reached = pair.new_node.id in new_reachable
            if not old_reached and not new_reached:
                continue
        old_label = pair.old_node.label or pair.old_node.id
        new_label = pair.new_node.label or pair.new_node.id
        prose = _OUTCOME_PROSE.get(pair.outcome, "identity-reconciled")
        # Prefer the new side's declaring file (matches the rest of the L5
        # findings' [L5_SOURCE_GRAPH]-boundary convention in
        # source_graph_findings.py); fall back to the old side, then to the
        # generic boundary tag when neither node carries a real path
        # (Codex review: this Change previously had no source_location at
        # all, unlike every other L5 finding, so suppression/report flows
        # that match by location couldn't target it).
        new_file = pair.new_identity.source_relative.split("\x1f", 1)[0]
        old_file = pair.old_identity.source_relative.split("\x1f", 1)[0]
        location = new_file or old_file or boundary
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
                source_location=location,
            )
        )
    return findings
