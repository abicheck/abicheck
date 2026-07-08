# pylint: disable=too-many-branches,too-many-statements,too-many-locals,too-many-arguments,too-many-return-statements
"""Unit tests for elf_metadata — mock pyelftools to cover internal parsing.

These tests exercise _parse, _parse_dynamic, _parse_version_def,
_parse_version_need, _parse_dynsym, and parse_elf_metadata edge cases
without needing a real ELF binary or gcc.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from abicheck.elf_metadata import (
    _BINDING_MAP,
    _HIDDEN_VISIBILITIES,
    _TYPE_MAP,
    ElfMetadata,
    ElfSymbol,
    SymbolBinding,
    SymbolType,
    _parse,
    _parse_dynamic,
    _parse_dynsym,
    _parse_version_def,
    _parse_version_need,
    parse_elf_metadata,
)

# ── ElfMetadata.symbol_map ───────────────────────────────────────────────

class TestElfMetadataSymbolMap:
    def test_symbol_map_returns_name_to_symbol(self):
        s1 = ElfSymbol(name="foo")
        s2 = ElfSymbol(name="bar")
        meta = ElfMetadata(symbols=[s1, s2])
        assert meta.symbol_map == {"foo": s1, "bar": s2}

    def test_symbol_map_cached(self):
        meta = ElfMetadata(symbols=[ElfSymbol(name="x")])
        m1 = meta.symbol_map
        m2 = meta.symbol_map
        assert m1 is m2


# ── parse_elf_metadata error paths ──────────────────────────────────────

class TestParseElfMetadataEdgeCases:
    def test_nonexistent_file_returns_empty(self):
        meta = parse_elf_metadata(Path("/nonexistent/libfake.so"))
        assert isinstance(meta, ElfMetadata)
        assert meta.symbols == []

    def test_non_regular_file_returns_empty(self, tmp_path):
        """Directory (not a regular file) → empty metadata."""
        meta = parse_elf_metadata(tmp_path)
        assert isinstance(meta, ElfMetadata)
        assert meta.symbols == []

    def test_non_elf_file_returns_empty(self, tmp_path):
        """Text file → ELFError → empty metadata."""
        f = tmp_path / "bad.so"
        f.write_text("not an elf", encoding="utf-8")
        meta = parse_elf_metadata(f)
        assert isinstance(meta, ElfMetadata)
        assert meta.symbols == []


# ── _parse_dynamic ──────────────────────────────────────────────────────

class TestParseDynamic:
    def _make_tag(self, d_tag: str, **kwargs):
        tag = MagicMock()
        tag.entry = MagicMock()
        tag.entry.d_tag = d_tag
        for k, v in kwargs.items():
            setattr(tag, k, v)
        return tag

    def test_soname(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.iter_tags.return_value = [self._make_tag("DT_SONAME", soname="libfoo.so.1")]
        _parse_dynamic(section, meta)
        assert meta.soname == "libfoo.so.1"

    def test_needed(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.iter_tags.return_value = [
            self._make_tag("DT_NEEDED", needed="libc.so.6"),
            self._make_tag("DT_NEEDED", needed="libm.so.6"),
        ]
        _parse_dynamic(section, meta)
        assert meta.needed == ["libc.so.6", "libm.so.6"]

    def test_rpath(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.iter_tags.return_value = [self._make_tag("DT_RPATH", rpath="/usr/lib")]
        _parse_dynamic(section, meta)
        assert meta.rpath == "/usr/lib"

    def test_runpath(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.iter_tags.return_value = [self._make_tag("DT_RUNPATH", runpath="$ORIGIN")]
        _parse_dynamic(section, meta)
        assert meta.runpath == "$ORIGIN"

    def _val_tag(self, d_tag: str, d_val: int):
        tag = MagicMock()
        tag.entry = MagicMock()
        tag.entry.d_tag = d_tag
        tag.entry.d_val = d_val
        return tag

    def test_dt_bind_now_sets_bind_now(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.iter_tags.return_value = [self._make_tag("DT_BIND_NOW")]
        _parse_dynamic(section, meta)
        assert meta.bind_now is True

    def test_df_bind_now_flag_sets_bind_now(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.iter_tags.return_value = [self._val_tag("DT_FLAGS", 0x8)]  # DF_BIND_NOW
        _parse_dynamic(section, meta)
        assert meta.bind_now is True

    def test_df_1_now_and_pie_flags(self):
        meta = ElfMetadata()
        section = MagicMock()
        # DF_1_NOW (0x1) | DF_1_PIE (0x08000000)
        section.iter_tags.return_value = [self._val_tag("DT_FLAGS_1", 0x1 | 0x08000000)]
        _parse_dynamic(section, meta)
        assert meta.bind_now is True
        assert meta.is_pie is True  # tentative; gated on ET_DYN by caller

    def test_df_static_tls_flag_sets_has_static_tls(self):
        # G23-A1: DF_STATIC_TLS (0x10) in DT_FLAGS.
        meta = ElfMetadata()
        section = MagicMock()
        section.iter_tags.return_value = [self._val_tag("DT_FLAGS", 0x10)]
        _parse_dynamic(section, meta)
        assert meta.has_static_tls is True


# ── _parse_gnu_property (G23-A2) ──────────────────────────────────────────

class TestParseGnuProperty:
    def _note_section_with(self, n_type, descdata: bytes):
        section = MagicMock()
        section.iter_notes.return_value = [{"n_type": n_type, "n_descdata": descdata}]
        return section

    # x86 X86_FEATURE_1_AND property with IBT|SHSTK bits set (little-endian):
    # pr_type=0xC0000002, pr_datasz=4, data=0x00000003, pad to 8.
    _IBT_SHSTK_DESC = b"\x02\x00\x00\xc0\x04\x00\x00\x00\x03\x00\x00\x00\x00\x00\x00\x00"

    def test_string_note_type_is_accepted(self):
        # Regression: pyelftools reports n_type as the *string*
        # "NT_GNU_PROPERTY_TYPE_0", not the numeric 5. The parser must not skip it.
        from abicheck.elf_metadata import _parse_gnu_property
        meta = ElfMetadata()
        elf = MagicMock()
        elf.little_endian = True
        elf.get_section_by_name.return_value = self._note_section_with(
            "NT_GNU_PROPERTY_TYPE_0", self._IBT_SHSTK_DESC
        )
        _parse_gnu_property(elf, meta, Path("x.so"))
        assert meta.gnu_properties == frozenset({"IBT", "SHSTK"})

    def test_numeric_note_type_is_accepted(self):
        from abicheck.elf_metadata import _parse_gnu_property
        meta = ElfMetadata()
        elf = MagicMock()
        elf.little_endian = True
        elf.get_section_by_name.return_value = self._note_section_with(5, self._IBT_SHSTK_DESC)
        _parse_gnu_property(elf, meta, Path("x.so"))
        assert meta.gnu_properties == frozenset({"IBT", "SHSTK"})

    def test_missing_section_is_empty(self):
        from abicheck.elf_metadata import _parse_gnu_property
        meta = ElfMetadata()
        elf = MagicMock()
        elf.get_section_by_name.return_value = None
        _parse_gnu_property(elf, meta, Path("x.so"))
        assert meta.gnu_properties == frozenset()

    def test_raw_note_segment_parser_extracts_gnu_property(self):
        # Section-header-stripped binaries keep only the PT_GNU_PROPERTY segment.
        # _parse_raw_notes must extract the description from raw note bytes.
        import struct

        from abicheck.elf_metadata import _decode_gnu_property_desc, _parse_raw_notes
        # ELF note: namesz=4, descsz=16, n_type=5, name="GNU\0", desc=property array.
        desc = struct.pack("<III", 0xC0000002, 4, 0x3) + b"\x00\x00\x00\x00"
        note = struct.pack("<III", 4, len(desc), 5) + b"GNU\x00" + desc
        descs = list(_parse_raw_notes(note, True))
        assert len(descs) == 1
        assert _decode_gnu_property_desc(descs[0], True, 8) == frozenset({"IBT", "SHSTK"})

    def test_raw_note_segment_parser_skips_non_gnu(self):
        import struct

        from abicheck.elf_metadata import _parse_raw_notes
        # A non-GNU note (name "XYZ") must be ignored.
        desc = struct.pack("<III", 0xC0000002, 4, 0x3) + b"\x00\x00\x00\x00"
        note = struct.pack("<III", 4, len(desc), 5) + b"XYZ\x00" + desc
        assert list(_parse_raw_notes(note, True)) == []

    def test_elf32_alignment_decodes_cet_after_preceding_property(self):
        # ELFCLASS32 pads each property to 4 bytes, not 8. A preceding property
        # with a 4-byte payload occupies exactly 12 bytes; with 8-byte alignment
        # the walk would land at offset 16 and misread the CET property. Verify
        # the 4-byte-aligned decode still finds IBT|SHSTK.
        import struct

        from abicheck.elf_metadata import _decode_gnu_property_desc
        # Property 1: some other pr_type (0xC0008000), datasz=4, data=0 → 12 bytes.
        desc = struct.pack("<III", 0xC0008000, 4, 0)
        # Property 2: X86_FEATURE_1_AND (0xC0000002), datasz=4, bits=IBT|SHSTK(3).
        desc += struct.pack("<III", 0xC0000002, 4, 0x3)
        # 8-byte alignment would skip the second property; 4-byte finds it.
        assert _decode_gnu_property_desc(desc, True, align=4) == frozenset({"IBT", "SHSTK"})
        assert _decode_gnu_property_desc(desc, True, align=8) != frozenset({"IBT", "SHSTK"})


# ── _decode_abi_flags (G23-A3) ────────────────────────────────────────────

class TestDecodeAbiFlags:
    def test_arm_hard_float_and_eabi(self):
        from abicheck.elf_metadata import _decode_abi_flags
        # EF_ARM_ABI_FLOAT_HARD (0x400) | EABI version 5 (5 << 24).
        flags = _decode_abi_flags("EM_ARM", 0x400 | (5 << 24))
        assert flags == frozenset({"float-hard", "eabi5"})

    def test_arm_soft_float(self):
        from abicheck.elf_metadata import _decode_abi_flags
        assert _decode_abi_flags("EM_ARM", 0x200) == frozenset({"float-soft"})

    def test_riscv_double_float_with_compressed(self):
        from abicheck.elf_metadata import _decode_abi_flags
        # float-abi double (0x4) | RVC (0x1).
        assert _decode_abi_flags("EM_RISCV", 0x4 | 0x1) == frozenset({"float-double", "rvc"})

    def test_riscv_soft_float_and_rve(self):
        from abicheck.elf_metadata import _decode_abi_flags
        assert _decode_abi_flags("EM_RISCV", 0x0 | 0x8) == frozenset({"float-soft", "rve"})

    def test_mips_abi_bits(self):
        from abicheck.elf_metadata import _decode_abi_flags
        assert _decode_abi_flags("EM_MIPS", 0x1000) == frozenset({"mips-abi-0x1000"})

    def test_unknown_arch_is_empty(self):
        from abicheck.elf_metadata import _decode_abi_flags
        # PPC64's ABI version lives in e_flags but is not decoded → empty set
        # (the raw e_flags fallback in the diff handles it).
        assert _decode_abi_flags("EM_PPC64", 0x2) == frozenset()


# ── _read_identity (G23-A3) ───────────────────────────────────────────────

class TestReadIdentity:
    def test_reads_header_fields(self):
        from abicheck.elf_metadata import _read_identity
        meta = ElfMetadata()
        elf = MagicMock()
        elf.elfclass = 64
        elf.__getitem__.side_effect = lambda k: {
            "e_machine": "EM_ARM",
            "e_flags": 0x400 | (5 << 24),
            "e_ident": {"EI_OSABI": "ELFOSABI_LINUX"},
        }[k]
        _read_identity(elf, meta, Path("x.so"))
        assert meta.machine == "EM_ARM"
        assert meta.elf_class == 64
        assert meta.osabi == "ELFOSABI_LINUX"
        assert meta.abi_flags == frozenset({"float-hard", "eabi5"})

    def test_parse_failure_is_swallowed(self):
        from abicheck.elf_metadata import _read_identity
        meta = ElfMetadata()
        elf = MagicMock()
        elf.__getitem__.side_effect = KeyError("e_machine")
        _read_identity(elf, meta, Path("x.so"))  # must not raise
        assert meta.machine == ""


# ── _iter_gnu_property_descs segment fallback (G23-A2) ────────────────────

class TestGnuPropertySegmentFallback:
    _IBT_SHSTK_DESC = b"\x02\x00\x00\xc0\x04\x00\x00\x00\x03\x00\x00\x00\x00\x00\x00\x00"

    def _note_bytes(self):
        import struct
        return struct.pack("<III", 4, len(self._IBT_SHSTK_DESC), 5) + b"GNU\x00" + self._IBT_SHSTK_DESC

    def test_falls_back_to_pt_gnu_property_segment(self):
        # Section headers stripped → no .note.gnu.property section, but the
        # loadable PT_GNU_PROPERTY segment still carries the note.
        from abicheck.elf_metadata import _parse_gnu_property
        meta = ElfMetadata()
        elf = MagicMock()
        elf.elfclass = 64
        elf.little_endian = True
        elf.get_section_by_name.return_value = None
        seg = MagicMock()
        seg.header.p_type = "PT_GNU_PROPERTY"
        seg.data.return_value = self._note_bytes()
        elf.iter_segments.return_value = [seg]
        _parse_gnu_property(elf, meta, Path("x.so"))
        assert meta.gnu_properties == frozenset({"IBT", "SHSTK"})

    def test_no_segment_and_no_section_is_empty(self):
        from abicheck.elf_metadata import _parse_gnu_property
        meta = ElfMetadata()
        elf = MagicMock()
        elf.elfclass = 64
        elf.get_section_by_name.return_value = None
        elf.iter_segments.return_value = []
        _parse_gnu_property(elf, meta, Path("x.so"))
        assert meta.gnu_properties == frozenset()


# ── _finalize_hardening ──────────────────────────────────────────────────

class TestFinalizeHardening:
    def test_relro_full_requires_bind_now(self):
        from abicheck.elf_metadata import _finalize_hardening
        meta = ElfMetadata(bind_now=True)
        _finalize_hardening(meta, has_relro_segment=True, is_et_dyn=True)
        assert meta.relro == "full"

    def test_relro_partial_without_bind_now(self):
        from abicheck.elf_metadata import _finalize_hardening
        meta = ElfMetadata(bind_now=False)
        _finalize_hardening(meta, has_relro_segment=True, is_et_dyn=True)
        assert meta.relro == "partial"

    def test_relro_none_without_segment(self):
        from abicheck.elf_metadata import _finalize_hardening
        meta = ElfMetadata(bind_now=True)
        _finalize_hardening(meta, has_relro_segment=False, is_et_dyn=True)
        assert meta.relro == "none"

    def test_pie_gated_on_et_dyn(self):
        from abicheck.elf_metadata import _finalize_hardening
        meta = ElfMetadata(is_pie=True)  # DF_1_PIE was set
        _finalize_hardening(meta, has_relro_segment=False, is_et_dyn=False)
        assert meta.is_pie is False

    def test_canary_and_fortify_from_imports(self):
        from abicheck.elf_metadata import ElfImport, _finalize_hardening
        meta = ElfMetadata(imports=[
            ElfImport(name="__stack_chk_fail"),
            ElfImport(name="__memcpy_chk"),
            ElfImport(name="malloc"),
        ])
        _finalize_hardening(meta, has_relro_segment=False, is_et_dyn=True)
        assert meta.has_stack_canary is True
        assert meta.has_fortify_source is True


# ── _parse_version_def ──────────────────────────────────────────────────

class TestParseVersionDef:
    def _verdef(self, flags: int = 0):
        """Return a verdef mock (flags=0 = real version node, flags=1 = base)."""
        vd = MagicMock()
        vd.entry.vd_flags = flags
        return vd

    def test_version_defs_collected(self):
        meta = ElfMetadata()
        aux1 = MagicMock()
        aux1.name = "LIBFOO_1.0"
        aux2 = MagicMock()
        aux2.name = "LIBFOO_2.0"
        section = MagicMock()
        section.iter_versions.return_value = [
            (self._verdef(0), [aux1]),
            (self._verdef(0), [aux2]),
        ]
        _parse_version_def(section, meta)
        assert meta.versions_defined == ["LIBFOO_1.0", "LIBFOO_2.0"]

    def test_base_entry_skipped(self):
        """VER_FLG_BASE (flags=1) entries are the SONAME marker — skip them."""
        meta = ElfMetadata()
        aux = MagicMock()
        aux.name = "libfoo.so.1"
        section = MagicMock()
        section.iter_versions.return_value = [(self._verdef(flags=1), [aux])]
        _parse_version_def(section, meta)
        assert meta.versions_defined == []

    def test_duplicates_not_added(self):
        meta = ElfMetadata()
        aux = MagicMock()
        aux.name = "VER_1"
        section = MagicMock()
        section.iter_versions.return_value = [
            (self._verdef(0), [aux]),
            (self._verdef(0), [aux]),
        ]
        _parse_version_def(section, meta)
        assert meta.versions_defined == ["VER_1"]

    def test_empty_name_skipped(self):
        meta = ElfMetadata()
        aux = MagicMock()
        aux.name = ""
        section = MagicMock()
        section.iter_versions.return_value = [(self._verdef(0), [aux])]
        _parse_version_def(section, meta)
        assert meta.versions_defined == []


# ── _parse_version_need ─────────────────────────────────────────────────

class TestParseVersionNeed:
    def test_version_needs_collected(self):
        meta = ElfMetadata()
        verneed = MagicMock()
        verneed.name = "libc.so.6"
        vernaux = MagicMock()
        vernaux.name = "GLIBC_2.17"
        section = MagicMock()
        section.iter_versions.return_value = [(verneed, [vernaux])]
        _parse_version_need(section, meta)
        assert meta.versions_required == {"libc.so.6": ["GLIBC_2.17"]}

    def test_multiple_versions_same_lib(self):
        meta = ElfMetadata()
        verneed = MagicMock()
        verneed.name = "libc.so.6"
        v1 = MagicMock()
        v1.name = "GLIBC_2.17"
        v2 = MagicMock()
        v2.name = "GLIBC_2.34"
        section = MagicMock()
        section.iter_versions.return_value = [(verneed, [v1, v2])]
        _parse_version_need(section, meta)
        assert meta.versions_required["libc.so.6"] == ["GLIBC_2.17", "GLIBC_2.34"]

    def test_duplicate_version_not_added(self):
        meta = ElfMetadata()
        verneed = MagicMock()
        verneed.name = "libc.so.6"
        v1 = MagicMock()
        v1.name = "GLIBC_2.17"
        section = MagicMock()
        section.iter_versions.return_value = [
            (verneed, [v1]),
            (verneed, [v1]),
        ]
        _parse_version_need(section, meta)
        assert meta.versions_required["libc.so.6"] == ["GLIBC_2.17"]

    def test_empty_name_skipped(self):
        meta = ElfMetadata()
        verneed = MagicMock()
        verneed.name = "libc.so.6"
        v = MagicMock()
        v.name = ""
        section = MagicMock()
        section.iter_versions.return_value = [(verneed, [v])]
        _parse_version_need(section, meta)
        assert meta.versions_required == {"libc.so.6": []}


# ── _parse_dynsym ──────────────────────────────────────────────────────

class TestParseDynsym:
    def _make_sym(self, name: str, shndx=1,  # 1 = normal .text section, not SHN_ABS
                  bind="STB_GLOBAL", typ="STT_FUNC",
                  vis="STV_DEFAULT", size=16):
        sym = MagicMock()
        sym.name = name
        sym.entry.st_shndx = shndx
        sym.entry.st_info.bind = bind
        sym.entry.st_info.type = typ
        sym.entry.st_other.visibility = vis
        sym.entry.st_size = size
        return sym

    def test_exported_func(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("foo")]
        _parse_dynsym(section, meta)
        assert len(meta.symbols) == 1
        assert meta.symbols[0].name == "foo"
        assert meta.symbols[0].binding == SymbolBinding.GLOBAL
        assert meta.symbols[0].sym_type == SymbolType.FUNC
        assert meta.symbols[0].size == 16
        assert meta.symbols[0].visibility == "default"

    def test_undefined_sym_skipped(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("undef", shndx="SHN_UNDEF")]
        _parse_dynsym(section, meta)
        assert meta.symbols == []

    def test_local_sym_skipped(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("local_fn", bind="STB_LOCAL")]
        _parse_dynsym(section, meta)
        assert meta.symbols == []

    def test_hidden_sym_skipped(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("hidden_fn", vis="STV_HIDDEN")]
        _parse_dynsym(section, meta)
        assert meta.symbols == []

    def test_internal_sym_skipped(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("internal_fn", vis="STV_INTERNAL")]
        _parse_dynsym(section, meta)
        assert meta.symbols == []

    def test_weak_binding(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("weak_fn", bind="STB_WEAK")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].binding == SymbolBinding.WEAK

    def test_object_type(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("global_var", typ="STT_OBJECT")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].sym_type == SymbolType.OBJECT

    def test_tls_type(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("tls_var", typ="STT_TLS")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].sym_type == SymbolType.TLS

    def test_ifunc_type(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("ifunc_fn", typ="STT_GNU_IFUNC")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].sym_type == SymbolType.IFUNC

    def test_common_type(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("common_sym", typ="STT_COMMON")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].sym_type == SymbolType.COMMON

    def test_notype(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("notype_sym", typ="STT_NOTYPE")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].sym_type == SymbolType.NOTYPE

    def test_unknown_bind_maps_to_other(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("exotic", bind="STB_EXOTIC")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].binding == SymbolBinding.OTHER

    def test_gnu_unique_bind_maps_to_unique(self):
        # STB_GNU_UNIQUE (and the STB_LOOS alias older pyelftools reports) is a
        # recognized binding, not "other" — G23-A4 routes it to dedicated kinds.
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("inst", bind="STB_GNU_UNIQUE")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].binding == SymbolBinding.UNIQUE

    def test_unknown_type_maps_to_other(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("exotic", typ="STT_UNKNOWN")]
        _parse_dynsym(section, meta)
        assert meta.symbols[0].sym_type == SymbolType.OTHER

    def test_protected_visibility_included(self):
        meta = ElfMetadata()
        section = MagicMock()
        section.name = ".dynsym"
        section.iter_symbols.return_value = [self._make_sym("prot_fn", vis="STV_PROTECTED")]
        _parse_dynsym(section, meta)
        assert len(meta.symbols) == 1
        assert meta.symbols[0].visibility == "protected"


# ── _parse (full section dispatch) ──────────────────────────────────────

class TestParseFull:
    def test_dispatches_to_all_section_types(self):
        """Mock ELFFile with all four section types → all parse methods called."""
        from elftools.elf.dynamic import DynamicSection
        from elftools.elf.gnuversions import GNUVerDefSection, GNUVerNeedSection
        from elftools.elf.sections import SymbolTableSection

        dyn = MagicMock(spec=DynamicSection)
        dyn.iter_tags.return_value = [MagicMock(entry=MagicMock(d_tag="DT_SONAME"), soname="lib.so")]

        verdef_aux = MagicMock()
        verdef_aux.name = "VER_DEF_1"
        verdef = MagicMock(spec=GNUVerDefSection)
        verdef_vd = MagicMock()
        verdef_vd.entry.vd_flags = 0  # not VER_FLG_BASE — real version node
        verdef.iter_versions.return_value = [(verdef_vd, [verdef_aux])]

        verneed_entry = MagicMock()
        verneed_entry.name = "libc.so.6"
        vernaux = MagicMock()
        vernaux.name = "GLIBC_2.17"
        verneed = MagicMock(spec=GNUVerNeedSection)
        verneed.iter_versions.return_value = [(verneed_entry, [vernaux])]

        sym = MagicMock()
        sym.name = "my_func"
        sym.entry.st_shndx = 1  # normal .text section
        sym.entry.st_info.bind = "STB_GLOBAL"
        sym.entry.st_info.type = "STT_FUNC"
        sym.entry.st_other.visibility = "STV_DEFAULT"
        sym.entry.st_size = 42
        dynsym = MagicMock(spec=SymbolTableSection)
        dynsym.name = ".dynsym"
        dynsym.iter_symbols.return_value = [sym]

        elf = MagicMock()
        elf.iter_sections.return_value = [dyn, verdef, verneed, dynsym]

        f = MagicMock()
        with patch("abicheck.elf_metadata.ELFFile", return_value=elf):
            meta = _parse(f, Path("test.so"))

        assert meta.soname == "lib.so"
        assert meta.versions_defined == ["VER_DEF_1"]
        assert meta.versions_required == {"libc.so.6": ["GLIBC_2.17"]}
        assert len(meta.symbols) == 1
        assert meta.symbols[0].name == "my_func"

    def test_malformed_section_logged_and_skipped(self):
        """If a section raises, other sections still parse."""
        from elftools.elf.dynamic import DynamicSection
        from elftools.elf.sections import SymbolTableSection

        bad_section = MagicMock(spec=DynamicSection)
        bad_section.name = ".dynamic"
        bad_section.iter_tags.side_effect = RuntimeError("corrupt section")

        good_sym = MagicMock(spec=SymbolTableSection)
        good_sym.name = ".dynsym"
        sym = MagicMock()
        sym.name = "good_fn"
        sym.entry.st_shndx = 1  # normal .text section
        sym.entry.st_info.bind = "STB_GLOBAL"
        sym.entry.st_info.type = "STT_FUNC"
        sym.entry.st_other.visibility = "STV_DEFAULT"
        sym.entry.st_size = 8
        good_sym.iter_symbols.return_value = [sym]

        elf = MagicMock()
        elf.iter_sections.return_value = [bad_section, good_sym]

        f = MagicMock()
        with patch("abicheck.elf_metadata.ELFFile", return_value=elf):
            meta = _parse(f, Path("test.so"))

        assert len(meta.symbols) == 1
        assert meta.symbols[0].name == "good_fn"

    def test_symtab_ignored_when_dynsym_present(self):
        """When a `.dynsym` exists (a normal DSO), `.symtab` is not parsed —
        `.dynsym` is the authoritative export surface."""
        from elftools.elf.sections import SymbolTableSection

        dynsym = MagicMock(spec=SymbolTableSection)
        dynsym.name = ".dynsym"
        dynsym.iter_symbols.return_value = []
        symtab = MagicMock(spec=SymbolTableSection)
        symtab.name = ".symtab"
        symtab.iter_symbols.return_value = []

        elf = MagicMock()
        elf.iter_sections.return_value = [dynsym, symtab]

        f = MagicMock()
        with patch("abicheck.elf_metadata.ELFFile", return_value=elf):
            _parse(f, Path("test.so"))

        symtab.iter_symbols.assert_not_called()  # .dynsym wins

    def test_symtab_parsed_as_fallback_without_dynsym(self):
        """A relocatable `.o` has no `.dynsym`; `.symtab` is parsed as the
        fallback symbol surface (G2 — probe object-file capture)."""
        from elftools.elf.sections import SymbolTableSection

        symtab = MagicMock(spec=SymbolTableSection)
        symtab.name = ".symtab"
        symtab.iter_symbols.return_value = []

        elf = MagicMock()
        elf.iter_sections.return_value = [symtab]

        f = MagicMock()
        with patch("abicheck.elf_metadata.ELFFile", return_value=elf):
            meta = _parse(f, Path("probe.o"))

        symtab.iter_symbols.assert_called()  # fallback parses .symtab
        assert meta.symbols == []  # the mock yields no symbols


# ── Constant correctness ────────────────────────────────────────────────

class TestConstants:
    def test_binding_map_completeness(self):
        assert "STB_GLOBAL" in _BINDING_MAP
        assert "STB_WEAK" in _BINDING_MAP
        assert "STB_LOCAL" in _BINDING_MAP

    def test_type_map_completeness(self):
        assert "STT_FUNC" in _TYPE_MAP
        assert "STT_OBJECT" in _TYPE_MAP
        assert "STT_TLS" in _TYPE_MAP
        assert "STT_GNU_IFUNC" in _TYPE_MAP
        assert "STT_COMMON" in _TYPE_MAP
        assert "STT_NOTYPE" in _TYPE_MAP

    def test_hidden_visibilities(self):
        assert "STV_HIDDEN" in _HIDDEN_VISIBILITIES
        assert "STV_INTERNAL" in _HIDDEN_VISIBILITIES
        assert "STV_DEFAULT" not in _HIDDEN_VISIBILITIES
