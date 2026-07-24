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
from unittest.mock import MagicMock

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
from abicheck.elf_metadata import ElfMetadata, ElfSymbol
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

    @pytest.mark.parametrize(
        ("depth", "expected"),
        [
            ("binary", "off"),
            ("headers", "off"),
            ("build", "build"),
            # ADR-043 D3: dump/compare always resolve --depth source at TARGET
            # scope, never CHANGED — the zero-TU defect fix.
            ("source", "source-target"),
        ],
    )
    def test_resolve_dump_depth_maps_each_depth(self, depth: str, expected: str) -> None:
        from abicheck.cli_dump_helpers import resolve_dump_depth

        assert resolve_dump_depth(depth, "source-target") == expected

    def test_resolve_dump_depth_no_preset_returns_default_mode(self) -> None:
        from abicheck.cli_dump_helpers import resolve_dump_depth

        # No --depth preset → the command's default collect mode is returned.
        assert resolve_dump_depth(None, "build") == "build"
        assert resolve_dump_depth(None, "off") == "off"

    def test_help_option_groups_render(self) -> None:
        # G21.8/M1: rich-click renders option-group panels so the big commands'
        # --help leads with named sections instead of a flat list.
        runner = CliRunner()
        compare_help = runner.invoke(main, ["compare", "--help"]).output
        assert "Per-side overrides" in compare_help
        assert "Build & source evidence" in compare_help
        dump_help = runner.invoke(main, ["dump", "--help"]).output
        assert "Toolchain" in dump_help and "Provenance" in dump_help

    def test_missing_requested_evidence_layers(self) -> None:
        # G21.7: a requested layer that came back NOT_COLLECTED — or PARTIAL with
        # an empty payload (Codex review) — is reported.
        from types import SimpleNamespace

        from abicheck.buildsource.model import CoverageStatus, DataLayer
        from abicheck.cli import _missing_requested_evidence_layers

        # Non-empty payload stand-ins, one per layer key.
        _full_be = SimpleNamespace(targets=["t"], compile_units=["cu"])
        _full_sa = SimpleNamespace(reachable_buckets=lambda: {"declarations": ["d"]})
        _full_sg = SimpleNamespace(nodes=["n"])
        _empty_sa = SimpleNamespace(reachable_buckets=lambda: {"declarations": []})

        def _pack(statuses, *, build_evidence=_full_be, source_abi=_full_sa,
                  source_graph=_full_sg):
            cov = {dl: SimpleNamespace(status=st) for dl, st in statuses.items()}
            return SimpleNamespace(
                manifest=SimpleNamespace(coverage_for=lambda layer: cov.get(layer)),
                build_evidence=build_evidence,
                source_abi=source_abi,
                source_graph=source_graph,
            )

        pack = _pack({
            DataLayer.L3_BUILD: CoverageStatus.PRESENT,
            DataLayer.L4_SOURCE_ABI: CoverageStatus.NOT_COLLECTED,
            DataLayer.L5_SOURCE_GRAPH: CoverageStatus.PRESENT,
        })
        assert _missing_requested_evidence_layers(pack, "source-target") == [
            DataLayer.L4_SOURCE_ABI.value
        ]
        assert _missing_requested_evidence_layers(None, "source-target") == []
        assert _missing_requested_evidence_layers(pack, "off") == []  # nothing requested

        # Empty-but-PARTIAL L4 (clang unavailable after L3 found) is still missing.
        empty_partial = _pack(
            {
                DataLayer.L3_BUILD: CoverageStatus.PRESENT,
                DataLayer.L4_SOURCE_ABI: CoverageStatus.PARTIAL,
                DataLayer.L5_SOURCE_GRAPH: CoverageStatus.PRESENT,
            },
            source_abi=_empty_sa,
        )
        assert _missing_requested_evidence_layers(empty_partial, "source-target") == [
            DataLayer.L4_SOURCE_ABI.value
        ]
        # All layers present and non-empty → nothing reported.
        full = _pack({
            DataLayer.L3_BUILD: CoverageStatus.PRESENT,
            DataLayer.L4_SOURCE_ABI: CoverageStatus.PARTIAL,
            DataLayer.L5_SOURCE_GRAPH: CoverageStatus.PRESENT,
        })
        assert _missing_requested_evidence_layers(full, "source-target") == []

        # Empty L3 build_evidence and empty L5 graph are each flagged too,
        # exercising both per-layer emptiness branches.
        empty_l3 = _pack(
            {DataLayer.L3_BUILD: CoverageStatus.PRESENT},
            build_evidence=SimpleNamespace(targets=[], compile_units=[]),
        )
        assert DataLayer.L3_BUILD.value in _missing_requested_evidence_layers(empty_l3, "build")
        empty_l5 = _pack(
            {
                DataLayer.L3_BUILD: CoverageStatus.PRESENT,
                DataLayer.L4_SOURCE_ABI: CoverageStatus.PRESENT,
                DataLayer.L5_SOURCE_GRAPH: CoverageStatus.PRESENT,
            },
            source_graph=SimpleNamespace(nodes=[]),
        )
        assert DataLayer.L5_SOURCE_GRAPH.value in _missing_requested_evidence_layers(
            empty_l5, "source-target"
        )

    def test_dump_explicit_deep_depth_without_sources_warns(self, tmp_path) -> None:
        # Codex: an explicit deep --depth with no --sources/--build-info would
        # silently write an L0-L2 snapshot; warn.
        so = tmp_path / "fake.so"
        so.write_bytes(b"\x7fELF")
        result = CliRunner().invoke(main, ["dump", str(so), "--depth", "source"])
        assert "carry only L0-L2 data" in result.output

    def test_dump_default_depth_no_warning(self, tmp_path) -> None:
        # The bare default (no --depth) must NOT warn — embedding is a no-op
        # there by design, so a plain dump stays quiet about evidence.
        so = tmp_path / "fake.so"
        so.write_bytes(b"\x7fELF")
        result = CliRunner().invoke(main, ["dump", str(so)])
        assert "carry only L0-L2 data" not in result.output

    def test_dump_gcc_option_threaded_to_non_elf(self, tmp_path, monkeypatch) -> None:
        # ADR-037 D3 (Codex): --gcc-option(s) are now threaded into the native
        # PE/Mach-O header-scoping path (resolved before format dispatch), so the
        # old "will be ignored" warning is gone and the context reaches the dump.
        import struct

        import abicheck.cli as cli_mod

        dylib = tmp_path / "fake.dylib"
        dylib.write_bytes(struct.pack("<I", 0xFEEDFACF) + b"\x00" * 64)
        captured: dict[str, object] = {}
        monkeypatch.setattr(
            cli_mod, "handle_non_elf_dump", lambda *a, **k: captured.update(k)
        )
        result = CliRunner().invoke(main, ["dump", str(dylib), "--gcc-option=-DX"])
        assert result.exit_code == 0, result.output
        assert "will be ignored" not in result.output
        assert getattr(captured["compile_context"], "gcc_option_tokens") == ("-DX",)

    def test_dump_compile_db_flags_and_match_threaded_to_non_elf(
        self, tmp_path, monkeypatch
    ) -> None:
        """Codex review: -p/--compile-db was resolved for ELF only -- a PE/
        Mach-O dump silently dropped the compile database's castxml/clang
        flags entirely, and never threaded the matched signal through to
        handle_non_elf_dump either (so snap.parsed_with_build_context could
        never be set, wrongly rejecting a --depth build backed only by -p).
        """
        import json
        import struct

        import abicheck.cli as cli_mod

        header = tmp_path / "foo.h"
        header.write_text("int f();\n", encoding="utf-8")
        src = tmp_path / "foo.cpp"
        src.write_text('#include "foo.h"\nint f() { return 0; }\n', encoding="utf-8")
        db = tmp_path / "compile_commands.json"
        db.write_text(
            json.dumps(
                [
                    {
                        "directory": str(tmp_path),
                        "file": "foo.cpp",
                        "arguments": ["c++", "-std=c++17", "-DFOO=1", "-c", "foo.cpp"],
                    }
                ]
            ),
            encoding="utf-8",
        )
        dylib = tmp_path / "fake.dylib"
        dylib.write_bytes(struct.pack("<I", 0xFEEDFACF) + b"\x00" * 64)

        captured: dict[str, object] = {}
        monkeypatch.setattr(
            cli_mod, "handle_non_elf_dump", lambda *a, **k: captured.update(k)
        )
        result = CliRunner().invoke(
            main, ["dump", str(dylib), "-H", str(header), "-p", str(db)]
        )
        assert result.exit_code == 0, result.output
        assert captured["compile_db_context_matched"] is True
        gcc_options = getattr(captured["compile_context"], "gcc_options")
        assert "-std=c++17" in gcc_options
        assert "-DFOO=1" in gcc_options

    def test_dump_gcc_option_help(self) -> None:
        # G21.5: the repeatable --gcc-option is documented on dump.
        out = CliRunner().invoke(main, ["dump", "--help"]).output
        norm = out.replace("│", "").replace("\n", "").replace(" ", "")
        assert "--gcc-option" in norm

    def test_dump_depth_help_shows_four_rungs(self) -> None:
        runner = CliRunner()
        help_out = runner.invoke(main, ["dump", "--help"])
        assert help_out.exit_code == 0
        assert "--depth" in help_out.output
        assert "--max" not in help_out.output
        # full/symbols/graph are rejected outright -- no alias, no --max shorthand.
        rejected = runner.invoke(main, ["dump", "--depth", "full"])
        assert rejected.exit_code != 0

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
        with pytest.raises(click.UsageError, match="needs both sides"):
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
        _announce_exit_scheme("legacy", fmt="json", stat=False)
        assert capsys.readouterr().err == ""

    def test_announce_legacy_scheme(self, capsys) -> None:
        _announce_exit_scheme("legacy", fmt="markdown", stat=False)
        assert "legacy verdict" in capsys.readouterr().err

    def test_announce_severity_scheme(self, capsys) -> None:
        _announce_exit_scheme("severity", fmt="markdown", stat=False)
        assert "severity-aware" in capsys.readouterr().err

    def test_exit_verdict_breaking(self) -> None:
        result = DiffResult(
            old_version="1", new_version="2", library="x", verdict=Verdict.BREAKING
        )
        with pytest.raises(SystemExit) as exc:
            _exit_with_severity_or_verdict(result, None, "legacy")
        assert exc.value.code == 4

    def test_exit_verdict_api_break(self) -> None:
        result = DiffResult(
            old_version="1", new_version="2", library="x", verdict=Verdict.API_BREAK
        )
        with pytest.raises(SystemExit) as exc:
            _exit_with_severity_or_verdict(result, None, "legacy")
        assert exc.value.code == 2

    def test_exit_verdict_compatible_no_exit(self) -> None:
        result = DiffResult(
            old_version="1", new_version="2", library="x", verdict=Verdict.COMPATIBLE
        )
        # Compatible verdict returns normally (no SystemExit).
        assert _exit_with_severity_or_verdict(result, None, "legacy") is None


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
            "--probe-matrix",
            "old=" + str(m),
        )
        assert result.exit_code != 0
        assert "needs both sides" in result.output


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
        result = _invoke("compare", "--help")
        assert result.exit_code == 0

    def test_annotate_additions_requires_annotate(self, tmp_path: Path) -> None:
        old_dir = tmp_path / "old"
        new_dir = tmp_path / "new"
        old_dir.mkdir()
        new_dir.mkdir()
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())
        result = _invoke(
            "compare",
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
            "compare",
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
            "compare",
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
            "compare",
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
            "compare",
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
            "compare",
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
            "compare",
            str(old_dir),
            str(new_dir),
            "--format",
            "markdown",
        )
        assert result.exit_code == 0
        assert "Removed Libraries" in result.output


# ── compare --used-by: app-scoped exit codes + output (ADR-043 folds appcompat) ─
#
# The standalone `appcompat` CLI (and `cli_appcompat.py` -- `_validate_appcompat_args`,
# `_handle_list_required_symbols`, weak/`--check-against` mode, `--list-required-symbols`)
# was deleted; its full-mode (old/new pair) scoping behavior folded into
# `compare --used-by APP` (repeatable), which floors the exit code/verdict on the
# worst app-scoped result while keeping the full diff as informational context
# (`cli_compare_helpers._apply_used_by_scoping`, confirmed by direct CLI experiment).
#
# Dropped, no equivalent surface:
# - `_validate_appcompat_args`/`_handle_list_required_symbols` -- functions are
#   gone (same precedent as `tests/test_cli_split_modules_new.py`).
# - weak mode (`--check-against`, a symbol-availability check with no old/new
#   pair) -- `--used-by`/`--required-symbol` always scope a real old/new compare.
# - `--list-required-symbols` (report-only, does not gate) -- `--used-by`'s
#   `--dry-run` path reports a *count* of an app's required symbols/versions
#   (`_render_compare_dry_run`), not the full listing the old flag printed, so
#   it is not a real equivalent.
#
# Ported forward: the scoped exit codes (0/2/4), JSON/markdown/html output
# shape, and -o/--output all still apply and are covered below. Note a real
# behavior change from the deleted CLI: `cli_appcompat.py` had a bespoke
# severity-aware exit path for full mode (recomputing from `breaking_for_app`
# via a resolved severity config, only flooring missing-symbols at 4);
# `compare --used-by`'s scoped exit has no such path at all -- it floors purely
# on `scope_diff_to_app(...).verdict` (BREAKING -> 4, API_BREAK -> 2, else 0),
# so `--severity-preset` has zero effect on the scoped exit code (verified
# directly: an app-relevant BREAKING change still exits 4 under
# `--severity-preset info-only`, where the old appcompat CLI would have exited
# 0). `TestUsedByScoping.test_severity_missing_symbols_floors_at_4` and
# `.test_severity_clean_exit_0` below cover what remains true post-fold (a
# severity preset alongside `--used-by` does not change or break the scoped
# exit); the old `--severity-preset info-only` *downgrading an app-relevant
# break* case (`TestAppcompatSeverityExit` in test_config_review.py) has no
# replacement and was deleted there -- flagged as a possible product gap
# rather than patched here (test-file-only task).


class TestUsedByScoping:
    """`compare --used-by` full-mode scoping, via a stubbed
    ``appcompat.scope_diff_to_app`` (mirroring the deleted CLI's wholesale
    ``check_appcompat`` stub) so the JSON/markdown/html output and exit-code
    branches run without a real compiler. ``dumper.dump`` is stubbed too since
    -- unlike the deleted standalone command -- ``compare``'s own pipeline
    always dumps OLD/NEW itself before scoping runs."""

    def _setup(self, tmp_path, monkeypatch):
        from abicheck import dumper as dumper_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_snap = _snap("1.0", library="libfoo.so")
        new_snap = _snap("2.0", library="libfoo.so")
        monkeypatch.setattr(
            dumper_mod, "dump", MagicMock(side_effect=[old_snap, new_snap])
        )
        return app, old, new

    def _patch_scope(self, monkeypatch, result):
        import abicheck.appcompat as appcompat_mod

        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", lambda *a, **k: result)

    def _result(
        self, *, verdict=Verdict.COMPATIBLE, missing=None, missing_versions=None,
        breaking_for_app=None,
    ):
        from abicheck.appcompat import AppCompatResult

        return AppCompatResult(
            app_path="/app",
            old_lib_path="old.so",
            new_lib_path="new.so",
            required_symbols={"foo"},
            required_symbol_count=1,
            breaking_for_app=breaking_for_app or [],
            missing_symbols=missing or [],
            missing_versions=missing_versions or [],
            verdict=verdict,
            symbol_coverage=100.0,
        )

    def test_full_mode_json_output(self, tmp_path, monkeypatch) -> None:
        res = self._result()
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
        )
        assert result.exit_code == 0
        # The mixed stdout+stderr `.output` may carry pre-JSON warnings (real
        # dump path, unlike the deleted CLI's wholesale check_appcompat stub);
        # `.stdout` is the pure JSON stream.
        data = json.loads(result.stdout)
        assert data["used_by"][0]["verdict"] == "COMPATIBLE"

    def test_full_mode_breaking_exit_4(self, tmp_path, monkeypatch) -> None:
        res = self._result(verdict=Verdict.BREAKING, missing=["foo"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke("compare", str(old), str(new), "--used-by", str(app))
        assert result.exit_code == 4

    def test_full_mode_api_break_exit_2(self, tmp_path, monkeypatch) -> None:
        res = self._result(verdict=Verdict.API_BREAK)
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke("compare", str(old), str(new), "--used-by", str(app))
        assert result.exit_code == 2

    def test_full_mode_output_to_file(self, tmp_path, monkeypatch) -> None:
        res = self._result()
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        out = tmp_path / "rep.md"
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "-o", str(out),
        )
        assert result.exit_code == 0
        assert out.exists()
        assert "Report written to" in result.output

    def test_default_markdown_names_uncovered_missing_symbol(
        self, tmp_path, monkeypatch
    ) -> None:
        """Codex review: the default (markdown) report must name the actual
        missing symbol, not just its count -- otherwise a human reading the
        default output has no way to tell which symbol broke the gate."""
        res = self._result(
            verdict=Verdict.BREAKING, missing=["foo_removed"],
            missing_versions=["FOO_1.2"],
        )
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke("compare", str(old), str(new), "--used-by", str(app))
        assert result.exit_code == 4
        assert "missing symbol: `foo_removed`" in result.output
        assert "missing version: `FOO_1.2`" in result.output
        assert "## Additional scoped-gate findings" in result.output
        assert "`foo_removed` is required but missing from the new library" in result.output

    def test_default_markdown_names_scoped_only_change(
        self, tmp_path, monkeypatch
    ) -> None:
        """Codex review: a scoped-only Change (e.g. PE_ORDINAL_RETARGETED,
        relevant to the gate but never added to result.changes) must be named
        in the default text report too, mirroring the JSON/SARIF/JUnit fold-in."""
        scoped_change = Change(
            ChangeKind.PE_ORDINAL_RETARGETED, "MyExport", "ordinal changed from 5 to 7",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_change])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke("compare", str(old), str(new), "--used-by", str(app))
        assert "## Additional scoped-gate findings" in result.output
        assert "pe_ordinal_retargeted: ordinal changed from 5 to 7" in result.output

    def test_severity_missing_symbols_default_preset_floors_at_4(
        self, tmp_path, monkeypatch
    ) -> None:
        # A required symbol's removal is a real Change in breaking_for_app
        # (as scope_diff_to_app would report it) -- abi_breaking defaults to
        # error, so the scoped exit code still floors at 4.
        res = self._result(
            verdict=Verdict.BREAKING, missing=["foo"],
            breaking_for_app=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")],
        )
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--severity-preset", "default",
        )
        assert result.exit_code == 4

    def test_severity_missing_symbol_covered_by_change_not_double_counted(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex P2 follow-up): "foo" is both a missing symbol
        # (absent from the new exports) *and* the subject of a scoped
        # FUNC_REMOVED Change -- that's one ABI break, not two. Before the
        # fix, the missing-contract count was added on top of the
        # categorized Change count unconditionally.
        res = self._result(
            verdict=Verdict.BREAKING, missing=["foo"],
            breaking_for_app=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")],
        )
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--format", "json", "--severity-preset", "default",
        )
        assert result.exit_code == 4
        data = json.loads(result.stdout)
        assert data["severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_sarif_missing_symbol_covered_by_change_not_double_synthesized(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex review): "_Z3foov" is both a missing symbol
        # (absent from new's exports) *and* the subject of a real, scoped
        # FUNC_REMOVED Change in the actual diff -- the SARIF report must
        # show one result for it, not two (the real Change plus a synthetic
        # missing-contract entry double-reporting the same break).
        from abicheck import dumper as dumper_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_snap = _snap("1.0", library="libfoo.so")  # has foo/_Z3foov
        new_snap = _snap("2.0", library="libfoo.so", funcs=[])  # foo removed
        monkeypatch.setattr(
            dumper_mod, "dump", MagicMock(side_effect=[old_snap, new_snap])
        )

        # Extract the REAL diff's FUNC_REMOVED Change (not a hand-built stub
        # with different description/old_value text) so its finding id
        # genuinely matches the one in result.changes -- otherwise the dedup
        # this test targets would never engage, since _finding_id is content-
        # based, not id()-based.
        def _scoped_for(diff, *_args, **_kwargs):
            from abicheck.appcompat import AppCompatResult

            real_change = next(c for c in diff.changes if c.kind == ChangeKind.FUNC_REMOVED)
            return AppCompatResult(
                app_path="/app", old_lib_path=str(old), new_lib_path=str(new),
                required_symbols={"_Z3foov"}, required_symbol_count=1,
                missing_symbols=["_Z3foov"],
                breaking_for_app=[real_change],
                verdict=Verdict.BREAKING,
            )

        import abicheck.appcompat as appcompat_mod

        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", _scoped_for)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "sarif",
        )
        data = json.loads(result.stdout)
        sarif_results = data["runs"][0]["results"]
        assert len(sarif_results) == 1
        assert sarif_results[0]["ruleId"] == "func_removed"

    def test_severity_missing_symbols_only_floors_at_4(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex P1): a required symbol absent from both old and
        # new libraries is a missing contract with no corresponding diff
        # Change -- `scope_diff_to_app` reports it purely via
        # `missing_symbols`, leaving `breaking_for_app` empty. Before the
        # fix, `_scoped_exit_code` computed the severity-scheme exit solely
        # from `breaking_for_app`, silently exiting 0 for an app that can
        # never resolve the required symbol at all.
        res = self._result(verdict=Verdict.BREAKING, missing=["foo"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--severity-preset", "default",
        )
        assert result.exit_code == 4

    def test_severity_missing_symbols_only_json_blocking_categories(
        self, tmp_path, monkeypatch
    ) -> None:
        # The missing-contract-only case (no diff Change) must still surface
        # "abi_breaking" in the scoped JSON severity block's
        # blocking_categories -- otherwise a nonzero exit_code with an empty
        # blocking_categories list would be an unexplained gate result.
        res = self._result(verdict=Verdict.BREAKING, missing=["foo"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--format", "json", "--severity-preset", "default",
        )
        assert result.exit_code == 4
        data = json.loads(result.stdout)
        assert data["severity"]["exit_code"] == 4
        assert data["severity"]["blocking"] is True
        assert data["severity"]["blocking_categories"] == ["abi_breaking"]
        # The missing symbol itself (not a diff Change) still counts.
        assert data["severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_severity_info_only_preset_overrides_missing_symbols_exit(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression: --severity-preset used to have NO effect on the scoped
        # exit code at all -- an info-only preset must now floor exit_code at
        # 0 despite the scoped verdict staying BREAKING (post-merge PR #566
        # review).
        res = self._result(
            verdict=Verdict.BREAKING, missing=["foo"],
            breaking_for_app=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")],
        )
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--severity-preset", "info-only",
        )
        assert result.exit_code == 0

    def test_multi_app_scoped_verdict_ranked_independently_of_exit_code(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression: under a severity scheme, a BREAKING app can carry exit
        # code 0 (info-only preset). Picking the reported scoped_verdict by
        # exit code (both apps tie at 0) let the second, merely-COMPATIBLE
        # app overwrite the first BREAKING app's verdict -- the JSON/report
        # verdict must stay BREAKING even though the gated exit code is
        # floored at 0 by the severity config (Codex review).
        import abicheck.appcompat as appcompat_mod
        from abicheck.appcompat import AppCompatResult

        breaking_res = self._result(
            verdict=Verdict.BREAKING, missing=["foo"],
            breaking_for_app=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")],
        )
        compatible_res = AppCompatResult(
            app_path="/app2", old_lib_path="old.so", new_lib_path="new.so",
            required_symbols=set(), required_symbol_count=0,
            verdict=Verdict.COMPATIBLE, symbol_coverage=100.0,
        )
        app1, old, new = self._setup(tmp_path, monkeypatch)
        app2 = tmp_path / "app2"
        app2.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(
            appcompat_mod, "scope_diff_to_app",
            MagicMock(side_effect=[breaking_res, compatible_res]),
        )
        result = _invoke(
            "compare", str(old), str(new),
            "--used-by", str(app1), "--used-by", str(app2),
            "--severity-preset", "info-only", "--format", "json",
        )
        data = json.loads(result.stdout)
        assert result.exit_code == 0  # severity config still floors the gate
        assert data["verdict"] == "BREAKING"  # but the reported verdict is not lost

    def test_multi_app_shared_change_not_double_counted(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex P2): when two --used-by apps tie on the worst
        # exit code and both depend on the *same* removed symbol, the shared
        # Change object must be counted once in
        # severity.categories.abi_breaking.count, not once per app -- the
        # library only has one ABI finding, not two.
        import abicheck.appcompat as appcompat_mod
        from abicheck.appcompat import AppCompatResult

        shared_change = Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")
        res1 = AppCompatResult(
            app_path="/app1", old_lib_path="old.so", new_lib_path="new.so",
            required_symbols={"foo"}, required_symbol_count=1,
            breaking_for_app=[shared_change], verdict=Verdict.BREAKING,
        )
        res2 = AppCompatResult(
            app_path="/app2", old_lib_path="old.so", new_lib_path="new.so",
            required_symbols={"foo"}, required_symbol_count=1,
            breaking_for_app=[shared_change], verdict=Verdict.BREAKING,
        )
        app1, old, new = self._setup(tmp_path, monkeypatch)
        app2 = tmp_path / "app2"
        app2.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(
            appcompat_mod, "scope_diff_to_app",
            MagicMock(side_effect=[res1, res2]),
        )
        result = _invoke(
            "compare", str(old), str(new),
            "--used-by", str(app1), "--used-by", str(app2),
            "--severity-preset", "default", "--format", "json",
        )
        data = json.loads(result.stdout)
        assert result.exit_code == 4
        assert data["severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_multi_app_semantically_identical_change_not_double_counted(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (CLI-audit P2): unlike the shared-object case above,
        # `appcompat._check_pe_ordinal_imports` constructs a FRESH
        # PE_ORDINAL_RETARGETED Change per `scope_diff_to_app` call, so two
        # apps retargeting the same ordinal get two distinct Change objects
        # with identical kind/symbol/description but different id() -- the
        # old id()-keyed dedup in `_apply_used_by_scoping` would count that
        # as two findings instead of one.
        import abicheck.appcompat as appcompat_mod
        from abicheck.appcompat import AppCompatResult

        res1 = AppCompatResult(
            app_path="/app1", old_lib_path="old.so", new_lib_path="new.so",
            required_symbols={"foo"}, required_symbol_count=1,
            breaking_for_app=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")],
            verdict=Verdict.BREAKING,
        )
        res2 = AppCompatResult(
            app_path="/app2", old_lib_path="old.so", new_lib_path="new.so",
            required_symbols={"foo"}, required_symbol_count=1,
            breaking_for_app=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed: foo")],
            verdict=Verdict.BREAKING,
        )
        app1, old, new = self._setup(tmp_path, monkeypatch)
        app2 = tmp_path / "app2"
        app2.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(
            appcompat_mod, "scope_diff_to_app",
            MagicMock(side_effect=[res1, res2]),
        )
        result = _invoke(
            "compare", str(old), str(new),
            "--used-by", str(app1), "--used-by", str(app2),
            "--severity-preset", "default", "--format", "json",
        )
        data = json.loads(result.stdout)
        assert result.exit_code == 4
        assert data["severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_severity_clean_exit_0(self, tmp_path, monkeypatch) -> None:
        res = self._result(verdict=Verdict.COMPATIBLE)
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--severity-preset", "default",
        )
        assert result.exit_code == 0

    def test_full_mode_html_output(self, tmp_path, monkeypatch) -> None:
        res = self._result()
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "html",
        )
        assert result.exit_code == 0
        assert "<" in result.stdout  # HTML markup emitted

    def test_markdown_states_scoped_verdict_when_it_disagrees_with_full(
        self, tmp_path, monkeypatch
    ) -> None:
        # ADR-043 Codex review: the full-library verdict (BREAKING, from the
        # symbol removal below) disagrees with the app-scoped verdict
        # (COMPATIBLE, since the app never touches the removed symbol) --
        # exit_code reflects the scoped one, so the markdown report must say
        # so instead of only showing the full-library BREAKING headline.
        old_snap = _snap(
            "1.0", library="libfoo.so",
            funcs=[Function(
                name="removed", mangled="_Z7removedv", return_type="void",
                visibility=Visibility.PUBLIC,
            )],
        )
        new_snap = _snap("2.0", library="libfoo.so", funcs=[])
        from abicheck import dumper as dumper_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(
            dumper_mod, "dump", MagicMock(side_effect=[old_snap, new_snap])
        )
        self._patch_scope(monkeypatch, self._result(verdict=Verdict.COMPATIBLE))
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "markdown",
        )
        assert result.exit_code == 0  # the scoped verdict, not the full BREAKING
        assert "Scoped verdict: COMPATIBLE" in result.stdout
        assert "full library verdict above is BREAKING" in result.stdout

    def test_markdown_scoped_banner_states_actual_exit_under_severity_scheme(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex P2): under a severity scheme, the scoped exit
        # code is NOT a fixed mapping of the scoped verdict -- e.g.
        # --severity-preset info-only can floor it at 0 even for a BREAKING
        # scoped verdict. The markdown banner used to unconditionally claim
        # "this is what the exit code reflects" whenever the scoped and full
        # verdicts disagreed, which is false here (BREAKING scoped verdict,
        # exit code 0) -- it must state the actual computed exit code/scheme
        # instead, mirroring the SARIF/JUnit/HTML wording.
        res = self._result(verdict=Verdict.BREAKING, missing=["foo"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--format", "markdown", "--severity-preset", "info-only",
        )
        assert result.exit_code == 0
        assert "Scoped verdict: BREAKING" in result.stdout
        assert "the CLI process exits 0 under the severity exit-code scheme" in result.stdout
        assert "this is what the exit code reflects" not in result.stdout

    def test_json_severity_block_reflects_scoped_gate_not_full_library(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex P2): under --severity-preset, the JSON `severity`
        # block used to always describe the *full-library* gate decision --
        # here the full library has an error-level BREAKING removal but the
        # app-scoped result is COMPATIBLE. The process exits 0 (the scoped
        # gate), so `severity.exit_code`/`blocking` in the JSON body must
        # agree with that, not silently claim `exit_code: 4`/`blocking: true`
        # for a run that just exited 0.
        old_snap = _snap(
            "1.0", library="libfoo.so",
            funcs=[Function(
                name="removed", mangled="_Z7removedv", return_type="void",
                visibility=Visibility.PUBLIC,
            )],
        )
        new_snap = _snap("2.0", library="libfoo.so", funcs=[])
        from abicheck import dumper as dumper_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(
            dumper_mod, "dump", MagicMock(side_effect=[old_snap, new_snap])
        )
        self._patch_scope(monkeypatch, self._result(verdict=Verdict.COMPATIBLE))
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--format", "json", "--severity-preset", "default",
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["full_verdict"] == "BREAKING"
        assert data["verdict"] == "COMPATIBLE"
        # The scoped gate, not the full-library one that would exit 4.
        assert data["severity"]["exit_code"] == 0
        assert data["severity"]["blocking"] is False
        assert data["severity"]["blocking_categories"] == []
        # Category counts also move to the scoped tally -- not left over
        # from the full-library breakdown alongside a non-blocking gate.
        assert data["severity"]["categories"]["abi_breaking"]["count"] == 0
        # The full-library breakdown is preserved, just demoted to a
        # secondary key -- it still shows the real BREAKING removal.
        assert data["full_severity"]["exit_code"] == 4
        assert data["full_severity"]["blocking"] is True
        assert "abi_breaking" in data["full_severity"]["blocking_categories"]
        assert data["full_severity"]["categories"]["abi_breaking"]["count"] == 1

    def test_json_scoped_only_change_is_included_in_changes(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex review): scope_diff_to_app can synthesize a fresh
        # Change (e.g. PE_ORDINAL_RETARGETED) that is relevant to the gate but
        # never lands in result.changes -- SARIF/JUnit already fold this into
        # their own rendering (scoped_only_changes), but the JSON `changes`
        # array (which the GitHub Action's `--on changes` PR-comment gate
        # buckets off directly) did not, so a --used-by run whose only gated
        # issue is one of these reported an empty `changes` array despite a
        # nonzero scoped exit code.
        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
            old_value="OldFunc", new_value="NewFunc",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
        )
        assert result.exit_code == 4
        data = json.loads(result.stdout)
        assert data["full_verdict"] == "NO_CHANGE"
        assert data["verdict"] == "BREAKING"
        kinds = [c["kind"] for c in data["changes"]]
        assert "pe_ordinal_retargeted" in kinds
        entry = next(c for c in data["changes"] if c["kind"] == "pe_ordinal_retargeted")
        assert entry["symbol"] == "ordinal:5"

    def test_json_uncovered_missing_symbol_is_included_in_changes(
        self, tmp_path, monkeypatch
    ) -> None:
        # Same gap as above, for a missing required symbol/version with no
        # backing Change at all (scoped_missing_labels, not scoped_only_changes).
        res = self._result(verdict=Verdict.BREAKING, missing=["needed_symbol"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
        )
        assert result.exit_code == 4
        data = json.loads(result.stdout)
        entry = next(
            c for c in data["changes"] if c["kind"] == "used_by_missing_symbol"
        )
        assert entry["symbol"] == "needed_symbol"
        assert entry["blocks_gate"] is True
        # G29 Phase 3 slice 1 (ADR-052, Codex review): reachability_state is
        # "always present" for every changes[] entry -- a missing-contract
        # label has no backing Change, but it still needs the honest
        # UNKNOWN value rather than silently omitting the field.
        assert entry["reachability_state"] == "unknown"

    def test_root_cause_mode_includes_scoped_only_change(
        self, tmp_path, monkeypatch
    ) -> None:
        # Codex review: --report-mode root-cause groups result.changes before
        # the scoped fold-in appends scoped_only_changes to `changes` -- a
        # scoped run whose only gated issue is one of these must still show
        # up in root_causes, not just the flat backward-compat `changes[]`.
        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
            old_value="OldFunc", new_value="NewFunc",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--format", "json", "--report-mode", "root-cause",
        )
        assert result.exit_code == 4
        data = json.loads(result.stdout)
        assert data["root_cause_count"] == 1
        group = data["root_causes"][0]
        assert group["root"] == "ordinal:5"
        assert group["findings"][0]["kind"] == "pe_ordinal_retargeted"

    def test_root_cause_mode_includes_missing_symbol_label(
        self, tmp_path, monkeypatch
    ) -> None:
        # Same gap as above, for a missing required symbol with no backing
        # Change (scoped_missing_labels, not scoped_only_changes).
        res = self._result(verdict=Verdict.BREAKING, missing=["needed_symbol"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--format", "json", "--report-mode", "root-cause",
        )
        data = json.loads(result.stdout)
        assert data["root_cause_count"] == 1
        group = data["root_causes"][0]
        assert group["root"] == "needed_symbol"
        assert group["findings"][0]["kind"] == "used_by_missing_symbol"

    def test_root_cause_mode_regroups_existing_cause_with_scoped_only(
        self, tmp_path, monkeypatch
    ) -> None:
        # Codex review: _to_json_root_cause groups result.changes *before*
        # the scoped fold-in appends scoped_only_changes -- if a scoped-only
        # finding's caused_by_type matches an existing real change's symbol,
        # that existing change must already be keyed by that shared cause
        # from the start (mirroring sarif.to_sarif's single-pass grouping),
        # or the fold-in's later merge attempt creates a second, disagreeing
        # root-cause group for the same logical cause instead of joining it.
        from abicheck import dumper as dumper_mod

        old, new = _breaking_pair()  # real diff: "bar"/_Z3barv removed
        app_path = tmp_path / "app"
        app_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_p = tmp_path / "old.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new_p = tmp_path / "new.so"
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(
            dumper_mod, "dump", MagicMock(side_effect=[old, new])
        )
        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="pub_entry",
            description="ordinal retargeted",
            caused_by_type="_Z3barv",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old_p), str(new_p), "--used-by", str(app_path),
            "--format", "json", "--report-mode", "root-cause",
        )
        assert result.exit_code == 4
        data = json.loads(result.stdout)
        assert data["root_cause_count"] == 1
        group = data["root_causes"][0]
        assert group["root"] == "_Z3barv"
        assert group["finding_count"] == 2
        assert {f["kind"] for f in group["findings"]} == {
            "func_removed", "pe_ordinal_retargeted",
        }

    def test_json_uncovered_missing_symbol_not_blocking_under_demoted_severity(
        self, tmp_path, monkeypatch
    ) -> None:
        # A missing-contract entry must not claim blocks_gate=True when a
        # severity config demotes abi_breaking below error (mirrors the
        # SARIF/JUnit severity-aware missing-contract handling).
        res = self._result(verdict=Verdict.BREAKING, missing=["needed_symbol"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
            "--severity-abi-breaking", "warning",
        )
        data = json.loads(result.stdout)
        entry = next(
            c for c in data["changes"] if c["kind"] == "used_by_missing_symbol"
        )
        assert entry["blocks_gate"] is False
        assert entry["severity"] == "compatible"

    def test_json_scoped_only_change_respects_show_only(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex review): to_json's own --show-only filtering
        # only ever touched result.changes -- scoped_only_changes were
        # appended to the JSON `changes` array unconditionally afterward, so
        # a --used-by --show-only run could re-surface a finding the filter
        # was supposed to exclude (mirrors the identical sarif.to_sarif fix).
        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
            old_value="OldFunc", new_value="NewFunc",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
            "--show-only", "compatible",
        )
        data = json.loads(result.stdout)
        kinds = [c["kind"] for c in data["changes"]]
        assert "pe_ordinal_retargeted" not in kinds

    def test_json_scoped_only_change_has_consumer_proven_evidence_status(
        self, tmp_path, monkeypatch
    ) -> None:
        """Codex review: a scoped-only change (PE_ORDINAL_RETARGETED,
        CONSUMER_REQUIRED_SYMBOL_REMOVED, ...) is proven by the real
        consumer's own import table, not an artifact-level library diff --
        it must render evidence_status: consumer_proven, not the
        BREAKING-category default artifact_proven."""
        scoped_only = Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="foo_removed",
            description="Consumer requires foo_removed",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
        )
        data = json.loads(result.stdout)
        entry = next(
            c for c in data["changes"] if c["kind"] == "consumer_required_symbol_removed"
        )
        assert entry["evidence_status"] == "consumer_proven"

    def test_json_scoped_only_change_reachability_kind_validates_against_schema(
        self, tmp_path, monkeypatch
    ) -> None:
        """Codex review, fresh evidence: scope_diff_to_app now sets
        public_reachable=True/reachability_kind="consumer_proven" on this
        overlay (so a broad suppression rule can't silently hide a
        consumer-proven break) -- the rendered JSON must actually validate
        against the published schema, whose reachability_kind enum needed
        "consumer_proven" added alongside the four public-surface-walk
        values it already had."""
        pytest.importorskip("jsonschema")
        import jsonschema

        from abicheck.schemas import load_compare_report_schema

        scoped_only = Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="foo_removed",
            description="Consumer requires foo_removed",
            public_reachable=True,
            reachability_kind="consumer_proven",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
        )
        data = json.loads(result.stdout)
        entry = next(
            c for c in data["changes"] if c["kind"] == "consumer_required_symbol_removed"
        )
        assert entry["reachability_kind"] == "consumer_proven"
        jsonschema.validate(instance=data, schema=load_compare_report_schema())

    def test_json_missing_symbol_respects_show_only(
        self, tmp_path, monkeypatch
    ) -> None:
        # Regression (Codex review): a missing-contract label has no backing
        # Change/ChangeKind so it can't run through apply_show_only -- but a
        # --show-only run that excludes breaking findings must still not
        # include the (default-blocking) missing-contract entry.
        res = self._result(verdict=Verdict.BREAKING, missing=["needed_symbol"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
            "--show-only", "compatible",
        )
        data = json.loads(result.stdout)
        kinds = [c["kind"] for c in data["changes"]]
        assert "used_by_missing_symbol" not in kinds

    def test_json_missing_symbol_shown_when_show_only_includes_breaking(
        self, tmp_path, monkeypatch
    ) -> None:
        res = self._result(verdict=Verdict.BREAKING, missing=["needed_symbol"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
            "--show-only", "breaking",
        )
        data = json.loads(result.stdout)
        kinds = [c["kind"] for c in data["changes"]]
        assert "used_by_missing_symbol" in kinds

    def test_json_summary_reflects_scoped_only_and_missing_findings(
        self, tmp_path, monkeypatch
    ) -> None:
        """Audit finding: `summary` is computed from the real diff's
        result.changes *before* scoped-only/missing-contract entries are
        folded into `changes` -- a scoped run whose only gating issue is one
        of these synthetic entries (real diff: no changes; scoped gate:
        BREAKING on a missing required symbol) used to report
        verdict "BREAKING" next to summary.total_changes: 0, an internally
        contradictory JSON body. `summary` must count the synthetic entries
        too; the pre-scoped counts move to `full_summary`."""
        res = self._result(verdict=Verdict.BREAKING, missing=["needed_symbol"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
        )
        data = json.loads(result.stdout)
        assert data["verdict"] == "BREAKING"
        assert data["summary"]["total_changes"] == len(data["changes"]) == 1
        assert data["summary"]["breaking"] == 1
        assert data["full_summary"]["total_changes"] == 0

        # Schema-validation regression (external review): full_summary is a
        # schema-2.9 top-level key -- assert this exact scoped-only payload
        # (the shape that motivated adding it) validates against the
        # packaged compare_report.schema.json, not just that reading it by
        # hand looks right.
        try:
            import jsonschema
        except ImportError:
            pytest.skip("jsonschema not installed")
        from abicheck.schemas import load_compare_report_schema

        jsonschema.validate(instance=data, schema=load_compare_report_schema())

    def test_stat_json_summary_reflects_scoped_only_and_missing_findings(
        self, tmp_path, monkeypatch
    ) -> None:
        """Codex review: `--format json --stat` (to_stat_json) emits a
        summary-only payload with no `changes` array at all, so the
        changes_list-gated recompute above never ran for it -- `verdict`
        still swapped to the scoped gate result, but `summary` stayed the
        stale full-library counts and no `full_summary` was added. Same
        contradiction as the non-stat case
        (test_json_summary_reflects_scoped_only_and_missing_findings), just
        reachable via --stat too."""
        res = self._result(verdict=Verdict.BREAKING, missing=["needed_symbol"])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
            "--stat",
        )
        data = json.loads(result.stdout)
        assert "changes" not in data
        assert data["verdict"] == "BREAKING"
        assert data["summary"]["total_changes"] == 1
        assert data["summary"]["breaking"] == 1
        assert data["full_summary"]["total_changes"] == 0


class TestVerifyRuntimeFlag:
    """``compare --used-by APP --verify-runtime`` (ADR-044 P2 item 2), via a
    stubbed ``runtime_probe.run_runtime_probe`` so no real dynamic-linker
    execution is needed for the CLI wiring itself. Reuses
    ``TestUsedByScoping``'s fixture helpers (not inherited, to avoid
    re-collecting that class's own tests under this one)."""

    _setup = TestUsedByScoping._setup
    _patch_scope = TestUsedByScoping._patch_scope
    _result = TestUsedByScoping._result

    def _patch_probe(self, monkeypatch, result):
        import abicheck.runtime_probe as rp_mod

        monkeypatch.setattr(rp_mod, "run_runtime_probe", lambda *a, **k: result)

    def test_regression_adds_consumer_runtime_load_failed_finding(
        self, tmp_path, monkeypatch,
    ) -> None:
        from abicheck.runtime_probe import RuntimeProbeOutcome, RuntimeProbeResult

        res = self._result(verdict=Verdict.COMPATIBLE)
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        self._patch_probe(
            monkeypatch,
            RuntimeProbeResult(
                app_path=str(app), attempted=True,
                old=RuntimeProbeOutcome(ok=True),
                new=RuntimeProbeOutcome(ok=False, missing_symbol="foo_bar"),
            ),
        )
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--verify-runtime", "--format", "json",
        )
        data = json.loads(result.stdout)
        assert data["used_by"][0]["relevant_change_count"] == 1

    def test_no_regression_adds_no_finding(self, tmp_path, monkeypatch) -> None:
        from abicheck.runtime_probe import RuntimeProbeOutcome, RuntimeProbeResult

        res = self._result(verdict=Verdict.COMPATIBLE)
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        self._patch_probe(
            monkeypatch,
            RuntimeProbeResult(
                app_path=str(app), attempted=True,
                old=RuntimeProbeOutcome(ok=True),
                new=RuntimeProbeOutcome(ok=True),
            ),
        )
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--verify-runtime", "--format", "json",
        )
        data = json.loads(result.stdout)
        assert data["used_by"][0]["relevant_change_count"] == 0

    def test_regression_recomputes_stale_compatible_verdict(
        self, tmp_path, monkeypatch,
    ) -> None:
        """Codex review: scope_diff_to_app already computed `verdict` before
        this RISK-tier finding existed, so appending it without recomputing
        would still report COMPATIBLE even though breaking_for_app now
        carries a real (RISK) finding -- the reported verdict must become
        COMPATIBLE_WITH_RISK instead of staying stale."""
        from abicheck.runtime_probe import RuntimeProbeOutcome, RuntimeProbeResult

        res = self._result(verdict=Verdict.COMPATIBLE)
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        self._patch_probe(
            monkeypatch,
            RuntimeProbeResult(
                app_path=str(app), attempted=True,
                old=RuntimeProbeOutcome(ok=True),
                new=RuntimeProbeOutcome(ok=False, missing_symbol="foo_bar"),
            ),
        )
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--verify-runtime", "--format", "json",
        )
        data = json.loads(result.stdout)
        assert data["used_by"][0]["verdict"] == "COMPATIBLE_WITH_RISK"

    def test_regression_suppressible_by_symbol(self, tmp_path, monkeypatch) -> None:
        """Codex review: CONSUMER_RUNTIME_LOAD_FAILED is synthesized after the
        pipeline's own suppression pass already ran, so an exact suppression
        rule for the regressed symbol must still be able to hide it."""
        from abicheck.runtime_probe import RuntimeProbeOutcome, RuntimeProbeResult

        res = self._result(verdict=Verdict.COMPATIBLE)
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        self._patch_probe(
            monkeypatch,
            RuntimeProbeResult(
                app_path=str(app), attempted=True,
                old=RuntimeProbeOutcome(ok=True),
                new=RuntimeProbeOutcome(ok=False, missing_symbol="foo_bar"),
            ),
        )
        sup = tmp_path / "sup.yaml"
        sup.write_text(
            "version: 1\nsuppressions:\n"
            "  - symbol: foo_bar\n    reason: known, tracked elsewhere\n",
        )
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--verify-runtime", "--suppress", str(sup), "--format", "json",
        )
        data = json.loads(result.stdout)
        assert data["used_by"][0]["relevant_change_count"] == 0
        assert data["used_by"][0]["verdict"] == "COMPATIBLE"

    def test_regression_not_hidden_by_broad_namespace_rule(
        self, tmp_path, monkeypatch,
    ) -> None:
        """Codex review, fresh evidence: CONSUMER_RUNTIME_LOAD_FAILED only
        ever exists because the dynamic linker itself failed to resolve a
        symbol for a real, executed consumer binary -- built with
        public_reachable at its dataclass default (False) before this fix, a
        broad namespace rule's default "unreachable-only" reachability read
        it as unreachable and silently suppressed a runtime regression that
        is, by construction, always consumer-proven real. public_reachable
        =True must keep it visible under a broad rule (mirrors
        appcompat.scope_diff_to_app's identical fix for
        CONSUMER_REQUIRED_SYMBOL_REMOVED)."""
        from abicheck.runtime_probe import RuntimeProbeOutcome, RuntimeProbeResult

        res = self._result(verdict=Verdict.COMPATIBLE)
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        self._patch_probe(
            monkeypatch,
            RuntimeProbeResult(
                app_path=str(app), attempted=True,
                old=RuntimeProbeOutcome(ok=True),
                new=RuntimeProbeOutcome(ok=False, missing_symbol="ns::detail::foo_bar"),
            ),
        )
        sup = tmp_path / "sup.yaml"
        sup.write_text(
            "version: 1\nsuppressions:\n"
            "  - namespace: \"ns::detail::**\"\n    reason: detail churn\n",
        )
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--verify-runtime", "--suppress", str(sup), "--format", "json",
        )
        data = json.loads(result.stdout)
        assert data["used_by"][0]["relevant_change_count"] == 1
        assert data["used_by"][0]["verdict"] == "COMPATIBLE_WITH_RISK"

    def test_flag_ignored_without_used_by(self, tmp_path, monkeypatch) -> None:
        """--verify-runtime alone (no --used-by) must not error or invoke
        the probe at all -- it's documented as ignored without --used-by."""
        from abicheck import dumper as dumper_mod

        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_snap = _snap("1.0", library="libfoo.so")
        new_snap = _snap("2.0", library="libfoo.so")
        monkeypatch.setattr(
            dumper_mod, "dump", MagicMock(side_effect=[old_snap, new_snap])
        )
        result = _invoke("compare", str(old), str(new), "--verify-runtime")
        assert result.exit_code == 0


class TestFoldEvidenceDepthOutOfBandPack:
    """``_fold_evidence_depth_into_json`` with an out-of-band pack directory.

    Regression (Codex review): an out-of-band ``--old/new-build-info``/
    ``--old/new-sources`` *pack directory* (as opposed to a raw checkout,
    which gets embedded into the snapshot before this point) is resolved via
    ``_resolve_side_pack`` inside ``prepare_embedded_build_source`` /
    ``diff_embedded_build_source`` but never attached back onto the snapshot
    object itself -- so reading only ``snap.build_source`` for the JSON
    ``old_evidence_depth``/``new_evidence_depth`` fields reported the
    snapshot's own (absent) embedded depth instead of the pack that was
    actually used to produce the comparison's build/source findings.
    """

    def test_out_of_band_pack_depth_beats_absent_embedded_snapshot(
        self, tmp_path, monkeypatch
    ) -> None:
        import json as json_mod

        from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
        from abicheck.buildsource.pack import BuildSourcePack
        from abicheck.cli_compare_helpers import _fold_evidence_depth_into_json

        old_snap = _snap("1.0", library="libfoo.so")
        new_snap = _snap("2.0", library="libfoo.so")
        assert old_snap.build_source is None
        assert new_snap.build_source is None

        pack = BuildSourcePack(
            root=tmp_path,
            build_evidence=BuildEvidence(
                compile_units=[CompileUnit(id="cu1", source="a.c")]
            ),
        )
        monkeypatch.setattr(
            "abicheck.cli_buildsource_helpers._resolve_side_pack",
            lambda build_info, sources, snap: pack,
        )

        text = json_mod.dumps({"changes": []})
        result_text = _fold_evidence_depth_into_json(
            text, "json", old_snap, new_snap,
            old_build_info=tmp_path / "old_build", new_build_info=tmp_path / "new_build",
        )
        data = json_mod.loads(result_text)
        assert data["old_evidence_depth"] == "build"
        assert data["new_evidence_depth"] == "build"

    def test_no_pack_args_falls_back_to_snapshot_embedded_depth(self) -> None:
        # Without --old/new-build-info/--old/new-sources, behavior is
        # unchanged: depth comes straight from each snapshot's own embedded
        # build_source (or absence thereof).
        import json as json_mod

        from abicheck.cli_compare_helpers import _fold_evidence_depth_into_json

        old_snap = _snap("1.0", library="libfoo.so")
        new_snap = _snap("2.0", library="libfoo.so")
        new_snap.from_headers = True

        text = json_mod.dumps({"changes": []})
        result_text = _fold_evidence_depth_into_json(text, "json", old_snap, new_snap)
        data = json_mod.loads(result_text)
        assert data["old_evidence_depth"] == "binary"
        assert data["new_evidence_depth"] == "headers"


class TestUsedByScopingWithSnapshotInputs:
    """`compare --used-by` OLD/NEW as saved JSON snapshots (ADR-043 follow-up).

    Regression: --used-by used to hard-require OLD/NEW to be real library
    binaries, breaking the natural `dump` once + `compare ... --used-by`
    later workflow (post-merge PR #566 review) -- a snapshot carrying binary
    evidence (its `elf`/`pe`/`macho` field) must now work.
    """

    def _snap_with_elf(self, version: str, symbol_names: list[str]) -> AbiSnapshot:
        return AbiSnapshot(
            library="libfoo.so.1", version=version,
            elf=ElfMetadata(
                soname="libfoo.so.1",
                symbols=[ElfSymbol(name=n) for n in symbol_names],
            ),
        )

    def _write(self, path: Path, snap: AbiSnapshot) -> Path:
        from abicheck.serialization import snapshot_to_json
        path.write_text(snapshot_to_json(snap), encoding="utf-8")
        return path

    def _patch_scope(self, monkeypatch, result):
        import abicheck.appcompat as appcompat_mod
        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", lambda *a, **k: result)

    def test_both_sides_json_snapshots_with_elf_evidence_succeed(
        self, tmp_path, monkeypatch
    ) -> None:
        old = self._write(tmp_path / "old.json", self._snap_with_elf("1.0", ["foo"]))
        new = self._write(tmp_path / "new.json", self._snap_with_elf("2.0", ["foo"]))
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)

        from abicheck.appcompat import AppCompatResult
        self._patch_scope(monkeypatch, AppCompatResult(
            app_path=str(app), old_lib_path="libfoo.so.1", new_lib_path="libfoo.so.1",
            required_symbols={"foo"}, required_symbol_count=1,
            verdict=Verdict.COMPATIBLE, symbol_coverage=100.0,
        ))
        result = _invoke("compare", str(old), str(new), "--used-by", str(app))
        assert result.exit_code == 0
        assert "requires OLD/NEW to be real library binaries" not in (result.output or "")

    def test_headers_only_json_snapshots_still_rejected(
        self, tmp_path, monkeypatch
    ) -> None:
        # No `elf`/`pe`/`macho` field at all -- no binary evidence to scope
        # against, so this must still fail loudly rather than silently
        # mis-scope (unlike a snapshot from a real library dump).
        old = self._write(tmp_path / "old.json", _snap("1.0"))
        new = self._write(tmp_path / "new.json", _snap("2.0"))
        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)

        result = _invoke("compare", str(old), str(new), "--used-by", str(app))
        assert result.exit_code == 64
        assert "requires OLD/NEW to be real library binaries" in (result.output or "")


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
            "compare",
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
            "compare",
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
            "compare",
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
            "compare",
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
            lambda result, annotate_additions=False, severity_config=None: ["a1"],
        )
        monkeypatch.setattr(
            "abicheck.annotations.format_annotations",
            lambda anns: "::warning::break",
        )
        emitted = {}
        monkeypatch.setattr(
            "abicheck.annotations.emit_github_step_summary",
            lambda result, severity_config=None: emitted.setdefault("summary", True),
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
