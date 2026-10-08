"""Source-contract changes are classified by direction, never as layout breaks.

Regressions for the GCC/Clang catalog audit (case186, case207, case30,
case95, case109). Each test states the invariant over a small exhaustive
domain against an independent oracle written from the language rules, not
from the detector's own helpers:

* a qualifier behind a parameter's pointer/reference: gained by the single
  pointee -> function-pointer consumers only (risk); lost, or gained deeper
  -> direct callers (API break); never FUNC_PARAMS_CHANGED;
* ``restrict`` added -> risk, removed -> compatible;
* a by-value field qualifier is reported by its dedicated kind and never as
  TYPE_FIELD_TYPE_CHANGED;
* a never-exported, compiler-generated special member vanishing is a source
  break, never FUNC_REMOVED;
* typedef/type removal alone is a source break;
* a parameter respelled from a typedef to the canonical type the unchanged
  Itanium name encodes is no parameter change.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, RISK_KINDS
from abicheck.extract.surface_fact_producers import header_ast_surface_facts
from abicheck.model import (
    AbiSnapshot,
    Function,
    Param,
    RecordType,
    TypeField,
    Visibility,
)


def _snap(**kwargs: object) -> AbiSnapshot:
    base: dict[str, object] = dict(library="lib.so", version="1", from_headers=True)
    base.update(kwargs)
    return AbiSnapshot(**base)  # type: ignore[arg-type]


def _fn(params: list[Param], *, name: str = "f", mangled: str = "f") -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type="void",
        params=params,
        visibility=Visibility.PUBLIC,
    )


def _kinds(result: object) -> set[ChangeKind]:
    return {c.kind for c in result.changes}  # type: ignore[attr-defined]


# --- pointee qualifiers ------------------------------------------------------

_BASE = "char"
# (qualifiers on the pointee, qualifiers on the first pointer) for a T ** ;
# and qualifiers on the pointee for a T *.
_Q = [(), ("const",), ("volatile",), ("const", "volatile")]


def _spell1(q: tuple[str, ...]) -> str:
    return " ".join([*q, _BASE]) + " *"


def _spell2(q0: tuple[str, ...], q1: tuple[str, ...]) -> str:
    return " ".join([*q0, _BASE]) + " *" + (" " + " ".join(q1) if q1 else "") + " *"


def _oracle1(old: tuple[str, ...], new: tuple[str, ...]) -> ChangeKind | None:
    if set(old) == set(new):
        return None
    if set(old) <= set(new):
        return ChangeKind.PARAM_POINTEE_QUALIFIER_ADDED  # T* -> cv T*
    return ChangeKind.PARAM_POINTEE_QUALIFIER_CHANGED


@pytest.mark.parametrize(("old", "new"), list(itertools.product(_Q, _Q)))
def test_single_level_pointee_qualifier(old: tuple, new: tuple) -> None:
    result = compare(
        _snap(functions=[_fn([Param(name="p", type=_spell1(old))])]),
        _snap(functions=[_fn([Param(name="p", type=_spell1(new))])]),
    )
    kinds = _kinds(result)
    assert ChangeKind.FUNC_PARAMS_CHANGED not in kinds
    want = _oracle1(old, new)
    got = kinds & {
        ChangeKind.PARAM_POINTEE_QUALIFIER_ADDED,
        ChangeKind.PARAM_POINTEE_QUALIFIER_CHANGED,
    }
    assert got == ({want} if want else set())


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ((_Q[0], _Q[0]), (_Q[1], _Q[0])),  # char ** -> const char **: not implicit
        ((_Q[0], _Q[0]), (_Q[0], _Q[1])),  # char ** -> char *const *
        ((_Q[1], _Q[1]), (_Q[0], _Q[1])),  # lost the innermost const
    ],
)
def test_deeper_pointee_qualifier_breaks_direct_callers(old: tuple, new: tuple) -> None:
    result = compare(
        _snap(functions=[_fn([Param(name="p", type=_spell2(*old))])]),
        _snap(functions=[_fn([Param(name="p", type=_spell2(*new))])]),
    )
    kinds = _kinds(result)
    assert ChangeKind.PARAM_POINTEE_QUALIFIER_CHANGED in kinds
    assert ChangeKind.PARAM_POINTEE_QUALIFIER_ADDED not in kinds
    assert ChangeKind.FUNC_PARAMS_CHANGED not in kinds


def test_reference_binding_widened_is_the_risk_direction() -> None:
    result = compare(
        _snap(functions=[_fn([Param(name="r", type="Widget &")])]),
        _snap(functions=[_fn([Param(name="r", type="const Widget &")])]),
    )
    assert ChangeKind.PARAM_POINTEE_QUALIFIER_ADDED in _kinds(result)


def test_pointee_qualifier_verdicts() -> None:
    assert ChangeKind.PARAM_POINTEE_QUALIFIER_ADDED in RISK_KINDS
    assert ChangeKind.PARAM_POINTEE_QUALIFIER_CHANGED in API_BREAK_KINDS
    assert ChangeKind.PARAM_RESTRICT_ADDED in RISK_KINDS
    assert ChangeKind.PARAM_RESTRICT_CHANGED not in RISK_KINDS | API_BREAK_KINDS


def test_header_less_side_reports_no_pointee_change() -> None:
    old = _snap(functions=[_fn([Param(name="p", type="char *")])], from_headers=False)
    new = _snap(functions=[_fn([Param(name="p", type="const char *")])])
    assert not _kinds(compare(old, new)) & {
        ChangeKind.PARAM_POINTEE_QUALIFIER_ADDED,
        ChangeKind.PARAM_POINTEE_QUALIFIER_CHANGED,
    }


# --- restrict ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new"), list(itertools.product([False, True], repeat=2))
)
def test_restrict_direction(old: bool, new: bool) -> None:
    result = compare(
        _snap(functions=[_fn([Param(name="p", type="float *", is_restrict=old)])]),
        _snap(functions=[_fn([Param(name="p", type="float *", is_restrict=new)])]),
    )
    kinds = _kinds(result)
    want = (
        set()
        if old == new
        else {
            ChangeKind.PARAM_RESTRICT_ADDED
            if new
            else ChangeKind.PARAM_RESTRICT_CHANGED
        }
    )
    assert (
        kinds & {ChangeKind.PARAM_RESTRICT_ADDED, ChangeKind.PARAM_RESTRICT_CHANGED}
        == want
    )
    assert ChangeKind.FUNC_PARAMS_CHANGED not in kinds


# --- by-value field qualifiers -----------------------------------------------


def _record(field_type: str) -> RecordType:
    # As both header backends report it: the spelling plus explicit flags.
    return RecordType(
        name="Cfg",
        kind="struct",
        size_bits=32,
        fields=[
            TypeField(
                name="rate",
                type=field_type,
                offset_bits=0,
                is_const="const" in field_type.split(),
                is_volatile="volatile" in field_type.split(),
            )
        ],
    )


@pytest.mark.parametrize(
    ("old", "new", "kind", "band"),
    [
        ("int", "const int", ChangeKind.FIELD_BECAME_CONST, API_BREAK_KINDS),
        ("const int", "int", ChangeKind.FIELD_LOST_CONST, None),
        ("int", "volatile int", ChangeKind.FIELD_BECAME_VOLATILE, RISK_KINDS),
        ("volatile int", "int", ChangeKind.FIELD_LOST_VOLATILE, RISK_KINDS),
    ],
)
def test_field_qualifier_is_not_a_field_type_change(
    old: str, new: str, kind: ChangeKind, band: frozenset | None
) -> None:
    result = compare(_snap(types=[_record(old)]), _snap(types=[_record(new)]))
    kinds = _kinds(result)
    assert kind in kinds
    assert ChangeKind.TYPE_FIELD_TYPE_CHANGED not in kinds
    assert not kinds & BREAKING_KINDS
    if band is not None:
        assert kind in band


# --- compiler-generated members ---------------------------------------------


@pytest.mark.parametrize(
    ("generated", "expected"),
    [
        # castxml `artificial="1"`: generated inline in every consumer that
        # uses it -- its disappearance is a source break.
        (True, ChangeKind.INLINE_FUNCTION_REMOVED),
        # Unknown provenance keeps the conservative binary removal.
        (None, ChangeKind.FUNC_REMOVED),
    ],
)
def test_vanished_special_member(generated: bool | None, expected: ChangeKind) -> None:
    # As CastXML reports an implicit constructor: a placeholder key no
    # export table can confirm or deny, judged public by its header.
    ctor = Function(
        name="Cfg",
        mangled="__abicheck_ctor__Cfg()",
        return_type="",
        params=[],
        visibility=Visibility.HIDDEN,
        is_compiler_generated=generated,
        **header_ast_surface_facts(
            exported=None, judged_public=True, producer="castxml"
        ),
    )
    kinds = _kinds(compare(_snap(functions=[ctor]), _snap(functions=[])))
    assert expected in kinds
    assert kinds & {ChangeKind.FUNC_REMOVED, ChangeKind.INLINE_FUNCTION_REMOVED} == {
        expected
    }


# --- typedef / type removal --------------------------------------------------


def test_removal_kinds_are_source_breaks() -> None:
    for kind in (
        ChangeKind.TYPEDEF_REMOVED,
        ChangeKind.TYPE_REMOVED,
        ChangeKind.TAG_TYPE_RENAMED,
    ):
        assert kind in API_BREAK_KINDS
        assert kind not in BREAKING_KINDS


# --- typedef respelled under an unchanged Itanium name ----------------------


@pytest.mark.parametrize(
    ("old", "new", "mangled", "changed"),
    [
        # Respelled: the mangled name encodes `unsigned long` on both sides.
        ("size_type", "long unsigned int", "_ZN2ns5alloc8allocateEm", False),
        ("long unsigned int", "size_type", "_ZN2ns5alloc8allocateEm", False),
        ("ns::alloc::size_type", "unsigned long", "_ZN2ns5alloc8allocateEm", False),
        # A real change the stale key cannot excuse: neither side is a
        # typedef-like name, or the encoded type matches neither side.
        ("int", "long long", "_Z1fi", True),
        ("size_type", "double", "_ZN2ns5alloc8allocateEm", True),
        # extern "C": no parameter encoding, nothing is excused.
        ("size_type", "long unsigned int", "allocate", True),
    ],
)
def test_typedef_respelling_under_unchanged_mangled_name(
    old: str, new: str, mangled: str, changed: bool
) -> None:
    result = compare(
        _snap(functions=[_fn([Param(name="n", type=old)], mangled=mangled)]),
        _snap(functions=[_fn([Param(name="n", type=new)], mangled=mangled)]),
    )
    assert (ChangeKind.FUNC_PARAMS_CHANGED in _kinds(result)) is changed


def test_pointee_const_added_overall_verdict_is_risk() -> None:
    result = compare(
        _snap(
            functions=[
                _fn(
                    [Param(name="data", type="char *")],
                    name="send_buffer",
                    mangled="send_buffer",
                )
            ]
        ),
        _snap(
            functions=[
                _fn(
                    [Param(name="data", type="const char *")],
                    name="send_buffer",
                    mangled="send_buffer",
                )
            ]
        ),
    )
    assert result.verdict == Verdict.COMPATIBLE_WITH_RISK
