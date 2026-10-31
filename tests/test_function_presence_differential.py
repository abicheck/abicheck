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

"""Differential: ``function_presence`` (``types`` blocked) against ``functions``.

``function_presence`` stands in for ``functions`` only while that detector is
not comparable, and claims to classify presence exactly as it does. The oracle
is the ``functions`` detector run on the same pair with nothing blocked; the
two finding sets, filtered to the kinds ``function_presence`` owns, must be
equal for every enumerated old/new transition and either blocked side.
"""

from __future__ import annotations

import dataclasses
import itertools

import pytest

from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.buildsource.template_graph_fold import NODE_SOURCE_DECL
from abicheck.compare.declined_comparisons import declined_scope
from abicheck.compare.function_presence import _diff_function_presence
from abicheck.diff_symbols import _diff_functions
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
from abicheck.model import AbiSnapshot
from abicheck.model.declarations import Function
from abicheck.model.elf_facts import SymbolBinding, SymbolType
from abicheck.model.fact import Fact

_OWNED = {
    "func_removed",
    "func_removed_elf_only",
    "func_visibility_changed",
    "inline_function_removed",
}
_TEMPLATE = "_ZN2ns5ftmplIdEEvPT_"  # ns::ftmpl<double>(double*)


def _fact(value: bool | None) -> Fact[bool]:
    if value is None:
        return Fact.not_collected(producer="test")
    return Fact.present(value, producer="test")


def _fn(mangled: str, contract: bool | None, exported: bool) -> Function:
    f = Function(name=mangled, mangled=mangled, return_type="void")
    f.in_public_contract_fact = _fact(contract)
    f.binary_exported_fact = _fact(exported)
    return f


def _graph() -> SourceGraphSummary:
    graph = SourceGraphSummary()
    graph.add_node(
        GraphNode(id=f"decl://ns::ftmpl#sha256:{0:064x}", kind=NODE_SOURCE_DECL)
    )
    return graph


def _snap(version: str, functions: list[Function], graph: bool) -> AbiSnapshot:
    exported = [f.mangled for f in functions if f.binary_exported_fact.value is True]
    return AbiSnapshot(
        library="libx.so",
        version=version,
        functions=functions,
        elf=ElfMetadata(
            machine="x86-64",
            symbols=[
                ElfSymbol(
                    name=n, binding=SymbolBinding.GLOBAL, sym_type=SymbolType.FUNC
                )
                for n in exported
            ],
        ),
        from_headers=True,
        surface_graph=_graph() if graph else None,
    )


def _new_side(name: str, shape: str) -> list[Function]:
    """The new side of one old exported, promised function *name*."""
    return {
        "unchanged": [_fn(name, True, True)],
        "removed": [],
        "export_lost": [_fn(name, True, False)],
        "hidden": [_fn(name, False, False)],
        "contract_unestablished": [_fn(name, None, True)],
    }[shape]


_SHAPES = ("unchanged", "removed", "export_lost", "hidden", "contract_unestablished")


def _owned(changes: list) -> set[tuple[str, str]]:
    return {(c.kind.value, c.symbol) for c in changes if c.kind.value in _OWNED}


def _pairs() -> list[tuple[str, AbiSnapshot, AbiSnapshot]]:
    pairs = []
    # Two independent functions, every combination of their transitions.
    for a, b in itertools.product(_SHAPES, repeat=2):
        old = _snap("1", [_fn("f", True, True), _fn("g", True, True)], graph=False)
        new = _snap("2", _new_side("f", a) + _new_side("g", b), graph=False)
        pairs.append((f"f:{a}/g:{b}", old, new))
    # A promised-but-unexported inline whose new capture had no header set.
    pairs.append(
        (
            "inline_contract_unestablished",
            _snap("1", [_fn("i", True, False)], graph=False),
            _snap("2", [_fn("i", None, False)], graph=False),
        )
    )
    # A template instance the new header no longer enumerates.
    for still_exported in (True, False):
        old = _snap("1", [_fn(_TEMPLATE, True, True)], graph=True)
        new = _snap("2", [], graph=True)
        if still_exported:
            new.elf.symbols.append(
                ElfSymbol(
                    name=_TEMPLATE, binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC
                )
            )
        pairs.append((f"template_omitted_exported={still_exported}", old, new))
    return pairs


@pytest.mark.parametrize("blocked_side", ["old", "new"])
@pytest.mark.parametrize(
    ("label", "old", "new"), _pairs(), ids=lambda v: v if isinstance(v, str) else ""
)
def test_presence_matches_functions_detector(
    label: str, old: AbiSnapshot, new: AbiSnapshot, blocked_side: str
) -> None:
    expected = _owned(_diff_functions(old, new))
    block = {"unreadable_sections": (("types", "failed"),)}
    if blocked_side == "old":
        got = _diff_function_presence(dataclasses.replace(old, **block), new)
    else:
        got = _diff_function_presence(old, dataclasses.replace(new, **block))
    assert _owned(got) == expected, label


def test_oracle_is_not_vacuous() -> None:
    """The enumeration reaches every owned outcome the oracle can give here."""
    kinds = {
        k for _, old, new in _pairs() for k, _s in _owned(_diff_functions(old, new))
    }
    assert kinds >= {"func_removed", "func_visibility_changed"}


@pytest.mark.parametrize("still_exported", [True, False])
def test_template_instance_with_unreadable_graph_is_never_removed_on_omission(
    still_exported: bool,
) -> None:
    """With the header graph unreadable the template rule cannot decide: an
    instance the new binary still exports is declined, not ``func_removed``;
    one the export table proves gone is still reported."""
    old = _snap("1", [_fn(_TEMPLATE, True, True)], graph=False)
    new = _snap("2", [], graph=False)
    if still_exported:
        new.elf.symbols.append(
            ElfSymbol(
                name=_TEMPLATE, binding=SymbolBinding.WEAK, sym_type=SymbolType.FUNC
            )
        )
    old = dataclasses.replace(old, unreadable_sections=(("graph", "failed"),))
    with declined_scope() as declined:
        got = _owned(_diff_function_presence(old, new))
    if still_exported:
        assert got == set()
        assert [d.entity for d in declined] == [_TEMPLATE]
    else:
        assert got == {("func_removed", _TEMPLATE)}
        assert declined == []
