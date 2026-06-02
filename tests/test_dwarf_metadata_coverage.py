"""Coverage tests for dwarf_metadata.py — mock-based unit tests.

Covers parse_dwarf_metadata entry points, _parse, _process_cu error handling,
_walk_die_iter traversal, _process_struct with ODR, _process_member bitfields,
_process_enum, _process_typedef, _expand_anonymous_member, _compute_type_info
branches, _attr_str/_attr_int edge cases, and _compute_fallback_type_info.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from abicheck.dwarf_metadata import (
    DwarfMetadata,
    _attr_int,
    _attr_str,
    _compute_fallback_type_info,
    _compute_type_info,
    _expand_anonymous_member,
    _parse,
    _process_enum,
    _process_member,
    _process_struct,
    _process_typedef,
    _resolve_inner_type_info,
    _resolve_type,
    _walk_die_iter,
    parse_dwarf_metadata,
)

# ── Mock helpers ───────────────────────────────────────────────────────

class _Attr:
    def __init__(self, value, form="DW_FORM_ref4"):
        self.value = value
        self.form = form


class _Die:
    def __init__(self, tag, attrs=None, children=None, offset=0):
        self.tag = tag
        self.attributes = {k: _Attr(v) if not isinstance(v, _Attr) else v
                          for k, v in (attrs or {}).items()}
        self._children = list(children or [])
        self.offset = offset

    def iter_children(self):
        return iter(self._children)


class _CU:
    cu_offset = 0

    def __init__(self, top_die=None, offset=0):
        self._top = top_die
        self.cu_offset = offset

    def get_top_DIE(self):
        return self._top

    def get_DIE_from_refaddr(self, off):
        return getattr(self, '_die_map', {}).get(off)


# ── parse_dwarf_metadata entry point ──────────────────────────────────

class TestParseDwarfMetadata:
    def test_nonexistent_file_returns_empty(self, tmp_path):
        result = parse_dwarf_metadata(tmp_path / "nope.so")
        assert isinstance(result, DwarfMetadata)
        assert result.has_dwarf is False

    def test_non_regular_file_returns_empty(self, tmp_path):
        """Passing a directory-like path returns empty metadata."""
        # Create a named pipe or use a special file; for simplicity, mock os.fstat
        import stat

        f = tmp_path / "weird.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        with patch("abicheck.dwarf_metadata.os.fstat") as mock_fstat:
            mock_st = MagicMock()
            mock_st.st_mode = stat.S_IFIFO | 0o644  # pipe, not regular
            mock_fstat.return_value = mock_st
            result = parse_dwarf_metadata(f)
        assert result.has_dwarf is False

    def test_elf_error_returns_empty(self, tmp_path):
        f = tmp_path / "bad.so"
        f.write_bytes(b"not elf at all")
        result = parse_dwarf_metadata(f)
        assert result.has_dwarf is False


# ── _parse with no DWARF ─────────────────────────────────────────────

class TestParse:
    def test_no_dwarf_info(self):
        mock_elf = MagicMock()
        # Strict DWARF check: no .debug_info / .zdebug_info section present.
        mock_elf.get_section_by_name.return_value = None

        with patch("abicheck.dwarf_metadata.ELFFile", return_value=mock_elf):
            result = _parse(MagicMock(), Path("/fake.so"))
        assert result.has_dwarf is False

    def test_cu_exception_skipped(self):
        """When a CU raises, it is skipped and processing continues."""
        mock_elf = MagicMock()
        mock_elf.has_dwarf_info.return_value = True
        mock_dwarf = MagicMock()

        # CU that will cause an error
        bad_cu = MagicMock()
        bad_cu.get_top_DIE.side_effect = RuntimeError("corrupt CU")

        # Good CU with empty top DIE
        good_die = _Die("DW_TAG_compile_unit")
        good_cu = MagicMock()
        good_cu.cu_offset = 0
        good_cu.get_top_DIE.return_value = good_die

        mock_dwarf.iter_CUs.return_value = [bad_cu, good_cu]
        mock_elf.get_dwarf_info.return_value = mock_dwarf

        with patch("abicheck.dwarf_metadata.ELFFile", return_value=mock_elf):
            result = _parse(MagicMock(), Path("/fake.so"))
        assert result.has_dwarf is True


# ── _walk_die_iter ────────────────────────────────────────────────────

class TestWalkDieIter:
    def test_skip_tags_not_descended(self):
        """DW_TAG_subprogram subtrees are skipped."""
        inner = _Die("DW_TAG_structure_type", {"DW_AT_name": "InnerStruct", "DW_AT_byte_size": 4})
        func = _Die("DW_TAG_subprogram", children=[inner])
        root = _Die("DW_TAG_compile_unit", children=[func])

        meta = DwarfMetadata(has_dwarf=True)
        cu = _CU(offset=0)
        _walk_die_iter(root, meta, cu, {})
        # Inner struct inside subprogram should NOT be registered
        assert "InnerStruct" not in meta.structs

    def test_namespace_scoping(self):
        """Types inside namespaces get qualified names."""
        struct = _Die("DW_TAG_structure_type", {
            "DW_AT_name": "Foo",
            "DW_AT_byte_size": 8,
        })
        ns = _Die("DW_TAG_namespace", {"DW_AT_name": "MyNS"}, children=[struct])
        root = _Die("DW_TAG_compile_unit", children=[ns])

        meta = DwarfMetadata(has_dwarf=True)
        cu = _CU(offset=0)
        _walk_die_iter(root, meta, cu, {})
        assert "MyNS::Foo" in meta.structs

    def test_enum_processed(self):
        """DW_TAG_enumeration_type is processed."""
        enumerator = _Die("DW_TAG_enumerator", {"DW_AT_name": "A", "DW_AT_const_value": 0})
        enum = _Die("DW_TAG_enumeration_type", {
            "DW_AT_name": "Color",
            "DW_AT_byte_size": 4,
        }, children=[enumerator])
        root = _Die("DW_TAG_compile_unit", children=[enum])

        meta = DwarfMetadata(has_dwarf=True)
        cu = _CU(offset=0)
        _walk_die_iter(root, meta, cu, {})
        assert "Color" in meta.enums
        assert meta.enums["Color"].members["A"] == 0

    def test_typedef_to_anonymous_struct(self):
        """DW_TAG_typedef pointing to anonymous struct registers under typedef name."""
        # Create anonymous struct (no DW_AT_name)
        member = _Die("DW_TAG_member", {
            "DW_AT_name": "x",
            "DW_AT_data_member_location": 0,
        }, offset=10)
        anon_struct = _Die("DW_TAG_structure_type", {
            "DW_AT_byte_size": 4,
        }, children=[member], offset=20)

        # Create typedef pointing to it
        typedef = _Die("DW_TAG_typedef", {
            "DW_AT_name": "MyType",
            "DW_AT_type": _Attr(20, "DW_FORM_ref_addr"),
        }, offset=30)

        root = _Die("DW_TAG_compile_unit", children=[anon_struct, typedef])

        cu = _CU(offset=0)
        cu._die_map = {20: anon_struct}

        meta = DwarfMetadata(has_dwarf=True)
        _walk_die_iter(root, meta, cu, {})
        assert "MyType" in meta.structs

    def test_typedef_to_anonymous_enum(self):
        """DW_TAG_typedef pointing to anonymous enum registers under typedef name."""
        enumerator = _Die("DW_TAG_enumerator", {"DW_AT_name": "VAL", "DW_AT_const_value": 1})
        anon_enum = _Die("DW_TAG_enumeration_type", {
            "DW_AT_byte_size": 4,
        }, children=[enumerator], offset=50)

        typedef = _Die("DW_TAG_typedef", {
            "DW_AT_name": "MyEnum",
            "DW_AT_type": _Attr(50, "DW_FORM_ref_addr"),
        }, offset=60)

        root = _Die("DW_TAG_compile_unit", children=[anon_enum, typedef])

        cu = _CU(offset=0)
        cu._die_map = {50: anon_enum}

        meta = DwarfMetadata(has_dwarf=True)
        _walk_die_iter(root, meta, cu, {})
        assert "MyEnum" in meta.enums


# ── _process_struct / _process_struct_named ────────────────────────────

class TestProcessStruct:
    def test_anonymous_struct_skipped(self):
        die = _Die("DW_TAG_structure_type", {"DW_AT_byte_size": 4})
        meta = DwarfMetadata(has_dwarf=True)
        _process_struct(die, meta, _CU(), {})
        assert len(meta.structs) == 0

    def test_declaration_only_skipped(self):
        """Struct with byte_size 0 (declaration-only) is skipped."""
        die = _Die("DW_TAG_structure_type", {"DW_AT_name": "Fwd", "DW_AT_byte_size": 0})
        meta = DwarfMetadata(has_dwarf=True)
        _process_struct(die, meta, _CU(), {})
        assert "Fwd" not in meta.structs

    def test_odr_keeps_first(self):
        """ODR: second definition with different size is not registered."""
        die1 = _Die("DW_TAG_structure_type", {"DW_AT_name": "S", "DW_AT_byte_size": 8})
        die2 = _Die("DW_TAG_structure_type", {"DW_AT_name": "S", "DW_AT_byte_size": 16})

        meta = DwarfMetadata(has_dwarf=True)
        cu = _CU()
        _process_struct(die1, meta, cu, {})
        _process_struct(die2, meta, cu, {})
        assert meta.structs["S"].byte_size == 8

    def test_union_flag(self):
        die = _Die("DW_TAG_union_type", {"DW_AT_name": "U", "DW_AT_byte_size": 4})
        meta = DwarfMetadata(has_dwarf=True)
        _process_struct(die, meta, _CU(), {})
        assert meta.structs["U"].is_union is True

    def test_with_scope_prefix(self):
        die = _Die("DW_TAG_structure_type", {"DW_AT_name": "Inner", "DW_AT_byte_size": 4})
        meta = DwarfMetadata(has_dwarf=True)
        _process_struct(die, meta, _CU(), {}, scope_prefix="Outer")
        assert "Outer::Inner" in meta.structs

    def test_anonymous_member_inlined(self):
        """Anonymous struct member fields are inlined into parent."""
        # Inner anon struct
        inner_member = _Die("DW_TAG_member", {
            "DW_AT_name": "y",
            "DW_AT_data_member_location": 0,
        })
        anon_struct = _Die("DW_TAG_structure_type", {
            "DW_AT_byte_size": 4,
        }, children=[inner_member], offset=100)

        # Anon member pointing to the struct
        anon_member = _Die("DW_TAG_member", {
            "DW_AT_data_member_location": 4,
            "DW_AT_type": _Attr(100, "DW_FORM_ref_addr"),
        })

        outer = _Die("DW_TAG_structure_type", {
            "DW_AT_name": "S",
            "DW_AT_byte_size": 8,
        }, children=[anon_member])

        cu = _CU()
        cu._die_map = {100: anon_struct}

        meta = DwarfMetadata(has_dwarf=True)
        _process_struct(outer, meta, cu, {})
        assert "S" in meta.structs
        assert any(f.name == "y" for f in meta.structs["S"].fields)


# ── _process_member ───────────────────────────────────────────────────

class TestProcessMember:
    def test_unnamed_member_returns_none(self):
        die = _Die("DW_TAG_member", {"DW_AT_data_member_location": 0})
        assert _process_member(die, _CU(), {}) is None

    def test_list_expression_offset(self):
        """DW_OP expression list: last element used as offset."""
        die = _Die("DW_TAG_member", {
            "DW_AT_name": "x",
            "DW_AT_data_member_location": _Attr([0x23, 8]),
        })
        fi = _process_member(die, _CU(), {})
        assert fi is not None
        assert fi.byte_offset == 8

    def test_empty_list_expression(self):
        """Empty DW_OP expression list → offset 0."""
        die = _Die("DW_TAG_member", {
            "DW_AT_name": "x",
            "DW_AT_data_member_location": _Attr([]),
        })
        fi = _process_member(die, _CU(), {})
        assert fi is not None
        assert fi.byte_offset == 0

    def test_bitfield_data_bit_offset(self):
        """DW_AT_data_bit_offset takes priority over DW_AT_bit_offset."""
        die = _Die("DW_TAG_member", {
            "DW_AT_name": "bf",
            "DW_AT_data_member_location": 0,
            "DW_AT_bit_size": 3,
            "DW_AT_data_bit_offset": 5,
            "DW_AT_bit_offset": 99,
        })
        fi = _process_member(die, _CU(), {})
        assert fi is not None
        assert fi.bit_size == 3
        assert fi.bit_offset == 5

    def test_bitfield_legacy_bit_offset(self):
        """When DW_AT_data_bit_offset absent, DW_AT_bit_offset is used."""
        die = _Die("DW_TAG_member", {
            "DW_AT_name": "bf",
            "DW_AT_data_member_location": 0,
            "DW_AT_bit_size": 4,
            "DW_AT_bit_offset": 12,
        })
        fi = _process_member(die, _CU(), {})
        assert fi is not None
        assert fi.bit_offset == 12


# ── _process_enum ─────────────────────────────────────────────────────

class TestProcessEnum:
    def test_anonymous_enum_skipped(self):
        die = _Die("DW_TAG_enumeration_type", {"DW_AT_byte_size": 4})
        meta = DwarfMetadata(has_dwarf=True)
        _process_enum(die, meta, _CU())
        assert len(meta.enums) == 0

    def test_declaration_only_skipped(self):
        die = _Die("DW_TAG_enumeration_type", {"DW_AT_name": "E", "DW_AT_byte_size": 0})
        meta = DwarfMetadata(has_dwarf=True)
        _process_enum(die, meta, _CU())
        assert "E" not in meta.enums

    def test_scoped_enum(self):
        enumerator = _Die("DW_TAG_enumerator", {"DW_AT_name": "A", "DW_AT_const_value": 1})
        die = _Die("DW_TAG_enumeration_type", {
            "DW_AT_name": "E",
            "DW_AT_byte_size": 4,
        }, children=[enumerator])
        meta = DwarfMetadata(has_dwarf=True)
        _process_enum(die, meta, _CU(), scope_prefix="NS")
        assert "NS::E" in meta.enums

    def test_odr_keeps_first_enum(self):
        die1 = _Die("DW_TAG_enumeration_type", {"DW_AT_name": "E", "DW_AT_byte_size": 4})
        die2 = _Die("DW_TAG_enumeration_type", {"DW_AT_name": "E", "DW_AT_byte_size": 8})
        meta = DwarfMetadata(has_dwarf=True)
        _process_enum(die1, meta, _CU())
        _process_enum(die2, meta, _CU())
        assert meta.enums["E"].underlying_byte_size == 4


# ── _expand_anonymous_member ──────────────────────────────────────────

class TestExpandAnonymousMember:
    def test_no_type_returns_empty(self):
        die = _Die("DW_TAG_member", {})
        result = _expand_anonymous_member(die, _CU(), {}, 0)
        assert result == []

    def test_bad_ref_returns_empty(self):
        die = _Die("DW_TAG_member", {"DW_AT_type": _Attr(999, "DW_FORM_ref_addr")})
        cu = _CU()
        cu._die_map = {}
        with patch("abicheck.dwarf_metadata._resolve_ref", side_effect=RuntimeError("bad")):
            result = _expand_anonymous_member(die, cu, {}, 0)
        assert result == []

    def test_non_struct_target_returns_empty(self):
        target = _Die("DW_TAG_base_type", {"DW_AT_name": "int"}, offset=10)
        die = _Die("DW_TAG_member", {"DW_AT_type": _Attr(10, "DW_FORM_ref_addr")})
        cu = _CU()
        cu._die_map = {10: target}
        result = _expand_anonymous_member(die, cu, {}, 0)
        assert result == []


# ── _compute_type_info branches ───────────────────────────────────────

class TestComputeTypeInfo:
    def test_base_type(self):
        die = _Die("DW_TAG_base_type", {"DW_AT_name": "long", "DW_AT_byte_size": 8})
        assert _compute_type_info(die, _CU(), 0, {}) == ("long", 8)

    def test_enum_type(self):
        die = _Die("DW_TAG_enumeration_type", {"DW_AT_name": "Color", "DW_AT_byte_size": 4})
        assert _compute_type_info(die, _CU(), 0, {}) == ("enum Color", 4)

    def test_enum_anonymous(self):
        die = _Die("DW_TAG_enumeration_type", {"DW_AT_byte_size": 4})
        assert _compute_type_info(die, _CU(), 0, {}) == ("enum <enum>", 4)

    def test_subroutine_type(self):
        die = _Die("DW_TAG_subroutine_type", {"DW_AT_byte_size": 8})
        assert _compute_type_info(die, _CU(), 0, {}) == ("fn(...)", 8)

    def test_record_struct(self):
        die = _Die("DW_TAG_structure_type", {"DW_AT_name": "Foo", "DW_AT_byte_size": 16})
        result = _compute_type_info(die, _CU(), 0, {})
        assert result == ("Foo", 16)

    def test_record_anon(self):
        die = _Die("DW_TAG_class_type", {"DW_AT_byte_size": 8})
        result = _compute_type_info(die, _CU(), 0, {})
        assert result == ("<anon>", 8)

    def test_rvalue_reference(self):
        base = _Die("DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10)
        rref = _Die("DW_TAG_rvalue_reference_type", {
            "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
            "DW_AT_byte_size": 8,
        })
        cu = _CU()
        cu._die_map = {10: base}
        result = _compute_type_info(rref, cu, 0, {})
        assert result == ("int &&", 8)

    def test_const_qualified(self):
        base = _Die("DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10)
        const = _Die("DW_TAG_const_type", {
            "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
        })
        cu = _CU()
        cu._die_map = {10: base}
        result = _compute_type_info(const, cu, 0, {})
        assert result == ("const int", 4)

    def test_volatile_qualified(self):
        base = _Die("DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10)
        vol = _Die("DW_TAG_volatile_type", {
            "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
        })
        cu = _CU()
        cu._die_map = {10: base}
        result = _compute_type_info(vol, cu, 0, {})
        assert result == ("volatile int", 4)

    def test_restrict_qualified(self):
        base = _Die("DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10)
        restrict = _Die("DW_TAG_restrict_type", {
            "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
        })
        cu = _CU()
        cu._die_map = {10: base}
        result = _compute_type_info(restrict, cu, 0, {})
        assert result == ("restrict int", 4)

    def test_typedef_chain(self):
        base = _Die("DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10)
        td = _Die("DW_TAG_typedef", {
            "DW_AT_name": "myint",
            "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
        })
        cu = _CU()
        cu._die_map = {10: base}
        result = _compute_type_info(td, cu, 0, {})
        assert result == ("myint", 4)

    def test_array_type(self):
        base = _Die("DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10)
        arr = _Die("DW_TAG_array_type", {
            "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
            "DW_AT_byte_size": 40,
        })
        cu = _CU()
        cu._die_map = {10: base}
        result = _compute_type_info(arr, cu, 0, {})
        assert result == ("int[]", 40)

    def test_array_no_inner_type(self):
        arr = _Die("DW_TAG_array_type", {"DW_AT_byte_size": 8})
        result = _compute_type_info(arr, _CU(), 0, {})
        assert result == ("array", 8)

    def test_qualified_no_inner(self):
        const = _Die("DW_TAG_const_type", {})
        result = _compute_type_info(const, _CU(), 0, {})
        assert result == ("const", 0)

    def test_typedef_no_inner(self):
        td = _Die("DW_TAG_typedef", {"DW_AT_name": "foo"})
        result = _compute_type_info(td, _CU(), 0, {})
        assert result == ("foo", 0)

    def test_typedef_unnamed_no_inner(self):
        td = _Die("DW_TAG_typedef", {})
        result = _compute_type_info(td, _CU(), 0, {})
        assert result == ("typedef", 0)

    def test_reference_type(self):
        base = _Die("DW_TAG_base_type", {"DW_AT_name": "int", "DW_AT_byte_size": 4}, offset=10)
        ref = _Die("DW_TAG_reference_type", {
            "DW_AT_type": _Attr(10, "DW_FORM_ref_addr"),
            "DW_AT_byte_size": 8,
        })
        cu = _CU()
        cu._die_map = {10: base}
        result = _compute_type_info(ref, cu, 0, {})
        assert result == ("int &", 8)

    def test_reference_no_inner(self):
        ref = _Die("DW_TAG_reference_type", {"DW_AT_byte_size": 8})
        result = _compute_type_info(ref, _CU(), 0, {})
        assert result == ("? &", 8)

    def test_rvalue_reference_no_inner(self):
        rref = _Die("DW_TAG_rvalue_reference_type", {"DW_AT_byte_size": 8})
        result = _compute_type_info(rref, _CU(), 0, {})
        assert result == ("? &&", 8)


# ── _compute_fallback_type_info ───────────────────────────────────────

class TestComputeFallbackTypeInfo:
    def test_with_name(self):
        die = _Die("DW_TAG_whatever", {"DW_AT_name": "CustomType", "DW_AT_byte_size": 4})
        result = _compute_fallback_type_info(die, "DW_TAG_whatever")
        assert result == ("CustomType", 4)

    def test_without_name_logs_warning(self, monkeypatch):
        """Unknown tag without name triggers warning and uses tag as name."""
        from abicheck import dwarf_metadata as dm
        monkeypatch.setattr(dm, "_SEEN_UNKNOWN_DWARF_TAGS", set())

        die = _Die("DW_TAG_exotic_vendor", {"DW_AT_byte_size": 2}, offset=42)
        result = _compute_fallback_type_info(die, "DW_TAG_exotic_vendor")
        assert result == ("DW_TAG_exotic_vendor", 2)
        assert "DW_TAG_exotic_vendor" in dm._SEEN_UNKNOWN_DWARF_TAGS

    def test_empty_tag(self, monkeypatch):
        from abicheck import dwarf_metadata as dm
        monkeypatch.setattr(dm, "_SEEN_UNKNOWN_DWARF_TAGS", set())

        die = _Die("", {"DW_AT_byte_size": 0}, offset=0)
        result = _compute_fallback_type_info(die, "")
        assert result == ("unknown", 0)


# ── _resolve_type ─────────────────────────────────────────────────────

class TestResolveType:
    def test_no_type_attr(self):
        die = _Die("DW_TAG_member", {"DW_AT_name": "x"})
        assert _resolve_type(die, _CU(), {}) == ("unknown", 0)

    def test_error_in_resolution(self):
        die = _Die("DW_TAG_member", {
            "DW_AT_name": "x",
            "DW_AT_type": _Attr(999, "DW_FORM_ref_addr"),
        })
        with patch("abicheck.dwarf_metadata._resolve_ref", side_effect=RuntimeError("bad")):
            assert _resolve_type(die, _CU(), {}) == ("unknown", 0)


# ── _resolve_inner_type_info ──────────────────────────────────────────

class TestResolveInnerTypeInfo:
    def test_no_type_attr(self):
        die = _Die("DW_TAG_const_type", {})
        assert _resolve_inner_type_info(die, _CU(), 0, {}) is None

    def test_error_returns_none(self):
        die = _Die("DW_TAG_const_type", {"DW_AT_type": _Attr(999, "DW_FORM_ref_addr")})
        with patch("abicheck.dwarf_metadata._resolve_ref", side_effect=RuntimeError("bad")):
            assert _resolve_inner_type_info(die, _CU(), 0, {}) is None


# ── _attr_str / _attr_int edge cases ─────────────────────────────────

class TestAttrHelpers:
    def test_attr_str_bytes_value(self):
        die = _Die("X", {"DW_AT_name": _Attr(b"hello\xff")})
        assert _attr_str(die, "DW_AT_name") == "hello\ufffd"

    def test_attr_str_none_value(self):
        die = _Die("X", {"DW_AT_name": _Attr(None)})
        assert _attr_str(die, "DW_AT_name") == ""

    def test_attr_str_missing(self):
        die = _Die("X", {})
        assert _attr_str(die, "DW_AT_name") == ""

    def test_attr_int_missing(self):
        die = _Die("X", {})
        assert _attr_int(die, "DW_AT_byte_size") == 0

    def test_attr_int_bad_value(self):
        die = _Die("X", {"DW_AT_byte_size": _Attr("not a number")})
        assert _attr_int(die, "DW_AT_byte_size") == 0

    def test_attr_int_none_value(self):
        die = _Die("X", {"DW_AT_byte_size": _Attr(None)})
        assert _attr_int(die, "DW_AT_byte_size") == 0


# ── _process_typedef ──────────────────────────────────────────────────

class TestProcessTypedef:
    def test_unnamed_typedef_skipped(self):
        die = _Die("DW_TAG_typedef", {"DW_AT_type": _Attr(10)})
        meta = DwarfMetadata(has_dwarf=True)
        _process_typedef(die, meta, _CU(), {})
        assert len(meta.structs) == 0
        assert len(meta.enums) == 0

    def test_no_type_attr_skipped(self):
        die = _Die("DW_TAG_typedef", {"DW_AT_name": "foo"})
        meta = DwarfMetadata(has_dwarf=True)
        _process_typedef(die, meta, _CU(), {})
        assert len(meta.structs) == 0

    def test_bad_ref_skipped(self):
        die = _Die("DW_TAG_typedef", {
            "DW_AT_name": "foo",
            "DW_AT_type": _Attr(999),
        })
        meta = DwarfMetadata(has_dwarf=True)
        with patch("abicheck.dwarf_metadata._resolve_ref", side_effect=RuntimeError("bad")):
            _process_typedef(die, meta, _CU(), {})
        assert len(meta.structs) == 0

    def test_named_target_not_registered(self):
        """Typedef to a named struct does not re-register."""
        target = _Die("DW_TAG_structure_type", {
            "DW_AT_name": "RealName",
            "DW_AT_byte_size": 8,
        }, offset=20)
        die = _Die("DW_TAG_typedef", {
            "DW_AT_name": "Alias",
            "DW_AT_type": _Attr(20, "DW_FORM_ref_addr"),
        })
        cu = _CU()
        cu._die_map = {20: target}
        meta = DwarfMetadata(has_dwarf=True)
        _process_typedef(die, meta, cu, {})
        assert "Alias" not in meta.structs
