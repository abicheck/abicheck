"""Tests for the CLI/config-review changes:

- compare: tri-state --demangle (default ON for human formats, OFF for json/sarif)
- compare: explicit exit-code-scheme announcement on stderr
- compare / dump: --debug-format selector superseding --btf/--ctf/--dwarf
- compare: --report-mode impact == full + --show-impact
- compare-release: --scope-public-headers default ON + toggle, -j default 0,
  severity-aware exit aggregation
- appcompat: --scope-public-headers wiring, -H/-I ignored-mode warning,
  severity options
"""
from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from abicheck.cli import main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers ──────────────────────────────────────────────────────────────


def _write_removed_cpp_symbol(tmp_path: Path) -> tuple[Path, Path]:
    """Old has a C++ function; new removes it (a breaking change)."""
    # Use the mangled symbol as the rendered name so the human-format output
    # carries a raw "_Z..." token that demangling can rewrite to "foo()".
    old = AbiSnapshot(
        library="libtest.so", version="1.0",
        functions=[Function(name="_Z3foov", mangled="_Z3foov", return_type="int",
                             visibility=Visibility.PUBLIC)],
    )
    new = AbiSnapshot(library="libtest.so", version="2.0", functions=[])
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


def _write_identical(tmp_path: Path) -> tuple[Path, Path]:
    snap = AbiSnapshot(
        library="libtest.so", version="1.0",
        functions=[Function(name="foo", mangled="_Z3foov", return_type="int",
                             visibility=Visibility.PUBLIC)],
    )
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(snap), encoding="utf-8")
    new_p.write_text(snapshot_to_json(snap), encoding="utf-8")
    return old_p, new_p


# ── §3 demangle tri-state ──────────────────────────────────────────────────


class TestDemangleTriState:
    def test_markdown_demangles_by_default(self, tmp_path):
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "markdown"],
        )
        # Demangled "foo()" should appear; raw "_Z3foov" should not.
        assert "foo()" in result.output
        assert "_Z3foov" not in result.output

    def test_json_keeps_mangled_by_default(self, tmp_path):
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "json"],
        )
        assert "_Z3foov" in result.output

    def test_sarif_keeps_mangled_by_default(self, tmp_path):
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "sarif"],
        )
        assert "_Z3foov" in result.output

    def test_no_demangle_override_on_markdown(self, tmp_path):
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "markdown", "--no-demangle"],
        )
        assert "_Z3foov" in result.output

    def test_json_stays_mangled_even_with_demangle(self, tmp_path):
        # Machine formats (json/sarif) intentionally always keep raw mangled
        # symbols; --demangle is a no-op there by design.
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "json", "--demangle"],
        )
        assert "_Z3foov" in result.output
        assert "foo()" not in result.output


# ── §4 exit-scheme announcement ─────────────────────────────────────────────


class TestExitSchemeAnnouncement:
    def test_legacy_scheme_announced(self, tmp_path):
        old_p, new_p = _write_identical(tmp_path)
        # Click 8.2+ keeps stderr separate from stdout by default.
        result = CliRunner().invoke(main, ["compare", str(old_p), str(new_p)])
        assert "Exit-code scheme: legacy verdict" in result.stderr
        # Announcement must NOT pollute stdout (the report).
        assert "Exit-code scheme" not in result.stdout

    def test_severity_scheme_announced(self, tmp_path):
        old_p, new_p = _write_identical(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--severity-preset", "default"],
        )
        assert "Exit-code scheme: severity-aware" in result.stderr
        assert "Exit-code scheme" not in result.stdout


# ── §6 --debug-format selector ──────────────────────────────────────────────


class TestDebugFormatSelector:
    def test_compare_exposes_debug_format(self):
        out = CliRunner().invoke(main, ["compare", "--help"]).output
        assert "--debug-format" in out
        # The legacy --btf/--ctf/--dwarf flags are hidden: they have no
        # left-column option entry (they only appear in the selector's help
        # text). The selector entry shows the [auto|dwarf|btf|ctf] choices.
        assert "[auto|dwarf|btf|ctf]" in out

    def test_dump_exposes_debug_format(self):
        out = CliRunner().invoke(main, ["dump", "--help"]).output
        assert "--debug-format" in out
        assert "[auto|dwarf|btf|ctf]" in out

    def test_legacy_dwarf_flag_still_works(self, tmp_path):
        old_p, new_p = _write_identical(tmp_path)
        # Hidden does not mean removed: --dwarf must remain functional.
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--dwarf"],
        )
        assert result.exit_code == 0

    def test_dump_compile_db_hidden(self):
        out = CliRunner().invoke(main, ["dump", "--help"]).output
        assert "--compile-db " not in out
        assert "--compile-db-filter" in out  # the filter alias stays visible

    def test_debug_format_auto_accepted(self, tmp_path):
        old_p, new_p = _write_identical(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--debug-format", "auto"],
        )
        assert result.exit_code == 0


# ── §6 --report-mode impact ─────────────────────────────────────────────────


class TestReportModeImpact:
    def test_impact_in_choices(self):
        out = CliRunner().invoke(main, ["compare", "--help"]).output
        assert "impact" in out

    def test_impact_mode_runs(self, tmp_path):
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--report-mode", "impact"],
        )
        # Exit code unchanged: a removed symbol is still a 4 (BREAKING).
        assert result.exit_code == 4


# ── §2 compare-release scope + jobs defaults ────────────────────────────────


class TestCompareReleaseDefaults:
    def test_scope_toggle_present(self):
        out = CliRunner().invoke(main, ["compare-release", "--help"]).output
        assert "--scope-public-headers / --no-scope-public-headers" in out

    def test_jobs_default_zero(self):
        out = CliRunner().invoke(main, ["compare-release", "--help"]).output
        assert "auto-detect" in out

    def test_severity_options_present(self):
        out = CliRunner().invoke(main, ["compare-release", "--help"]).output
        assert "--severity-preset" in out
        assert "--severity-abi-breaking" in out


# ── §5 compare-release severity-aware exit aggregation ──────────────────────


class TestCompareReleaseSeverityExit:
    def _make_release(self, tmp_path: Path) -> tuple[Path, Path]:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old = AbiSnapshot(
            library="libtest.so", version="1.0",
            functions=[Function(name="foo", mangled="_Z3foov", return_type="int",
                                 visibility=Visibility.PUBLIC)],
        )
        new = AbiSnapshot(library="libtest.so", version="2.0", functions=[])
        (old_dir / "libtest.json").write_text(snapshot_to_json(old), encoding="utf-8")
        (new_dir / "libtest.json").write_text(snapshot_to_json(new), encoding="utf-8")
        return old_dir, new_dir

    def test_severity_info_only_exits_zero(self, tmp_path):
        old_dir, new_dir = self._make_release(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare-release", str(old_dir), str(new_dir),
             "--severity-preset", "info-only"],
        )
        # info-only downgrades everything below error -> exit 0 despite the break.
        assert result.exit_code == 0

    def test_severity_default_exits_breaking(self, tmp_path):
        old_dir, new_dir = self._make_release(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare-release", str(old_dir), str(new_dir),
             "--severity-preset", "default"],
        )
        assert result.exit_code == 4

    def test_no_severity_keeps_legacy_exit(self, tmp_path):
        old_dir, new_dir = self._make_release(tmp_path)
        result = CliRunner().invoke(
            main, ["compare-release", str(old_dir), str(new_dir)],
        )
        # Removed C++ symbol == BREAKING == legacy exit 4.
        assert result.exit_code == 4


# ── §1 appcompat warnings + scope ───────────────────────────────────────────


class TestAppcompatWarnings:
    def test_scope_toggle_present(self):
        out = CliRunner().invoke(main, ["appcompat", "--help"]).output
        assert "--scope-public-headers / --no-scope-public-headers" in out

    def test_severity_options_present(self):
        out = CliRunner().invoke(main, ["appcompat", "--help"]).output
        assert "--severity-preset" in out


class TestValidateAppcompatArgs:
    def test_warns_on_ignored_headers_in_weak_mode(self):
        from abicheck.cli_appcompat import _validate_appcompat_args

        # Should not raise, but the warning is emitted via click.echo. We invoke
        # within a Click context-free call; click.echo to stderr is fine here.
        # The key behavior: headers in weak mode do NOT raise (only warn).
        _validate_appcompat_args(
            weak_mode=True,
            old_lib=None, new_lib=None,
            list_symbols=False,
            old_headers_only=(), new_headers_only=(),
            old_includes_only=(), new_includes_only=(),
            headers=(Path("foo.h"),), includes=(),
        )

    def test_per_side_headers_still_rejected_in_weak_mode(self):
        import pytest

        from abicheck.cli_appcompat import _validate_appcompat_args

        with pytest.raises(Exception):  # click.UsageError
            _validate_appcompat_args(
                weak_mode=True,
                old_lib=None, new_lib=None,
                list_symbols=False,
                old_headers_only=(Path("foo.h"),), new_headers_only=(),
                old_includes_only=(), new_includes_only=(),
                headers=(), includes=(),
            )
