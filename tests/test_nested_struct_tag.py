"""Nested struct tag normalization (abicc #53).

castxml returns bare type names for struct/class/union fields (e.g. "Inner"),
while older DWARF parsers historically prefixed the tag keyword ("struct Inner").
This mismatch caused false STRUCT_FIELD_TYPE_CHANGED reports.

Fixes:
1. _compute_record_type_info() in dwarf_metadata.py — drops "struct " prefix
2. _diff_struct_layouts() in checker.py — normalizes type names before comparison
"""
from __future__ import annotations

from abicheck.dwarf_metadata import DwarfMetadata, FieldInfo, StructLayout


class TestComputeRecordTypeInfo:
    """_compute_record_type_info must return bare names (no struct/class prefix)."""

    def _make_die(self, tag: str, name: str, size: int = 4) -> object:
        from types import SimpleNamespace
        return SimpleNamespace(
            tag=tag,
            attributes={
                "DW_AT_name": SimpleNamespace(value=name.encode()),
                "DW_AT_byte_size": SimpleNamespace(value=size),
            }
        )

    def test_struct_tag_no_prefix(self) -> None:
        """DW_TAG_structure_type must produce 'Inner', not 'struct Inner'."""
        from abicheck.dwarf_metadata import (
            _compute_record_type_info,  # type: ignore[attr-defined]
        )
        die = self._make_die("DW_TAG_structure_type", "Inner", 8)
        name, _ = _compute_record_type_info(die, "DW_TAG_structure_type")
        assert name == "Inner", f"Expected 'Inner', got {name!r}"
        assert not name.startswith("struct "), "struct prefix must NOT be added"

    def test_class_tag_no_prefix(self) -> None:
        """DW_TAG_class_type must produce 'Foo', not 'class Foo'."""
        from abicheck.dwarf_metadata import (
            _compute_record_type_info,  # type: ignore[attr-defined]
        )
        die = self._make_die("DW_TAG_class_type", "Foo", 16)
        name, _ = _compute_record_type_info(die, "DW_TAG_class_type")
        assert name == "Foo", f"Expected 'Foo', got {name!r}"


class TestNestedStructTagNormalization:
    """DWARF 'struct Foo' vs castxml 'Foo' must not emit STRUCT_FIELD_TYPE_CHANGED."""

    def _make_meta(
        self,
        struct_name: str,
        field_name: str,
        field_type: str,
        byte_size: int = 8,
    ) -> DwarfMetadata:
        return DwarfMetadata(
            structs={
                struct_name: StructLayout(
                    name=struct_name,
                    byte_size=byte_size,
                    fields=[FieldInfo(name=field_name, type_name=field_type,
                                     byte_offset=0, byte_size=4)],
                )
            },
            has_dwarf=True,
        )

    def test_no_false_struct_field_type_changed(self) -> None:
        """'struct Inner' (old) vs 'Inner' (new) must NOT emit STRUCT_FIELD_TYPE_CHANGED."""
        from abicheck.checker import (
            ChangeKind,
            _diff_struct_layouts,  # type: ignore[attr-defined]
        )

        old_meta = self._make_meta("Outer", "child", "struct Inner")
        new_meta = self._make_meta("Outer", "child", "Inner")

        changes = _diff_struct_layouts(old_meta, new_meta)
        kinds = {c.kind for c in changes}
        assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED not in kinds, (
            "'struct Inner' vs 'Inner' must not trigger STRUCT_FIELD_TYPE_CHANGED"
        )

    def test_no_false_class_field_type_changed(self) -> None:
        """'class Foo' vs 'Foo' must NOT emit STRUCT_FIELD_TYPE_CHANGED."""
        from abicheck.checker import (
            ChangeKind,
            _diff_struct_layouts,  # type: ignore[attr-defined]
        )

        old_meta = self._make_meta("Container", "item", "class Foo")
        new_meta = self._make_meta("Container", "item", "Foo")

        changes = _diff_struct_layouts(old_meta, new_meta)
        kinds = {c.kind for c in changes}
        assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED not in kinds

    def test_real_type_change_still_detected(self) -> None:
        """'Inner' vs 'Other' (genuinely different type) must STILL be detected."""
        from abicheck.checker import (
            ChangeKind,
            _diff_struct_layouts,  # type: ignore[attr-defined]
        )

        old_meta = self._make_meta("Outer", "child", "Inner")
        new_meta = self._make_meta("Outer", "child", "Other")

        changes = _diff_struct_layouts(old_meta, new_meta)
        kinds = {c.kind for c in changes}
        assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in kinds, (
            "Real type change Inner→Other must still be detected"
        )

    def test_union_prefix_normalized(self) -> None:
        """'union U' vs 'U' must NOT emit STRUCT_FIELD_TYPE_CHANGED."""
        from abicheck.checker import (
            ChangeKind,
            _diff_struct_layouts,  # type: ignore[attr-defined]
        )

        old_meta = self._make_meta("Outer", "u_field", "union U")
        new_meta = self._make_meta("Outer", "u_field", "U")

        changes = _diff_struct_layouts(old_meta, new_meta)
        kinds = {c.kind for c in changes}
        assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED not in kinds

    def test_anonymous_member_type_name_mismatch_not_a_change(self) -> None:
        """An anonymous union member named "<unnamed-tag>" by one reader and
        "Parent::<unnamed-type-u>" by another (same size) must NOT emit
        STRUCT_FIELD_TYPE_CHANGED — the placeholder names have no stable
        identity. (Mirrors the Windows SDK _TP_CALLBACK_ENVIRON_V3::u case that
        differs between two MSVC builds.)"""
        from abicheck.checker import (
            ChangeKind,
            _diff_struct_layouts,  # type: ignore[attr-defined]
        )

        old_meta = self._make_meta("_TP_CALLBACK_ENVIRON_V3", "u", "<unnamed-tag>")
        new_meta = self._make_meta(
            "_TP_CALLBACK_ENVIRON_V3", "u", "_TP_CALLBACK_ENVIRON_V3::<unnamed-type-u>"
        )

        changes = _diff_struct_layouts(old_meta, new_meta)
        kinds = {c.kind for c in changes}
        assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED not in kinds, (
            "anonymous-member placeholder rename must not be a field type change"
        )

    def test_anonymous_member_size_drift_still_detected(self) -> None:
        """A genuine size change of an anonymous member is still caught (the
        name collapse only suppresses unstable-name noise, not real drift)."""
        from abicheck.checker import (
            ChangeKind,
            _diff_struct_layouts,  # type: ignore[attr-defined]
        )

        old_meta = DwarfMetadata(
            structs={"Outer": StructLayout(name="Outer", byte_size=8, fields=[
                FieldInfo(name="u", type_name="<unnamed-tag>", byte_offset=0, byte_size=4)])},
            has_dwarf=True,
        )
        new_meta = DwarfMetadata(
            structs={"Outer": StructLayout(name="Outer", byte_size=8, fields=[
                FieldInfo(name="u", type_name="<unnamed-type-u>", byte_offset=0, byte_size=8)])},
            has_dwarf=True,
        )

        changes = _diff_struct_layouts(old_meta, new_meta)
        kinds = {c.kind for c in changes}
        assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in kinds, (
            "size drift of an anonymous member must still be reported"
        )

    def test_anonymous_aggregate_kind_change_still_detected(self) -> None:
        """A same-size anonymous *kind* change (union → struct) is ABI-relevant
        and must still be reported — the placeholder collapse preserves the
        aggregate keyword, so it is not masked."""
        from abicheck.checker import (
            ChangeKind,
            _diff_struct_layouts,  # type: ignore[attr-defined]
        )

        old_meta = self._make_meta("Outer", "u", "<anonymous union>")
        new_meta = self._make_meta("Outer", "u", "<anonymous struct>")

        changes = _diff_struct_layouts(old_meta, new_meta)
        kinds = {c.kind for c in changes}
        assert ChangeKind.STRUCT_FIELD_TYPE_CHANGED in kinds, (
            "anonymous union → anonymous struct must still be a field type change"
        )


# ---------------------------------------------------------------------------
# Reserved-field reuse: type matching (architecture review fix #4)
# ---------------------------------------------------------------------------


class TestReservedFieldTypeMatch:
    """_diff_struct_layouts must verify type_name match for reserved-field reuse."""

    def _make_dwarf(self, struct_name: str, fields: list[FieldInfo]) -> DwarfMetadata:
        return DwarfMetadata(
            structs={
                struct_name: StructLayout(
                    name=struct_name,
                    byte_size=16,
                    fields=fields,
                ),
            },
            has_dwarf=True,
        )

    def test_same_type_matches_reserved(self):
        """Reserved field with same type at same offset → USED_RESERVED_FIELD."""
        from abicheck.checker import ChangeKind, _diff_struct_layouts

        old = self._make_dwarf("S", [
            FieldInfo(name="__reserved0", type_name="uint32_t", byte_offset=0, byte_size=4),
            FieldInfo(name="x", type_name="int", byte_offset=4, byte_size=4),
        ])
        new = self._make_dwarf("S", [
            FieldInfo(name="active", type_name="uint32_t", byte_offset=0, byte_size=4),
            FieldInfo(name="x", type_name="int", byte_offset=4, byte_size=4),
        ])
        changes = _diff_struct_layouts(old, new)
        kinds = [c.kind for c in changes]
        assert ChangeKind.USED_RESERVED_FIELD in kinds
        assert ChangeKind.STRUCT_FIELD_REMOVED not in kinds

    def test_different_type_no_reserved_match(self):
        """Reserved field with different type at same offset → STRUCT_FIELD_REMOVED."""
        from abicheck.checker import ChangeKind, _diff_struct_layouts

        old = self._make_dwarf("S", [
            FieldInfo(name="__reserved0", type_name="uint32_t", byte_offset=0, byte_size=4),
            FieldInfo(name="x", type_name="int", byte_offset=4, byte_size=4),
        ])
        new = self._make_dwarf("S", [
            FieldInfo(name="active", type_name="float", byte_offset=0, byte_size=4),
            FieldInfo(name="x", type_name="int", byte_offset=4, byte_size=4),
        ])
        changes = _diff_struct_layouts(old, new)
        kinds = [c.kind for c in changes]
        assert ChangeKind.USED_RESERVED_FIELD not in kinds
        assert ChangeKind.STRUCT_FIELD_REMOVED in kinds
