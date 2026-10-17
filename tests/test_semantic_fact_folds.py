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

"""``semantic_fact_folds`` on a committed real clang AST, with no compiler.

The capture (``tests/fixtures/semantic_facts``) holds one of each family. The
real-compiler parity suite is ``tests/test_header_graph_semantic_facts.py``;
this one runs everywhere, including the coverage lane, and checks the same
invariants against oracles that are not the owner itself:

* the owner's parse equals each family's own parser plus its own merge;
* the streamed reader equals the whole-tree projection of the materialized
  tree (what the attach's whole-tree path reads);
* the default L2 header route and the L5 route (its per-TU dump replaced by
  the same capture) fold identical family edges.
"""

from __future__ import annotations

import copy
import json
from concurrent.futures import Future
from pathlib import Path

import pytest

from abicheck.buildsource import l5_ast_pass
from abicheck.buildsource.callback_graph import (
    merge_callback_edges,
    parse_clang_ast_callbacks,
)
from abicheck.buildsource.header_graph import build_header_only_graph
from abicheck.buildsource.header_graph_ast_projection import (
    HeaderGraphAstProjection,
    merge_header_graph_ast_projections,
    project_header_graph_ast,
)
from abicheck.buildsource.header_graph_ast_stream import project_header_graph_ast_file
from abicheck.buildsource.header_graph_projection_cache import (
    decode_projection,
    encode_projection,
)
from abicheck.buildsource.override_graph import (
    merge_override_facts,
    parse_clang_ast_override_facts,
    parse_clang_ast_virtual_destructor_owners,
)
from abicheck.buildsource.semantic_fact_folds import (
    HEADER_SEMANTIC_PASSES,
    SemanticFacts,
    merge_semantic_facts,
    parse_semantic_facts,
)
from abicheck.buildsource.template_graph import (
    merge_template_instantiations,
    parse_clang_ast_templates,
)
from abicheck.buildsource.type_graph_index import ast_derived_scope
from abicheck.extract.headers.clang.locations import materialize_locations
from abicheck.model import AbiSnapshot
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.model.source_graph_coverage import SEMANTIC_FAMILIES

FIXTURE = Path(__file__).parent / "fixtures" / "semantic_facts" / "ast_dump.json"
FAMILY_KINDS = frozenset().union(*(f.edge_kinds for f in SEMANTIC_FAMILIES))


def _tree() -> dict:
    return materialize_locations(json.loads(FIXTURE.read_text(encoding="utf-8")))


def _family_edges(graph: SourceGraphSummary) -> frozenset[tuple[str, str, str]]:
    return frozenset(
        (e.kind, e.src, e.dst) for e in graph.edges if e.kind in FAMILY_KINDS
    )


def test_the_owner_parse_equals_each_family_parser_and_its_own_merge() -> None:
    tree = _tree()
    with ast_derived_scope():
        facts = parse_semantic_facts(tree)
        edges, methods, owners = merge_override_facts(
            [parse_clang_ast_override_facts(tree)]
        )
    assert facts.overrides == (edges, frozenset(methods), frozenset(owners))
    assert facts.templates == merge_template_instantiations(
        [parse_clang_ast_templates(tree)]
    )
    assert facts.callbacks == merge_callback_edges([parse_clang_ast_callbacks(tree)])
    # Not vacuous: the capture holds every family, both template kinds, and
    # a class declaring its own virtual destructor.
    assert facts.overrides[0] and facts.callbacks
    assert {t.kind for t in facts.templates} == {"record", "function"}
    assert any(t.template_signature for t in facts.templates if t.kind == "record")
    assert "sf::Base" in parse_clang_ast_virtual_destructor_owners(tree)


def test_merging_is_idempotent_and_order_preserving() -> None:
    with ast_derived_scope():
        facts = parse_semantic_facts(_tree())
    assert merge_semantic_facts([facts, facts]) == facts
    assert merge_semantic_facts([SemanticFacts(), facts]) == facts


def test_the_streamed_reader_equals_the_materialized_whole_tree() -> None:
    whole = project_header_graph_ast(_tree())
    streamed = project_header_graph_ast_file(FIXTURE)
    assert streamed == whole
    assert streamed.semantic_facts is not None and streamed.semantic_facts.templates


def test_projection_merge_keeps_facts_only_when_every_part_has_them() -> None:
    whole = project_header_graph_ast(_tree())
    merged = merge_header_graph_ast_projections([whole, whole])
    assert merged.semantic_facts == whole.semantic_facts
    partial = merge_header_graph_ast_projections([whole, HeaderGraphAstProjection()])
    assert partial.semantic_facts is None


def test_the_projection_cache_round_trips_the_facts() -> None:
    whole = project_header_graph_ast(_tree())
    assert decode_projection(encode_projection(whole)) == whole
    doc = json.loads(encode_projection(whole))
    doc["template_fields"] = list(reversed(doc["template_fields"]))
    assert decode_projection(json.dumps(doc)) is None
    doc = json.loads(encode_projection(whole))
    doc["semantic_facts"]["templates"] = [["not", "a", "row"]]
    assert decode_projection(json.dumps(doc)) is None


def test_a_hand_built_projection_leaves_the_families_unknown() -> None:
    graph = build_header_only_graph(
        AbiSnapshot(library="l", version="1"), ast_projection=HeaderGraphAstProjection()
    )
    assert not any(p in graph.extractor_passes for p in HEADER_SEMANTIC_PASSES)


def _header_route() -> SourceGraphSummary:
    return build_header_only_graph(
        AbiSnapshot(library="l", version="1"),
        ast_projection=project_header_graph_ast(_tree()),
    )


def _l5_route(monkeypatch: pytest.MonkeyPatch) -> SourceGraphSummary:
    from abicheck.buildsource import inline_graph_fold
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    # Each TU's dump is replaced by the capture, as `clang_ast_run` hands it
    # over: materialized.
    monkeypatch.setattr(l5_ast_pass, "run_clang_ast_dump", lambda *_a, **_k: _tree())
    monkeypatch.setattr(l5_ast_pass, "_clang_available", lambda _b: True)
    monkeypatch.setattr(inline_graph_fold, "fold_include_graph", lambda *_a, **_k: None)
    graph = SourceGraphSummary()
    unit = CompileUnit(id="cu://tu.cpp", source="tu.cpp", language="CXX")
    l5_ast_pass.fold_semantic_graphs(
        graph, BuildEvidence(compile_units=[unit]), "clang++", []
    )
    return graph


def test_the_header_and_l5_routes_fold_the_same_family_edges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    header = _header_route()
    l5 = _l5_route(monkeypatch)
    assert {k for k, _s, _d in _family_edges(l5)} >= {
        "METHOD_POSSIBLE_OVERRIDE",
        "VIRTUAL_CALL_MAY_DISPATCH_TO",
        "DECL_INSTANTIATES_TEMPLATE",
        "TEMPLATE_USES_TYPE",
        "DECL_REGISTERS_CALLBACK",
        "DECL_TAKES_ADDRESS_OF",
    }
    assert _family_edges(header) == _family_edges(l5)
    for pass_name in HEADER_SEMANTIC_PASSES:
        assert header.extractor_passes.get(pass_name) is True
    for family in SEMANTIC_FAMILIES:
        assert family.build_pass in l5.extractor_passes, family.name


def test_mutant_header_route_without_the_folds_is_caught(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-fix shape: the header route keeps type/call edges only."""
    from abicheck.buildsource import header_graph_ast_projection

    l5 = _l5_route(monkeypatch)
    monkeypatch.setattr(
        header_graph_ast_projection, "fold_header_semantic_facts", lambda *_a: None
    )
    header = _header_route()
    assert _family_edges(l5) and not _family_edges(header)


def test_a_partial_parse_degrades_the_semantic_passes(tmp_path: Path) -> None:
    from abicheck.service_header_graph_attach import (
        HeaderGraphAst,
        _attach_header_graph,
    )

    header = tmp_path / "api.h"
    header.write_text("int f(void);\n", encoding="utf-8")
    acquired: Future[HeaderGraphAst] = Future()
    acquired.set_result(
        HeaderGraphAst(
            project_header_graph_ast(_tree()),
            "clang AST parse failed for 1 of 2 header(s)",
            [header],
            [],
            (),
        )
    )
    snap = _attach_header_graph(
        AbiSnapshot(library="l", version="1"),
        header_graph=True,
        header_graph_includes=False,
        headers=[header],
        includes=[],
        lang=None,
        compile=None,
        public_headers=None,
        public_header_dirs=None,
        prefetched=acquired,
    )
    graph = snap.surface_graph
    assert graph is not None
    for pass_name in HEADER_SEMANTIC_PASSES:
        assert graph.degraded_passes.get(pass_name) is True, pass_name


def test_a_graph_diff_with_a_gap_still_reports_a_change() -> None:
    from abicheck.buildsource.source_graph_compare import diff_source_graph

    fresh = _header_route()
    stored = copy.deepcopy(fresh)
    stored.extractor_passes = {
        k: v
        for k, v in stored.extractor_passes.items()
        if k not in HEADER_SEMANTIC_PASSES
    }
    diff = diff_source_graph(stored, fresh)
    assert not diff.changed
    assert {f.name for f in SEMANTIC_FAMILIES} == set(diff.coverage_gaps)
    assert diff_source_graph(SourceGraphSummary(), fresh).changed
