# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for :mod:`abicheck.extract.consumer_imports` (ADR-005).

The application-side producer: format detection, the ELF/PE/Mach-O import
parsers, and :func:`read_consumer_imports`, which must report an unreadable
consumer as an explicit ``FAILED`` fact rather than an empty requirement set.
Only third-party parsing boundaries (pyelftools, pefile, macholib) and the
ELF origin heuristic are stubbed; every probe runs for real.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from abicheck.extract.consumer_imports import (
    _parse_elf_app_requirements,
    _parse_macho_app_requirements,
    _parse_pe_app_requirements,
    detect_binary_file_format,
    read_consumer_imports,
)
from abicheck.model.availability import FactStatus
from abicheck.model.consumer_requirements import AppRequirements

_ELF = b"\x7fELF" + b"\x00" * 100
_PE_MZ = b"MZ" + b"\x00" * 100
_MACHO = b"\xfe\xed\xfa\xcf" + b"\x00" * 100
_PE_DIR = {"IMAGE_DIRECTORY_ENTRY_IMPORT": 1}


def _write(tmp_path, name, data):
    f = tmp_path / name
    f.write_bytes(data)
    return f


# ---------------------------------------------------------------------------
# detect_binary_file_format (was _detect_app_format)
# ---------------------------------------------------------------------------


class TestDetectBinaryFileFormat:
    def test_nonexistent_path(self, tmp_path):
        assert detect_binary_file_format(tmp_path / "nope") is None

    def test_unknown_format(self, tmp_path):
        assert detect_binary_file_format(_write(tmp_path, "u.bin", b"\0" * 4)) is None

    def test_elf_magic(self, tmp_path):
        assert detect_binary_file_format(_write(tmp_path, "app.elf", _ELF)) == "elf"

    def test_pe_magic(self, tmp_path):
        data = bytearray(512)
        data[0:2] = b"MZ"
        data[0x3C:0x40] = (0x80).to_bytes(4, "little")
        data[0x80:0x84] = b"PE\x00\x00"
        assert detect_binary_file_format(_write(tmp_path, "a.exe", bytes(data))) == "pe"

    def test_macho_magic(self, tmp_path):
        assert detect_binary_file_format(_write(tmp_path, "a.macho", _MACHO)) == "macho"

    def test_directory_returns_none(self, tmp_path):
        """Directories are not regular files."""
        assert detect_binary_file_format(tmp_path) is None

    @pytest.mark.parametrize(
        "magic",
        [
            b"\xfe\xed\xfa\xce",
            b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
            b"\xca\xfe\xba\xbf",
            b"\xbf\xba\xfe\xca",
        ],
    )
    def test_all_macho_magics(self, tmp_path, magic):
        f = _write(tmp_path, "app.macho", magic + b"\x00" * 100)
        assert detect_binary_file_format(f) == "macho"

    def test_pe_without_pe_signature(self, tmp_path):
        """MZ magic but no PE signature -> still 'pe' (MZ detected)."""
        assert detect_binary_file_format(_write(tmp_path, "a.exe", _PE_MZ)) == "pe"


# ---------------------------------------------------------------------------
# read_consumer_imports: per-format routing + explicit FAILED facts
# ---------------------------------------------------------------------------


def _pe_imp(name=None, ordinal=0, import_by_ordinal=False):
    return SimpleNamespace(
        name=name, ordinal=ordinal, import_by_ordinal=import_by_ordinal
    )


def _pe_entry(dll_name, imports):
    return SimpleNamespace(dll=dll_name.encode("utf-8"), imports=imports)


def _undef_sym(name, bind="STB_GLOBAL", shndx="SHN_UNDEF"):
    sym = MagicMock()
    sym.entry.st_shndx = shndx
    sym.name = name
    sym.entry.st_info.bind = bind
    return sym


def _dynsym(symbols):
    from elftools.elf.sections import SymbolTableSection

    section = MagicMock(spec=SymbolTableSection)
    section.name = ".dynsym"
    section.iter_symbols.return_value = symbols
    return section


def _elf_with(sections):
    elf = MagicMock()
    elf.iter_sections.return_value = sections
    return elf


def _versym(ndx=None, side_effect=None):
    from elftools.elf.gnuversions import GNUVerSymSection

    section = MagicMock(spec=GNUVerSymSection)
    if side_effect is not None:
        section.get_symbol.side_effect = side_effect
    else:
        entry = MagicMock()
        entry.entry = {"ndx": ndx}
        section.get_symbol.return_value = entry
    return section


class TestReadConsumerImports:
    def test_unknown_format_is_explicit_failed_fact(self, tmp_path):
        f = _write(tmp_path, "unknown.bin", b"\0" * 4)
        fact = read_consumer_imports(f, "libfoo.so")
        assert fact.status is FactStatus.FAILED
        assert fact.binary_format is None
        assert fact.is_readable is False
        assert fact.requirements == AppRequirements()
        assert fact.failure_reason == (
            f"Cannot detect binary format of '{f}'. "
            "Expected: ELF, PE, or Mach-O executable."
        )

    def test_missing_file_is_failed_not_raised(self, tmp_path):
        fact = read_consumer_imports(tmp_path / "absent", "libfoo.so")
        assert fact.status is FactStatus.FAILED
        assert fact.binary_format is None

    @pytest.mark.parametrize(
        "body", [b"\xff" * 60, b"\x00" * 100, b"\x02\x01\x01" + b"\xab" * 13]
    )
    def test_corrupt_elf_is_failed_with_reason_and_empty_requirements(
        self, tmp_path, body
    ):
        """Valid ELF magic, garbage body -- real pyelftools, no stubs."""
        f = _write(tmp_path, "corrupt.elf", b"\x7fELF" + body)
        fact = read_consumer_imports(f, "libfoo.so.1")
        assert fact.binary_format == "elf"
        assert fact.is_readable is True
        assert fact.status is FactStatus.FAILED
        assert fact.failure_reason
        assert fact.failure_reason.startswith("ELF import table unreadable")
        assert fact.requirements.undefined_symbols == set()
        assert fact.requirements.required_versions == {}

    def test_elf_dispatch(self, tmp_path):
        f = _write(tmp_path, "app.elf", _ELF)
        elf = _elf_with([_dynsym([_undef_sym("any_func")])])
        with patch("elftools.elf.elffile.ELFFile", return_value=elf):
            fact = read_consumer_imports(f, "")
        assert fact.binary_format == "elf"
        assert fact.status is FactStatus.PRESENT
        assert fact.failure_reason is None
        assert fact.target_library == ""
        assert fact.path == f
        assert fact.requirements.undefined_symbols == {"any_func"}

    def test_pe_dispatch(self, tmp_path):
        f = _write(tmp_path, "app.exe", _PE_MZ)
        pe = MagicMock()
        pe.DIRECTORY_ENTRY_IMPORT = [_pe_entry("foo.dll", [_pe_imp(name=b"Foo")])]
        with (
            patch("pefile.PE", return_value=pe),
            patch("pefile.DIRECTORY_ENTRY", _PE_DIR),
        ):
            fact = read_consumer_imports(f, "foo.dll")
        assert fact.binary_format == "pe"
        assert fact.status is FactStatus.PRESENT
        assert fact.target_library == "foo.dll"
        assert fact.requirements.undefined_symbols == {"Foo"}

    def test_pe_parse_error_is_failed(self, tmp_path):
        f = _write(tmp_path, "bad.exe", _PE_MZ)
        with patch("pefile.PE", side_effect=Exception("bad PE")):
            fact = read_consumer_imports(f, "foo.dll")
        assert fact.binary_format == "pe"
        assert fact.status is FactStatus.FAILED
        assert "bad PE" in fact.failure_reason
        assert fact.requirements.undefined_symbols == set()

    def test_macho_dispatch(self, tmp_path):
        f = _write(tmp_path, "app.macho", _MACHO)
        header = MagicMock()
        header.commands = []
        macho = MagicMock()
        macho.headers = [header]
        symtab = MagicMock()
        symtab.undefsyms = []
        with (
            patch("macholib.MachO.MachO", return_value=macho),
            patch("macholib.SymbolTable.SymbolTable", return_value=symtab),
        ):
            fact = read_consumer_imports(f, "libfoo.dylib")
        assert fact.binary_format == "macho"
        assert fact.status is FactStatus.PRESENT
        assert fact.target_library == "libfoo.dylib"


# ---------------------------------------------------------------------------
# PE parser
# ---------------------------------------------------------------------------


class TestParsePeAppRequirements:
    def _parse(self, tmp_path, entries, library):
        f = _write(tmp_path, "app.exe", _PE_MZ)
        pe = MagicMock()
        pe.DIRECTORY_ENTRY_IMPORT = entries
        with (
            patch("pefile.PE", return_value=pe),
            patch("pefile.DIRECTORY_ENTRY", _PE_DIR),
        ):
            return _parse_pe_app_requirements(f, library)

    def test_named_imports(self, tmp_path):
        entry = _pe_entry(
            "widget.dll", [_pe_imp(b"CreateWidget"), _pe_imp(b"DestroyWidget")]
        )
        reqs, failure = self._parse(tmp_path, [entry], "widget.dll")
        assert failure is None
        assert {"CreateWidget", "DestroyWidget"} <= reqs.undefined_symbols
        assert "widget.dll" in reqs.needed_libs

    def test_ordinal_only_imports(self, tmp_path):
        entry = _pe_entry("mylib.dll", [_pe_imp(ordinal=42, import_by_ordinal=True)])
        reqs, failure = self._parse(tmp_path, [entry], "mylib.dll")
        assert failure is None
        assert "ordinal:42" in reqs.undefined_symbols

    def test_filter_by_dll_name(self, tmp_path):
        entries = [
            _pe_entry("foo.dll", [_pe_imp(b"FooFunc")]),
            _pe_entry("bar.dll", [_pe_imp(b"BarFunc")]),
        ]
        reqs, _ = self._parse(tmp_path, entries, "foo.dll")
        assert "FooFunc" in reqs.undefined_symbols
        assert "BarFunc" not in reqs.undefined_symbols
        # Both DLLs still in needed_libs
        assert len(reqs.needed_libs) == 2

    def test_pe_parse_error(self, tmp_path):
        f = _write(tmp_path, "bad.exe", _PE_MZ)
        with patch("pefile.PE", side_effect=Exception("bad PE")):
            reqs, failure = _parse_pe_app_requirements(f, "foo.dll")
        assert reqs.undefined_symbols == set()
        assert failure == "PE import table unreadable: bad PE"

    def test_no_import_directory(self, tmp_path):
        f = _write(tmp_path, "app.exe", _PE_MZ)
        pe = MagicMock(spec=[])  # No DIRECTORY_ENTRY_IMPORT attribute
        pe.parse_data_directories = MagicMock()
        pe.close = MagicMock()
        with (
            patch("pefile.PE", return_value=pe),
            patch("pefile.DIRECTORY_ENTRY", _PE_DIR),
        ):
            reqs, failure = _parse_pe_app_requirements(f, "foo.dll")
        assert reqs.undefined_symbols == set()
        assert failure is None


# ---------------------------------------------------------------------------
# Mach-O parser
# ---------------------------------------------------------------------------


def _macho_with(commands):
    header = MagicMock()
    header.commands = commands
    macho = MagicMock()
    macho.headers = [header]
    return macho


class TestParseMachoAppRequirements:
    def test_no_headers(self, tmp_path):
        f = _write(tmp_path, "app.macho", _MACHO)
        macho = MagicMock()
        macho.headers = []
        with patch("macholib.MachO.MachO", return_value=macho):
            reqs, failure = _parse_macho_app_requirements(f, "libfoo.dylib")
        assert reqs.undefined_symbols == set()
        assert failure == "Mach-O file has no headers"

    def test_with_symbols(self, tmp_path):
        """Undefined symbols are filtered by library ordinal."""
        from macholib.mach_o import LC_LOAD_DYLIB, N_EXT, N_UNDF

        f = _write(tmp_path, "app.macho", _MACHO)
        lc = SimpleNamespace(cmd=LC_LOAD_DYLIB)
        macho = _macho_with([(lc, SimpleNamespace(), b"/usr/lib/libfoo.dylib\x00")])
        symtab = MagicMock()
        symtab.undefsyms = [
            (SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=(1 << 8)), b"_foo_init"),
            (SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=(2 << 8)), b"_bar_init"),
        ]
        with (
            patch("macholib.MachO.MachO", return_value=macho),
            patch("macholib.SymbolTable.SymbolTable", return_value=symtab),
        ):
            reqs, failure = _parse_macho_app_requirements(f, "libfoo.dylib")
        assert failure is None
        assert "foo_init" in reqs.undefined_symbols
        assert "bar_init" not in reqs.undefined_symbols
        assert "/usr/lib/libfoo.dylib" in reqs.needed_libs

    def test_no_library_filter(self, tmp_path):
        """When library_name is empty, all symbols are included."""
        from macholib.mach_o import N_EXT, N_UNDF

        f = _write(tmp_path, "app.macho", _MACHO)
        symtab = MagicMock()
        symtab.undefsyms = [
            (SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=0), b"_foo_func"),
            (SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=0), b"_bar_func"),
        ]
        with (
            patch("macholib.MachO.MachO", return_value=_macho_with([])),
            patch("macholib.SymbolTable.SymbolTable", return_value=symtab),
        ):
            reqs, _ = _parse_macho_app_requirements(f, "")
        assert {"foo_func", "bar_func"} <= reqs.undefined_symbols

    def test_symtab_failure(self, tmp_path):
        """A SymbolTable failure keeps the load commands and names the failure."""
        from macholib.mach_o import LC_LOAD_DYLIB

        f = _write(tmp_path, "app.macho", _MACHO)
        lc = SimpleNamespace(cmd=LC_LOAD_DYLIB)
        macho = _macho_with([(lc, SimpleNamespace(), b"libfoo.dylib\x00")])
        with (
            patch("macholib.MachO.MachO", return_value=macho),
            patch("macholib.SymbolTable.SymbolTable", side_effect=Exception("fail")),
        ):
            reqs, failure = _parse_macho_app_requirements(f, "libfoo.dylib")
        assert reqs.undefined_symbols == set()
        assert "libfoo.dylib" in reqs.needed_libs
        assert failure == "Mach-O symbol table unreadable: fail"

    def test_nlists_fallback(self, tmp_path):
        """When undefsyms is None, falls back to nlists with manual filtering."""
        from macholib.mach_o import N_EXT, N_SECT, N_UNDF

        f = _write(tmp_path, "app.macho", _MACHO)
        symtab = MagicMock()
        symtab.undefsyms = None
        symtab.nlists = [
            (SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=0), b"_foo_func"),
            (SimpleNamespace(n_type=N_SECT | N_EXT, n_desc=0), b"_bar_defined"),
        ]
        with (
            patch("macholib.MachO.MachO", return_value=_macho_with([])),
            patch("macholib.SymbolTable.SymbolTable", return_value=symtab),
        ):
            reqs, _ = _parse_macho_app_requirements(f, "")
        assert "foo_func" in reqs.undefined_symbols
        assert "bar_defined" not in reqs.undefined_symbols

    def test_data_no_null_terminator(self, tmp_path):
        """Data without a null terminator uses the full length."""
        from macholib.mach_o import LC_LOAD_DYLIB

        f = _write(tmp_path, "app.macho", _MACHO)
        lc = SimpleNamespace(cmd=LC_LOAD_DYLIB)
        macho = _macho_with([(lc, SimpleNamespace(), b"libfoo.dylib")])
        with (
            patch("macholib.MachO.MachO", return_value=macho),
            patch("macholib.SymbolTable.SymbolTable", side_effect=Exception("skip")),
        ):
            reqs, _ = _parse_macho_app_requirements(f, "libfoo.dylib")
        assert "libfoo.dylib" in reqs.needed_libs


# ---------------------------------------------------------------------------
# ELF parser
# ---------------------------------------------------------------------------


class TestParseElfAppRequirements:
    def _parse(self, tmp_path, sections, library, origin=None, patch_origin=True):
        f = _write(tmp_path, "app.elf", _ELF)
        with patch("elftools.elf.elffile.ELFFile", return_value=_elf_with(sections)):
            if not patch_origin:
                return _parse_elf_app_requirements(f, library)
            with patch(
                "abicheck.elf_metadata._guess_symbol_origin", return_value=origin
            ):
                return _parse_elf_app_requirements(f, library)

    def test_elf_parse_error(self, tmp_path):
        from elftools.common.exceptions import ELFError

        f = _write(tmp_path, "bad.elf", _ELF)
        with patch("elftools.elf.elffile.ELFFile", side_effect=ELFError("bad")):
            reqs, failure = _parse_elf_app_requirements(f, "libfoo.so")
        assert reqs.undefined_symbols == set()
        assert failure == "ELF import table unreadable: bad"

    def test_elf_os_error(self, tmp_path):
        reqs, failure = _parse_elf_app_requirements(tmp_path / "missing", "libfoo.so")
        assert reqs.undefined_symbols == set()
        assert failure is not None

    def test_elf_full_parsing(self, tmp_path):
        """DT_NEEDED, .gnu.version_r and .gnu.version drive the target filter."""
        from elftools.elf.dynamic import DynamicSection
        from elftools.elf.gnuversions import GNUVerNeedSection, GNUVerSymSection

        needed = MagicMock()
        needed.entry.d_tag = "DT_NEEDED"
        needed.needed = "libfoo.so.1"
        other = MagicMock()
        other.entry.d_tag = "DT_NULL"
        dynamic = MagicMock(spec=DynamicSection)
        dynamic.iter_tags.return_value = [needed, other]

        vernaux = MagicMock()
        vernaux.entry.vna_other = 2
        vernaux.name = "FOO_1.0"
        verneed = MagicMock()
        verneed.name = "libfoo.so.1"
        verneed_section = MagicMock(spec=GNUVerNeedSection)
        verneed_section.iter_versions.return_value = [(verneed, [vernaux])]

        entries = []
        for ndx in (2, 3, 1):
            e = MagicMock()
            e.entry = {"ndx": ndx}
            entries.append(e)
        versym = MagicMock(spec=GNUVerSymSection)
        versym.get_symbol.side_effect = lambda idx: entries[idx]

        dynsym = _dynsym(
            [
                _undef_sym("foo_init"),  # idx 0 -> ver 2 (libfoo.so.1)
                _undef_sym("bar_init"),  # idx 1 -> ver 3 (other lib)
                _undef_sym("unknown_func"),  # idx 2 -> unversioned
                _undef_sym("defined_func", shndx=1),  # not UNDEF
                _undef_sym(""),  # empty name
                _undef_sym("local_func", bind="STB_LOCAL"),
            ]
        )
        reqs, failure = self._parse(
            tmp_path, [dynamic, verneed_section, versym, dynsym], "libfoo.so.1"
        )
        assert failure is None
        assert "foo_init" in reqs.undefined_symbols
        assert "bar_init" not in reqs.undefined_symbols  # from other lib
        assert "unknown_func" in reqs.undefined_symbols  # unversioned, no origin
        assert "defined_func" not in reqs.undefined_symbols
        assert "local_func" not in reqs.undefined_symbols
        assert "libfoo.so.1" in reqs.needed_libs
        assert "FOO_1.0" in reqs.required_versions

    def test_elf_unversioned_symbol_from_known_lib(self, tmp_path):
        """Unversioned symbol attributed to another lib is excluded."""
        reqs, _ = self._parse(
            tmp_path,
            [_versym(ndx=1), _dynsym([_undef_sym("printf")])],
            "libfoo.so.1",
            origin="libc.so.6",
        )
        assert "printf" not in reqs.undefined_symbols

    def test_elf_versym_index_error(self, tmp_path):
        """IndexError from get_symbol falls back to unversioned."""
        reqs, _ = self._parse(
            tmp_path,
            [
                _versym(side_effect=IndexError("oob")),
                _dynsym([_undef_sym("some_func")]),
            ],
            "libfoo.so.1",
        )
        assert "some_func" in reqs.undefined_symbols

    def test_elf_versym_string_ndx(self, tmp_path):
        """String ndx like 'VER_NDX_GLOBAL' maps to unversioned."""
        reqs, _ = self._parse(
            tmp_path,
            [_versym(ndx="VER_NDX_GLOBAL"), _dynsym([_undef_sym("my_func")])],
            "libfoo.so.1",
        )
        assert "my_func" in reqs.undefined_symbols

    def test_elf_weak_unversioned_excluded(self, tmp_path):
        """Weak undefined symbols with unknown origin are excluded."""
        reqs, _ = self._parse(
            tmp_path,
            [
                _versym(ndx="VER_NDX_GLOBAL"),
                _dynsym([_undef_sym("__gmon_start__", bind="STB_WEAK")]),
            ],
            "libfoo.so.1",
        )
        assert "__gmon_start__" not in reqs.undefined_symbols

    def test_elf_no_library_filter(self, tmp_path):
        """When library_soname is empty, all UNDEF symbols are included."""
        reqs, _ = self._parse(
            tmp_path, [_dynsym([_undef_sym("any_func")])], "", patch_origin=False
        )
        assert "any_func" in reqs.undefined_symbols

    def test_elf_without_versym_includes_symbol_from_target_origin(self, tmp_path):
        reqs, _ = self._parse(
            tmp_path,
            [_dynsym([_undef_sym("foo_init")])],
            "libfoo.so.1",
            origin="libfoo.so.1",
        )
        assert "foo_init" in reqs.undefined_symbols

    def test_elf_without_versym_excludes_symbol_from_other_origin(self, tmp_path):
        reqs, _ = self._parse(
            tmp_path,
            [_dynsym([_undef_sym("printf")])],
            "libfoo.so.1",
            origin="libc.so.6",
        )
        assert "printf" not in reqs.undefined_symbols
