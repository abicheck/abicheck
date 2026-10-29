"""The retype refinement (schema v69, compare/abi_equivalent_retype.py), unit level.

Each case builds two declarations whose slot spellings differ and carries the
fact each side has (or lacks), then checks the kind the refinement leaves. The
end-to-end behaviour is pinned by the differential matrix, not here.
"""

from __future__ import annotations

import pytest

from abicheck.compare.abi_equivalent_retype import refine_abi_equivalent_retypes
from abicheck.model import AbiSnapshot
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.change import Change
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.declarations import Function, Param, Variable
from abicheck.model.entities import RecordType, TypeField
from abicheck.model.fact import Fact

_LONG = AbiFundamental(FundamentalEncoding.SIGNED, 8, 8)
_LLONG = AbiFundamental(FundamentalEncoding.SIGNED, 8, 8)
_INT = AbiFundamental(FundamentalEncoding.SIGNED, 4, 4)
_UINT = AbiFundamental(FundamentalEncoding.UNSIGNED, 4, 4)


def _present(fact: AbiFundamental | None) -> Fact | None:
    return None if fact is None else Fact.present(fact)


def _param(spelling: str, fact: AbiFundamental | None, kind=None) -> Param:
    p = Param(name="p", type=spelling, type_abi_fact=_present(fact))
    if kind is not None:
        p.kind = kind
    return p


def _snap(*, functions=(), variables=(), types=()) -> AbiSnapshot:
    return AbiSnapshot(
        library="l",
        version="1",
        functions=list(functions),
        variables=list(variables),
        types=list(types),
    )


def _fn(name: str, *params: Param, ret: str = "void", ret_fact=None) -> Function:
    return Function(
        name=name,
        mangled=name,
        return_type=ret,
        params=list(params),
        return_type_abi_fact=_present(ret_fact),
    )


def _change(kind: ChangeKind, symbol: str, old: str, new: str, **kw) -> Change:
    return Change(
        kind=kind, symbol=symbol, description="x", old_value=old, new_value=new, **kw
    )


def _kinds(changes: list[Change]) -> list[ChangeKind]:
    return [c.kind for c in changes]


_RETYPED = ChangeKind.ABI_EQUIVALENT_TYPE_CHANGED


@pytest.mark.parametrize(
    ("old_fact", "new_fact", "expected"),
    [
        (_LONG, _LLONG, _RETYPED),  # long -> long long on LP64
        (_INT, _UINT, ChangeKind.FUNC_PARAMS_CHANGED),  # sign at 4 bytes: a break
        (None, _LLONG, ChangeKind.FUNC_PARAMS_CHANGED),  # no fact on the old side
        (_LONG, None, ChangeKind.FUNC_PARAMS_CHANGED),  # no fact on the new side
        (None, None, ChangeKind.FUNC_PARAMS_CHANGED),  # no fact on either side
    ],
)
def test_param_retype_is_refined_only_on_equal_facts(
    old_fact, new_fact, expected
) -> None:
    old = _snap(functions=[_fn("f", _param("long", old_fact))])
    new = _snap(functions=[_fn("f", _param("long long", new_fact))])
    change = _change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "(long)", "(long long)")
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert out.kind is expected


def test_refined_change_keeps_symbol_names_and_spellings() -> None:
    old = _snap(functions=[_fn("f", _param("char", _present_fact()))])
    new = _snap(functions=[_fn("f", _param("signed char", _present_fact()))])
    change = _change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "(char)", "(signed char)")
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert (out.kind, out.symbol, out.old_value, out.new_value) == (
        _RETYPED,
        "f",
        "(char)",
        "(signed char)",
    )
    assert "ABI-identical" in out.description


def _present_fact() -> AbiFundamental:
    return AbiFundamental(FundamentalEncoding.SIGNED, 1, 1)


def test_one_unproven_slot_keeps_the_whole_break() -> None:
    """A pointer beside an equal retype is still a break: every differing slot
    must be proven, not just most of them."""
    old = _snap(functions=[_fn("f", _param("long", _LONG), _param("int *", None))])
    new = _snap(
        functions=[_fn("f", _param("long long", _LLONG), _param("long *", None))]
    )
    change = _change(
        ChangeKind.FUNC_PARAMS_CHANGED, "f", "(long, int *)", "(long long, long *)"
    )
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert out.kind is ChangeKind.FUNC_PARAMS_CHANGED


def test_a_pointer_kind_change_is_never_refined() -> None:
    from abicheck.model.vocabulary import ParamKind

    old = _snap(functions=[_fn("f", _param("long", _LONG))])
    new = _snap(
        functions=[_fn("f", _param("long long", _LLONG, kind=ParamKind.POINTER))]
    )
    change = _change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "(long)", "(long long)")
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert out.kind is ChangeKind.FUNC_PARAMS_CHANGED


def test_return_retype_is_refined_on_equal_facts() -> None:
    old = _snap(functions=[_fn("f", ret="long", ret_fact=_LONG)])
    new = _snap(functions=[_fn("f", ret="long long", ret_fact=_LLONG)])
    change = _change(ChangeKind.FUNC_RETURN_CHANGED, "f", "long", "long long")
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert out.kind is _RETYPED


def test_return_retype_with_a_sign_change_at_four_bytes_stays_a_break() -> None:
    old = _snap(functions=[_fn("f", ret="int", ret_fact=_INT)])
    new = _snap(functions=[_fn("f", ret="unsigned int", ret_fact=_UINT)])
    change = _change(ChangeKind.FUNC_RETURN_CHANGED, "f", "int", "unsigned int")
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert out.kind is ChangeKind.FUNC_RETURN_CHANGED


def test_field_retype_is_refined_by_record_and_field_name() -> None:
    old_rec = RecordType(
        name="S",
        kind="struct",
        fields=[TypeField(name="m", type="enum E", type_abi_fact=_present(_UINT))],
    )
    new_rec = RecordType(
        name="S",
        kind="struct",
        fields=[
            TypeField(name="m", type="unsigned int", type_abi_fact=_present(_UINT))
        ],
    )
    change = _change(
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        "S",
        "enum E",
        "unsigned int",
        qualified_name="S",
        field_name="m",
    )
    (out,) = refine_abi_equivalent_retypes(
        [change], _snap(types=[old_rec]), _snap(types=[new_rec])
    )
    assert out.kind is _RETYPED


def test_variable_retype_is_refined_on_equal_facts() -> None:
    old = _snap(
        variables=[
            Variable(name="v", mangled="v", type="long", type_abi_fact=_present(_LONG))
        ]
    )
    new = _snap(
        variables=[
            Variable(
                name="v", mangled="v", type="long long", type_abi_fact=_present(_LLONG)
            )
        ]
    )
    change = _change(ChangeKind.VAR_TYPE_CHANGED, "v", "long", "long long")
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert out.kind is _RETYPED


def test_a_symbol_missing_on_one_side_is_never_refined() -> None:
    """A replaced symbol (its mangled name moved) is a removal and an addition."""
    old = _snap(functions=[_fn("f", _param("long", _LONG))])
    new = _snap(functions=[_fn("g", _param("long long", _LLONG))])
    change = _change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "(long)", "(long long)")
    (out,) = refine_abi_equivalent_retypes([change], old, new)
    assert out.kind is ChangeKind.FUNC_PARAMS_CHANGED


def test_unrelated_changes_pass_through_unchanged_and_in_order() -> None:
    old = _snap(functions=[_fn("f", _param("long", _LONG))])
    new = _snap(functions=[_fn("f", _param("long long", _LLONG))])
    removed = _change(ChangeKind.FUNC_REMOVED, "h", "h", "")
    retype = _change(ChangeKind.FUNC_PARAMS_CHANGED, "f", "(long)", "(long long)")
    out = refine_abi_equivalent_retypes([removed, retype], old, new)
    assert _kinds(out) == [ChangeKind.FUNC_REMOVED, _RETYPED]
