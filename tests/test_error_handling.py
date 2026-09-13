"""Error handling tests -- verify graceful behavior on bad/edge-case inputs.

Tests cover:
- Corrupted/non-ELF files for ELF and DWARF parsers
- Invalid/empty JSON snapshots for serialization
- Suppression file edge cases
- Checker edge cases with unusual data
"""

import json

import pytest

from abicheck.checker import ChangeKind, Verdict, compare
from abicheck.dwarf_advanced import AdvancedDwarfMetadata, parse_advanced_dwarf
from abicheck.dwarf_metadata import DwarfMetadata, parse_dwarf_metadata
from abicheck.elf_metadata import ElfMetadata, parse_elf_metadata
from abicheck.model import (
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    Visibility,
)
from abicheck.serialization import (
    load_snapshot,
    snapshot_from_dict,
    snapshot_to_dict,
)
from abicheck.suppression import Suppression, SuppressionList


def _snap(**kwargs):
    defaults = {
        "library": "libtest.so",
        "version": "1.0",
        "functions": [],
        "variables": [],
        "types": [],
    }
    defaults.update(kwargs)
    return AbiSnapshot(**defaults)


class TestElfParserErrorHandling:
    """ELF parser should return empty metadata on bad input, never crash."""

    def test_non_elf_file(self, tmp_path):
        bad_file = tmp_path / "not_elf.so"
        bad_file.write_bytes(b"This is not an ELF file at all")
        meta = parse_elf_metadata(bad_file)
        assert isinstance(meta, ElfMetadata)
        assert meta.symbols == []

    def test_empty_file(self, tmp_path):
        empty = tmp_path / "empty.so"
        empty.write_bytes(b"")
        meta = parse_elf_metadata(empty)
        assert isinstance(meta, ElfMetadata)

    def test_nonexistent_file(self, tmp_path):
        missing = tmp_path / "missing.so"
        meta = parse_elf_metadata(missing)
        assert isinstance(meta, ElfMetadata)

    def test_truncated_elf_header(self, tmp_path):
        # ELF magic but truncated
        bad_elf = tmp_path / "truncated.so"
        bad_elf.write_bytes(b"\x7fELF\x02\x01\x01")
        meta = parse_elf_metadata(bad_elf)
        assert isinstance(meta, ElfMetadata)

    def test_directory_instead_of_file(self, tmp_path):
        meta = parse_elf_metadata(tmp_path)
        assert isinstance(meta, ElfMetadata)


class TestDwarfParserErrorHandling:
    """DWARF parsers should return empty metadata on bad input."""

    def test_non_elf_file(self, tmp_path):
        bad_file = tmp_path / "bad.so"
        bad_file.write_bytes(b"not an ELF file")
        meta = parse_dwarf_metadata(bad_file)
        assert isinstance(meta, DwarfMetadata)
        assert not meta.has_dwarf

    def test_nonexistent_file(self, tmp_path):
        missing = tmp_path / "missing.so"
        meta = parse_dwarf_metadata(missing)
        assert isinstance(meta, DwarfMetadata)

    def test_advanced_dwarf_non_elf(self, tmp_path):
        bad_file = tmp_path / "bad.so"
        bad_file.write_bytes(b"garbage")
        meta = parse_advanced_dwarf(bad_file)
        assert isinstance(meta, AdvancedDwarfMetadata)
        assert not meta.has_dwarf

    def test_advanced_dwarf_nonexistent(self, tmp_path):
        missing = tmp_path / "missing.so"
        meta = parse_advanced_dwarf(missing)
        assert isinstance(meta, AdvancedDwarfMetadata)


class TestSerializationErrorHandling:
    """Serialization should handle malformed/incomplete JSON gracefully."""

    def test_missing_required_fields(self):
        with pytest.raises(KeyError):
            snapshot_from_dict({})

    def test_minimal_valid_dict(self):
        snap = snapshot_from_dict({"library": "lib.so", "version": "1.0"})
        assert snap.library == "lib.so"
        assert snap.functions == []

    def test_unknown_visibility_in_function(self):
        with pytest.raises(ValueError):
            snapshot_from_dict(
                {
                    "library": "lib.so",
                    "version": "1.0",
                    "functions": [
                        {
                            "name": "f",
                            "mangled": "f",
                            "return_type": "void",
                            "visibility": "invalid_visibility",
                        }
                    ],
                }
            )

    def test_load_nonexistent_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_snapshot(tmp_path / "does_not_exist.json")

    def test_load_invalid_json(self, tmp_path):
        bad_json = tmp_path / "bad.json"
        bad_json.write_text("not valid json {{{", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            load_snapshot(bad_json)

    def test_roundtrip_preserves_is_extern_c(self):
        """Verify is_extern_c survives serialization round-trip."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            functions=[
                Function(
                    name="cfunc",
                    mangled="cfunc",
                    return_type="void",
                    is_extern_c=True,
                    visibility=Visibility.PUBLIC,
                )
            ],
        )
        d = snapshot_to_dict(snap)
        snap2 = snapshot_from_dict(d)
        assert snap2.functions[0].is_extern_c is True

    def test_roundtrip_preserves_alignment_bits(self):
        """Verify alignment_bits survives serialization round-trip."""
        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            types=[
                RecordType(
                    name="Aligned",
                    kind="struct",
                    size_bits=128,
                    alignment_bits=64,
                )
            ],
        )
        d = snapshot_to_dict(snap)
        snap2 = snapshot_from_dict(d)
        assert snap2.types[0].alignment_bits == 64

    def test_is_union_derived_from_kind_when_missing(self):
        """When is_union is absent from JSON, derive from kind."""
        d = {
            "library": "lib.so",
            "version": "1.0",
            "types": [{"name": "U", "kind": "union", "fields": []}],
        }
        snap = snapshot_from_dict(d)
        assert snap.types[0].is_union is True

    def test_elf_metadata_roundtrip(self):
        from abicheck.elf_metadata import ElfSymbol, SymbolBinding, SymbolType

        snap = AbiSnapshot(
            library="lib.so",
            version="1.0",
            elf=ElfMetadata(
                soname="lib.so.1",
                needed=["libc.so.6"],
                symbols=[
                    ElfSymbol(
                        name="sym",
                        binding=SymbolBinding.GLOBAL,
                        sym_type=SymbolType.FUNC,
                        size=42,
                    )
                ],
            ),
        )
        d = snapshot_to_dict(snap)
        snap2 = snapshot_from_dict(d)
        assert snap2.elf is not None
        assert snap2.elf.soname == "lib.so.1"
        assert len(snap2.elf.symbols) == 1


class TestSuppressionErrorHandling:
    """Suppression system should reject invalid inputs clearly."""

    def test_both_symbol_and_pattern_rejected(self):
        with pytest.raises(ValueError, match="mutually exclusive"):
            Suppression(symbol="foo", symbol_pattern="bar")

    def test_neither_symbol_nor_pattern_rejected(self):
        with pytest.raises(ValueError, match="must have"):
            Suppression()

    def test_invalid_regex_pattern_rejected(self):
        with pytest.raises(ValueError, match="Invalid"):
            Suppression(symbol_pattern="[invalid")

    def test_unknown_change_kind_rejected(self):
        with pytest.raises(ValueError, match="Unknown change_kind"):
            Suppression(symbol="foo", change_kind="not_a_real_kind")

    def test_load_nonexistent_suppression_file(self, tmp_path):
        with pytest.raises(OSError):
            SuppressionList.load(tmp_path / "missing.yaml")

    def test_load_invalid_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{: invalid", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid YAML"):
            SuppressionList.load(bad)

    def test_load_wrong_version(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("version: 99\nsuppressions: []\n", encoding="utf-8")
        with pytest.raises(ValueError, match="version"):
            SuppressionList.load(bad)

    def test_load_unknown_key_rejected(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            "version: 1\nsuppressions:\n  - symbol: foo\n    typo_key: bar\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="unknown key"):
            SuppressionList.load(bad)


class TestCheckerEdgeCases:
    """Edge cases for the checker diff engine."""

    def test_vtable_identical_entries_different_order_is_breaking(self):
        """Vtable reorder (same entries, different order) is breaking."""
        t_old = RecordType(name="W", kind="class", vtable=["_ZA", "_ZB", "_ZC"])
        t_new = RecordType(name="W", kind="class", vtable=["_ZC", "_ZA", "_ZB"])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert r.verdict == Verdict.BREAKING
        vtable_changes = [
            c for c in r.changes if c.kind == ChangeKind.TYPE_VTABLE_CHANGED
        ]
        assert len(vtable_changes) == 1
        assert "reordered" in vtable_changes[0].description

    def test_empty_vtable_to_nonempty_is_breaking(self):
        t_old = RecordType(name="W", kind="class", vtable=[])
        t_new = RecordType(name="W", kind="class", vtable=["_ZA"])
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert r.verdict == Verdict.BREAKING

    def test_enum_with_no_members(self):
        """Empty enum (forward-declared) should not crash."""
        e_old = EnumType(name="E", members=[])
        e_new = EnumType(name="E", members=[EnumMember("A", 0)])
        r = compare(_snap(enums=[e_old]), _snap(enums=[e_new]))
        assert any(c.kind == ChangeKind.ENUM_MEMBER_ADDED for c in r.changes)

    def test_type_with_no_fields(self):
        """Empty struct (opaque) should be compared without crash."""
        t_old = RecordType(name="Opaque", kind="struct", size_bits=8)
        t_new = RecordType(name="Opaque", kind="struct", size_bits=16)
        r = compare(_snap(types=[t_old]), _snap(types=[t_new]))
        assert r.verdict == Verdict.BREAKING

    def test_typedef_no_change(self):
        r = compare(
            _snap(typedefs={"Alias": "int"}),
            _snap(typedefs={"Alias": "int"}),
        )
        assert r.verdict == Verdict.NO_CHANGE
