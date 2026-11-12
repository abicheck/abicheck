"""`TypeDatabase.parse_all` is a two-state lifecycle: unparsed -> parsed.

Bug class: a "done" flag set before the work it guards. `_parsed = True` ran
first, so an exception from any later step left a half-filled database that
every later call accepted as complete. The invariant checked here, for a
failure injected at *each* step (not one reported input): after a failed
call, a retry yields exactly what a fresh database yields -- the oracle is a
second, never-failed `TypeDatabase`, not the parser's own bookkeeping.
"""

from __future__ import annotations

import pytest

import abicheck.pdb_parser as pdb_parser
from abicheck.pdb_parser import (
    LF_FIELDLIST,
    LF_STRUCTURE,
    TypeDatabase,
    parse_tpi_stream,
)
from tests.test_pdb_parser import (
    _build_tpi_stream,
    _make_lf_fieldlist,
    _make_lf_member,
    _make_lf_structure,
)


class _Injected(Exception):
    """An error type no per-record handler catches."""


def _records() -> list[tuple[int, bytes]]:
    fields = _make_lf_fieldlist(
        [_make_lf_member(0, 0x74, 0, "x"), _make_lf_member(0, 0x74, 4, "y")]
    )
    struct = _make_lf_structure(
        count=2, prop=0, field_ti=0x1000, byte_size=8, name="Point"
    )
    # A third, truncated structure record: the parser records it in
    # ``parse_failures``, so diagnostics kept from a failed call would make a
    # retry differ from a fresh parse.
    return [(LF_FIELDLIST, fields), (LF_STRUCTURE, struct), (LF_STRUCTURE, b"\x02")]


def _db() -> TypeDatabase:
    return TypeDatabase(parse_tpi_stream(_build_tpi_stream(_records())))


def _observable(db: TypeDatabase) -> tuple[object, ...]:
    s = db.resolve_struct(0x1001)
    return (
        None if s is None else (s.name, s.byte_size, s.count),
        [m.name for m in db.get_fieldlist(0x1000)],
        list(db.parse_failures),
    )


def _fail_on_call(n: int, real):  # type: ignore[no-untyped-def]
    calls = {"n": 0}

    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        if calls["n"] == n:
            raise _Injected
        return real(*args, **kwargs)

    return wrapper


# Every step of parse_all that can raise: each record and the forward-ref
# link that follows the record loop (by then every table is filled).
_STEPS = [
    ("_parse_record", 1),
    ("_parse_record", 2),
    ("_parse_record", 3),
    ("_link_forward_refs", 1),
]

_UNPARSED = (None, [], [])


@pytest.mark.parametrize(("step", "nth"), _STEPS)
def test_a_failed_parse_is_not_remembered_as_parsed(
    monkeypatch: pytest.MonkeyPatch, step: str, nth: int
) -> None:
    expected = _db()
    expected.parse_all()

    db = _db()
    if step == "_link_forward_refs":
        monkeypatch.setattr(
            pdb_parser, step, _fail_on_call(nth, pdb_parser._link_forward_refs)
        )
    else:
        monkeypatch.setattr(
            TypeDatabase, step, _fail_on_call(nth, TypeDatabase._parse_record)
        )
    with pytest.raises(_Injected):
        db.parse_all()
    monkeypatch.undo()
    # Rolled back, not merely re-parsable: nothing from the failed call stays.
    assert _observable(db) == _UNPARSED

    db.parse_all()
    assert _observable(db) == _observable(expected)


def test_a_successful_parse_runs_once() -> None:
    db = _db()
    db.parse_all()
    first = _observable(db)
    db.parse_all()
    assert _observable(db) == first
