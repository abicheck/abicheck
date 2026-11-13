"""The inline-accessor rename check pairs every field-removal kind with every field-addition kind.

Bug class: a detector read one member of a sibling ChangeKind set and missed
the others. ``detect_inline_body_renamed_member`` paired ``type_field_removed``
only with ``type_field_added``, but a renamed member that lands at the end of
the record reads ``type_field_added_compatible`` (the generated edit corpus's
``pimpl_member_renamed_under_inline_accessor``), so the finding never fired.

The oracle is the ChangeKind catalog itself, read by name: every kind whose
name ends in ``FIELD_REMOVED`` is a removal and every kind ending in
``FIELD_ADDED`` or ``FIELD_ADDED_COMPATIBLE`` an addition, so a field-delta
kind added later fails here until the check learns it.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.checker_policy import ChangeKind
from abicheck.diff_cpp_patterns import detect_inline_body_renamed_member
from abicheck.model import AbiSnapshot, Function, RecordType, TypeField, Visibility
from abicheck.model.change import Change

_REMOVALS = sorted(k for k in ChangeKind if k.name.endswith("FIELD_REMOVED"))
_ADDITIONS = sorted(
    k for k in ChangeKind if k.name.endswith(("FIELD_ADDED", "FIELD_ADDED_COMPATIBLE"))
)
_IMPL = "lib::detail::impl"


def _pair(removed_kind: ChangeKind, added_kind: ChangeKind, internal: str = _IMPL):
    def rec(field: str) -> RecordType:
        return RecordType(
            name=internal,
            kind="struct",
            size_bits=64,
            fields=[TypeField(name=field, type="int")],
        )

    holder = RecordType(
        name="lib::Desc",
        kind="class",
        size_bits=64,
        fields=[TypeField(name="p_", type=f"{internal} *")],
    )
    getter = Function(
        name="lib::Desc::count",
        mangled="_ZNK3lib4Desc5countEv",
        return_type="int",
        visibility=Visibility.PUBLIC,
        is_inline=True,
    )

    def snap(field: str) -> AbiSnapshot:
        return AbiSnapshot(
            library="l",
            version="0",
            functions=[getter],
            variables=[],
            types=[rec(field), holder],
        )

    changes = [
        Change(
            kind=removed_kind,
            symbol=f"{internal}::count_",
            description="",
            old_value="count_",
        ),
        Change(
            kind=added_kind, symbol=f"{internal}::n_", description="", new_value="n_"
        ),
    ]
    return snap("count_"), snap("n_"), changes


def test_the_catalog_has_the_sibling_kinds_this_test_ranges_over() -> None:
    assert ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE in _ADDITIONS
    assert len(_REMOVALS) >= 2 and len(_ADDITIONS) >= 3


@pytest.mark.parametrize(
    ("removed_kind", "added_kind"),
    list(itertools.product(_REMOVALS, _ADDITIONS)),
    ids=lambda k: k.name,
)
def test_every_removal_kind_pairs_with_every_addition_kind(
    removed_kind: ChangeKind, added_kind: ChangeKind
) -> None:
    old, new, changes = _pair(removed_kind, added_kind)
    found = detect_inline_body_renamed_member(old, new, changes)
    assert [(f.kind, f.old_value, f.new_value) for f in found] == [
        (ChangeKind.INLINE_BODY_REFERENCES_RENAMED_MEMBER, "count_", "n_")
    ]


@pytest.mark.parametrize("added_kind", _ADDITIONS, ids=lambda k: k.name)
def test_a_non_internal_record_never_pairs(added_kind: ChangeKind) -> None:
    # Negative control: the same delta on a public record is no pimpl rename.
    old, new, changes = _pair(
        ChangeKind.TYPE_FIELD_REMOVED, added_kind, internal="lib::Store"
    )
    assert detect_inline_body_renamed_member(old, new, changes) == []


def test_two_unrelated_kinds_never_pair() -> None:
    # Negative control: a removal with no addition is not a rename.
    old, new, changes = _pair(
        ChangeKind.TYPE_FIELD_REMOVED, ChangeKind.TYPE_FIELD_ADDED
    )
    assert detect_inline_body_renamed_member(old, new, changes[:1]) == []


def test_rename_candidates_read_only_field_renames_with_both_names() -> None:
    from abicheck.compare.field_rename_candidates import collect_field_rename_candidates

    changes = [
        Change(ChangeKind.FIELD_RENAMED, _IMPL, "renamed", "old_v", "new_v"),
        Change(ChangeKind.FIELD_RENAMED, _IMPL, "no new name", "old_w", None),
        Change(ChangeKind.TYPE_FIELD_REMOVED, f"{_IMPL}::x", "removed", "x", None),
    ]
    assert collect_field_rename_candidates(changes, ("detail",)) == [
        (_IMPL, "old_v", "new_v")
    ]


def test_paired_candidates_skip_other_kinds_and_unqualified_symbols() -> None:
    from abicheck.compare.field_rename_candidates import collect_paired_field_candidates

    changes = [
        # neither a field removal nor a field addition
        Change(ChangeKind.FIELD_RENAMED, _IMPL, "renamed", "a", "b"),
        # no ``Record::field`` qualification: nothing to pair on
        Change(ChangeKind.TYPE_FIELD_REMOVED, "loose", "removed"),
        Change(ChangeKind.TYPE_FIELD_ADDED, "other", "added"),
    ]
    assert collect_paired_field_candidates(changes, ("detail",)) == []
