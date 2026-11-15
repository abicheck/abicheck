"""An enum the new side adds is reported, as a record's addition is.

Oracle: the set of enum identities (qualified name when known, else the bare
name) in the new snapshot minus those in the old one, computed here from the
fixtures, never from the detector's matcher.
"""

from __future__ import annotations

import pytest

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot
from abicheck.model.entities import EnumMember, EnumType


def _enum(name: str, qualified: str | None = None) -> EnumType:
    return EnumType(
        name=name,
        members=[EnumMember("A", 0), EnumMember("B", 1)],
        qualified_name=qualified,
    )


def _snap(enums: list[EnumType]) -> AbiSnapshot:
    return AbiSnapshot(library="libe.so", version="1", enums=enums)


def _ident(e: EnumType) -> str:
    return e.qualified_name or e.name


@pytest.mark.parametrize(
    "old,new",
    [
        ([], [_enum("Color")]),
        ([_enum("Color")], [_enum("Color"), _enum("Mode")]),
        ([_enum("Color")], [_enum("Color")]),
        # a same-leaf enum in another namespace is a different enum
        (
            [_enum("Kind", "a::Kind")],
            [_enum("Kind", "a::Kind"), _enum("Kind", "b::Kind")],
        ),
        # renamed: one removal and one addition
        ([_enum("Old")], [_enum("New")]),
    ],
)
def test_added_enums_are_exactly_the_new_identities(
    old: list[EnumType], new: list[EnumType]
) -> None:
    want = sorted({_ident(e) for e in new} - {_ident(e) for e in old})
    changes = compare(_snap(old), _snap(new)).changes
    added = sorted(c.symbol for c in changes if c.kind is ChangeKind.TYPE_ADDED)
    assert len(added) == len(want)
    assert {a.rsplit("::", 1)[-1] for a in added} == {
        w.rsplit("::", 1)[-1] for w in want
    }
    removed = {c.symbol for c in changes if c.kind is ChangeKind.TYPE_REMOVED}
    assert len(removed) == len({_ident(e) for e in old} - {_ident(e) for e in new})
