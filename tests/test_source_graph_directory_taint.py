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

"""L5 source-graph node ids/labels for anonymous-tag/lambda-closure type
identities must not embed a checkout-dependent directory (ADR-031/ADR-048).

Split out of ``test_source_graph.py`` (which sits at its own AI-readiness
line-count cap) rather than appended there. Regression coverage for the
reported oneDNN/oneDPL "risk declaration_renamed" false positive: a real
declaration's identity falls back to its raw ``"lambda at <path>:<line>:
<col>"`` spelling, and two builds of the identical, unedited declaration
under different checkout roots must reconcile to identical L5 node ids and
labels rather than reading as a real rename.
"""

from __future__ import annotations

from abicheck.buildsource.build_evidence import BuildEvidence
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_graph import _type_node_id, build_source_graph


def _entity(qn: str, kind: str) -> SourceEntity:
    return SourceEntity(
        id=qn,
        kind=kind,
        qualified_name=qn,
        source_location=SourceLocation(
            path="include/foo.h", line=1, origin="PUBLIC_HEADER"
        ),
        visibility="public_header",
    )


def test_type_node_id_strips_checkout_directory_from_parenthesized_lambda_marker() -> (
    None
):
    # castxml/clang qualType-style spelling, as strip_anonymous_type_location
    # already handles for the L2 header-AST backend.
    old = _type_node_id("raii_guard<(lambda at /old/checkout/lib.hpp:4:37)>")
    new = _type_node_id("raii_guard<(lambda at /new/checkout/lib.hpp:4:37)>")
    assert old == new


def test_type_node_id_strips_checkout_directory_from_bare_lambda_marker() -> None:
    # Observed directly in real L5 graphs (oneDNN/oneDPL): a bare "lambda at
    # <path>:<line>:<col>" identity with no wrapping parens at all -- a shape
    # strip_anonymous_type_location's own paren-anchored regex does not match.
    old = _type_node_id(
        "lambda at /tmp/abicheck-eval/pkgs/onedpl/nanorange.hpp:16516:32"
    )
    new = _type_node_id("lambda at /mnt/pr-gate/onedpl-inc/nanorange.hpp:16516:32")
    assert old == new
    # A genuinely different header (different basename) at the same
    # coordinates must still stay distinct.
    other_header = _type_node_id("lambda at /mnt/pr-gate/other.hpp:16516:32")
    assert other_header != old


def test_build_source_graph_type_node_label_and_id_are_directory_independent() -> None:
    # End-to-end: two SourceAbiSurfaces built from the "same" unedited lambda
    # closure declaration under two different checkout roots must reconcile
    # to identical node ids and labels, not read as a rename/add+remove pair
    # purely from directory taint (the reported oneDNN/oneDPL "risk
    # declaration_renamed" false positive).
    old_surface = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    old_surface.reachable_types.append(
        _entity("lambda at /old/checkout/lib.hpp:4:37", "record")
    )
    new_surface = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    new_surface.reachable_types.append(
        _entity("lambda at /new/checkout/lib.hpp:4:37", "record")
    )

    old_graph = build_source_graph(BuildEvidence(), source_abi=old_surface)
    new_graph = build_source_graph(BuildEvidence(), source_abi=new_surface)

    old_type_nodes = [n for n in old_graph.nodes if n.kind == "record_type"]
    new_type_nodes = [n for n in new_graph.nodes if n.kind == "record_type"]
    assert len(old_type_nodes) == len(new_type_nodes) == 1
    assert old_type_nodes[0].id == new_type_nodes[0].id
    assert old_type_nodes[0].label == new_type_nodes[0].label


def test_bare_marker_normalization_skips_quoted_literals() -> None:
    # A C++20 fixed-string NTTP argument can quote text that merely *looks*
    # like a bare anonymous/lambda marker (Codex review, fresh evidence) --
    # a real marker is never itself quoted, so rewriting one inside a quoted
    # literal would fabricate a same-identity collision between two
    # genuinely distinct specializations quoting different paths.
    old = _type_node_id('Tag<"lambda at /a/foo.hpp:1:2">')
    new = _type_node_id('Tag<"lambda at /b/foo.hpp:1:2">')
    assert old != new
    # An actual, unquoted bare marker alongside quoted lookalike text is
    # still normalized.
    mixed = _type_node_id(
        'Wrapper<"lambda at /a/foo.hpp:1:2", (lambda at /c/bar.hpp:9:1)>'
    )
    assert "/a/foo.hpp" in mixed
    assert "/c/bar.hpp" not in mixed


def test_bare_marker_normalization_finds_the_terminal_coordinates() -> None:
    # A checkout path can itself contain a colon-digit-colon-digit-shaped
    # segment (a timestamped build directory) that a non-greedy path group
    # would mistake for the marker's own terminal ":line:col" -- silently
    # leaving the real, checkout-dependent tail unmodified past the
    # truncated match (Codex review, fresh evidence, fifth round).
    old = _type_node_id("lambda at /tmp/build-2026T12:34:56/src/foo.hpp:4:37")
    new = _type_node_id("lambda at /mnt/build-2026T12:34:56/src/foo.hpp:4:37")
    assert old == new
    assert "build-2026T12" not in old
    assert old.endswith(":4:37")


def test_graph_node_from_dict_migrates_pre_normalization_ids() -> None:
    # A build-source pack persisted before this normalization existed
    # carries the old, raw, checkout-path-bearing node id/label -- loading
    # it must migrate transparently (Codex review, fresh evidence), or a
    # freshly-generated graph for the identical, unedited declaration would
    # never match it and diff_source_graph would read it as removed+added.
    from abicheck.buildsource.graph_facts import GraphNode

    old_node = GraphNode.from_dict(
        {
            "id": "type://lambda at /old/checkout/lib.hpp:4:37",
            "kind": "record_type",
            "label": "lambda at /old/checkout/lib.hpp:4:37",
        }
    )
    fresh_id = _type_node_id("lambda at /new/checkout/lib.hpp:4:37")
    assert old_node.id == fresh_id
    assert old_node.label == "lambda:lib.hpp:4:37"


def test_graph_edge_from_dict_migrates_pre_normalization_endpoints() -> None:
    from abicheck.buildsource.graph_facts import GraphEdge

    old_edge = GraphEdge.from_dict(
        {
            "src": "decl://foo",
            "dst": "type://lambda at /old/checkout/lib.hpp:4:37",
            "edge": "DECL_HAS_TYPE",
        }
    )
    assert old_edge.dst == _type_node_id("lambda at /new/checkout/lib.hpp:4:37")


def test_source_graph_summary_from_dict_recomputes_stale_graph_id() -> None:
    # A persisted graph_id was only ever trustworthy for the exact,
    # pre-migration content it was computed over -- to_dict() reuses a
    # truthy self.graph_id as-is, so a stale value must not survive a load
    # that changed node ids underneath it (Codex review, fresh evidence).
    from abicheck.buildsource.source_graph import SourceGraphSummary

    g = SourceGraphSummary.from_dict(
        {
            "schema_version": 2,
            "graph_id": "sha256:deliberately-stale",
            "nodes": [
                {
                    "id": "type://lambda at /old/checkout/lib.hpp:4:37",
                    "kind": "record_type",
                    "label": "lambda at /old/checkout/lib.hpp:4:37",
                }
            ],
            "edges": [],
        }
    )
    assert g.graph_id != "sha256:deliberately-stale"
    assert g.graph_id == g.compute_graph_id()


def test_source_graph_summary_from_dict_migrates_entity_resolver_aliases() -> None:
    # An EntityResolver persisted alongside a pre-normalization graph is
    # keyed by the OLD, pre-migration node id -- canonical_id_for(node.id)
    # must still resolve after load, or a persisted canonical identity goes
    # silently unreachable (Codex review, fresh evidence). The node carries
    # the same "usr" attr resolve_identity_for_node() would have read when
    # the persisted canonical id was first computed -- from_dict() rebuilds
    # the resolver from current (coalesced) node facts (a later Codex
    # finding) rather than trust the remap alone, so this is what actually
    # makes the rebuilt canonical id agree with the persisted one.
    from abicheck.buildsource.source_graph import SourceGraphSummary

    old_id = "type://lambda at /old/checkout/lib.hpp:4:37"
    g = SourceGraphSummary.from_dict(
        {
            "schema_version": 2,
            "nodes": [
                {
                    "id": old_id,
                    "kind": "record_type",
                    "label": old_id,
                    "attrs": {"usr": "c:@S@Widget"},
                }
            ],
            "edges": [],
            "entity_resolver": {
                "aliases": {old_id: "usr:c:@S@Widget"},
                "conflicts": [],
            },
        }
    )
    migrated_id = g.nodes[0].id
    assert migrated_id != old_id
    assert g.entity_resolver.canonical_id_for(migrated_id) == "usr:c:@S@Widget"
    assert g.entity_resolver.canonical_id_for(old_id) is None


def test_add_node_normalizes_label_for_any_producer() -> None:
    # Label normalization is centralized in ensure_facts_and_resolve (Codex
    # review, fresh evidence) so it covers every decl/type producer that
    # calls SourceGraphSummary.add_node -- not just the two that happened to
    # build GraphNodes directly in source_graph.py/type_graph.py.
    from abicheck.buildsource.graph_facts import GraphNode
    from abicheck.buildsource.source_graph import SourceGraphSummary

    g = SourceGraphSummary()
    g.add_node(
        GraphNode(
            id="decl://x",
            kind="source_decl",
            label="lambda at /some/checkout/foo.hpp:9:1",
            provenance="call_graph",
        )
    )
    assert g.nodes[0].label == "lambda:foo.hpp:9:1"


def test_source_graph_summary_from_dict_coalesces_ids_colliding_after_migration() -> (
    None
):
    # Two persisted nodes that only differ by checkout root normalize to the
    # SAME id -- from_dict() must route them through add_node() so they
    # coalesce into one entry, not leave both objects in self.nodes under
    # one shared id with self._node_ids/_node_by_id silently disagreeing
    # about the count (Codex review, fresh evidence).
    from abicheck.buildsource.source_graph import SourceGraphSummary

    g = SourceGraphSummary.from_dict(
        {
            "schema_version": 2,
            "nodes": [
                {
                    "id": "type://lambda at /old/checkout/lib.hpp:4:37",
                    "kind": "record_type",
                    "label": "lambda at /old/checkout/lib.hpp:4:37",
                },
                {
                    "id": "type://lambda at /new/checkout/lib.hpp:4:37",
                    "kind": "record_type",
                    "label": "lambda at /new/checkout/lib.hpp:4:37",
                },
            ],
            "edges": [],
        }
    )
    assert len(g.nodes) == 1
    assert len(g._node_ids) == len(g.nodes)
    assert len(g._node_by_id) == len(g.nodes)


def test_entity_resolver_remap_normalizes_canonical_values_too() -> None:
    # A canonical id with no USR/mangled name available falls back to
    # entity_identity.normalized_signature's "sig:<qualified_name>..." form,
    # which embeds the raw, checkout-path-bearing qualified name verbatim --
    # remap_node_ids must normalize that VALUE, not just the v1-id KEY, or
    # canonical_id_for() keeps returning a directory-tainted id that never
    # matches a freshly-resolved graph's canonical id (Codex review, fresh
    # evidence, second round).
    from abicheck.buildsource.entity_resolver import EntityResolver

    old_id = "type://lambda at /old/checkout/lib.hpp:4:37"
    stale_canonical = "sig:lambda at /old/checkout/lib.hpp:4:37\x1frecord\x1f0"
    r = EntityResolver.from_dict(
        {"aliases": {old_id: stale_canonical}, "conflicts": []}
    )
    (canonical,) = r.aliases.values()
    assert "/old/checkout" not in canonical
    assert canonical == "sig:lambda:lib.hpp:4:37\x1frecord\x1f0"


def test_source_graph_summary_from_dict_rebuilds_resolver_from_coalesced_node() -> None:
    # Two persisted nodes for the same declaration under different checkout
    # roots can have been resolved to weaker/stronger canonical ids
    # independently -- once they coalesce into one node (merge_entity_facts),
    # the resolver must be rebuilt from the coalesced node's own merged
    # attrs, not keep whichever pre-merge alias a dict comprehension happened
    # to keep last (Codex review, fresh evidence, third round).
    from abicheck.buildsource.source_graph import SourceGraphSummary

    old_id = "type://lambda at /old/checkout/lib.hpp:4:37"
    new_id_raw = "type://lambda at /new/checkout/lib.hpp:4:37"
    g = SourceGraphSummary.from_dict(
        {
            "schema_version": 2,
            "nodes": [
                # No usr -- would resolve to a weaker sig:-tier canonical id.
                {"id": old_id, "kind": "record_type", "label": old_id},
                # Carries a usr -- the coalesced node inherits it via
                # merge_entity_facts, so the rebuilt resolver must reflect it.
                {
                    "id": new_id_raw,
                    "kind": "record_type",
                    "label": new_id_raw,
                    "attrs": {"usr": "c:@S@Widget"},
                },
            ],
            "edges": [],
            "entity_resolver": {"aliases": {}, "conflicts": []},
        }
    )
    assert len(g.nodes) == 1
    assert g.entity_resolver.canonical_id_for(g.nodes[0].id) == "usr:c:@S@Widget"


def test_source_graph_summary_from_dict_recomputes_coverage_after_coalescing() -> None:
    # coverage's node_kinds/source_decls counts are stale the moment
    # migration coalesces two persisted nodes into one -- from_dict() must
    # recompute them (via finalize()), not copy the pre-migration payload
    # verbatim (Codex review, fresh evidence, third round).
    from abicheck.buildsource.source_graph import SourceGraphSummary

    g = SourceGraphSummary.from_dict(
        {
            "schema_version": 2,
            "coverage": {"source_decls": 99, "node_kinds": {"record_type": 99}},
            "nodes": [
                {
                    "id": "type://lambda at /old/checkout/lib.hpp:4:37",
                    "kind": "record_type",
                    "label": "lambda at /old/checkout/lib.hpp:4:37",
                },
                {
                    "id": "type://lambda at /new/checkout/lib.hpp:4:37",
                    "kind": "record_type",
                    "label": "lambda at /new/checkout/lib.hpp:4:37",
                },
            ],
            "edges": [],
        }
    )
    assert len(g.nodes) == 1
    assert g.coverage["node_kinds"] == {"record_type": 1}


def test_normalization_never_touches_non_decl_or_type_node_ids() -> None:
    # A non-declaration node whose id/label happens to spell marker-shaped
    # text -- e.g. a source:// node at a real path literally containing
    # "lambda at ...:1:2" -- must not be rewritten just because the text
    # coincidentally matches; the normalization is gated to decl://type://
    # ids only (Codex review, fresh evidence, sixth round). Only a genuine
    # decl/type node still normalizes.
    from abicheck.buildsource.graph_facts import GraphEdge, GraphNode

    raw_source_id = "source:///tmp/lambda at build/foo.hpp:1:2"
    raw_source_label = "/tmp/lambda at build/foo.hpp:1:2"
    n = GraphNode.from_dict(
        {"id": raw_source_id, "kind": "source", "label": raw_source_label}
    )
    assert n.id == raw_source_id
    assert n.label == raw_source_label

    e = GraphEdge.from_dict(
        {
            "src": raw_source_id,
            "dst": raw_source_id,
            "edge": "COMPILE_UNIT_INCLUDES_FILE",
        }
    )
    assert e.src == raw_source_id
    assert e.dst == raw_source_id

    # ensure_facts_and_resolve's own label normalization (add_node's path,
    # not from_dict) is gated the same way.
    from abicheck.buildsource.source_graph import SourceGraphSummary

    g = SourceGraphSummary()
    g.add_node(GraphNode(id=raw_source_id, kind="source", label=raw_source_label))
    assert g.nodes[0].label == raw_source_label

    # A genuine decl/type node under the identical marker text still
    # normalizes correctly -- the gate doesn't just disable everything.
    decl_id = "decl://lambda at /old/checkout/foo.hpp:1:2"
    d = GraphNode.from_dict({"id": decl_id, "kind": "source_decl", "label": decl_id})
    assert d.id != decl_id
    assert "/old/checkout" not in d.id


def test_identity_attrs_are_normalized_alongside_label() -> None:
    # A decl/type node's attrs["name"]/attrs["qualified_name"] can carry the
    # identical raw, checkout-path-bearing spelling as its label -- both
    # producer fields are populated from the same declaration identity.
    # entity_identity.resolve_identity_for_node() prefers these attrs over
    # node.label, so leaving them un-normalized would let two
    # checkout-equivalent nodes -- identical id/label after normalization --
    # still resolve to two different ADR-048 canonical identities purely
    # from directory taint surviving in attrs (Codex review, fresh evidence).
    from abicheck.buildsource.graph_facts import GraphNode, _decl_node_id

    old_qn = "raii_guard<(lambda at /old/checkout/lib.hpp:4:37)>"
    new_qn = "raii_guard<(lambda at /new/checkout/lib.hpp:4:37)>"

    # A real producer always builds a decl/type node's own id via
    # _decl_node_id/_type_node_id (already normalized) -- this test's
    # subject is specifically whether attrs["name"]/["qualified_name"] get
    # the same treatment, not id normalization itself.
    old_node = GraphNode(
        id=_decl_node_id(old_qn),
        kind="source_decl",
        label=old_qn,
        attrs={"name": old_qn, "qualified_name": old_qn, "usr": None},
    )
    new_node = GraphNode(
        id=_decl_node_id(new_qn),
        kind="source_decl",
        label=new_qn,
        attrs={"name": new_qn, "qualified_name": new_qn, "usr": None},
    )

    # ensure_facts_and_resolve already runs in GraphNode's own __post_init__
    # path is not automatic for a bare construction -- route both through
    # the same choke point add_node uses.
    from abicheck.buildsource.graph_facts import ensure_facts_and_resolve

    ensure_facts_and_resolve(old_node)
    ensure_facts_and_resolve(new_node)

    assert old_node.id == new_node.id
    assert old_node.label == new_node.label
    assert "/old/checkout" not in old_node.attrs["name"]
    assert "/new/checkout" not in new_node.attrs["name"]
    assert old_node.attrs["name"] == new_node.attrs["name"]
    assert old_node.attrs["qualified_name"] == new_node.attrs["qualified_name"]

    from abicheck.buildsource import entity_identity

    old_identity = entity_identity.resolve_identity_for_node(old_node)
    new_identity = entity_identity.resolve_identity_for_node(new_node)
    assert old_identity.primary_id == new_identity.primary_id


def test_resolve_entities_rebuild_does_not_reintroduce_taint_from_loaded_pack() -> None:
    # SourceGraphSummary.from_dict() rebuilds entity_resolver from the
    # loaded nodes' own (now-normalized) attrs via resolve_entities() --
    # confirming the rebuild reads already-normalized attrs rather than
    # reintroducing checkout taint EntityResolver.from_dict()'s own
    # remap_node_ids() already cleaned out of the persisted aliases.
    from abicheck.buildsource.source_graph import SourceGraphSummary

    old_qn = "raii_guard<(lambda at /old/checkout/lib.hpp:4:37)>"
    new_qn = "raii_guard<(lambda at /new/checkout/lib.hpp:4:37)>"

    old_pack = {
        "nodes": [
            {
                "id": f"decl://{old_qn}",
                "kind": "source_decl",
                "label": old_qn,
                "attrs": {"name": old_qn, "qualified_name": old_qn},
            }
        ],
        "edges": [],
        "entity_resolver": {"aliases": {f"decl://{old_qn}": f"sig:{old_qn}\x1f\x1f0"}},
    }
    new_pack = {
        "nodes": [
            {
                "id": f"decl://{new_qn}",
                "kind": "source_decl",
                "label": new_qn,
                "attrs": {"name": new_qn, "qualified_name": new_qn},
            }
        ],
        "edges": [],
        "entity_resolver": {"aliases": {f"decl://{new_qn}": f"sig:{new_qn}\x1f\x1f0"}},
    }

    old_graph = SourceGraphSummary.from_dict(old_pack)
    new_graph = SourceGraphSummary.from_dict(new_pack)

    old_v1 = old_graph.nodes[0].id
    new_v1 = new_graph.nodes[0].id
    assert old_v1 == new_v1

    old_canonical = old_graph.entity_resolver.canonical_id_for(old_v1)
    new_canonical = new_graph.entity_resolver.canonical_id_for(new_v1)
    assert old_canonical is not None
    assert "/old/checkout" not in old_canonical
    assert "/new/checkout" not in new_canonical
    assert old_canonical == new_canonical


def test_facts_normalize_before_merge_so_checkout_taint_never_becomes_a_conflict() -> (
    None
):
    # Two facts for the same decl/type node -- e.g. two producers, or two
    # pre-migration nodes that coalesced onto one id -- reporting an
    # identical declaration under two different checkout roots must merge
    # without a spurious FactConflict, and the raw checkout-tainted spelling
    # must not survive anywhere in the persisted facts list either (Codex
    # review, fresh evidence).
    from abicheck.buildsource.graph_facts import GraphFact, GraphNode, _decl_node_id

    qn_template = "raii_guard<(lambda at {}/lib.hpp:4:37)>"
    old_qn = qn_template.format("/old/checkout")
    new_qn = qn_template.format("/new/checkout")

    node = GraphNode(
        id=_decl_node_id(old_qn),
        kind="source_decl",
        label=old_qn,
        facts=[
            GraphFact(
                producer="header_graph",
                confidence="high",
                attrs={"name": old_qn, "qualified_name": old_qn},
            ),
            GraphFact(
                producer="call_graph",
                confidence="high",
                attrs={"name": new_qn, "qualified_name": new_qn},
            ),
        ],
    )

    from abicheck.buildsource.graph_facts import ensure_facts_and_resolve

    ensure_facts_and_resolve(node)

    # No conflict: the two facts agree once directory taint is stripped.
    assert node.conflicts == []
    assert "/old/checkout" not in node.attrs["name"]
    assert "/new/checkout" not in node.attrs["name"]

    # The persisted facts list itself must not leak either checkout root.
    d = node.to_dict()
    blob = str(d["facts"])
    assert "/old/checkout" not in blob
    assert "/new/checkout" not in blob
