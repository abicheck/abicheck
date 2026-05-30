"""Unit tests for abicheck.service — targeting ≥80% coverage."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from abicheck.checker_types import DiffResult
from abicheck.errors import SnapshotError, ValidationError
from abicheck.model import AbiSnapshot, DependencyInfo, Function, Visibility
from abicheck.service import (
    _render_deps_section_md,
    collect_metadata,
    detect_binary_format,
    expand_header_inputs,
    load_suppression_and_policy,
    render_output,
    resolve_input,
    run_compare,
    run_dump,
    sniff_text_format,
)

# ── detect_binary_format() ──────────────────────────────────────────────────


class TestDetectBinaryFormat:
    def test_delegates_to_binary_utils(self, tmp_path):
        p = tmp_path / "test.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        result = detect_binary_format(p)
        assert result == "elf"

    def test_non_binary_returns_none(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("hello world")
        result = detect_binary_format(p)
        assert result is None


# ── sniff_text_format() ─────────────────────────────────────────────────────


class TestSniffTextFormat:
    def test_json_format(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text('{"library": "test"}')
        assert sniff_text_format(p) == "json"

    def test_perl_format(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = { 'Headers' => {} };")
        assert sniff_text_format(p) == "perl"

    def test_unknown_format(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("Some random text content")
        assert sniff_text_format(p) == "unknown"

    def test_oserror_returns_unknown(self, tmp_path):
        p = tmp_path / "nonexistent"
        assert sniff_text_format(p) == "unknown"

    def test_json_with_whitespace(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text("   \n  {}")
        assert sniff_text_format(p) == "json"


# ── expand_header_inputs() ──────────────────────────────────────────────────


class TestExpandHeaderInputs:
    def test_single_file(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("#pragma once")
        result = expand_header_inputs([h])
        assert result == [h]

    def test_directory_expansion(self, tmp_path):
        d = tmp_path / "include"
        d.mkdir()
        (d / "a.h").write_text("")
        (d / "b.hpp").write_text("")
        (d / "c.txt").write_text("")  # not a header
        result = expand_header_inputs([d])
        names = {p.name for p in result}
        assert "a.h" in names
        assert "b.hpp" in names
        assert "c.txt" not in names

    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(ValidationError, match="not found"):
            expand_header_inputs([tmp_path / "missing.h"])

    def test_empty_directory_raises(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(ValidationError, match="no supported header"):
            expand_header_inputs([d])

    def test_deduplication(self, tmp_path):
        h = tmp_path / "foo.h"
        h.write_text("")
        result = expand_header_inputs([h, h])
        assert len(result) == 1

    def test_directory_with_subdirs(self, tmp_path):
        d = tmp_path / "include"
        d.mkdir()
        sub = d / "sub"
        sub.mkdir()
        (sub / "deep.h").write_text("")
        result = expand_header_inputs([d])
        assert len(result) == 1
        assert result[0].name == "deep.h"

    def test_various_extensions(self, tmp_path):
        d = tmp_path / "hdrs"
        d.mkdir()
        for ext in (".h", ".hh", ".hpp", ".hxx", ".h++", ".ipp", ".tpp", ".inc"):
            (d / f"test{ext}").write_text("")
        result = expand_header_inputs([d])
        assert len(result) == 8


# ── resolve_input() ─────────────────────────────────────────────────────────


class TestResolveInput:
    def test_is_elf_true_calls_run_dump(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.run_dump", return_value=snap) as mock:
            result = resolve_input(p, is_elf=True)
        assert result is snap
        mock.assert_called_once()

    def test_binary_detection_elf(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.run_dump", return_value=snap):
            result = resolve_input(p)
        assert result is snap

    def test_json_text_format(self, tmp_path):
        p = tmp_path / "snap.json"
        snap = AbiSnapshot(library="test", version="1.0")
        p.write_text('{"library": "test"}')
        with patch("abicheck.service.load_snapshot", return_value=snap):
            result = resolve_input(p, is_elf=False)
        assert result is snap

    def test_json_load_error_wraps_in_snapshot_error(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{invalid json")
        with patch("abicheck.service.load_snapshot", side_effect=ValueError("bad")):
            with pytest.raises(SnapshotError, match="Failed to load JSON"):
                resolve_input(p, is_elf=False)

    def test_perl_format(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.detect_binary_format", return_value=None):
            with patch("abicheck.service.sniff_text_format", return_value="perl"):
                with patch(
                    "abicheck.compat.abicc_dump_import.import_abicc_perl_dump",
                    return_value=snap,
                ):
                    result = resolve_input(p, is_elf=False)
        assert result is snap

    def test_perl_import_error(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        with patch("abicheck.service.detect_binary_format", return_value=None):
            with patch("abicheck.service.sniff_text_format", return_value="perl"):
                with patch(
                    "abicheck.compat.abicc_dump_import.import_abicc_perl_dump",
                    side_effect=ValueError("parse fail"),
                ):
                    with pytest.raises(SnapshotError, match="ABICC Perl"):
                        resolve_input(p, is_elf=False)

    def test_unknown_format_raises(self, tmp_path):
        p = tmp_path / "mystery"
        p.write_text("???")
        with patch("abicheck.service.detect_binary_format", return_value=None):
            with patch("abicheck.service.sniff_text_format", return_value="unknown"):
                with pytest.raises(ValidationError, match="Cannot detect format"):
                    resolve_input(p, is_elf=False)


# ── run_dump() ──────────────────────────────────────────────────────────────


class TestRunDump:
    def test_unsupported_format(self, tmp_path):
        p = tmp_path / "lib.xyz"
        p.write_bytes(b"\x00" * 100)
        with pytest.raises(ValidationError, match="Unsupported binary format"):
            run_dump(p, "webasm")

    def test_elf_format_delegates(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service._dump_elf", return_value=snap):
            result = run_dump(p, "elf")
        assert result is snap

    def test_pe_format_delegates(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service._dump_pe", return_value=snap):
            result = run_dump(p, "pe")
        assert result is snap

    def test_macho_format_delegates(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service._dump_macho", return_value=snap):
            result = run_dump(p, "macho")
        assert result is snap


# ── _dump_elf() ─────────────────────────────────────────────────────────────


class TestDumpElf:
    def test_no_headers_warning(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap):
                result = _dump_elf(p, [], [], "1.0", "c++")
        assert result is snap

    def test_invalid_include_dir_raises(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        h = tmp_path / "foo.h"
        h.write_text("")
        bad_inc = tmp_path / "nonexistent"
        with patch("abicheck.service.expand_header_inputs", return_value=[h]):
            with pytest.raises(ValidationError, match="Include directory"):
                _dump_elf(p, [h], [bad_inc], "1.0", "c++")

    def test_dump_error_wraps(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x00" * 10)
        with patch("abicheck.service.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", side_effect=RuntimeError("bad elf")):
                with pytest.raises(SnapshotError, match="Failed to dump"):
                    _dump_elf(p, [], [], "1.0", "c++")

    def test_includes_without_headers_warns(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        inc = tmp_path / "inc"
        inc.mkdir()
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap):
                result = _dump_elf(p, [], [inc], "1.0", "c++")
        assert result is snap

    def test_lang_c_sets_compiler(self, tmp_path):
        from abicheck.service import _dump_elf

        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        snap = AbiSnapshot(library="test", version="1.0")
        with patch("abicheck.service.expand_header_inputs", return_value=[]):
            with patch("abicheck.dumper.dump", return_value=snap) as mock_dump:
                _dump_elf(p, [], [], "1.0", "c")
        call_kwargs = mock_dump.call_args
        assert call_kwargs.kwargs.get("compiler") == "cc" or call_kwargs[1].get("compiler") == "cc"


# ── _dump_pe() ──────────────────────────────────────────────────────────────


class TestDumpPe:
    def test_no_machine_raises(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = MagicMock()
        pe_meta.machine = None
        pe_meta.exports = []
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with pytest.raises(SnapshotError, match="Failed to extract PE metadata"):
                _dump_pe(p, "1.0")

    def test_no_exports_raises(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = []
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with pytest.raises(ValidationError, match="no exports"):
                _dump_pe(p, "1.0")

    def test_successful_pe_dump(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "MyFunc"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.platform == "pe"
        assert len(result.functions) == 1
        assert result.functions[0].name == "MyFunc"

    def test_pe_import_error(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch("abicheck.pe_metadata.parse_pe_metadata", side_effect=ImportError("no pefile")):
            with pytest.raises(SnapshotError, match="no pefile"):
                _dump_pe(p, "1.0")

    def test_pe_runtime_error(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch("abicheck.pe_metadata.parse_pe_metadata", side_effect=RuntimeError("corrupt")):
            with pytest.raises(SnapshotError, match="Failed to parse PE"):
                _dump_pe(p, "1.0")

    def test_ordinal_export(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = None
        export.ordinal = 42
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.functions[0].name == "ordinal:42"

    def test_pdb_found_and_parsed(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "Func"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        mock_dwarf = MagicMock()
        mock_adv = MagicMock()
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=Path("/fake.pdb")):
                with patch("abicheck.pdb_metadata.parse_pdb_debug_info", return_value=(mock_dwarf, mock_adv)):
                    result = _dump_pe(p, "1.0")
        assert result.dwarf is mock_dwarf
        assert result.dwarf_advanced is mock_adv

    def test_pdb_parsing_exception_handled(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "Func"
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", side_effect=RuntimeError("pdb error")):
                result = _dump_pe(p, "1.0")
        assert result.dwarf is None

    def test_cpp_name_not_extern_c(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        export = MagicMock()
        export.name = "?MyFunc@@YAXXZ"  # MSVC mangled
        export.ordinal = 1
        pe_meta = MagicMock()
        pe_meta.machine = "AMD64"
        pe_meta.exports = [export]
        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta):
            with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
                result = _dump_pe(p, "1.0")
        assert result.functions[0].is_extern_c is False


# ── _dump_macho() ───────────────────────────────────────────────────────────


class TestDumpMacho:
    def test_successful_macho_dump(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_myFunc"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta):
            result = _dump_macho(p, "1.0")
        assert result.platform == "macho"
        assert len(result.functions) == 1

    def test_no_exports_no_metadata_raises(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        macho_meta = MagicMock()
        macho_meta.exports = []
        macho_meta.install_name = None
        macho_meta.dependent_libs = []
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta):
            with pytest.raises(SnapshotError, match="no exports"):
                _dump_macho(p, "1.0")

    def test_parse_error(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        with patch("abicheck.macho_metadata.parse_macho_metadata", side_effect=RuntimeError("bad macho")):
            with pytest.raises(SnapshotError, match="Failed to parse Mach-O"):
                _dump_macho(p, "1.0")

    def test_export_without_name_skipped(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        exp_named = MagicMock()
        exp_named.name = "_func"
        exp_empty = MagicMock()
        exp_empty.name = ""
        macho_meta = MagicMock()
        macho_meta.exports = [exp_named, exp_empty]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta):
            result = _dump_macho(p, "1.0")
        assert len(result.functions) == 1

    def test_cpp_symbol_not_extern_c(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\x00" * 100)
        export = MagicMock()
        export.name = "_ZN3foo3barEv"  # C++ mangled
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta):
            result = _dump_macho(p, "1.0")
        assert result.functions[0].is_extern_c is False


# ── collect_metadata() ──────────────────────────────────────────────────────


class TestCollectMetadata:
    def test_binary_file(self, tmp_path):
        p = tmp_path / "lib.so"
        p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        with patch("abicheck.service.sniff_text_format", return_value="unknown"):
            meta = collect_metadata(p)
        assert meta is not None
        assert meta.path == str(p)
        assert len(meta.sha256) == 64
        assert meta.size_bytes == 104

    def test_json_snapshot_returns_none(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text('{"library": "test"}')
        meta = collect_metadata(p)
        assert meta is None

    def test_perl_dump_returns_none(self, tmp_path):
        p = tmp_path / "dump.pl"
        p.write_text("$VAR1 = {};")
        meta = collect_metadata(p)
        assert meta is None


# ── load_suppression_and_policy() ───────────────────────────────────────────


class TestLoadSuppressionAndPolicy:
    def test_no_suppress_no_policy(self):
        s, p = load_suppression_and_policy(None)
        assert s is None
        assert p is None

    def test_invalid_suppression_file(self, tmp_path):
        f = tmp_path / "bad.yaml"
        f.write_text("not: [valid: suppression")
        with pytest.raises(ValidationError, match="Invalid suppression"):
            load_suppression_and_policy(f)

    def test_valid_suppression_file(self, tmp_path):
        f = tmp_path / "suppress.yaml"
        f.write_text("version: 1\nsuppressions:\n  - symbol: 'foo'\n    change_kind: func_removed\n")
        s, p = load_suppression_and_policy(f)
        assert s is not None
        assert p is None

    def test_policy_file_with_non_default_policy_warns(self, tmp_path, caplog):
        import logging

        pf = tmp_path / "policy.yaml"
        pf.write_text("overrides: {}\n")
        with caplog.at_level(logging.WARNING, logger="abicheck.service"):
            _, p = load_suppression_and_policy(None, policy="permissive", policy_file_path=pf)
        assert p is not None
        assert "ignored" in caplog.text.lower()

    def test_invalid_policy_file(self, tmp_path):
        pf = tmp_path / "bad_policy.yaml"
        pf.write_text("- this is a list not a mapping\n")
        with pytest.raises(ValidationError):
            load_suppression_and_policy(None, policy_file_path=pf)


# ── run_compare() ───────────────────────────────────────────────────────────


class TestRunCompare:
    def _make_snap_file(self, tmp_path, name, version="1.0"):
        """Create a minimal JSON snapshot file."""
        snap = AbiSnapshot(
            library=name, version=version,
            functions=[
                Function(name="foo", mangled="foo", return_type="int",
                         visibility=Visibility.PUBLIC, is_extern_c=True),
            ],
        )
        from abicheck.serialization import save_snapshot
        p = tmp_path / f"{name}_{version}.json"
        save_snapshot(snap, p)
        return p

    def test_compare_two_snapshots(self, tmp_path):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        result, old, new = run_compare(old_p, new_p)
        assert isinstance(result, DiffResult)
        assert isinstance(old, AbiSnapshot)
        assert isinstance(new, AbiSnapshot)

    def test_compare_with_suppression(self, tmp_path):
        old_p = self._make_snap_file(tmp_path, "libtest", "1.0")
        new_p = self._make_snap_file(tmp_path, "libtest", "2.0")
        sf = tmp_path / "suppress.yaml"
        sf.write_text("version: 1\nsuppressions:\n  - symbol: foo\n    change_kind: func_removed\n")
        result, _, _ = run_compare(old_p, new_p, suppress=sf)
        assert isinstance(result, DiffResult)


# ── render_output() ─────────────────────────────────────────────────────────


class TestRenderOutput:
    @pytest.fixture
    def snap(self):
        return AbiSnapshot(library="libtest", version="1.0",
                           functions=[Function(name="foo", mangled="foo",
                                               return_type="int")])

    @pytest.fixture
    def diff_result(self):
        return DiffResult(old_version="1.0", new_version="2.0", library="libtest")

    def test_json_format(self, diff_result, snap):
        out = render_output("json", diff_result, snap)
        d = json.loads(out)
        assert "library" in d or "verdict" in d or isinstance(d, dict)

    def test_markdown_format(self, diff_result, snap):
        out = render_output("markdown", diff_result, snap)
        assert isinstance(out, str)

    def test_md_format(self, diff_result, snap):
        out = render_output("md", diff_result, snap)
        assert isinstance(out, str)

    def test_sarif_format(self, diff_result, snap):
        out = render_output("sarif", diff_result, snap)
        d = json.loads(out)
        assert "$schema" in d or "runs" in d

    def test_html_format(self, diff_result, snap):
        out = render_output("html", diff_result, snap)
        assert "<html" in out.lower() or "<!doctype" in out.lower() or "<div" in out.lower()

    def test_unsupported_format_raises(self, diff_result, snap):
        with pytest.raises(ValidationError, match="Unsupported output format"):
            render_output("xml", diff_result, snap)

    def test_stat_json(self, diff_result, snap):
        out = render_output("json", diff_result, snap, stat=True)
        d = json.loads(out)
        assert isinstance(d, dict)

    def test_stat_text(self, diff_result, snap):
        out = render_output("markdown", diff_result, snap, stat=True)
        assert isinstance(out, str)

    def test_json_follow_deps(self, snap):
        snap.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0}],
        )
        diff_result = DiffResult(old_version="1.0", new_version="2.0", library="libtest")
        out = render_output("json", diff_result, snap, follow_deps=True)
        d = json.loads(out)
        assert "old_dependency_info" in d

    def test_markdown_follow_deps(self, snap):
        snap.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0}],
        )
        diff_result = DiffResult(old_version="1.0", new_version="2.0", library="libtest")
        out = render_output("markdown", diff_result, snap, follow_deps=True)
        assert "Dependency" in out

    def test_html_with_new_snap(self, snap):
        new_snap = AbiSnapshot(library="libtest", version="2.0")
        diff_result = DiffResult(old_version="1.0", new_version="2.0", library="libtest")
        out = render_output("html", diff_result, snap, new=new_snap)
        assert isinstance(out, str)


# ── _render_deps_section_md() ──────────────────────────────────────────────


class TestRenderDepsSection:
    def test_basic_deps(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": 0, "resolution_reason": "system"}],
            bindings_summary={"GLOBAL": 5},
            unresolved=[{"soname": "libmissing.so", "consumer": "lib.so"}],
            missing_symbols=[
                {"symbol": "foo", "version": "GLIBC_2.17"},
                {"symbol": "bar"},
            ],
        )
        result = _render_deps_section_md(old, None)
        assert "libc.so.6" in result
        assert "GLOBAL" in result
        assert "libmissing.so" in result
        assert "foo" in result
        assert "bar" in result

    def test_no_dep_info(self):
        old = AbiSnapshot(library="lib", version="1.0")
        result = _render_deps_section_md(old, None)
        assert "Dependency" in result
        # Should still have the header

    def test_missing_symbols_truncated(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            missing_symbols=[{"symbol": f"sym{i}"} for i in range(15)],
        )
        result = _render_deps_section_md(old, None)
        assert "+5 more" in result

    def test_non_int_depth(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(
            nodes=[{"soname": "libc.so.6", "depth": "invalid"}],
        )
        result = _render_deps_section_md(old, None)
        assert "libc.so.6" in result

    def test_both_old_and_new(self):
        old = AbiSnapshot(library="lib", version="1.0")
        old.dependency_info = DependencyInfo(nodes=[{"soname": "old.so", "depth": 0}])
        new = AbiSnapshot(library="lib", version="2.0")
        new.dependency_info = DependencyInfo(nodes=[{"soname": "new.so", "depth": 0}])
        result = _render_deps_section_md(old, new)
        assert "old.so" in result
        assert "new.so" in result


# ── Header-scoped PE/Mach-O dumps (issue #235) ───────────────────────────────


def _scoped_snapshot(platform: str, *funcs: tuple[str, Visibility]) -> AbiSnapshot:
    """Build a fake header-scoped snapshot as ``dumper._dump_*`` would return."""
    from abicheck.model import RecordType

    snap = AbiSnapshot(library="lib", version="1.0", platform=platform)
    snap.functions = [
        Function(name=n, mangled=n, return_type="int", visibility=v) for n, v in funcs
    ]
    # A header-scoped dump carries real type info (so layout diffs still fire).
    snap.types = [RecordType(name="PublicStruct", kind="struct")]
    return snap


def _pe_meta(*export_names: str) -> MagicMock:
    meta = MagicMock()
    meta.machine = "AMD64"
    exports = []
    for i, name in enumerate(export_names, start=1):
        exp = MagicMock()
        exp.name = name
        exp.ordinal = i
        exports.append(exp)
    meta.exports = exports
    return meta


def _mk_header(tmp_path: Path, name: str = "api.h") -> Path:
    """Create a real public header file so expand_header_inputs accepts it."""
    h = tmp_path / name
    h.write_text("int PublicApiFunc(void);\n")
    return h


class TestPeHeaderScoping:
    """Issue #235: --header/--include must scope the PE ABI surface."""

    def test_headers_route_to_castxml_scoped_dump(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        # Export table has a public API symbol AND a private/internal export.
        pe_meta = _pe_meta("PublicApiFunc", "InternalPrivateFunc")
        # Header-scoped dump only sees the symbol declared in the public header.
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta), \
             patch("abicheck.pdb_utils.locate_pdb", return_value=None), \
             patch("abicheck.dumper._dump_pe", return_value=scoped) as mock_dump:
            result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)], includes=[Path("inc")])

        # The header-aware dumper was actually invoked with the (expanded) headers.
        assert mock_dump.called
        called_headers = mock_dump.call_args.args[1]
        assert called_headers == [tmp_path / "api.h"]
        # Surface is scoped: private export absent, public symbol present.
        names = [f.name for f in result.functions]
        assert "PublicApiFunc" in names
        assert "InternalPrivateFunc" not in names
        # Type info preserved so reachable layout changes still diff.
        assert any(t.name == "PublicStruct" for t in result.types)

    def test_private_export_absent_from_headers_not_compared(self, tmp_path):
        """An exported-but-private symbol removed in 'new' must not surface."""
        from abicheck.checker import compare
        from abicheck.service import _dump_pe

        old_p = tmp_path / "old.dll"
        new_p = tmp_path / "new.dll"
        old_p.write_bytes(b"MZ" + b"\x00" * 100)
        new_p.write_bytes(b"MZ" + b"\x00" * 100)

        old_pe = _pe_meta("PublicApiFunc", "InternalPrivateFunc")
        new_pe = _pe_meta("PublicApiFunc")  # private export dropped in new
        old_scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))
        new_scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with patch("abicheck.pdb_utils.locate_pdb", return_value=None):
            with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=old_pe), \
                 patch("abicheck.dumper._dump_pe", return_value=old_scoped):
                old_snap = _dump_pe(old_p, "1.0", headers=[_mk_header(tmp_path)])
            with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=new_pe), \
                 patch("abicheck.dumper._dump_pe", return_value=new_scoped):
                new_snap = _dump_pe(new_p, "2.0", headers=[_mk_header(tmp_path)])

        result = compare(old_snap, new_snap)
        removed = [c for c in result.changes if "InternalPrivateFunc" in (c.symbol or "")]
        assert removed == [], f"private export must not be reported: {removed}"

    def test_fallback_when_no_header_match(self, tmp_path):
        """MSVC-mangled C++ exports won't match Itanium names → warn + fallback."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("?realFunc@@YAHXZ")
        # castxml parsed headers but nothing matched the export table.
        scoped = _scoped_snapshot("pe", ("someDecl", Visibility.HIDDEN))

        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta), \
             patch("abicheck.pdb_utils.locate_pdb", return_value=None), \
             patch("abicheck.dumper._dump_pe", return_value=scoped):
            with pytest.warns(UserWarning, match="None of the provided headers matched"):
                result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        # Fell back to the full export table.
        names = [f.name for f in result.functions]
        assert "?realFunc@@YAHXZ" in names

    def test_fallback_when_castxml_unavailable(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")

        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta), \
             patch("abicheck.pdb_utils.locate_pdb", return_value=None), \
             patch("abicheck.dumper._dump_pe", side_effect=RuntimeError("castxml not found")):
            with pytest.warns(UserWarning, match="Header-based ABI scoping unavailable"):
                result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        names = [f.name for f in result.functions]
        assert "PublicApiFunc" in names

    def test_no_headers_uses_export_table(self, tmp_path):
        """Without headers, behaviour is unchanged: full export table, PUBLIC."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc", "InternalPrivateFunc")

        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta), \
             patch("abicheck.pdb_utils.locate_pdb", return_value=None), \
             patch("abicheck.dumper._dump_pe") as mock_dump:
            result = _dump_pe(p, "1.0")

        assert not mock_dump.called  # castxml path never taken
        names = {f.name for f in result.functions}
        assert names == {"PublicApiFunc", "InternalPrivateFunc"}
        assert all(f.visibility == Visibility.PUBLIC for f in result.functions)

    def test_pdb_debug_preserved_on_scoped_snapshot(self, tmp_path):
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))
        dwarf_meta = MagicMock()
        dwarf_adv = MagicMock()

        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta), \
             patch("abicheck.dumper._dump_pe", return_value=scoped), \
             patch("abicheck.service._extract_pdb_debug", return_value=(dwarf_meta, dwarf_adv)):
            result = _dump_pe(p, "1.0", headers=[_mk_header(tmp_path)])

        assert result.dwarf is dwarf_meta
        assert result.dwarf_advanced is dwarf_adv

    def test_header_directory_is_expanded(self, tmp_path):
        """`--header <dir>` must expand to files, not feed a dir to castxml."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        hdr_dir = tmp_path / "include"
        hdr_dir.mkdir()
        (hdr_dir / "a.h").write_text("int PublicApiFunc(void);\n")
        (hdr_dir / "b.hpp").write_text("int Other(void);\n")
        pe_meta = _pe_meta("PublicApiFunc")
        scoped = _scoped_snapshot("pe", ("PublicApiFunc", Visibility.PUBLIC))

        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta), \
             patch("abicheck.pdb_utils.locate_pdb", return_value=None), \
             patch("abicheck.dumper._dump_pe", return_value=scoped) as mock_dump:
            _dump_pe(p, "1.0", headers=[hdr_dir])

        # The dumper received the individual header files, not the directory.
        called_headers = mock_dump.call_args.args[1]
        assert hdr_dir not in called_headers
        assert {h.name for h in called_headers} == {"a.h", "b.hpp"}

    def test_bad_header_path_raises_not_silent_fallback(self, tmp_path):
        """A nonexistent header must raise, not silently fall back to exports."""
        from abicheck.service import _dump_pe

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        pe_meta = _pe_meta("PublicApiFunc")

        with patch("abicheck.pe_metadata.parse_pe_metadata", return_value=pe_meta), \
             patch("abicheck.pdb_utils.locate_pdb", return_value=None):
            with pytest.raises(ValidationError, match="not found"):
                _dump_pe(p, "1.0", headers=[tmp_path / "missing.h"])


class TestMachoHeaderScoping:
    def test_headers_route_to_castxml_scoped_dump(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_publicFn"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        scoped = _scoped_snapshot("macho", ("publicFn", Visibility.PUBLIC))

        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta), \
             patch("abicheck.dumper._dump_macho", return_value=scoped) as mock_dump:
            result = _dump_macho(p, "1.0", headers=[_mk_header(tmp_path)])

        assert mock_dump.called
        assert [f.name for f in result.functions] == ["publicFn"]

    def test_fallback_when_no_header_match(self, tmp_path):
        from abicheck.service import _dump_macho

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        export = MagicMock()
        export.name = "_publicFn"
        macho_meta = MagicMock()
        macho_meta.exports = [export]
        macho_meta.install_name = "libtest.dylib"
        macho_meta.dependent_libs = []
        scoped = _scoped_snapshot("macho", ("other", Visibility.HIDDEN))

        with patch("abicheck.macho_metadata.parse_macho_metadata", return_value=macho_meta), \
             patch("abicheck.dumper._dump_macho", return_value=scoped):
            with pytest.warns(UserWarning, match="None of the provided headers matched"):
                result = _dump_macho(p, "1.0", headers=[_mk_header(tmp_path)])

        assert [f.name for f in result.functions] == ["_publicFn"]


class TestRunDumpHeaderWiring:
    """run_dump must forward headers/includes to the PE/Mach-O dumpers."""

    def test_run_dump_pe_forwards_headers(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service._dump_pe", return_value=snap) as mock_pe:
            run_dump(p, "pe", [Path("api.h")], [Path("inc")], "1.0", "c++")
        assert mock_pe.call_args.kwargs["headers"] == [Path("api.h")]
        assert mock_pe.call_args.kwargs["includes"] == [Path("inc")]

    def test_run_dump_macho_forwards_headers(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="macho")
        with patch("abicheck.service._dump_macho", return_value=snap) as mock_macho:
            run_dump(p, "macho", [Path("api.h")], [], "1.0", "c++")
        assert mock_macho.call_args.kwargs["headers"] == [Path("api.h")]


class TestCliNativeBinaryHeaderWiring:
    """CLI _dump_native_binary must forward headers to service._dump_pe/_dump_macho."""

    def test_cli_pe_forwards_headers(self, tmp_path):
        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="pe")
        with patch("abicheck.service._dump_pe", return_value=snap) as mock_pe:
            _dump_native_binary(p, "pe", [Path("api.h")], [Path("inc")], "1.0", "c++")
        assert mock_pe.call_args.kwargs["headers"] == [Path("api.h")]
        assert mock_pe.call_args.kwargs["includes"] == [Path("inc")]

    def test_cli_macho_forwards_headers(self, tmp_path):
        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        snap = AbiSnapshot(library="lib", version="1.0", platform="macho")
        with patch("abicheck.service._dump_macho", return_value=snap) as mock_macho:
            _dump_native_binary(p, "macho", [Path("api.h")], [], "1.0", "c++")
        assert mock_macho.call_args.kwargs["headers"] == [Path("api.h")]

    def test_cli_pe_wraps_abicheck_error_as_click(self, tmp_path):
        import click

        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 100)
        with patch("abicheck.service._dump_pe", side_effect=SnapshotError("boom")):
            with pytest.raises(click.ClickException, match="boom"):
                _dump_native_binary(p, "pe", [], [], "1.0", "c++")

    def test_cli_macho_wraps_abicheck_error_as_click(self, tmp_path):
        import click

        from abicheck.cli import _dump_native_binary

        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        with patch("abicheck.service._dump_macho", side_effect=SnapshotError("nope")):
            with pytest.raises(click.ClickException, match="nope"):
                _dump_native_binary(p, "macho", [], [], "1.0", "c++")
