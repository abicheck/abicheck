"""Coverage tests for cli.py — target 100% on non-compat code paths.

Tests _sniff_text_format edge cases, _resolve_input error paths,
dump_cmd stdout output, compare_cmd ignored-flags warnings,
and policy-file warning.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import _resolve_input, _sniff_text_format, main
from abicheck.model import AbiSnapshot
from abicheck.serialization import snapshot_to_json

# ── _sniff_text_format ─────────────────────────────────────────────────

class TestSniffTextFormat:
    def test_oserror_returns_unknown(self, tmp_path):
        """When opening the file raises OSError, return 'unknown'."""
        missing = tmp_path / "no_such_file.txt"
        assert _sniff_text_format(missing) == "unknown"

    def test_perl_dump_detected(self, tmp_path):
        """A file starting with $VAR1 is detected as perl format."""
        f = tmp_path / "dump.pl"
        f.write_text("$VAR1 = {\n  'key' => 'value'\n};", encoding="utf-8")
        assert _sniff_text_format(f) == "perl"

    def test_json_detected(self, tmp_path):
        """A file starting with '{' is detected as json format."""
        f = tmp_path / "snap.json"
        f.write_text('{"library": "test"}', encoding="utf-8")
        assert _sniff_text_format(f) == "json"

    def test_unknown_content(self, tmp_path):
        """Unrecognized content returns 'unknown'."""
        f = tmp_path / "mystery.txt"
        f.write_text("neither json nor perl", encoding="utf-8")
        assert _sniff_text_format(f) == "unknown"


# ── _resolve_input error paths ─────────────────────────────────────────

class TestResolveInputErrors:
    def test_elf_bad_include_dir(self, tmp_path, monkeypatch):
        """Include directory that doesn't exist raises ClickException."""
        import click
        import pytest

        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 100)
        hdr = tmp_path / "test.h"
        hdr.write_text("int f();", encoding="utf-8")

        bad_inc = tmp_path / "nonexistent_inc"
        with pytest.raises(click.ClickException, match="Include directory not found"):
            _resolve_input(so, [hdr], [bad_inc], "1.0", "c++", is_elf=True)

    def test_elf_dump_error(self, tmp_path, monkeypatch):
        """When dump() raises, _resolve_input wraps it in ClickException."""
        import click
        import pytest

        from abicheck.errors import AbicheckError

        so = tmp_path / "lib.so"
        so.write_bytes(b"\x7fELF" + b"\x00" * 100)
        hdr = tmp_path / "test.h"
        hdr.write_text("int f();", encoding="utf-8")

        monkeypatch.setattr(
            "abicheck.cli_dump_helpers.dump",
            lambda **_kw: (_ for _ in ()).throw(AbicheckError("castxml died")),
        )
        with pytest.raises(click.ClickException, match="Failed to dump"):
            _resolve_input(so, [hdr], [], "1.0", "c++", is_elf=True)

    def test_perl_import_error(self, tmp_path, monkeypatch):
        """When perl dump import fails, _resolve_input wraps the error."""
        import click
        import pytest

        f = tmp_path / "bad.dump"
        f.write_text("$VAR1 = {\n  broken\n};", encoding="utf-8")

        monkeypatch.setattr(
            "abicheck.cli.import_abicc_perl_dump",
            lambda _p: (_ for _ in ()).throw(ValueError("parse error")),
        )
        with pytest.raises(click.ClickException, match="Failed to import ABICC Perl dump"):
            _resolve_input(f, [], [], "1.0", "c++", is_elf=False)

    def test_unknown_format_error(self, tmp_path):
        """A file with unrecognized format raises UsageError."""
        import click
        import pytest

        f = tmp_path / "mystery.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        with pytest.raises(click.UsageError, match="Cannot detect format"):
            _resolve_input(f, [], [], "1.0", "c++", is_elf=False)

    def test_json_load_error(self, tmp_path, monkeypatch):
        """When JSON snapshot loading fails, _resolve_input wraps the error."""
        import click
        import pytest

        f = tmp_path / "bad.json"
        f.write_text("{invalid json", encoding="utf-8")

        monkeypatch.setattr(
            "abicheck.cli.load_snapshot",
            lambda _p: (_ for _ in ()).throw(ValueError("bad json")),
        )
        with pytest.raises(click.ClickException, match="Failed to load JSON snapshot"):
            _resolve_input(f, [], [], "1.0", "c++", is_elf=False)


# ── dump_cmd stdout ────────────────────────────────────────────────────

class TestDumpCmdStdout:
    def test_dump_to_stdout(self, tmp_path, monkeypatch):
        """dump command without -o writes JSON to stdout."""
        so = tmp_path / "libfoo.so"
        so.write_bytes(b"\x7fELF")
        hdr = tmp_path / "foo.h"
        hdr.write_text("int foo();", encoding="utf-8")

        snap = AbiSnapshot(library="libfoo.so", version="1.0")
        monkeypatch.setattr("abicheck.cli_dump_helpers.dump", lambda **_kw: snap)

        runner = CliRunner()
        result = runner.invoke(main, ["dump", str(so), "-H", str(hdr)])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert parsed["library"] == "libfoo.so"


# ── compare_cmd ignored-flags warnings ─────────────────────────────────

def _make_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    old = AbiSnapshot(library="lib.so", version="1.0")
    new = AbiSnapshot(library="lib.so", version="2.0")
    old_p = tmp_path / "old.json"
    new_p = tmp_path / "new.json"
    old_p.write_text(snapshot_to_json(old), encoding="utf-8")
    new_p.write_text(snapshot_to_json(new), encoding="utf-8")
    return old_p, new_p


class TestCompareIgnoredFlagsWarnings:
    @pytest.mark.parametrize("flag,arg_maker,expected_warning", [
        ("-I", "dir", "-I/--include"),
        ("--old-header", "file", "--old-header"),
        ("--new-header", "file", "--new-header"),
        ("--old-include", "dir", "--old-include"),
        ("--new-include", "dir", "--new-include"),
    ])
    def test_ignored_flag_warning(self, tmp_path, flag, arg_maker, expected_warning):
        """Header/include flags are warned when both inputs are snapshots."""
        old_p, new_p = _make_snapshots(tmp_path)
        if arg_maker == "dir":
            arg = tmp_path / "inc"
            arg.mkdir(exist_ok=True)
        else:
            arg = tmp_path / "h.h"
            arg.write_text("int f();", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), flag, str(arg),
        ])
        assert result.exit_code == 0
        assert expected_warning in result.output


# ── compare_cmd policy-file warning ────────────────────────────────────

class TestComparePolicyFileWarning:
    def test_policy_ignored_when_policy_file_given(self, tmp_path):
        """When --policy-file is given, --policy is warned as ignored."""
        old_p, new_p = _make_snapshots(tmp_path)

        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text(
            "base_policy: strict_abi\noverrides: {}\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p),
            "--policy", "sdk_vendor",
            "--policy-file", str(policy_file),
        ])
        assert result.exit_code == 0
        assert "ignored" in result.output.lower()


# ── compare_cmd policy-file error paths ────────────────────────────────

class TestComparePolicyFileErrors:
    def test_policy_file_import_error(self, tmp_path, monkeypatch):
        """ImportError from PolicyFile.load raises ClickException."""
        old_p, new_p = _make_snapshots(tmp_path)
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text("bad: policy\n", encoding="utf-8")

        monkeypatch.setattr(
            "abicheck.policy_file.PolicyFile.load",
            classmethod(lambda cls, _p: (_ for _ in ()).throw(ImportError("missing dep"))),
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p),
            "--policy-file", str(policy_file),
        ])
        assert result.exit_code != 0
        assert "missing dep" in result.output

    def test_policy_file_value_error(self, tmp_path, monkeypatch):
        """ValueError from PolicyFile.load raises BadParameter."""
        old_p, new_p = _make_snapshots(tmp_path)
        policy_file = tmp_path / "policy.yaml"
        policy_file.write_text("bad: policy\n", encoding="utf-8")

        monkeypatch.setattr(
            "abicheck.policy_file.PolicyFile.load",
            classmethod(lambda cls, _p: (_ for _ in ()).throw(ValueError("invalid format"))),
        )

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p),
            "--policy-file", str(policy_file),
        ])
        assert result.exit_code != 0
        assert "invalid format" in result.output


# ── compare_cmd API_BREAK exit code ────────────────────────────────────

class TestCompareApiBreakExitCode:
    def test_api_break_exits_2(self, tmp_path, monkeypatch):
        """When verdict is API_BREAK, exit code is 2."""
        from abicheck.checker import Change, ChangeKind, DiffResult, Verdict

        old_p = tmp_path / "old.json"
        new_p = tmp_path / "new.json"
        old_p.write_text("{}", encoding="utf-8")
        new_p.write_text("{}", encoding="utf-8")

        snap = AbiSnapshot(library="lib.so", version="1.0")
        monkeypatch.setattr("abicheck.cli.load_snapshot", lambda _: snap)
        monkeypatch.setattr(
            "abicheck.cli.compare",
            lambda *_a, **_kw: DiffResult(
                old_version="1", new_version="2", library="lib.so",
                verdict=Verdict.API_BREAK,
                changes=[Change(ChangeKind.FUNC_REMOVED, "foo", "removed")],
            ),
        )
        monkeypatch.setattr("abicheck.service.to_markdown", lambda _r, **_kw: "API_BREAK REPORT")

        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 2
