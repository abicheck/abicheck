"""Unit tests for abicheck.mcp_server — mock-based, no real MCP server needed.

These tests mock the ``mcp`` package at import time so the MCP server module
can be loaded and tested without installing the ``mcp`` dependency.  All
public helpers, path-safety routines, and MCP tool functions are exercised
through mocks and ``tmp_path`` fixtures.

Target: 80%+ statement coverage of abicheck/mcp_server.py (287 statements).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

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

# Make FastMCP return a mock whose .tool() is a pass-through decorator
_mock_mcp_instance = MagicMock()
_mock_mcp_instance.tool.return_value = lambda fn: fn
_mock_fastmcp.return_value = _mock_mcp_instance

# Now it is safe to import
from abicheck.checker import DiffResult  # noqa: E402
from abicheck.checker_policy import (  # noqa: E402
    API_BREAK_KINDS,
    ChangeKind,
    Verdict,
)
from abicheck.errors import AbicheckError  # noqa: E402
from abicheck.mcp_server import (  # noqa: E402
    _detect_binary_format,
    _impact_category,
    _render_output,
    _resolve_input,
    _safe_read_path,
    _safe_write_path,
    _sanitize_error,
    _snapshot_summary,
    abi_compare,
    abi_dump,
    abi_explain_change,
    abi_list_changes,
    abi_scan,
    main,
)
from abicheck.model import (  # noqa: E402
    AbiSnapshot,
    EnumMember,
    EnumType,
    Function,
    RecordType,
    Variable,
    Visibility,
)
from abicheck.serialization import snapshot_to_json  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(
    version: str = "1.0",
    library: str = "libtest.so.1",
    functions: list[Function] | None = None,
    variables: list[Variable] | None = None,
    types: list[RecordType] | None = None,
    enums: list[EnumType] | None = None,
    platform: str | None = None,
) -> AbiSnapshot:
    return AbiSnapshot(
        library=library,
        version=version,
        functions=functions or [],
        variables=variables or [],
        types=types or [],
        enums=enums or [],
        platform=platform,
    )


def _pub_func(name: str, mangled: str, ret: str = "void") -> Function:
    return Function(
        name=name,
        mangled=mangled,
        return_type=ret,
        visibility=Visibility.PUBLIC,
    )


def _pub_var(name: str, mangled: str, type_: str) -> Variable:
    return Variable(
        name=name,
        mangled=mangled,
        type=type_,
        visibility=Visibility.PUBLIC,
    )


def _write_snapshot(path: Path, snap: AbiSnapshot) -> None:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")


# ===================================================================
# 1. _safe_read_path
# ===================================================================


class TestSafeReadPath:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            _safe_read_path("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            _safe_read_path("   ")

    def test_valid_path_returns_resolved(self, tmp_path: Path):
        f = tmp_path / "foo.so"
        f.write_bytes(b"\x00")
        result = _safe_read_path(str(f))
        assert result == f.resolve()
        assert result.is_absolute()

    def test_relative_path_resolved(self):
        result = _safe_read_path("some/relative/path.json")
        assert result.is_absolute()

    def test_dotdot_resolved(self, tmp_path: Path):
        sub = tmp_path / "sub"
        sub.mkdir()
        result = _safe_read_path(str(sub / ".." / "sub"))
        assert result == sub.resolve()

    def test_custom_label_in_error(self):
        with pytest.raises(ValueError, match="Empty library_path"):
            _safe_read_path("", label="library_path")


# ===================================================================
# 2. _safe_write_path
# ===================================================================


class TestSafeWritePath:
    def test_empty_string_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            _safe_write_path("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="Empty"):
            _safe_write_path("   ")

    def test_non_json_extension_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match=r"\.json"):
            _safe_write_path(str(tmp_path / "output.txt"))

    def test_no_extension_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match=r"\.json"):
            _safe_write_path(str(tmp_path / "output"))

    def test_xml_extension_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match=r"\.json"):
            _safe_write_path(str(tmp_path / "output.xml"))

    def test_valid_json_path(self, tmp_path: Path):
        result = _safe_write_path(str(tmp_path / "out.json"))
        assert result.suffix == ".json"
        assert result.is_absolute()

    @pytest.mark.skipif(
        __import__("platform").system() == "Windows",
        reason="POSIX-only sensitive paths",
    )
    @pytest.mark.parametrize(
        "path",
        [
            "/etc/foo.json",
            "/bin/foo.json",
            "/sbin/foo.json",
            "/usr/bin/foo.json",
            "/usr/sbin/foo.json",
            "/boot/foo.json",
            "/sys/foo.json",
            "/proc/foo.json",
            "/dev/foo.json",
        ],
    )
    def test_posix_sensitive_path_blocked(self, path):
        with pytest.raises(ValueError, match="sensitive system path"):
            _safe_write_path(path)

    @pytest.mark.parametrize(
        "dotdir,filename",
        [
            (".ssh", "keys.json"),
            (".aws", "config.json"),
            (".gnupg", "ring.json"),
        ],
    )
    def test_credential_dir_blocked(self, dotdir, filename):
        home = Path.home()
        with pytest.raises(ValueError, match="credential directory"):
            _safe_write_path(str(home / dotdir / filename))

    def test_custom_label_in_error(self):
        with pytest.raises(ValueError, match="Empty my_output"):
            _safe_write_path("", label="my_output")

    @pytest.mark.skipif(
        __import__("platform").system() != "Windows",
        reason="Windows-only test",
    )
    def test_windows_system_path_blocked(self):
        with pytest.raises(ValueError, match="sensitive system path"):
            _safe_write_path("C:\\Windows\\System32\\out.json")

    @pytest.mark.skipif(
        __import__("platform").system() != "Windows",
        reason="Windows-only test",
    )
    def test_windows_program_files_blocked(self):
        with pytest.raises(ValueError, match="sensitive system path"):
            _safe_write_path("C:\\Program Files\\out.json")

    @pytest.mark.skipif(
        __import__("platform").system() != "Windows",
        reason="Windows-only test",
    )
    def test_windows_nt_extended_path_blocked(self):
        with pytest.raises(ValueError, match="sensitive system path"):
            _safe_write_path(r"\\?\C:\Windows\out.json")

    @pytest.mark.skipif(
        __import__("platform").system() != "Windows",
        reason="Windows-only test",
    )
    def test_windows_unc_admin_share_blocked(self):
        with pytest.raises(ValueError, match="sensitive system path"):
            _safe_write_path(r"\\?\UNC\localhost\c$\Windows\out.json")


# ===================================================================
# 3. _sanitize_error
# ===================================================================


class TestSanitizeError:
    def test_abicheck_error_passed_through(self):
        exc = AbicheckError("domain-specific message")
        assert _sanitize_error(exc) == "domain-specific message"

    def test_value_error_passed_through(self):
        exc = ValueError("bad input value")
        assert _sanitize_error(exc) == "bad input value"

    def test_key_error_passed_through(self):
        exc = KeyError("missing_key")
        result = _sanitize_error(exc)
        assert "missing_key" in result

    def test_os_error_generic(self):
        exc = OSError("[Errno 13] Permission denied: '/secret/path'")
        result = _sanitize_error(exc, context="read_op")
        assert "/secret/path" not in result
        assert "file system error" in result
        assert "read_op" in result

    def test_file_not_found_error_generic(self):
        exc = FileNotFoundError("No such file: '/home/user/private.dat'")
        result = _sanitize_error(exc, context="load")
        assert "/home/user" not in result
        assert "file system error" in result
        assert "load" in result

    def test_permission_error_generic(self):
        exc = PermissionError("cannot read /etc/shadow")
        result = _sanitize_error(exc, context="access")
        assert "/etc/shadow" not in result
        assert "file system error" in result

    def test_random_exception_generic(self):
        exc = RuntimeError("internal detail: 0xDEADBEEF")
        result = _sanitize_error(exc, context="process")
        assert "0xDEADBEEF" not in result
        assert "unexpected error" in result
        assert "process" in result

    def test_type_error_generic(self):
        exc = TypeError("NoneType is not iterable")
        result = _sanitize_error(exc, context="parsing")
        assert "NoneType" not in result
        assert "unexpected error" in result

    def test_default_context(self):
        exc = OSError("disk full")
        result = _sanitize_error(exc)
        assert "operation failed" in result


# ===================================================================
# 4. _detect_binary_format
# ===================================================================


class TestDetectBinaryFormat:
    def test_elf_magic(self, tmp_path: Path):
        f = tmp_path / "lib.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 100)
        assert _detect_binary_format(f) == "elf"

    def test_pe_magic(self, tmp_path: Path):
        f = tmp_path / "lib.dll"
        f.write_bytes(b"MZ\x90\x00" + b"\x00" * 100)
        assert _detect_binary_format(f) == "pe"

    def test_pe_minimal_mz(self, tmp_path: Path):
        """PE detection only checks first 2 bytes for 'MZ'."""
        f = tmp_path / "lib.dll"
        f.write_bytes(b"MZ\x00\x00")
        assert _detect_binary_format(f) == "pe"

    def test_macho_le32(self, tmp_path: Path):
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xce\xfa\xed\xfe" + b"\x00" * 100)
        assert _detect_binary_format(f) == "macho"

    def test_macho_be32(self, tmp_path: Path):
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 100)
        assert _detect_binary_format(f) == "macho"

    def test_macho_le64(self, tmp_path: Path):
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xcf\xfa\xed\xfe" + b"\x00" * 100)
        assert _detect_binary_format(f) == "macho"

    def test_macho_be64(self, tmp_path: Path):
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xfe\xed\xfa\xcf" + b"\x00" * 100)
        assert _detect_binary_format(f) == "macho"

    def test_macho_fat_le(self, tmp_path: Path):
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xca\xfe\xba\xbe" + b"\x00" * 100)
        assert _detect_binary_format(f) == "macho"

    def test_macho_fat_be(self, tmp_path: Path):
        f = tmp_path / "lib.dylib"
        f.write_bytes(b"\xbe\xba\xfe\xca" + b"\x00" * 100)
        assert _detect_binary_format(f) == "macho"

    def test_unknown_magic(self, tmp_path: Path):
        f = tmp_path / "unknown.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        assert _detect_binary_format(f) is None

    def test_nonexistent_file(self):
        assert _detect_binary_format(Path("/nonexistent/no_such_file.so")) is None

    def test_empty_file(self, tmp_path: Path):
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert _detect_binary_format(f) is None

    def test_json_file_not_binary(self, tmp_path: Path):
        f = tmp_path / "snap.json"
        f.write_text('{"library": "test"}', encoding="utf-8")
        assert _detect_binary_format(f) is None


# ===================================================================
# 5. _snapshot_summary
# ===================================================================


class TestSnapshotSummary:
    def test_basic_summary(self):
        snap = _make_snapshot(
            version="2.0",
            library="libfoo.so.3",
            functions=[_pub_func("a", "_Za"), _pub_func("b", "_Zb")],
            variables=[_pub_var("x", "x", "int")],
        )
        summary = _snapshot_summary(snap)
        assert summary["library"] == "libfoo.so.3"
        assert summary["version"] == "2.0"
        assert summary["functions"] == 2
        assert summary["variables"] == 1
        assert summary["types"] == 0
        assert summary["enums"] == 0

    def test_with_types_and_enums(self):
        snap = _make_snapshot(
            types=[RecordType(name="MyStruct", kind="struct")],
            enums=[EnumType(name="Color", members=[EnumMember(name="RED", value=0)])],
        )
        summary = _snapshot_summary(snap)
        assert summary["types"] == 1
        assert summary["enums"] == 1

    def test_empty_snapshot(self):
        snap = _make_snapshot()
        summary = _snapshot_summary(snap)
        assert summary["functions"] == 0
        assert summary["variables"] == 0
        assert summary["types"] == 0
        assert summary["enums"] == 0

    def test_platform_field(self):
        snap = _make_snapshot(platform="elf")
        summary = _snapshot_summary(snap)
        assert summary["platform"] == "elf"

    def test_platform_none(self):
        snap = _make_snapshot()
        summary = _snapshot_summary(snap)
        assert summary["platform"] is None


# ===================================================================
# 6. _render_output
# ===================================================================


class TestRenderOutput:
    def _make_diff_result(
        self, verdict: Verdict = Verdict.NO_CHANGE, changes: list | None = None
    ) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
        old = _make_snapshot("1.0", functions=[_pub_func("init", "_Z4initv")])
        new = _make_snapshot("2.0", functions=[_pub_func("init", "_Z4initv")])
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            verdict=verdict,
            changes=changes or [],
        )
        return result, old, new

    def test_json_format(self):
        result, old, new = self._make_diff_result()
        output = _render_output("json", result, old, new)
        parsed = json.loads(output)
        assert parsed["verdict"] == "NO_CHANGE"

    def test_markdown_format(self):
        result, old, new = self._make_diff_result()
        output = _render_output("markdown", result, old, new)
        assert isinstance(output, str)
        assert len(output) > 0

    def test_sarif_format(self):
        result, old, new = self._make_diff_result()
        output = _render_output("sarif", result, old, new)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_html_format(self):
        result, old, new = self._make_diff_result()
        output = _render_output("html", result, old, new)
        assert "<html" in output.lower() or "<!doctype" in output.lower()

    def test_html_format_forwards_severity_config(self):
        """The MCP HTML branch must forward severity_config so the CI Gate
        card renders — otherwise an MCP abi_compare call configured with
        severity_* options reports a failing exit_code in the JSON wrapper
        while the embedded HTML shows no gate at all."""
        from abicheck.checker import Change, ChangeKind
        from abicheck.severity import resolve_severity_config

        c = Change(ChangeKind.FUNC_ADDED, "_Z3newv", "new public function")
        result, old, new = self._make_diff_result(
            verdict=Verdict.COMPATIBLE, changes=[c],
        )
        cfg = resolve_severity_config("default", addition="error")
        output = _render_output(
            "html", result, old, new, severity_config=cfg,
        )
        assert "CI Gate" in output
        assert "FAIL" in output

    def test_invalid_format_raises(self):
        result, old, new = self._make_diff_result()
        with pytest.raises(ValueError, match="Unknown output format"):
            _render_output("xml", result, old, new)

    def test_invalid_format_csv_raises(self):
        result, old, new = self._make_diff_result()
        with pytest.raises(ValueError, match="Unknown output format"):
            _render_output("csv", result, old, new)

    def test_stat_true_json(self):
        result, old, new = self._make_diff_result()
        output = _render_output("json", result, old, new, stat=True)
        # stat_json returns a JSON string
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_stat_true_non_json(self):
        result, old, new = self._make_diff_result()
        output = _render_output("markdown", result, old, new, stat=True)
        assert isinstance(output, str)

    def test_show_only_parameter(self):
        result, old, new = self._make_diff_result()
        output = _render_output("json", result, old, new, show_only="functions")
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_report_mode_leaf(self):
        result, old, new = self._make_diff_result()
        output = _render_output("json", result, old, new, report_mode="leaf")
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_show_impact_true(self):
        result, old, new = self._make_diff_result()
        output = _render_output("markdown", result, old, new, show_impact=True)
        assert isinstance(output, str)

    def test_html_with_public_functions(self):
        """HTML render counts public+elf_only symbols."""
        old = _make_snapshot(
            "1.0",
            functions=[
                _pub_func("f1", "_Zf1"),
                Function(
                    name="elf_only_fn",
                    mangled="_elf",
                    return_type="void",
                    visibility=Visibility.ELF_ONLY,
                ),
            ],
        )
        new = _make_snapshot("2.0")
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            verdict=Verdict.BREAKING,
        )
        output = _render_output("html", result, old, new)
        assert "<html" in output.lower() or "<!doctype" in output.lower()

    def test_html_with_public_variables(self):
        """HTML render counts public variables in old_symbol_count."""
        old = _make_snapshot(
            "1.0",
            variables=[_pub_var("v1", "v1", "int")],
        )
        new = _make_snapshot("2.0")
        result = DiffResult(
            old_version="1.0",
            new_version="2.0",
            library="libtest.so.1",
            verdict=Verdict.NO_CHANGE,
        )
        output = _render_output("html", result, old, new)
        assert "<html" in output.lower() or "<!doctype" in output.lower()


# ===================================================================
# 7. _impact_category
# ===================================================================


class TestImpactCategory:
    def test_breaking_kinds(self):
        for kind in [
            ChangeKind.FUNC_REMOVED,
            ChangeKind.FUNC_RETURN_CHANGED,
            ChangeKind.TYPE_SIZE_CHANGED,
            ChangeKind.VAR_REMOVED,
        ]:
            assert _impact_category(kind) == "breaking", f"{kind} should be breaking"

    def test_api_break_kinds(self):
        for kind in [
            ChangeKind.ENUM_MEMBER_RENAMED,
            ChangeKind.PARAM_DEFAULT_VALUE_REMOVED,
        ]:
            if kind in API_BREAK_KINDS:
                assert _impact_category(kind) == "api_break", (
                    f"{kind} should be api_break"
                )

    def test_risk_kinds(self):
        assert _impact_category(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED) == "risk"

    def test_compatible_kinds(self):
        assert _impact_category(ChangeKind.FUNC_ADDED) == "compatible"
        assert _impact_category(ChangeKind.VAR_ADDED) == "compatible"
        assert _impact_category(ChangeKind.TYPE_ADDED) == "compatible"

    def test_default_policy_is_strict_abi(self):
        # Without specifying policy, should use strict_abi
        result = _impact_category(ChangeKind.FUNC_REMOVED)
        assert result == "breaking"

    def test_sdk_vendor_downgrades_some_api_breaks(self):
        # enum_member_renamed is api_break under strict_abi, compatible under sdk_vendor
        assert (
            _impact_category(ChangeKind.ENUM_MEMBER_RENAMED, "strict_abi")
            == "api_break"
        )
        assert (
            _impact_category(ChangeKind.ENUM_MEMBER_RENAMED, "sdk_vendor")
            == "compatible"
        )

    def test_plugin_abi_policy(self):
        # calling_convention_changed is breaking under strict_abi
        assert (
            _impact_category(ChangeKind.CALLING_CONVENTION_CHANGED, "strict_abi")
            == "breaking"
        )
        # but compatible under plugin_abi
        assert (
            _impact_category(ChangeKind.CALLING_CONVENTION_CHANGED, "plugin_abi")
            == "compatible"
        )


# ===================================================================
# 8. abi_list_changes
# ===================================================================


class TestAbiListChanges:
    def test_no_filter_returns_all(self):
        raw = abi_list_changes()
        data = json.loads(raw)
        assert "change_kinds" in data
        assert "count" in data
        assert data["count"] > 50
        assert data["count"] == len(data["change_kinds"])

    @pytest.mark.parametrize("impact", ["breaking", "api_break", "risk", "compatible"])
    def test_filter_by_impact(self, impact):
        raw = abi_list_changes(impact=impact)
        data = json.loads(raw)
        assert data["count"] > 0
        for entry in data["change_kinds"]:
            assert entry["impact"] == impact

    def test_unknown_impact_returns_error(self):
        raw = abi_list_changes(impact="bogus")
        data = json.loads(raw)
        assert data["status"] == "error"
        assert "Unknown impact filter" in data["error"]

    def test_entries_have_required_fields(self):
        raw = abi_list_changes()
        data = json.loads(raw)
        for entry in data["change_kinds"]:
            assert "kind" in entry
            assert "impact" in entry
            assert "default_verdict" in entry
            assert "description" in entry

    def test_all_change_kinds_covered(self):
        raw = abi_list_changes()
        data = json.loads(raw)
        returned_kinds = {e["kind"] for e in data["change_kinds"]}
        all_kinds = {k.value for k in ChangeKind}
        assert returned_kinds == all_kinds

    def test_results_sorted_by_kind_value(self):
        raw = abi_list_changes()
        data = json.loads(raw)
        kinds = [e["kind"] for e in data["change_kinds"]]
        assert kinds == sorted(kinds)


# ===================================================================
# 9. abi_explain_change
# ===================================================================


class TestAbiExplainChange:
    def test_valid_kind_returns_details(self):
        raw = abi_explain_change("func_removed")
        data = json.loads(raw)
        assert data["kind"] == "func_removed"
        assert data["impact"] == "breaking"
        assert data["default_verdict"] == "BREAKING"
        assert "description" in data
        assert "fix_guidance" in data
        assert "severity" in data

    def test_case_insensitive_lookup(self):
        raw = abi_explain_change("FUNC_REMOVED")
        data = json.loads(raw)
        assert data["kind"] == "func_removed"

    def test_mixed_case_lookup(self):
        raw = abi_explain_change("Func_Removed")
        data = json.loads(raw)
        assert data["kind"] == "func_removed"

    def test_unknown_kind_returns_error(self):
        raw = abi_explain_change("totally_nonexistent_kind")
        data = json.loads(raw)
        assert data["status"] == "error"
        assert "Unknown change kind" in data["error"]

    def test_breaking_fix_guidance(self):
        raw = abi_explain_change("func_removed")
        data = json.loads(raw)
        assert "binary ABI break" in data["fix_guidance"]

    def test_api_break_fix_guidance(self):
        raw = abi_explain_change("enum_member_renamed")
        data = json.loads(raw)
        assert "source-level API break" in data["fix_guidance"]

    def test_risk_fix_guidance(self):
        raw = abi_explain_change("symbol_version_required_added")
        data = json.loads(raw)
        assert "deployment risk" in data["fix_guidance"]

    def test_compatible_fix_guidance(self):
        raw = abi_explain_change("func_added")
        data = json.loads(raw)
        assert "backward-compatible" in data["fix_guidance"]

    def test_all_standard_kinds_explainable(self):
        """Every ChangeKind can be explained without error."""
        for kind in ChangeKind:
            raw = abi_explain_change(kind.value)
            data = json.loads(raw)
            assert "kind" in data, f"Failed to explain {kind.value}"
            assert data["kind"] == kind.value


# ===================================================================
# 10. abi_dump (with mocked _resolve_input)
# ===================================================================


class TestAbiDump:
    def test_file_not_found(self):
        raw = abi_dump("/nonexistent/path/libfoo.so")
        data = json.loads(raw)
        assert data["status"] == "error"
        assert "not found" in data["error"].lower()

    def test_empty_path_returns_error(self):
        raw = abi_dump("")
        data = json.loads(raw)
        assert data["status"] == "error"

    def test_successful_dump_inline(self, tmp_path: Path):
        snap = _make_snapshot(
            "1.0",
            functions=[_pub_func("foo", "_Z3foov")],
            variables=[_pub_var("bar", "bar", "int")],
        )
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)

        raw = abi_dump(str(snap_path))
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert "summary" in data
        assert data["summary"]["functions"] == 1
        assert data["summary"]["variables"] == 1
        assert "snapshot" in data
        assert isinstance(data["snapshot"], dict)

    def test_successful_dump_with_output_path(self, tmp_path: Path):
        snap = _make_snapshot("1.0", functions=[_pub_func("foo", "_Z3foov")])
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)
        out_path = tmp_path / "output.json"

        raw = abi_dump(str(snap_path), output_path=str(out_path))
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["output_path"] == str(out_path)
        assert out_path.exists()
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert "library" in loaded

    def test_invalid_output_extension(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)

        raw = abi_dump(str(snap_path), output_path=str(tmp_path / "out.txt"))
        data = json.loads(raw)
        assert data["status"] == "error"
        assert ".json" in data["error"]

    def test_version_parameter(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)

        raw = abi_dump(str(snap_path), version="3.14.0")
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_language_parameter(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)

        raw = abi_dump(str(snap_path), language="c")
        data = json.loads(raw)
        assert data["status"] == "ok"

    @pytest.mark.skipif(
        __import__("platform").system() == "Windows",
        reason="POSIX-only sensitive paths",
    )
    def test_system_output_path_blocked(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)

        raw = abi_dump(str(snap_path), output_path="/etc/evil.json")
        data = json.loads(raw)
        assert data["status"] == "error"

    def test_dump_with_headers(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)

        raw = abi_dump(str(snap_path), headers=[])
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_dump_with_include_dirs(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)

        raw = abi_dump(str(snap_path), include_dirs=[])
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_headers_are_passed_as_public_headers(self, tmp_path: Path, monkeypatch):
        """abi_dump documents ``headers`` as "Public header file paths" — it
        must actually classify provenance with them, mirroring the CLI
        ``compare --header`` fix."""
        from abicheck import mcp_server

        snap = _make_snapshot("1.0")
        snap_path = tmp_path / "input.json"
        _write_snapshot(snap_path, snap)
        hdr = tmp_path / "api.h"
        hdr.write_text("int foo(void);\n", encoding="utf-8")

        captured: dict[str, object] = {}
        original_resolve = mcp_server._resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            captured["public_headers"] = kwargs.get("public_headers")
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(mcp_server, "_resolve_input", _spy)

        raw = abi_dump(str(snap_path), headers=[str(hdr)])
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert captured["public_headers"] == [hdr]


# ===================================================================
# 11. abi_compare (with mocked _resolve_input)
# ===================================================================


class TestAbiCompare:
    def _make_pair(self, tmp_path: Path, old_snap: AbiSnapshot, new_snap: AbiSnapshot):
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        _write_snapshot(old_p, old_snap)
        _write_snapshot(new_p, new_snap)
        return old_p, new_p

    def test_file_not_found_old(self, tmp_path: Path):
        new_p = tmp_path / "new.json"
        _write_snapshot(new_p, _make_snapshot("2.0"))
        raw = abi_compare(str(tmp_path / "nonexistent.json"), str(new_p))
        data = json.loads(raw)
        assert "error" in data

    def test_file_not_found_new(self, tmp_path: Path):
        old_p = tmp_path / "old.json"
        _write_snapshot(old_p, _make_snapshot("1.0"))
        raw = abi_compare(str(old_p), str(tmp_path / "nonexistent.json"))
        data = json.loads(raw)
        assert "error" in data

    def test_invalid_policy(self, tmp_path: Path):
        old_p, new_p = self._make_pair(
            tmp_path, _make_snapshot("1.0"), _make_snapshot("2.0")
        )
        raw = abi_compare(str(old_p), str(new_p), policy="not_a_policy")
        data = json.loads(raw)
        assert "error" in data
        assert "Unknown policy" in data["error"]

    def test_invalid_output_format(self, tmp_path: Path):
        old_p, new_p = self._make_pair(
            tmp_path, _make_snapshot("1.0"), _make_snapshot("2.0")
        )
        raw = abi_compare(str(old_p), str(new_p), output_format="xml")
        data = json.loads(raw)
        assert "error" in data
        assert "Unknown output format" in data["error"]

    def test_invalid_show_only(self, tmp_path: Path):
        old_p, new_p = self._make_pair(
            tmp_path, _make_snapshot("1.0"), _make_snapshot("2.0")
        )
        raw = abi_compare(str(old_p), str(new_p), show_only="totally_invalid_token")
        data = json.loads(raw)
        assert "error" in data
        assert "show_only" in data["error"].lower() or "Invalid" in data["error"]

    def test_no_change_verdict(self, tmp_path: Path):
        snap = _make_snapshot("1.0", functions=[_pub_func("f", "_Zf")])
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p))
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["verdict"] == "NO_CHANGE"
        assert data["exit_code"] == 0
        assert data["summary"]["total_changes"] == 0

    def test_breaking_verdict(self, tmp_path: Path):
        old = _make_snapshot("1.0", functions=[_pub_func("init", "_Z4initv", "int")])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(str(old_p), str(new_p))
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["verdict"] == "BREAKING"
        assert data["exit_code"] == 4
        assert data["summary"]["breaking"] > 0

    def test_compatible_verdict(self, tmp_path: Path):
        f = _pub_func("init", "_Z4initv", "int")
        old = _make_snapshot("1.0", functions=[f])
        new = _make_snapshot("2.0", functions=[f, _pub_func("helper", "_Z6helperv")])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(str(old_p), str(new_p))
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["verdict"] == "COMPATIBLE"
        assert data["exit_code"] == 0
        assert data["summary"]["compatible"] > 0

    def test_api_break_exit_code(self, tmp_path: Path):
        old_enum = EnumType(name="Color", members=[EnumMember(name="RED", value=0)])
        new_enum = EnumType(name="Color", members=[EnumMember(name="ROUGE", value=0)])
        old = _make_snapshot("1.0", enums=[old_enum])
        new = _make_snapshot("2.0", enums=[new_enum])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(str(old_p), str(new_p))
        data = json.loads(raw)
        assert data["verdict"] == "API_BREAK"
        assert data["exit_code"] == 2

    def test_changes_have_required_fields(self, tmp_path: Path):
        old = _make_snapshot("1.0", functions=[_pub_func("f", "_Zf")])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(str(old_p), str(new_p))
        data = json.loads(raw)
        for c in data["changes"]:
            assert "kind" in c
            assert "symbol" in c
            assert "description" in c
            assert "impact" in c
            assert "old_value" in c
            assert "new_value" in c
            assert "source_location" in c

    def test_used_by_and_required_symbols_are_mutually_exclusive(
        self, tmp_path: Path
    ):
        old_p, new_p = self._make_pair(
            tmp_path, _make_snapshot("1.0"), _make_snapshot("2.0")
        )
        raw = abi_compare(
            str(old_p), str(new_p),
            used_by=[str(tmp_path / "app")],
            required_symbols=["_Zf"],
        )
        data = json.loads(raw)
        assert data["status"] == "error"
        assert "mutually exclusive" in data["error"]

    def test_required_symbols_scopes_verdict_and_floors_exit_code(
        self, tmp_path: Path
    ):
        # A break in a function no host-required entrypoint touches must not
        # surface in the scoped verdict (ADR-043: compare --required-symbol
        # folds the deleted plugin-check command).
        kept = _pub_func("kept_entry", "_Z10kept_entryv", "int")
        removed = _pub_func("unrelated", "_Z9unrelatedv", "int")
        old = _make_snapshot("1.0", functions=[kept, removed])
        new = _make_snapshot("2.0", functions=[kept])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(
            str(old_p), str(new_p), required_symbols=["_Z10kept_entryv"],
        )
        data = json.loads(raw)
        assert data["status"] == "ok"
        # The full (unscoped) diff is still BREAKING (removed is gone) and is
        # kept as informational context under full_verdict...
        assert data["full_verdict"] == "BREAKING"
        # ...but the required-symbol contract is untouched, so the *primary*
        # verdict/exit_code the caller reads are the scoped ones (floored to
        # COMPATIBLE/0), not the unscoped diff's BREAKING/4 (Codex review: a
        # caller reading only `verdict` must not see BREAKING alongside a
        # scoped-compatible exit_code: 0).
        assert data["verdict"] == "COMPATIBLE"
        contract = data["required_symbol_contract"]
        assert contract["verdict"] == "COMPATIBLE"
        assert contract["missing_entrypoints"] == []
        assert data["exit_code"] == 0
        assert data["exit_code_scheme"] == "scoped"

    def test_multi_app_scoped_verdict_ranked_independently_of_exit_code(
        self, tmp_path: Path, monkeypatch,
    ):
        # Regression: under a severity config, a BREAKING app can carry exit
        # code 0 (info-only preset). Picking the reported scoped verdict by
        # exit code (both apps tie at 0) let a later, merely-COMPATIBLE app
        # overwrite the first BREAKING app's verdict (Codex review).
        from abicheck import mcp_server
        from abicheck.appcompat import AppCompatResult

        old = _make_snapshot("1.0", functions=[_pub_func("f", "_Zf")])
        new = _make_snapshot("2.0", functions=[_pub_func("f", "_Zf")])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app1 = tmp_path / "app1"
        app1.write_bytes(b"\x7fELF" + b"\x00" * 100)
        app2 = tmp_path / "app2"
        app2.write_bytes(b"\x7fELF" + b"\x00" * 100)

        monkeypatch.setattr(
            mcp_server, "_resolve_input", MagicMock(side_effect=[old, new]),
        )
        breaking_scoped = AppCompatResult(
            app_path=str(app1), old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols={"_Zf"}, required_symbol_count=1,
            missing_symbols=["_Zf"], verdict=Verdict.BREAKING,
        )
        compatible_scoped = AppCompatResult(
            app_path=str(app2), old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols=set(), required_symbol_count=0,
            verdict=Verdict.COMPATIBLE,
        )
        import abicheck.appcompat as appcompat_mod
        monkeypatch.setattr(
            appcompat_mod, "scope_diff_to_app",
            MagicMock(side_effect=[breaking_scoped, compatible_scoped]),
        )

        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(app1), str(app2)],
            severity_preset="info-only",
        )
        data = json.loads(raw)
        assert data["exit_code"] == 0  # severity config still floors the gate
        assert data["verdict"] == "BREAKING"  # but the reported verdict is not lost

    def test_required_symbols_scoped_exit_respects_severity_config(
        self, tmp_path: Path
    ):
        # Regression: the scoped exit code used to always use the legacy
        # BREAKING->4/API_BREAK->2 floor even when a severity_* argument was
        # given, silently ignoring it (post-merge PR #566 review). A required
        # entrypoint being removed is still scoped-BREAKING, but
        # severity_preset="info-only" must now floor exit_code at 0.
        removed = _pub_func("entry", "_Z5entryv", "int")
        old = _make_snapshot("1.0", functions=[removed])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(
            str(old_p), str(new_p), required_symbols=["_Z5entryv"],
            severity_preset="info-only",
        )
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["required_symbol_contract"]["verdict"] == "BREAKING"
        assert data["exit_code"] == 0

    def test_required_symbols_never_present_floors_severity_at_4(
        self, tmp_path: Path
    ):
        # Regression (Codex P1): a required symbol absent from *both* old and
        # new is a missing contract with no corresponding diff Change (it was
        # never removed -- it never existed), so `breaking_for_host` is empty
        # even though the scoped verdict is BREAKING. `_scoped_exit_code`
        # used to compute the severity-scheme exit purely from
        # `breaking_for_host`, silently exiting 0.
        kept = _pub_func("kept", "_Z4keptv", "int")
        old = _make_snapshot("1.0", functions=[kept])
        new = _make_snapshot("2.0", functions=[kept])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(
            str(old_p), str(new_p), required_symbols=["never_existed"],
            severity_preset="default",
        )
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["required_symbol_contract"]["verdict"] == "BREAKING"
        assert data["required_symbol_contract"]["missing_entrypoints"] == [
            "never_existed"
        ]
        assert data["exit_code"] == 4

    def test_required_symbols_folds_scoped_verdict_into_nested_report(
        self, tmp_path: Path
    ):
        # The nested response["report"] (output_format="json") must reflect
        # the same scoped verdict as the top-level fields, not the unscoped
        # full-library result (Codex review).
        kept = _pub_func("kept_entry", "_Z10kept_entryv", "int")
        removed = _pub_func("unrelated", "_Z9unrelatedv", "int")
        old = _make_snapshot("1.0", functions=[kept, removed])
        new = _make_snapshot("2.0", functions=[kept])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(
            str(old_p), str(new_p),
            required_symbols=["_Z10kept_entryv"], output_format="json",
        )
        data = json.loads(raw)
        report = data["report"]
        assert report["verdict"] == "COMPATIBLE"
        assert report["full_verdict"] == "BREAKING"
        assert report["required_symbol_contract"]["verdict"] == "COMPATIBLE"

    def test_required_symbols_nested_json_severity_reflects_scoped_gate(
        self, tmp_path: Path
    ):
        # Regression (Codex P2): the nested response["report"]["severity"]
        # block used to always describe the full-library gate decision, even
        # when the contract itself is untouched by the removal -- the top
        # -level exit_code is 0 (scoped-compatible), but report.severity used
        # to still say exit_code: 4 for the unrelated full-library removal.
        kept = _pub_func("kept_entry", "_Z10kept_entryv", "int")
        removed = _pub_func("unrelated", "_Z9unrelatedv", "int")
        old = _make_snapshot("1.0", functions=[kept, removed])
        new = _make_snapshot("2.0", functions=[kept])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(
            str(old_p), str(new_p),
            required_symbols=["_Z10kept_entryv"], output_format="json",
            severity_preset="default",
        )
        data = json.loads(raw)
        assert data["exit_code"] == 0
        report = data["report"]
        assert report["severity"]["exit_code"] == 0
        assert report["severity"]["blocking"] is False
        assert report["severity"]["categories"]["abi_breaking"]["count"] == 0
        assert report["full_severity"]["exit_code"] == 4
        assert report["full_severity"]["blocking"] is True
        assert report["full_severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_required_symbols_missing_entrypoint_is_breaking(self, tmp_path: Path):
        kept = _pub_func("entry", "_Z5entryv", "int")
        old = _make_snapshot("1.0", functions=[kept])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        raw = abi_compare(str(old_p), str(new_p), required_symbols=["_Z5entryv"])
        data = json.loads(raw)
        assert data["status"] == "ok"
        contract = data["required_symbol_contract"]
        assert contract["verdict"] == "BREAKING"
        assert contract["missing_entrypoints"] == ["_Z5entryv"]
        assert data["exit_code"] == 4
        assert data["exit_code_scheme"] == "scoped"

    def _make_binary_pair(self, tmp_path: Path):
        """Real-binary-shaped (ELF-magic) old/new paths for --used-by, which
        requires actual library binaries, not JSON snapshots."""
        old_p = tmp_path / "old.so"
        new_p = tmp_path / "new.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 100)
        return old_p, new_p

    def _patch_used_by(self, monkeypatch, tmp_path: Path, old_snap, new_snap, scoped):
        """Stub snapshot resolution (fake ELF paths aren't real libraries) and
        appcompat.scope_diff_to_app so --used-by is exercised end to end
        without a real compiler/binary."""
        from abicheck import mcp_server

        monkeypatch.setattr(
            mcp_server, "_resolve_input",
            MagicMock(side_effect=[old_snap, new_snap]),
        )
        import abicheck.appcompat as appcompat_mod

        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", lambda *a, **k: scoped)

    def test_used_by_scopes_verdict_and_floors_exit_code(
        self, tmp_path: Path, monkeypatch
    ):
        # A break in a function the app doesn't use must not surface in the
        # scoped verdict (ADR-043: compare --used-by folds appcompat).
        from abicheck.appcompat import AppCompatResult

        kept = _pub_func("kept_entry", "_Z10kept_entryv", "int")
        removed = _pub_func("unrelated", "_Z9unrelatedv", "int")
        old = _make_snapshot("1.0", functions=[kept, removed])
        new = _make_snapshot("2.0", functions=[kept])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)
        scoped = AppCompatResult(
            app_path=str(app), old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols={"_Z10kept_entryv"}, required_symbol_count=1,
            verdict=Verdict.COMPATIBLE,
        )
        self._patch_used_by(monkeypatch, tmp_path, old, new, scoped)

        raw = abi_compare(str(old_p), str(new_p), used_by=[str(app)])
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["full_verdict"] == "BREAKING"
        assert data["verdict"] == "COMPATIBLE"
        assert data["used_by"][0]["app"] == str(app)
        assert data["used_by"][0]["verdict"] == "COMPATIBLE"
        assert data["exit_code"] == 0
        assert data["exit_code_scheme"] == "scoped"

    def test_used_by_missing_symbol_is_breaking(self, tmp_path: Path, monkeypatch):
        from abicheck.appcompat import AppCompatResult

        old = _make_snapshot("1.0", functions=[_pub_func("entry", "_Z5entryv", "int")])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)
        scoped = AppCompatResult(
            app_path=str(app), old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols={"_Z5entryv"}, required_symbol_count=1,
            missing_symbols=["_Z5entryv"], verdict=Verdict.BREAKING,
        )
        self._patch_used_by(monkeypatch, tmp_path, old, new, scoped)

        raw = abi_compare(str(old_p), str(new_p), used_by=[str(app)])
        data = json.loads(raw)
        assert data["used_by"][0]["missing_symbols"] == ["_Z5entryv"]
        assert data["verdict"] == "BREAKING"
        assert data["exit_code"] == 4
        assert data["exit_code_scheme"] == "scoped"

    def test_used_by_missing_symbol_only_floors_severity_at_4(
        self, tmp_path: Path, monkeypatch
    ):
        # Regression (Codex P1): a required symbol absent from both old and
        # new is a missing contract with no corresponding diff Change --
        # `breaking_for_app` stays empty even though the scoped verdict is
        # BREAKING. `_scoped_exit_code` used to compute the severity-scheme
        # exit purely from `breaking_for_app`, silently exiting 0 for an app
        # that can never resolve the required symbol at all.
        from abicheck.appcompat import AppCompatResult

        kept = _pub_func("kept", "_Z4keptv", "int")
        old = _make_snapshot("1.0", functions=[kept])
        new = _make_snapshot("2.0", functions=[kept])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)
        scoped = AppCompatResult(
            app_path=str(app), old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols={"never_existed"}, required_symbol_count=1,
            missing_symbols=["never_existed"], verdict=Verdict.BREAKING,
        )
        self._patch_used_by(monkeypatch, tmp_path, old, new, scoped)

        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(app)],
            severity_preset="default",
        )
        data = json.loads(raw)
        assert data["used_by"][0]["missing_symbols"] == ["never_existed"]
        assert data["verdict"] == "BREAKING"
        assert data["exit_code"] == 4
        report = data["report"]
        assert report["severity"]["exit_code"] == 4
        assert report["severity"]["blocking_categories"] == ["abi_breaking"]
        # The missing symbol itself (not a diff Change) still counts.
        assert report["severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_used_by_missing_symbol_only_summary_reflects_folded_changes(
        self, tmp_path: Path, monkeypatch
    ):
        # Regression (CodeRabbit review): `summary` was computed from
        # result.changes before scoped_missing_labels/scoped_only_changes get
        # folded into the top-level "changes" array, so a run gated purely by
        # a missing required symbol (no backing diff Change) reported
        # total_changes: 0 alongside a BREAKING verdict/exit_code 4.
        from abicheck.appcompat import AppCompatResult

        kept = _pub_func("kept", "_Z4keptv", "int")
        old = _make_snapshot("1.0", functions=[kept])
        new = _make_snapshot("2.0", functions=[kept])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)
        scoped = AppCompatResult(
            app_path=str(app), old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols={"never_existed"}, required_symbol_count=1,
            missing_symbols=["never_existed"], verdict=Verdict.BREAKING,
        )
        self._patch_used_by(monkeypatch, tmp_path, old, new, scoped)

        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(app)],
            severity_preset="default",
        )
        data = json.loads(raw)
        assert data["verdict"] == "BREAKING"
        assert data["exit_code"] == 4
        assert len(data["changes"]) == 1
        assert data["summary"]["total_changes"] == len(data["changes"])
        assert data["summary"]["breaking"] == 1

    def test_used_by_missing_symbol_covered_by_change_not_double_counted(
        self, tmp_path: Path, monkeypatch
    ):
        # Regression (Codex P2 follow-up): "foo" is both a missing symbol
        # (absent from the new exports) *and* the subject of a scoped
        # FUNC_REMOVED Change -- one ABI break, not two.
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker import Change
        from abicheck.checker_policy import ChangeKind

        old = _make_snapshot("1.0", functions=[_pub_func("foo", "foo", "int")])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)
        scoped = AppCompatResult(
            app_path=str(app), old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols={"foo"}, required_symbol_count=1,
            missing_symbols=["foo"],
            breaking_for_app=[
                Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")
            ],
            verdict=Verdict.BREAKING,
        )
        self._patch_used_by(monkeypatch, tmp_path, old, new, scoped)

        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(app)],
            severity_preset="default",
        )
        data = json.loads(raw)
        assert data["exit_code"] == 4
        report = data["report"]
        assert report["severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_used_by_multi_app_shared_change_not_double_counted(
        self, tmp_path: Path, monkeypatch
    ):
        # Regression (Codex P2): when two --used-by apps tie on the worst
        # exit code and both depend on the *same* removed symbol, the shared
        # Change object must be counted once in
        # severity.categories.abi_breaking.count, not once per app.
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker import Change
        from abicheck.checker_policy import ChangeKind

        shared_change = Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")
        old = _make_snapshot("1.0", functions=[_pub_func("foo", "foo", "int")])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app1 = tmp_path / "app1"
        app1.write_bytes(b"\x7fELF" + b"\x00" * 100)
        app2 = tmp_path / "app2"
        app2.write_bytes(b"\x7fELF" + b"\x00" * 100)
        # `_patch_used_by` returns this same object for every app -- both
        # apps therefore share the identical Change instance, exactly the
        # scenario the dedup fix targets.
        scoped = AppCompatResult(
            app_path="/app", old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols={"foo"}, required_symbol_count=1,
            breaking_for_app=[shared_change], verdict=Verdict.BREAKING,
        )
        self._patch_used_by(monkeypatch, tmp_path, old, new, scoped)

        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(app1), str(app2)],
            severity_preset="default",
        )
        data = json.loads(raw)
        assert data["exit_code"] == 4
        report = data["report"]
        assert report["severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_used_by_multi_app_semantically_identical_change_not_double_counted(
        self, tmp_path: Path, monkeypatch
    ):
        # Regression (CLI-audit P2): two apps whose scope_diff_to_app calls
        # each synthesize their OWN fresh Change object for the same
        # underlying finding (exactly what appcompat._check_pe_ordinal_imports
        # does for PE_ORDINAL_RETARGETED -- one new Change per app, not a
        # shared reference into the base diff) must still collapse to one
        # entry. id()-based dedup would double-count these two
        # structurally-identical-but-object-distinct Changes.
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker import Change
        from abicheck.checker_policy import ChangeKind

        old = _make_snapshot("1.0", functions=[_pub_func("foo", "foo", "int")])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app1 = tmp_path / "app1"
        app1.write_bytes(b"\x7fELF" + b"\x00" * 100)
        app2 = tmp_path / "app2"
        app2.write_bytes(b"\x7fELF" + b"\x00" * 100)

        def _scoped_for(*_args, **_kwargs):
            # A fresh, distinct Change object per call -- same semantic
            # identity (kind/symbol/description), different Python id().
            change = Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")
            return AppCompatResult(
                app_path="/app", old_lib_path=str(old_p), new_lib_path=str(new_p),
                required_symbols={"foo"}, required_symbol_count=1,
                breaking_for_app=[change], verdict=Verdict.BREAKING,
            )

        from abicheck import mcp_server

        monkeypatch.setattr(
            mcp_server, "_resolve_input",
            MagicMock(side_effect=[old, new]),
        )
        import abicheck.appcompat as appcompat_mod

        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", _scoped_for)

        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(app1), str(app2)],
            severity_preset="default",
        )
        data = json.loads(raw)
        assert data["exit_code"] == 4
        report = data["report"]
        assert report["severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_used_by_scoped_only_change_included_in_top_level_and_report_changes(
        self, tmp_path: Path, monkeypatch
    ):
        # Regression (Codex review): scope_diff_to_app can synthesize a Change
        # (e.g. PE_ORDINAL_RETARGETED) relevant to the gate but never added to
        # result.changes -- mirrors cli_compare_helpers's identical gap. Both
        # the top-level response["changes"] and the embedded response["report"]
        # (folded via _fold_scoped_compat_into_text) must carry it, not just
        # SARIF/JUnit.
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker import Change
        from abicheck.checker_policy import ChangeKind

        old = _make_snapshot("1.0")
        new = _make_snapshot("2.0")
        old_p, new_p = self._make_binary_pair(tmp_path)
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)

        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5", description="ordinal 5 retargeted",
            old_value="OldFunc", new_value="NewFunc",
        )

        def _scoped_for(*_args, **_kwargs):
            return AppCompatResult(
                app_path="/app", old_lib_path=str(old_p), new_lib_path=str(new_p),
                required_symbols={"foo"}, required_symbol_count=1,
                breaking_for_app=[scoped_only], verdict=Verdict.BREAKING,
            )

        from abicheck import mcp_server

        monkeypatch.setattr(
            mcp_server, "_resolve_input",
            MagicMock(side_effect=[old, new]),
        )
        import abicheck.appcompat as appcompat_mod

        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", _scoped_for)

        raw = abi_compare(str(old_p), str(new_p), used_by=[str(app)])
        data = json.loads(raw)
        assert data["exit_code"] == 4
        top_kinds = [c["kind"] for c in data["changes"]]
        assert "pe_ordinal_retargeted" in top_kinds
        report_kinds = [c["kind"] for c in data["report"]["changes"]]
        assert "pe_ordinal_retargeted" in report_kinds

    def test_used_by_root_cause_markdown_report_does_not_duplicate_scoped_finding(
        self, tmp_path: Path, monkeypatch
    ):
        # Codex review: the embedded response["report"] fold-in
        # (mcp_server.abi_compare -> _fold_scoped_compat_into_text) omitted
        # report_mode, unlike the CLI's identical primary-render call site --
        # with the default "full", the fold always appended its own
        # "Additional scoped-gate findings" appendix even though
        # _to_markdown_root_cause had already placed the same scoped-only
        # finding under "## Root Causes", duplicating it in the rendered text.
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker import Change
        from abicheck.checker_policy import ChangeKind

        old = _make_snapshot("1.0")
        new = _make_snapshot("2.0")
        old_p, new_p = self._make_binary_pair(tmp_path)
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)

        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5", description="ordinal 5 retargeted",
            old_value="OldFunc", new_value="NewFunc",
        )

        def _scoped_for(*_args, **_kwargs):
            return AppCompatResult(
                app_path="/app", old_lib_path=str(old_p), new_lib_path=str(new_p),
                required_symbols={"foo"}, required_symbol_count=1,
                breaking_for_app=[scoped_only], verdict=Verdict.BREAKING,
            )

        from abicheck import mcp_server

        monkeypatch.setattr(
            mcp_server, "_resolve_input",
            MagicMock(side_effect=[old, new]),
        )
        import abicheck.appcompat as appcompat_mod

        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", _scoped_for)

        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(app)],
            output_format="markdown", report_mode="root-cause",
        )
        data = json.loads(raw)
        report = data["report"]
        assert "### `ordinal:5` (1 finding)" in report
        assert "## Additional scoped-gate findings" not in report

    def test_used_by_rejects_non_binary_snapshot_input(self, tmp_path: Path):
        # --used-by needs real library binaries to parse app requirements
        # against; JSON snapshots must be rejected with a clear error rather
        # than silently mis-scoping.
        old_p, new_p = self._make_pair(
            tmp_path, _make_snapshot("1.0"), _make_snapshot("2.0")
        )
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)
        raw = abi_compare(str(old_p), str(new_p), used_by=[str(app)])
        data = json.loads(raw)
        assert data["status"] == "error"
        assert "real library binaries" in data["error"]

    def test_used_by_app_not_found(self, tmp_path: Path, monkeypatch):
        from abicheck import mcp_server

        old = _make_snapshot("1.0")
        new = _make_snapshot("2.0")
        old_p, new_p = self._make_binary_pair(tmp_path)
        monkeypatch.setattr(
            mcp_server, "_resolve_input",
            MagicMock(side_effect=[old, new]),
        )
        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(tmp_path / "no_such_app")],
        )
        data = json.loads(raw)
        assert data["status"] == "error"
        assert "not found" in data["error"]

    def test_used_by_folds_scoped_verdict_into_nested_report(
        self, tmp_path: Path, monkeypatch
    ):
        from abicheck.appcompat import AppCompatResult

        kept = _pub_func("kept_entry", "_Z10kept_entryv", "int")
        removed = _pub_func("unrelated", "_Z9unrelatedv", "int")
        old = _make_snapshot("1.0", functions=[kept, removed])
        new = _make_snapshot("2.0", functions=[kept])
        old_p, new_p = self._make_binary_pair(tmp_path)
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 100)
        scoped = AppCompatResult(
            app_path=str(app), old_lib_path=str(old_p), new_lib_path=str(new_p),
            required_symbols={"_Z10kept_entryv"}, required_symbol_count=1,
            verdict=Verdict.COMPATIBLE,
        )
        self._patch_used_by(monkeypatch, tmp_path, old, new, scoped)

        raw = abi_compare(
            str(old_p), str(new_p), used_by=[str(app)], output_format="json",
        )
        data = json.loads(raw)
        report = data["report"]
        assert report["verdict"] == "COMPATIBLE"
        assert report["full_verdict"] == "BREAKING"
        assert report["used_by"][0]["verdict"] == "COMPATIBLE"

    def test_json_report_embedded_as_object(self, tmp_path: Path):
        snap = _make_snapshot("1.0", functions=[_pub_func("f", "_Zf")])
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p), output_format="json")
        data = json.loads(raw)
        assert isinstance(data["report"], dict)

    def test_markdown_report_is_string(self, tmp_path: Path):
        snap = _make_snapshot("1.0", functions=[_pub_func("f", "_Zf")])
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p), output_format="markdown")
        data = json.loads(raw)
        assert isinstance(data["report"], str)

    def test_sarif_format(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p), output_format="sarif")
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_html_format(self, tmp_path: Path):
        old = _make_snapshot("1.0", functions=[_pub_func("f", "_Zf")])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_pair(tmp_path, old, new)
        raw = abi_compare(str(old_p), str(new_p), output_format="html")
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert (
            "<html" in data["report"].lower() or "<!doctype" in data["report"].lower()
        )

    def test_stat_parameter(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p), stat=True)
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_show_impact_parameter(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p), show_impact=True)
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_suppressed_count_present(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p))
        data = json.loads(raw)
        assert "suppressed_count" in data

    def test_valid_policies_accepted(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        for policy in ("strict_abi", "sdk_vendor", "plugin_abi"):
            raw = abi_compare(str(old_p), str(new_p), policy=policy)
            data = json.loads(raw)
            assert data["status"] == "ok", f"policy={policy} failed"

    def test_impact_respects_policy(self, tmp_path: Path):
        old_enum = EnumType(name="Color", members=[EnumMember(name="RED", value=0)])
        new_enum = EnumType(name="Color", members=[EnumMember(name="ROUGE", value=0)])
        old = _make_snapshot("1.0", enums=[old_enum])
        new = _make_snapshot("2.0", enums=[new_enum])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        # strict_abi: enum_member_renamed is api_break
        raw = abi_compare(str(old_p), str(new_p), policy="strict_abi")
        data = json.loads(raw)
        rename_changes = [
            c for c in data["changes"] if c["kind"] == "enum_member_renamed"
        ]
        assert rename_changes
        assert rename_changes[0]["impact"] == "api_break"

        # sdk_vendor: downgraded to compatible
        raw = abi_compare(str(old_p), str(new_p), policy="sdk_vendor")
        data = json.loads(raw)
        rename_changes = [
            c for c in data["changes"] if c["kind"] == "enum_member_renamed"
        ]
        assert rename_changes
        assert rename_changes[0]["impact"] == "compatible"

    def test_policy_file_parameter(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides: {}\n", encoding="utf-8")
        raw = abi_compare(str(old_p), str(new_p), policy_file=str(pf))
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_suppression_file_parameter(self, tmp_path: Path):
        old = _make_snapshot("1.0", functions=[_pub_func("init", "_Z4initv")])
        new = _make_snapshot("2.0", functions=[])
        old_p, new_p = self._make_pair(tmp_path, old, new)

        sup = tmp_path / "suppressions.yaml"
        sup.write_text(
            "version: 1\n"
            "suppressions:\n"
            "  - symbol: _Z4initv\n"
            "    change_kind: func_removed\n"
            "    reason: intentional removal\n",
            encoding="utf-8",
        )
        raw = abi_compare(str(old_p), str(new_p), suppression_file=str(sup))
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["suppressed_count"] >= 1

    def test_empty_path_returns_error(self):
        raw = abi_compare("", "/some/path.json")
        data = json.loads(raw)
        assert data["status"] == "error"

    def test_headers_parameter(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p), headers=[])
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_old_headers_overrides_shared(self, tmp_path: Path, monkeypatch):
        """When old_headers=[] is given, it overrides shared headers."""
        from abicheck import mcp_server

        captured: list[tuple[str, list]] = []
        original_resolve = mcp_server._resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            captured.append((str(path), list(headers)))
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(mcp_server, "_resolve_input", _spy)

        shared_hdr = tmp_path / "shared.h"
        shared_hdr.write_text("// shared\n", encoding="utf-8")

        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)

        raw = abi_compare(
            str(old_p),
            str(new_p),
            headers=[str(shared_hdr)],
            old_headers=[],
            new_headers=[],
        )
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert len(captured) == 2
        for _path, hdrs in captured:
            assert hdrs == []

    def test_headers_are_passed_as_public_headers(self, tmp_path: Path, monkeypatch):
        """abi_compare's ``old_headers``/``new_headers`` are documented as
        header files just like the CLI's ``compare --header`` — both sides
        must be threaded through as the public-header set for provenance."""
        from abicheck import mcp_server

        captured: list[tuple[str, object]] = []
        original_resolve = mcp_server._resolve_input

        def _spy(path, headers, includes, version, lang, **kwargs):
            captured.append((version, kwargs.get("public_headers")))
            return original_resolve(path, headers, includes, version, lang, **kwargs)

        monkeypatch.setattr(mcp_server, "_resolve_input", _spy)

        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        old_hdr = tmp_path / "old.h"
        old_hdr.write_text("int foo(void);\n", encoding="utf-8")
        new_hdr = tmp_path / "new.h"
        new_hdr.write_text("int foo(void);\n", encoding="utf-8")

        raw = abi_compare(
            str(old_p),
            str(new_p),
            old_headers=[str(old_hdr)],
            new_headers=[str(new_hdr)],
        )
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert dict(captured)["old"] == [old_hdr]
        assert dict(captured)["new"] == [new_hdr]

    def test_report_mode_leaf(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        raw = abi_compare(str(old_p), str(new_p), report_mode="leaf")
        data = json.loads(raw)
        assert data["status"] == "ok"

    def test_policy_file_skips_base_policy_validation(self, tmp_path: Path):
        """When policy_file is provided, base policy name is not validated."""
        snap = _make_snapshot("1.0")
        old_p, new_p = self._make_pair(tmp_path, snap, snap)
        pf = tmp_path / "policy.yaml"
        pf.write_text("base_policy: strict_abi\noverrides: {}\n", encoding="utf-8")
        # Even with an invalid base policy name, policy_file takes precedence
        raw = abi_compare(
            str(old_p), str(new_p), policy="totally_invalid", policy_file=str(pf)
        )
        data = json.loads(raw)
        assert data["status"] == "ok"


# ===================================================================
# _resolve_input
# ===================================================================


class TestResolveInput:
    def test_json_snapshot(self, tmp_path: Path):
        snap = _make_snapshot("1.0", functions=[_pub_func("foo", "_Z3foov")])
        p = tmp_path / "snap.json"
        _write_snapshot(p, snap)
        result = _resolve_input(p, [], [], "1.0", "c++")
        assert result.library == "libtest.so.1"
        assert len(result.functions) == 1

    def test_unknown_format_raises(self, tmp_path: Path):
        f = tmp_path / "unknown.xyz"
        f.write_text("not any recognized format at all", encoding="utf-8")
        with pytest.raises(AbicheckError, match="Cannot detect format"):
            _resolve_input(f, [], [], "1.0", "c++")

    def test_unreadable_file_raises(self, tmp_path: Path):
        f = tmp_path / "unreadable.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        # The file is readable, but not recognized as any format
        with pytest.raises(AbicheckError, match="Cannot detect format"):
            _resolve_input(f, [], [], "1.0", "c++")

    def test_static_archive_raises_with_guidance(self, tmp_path: Path):
        """A `.a`/`.lib` archive is rejected with the static-archive guidance
        through the MCP resolver too (G8), not the generic 'Cannot detect input
        format' fallback."""
        f = tmp_path / "libfoo.a"
        f.write_bytes(b"!<arch>\n" + b"\x00" * 16)
        with pytest.raises(AbicheckError, match="static/import library archive"):
            _resolve_input(f, [], [], "1.0", "c++")


# ===================================================================
# main()
# ===================================================================


class TestMain:
    def test_main_calls_mcp_run(self, monkeypatch):
        from abicheck import mcp_server

        calls = []
        monkeypatch.setattr(
            mcp_server.mcp, "run", lambda transport: calls.append(transport)
        )
        monkeypatch.setattr("sys.argv", ["abicheck-mcp"])
        main()
        assert calls == ["stdio"]


# ===================================================================
# Edge cases and additional coverage
# ===================================================================


class TestAbiDumpEdgeCases:
    def test_dump_empty_snapshot(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        snap_path = tmp_path / "empty.json"
        _write_snapshot(snap_path, snap)
        raw = abi_dump(str(snap_path))
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["summary"]["functions"] == 0
        assert data["summary"]["variables"] == 0

    def test_dump_whitespace_path(self):
        raw = abi_dump("   ")
        data = json.loads(raw)
        assert data["status"] == "error"


class TestAbiCompareEdgeCases:
    def test_compare_same_file(self, tmp_path: Path):
        snap = _make_snapshot("1.0", functions=[_pub_func("f", "_Zf")])
        p = tmp_path / "snap.json"
        _write_snapshot(p, snap)
        raw = abi_compare(str(p), str(p))
        data = json.loads(raw)
        assert data["verdict"] == "NO_CHANGE"

    def test_compare_stat_json(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        _write_snapshot(old_p, snap)
        _write_snapshot(new_p, snap)
        raw = abi_compare(str(old_p), str(new_p), stat=True, output_format="json")
        data = json.loads(raw)
        assert data["status"] == "ok"
        # report is embedded as object since format=json
        assert isinstance(data["report"], dict)

    def test_compare_stat_markdown(self, tmp_path: Path):
        snap = _make_snapshot("1.0")
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        _write_snapshot(old_p, snap)
        _write_snapshot(new_p, snap)
        raw = abi_compare(str(old_p), str(new_p), stat=True, output_format="markdown")
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert isinstance(data["report"], str)


class TestAbiCompareSeverity:
    """Without any severity_* argument, exit_code follows the legacy
    verdict-based scheme — which can misreport the actual gate once severity
    configuration is in play (the MCP surface previously had no severity
    configuration at all). These guard the fix."""

    def _make_pair(self, tmp_path: Path, old_snap: AbiSnapshot, new_snap: AbiSnapshot):
        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        _write_snapshot(old_p, old_snap)
        _write_snapshot(new_p, new_snap)
        return old_p, new_p

    def test_no_severity_args_uses_legacy_scheme(self, tmp_path: Path):
        old_p, new_p = self._make_pair(
            tmp_path,
            _make_snapshot("1.0"),
            _make_snapshot("2.0", functions=[_pub_func("bar", "_Z3barv")]),
        )
        raw = abi_compare(str(old_p), str(new_p))
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["verdict"] == "COMPATIBLE"
        assert data["exit_code"] == 0
        assert data["exit_code_scheme"] == "legacy"

    def test_addition_configured_as_error_switches_to_severity_scheme(
        self, tmp_path: Path,
    ):
        old_p, new_p = self._make_pair(
            tmp_path,
            _make_snapshot("1.0"),
            _make_snapshot("2.0", functions=[_pub_func("bar", "_Z3barv")]),
        )
        raw = abi_compare(
            str(old_p), str(new_p),
            severity_preset="default", severity_addition="error",
        )
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["verdict"] == "COMPATIBLE"
        # Legacy scheme would say exit_code=0; the severity gate says 1.
        assert data["exit_code"] == 1
        assert data["exit_code_scheme"] == "severity"

    def test_breaking_demoted_to_info_exits_zero(self, tmp_path: Path):
        old_p, new_p = self._make_pair(
            tmp_path,
            _make_snapshot("1.0", functions=[_pub_func("foo", "_Z3foov")]),
            _make_snapshot("2.0"),
        )
        raw = abi_compare(str(old_p), str(new_p), severity_abi_breaking="info")
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert data["verdict"] == "BREAKING"
        # Legacy scheme would say exit_code=4; the severity gate says 0.
        assert data["exit_code"] == 0
        assert data["exit_code_scheme"] == "severity"

    def test_invalid_severity_preset_returns_error(self, tmp_path: Path):
        old_p, new_p = self._make_pair(
            tmp_path, _make_snapshot("1.0"), _make_snapshot("2.0"),
        )
        raw = abi_compare(str(old_p), str(new_p), severity_preset="not_a_preset")
        data = json.loads(raw)
        assert "error" in data

    def test_empty_string_severity_arg_is_validated_not_ignored(self, tmp_path: Path):
        """CodeRabbit review: `any((severity_preset, ...))` treated an empty
        string as absent (falsy), silently selecting the legacy scheme
        instead of surfacing the documented validation error."""
        old_p, new_p = self._make_pair(
            tmp_path, _make_snapshot("1.0"), _make_snapshot("2.0"),
        )
        raw = abi_compare(str(old_p), str(new_p), severity_abi_breaking="")
        data = json.loads(raw)
        assert "error" in data

    def test_json_report_embeds_severity_block(self, tmp_path: Path):
        old_p, new_p = self._make_pair(
            tmp_path,
            _make_snapshot("1.0"),
            _make_snapshot("2.0", functions=[_pub_func("bar", "_Z3barv")]),
        )
        raw = abi_compare(
            str(old_p), str(new_p),
            severity_addition="error", output_format="json",
        )
        data = json.loads(raw)
        assert "severity" in data["report"]
        assert data["report"]["severity"]["exit_code"] == 1

    def test_leaf_mode_json_report_embeds_severity_block(self, tmp_path: Path):
        """CodeRabbit review: report_mode="leaf" returned before the severity
        block was built, so the MCP JSON report silently had no `severity`
        key even though `exit_code`/`exit_code_scheme` were already
        severity-gated — a direct contradiction on the same response."""
        old_p, new_p = self._make_pair(
            tmp_path,
            _make_snapshot("1.0"),
            _make_snapshot("2.0", functions=[_pub_func("bar", "_Z3barv")]),
        )
        raw = abi_compare(
            str(old_p), str(new_p),
            severity_addition="error", output_format="json", report_mode="leaf",
        )
        data = json.loads(raw)
        assert data["exit_code_scheme"] == "severity"
        assert "severity" in data["report"]
        assert data["report"]["severity"]["exit_code"] == 1

    def test_stat_output_reflects_severity_gate(self, tmp_path: Path):
        old_p, new_p = self._make_pair(
            tmp_path,
            _make_snapshot("1.0"),
            _make_snapshot("2.0", functions=[_pub_func("bar", "_Z3barv")]),
        )
        raw = abi_compare(
            str(old_p), str(new_p),
            severity_addition="error", stat=True, output_format="markdown",
        )
        data = json.loads(raw)
        assert "gate: FAIL" in data["report"]


class TestSanitizeErrorEdgeCases:
    def test_nested_abicheck_error(self):
        """Subclasses of AbicheckError also pass through."""
        from abicheck.errors import ValidationError

        exc = ValidationError("custom validation fail")
        assert _sanitize_error(exc) == "custom validation fail"

    def test_key_error_format(self):
        exc = KeyError("missing")
        result = _sanitize_error(exc)
        # KeyError repr includes quotes
        assert "missing" in result

    def test_permission_error_is_os_error(self):
        """PermissionError inherits from OSError so gets generic message."""
        exc = PermissionError("nope")
        result = _sanitize_error(exc, context="write")
        assert "file system error" in result
        assert "nope" not in result

    def test_is_a_directory_error(self):
        """IsADirectoryError inherits from OSError."""
        exc = IsADirectoryError("it's a dir")
        result = _sanitize_error(exc, context="open")
        assert "file system error" in result


class TestDetectBinaryFormatEdgeCases:
    def test_short_file_1_byte(self, tmp_path: Path):
        f = tmp_path / "tiny.bin"
        f.write_bytes(b"\x7f")
        assert _detect_binary_format(f) is None

    def test_exactly_4_bytes_unknown(self, tmp_path: Path):
        f = tmp_path / "four.bin"
        f.write_bytes(b"ABCD")
        assert _detect_binary_format(f) is None

    def test_directory_returns_none(self, tmp_path: Path):
        # Opening a directory for reading should raise OSError
        assert _detect_binary_format(tmp_path) is None


class TestSafeWritePathTraversalEdgeCases:
    @pytest.mark.skipif(
        __import__("platform").system() == "Windows",
        reason="POSIX-only test",
    )
    def test_traversal_into_etc(self):
        """Path traversal with .. into /etc should be blocked."""
        # Use an absolute path that resolves to /etc/foo.json via traversal
        with pytest.raises(ValueError, match="sensitive system path"):
            _safe_write_path("/tmp/../etc/foo.json")  # noqa: S108  # nosec B108

    @pytest.mark.skipif(
        __import__("platform").system() == "Windows",
        reason="POSIX-only test",
    )
    def test_double_slash_etc(self):
        with pytest.raises(ValueError, match="sensitive system path"):
            _safe_write_path("//etc/foo.json")


# ---------------------------------------------------------------------------
# abi_scan: --public-header-dir provenance wiring (A1)
# ---------------------------------------------------------------------------


class TestAbiScanPublicHeaderDir:
    def _snap_file(self, tmp_path: Path) -> Path:
        p = tmp_path / "lib.abi.json"
        p.write_text(snapshot_to_json(_make_snapshot("1.0")), encoding="utf-8")
        return p

    def test_nonexistent_public_header_dir_is_error(self, tmp_path: Path):
        # A public-header boundary must be a real directory (matches the CLI's
        # exists=True, file_okay=False) — else provenance would mis-classify.
        snap = self._snap_file(tmp_path)
        raw = abi_scan(
            str(snap),
            public_header_dirs=[str(tmp_path / "missing")],
        )
        data = json.loads(raw)
        assert data["status"] == "error"
        assert "public_header_dir must be an existing directory" in data["error"]

    def test_file_as_public_header_dir_is_error(self, tmp_path: Path):
        snap = self._snap_file(tmp_path)
        not_a_dir = tmp_path / "all.hpp"
        not_a_dir.write_text("// umbrella\n", encoding="utf-8")
        raw = abi_scan(str(snap), public_header_dirs=[str(not_a_dir)])
        data = json.loads(raw)
        assert data["status"] == "error"
        assert "must be an existing directory" in data["error"]

    def test_public_header_dir_forwarded_to_request(self, tmp_path: Path, monkeypatch):
        # A valid directory is resolved and threaded onto ScanRequest so the typed
        # engine classifies provenance (unlocking the leakage/RTTI cross-checks).
        from abicheck import service

        snap = self._snap_file(tmp_path)
        pub = tmp_path / "pub"
        pub.mkdir()

        captured: dict[str, object] = {}

        def _fake_subprocess(req, timeout):
            captured["public_header_dirs"] = list(req.public_header_dirs)
            return {"verdict": "COMPATIBLE", "exit_code": 0}

        monkeypatch.setattr(service, "run_scan_subprocess", _fake_subprocess)
        raw = abi_scan(str(snap), public_header_dirs=[str(pub)])
        data = json.loads(raw)
        assert data["status"] == "ok"
        assert captured["public_header_dirs"] == [pub.resolve()]
