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
    # silently unreachable (Codex review, fresh evidence).
    from abicheck.buildsource.source_graph import SourceGraphSummary

    old_id = "type://lambda at /old/checkout/lib.hpp:4:37"
    g = SourceGraphSummary.from_dict(
        {
            "schema_version": 2,
            "nodes": [{"id": old_id, "kind": "record_type", "label": old_id}],
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
