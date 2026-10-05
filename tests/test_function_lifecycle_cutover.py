# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``compare/function_lifecycle.py``: ``FUNC_BECAME_INLINE``/
``FUNC_LOST_INLINE``, ``FUNC_DELETED``/``FUNC_DELETED_DWARF`` and the
converting-constructor key behind ``CTOR_OVERLOAD_AMBIGUITY_RISK``.

Every check is swept exhaustively over a small domain: each side's fact in
each ``FactStatus`` with each value, plus a missing entity, in both
directions. The oracle restates each detector's documented contract from
scratch -- its own "is the value usable" status set and its own
copy/move-constructor table -- rather than calling the module's helpers, so
a shared bug cannot make both sides agree.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.compare.function_lifecycle import (
    converting_ctor_signature,
    deletion_kind,
    inline_changes,
    is_deleted,
)
from abicheck.model.availability import FactStatus
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.fact import Fact
from abicheck.model.semantic_ir import CanonicalEntity

#: The oracle's own reading of "usable evidence" (``Fact``'s documented
#: PRESENT/PARTIAL contract), stated independently of ``Fact.is_present``.
_USABLE = {FactStatus.PRESENT, FactStatus.PARTIAL}
_STATUSES = list(FactStatus)


def _fact(status: FactStatus, value: object) -> Fact:
    if status is FactStatus.PRESENT:
        return Fact.present(value)
    if status is FactStatus.PARTIAL:
        return Fact.partial(value)
    return {
        FactStatus.NOT_COLLECTED: Fact.not_collected,
        FactStatus.UNSUPPORTED: Fact.unsupported,
        FactStatus.FAILED: Fact.failed,
        FactStatus.NOT_APPLICABLE: Fact.not_applicable,
    }[status]("test")


def _entity(**facts: Fact) -> CanonicalEntity:
    return CanonicalEntity(canonical_spelling=Fact.not_collected(), **facts)


def _known(status: FactStatus, value: object) -> object:
    return value if status in _USABLE else None


#: One side of a boolean fact: ``None`` (no entity) or (status, value).
_BOOL_SIDES: list[tuple[FactStatus, bool] | None] = [None] + [
    (s, v) for s in _STATUSES for v in (False, True)
]


def _bool_entity(name: str, side: tuple[FactStatus, bool] | None, **extra: Fact):
    if side is None:
        return None
    return _entity(**{name: _fact(*side)}, **extra)


def _side_value(side: tuple[FactStatus, bool] | None) -> object:
    return None if side is None else _known(*side)


# -- inline -----------------------------------------------------------------


def _inline_oracle(old, new) -> list[ChangeKind]:
    o, n = _side_value(old), _side_value(new)
    if o is False and n is True:
        return [ChangeKind.FUNC_BECAME_INLINE]
    if o is True and n is False:
        return [ChangeKind.FUNC_LOST_INLINE]
    return []


def test_inline_changes_match_the_oracle_exhaustively() -> None:
    bad = []
    for old, new, exported in itertools.product(
        _BOOL_SIDES, _BOOL_SIDES, (False, True)
    ):
        got = [
            c.kind
            for c in inline_changes(
                "_Z1fv",
                "f",
                _bool_entity("is_inline", old),
                _bool_entity("is_inline", new),
                entity_id=None,
                still_exported=exported,
            )
        ]
        if got != _inline_oracle(old, new):
            bad.append((old, new, exported, got))
    assert not bad, bad


def test_inline_oracle_is_not_vacuous() -> None:
    outcomes = {
        tuple(_inline_oracle(o, n))
        for o, n in itertools.product(_BOOL_SIDES, _BOOL_SIDES)
    }
    assert outcomes == {
        (),
        (ChangeKind.FUNC_BECAME_INLINE,),
        (ChangeKind.FUNC_LOST_INLINE,),
    }


@pytest.mark.parametrize(
    ("exported", "fragment"),
    [(True, "still exported"), (False, "may be removed")],
)
def test_became_inline_description_follows_export_answer(exported, fragment) -> None:
    (change,) = inline_changes(
        "_Z1fv",
        "f",
        _entity(is_inline=Fact.present(False)),
        _entity(is_inline=Fact.present(True)),
        entity_id=None,
        still_exported=exported,
    )
    assert fragment in change.description
    assert (change.old_value, change.new_value) == ("non-inline", "inline")


# -- deleted ------------------------------------------------------------------


def _deletion_oracle(old, new, dwarf) -> ChangeKind | None:
    if _side_value(old) is not False or _side_value(new) is not True:
        return None
    return (
        ChangeKind.FUNC_DELETED_DWARF if _side_value(dwarf) else ChangeKind.FUNC_DELETED
    )


def test_is_deleted_reads_only_usable_bools() -> None:
    for side in _BOOL_SIDES:
        assert is_deleted(_bool_entity("is_deleted", side)) is _side_value(side)


def test_deletion_kind_matches_the_oracle_exhaustively() -> None:
    bad = []
    for old, new, dwarf in itertools.product(_BOOL_SIDES, _BOOL_SIDES, _BOOL_SIDES):
        new_entity = (
            None
            if new is None
            else _entity(
                is_deleted=_fact(*new),
                **({} if dwarf is None else {"deleted_from_dwarf": _fact(*dwarf)}),
            )
        )
        got = deletion_kind(_bool_entity("is_deleted", old), new_entity)
        want = _deletion_oracle(old, new, dwarf if new is not None else None)
        if got is not want:
            bad.append((old, new, dwarf, got, want))
    assert not bad, bad


def test_deletion_is_directional() -> None:
    """Un-deleting (old deleted, new not) is never a deletion finding."""
    for s_old, s_new in itertools.product(_USABLE, _USABLE):
        assert (
            deletion_kind(
                _entity(is_deleted=_fact(s_old, True)),
                _entity(is_deleted=_fact(s_new, False)),
            )
            is None
        )


# -- converting constructor ---------------------------------------------------

#: (class name, first-parameter spelling) -> is it the class itself after
#: cv/reference stripping (i.e. a copy/move constructor). Hand-written.
_SELF_TABLE: dict[tuple[str, str], bool] = {
    ("C", "int"): False,
    ("C", "C"): True,
    ("C", "const C &"): True,
    ("C", "C &&"): True,
    ("C", "volatile C&"): True,
    ("C", "const D &"): False,
    ("myconst", "const myconst &"): True,
    ("myconst", "myconst&&"): True,
    # The class name merely *contains* the cv keyword: not stripped to "my".
    ("myconst", "const my &"): False,
}

_TRISTATE = [("known", False), ("known", True), ("unknown", None)]
_ACCESS = ["public", "protected", "private", None]
#: (parameter-type tail after the first, defaults) shapes.
_ARITY = [
    ((), (None,)),  # one required parameter
    ((), ("0",)),  # one defaulted parameter
    (("double",), (None, "1.0")),  # one required + one defaulted
    (("double",), (None, None)),  # two required: not single-argument
    (("double",), ("0", None)),  # one required, in second position
    (("double",), (None,)),  # defaults length disagrees with types
]


def _ctor_oracle(name, first, deleted, explicit, access, tail, defaults):
    if deleted != ("known", False) or explicit != ("known", False):
        return None
    if access != "public":
        return None
    types = (first, *tail)
    if defaults is None or len(defaults) != len(types):
        return None
    if [d is None for d in defaults].count(True) > 1:
        return None
    if _SELF_TABLE[(name, first)]:
        return None
    return types


def _ctor_entity(first, deleted, explicit, access, tail, defaults):
    def tri(v):
        return Fact.present(v[1]) if v[0] == "known" else Fact.not_collected()

    facts = {
        "is_deleted": tri(deleted),
        "is_explicit": tri(explicit),
        "access": Fact.present(access) if access is not None else Fact.not_collected(),
        "parameter_type_spellings": Fact.present((first, *tail)),
    }
    if defaults is not None:
        facts["parameter_defaults"] = Fact.present(defaults)
    return _entity(**facts)


def test_converting_ctor_signature_matches_the_oracle_exhaustively() -> None:
    bad = []
    defaults_choices = [*(d for _, d in _ARITY), None]
    for (name, first), deleted, explicit, access, (
        tail,
        _,
    ), defaults in itertools.product(
        _SELF_TABLE, _TRISTATE, _TRISTATE, _ACCESS, _ARITY, defaults_choices
    ):
        entity = _ctor_entity(first, deleted, explicit, access, tail, defaults)
        got = converting_ctor_signature(name, entity)
        want = _ctor_oracle(name, first, deleted, explicit, access, tail, defaults)
        if got != want:
            bad.append((name, first, deleted, explicit, access, tail, defaults, got))
    assert not bad, bad[:10]


def test_converting_ctor_oracle_is_not_vacuous() -> None:
    hits = sum(
        _ctor_oracle(n, f, ("known", False), ("known", False), "public", (), (None,))
        is not None
        for n, f in _SELF_TABLE
    )
    assert 0 < hits < len(_SELF_TABLE)


def test_converting_ctor_of_missing_entity_is_none() -> None:
    assert converting_ctor_signature("C", None) is None
