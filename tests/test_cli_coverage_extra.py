"""Extra CLI coverage tests for uncovered command paths.

Targets uncovered lines in cli.py: PE/Mach-O dump paths, show-only validation,
stat output, compare error display, check-compat extraction, batch comparison
error recovery, and stack-check command.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from abicheck.cli import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pe_bytes() -> bytes:
    """Create minimal PE bytes (MZ header + PE signature + COFF header)."""
    dos_header = bytearray(64)
    dos_header[0:2] = b"MZ"
    pe_offset = 64
    struct.pack_into("<I", dos_header, 0x3C, pe_offset)
    pe_sig = b"PE\x00\x00"
    coff_header = struct.pack(
        "<HHIIIHH",
        0x8664,  # Machine (x86_64)
        0,       # NumberOfSections
        0,       # TimeDateStamp
        0,       # PointerToSymbolTable
        0,       # NumberOfSymbols
        0,       # SizeOfOptionalHeader
        0x2000,  # Characteristics (DLL)
    )
    return bytes(dos_header) + pe_sig + coff_header


def _make_json_snapshot(path: Path, *, name: str = "lib.so", version: str = "1.0") -> Path:
    """Write a minimal valid JSON snapshot file."""
    from abicheck.model import AbiSnapshot
    from abicheck.serialization import snapshot_to_json

    snap = AbiSnapshot(library=name, version=version, functions=[], platform="elf")
    out = path / f"{name}_{version}.json"
    out.write_text(snapshot_to_json(snap), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------
# PE / Mach-O dump path (lines 496-513)
# ---------------------------------------------------------------------------


class TestDumpNativeBinary:
    """Test dump command with PE and Mach-O binaries."""

    def test_dump_pe_follow_deps_warning(self, tmp_path: Path) -> None:
        """--follow-deps on PE emits warning and still produces output."""
        from abicheck.model import AbiSnapshot, Function, Visibility

        pe_file = tmp_path / "test.dll"
        pe_file.write_bytes(_make_pe_bytes())

        mock_snap = AbiSnapshot(
            library="test.dll", version="1.0",
            functions=[
                Function(name="TestFunc", mangled="TestFunc", return_type="?",
                         visibility=Visibility.PUBLIC, is_extern_c=True),
            ],
            platform="pe",
        )

        with patch("abicheck.cli._detect_binary_format", return_value="pe"), \
             patch("abicheck.cli._dump_native_binary", return_value=mock_snap):
            runner = CliRunner()
            result = runner.invoke(main, [
                "dump", str(pe_file), "--version", "1.0", "--follow-deps",
            ])
            assert result.exit_code == 0
            # Verify the warning about --follow-deps on PE was emitted
            combined = (result.output + (result.stderr if hasattr(result, 'stderr') else "")).lower()
            assert "follow-deps" in combined, (
                "--follow-deps on PE should emit a warning"
            )

    def test_dump_pe_to_file(self, tmp_path: Path) -> None:
        """PE dump with --output writes JSON file."""
        pe_file = tmp_path / "test.dll"
        pe_file.write_bytes(_make_pe_bytes())
        out_file = tmp_path / "dump.json"

        # Create a proper AbiSnapshot to return from _dump_native_binary
        from abicheck.model import AbiSnapshot, Function, Visibility

        mock_snap = AbiSnapshot(
            library="test.dll", version="1.0",
            functions=[
                Function(name="MyFunc", mangled="MyFunc", return_type="?",
                         visibility=Visibility.PUBLIC, is_extern_c=True),
            ],
            platform="pe",
        )

        with patch("abicheck.cli._detect_binary_format", return_value="pe"), \
             patch("abicheck.cli._dump_native_binary", return_value=mock_snap):
            runner = CliRunner()
            result = runner.invoke(main, [
                "dump", str(pe_file), "--version", "1.0",
                "-o", str(out_file),
            ])
            assert result.exit_code == 0
            assert out_file.exists()
            data = json.loads(out_file.read_text(encoding="utf-8"))
            assert "functions" in data


# ---------------------------------------------------------------------------
# Show-only validation (lines 638-643)
# ---------------------------------------------------------------------------


class TestShowOnlyValidation:
    """Test --show-only parameter validation."""

    def test_invalid_show_only_token(self, tmp_path: Path) -> None:
        """Invalid --show-only token produces error with descriptive message."""
        old = _make_json_snapshot(tmp_path, name="libold", version="1.0")
        new = _make_json_snapshot(tmp_path, name="libnew", version="2.0")

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old), str(new),
            "--show-only", "invalid_token_xyz",
        ])
        assert result.exit_code != 0
        assert "Unknown --show-only token" in result.output or "Invalid value" in result.output


# ---------------------------------------------------------------------------
# Stat output (lines 660-662)
# ---------------------------------------------------------------------------


class TestStatOutput:
    """Test --stat flag for one-line summary output."""

    def test_stat_json_output(self, tmp_path: Path) -> None:
        """--stat with --format json produces JSON stat output."""
        old = _make_json_snapshot(tmp_path, name="libold", version="1.0")
        new = _make_json_snapshot(tmp_path, name="libnew", version="2.0")

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old), str(new), "--stat", "--format", "json",
        ])
        assert result.exit_code == 0
        # Read stdout (not .output) so any stderr warning — e.g. the
        # public-surface scoping fallback — does not corrupt the JSON payload.
        data = json.loads(result.stdout)
        assert "verdict" in data

    def test_stat_text_output(self, tmp_path: Path) -> None:
        """--stat produces one-line summary containing verdict."""
        old = _make_json_snapshot(tmp_path, name="libold", version="1.0")
        new = _make_json_snapshot(tmp_path, name="libnew", version="2.0")

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old), str(new), "--stat",
        ])
        assert result.exit_code == 0
        output = result.output.strip()
        assert output  # non-empty
        assert "NO_CHANGE" in output  # verdict should appear in stat output


# ---------------------------------------------------------------------------
# Render output formats (lines 660-698)
# ---------------------------------------------------------------------------


class TestRenderOutputFormats:
    """Test all output format rendering through compare command."""

    @pytest.fixture
    def snapshot_pair(self, tmp_path: Path) -> tuple[Path, Path]:
        old = _make_json_snapshot(tmp_path, name="libtest", version="1.0")
        new = _make_json_snapshot(tmp_path, name="libtest", version="2.0")
        return old, new

    def test_sarif_output(self, snapshot_pair: tuple[Path, Path]) -> None:
        old, new = snapshot_pair
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old), str(new), "--format", "sarif",
        ])
        assert result.exit_code == 0
        # Read stdout (not .output) so any stderr warning — e.g. the
        # public-surface scoping fallback — does not corrupt the SARIF payload.
        data = json.loads(result.stdout)
        assert "$schema" in data

    # test_html_output: covered by test_cli_unit.py::TestCompareHtml
    # test_json_output: covered by test_cli_unit.py::TestCompareJson

    def test_show_impact_flag(self, tmp_path: Path) -> None:
        """--show-impact includes impact summary when type changes have affected symbols."""
        from abicheck.model import (
            AbiSnapshot,
            Function,
            Param,
            RecordType,
            TypeField,
            Visibility,
        )
        from abicheck.serialization import snapshot_to_json

        rec = RecordType(name="Pt", kind="struct", size_bits=32, fields=[
            TypeField(name="x", type="int", offset_bits=0),
        ])
        rec_v2 = RecordType(name="Pt", kind="struct", size_bits=64, fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])
        func = Function(name="draw", mangled="_Z4draw2Pt", return_type="void",
                        params=[Param(name="p", type="Pt")], visibility=Visibility.PUBLIC)

        old_snap = AbiSnapshot(library="lib.so", version="1.0", functions=[func], types=[rec])
        new_snap = AbiSnapshot(library="lib.so", version="2.0", functions=[func], types=[rec_v2])

        old_f = tmp_path / "old.json"
        new_f = tmp_path / "new.json"
        old_f.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_f.write_text(snapshot_to_json(new_snap), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_f), str(new_f), "--show-impact"])
        assert result.exit_code == 4  # breaking (struct size changed)
        assert "Impact Summary" in result.output or "impact" in result.output.lower()

    def test_leaf_report_mode(self, tmp_path: Path) -> None:
        """--report-mode leaf produces leaf-change view with type root changes."""
        from abicheck.model import (
            AbiSnapshot,
            Function,
            Param,
            RecordType,
            TypeField,
            Visibility,
        )
        from abicheck.serialization import snapshot_to_json

        rec = RecordType(name="Cfg", kind="struct", size_bits=32, fields=[
            TypeField(name="x", type="int", offset_bits=0),
        ])
        rec_v2 = RecordType(name="Cfg", kind="struct", size_bits=64, fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])
        func = Function(name="init", mangled="_Z4init3Cfg", return_type="void",
                        params=[Param(name="c", type="Cfg")], visibility=Visibility.PUBLIC)

        old_snap = AbiSnapshot(library="lib.so", version="1.0", functions=[func], types=[rec])
        new_snap = AbiSnapshot(library="lib.so", version="2.0", functions=[func], types=[rec_v2])

        old_f = tmp_path / "old.json"
        new_f = tmp_path / "new.json"
        old_f.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_f.write_text(snapshot_to_json(new_snap), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_f), str(new_f), "--report-mode", "leaf"])
        assert result.exit_code == 4
        assert "leaf-change view" in result.output or "Cfg" in result.output


# ---------------------------------------------------------------------------
# Compare error display (lines 732-753)
# ---------------------------------------------------------------------------


class TestCompareErrorDisplay:
    """Test compare command error handling paths."""

    def test_nonexistent_old_input(self) -> None:
        """Non-existent old input produces clean error with path message."""
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", "/nonexistent/old.so", "/nonexistent/new.so",
        ])
        assert result.exit_code != 0
        assert "does not exist" in result.output or "Invalid value" in result.output

    def test_nonexistent_new_input(self, tmp_path: Path) -> None:
        """Non-existent new input produces clean error with path message."""
        old = _make_json_snapshot(tmp_path, name="libold", version="1.0")
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old), "/nonexistent/new.so",
        ])
        assert result.exit_code != 0
        assert "does not exist" in result.output or "Invalid value" in result.output


# ---------------------------------------------------------------------------
# Show-redundant flag (lines 1078-1085)
# ---------------------------------------------------------------------------


class TestShowRedundant:
    """Test --show-redundant flag for merging redundant changes."""

    def test_show_redundant_flag(self, tmp_path: Path) -> None:
        """--show-redundant merges redundant changes back into main list."""
        from abicheck.model import (
            AbiSnapshot,
            Function,
            Param,
            RecordType,
            TypeField,
            Visibility,
        )
        from abicheck.serialization import snapshot_to_json

        # A struct that changes size between versions
        rec = RecordType(name="Cfg", kind="struct", size_bits=32, fields=[
            TypeField(name="x", type="int", offset_bits=0),
        ])
        rec_v2 = RecordType(name="Cfg", kind="struct", size_bits=64, fields=[
            TypeField(name="x", type="int", offset_bits=0),
            TypeField(name="y", type="int", offset_bits=32),
        ])
        # Function whose param type references Cfg — changing from Cfg* to Cfg
        # produces FUNC_PARAMS_CHANGED which the redundancy filter hides because
        # it is caused by the root TYPE_SIZE_CHANGED on Cfg.
        func_old = Function(name="init", mangled="_Z4initP3Cfg", return_type="void",
                            params=[Param(name="c", type="Cfg*")],
                            visibility=Visibility.PUBLIC)
        func_new = Function(name="init", mangled="_Z4initP3Cfg", return_type="void",
                            params=[Param(name="c", type="Cfg")],
                            visibility=Visibility.PUBLIC)

        old_snap = AbiSnapshot(library="libtest.so", version="1.0",
                               functions=[func_old], types=[rec])
        new_snap = AbiSnapshot(library="libtest.so", version="2.0",
                               functions=[func_new], types=[rec_v2])

        old_file = tmp_path / "old.json"
        new_file = tmp_path / "new.json"
        old_file.write_text(snapshot_to_json(old_snap), encoding="utf-8")
        new_file.write_text(snapshot_to_json(new_snap), encoding="utf-8")

        # Without --show-redundant the derived FUNC_PARAMS_CHANGED is hidden
        runner = CliRunner()
        hidden = runner.invoke(main, ["compare", str(old_file), str(new_file)])
        assert hidden.exit_code == 4

        # With --show-redundant the hidden derived change is restored
        result = runner.invoke(main, [
            "compare", str(old_file), str(new_file), "--show-redundant",
        ])
        assert result.exit_code == 4
        assert "init" in result.output  # derived change now visible


# ---------------------------------------------------------------------------
# Stack-check command (lines 1625-1648)
# ---------------------------------------------------------------------------


class TestStackCheckCommand:
    """Test the stack-check CLI command."""

    def test_stack_check_nonexistent_dirs(self) -> None:
        """stack-check with non-existent directories produces descriptive error."""
        runner = CliRunner()
        result = runner.invoke(main, [
            "stack-check", "usr/bin/test",
            "--baseline", "/nonexistent/baseline",
            "--candidate", "/nonexistent/candidate",
        ])
        assert result.exit_code != 0
        assert "does not exist" in result.output or "Invalid value" in result.output

    def test_stack_check_with_mock(self, tmp_path: Path) -> None:
        """stack-check with mocked resolver produces proper output."""
        from abicheck.resolver import DependencyGraph
        from abicheck.stack_checker import StackCheckResult, StackVerdict

        baseline = tmp_path / "baseline"
        candidate = tmp_path / "candidate"
        baseline.mkdir()
        candidate.mkdir()

        mock_result = StackCheckResult(
            root_binary="usr/bin/test",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
            loadability=StackVerdict.PASS,
            abi_risk=StackVerdict.PASS,
            baseline_graph=DependencyGraph(root="test"),
            candidate_graph=DependencyGraph(root="test"),
        )

        with patch("abicheck.stack_checker.check_stack", return_value=mock_result):
            runner = CliRunner()
            result = runner.invoke(main, [
                "stack-check", "usr/bin/test",
                "--baseline", str(baseline),
                "--candidate", str(candidate),
            ])
            assert result.exit_code == 0

    def test_stack_check_json_format(self, tmp_path: Path) -> None:
        """stack-check with --format json produces JSON output."""
        from abicheck.resolver import DependencyGraph
        from abicheck.stack_checker import StackCheckResult, StackVerdict

        baseline = tmp_path / "baseline"
        candidate = tmp_path / "candidate"
        baseline.mkdir()
        candidate.mkdir()

        mock_result = StackCheckResult(
            root_binary="usr/bin/test",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
            loadability=StackVerdict.PASS,
            abi_risk=StackVerdict.PASS,
            baseline_graph=DependencyGraph(root="test"),
            candidate_graph=DependencyGraph(root="test"),
        )

        with patch("abicheck.stack_checker.check_stack", return_value=mock_result):
            runner = CliRunner()
            result = runner.invoke(main, [
                "stack-check", "usr/bin/test",
                "--baseline", str(baseline),
                "--candidate", str(candidate),
                "--format", "json",
            ])
            assert result.exit_code == 0
            data = json.loads(result.output)
            # Check for any expected top-level key from stack report
            assert isinstance(data, dict)
            assert len(data) > 0

    def test_stack_check_fail_exit_code(self, tmp_path: Path) -> None:
        """stack-check returns exit 4 when loadability is fail."""
        from abicheck.resolver import DependencyGraph
        from abicheck.stack_checker import StackCheckResult, StackVerdict

        baseline = tmp_path / "baseline"
        candidate = tmp_path / "candidate"
        baseline.mkdir()
        candidate.mkdir()

        mock_result = StackCheckResult(
            root_binary="usr/bin/test",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
            loadability=StackVerdict.FAIL,
            abi_risk=StackVerdict.PASS,
            baseline_graph=DependencyGraph(root="test"),
            candidate_graph=DependencyGraph(root="test"),
        )

        with patch("abicheck.stack_checker.check_stack", return_value=mock_result):
            runner = CliRunner()
            result = runner.invoke(main, [
                "stack-check", "usr/bin/test",
                "--baseline", str(baseline),
                "--candidate", str(candidate),
            ])
            assert result.exit_code == 4

    def test_stack_check_warn_exit_code(self, tmp_path: Path) -> None:
        """stack-check returns exit 1 when abi_risk is warn."""
        from abicheck.resolver import DependencyGraph
        from abicheck.stack_checker import StackCheckResult, StackVerdict

        baseline = tmp_path / "baseline"
        candidate = tmp_path / "candidate"
        baseline.mkdir()
        candidate.mkdir()

        mock_result = StackCheckResult(
            root_binary="usr/bin/test",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
            loadability=StackVerdict.PASS,
            abi_risk=StackVerdict.WARN,
            baseline_graph=DependencyGraph(root="test"),
            candidate_graph=DependencyGraph(root="test"),
        )

        with patch("abicheck.stack_checker.check_stack", return_value=mock_result):
            runner = CliRunner()
            result = runner.invoke(main, [
                "stack-check", "usr/bin/test",
                "--baseline", str(baseline),
                "--candidate", str(candidate),
            ])
            assert result.exit_code == 1

    def test_stack_check_to_file(self, tmp_path: Path) -> None:
        """stack-check with --output writes report to file."""
        from abicheck.resolver import DependencyGraph
        from abicheck.stack_checker import StackCheckResult, StackVerdict

        baseline = tmp_path / "baseline"
        candidate = tmp_path / "candidate"
        baseline.mkdir()
        candidate.mkdir()
        out_file = tmp_path / "report.md"

        mock_result = StackCheckResult(
            root_binary="usr/bin/test",
            baseline_env=str(baseline),
            candidate_env=str(candidate),
            loadability=StackVerdict.PASS,
            abi_risk=StackVerdict.PASS,
            baseline_graph=DependencyGraph(root="test"),
            candidate_graph=DependencyGraph(root="test"),
        )

        with patch("abicheck.stack_checker.check_stack", return_value=mock_result):
            runner = CliRunner()
            result = runner.invoke(main, [
                "stack-check", "usr/bin/test",
                "--baseline", str(baseline),
                "--candidate", str(candidate),
                "--output", str(out_file),
            ])
            assert result.exit_code == 0
            assert out_file.exists()


# ---------------------------------------------------------------------------
# Compare output to file
# ---------------------------------------------------------------------------


class TestCompareOutputToFile:
    """Test compare command writing output to file."""

    def test_compare_output_to_file(self, tmp_path: Path) -> None:
        old = _make_json_snapshot(tmp_path, name="libtest", version="1.0")
        new = _make_json_snapshot(tmp_path, name="libtest", version="2.0")
        out_file = tmp_path / "report.json"

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old), str(new),
            "--format", "json",
            "-o", str(out_file),
        ])
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text(encoding="utf-8"))
        assert "verdict" in data
