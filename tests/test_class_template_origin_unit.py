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

"""No-toolchain twin of ``test_class_template_specialization_origin_integration.py``.

Two halves of one decision: which pattern a class-template specialization was
instantiated from (``buildsource/template_class_origin.py``, over AST trees
shaped exactly like the ``clang -ast-dump=json`` output recorded in the
docstrings below), and whether the comparison may then treat the old
specialization as not removed (``compare/template_instance_absence.py``).

The oracles are written down per case, never derived from the code under test:
each AST case names the pattern clang instantiated it from, and the decision
table spells out every combination of the two producers.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from abicheck.buildsource.source_graph import (
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
)
from abicheck.buildsource.template_graph import (
    TemplateInstantiation,
    merge_template_instantiations,
    parse_clang_ast_templates,
)
from abicheck.buildsource.template_graph_fold import augment_graph_with_templates
from abicheck.compare.template_instance_absence import TemplateInstanceAbsence
from abicheck.extract.headers.castxml.specialization import specialization_declared
from abicheck.model import AbiSnapshot, Fact, RecordType
from abicheck.model.availability import FactStatus

# ── the AST shapes (recorded from clang 18 -ast-dump=json) ──


def _loc(offset: int) -> dict[str, Any]:
    return {"offset": offset, "col": 1, "tokLen": 3}


def _macro_loc(spelling: int, expansion: int) -> dict[str, Any]:
    return {
        "spellingLoc": {"offset": spelling, "col": 10, "tokLen": 3},
        "expansionLoc": {"offset": expansion, "col": 1, "tokLen": 8},
    }


def _arg(qual_type: str) -> dict[str, Any]:
    return {"kind": "TemplateArgument", "type": {"qualType": qual_type}}


def _instantiation(sid: str, loc: dict[str, Any], arg: str) -> dict[str, Any]:
    """An implicit instantiation: complete, nested under its template, located
    at the pattern it was instantiated from."""
    return {
        "id": sid,
        "kind": "ClassTemplateSpecializationDecl",
        "name": "Box",
        "loc": loc,
        "tagUsed": "struct",
        "completeDefinition": True,
        "inner": [_arg(arg)],
    }


def _stub(sid: str) -> dict[str, Any]:
    """An explicit specialization or instantiation, as nested: id only."""
    return {"id": sid, "kind": "ClassTemplateSpecializationDecl", "name": "Box"}


def _detached(sid: str, loc: dict[str, Any], arg: str) -> dict[str, Any]:
    """The stub's complete definition, a sibling of the template."""
    return {**_instantiation(sid, loc, arg)}


def _template(
    head: dict[str, Any], *specs: dict[str, Any], name: str = "Box"
) -> dict[str, Any]:
    return {
        "id": f"t-{name}-{head.get('offset', head.get('spellingLoc'))}",
        "kind": "ClassTemplateDecl",
        "name": name,
        "inner": [
            {"kind": "TemplateTypeParmDecl", "name": "T"},
            {
                "id": "pattern",
                "kind": "CXXRecordDecl",
                "name": name,
                "loc": head,
                "tagUsed": "struct",
                "completeDefinition": True,
                "inner": [{"kind": "CXXRecordDecl", "isImplicit": True}],
            },
            *specs,
        ],
    }


def _partial(loc: dict[str, Any], pattern: str, name: str = "Box") -> dict[str, Any]:
    return {
        "id": f"p-{pattern}",
        "kind": "ClassTemplatePartialSpecializationDecl",
        "name": name,
        "loc": loc,
        "tagUsed": "struct",
        "completeDefinition": True,
        "inner": [_arg(pattern)],
    }


def _tu(*decls: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "TranslationUnitDecl",
        "inner": [{"kind": "NamespaceDecl", "name": "ns", "inner": list(decls)}],
    }


def _origins(ast: dict[str, Any]) -> dict[str, tuple[str, str]]:
    return {
        t.label: (t.instantiated_from, t.template_signature)
        for t in parse_clang_ast_templates(ast)
        if t.kind == "record"
    }


#: name -> (AST, expected {label: (instantiated_from, template_signature)}).
_AST_CASES: dict[str, tuple[dict[str, Any], dict[str, tuple[str, str]]]] = {
    "primary": (
        _tu(_template(_loc(40), _instantiation("s1", _loc(40), "double"))),
        {"ns::Box<double>": ("primary", "")},
    ),
    "primary_from_a_macro": (
        _tu(
            _template(
                _macro_loc(181, 172), _instantiation("s1", _macro_loc(181, 172), "int")
            )
        ),
        {"ns::Box<int>": ("primary", "")},
    ),
    "primary_defined_after_a_forward_declaration": (
        # Instantiations nest under the first declaration, at the definition.
        _tu(
            _template(_loc(44), _instantiation("s1", _loc(76), "long")),
            _template(_loc(76), _stub("s1")),
        ),
        {"ns::Box<long>": ("primary", "")},
    ),
    "partial": (
        _tu(
            _template(_loc(40), _instantiation("s1", _loc(90), "int *")),
            _partial(_loc(90), "type-parameter-0-0 *"),
        ),
        {"ns::Box<int *>": ("partial", "type-parameter-0-0 *")},
    ),
    "partial_from_a_macro": (
        _tu(
            _template(_loc(40), _instantiation("s1", _macro_loc(300, 250), "int *")),
            _partial(_macro_loc(300, 250), "type-parameter-0-0 *"),
        ),
        {"ns::Box<int *>": ("partial", "type-parameter-0-0 *")},
    ),
    "explicit_specialization": (
        _tu(_template(_loc(40), _stub("s1")), _detached("s1", _loc(120), "double")),
        {"ns::Box<double>": ("", "")},
    ),
    "explicit_specialization_from_the_primary_macro_line": (
        # Same expansion offset as the primary, different spelling offset.
        _tu(
            _template(_macro_loc(181, 172), _stub("s1")),
            _detached("s1", _macro_loc(230, 172), "double"),
        ),
        {"ns::Box<double>": ("", "")},
    ),
    "primary_and_partial_share_a_location": (
        # Different files, same offset: clang does not say which.
        _tu(
            _template(_loc(40), _instantiation("s1", _loc(40), "int *")),
            _partial(_loc(40), "type-parameter-0-0 *"),
        ),
        # No origin; the graph's own (file, offset) join keeps its signature.
        {"ns::Box<int *>": ("", "type-parameter-0-0 *")},
    ),
    "partial_of_another_template_at_the_same_offset": (
        _tu(
            _template(_loc(40), _instantiation("s1", _loc(90), "int *")),
            _partial(_loc(90), "type-parameter-0-0 *", name="Other"),
        ),
        # The (file, offset) join is not per template; the origin claims nothing.
        {"ns::Box<int *>": ("", "type-parameter-0-0 *")},
    ),
    "incomplete_specialization": (
        _tu(
            _template(
                _loc(40),
                {**_instantiation("s1", _loc(40), "int"), "completeDefinition": False},
            )
        ),
        {},
    ),
}


@pytest.mark.parametrize("case", list(_AST_CASES))
def test_instantiation_origin_follows_the_ast_structure(case: str) -> None:
    ast, expected = _AST_CASES[case]
    assert _origins(ast) == expected


def test_every_declared_partial_specialization_is_listed() -> None:
    """Instantiated or not, each partial specialization is a pattern record."""
    ast = _tu(
        _template(_loc(40)),
        _partial(_loc(90), "type-parameter-0-0 *"),
        _partial(_loc(150), "const type-parameter-0-0 *"),
    )
    patterns = {
        (t.template_qname, t.template_signature)
        for t in parse_clang_ast_templates(ast)
        if t.kind == "partial_pattern"
    }
    assert patterns == {
        ("ns::Box", "type-parameter-0-0 *"),
        ("ns::Box", "const type-parameter-0-0 *"),
    }


@pytest.mark.parametrize(
    ("first", "second", "merged"),
    [
        ("primary", "primary", "primary"),
        ("primary", "", ""),
        ("", "partial", ""),
        ("primary", "partial", ""),
    ],
)
def test_translation_units_must_agree_on_the_origin(
    first: str, second: str, merged: str
) -> None:
    def inst(origin: str) -> TemplateInstantiation:
        return TemplateInstantiation(
            "record", "ns::Box", "ns::Box<int>", instantiated_from=origin
        )

    (result,) = merge_template_instantiations([[inst(first)], [inst(second)]])
    assert result.instantiated_from == merged


def test_fold_records_the_origin_and_every_partial_pattern() -> None:
    graph = SourceGraphSummary()
    augment_graph_with_templates(
        graph,
        [
            TemplateInstantiation(
                "record",
                "ns::Box",
                "ns::Box<int *>",
                template_signature="type-parameter-0-0 *",
                instantiated_from="partial",
            ),
            TemplateInstantiation(
                "partial_pattern",
                "ns::Box",
                "ns::Box<const type-parameter-0-0 *>",
                template_signature="const type-parameter-0-0 *",
            ),
        ],
    )
    nodes = {n.id: n for n in graph.nodes}
    assert nodes["template_instantiation://ns::Box<int *>"].attrs == {
        "instantiated_from": "partial"
    }
    assert "template_decl://ns::Box#const type-parameter-0-0 *" in nodes
    assert {(e.src, e.dst) for e in graph.edges} == {
        (
            "template_instantiation://ns::Box<int *>",
            "template_decl://ns::Box#type-parameter-0-0 *",
        )
    }


# ── the comparison: both producers, every combination ──

_PARTIAL_DECL = "template_decl://ns::Box#type-parameter-0-0 *"

#: castxml's line reading of the old record.
_HEADS: dict[str, Fact[bool] | None] = {
    "own_head": Fact.present(True),
    "primary_head": Fact.present(False),
    "not_collected": Fact.not_collected(),
    "unset": None,
}

#: (castxml reading, graph origin) -> the pattern that must still be declared
#: for the record not to be removed, or None when nothing is proven.
_DECISION: dict[tuple[str, str], str | None] = {
    ("own_head", "primary"): None,  # contradiction
    ("own_head", "partial"): "partial",
    ("own_head", "none"): None,
    ("primary_head", "primary"): "primary",
    ("primary_head", "partial"): None,  # contradiction
    ("primary_head", "none"): "primary",
    ("not_collected", "primary"): "primary",
    ("not_collected", "partial"): "partial",
    ("not_collected", "none"): None,
    ("unset", "primary"): "primary",
    ("unset", "partial"): "partial",
    ("unset", "none"): None,
}

_SPEC = "Box<int *>"


def _old_graph(origin: str) -> SourceGraphSummary:
    graph = SourceGraphSummary()
    graph.add_node(GraphNode(id="type://ns::Box", kind="record_type"))
    if origin == "none":
        return graph
    inst = f"template_instantiation://ns::{_SPEC}"
    graph.add_node(
        GraphNode(
            id=inst,
            kind="template_instantiation",
            label=f"ns::{_SPEC}",
            attrs={"instantiated_from": origin},
        )
    )
    dst = _PARTIAL_DECL if origin == "partial" else "template_decl://ns::Box"
    graph.add_node(GraphNode(id=dst, kind="template_decl", label="ns::Box"))
    graph.add_edge(GraphEdge(src=inst, dst=dst, kind="DECL_INSTANTIATES_TEMPLATE"))
    return graph


def _new_graph(*, primary: bool, partial: bool) -> SourceGraphSummary:
    graph = SourceGraphSummary()
    if primary:
        graph.add_node(GraphNode(id="type://ns::Box", kind="record_type"))
    if partial:
        graph.add_node(GraphNode(id=_PARTIAL_DECL, kind="template_decl"))
    return graph


def _snap(
    version: str, types: list[RecordType], graph: SourceGraphSummary
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version=version,
        types=types,
        from_headers=True,
        surface_graph=graph,
    )


def _record(head: Fact[bool] | None) -> RecordType:
    return RecordType(
        name=_SPEC,
        kind="struct",
        size_bits=64,
        qualified_name=f"ns::{_SPEC}",
        specialization_declared_fact=head,
    )


@pytest.mark.parametrize(
    ("head", "origin", "primary", "partial", "enumerated"),
    list(
        itertools.product(
            _HEADS,
            ("primary", "partial", "none"),
            (True, False),
            (True, False),
            (True, False),
        )
    ),
)
def test_specialization_vanished_decision_table(
    head: str, origin: str, primary: bool, partial: bool, enumerated: bool
) -> None:
    """Not removed exactly when one producer proves the pattern, none
    contradicts it, the new graph still declares that pattern, and the new
    side does not enumerate the specialization."""
    record = _record(_HEADS[head])
    old = _snap("1", [record], _old_graph(origin))
    new = _snap(
        "2",
        [_record(Fact.present(False))] if enumerated else [],
        _new_graph(primary=primary, partial=partial),
    )
    pattern = _DECISION[(head, origin)]
    still_declared = {"primary": primary, "partial": partial, None: False}[pattern]
    expected = still_declared and not enumerated
    assert TemplateInstanceAbsence(new, old).specialization_vanished(record) is expected


def test_a_spelling_two_graph_nodes_disagree_on_proves_nothing() -> None:
    graph = _old_graph("primary")
    graph.add_node(
        GraphNode(
            id=f"template_instantiation://ns::{_SPEC.replace(' ', '')}",
            kind="template_instantiation",
            label=f"ns::{_SPEC.replace(' ', '')}",
            attrs={"instantiated_from": "partial"},
        )
    )
    graph.add_edge(
        GraphEdge(
            src=f"template_instantiation://ns::{_SPEC.replace(' ', '')}",
            dst=_PARTIAL_DECL,
            kind="DECL_INSTANTIATES_TEMPLATE",
        )
    )
    record = _record(None)
    old = _snap("1", [record], graph)
    new = _snap("2", [], _new_graph(primary=True, partial=True))
    assert TemplateInstanceAbsence(new, old).specialization_vanished(record) is False


# ── castxml: a line holding both class-head shapes ──


@pytest.mark.parametrize(
    "line",
    [
        "template <typename T> struct Box { T v; }; template <> struct Box<double> { double v; };",
        "template <> struct Box<double> { double v; }; template <typename T> struct Box;",
    ],
)
def test_a_line_with_both_class_heads_is_not_collected(
    tmp_path: Path, line: str
) -> None:
    header = tmp_path / "m.h"
    header.write_text(f"#pragma once\n{line}\n", encoding="utf-8")
    ctx = SimpleNamespace(id_map={"f1": {"name": str(header)}}, source_lines_cache={})
    fact = specialization_declared(ctx, {"file": "f1", "line": "2"}, "Box<double>")  # type: ignore[arg-type]
    assert fact is not None and fact.status is FactStatus.NOT_COLLECTED
