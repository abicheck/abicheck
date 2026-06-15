"""Tests for compare command input auto-detection.

Covers: .so + .so, .json + .json, .json + .so (mixed), error paths,
and per-side header options (--old-header / --new-header).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.binary_utils import detect_archive, detect_binary_format
from abicheck.cli import _resolve_input, main
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.serialization import snapshot_to_json

# ── helpers ──────────────────────────────────────────────────────────────


def _make_snapshot(version: str = "1.0", funcs: list[Function] | None = None) -> AbiSnapshot:
    if funcs is None:
        funcs = [
            Function(name="foo", mangled="_Z3foov", return_type="int",
                     visibility=Visibility.PUBLIC),
        ]
    return AbiSnapshot(library="libtest.so", version=version, functions=funcs)


def _write_snapshot(path: Path, snap: AbiSnapshot | None = None) -> Path:
    if snap is None:
        snap = _make_snapshot()
    path.write_text(snapshot_to_json(snap), encoding="utf-8")
    return path


def _write_fake_elf(path: Path) -> Path:
    """Write a minimal file with ELF magic bytes."""
    path.write_bytes(b"\x7fELF" + b"\x00" * 64)
    return path


def _write_snapshots(tmp_path: Path) -> tuple[Path, Path]:
    old_p = _write_snapshot(tmp_path / "old.json", _make_snapshot("1.0"))
    new_p = _write_snapshot(tmp_path / "new.json", _make_snapshot("2.0"))
    return old_p, new_p


def _breaking_snapshots(tmp_path: Path) -> tuple[AbiSnapshot, AbiSnapshot]:
    old = _make_snapshot("1.0", [
        Function(name="foo", mangled="_Z3foov", return_type="int",
                 visibility=Visibility.PUBLIC),
        Function(name="bar", mangled="_Z3barv", return_type="void",
                 visibility=Visibility.PUBLIC),
    ])
    new = _make_snapshot("2.0", [
        Function(name="foo", mangled="_Z3foov", return_type="int",
                 visibility=Visibility.PUBLIC),
    ])
    return old, new


# ── detect_binary_format tests ─────────────────────────────────────────


class TestDetectBinaryFormat:
    def test_elf_file_detected(self, tmp_path):
        p = _write_fake_elf(tmp_path / "lib.so")
        assert detect_binary_format(p) == "elf"

    def test_json_file_not_elf(self, tmp_path):
        p = tmp_path / "snap.json"
        p.write_text('{"library": "test"}', encoding="utf-8")
        assert detect_binary_format(p) is None

    def test_nonexistent_file(self, tmp_path):
        assert detect_binary_format(tmp_path / "missing") is None

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty"
        p.write_bytes(b"")
        assert detect_binary_format(p) is None

    def test_short_file(self, tmp_path):
        p = tmp_path / "short"
        p.write_bytes(b"\x7f")
        assert detect_binary_format(p) is None

    def test_pe_file_detected(self, tmp_path):
        p = tmp_path / "lib.dll"
        p.write_bytes(b"MZ" + b"\x00" * 64)
        assert detect_binary_format(p) == "pe"

    def test_macho_file_detected(self, tmp_path):
        p = tmp_path / "lib.dylib"
        p.write_bytes(b"\xfe\xed\xfa\xce" + b"\x00" * 64)
        assert detect_binary_format(p) == "macho"

    def test_ar_archive_is_not_a_binary_format(self, tmp_path):
        """ar archives (.a/.lib) are not single linkable images — detect_binary_format
        returns None for them (they are handled separately via detect_archive)."""
        p = tmp_path / "libfoo.a"
        p.write_bytes(b"!<arch>\n" + b"\x00" * 64)
        assert detect_binary_format(p) is None


class TestDetectArchive:
    def test_static_archive_detected(self, tmp_path):
        p = tmp_path / "libfoo.a"
        p.write_bytes(b"!<arch>\n" + b"\x00" * 64)
        assert detect_archive(p) is True

    def test_coff_import_library_detected(self, tmp_path):
        # MSVC .lib import/static libraries share the ar magic.
        p = tmp_path / "foo.lib"
        p.write_bytes(b"!<arch>\n" + b"\x00" * 64)
        assert detect_archive(p) is True

    def test_gnu_thin_archive_detected(self, tmp_path):
        # GNU thin archives (`ar rcT`) use the `!<thin>\n` magic.
        p = tmp_path / "libfoo.a"
        p.write_bytes(b"!<thin>\n" + b"\x00" * 64)
        assert detect_archive(p) is True

    def test_elf_is_not_archive(self, tmp_path):
        p = _write_fake_elf(tmp_path / "lib.so")
        assert detect_archive(p) is False

    def test_nonexistent_is_not_archive(self, tmp_path):
        assert detect_archive(tmp_path / "missing") is False

    def test_short_file_is_not_archive(self, tmp_path):
        p = tmp_path / "short"
        p.write_bytes(b"!<ar")
        assert detect_archive(p) is False


# ── _resolve_input tests ────────────────────────────────────────────────


class TestResolveInput:
    def test_json_snapshot_loaded(self, tmp_path):
        snap = _make_snapshot("1.0")
        p = _write_snapshot(tmp_path / "snap.json", snap)
        result = _resolve_input(p, headers=[], includes=[], version="1.0", lang="c++")
        assert result.library == "libtest.so"
        assert result.version == "1.0"

    def test_elf_without_headers_raises(self, tmp_path):
        p = _write_fake_elf(tmp_path / "lib.so")
        with pytest.raises(Exception, match="header"):
            _resolve_input(p, headers=[], includes=[], version="1.0", lang="c++")

    def test_unknown_format_raises(self, tmp_path):
        p = tmp_path / "mystery.dat"
        p.write_text("not json, not perl, not elf", encoding="utf-8")
        with pytest.raises(Exception, match="Cannot detect format"):
            _resolve_input(p, headers=[], includes=[], version="1.0", lang="c++")

    def test_static_archive_raises_with_guidance(self, tmp_path):
        """A `.a`/`.lib` archive is rejected with actionable guidance at the CLI
        layer (G8 non-goal), not the generic 'Cannot detect format' error."""
        p = tmp_path / "libfoo.a"
        p.write_bytes(b"!<arch>\n" + b"\x00" * 16)
        with pytest.raises(Exception, match="static/import library archive"):
            _resolve_input(p, headers=[], includes=[], version="1.0", lang="c++")

    def test_malformed_json_raises_click_exception(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text('{"not_a_valid_snapshot": true}', encoding="utf-8")
        with pytest.raises(Exception, match="Failed to load JSON snapshot"):
            _resolve_input(p, headers=[], includes=[], version="1.0", lang="c++")

    def test_elf_with_missing_header_raises(self, tmp_path):
        p = _write_fake_elf(tmp_path / "lib.so")
        missing = tmp_path / "nonexistent.h"
        with pytest.raises(Exception, match="Header file not found"):
            _resolve_input(
                p, headers=[missing], includes=[], version="1.0", lang="c++",
            )


# ── GNU ld linker-script inputs (the libfoo.so dev symlink case) ─────────


class TestLinkerScriptInput:
    """The conventional ``libfoo.so`` dev symlink is often a GNU ld linker
    script (``INPUT(libfoo.so.1)``) rather than an ELF file. abicheck should
    follow it to the real shared library instead of failing."""

    def test_follows_input_directive(self, tmp_path):
        from abicheck.cli import _resolve_linker_script
        target = _write_fake_elf(tmp_path / "libfoo.so.1")
        script = tmp_path / "libfoo.so"
        script.write_text("INPUT(libfoo.so.1)\n", encoding="utf-8")
        resolved, is_ld = _resolve_linker_script(script)
        assert is_ld is True
        assert resolved == target

    def test_follows_group_with_comment_and_as_needed(self, tmp_path):
        from abicheck.cli import _resolve_linker_script
        target = _write_fake_elf(tmp_path / "libbar.so.6")
        script = tmp_path / "libbar.so"
        script.write_text(
            "/* GNU ld script */\nGROUP ( libbar.so.6 AS_NEEDED ( ld.so ) )\n",
            encoding="utf-8",
        )
        resolved, is_ld = _resolve_linker_script(script)
        assert is_ld is True
        assert resolved == target

    def test_follows_absolute_path_target(self, tmp_path):
        from abicheck.cli import _resolve_linker_script
        target = _write_fake_elf(tmp_path / "libabs.so.2")
        script = tmp_path / "libabs.so"
        script.write_text(f"INPUT({target})\n", encoding="utf-8")
        resolved, is_ld = _resolve_linker_script(script)
        assert is_ld is True
        assert resolved == target

    def test_plain_text_is_not_a_linker_script(self, tmp_path):
        from abicheck.cli import _resolve_linker_script
        p = tmp_path / "notes.txt"
        p.write_text("this is just text, not a script", encoding="utf-8")
        resolved, is_ld = _resolve_linker_script(p)
        assert is_ld is False
        assert resolved is None

    def test_maybe_follow_returns_original_for_non_script(self, tmp_path):
        from abicheck.cli import _maybe_follow_linker_script
        p = _write_fake_elf(tmp_path / "real.so.1")
        assert _maybe_follow_linker_script(p) == p

    def test_resolve_input_follows_script_to_elf_path(self, tmp_path):
        # Following the script must reach the ELF parser (error references the
        # resolved target) rather than a generic "Cannot detect format".
        _write_fake_elf(tmp_path / "libfollow.so.1")
        script = tmp_path / "libfollow.so"
        script.write_text("INPUT(libfollow.so.1)\n", encoding="utf-8")
        with pytest.raises(Exception, match="libfollow.so.1"):
            _resolve_input(
                script, headers=[], includes=[], version="1.0", lang="c++",
            )

    def test_unresolvable_script_gives_targeted_error(self, tmp_path):
        script = tmp_path / "libmissing.so"
        script.write_text("INPUT(libmissing.so.9)\n", encoding="utf-8")
        with pytest.raises(Exception, match="linker script"):
            _resolve_input(
                script, headers=[], includes=[], version="1.0", lang="c++",
            )

    def test_normalize_binary_input_follows_script_and_reports_elf(self, tmp_path):
        # Resolving at the caller is what lets downstream metadata/dependency
        # analysis use the real DSO instead of the text script.
        from abicheck.cli import _normalize_binary_input
        _write_fake_elf(tmp_path / "libn.so.1")
        script = tmp_path / "libn.so"
        script.write_text("INPUT(libn.so.1)\n", encoding="utf-8")
        resolved, fmt = _normalize_binary_input(script)
        assert resolved == tmp_path / "libn.so.1"
        assert fmt == "elf"

    def test_normalize_binary_input_passthrough_for_elf(self, tmp_path):
        from abicheck.cli import _normalize_binary_input
        p = _write_fake_elf(tmp_path / "plain.so.1")
        assert _normalize_binary_input(p) == (p, "elf")

    def test_normalize_binary_input_unknown_text(self, tmp_path):
        from abicheck.cli import _normalize_binary_input
        p = tmp_path / "notes.txt"
        p.write_text("just prose", encoding="utf-8")
        assert _normalize_binary_input(p) == (p, None)

    def test_dump_cli_follows_linker_script(self, tmp_path):
        # `dump` should follow the script (note on stderr) and then fail at the
        # ELF parser for the (fake) target — not "Cannot detect format".
        _write_fake_elf(tmp_path / "libd.so.1")
        script = tmp_path / "libd.so"
        script.write_text("INPUT(libd.so.1)\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["dump", str(script), "--dwarf-only"])
        assert result.exit_code != 0
        assert "GNU ld linker script" in result.output
        assert "Cannot detect format" not in result.output


# ── compare with .json + .json (backward compat) ────────────────────────


class TestCompareJsonJson:
    def test_no_change(self, tmp_path):
        old_p, new_p = _write_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 0
        assert "NO_CHANGE" in result.output

    def test_breaking(self, tmp_path):
        old, new = _breaking_snapshots(tmp_path)
        old_p = _write_snapshot(tmp_path / "old.json", old)
        new_p = _write_snapshot(tmp_path / "new.json", new)
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_p), str(new_p)])
        assert result.exit_code == 4

    def test_headers_ignored_warning(self, tmp_path):
        """When both inputs are JSON, -H should emit a warning."""
        old_p, new_p = _write_snapshots(tmp_path)
        hdr = tmp_path / "dummy.h"
        hdr.write_text("// dummy", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p), "-H", str(hdr),
        ])
        assert result.exit_code == 0
        assert "ignored" in result.output.lower()

    def test_per_side_headers_ignored_warning(self, tmp_path):
        """When both inputs are JSON, --old-header/--new-header should warn."""
        old_p, new_p = _write_snapshots(tmp_path)
        hdr = tmp_path / "dummy.h"
        hdr.write_text("// dummy", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p),
            "--old-header", str(hdr), "--new-header", str(hdr),
        ])
        assert result.exit_code == 0
        assert "--old-header" in result.output
        assert "--new-header" in result.output
        assert "ignored" in result.output.lower()


# ── compare with .so + .so (primary flow, monkeypatched) ────────────────


class TestCompareSoSo:
    def test_so_vs_so_with_shared_headers(self, tmp_path, monkeypatch):
        """Compare two .so files with -H (same header for both)."""
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")
        hdr = tmp_path / "foo.h"
        hdr.write_text("int foo(void);", encoding="utf-8")

        old_snap = _make_snapshot("old")
        new_snap = _make_snapshot("new")

        call_count = [0]

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            call_count[0] += 1
            if "v1" in str(so_path):
                return old_snap
            return new_snap

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_elf), str(new_elf), "-H", str(hdr),
        ])
        assert result.exit_code == 0, result.output
        assert call_count[0] == 2  # dump called for both sides

    def test_so_vs_so_with_per_side_headers(self, tmp_path, monkeypatch):
        """Compare two .so files with --old-header / --new-header."""
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")
        old_hdr = tmp_path / "v1.h"
        new_hdr = tmp_path / "v2.h"
        old_hdr.write_text("int foo(void);", encoding="utf-8")
        new_hdr.write_text("int foo(void); int bar(void);", encoding="utf-8")

        recorded_headers: list[list[Path]] = []

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            recorded_headers.append(list(headers))
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_elf), str(new_elf),
            "--old-header", str(old_hdr),
            "--new-header", str(new_hdr),
        ])
        assert result.exit_code == 0, result.output
        assert len(recorded_headers) == 2
        # old side got old_hdr, new side got new_hdr
        assert recorded_headers[0] == [old_hdr]
        assert recorded_headers[1] == [new_hdr]

    def test_so_without_headers_falls_back_to_symbols_only(self, tmp_path, monkeypatch):
        """Passing .so files without -H falls back to symbols-only with warning."""
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_elf), str(new_elf)])
        assert result.exit_code == 0, result.output
        out = result.output.lower()
        assert "symbols-only mode" in out or "weaker" in out

    def test_so_vs_so_with_version_labels(self, tmp_path, monkeypatch):
        """--old-version / --new-version are passed through to dump."""
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")
        hdr = tmp_path / "foo.h"
        hdr.write_text("int foo(void);", encoding="utf-8")

        recorded_versions: list[str] = []

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            recorded_versions.append(version)
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_elf), str(new_elf), "-H", str(hdr),
            "--old-version", "1.0", "--new-version", "2.0",
        ])
        assert result.exit_code == 0, result.output
        assert recorded_versions == ["1.0", "2.0"]

    def test_so_vs_so_with_includes(self, tmp_path, monkeypatch):
        """Verify -I / --old-include / --new-include are passed correctly."""
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")
        hdr = tmp_path / "foo.h"
        hdr.write_text("int foo(void);", encoding="utf-8")
        inc_dir = tmp_path / "inc"
        inc_dir.mkdir()

        recorded_includes: list[list[Path]] = []

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            recorded_includes.append(list(extra_includes or []))
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_elf), str(new_elf),
            "-H", str(hdr), "-I", str(inc_dir),
        ])
        assert result.exit_code == 0, result.output
        assert len(recorded_includes) == 2
        assert recorded_includes[0] == [inc_dir]
        assert recorded_includes[1] == [inc_dir]


# ── compare mixed mode: .json + .so ─────────────────────────────────────


class TestCompareMixed:
    def test_json_vs_so(self, tmp_path, monkeypatch):
        """Baseline snapshot (.json) vs current build (.so)."""
        old_p = _write_snapshot(tmp_path / "baseline.json", _make_snapshot("1.0"))
        new_elf = _write_fake_elf(tmp_path / "libv2.so")
        hdr = tmp_path / "foo.h"
        hdr.write_text("int foo(void);", encoding="utf-8")

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_elf), "-H", str(hdr),
        ])
        assert result.exit_code == 0, result.output
        assert "NO_CHANGE" in result.output

    def test_so_vs_json(self, tmp_path, monkeypatch):
        """Current build (.so) vs stored snapshot (.json)."""
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_p = _write_snapshot(tmp_path / "release.json", _make_snapshot("2.0"))
        hdr = tmp_path / "foo.h"
        hdr.write_text("int foo(void);", encoding="utf-8")

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_elf), str(new_p), "-H", str(hdr),
        ])
        assert result.exit_code == 0, result.output

    def test_mixed_breaking_detection(self, tmp_path, monkeypatch):
        """Mixed mode detects breaking changes correctly."""
        old_snap, new_snap = _breaking_snapshots(tmp_path)
        old_p = _write_snapshot(tmp_path / "baseline.json", old_snap)
        new_elf = _write_fake_elf(tmp_path / "libv2.so")
        hdr = tmp_path / "foo.h"
        hdr.write_text("int foo(void);", encoding="utf-8")

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            return new_snap

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_elf), "-H", str(hdr),
        ])
        assert result.exit_code == 4  # BREAKING

    def test_mixed_with_per_side_headers(self, tmp_path, monkeypatch):
        """--new-header only applies to the .so side, JSON side ignores headers."""
        old_p = _write_snapshot(tmp_path / "baseline.json", _make_snapshot("1.0"))
        new_elf = _write_fake_elf(tmp_path / "libv2.so")
        new_hdr = tmp_path / "v2.h"
        new_hdr.write_text("int foo(void);", encoding="utf-8")

        recorded_headers: list[list[Path]] = []

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            recorded_headers.append(list(headers))
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_elf),
            "--new-header", str(new_hdr),
        ])
        assert result.exit_code == 0, result.output
        # dump should only be called for the .so side (new)
        assert len(recorded_headers) == 1
        assert recorded_headers[0] == [new_hdr]


# ── compare with output formats (using .so inputs) ──────────────────────


class TestCompareSoOutputFormats:
    def _run_with_format(self, tmp_path, monkeypatch, fmt, extra_args=None):
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")
        hdr = tmp_path / "foo.h"
        hdr.write_text("int foo(void);", encoding="utf-8")

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        args = ["compare", str(old_elf), str(new_elf), "-H", str(hdr),
                "--format", fmt]
        if extra_args:
            args.extend(extra_args)

        runner = CliRunner()
        return runner.invoke(main, args)

    def test_json_format(self, tmp_path, monkeypatch):
        result = self._run_with_format(tmp_path, monkeypatch, "json")
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "verdict" in parsed

    def test_sarif_format(self, tmp_path, monkeypatch):
        out = tmp_path / "abi.sarif"
        result = self._run_with_format(tmp_path, monkeypatch, "sarif",
                                       ["-o", str(out)])
        assert result.exit_code == 0
        assert out.exists()

    def test_html_format(self, tmp_path, monkeypatch):
        result = self._run_with_format(tmp_path, monkeypatch, "html")
        assert result.exit_code == 0
        assert "<html" in result.output.lower()

    def test_markdown_format(self, tmp_path, monkeypatch):
        result = self._run_with_format(tmp_path, monkeypatch, "markdown")
        assert result.exit_code == 0


# ── compare help output ─────────────────────────────────────────────────


class TestCompareHelp:
    def test_nonexistent_header_ok_for_snapshots(self, tmp_path):
        """Headers with non-existent paths should not block snapshot-only runs."""
        old_p, new_p = _write_snapshots(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_p), str(new_p),
            "-H", str(tmp_path / "nonexistent.h"),
        ])
        # Should succeed (with warning) — header is ignored for snapshot inputs
        assert result.exit_code == 0
        assert "ignored" in result.output.lower()

    def test_help_shows_new_options(self):
        runner = CliRunner()
        result = runner.invoke(main, ["compare", "--help"])
        assert result.exit_code == 0
        for flag in ["-H", "--header", "--old-header", "--new-header",
                     "--old-version", "--new-version", "--old-include",
                     "--new-include", "--lang"]:
            assert flag in result.output, f"{flag} not in help output"

    def test_help_shows_examples(self):
        runner = CliRunner()
        result = runner.invoke(main, ["compare", "--help"])
        assert "libfoo.so" in result.output
        assert "baseline.json" in result.output


# ── ELF compare without headers (default symbols-only fallback) ─────────────

class TestElfNoHeaderFallback:
    """ELF compare without headers should fallback to symbols-only with warning."""

    def test_elf_no_header_defaults_to_symbols_only_warning(self, tmp_path, monkeypatch):
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")

        def mock_dump(so_path, headers, extra_includes=None, version="unknown",
                      lang="c++", **kw):
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)
        runner = CliRunner()
        result = runner.invoke(main, ["compare", str(old_elf), str(new_elf)])
        assert result.exit_code == 0, result.output
        out = result.output.lower()
        assert "symbols-only mode" in out or "weaker" in out


class TestHeaderDirectoryInput:
    def test_header_directory_is_expanded_recursively(self, tmp_path, monkeypatch):
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")

        hdr_dir = tmp_path / "include"
        (hdr_dir / "sub").mkdir(parents=True)
        h1 = hdr_dir / "api.h"
        h2 = hdr_dir / "sub" / "detail.hpp"
        h3 = hdr_dir / "sub" / "x.hh"
        h1.write_text("int foo(void);", encoding="utf-8")
        h2.write_text("int bar(void);", encoding="utf-8")
        h3.write_text("int baz(void);", encoding="utf-8")

        captured: list[list[Path]] = []

        def mock_dump(so_path, headers, extra_includes=None, version="unknown", lang="c++", **kw):
            captured.append(list(headers))
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)

        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_elf), str(new_elf), "-H", str(hdr_dir)
        ])
        assert result.exit_code == 0, result.output
        assert len(captured) == 2  # old + new side
        expected = sorted([h1, h2, h3], key=lambda p: str(p))
        assert captured[0] == expected
        assert captured[1] == expected

    def test_old_new_header_directories(self, tmp_path, monkeypatch):
        """--old-header/--new-header accept directories and expand recursively."""
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")

        old_dir = tmp_path / "include_v1"
        new_dir = tmp_path / "include_v2"
        (old_dir / "sub").mkdir(parents=True)
        (new_dir / "sub").mkdir(parents=True)
        old_h = old_dir / "sub" / "foo.h"
        new_h = new_dir / "sub" / "foo.h"
        old_h.write_text("int foo(int);", encoding="utf-8")
        new_h.write_text("int foo(double);", encoding="utf-8")

        captured: list[list[Path]] = []

        def mock_dump(so_path, headers, extra_includes=None, version="unknown", lang="c++", **kw):
            captured.append(list(headers))
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_elf), str(new_elf),
            "--old-header", str(old_dir),
            "--new-header", str(new_dir),
        ])
        assert result.exit_code == 0, result.output
        assert len(captured) == 2
        assert captured[0] == [old_h]
        assert captured[1] == [new_h]

    def test_mixed_header_file_and_directory(self, tmp_path, monkeypatch):
        """Mixing file and directory inputs in -H should work and deduplicate."""
        old_elf = _write_fake_elf(tmp_path / "libv1.so")
        new_elf = _write_fake_elf(tmp_path / "libv2.so")

        hdr_dir = tmp_path / "include"
        hdr_dir.mkdir()
        h1 = hdr_dir / "foo.h"
        h2 = hdr_dir / "bar.hpp"
        h1.write_text("int foo(void);", encoding="utf-8")
        h2.write_text("int bar(void);", encoding="utf-8")

        captured: list[list[Path]] = []

        def mock_dump(so_path, headers, extra_includes=None, version="unknown", lang="c++", **kw):
            captured.append(list(headers))
            return _make_snapshot(version)

        monkeypatch.setattr("abicheck.cli_resolve.dump", mock_dump)
        runner = CliRunner()
        result = runner.invoke(main, [
            "compare", str(old_elf), str(new_elf),
            "-H", str(hdr_dir), "-H", str(h1),
        ])
        assert result.exit_code == 0, result.output
        expected = sorted([h1, h2], key=lambda p: str(p))
        assert captured[0] == expected
        assert captured[1] == expected
