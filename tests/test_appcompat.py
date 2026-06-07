# Copyright 2026 Nikolay Petrov
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

"""Tests for abicheck.appcompat — Application Compatibility Checking (ADR-005)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from abicheck.appcompat import (
    AppCompatResult,
    AppRequirements,
    _detect_app_format,
    _get_lib_soname,
    _get_new_lib_exports,
    _get_old_lib_exports_for_scoping,
    _is_relevant_to_app,
    _normalize_elf_symbol_name,
    _parse_elf_app_requirements,
    _parse_macho_app_requirements,
    _parse_pe_app_requirements,
    check_against,
    check_appcompat,
    parse_app_requirements,
)
from abicheck.checker import Change, DiffResult
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.reporter import appcompat_to_json, appcompat_to_markdown

# ---------------------------------------------------------------------------
# Unit tests: AppRequirements / AppCompatResult data structures
# ---------------------------------------------------------------------------

class TestDataStructures:
    def test_app_requirements_defaults(self):
        reqs = AppRequirements()
        assert reqs.needed_libs == []
        assert reqs.undefined_symbols == set()
        assert reqs.required_versions == {}

    def test_app_requirements_populated(self):
        reqs = AppRequirements(
            needed_libs=["libfoo.so.1", "libc.so.6"],
            undefined_symbols={"foo_init", "foo_process"},
            required_versions={"FOO_1.0": "libfoo.so.1"},
        )
        assert len(reqs.needed_libs) == 2
        assert "foo_init" in reqs.undefined_symbols
        assert reqs.required_versions["FOO_1.0"] == "libfoo.so.1"

    def test_app_compat_result_defaults(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
        )
        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0
        assert result.missing_symbols == []
        assert result.breaking_for_app == []


# ---------------------------------------------------------------------------
# Unit tests: _is_relevant_to_app
# ---------------------------------------------------------------------------

class TestIsRelevantToApp:
    def _make_app(
        self,
        symbols: set[str] | None = None,
        versions: dict[str, str] | None = None,
        needed_libs: list[str] | None = None,
    ):
        return AppRequirements(
            needed_libs=needed_libs or [],
            undefined_symbols=symbols or {"foo_init", "foo_process", "foo_cleanup"},
            required_versions=versions or {},
        )

    def test_direct_symbol_match(self):
        app = self._make_app()
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo_init",
            description="Function removed: foo_init",
        )
        assert _is_relevant_to_app(change, app) is True

    def test_no_match(self):
        app = self._make_app()
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="bar_init",
            description="Function removed: bar_init",
        )
        assert _is_relevant_to_app(change, app) is False

    def test_affected_symbols_match(self):
        app = self._make_app()
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Config",
            description="Type size changed: Config",
            affected_symbols=["foo_init", "bar_init"],
        )
        assert _is_relevant_to_app(change, app) is True

    def test_affected_symbols_no_match(self):
        app = self._make_app()
        change = Change(
            kind=ChangeKind.TYPE_SIZE_CHANGED,
            symbol="Config",
            description="Type size changed: Config",
            affected_symbols=["bar_init", "baz_init"],
        )
        assert _is_relevant_to_app(change, app) is False

    def test_soname_changed_relevant_when_app_needs_old_soname(self):
        app = self._make_app(needed_libs=["libfoo.so.1"])
        change = Change(
            kind=ChangeKind.SONAME_CHANGED,
            symbol="",
            description="SONAME changed",
            old_value="libfoo.so.1",
            new_value="libfoo.so.2",
        )
        assert _is_relevant_to_app(change, app) is True

    def test_soname_changed_not_relevant_without_old_needed_soname(self):
        app = self._make_app(needed_libs=["libbar.so.1"])
        change = Change(
            kind=ChangeKind.SONAME_CHANGED,
            symbol="",
            description="SONAME changed",
            old_value="libfoo.so.1",
            new_value="libfoo.so.2",
        )
        assert _is_relevant_to_app(change, app) is False

    def test_compat_version_changed_always_relevant(self):
        app = self._make_app()
        change = Change(
            kind=ChangeKind.COMPAT_VERSION_CHANGED,
            symbol="",
            description="Mach-O compat version changed",
        )
        assert _is_relevant_to_app(change, app) is True

    def test_symbol_version_removed_relevant(self):
        app = self._make_app(
            versions={"FOO_1.0": "libfoo.so.1"},
        )
        change = Change(
            kind=ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
            symbol="FOO_1.0",
            description="Symbol version removed",
            old_value="FOO_1.0",
        )
        assert _is_relevant_to_app(change, app) is True

    def test_symbol_version_removed_different_version(self):
        app = self._make_app(
            versions={"FOO_1.0": "libfoo.so.1"},
        )
        change = Change(
            kind=ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
            symbol="FOO_2.0",
            description="Symbol version removed",
            old_value="FOO_2.0",
        )
        assert _is_relevant_to_app(change, app) is False


# ---------------------------------------------------------------------------
# Unit tests: _detect_app_format
# ---------------------------------------------------------------------------

class TestDetectAppFormat:
    def test_nonexistent_path(self, tmp_path):
        assert _detect_app_format(tmp_path / "nope") is None

    def test_unknown_format(self, tmp_path):
        f = tmp_path / "unknown.bin"
        f.write_bytes(b"\x00\x00\x00\x00")
        assert _detect_app_format(f) is None

    def test_elf_magic(self, tmp_path):
        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        assert _detect_app_format(f) == "elf"

    def test_pe_magic(self, tmp_path):
        data = bytearray(512)
        data[0:2] = b"MZ"
        data[0x3C:0x40] = (0x80).to_bytes(4, "little")
        data[0x80:0x84] = b"PE\x00\x00"
        f = tmp_path / "app.exe"
        f.write_bytes(bytes(data))
        assert _detect_app_format(f) == "pe"

    def test_macho_magic(self, tmp_path):
        f = tmp_path / "app.macho"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)
        assert _detect_app_format(f) == "macho"


# ---------------------------------------------------------------------------
# Unit tests: AppCompatResult verdict computation
# ---------------------------------------------------------------------------

class TestAppCompatResultVerdict:
    def test_missing_symbols_means_breaking(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            missing_symbols=["foo_init"],
            verdict=Verdict.BREAKING,
        )
        assert result.verdict == Verdict.BREAKING

    def test_missing_versions_means_breaking(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            missing_versions=["FOO_1.0"],
            verdict=Verdict.BREAKING,
        )
        assert result.verdict == Verdict.BREAKING

    def test_no_changes_means_compatible(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            required_symbol_count=5,
            verdict=Verdict.COMPATIBLE,
        )
        assert result.verdict == Verdict.COMPATIBLE


# ---------------------------------------------------------------------------
# Unit tests: reporters
# ---------------------------------------------------------------------------

class TestAppCompatReporters:
    def _make_result(self, *, missing=None, breaking=None, irrelevant=None, verdict=None):
        return AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="libfoo.so.1",
            new_lib_path="libfoo.so.2",
            required_symbols={"foo_init", "foo_process", "foo_cleanup"},
            required_symbol_count=3,
            breaking_for_app=breaking or [],
            irrelevant_for_app=irrelevant or [],
            missing_symbols=missing or [],
            missing_versions=[],
            full_diff=DiffResult(
                old_version="1.0",
                new_version="2.0",
                library="libfoo",
            ),
            verdict=verdict or Verdict.COMPATIBLE,
            symbol_coverage=100.0,
        )

    def test_markdown_compatible(self):
        result = self._make_result()
        md = appcompat_to_markdown(result)
        assert "# Application Compatibility Report" in md
        assert "COMPATIBLE" in md
        assert "/usr/bin/myapp" in md

    def test_markdown_with_missing_symbols(self):
        result = self._make_result(
            missing=["foo_init"],
            verdict=Verdict.BREAKING,
        )
        md = appcompat_to_markdown(result)
        assert "Missing Symbols" in md
        assert "foo_init" in md

    def test_markdown_with_relevant_changes(self):
        change = Change(
            kind=ChangeKind.FUNC_PARAMS_CHANGED,
            symbol="foo_process",
            description="parameter type changed",
        )
        result = self._make_result(breaking=[change])
        md = appcompat_to_markdown(result)
        assert "Relevant Changes" in md
        assert "foo_process" in md

    def test_markdown_show_irrelevant(self):
        change = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="bar_new",
            description="function added: bar_new",
        )
        result = self._make_result(irrelevant=[change])
        md = appcompat_to_markdown(result, show_irrelevant=True)
        assert "Irrelevant Changes" in md
        assert "bar_new" in md

    def test_markdown_hide_irrelevant_default(self):
        change = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="bar_new",
            description="function added",
        )
        result = self._make_result(irrelevant=[change])
        md = appcompat_to_markdown(result)
        assert "--show-irrelevant" in md

    def test_json_output(self):
        result = self._make_result()
        j = appcompat_to_json(result)
        data = json.loads(j)
        assert data["verdict"] == "COMPATIBLE"
        assert data["application"] == "/usr/bin/myapp"
        assert data["required_symbol_count"] == 3

    def test_json_with_missing(self):
        result = self._make_result(
            missing=["foo_init"],
            verdict=Verdict.BREAKING,
        )
        j = appcompat_to_json(result)
        data = json.loads(j)
        assert data["verdict"] == "BREAKING"
        assert "foo_init" in data["missing_symbols"]

    def test_json_with_relevant_changes(self):
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo_init",
            description="Function removed",
        )
        result = self._make_result(breaking=[change])
        j = appcompat_to_json(result)
        data = json.loads(j)
        assert data["relevant_change_count"] == 1
        assert data["relevant_changes"][0]["symbol"] == "foo_init"

    def test_markdown_weak_mode(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="",
            new_lib_path="libfoo.so.2",
            required_symbols={"foo_init"},
            required_symbol_count=1,
            verdict=Verdict.COMPATIBLE,
            symbol_coverage=100.0,
        )
        md = appcompat_to_markdown(result)
        assert "libfoo.so.2" in md
        # Weak mode: no old lib shown with arrow
        assert "→" not in md


# ---------------------------------------------------------------------------
# CLI smoke test
# ---------------------------------------------------------------------------

class TestAppcompatCLI:
    def test_appcompat_help(self):
        from click.testing import CliRunner

        from abicheck.cli import main

        runner = CliRunner()
        result = runner.invoke(main, ["appcompat", "--help"])
        assert result.exit_code == 0
        assert "Check if an application is compatible" in result.output

    def test_appcompat_missing_args(self):
        from click.testing import CliRunner

        from abicheck.cli import main

        runner = CliRunner()
        # No arguments at all
        result = runner.invoke(main, ["appcompat"])
        assert result.exit_code != 0

    def test_appcompat_weak_mode_with_positional_fails(self, tmp_path):
        from click.testing import CliRunner

        from abicheck.cli import main

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)
        lib1 = tmp_path / "lib1.so"
        lib1.write_bytes(b"\x7fELF" + b"\x00" * 100)
        lib2 = tmp_path / "lib2.so"
        lib2.write_bytes(b"\x7fELF" + b"\x00" * 100)

        runner = CliRunner()
        result = runner.invoke(main, [
            "appcompat", str(app), str(lib1), str(lib2),
            "--check-against", str(lib2),
        ])
        assert result.exit_code != 0
        assert "cannot be used with" in result.output


# ---------------------------------------------------------------------------
# Integration-ish: _is_relevant_to_app with realistic change sets
# ---------------------------------------------------------------------------

class TestFilteringIntegration:
    """Test that filtering correctly partitions changes."""

    def test_mixed_changes_partitioned(self):
        app = AppRequirements(
            undefined_symbols={"foo_init", "foo_process"},
        )
        changes = [
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="foo_init", description="removed"),
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="bar_init", description="removed"),
            Change(kind=ChangeKind.FUNC_ADDED, symbol="baz_new", description="added"),
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Config",
                   description="size changed", affected_symbols=["foo_process"]),
            Change(kind=ChangeKind.TYPE_SIZE_CHANGED, symbol="Internal",
                   description="size changed", affected_symbols=["bar_helper"]),
        ]

        relevant = [c for c in changes if _is_relevant_to_app(c, app)]
        irrelevant = [c for c in changes if not _is_relevant_to_app(c, app)]

        assert len(relevant) == 2  # foo_init removed + Config size (affects foo_process)
        assert len(irrelevant) == 3  # bar_init removed + baz_new added + Internal size
        assert relevant[0].symbol == "foo_init"
        assert relevant[1].symbol == "Config"


# ---------------------------------------------------------------------------
# Unit tests: _detect_app_format edge cases
# ---------------------------------------------------------------------------

class TestDetectAppFormatEdgeCases:
    def test_directory_returns_none(self, tmp_path):
        """Directories are not regular files."""
        assert _detect_app_format(tmp_path) is None

    def test_all_macho_magics(self, tmp_path):
        """All recognized Mach-O magic bytes should return 'macho'."""
        magics = [
            b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe",
            b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
            b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
        ]
        for i, magic in enumerate(magics):
            f = tmp_path / f"app_{i}.macho"
            f.write_bytes(magic + b"\x00" * 100)
            assert _detect_app_format(f) == "macho"

    def test_pe_without_pe_signature(self, tmp_path):
        """MZ magic but no PE signature → still returns 'pe' (MZ detected)."""
        f = tmp_path / "app.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)
        assert _detect_app_format(f) == "pe"


# ---------------------------------------------------------------------------
# Unit tests: parse_app_requirements dispatch
# ---------------------------------------------------------------------------

class TestParseAppRequirements:
    def test_unknown_format_raises(self, tmp_path):
        f = tmp_path / "unknown.bin"
        f.write_bytes(b"\x00\x00\x00\x00")
        with pytest.raises(ValueError, match="Cannot detect binary format"):
            parse_app_requirements(f, "libfoo.so")

    def test_elf_dispatch(self, tmp_path):
        """ELF format dispatches to _parse_elf_app_requirements."""
        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        with patch("abicheck.appcompat._parse_elf_app_requirements") as mock:
            mock.return_value = AppRequirements()
            result = parse_app_requirements(f, "libfoo.so")
            mock.assert_called_once_with(f, "libfoo.so")
            assert isinstance(result, AppRequirements)

    def test_pe_dispatch(self, tmp_path):
        f = tmp_path / "app.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)
        with patch("abicheck.appcompat._parse_pe_app_requirements") as mock:
            mock.return_value = AppRequirements()
            result = parse_app_requirements(f, "foo.dll")
            mock.assert_called_once_with(f, "foo.dll")
            assert isinstance(result, AppRequirements)

    def test_macho_dispatch(self, tmp_path):
        f = tmp_path / "app.macho"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)
        with patch("abicheck.appcompat._parse_macho_app_requirements") as mock:
            mock.return_value = AppRequirements()
            result = parse_app_requirements(f, "libfoo.dylib")
            mock.assert_called_once_with(f, "libfoo.dylib")
            assert isinstance(result, AppRequirements)


# ---------------------------------------------------------------------------
# Unit tests: _parse_pe_app_requirements with mocks
# ---------------------------------------------------------------------------

class TestParsePeAppRequirements:
    def _make_imp(self, name=None, ordinal=0, import_by_ordinal=False):
        imp = SimpleNamespace()
        imp.name = name
        imp.ordinal = ordinal
        imp.import_by_ordinal = import_by_ordinal
        return imp

    def _make_entry(self, dll_name, imports):
        entry = SimpleNamespace()
        entry.dll = dll_name.encode("utf-8")
        entry.imports = imports
        return entry

    def test_named_imports(self, tmp_path):
        f = tmp_path / "app.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)

        imp1 = self._make_imp(name=b"CreateWidget")
        imp2 = self._make_imp(name=b"DestroyWidget")
        entry = self._make_entry("widget.dll", [imp1, imp2])

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_IMPORT = [entry]

        with patch("pefile.PE", return_value=mock_pe):
            with patch("pefile.DIRECTORY_ENTRY", {"IMAGE_DIRECTORY_ENTRY_IMPORT": 1}):
                reqs = _parse_pe_app_requirements(f, "widget.dll")

        assert "CreateWidget" in reqs.undefined_symbols
        assert "DestroyWidget" in reqs.undefined_symbols
        assert "widget.dll" in reqs.needed_libs

    def test_ordinal_only_imports(self, tmp_path):
        f = tmp_path / "app.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)

        imp = self._make_imp(name=None, ordinal=42, import_by_ordinal=True)
        entry = self._make_entry("mylib.dll", [imp])

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_IMPORT = [entry]

        with patch("pefile.PE", return_value=mock_pe):
            with patch("pefile.DIRECTORY_ENTRY", {"IMAGE_DIRECTORY_ENTRY_IMPORT": 1}):
                reqs = _parse_pe_app_requirements(f, "mylib.dll")

        assert "ordinal:42" in reqs.undefined_symbols

    def test_filter_by_dll_name(self, tmp_path):
        f = tmp_path / "app.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)

        imp1 = self._make_imp(name=b"FooFunc")
        imp2 = self._make_imp(name=b"BarFunc")
        entry_foo = self._make_entry("foo.dll", [imp1])
        entry_bar = self._make_entry("bar.dll", [imp2])

        mock_pe = MagicMock()
        mock_pe.DIRECTORY_ENTRY_IMPORT = [entry_foo, entry_bar]

        with patch("pefile.PE", return_value=mock_pe):
            with patch("pefile.DIRECTORY_ENTRY", {"IMAGE_DIRECTORY_ENTRY_IMPORT": 1}):
                reqs = _parse_pe_app_requirements(f, "foo.dll")

        assert "FooFunc" in reqs.undefined_symbols
        assert "BarFunc" not in reqs.undefined_symbols
        # Both DLLs still in needed_libs
        assert len(reqs.needed_libs) == 2

    def test_pe_parse_error(self, tmp_path):
        f = tmp_path / "bad.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)

        with patch("pefile.PE", side_effect=Exception("bad PE")):
            reqs = _parse_pe_app_requirements(f, "foo.dll")

        assert reqs.undefined_symbols == set()

    def test_no_import_directory(self, tmp_path):
        f = tmp_path / "app.exe"
        f.write_bytes(b"MZ" + b"\x00" * 100)

        mock_pe = MagicMock(spec=[])  # No DIRECTORY_ENTRY_IMPORT attribute
        mock_pe.parse_data_directories = MagicMock()
        mock_pe.close = MagicMock()

        with patch("pefile.PE", return_value=mock_pe):
            with patch("pefile.DIRECTORY_ENTRY", {"IMAGE_DIRECTORY_ENTRY_IMPORT": 1}):
                reqs = _parse_pe_app_requirements(f, "foo.dll")

        assert reqs.undefined_symbols == set()


# ---------------------------------------------------------------------------
# Unit tests: _parse_macho_app_requirements with mocks
# ---------------------------------------------------------------------------

class TestParseMachoAppRequirements:
    def test_no_headers(self, tmp_path):
        f = tmp_path / "app.macho"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        mock_macho = MagicMock()
        mock_macho.headers = []

        with patch("macholib.MachO.MachO", return_value=mock_macho):
            reqs = _parse_macho_app_requirements(f, "libfoo.dylib")

        assert reqs.undefined_symbols == set()

    def test_with_symbols(self, tmp_path):
        """Test extraction of undefined symbols with library ordinal filtering."""
        from macholib.mach_o import LC_LOAD_DYLIB, N_EXT, N_UNDF

        f = tmp_path / "app.macho"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        # Build load commands: one LC_LOAD_DYLIB for libfoo.dylib
        lc = SimpleNamespace(cmd=LC_LOAD_DYLIB)
        cmd = SimpleNamespace()
        data = b"/usr/lib/libfoo.dylib\x00"

        header = MagicMock()
        header.commands = [(lc, cmd, data)]

        # Build symbol table entries
        # Symbol from libfoo.dylib (ordinal=1)
        nlist1 = SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=(1 << 8))
        # Symbol from different lib (ordinal=2)
        nlist2 = SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=(2 << 8))

        mock_symtab = MagicMock()
        mock_symtab.undefsyms = [
            (nlist1, b"_foo_init"),
            (nlist2, b"_bar_init"),
        ]

        mock_macho = MagicMock()
        mock_macho.headers = [header]

        with patch("macholib.MachO.MachO", return_value=mock_macho):
            with patch("macholib.SymbolTable.SymbolTable", return_value=mock_symtab):
                reqs = _parse_macho_app_requirements(f, "libfoo.dylib")

        assert "foo_init" in reqs.undefined_symbols
        assert "bar_init" not in reqs.undefined_symbols
        assert "/usr/lib/libfoo.dylib" in reqs.needed_libs

    def test_no_library_filter(self, tmp_path):
        """When library_name is empty, all symbols are included."""
        from macholib.mach_o import N_EXT, N_UNDF

        f = tmp_path / "app.macho"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        header = MagicMock()
        header.commands = []

        nlist1 = SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=0)
        nlist2 = SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=0)

        mock_symtab = MagicMock()
        mock_symtab.undefsyms = [
            (nlist1, b"_foo_func"),
            (nlist2, b"_bar_func"),
        ]

        mock_macho = MagicMock()
        mock_macho.headers = [header]

        with patch("macholib.MachO.MachO", return_value=mock_macho):
            with patch("macholib.SymbolTable.SymbolTable", return_value=mock_symtab):
                reqs = _parse_macho_app_requirements(f, "")

        assert "foo_func" in reqs.undefined_symbols
        assert "bar_func" in reqs.undefined_symbols

    def test_symtab_failure(self, tmp_path):
        """SymbolTable failure is caught, returns empty symbols."""
        from macholib.mach_o import LC_LOAD_DYLIB

        f = tmp_path / "app.macho"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        lc = SimpleNamespace(cmd=LC_LOAD_DYLIB)
        data = b"libfoo.dylib\x00"
        header = MagicMock()
        header.commands = [(lc, SimpleNamespace(), data)]

        mock_macho = MagicMock()
        mock_macho.headers = [header]

        with patch("macholib.MachO.MachO", return_value=mock_macho):
            with patch("macholib.SymbolTable.SymbolTable", side_effect=Exception("fail")):
                reqs = _parse_macho_app_requirements(f, "libfoo.dylib")

        assert reqs.undefined_symbols == set()
        assert "libfoo.dylib" in reqs.needed_libs

    def test_nlists_fallback(self, tmp_path):
        """When undefsyms is None, falls back to nlists with manual filtering."""
        from macholib.mach_o import N_EXT, N_SECT, N_UNDF

        f = tmp_path / "app.macho"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        header = MagicMock()
        header.commands = []

        # Undefined external symbol
        nlist_undef = SimpleNamespace(n_type=N_UNDF | N_EXT, n_desc=0)
        # Defined external symbol (N_SECT | N_EXT → should be skipped)
        nlist_def = SimpleNamespace(n_type=N_SECT | N_EXT, n_desc=0)

        mock_symtab = MagicMock()
        mock_symtab.undefsyms = None
        mock_symtab.nlists = [
            (nlist_undef, b"_foo_func"),
            (nlist_def, b"_bar_defined"),
        ]

        mock_macho = MagicMock()
        mock_macho.headers = [header]

        with patch("macholib.MachO.MachO", return_value=mock_macho):
            with patch("macholib.SymbolTable.SymbolTable", return_value=mock_symtab):
                reqs = _parse_macho_app_requirements(f, "")

        assert "foo_func" in reqs.undefined_symbols
        assert "bar_defined" not in reqs.undefined_symbols

    def test_data_no_null_terminator(self, tmp_path):
        """Data without null terminator uses full length."""
        from macholib.mach_o import LC_LOAD_DYLIB

        f = tmp_path / "app.macho"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        lc = SimpleNamespace(cmd=LC_LOAD_DYLIB)
        data = b"libfoo.dylib"  # No null terminator

        header = MagicMock()
        header.commands = [(lc, SimpleNamespace(), data)]

        mock_macho = MagicMock()
        mock_macho.headers = [header]

        with patch("macholib.MachO.MachO", return_value=mock_macho):
            with patch("macholib.SymbolTable.SymbolTable", side_effect=Exception("skip")):
                reqs = _parse_macho_app_requirements(f, "libfoo.dylib")

        assert "libfoo.dylib" in reqs.needed_libs


# ---------------------------------------------------------------------------
# Unit tests: _parse_elf_app_requirements with mocks
# ---------------------------------------------------------------------------

class TestParseElfAppRequirements:
    def test_elf_parse_error(self, tmp_path):
        f = tmp_path / "bad.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        from elftools.common.exceptions import ELFError

        with patch("elftools.elf.elffile.ELFFile", side_effect=ELFError("bad")):
            reqs = _parse_elf_app_requirements(f, "libfoo.so")

        assert reqs.undefined_symbols == set()

    def test_elf_os_error(self, tmp_path):
        f = tmp_path / "missing.elf"  # doesn't exist
        reqs = _parse_elf_app_requirements(f, "libfoo.so")
        assert reqs.undefined_symbols == set()

    def test_elf_full_parsing(self, tmp_path):
        """Test ELF parsing with mocked pyelftools sections."""
        from elftools.elf.dynamic import DynamicSection
        from elftools.elf.gnuversions import GNUVerNeedSection, GNUVerSymSection
        from elftools.elf.sections import SymbolTableSection

        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        # Mock DT_NEEDED tag
        mock_tag = MagicMock()
        mock_tag.entry.d_tag = "DT_NEEDED"
        mock_tag.needed = "libfoo.so.1"

        mock_other_tag = MagicMock()
        mock_other_tag.entry.d_tag = "DT_NULL"

        # Mock DynamicSection
        mock_dynamic = MagicMock(spec=DynamicSection)
        mock_dynamic.iter_tags.return_value = [mock_tag, mock_other_tag]

        # Mock GNUVerNeedSection
        mock_vernaux = MagicMock()
        mock_vernaux.entry.vna_other = 2
        mock_vernaux.name = "FOO_1.0"

        mock_verneed = MagicMock()
        mock_verneed.name = "libfoo.so.1"

        mock_verneed_section = MagicMock(spec=GNUVerNeedSection)
        mock_verneed_section.iter_versions.return_value = [
            (mock_verneed, [mock_vernaux]),
        ]

        # Mock GNUVerSymSection
        mock_versym_section = MagicMock(spec=GNUVerSymSection)

        # Symbol with version index 2 (from libfoo.so.1)
        mock_ver_entry_2 = MagicMock()
        mock_ver_entry_2.entry = {"ndx": 2}

        # Symbol with version index 3 (from other lib)
        mock_ver_entry_3 = MagicMock()
        mock_ver_entry_3.entry = {"ndx": 3}

        # Unversioned symbol (index 1)
        mock_ver_entry_1 = MagicMock()
        mock_ver_entry_1.entry = {"ndx": 1}

        def get_symbol_side_effect(idx):
            return [mock_ver_entry_2, mock_ver_entry_3, mock_ver_entry_1][idx]

        mock_versym_section.get_symbol.side_effect = get_symbol_side_effect

        # Mock SymbolTableSection (.dynsym)
        mock_sym_from_foo = MagicMock()
        mock_sym_from_foo.entry.st_shndx = "SHN_UNDEF"
        mock_sym_from_foo.name = "foo_init"
        mock_sym_from_foo.entry.st_info.bind = "STB_GLOBAL"

        mock_sym_from_other = MagicMock()
        mock_sym_from_other.entry.st_shndx = "SHN_UNDEF"
        mock_sym_from_other.name = "bar_init"
        mock_sym_from_other.entry.st_info.bind = "STB_GLOBAL"

        mock_sym_unversioned = MagicMock()
        mock_sym_unversioned.entry.st_shndx = "SHN_UNDEF"
        mock_sym_unversioned.name = "unknown_func"
        mock_sym_unversioned.entry.st_info.bind = "STB_GLOBAL"

        # Non-UNDEF symbol (should be skipped)
        mock_sym_defined = MagicMock()
        mock_sym_defined.entry.st_shndx = 1  # defined section
        mock_sym_defined.name = "defined_func"
        mock_sym_defined.entry.st_info.bind = "STB_GLOBAL"

        # Empty name symbol (should be skipped)
        mock_sym_empty = MagicMock()
        mock_sym_empty.entry.st_shndx = "SHN_UNDEF"
        mock_sym_empty.name = ""
        mock_sym_empty.entry.st_info.bind = "STB_GLOBAL"

        # Local binding (should be skipped)
        mock_sym_local = MagicMock()
        mock_sym_local.entry.st_shndx = "SHN_UNDEF"
        mock_sym_local.name = "local_func"
        mock_sym_local.entry.st_info.bind = "STB_LOCAL"

        mock_dynsym = MagicMock(spec=SymbolTableSection)
        mock_dynsym.name = ".dynsym"
        mock_dynsym.iter_symbols.return_value = [
            mock_sym_from_foo,     # idx=0 → ver_entry_2 (from libfoo.so.1)
            mock_sym_from_other,   # idx=1 → ver_entry_3 (from other lib)
            mock_sym_unversioned,  # idx=2 → ver_entry_1 (unversioned)
            mock_sym_defined,      # idx=3 → should be skipped (not UNDEF)
            mock_sym_empty,        # idx=4 → should be skipped (empty name)
            mock_sym_local,        # idx=5 → should be skipped (local binding)
        ]

        sections = [mock_dynamic, mock_verneed_section, mock_versym_section, mock_dynsym]

        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = sections

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf):
            with patch("abicheck.elf_metadata._guess_symbol_origin", return_value=None):
                reqs = _parse_elf_app_requirements(f, "libfoo.so.1")

        assert "foo_init" in reqs.undefined_symbols
        assert "bar_init" not in reqs.undefined_symbols  # from other lib
        assert "unknown_func" in reqs.undefined_symbols  # unversioned, no known origin
        assert "defined_func" not in reqs.undefined_symbols
        assert "local_func" not in reqs.undefined_symbols
        assert "libfoo.so.1" in reqs.needed_libs
        assert "FOO_1.0" in reqs.required_versions

    def test_elf_unversioned_symbol_from_known_lib(self, tmp_path):
        """Unversioned symbol identified as from another lib should be excluded."""
        from elftools.elf.gnuversions import GNUVerSymSection
        from elftools.elf.sections import SymbolTableSection

        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_versym = MagicMock(spec=GNUVerSymSection)
        mock_ver_entry = MagicMock()
        mock_ver_entry.entry = {"ndx": 1}  # unversioned
        mock_versym.get_symbol.return_value = mock_ver_entry

        mock_sym = MagicMock()
        mock_sym.entry.st_shndx = "SHN_UNDEF"
        mock_sym.name = "printf"
        mock_sym.entry.st_info.bind = "STB_GLOBAL"

        mock_dynsym = MagicMock(spec=SymbolTableSection)
        mock_dynsym.name = ".dynsym"
        mock_dynsym.iter_symbols.return_value = [mock_sym]

        sections = [mock_versym, mock_dynsym]
        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = sections

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf):
            # _guess_symbol_origin returns "libc.so.6" → exclude this symbol
            with patch("abicheck.elf_metadata._guess_symbol_origin", return_value="libc.so.6"):
                reqs = _parse_elf_app_requirements(f, "libfoo.so.1")

        assert "printf" not in reqs.undefined_symbols

    def test_elf_versym_index_error(self, tmp_path):
        """IndexError from get_symbol falls back to unversioned."""
        from elftools.elf.gnuversions import GNUVerSymSection
        from elftools.elf.sections import SymbolTableSection

        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_versym = MagicMock(spec=GNUVerSymSection)
        mock_versym.get_symbol.side_effect = IndexError("out of range")

        mock_sym = MagicMock()
        mock_sym.entry.st_shndx = "SHN_UNDEF"
        mock_sym.name = "some_func"
        mock_sym.entry.st_info.bind = "STB_GLOBAL"

        mock_dynsym = MagicMock(spec=SymbolTableSection)
        mock_dynsym.name = ".dynsym"
        mock_dynsym.iter_symbols.return_value = [mock_sym]

        sections = [mock_versym, mock_dynsym]
        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = sections

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf):
            with patch("abicheck.elf_metadata._guess_symbol_origin", return_value=None):
                reqs = _parse_elf_app_requirements(f, "libfoo.so.1")

        # Falls back to ver_ndx=1 (unversioned), then _guess_symbol_origin=None → include
        assert "some_func" in reqs.undefined_symbols

    def test_elf_versym_string_ndx(self, tmp_path):
        """pyelftools returns string ndx like 'VER_NDX_GLOBAL' for special indices."""
        from elftools.elf.gnuversions import GNUVerSymSection
        from elftools.elf.sections import SymbolTableSection

        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_versym = MagicMock(spec=GNUVerSymSection)
        mock_ver_entry = MagicMock()
        mock_ver_entry.entry = {"ndx": "VER_NDX_GLOBAL"}  # string, not int
        mock_versym.get_symbol.return_value = mock_ver_entry

        mock_sym = MagicMock()
        mock_sym.entry.st_shndx = "SHN_UNDEF"
        mock_sym.name = "my_func"
        mock_sym.entry.st_info.bind = "STB_GLOBAL"

        mock_dynsym = MagicMock(spec=SymbolTableSection)
        mock_dynsym.name = ".dynsym"
        mock_dynsym.iter_symbols.return_value = [mock_sym]

        sections = [mock_versym, mock_dynsym]
        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = sections

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf):
            with patch("abicheck.elf_metadata._guess_symbol_origin", return_value=None):
                reqs = _parse_elf_app_requirements(f, "libfoo.so.1")

        # String ndx mapped to 1 (unversioned), unknown origin, STB_GLOBAL → included
        assert "my_func" in reqs.undefined_symbols

    def test_elf_weak_unversioned_excluded(self, tmp_path):
        """Weak undefined symbols with unknown origin are excluded (optional linker refs)."""
        from elftools.elf.gnuversions import GNUVerSymSection
        from elftools.elf.sections import SymbolTableSection

        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_versym = MagicMock(spec=GNUVerSymSection)
        mock_ver_entry = MagicMock()
        mock_ver_entry.entry = {"ndx": "VER_NDX_GLOBAL"}
        mock_versym.get_symbol.return_value = mock_ver_entry

        mock_sym = MagicMock()
        mock_sym.entry.st_shndx = "SHN_UNDEF"
        mock_sym.name = "__gmon_start__"
        mock_sym.entry.st_info.bind = "STB_WEAK"

        mock_dynsym = MagicMock(spec=SymbolTableSection)
        mock_dynsym.name = ".dynsym"
        mock_dynsym.iter_symbols.return_value = [mock_sym]

        sections = [mock_versym, mock_dynsym]
        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = sections

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf):
            with patch("abicheck.elf_metadata._guess_symbol_origin", return_value=None):
                reqs = _parse_elf_app_requirements(f, "libfoo.so.1")

        # STB_WEAK + unknown origin → excluded (optional linker symbol)
        assert "__gmon_start__" not in reqs.undefined_symbols

    def test_elf_no_library_filter(self, tmp_path):
        """When library_soname is empty, all UNDEF symbols are included."""
        from elftools.elf.sections import SymbolTableSection

        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_sym = MagicMock()
        mock_sym.entry.st_shndx = "SHN_UNDEF"
        mock_sym.name = "any_func"
        mock_sym.entry.st_info.bind = "STB_GLOBAL"

        mock_dynsym = MagicMock(spec=SymbolTableSection)
        mock_dynsym.name = ".dynsym"
        mock_dynsym.iter_symbols.return_value = [mock_sym]

        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = [mock_dynsym]

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf):
            reqs = _parse_elf_app_requirements(f, "")

        assert "any_func" in reqs.undefined_symbols

    def test_elf_without_versym_includes_symbol_from_target_origin(self, tmp_path):
        """Without .gnu.version, origin guess equal to target library should be included."""
        from elftools.elf.sections import SymbolTableSection

        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_sym = MagicMock()
        mock_sym.entry.st_shndx = "SHN_UNDEF"
        mock_sym.name = "foo_init"
        mock_sym.entry.st_info.bind = "STB_GLOBAL"

        mock_dynsym = MagicMock(spec=SymbolTableSection)
        mock_dynsym.name = ".dynsym"
        mock_dynsym.iter_symbols.return_value = [mock_sym]

        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = [mock_dynsym]

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf):
            with patch("abicheck.elf_metadata._guess_symbol_origin", return_value="libfoo.so.1"):
                reqs = _parse_elf_app_requirements(f, "libfoo.so.1")

        assert "foo_init" in reqs.undefined_symbols

    def test_elf_without_versym_excludes_symbol_from_other_origin(self, tmp_path):
        """Without .gnu.version, origin guess for another library should be excluded."""
        from elftools.elf.sections import SymbolTableSection

        f = tmp_path / "app.elf"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        mock_sym = MagicMock()
        mock_sym.entry.st_shndx = "SHN_UNDEF"
        mock_sym.name = "printf"
        mock_sym.entry.st_info.bind = "STB_GLOBAL"

        mock_dynsym = MagicMock(spec=SymbolTableSection)
        mock_dynsym.name = ".dynsym"
        mock_dynsym.iter_symbols.return_value = [mock_sym]

        mock_elf = MagicMock()
        mock_elf.iter_sections.return_value = [mock_dynsym]

        with patch("elftools.elf.elffile.ELFFile", return_value=mock_elf):
            with patch("abicheck.elf_metadata._guess_symbol_origin", return_value="libc.so.6"):
                reqs = _parse_elf_app_requirements(f, "libfoo.so.1")

        assert "printf" not in reqs.undefined_symbols


# ---------------------------------------------------------------------------
# Unit tests: _get_new_lib_exports
# ---------------------------------------------------------------------------

class TestGetNewLibExports:
    def test_elf_exports(self, tmp_path):
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol

        f = tmp_path / "lib.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        meta = ElfMetadata(symbols=[ElfSymbol(name="foo"), ElfSymbol(name="bar")])
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            exports = _get_new_lib_exports(f)

        assert exports == {"foo", "bar"}

    def test_old_exports_for_scoping_elf(self, tmp_path):
        from abicheck.elf_metadata import ElfMetadata, ElfSymbol

        f = tmp_path / "libold.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        meta = ElfMetadata(symbols=[ElfSymbol(name="foo"), ElfSymbol(name="bar")])
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            exports = _get_old_lib_exports_for_scoping(f)

        assert exports == {"foo", "bar"}

    def test_old_exports_for_scoping_non_elf(self, tmp_path):
        f = tmp_path / "lib.dll"
        f.write_bytes(b"MZ" + b"\x00" * 100)
        assert _get_old_lib_exports_for_scoping(f) == set()

    def test_normalize_elf_symbol_name(self):
        assert _normalize_elf_symbol_name("inflate") == "inflate"
        assert _normalize_elf_symbol_name("inflate@ZLIB_1.2.0") == "inflate"
        assert _normalize_elf_symbol_name("inflate@@ZLIB_1.2.0") == "inflate"

    def test_pe_exports(self, tmp_path):
        from abicheck.pe_metadata import PeExport, PeMetadata

        f = tmp_path / "lib.dll"
        f.write_bytes(b"MZ" + b"\x00" * 100)

        meta = PeMetadata(exports=[PeExport(name="CreateFoo"), PeExport(name="")])
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=meta):
            exports = _get_new_lib_exports(f)

        assert exports == {"CreateFoo"}

    def test_macho_exports(self, tmp_path):
        from abicheck.macho_metadata import MachoExport, MachoMetadata

        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        meta = MachoMetadata(exports=[MachoExport(name="foo_init"), MachoExport(name="")])
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=meta):
            exports = _get_new_lib_exports(f)

        assert exports == {"foo_init"}

    def test_unknown_format(self, tmp_path):
        f = tmp_path / "lib.bin"
        f.write_bytes(b"\x00\x00\x00\x00")
        assert _get_new_lib_exports(f) == set()


# ---------------------------------------------------------------------------
# Unit tests: _get_lib_soname
# ---------------------------------------------------------------------------

class TestGetLibSoname:
    def test_elf_soname(self, tmp_path):
        from abicheck.elf_metadata import ElfMetadata

        f = tmp_path / "libfoo.so.1.2.3"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        meta = ElfMetadata(soname="libfoo.so.1")
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            assert _get_lib_soname(f) == "libfoo.so.1"

    def test_elf_no_soname(self, tmp_path):
        from abicheck.elf_metadata import ElfMetadata

        f = tmp_path / "libfoo.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)

        meta = ElfMetadata(soname="")
        with patch("abicheck.elf_metadata.parse_elf_metadata", return_value=meta):
            assert _get_lib_soname(f) == "libfoo.so"

    def test_pe_soname(self, tmp_path):
        f = tmp_path / "foo.dll"
        f.write_bytes(b"MZ" + b"\x00" * 100)
        assert _get_lib_soname(f) == "foo.dll"

    def test_macho_install_name(self, tmp_path):
        from abicheck.macho_metadata import MachoMetadata

        f = tmp_path / "libfoo.dylib"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        meta = MachoMetadata(install_name="/usr/lib/libfoo.1.dylib")
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=meta):
            assert _get_lib_soname(f) == "/usr/lib/libfoo.1.dylib"

    def test_macho_no_install_name(self, tmp_path):
        from abicheck.macho_metadata import MachoMetadata

        f = tmp_path / "libfoo.dylib"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)

        meta = MachoMetadata(install_name="")
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=meta):
            assert _get_lib_soname(f) == "libfoo.dylib"

    def test_unknown_format(self, tmp_path):
        f = tmp_path / "lib.bin"
        f.write_bytes(b"\x00\x00\x00\x00")
        assert _get_lib_soname(f) == "lib.bin"


# ---------------------------------------------------------------------------
# Unit tests: check_appcompat with mocks
# ---------------------------------------------------------------------------

class TestCheckAppcompat:
    def _mock_deps(self, app_reqs, new_exports, diff, soname="libfoo.so.1"):
        """Return patches for check_appcompat dependencies."""
        return [
            patch("abicheck.appcompat._get_lib_soname", return_value=soname),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.dumper.dump", return_value=MagicMock()),
            patch("abicheck.appcompat.compare", return_value=diff),
            patch("abicheck.appcompat._get_new_lib_exports", return_value=new_exports),
            patch("abicheck.appcompat._detect_app_format", return_value=None),
        ]

    def test_compatible_no_changes(self, tmp_path):
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(
            undefined_symbols={"foo_init", "foo_process"},
        )
        diff = DiffResult(old_version="1", new_version="2", library="libfoo")
        new_exports = {"foo_init", "foo_process", "foo_cleanup"}

        patches = self._mock_deps(app_reqs, new_exports, diff)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = check_appcompat(app, old_lib, new_lib)

        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0
        assert result.missing_symbols == []

    def test_missing_symbols_breaking(self, tmp_path):
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(
            undefined_symbols={"foo_init", "foo_gone"},
        )
        diff = DiffResult(old_version="1", new_version="2", library="libfoo")
        new_exports = {"foo_init"}  # foo_gone is missing

        patches = self._mock_deps(app_reqs, new_exports, diff)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = check_appcompat(app, old_lib, new_lib)

        assert result.verdict == Verdict.BREAKING
        assert "foo_gone" in result.missing_symbols
        assert result.symbol_coverage == 50.0

    def test_relevant_changes_verdict(self, tmp_path):
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(
            undefined_symbols={"foo_init"},
        )
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo_init",
            description="removed",
        )
        diff = DiffResult(
            old_version="1", new_version="2", library="libfoo",
            changes=[change],
        )
        new_exports = {"foo_init"}  # still exported (e.g., diff reports signature change)

        patches = self._mock_deps(app_reqs, new_exports, diff)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = check_appcompat(app, old_lib, new_lib)

        assert len(result.breaking_for_app) == 1
        assert result.verdict == Verdict.BREAKING

    def test_irrelevant_changes_compatible(self, tmp_path):
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(
            undefined_symbols={"foo_init"},
        )
        change = Change(
            kind=ChangeKind.FUNC_ADDED,
            symbol="bar_new",
            description="added",
        )
        diff = DiffResult(
            old_version="1", new_version="2", library="libfoo",
            changes=[change],
        )
        new_exports = {"foo_init", "bar_new"}

        patches = self._mock_deps(app_reqs, new_exports, diff)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = check_appcompat(app, old_lib, new_lib)

        assert result.verdict == Verdict.COMPATIBLE
        assert len(result.irrelevant_for_app) == 1

    def test_no_required_symbols_no_change(self, tmp_path):
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(undefined_symbols=set())
        diff = DiffResult(old_version="1", new_version="2", library="libfoo")
        new_exports = {"foo_init"}

        patches = self._mock_deps(app_reqs, new_exports, diff)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = check_appcompat(app, old_lib, new_lib)

        assert result.verdict == Verdict.NO_CHANGE

    def test_no_exports_zero_coverage(self, tmp_path):
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(undefined_symbols={"foo_init"})
        diff = DiffResult(old_version="1", new_version="2", library="libfoo")
        new_exports: set[str] = set()  # No exports at all

        patches = self._mock_deps(app_reqs, new_exports, diff)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = check_appcompat(app, old_lib, new_lib)

        assert result.symbol_coverage == 0.0

    def test_policy_file_used_for_verdict(self, tmp_path):
        """When policy_file is provided, it's used for verdict computation."""
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(undefined_symbols={"foo_init"})
        change = Change(
            kind=ChangeKind.FUNC_REMOVED,
            symbol="foo_init",
            description="removed",
        )
        diff = DiffResult(
            old_version="1", new_version="2", library="libfoo",
            changes=[change],
        )
        new_exports = {"foo_init"}

        mock_pf = MagicMock()
        mock_pf.compute_verdict.return_value = Verdict.COMPATIBLE_WITH_RISK

        patches = self._mock_deps(app_reqs, new_exports, diff)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5]:
            result = check_appcompat(
                app, old_lib, new_lib, policy_file=mock_pf,
            )

        assert result.verdict == Verdict.COMPATIBLE_WITH_RISK
        mock_pf.compute_verdict.assert_called_once()

    def test_missing_versions_breaking(self, tmp_path):
        """Missing ELF versions should result in BREAKING verdict."""
        from abicheck.elf_metadata import ElfMetadata

        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(
            undefined_symbols={"foo_init"},
            required_versions={"FOO_1.0": "libfoo.so"},
        )
        diff = DiffResult(old_version="1", new_version="2", library="libfoo")
        new_exports = {"foo_init"}
        elf_meta = ElfMetadata(versions_defined=["FOO_2.0"])

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="libfoo.so"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.dumper.dump", return_value=MagicMock()),
            patch("abicheck.appcompat.compare", return_value=diff),
            patch("abicheck.appcompat._get_new_lib_exports", return_value=new_exports),
            patch("abicheck.appcompat._detect_app_format", return_value="elf"),
            patch("abicheck.elf_metadata.parse_elf_metadata", return_value=elf_meta),
        ):
            result = check_appcompat(app, old_lib, new_lib)

        assert result.verdict == Verdict.BREAKING
        assert "FOO_1.0" in result.missing_versions

    def test_lang_c(self, tmp_path):
        """Test lang='c' passes correct compiler."""
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(undefined_symbols=set())
        diff = DiffResult(old_version="1", new_version="2", library="libfoo")
        new_exports: set[str] = set()

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="libfoo.so"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.dumper.dump", return_value=MagicMock()) as mock_dump,
            patch("abicheck.appcompat.compare", return_value=diff),
            patch("abicheck.appcompat._get_new_lib_exports", return_value=new_exports),
            patch("abicheck.appcompat._detect_app_format", return_value=None),
        ):
            check_appcompat(app, old_lib, new_lib, lang="c")
            # verify dump was called with compiler="cc" and lang="c"
            for call in mock_dump.call_args_list:
                assert call.kwargs.get("compiler") == "cc"
                assert call.kwargs.get("lang") == "c"

    def test_elf_scopes_symbols_to_old_lib_exports(self, tmp_path):
        """Symbols not exported by target old DSO must be ignored (no false positives)."""
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(
            undefined_symbols={"inflate", "XML_Parse"},
        )
        diff = DiffResult(old_version="1", new_version="2", library="libz")

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="libz.so.1"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.appcompat._detect_app_format", return_value="elf"),
            patch("abicheck.appcompat._get_old_lib_exports_for_scoping", return_value={"inflate"}),
            patch("abicheck.dumper.dump", return_value=MagicMock()),
            patch("abicheck.appcompat.compare", return_value=diff),
            patch("abicheck.appcompat._get_new_lib_exports", return_value={"inflate"}),
            patch("abicheck.elf_metadata.parse_elf_metadata", return_value=SimpleNamespace(versions_defined=[])),
        ):
            result = check_appcompat(app, old_lib, new_lib)

        assert result.required_symbols == {"inflate"}
        assert result.missing_symbols == []
        assert result.verdict == Verdict.COMPATIBLE

    def test_elf_versioned_symbol_names_are_normalized_for_scoping(self, tmp_path):
        """Version suffixes (@@VER/@VER) should not cause missed symbol matches."""
        app = tmp_path / "app"
        old_lib = tmp_path / "old.so"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(undefined_symbols={"inflate@@ZLIB_1.2.0"})
        diff = DiffResult(old_version="1", new_version="2", library="libz")

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="libz.so.1"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.appcompat._detect_app_format", return_value="elf"),
            patch("abicheck.appcompat._get_old_lib_exports_for_scoping", return_value={"inflate"}),
            patch("abicheck.dumper.dump", return_value=MagicMock()),
            patch("abicheck.appcompat.compare", return_value=diff),
            patch("abicheck.appcompat._get_new_lib_exports", return_value={"inflate"}),
            patch("abicheck.elf_metadata.parse_elf_metadata", return_value=SimpleNamespace(versions_defined=[])),
        ):
            result = check_appcompat(app, old_lib, new_lib)

        assert result.required_symbols == {"inflate"}
        assert result.missing_symbols == []
        assert result.verdict == Verdict.COMPATIBLE


# ---------------------------------------------------------------------------
# Unit tests: check_against with mocks
# ---------------------------------------------------------------------------

class TestCheckAgainst:
    def test_compatible(self, tmp_path):
        app = tmp_path / "app"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(undefined_symbols={"foo_init"})

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="new.so"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.appcompat._get_new_lib_exports", return_value={"foo_init"}),
            patch("abicheck.appcompat._detect_app_format", return_value=None),
        ):
            result = check_against(app, new_lib)

        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0
        assert result.old_lib_path == ""

    def test_missing_symbols_breaking(self, tmp_path):
        app = tmp_path / "app"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(
            undefined_symbols={"foo_init", "foo_missing"},
        )

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="new.so"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.appcompat._get_new_lib_exports", return_value={"foo_init"}),
            patch("abicheck.appcompat._detect_app_format", return_value=None),
        ):
            result = check_against(app, new_lib)

        assert result.verdict == Verdict.BREAKING
        assert "foo_missing" in result.missing_symbols
        assert result.symbol_coverage == 50.0

    def test_missing_versions_breaking(self, tmp_path):
        from abicheck.elf_metadata import ElfMetadata

        app = tmp_path / "app"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(
            undefined_symbols={"foo_init"},
            required_versions={"FOO_1.0": "libfoo.so"},
        )

        elf_meta = ElfMetadata(versions_defined=["FOO_2.0"])  # FOO_1.0 missing

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="new.so"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.appcompat._get_new_lib_exports", return_value={"foo_init"}),
            patch("abicheck.appcompat._detect_app_format", return_value="elf"),
            patch("abicheck.elf_metadata.parse_elf_metadata", return_value=elf_meta),
        ):
            result = check_against(app, new_lib)

        assert result.verdict == Verdict.BREAKING
        assert "FOO_1.0" in result.missing_versions

    def test_no_exports_zero_coverage(self, tmp_path):
        app = tmp_path / "app"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(undefined_symbols={"foo_init"})

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="new.so"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.appcompat._get_new_lib_exports", return_value=set()),
            patch("abicheck.appcompat._detect_app_format", return_value=None),
        ):
            result = check_against(app, new_lib)

        assert result.symbol_coverage == 0.0

    def test_no_required_symbols(self, tmp_path):
        app = tmp_path / "app"
        new_lib = tmp_path / "new.so"

        app_reqs = AppRequirements(undefined_symbols=set())

        with (
            patch("abicheck.appcompat._get_lib_soname", return_value="new.so"),
            patch("abicheck.appcompat.parse_app_requirements", return_value=app_reqs),
            patch("abicheck.appcompat._get_new_lib_exports", return_value={"foo"}),
            patch("abicheck.appcompat._detect_app_format", return_value=None),
        ):
            result = check_against(app, new_lib)

        assert result.verdict == Verdict.COMPATIBLE
        assert result.symbol_coverage == 100.0


# ---------------------------------------------------------------------------
# Reporter edge cases
# ---------------------------------------------------------------------------

class TestReporterEdgeCases:
    def test_json_missing_versions(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            missing_versions=["FOO_1.0"],
            verdict=Verdict.BREAKING,
        )
        j = appcompat_to_json(result)
        data = json.loads(j)
        assert "FOO_1.0" in data["missing_versions"]

    def test_markdown_missing_versions(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            missing_versions=["FOO_1.0"],
            verdict=Verdict.BREAKING,
        )
        md = appcompat_to_markdown(result)
        assert "Missing Symbol Versions" in md
        assert "FOO_1.0" in md

    def test_json_full_diff_verdict(self):
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            full_diff=DiffResult(
                old_version="1", new_version="2",
                library="libfoo", verdict=Verdict.BREAKING,
            ),
            verdict=Verdict.COMPATIBLE,
        )
        j = appcompat_to_json(result)
        data = json.loads(j)
        assert data["full_library_verdict"] == "BREAKING"

    def test_markdown_no_changes_message(self):
        change = Change(
            kind=ChangeKind.FUNC_ADDED, symbol="x", description="added",
        )
        result = AppCompatResult(
            app_path="/usr/bin/myapp",
            old_lib_path="old.so",
            new_lib_path="new.so",
            irrelevant_for_app=[change],
            verdict=Verdict.COMPATIBLE,
        )
        md = appcompat_to_markdown(result)
        assert "0 of 1 total" in md
        assert "do NOT affect" in md
