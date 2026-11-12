"""Unknown DWARF member offsets are ``None``, never ``0`` (E9 finding 1).

``FieldInfo.byte_offset`` is ``int | None``: ``None`` is unknown. Three rules
are tested for the whole class, not only the reported input:

* the layout offset rule fires only when both offsets are known and differ,
  checked exhaustively over ``{None, 0, 4, 8}^2`` against the truth table the
  Lean model computed (``formal/lean/fires_truth_table.json``, whose domain
  is the same four values in bits), not against the implementation's helper;
* a serialized ``None`` round-trips as ``None``, a stored ``0`` as ``0``;
* a legacy snapshot whose member has no ``byte_offset`` key decodes to
  ``None``, not to offset 0.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from abicheck.compare.type_layout_diff import _existing_field_changes
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.debug_type_match import DebugRecordFacts
from abicheck.model.dwarf_facts import FieldInfo, StructLayout
from abicheck.snapshot_platform_blocks import dwarf_from_dict
from abicheck.storage.snapshot_encode import _encode_value

ROOT = Path(__file__).resolve().parent.parent
_LEAN_ROWS = json.loads(
    (ROOT / "formal" / "lean" / "fires_truth_table.json").read_text(encoding="utf-8")
)["rows"]
#: The Lean table is over offset *bits* {None, 0, 32, 64}; the domain under
#: test is the same set in bytes {None, 0, 4, 8}.
_ORACLE = {
    (None if a is None else a // 8, None if b is None else b // 8): fires
    for a, b, fires in _LEAN_ROWS
}
_DOMAIN = (None, 0, 4, 8)


def _field(offset: int | None) -> FieldInfo:
    return FieldInfo(name="f", type_name="int", byte_offset=offset, byte_size=4)


def _offset_changes(old: int | None, new: int | None) -> list[Any]:
    changes = _existing_field_changes("S", {"f": _field(old)}, {"f": _field(new)})
    return [c for c in changes if c.kind is ChangeKind.STRUCT_FIELD_OFFSET_CHANGED]


def test_oracle_covers_the_full_domain() -> None:
    assert set(_ORACLE) == set(itertools.product(_DOMAIN, repeat=2))


@pytest.mark.parametrize(("old", "new"), list(itertools.product(_DOMAIN, repeat=2)))
def test_offset_rule_fires_only_when_both_known_and_differ(
    old: int | None, new: int | None
) -> None:
    fires = bool(_offset_changes(old, new))
    assert fires == _ORACLE[(old, new)]
    # Stated directly, so the oracle is not the only check on the rule:
    assert fires == (old is not None and new is not None and old != new)


def test_unknown_offset_never_fabricates_a_move() -> None:
    assert _offset_changes(None, 0) == []  # 0 would fabricate a move
    assert _offset_changes(0, None) == []


def test_none_round_trips_through_the_snapshot_encoder() -> None:
    layout = StructLayout(
        name="S",
        byte_size=8,
        alignment=4,
        fields=[_field(None), _field(0), _field(4)],
    )
    encoded = json.loads(json.dumps(_encode_value(layout)))
    offsets = [f["byte_offset"] for f in encoded["fields"]]
    assert offsets == [None, 0, 4]  # written explicitly: null, 0, 4
    decoded = dwarf_from_dict({"structs": {"S": encoded}}).structs["S"]
    assert [f.byte_offset for f in decoded.fields] == [None, 0, 4]


@pytest.mark.parametrize(
    ("member", "expected"),
    [
        ({"name": "f", "byte_size": 4}, None),  # legacy: key absent
        ({"name": "f", "byte_size": 4, "byte_offset": None}, None),
        ({"name": "f", "byte_size": 4, "byte_offset": 0}, 0),  # stored 0 stays 0
        ({"name": "f", "byte_size": 4, "byte_offset": 8}, 8),
    ],
)
def test_legacy_member_decodes_missing_offset_as_unknown(
    member: dict[str, object], expected: int | None
) -> None:
    legacy = {"structs": {"S": {"name": "S", "byte_size": 8, "fields": [member]}}}
    (field,) = dwarf_from_dict(legacy).structs["S"].fields
    assert field.byte_offset == expected


def test_unknown_offset_is_not_compared_in_debug_type_match() -> None:
    layout = StructLayout(
        name="S",
        byte_size=8,
        alignment=4,
        fields=[
            FieldInfo(
                name="unknown_at", type_name="int", byte_offset=None, byte_size=4
            ),
            FieldInfo(name="known_at", type_name="int", byte_offset=4, byte_size=4),
        ],
    )
    facts = DebugRecordFacts.from_struct_layout(layout)
    # The unknown member has no entry at all, so it is never compared against
    # a header offset; the known one keeps its real bit offset.
    assert dict(facts.field_offsets_bits) == {"known_at": 32}


# --- Offset-based pairing (rename / reserved-field activation) -------------
# A pairing claims "same storage, new name"; it needs both offsets. With
# either side unknown the pair cannot be established, so the name that left
# is reported as removed -- never paired with a field at a guessed offset 0.


def _named(name: str, offset: int | None) -> FieldInfo:
    return FieldInfo(name=name, type_name="int", byte_offset=offset, byte_size=4)


def _removed_kinds(old: FieldInfo, new: FieldInfo) -> list[ChangeKind]:
    from abicheck.compare.type_layout_diff import _removed_field_changes

    changes = _removed_field_changes("S", {old.name: old}, {new.name: new})
    return [c.kind for c in changes]


@pytest.mark.parametrize(
    ("old_name", "paired"),
    [
        ("count", ChangeKind.FIELD_RENAMED),
        ("__reserved1", ChangeKind.USED_RESERVED_FIELD),
    ],
)
@pytest.mark.parametrize(
    ("old_off", "new_off"), [(None, 0), (0, None), (None, None), (0, 0), (4, 0)]
)
def test_offset_pairing_needs_both_offsets_known_and_equal(
    old_name: str, paired: ChangeKind, old_off: int | None, new_off: int | None
) -> None:
    kinds = _removed_kinds(_named(old_name, old_off), _named("flags", new_off))
    pairs = old_off is not None and old_off == new_off
    assert kinds == ([paired] if pairs else [ChangeKind.STRUCT_FIELD_REMOVED])


def test_anonymous_member_with_an_unknown_inner_offset_is_failed(monkeypatch) -> None:
    """An inner offset the producer could not evaluate makes every inlined
    offset unknown, so the container is FAILED rather than summed with 0."""
    from abicheck import dwarf_metadata as dm

    class _Die:
        tag = "DW_TAG_member"
        attributes = {"DW_AT_type": object()}

        def iter_children(self):  # type: ignore[no-untyped-def]
            return iter([_Die()])

    class _Union(_Die):
        tag = "DW_TAG_union_type"

    monkeypatch.setattr(dm, "_resolve_ref", lambda die, attr, cu: _Union())
    monkeypatch.setattr(dm, "_process_member", lambda die, cu, cache: _named("a", None))
    fact = dm._expand_anonymous_member(_Die(), None, {}, 8)
    from abicheck.model.fact import FactStatus

    assert fact.status is FactStatus.FAILED
    assert any("inner member offset is unknown" in d for d in fact.diagnostics)
