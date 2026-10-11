"""A cv change behind a *returned* pointer/reference is classified by direction.

Return-type mirror of ``test_source_contract_direction.py``'s parameter
domain (known gap closed after the 2026-10 GCC/Clang catalog re-audit:
``char *get_name()`` -> ``const char *get_name()`` read ``NO_CHANGE``).
The oracle is written from the language rule, not from the detector: the
result flows *out* to the caller, who binds it to the old type, so

* the new result converts to the old type by a qualification conversion
  (C++ [conv.qual], walked here outermost level first, the way the standard
  states it) -> only a function-pointer consumer of the old type breaks ->
  ``_REMOVED`` (risk);
* it does not convert -> a direct caller breaks -> API break: ``_ADDED`` when
  a qualifier was gained anywhere, else ``_CHANGED`` (2026-10-11 re-audit:
  ``const char **`` -> ``char **`` was wrongly a risk, because the previous
  oracle here assumed every loss converts);
* never ``FUNC_RETURN_CHANGED`` (the return register is unchanged).

``test_return_pointee_qualifier_compiler_integration.py`` checks the same
matrix against g++ itself.
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


_CHANGED = ChangeKind.FUNC_RETURN_POINTEE_QUALIFIER_CHANGED


def _converts(src: list[set[str]], dst: list[set[str]]) -> bool:
    """[conv.qual] for two cv lists ordered outermost pointee first."""
    const_above = True
    for cv_s, cv_d in zip(src, dst, strict=True):
        if not cv_s <= cv_d:
            return False
        if cv_s != cv_d and not const_above:
            return False
        const_above = const_above and "const" in cv_d
    return True


def _oracle(old: list[set[str]], new: list[set[str]]) -> ChangeKind | None:
    """*old*/*new*: cv per level, outermost pointee first."""
    if old == new:
        return None
    if _converts(new, old):
        return _REMOVED
    gained = any(n - o for o, n in zip(old, new, strict=True))
    return _ADDED if gained else _CHANGED


def _spell2(q0: tuple[str, ...], q1: tuple[str, ...]) -> str:
    return " ".join([*q0, "char"]) + " *" + (" " + " ".join(q1) if q1 else "") + " *"


@pytest.mark.parametrize(("old", "new"), list(itertools.product(_Q, _Q)))
def test_single_level(old: tuple[str, ...], new: tuple[str, ...]) -> None:
    kinds = _kinds(" ".join([*old, "char"]) + " *", " ".join([*new, "char"]) + " *")
    assert ChangeKind.FUNC_RETURN_CHANGED not in kinds
    want = _oracle([set(old)], [set(new)])
    assert kinds & {_ADDED, _REMOVED, _CHANGED} == ({want} if want else set())


@pytest.mark.parametrize(
    ("old", "new"),
    list(itertools.product(itertools.product(_Q[:2], _Q[:2]), repeat=2)),
)
def test_two_levels(old: tuple, new: tuple) -> None:
    kinds = _kinds(_spell2(*old), _spell2(*new))
    assert ChangeKind.FUNC_RETURN_CHANGED not in kinds
    # _spell2(q0, q1) is ``q0 char * q1 *``: q1 is the outer pointee.
    want = _oracle([set(old[1]), set(old[0])], [set(new[1]), set(new[0])])
    assert kinds & {_ADDED, _REMOVED, _CHANGED} == ({want} if want else set())


def _spell3(q: tuple[tuple[str, ...], ...]) -> str:
    """``q[2] char * q[1] * q[0] *`` (q outermost pointee first)."""
    return " ".join([*q[2], "char", "*", *q[1], "*", *q[0], "*"])


_C2 = [(), ("const",)]


@pytest.mark.parametrize(
    ("old", "new"),
    list(itertools.product(itertools.product(_C2, repeat=3), repeat=2)),
)
def test_three_levels(old: tuple, new: tuple) -> None:
    kinds = _kinds(_spell3(old), _spell3(new))
    assert ChangeKind.FUNC_RETURN_CHANGED not in kinds
    want = _oracle([set(q) for q in old], [set(q) for q in new])
    assert kinds & {_ADDED, _REMOVED, _CHANGED} == ({want} if want else set())


@pytest.mark.parametrize(
    ("old", "new", "want"),
    [
        # The re-audit's case: ``const char **p = get_name();`` stops compiling.
        ("const char **", "char **", _CHANGED),
        ("const char * const *", "char * const *", _REMOVED),
        ("const char * const *", "char **", _REMOVED),
        ("volatile char **", "char **", _CHANGED),
        ("const volatile char * const *", "volatile char * const *", _REMOVED),
        ("const char * volatile *", "char * volatile *", _CHANGED),
        ("const char * const &", "char * const &", _REMOVED),
        ("const char * &", "char * &", _CHANGED),
        ("Widget &", "const Widget &", _ADDED),
        ("const Widget &", "Widget &", _REMOVED),
        ("const char *", "char const *", None),  # respelling only
        ("char *", "char * const", None),  # top-level, not in the function type
    ],
)
def test_spellings(old: str, new: str, want: ChangeKind | None) -> None:
    got = _kinds(old, new) & {_ADDED, _REMOVED, _CHANGED}
    assert got == ({want} if want else set())


def test_verdicts() -> None:
    assert _ADDED in API_BREAK_KINDS
    assert _CHANGED in API_BREAK_KINDS
    assert _REMOVED in RISK_KINDS


def test_header_less_side_declines() -> None:
    got = _kinds("char *", "const char *", from_headers=False)
    assert not got & {_ADDED, _REMOVED, _CHANGED}
