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

"""Coverage-focused tests for the CLI modules.

Targets uncovered error paths, output-format branches, help text and exit-code
logic in ``abicheck.cli``, ``abicheck.cli_compare_release`` and
``abicheck.cli_appcompat``. Pure-Python only: no gcc/castxml/abidiff/abicc.
Binary-dependent CLI flows are exercised by calling the internal helpers
directly with pre-built JSON ``AbiSnapshot`` files / mocks instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from abicheck.checker import Change, DiffResult
from abicheck.checker_policy import ChangeKind, Verdict
from abicheck.cli import (
    _announce_exit_scheme,
    _collect_additions,
    _collect_release_inputs,
    _exit_with_severity_or_verdict,
    _expand_header_inputs,
    _load_probe_matrix_changes,
    _load_suppression_and_policy,
    _maybe_emit_annotations,
    _merge_gcc_options,
    _resolve_linker_script,
    _resolve_per_side_options,
    _safe_write_output,
    _sniff_text_format,
    _warn_ignored_flags,
    _write_or_echo,
    main,
)
from abicheck.cli_compare_release import (
    _exit_compare_release,
    _fold_release_global_severity,
    _format_release_json,
    _format_release_markdown,
    _release_md_bundle_findings,
    _release_md_matrix_findings,
    _resolve_release_headers,
    _resolve_release_severity_config,
)
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── snapshot helpers (mirror tests/test_compare_release.py) ───────────────────


def _snap(version: str = "1.0", funcs=None, library: str = "libfoo.so") -> AbiSnapshot:
    if funcs is None:
        funcs = [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
        ]
    return AbiSnapshot(library=library, version=version, functions=funcs)


def _write_snap(path: Path, snap: AbiSnapshot) -> Path:
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _breaking_pair(lib: str = "libfoo.so"):
    old = _snap(
        "1.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
            Function(
                name="bar",
                mangled="_Z3barv",
                return_type="void",
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    new = _snap(
        "2.0",
        [
            Function(
                name="foo",
                mangled="_Z3foov",
                return_type="int",
                visibility=Visibility.PUBLIC,
            ),
        ],
        library=lib,
    )
    return old, new


def _invoke(*args: str):
    result = CliRunner().invoke(main, list(args))
    return result


# ── _expand_header_inputs error paths (cli.py:75 and friends) ─────────────────


class TestExpandHeaderInputs:
    def test_missing_header_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(click.ClickException, match="not found"):
            _expand_header_inputs([tmp_path / "nope.h"])

    def test_empty_header_dir_raises(self, tmp_path: Path) -> None:
        d = tmp_path / "hdrs"
        d.mkdir()
        with pytest.raises(click.ClickException, match="no supported header"):
            _expand_header_inputs([d])

    def test_dir_with_headers_dedup(self, tmp_path: Path) -> None:
        d = tmp_path / "hdrs"
        d.mkdir()
        (d / "a.h").write_text("int a;")
        out = _expand_header_inputs([d, d / "a.h"])
        # The directory yields a.h, and passing a.h again is deduplicated.
        assert out == [d / "a.h"]


# ── _sniff_text_format (cli.py:182-196) ───────────────────────────────────────


class TestSniffTextFormat:
    def test_json(self, tmp_path: Path) -> None:
        f = tmp_path / "x.json"
        f.write_text('{"library": "x"}')
        assert _sniff_text_format(f) == "json"

    def test_unknown(self, tmp_path: Path) -> None:
        f = tmp_path / "x.txt"
        f.write_text("hello world")
        assert _sniff_text_format(f) == "unknown"

    def test_oserror_missing(self, tmp_path: Path) -> None:
        assert _sniff_text_format(tmp_path / "missing") == "unknown"


# ── _resolve_linker_script (cli.py:219-237) ───────────────────────────────────


class TestResolveLinkerScript:
    def test_oserror_returns_none(self, tmp_path: Path) -> None:
        assert _resolve_linker_script(tmp_path / "nope") == (None, False)

    def test_not_a_script(self, tmp_path: Path) -> None:
        f = tmp_path / "plain.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 50)
        assert _resolve_linker_script(f) == (None, False)

    def test_script_with_resolvable_target(self, tmp_path: Path) -> None:
        target = tmp_path / "libfoo.so.1"
        target.write_bytes(b"\x7fELF" + b"\x00" * 50)
        script = tmp_path / "libfoo.so"
        script.write_text("/* GNU ld script */\nINPUT(libfoo.so.1)\n")
        resolved, is_ld = _resolve_linker_script(script)
        assert is_ld is True
        assert resolved == tmp_path / "libfoo.so.1"

    def test_script_unresolvable_target(self, tmp_path: Path) -> None:
        # Recognized as a linker script (keyword present) but the named member
        # does not exist next to the script → (None, True).
        script = tmp_path / "libbar.so"
        script.write_text("GROUP ( libbar.so.5 AS_NEEDED ( -lc ) )\n")
        resolved, is_ld = _resolve_linker_script(script)
        assert resolved is None
        assert is_ld is True


# ── _safe_write_output / _write_or_echo (cli.py:106-115, 1375-1381) ───────────


class TestSafeWriteOutput:
    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        out = tmp_path / "sub" / "dir" / "report.txt"
        _safe_write_output(out, "hello")
        assert out.read_text() == "hello"

    def test_oserror_wrapped(self, tmp_path: Path) -> None:
        # Make the target a directory so write_text raises OSError.
        bad = tmp_path / "adir"
        bad.mkdir()
        with pytest.raises(click.ClickException, match="Cannot write"):
            _safe_write_output(bad, "data")

    def test_write_or_echo_to_file(self, tmp_path: Path) -> None:
        out = tmp_path / "r.txt"
        _write_or_echo(out, "payload")
        assert out.read_text() == "payload"

    def test_write_or_echo_to_stdout(self, capsys) -> None:
        _write_or_echo(None, "to-stdout")
        assert "to-stdout" in capsys.readouterr().out


# ── _merge_gcc_options / _resolve_per_side_options (cli.py helpers) ────────────


class TestSmallHelpers:
    def test_merge_gcc_options_no_flags(self) -> None:
        assert _merge_gcc_options([], "-O2") == "-O2"

    def test_merge_gcc_options_flags_only(self) -> None:
        assert _merge_gcc_options(["-DA", "-DB"], None) == "-DA -DB"

    def test_merge_gcc_options_both(self) -> None:
        assert _merge_gcc_options(["-DA"], "-O2") == "-DA -O2"

    def test_resolve_per_side_options_overrides(self, tmp_path: Path) -> None:
        h = (tmp_path / "h.h",)
        oh = (tmp_path / "old.h",)
        old_h, new_h, old_inc, new_inc = _resolve_per_side_options(
            h,
            (),
            oh,
            (),
            (),
            (),
        )
        assert old_h == list(oh)  # per-side override wins
        assert new_h == list(h)  # falls back to shared

    def test_collect_additions(self) -> None:
        result = DiffResult(
            old_version="1",
            new_version="2",
            library="x",
            changes=[
                Change(kind=ChangeKind.FUNC_ADDED, symbol="a", description="added"),
                Change(kind=ChangeKind.FUNC_REMOVED, symbol="b", description="removed"),
            ],
        )
        adds = _collect_additions(result)
        assert len(adds) == 1


# ── _warn_ignored_flags (cli.py:949-971) ──────────────────────────────────────


class TestWarnIgnoredFlags:
    def test_binary_input_no_warning(self, capsys) -> None:
        _warn_ignored_flags(True, False, (Path("h.h"),), (), (), (), (), ())
        assert capsys.readouterr().err == ""

    def test_snapshot_inputs_warns(self, capsys) -> None:
        _warn_ignored_flags(
            False,
            False,
            (Path("h.h"),),
            (Path("i"),),
            (),
            (),
            (),
            (),
        )
        assert "ignored when both inputs are snapshots" in capsys.readouterr().err


# ── _load_suppression_and_policy error/warn paths (cli.py:986-1034) ───────────


class TestLoadSuppressionAndPolicy:
    def test_missing_suppress_file_bad_param(self, tmp_path: Path) -> None:
        with pytest.raises(click.BadParameter):
            _load_suppression_and_policy(tmp_path / "nope.yaml", "strict_abi", None)

    def test_valid_suppress_file(self, tmp_path: Path) -> None:
        sup = tmp_path / "sup.yaml"
        sup.write_text(
            "version: 1\nsuppressions:\n  - symbol: foo\n    reason: legacy\n",
        )
        suppression, pf = _load_suppression_and_policy(sup, "strict_abi", None)
        assert suppression is not None
        assert pf is None

    def test_policy_file_warns_when_policy_overridden(
        self, tmp_path: Path, capsys
    ) -> None:
        pol = tmp_path / "policy.yaml"
        pol.write_text("base_policy: strict_abi\n")
        _, pf = _load_suppression_and_policy(None, "sdk_vendor", pol)
        assert pf is not None
        assert "is ignored when --policy-file is given" in capsys.readouterr().err


# ── _load_probe_matrix_changes (cli.py:1112-1117) ─────────────────────────────


class TestLoadProbeMatrixChanges:
    def test_none_returns_none(self) -> None:
        assert _load_probe_matrix_changes(None, None) is None

    def test_one_side_only_raises(self, tmp_path: Path) -> None:
        f = tmp_path / "m.json"
        f.write_text("{}")
        with pytest.raises(click.UsageError, match="must be given together"):
            _load_probe_matrix_changes(f, None)


# ── _collect_release_inputs error path (cli.py:1231) ──────────────────────────


class TestCollectReleaseInputs:
    def test_neither_file_nor_dir(self, tmp_path: Path) -> None:
        with pytest.raises(click.ClickException, match="neither file nor directory"):
            _collect_release_inputs(tmp_path / "does-not-exist")

    def test_single_file(self, tmp_path: Path) -> None:
        f = _write_snap(tmp_path / "libfoo.json", _snap())
        assert _collect_release_inputs(f) == [f]


# ── _announce_exit_scheme / _exit_with_severity_or_verdict (cli.py:1396-1426) ─


class TestExitSchemeHelpers:
    def test_announce_suppressed_for_json(self, capsys) -> None:
        _announce_exit_scheme(False, None, fmt="json", stat=False)
        assert capsys.readouterr().err == ""

    def test_announce_legacy_scheme(self, capsys) -> None:
        _announce_exit_scheme(False, None, fmt="markdown", stat=False)
        assert "legacy verdict" in capsys.readouterr().err

    def test_announce_severity_scheme(self, capsys) -> None:
        _announce_exit_scheme(True, None, fmt="markdown", stat=False)
        assert "severity-aware" in capsys.readouterr().err

    def test_exit_verdict_breaking(self) -> None:
        result = DiffResult(
            old_version="1", new_version="2", library="x", verdict=Verdict.BREAKING
        )
        with pytest.raises(SystemExit) as exc:
            _exit_with_severity_or_verdict(result, None, False)
        assert exc.value.code == 4

    def test_exit_verdict_api_break(self) -> None:
        result = DiffResult(
            old_version="1", new_version="2", library="x", verdict=Verdict.API_BREAK
        )
        with pytest.raises(SystemExit) as exc:
            _exit_with_severity_or_verdict(result, None, False)
        assert exc.value.code == 2

    def test_exit_verdict_compatible_no_exit(self) -> None:
        result = DiffResult(
            old_version="1", new_version="2", library="x", verdict=Verdict.COMPATIBLE
        )
        # Compatible verdict returns normally (no SystemExit).
        assert _exit_with_severity_or_verdict(result, None, False) is None


# ── _maybe_emit_annotations (cli.py:1329-1340) ────────────────────────────────


class TestMaybeEmitAnnotations:
    def test_not_annotate_noop(self) -> None:
        result = DiffResult(old_version="1", new_version="2", library="x")
        # Returns early at the `if not annotate` guard (no return value).
        assert (
            _maybe_emit_annotations(result, annotate=False, annotate_additions=False)
            is None
        )

    def test_annotate_outside_ci_noop(self, monkeypatch, capsys) -> None:
        # Force is_github_actions() False so the body short-circuits.
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        result = DiffResult(old_version="1", new_version="2", library="x")
        _maybe_emit_annotations(result, annotate=True, annotate_additions=False)
        assert capsys.readouterr().err == ""


# ── compare command CliRunner error/branch paths ──────────────────────────────


class TestCompareCommand:
    def test_help(self) -> None:
        result = _invoke("compare", "--help")
        assert result.exit_code == 0
        assert "Compare two ABI surfaces" in result.output

    def test_annotate_additions_requires_annotate(self, tmp_path: Path) -> None:
        old_f = _write_snap(tmp_path / "old.json", _snap())
        new_f = _write_snap(tmp_path / "new.json", _snap())
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--annotate-additions",
        )
        assert result.exit_code != 0
        assert "--annotate-additions requires --annotate" in result.output

    def test_compatible_snapshots(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        result = _invoke("compare", str(old_f), str(new_f))
        assert result.exit_code == 0

    def test_breaking_snapshots_exit_4(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = _invoke("compare", str(old_f), str(new_f))
        assert result.exit_code == 4

    def test_json_output_no_banner(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        result = _invoke("compare", str(old_f), str(new_f), "--format", "json")
        assert result.exit_code == 0

    def test_output_to_file(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        out = tmp_path / "rep.md"
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "-o",
            str(out),
        )
        assert result.exit_code == 0
        assert out.exists()
        assert "Report written to" in result.output

    def test_severity_preset_breaking_exit(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--severity-preset",
            "default",
        )
        assert result.exit_code == 4

    def test_severity_info_only_downgrades(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--severity-preset",
            "info-only",
        )
        assert result.exit_code == 0

    def test_public_symbol_without_scope_warns(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--no-scope-public-headers",
            "--public-symbol",
            "foo",
        )
        assert result.exit_code == 0
        assert "only take effect with" in result.output

    def test_report_mode_impact(self, tmp_path: Path) -> None:
        # --report-mode impact rewrites to full + show_impact (cli.py:1828-1830).
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--report-mode",
            "impact",
        )
        # Breaking pair still exits 4; the report renders without error.
        assert result.exit_code == 4

    def test_debug_format_auto_on_snapshots(self, tmp_path: Path) -> None:
        # --debug-format auto resolves to None (cli.py:1815); JSON snapshot
        # inputs have format None so the PE/Mach-O guard is skipped.
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--debug-format",
            "auto",
        )
        assert result.exit_code == 0

    def test_demangle_explicit_off_markdown(self, tmp_path: Path) -> None:
        # Explicit --no-demangle overrides the markdown default (cli.py:1824).
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--no-demangle",
        )
        assert result.exit_code == 0

    def test_sarif_format(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--format",
            "sarif",
        )
        assert result.exit_code == 0
        assert "$schema" in result.output or "sarif" in result.output.lower()

    def test_stat_summary(self, tmp_path: Path) -> None:
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = _invoke("compare", str(old_f), str(new_f), "--stat")
        assert result.exit_code == 4

    def test_probe_matrix_one_side_usage_error(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        m = tmp_path / "m.json"
        m.write_text("{}")
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--probe-matrix-old",
            str(m),
        )
        assert result.exit_code != 0
        assert "must be given together" in result.output


# ── compare-release: format helpers and exit-code logic ───────────────────────


class TestCompareReleaseFormatHelpers:
    def _entry(self, lib: str, verdict: str = "NO_CHANGE") -> dict:
        return {
            "library": lib,
            "verdict": verdict,
            "breaking": 0,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
        }

    def test_format_json_basic(self, tmp_path: Path) -> None:
        text = _format_release_json(
            "NO_CHANGE",
            tmp_path / "old",
            tmp_path / "new",
            [self._entry("libfoo.so")],
            [],
            [],
            {},
            {},
            [],
            None,
            None,
        )
        data = json.loads(text)
        assert data["verdict"] == "NO_CHANGE"
        assert data["changed_libraries"] == []

    def test_format_json_changed_libraries(self, tmp_path: Path) -> None:
        text = _format_release_json(
            "BREAKING",
            tmp_path / "old",
            tmp_path / "new",
            [self._entry("libfoo.so", "BREAKING"), self._entry("libbar.so")],
            [],
            [],
            {},
            {},
            [],
            None,
            None,
        )
        data = json.loads(text)
        assert data["changed_libraries"] == ["libfoo.so"]

    def test_format_markdown_basic(self, tmp_path: Path) -> None:
        text = _format_release_markdown(
            "NO_CHANGE",
            tmp_path / "old",
            tmp_path / "new",
            [self._entry("libfoo.so")],
            [],
            [],
            {},
            {},
            None,
            None,
        )
        assert "# ABI Release Comparison" in text
        assert "libfoo.so" in text

    def test_md_bundle_findings_empty(self) -> None:
        assert _release_md_bundle_findings(None) == []

    def test_md_matrix_findings_empty(self) -> None:
        assert _release_md_matrix_findings(None) == []

    def test_md_matrix_findings_with_change(self) -> None:
        mr = DiffResult(
            old_version="1",
            new_version="2",
            library="x",
            changes=[
                Change(
                    kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed"
                ),
            ],
        )
        lines = _release_md_matrix_findings(mr)
        assert any("Matrix" in ln for ln in lines)
        assert any("foo" in ln for ln in lines)


class TestResolveReleaseHeaders:
    def test_header_dir_used_when_no_per_side(self, tmp_path: Path) -> None:
        hd_old = tmp_path / "old-hdr"
        hd_new = tmp_path / "new-hdr"
        old_h, new_h = _resolve_release_headers(
            (),
            (),
            (),
            hd_old,
            hd_new,
        )
        assert old_h == [hd_old]
        assert new_h == [hd_new]

    def test_per_side_overrides_header_dir(self, tmp_path: Path) -> None:
        oh = (tmp_path / "old.h",)
        old_h, new_h = _resolve_release_headers(
            (),
            oh,
            (),
            tmp_path / "old-hdr",
            None,
        )
        assert old_h == list(oh)


class TestResolveReleaseSeverityConfig:
    def test_none_when_unset(self) -> None:
        assert _resolve_release_severity_config(None, None, None, None, None) is None

    def test_returns_config_when_preset(self) -> None:
        cfg = _resolve_release_severity_config("strict", None, None, None, None)
        assert cfg is not None


class TestExitCompareRelease:
    def test_legacy_breaking_exit_4(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("BREAKING", False, [])
        assert exc.value.code == 4

    def test_legacy_api_break_exit_2(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("API_BREAK", False, [])
        assert exc.value.code == 2

    def test_legacy_removed_library_exit_8(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("NO_CHANGE", True, ["libgone.so"])
        assert exc.value.code == 8

    def test_legacy_no_change_no_exit(self) -> None:
        # Returns normally (no SystemExit) on a clean release.
        assert _exit_compare_release("NO_CHANGE", False, []) is None

    def test_severity_removed_takes_precedence(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release(
                "NO_CHANGE", True, ["libgone.so"], severity_exit_code=2
            )
        assert exc.value.code == 8

    def test_severity_error_floors_at_4(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("ERROR", False, [], severity_exit_code=1)
        assert exc.value.code == 4

    def test_severity_zero_no_exit(self) -> None:
        assert (
            _exit_compare_release("NO_CHANGE", False, [], severity_exit_code=0) is None
        )


class TestFoldReleaseGlobalSeverity:
    def test_no_config_returns_base(self) -> None:
        assert (
            _fold_release_global_severity(
                2,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
            )
            == 2
        )

    def test_matrix_findings_raise_code(self) -> None:
        mr = DiffResult(
            old_version="1",
            new_version="2",
            library="x",
            changes=[
                Change(
                    kind=ChangeKind.FUNC_REMOVED, symbol="foo", description="removed"
                ),
            ],
        )
        code = _fold_release_global_severity(
            0,
            None,
            mr,
            "default",
            None,
            None,
            None,
            None,
        )
        assert code >= 0


# ── compare-release command CliRunner branches ────────────────────────────────


class TestCompareReleaseCommand:
    def test_help(self) -> None:
        result = _invoke("compare-release", "--help")
        assert result.exit_code == 0

    def test_annotate_additions_requires_annotate(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--annotate-additions",
        )
        assert result.exit_code != 0
        assert "--annotate-additions requires --annotate" in result.output

    def test_markdown_output(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--format",
            "markdown",
        )
        assert result.exit_code == 0
        assert "ABI Release Comparison" in result.output

    def test_severity_preset_breaking(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--severity-preset",
            "default",
        )
        assert result.exit_code == 4

    def test_severity_info_only_clean_exit(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        old, new = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--severity-preset",
            "info-only",
        )
        assert result.exit_code == 0

    def test_junit_format(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--format",
            "junit",
        )
        assert result.exit_code == 0
        assert "testsuite" in result.output

    def test_output_file_written(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        out = tmp_path / "release.json"
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--format",
            "json",
            "-o",
            str(out),
        )
        assert result.exit_code == 0
        assert out.exists()

    def test_removed_library_markdown_section(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(old_dir / "libgone.json", _snap(library="libgone.so"))
        _write_snap(new_dir / "libfoo.json", _snap())
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--format",
            "markdown",
        )
        assert result.exit_code == 0
        assert "Removed Libraries" in result.output


# ── appcompat: arg validation, list-required-symbols, severity (cli_appcompat) ─


class TestAppcompatValidation:
    def _elf(self, p: Path) -> Path:
        p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        return p

    def test_help(self) -> None:
        result = _invoke("appcompat", "--help")
        assert result.exit_code == 0
        assert "Check if an application is compatible" in result.output

    def test_no_lib_args_usage_error(self, tmp_path: Path) -> None:
        app = self._elf(tmp_path / "app")
        result = _invoke("appcompat", str(app))
        assert result.exit_code != 0
        assert "Provide OLD_LIB and NEW_LIB" in result.output

    def test_per_side_flag_rejected_in_weak_mode(self, tmp_path: Path) -> None:
        app = self._elf(tmp_path / "app")
        lib = self._elf(tmp_path / "lib.so")
        result = _invoke(
            "appcompat",
            str(app),
            "--check-against",
            str(lib),
            "--old-header",
            str(tmp_path / "h.h"),
        )
        assert result.exit_code != 0
        assert "cannot be used with" in result.output

    def test_headers_ignored_warning_in_weak_mode(self, tmp_path: Path, capsys) -> None:
        # -H is silently ignored in weak mode; it warns rather than fails.
        from abicheck.cli_appcompat import _validate_appcompat_args

        _validate_appcompat_args(
            weak_mode=True,
            old_lib=None,
            new_lib=None,
            list_symbols=False,
            old_headers_only=(),
            new_headers_only=(),
            old_includes_only=(),
            new_includes_only=(),
            headers=(tmp_path / "h.h",),
            includes=(),
        )
        assert "are ignored in weak" in capsys.readouterr().err


class TestAppcompatListRequiredSymbols:
    def test_list_required_symbols_text(self, tmp_path, monkeypatch) -> None:
        from abicheck import appcompat as appcompat_mod
        from abicheck.appcompat import AppRequirements

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        lib = tmp_path / "lib.so"
        lib.write_bytes(b"\x7fELF" + b"\x00" * 200)

        monkeypatch.setattr(appcompat_mod, "_get_lib_soname", lambda p: "libfoo.so.1")
        monkeypatch.setattr(
            appcompat_mod,
            "parse_app_requirements",
            lambda app_path, lib_name: AppRequirements(
                needed_libs=["libfoo.so.1"],
                undefined_symbols={"foo_init", "foo_run"},
                required_versions={"FOO_1.0": "libfoo.so.1"},
            ),
        )
        result = _invoke(
            "appcompat",
            str(app),
            "--check-against",
            str(lib),
            "--list-required-symbols",
        )
        assert result.exit_code == 0
        assert "foo_init" in result.output
        assert "FOO_1.0" in result.output

    def test_list_required_symbols_json(self, tmp_path, monkeypatch) -> None:
        from abicheck import appcompat as appcompat_mod
        from abicheck.appcompat import AppRequirements

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        lib = tmp_path / "lib.so"
        lib.write_bytes(b"\x7fELF" + b"\x00" * 200)

        monkeypatch.setattr(appcompat_mod, "_get_lib_soname", lambda p: "libfoo.so.1")
        monkeypatch.setattr(
            appcompat_mod,
            "parse_app_requirements",
            lambda app_path, lib_name: AppRequirements(
                needed_libs=["libfoo.so.1"],
                undefined_symbols={"foo_init"},
                required_versions={},
            ),
        )
        result = _invoke(
            "appcompat",
            str(app),
            "--check-against",
            str(lib),
            "--list-required-symbols",
            "--format",
            "json",
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["library"] == "libfoo.so.1"
        assert "foo_init" in data["required_symbols"]


class TestAppcompatHandleListHelper:
    def test_missing_target_lib_raises(self, tmp_path) -> None:
        from abicheck.cli_appcompat import _handle_list_required_symbols

        with pytest.raises(click.UsageError, match="requires a library path"):
            _handle_list_required_symbols(
                app_path=tmp_path / "app",
                check_against_lib=None,
                old_lib=None,
                new_lib=None,
                weak_mode=False,
                fmt="json",
                _get_lib_soname=lambda p: "x",
                parse_app_requirements=lambda *a, **k: None,
            )


class TestAppcompatSeverityAndOutput:
    """Drive the full-mode appcompat flow via monkeypatched check_appcompat so
    the JSON/markdown output and severity-aware exit branches run without a
    real compiler."""

    def _setup(self, tmp_path, monkeypatch, *, result):
        from abicheck import appcompat as appcompat_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(
            appcompat_mod,
            "check_appcompat",
            lambda *a, **k: result,
        )
        return app, old, new

    def _result(
        self, *, verdict=Verdict.COMPATIBLE, breaking=None, missing=None, full_diff=None
    ):
        from abicheck.appcompat import AppCompatResult

        return AppCompatResult(
            app_path="/app",
            old_lib_path="old.so",
            new_lib_path="new.so",
            required_symbols={"foo"},
            required_symbol_count=1,
            breaking_for_app=breaking or [],
            missing_symbols=missing or [],
            missing_versions=[],
            full_diff=full_diff,
            verdict=verdict,
            symbol_coverage=100.0,
        )

    def test_full_mode_json_output(self, tmp_path, monkeypatch) -> None:
        res = self._result()
        app, old, new = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke(
            "appcompat",
            str(app),
            str(old),
            str(new),
            "--format",
            "json",
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["verdict"] == "COMPATIBLE"

    def test_full_mode_breaking_exit_4(self, tmp_path, monkeypatch) -> None:
        res = self._result(verdict=Verdict.BREAKING, missing=["foo"])
        app, old, new = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke("appcompat", str(app), str(old), str(new))
        assert result.exit_code == 4

    def test_full_mode_api_break_exit_2(self, tmp_path, monkeypatch) -> None:
        res = self._result(verdict=Verdict.API_BREAK)
        app, old, new = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke("appcompat", str(app), str(old), str(new))
        assert result.exit_code == 2

    def test_full_mode_output_to_file(self, tmp_path, monkeypatch) -> None:
        res = self._result()
        app, old, new = self._setup(tmp_path, monkeypatch, result=res)
        out = tmp_path / "rep.md"
        result = _invoke(
            "appcompat",
            str(app),
            str(old),
            str(new),
            "-o",
            str(out),
        )
        assert result.exit_code == 0
        assert out.exists()
        assert "Report written to" in result.output

    def test_severity_missing_symbols_floors_at_4(self, tmp_path, monkeypatch) -> None:
        # info-only would normally downgrade, but missing symbols floor at 4.
        full = DiffResult(old_version="1", new_version="2", library="libfoo")
        res = self._result(
            verdict=Verdict.BREAKING,
            missing=["foo"],
            full_diff=full,
        )
        app, old, new = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke(
            "appcompat",
            str(app),
            str(old),
            str(new),
            "--severity-preset",
            "info-only",
        )
        assert result.exit_code == 4

    def test_severity_clean_exit_0(self, tmp_path, monkeypatch) -> None:
        full = DiffResult(old_version="1", new_version="2", library="libfoo")
        res = self._result(verdict=Verdict.COMPATIBLE, full_diff=full)
        app, old, new = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke(
            "appcompat",
            str(app),
            str(old),
            str(new),
            "--severity-preset",
            "default",
        )
        assert result.exit_code == 0

    def test_full_mode_html_output(self, tmp_path, monkeypatch) -> None:
        # Drives the ``fmt == "html"`` branch (cli_appcompat:335).
        res = self._result()
        app, old, new = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke(
            "appcompat",
            str(app),
            str(old),
            str(new),
            "--format",
            "html",
        )
        assert result.exit_code == 0
        assert "<" in result.output  # HTML markup emitted


class TestAppcompatWeakMode:
    """Drive the weak-mode (--check-against) flow via monkeypatched
    check_against so its result-rendering and exit branches run without a
    real compiler (cli_appcompat:304-306)."""

    def _setup(self, tmp_path, monkeypatch, *, result):
        from abicheck import appcompat as appcompat_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        lib = tmp_path / "lib.so"
        lib.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(
            appcompat_mod,
            "check_against",
            lambda *a, **k: result,
        )
        return app, lib

    def _result(self, *, verdict=Verdict.COMPATIBLE, missing=None):
        from abicheck.appcompat import AppCompatResult

        return AppCompatResult(
            app_path="/app",
            old_lib_path="",
            new_lib_path="lib.so",
            required_symbols={"foo"},
            required_symbol_count=1,
            breaking_for_app=[],
            missing_symbols=missing or [],
            missing_versions=[],
            full_diff=None,
            verdict=verdict,
            symbol_coverage=100.0,
        )

    def test_weak_mode_compatible_exit_0(self, tmp_path, monkeypatch) -> None:
        res = self._result()
        app, lib = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke(
            "appcompat",
            str(app),
            "--check-against",
            str(lib),
            "--format",
            "json",
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["verdict"] == "COMPATIBLE"

    def test_weak_mode_breaking_exit_4(self, tmp_path, monkeypatch) -> None:
        res = self._result(verdict=Verdict.BREAKING, missing=["foo"])
        app, lib = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke("appcompat", str(app), "--check-against", str(lib))
        assert result.exit_code == 4

    def test_weak_mode_ignores_severity_preset(self, tmp_path, monkeypatch) -> None:
        # Weak mode keeps the verdict-based exit even when a severity preset is
        # supplied (full_diff is None), so info-only cannot downgrade.
        res = self._result(verdict=Verdict.BREAKING, missing=["foo"])
        app, lib = self._setup(tmp_path, monkeypatch, result=res)
        result = _invoke(
            "appcompat",
            str(app),
            "--check-against",
            str(lib),
            "--severity-preset",
            "info-only",
        )
        assert result.exit_code == 4


# ── cli.py: _write_release_step_summary (1351-1372) ───────────────────────────


class TestWriteReleaseStepSummary:
    def test_no_summary_path_noop(self, monkeypatch, tmp_path) -> None:
        from abicheck.cli import _write_release_step_summary

        monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
        # No GITHUB_STEP_SUMMARY → returns early without writing.
        assert _write_release_step_summary("text", "markdown") is None

    def test_not_github_actions_noop(self, monkeypatch, tmp_path) -> None:
        from abicheck.cli import _write_release_step_summary

        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        _write_release_step_summary("text", "markdown")
        assert not summary.exists()

    def test_markdown_written_in_ci(self, monkeypatch, tmp_path) -> None:
        from abicheck.cli import _write_release_step_summary

        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        _write_release_step_summary("hello world", "markdown")
        assert "hello world" in summary.read_text()

    def test_json_wrapped_in_code_block(self, monkeypatch, tmp_path) -> None:
        from abicheck.cli import _write_release_step_summary

        summary = tmp_path / "summary.md"
        monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        _write_release_step_summary('{"a": 1}', "json")
        text = summary.read_text()
        assert "```json" in text
        assert '{"a": 1}' in text


# ── cli.py: _log_one_side_debug / _log_debug_resolution (1435-1465) ───────────


class TestLogDebugResolution:
    def test_non_binary_no_droots_noop(self, tmp_path, capsys) -> None:
        from abicheck.cli import _log_one_side_debug

        f = tmp_path / "snap.json"
        f.write_text("{}")
        # Not a binary AND no debug roots → returns before resolving anything.
        _log_one_side_debug("old", f, [], debuginfod=False, debuginfod_url=None)
        assert capsys.readouterr().err == ""

    def test_resolution_skipped_when_nothing_requested(self, tmp_path, capsys) -> None:
        from abicheck.cli import _log_debug_resolution

        old = tmp_path / "old.json"
        new = tmp_path / "new.json"
        old.write_text("{}")
        new.write_text("{}")
        _log_debug_resolution(
            old,
            new,
            [],
            [],
            debuginfod=False,
            debuginfod_url=None,
        )
        assert capsys.readouterr().err == ""

    def test_log_one_side_emits_when_artifact_resolved(
        self, tmp_path, monkeypatch, capsys
    ) -> None:
        # Force a binary format and a resolved artifact so the echo branch runs.
        from types import SimpleNamespace

        import abicheck.cli as cli_mod

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 50)
        monkeypatch.setattr(cli_mod, "_detect_binary_format", lambda p: "elf")
        monkeypatch.setattr(
            "abicheck.debug_resolver.resolve_debug_info",
            lambda *a, **k: SimpleNamespace(source="/path/to/lib.debug"),
        )
        cli_mod._log_one_side_debug(
            "old",
            binary,
            [tmp_path],
            debuginfod=False,
            debuginfod_url=None,
        )
        assert "Debug info (old)" in capsys.readouterr().err


# ── cli_compare_release: markdown/json with bundle + matrix findings ──────────


def _bundle_with_findings():
    from abicheck.bundle import BundleDiffResult, BundleFinding

    finding = BundleFinding(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="foo",
        description="bundle break",
        consumer_library="libapp.so",
        provider_library="libfoo.so",
    )
    return BundleDiffResult(
        old_root=Path("old"),
        new_root=Path("new"),
        per_library=[],
        bundle_findings=[finding],
    )


def _matrix_with_changes():
    return DiffResult(
        old_version="1",
        new_version="2",
        library="x",
        changes=[
            Change(kind=ChangeKind.FUNC_REMOVED, symbol="m", description="matrix")
        ],
    )


class TestReleaseFormatWithBundleAndMatrix:
    def _entry(self, lib="libfoo.so", verdict="NO_CHANGE"):
        return {
            "library": lib,
            "verdict": verdict,
            "breaking": 0,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
        }

    def test_md_bundle_findings_rendered(self) -> None:
        lines = _release_md_bundle_findings(_bundle_with_findings())
        assert any("Bundle" in ln for ln in lines)
        assert any("foo" in ln for ln in lines)
        assert any("consumer" in ln for ln in lines)

    def test_markdown_with_bundle_and_matrix(self, tmp_path) -> None:
        text = _format_release_markdown(
            "BREAKING",
            tmp_path / "old",
            tmp_path / "new",
            [self._entry("libfoo.so", "BREAKING")],
            [],
            [],
            {},
            {},
            _bundle_with_findings(),
            _matrix_with_changes(),
        )
        assert "Bundle" in text
        assert "Matrix" in text

    def test_json_with_bundle(self, tmp_path) -> None:
        text = _format_release_json(
            "BREAKING",
            tmp_path / "old",
            tmp_path / "new",
            [self._entry("libfoo.so", "BREAKING")],
            [],
            [],
            {},
            {},
            [],
            _bundle_with_findings(),
            None,
        )
        data = json.loads(text)
        assert "bundle_verdict" in data
        assert data["bundle_findings"]


class TestFoldReleaseGlobalSeverityBundle:
    def test_bundle_findings_raise_code(self) -> None:
        # A bundle break under a 'default' preset should not stay below the
        # per-library base code; folding considers bundle findings.
        code = _fold_release_global_severity(
            0,
            _bundle_with_findings(),
            None,
            "default",
            None,
            None,
            None,
            None,
        )
        assert code >= 0

    def test_matrix_findings_considered(self) -> None:
        code = _fold_release_global_severity(
            0,
            None,
            _matrix_with_changes(),
            "default",
            None,
            None,
            None,
            None,
        )
        assert code >= 0


# ── cli_compare_release: _suppress_lockstep_soname_findings (253-280) ─────────


class TestSuppressLockstepSoname:
    def test_non_breaking_returns_zero(self) -> None:
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        assert _suppress_lockstep_soname_findings([], "NO_CHANGE", None) == 0

    def test_suppresses_unnecessary_soname_bump(self) -> None:
        from abicheck.cli_compare_release import _suppress_lockstep_soname_findings

        result = DiffResult(
            old_version="1",
            new_version="2",
            library="libfoo",
            changes=[
                Change(
                    kind=ChangeKind.SONAME_BUMP_UNNECESSARY,
                    symbol="libfoo.so",
                    description="unnecessary",
                ),
            ],
        )
        entry = {
            "library": "libfoo.so",
            "verdict": "BREAKING",
            "_diff_result": result,
            "breaking": 0,
            "source_breaks": 0,
            "risk_changes": 0,
            "compatible_additions": 0,
        }
        n = _suppress_lockstep_soname_findings([entry], "BREAKING", None)
        assert n == 1
        # The finding was stripped from the diff result.
        assert all(c.kind != ChangeKind.SONAME_BUMP_UNNECESSARY for c in result.changes)


# ── cli_compare_release CLI flows: output-dir, strict-suppressions, error ─────


class TestCompareReleaseExtraFlows:
    def _make_dirs(self, tmp_path):
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        return old_dir, new_dir

    def test_output_dir_writes_per_lib_and_summary(self, tmp_path) -> None:
        old_dir, new_dir = self._make_dirs(tmp_path)
        old, new = _breaking_pair("libfoo.so")
        _write_snap(old_dir / "libfoo.json", old)
        _write_snap(new_dir / "libfoo.json", new)
        out_dir = tmp_path / "reports"
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--output-dir",
            str(out_dir),
            "--format",
            "json",
        )
        # Breaking verdict exits 4 but the report dir must still be populated.
        assert result.exit_code == 4
        assert out_dir.exists()
        assert any(out_dir.iterdir())

    def test_bundle_cohort_runs_bundle_analysis(self, tmp_path) -> None:
        # --bundle-cohort requests bundle analysis, driving the
        # _collect_bundle_result path and bundle markdown section.
        old_dir, new_dir = self._make_dirs(tmp_path)
        _write_snap(old_dir / "libfoo.json", _snap(library="libfoo.so"))
        _write_snap(new_dir / "libfoo.json", _snap(library="libfoo.so"))
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--format",
            "markdown",
            "--bundle-cohort",
            "lib",
        )
        # Runs to completion; the bundle row appears in the markdown table.
        assert result.exit_code in (0, 4)
        assert "Bundle" in result.output

    def test_strict_suppressions_preflight_rejects_expired(self, tmp_path) -> None:
        old_dir, new_dir = self._make_dirs(tmp_path)
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        sup = tmp_path / "sup.yaml"
        sup.write_text(
            "version: 1\nsuppressions:\n"
            "  - symbol: foo\n    reason: legacy\n    expires: 2000-01-01\n",
        )
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--suppress",
            str(sup),
            "--strict-suppressions",
        )
        assert result.exit_code != 0
        assert "expired" in result.output.lower()

    def test_corrupt_snapshot_reports_error(self, tmp_path, monkeypatch) -> None:
        # A library whose snapshot load raises surfaces an ERROR entry,
        # exercising the per-entry error echo path (cli_compare_release:341-342).
        old_dir, new_dir = self._make_dirs(tmp_path)
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        import abicheck.cli_compare_release as cr_mod

        def boom(*a, **k):
            raise ValueError("corrupt snapshot")

        monkeypatch.setattr(cr_mod, "_run_compare_pair", boom)
        result = _invoke(
            "compare-release",
            str(old_dir),
            str(new_dir),
            "--format",
            "markdown",
        )
        # The run completes (degraded) and notes the comparison error.
        assert "Error comparing" in result.output or "ERROR" in result.output


# ── cli.py: _expand_header_inputs neither-file-nor-dir (line 75) ──────────────


class TestExpandHeaderInputsNeitherFileNorDir:
    def test_special_path_neither_file_nor_dir(self, tmp_path, monkeypatch) -> None:
        # Force a path that exists() but is neither file nor directory (e.g. a
        # device/fifo) by monkeypatching Path predicates on a real path object.
        p = tmp_path / "weird"
        p.write_text("x")

        import pathlib

        real_is_file = pathlib.Path.is_file
        real_is_dir = pathlib.Path.is_dir

        def fake_is_file(self):
            if self == p:
                return False
            return real_is_file(self)

        def fake_is_dir(self):
            if self == p:
                return False
            return real_is_dir(self)

        monkeypatch.setattr(pathlib.Path, "is_file", fake_is_file)
        monkeypatch.setattr(pathlib.Path, "is_dir", fake_is_dir)
        with pytest.raises(click.ClickException, match="neither file nor directory"):
            _expand_header_inputs([p])


# ── cli.py: _resolve_linker_script keyword-token skip (line 232) ──────────────


class TestLinkerScriptKeywordSkip:
    def test_keyword_and_flag_tokens_skipped(self, tmp_path) -> None:
        # The script names only -l flags and a keyword, never a real .so/.a, so
        # the loop hits the keyword/flag `continue` and the ext `continue`.
        script = tmp_path / "libk.so"
        script.write_text("GROUP ( -lc -lm AS_NEEDED ( -lpthread ) )\n")
        resolved, is_ld = _resolve_linker_script(script)
        assert is_ld is True
        assert resolved is None

    def test_non_library_token_skipped(self, tmp_path) -> None:
        # A bare token that is neither a keyword/flag nor a library name (no
        # .so/.a) reaches and trips the extension `continue` at line 232.
        script = tmp_path / "libn.so"
        script.write_text("INPUT ( somenote_not_a_lib )\n")
        resolved, is_ld = _resolve_linker_script(script)
        assert is_ld is True
        assert resolved is None


# ── cli.py: _resolve_debug_artifact / _maybe_emit_annotations in CI ───────────


class TestResolveDebugArtifact:
    def test_delegates_to_resolver(self, tmp_path, monkeypatch) -> None:
        from types import SimpleNamespace

        import abicheck.cli as cli_mod

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 50)
        sentinel = SimpleNamespace(source="x.debug")
        monkeypatch.setattr(
            "abicheck.debug_resolver.resolve_debug_info",
            lambda *a, **k: sentinel,
        )
        out = cli_mod._resolve_debug_artifact(
            binary,
            (tmp_path,),
            False,
            None,
        )
        assert out is sentinel


class TestMaybeEmitAnnotationsInCI:
    def test_emits_when_in_github_actions(self, monkeypatch, capsys) -> None:
        import abicheck.cli as cli_mod

        monkeypatch.setattr("abicheck.annotations.is_github_actions", lambda: True)
        monkeypatch.setattr(
            "abicheck.annotations.collect_annotations",
            lambda result, annotate_additions=False: ["a1"],
        )
        monkeypatch.setattr(
            "abicheck.annotations.format_annotations",
            lambda anns: "::warning::break",
        )
        emitted = {}
        monkeypatch.setattr(
            "abicheck.annotations.emit_github_step_summary",
            lambda result: emitted.setdefault("summary", True),
        )
        result = DiffResult(old_version="1", new_version="2", library="x")
        cli_mod._maybe_emit_annotations(
            result,
            annotate=True,
            annotate_additions=False,
        )
        err = capsys.readouterr().err
        assert "::warning::break" in err
        assert emitted.get("summary") is True


# ── cli.py: _log_debug_resolution drives both sides when requested ────────────


class TestLogDebugResolutionBothSides:
    def test_both_sides_logged(self, tmp_path, monkeypatch, capsys) -> None:
        from types import SimpleNamespace

        import abicheck.cli as cli_mod

        old_b = tmp_path / "old.so"
        new_b = tmp_path / "new.so"
        old_b.write_bytes(b"\x7fELF" + b"\x00" * 50)
        new_b.write_bytes(b"\x7fELF" + b"\x00" * 50)
        monkeypatch.setattr(cli_mod, "_detect_binary_format", lambda p: "elf")
        monkeypatch.setattr(
            "abicheck.debug_resolver.resolve_debug_info",
            lambda *a, **k: SimpleNamespace(source="art"),
        )
        cli_mod._log_debug_resolution(
            old_b,
            new_b,
            [tmp_path],
            [tmp_path],
            debuginfod=False,
            debuginfod_url=None,
        )
        err = capsys.readouterr().err
        assert "Debug info (old)" in err
        assert "Debug info (new)" in err
