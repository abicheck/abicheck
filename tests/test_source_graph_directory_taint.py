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
