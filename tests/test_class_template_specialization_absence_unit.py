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

"""No-toolchain twin of the class-template cells of
``test_template_export_loss_declaration_presence.py``.

A class-template specialization the new header stops instantiating, on
constructed snapshots. The bug class: a record (and its members) that the new
castxml enumeration merely does not list, while its primary template is still
declared, was reported as removed. The oracles are written down per case: the
truth table of the three facts the decision may read, the export table each
case is built with, and the expected owner spelling computed from the pieces
the mangled name was assembled from (never from the parser under test).
"""

from __future__ import annotations

import itertools
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.compare.template_instance_absence import (
    TemplateInstanceAbsence,
    member_owner,
)
from abicheck.demangle import demangle
from abicheck.diff_symbols import _diff_functions, _diff_variables
from abicheck.diff_types import _diff_types
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.extract.headers.castxml.specialization import specialization_declared
from abicheck.model import AbiSnapshot, Fact, RecordType
from abicheck.model.availability import FactStatus
from abicheck.model.declarations import Function, Variable, Visibility
from abicheck.model.elf_facts import SymbolBinding, SymbolType

_needs_demangler = pytest.mark.skipif(
    demangle("_ZN2ns3BoxIdE3setEd") is None, reason="no C++ demangler available"
)

#: The specialization every diff-level case is about.
_SPEC = "Box<double>"
_QUALIFIED = "ns::Box<double>"
_SET = "_ZN2ns3BoxIdE3setEd"
_ASSIGN = "_ZN2ns3BoxIdEaSERKS1_"
_COUNT = "_ZN2ns3BoxIdE5countE"
_CTOR = "__abicheck_ctor__ns::Box<double>(const Box<double>&)"
_DTOR = "~ns::Box<double>"

_FACTS: dict[str, Fact[bool] | None] = {
    "instantiated": Fact.present(False),
    "declared_specialization": Fact.present(True),
    "not_collected": Fact.not_collected(),
    "unset": None,
}


# ── member_owner: the owner spelling, against an independent construction ──

#: (Itanium code, spelling) of a template argument.
_ARGS = (("i", "int"), ("d", "double"), ("c", "char"), ("l", "long"))
_SCOPES = (("2ns", "ns"), ("3lib6detail", "lib::detail"))
_CLASSES = ("Box", "Matrix")


def _source_name(identifier: str) -> str:
    return f"{len(identifier)}{identifier}"


@_needs_demangler
@pytest.mark.parametrize(
    ("scope", "cls", "arg"), list(itertools.product(_SCOPES, _CLASSES, _ARGS))
)
def test_member_owner_names_the_enclosing_specialization(
    scope: tuple[str, str], cls: str, arg: tuple[str, str]
) -> None:
    """Every member spelling of one specialization resolves to the same owner,
    and that owner is the spelling the symbol was built from."""
    (scope_code, scope_text), (arg_code, arg_text) = scope, arg
    owner = f"{scope_text}::{cls}<{arg_text}>"
    prefix = f"_ZN{scope_code}{_source_name(cls)}I{arg_code}E"
    members = (
        f"{prefix}3getEv",  # method
        f"{prefix}3setE{arg_code}",  # method taking the argument
        f"{prefix}5countE",  # static data member
        f"{prefix}aSERKS{'1' if scope_code == '2ns' else '2'}_",  # operator=
        f"__abicheck_ctor__{owner}()",  # synthetic constructor key
        f"~{owner}",  # synthetic destructor key
    )
    for symbol in members[:3] + members[4:]:
        assert member_owner(symbol) == owner, symbol
    if scope_code == "2ns":
        assert member_owner(members[3]) == owner


@pytest.mark.parametrize(
    "symbol",
    [
        "_ZN2ns5Plain3getEv",  # member of a non-template class
        "_ZN2ns5ftmplIdEEvPT_",  # free function template: no enclosing class
        "_ZN2ns3CSR9setArraysIdEEvPT_",  # member template of a plain class
        "_ZN2ns3barEi",  # plain function
        "__abicheck_ctor__ns::Plain()",  # constructor of a plain class
        "~ns::Plain",  # destructor of a plain class
        "bar",  # extern "C"
    ],
)
def test_member_owner_makes_no_claim_outside_a_specialization(symbol: str) -> None:
    assert member_owner(symbol) is None


# ── specialization_vanished: the full truth table ──


def _graph(*templates: str) -> SourceGraphSummary:
    graph = SourceGraphSummary()
    for name in templates:
        graph.add_node(GraphNode(id=f"type://{name}", kind="record_type"))
    return graph


def _record(fact: Fact[bool] | None, name: str = _SPEC) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64,
        qualified_name=f"ns::{name}",
        specialization_declared_fact=fact,
    )


def _snap(
    *,
    version: str,
    types: list[RecordType] = (),  # type: ignore[assignment]
    functions: list[Function] = (),  # type: ignore[assignment]
    variables: list[Variable] = (),  # type: ignore[assignment]
    graph: SourceGraphSummary | None = None,
    exports: tuple[tuple[str, SymbolType], ...] = (),
) -> AbiSnapshot:
    return AbiSnapshot(
        library="libx.so",
        version=version,
        types=list(types),
        functions=list(functions),
        variables=list(variables),
        elf=ElfMetadata(
            machine="x86-64",
            symbols=[
                ElfSymbol(name=n, binding=SymbolBinding.WEAK, sym_type=t)
                for n, t in exports
            ],
        ),
        from_headers=True,
        surface_graph=graph,
    )


@pytest.mark.parametrize(
    ("fact_name", "declared", "enumerated"),
    list(itertools.product(_FACTS, (True, False), (True, False))),
)
def test_specialization_vanished_truth_table(
    fact_name: str, declared: bool, enumerated: bool
) -> None:
    """Only a proven implicit instantiation, whose primary is still declared,
    and which the new side does not enumerate, is not a removal."""
    old_record = _record(_FACTS[fact_name])
    new = _snap(
        version="2",
        types=[_record(Fact.present(False))] if enumerated else [],
        graph=_graph("ns::Box") if declared else _graph(),
    )
    rule = TemplateInstanceAbsence(new, _snap(version="1", types=[old_record]))
    expected = fact_name == "instantiated" and declared and not enumerated
    assert rule.specialization_vanished(old_record) is expected


# ── the detectors, end to end on constructed snapshots ──


def _members(*, exported: bool) -> tuple[list[Function], list[Variable]]:
    visibility = Visibility.PUBLIC if exported else Visibility.HIDDEN
    binding = SymbolBinding.WEAK if exported else None
    functions = [
        Function(
            name="set",
            mangled=_SET,
            return_type="void",
            visibility=visibility,
            elf_binding=binding,
        ),
        Function(
            name="operator=",
            mangled=_ASSIGN,
            return_type="Box<double>&",
            visibility=Visibility.HIDDEN,
            is_compiler_generated=True,
        ),
        Function(
            name="Box",
            mangled=_CTOR,
            return_type="",
            visibility=Visibility.HIDDEN,
            is_compiler_generated=True,
        ),
        Function(
            name="~Box",
            mangled=_DTOR,
            return_type="",
            visibility=Visibility.HIDDEN,
            is_compiler_generated=True,
        ),
    ]
    variables = [
        Variable(
            name="count",
            mangled=_COUNT,
            type="int",
            visibility=visibility,
            elf_binding=binding,
        )
    ]
    return functions, variables


def _pair(
    *,
    fact: Fact[bool] | None,
    template_declared: bool,
    old_exported: bool,
    new_exports: tuple[str, ...],
) -> tuple[AbiSnapshot, AbiSnapshot]:
    functions, variables = _members(exported=old_exported)
    old_exports = (
        ((_SET, SymbolType.FUNC), (_COUNT, SymbolType.OBJECT)) if old_exported else ()
    )
    old = _snap(
        version="1",
        types=[_record(fact)],
        functions=functions,
        variables=variables,
        graph=_graph("ns::Box"),
        exports=old_exports,
    )
    new_kinds = {_SET: SymbolType.FUNC, _COUNT: SymbolType.OBJECT}
    new = _snap(
        version="2",
        graph=_graph("ns::Box") if template_declared else _graph(),
        exports=tuple((n, new_kinds[n]) for n in new_exports),
    )
    return old, new


def _findings(old: AbiSnapshot, new: AbiSnapshot) -> dict[str, set[str]]:
    by_symbol: dict[str, set[str]] = {}
    changes = [
        *_diff_types(old, new),
        *_diff_functions(old, new),
        *_diff_variables(old, new),
    ]
    for change in changes:
        by_symbol.setdefault(change.symbol, set()).add(change.kind.value)
    return by_symbol


_REMOVAL_KINDS = {
    "type_removed",
    "func_removed",
    "inline_function_removed",
    "var_removed",
}


@_needs_demangler
@pytest.mark.parametrize("old_exported", [True, False])
@pytest.mark.parametrize(
    "new_exports",
    [(), (_SET,), (_COUNT,), (_SET, _COUNT)],
    ids=lambda e: "+".join(e) or "none",
)
def test_vanished_specialization_is_judged_by_the_export_table(
    old_exported: bool, new_exports: tuple[str, ...]
) -> None:
    old, new = _pair(
        fact=Fact.present(False),
        template_declared=True,
        old_exported=old_exported,
        new_exports=new_exports,
    )
    found = _findings(old, new)
    for symbol in (_SPEC, _SET, _ASSIGN, _COUNT, _CTOR, _DTOR):
        assert not found.get(symbol, set()) & _REMOVAL_KINDS, (symbol, found)
    for symbol, kind in (
        (_SET, "func_visibility_changed"),
        (_COUNT, "var_visibility_changed"),
    ):
        lost = old_exported and symbol not in new_exports
        assert (kind in found.get(symbol, set())) is lost, (symbol, found)


@pytest.mark.parametrize(
    ("fact_name", "template_declared"),
    [
        ("declared_specialization", True),  # an explicit specialization was deleted
        ("not_collected", True),  # the old producer could not tell
        ("unset", True),  # another producer, or a pre-v61 snapshot
        ("instantiated", False),  # the class template itself was removed
    ],
)
def test_real_or_unproven_removals_keep_type_removed(
    fact_name: str, template_declared: bool
) -> None:
    old, new = _pair(
        fact=_FACTS[fact_name],
        template_declared=template_declared,
        old_exported=True,
        new_exports=(),
    )
    found = _findings(old, new)
    assert "type_removed" in found.get(_SPEC, set()), found
    assert "var_removed" in found.get(_COUNT, set()), found


def test_non_template_class_removed_keeps_type_removed() -> None:
    """Control: a plain class has no primary template to keep it declared."""
    plain = _record(None, name="Plain")
    old = _snap(version="1", types=[plain], graph=_graph("ns::Plain"))
    new = _snap(version="2", graph=_graph("ns::Plain"))
    assert "type_removed" in _findings(old, new).get("Plain", set())


# ── the castxml extractor's reading of the declaring line ──


@pytest.mark.parametrize(
    ("line", "name", "expected"),
    [
        ("template <typename T> struct Box {", "Box<double>", False),
        ("template <typename T> class MYLIB_API Box", "Box<double>", False),
        ("template <typename T> struct alignas(16) Box {", "Box<double>", False),
        ("template <typename T> struct Box : Base<Box<T>> {", "Box<double>", False),
        ("template <> struct Box<double> {", "Box<double>", True),
        ("template <typename T> struct Box<T*> {", "Box<int *>", True),
        ("struct Box<double> final : Base {", "Box<double>", True),
        ("template <> union Box <double> {", "Box<double>", True),
        ("DECLARE_BOX(double)", "Box<double>", "not_collected"),
        ("template <typename T> struct Box {", "Plain", None),
    ],
)
def test_specialization_declared_reads_the_class_head(
    tmp_path: Path, line: str, name: str, expected: object
) -> None:
    header = tmp_path / "m.h"
    header.write_text(f"#pragma once\n{line}\n", encoding="utf-8")
    ctx = SimpleNamespace(id_map={"f1": {"name": str(header)}}, source_lines_cache={})
    fact = specialization_declared(ctx, {"file": "f1", "line": "2"}, name)  # type: ignore[arg-type]
    if expected is None:
        assert fact is None
    elif expected == "not_collected":
        assert fact is not None and fact.status is FactStatus.NOT_COLLECTED
    else:
        assert fact is not None and fact.status is FactStatus.PRESENT
        assert fact.value is expected


def test_unreadable_header_is_not_collected(tmp_path: Path) -> None:
    ctx = SimpleNamespace(
        id_map={"f1": {"name": str(tmp_path / "missing.h")}}, source_lines_cache={}
    )
    fact = specialization_declared(ctx, {"file": "f1", "line": "1"}, "Box<int>")  # type: ignore[arg-type]
    assert fact is not None and fact.status is FactStatus.NOT_COLLECTED
