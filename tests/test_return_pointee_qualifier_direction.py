"""A cv change behind a *returned* pointer/reference is classified by direction.

Return-type mirror of ``test_source_contract_direction.py``'s parameter
domain (known gap closed after the 2026-10 GCC/Clang catalog re-audit:
``char *get_name()`` -> ``const char *get_name()`` read ``NO_CHANGE``).
The oracle is written from the language rule, not from the detector: the
result flows *out* to the caller, so

* any qualifier gained on a pointee (at any level) breaks a caller binding
  the result to the old, less-qualified type -> API break;
* qualifiers only lost -> every direct call still converts implicitly; only
  a consumer holding the function in a pointer of the old type breaks -> risk;
* never ``FUNC_RETURN_CHANGED`` (the return register is unchanged).
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.checker_policy import API_BREAK_KINDS, RISK_KINDS
from abicheck.model import AbiSnapshot, Function, Visibility

_ADDED = ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_ADDED
_REMOVED = ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_REMOVED
_Q = [(), ("const",), ("volatile",), ("const", "volatile")]


def _snap(ret: str, *, from_headers: bool = True) -> AbiSnapshot:
    fn = Function(
        name="get_name",
        mangled="get_name",
        return_type=ret,
        params=[],
        visibility=Visibility.PUBLIC,
    )
    return AbiSnapshot(
        library="lib.so", version="1", from_headers=from_headers, functions=[fn]
    )


def _kinds(old: str, new: str, **kw: bool) -> set[ChangeKind]:
    return {c.kind for c in compare(_snap(old, **kw), _snap(new)).changes}


def _oracle(old: set[tuple[str, int]], new: set[tuple[str, int]]) -> ChangeKind | None:
    if old == new:
        return None
    return _ADDED if new - old else _REMOVED


def _spell2(q0: tuple[str, ...], q1: tuple[str, ...]) -> str:
    return " ".join([*q0, "char"]) + " *" + (" " + " ".join(q1) if q1 else "") + " *"


@pytest.mark.parametrize(("old", "new"), list(itertools.product(_Q, _Q)))
def test_single_level(old: tuple[str, ...], new: tuple[str, ...]) -> None:
    kinds = _kinds(" ".join([*old, "char"]) + " *", " ".join([*new, "char"]) + " *")
    assert ChangeKind.FUNC_RETURN_CHANGED not in kinds
    want = _oracle({(q, 0) for q in old}, {(q, 0) for q in new})
    assert kinds & {_ADDED, _REMOVED} == ({want} if want else set())


@pytest.mark.parametrize(
    ("old", "new"),
    list(itertools.product(itertools.product(_Q[:2], _Q[:2]), repeat=2)),
)
def test_two_levels(old: tuple, new: tuple) -> None:
    kinds = _kinds(_spell2(*old), _spell2(*new))
    assert ChangeKind.FUNC_RETURN_CHANGED not in kinds
    o = {(q, 0) for q in old[0]} | {(q, 1) for q in old[1]}
    n = {(q, 0) for q in new[0]} | {(q, 1) for q in new[1]}
    want = _oracle(o, n)
    assert kinds & {_ADDED, _REMOVED} == ({want} if want else set())


@pytest.mark.parametrize(
    ("old", "new", "want"),
    [
        ("Widget &", "const Widget &", _ADDED),
        ("const Widget &", "Widget &", _REMOVED),
        ("const char *", "char const *", None),  # respelling only
        ("char *", "char * const", None),  # top-level, not in the function type
    ],
)
def test_spellings(old: str, new: str, want: ChangeKind | None) -> None:
    assert _kinds(old, new) & {_ADDED, _REMOVED} == ({want} if want else set())


def test_verdicts() -> None:
    assert _ADDED in API_BREAK_KINDS
    assert _REMOVED in RISK_KINDS


def test_header_less_side_declines() -> None:
    assert not _kinds("char *", "const char *", from_headers=False) & {_ADDED, _REMOVED}
