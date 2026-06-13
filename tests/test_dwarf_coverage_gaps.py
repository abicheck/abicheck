"""Tests targeting uncovered lines in dwarf_utils, dwarf_unified, dwarf_advanced, dwarf_snapshot.

Covers:
- dwarf_utils.py lines 85-86 (resolve_type_die exception path)
- dwarf_unified.py lines 75-76, 96-97, 100-101 (non-regular file, meta/adv CU skip)
- dwarf_advanced.py lines 161-172, 208, 226, 252-254, 273, 340, 412, 418, 483,
  502, 521, 527-528, 535, 558, 601, 612-613, 748-770, 785-793
- dwarf_snapshot.py (missing attribute fallback paths)
"""
from __future__ import annotations

import stat
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from elftools.common.exceptions import ELFError

from abicheck.dwarf_utils import (
    attr_bool,
    attr_int,
    attr_str,
    resolve_die_ref,
    resolve_type_die,
)

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------

class MockAttr:
    """Simulate a pyelftools AttributeValue."""

    def __init__(self, value: Any, form: str = "DW_FORM_data4"):
        self.value = value
        self.form = form


class MockDIE:
    """Simulate a pyelftools DIE."""

    def __init__(
        self,
        tag: str = "DW_TAG_base_type",
        attributes: dict[str, MockAttr] | None = None,
        offset: int = 0,
        children: list[Any] | None = None,
    ):
        self.tag = tag
        self.attributes = attributes or {}
        self.offset = offset
        self._children = children or []

    def iter_children(self):
        return iter(self._children)


class MockCU:
    """Simulate a pyelftools CompilationUnit."""

    def __init__(
        self,
        cu_offset: int = 0,
        die_map: dict[int, MockDIE] | None = None,
        top_die: MockDIE | None = None,
    ):
        self.cu_offset = cu_offset
        self._die_map = die_map or {}
        self._top_die = top_die or MockDIE(tag="DW_TAG_compile_unit")

    def get_DIE_from_refaddr(self, offset: int) -> MockDIE:
        if offset in self._die_map:
            return self._die_map[offset]
        return MockDIE()

    def get_top_DIE(self) -> MockDIE:
        return self._top_die


# ===========================================================================
# dwarf_utils tests
# ===========================================================================

class TestAttrStr:
    """attr_str: bytes value, None value, missing attribute."""

    def test_bytes_value(self):
        die = MockDIE(attributes={"DW_AT_name": MockAttr(b"hello")})
        assert attr_str(die, "DW_AT_name") == "hello"

    def test_bytes_invalid_utf8(self):
        die = MockDIE(attributes={"DW_AT_name": MockAttr(b"\xff\xfe")})
        result = attr_str(die, "DW_AT_name")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_none_value(self):
        die = MockDIE(attributes={"DW_AT_name": MockAttr(None)})
        assert attr_str(die, "DW_AT_name") == ""

    def test_missing_attribute(self):
        die = MockDIE(attributes={})
        assert attr_str(die, "DW_AT_name") == ""

    def test_int_value(self):
        die = MockDIE(attributes={"DW_AT_name": MockAttr(42)})
        assert attr_str(die, "DW_AT_name") == "42"


class TestAttrInt:
    """attr_int: TypeError/ValueError for non-integer values."""

    def test_normal_int(self):
        die = MockDIE(attributes={"DW_AT_byte_size": MockAttr(8)})
        assert attr_int(die, "DW_AT_byte_size") == 8

    def test_type_error(self):
        """attr_int returns 0 when value cannot be converted to int."""
        die = MockDIE(attributes={"DW_AT_byte_size": MockAttr(object())})
        assert attr_int(die, "DW_AT_byte_size") == 0

    def test_value_error(self):
        die = MockDIE(attributes={"DW_AT_byte_size": MockAttr("not_a_number")})
        assert attr_int(die, "DW_AT_byte_size") == 0

    def test_missing_attribute(self):
        die = MockDIE(attributes={})
        assert attr_int(die, "DW_AT_byte_size") == 0

    def test_none_value(self):
        die = MockDIE(attributes={"DW_AT_byte_size": MockAttr(None)})
        assert attr_int(die, "DW_AT_byte_size") == 0


class TestAttrBool:
    """attr_bool: missing attribute returns False."""

    def test_missing_attribute(self):
        die = MockDIE(attributes={})
        assert attr_bool(die, "DW_AT_external") is False

    def test_true_value(self):
        die = MockDIE(attributes={"DW_AT_external": MockAttr(1)})
        assert attr_bool(die, "DW_AT_external") is True

    def test_false_value(self):
        die = MockDIE(attributes={"DW_AT_external": MockAttr(0)})
        assert attr_bool(die, "DW_AT_external") is False


class TestResolveDieRef:
    """resolve_die_ref: DW_FORM_ref_addr and CU-relative forms."""

    def test_ref_addr_absolute(self):
        """DW_FORM_ref_addr: value is section-absolute offset."""
        target = MockDIE(tag="DW_TAG_base_type", offset=100)
        cu = MockCU(cu_offset=50, die_map={100: target})
        die = MockDIE(attributes={
            "DW_AT_type": MockAttr(100, form="DW_FORM_ref_addr")
        })
        result = resolve_die_ref(die, "DW_AT_type", cu)
        assert result is target

    def test_cu_relative_ref4(self):
        """DW_FORM_ref4: value is CU-relative, need to add cu_offset."""
        target = MockDIE(tag="DW_TAG_pointer_type", offset=150)
        cu = MockCU(cu_offset=100, die_map={150: target})
        die = MockDIE(attributes={
            "DW_AT_type": MockAttr(50, form="DW_FORM_ref4")
        })
        result = resolve_die_ref(die, "DW_AT_type", cu)
        assert result is target

    def test_cu_relative_ref1(self):
        target = MockDIE(tag="DW_TAG_base_type", offset=30)
        cu = MockCU(cu_offset=20, die_map={30: target})
        die = MockDIE(attributes={
            "DW_AT_type": MockAttr(10, form="DW_FORM_ref1")
        })
        result = resolve_die_ref(die, "DW_AT_type", cu)
        assert result is target

    def test_cu_relative_ref_udata(self):
        target = MockDIE(tag="DW_TAG_typedef", offset=80)
        cu = MockCU(cu_offset=30, die_map={80: target})
        die = MockDIE(attributes={
            "DW_AT_type": MockAttr(50, form="DW_FORM_ref_udata")
        })
        result = resolve_die_ref(die, "DW_AT_type", cu)
        assert result is target


class TestResolveTypeDie:
    """resolve_type_die: missing attribute, and resolve_die_ref exception."""

    def test_missing_dw_at_type(self):
        die = MockDIE(attributes={})
        cu = MockCU()
        assert resolve_type_die(die, cu) is None

    def test_resolve_die_ref_raises(self):
        """Lines 85-86: when resolve_die_ref raises, return None."""
        die = MockDIE(attributes={
            "DW_AT_type": MockAttr(9999, form="DW_FORM_ref4")
        })
        cu = MockCU(cu_offset=0)
        # Make get_DIE_from_refaddr raise
        cu.get_DIE_from_refaddr = MagicMock(side_effect=KeyError("bad ref"))
        result = resolve_type_die(die, cu)
        assert result is None

    def test_resolve_succeeds(self):
        target = MockDIE(tag="DW_TAG_base_type", offset=50)
        cu = MockCU(cu_offset=0, die_map={50: target})
        die = MockDIE(attributes={
            "DW_AT_type": MockAttr(50, form="DW_FORM_ref4")
        })
        result = resolve_type_die(die, cu)
        assert result is target


# ===========================================================================
# dwarf_unified tests
# ===========================================================================

class TestParseUnified:
    """dwarf_unified.parse_dwarf edge cases."""

    def test_non_regular_file(self, tmp_path):
        """Lines 74-76: non-regular file returns empty tuple."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata
        from abicheck.dwarf_unified import parse_dwarf

        fake_path = tmp_path / "fake.so"
        fake_path.write_bytes(b"\x00")

        # Mock os.fstat to return a non-regular file mode (e.g. S_IFIFO)
        fake_stat = MagicMock()
        fake_stat.st_mode = stat.S_IFIFO  # pipe, not regular file

        with patch("abicheck.dwarf_unified.os.fstat", return_value=fake_stat):
            meta, adv = parse_dwarf(fake_path)

        assert isinstance(meta, DwarfMetadata)
        assert isinstance(adv, AdvancedDwarfMetadata)
        assert not meta.has_dwarf
        assert not adv.has_dwarf

    def test_meta_process_cu_raises(self, tmp_path):
        """Lines 96-97: _meta_process_cu exception is caught and logged."""
        from abicheck.dwarf_unified import parse_dwarf

        fake_path = tmp_path / "fake.so"
        fake_path.write_bytes(b"\x00")

        mock_cu = MockCU()
        mock_elf = MagicMock()
        mock_elf.has_dwarf_info.return_value = True
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [mock_cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        fake_stat = MagicMock()
        fake_stat.st_mode = stat.S_IFREG | 0o644

        with (
            patch("abicheck.dwarf_unified.os.fstat", return_value=fake_stat),
            patch("abicheck.dwarf_unified.ELFFile", return_value=mock_elf),
            patch(
                "abicheck.dwarf_unified._meta_process_cu",
                side_effect=RuntimeError("meta boom"),
            ),
            patch(
                "abicheck.dwarf_unified._adv_process_cu",
                side_effect=lambda cu, adv: None,
            ),
        ):
            meta, adv = parse_dwarf(fake_path)

        # Should still return (possibly partial) results without raising
        assert meta is not None
        assert adv is not None

    def test_adv_process_cu_raises_elferror(self, tmp_path):
        """Lines 100-101: _adv_process_cu ELFError is caught and logged."""
        from abicheck.dwarf_unified import parse_dwarf

        fake_path = tmp_path / "fake.so"
        fake_path.write_bytes(b"\x00")

        mock_cu = MockCU()
        mock_elf = MagicMock()
        mock_elf.has_dwarf_info.return_value = True
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [mock_cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        fake_stat = MagicMock()
        fake_stat.st_mode = stat.S_IFREG | 0o644

        with (
            patch("abicheck.dwarf_unified.os.fstat", return_value=fake_stat),
            patch("abicheck.dwarf_unified.ELFFile", return_value=mock_elf),
            patch(
                "abicheck.dwarf_unified._meta_process_cu",
                side_effect=lambda cu, meta, tc: None,
            ),
            patch(
                "abicheck.dwarf_unified._adv_process_cu",
                side_effect=ELFError("adv boom"),
            ),
        ):
            meta, adv = parse_dwarf(fake_path)

        assert meta is not None
        assert adv is not None

    def test_unified_path_sets_target_arch(self, tmp_path):
        """parse_dwarf() records the normalized target arch on AdvancedDwarfMetadata.

        Regression: the SysV-AMD64 struct-return guard reads adv.target_arch, so
        the unified dump path (not just the standalone parse_advanced_dwarf) must
        populate it — otherwise AArch64/i386 dumps keep "" and are mis-treated as
        SysV AMD64.
        """
        from abicheck.dwarf_unified import parse_dwarf

        fake_path = tmp_path / "fake.so"
        fake_path.write_bytes(b"\x00")

        mock_cu = MockCU()
        mock_elf = MagicMock()
        mock_elf.has_dwarf_info.return_value = True
        mock_elf.get_machine_arch.return_value = "AArch64"
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [mock_cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        fake_stat = MagicMock()
        fake_stat.st_mode = stat.S_IFREG | 0o644

        with (
            patch("abicheck.dwarf_unified.os.fstat", return_value=fake_stat),
            patch("abicheck.dwarf_unified.ELFFile", return_value=mock_elf),
            patch("abicheck.dwarf_unified._meta_process_cu", side_effect=lambda cu, meta, tc: None),
            patch("abicheck.dwarf_unified._adv_process_cu", side_effect=lambda cu, adv: None),
        ):
            _meta, adv = parse_dwarf(fake_path)

        assert adv.target_arch == "aarch64"

    def test_elffile_raises_elferror(self, tmp_path):
        """Lines 105-107: ELFFile raises ELFError => returns empty tuple."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_metadata import DwarfMetadata
        from abicheck.dwarf_unified import parse_dwarf

        fake_path = tmp_path / "fake.so"
        fake_path.write_bytes(b"\x00not-elf")

        fake_stat = MagicMock()
        fake_stat.st_mode = stat.S_IFREG | 0o644

        with (
            patch("abicheck.dwarf_unified.os.fstat", return_value=fake_stat),
            patch(
                "abicheck.dwarf_unified.ELFFile",
                side_effect=ELFError("not an ELF"),
            ),
        ):
            meta, adv = parse_dwarf(fake_path)

        assert isinstance(meta, DwarfMetadata)
        assert isinstance(adv, AdvancedDwarfMetadata)
        assert not meta.has_dwarf

    def test_parse_dwarf_metadata_shim(self, tmp_path):
        """parse_dwarf_metadata delegates to parse_dwarf and returns DwarfMetadata."""
        from abicheck.dwarf_metadata import DwarfMetadata
        from abicheck.dwarf_unified import parse_dwarf_metadata

        fake_path = tmp_path / "fake.so"
        fake_path.write_bytes(b"\x00")

        fake_stat = MagicMock()
        fake_stat.st_mode = stat.S_IFREG | 0o644

        with (
            patch("abicheck.dwarf_unified.os.fstat", return_value=fake_stat),
            patch(
                "abicheck.dwarf_unified.ELFFile",
                side_effect=ELFError("bad"),
            ),
        ):
            result = parse_dwarf_metadata(fake_path)

        assert isinstance(result, DwarfMetadata)

    def test_parse_advanced_dwarf_shim(self, tmp_path):
        """parse_advanced_dwarf delegates to parse_dwarf and returns AdvancedDwarfMetadata."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata
        from abicheck.dwarf_unified import parse_advanced_dwarf

        fake_path = tmp_path / "fake.so"
        fake_path.write_bytes(b"\x00")

        fake_stat = MagicMock()
        fake_stat.st_mode = stat.S_IFREG | 0o644

        with (
            patch("abicheck.dwarf_unified.os.fstat", return_value=fake_stat),
            patch(
                "abicheck.dwarf_unified.ELFFile",
                side_effect=ELFError("bad"),
            ),
        ):
            result = parse_advanced_dwarf(fake_path)

        assert isinstance(result, AdvancedDwarfMetadata)


# ===========================================================================
# dwarf_advanced tests
# ===========================================================================

class TestParseAdvancedDwarf:
    """parse_advanced_dwarf edge cases (standalone function)."""

    def test_no_dwarf_info(self, tmp_path):
        """Line 161-162: ELF with no DWARF info returns empty metadata."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, parse_advanced_dwarf

        fake_path = tmp_path / "nodwarf.so"
        fake_path.write_bytes(b"\x00")

        mock_elf = MagicMock()
        # Strict DWARF check: no .debug_info / .zdebug_info section present.
        mock_elf.get_section_by_name.return_value = None

        with patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf):
            result = parse_advanced_dwarf(fake_path)

        assert isinstance(result, AdvancedDwarfMetadata)
        assert not result.has_dwarf

    def test_elf_open_error(self, tmp_path):
        """Lines 173-175: ELFFile raises => returns empty metadata."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, parse_advanced_dwarf

        fake_path = tmp_path / "bad.so"
        fake_path.write_bytes(b"\x00")

        with patch("abicheck.dwarf_advanced.ELFFile", side_effect=ELFError("nope")):
            result = parse_advanced_dwarf(fake_path)

        assert isinstance(result, AdvancedDwarfMetadata)
        assert not result.has_dwarf

    def test_cu_processing_valueerror_skipped(self, tmp_path):
        """Lines 166-169: CU processing that raises ValueError is skipped."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, parse_advanced_dwarf

        fake_path = tmp_path / "bad_cu.so"
        fake_path.write_bytes(b"\x00")

        mock_cu = MagicMock()
        mock_elf = MagicMock()
        mock_elf.has_dwarf_info.return_value = True
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [mock_cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        with (
            patch("abicheck.dwarf_advanced.ELFFile", return_value=mock_elf),
            patch(
                "abicheck.dwarf_advanced._process_cu",
                side_effect=ValueError("bad CU"),
            ),
            patch("abicheck.dwarf_advanced._parse_frame_registers"),
        ):
            result = parse_advanced_dwarf(fake_path)

        assert isinstance(result, AdvancedDwarfMetadata)
        assert result.has_dwarf  # has_dwarf=True even though CU was skipped


class TestGetTypeAlign:
    """_get_type_align edge cases (lines 207-254)."""

    def test_no_type_attribute(self):
        """Line 208: member DIE has no DW_AT_type => returns 0."""
        from abicheck.dwarf_advanced import _get_type_align

        member = MockDIE(tag="DW_TAG_member", attributes={})
        cu = MockCU()
        assert _get_type_align(member, cu) == 0

    def test_typedef_chain_missing_type(self):
        """Line 226: typedef with no DW_AT_type in chain => returns 0."""
        from abicheck.dwarf_advanced import _get_type_align

        # The resolved type is a typedef that has no DW_AT_type
        typedef_die = MockDIE(
            tag="DW_TAG_typedef",
            attributes={},  # no DW_AT_type
            offset=200,
        )
        cu = MockCU(cu_offset=0, die_map={200: typedef_die})
        member = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_type": MockAttr(200, form="DW_FORM_ref4")},
        )
        assert _get_type_align(member, cu) == 0

    def test_composite_type_returns_zero(self):
        """Lines 252-253: struct/array type => alignment cannot be inferred => 0."""
        from abicheck.dwarf_advanced import _get_type_align

        struct_die = MockDIE(
            tag="DW_TAG_structure_type",
            attributes={"DW_AT_byte_size": MockAttr(16)},
            offset=300,
        )
        cu = MockCU(cu_offset=0, die_map={300: struct_die})
        member = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_type": MockAttr(300, form="DW_FORM_ref4")},
        )
        assert _get_type_align(member, cu) == 0

    def test_exception_returns_zero(self):
        """Line 254: any exception during type resolution => returns 0."""
        from abicheck.dwarf_advanced import _get_type_align

        cu = MockCU(cu_offset=0)
        cu.get_DIE_from_refaddr = MagicMock(side_effect=KeyError("missing"))
        member = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_type": MockAttr(999, form="DW_FORM_ref4")},
        )
        assert _get_type_align(member, cu) == 0

    def test_base_type_alignment(self):
        """Primitive type: alignment == byte_size."""
        from abicheck.dwarf_advanced import _get_type_align

        base_die = MockDIE(
            tag="DW_TAG_base_type",
            attributes={"DW_AT_byte_size": MockAttr(4)},
            offset=100,
        )
        cu = MockCU(cu_offset=0, die_map={100: base_die})
        member = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_type": MockAttr(100, form="DW_FORM_ref4")},
        )
        assert _get_type_align(member, cu) == 4

    def test_dwarf5_alignment_attribute(self):
        """DW_AT_alignment on type DIE takes priority."""
        from abicheck.dwarf_advanced import _get_type_align

        type_die = MockDIE(
            tag="DW_TAG_base_type",
            attributes={
                "DW_AT_byte_size": MockAttr(4),
                "DW_AT_alignment": MockAttr(8),
            },
            offset=100,
        )
        cu = MockCU(cu_offset=0, die_map={100: type_die})
        member = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_type": MockAttr(100, form="DW_FORM_ref4")},
        )
        assert _get_type_align(member, cu) == 8


class TestWalkCu:
    """_walk_cu edge cases (lines 272-297)."""

    def test_prune_tags_skipped(self):
        """Line 273: prune tags are skipped and not descended into."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _walk_cu

        lexical = MockDIE(tag="DW_TAG_lexical_block")
        inlined = MockDIE(tag="DW_TAG_inlined_subroutine")
        call_site = MockDIE(tag="DW_TAG_GNU_call_site")
        root = MockDIE(
            tag="DW_TAG_compile_unit",
            children=[lexical, inlined, call_site],
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _walk_cu(root, meta, cu)
        # Should not crash and no data extracted from pruned tags
        assert len(meta.calling_conventions) == 0


class TestExtractCallingConvention:
    """_extract_calling_convention edge cases."""

    def test_not_external(self):
        """Line 474: non-external function is skipped."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _extract_calling_convention,
        )

        die = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={"DW_AT_name": MockAttr(b"internal_func")},
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _extract_calling_convention(die, meta, cu)
        assert len(meta.calling_conventions) == 0

    def test_no_name(self):
        """Line 483: function with no name is skipped."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _extract_calling_convention,
        )

        die = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={"DW_AT_external": MockAttr(1)},
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _extract_calling_convention(die, meta, cu)
        assert len(meta.calling_conventions) == 0

    def test_explicit_calling_convention(self):
        """Lines 484-489: explicit DW_AT_calling_convention is recorded."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _extract_calling_convention,
        )

        die = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={
                "DW_AT_external": MockAttr(1),
                "DW_AT_name": MockAttr(b"my_func"),
                "DW_AT_calling_convention": MockAttr(0x01),
            },
            children=[],
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _extract_calling_convention(die, meta, cu)
        assert meta.calling_conventions["my_func"] == "normal"

    def test_default_normal_cc(self):
        """Lines 487-488: no DW_AT_calling_convention defaults to 'normal'."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _extract_calling_convention,
        )

        die = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={
                "DW_AT_external": MockAttr(1),
                "DW_AT_name": MockAttr(b"plain_func"),
            },
            children=[],
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _extract_calling_convention(die, meta, cu)
        assert meta.calling_conventions["plain_func"] == "normal"

    def test_value_abi_trait_recorded(self):
        """Lines 492-505: value-ABI trait is recorded for aggregate return types."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _extract_calling_convention,
        )

        # Create a struct type DIE that is trivial (no children)
        struct_die = MockDIE(
            tag="DW_TAG_structure_type",
            attributes={"DW_AT_name": MockAttr(b"Trivial")},
            offset=200,
        )
        cu = MockCU(cu_offset=0, die_map={200: struct_die})

        die = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={
                "DW_AT_external": MockAttr(1),
                "DW_AT_name": MockAttr(b"returns_struct"),
                "DW_AT_type": MockAttr(200, form="DW_FORM_ref4"),
            },
            children=[],
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        _extract_calling_convention(die, meta, cu)
        assert "returns_struct" in meta.calling_conventions
        assert "returns_struct" in meta.value_abi_traits
        assert "ret:trivial" in meta.value_abi_traits["returns_struct"]

    def test_return_value_size_recorded(self) -> None:
        """The by-value aggregate return size is captured for the sret gate."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _aggregate_byte_size_for_typed_die,
            _extract_calling_convention,
        )

        struct_die = MockDIE(
            tag="DW_TAG_structure_type",
            attributes={
                "DW_AT_name": MockAttr(b"Big"),
                "DW_AT_byte_size": MockAttr(24),
            },
            offset=400,
        )
        cu = MockCU(cu_offset=0, die_map={400: struct_die})
        die = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={
                "DW_AT_external": MockAttr(1),
                "DW_AT_name": MockAttr(b"big_ret"),
                "DW_AT_type": MockAttr(400, form="DW_FORM_ref4"),
            },
            children=[],
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        _extract_calling_convention(die, meta, cu)
        assert meta.return_value_sizes.get("big_ret") == 24

        # The helper returns None for a pointer (not a by-value aggregate).
        ptr_die = MockDIE(tag="DW_TAG_pointer_type", attributes={}, offset=401)
        ptr_cu = MockCU(cu_offset=0, die_map={401: ptr_die})
        ptr_fn = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={"DW_AT_type": MockAttr(401, form="DW_FORM_ref4")},
            children=[],
        )
        assert _aggregate_byte_size_for_typed_die(ptr_fn, ptr_cu) is None

    def test_param_value_abi_trait(self):
        """Lines 497-502: parameter value-ABI trait for aggregate param."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _extract_calling_convention,
        )

        struct_die = MockDIE(
            tag="DW_TAG_class_type",
            attributes={"DW_AT_name": MockAttr(b"Widget")},
            offset=300,
        )
        param_die = MockDIE(
            tag="DW_TAG_formal_parameter",
            attributes={"DW_AT_type": MockAttr(300, form="DW_FORM_ref4")},
        )
        cu = MockCU(cu_offset=0, die_map={300: struct_die})

        die = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={
                "DW_AT_external": MockAttr(1),
                "DW_AT_name": MockAttr(b"takes_widget"),
            },
            children=[param_die],
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        _extract_calling_convention(die, meta, cu)
        assert "takes_widget" in meta.value_abi_traits
        assert "p0:trivial" in meta.value_abi_traits["takes_widget"]


class TestCheckPackedTypedef:
    """_check_packed_typedef edge cases (lines 519-535)."""

    def test_no_typedef_name(self):
        """Line 520: typedef with no name is skipped."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _check_packed_typedef

        die = MockDIE(tag="DW_TAG_typedef", attributes={})
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _check_packed_typedef(die, meta, cu)
        assert len(meta.packed_structs) == 0

    def test_no_type_attribute(self):
        """Line 520: typedef with name but no DW_AT_type is skipped."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _check_packed_typedef

        die = MockDIE(
            tag="DW_TAG_typedef",
            attributes={"DW_AT_name": MockAttr(b"MyType")},
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _check_packed_typedef(die, meta, cu)
        assert len(meta.packed_structs) == 0

    def test_resolve_exception(self):
        """Lines 527-528: exception during type resolution => return early."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _check_packed_typedef

        cu = MockCU(cu_offset=0)
        cu.get_DIE_from_refaddr = MagicMock(side_effect=KeyError("bad ref"))
        die = MockDIE(
            tag="DW_TAG_typedef",
            attributes={
                "DW_AT_name": MockAttr(b"PackedThing"),
                "DW_AT_type": MockAttr(999, form="DW_FORM_ref4"),
            },
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        _check_packed_typedef(die, meta, cu)
        assert len(meta.packed_structs) == 0

    def test_named_target_struct(self):
        """Line 535: named struct target => skip (handled under its own name)."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _check_packed_typedef

        target = MockDIE(
            tag="DW_TAG_structure_type",
            attributes={"DW_AT_name": MockAttr(b"NamedStruct")},
            offset=100,
        )
        cu = MockCU(cu_offset=0, die_map={100: target})
        die = MockDIE(
            tag="DW_TAG_typedef",
            attributes={
                "DW_AT_name": MockAttr(b"TypedefAlias"),
                "DW_AT_type": MockAttr(100, form="DW_FORM_ref4"),
            },
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        _check_packed_typedef(die, meta, cu)
        # Named struct target is skipped
        assert len(meta.packed_structs) == 0


class TestCheckPacked:
    """_check_packed edge cases (lines 553-584)."""

    def test_no_name(self):
        """Line 554-555: anonymous struct is skipped."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _check_packed

        die = MockDIE(tag="DW_TAG_structure_type", attributes={})
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _check_packed(die, meta, cu, override_name=None)
        assert len(meta.packed_structs) == 0

    def test_zero_byte_size(self):
        """Lines 557-558: forward declaration (byte_size == 0) is skipped."""
        from abicheck.dwarf_advanced import AdvancedDwarfMetadata, _check_packed

        die = MockDIE(
            tag="DW_TAG_structure_type",
            attributes={
                "DW_AT_name": MockAttr(b"FwdDecl"),
                "DW_AT_byte_size": MockAttr(0),
            },
        )
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        cu = MockCU()
        _check_packed(die, meta, cu, override_name=None)
        assert "FwdDecl" not in meta.packed_structs


class TestDecodeMemberLocation:
    """_decode_member_location edge cases (lines 600-615)."""

    def test_no_location(self):
        """Line 601: missing DW_AT_data_member_location => 0."""
        from abicheck.dwarf_advanced import _decode_member_location

        die = MockDIE(tag="DW_TAG_member", attributes={})
        assert _decode_member_location(die) == 0

    def test_int_location(self):
        """Line 603-604: integer value returned directly."""
        from abicheck.dwarf_advanced import _decode_member_location

        die = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_data_member_location": MockAttr(16)},
        )
        assert _decode_member_location(die) == 16

    def test_loc_expr_plus_uconst(self):
        """DW_OP_plus_uconst location expression via shared decoder."""
        from abicheck.dwarf_advanced import _decode_member_location

        # Shared decoder handles tuples: (opcode, operand)
        die = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_data_member_location": MockAttr([(0x23, 24)])},
        )
        assert _decode_member_location(die) == 24

    def test_loc_expr_raw_ints(self):
        """DW_OP_plus_uconst as raw integers via shared decoder."""
        from abicheck.dwarf_advanced import _decode_member_location

        die = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_data_member_location": MockAttr([0x23, 32])},
        )
        assert _decode_member_location(die) == 32

    def test_multi_op_expression(self):
        """Multi-op expression handled by shared stack machine."""
        from abicheck.dwarf_advanced import _decode_member_location

        # DW_OP_constu 8 + DW_OP_constu 4 + DW_OP_plus => 12
        die = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_data_member_location": MockAttr(
                [(0x10, 8), (0x10, 4), (0x22, 0)]
            )},
        )
        assert _decode_member_location(die) == 12


class TestUnwrapQualifiers:
    """_unwrap_qualifiers edge cases (lines 395-424)."""

    def test_unwrap_const_typedef(self):
        """Follow const -> typedef -> base_type."""
        from abicheck.dwarf_advanced import _unwrap_qualifiers

        base = MockDIE(
            tag="DW_TAG_base_type",
            attributes={"DW_AT_name": MockAttr(b"int")},
            offset=100,
        )
        typedef = MockDIE(
            tag="DW_TAG_typedef",
            attributes={
                "DW_AT_name": MockAttr(b"myint"),
                "DW_AT_type": MockAttr(100, form="DW_FORM_ref4"),
            },
            offset=200,
        )
        const = MockDIE(
            tag="DW_TAG_const_type",
            attributes={"DW_AT_type": MockAttr(200, form="DW_FORM_ref4")},
            offset=300,
        )
        cu = MockCU(cu_offset=0, die_map={100: base, 200: typedef, 300: const})
        result = _unwrap_qualifiers(const, cu)
        assert result.tag == "DW_TAG_base_type"

    def test_unwrap_stops_at_none(self):
        """Line 412: resolve returns None => break."""
        from abicheck.dwarf_advanced import _unwrap_qualifiers

        # Typedef with no DW_AT_type
        typedef = MockDIE(
            tag="DW_TAG_typedef",
            attributes={},
            offset=100,
        )
        cu = MockCU(cu_offset=0, die_map={100: typedef})
        result = _unwrap_qualifiers(typedef, cu)
        assert result.tag == "DW_TAG_typedef"

    def test_unwrap_depth_limit(self):
        """Lines 417-420: depth limit reached logs and returns."""
        from abicheck.dwarf_advanced import _unwrap_qualifiers

        # Create a chain of 13 typedefs (exceeds limit of 12)
        dies: dict[int, MockDIE] = {}
        for i in range(13):
            offset = (i + 1) * 100
            next_offset = (i + 2) * 100
            if i < 12:
                dies[offset] = MockDIE(
                    tag="DW_TAG_typedef",
                    attributes={"DW_AT_type": MockAttr(next_offset, form="DW_FORM_ref4")},
                    offset=offset,
                )
            else:
                dies[offset] = MockDIE(
                    tag="DW_TAG_typedef",
                    attributes={},
                    offset=offset,
                )
        cu = MockCU(cu_offset=0, die_map=dies)
        first_die = dies[100]
        result = _unwrap_qualifiers(first_die, cu)
        # Should return some typedef DIE without crashing
        assert result is not None


class TestIsNontrivialAggregate:
    """_is_nontrivial_aggregate edge cases (lines 314-392)."""

    def test_non_struct_tag(self):
        """Line 338: non-struct/class/union tag => trivial."""
        from abicheck.dwarf_advanced import _is_nontrivial_aggregate

        die = MockDIE(tag="DW_TAG_base_type", offset=10)
        assert _is_nontrivial_aggregate(die) is False

    def test_cache_hit(self):
        """Lines 332-334: cached result returned."""
        from abicheck.dwarf_advanced import _is_nontrivial_aggregate

        die = MockDIE(tag="DW_TAG_structure_type", offset=42)
        cache: dict[int, bool] = {42: True}
        assert _is_nontrivial_aggregate(die, cache=cache) is True

    def test_inheritance_nontrivial(self):
        """Line 352: DW_TAG_inheritance => nontrivial."""
        from abicheck.dwarf_advanced import _is_nontrivial_aggregate

        inheritance = MockDIE(tag="DW_TAG_inheritance")
        struct = MockDIE(
            tag="DW_TAG_structure_type",
            attributes={"DW_AT_name": MockAttr(b"Derived")},
            offset=50,
            children=[inheritance],
        )
        assert _is_nontrivial_aggregate(struct) is True

    def test_user_defined_destructor(self):
        """Lines 380-382: user-defined destructor => nontrivial."""
        from abicheck.dwarf_advanced import _is_nontrivial_aggregate

        dtor = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={"DW_AT_name": MockAttr(b"~MyClass")},
            offset=60,
        )
        struct = MockDIE(
            tag="DW_TAG_class_type",
            attributes={"DW_AT_name": MockAttr(b"MyClass")},
            offset=50,
            children=[dtor],
        )
        assert _is_nontrivial_aggregate(struct) is True

    def test_member_type_nontrivial(self):
        """Lines 356-364: member whose type is nontrivial => struct is nontrivial."""
        from abicheck.dwarf_advanced import _is_nontrivial_aggregate

        # Inner struct with a destructor => nontrivial
        inner_dtor = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={"DW_AT_name": MockAttr(b"~Inner")},
            offset=70,
        )
        inner_struct = MockDIE(
            tag="DW_TAG_structure_type",
            attributes={"DW_AT_name": MockAttr(b"Inner")},
            offset=200,
            children=[inner_dtor],
        )

        # Outer struct has a member of type Inner
        member = MockDIE(
            tag="DW_TAG_member",
            attributes={"DW_AT_type": MockAttr(200, form="DW_FORM_ref4")},
        )
        cu = MockCU(cu_offset=0, die_map={200: inner_struct})

        outer = MockDIE(
            tag="DW_TAG_structure_type",
            attributes={"DW_AT_name": MockAttr(b"Outer")},
            offset=100,
            children=[member],
        )
        assert _is_nontrivial_aggregate(outer, CU=cu) is True


class TestParseProducer:
    """_parse_producer edge cases (lines 773-796)."""

    def test_gcc_producer(self):
        from abicheck.dwarf_advanced import _parse_producer

        info = _parse_producer("GNU C17 13.2.1 20240210 -fshort-enums -m64")
        assert info.compiler == "GCC"
        assert info.version == "13.2.1"
        assert "-fshort-enums" in info.abi_flags
        assert "-m64" in info.abi_flags

    def test_clang_producer(self):
        from abicheck.dwarf_advanced import _parse_producer

        info = _parse_producer("clang version 17.0.6 -fpack-struct=4")
        assert info.compiler == "clang"
        assert info.version == "17.0.6"
        assert "-fpack-struct=4" in info.abi_flags

    def test_intel_producer(self):
        """Lines 787-792: Intel compiler (ICC) recognized."""
        from abicheck.dwarf_advanced import _parse_producer

        info = _parse_producer("Intel ICC 2024.1.0")
        assert info.compiler == "ICC"
        assert info.version == "2024.1.0"

    def test_unknown_producer(self):
        from abicheck.dwarf_advanced import _parse_producer

        info = _parse_producer("some-unknown-compiler 1.0")
        assert info.compiler == ""
        assert info.producer_string == "some-unknown-compiler 1.0"


class TestParseFrameRegisters:
    """_parse_frame_registers edge cases (lines 748-770)."""

    def test_no_cfi_source(self):
        """Lines 752-753: no CFI source => return early."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _parse_frame_registers,
        )

        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        mock_elf.get_section_by_name.return_value = None

        mock_dwarf = MagicMock()
        mock_dwarf.get_EH_CFI_entries.return_value = None
        mock_dwarf.get_CFI_entries.return_value = None

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        _parse_frame_registers(mock_elf, mock_dwarf, meta)
        assert len(meta.frame_registers) == 0

    def test_fde_no_symbol(self):
        """Lines 761-762: FDE with no matching symbol is skipped."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _parse_frame_registers,
        )

        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        mock_elf.get_section_by_name.return_value = None

        fde = MagicMock()
        fde.__class__ = type("FDE", (), {})
        fde.__class__.__name__ = "FDE"
        fde.__getitem__ = MagicMock(return_value=0x1000)

        mock_dwarf = MagicMock()
        mock_dwarf.get_EH_CFI_entries.return_value = [fde]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        _parse_frame_registers(mock_elf, mock_dwarf, meta)
        assert len(meta.frame_registers) == 0

    def test_outer_exception(self):
        """Lines 769-770: outer exception is caught and logged."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _parse_frame_registers,
        )

        mock_elf = MagicMock()
        mock_elf.get_machine_arch.side_effect = ELFError("arch fail")

        mock_dwarf = MagicMock()
        meta = AdvancedDwarfMetadata(has_dwarf=True)
        # Should not raise
        _parse_frame_registers(mock_elf, mock_dwarf, meta)
        assert len(meta.frame_registers) == 0

    def test_fde_inner_exception(self):
        """Lines 766-767: inner FDE exception is caught per-entry."""
        from abicheck.dwarf_advanced import (
            AdvancedDwarfMetadata,
            _parse_frame_registers,
        )

        mock_elf = MagicMock()
        mock_elf.get_machine_arch.return_value = "x64"
        mock_elf.get_section_by_name.return_value = None

        fde = MagicMock()
        fde.__class__ = type("FDE", (), {})
        fde.__class__.__name__ = "FDE"
        fde.__getitem__ = MagicMock(side_effect=KeyError("no initial_location"))

        mock_dwarf = MagicMock()
        mock_dwarf.get_EH_CFI_entries.return_value = [fde]

        meta = AdvancedDwarfMetadata(has_dwarf=True)
        _parse_frame_registers(mock_elf, mock_dwarf, meta)
        assert len(meta.frame_registers) == 0


class TestExtractCfaReg:
    """_extract_cfa_reg_from_fde edge cases."""

    def test_empty_table(self):
        from abicheck.dwarf_advanced import _extract_cfa_reg_from_fde

        entry = MagicMock()
        decoded = MagicMock()
        decoded.table = []
        entry.get_decoded.return_value = decoded
        assert _extract_cfa_reg_from_fde(entry, "x64") is None

    def test_no_cfa_in_rows(self):
        from abicheck.dwarf_advanced import _extract_cfa_reg_from_fde

        entry = MagicMock()
        decoded = MagicMock()
        row = {"pc": 0x1000}  # no 'cfa' key
        decoded.table = [row]
        entry.get_decoded.return_value = decoded
        assert _extract_cfa_reg_from_fde(entry, "x64") is None

    def test_exception_returns_none(self):
        from abicheck.dwarf_advanced import _extract_cfa_reg_from_fde

        entry = MagicMock()
        entry.get_decoded.side_effect = ELFError("bad decode")
        assert _extract_cfa_reg_from_fde(entry, "x64") is None


class TestRegName:
    """_reg_name edge cases."""

    def test_x86_reg(self):
        from abicheck.dwarf_advanced import _reg_name
        assert _reg_name(5, "x86") == "ebp"

    def test_aarch64_reg(self):
        from abicheck.dwarf_advanced import _reg_name
        assert _reg_name(31, "aarch64") == "sp"

    def test_unknown_arch(self):
        from abicheck.dwarf_advanced import _reg_name
        assert _reg_name(99, "mips") == "reg99"


# ===========================================================================
# dwarf_snapshot tests
# ===========================================================================

class TestDwarfSnapshotFallbacks:
    """dwarf_snapshot.py: uncovered branches for missing attributes, fallback paths."""

    def _make_elf_meta(self):
        """Create a minimal ElfMetadata mock with exported symbols."""
        meta = MagicMock()
        sym = MagicMock()
        sym.name = "exported_func"
        sym.visibility = "default"
        sym2 = MagicMock()
        sym2.name = "exported_var"
        sym2.visibility = "default"
        meta.symbols = [sym, sym2]
        meta.soname = "libtest.so.1"
        return meta

    def test_show_data_sources_no_elf(self):
        """show_data_sources with no ELF metadata."""
        from abicheck.dwarf_snapshot import show_data_sources

        result = show_data_sources(
            Path("test.so"), None, None, has_headers=False,
        )
        assert "not available" in result
        assert "Symbols-only" in result

    def test_show_data_sources_with_dwarf(self):
        """show_data_sources with DWARF metadata."""
        from abicheck.dwarf_snapshot import show_data_sources

        dwarf_meta = MagicMock()
        dwarf_meta.has_dwarf = True
        dwarf_meta.structs = {"S": {}}
        dwarf_meta.enums = {"E": {}}

        elf_meta = self._make_elf_meta()

        result = show_data_sources(
            Path("test.so"), elf_meta, dwarf_meta, has_headers=False,
        )
        assert "DWARF-only" in result
        assert "DWARF" in result

    def test_show_data_sources_with_headers(self):
        """show_data_sources with headers available."""
        from abicheck.dwarf_snapshot import show_data_sources

        result = show_data_sources(
            Path("test.so"), self._make_elf_meta(), None, has_headers=True,
        )
        assert "Headers mode" in result

    def test_evaluate_location_expr_tuple(self):
        """_evaluate_location_expr with tuple operands."""
        from abicheck.dwarf_utils import _evaluate_location_expr

        # DW_OP_plus_uconst as tuple
        result = _evaluate_location_expr([(0x23, 16)])
        assert result == 16

    def test_evaluate_location_expr_constu(self):
        """_evaluate_location_expr with DW_OP_constu."""
        from abicheck.dwarf_utils import _evaluate_location_expr

        result = _evaluate_location_expr([(0x10, 42)])
        assert result == 42

    def test_evaluate_location_expr_lit(self):
        """_evaluate_location_expr with DW_OP_lit0..31."""
        from abicheck.dwarf_utils import _evaluate_location_expr

        result = _evaluate_location_expr([(0x35, 0)])  # DW_OP_lit5
        assert result == 5

    def test_evaluate_location_expr_plus(self):
        """_evaluate_location_expr with DW_OP_plus."""
        from abicheck.dwarf_utils import _evaluate_location_expr

        # Push 10, then plus (adds to base 0)
        result = _evaluate_location_expr([(0x10, 10), (0x22, 0)])
        assert result == 10

    def test_evaluate_location_expr_raw_ints(self):
        """_evaluate_location_expr with raw integer opcodes."""
        from abicheck.dwarf_utils import _evaluate_location_expr

        # DW_OP_plus_uconst(0x23), 8
        result = _evaluate_location_expr([0x23, 8])
        assert result == 8

    def test_evaluate_location_expr_raw_constu(self):
        from abicheck.dwarf_utils import _evaluate_location_expr

        result = _evaluate_location_expr([0x10, 5])
        assert result == 5

    def test_evaluate_location_expr_raw_lit(self):
        from abicheck.dwarf_utils import _evaluate_location_expr

        # DW_OP_lit3 = 0x33
        result = _evaluate_location_expr([0x33])
        assert result == 3

    def test_evaluate_location_expr_empty(self):
        from abicheck.dwarf_utils import _evaluate_location_expr

        result = _evaluate_location_expr([])
        assert result == 0

    def test_strip_type_decorators(self):
        """_strip_type_decorators removes pointer/reference/const/volatile."""
        from abicheck.dwarf_snapshot import _strip_type_decorators

        assert _strip_type_decorators("const int *") == "int"
        assert _strip_type_decorators("volatile char &") == "char"
        assert _strip_type_decorators("int **") == "int"
        assert _strip_type_decorators("MyType[]") == "MyType"
        assert _strip_type_decorators("int &&") == "int"
        assert _strip_type_decorators("restrict const int") == "int"

    def test_builder_extract_elf_open_error(self, tmp_path):
        """Builder.extract handles ELFFile open error gracefully."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        fake_path = tmp_path / "bad.so"
        fake_path.write_bytes(b"\x00bad")

        elf_meta = self._make_elf_meta()

        with patch("abicheck.dwarf_snapshot.ELFFile", side_effect=ELFError("bad")):
            builder = _DwarfSnapshotBuilder(fake_path, elf_meta)
            builder.extract()

        assert builder.functions == []
        assert builder.variables == []
        assert builder.types == []

    def test_builder_extract_no_dwarf(self, tmp_path):
        """Builder.extract with ELF having no DWARF."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        fake_path = tmp_path / "nodwarf.so"
        fake_path.write_bytes(b"\x00")

        mock_elf = MagicMock()
        # Strict DWARF check: no .debug_info / .zdebug_info section present.
        mock_elf.get_section_by_name.return_value = None

        elf_meta = self._make_elf_meta()

        with patch("abicheck.dwarf_snapshot.ELFFile", return_value=mock_elf):
            builder = _DwarfSnapshotBuilder(fake_path, elf_meta)
            builder.extract()

        assert builder.functions == []

    def test_builder_cu_exception_skipped(self, tmp_path):
        """Builder._process_cu exception is caught and logged."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        fake_path = tmp_path / "bad_cu.so"
        fake_path.write_bytes(b"\x00")

        mock_cu = MagicMock()
        mock_cu.get_top_DIE.side_effect = RuntimeError("bad CU")

        mock_elf = MagicMock()
        mock_elf.has_dwarf_info.return_value = True
        mock_dwarf = MagicMock()
        mock_dwarf.iter_CUs.return_value = [mock_cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        elf_meta = self._make_elf_meta()

        with patch("abicheck.dwarf_snapshot.ELFFile", return_value=mock_elf):
            builder = _DwarfSnapshotBuilder(fake_path, elf_meta)
            builder.extract()

        assert builder.functions == []

    def test_process_param_no_type(self):
        """_process_param with no DW_AT_type returns Param(type='?')."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(
            tag="DW_TAG_formal_parameter",
            attributes={"DW_AT_name": MockAttr(b"x")},
        )
        cu = MockCU()
        param = builder._process_param(die, cu)
        assert param is not None
        assert param.type == "?"

    def test_process_variable_no_name(self):
        """_process_variable with no name is skipped."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(
            tag="DW_TAG_variable",
            attributes={"DW_AT_external": MockAttr(1)},
        )
        cu = MockCU()
        builder._process_variable(die, cu, "")
        assert builder.variables == []

    def test_process_record_type_anonymous(self):
        """_process_record_type with no name is skipped."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(tag="DW_TAG_structure_type", attributes={})
        cu = MockCU()
        builder._process_record_type(die, cu, "")
        assert builder.types == []

    def test_process_field_no_name(self):
        """_process_field with no name returns None."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(tag="DW_TAG_member", attributes={})
        cu = MockCU()
        result = builder._process_field(die, cu)
        assert result is None

    def test_process_enum_no_name(self):
        """_process_enum with no name is skipped."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(tag="DW_TAG_enumeration_type", attributes={})
        cu = MockCU()
        builder._process_enum(die, cu, "")
        assert builder.enums == []

    def test_resolve_base_name_no_type(self):
        """_resolve_base_name with no DW_AT_type returns ''."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(tag="DW_TAG_inheritance", attributes={})
        cu = MockCU()
        assert builder._resolve_base_name(die, cu) == ""

    def test_resolve_base_name_exception(self):
        """_resolve_base_name handles resolve exception."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        cu = MockCU(cu_offset=0)
        cu.get_DIE_from_refaddr = MagicMock(side_effect=KeyError("bad"))

        die = MockDIE(
            tag="DW_TAG_inheritance",
            attributes={"DW_AT_type": MockAttr(999, form="DW_FORM_ref4")},
        )
        assert builder._resolve_base_name(die, cu) == ""

    def test_resolve_type_no_type(self):
        """_resolve_type with no DW_AT_type returns ('void', 0)."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(tag="DW_TAG_subprogram", attributes={})
        cu = MockCU()
        name, size = builder._resolve_type(die, cu)
        assert name == "void"
        assert size == 0

    def test_resolve_type_exception(self):
        """_resolve_type handles resolution exception."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        cu = MockCU(cu_offset=0)
        cu.get_DIE_from_refaddr = MagicMock(side_effect=KeyError("bad"))

        die = MockDIE(
            tag="DW_TAG_subprogram",
            attributes={"DW_AT_type": MockAttr(999, form="DW_FORM_ref4")},
        )
        name, size = builder._resolve_type(die, cu)
        assert name == "?"
        assert size == 0

    def test_access_from_dwarf(self):
        """_access_from_dwarf static method."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder
        from abicheck.model import AccessLevel

        assert _DwarfSnapshotBuilder._access_from_dwarf(0) == AccessLevel.PUBLIC
        assert _DwarfSnapshotBuilder._access_from_dwarf(1) == AccessLevel.PUBLIC
        assert _DwarfSnapshotBuilder._access_from_dwarf(2) == AccessLevel.PROTECTED
        assert _DwarfSnapshotBuilder._access_from_dwarf(3) == AccessLevel.PRIVATE

    def test_compute_type_name_depth_limit(self):
        """_die_to_type_name with depth > 8 returns ('...', 0)."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(tag="DW_TAG_base_type", offset=100)
        cu = MockCU(cu_offset=0)
        name, size = builder._die_to_type_name(die, cu, depth=9)
        assert name == "..."
        assert size == 0

    def test_compute_type_name_enum(self):
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(
            tag="DW_TAG_enumeration_type",
            attributes={
                "DW_AT_name": MockAttr(b"Color"),
                "DW_AT_byte_size": MockAttr(4),
            },
            offset=100,
        )
        cu = MockCU(cu_offset=0)
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "enum Color"
        assert size == 4

    def test_compute_type_name_subroutine(self):
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(
            tag="DW_TAG_subroutine_type",
            attributes={"DW_AT_byte_size": MockAttr(0)},
            offset=100,
        )
        cu = MockCU(cu_offset=0)
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "fn(...)"

    def test_compute_type_name_fallback(self):
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(
            tag="DW_TAG_unspecified_type",
            attributes={"DW_AT_byte_size": MockAttr(0)},
            offset=100,
        )
        cu = MockCU(cu_offset=0)
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "DW_TAG_unspecified_type"

    def test_resolve_inner_info_no_type(self):
        """_resolve_inner_info with no DW_AT_type returns None."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(tag="DW_TAG_const_type", attributes={}, offset=100)
        cu = MockCU(cu_offset=0)
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "const"
        assert size == 0

    def test_pointer_type_no_inner(self):
        """Pointer type with no DW_AT_type => 'void *'."""
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        die = MockDIE(
            tag="DW_TAG_pointer_type",
            attributes={"DW_AT_byte_size": MockAttr(8)},
            offset=100,
        )
        cu = MockCU(cu_offset=0)
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "void *"
        assert size == 8

    def test_reference_type(self):
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        inner = MockDIE(
            tag="DW_TAG_base_type",
            attributes={
                "DW_AT_name": MockAttr(b"int"),
                "DW_AT_byte_size": MockAttr(4),
            },
            offset=50,
        )
        die = MockDIE(
            tag="DW_TAG_reference_type",
            attributes={
                "DW_AT_type": MockAttr(50, form="DW_FORM_ref4"),
                "DW_AT_byte_size": MockAttr(8),
            },
            offset=100,
        )
        cu = MockCU(cu_offset=0, die_map={50: inner})
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "int &"

    def test_rvalue_reference_type(self):
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        inner = MockDIE(
            tag="DW_TAG_base_type",
            attributes={
                "DW_AT_name": MockAttr(b"int"),
                "DW_AT_byte_size": MockAttr(4),
            },
            offset=50,
        )
        die = MockDIE(
            tag="DW_TAG_rvalue_reference_type",
            attributes={
                "DW_AT_type": MockAttr(50, form="DW_FORM_ref4"),
                "DW_AT_byte_size": MockAttr(8),
            },
            offset=100,
        )
        cu = MockCU(cu_offset=0, die_map={50: inner})
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "int &&"

    def test_typedef_type(self):
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        inner = MockDIE(
            tag="DW_TAG_base_type",
            attributes={
                "DW_AT_name": MockAttr(b"int"),
                "DW_AT_byte_size": MockAttr(4),
            },
            offset=50,
        )
        die = MockDIE(
            tag="DW_TAG_typedef",
            attributes={
                "DW_AT_name": MockAttr(b"myint"),
                "DW_AT_type": MockAttr(50, form="DW_FORM_ref4"),
            },
            offset=100,
        )
        cu = MockCU(cu_offset=0, die_map={50: inner})
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "myint"

    def test_array_type(self):
        from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder

        elf_meta = self._make_elf_meta()
        builder = _DwarfSnapshotBuilder(Path("test.so"), elf_meta)

        inner = MockDIE(
            tag="DW_TAG_base_type",
            attributes={
                "DW_AT_name": MockAttr(b"int"),
                "DW_AT_byte_size": MockAttr(4),
            },
            offset=50,
        )
        die = MockDIE(
            tag="DW_TAG_array_type",
            attributes={
                "DW_AT_type": MockAttr(50, form="DW_FORM_ref4"),
                "DW_AT_byte_size": MockAttr(40),
            },
            offset=100,
        )
        cu = MockCU(cu_offset=0, die_map={50: inner})
        name, size = builder._die_to_type_name(die, cu, depth=0)
        assert name == "int[]"
        assert size == 40


class TestAggregateHasUnalignedMemberNested:
    """_aggregate_has_unaligned_member carries the parent offset into nested aggregates."""

    def _double(self) -> MockDIE:
        return MockDIE(tag="DW_TAG_base_type",
                       attributes={"DW_AT_byte_size": MockAttr(8)}, offset=100)

    def _char(self) -> MockDIE:
        return MockDIE(tag="DW_TAG_base_type",
                       attributes={"DW_AT_byte_size": MockAttr(1)}, offset=108)

    def _inner(self) -> MockDIE:
        # struct Inner { double d; }  — d at offset 0 within Inner
        member_d = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(100, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(0),
            },
            offset=210,
        )
        return MockDIE(tag="DW_TAG_structure_type",
                       attributes={"DW_AT_byte_size": MockAttr(8)},
                       offset=200, children=[member_d])

    def _wrapper(self, struct_offset: int) -> MockDIE:
        # A DIE whose DW_AT_type is the (return) aggregate.
        return MockDIE(tag="DW_TAG_subprogram",
                       attributes={"DW_AT_type": MockAttr(struct_offset, form="DW_FORM_ref4")})

    def test_packed_outer_misaligns_nested_double(self):
        from abicheck.dwarf_advanced import _aggregate_has_unaligned_member

        # struct __attribute__((packed)) Outer { char c; Inner i; };
        # c at 0, i at offset 1 -> i.d lands at absolute offset 1 (misaligned for 8-byte double).
        member_c = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(108, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(0),
            },
            offset=300,
        )
        member_i = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(200, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(1),
            },
            offset=310,
        )
        outer = MockDIE(tag="DW_TAG_structure_type",
                        attributes={"DW_AT_byte_size": MockAttr(9)},
                        offset=320, children=[member_c, member_i])
        cu = MockCU(cu_offset=0, die_map={100: self._double(), 108: self._char(),
                                          200: self._inner(), 320: outer})
        assert _aggregate_has_unaligned_member(self._wrapper(320), cu) is True

    def test_aligned_outer_with_nested_aggregate_is_not_unaligned(self):
        from abicheck.dwarf_advanced import _aggregate_has_unaligned_member

        # struct Outer { Inner i; char c; };  — i at offset 0, properly aligned.
        member_i = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(200, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(0),
            },
            offset=330,
        )
        member_c = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(108, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(8),
            },
            offset=340,
        )
        outer = MockDIE(tag="DW_TAG_structure_type",
                        attributes={"DW_AT_byte_size": MockAttr(16)},
                        offset=350, children=[member_i, member_c])
        cu = MockCU(cu_offset=0, die_map={100: self._double(), 108: self._char(),
                                          200: self._inner(), 350: outer})
        assert _aggregate_has_unaligned_member(self._wrapper(350), cu) is False

    def _int(self) -> MockDIE:
        return MockDIE(tag="DW_TAG_base_type",
                       attributes={"DW_AT_byte_size": MockAttr(4)}, offset=120)

    def test_packed_outer_misaligns_array_element(self):
        from abicheck.dwarf_advanced import _aggregate_has_unaligned_member

        # struct __attribute__((packed)) R { char c; int a[1]; };
        # a (int[1]) at offset 1 -> a[0] is a 4-byte int at a misaligned offset.
        int_array = MockDIE(
            tag="DW_TAG_array_type",
            attributes={"DW_AT_type": MockAttr(120, form="DW_FORM_ref4")},
            offset=400,
        )
        member_c = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(108, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(0),
            },
            offset=410,
        )
        member_a = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(400, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(1),
            },
            offset=420,
        )
        outer = MockDIE(tag="DW_TAG_structure_type",
                        attributes={"DW_AT_byte_size": MockAttr(5)},
                        offset=430, children=[member_c, member_a])
        cu = MockCU(cu_offset=0, die_map={108: self._char(), 120: self._int(),
                                          400: int_array, 430: outer})
        assert _aggregate_has_unaligned_member(self._wrapper(430), cu) is True

    def test_aligned_array_element_is_not_unaligned(self):
        from abicheck.dwarf_advanced import _aggregate_has_unaligned_member

        # struct S { int a[2]; char c; };  — a at offset 0, properly aligned.
        int_array = MockDIE(
            tag="DW_TAG_array_type",
            attributes={"DW_AT_type": MockAttr(120, form="DW_FORM_ref4")},
            offset=440,
        )
        member_a = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(440, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(0),
            },
            offset=450,
        )
        member_c = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(108, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(8),
            },
            offset=460,
        )
        outer = MockDIE(tag="DW_TAG_structure_type",
                        attributes={"DW_AT_byte_size": MockAttr(12)},
                        offset=470, children=[member_a, member_c])
        cu = MockCU(cu_offset=0, die_map={108: self._char(), 120: self._int(),
                                          440: int_array, 470: outer})
        assert _aggregate_has_unaligned_member(self._wrapper(470), cu) is False

    def test_dw_at_alignment_override_misaligned(self):
        from abicheck.dwarf_advanced import _aggregate_has_unaligned_member

        # A member type that declares DW_AT_alignment=8 (DWARF 5) placed at a
        # misaligned offset is caught via the alignment override, not byte size.
        over = MockDIE(
            tag="DW_TAG_base_type",
            attributes={"DW_AT_byte_size": MockAttr(1), "DW_AT_alignment": MockAttr(8)},
            offset=500,
        )
        member_c = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(108, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(0),
            },
            offset=510,
        )
        member_x = MockDIE(
            tag="DW_TAG_member",
            attributes={
                "DW_AT_type": MockAttr(500, form="DW_FORM_ref4"),
                "DW_AT_data_member_location": MockAttr(1),
            },
            offset=520,
        )
        outer = MockDIE(tag="DW_TAG_structure_type",
                        attributes={"DW_AT_byte_size": MockAttr(9)},
                        offset=530, children=[member_c, member_x])
        cu = MockCU(cu_offset=0, die_map={108: self._char(), 500: over, 530: outer})
        assert _aggregate_has_unaligned_member(self._wrapper(530), cu) is True

    def test_pointer_return_type_is_not_aggregate(self):
        from abicheck.dwarf_advanced import _aggregate_has_unaligned_member

        ptr = MockDIE(tag="DW_TAG_pointer_type",
                      attributes={"DW_AT_byte_size": MockAttr(8)}, offset=600)
        cu = MockCU(cu_offset=0, die_map={600: ptr})
        assert _aggregate_has_unaligned_member(self._wrapper(600), cu) is False

    def test_scalar_return_type_is_not_aggregate(self):
        from abicheck.dwarf_advanced import _aggregate_has_unaligned_member

        cu = MockCU(cu_offset=0, die_map={120: self._int()})
        assert _aggregate_has_unaligned_member(self._wrapper(120), cu) is False
