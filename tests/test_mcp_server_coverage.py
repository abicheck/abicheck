"""Additional coverage tests for abicheck.mcp_server.

Targets uncovered lines: 37-45, 94-95, 112-113, 140-161, 165-176,
190-195, 208-209, 257-264, 274-297, 303-319, 328-330, 334,
426-427, 778.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock the mcp package before importing mcp_server
# ---------------------------------------------------------------------------
_mock_fastmcp = MagicMock()
_mock_mcp_module = MagicMock()
_mock_mcp_module.server.fastmcp.FastMCP = _mock_fastmcp
sys.modules.setdefault("mcp", _mock_mcp_module)
sys.modules.setdefault("mcp.server", _mock_mcp_module.server)
sys.modules.setdefault("mcp.server.fastmcp", _mock_mcp_module.server.fastmcp)

_mock_mcp_instance = MagicMock()
_mock_mcp_instance.tool.return_value = lambda fn: fn
_mock_fastmcp.return_value = _mock_mcp_instance

# Now safe to import
from abicheck.checker import DiffResult  # noqa: E402
from abicheck.checker_policy import Verdict  # noqa: E402
from abicheck.errors import AbicheckError  # noqa: E402
from abicheck.mcp_server import (  # noqa: E402
    _impact_category,
    _render_output,
    _resolve_input,
    _safe_write_path,
    abi_compare,
    abi_dump,
)
from abicheck.model import AbiSnapshot, Function, Visibility  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_snapshot(lib: str = "libtest.so", version: str = "1.0") -> AbiSnapshot:
    return AbiSnapshot(library=lib, version=version)


def _minimal_diff(verdict: Verdict = Verdict.NO_CHANGE, changes: list | None = None) -> DiffResult:
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libtest.so",
        changes=changes or [],
        verdict=verdict,
    )


# ---------------------------------------------------------------------------
# _safe_write_path — Windows sensitive paths (lines 140-161)
# ---------------------------------------------------------------------------

class TestSafeWritePathWindows:
    """Cover the Windows branch of _safe_write_path."""

    def test_windows_system_path_blocked(self, tmp_path, monkeypatch):
        """Lines 140-163: Windows sensitive prefix detection."""
        monkeypatch.setattr("platform.system", lambda: "Windows")
        # Simulate a resolved path under C:\Windows
        fake_path = r"C:\Windows\System32\evil.json"
        with patch("abicheck.mcp_server.Path") as MockPath:
            mock_resolved = MagicMock()
            mock_resolved.suffix = ".json"
            mock_resolved.__str__ = lambda self: fake_path
            mock_resolved.resolve.return_value = mock_resolved
            # relative_to should raise ValueError (not under home sensitive dirs)
            mock_resolved.relative_to.side_effect = ValueError("not relative")
            MockPath.return_value.resolve.return_value = mock_resolved
            MockPath.home.return_value.resolve.return_value = MagicMock()
            # Make suffix accessible
            type(mock_resolved).suffix = property(lambda s: ".json")

            with pytest.raises(ValueError, match="sensitive system path"):
                _safe_write_path(fake_path)

    def test_windows_nt_extended_path_blocked(self, tmp_path, monkeypatch):
        """Lines 145-148: NT extended path prefix stripping."""
        monkeypatch.setattr("platform.system", lambda: "Windows")
        fake_path = r"\\?\C:\Windows\System32\evil.json"
        with patch("abicheck.mcp_server.Path") as MockPath:
            mock_resolved = MagicMock()
            mock_resolved.suffix = ".json"
            mock_resolved.__str__ = lambda self: fake_path
            mock_resolved.resolve.return_value = mock_resolved
            mock_resolved.relative_to.side_effect = ValueError("not relative")
            MockPath.return_value.resolve.return_value = mock_resolved
            MockPath.home.return_value.resolve.return_value = MagicMock()
            type(mock_resolved).suffix = property(lambda s: ".json")

            with pytest.raises(ValueError, match="sensitive system path"):
                _safe_write_path(fake_path)

    def test_windows_unc_path_blocked(self, tmp_path, monkeypatch):
        """Lines 147-148: UNC prefix handling after NT extended strip."""
        monkeypatch.setattr("platform.system", lambda: "Windows")
        fake_path = r"\\?\UNC\localhost\c$\Windows\evil.json"
        with patch("abicheck.mcp_server.Path") as MockPath:
            mock_resolved = MagicMock()
            mock_resolved.suffix = ".json"
            mock_resolved.__str__ = lambda self: fake_path
            mock_resolved.resolve.return_value = mock_resolved
            mock_resolved.relative_to.side_effect = ValueError("not relative")
            MockPath.return_value.resolve.return_value = mock_resolved
            MockPath.home.return_value.resolve.return_value = MagicMock()
            type(mock_resolved).suffix = property(lambda s: ".json")

            with pytest.raises(ValueError, match="sensitive system path"):
                _safe_write_path(fake_path)


# ---------------------------------------------------------------------------
# _safe_write_path — resolve errors (lines 112-113)
# (credential dir tests moved to test_mcp_server_unit.py::TestSafeWritePath)
# ---------------------------------------------------------------------------

class TestSafeWritePathResolveError:
    def test_invalid_path_raises(self):
        """Lines 112-113: TypeError/ValueError from Path().resolve()."""
        with patch("abicheck.mcp_server.Path") as MockPath:
            MockPath.return_value.resolve.side_effect = ValueError("bad path")
            with pytest.raises(ValueError, match="Invalid output_path"):
                _safe_write_path("some\x00path.json")


# ---------------------------------------------------------------------------
# (_sanitize_error tests moved to test_mcp_server_unit.py::TestSanitizeError)
# ---------------------------------------------------------------------------
# _impact_category — unknown ChangeKind fallback (lines 426-427)
# ---------------------------------------------------------------------------

class TestImpactCategoryUnknown:
    def test_unknown_kind_defaults_to_breaking(self):
        """Lines 426-427: ChangeKind not in any set returns 'breaking'."""
        fake_kind = MagicMock()
        fake_kind.__hash__ = lambda self: hash("fake_unknown_kind")
        fake_kind.__eq__ = lambda self, other: False
        result = _impact_category(fake_kind)
        assert result == "breaking"


# ---------------------------------------------------------------------------
# _resolve_input — PE with no exports (lines 274-297)
# ---------------------------------------------------------------------------

class TestResolveInputPE:
    def test_pe_no_machine_raises(self, tmp_path, monkeypatch):
        """Lines 277-281: PE with no machine field raises."""
        pe_file = tmp_path / "test.dll"
        pe_file.write_bytes(b"MZ" + b"\x00" * 100)

        mock_pe_meta = MagicMock()
        mock_pe_meta.machine = ""
        mock_pe_meta.exports = []

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: "pe",
        )
        monkeypatch.setattr(
            "abicheck.pe_metadata.parse_pe_metadata",
            lambda p: mock_pe_meta,
        )
        with pytest.raises(AbicheckError, match="Failed to extract PE metadata"):
            _resolve_input(pe_file, [], [], "1.0", "c++")

    def test_pe_no_exports_raises(self, tmp_path, monkeypatch):
        """Lines 282-286: PE with machine but no exports raises."""
        pe_file = tmp_path / "test.dll"
        pe_file.write_bytes(b"MZ" + b"\x00" * 100)

        mock_pe_meta = MagicMock()
        mock_pe_meta.machine = "IMAGE_FILE_MACHINE_AMD64"
        mock_pe_meta.exports = []

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: "pe",
        )
        monkeypatch.setattr(
            "abicheck.pe_metadata.parse_pe_metadata",
            lambda p: mock_pe_meta,
        )
        with pytest.raises(AbicheckError, match="has no exports"):
            _resolve_input(pe_file, [], [], "1.0", "c++")

    def test_pe_with_exports_returns_snapshot(self, tmp_path, monkeypatch):
        """Lines 287-300: PE with exports returns AbiSnapshot."""
        pe_file = tmp_path / "test.dll"
        pe_file.write_bytes(b"MZ" + b"\x00" * 100)

        mock_export = MagicMock()
        mock_export.name = "MyFunc"
        mock_export.ordinal = 1

        mock_pe_meta = MagicMock()
        mock_pe_meta.machine = "IMAGE_FILE_MACHINE_AMD64"
        mock_pe_meta.exports = [mock_export]

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: "pe",
        )
        monkeypatch.setattr(
            "abicheck.pe_metadata.parse_pe_metadata",
            lambda p: mock_pe_meta,
        )
        snap = _resolve_input(pe_file, [], [], "1.0", "c++")
        assert snap.platform == "pe"
        assert len(snap.functions) == 1
        assert snap.functions[0].name == "MyFunc"


# ---------------------------------------------------------------------------
# _resolve_input — Mach-O paths (lines 302-322)
# ---------------------------------------------------------------------------

class TestResolveInputMachO:
    def test_macho_no_exports_no_metadata_raises(self, tmp_path, monkeypatch):
        """Lines 306-310: Mach-O with no exports and no load-command metadata."""
        macho_file = tmp_path / "test.dylib"
        macho_file.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)

        mock_macho_meta = MagicMock()
        mock_macho_meta.exports = []
        mock_macho_meta.install_name = ""
        mock_macho_meta.dependent_libs = []

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: "macho",
        )
        monkeypatch.setattr(
            "abicheck.macho_metadata.parse_macho_metadata",
            lambda p: mock_macho_meta,
        )
        with pytest.raises(AbicheckError, match="has no exports"):
            _resolve_input(macho_file, [], [], "1.0", "c++")

    def test_macho_with_exports_returns_snapshot(self, tmp_path, monkeypatch):
        """Lines 311-322: Mach-O with exports returns AbiSnapshot."""
        macho_file = tmp_path / "test.dylib"
        macho_file.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)

        mock_export = MagicMock()
        mock_export.name = "_my_func"

        mock_macho_meta = MagicMock()
        mock_macho_meta.exports = [mock_export]
        mock_macho_meta.install_name = "libtest.dylib"
        mock_macho_meta.dependent_libs = []

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: "macho",
        )
        monkeypatch.setattr(
            "abicheck.macho_metadata.parse_macho_metadata",
            lambda p: mock_macho_meta,
        )
        snap = _resolve_input(macho_file, [], [], "1.0", "c++")
        assert snap.platform == "macho"
        assert len(snap.functions) == 1
        assert snap.functions[0].name == "_my_func"


# ---------------------------------------------------------------------------
# _resolve_input — text-based: perl dump, json snapshot, unknown (lines 324-342)
# ---------------------------------------------------------------------------

class TestResolveInputText:
    def test_read_error_raises_abicheckError(self, tmp_path, monkeypatch):
        """Lines 328-330: OSError reading text file."""
        bad_file = tmp_path / "bad.txt"
        bad_file.write_text("hello")

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: None,
        )
        # Make open fail
        import builtins
        real_open = builtins.open

        def failing_open(path, *a, **kw):
            if str(path) == str(bad_file):
                raise OSError("disk error")
            return real_open(path, *a, **kw)

        monkeypatch.setattr(builtins, "open", failing_open)
        with pytest.raises(AbicheckError, match="Cannot detect format"):
            _resolve_input(bad_file, [], [], "1.0", "c++")

    def test_perl_dump_detected(self, tmp_path, monkeypatch):
        """Line 334: Perl dump detection and import."""
        dump_file = tmp_path / "dump.pl"
        dump_file.write_text("$VAR1 = { ... };")

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: None,
        )
        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.compat.abicc_dump_import.import_abicc_perl_dump",
            lambda p: fake_snap,
        )
        result = _resolve_input(dump_file, [], [], "1.0", "c++")
        assert result is fake_snap

    def test_json_snapshot_detected(self, tmp_path, monkeypatch):
        """Lines 336-337: JSON snapshot starts with '{'."""
        snap_file = tmp_path / "snap.json"
        snap_file.write_text('{"library": "libfoo.so", "version": "1.0", "functions": []}')

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: None,
        )
        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.service.load_snapshot",
            lambda p: fake_snap,
        )
        result = _resolve_input(snap_file, [], [], "1.0", "c++")
        assert result is fake_snap

    def test_unknown_format_raises(self, tmp_path, monkeypatch):
        """Lines 339-342: unrecognized text format raises."""
        unknown_file = tmp_path / "unknown.txt"
        unknown_file.write_text("some random data that is not json or perl")

        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: None,
        )
        with pytest.raises(AbicheckError, match="Cannot detect format"):
            _resolve_input(unknown_file, [], [], "1.0", "c++")


# ---------------------------------------------------------------------------
# _resolve_input — ELF (lines 257-264)
# ---------------------------------------------------------------------------

class TestResolveInputELF:
    def test_elf_invokes_dump(self, tmp_path, monkeypatch):
        """Lines 257-271: ELF binary triggers dump()."""
        elf_file = tmp_path / "lib.so"
        elf_file.write_bytes(b"\x7fELF" + b"\x00" * 100)

        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._detect_binary_format",
            lambda p: "elf",
        )
        monkeypatch.setattr(
            "abicheck.dumper.dump",
            lambda **kw: fake_snap,
        )
        result = _resolve_input(elf_file, [], [], "1.0", "c++")
        assert result is fake_snap

    def test_elf_unusual_lang_forwarded(self, tmp_path, monkeypatch):
        """lang is no longer rejected at the MCP resolve layer.

        _resolve_input now delegates to ``service.resolve_input`` (the single
        source of truth), which forwards lang to the dumper (mapping any non-'c'
        value to the c++ profile) rather than raising. The dump call is mocked
        so we assert the value is threaded through instead of rejected.
        """
        elf_file = tmp_path / "lib.so"
        elf_file.write_bytes(b"\x7fELF" + b"\x00" * 100)

        fake_snap = _empty_snapshot()
        monkeypatch.setattr("abicheck.dumper.dump", lambda **kw: fake_snap)
        result = _resolve_input(elf_file, [], [], "1.0", "rust")
        assert result is fake_snap

    def test_linker_script_following_disabled(self, tmp_path, monkeypatch):
        """MCP must not follow GNU ld linker scripts.

        _check_file_size only guards the caller-supplied path, so following an
        INPUT(huge.so) directive would parse an unchecked target and bypass
        MCP_MAX_FILE_SIZE. _resolve_input pins follow_linker_scripts=False.
        """
        import abicheck.service as svc

        captured: dict = {}

        def fake_resolve(path, headers, includes, version, lang, **kwargs):
            captured.update(kwargs)
            return _empty_snapshot()

        monkeypatch.setattr(svc, "resolve_input", fake_resolve)
        _resolve_input(tmp_path / "libfoo.so", [], [], "1.0", "c++")
        assert captured.get("follow_linker_scripts") is False


# ---------------------------------------------------------------------------
# _render_output — stat, sarif, html, markdown, unknown (lines 376-405)
# ---------------------------------------------------------------------------

class TestRenderOutput:
    def test_stat_json(self, monkeypatch):
        """Lines 377-379: stat=True with json format."""
        result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.reporter.to_stat_json",
            lambda r: '{"stat": "ok"}',
        )
        out = _render_output("json", result, _empty_snapshot(), _empty_snapshot(), stat=True)
        assert '"stat"' in out

    def test_stat_non_json(self, monkeypatch):
        """Lines 380-381: stat=True with non-json format."""
        result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.reporter.to_stat",
            lambda r: "COMPATIBLE 0 breaking",
        )
        out = _render_output("markdown", result, _empty_snapshot(), _empty_snapshot(), stat=True)
        assert "COMPATIBLE" in out

    def test_sarif_format(self, monkeypatch):
        """Lines 384-386: sarif output format."""
        result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.sarif.to_sarif_str",
            lambda r, **kw: '{"$schema": "sarif"}',
        )
        out = _render_output("sarif", result, _empty_snapshot(), _empty_snapshot())
        assert "sarif" in out

    def test_html_format(self, monkeypatch):
        """Lines 387-404: html output format."""
        result = _minimal_diff()
        old_snap = AbiSnapshot(
            library="libtest.so",
            version="1.0",
            functions=[
                Function(name="foo", mangled="foo", return_type="int", visibility=Visibility.PUBLIC),
            ],
        )
        monkeypatch.setattr(
            "abicheck.html_report.generate_html_report",
            lambda r, **kw: "<html>report</html>",
        )
        out = _render_output("html", result, old_snap, _empty_snapshot())
        assert "<html>" in out

    def test_markdown_format(self, monkeypatch):
        """Line 405: markdown output format (default path)."""
        result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.mcp_server.to_markdown",
            lambda r, **kw: "# ABI Report\n",
        )
        out = _render_output("markdown", result, _empty_snapshot(), _empty_snapshot())
        assert "ABI Report" in out

    def test_unknown_format_raises(self):
        """Lines 373-375: unknown format raises ValueError."""
        result = _minimal_diff()
        with pytest.raises(ValueError, match="Unknown output format"):
            _render_output("xml", result, _empty_snapshot(), _empty_snapshot())


# ---------------------------------------------------------------------------
# abi_dump — file not found, output_path writing (lines 461-486)
# ---------------------------------------------------------------------------

class TestAbiDumpTool:
    def test_file_not_found_returns_error(self, tmp_path):
        """Line 462: library_path does not exist."""
        result_str = abi_dump(library_path=str(tmp_path / "nonexistent.so"))
        result = json.loads(result_str)
        assert result["status"] == "error"
        assert "not found" in result["error"]

    def test_output_path_writes_file(self, tmp_path, monkeypatch):
        """Lines 470-477: output_path writes snapshot to disk."""
        lib = tmp_path / "lib.so"
        lib.write_bytes(b"\x7fELF" + b"\x00" * 100)
        out = tmp_path / "snap.json"

        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        monkeypatch.setattr(
            "abicheck.mcp_server.snapshot_to_json",
            lambda s: '{"library": "libtest.so"}',
        )
        result_str = abi_dump(library_path=str(lib), output_path=str(out))
        result = json.loads(result_str)
        assert result["status"] == "ok"
        assert "output_path" in result
        assert out.read_text() == '{"library": "libtest.so"}'


# ---------------------------------------------------------------------------
# abi_compare — various coverage gaps
# ---------------------------------------------------------------------------

class TestAbiCompareTool:
    def _make_inputs(self, tmp_path):
        """Create two fake JSON snapshot files."""
        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        old.write_text('{"library": "lib", "version": "1.0", "functions": []}')
        new.write_text('{"library": "lib", "version": "2.0", "functions": []}')
        return old, new

    def test_invalid_policy_returns_error(self, tmp_path, monkeypatch):
        """Lines 548-553: unknown policy name."""
        old, new = self._make_inputs(tmp_path)
        result_str = abi_compare(
            old_input=str(old), new_input=str(new), policy="nonexistent_policy",
        )
        result = json.loads(result_str)
        assert result["status"] == "error"
        assert "Unknown policy" in result["error"]

    def test_invalid_show_only_returns_error(self, tmp_path, monkeypatch):
        """Lines 581-586: invalid show_only tokens."""
        old, new = self._make_inputs(tmp_path)
        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        # Mock ShowOnlyFilter.parse to raise
        monkeypatch.setattr(
            "abicheck.reporter.ShowOnlyFilter.parse",
            staticmethod(lambda s: (_ for _ in ()).throw(ValueError("invalid token: garbage"))),
        )
        result_str = abi_compare(
            old_input=str(old), new_input=str(new),
            show_only="garbage",
        )
        result = json.loads(result_str)
        assert result["status"] == "error"
        assert "Invalid show_only" in result["error"]

    def test_suppression_file_loaded(self, tmp_path, monkeypatch):
        """Lines 566-568: suppression_file loading."""
        old, new = self._make_inputs(tmp_path)
        supp_file = tmp_path / "suppress.yaml"
        supp_file.write_text("version: 1\nsuppressions: []")

        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        mock_supp = MagicMock()
        monkeypatch.setattr(
            "abicheck.suppression.SuppressionList.load",
            classmethod(lambda cls, p: mock_supp),
        )
        fake_result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.mcp_server.compare_snapshots",
            lambda old, new, **kw: fake_result,
        )
        monkeypatch.setattr(
            "abicheck.mcp_server.to_json",
            lambda r, **kw: '{"report": "ok"}',
        )
        result_str = abi_compare(
            old_input=str(old), new_input=str(new),
            suppression_file=str(supp_file),
        )
        result = json.loads(result_str)
        assert result["status"] == "ok"

    def test_policy_file_loaded(self, tmp_path, monkeypatch):
        """Lines 571-574: policy_file loading (skips base policy validation)."""
        old, new = self._make_inputs(tmp_path)
        pf = tmp_path / "policy.yaml"
        pf.write_text("base: strict_abi")

        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        mock_pf = MagicMock()
        monkeypatch.setattr(
            "abicheck.policy_file.PolicyFile.load",
            classmethod(lambda cls, p: mock_pf),
        )
        fake_result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.mcp_server.compare_snapshots",
            lambda old, new, **kw: fake_result,
        )
        monkeypatch.setattr(
            "abicheck.mcp_server.to_json",
            lambda r, **kw: '{"report": "ok"}',
        )
        result_str = abi_compare(
            old_input=str(old), new_input=str(new),
            policy_file=str(pf),
        )
        result = json.loads(result_str)
        assert result["status"] == "ok"

    def test_stat_mode(self, tmp_path, monkeypatch):
        """stat=True triggers stat rendering path."""
        old, new = self._make_inputs(tmp_path)
        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        fake_result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.mcp_server.compare_snapshots",
            lambda old, new, **kw: fake_result,
        )
        monkeypatch.setattr(
            "abicheck.reporter.to_stat_json",
            lambda r: '{"stat": true}',
        )
        result_str = abi_compare(
            old_input=str(old), new_input=str(new), stat=True,
        )
        result = json.loads(result_str)
        assert result["status"] == "ok"

    def test_sarif_output_format(self, tmp_path, monkeypatch):
        """sarif output format in abi_compare."""
        old, new = self._make_inputs(tmp_path)
        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        fake_result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.mcp_server.compare_snapshots",
            lambda old, new, **kw: fake_result,
        )
        monkeypatch.setattr(
            "abicheck.sarif.to_sarif_str",
            lambda r, **kw: '{"$schema": "sarif"}',
        )
        result_str = abi_compare(
            old_input=str(old), new_input=str(new), output_format="sarif",
        )
        result = json.loads(result_str)
        assert result["status"] == "ok"
        assert "report" in result

    def test_html_output_format(self, tmp_path, monkeypatch):
        """html output format in abi_compare."""
        old, new = self._make_inputs(tmp_path)
        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        fake_result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.mcp_server.compare_snapshots",
            lambda old, new, **kw: fake_result,
        )
        monkeypatch.setattr(
            "abicheck.html_report.generate_html_report",
            lambda r, **kw: "<html>report</html>",
        )
        result_str = abi_compare(
            old_input=str(old), new_input=str(new), output_format="html",
        )
        result = json.loads(result_str)
        assert result["status"] == "ok"
        assert "<html>" in result["report"]

    def test_markdown_output_format(self, tmp_path, monkeypatch):
        """markdown output format in abi_compare."""
        old, new = self._make_inputs(tmp_path)
        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        fake_result = _minimal_diff()
        monkeypatch.setattr(
            "abicheck.mcp_server.compare_snapshots",
            lambda old, new, **kw: fake_result,
        )
        monkeypatch.setattr(
            "abicheck.mcp_server.to_markdown",
            lambda r, **kw: "# ABI Report\n",
        )
        result_str = abi_compare(
            old_input=str(old), new_input=str(new), output_format="markdown",
        )
        result = json.loads(result_str)
        assert result["status"] == "ok"
        assert "ABI Report" in result["report"]

    def test_invalid_output_format(self, tmp_path, monkeypatch):
        """Lines 577-578: invalid output_format returns error early."""
        old, new = self._make_inputs(tmp_path)
        fake_snap = _empty_snapshot()
        monkeypatch.setattr(
            "abicheck.mcp_server._resolve_input",
            lambda *a, **kw: fake_snap,
        )
        result_str = abi_compare(
            old_input=str(old), new_input=str(new), output_format="xml",
        )
        result = json.loads(result_str)
        assert result["status"] == "error"
        assert "Unknown output format" in result["error"]


# ---------------------------------------------------------------------------
# _safe_read_path — resolve error (lines 94-95)
# ---------------------------------------------------------------------------

class TestSafeReadPathResolveError:
    def test_invalid_path_raises(self):
        """Lines 94-95: resolve raises TypeError/ValueError."""
        from abicheck.mcp_server import _safe_read_path
        with patch("abicheck.mcp_server.Path") as MockPath:
            MockPath.return_value.resolve.side_effect = TypeError("bad type")
            with pytest.raises(ValueError, match="Invalid path"):
                _safe_read_path("some\x00path")
