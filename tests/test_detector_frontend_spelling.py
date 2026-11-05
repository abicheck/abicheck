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

"""Internal-leak, type-diff and template detectors must not depend on spelling.

Each scenario is one logical old/new snapshot pair (every name fully
qualified, the usual unit-test convention) with the ``ChangeKind`` it must and
must not produce. It runs through the public ``compare()`` workflow three
times: as written, and rendered in the castxml and the clang backend's spelling
(``_frontend_spelling.render``, whose rules ``test_frontend_spelling_
integration.py`` pins to real dumps). The oracle is the scenario's own
expectation, not the detector: every spelling must contain the expected kinds,
must not contain the forbidden ones, and all three must reach the same kind set
and verdict. A detector that answers differently for the same declarations
spelled differently fails here, which is the class of bug that kept
``internal_type_leaks_via_public_api`` silent under clang until #1918.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pytest
from _frontend_spelling import FRONTENDS, Frontend, render

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Variable,
    Visibility,
)
from abicheck.model.vocabulary import ScopeOrigin

LEAK = ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API

SPELLINGS: tuple[Frontend | None, ...] = (None, *FRONTENDS)


def _ids(fe: Frontend | None) -> str:
    return "as-written" if fe is None else str(fe)


@dataclass(frozen=True)
class Scenario:
    name: str
    build: Callable[[int], AbiSnapshot]  # version 1 or 2 -> logical snapshot
    present: frozenset[ChangeKind]
    absent: frozenset[ChangeKind] = frozenset()
    # Kinds recorded but disposed as unreachable internal churn (ADR-067:
    # observed, kept in the out-of-surface ledger, never gating).
    demoted: frozenset[ChangeKind] = frozenset()
    canonical_fields: frozenset[tuple[str, str]] = field(default_factory=frozenset)


def _snap(v: int, functions: list[Function], types: list[RecordType]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libspell.so.1",
        version=f"{v}.0",
        functions=functions,
        types=types,
    )


def _fn(
    name: str, mangled: str, ret: str = "void", params: tuple[str, ...] = ()
) -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        params=[Param(name=f"a{i}", type=t) for i, t in enumerate(params)],
        visibility=Visibility.PUBLIC,
    )


def _grow(name: str, v: int, **kw: object) -> RecordType:
    """A record that gains a field (32 -> 64 bits) in version 2."""
    fields = [TypeField(name="a", type="int", offset_bits=0)]
    if v == 2:
        fields.append(TypeField(name="b", type="int", offset_bits=32))
    return RecordType(name=name, kind="struct", size_bits=32 * v, fields=fields, **kw)


def _holder(
    name: str, v: int, fields: list[tuple[str, str]], **kw: object
) -> RecordType:
    return RecordType(
        name=name,
        kind="struct",
        size_bits=64 + 32 * v,
        fields=[TypeField(name=n, type=t) for n, t in fields],
        **kw,
    )


_MAKE = ("lib::make", "_ZN3lib4makeEv")


def _make(ret: str) -> Function:
    return _fn(*_MAKE, ret=ret)


# --------------------------------------------------------------------------
# Internal-type leaks
# --------------------------------------------------------------------------


def _leak_via_base(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            _holder("lib::Pub", v, [("z", "int")], bases=["lib::detail::Base"]),
            _grow("lib::detail::Base", v),
        ],
    )


def _leak_via_virtual_base(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            _holder(
                "lib::Pub", v, [("z", "int")], virtual_bases=["lib::detail::VBase"]
            ),
            _grow("lib::detail::VBase", v),
        ],
    )


def _leak_via_field(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            _holder("lib::Pub", v, [("impl", "lib::detail::Impl")]),
            _grow("lib::detail::Impl", v),
        ],
    )


def _leak_via_alias_template_field(v: int) -> AbiSnapshot:
    # `pimpl<int> p;` with `template <class T> using pimpl = detail::Box<T>;`.
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            _holder("lib::Pub", v, [("p", "lib::detail::Box<int>")]),
            _grow("lib::detail::Box<int>", v),
        ],
    )


def _leak_via_nested_scope_field(v: int) -> AbiSnapshot:
    # A field written relative to an enclosing scope two levels up.
    return _snap(
        v,
        [_make("lib::api::Pub*")],
        [
            _holder("lib::api::Pub", v, [("impl", "lib::detail::Impl")]),
            _grow("lib::detail::Impl", v),
        ],
    )


def _leak_via_return(v: int) -> AbiSnapshot:
    return _snap(v, [_make("lib::detail::Impl")], [_grow("lib::detail::Impl", v)])


def _leak_via_variable(v: int) -> AbiSnapshot:
    # `namespace lib { extern detail::Impl g; }`: the only public reference is
    # a variable, written `detail::Impl` relative to `lib` by clang and named by
    # its leaf `g`; its scope comes from the mangling.
    return AbiSnapshot(
        library="libspell.so.1",
        version=f"{v}.0",
        types=[_grow("lib::detail::Impl", v)],
        variables=[
            Variable(
                name="lib::g",
                mangled="_ZN3lib1gE",
                type="lib::detail::Impl",
                visibility=Visibility.PUBLIC,
            )
        ],
    )


def _leak_via_operator_param(v: int) -> AbiSnapshot:
    # `struct Pub { static void* operator new(unsigned long, detail::Impl); }`
    # inside `lib`: the operator's scope is `lib::Pub` (the text before
    # `::operator`), from which clang's relative `detail::Impl` resolves.
    return _snap(
        v,
        [
            _fn(
                "lib::Pub::operator new",
                "_ZN3lib3PubnwEmNS_6detail4ImplE",
                ret="void*",
                params=("unsigned long", "lib::detail::Impl"),
            )
        ],
        [_holder("lib::Pub", v, [("z", "int")]), _grow("lib::detail::Impl", v)],
    )


def _leak_via_param(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [
            _fn(
                "lib::take",
                "_ZN3lib4takeENS_6detail4ImplE",
                params=("lib::detail::Impl",),
            )
        ],
        [_grow("lib::detail::Impl", v)],
    )


def _leak_via_template_arg(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [_make("lib::Wrap<lib::detail::Impl>")],
        [
            _holder("lib::Wrap<lib::detail::Impl>", v, [("t", "lib::detail::Impl")]),
            _grow("lib::detail::Impl", v),
        ],
    )


def _leak_unreachable(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [_make("lib::Pub*")],
        [_holder("lib::Pub", 1, [("z", "int")]), _grow("lib::detail::Impl", v)],
    )


def _pointer_only(v: int, *, vtable: bool) -> AbiSnapshot:
    impl = RecordType(
        name="lib::detail::Impl",
        kind="class",
        size_bits=32 * v,
        vtable=(["f()", "g()"][:v] if vtable else []),
        origin=ScopeOrigin.PRIVATE_HEADER,
    )
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            RecordType(
                name="lib::Pub",
                kind="struct",
                size_bits=64,
                fields=[TypeField(name="impl", type="lib::detail::Impl*")],
            ),
            impl,
        ],
    )


def _same_leaf_public_holder(v: int) -> AbiSnapshot:
    # A public record embeds the public `lib::Impl`; an unrelated private
    # `lib::detail::Impl` (same leaf) changes and is reachable from nothing.
    # castxml spells the field as the bare, ambiguous `Impl`.
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            _holder("lib::Pub", 1, [("impl", "lib::Impl")]),
            RecordType(name="lib::Impl", kind="struct", size_bits=32),
            _grow("lib::detail::Impl", v),
        ],
    )


def _same_leaf_internal_holder(v: int) -> AbiSnapshot:
    # The mirror image: the internal `lib::detail::Impl` is embedded and
    # changes, while an unrelated public `lib::Impl` exists alongside it.
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            _holder("lib::Pub", v, [("impl", "lib::detail::Impl")]),
            RecordType(name="lib::Impl", kind="struct", size_bits=32),
            _grow("lib::detail::Impl", v),
        ],
    )


def _evidence_gap_behind_pointer(v: int) -> AbiSnapshot:
    # An older dump without layout (v1) against a newer one with it (v2): the
    # only finding is LAYOUT_UNVERIFIABLE, an evidence gap, which must never
    # become a BREAKING leak (the container corpus's clang/1 baseline shape)
    # nor be filed as unreachable churn.
    laid = v == 2
    node = RecordType(
        name="lib::detail::Node",
        kind="struct",
        size_bits=128 if laid else None,
        data_size_bits=128 if laid else None,
        is_standard_layout=True,
        fields=[
            TypeField(
                name="next", type="lib::detail::Node*", offset_bits=0 if laid else None
            ),
            TypeField(
                name="label", type="const char*", offset_bits=64 if laid else None
            ),
        ],
    )
    first = _fn("lib::first_node", "_ZN3lib10first_nodeEv", ret="lib::detail::Node*")
    first.return_pointer_depth = 1
    return _snap(v, [first], [node])


def _evidence_gap_by_value(v: int) -> AbiSnapshot:
    # The same evidence gap on an internal type embedded *by value* in a
    # public holder whose own layout both sides record: reachable, so the gap
    # is neither a leak nor "unreachable internal churn" -- it stays in the
    # surface as the informational finding it is.
    laid = v == 2
    node = RecordType(
        name="lib::detail::Node",
        kind="struct",
        size_bits=64 if laid else None,
        data_size_bits=64 if laid else None,
        is_standard_layout=True,
        fields=[TypeField(name="n", type="long", offset_bits=0 if laid else None)],
    )
    holder = RecordType(
        name="lib::Pub",
        kind="struct",
        size_bits=64,
        fields=[TypeField(name="node", type="lib::detail::Node", offset_bits=0)],
    )
    return _snap(v, [_make("lib::Pub*")], [holder, node])


# --------------------------------------------------------------------------
# Type diffs
# --------------------------------------------------------------------------


def _field_type_changed(v: int) -> AbiSnapshot:
    target = "lib::detail::A" if v == 1 else "lib::detail::B"
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            RecordType(
                name="lib::Pub",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="x", type=target, offset_bits=0)],
            ),
            RecordType(name="lib::detail::A", kind="struct", size_bits=32),
            RecordType(name="lib::detail::B", kind="struct", size_bits=32),
        ],
    )


def _field_builtin_changed(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            RecordType(
                name="lib::Pub",
                kind="struct",
                size_bits=64,
                fields=[
                    TypeField(
                        name="x",
                        type="unsigned long" if v == 1 else "long long",
                        offset_bits=0,
                    )
                ],
            )
        ],
    )


def _unchanged(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [
            _make("lib::Pub*"),
            _fn(
                "lib::Pub::set",
                "_ZN3lib3Pub3setENS_6detail4ImplEm",
                params=("lib::detail::Impl", "unsigned long", "const char*"),
            ),
        ],
        [
            _holder(
                "lib::Pub",
                1,
                [("impl", "lib::detail::Impl"), ("w", "lib::Wrap<lib::detail::Impl>")],
                bases=["lib::detail::Base"],
            ),
            _grow("lib::detail::Base", 1),
            _grow("lib::detail::Impl", 1),
            _holder("lib::Wrap<lib::detail::Impl>", 1, [("t", "lib::detail::Impl")]),
        ],
    )


def _base_replaced(v: int) -> AbiSnapshot:
    base = "lib::detail::Base1" if v == 1 else "lib::detail::Base2"
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            RecordType(name="lib::Pub", kind="class", size_bits=64, bases=[base]),
            RecordType(name="lib::detail::Base1", kind="class", size_bits=32),
            RecordType(name="lib::detail::Base2", kind="class", size_bits=32),
        ],
    )


def _param_type_changed(v: int) -> AbiSnapshot:
    arg = "lib::detail::A" if v == 1 else "lib::detail::B"
    return _snap(
        v,
        [_fn("lib::take", "_ZN3lib4takeEv", params=(f"const {arg}&",))],
        [
            RecordType(name="lib::detail::A", kind="struct", size_bits=32),
            RecordType(name="lib::detail::B", kind="struct", size_bits=32),
        ],
    )


def _return_type_changed(v: int) -> AbiSnapshot:
    ret = "lib::detail::A*" if v == 1 else "lib::detail::B*"
    return _snap(
        v,
        [_make(ret)],
        [
            RecordType(name="lib::detail::A", kind="struct", size_bits=32),
            RecordType(name="lib::detail::B", kind="struct", size_bits=32),
        ],
    )


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------


def _instance_size_changed(v: int) -> AbiSnapshot:
    return _snap(
        v,
        [_make("lib::Wrap<lib::detail::Impl>*")],
        [_grow("lib::Wrap<lib::detail::Impl>", v), _grow("lib::detail::Impl", 1)],
    )


def _template_arg_changed(v: int) -> AbiSnapshot:
    arg = "lib::A" if v == 1 else "lib::B"
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            RecordType(
                name="lib::Pub",
                kind="struct",
                size_bits=32,
                fields=[TypeField(name="w", type=f"lib::Wrap<{arg}>", offset_bits=0)],
            ),
            RecordType(name=f"lib::Wrap<{arg}>", kind="struct", size_bits=32),
            RecordType(name="lib::A", kind="struct", size_bits=32),
            RecordType(name="lib::B", kind="struct", size_bits=32),
        ],
    )


def _nested_template_instance_grows(v: int) -> AbiSnapshot:
    inner = "lib::detail::Box<lib::detail::Box<int>>"
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            _holder("lib::Pub", v, [("bb", inner)]),
            _grow(inner, v),
        ],
    )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("leak_via_base", _leak_via_base, frozenset({LEAK})),
    Scenario("leak_via_virtual_base", _leak_via_virtual_base, frozenset({LEAK})),
    Scenario("leak_via_field", _leak_via_field, frozenset({LEAK})),
    Scenario(
        "leak_via_alias_template_field",
        _leak_via_alias_template_field,
        frozenset({LEAK}),
        canonical_fields=frozenset({("lib::Pub", "p")}),
    ),
    Scenario(
        "leak_via_nested_scope_field", _leak_via_nested_scope_field, frozenset({LEAK})
    ),
    Scenario("leak_via_return", _leak_via_return, frozenset({LEAK})),
    Scenario("leak_via_param", _leak_via_param, frozenset({LEAK})),
    Scenario("leak_via_variable", _leak_via_variable, frozenset({LEAK})),
    Scenario("leak_via_operator_param", _leak_via_operator_param, frozenset({LEAK})),
    Scenario("leak_via_template_arg", _leak_via_template_arg, frozenset({LEAK})),
    Scenario(
        "leak_unreachable_is_not_a_leak",
        _leak_unreachable,
        frozenset(),
        frozenset({LEAK, ChangeKind.TYPE_SIZE_CHANGED}),
        frozenset({ChangeKind.TYPE_SIZE_CHANGED}),
    ),
    Scenario(
        "pointer_only_layout_change_is_demoted",
        lambda v: _pointer_only(v, vtable=False),
        frozenset(),
        frozenset({LEAK, ChangeKind.TYPE_SIZE_CHANGED}),
        frozenset({ChangeKind.TYPE_SIZE_CHANGED}),
    ),
    Scenario(
        "pointer_only_vtable_change_still_leaks",
        lambda v: _pointer_only(v, vtable=True),
        frozenset({LEAK, ChangeKind.TYPE_VTABLE_CHANGED}),
    ),
    Scenario(
        "evidence_gap_is_not_a_leak",
        _evidence_gap_behind_pointer,
        # Reachable through first_node(): the gap stays in the surface. With
        # no layout on one side, invisibility behind the pointer is unproven,
        # so "private-internal-unreachable" would be a false disposition.
        frozenset({ChangeKind.LAYOUT_UNVERIFIABLE}),
        frozenset({LEAK}),
    ),
    Scenario(
        "evidence_gap_by_value_stays_in_surface",
        _evidence_gap_by_value,
        frozenset({ChangeKind.LAYOUT_UNVERIFIABLE}),
        frozenset({LEAK}),
    ),
    Scenario(
        "same_leaf_public_holder_is_not_a_leak",
        _same_leaf_public_holder,
        frozenset(),
        frozenset({LEAK, ChangeKind.TYPE_SIZE_CHANGED}),
        frozenset({ChangeKind.TYPE_SIZE_CHANGED}),
    ),
    Scenario(
        "same_leaf_internal_holder_leaks",
        _same_leaf_internal_holder,
        frozenset({LEAK}),
    ),
    Scenario(
        "field_type_changed",
        _field_type_changed,
        frozenset({ChangeKind.TYPE_FIELD_TYPE_CHANGED}),
    ),
    Scenario(
        "field_builtin_changed",
        _field_builtin_changed,
        frozenset({ChangeKind.TYPE_FIELD_TYPE_CHANGED}),
    ),
    Scenario(
        "unchanged_is_quiet",
        _unchanged,
        frozenset(),
        frozenset(
            {
                LEAK,
                ChangeKind.TYPE_FIELD_TYPE_CHANGED,
                ChangeKind.TYPE_BASE_CHANGED,
                ChangeKind.FUNC_PARAMS_CHANGED,
            }
        ),
    ),
    Scenario(
        "base_replaced", _base_replaced, frozenset({ChangeKind.TYPE_BASE_CHANGED})
    ),
    Scenario(
        "param_type_changed",
        _param_type_changed,
        frozenset({ChangeKind.FUNC_PARAMS_CHANGED}),
    ),
    Scenario(
        "return_type_changed",
        _return_type_changed,
        frozenset({ChangeKind.FUNC_RETURN_CHANGED}),
    ),
    Scenario(
        "template_instance_size_changed",
        _instance_size_changed,
        frozenset({ChangeKind.TYPE_SIZE_CHANGED}),
    ),
    Scenario(
        "template_arg_changed",
        _template_arg_changed,
        frozenset({ChangeKind.TYPE_FIELD_TYPE_CHANGED}),
    ),
    Scenario(
        "nested_template_instance_leaks",
        _nested_template_instance_grows,
        frozenset({LEAK, ChangeKind.TYPE_SIZE_CHANGED}),
    ),
)


def _run(sc: Scenario, fe: Frontend | None):
    # Function names written as their leaf, as both frontends write them; the
    # scenarios' manglings are real Itanium names, so the scope survives.
    kw = {"canonical_fields": sc.canonical_fields, "leaf_decl_names": True}
    old = render(sc.build(1), fe, **kw)
    new = render(sc.build(2), fe, **kw)
    return compare(old, new)


@pytest.mark.parametrize("fe", SPELLINGS, ids=_ids)
@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s.name)
def test_scenario_holds_in_every_spelling(sc: Scenario, fe: Frontend | None) -> None:
    result = _run(sc, fe)
    kinds = {c.kind for c in result.changes}
    demoted = {c.kind for c in result.out_of_surface_changes}
    assert sc.demoted <= demoted, (
        f"{sc.name} [{_ids(fe)}]: not recorded as out of surface "
        f"{sorted(k.value for k in sc.demoted - demoted)}"
    )
    assert sc.present <= kinds, (
        f"{sc.name} [{_ids(fe)}]: missing {sorted(k.value for k in sc.present - kinds)}; "
        f"got {sorted(k.value for k in kinds)}"
    )
    assert not (sc.absent & kinds), (
        f"{sc.name} [{_ids(fe)}]: unexpected {sorted(k.value for k in sc.absent & kinds)}"
    )


@pytest.mark.parametrize("fe", FRONTENDS, ids=str)
@pytest.mark.parametrize("sc", SCENARIOS, ids=lambda s: s.name)
def test_frontend_spelling_does_not_change_the_answer(
    sc: Scenario, fe: Frontend
) -> None:
    """Same declarations, different spelling: same kept and demoted kinds, same
    counts, same verdict."""
    base = _run(sc, None)
    spelled = _run(sc, fe)
    assert sorted(c.kind.value for c in spelled.changes) == sorted(
        c.kind.value for c in base.changes
    )
    assert sorted(c.kind.value for c in spelled.out_of_surface_changes) == sorted(
        c.kind.value for c in base.out_of_surface_changes
    )
    assert spelled.verdict == base.verdict


def _ambiguous_castxml_base(v: int) -> AbiSnapshot:
    # Two records share the leaf `Base`; castxml writes the internal base as
    # bare `Base` with no type identity, and lookup from `lib::Pub` finds
    # neither, so the leak walk leaves it unresolved (known gap).
    return _snap(
        v,
        [_make("lib::Pub*")],
        [
            _holder("lib::Pub", v, [("z", "int")], bases=["lib::detail::Base"]),
            _grow("lib::detail::Base", v),
            RecordType(name="other::Base", kind="struct", size_bits=32),
        ],
    )


@pytest.mark.known_gap_canary
def test_castxml_ambiguous_bare_base_leak_is_not_reported() -> None:
    old = render(_ambiguous_castxml_base(1), Frontend.CASTXML)
    new = render(_ambiguous_castxml_base(2), Frontend.CASTXML)
    kinds = {c.kind for c in compare(old, new).changes}
    assert LEAK not in kinds
    # The as-written spelling resolves the base and reports the leak.
    written = {
        c.kind
        for c in compare(_ambiguous_castxml_base(1), _ambiguous_castxml_base(2)).changes
    }
    assert LEAK in written
