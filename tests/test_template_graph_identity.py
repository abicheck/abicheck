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

"""Declaration-identity discriminator tests for
:mod:`abicheck.buildsource.template_graph` (G29 Phase 5 item 1).

Split out of ``test_template_graph.py`` once that file reached the
AI-readiness 2000-line hard cap (CLAUDE.md's "prefer extending a split-out
module" guidance) -- home for tests specifically about *how two distinct
function templates sharing a qualified name are told apart*, as opposed to
the parent file's broader AST-shape/parsing coverage.
"""

from __future__ import annotations

from abicheck.buildsource.source_graph import SourceGraphSummary
from abicheck.buildsource.template_graph import (
    EDGE_DECL_INSTANTIATES_TEMPLATE,
    NODE_TEMPLATE_DECL,
    augment_graph_with_templates,
    parse_clang_ast_templates,
)


def test_function_templates_differing_only_in_template_parameter_list_stay_distinct() -> (
    None
):
    """``template <class T> void f()`` and ``template <class T, class U>
    void f()`` both print the *identical* function-type spelling ``"void
    ()"`` -- the function parameter list is genuinely empty for both, and
    clang's printer never reflects the *template* parameter list in that
    spelling at all -- confirmed against a real compiled/dumped pair
    (Codex review, fresh evidence beyond the earlier overload fix). The
    pattern-type discriminator alone therefore still collapsed both onto
    one shared ``template_decl`` node despite their own instantiations
    correctly staying separate (distinct mangled names), falsely making
    both ``DECL_INSTANTIATES_TEMPLATE`` edges point at a declaration the
    other template didn't actually come from."""

    def f(mangled: str, param_decls: list[dict]) -> dict:
        return {
            "kind": "FunctionTemplateDecl",
            "name": "f",
            "inner": [
                *param_decls,
                {"kind": "FunctionDecl", "name": "f", "type": {"qualType": "void ()"}},
                {
                    "kind": "FunctionDecl",
                    "name": "f",
                    "mangledName": mangled,
                    "inner": [],
                },
            ],
        }

    one_param = f("_Z1fIiEvv", [{"kind": "TemplateTypeParmDecl", "name": "T"}])
    two_params = f(
        "_Z1fIidEvv",
        [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            {"kind": "TemplateTypeParmDecl", "name": "U"},
        ],
    )
    ast = {"kind": "TranslationUnitDecl", "inner": [one_param, two_params]}
    out = parse_clang_ast_templates(ast)
    function_insts = [i for i in out if i.kind == "function"]
    assert len(function_insts) == 2
    # Both instantiations correctly stay distinct...
    assert {i.emitted_symbols for i in function_insts} == {
        ("_Z1fIiEvv",),
        ("_Z1fIidEvv",),
    }

    graph = SourceGraphSummary()
    augment_graph_with_templates(graph, out)
    decl_nodes = {n.id for n in graph.nodes if n.kind == NODE_TEMPLATE_DECL}
    # ...and so must their own abstract declarations, one per template.
    assert len(decl_nodes) == 2
    instantiates_edges = {
        (e.src, e.dst) for e in graph.edges if e.kind == EDGE_DECL_INSTANTIATES_TEMPLATE
    }
    # Each instantiation's edge must land on a *different* declaration node.
    targets = {dst for _src, dst in instantiates_edges}
    assert len(targets) == 2
