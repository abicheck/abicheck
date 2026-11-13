"""Base-class access narrowing: BASE_CLASS_ACCESS_NARROWED (header evidence).

Covers the comparison primitive ``diff_bases`` (narrowing reports, widening
and unknown evidence do not), the clang JSON parser's access handling, and
loading a snapshot that predates the ``base_access_fact`` field.
"""

from __future__ import annotations

import pytest

from abicheck.compare.base_class_diff import diff_bases
from abicheck.extract.headers.clang.records import _parse_bases
from abicheck.model import Fact, RecordType
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.fact import FactStatus
from abicheck.storage.fact_codec import decode_record_facts


def _rec(
    access: dict[str, str] | None,
    *,
    bases: list[str] | None = None,
    virtual_bases: list[str] | None = None,
) -> RecordType:
    """A ``RecordType`` whose base-access fact is present, or not collected."""
    base_names = ["LB3"] if bases is None else bases
    access_fact = Fact.not_collected() if access is None else Fact.present(dict(access))
    return RecordType(
        name="LD3",
        kind="struct",
        bases=base_names,
        virtual_bases=[] if virtual_bases is None else virtual_bases,
        base_access_fact=access_fact,
    )


def _access_kinds(old: RecordType, new: RecordType) -> list[ChangeKind]:
    return [
        c.kind
        for c in diff_bases("LD3", old, new)
        if c.kind == ChangeKind.BASE_CLASS_ACCESS_NARROWED
    ]


@pytest.mark.parametrize(
    ("old_acc", "new_acc"),
    [
        ("public", "protected"),
        ("public", "private"),
        ("protected", "private"),
    ],
)
def test_narrowing_reports_base_access_narrowed(old_acc: str, new_acc: str) -> None:
    changes = diff_bases(
        "LD3",
        _rec({"LB3": old_acc}),
        _rec({"LB3": new_acc}),
    )
    assert [c.kind for c in changes] == [ChangeKind.BASE_CLASS_ACCESS_NARROWED]
    change = changes[0]
    assert change.old_value == old_acc
    assert change.new_value == new_acc
    assert "LB3" in change.description


@pytest.mark.parametrize(
    ("old_acc", "new_acc"),
    [
        ("private", "public"),
        ("protected", "public"),
        ("private", "protected"),
        ("public", "public"),
    ],
)
def test_widening_or_unchanged_is_not_reported(old_acc: str, new_acc: str) -> None:
    assert _access_kinds(_rec({"LB3": old_acc}), _rec({"LB3": new_acc})) == []


def test_unknown_access_on_either_side_is_never_a_change() -> None:
    known = _rec({"LB3": "public"})
    unknown = _rec(None)
    assert _access_kinds(known, unknown) == []
    assert _access_kinds(unknown, known) == []
    assert _access_kinds(unknown, unknown) == []


def test_unknown_access_for_one_base_is_skipped_not_guessed() -> None:
    # "LB3" narrowed, but "LB4" has no recognised spelling on the new side:
    # only the evidenced narrowing is reported.
    old = _rec({"LB3": "public", "LB4": "public"}, bases=["LB3", "LB4"])
    new = _rec({"LB3": "private", "LB4": ""}, bases=["LB3", "LB4"])
    changes = diff_bases("LD3", old, new)
    assert [c.kind for c in changes] == [ChangeKind.BASE_CLASS_ACCESS_NARROWED]
    assert changes[0].old_value == "public"
    assert changes[0].new_value == "private"


def test_base_added_or_removed_is_not_an_access_narrowing() -> None:
    # Only bases named on both sides are compared; the hierarchy change is
    # TYPE_BASE_CHANGED's, and must not also read as an access narrowing.
    old = _rec({"LB3": "public"}, bases=["LB3"])
    new = _rec({"LB3": "public", "LB9": "private"}, bases=["LB3", "LB9"])
    assert _access_kinds(old, new) == []


def test_virtual_base_narrowing_is_reported() -> None:
    old = _rec({"VB": "public"}, bases=[], virtual_bases=["VB"])
    new = _rec({"VB": "private"}, bases=[], virtual_bases=["VB"])
    assert _access_kinds(old, new) == [ChangeKind.BASE_CLASS_ACCESS_NARROWED]


def test_incomplete_access_fact_on_one_side_declines_comparison() -> None:
    old = _rec({"LB3": "public"})
    new = RecordType(
        name="LD3",
        kind="struct",
        bases=["LB3"],
        base_access_fact=Fact.partial({"LB3": "private"}),
    )
    assert _access_kinds(old, new) == []


def test_clang_parse_bases_keeps_explicit_access_and_marks_missing_unknown() -> None:
    node = {
        "bases": [
            {"type": {"qualType": "LB3"}, "access": "private"},
            {"type": {"qualType": "VB"}, "access": "protected", "isVirtual": True},
            {"type": {"qualType": "NoAccess"}},
        ]
    }
    bases, virtual_bases, access = _parse_bases(node)
    assert bases == ["LB3", "NoAccess"]
    assert virtual_bases == ["VB"]
    assert access == {"LB3": "private", "VB": "protected", "NoAccess": ""}


def test_snapshot_without_base_access_key_loads_as_unknown_not_change() -> None:
    # A record dict written before this field existed has no key at all.
    decoded = decode_record_facts({}, schema_version=10_000)
    loaded = decoded["base_access_fact"]
    assert loaded is not None
    assert loaded.status is FactStatus.NOT_COLLECTED
    old = _rec({"LB3": "public"})
    new = RecordType(name="LD3", kind="struct", bases=["LB3"], base_access_fact=loaded)
    assert _access_kinds(old, new) == []


_SPECIFIERS = ("public", "protected", "private")


@pytest.mark.parametrize("old_acc", _SPECIFIERS)
@pytest.mark.parametrize("new_acc", _SPECIFIERS)
def test_every_specifier_pair_reports_iff_access_narrows(
    old_acc: str, new_acc: str
) -> None:
    # Oracle: the C++ access order written out independently of _ACCESS_RANK.
    narrows = (old_acc, new_acc) in {
        ("public", "protected"),
        ("public", "private"),
        ("protected", "private"),
    }
    want = [ChangeKind.BASE_CLASS_ACCESS_NARROWED] if narrows else []
    assert _access_kinds(_rec({"LB3": old_acc}), _rec({"LB3": new_acc})) == want
