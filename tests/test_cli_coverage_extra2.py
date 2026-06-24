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

"""Extra CLI coverage tests for private helper functions.

Covers: _expand_header_inputs, _setup_verbosity, _safe_write_output,
_stamp_provenance, _sniff_text_format, _detect_binary_format,
and _write_snapshot_output.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import click
import pytest

from abicheck.cli import (
    _detect_binary_format,
    _expand_header_inputs,
    _safe_write_output,
    _setup_verbosity,
    _sniff_text_format,
    _stamp_provenance,
    _write_snapshot_output,
)
from abicheck.model import AbiSnapshot

# ---------------------------------------------------------------------------
# _expand_header_inputs
# ---------------------------------------------------------------------------


class TestExpandHeaderInputs:
    """Tests for _expand_header_inputs."""

    def test_single_file(self, tmp_path: Path) -> None:
        """A single header file is returned as-is."""
        h = tmp_path / "foo.h"
        h.write_text("int foo();", encoding="utf-8")
        result = _expand_header_inputs([h])
        assert result == [h]

    def test_directory_with_headers(self, tmp_path: Path) -> None:
        """A directory is recursively scanned for header files."""
        inc = tmp_path / "include"
        inc.mkdir()
        (inc / "a.h").write_text("int a();", encoding="utf-8")
        sub = inc / "sub"
        sub.mkdir()
        (sub / "b.hpp").write_text("int b();", encoding="utf-8")
        result = _expand_header_inputs([inc])
        names = {p.name for p in result}
        assert "a.h" in names
        assert "b.hpp" in names

    @pytest.mark.parametrize("noise_dir", [".abicheck-build", ".git"])
    def test_prunes_build_and_vcs_dirs(self, tmp_path: Path, noise_dir: str) -> None:
        """Generated headers under abicheck's cmake build dir (and VCS dirs) are
        not swept into the -H surface on the dump/compare CLI path (Codex P2)."""
        inc = tmp_path / "include"
        inc.mkdir()
        (inc / "public.h").write_text("int api();", encoding="utf-8")
        sub = inc / noise_dir
        sub.mkdir()
        (sub / "config.h").write_text("#define GENERATED 1", encoding="utf-8")
        result = _expand_header_inputs([inc])
        assert {p.name for p in result} == {"public.h"}

    def test_directory_empty_raises(self, tmp_path: Path) -> None:
        """A directory with no header files raises ClickException."""
        empty = tmp_path / "empty"
        empty.mkdir()
        (empty / "readme.txt").write_text("not a header", encoding="utf-8")
        with pytest.raises(click.ClickException, match="no supported header files"):
            _expand_header_inputs([empty])

    def test_nonexistent_raises(self, tmp_path: Path) -> None:
        """A nonexistent path raises ClickException."""
        missing = tmp_path / "no_such_file.h"
        with pytest.raises(click.ClickException, match="not found"):
            _expand_header_inputs([missing])

    def test_deduplication(self, tmp_path: Path) -> None:
        """Duplicate paths (same resolved file) are deduplicated."""
        h = tmp_path / "foo.h"
        h.write_text("int foo();", encoding="utf-8")
        result = _expand_header_inputs([h, h])
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _setup_verbosity
# ---------------------------------------------------------------------------


class TestSetupVerbosity:
    """Tests for _setup_verbosity."""

    def test_verbose_sets_debug(self) -> None:
        """verbose=True sets logger to DEBUG."""
        logger = logging.getLogger("abicheck")
        original_level = logger.level
        original_handlers = logger.handlers[:]
        try:
            _setup_verbosity(verbose=True)
            assert logger.level == logging.DEBUG
        finally:
            logger.setLevel(original_level)
            logger.handlers = original_handlers

    def test_non_verbose_sets_warning(self) -> None:
        """verbose=False sets logger to WARNING."""
        logger = logging.getLogger("abicheck")
        original_level = logger.level
        original_handlers = logger.handlers[:]
        try:
            _setup_verbosity(verbose=False)
            assert logger.level == logging.WARNING
        finally:
            logger.setLevel(original_level)
            logger.handlers = original_handlers


# ---------------------------------------------------------------------------
# _safe_write_output
# ---------------------------------------------------------------------------


class TestSafeWriteOutput:
    """Tests for _safe_write_output."""

    def test_write_success(self, tmp_path: Path) -> None:
        """Writes text to an existing directory."""
        out = tmp_path / "result.json"
        _safe_write_output(out, '{"ok": true}')
        assert out.read_text(encoding="utf-8") == '{"ok": true}'

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        """Creates missing parent directories."""
        out = tmp_path / "deep" / "nested" / "result.json"
        _safe_write_output(out, "data")
        assert out.exists()
        assert out.read_text(encoding="utf-8") == "data"

    def test_oserror_raises_click(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OSError during write is wrapped in ClickException."""
        out = tmp_path / "result.json"
        monkeypatch.setattr(
            Path,
            "write_text",
            lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")),
        )
        with pytest.raises(click.ClickException, match="Cannot write"):
            _safe_write_output(out, "data")


# ---------------------------------------------------------------------------
# _stamp_provenance
# ---------------------------------------------------------------------------


class TestStampProvenance:
    """Tests for _stamp_provenance."""

    def test_with_git(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When git is available, git_commit is set."""
        snap = AbiSnapshot(library="lib.so", version="1.0")
        fake_result = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="abc123\n", stderr=""
        )
        monkeypatch.setattr(
            "subprocess.run",
            lambda *_a, **_kw: fake_result,
        )
        _stamp_provenance(snap, git_tag="v1.0", build_id="42", no_git=False)
        assert snap.git_commit == "abc123"
        assert snap.git_tag == "v1.0"
        assert snap.build_id == "42"
        assert snap.created_at is not None

    def test_no_git_flag(self) -> None:
        """When no_git=True, git_commit remains None."""
        snap = AbiSnapshot(library="lib.so", version="1.0")
        _stamp_provenance(snap, git_tag=None, build_id=None, no_git=True)
        assert snap.git_commit is None
        assert snap.created_at is not None

    def test_git_not_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When git binary is missing, git_commit stays None."""
        snap = AbiSnapshot(library="lib.so", version="1.0")

        def _raise_fnf(*_a: object, **_kw: object) -> None:
            raise FileNotFoundError("git not found")

        monkeypatch.setattr("subprocess.run", _raise_fnf)
        _stamp_provenance(snap, git_tag=None, build_id=None, no_git=False)
        assert snap.git_commit is None


# ---------------------------------------------------------------------------
# _sniff_text_format
# ---------------------------------------------------------------------------


class TestSniffTextFormatExtra:
    """Additional edge-case tests for _sniff_text_format."""

    def test_json_with_leading_whitespace(self, tmp_path: Path) -> None:
        """JSON preceded by whitespace is still detected."""
        f = tmp_path / "ws.json"
        f.write_text('  \n  {"library": "x"}', encoding="utf-8")
        assert _sniff_text_format(f) == "json"

    def test_perl_detected(self, tmp_path: Path) -> None:
        """Perl dump is detected correctly."""
        f = tmp_path / "dump.pl"
        f.write_text("$VAR1 = {\n  'k' => 'v'\n};", encoding="utf-8")
        assert _sniff_text_format(f) == "perl"

    def test_unknown_content(self, tmp_path: Path) -> None:
        """Unrecognized text returns 'unknown'."""
        f = tmp_path / "mystery.txt"
        f.write_text("just some random text", encoding="utf-8")
        assert _sniff_text_format(f) == "unknown"

    def test_oserror_returns_unknown(self, tmp_path: Path) -> None:
        """Missing file returns 'unknown'."""
        missing = tmp_path / "gone.txt"
        assert _sniff_text_format(missing) == "unknown"


# ---------------------------------------------------------------------------
# _detect_binary_format
# ---------------------------------------------------------------------------


class TestDetectBinaryFormat:
    """Tests for _detect_binary_format (thin wrapper around binary_utils)."""

    def test_delegates_to_binary_utils(self, tmp_path: Path) -> None:
        """_detect_binary_format delegates to binary_utils.detect_binary_format."""
        elf = tmp_path / "lib.so"
        elf.write_bytes(b"\x7fELF" + b"\x00" * 20)
        result = _detect_binary_format(elf)
        assert result == "elf"

    def test_unknown_magic(self, tmp_path: Path) -> None:
        """Unrecognized magic bytes return None."""
        f = tmp_path / "unknown.bin"
        f.write_bytes(b"\x00\x01\x02\x03" + b"\x00" * 20)
        result = _detect_binary_format(f)
        assert result is None


# ---------------------------------------------------------------------------
# _write_snapshot_output
# ---------------------------------------------------------------------------


class TestWriteSnapshotOutput:
    """Tests for _write_snapshot_output."""

    def test_stdout_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        """When effective_output is None, writes to stdout."""
        snap = AbiSnapshot(library="lib.so", version="1.0")
        _write_snapshot_output(snap, None)
        captured = capsys.readouterr()
        assert "lib.so" in captured.out

    def test_file_output(self, tmp_path: Path) -> None:
        """When effective_output is a path, writes JSON to that file."""
        snap = AbiSnapshot(library="lib.so", version="1.0")
        out = tmp_path / "snap.json"
        _write_snapshot_output(snap, out)
        assert out.exists()
        assert "lib.so" in out.read_text(encoding="utf-8")

    def test_warns_on_empty_requested_layer(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """G21.7: a requested layer that comes back empty triggers a loud warning."""
        import abicheck.cli as cli_mod
        import abicheck.cli_buildsource as cbs_mod

        snap = AbiSnapshot(library="lib.so", version="1.0")

        # Fake embed (imported locally from cli_buildsource): pretend a source
        # tree was processed but produced no facts, so the missing-layer helper
        # reports the requested L4/L5.
        def _fake_embed(s, build_info, sources, **kwargs):
            pass  # leave build_source None; the patched helper drives the warning

        monkeypatch.setattr(cbs_mod, "embed_build_source", _fake_embed)
        monkeypatch.setattr(
            cli_mod,
            "_missing_requested_evidence_layers",
            lambda pack, mode: ["L4", "L5"],
        )
        srcs = tmp_path / "src"
        srcs.mkdir()
        _write_snapshot_output(snap, None, sources=srcs, collect_mode="source-target")
        err = capsys.readouterr().err
        assert "requested evidence layer(s) not collected" in err
        assert "L4" in err and "L5" in err
