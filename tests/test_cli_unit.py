"""Unit tests for cli.py — compare and compat subcommands.

Covers compare_cmd output formats, exit codes, suppression handling,
and compat_check_cmd descriptor parsing/error paths.
"""
from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers ──────────────────────────────────────────────────────────────

def _write_snapshots(tmp_path: Path, old_snap: AbiSnapshot | None = None,
                     new_snap: AbiSnapshot | None = None) -> tuple[Path, Path]:
    """Write old/new snapshots to JSON files and return their paths."""
    if old_snap is None:
        old_snap = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[Function(name="foo", mangled="_Z3foov", return_type="int",
                                visibility=Visibility.PUBLIC)],
        )
    if new_snap is None:
        new_snap = AbiSnapshot(
            library="libtest.so", version="2.0",
            functions=[Function(name="foo", mangled="_Z3foov", return_type="int",
                                visibility=Visibility.PUBLIC)],
        )
    old_path = tmp_path / "old.json"
    new_path = tmp_path / "new.json"
    old_path.write_text(snapshot_to_json(old_snap), encoding="utf-8")
    new_path.write_text(snapshot_to_json(new_snap), encoding="utf-8")
    return old_path, new_path


def _breaking_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    """Snapshots where a function is removed → BREAKING."""
    old = AbiSnapshot(
        library="libtest.so", version="1.0",
        functions=[
            Function(name="foo", mangled="_Z3foov", return_type="int",
                     visibility=Visibility.PUBLIC),
            Function(name="bar", mangled="_Z3barv", return_type="void",
                     visibility=Visibility.PUBLIC),
        ],
    )
    new = AbiSnapshot(
        library="libtest.so", version="2.0",
        functions=[
            Function(name="foo", mangled="_Z3foov", return_type="int",
                     visibility=Visibility.PUBLIC),
        ],
    )
    return _write_snapshots(tmp_path, old, new)


# ── compare markdown ────────────────────────────────────────────────────

class TestCompareMarkdown:
    def test_no_change_exit_0(self, tmp_path):
        old_p, new_p = _write_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 0
        assert "NO_CHANGE" in result.output

    def test_breaking_exit_4(self, tmp_path):
        old_p, new_p = _breaking_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 4

    def test_output_to_file(self, tmp_path):
        old_p, new_p = _write_snapshots(tmp_path)
        out = tmp_path / "report.md"
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_p), str(new_p), "-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()
        assert "Report written to" in result.output


class TestBaselinePushAutoPlatform:
    def test_auto_platform_detection_failure_requires_explicit_platform(self, tmp_path, monkeypatch):
        snap = AbiSnapshot(library=str(tmp_path / "libfoo.so"), version="1.0", functions=[])
        Path(snap.library).write_bytes(b"\x7fELF")
        snapshot_path = tmp_path / "snap.json"
        snapshot_path.write_text(snapshot_to_json(snap), encoding="utf-8")
        registry = tmp_path / "registry"

        monkeypatch.setattr("abicheck.baseline.detect_platform_from_binary", lambda _p: None)

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "baseline", "push", "libfoo",
                "--version", "1.0",
                "--snapshot", str(snapshot_path),
                "--registry", str(registry),
                "--auto-platform",
            ],
        )
        assert result.exit_code != 0
        assert "failed to detect binary architecture" in result.output

    def test_auto_platform_detection_success_pushes_baseline(self, tmp_path, monkeypatch):
        snap = AbiSnapshot(library=str(tmp_path / "libfoo.so"), version="1.0", functions=[])
        Path(snap.library).write_bytes(b"\x7fELF")
        snapshot_path = tmp_path / "snap.json"
        snapshot_path.write_text(snapshot_to_json(snap), encoding="utf-8")
        registry = tmp_path / "registry"

        monkeypatch.setattr(
            "abicheck.baseline.detect_platform_from_binary",
            lambda _p: "linux-x86_64",
        )

        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "baseline", "push", "libfoo",
                "--version", "1.0",
                "--snapshot", str(snapshot_path),
                "--registry", str(registry),
                "--auto-platform",
            ],
        )
        assert result.exit_code == 0
        assert "Auto-detected platform: linux-x86_64" in result.output
        assert "Baseline pushed:" in result.output


# ── compare JSON ────────────────────────────────────────────────────────

class TestCompareJson:
    def test_json_output(self, tmp_path):
        old_p, new_p = _write_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_p), str(new_p), "--format", "json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "verdict" in parsed


# ── compare SARIF ───────────────────────────────────────────────────────

class TestCompareSarif:
    def test_sarif_output(self, tmp_path):
        old_p, new_p = _breaking_snapshots(tmp_path)
        out = tmp_path / "results.sarif"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "sarif", "-o", str(out),
        ])
        assert result.exit_code == 4
        content = json.loads(out.read_text(encoding="utf-8"))
        assert content.get("$schema") or "runs" in content


# ── compare HTML ────────────────────────────────────────────────────────

class TestCompareHtml:
    def test_html_output_to_file(self, tmp_path):
        old_p, new_p = _write_snapshots(tmp_path)
        out = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "html", "-o", str(out),
        ])
        assert result.exit_code == 0
        assert out.exists()
        assert "<html" in out.read_text(encoding="utf-8").lower()

    def test_html_output_to_stdout(self, tmp_path):
        old_p, new_p = _write_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "html",
        ])
        assert result.exit_code == 0
        assert "<html" in result.output.lower()


# ── compare with suppression ────────────────────────────────────────────

class TestCompareSuppression:
    def test_suppression_file_applied(self, tmp_path):
        old_p, new_p = _breaking_snapshots(tmp_path)
        sup = tmp_path / "suppress.yaml"
        sup.write_text(
            "version: 1\nsuppressions:\n  - symbol: _Z3barv\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--suppress", str(sup),
        ])
        # After suppression, the removed function is suppressed → NO_CHANGE
        assert result.exit_code == 0

    def test_bad_suppression_file(self, tmp_path):
        old_p, new_p = _write_snapshots(tmp_path)
        sup = tmp_path / "bad.yaml"
        sup.write_text("not: valid: suppression: format", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--suppress", str(sup),
        ])
        assert result.exit_code != 0


# ── compare suppression warning ─────────────────────────────────────────

class TestCompareSuppressionWarning:
    def test_all_changes_suppressed_warns(self, tmp_path):
        """When suppression file swallows all changes, a warning is shown."""
        old_p, new_p = _breaking_snapshots(tmp_path)
        sup = tmp_path / "suppress.yaml"
        sup.write_text(
            "version: 1\nsuppressions:\n  - symbol: _Z3barv\n",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--suppress", str(sup),
        ])
        assert result.exit_code == 0
        assert "suppressed" in result.output.lower()


# ── compat descriptor errors ────────────────────────────────────────────

class TestCompatErrors:
    def test_invalid_descriptor_exits_6(self, tmp_path):
        old = tmp_path / "old.xml"
        new = tmp_path / "new.xml"
        old.write_text("<invalid>", encoding="utf-8")
        new.write_text("<invalid>", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, [
            "compat", "check", "-lib", "libtest", "-old", str(old), "-new", str(new),
        ])
        assert result.exit_code == 6

    def test_missing_library_exits_4(self, tmp_path):
        """Descriptor references a .so that doesn't exist → exit 4."""
        old = tmp_path / "old.xml"
        new = tmp_path / "new.xml"
        old.write_text(
            "<descriptor><version>1.0</version><libs>/nonexistent/lib.so</libs></descriptor>",
            encoding="utf-8",
        )
        new.write_text(
            "<descriptor><version>2.0</version><libs>/nonexistent/lib.so</libs></descriptor>",
            encoding="utf-8",
        )
        runner = CliRunner()
        result = runner.invoke(main, [
            "compat", "check", "-lib", "libtest", "-old", str(old), "-new", str(new),
        ])
        assert result.exit_code == 4


# ── --version ───────────────────────────────────────────────────────────

class TestVersionFlag:
    def test_version_flag_prints_semver(self):
        """abicheck --version prints a semver-shaped string."""
        import re
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        # should contain at least one digit.digit pattern (e.g. "0.1.0")
        assert re.search(r"\d+\.\d+", result.output), (
            f"--version output doesn't look like a version: {result.output!r}"
        )
        assert "abicheck" in result.output.lower()


# ── compat help output ──────────────────────────────────────────────────

class TestCompatHelp:
    def test_compat_help_lists_flags(self):
        runner = CliRunner()
        result = runner.invoke(main, ["compat", "check", "--help"])
        assert result.exit_code == 0
        for flag in ["-lib", "-old", "-new", "-s", "-source", "-stdout",
                     "-skip-symbols", "-v1", "-v2"]:
            assert flag in result.output, f"{flag} not in help output"


class TestCompatClassifiedErrorPaths:
    def _snap(self, version: str) -> AbiSnapshot:
        return AbiSnapshot(library="libtest.so", version=version)

    def _write_minimal_descriptors(self, tmp_path):
        old = tmp_path / "old.xml"
        new = tmp_path / "new.xml"
        old.write_text("<descriptor/>", encoding="utf-8")
        new.write_text("<descriptor/>", encoding="utf-8")
        return old, new

    def test_skip_symbols_invalid_regex_exits_6(self, tmp_path, monkeypatch):
        old, new = self._write_minimal_descriptors(tmp_path)
        bad = tmp_path / "skip.txt"
        bad.write_text("([\n", encoding="utf-8")

        snaps = [self._snap("1.0"), self._snap("2.0")]
        monkeypatch.setattr("abicheck.compat.cli._load_descriptor_or_dump", lambda *_a, **_k: snaps.pop(0))

        runner = CliRunner()
        result = runner.invoke(main, [
            "compat", "check", "-lib", "libtest", "-old", str(old), "-new", str(new),
            "-skip-symbols", str(bad),
        ])
        assert result.exit_code == 6
        assert "pattern" in result.output.lower() or "skip-symbols" in result.output.lower()

    def test_skip_internal_invalid_regex_exits_6(self, tmp_path, monkeypatch):
        old, new = self._write_minimal_descriptors(tmp_path)

        snaps = [self._snap("1.0"), self._snap("2.0")]
        monkeypatch.setattr("abicheck.compat.cli._load_descriptor_or_dump", lambda *_a, **_k: snaps.pop(0))

        runner = CliRunner()
        result = runner.invoke(main, [
            "compat", "check", "-lib", "libtest", "-old", str(old), "-new", str(new),
            "-skip-internal-symbols", "([",
        ])
        assert result.exit_code == 6
        assert "pattern" in result.output.lower() or "skip-internal" in result.output.lower()

    def test_suppression_load_error_exits_6(self, tmp_path, monkeypatch):
        old, new = self._write_minimal_descriptors(tmp_path)
        sup = tmp_path / "bad_sup.yaml"
        sup.write_text("- this is a list not a dict\n", encoding="utf-8")

        snaps = [self._snap("1.0"), self._snap("2.0")]
        monkeypatch.setattr("abicheck.compat.cli._load_descriptor_or_dump", lambda *_a, **_k: snaps.pop(0))

        runner = CliRunner()
        result = runner.invoke(main, [
            "compat", "check", "-lib", "libtest", "-old", str(old), "-new", str(new),
            "--suppress", str(sup),
        ])
        assert result.exit_code == 6
        assert "suppression" in result.output.lower() or "mapping" in result.output.lower()

    def test_skip_symbols_missing_file_exits_4(self, tmp_path, monkeypatch):
        old, new = self._write_minimal_descriptors(tmp_path)

        snaps = [self._snap("1.0"), self._snap("2.0")]
        monkeypatch.setattr("abicheck.compat.cli._load_descriptor_or_dump", lambda *_a, **_k: snaps.pop(0))

        missing = tmp_path / "missing_skip.txt"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compat", "check", "-lib", "libtest", "-old", str(old), "-new", str(new),
            "-skip-symbols", str(missing),
        ])
        assert result.exit_code == 4
        assert "no such file" in result.output.lower() or "skip-symbols" in result.output.lower()

    def test_symbols_list_missing_file_exits_4(self, tmp_path, monkeypatch):
        old, new = self._write_minimal_descriptors(tmp_path)

        snaps = [self._snap("1.0"), self._snap("2.0")]
        monkeypatch.setattr("abicheck.compat.cli._load_descriptor_or_dump", lambda *_a, **_k: snaps.pop(0))

        missing = tmp_path / "missing_symbols_list.txt"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compat", "check", "-lib", "libtest", "-old", str(old), "-new", str(new),
            "-symbols-list", str(missing),
        ])
        assert result.exit_code == 4
        assert "no such file" in result.output.lower() or "symbols-list" in result.output.lower()

    def test_report_write_error_exits_7(self, tmp_path, monkeypatch):
        old, new = self._write_minimal_descriptors(tmp_path)

        snaps = [self._snap("1.0"), self._snap("2.0")]
        monkeypatch.setattr("abicheck.compat.cli._load_descriptor_or_dump", lambda *_a, **_k: snaps.pop(0))

        def _raise_write(*_a, **_k):
            raise OSError("write failed")

        monkeypatch.setattr("abicheck.compat.cli.write_html_report", _raise_write)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compat", "check", "-lib", "libtest", "-old", str(old), "-new", str(new),
            "-report-path", str(tmp_path / "r.html"), "-report-format", "html",
        ])
        assert result.exit_code == 7
        assert "write" in result.output.lower() or "report" in result.output.lower()


class TestNoFailOnAdditionsFlag:
    """Verify --fail-on-additions was removed (use --severity-addition error instead)."""

    def test_fail_on_additions_flag_rejected(self, tmp_path: Path) -> None:
        """--fail-on-additions should no longer be recognized by the CLI."""
        snap = {
            "library": "libtest.so", "version": "1.0", "platform": "elf",
            "functions": [], "variables": [], "types": [], "enums": [], "typedefs": {},
        }
        p = tmp_path / "snap.json"
        p.write_text(json.dumps(snap), encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(p), str(p), "--fail-on-additions"])
        # Unrecognised option → Click usage error, remapped to the dedicated
        # usage-error code (outside the compare result space {0,1,2,4}) so it is
        # not mistaken for a "2 = source break" verdict.
        from abicheck.cli import _EXIT_USAGE_ERROR
        assert result.exit_code == _EXIT_USAGE_ERROR
