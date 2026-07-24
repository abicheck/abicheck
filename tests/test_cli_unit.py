"""Unit tests for cli.py — compare and compat subcommands.

Covers compare_cmd output formats, exit codes, suppression handling,
and compat_check_cmd descriptor parsing/error paths.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
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

    def test_sarif_root_cause_mode_adds_properties(self, tmp_path):
        # G29 Phase 3 slice 5 (ADR-051): --report-mode root-cause must reach
        # SARIF through the full CLI -> service_render.render_output ->
        # sarif.to_sarif_str chain, not just the unit-level to_sarif() call.
        old_p, new_p = _breaking_snapshots(tmp_path)
        out = tmp_path / "results.sarif"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "sarif",
            "--report-mode", "root-cause", "-o", str(out),
        ])
        assert result.exit_code == 4
        content = json.loads(out.read_text(encoding="utf-8"))
        results = content["runs"][0]["results"]
        assert results
        assert all("rootCauseId" in r["properties"] for r in results)


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


# ── _resolve_demangle (shared by primary and --secondary-format renders) ──

class TestResolveDemangle:
    def test_defaults_on_for_markdown_and_review(self):
        from abicheck.cli_compare_helpers import _resolve_demangle

        assert _resolve_demangle("markdown", None) is True
        assert _resolve_demangle("review", None) is True

    @pytest.mark.parametrize("fmt", ["json", "sarif", "junit", "html"])
    def test_defaults_off_for_machine_formats_and_html(self, fmt):
        from abicheck.cli_compare_helpers import _resolve_demangle

        assert _resolve_demangle(fmt, None) is False

    def test_explicit_flag_always_wins(self):
        from abicheck.cli_compare_helpers import _resolve_demangle

        assert _resolve_demangle("json", True) is True
        assert _resolve_demangle("markdown", False) is False


# ── _resolve_compare_collect_mode (CLI-audit P1: --depth inference) ──────
#
# Precedence: explicit --depth > .abicheck.yml source.method > inferred from
# raw --sources/--build-info > off. Before this fix, omitting --depth always
# resolved to "off" even when --old-sources/--new-sources/--build-info were
# explicitly given, so those inputs were silently ignored.

class TestResolveCompareCollectMode:
    def _call(self, depth=None, source_method=None, old_sources=None,
              new_sources=None, old_build_info=None, new_build_info=None):
        from abicheck.cli_compare_helpers import _resolve_compare_collect_mode

        return _resolve_compare_collect_mode(
            depth, source_method, old_sources, new_sources,
            old_build_info, new_build_info,
        )

    def test_no_depth_no_inputs_is_off(self):
        mode, label = self._call()
        assert mode == "off"
        assert "off" in label

    def test_sources_given_no_depth_infers_source(self, tmp_path):
        mode, label = self._call(old_sources=tmp_path / "src")
        assert mode == "source-target"
        assert "inferred" in label

    def test_new_sources_alone_also_infers_source(self, tmp_path):
        mode, _ = self._call(new_sources=tmp_path / "src")
        assert mode == "source-target"

    def test_build_info_given_no_depth_infers_build(self, tmp_path):
        mode, label = self._call(old_build_info=tmp_path / "build")
        assert mode == "build"
        assert "inferred" in label

    def test_sources_takes_precedence_over_build_info_when_both_given(self, tmp_path):
        mode, _ = self._call(
            old_sources=tmp_path / "src", old_build_info=tmp_path / "build",
        )
        assert mode == "source-target"

    def test_explicit_depth_wins_over_inference(self, tmp_path):
        # --depth binary explicitly requested despite raw --sources: the
        # explicit choice must still suppress collection (with the CLI's own
        # "ignoring it" warning), not be silently overridden by inference.
        mode, label = self._call(depth="binary", old_sources=tmp_path / "src")
        assert mode == "off"
        assert "--depth binary" in label

    def test_config_source_method_wins_over_inference(self, tmp_path):
        # .abicheck.yml source.method applies when no --depth was given, even
        # if raw --sources are also present (config > bare inference).
        mode, label = self._call(source_method="s1", old_sources=tmp_path / "src")
        assert mode == "build"
        assert "source.method=s1" in label

    def test_explicit_depth_wins_over_config_source_method(self, tmp_path):
        mode, label = self._call(depth="source", source_method="s1")
        assert mode == "source-target"
        assert "--depth source" in label

    def test_invalid_source_method_raises_usage_error(self):
        import click

        with pytest.raises(click.UsageError, match="source.method"):
            self._call(source_method="not-a-method")


# ── compare --secondary-format/--secondary-output ───────────────────────

class TestCompareSecondaryFormat:
    def test_writes_second_format_from_same_run(self, tmp_path):
        old_p, new_p = _breaking_snapshots(tmp_path)
        secondary_out = tmp_path / "secondary.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "markdown",
            "--secondary-format", "json", "--secondary-output", str(secondary_out),
        ])
        assert result.exit_code == 4
        assert "# ABI Report" in result.output
        parsed = json.loads(secondary_out.read_text(encoding="utf-8"))
        assert parsed["verdict"] == "BREAKING"

    def test_secondary_format_ignores_show_only_filter(self, tmp_path):
        # The secondary render always emits the full, unfiltered report even
        # when the primary format's --show-only narrows the display.
        old_p, new_p = _breaking_snapshots(tmp_path)
        secondary_out = tmp_path / "secondary.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "markdown",
            "--show-only", "added",
            "--secondary-format", "json", "--secondary-output", str(secondary_out),
        ])
        assert result.exit_code == 4
        parsed = json.loads(secondary_out.read_text(encoding="utf-8"))
        assert parsed["changes"]

    def test_secondary_format_ignores_primary_report_mode(self, tmp_path):
        # The secondary render always uses report_mode="full", not the
        # primary's --report-mode leaf — a --secondary-format consumer
        # expects the same full shape regardless of how the primary format
        # groups its own display (Codex review, PR #557).
        old_p, new_p = _breaking_snapshots(tmp_path)
        secondary_out = tmp_path / "secondary.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "markdown",
            "--report-mode", "leaf",
            "--secondary-format", "json", "--secondary-output", str(secondary_out),
        ])
        assert result.exit_code == 4
        parsed = json.loads(secondary_out.read_text(encoding="utf-8"))
        assert "leaf_changes" not in parsed
        assert parsed["changes"]

    def test_secondary_format_resolves_own_demangle_default(self, tmp_path):
        # demangle is resolved per-format (markdown/review default ON, json/
        # sarif/html/junit default OFF) — a machine primary format (json)
        # must not force demangle=False onto a markdown/review secondary
        # render just because that's the primary's own default
        # (Codex review, PR #557).
        old_p, new_p = _breaking_snapshots(tmp_path)
        secondary_out = tmp_path / "secondary.md"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "json",
            "--secondary-format", "markdown", "--secondary-output", str(secondary_out),
        ])
        assert result.exit_code == 4
        secondary_text = secondary_out.read_text(encoding="utf-8")
        assert "_Z3barv" not in secondary_text
        assert "bar" in secondary_text

    def test_secondary_format_requires_secondary_output(self, tmp_path):
        old_p, new_p = _write_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "markdown",
            "--secondary-format", "json",
        ])
        assert result.exit_code == 64
        assert "--secondary-format requires --secondary-output" in result.output

    def test_secondary_output_requires_secondary_format(self, tmp_path):
        # Passing --secondary-output alone would otherwise be silently
        # ignored — no secondary artifact, no error (Codex review, PR #557).
        old_p, new_p = _write_snapshots(tmp_path)
        secondary_out = tmp_path / "secondary.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "markdown",
            "--secondary-output", str(secondary_out),
        ])
        assert result.exit_code == 64
        assert "--secondary-output requires --secondary-format" in result.output
        assert not secondary_out.exists()

    def test_secondary_output_rejects_same_path_as_primary(self, tmp_path):
        # Writing both formats to the same file would silently overwrite the
        # primary report with the secondary one (Codex review, PR #557).
        old_p, new_p = _write_snapshots(tmp_path)
        same_path = tmp_path / "report"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--format", "markdown",
            "-o", str(same_path),
            "--secondary-format", "json", "--secondary-output", str(same_path),
        ])
        assert result.exit_code == 64
        assert "--secondary-output must differ from --output/-o" in result.output

    def test_dry_run_rejects_secondary_output(self, tmp_path):
        # Regression (CLI-audit P2): --dry-run promises no output-file side
        # effect and already rejects -o/--output, but --secondary-output was
        # accepted and then silently never written (the dry run exits before
        # the secondary render runs) — reject it the same way.
        old_p, new_p = _write_snapshots(tmp_path)
        secondary_out = tmp_path / "secondary.json"
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "--dry-run",
            "--secondary-format", "json", "--secondary-output", str(secondary_out),
        ])
        assert result.exit_code == 64
        assert "--dry-run cannot be combined with --secondary-output" in result.output
        assert not secondary_out.exists()


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


def test_main_installs_sigterm_cleanup(monkeypatch) -> None:
    """Codex review (PR #591): the plain CLI/CI path has no outer watchdog
    (unlike the MCP path's service_scan._kill_process_tree) that would
    otherwise clean up a run_bounded()-detached compiler process group on an
    external SIGTERM. Every CLI invocation must install that cleanup via
    deadline.install_sigterm_cleanup() before any subcommand runs."""
    from abicheck import cli

    calls: list[bool] = []
    monkeypatch.setattr(
        cli.deadline, "install_sigterm_cleanup", lambda: calls.append(True)
    )
    # `abicheck --help` alone is handled by click's eager root --help option,
    # which exits before the group callback body runs; a subcommand
    # invocation (its own --help is eager only for the *subcommand*) is what
    # actually exercises `main()`'s body first.
    CliRunner().invoke(main, ["dump", "--help"])
    assert calls == [True]

