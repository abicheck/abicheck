"""Overload and instantiation checks group functions by their real demangled stem.

Bug class: a detector keyed functions on ``qualified_declaration_name``, which
for a real Itanium symbol is the demangled *signature* -- the parameter list,
and for a function template a leaked return type too (``route(double)``,
``long getv<long>(long)``). Every overload got its own stem, so
``overload_set_rerouted`` never fired, and two instantiations of one template
never shared a stem, so ``instantiation_missing_from_binary`` never fired at
headers depth (a header backend names the function by its bare leaf). The
existing fixtures used invented manglings (``_Zold1``) that demangle to
nothing, which is why the class survived.

The domain below is exhaustive over six builtin types, with real Itanium
manglings built by the rules here (``_Z<len><name><params>`` for a free
function, ``_Z<len><name>I<T>ET_S0_`` for ``template <class T> T name(T)``),
not by abicheck. The oracle is the stated rule: a re-route is a name that lost
one overload and gained another while one side holds two or more; a missing
instantiation is one gone while another of the same template survives.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_templates import (
    detect_missing_instantiations,
    detect_overload_set_rerouted,
)
from abicheck.model import AbiSnapshot, Function, Param, Visibility

# Itanium builtin type codes and the C++ spelling a header backend records.
_BUILTINS = {
    "i": "int",
    "l": "long",
    "d": "double",
    "f": "float",
    "c": "char",
    "j": "unsigned int",
}


def _snap(funcs: list[Function]) -> AbiSnapshot:
    return AbiSnapshot(
        library="libt.so", version="0", functions=funcs, variables=[], types=[]
    )


def _overload(name: str, code: str) -> Function:
    # A header backend records the bare leaf; the mangled name is real.
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}{code}",
        return_type="int",
        params=[Param(name="a", type=_BUILTINS[code])],
        visibility=Visibility.PUBLIC,
    )


def _instantiation(name: str, code: str) -> Function:
    t = _BUILTINS[code]
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}I{code}ET_S0_",
        return_type=t,
        params=[Param(name="a", type=t)],
        visibility=Visibility.PUBLIC,
    )


def _rerouted(old: set[str], new: set[str]) -> bool:
    return bool(old - new) and bool(new - old) and (len(old) >= 2 or len(new) >= 2)


_CODES = sorted(_BUILTINS)
# Every old overload set of one or two members, against every new set of one
# or two members over the same six types.
_SETS = [frozenset(c) for n in (1, 2) for c in itertools.combinations(_CODES, n)]


@pytest.mark.parametrize(
    ("old", "new"),
    [(o, n) for o, n in itertools.product(_SETS, _SETS) if o != n],
    ids=lambda s: "+".join(sorted(s)),
)
def test_overload_set_rerouted_follows_the_rule_on_real_manglings(
    old: frozenset[str], new: frozenset[str]
) -> None:
    found = detect_overload_set_rerouted(
        _snap([_overload("route", c) for c in sorted(old)]),
        _snap([_overload("route", c) for c in sorted(new)]),
    )
    assert bool(found) == _rerouted(set(old), set(new))
    if found:
        assert [c.kind for c in found] == [ChangeKind.OVERLOAD_SET_REROUTED]
        assert found[0].symbol == "route"


def test_overloads_of_different_names_never_group() -> None:
    # Negative control: the stem is the name, so route/other never mix.
    old = _snap([_overload("route", "i"), _overload("other", "d")])
    new = _snap([_overload("route", "i"), _overload("other", "f")])
    assert detect_overload_set_rerouted(old, new) == []


@pytest.mark.parametrize(
    ("kept", "dropped"),
    [
        (kept, dropped)
        for n in (2, 3)
        for combo in itertools.combinations(_CODES, n)
        for dropped in itertools.combinations(combo, 1)
        for kept in [tuple(c for c in combo if c not in dropped)]
    ],
    ids=lambda t: "+".join(t),
)
def test_a_dropped_instantiation_is_missing_whatever_its_return_type(
    kept: tuple[str, ...], dropped: tuple[str, ...]
) -> None:
    old = _snap([_instantiation("getv", c) for c in (*kept, *dropped)])
    new = _snap([_instantiation("getv", c) for c in kept])
    found = detect_missing_instantiations(old, new)
    assert {c.symbol for c in found} == {
        _instantiation("getv", d).mangled for d in dropped
    }
    assert {c.kind for c in found} == {ChangeKind.INSTANTIATION_MISSING_FROM_BINARY}


def test_dropping_every_instantiation_is_not_a_missing_one() -> None:
    # Negative control: no instantiation of the template survives.
    old = _snap([_instantiation("getv", "i"), _instantiation("getv", "l")])
    assert detect_missing_instantiations(old, _snap([])) == []


def _member(spec: str, code: str) -> Function:
    # ``C<T>::f(P)`` with its real Itanium mangling: _ZN1CI<T>E1fE<P>.
    return Function(
        name=f"C<{_BUILTINS[spec]}>::f",
        mangled=f"_ZN1CI{spec}E1fE{code}",
        return_type="int",
        params=[Param(name="a", type=_BUILTINS[code])],
        visibility=Visibility.PUBLIC,
    )


@pytest.mark.known_gap_canary
def test_two_specializations_swapping_overloads_read_as_no_reroute() -> None:
    # Known gap: the overload-set key drops the enclosing class's template
    # arguments, so C<int>::f and C<long>::f share one set. Each specialization
    # loses one overload and gains another, yet the merged sets are equal.
    old = _snap(
        [_member("i", "i"), _member("i", "d"), _member("l", "i"), _member("l", "f")]
    )
    new = _snap(
        [_member("i", "i"), _member("i", "f"), _member("l", "i"), _member("l", "d")]
    )
    assert detect_overload_set_rerouted(old, new) == []
