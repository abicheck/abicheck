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
    @staticmethod
    def _patch_demangler(monkeypatch):
        """Stub the demangler so the test is independent of whether the host has
        a working C++ demangler (cxxfilt / c++filt) — macOS CI runners do not.
        The reporter imports ``demangle_text`` at call time, so patching the
        module attribute is sufficient. This verifies the *wiring* (which formats
        request demangling), not the platform demangler itself."""
        import abicheck.demangle as _dem
        monkeypatch.setattr(
            _dem, "demangle_text",
            lambda text: text.replace("_Z3foov", "foo()"),
        )

    def test_markdown_demangles_by_default(self, tmp_path, monkeypatch):
        self._patch_demangler(monkeypatch)
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "markdown"],
        )
        # markdown requests demangling by default -> stub rewrites the symbol.
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

    def test_html_keeps_mangled_by_default(self, tmp_path, monkeypatch):
        # HTML is NOT in the demangle default set: its renderer emits symbols
        # structurally and demangling the HTML string would inject unescaped
        # C++ '<'/'>'/'&'. Even with the demangler stubbed, html stays mangled.
        self._patch_demangler(monkeypatch)
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--format", "html"],
        )
        assert "_Z3foov" in result.output
        assert "foo()" not in result.output

    def test_no_demangle_override_on_markdown(self, tmp_path, monkeypatch):
        self._patch_demangler(monkeypatch)
        old_p, new_p = _write_removed_cpp_symbol(tmp_path)
        result = CliRunner().invoke(
            main,
            ["compare", str(old_p), str(new_p), "--format", "markdown", "--no-demangle"],
        )
        # --no-demangle suppresses demangling even on markdown -> stub not run.
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

    def test_debug_format_rejected_on_non_elf(self, tmp_path):
        # --debug-format dwarf/btf/ctf is ELF-only; compare must reject (not
        # silently ignore) it for a PE/Mach-O binary input, like dump does.
        old = tmp_path / "old.dll"
        new = tmp_path / "new.dll"
        old.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00")  # PE magic
        new.write_bytes(b"MZ\x90\x00\x03\x00\x00\x00")
        result = CliRunner().invoke(
            main, ["compare", str(old), str(new), "--debug-format", "dwarf"],
        )
        assert result.exit_code != 0
        combined = result.output + (result.stderr or "")
        assert "ELF" in combined


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


# ── §2.2 severity-exit floors (Codex P1 fixes) ──────────────────────────────


def _breaking_diff():
    """A real DiffResult with one BREAKING change (func removed)."""
    from abicheck.checker import compare
    old = AbiSnapshot(
        library="libtest.so", version="1.0",
        functions=[Function(name="_Z3foov", mangled="_Z3foov", return_type="int",
                             visibility=Visibility.PUBLIC)],
    )
    new = AbiSnapshot(library="libtest.so", version="2.0", functions=[])
    return compare(old, new)


class TestCompareReleaseExitFloors:
    """_exit_compare_release: severity must not downgrade operational failures."""

    def test_error_verdict_floors_severity_exit(self):
        import pytest

        from abicheck.cli_compare_release import _exit_compare_release

        # A per-library ERROR (failed dump/extract) produces no changes, so the
        # severity aggregation sees 0 — but it must still exit 4, not 0.
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("ERROR", False, [], severity_exit_code=0)
        assert exc.value.code == 4

    def test_removed_library_precedence_under_severity(self):
        import pytest

        from abicheck.cli_compare_release import _exit_compare_release

        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("BREAKING", True, ["libgone"], severity_exit_code=0)
        assert exc.value.code == 8

    def test_severity_code_passthrough(self):
        import pytest

        from abicheck.cli_compare_release import _exit_compare_release

        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("API_BREAK", False, [], severity_exit_code=2)
        assert exc.value.code == 2

    def test_clean_severity_does_not_exit(self):
        from abicheck.cli_compare_release import _exit_compare_release

        # severity says clean and no operational error -> returns without exiting.
        assert _exit_compare_release("COMPATIBLE", False, [], severity_exit_code=0) is None


class TestComputeReleaseSeverityExitCode:
    def test_none_without_flags(self):
        from abicheck.cli_compare_release import _compute_release_severity_exit_code

        assert _compute_release_severity_exit_code(
            [], None, None, None, None, None) is None

    def test_zero_with_flag_and_no_changes(self):
        from abicheck.cli_compare_release import _compute_release_severity_exit_code

        assert _compute_release_severity_exit_code(
            [], "info-only", None, None, None, None) == 0

    def test_aggregates_breaking_change(self):
        from abicheck.cli_compare_release import _compute_release_severity_exit_code

        entry = {"library": "libtest.so", "_diff_result": _breaking_diff()}
        # default preset: abi_breaking == error -> exit 4.
        assert _compute_release_severity_exit_code(
            [entry], "default", None, None, None, None) == 4
        # info-only downgrades everything below error -> exit 0.
        assert _compute_release_severity_exit_code(
            [entry], "info-only", None, None, None, None) == 0


class TestAppcompatSeverityExit:
    """Full-mode appcompat severity exit, via a stubbed check_appcompat."""

    def _dummy_libs(self, tmp_path):
        app = tmp_path / "app"
        old = tmp_path / "old.so"
        new = tmp_path / "new.so"
        for p in (app, old, new):
            p.write_bytes(b"\x7fELF")
        return app, old, new

    def _patch_result(self, monkeypatch, *, missing=None, app_break=False):
        import abicheck.appcompat as _ac
        from abicheck.appcompat import AppCompatResult
        from abicheck.checker import Verdict

        diff = _breaking_diff()
        res = AppCompatResult(
            app_path="app", old_lib_path="old.so", new_lib_path="new.so",
            missing_symbols=list(missing or []),
            # app_break -> the break is relevant to the app (breaking_for_app);
            # otherwise the break is present in full_diff but NOT app-scoped, so
            # it must not gate the app.
            breaking_for_app=list(diff.changes) if app_break else [],
            full_diff=diff,
            verdict=Verdict.BREAKING if (missing or app_break) else Verdict.COMPATIBLE,
        )
        monkeypatch.setattr(_ac, "check_appcompat", lambda *a, **k: res)
        return res

    def test_info_only_downgrades_app_break(self, tmp_path, monkeypatch):
        app, old, new = self._dummy_libs(tmp_path)
        self._patch_result(monkeypatch, app_break=True)
        result = CliRunner().invoke(
            main, ["appcompat", str(app), str(old), str(new),
                   "--severity-preset", "info-only"],
        )
        # An app-relevant break is governed by severity -> info-only exits 0.
        assert result.exit_code == 0

    def test_default_preset_exits_on_app_break(self, tmp_path, monkeypatch):
        app, old, new = self._dummy_libs(tmp_path)
        self._patch_result(monkeypatch, app_break=True)
        result = CliRunner().invoke(
            main, ["appcompat", str(app), str(old), str(new),
                   "--severity-preset", "default"],
        )
        assert result.exit_code == 4

    def test_unrelated_library_break_not_app_scoped(self, tmp_path, monkeypatch):
        app, old, new = self._dummy_libs(tmp_path)
        # full_diff has a break, but it is NOT relevant to the app
        # (breaking_for_app empty). The severity exit must stay app-scoped -> 0,
        # even under the default preset.
        self._patch_result(monkeypatch, app_break=False)
        result = CliRunner().invoke(
            main, ["appcompat", str(app), str(old), str(new),
                   "--severity-preset", "default"],
        )
        assert result.exit_code == 0

    def test_missing_symbols_floor_not_downgraded(self, tmp_path, monkeypatch):
        app, old, new = self._dummy_libs(tmp_path)
        # No app-relevant changes, but the app is missing a required symbol:
        # info-only must NOT downgrade this hard runtime break below 4.
        self._patch_result(monkeypatch, missing=["_Z3barv"])
        result = CliRunner().invoke(
            main, ["appcompat", str(app), str(old), str(new),
                   "--severity-preset", "info-only"],
        )
        assert result.exit_code == 4


class TestReleaseSeverityPolicyAndGlobal:
    """P2: per-library policy-file kind overrides; P1: bundle/matrix folding."""

    def test_per_library_uses_effective_kind_sets(self, monkeypatch):
        from abicheck.cli_compare_release import _compute_release_severity_exit_code

        diff = _breaking_diff()
        # Simulate a policy-file that reclassifies the (normally breaking) change
        # as compatible via the per-library effective kind sets. Proves the exit
        # consults diff._effective_kind_sets(), not the canonical sets.
        empty = frozenset()
        all_kinds = frozenset(c.kind for c in diff.changes)
        monkeypatch.setattr(
            diff, "_effective_kind_sets",
            lambda: (empty, empty, all_kinds, empty),
        )
        entry = {"_diff_result": diff}
        assert _compute_release_severity_exit_code(
            [entry], "default", None, None, None, None) == 0

    def test_fold_matrix_break_raises_exit(self):
        from abicheck.cli_compare_release import _fold_release_global_severity

        # Per-library clean (base 0), but a matrix DiffResult carries a break.
        matrix = _breaking_diff()
        assert _fold_release_global_severity(
            0, None, matrix, "default", None, None, None, None) == 4

    def test_fold_bundle_break_raises_exit(self):
        import types

        from abicheck.cli_compare_release import _fold_release_global_severity

        change = _breaking_diff().changes[0]
        finding = types.SimpleNamespace(to_change=lambda: change)
        bundle = types.SimpleNamespace(bundle_findings=[finding])
        assert _fold_release_global_severity(
            0, bundle, None, "default", None, None, None, None) == 4

    def test_fold_info_only_does_not_escalate(self):
        from abicheck.cli_compare_release import _fold_release_global_severity

        matrix = _breaking_diff()
        # info-only downgrades the matrix break below error -> base 0 preserved.
        assert _fold_release_global_severity(
            0, None, matrix, "info-only", None, None, None, None) == 0

    def test_fold_no_extras_returns_base(self):
        from abicheck.cli_compare_release import _fold_release_global_severity

        assert _fold_release_global_severity(
            2, None, None, "default", None, None, None, None) == 2

    def test_resolve_config_none_without_flags(self):
        from abicheck.cli_compare_release import _resolve_release_severity_config

        assert _resolve_release_severity_config(
            None, None, None, None, None) is None

    def test_resolve_config_set_with_flag(self):
        from abicheck.cli_compare_release import _resolve_release_severity_config

        assert _resolve_release_severity_config(
            "strict", None, None, None, None) is not None


# ── §6 follow-ups: debug-format auto override + parallel determinism ─────────


class TestDebugFormatAutoOverride:
    def test_auto_overrides_legacy_flag(self, tmp_path):
        # --debug-format auto must supersede a legacy --dwarf and run in
        # auto-detect mode (on JSON snapshots this is a smoke check: it must
        # not error and must exit 0 on identical input).
        old_p, new_p = _write_identical(tmp_path)
        result = CliRunner().invoke(
            main, ["compare", str(old_p), str(new_p), "--debug-format", "auto", "--dwarf"],
        )
        assert result.exit_code == 0


class TestCompareReleaseParallelOrdering:
    def test_parallel_results_in_matched_keys_order(self, monkeypatch):
        from pathlib import Path as _P

        import abicheck.cli_compare_release as _cr

        monkeypatch.setattr(
            _cr, "_compare_one_library",
            lambda key, *a: {"library": key, "key": key},
        )
        keys = ["libc", "liba", "libb"]
        old_map = {k: _P(k) for k in keys}
        out = _cr._compare_release_parallel(keys, (), old_map, max_workers=4)
        # Deterministic: emitted in matched_keys order, not completion order.
        assert [r["key"] for r in out] == keys
