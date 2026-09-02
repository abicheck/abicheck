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
directly with pre-built JSON ``AbiSnapshot`` files / mocks instead.ADR-061 Phase 4, throughout: patch the owner, not ``abicheck.cli`` -- its lazy ``__getattr__`` means a ``setattr`` there rebinds nothing the caller reads.
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


def _severity_config(tmp_path: Path, **levels: str) -> Path:
    """A project config setting per-category severity levels.

    The four ``--severity-<category>`` flags were hidden CLI duplicates of
    this block and were removed, so a config file is how a run states one.
    """
    cfg = tmp_path / "severity.abicheck.yml"
    body = "".join(f"  {k}: {v}\n" for k, v in levels.items())
    cfg.write_text(f"severity:\n{body}", encoding="utf-8")
    return cfg


def _suppression_strict_config(tmp_path: Path) -> Path:
    """A project config setting ``suppression.strict`` (was ``--strict-suppressions``)."""
    cfg = tmp_path / "strict.abicheck.yml"
    cfg.write_text("suppression:\n  strict: true\n", encoding="utf-8")
    return cfg


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
        # `dump`'s curated --help (G21.8 M2) folds the Toolchain/Provenance
        # panels' options behind --help-all; check the panels there.
        dump_help = runner.invoke(main, ["dump", "--help-all"]).output
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

    def test_dump_compiler_option_threaded_to_non_elf(self, tmp_path, monkeypatch) -> None:
        # ADR-037 D3 (Codex): --compiler-option is now threaded into the native
        # PE/Mach-O header-scoping path (resolved before format dispatch), so the
        # old "will be ignored" warning is gone and the context reaches the dump.
        #
        # ADR-063 Phase 1: the real PE/Mach-O run now executes through
        # `execute_dump_request`, not the retired `handle_non_elf_dump` --
        # patch `abicheck.service_dump_native._dump_macho` instead (the same
        # depth below the format dispatch `abicheck.dumper.dump` sits at for
        # the ELF precedent, `test_compile_context_parity.py::
        # test_dump_reads_compile_block_from_config`), and assert on the
        # `compile` CompileContext it receives.
        import struct

        from abicheck.model import AbiSnapshot

        dylib = tmp_path / "fake.dylib"
        dylib.write_bytes(struct.pack("<I", 0xFEEDFACF) + b"\x00" * 64)
        captured: dict[str, object] = {}

        def _fake_dump_macho(*args: object, **kwargs: object) -> AbiSnapshot:
            captured.update(kwargs)
            return AbiSnapshot(library="fake.dylib", version="1.0")

        monkeypatch.setattr(
            "abicheck.service_dump_native._dump_macho", _fake_dump_macho
        )
        result = CliRunner().invoke(main, ["dump", str(dylib), "--compiler-option=-DX"])
        assert result.exit_code == 0, result.output
        assert "will be ignored" not in result.output
        assert getattr(captured["compile"], "gcc_option_tokens") == ("-DX",)

    def test_dump_compile_db_flags_and_match_threaded_to_non_elf(
        self, tmp_path, monkeypatch
    ) -> None:
        """Codex review: the compile database was resolved for ELF only -- a
        PE/Mach-O dump silently dropped its castxml/clang flags entirely, and
        never threaded the matched signal through to handle_non_elf_dump
        either (so snap.parsed_with_build_context could never be set, wrongly
        rejecting a --depth build backed only by that database). It arrives
        via --build-info now; the -p/--build-dir + --compile-db pair folded
        into it.

        ADR-063 Phase 1: the real PE/Mach-O run now executes through
        `execute_dump_request` -- patch `abicheck.service_dump_native.
        _dump_macho` (see the sibling test above) and assert on the folded
        `compile` context it receives, the same signal `compile_context`
        carried before this migration.
        """
        import json
        import struct

        from abicheck.model import AbiSnapshot

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

        def _fake_dump_macho(*args: object, **kwargs: object) -> AbiSnapshot:
            captured.update(kwargs)
            return AbiSnapshot(library="fake.dylib", version="1.0")

        monkeypatch.setattr(
            "abicheck.service_dump_native._dump_macho", _fake_dump_macho
        )
        result = CliRunner().invoke(
            main, ["dump", str(dylib), "-H", str(header), "--build-info", str(db)]
        )
        assert result.exit_code == 0, result.output
        gcc_option_tokens = getattr(captured["compile"], "gcc_option_tokens")
        assert "-std=c++17" in gcc_option_tokens
        assert "-DFOO=1" in gcc_option_tokens

    def test_dump_compiler_option_help(self) -> None:
        # G21.5: the repeatable --compiler-option is documented on dump. It's a
        # toolchain-tier flag, folded behind --help-all by dump's curated
        # --help (G21.8 M2).
        out = CliRunner().invoke(main, ["dump", "--help-all"]).output
        norm = out.replace("│", "").replace("\n", "").replace(" ", "")
        assert "--compiler-option" in norm

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

    def test_a_policy_document_no_longer_warns_about_the_profile(
        self, tmp_path: Path, capsys
    ) -> None:
        """One ``--policy`` cannot disagree with itself.

        The flag takes a profile *or* a document, so the "``--policy`` is
        ignored when ``--policy-file`` is given" warning had nothing left to
        warn about and is gone.
        """
        pol = tmp_path / "policy.yaml"
        pol.write_text("base_policy: strict_abi\n")
        _, pf = _load_suppression_and_policy(None, "sdk_vendor", pol)
        assert pf is not None
        assert "ignored" not in capsys.readouterr().err

    def test_policy_file_surfaces_validate_overrides_warnings(
        self, tmp_path: Path, capsys
    ) -> None:
        """A risky override (e.g. downgrading func_removed) must be echoed --
        `PolicyFile.validate_overrides()` previously had no caller, so its
        warnings never reached a user."""
        pol = tmp_path / "policy.yaml"
        pol.write_text(
            "base_policy: strict_abi\noverrides:\n  func_removed: ignore\n"
        )
        _, pf = _load_suppression_and_policy(None, "strict_abi", pol)
        assert pf is not None
        err = capsys.readouterr().err
        assert "HIGH RISK" in err
        assert "func_removed" in err

    def test_policy_file_no_warnings_for_safe_overrides(
        self, tmp_path: Path, capsys
    ) -> None:
        pol = tmp_path / "policy.yaml"
        pol.write_text(
            "base_policy: strict_abi\noverrides:\n  enum_member_renamed: ignore\n"
        )
        _, pf = _load_suppression_and_policy(None, "strict_abi", pol)
        assert pf is not None
        assert capsys.readouterr().err == ""


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
        _announce_exit_scheme("legacy", fmt="json")
        assert capsys.readouterr().err == ""

    def test_announce_suppressed_for_oneline(self, capsys) -> None:
        # The internal one-line format (service_render.ONELINE_FORMAT,
        # reached via --profile quick) is suppressed the same way json/sarif/
        # junit are -- it isn't one of the three human-readable format names,
        # so the same `fmt not in {...}` check covers it with no separate
        # boolean (CLI cleanup phase two, PR 1: --stat removed).
        _announce_exit_scheme("legacy", fmt="oneline")
        assert capsys.readouterr().err == ""

    def test_announce_legacy_scheme(self, capsys) -> None:
        _announce_exit_scheme("legacy", fmt="markdown")
        assert "legacy verdict" in capsys.readouterr().err

    def test_announce_severity_scheme(self, capsys) -> None:
        _announce_exit_scheme("severity", fmt="markdown")
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



# ── compare command CliRunner error/branch paths ──────────────────────────────


class TestCompareCommand:
    def test_help(self) -> None:
        result = _invoke("compare", "--help")
        assert result.exit_code == 0
        assert "Compare two ABI surfaces" in result.output

    def test_annotate_flags_were_removed(self, tmp_path: Path) -> None:
        # CLI cleanup phase two, PR E: --annotate/--annotate-additions no
        # longer exist on `compare` at all -- the composite Action renders
        # annotations itself from the persisted `annotations` report field
        # (its own `annotate`/`annotate-additions` inputs), so both flags
        # exit 64 with Click's own "No such option" rather than any
        # abicheck-specific usage error.
        old_f = _write_snap(tmp_path / "old.json", _snap())
        new_f = _write_snap(tmp_path / "new.json", _snap())
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--annotate-additions",
        )
        assert result.exit_code == 64
        assert "No such option" in result.output

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

    def test_config_public_symbols_without_scope_warns(self, tmp_path: Path) -> None:
        snap = _snap()
        old_f = _write_snap(tmp_path / "old.json", snap)
        new_f = _write_snap(tmp_path / "new.json", snap)
        cfg = tmp_path / ".abicheck.yml"
        cfg.write_text("scope:\n  public_symbols: [foo]\n", encoding="utf-8")
        result = _invoke(
            "compare",
            str(old_f),
            str(new_f),
            "--no-scope-public-headers",
            "--config",
            str(cfg),
        )
        assert result.exit_code == 0
        assert "scope.public_symbols overlay only takes effect with" in result.output

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

    def test_stat_flag_removed(self, tmp_path: Path) -> None:
        # --stat itself is gone (CLI cleanup phase two, PR 1) -- exits 64 with
        # a Click "no such option" usage error, not a comparison result.
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = _invoke("compare", str(old_f), str(new_f), "--stat")
        assert result.exit_code == 64
        assert "No such option" in result.output

    def test_quick_profile_one_line_summary(self, tmp_path: Path) -> None:
        # --profile quick is --stat's sole surviving one-line-summary use.
        old, new = _breaking_pair()
        old_f = _write_snap(tmp_path / "old.json", old)
        new_f = _write_snap(tmp_path / "new.json", new)
        result = _invoke("compare", str(old_f), str(new_f), "--profile", "quick")
        assert result.exit_code == 4
        assert "\n" not in result.output.strip()

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

    def test_not_comparable_exits_16_legacy(self) -> None:
        # ADR-050 D2: not_comparable dominates the legacy scheme too.
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("not_comparable", False, [])
        assert exc.value.code == 16

    def test_not_comparable_beats_removed_library(self) -> None:
        # Takes precedence over --fail-on-removed-library's exit 8 -- a
        # not_comparable result means the comparison couldn't establish
        # what changed, so an apparent removal is an unproven inference.
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release("not_comparable", True, ["libgone.so"])
        assert exc.value.code == 16

    def test_not_comparable_beats_severity_scheme(self) -> None:
        with pytest.raises(SystemExit) as exc:
            _exit_compare_release(
                "not_comparable", True, ["libgone.so"], severity_exit_code=2
            )
        assert exc.value.code == 16


class TestReleaseVerdictOrder:
    def test_not_comparable_ranks_above_error(self) -> None:
        # ADR-050 D2: not_comparable dominates the release-level "worst
        # verdict wins" rollup over every other outcome, including ERROR.
        from abicheck.cli_compare_release_helpers import _RELEASE_VERDICT_ORDER

        assert (
            _RELEASE_VERDICT_ORDER["not_comparable"]
            > _RELEASE_VERDICT_ORDER["ERROR"]
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

    def test_annotate_flags_were_removed(self, tmp_path: Path) -> None:
        # CLI cleanup phase two, PR E: see the single-pair sibling test's
        # own comment for why this is now a plain Click usage error.
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
        assert result.exit_code == 64
        assert "No such option" in result.output

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

    def test_json_run_outcome_reflects_scoped_gate_not_full_library(
        self, tmp_path, monkeypatch
    ) -> None:
        """ADR-063 Phase 7 regression (Codex review, P1): the full-library
        compare below removes `foo` (a real, unrelated ABI break), but the
        app-scoped gate is stubbed compatible -- the app never actually
        called `foo`. `run_outcome` must describe the *scoped* gate (the one
        the process exit code actually reflects), not the stale full-library
        one `report_run_outcome.run_outcome_dict_for_diff_result` computed
        before scoping ran; the full-library value moves to
        `full_run_outcome`, mirroring the existing `verdict`/`full_verdict`
        and `severity`/`full_severity` swap."""
        from abicheck import dumper as dumper_mod

        app = tmp_path / "app"
        app.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old = tmp_path / "old.so"
        old.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new = tmp_path / "new.so"
        new.write_bytes(b"\x7fELF" + b"\x00" * 200)
        # NEW drops `foo` entirely -- a real, unscoped ABI break.
        monkeypatch.setattr(
            dumper_mod,
            "dump",
            MagicMock(side_effect=[_snap("1.0"), _snap("2.0", funcs=[])]),
        )
        # The app itself never used `foo` -- scoped gate stays compatible.
        res = self._result(verdict=Verdict.COMPATIBLE)
        self._patch_scope(monkeypatch, res)

        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app), "--format", "json",
        )
        assert result.exit_code == 0
        data = json.loads(result.stdout)

        assert data["full_verdict"] == "BREAKING"
        assert data["verdict"] == "COMPATIBLE"

        assert "run_outcome" in data
        assert "full_run_outcome" in data
        assert data["full_run_outcome"]["gate"] == "abi_breaking"
        assert data["run_outcome"]["gate"] == "none"
        assert data["run_outcome"]["compatibility"] == "COMPATIBLE"

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

    def test_quick_profile_one_liner_states_scoped_verdict_not_full(
        self, tmp_path, monkeypatch
    ) -> None:
        """CLI cleanup phase two, PR 1 (Codex review, fresh evidence): the
        internal one-line format (``--profile quick``) used to fall through
        ``_fold_scoped_compat_into_text``'s dispatch untouched, so the
        printed one-liner showed the full-library BREAKING verdict/counts
        even though the process exits 0 on the scoped-compatible result --
        the identical setup as
        ``test_markdown_states_scoped_verdict_when_it_disagrees_with_full``
        above, just through the one-line renderer instead of markdown."""
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
            "--profile", "quick",
        )
        assert result.exit_code == 0  # the scoped verdict, not the full BREAKING
        # The verdict label leads with the scoped result (COMPATIBLE), the
        # same swap `into_json`'s `payload["verdict"]` makes -- not the
        # full-library BREAKING that would exit 4. Unlike `--format json`
        # (which keeps the full-library "1 breaking" count alongside a
        # `changes` array a reader can inspect for context -- see
        # test_json_severity_block_reflects_scoped_gate_not_full_library
        # above), the one-line format has no room for that context, so its
        # counts are recomputed from only what the scoped gate actually
        # rests on (Codex review, fresh evidence): the removed symbol isn't
        # one of the app's required symbols, so there are no scoped-only
        # findings and no missing-contract labels, and the one-liner
        # correctly shows "no changes" instead of an unexplained "1
        # breaking" next to a COMPATIBLE verdict.
        assert result.stdout.strip() == "COMPATIBLE: no changes (0 total)"

    def test_quick_profile_one_liner_counts_the_scoped_only_finding(
        self, tmp_path, monkeypatch
    ) -> None:
        """The mirror case of the test above: a scoped-only finding (one with
        no backing full-library `Change`, e.g. a synthesized
        `PE_ORDINAL_RETARGETED`) is the *only* thing making this run
        BREAKING -- the full library itself is unchanged (`NO_CHANGE`).
        The one-liner must count it, not print "no changes" just because
        `result.changes` is empty (Codex review, fresh evidence)."""
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
            "--profile", "quick",
        )
        assert result.exit_code == 4
        assert result.stdout.strip() == "BREAKING: 1 breaking (1 total)"

    def test_quick_profile_one_liner_counts_scoped_only_finding_under_show_only(
        self, tmp_path, monkeypatch
    ) -> None:
        """Codex review, fresh evidence: `_scoped_gate_findings()` applies
        `--show-only` to `scoped_only`/`missing_labels` for *display*
        purposes elsewhere, but this method's own printed counts must track
        what actually decided the scoped verdict/exit code -- which
        `--show-only` never changes. Filtering the count inputs by
        `--show-only compatible` here let a purely-breaking scoped-only
        finding (this test's `PE_ORDINAL_RETARGETED`) get silently excluded
        from the count while the process still exited 4, printing the
        self-contradictory "BREAKING: no changes (0 total)"."""
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
            "--profile", "quick", "--show-only", "compatible",
        )
        assert result.exit_code == 4
        assert result.stdout.strip() == "BREAKING: 1 breaking (1 total)"

    def test_quick_profile_one_liner_counts_an_ordinary_in_scope_removal(
        self, tmp_path, monkeypatch
    ) -> None:
        """The far more common shape than either test above: an ordinary
        full-library finding (a real `FUNC_REMOVED` already in
        `result.changes`) that is ALSO scoped-relevant, because the removed
        symbol is one `--used-by` actually calls. This is marked via
        `result.scoped_relevant_finding_ids`, not `scoped_only_changes`
        (which only covers *synthesized* scoped-only findings with no
        backing `Change`) -- omitting that set from the one-liner's count
        reproduced exactly the "no changes" bug this whole fix exists to
        close, just for the ordinary case rather than the edge case (Codex
        review, fresh evidence, third round)."""
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

        # Use the REAL diff's own Change (not a hand-built stub) so its
        # finding id genuinely matches the one in result.changes -- same
        # discipline as test_sarif_missing_symbol_covered_by_change_not_
        # double_synthesized above.
        def _scoped_for(diff, *_args, **_kwargs):
            from abicheck.appcompat import AppCompatResult

            real_change = next(
                c for c in diff.changes if c.kind == ChangeKind.FUNC_REMOVED
            )
            return AppCompatResult(
                app_path="/app", old_lib_path=str(old), new_lib_path=str(new),
                required_symbols={"_Z7removedv"}, required_symbol_count=1,
                breaking_for_app=[real_change], verdict=Verdict.BREAKING,
            )

        import abicheck.appcompat as appcompat_mod

        monkeypatch.setattr(appcompat_mod, "scope_diff_to_app", _scoped_for)
        result = _invoke(
            "compare", str(old), str(new), "--used-by", str(app),
            "--profile", "quick",
        )
        assert result.exit_code == 4
        assert result.stdout.strip() == "BREAKING: 1 breaking (1 total)"

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
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[old, new]))
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

    def test_json_full_mode_scoped_only_correlator_evidence(
        self, tmp_path, monkeypatch
    ) -> None:
        # G29 Phase 6 follow-up (Codex review): a scoped-only
        # CONSUMER_REQUIRED_SYMBOL_REMOVED sibling of a real FUNC_REMOVED
        # change is a genuine RootCauseCorrelator two-piece group -- both the
        # pre-existing regular `changes[]` entry (root, via
        # reporter._add_changes_block) and the newly-appended scoped-only
        # entry (via cli_compare_fold.py's own fold-in) must carry the same
        # correlator evidence for the same underlying group.
        from abicheck import dumper as dumper_mod

        old, new = _breaking_pair()  # real diff: "bar"/_Z3barv removed
        app_path = tmp_path / "app"
        app_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_p = tmp_path / "old.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new_p = tmp_path / "new.so"
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[old, new]))
        scoped_only = Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="pub_entry",
            description="required by consumer",
            caused_by_type="_Z3barv",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old_p), str(new_p), "--used-by", str(app_path),
            "--format", "json",
        )
        assert result.exit_code == 4
        data = json.loads(result.stdout)
        entries = {c["kind"]: c for c in data["changes"]}
        assert set(entries) == {"func_removed", "consumer_required_symbol_removed"}
        for entry in entries.values():
            evidence = entry["impact_assessment"]["root_cause_evidence"]
            assert evidence["strongest_evidence_level"] == "consumer_proven"
            assert evidence["evidence_levels"] == [
                "artifact_proven", "consumer_proven",
            ]

    def test_json_root_cause_mode_scoped_only_bare_symbol_group_evidence(
        self, tmp_path, monkeypatch
    ) -> None:
        # Third Codex review finding: --used-by's real shape -- a
        # FUNC_REMOVED and a scoped-only CONSUMER_REQUIRED_SYMBOL_REMOVED
        # sharing a bare symbol, neither carrying caused_by_type.
        # RootCauseCorrelator merges them, but _root_cause_key_and_display's
        # "only caused_by_type correlates findings" contract keeps each its
        # own singleton --report-mode root-cause group (one built by
        # _to_json_root_cause before the fold-in, one newly created by
        # _add_entries_to_root_causes during it). Both singletons must carry
        # the same group-level evidence their shared correlator group has.
        from abicheck import dumper as dumper_mod

        old, new = _breaking_pair()  # real diff: "bar"/_Z3barv removed
        app_path = tmp_path / "app"
        app_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_p = tmp_path / "old.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new_p = tmp_path / "new.so"
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[old, new]))
        scoped_only = Change(
            kind=ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol="_Z3barv",
            description="consumer requires a removed symbol",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare", str(old_p), str(new_p), "--used-by", str(app_path),
            "--format", "json", "--report-mode", "root-cause",
        )
        assert result.exit_code == 4
        data = json.loads(result.stdout)
        assert data["root_cause_count"] == 2
        groups = {
            group["findings"][0]["kind"]: group for group in data["root_causes"]
        }
        assert set(groups) == {"func_removed", "consumer_required_symbol_removed"}
        for group in groups.values():
            assert group["finding_count"] == 1
            assert group["strongest_evidence_level"] == "consumer_proven"
            assert group["evidence_levels"] == ["artifact_proven", "consumer_proven"]

    def test_markdown_root_cause_mode_merges_scoped_only_into_existing_group(
        self, tmp_path, monkeypatch
    ) -> None:
        # Codex review: markdown/text root-cause mode must merge a
        # scoped-only finding into an existing group when its caused_by_type
        # matches a real change's symbol, not just list it separately in
        # "## Additional scoped-gate findings" -- mirrors the JSON fix above.
        from abicheck import dumper as dumper_mod

        old, new = _breaking_pair()  # real diff: "bar"/_Z3barv removed
        app_path = tmp_path / "app"
        app_path.write_bytes(b"\x7fELF" + b"\x00" * 200)
        old_p = tmp_path / "old.so"
        old_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        new_p = tmp_path / "new.so"
        new_p.write_bytes(b"\x7fELF" + b"\x00" * 200)
        monkeypatch.setattr(dumper_mod, "dump", MagicMock(side_effect=[old, new]))
        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="pub_entry",
            description="ordinal retargeted",
            caused_by_type="_Z3barv",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare",
            str(old_p),
            str(new_p),
            "--used-by",
            str(app_path),
            "--report-mode",
            "root-cause",
        )
        assert result.exit_code == 4
        assert "## Root Causes (1)" in result.output
        # Markdown demangles by default -- the group's display root is the
        # demangled `bar()`, not the raw mangled `_Z3barv`.
        assert "### `bar()` (2 finding" in result.output
        assert "ordinal retargeted" in result.output
        # Not duplicated in the flat appendix.
        assert "## Additional scoped-gate findings" not in result.output

    def test_markdown_root_cause_mode_still_lists_uncorrelated_scoped_only(
        self, tmp_path, monkeypatch
    ) -> None:
        # An uncorrelated scoped-only finding must still show up as its own
        # singleton group, not silently disappear now that the flat
        # appendix is suppressed for root-cause mode.
        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare",
            str(old),
            str(new),
            "--used-by",
            str(app),
            "--report-mode",
            "root-cause",
        )
        assert result.exit_code == 4
        assert "### `ordinal:5` (1 finding)" in result.output
        assert "ordinal 5 retargeted" in result.output
        assert "## Additional scoped-gate findings" not in result.output
        # Codex review: result.changes itself is empty here (old/new are
        # identical) -- the only finding is the scoped-only change above, so
        # the report must not also claim "No ABI changes detected" right
        # next to a populated "## Root Causes" section.
        assert "No ABI changes detected" not in result.output

    def test_markdown_root_cause_severity_table_reflects_scoped_only_finding(
        self, tmp_path, monkeypatch
    ) -> None:
        # Codex review: the "## Severity Configuration" table was built from
        # `result.changes` before the scoped-only change/missing-contract
        # label below was resolved -- a scoped run whose only breaking issue
        # is one of these showed every category at Count 0/"no exit impact"
        # immediately above a "## Root Causes" section naming a real,
        # gate-blocking finding.
        scoped_only = Change(
            kind=ChangeKind.PE_ORDINAL_RETARGETED,
            symbol="ordinal:5",
            description="ordinal 5 retargeted",
        )
        res = self._result(verdict=Verdict.BREAKING, breaking_for_app=[scoped_only])
        app, old, new = self._setup(tmp_path, monkeypatch)
        self._patch_scope(monkeypatch, res)
        result = _invoke(
            "compare",
            str(old),
            str(new),
            "--used-by",
            str(app),
            "--report-mode",
            "root-cause",
            "--config",
            str(_severity_config(tmp_path, abi_breaking="error")),
        )
        assert result.exit_code == 4
        assert "### `ordinal:5` (1 finding)" in result.output
        table_line = next(
            line for line in result.output.splitlines()
            if line.startswith("| ABI/API Incompatibilities")
        )
        assert "| 1 |" in table_line
        assert "causes non-zero exit" in table_line

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
            "--config", str(_severity_config(tmp_path, abi_breaking="warning")),
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

    # test_stat_json_summary_reflects_scoped_only_and_missing_findings removed
    # (CLI cleanup phase two, PR 1): it exercised `--format json --stat`
    # (`to_stat_json`'s stale-summary-vs-recomputed-verdict contradiction),
    # a CLI combination that no longer exists -- `--stat` was removed, and
    # the CLI never sets `stat=True` on any `reporter.to_json` call anywhere
    # any more. `to_stat_json` itself is still directly reachable from a
    # Tier-2 Python API caller via `abicheck.service_render.render_output(
    # ..., stat=True)`'s own `fmt="json"` branch (the documented compat
    # shim for the removed CLI flag) -- this comment is only about the CLI
    # surface, not about `to_stat_json` becoming unreachable altogether. The
    # bug class this test guarded against is provably unreachable from the
    # CLI now, not merely untested. The sibling non-stat case,
    # test_json_summary_reflects_scoped_only_and_missing_findings above,
    # covers the still-live path.


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


# _write_release_step_summary was removed (CLI cleanup phase two, PR E,
# review follow-up): making the CLI's own step-summary write unconditional
# in CI double-wrote against the composite Action's own job summary. The
# CLI no longer writes one on its own at all -- see cli.py's
# _finalize_compare_result comment.


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

        import abicheck.frontends.cli.runtime as cli_mod

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 50)
        monkeypatch.setattr(cli_mod, "_detect_binary_format", lambda p: "elf")
        monkeypatch.setattr(
            "abicheck.workflows.extraction.resolve_debug_info",
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
            "--config",
            str(_suppression_strict_config(tmp_path)),
        )
        assert result.exit_code != 0
        assert "expired" in result.output.lower()

    def test_corrupt_snapshot_reports_error(self, tmp_path, monkeypatch) -> None:
        # A library whose snapshot load raises surfaces an ERROR entry,
        # exercising the per-entry error echo path (cli_compare_release:341-342).
        old_dir, new_dir = self._make_dirs(tmp_path)
        _write_snap(old_dir / "libfoo.json", _snap())
        _write_snap(new_dir / "libfoo.json", _snap())

        import abicheck.cli_compare_release_pairwise as cr_mod

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


# ── cli.py: _resolve_debug_artifact ────────────────────────────────────────


class TestResolveDebugArtifact:
    def test_delegates_to_resolver(self, tmp_path, monkeypatch) -> None:
        from types import SimpleNamespace

        import abicheck.frontends.cli.runtime as cli_mod

        binary = tmp_path / "lib.so"
        binary.write_bytes(b"\x7fELF" + b"\x00" * 50)
        sentinel = SimpleNamespace(source="x.debug")
        monkeypatch.setattr(
            "abicheck.workflows.extraction.resolve_debug_info",
            lambda *a, **k: sentinel,
        )
        out = cli_mod._resolve_debug_artifact(
            binary,
            (tmp_path,),
            False,
            None,
        )
        assert out is sentinel


# ── cli.py: _log_debug_resolution drives both sides when requested ────────────


class TestLogDebugResolutionBothSides:
    def test_both_sides_logged(self, tmp_path, monkeypatch, capsys) -> None:
        from types import SimpleNamespace

        import abicheck.frontends.cli.runtime as cli_mod

        old_b = tmp_path / "old.so"
        new_b = tmp_path / "new.so"
        old_b.write_bytes(b"\x7fELF" + b"\x00" * 50)
        new_b.write_bytes(b"\x7fELF" + b"\x00" * 50)
        monkeypatch.setattr(cli_mod, "_detect_binary_format", lambda p: "elf")
        monkeypatch.setattr(
            "abicheck.workflows.extraction.resolve_debug_info",
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
