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

"""Non-trivial-class detection over a struct/class DIE's children.

Pins each branch of ``dwarf_advanced._check_children_nontrivial`` and its
per-child helpers with fake DIEs, so the answer does not depend on which
real DWARF sample the integration lane happens to build.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import abicheck.dwarf_advanced as dwa


class Die:
    def __init__(
        self,
        tag: str,
        offset: int = 0,
        *,
        name: str = "",
        linkage: str = "",
        defaulted: int | None = None,
        artificial: int | None = None,
        children: tuple[Any, ...] = (),
    ) -> None:
        self.tag = tag
        self.offset = offset
        self.attributes: dict[str, Any] = {}
        if name:
            self.attributes["DW_AT_name"] = SimpleNamespace(value=name.encode())
        if linkage:
            self.attributes["DW_AT_linkage_name"] = SimpleNamespace(
                value=linkage.encode()
            )
        if defaulted is not None:
            self.attributes["DW_AT_defaulted"] = SimpleNamespace(value=defaulted)
        if artificial is not None:
            self.attributes["DW_AT_artificial"] = SimpleNamespace(value=artificial)
        self._children = list(children)

    def iter_children(self):
        return iter(self._children)


CU = object()


@pytest.fixture
def no_type_lookup(monkeypatch):
    """No member resolves to a type unless a test says so."""
    monkeypatch.setattr(dwa, "_resolve_type_die", lambda die, cu: None)


def _check(children, *, class_name: str = "C", cu=CU) -> bool:
    type_die = Die("DW_TAG_class_type", 0, children=tuple(children))
    return dwa._check_children_nontrivial(type_die, class_name, None, cu)


def test_a_base_class_is_conservatively_non_trivial(no_type_lookup) -> None:
    assert _check([Die("DW_TAG_inheritance", 1)]) is True


def test_a_member_of_a_non_trivial_aggregate_makes_the_class_non_trivial(
    monkeypatch,
) -> None:
    inner = Die("DW_TAG_structure_type", 9)
    monkeypatch.setattr(dwa, "_resolve_type_die", lambda die, cu: inner)
    monkeypatch.setattr(dwa, "_is_nontrivial_aggregate", lambda t, cache, CU: True)
    assert _check([Die("DW_TAG_member", 2)]) is True


def test_a_member_of_a_trivial_aggregate_is_not_non_trivial(monkeypatch) -> None:
    inner = Die("DW_TAG_structure_type", 9)
    monkeypatch.setattr(dwa, "_resolve_type_die", lambda die, cu: inner)
    monkeypatch.setattr(dwa, "_is_nontrivial_aggregate", lambda t, cache, CU: False)
    assert _check([Die("DW_TAG_member", 2)]) is False


def test_a_scalar_member_is_never_non_trivial(monkeypatch) -> None:
    scalar = Die("DW_TAG_base_type", 7)
    monkeypatch.setattr(dwa, "_resolve_type_die", lambda die, cu: scalar)
    called = []
    monkeypatch.setattr(
        dwa, "_is_nontrivial_aggregate", lambda *a, **k: called.append(a) or True
    )
    assert _check([Die("DW_TAG_member", 2)]) is False
    assert called == []


def test_a_member_with_no_resolvable_type_is_not_non_trivial(no_type_lookup) -> None:
    assert _check([Die("DW_TAG_member", 2)]) is False


def test_a_member_without_a_compile_unit_is_not_resolved(monkeypatch) -> None:
    def fail(*_a):
        raise AssertionError("no CU: the member type must not be resolved")

    monkeypatch.setattr(dwa, "_resolve_type_die", fail)
    assert _check([Die("DW_TAG_member", 2)], cu=None) is False


def test_a_user_destructor_by_name_is_non_trivial(no_type_lookup) -> None:
    assert _check([Die("DW_TAG_subprogram", 3, name="~C")]) is True


def test_a_user_destructor_by_mangled_name_is_non_trivial(no_type_lookup) -> None:
    dtor = Die("DW_TAG_subprogram", 3, linkage="_ZN1CD1Ev")
    assert _check([dtor]) is True


def test_a_defaulted_destructor_is_trivial(no_type_lookup) -> None:
    dtor = Die("DW_TAG_subprogram", 3, name="~C", defaulted=1)
    assert _check([dtor]) is False


def test_a_compiler_generated_destructor_is_trivial(no_type_lookup) -> None:
    dtor = Die("DW_TAG_subprogram", 3, name="~C", artificial=1)
    assert _check([dtor]) is False


def test_a_copy_or_move_constructor_is_non_trivial(monkeypatch, no_type_lookup) -> None:
    monkeypatch.setattr(dwa, "is_copy_or_move_ctor", lambda *a: True)
    assert _check([Die("DW_TAG_subprogram", 3, name="C")]) is True


def test_an_ordinary_method_is_trivial(monkeypatch, no_type_lookup) -> None:
    monkeypatch.setattr(dwa, "is_copy_or_move_ctor", lambda *a: False)
    assert _check([Die("DW_TAG_subprogram", 3, name="f")]) is False


def test_without_a_class_name_no_constructor_check_runs(
    monkeypatch, no_type_lookup
) -> None:
    def fail(*_a):
        raise AssertionError("no class name: the constructor check must not run")

    monkeypatch.setattr(dwa, "is_copy_or_move_ctor", fail)
    assert _check([Die("DW_TAG_subprogram", 3, name="C")], class_name="") is False


def test_other_child_kinds_are_ignored(no_type_lookup) -> None:
    assert (
        _check([Die("DW_TAG_typedef", 4), Die("DW_TAG_enumeration_type", 5)]) is False
    )


def test_the_first_non_trivial_child_decides_the_answer(monkeypatch) -> None:
    seen = []

    def resolve(die, cu):
        seen.append(die.offset)
        return None

    monkeypatch.setattr(dwa, "_resolve_type_die", resolve)
    children = [
        Die("DW_TAG_inheritance", 1),
        Die("DW_TAG_member", 2),
    ]
    assert _check(children) is True
    assert seen == [], "a base class decides the answer before any member is resolved"
