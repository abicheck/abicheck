"""Exact AST producer/compiler identity and cache-key regression tests."""

import os
import shutil

import pytest

from abicheck.dumper import _cache_key, _tool_identity
from abicheck.dumper_toolchain import (
    _compiler_family_from_toolchain,
    _resolved_tool,
    _tool_identity_metadata,
    _tool_target_triple,
)


def test_frontend_binary_identity_is_part_of_key(tmp_path):
    header = tmp_path / "foo.h"
    header.write_text("int x;", encoding="utf-8")
    before = _cache_key([header], [], "c++", frontend_identity="castxml 0.6; clang 17")
    after = _cache_key([header], [], "c++", frontend_identity="castxml 0.6; clang 21")
    assert before != after


def test_host_compiler_identity_is_part_of_key(tmp_path):
    header = tmp_path / "foo.h"
    header.write_text("int x;", encoding="utf-8")
    before = _cache_key([header], [], "c++", compiler_identity="gcc 12")
    after = _cache_key([header], [], "c++", compiler_identity="gcc 13")
    assert before != after


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-script fixture")
def test_tool_identity_changes_for_replaced_same_version_binary(tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\necho tool-version-1\n# A\n", encoding="utf-8")
    tool.chmod(0o755)
    identity_a = _tool_identity(str(tool))

    replacement = tmp_path / "replacement"
    replacement.write_text("#!/bin/sh\necho tool-version-1\n# B\n", encoding="utf-8")
    replacement.chmod(0o755)
    replacement.replace(tool)
    identity_b = _tool_identity(str(tool))

    assert identity_a != identity_b
    assert "sha256=" in identity_a
    assert "sha256=" in identity_b


def test_tool_identity_rejects_nonregular_file(tmp_path):
    identity = _tool_identity(str(tmp_path))
    assert "unavailable=OSError" in identity
    assert "not a regular file" in identity


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable-script fixture")
def test_tool_version_output_is_utf8_safe_and_bounded(tmp_path):
    tool = tmp_path / "noisy-tool"
    tool.write_bytes(b"#!/bin/sh\nprintf '\\377'\nyes x | head -c 70000\n")
    tool.chmod(0o755)

    identity = _tool_identity(str(tool))

    assert "version=\ufffd" in identity
    assert "[truncated]" in identity
    assert len(identity) < 66_000


@pytest.mark.skipif(os.name == "nt", reason="symlink fixture requires POSIX")
def test_resolved_tool_returns_canonical_executable(tmp_path):
    tool = tmp_path / "real-tool"
    tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    tool.chmod(0o755)
    link = tmp_path / "tool-link"
    link.symlink_to(tool)

    selected, real, _stat, _digest = _resolved_tool(str(link))

    assert selected == str(link)
    assert real == tool


def test_bare_missing_tool_does_not_resolve_from_cwd(tmp_path, monkeypatch):
    tool = tmp_path / "abicheck-definitely-missing-tool"
    tool.write_text("#!/bin/sh\necho should-not-run\n", encoding="utf-8")
    tool.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))

    identity = _tool_identity(tool.name)

    assert "unavailable=FileNotFoundError" in identity
    assert "tool not found on PATH" in identity


class TestToolTargetTriple:
    """``-dumpmachine`` probing (P1 toolchain-provenance audit) —
    ``_tool_identity_metadata``'s ``target_triple`` key, which flows into a
    snapshot's ``ast_toolchain``/``compiler_target_triple``."""

    @pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
    def test_real_gcc_reports_a_target_triple(self):
        selected, _real, _stat, digest = _resolved_tool("gcc")
        triple = _tool_target_triple(selected, digest)
        assert triple
        assert "-" in triple  # e.g. x86_64-linux-gnu, x86_64-pc-linux-gnu

    @pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not on PATH")
    def test_tool_identity_metadata_includes_target_triple_for_gcc(self):
        metadata = _tool_identity_metadata("gcc")
        assert "target_triple" in metadata
        assert metadata["target_triple"]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX executable-script fixture")
    def test_flag_unsupported_by_tool_omits_target_triple_key(self, tmp_path):
        tool = tmp_path / "no-dumpmachine-tool"
        tool.write_text(
            "#!/bin/sh\necho unknown option >&2\nexit 1\n", encoding="utf-8"
        )
        tool.chmod(0o755)

        metadata = _tool_identity_metadata(str(tool))

        assert "target_triple" not in metadata

    @pytest.mark.skipif(os.name == "nt", reason="POSIX executable-script fixture")
    def test_tool_target_triple_returns_stripped_output(self, tmp_path):
        tool = tmp_path / "fake-gcc"
        tool.write_text(
            '#!/bin/sh\nif [ "$1" = "-dumpmachine" ]; then echo "  x86_64-linux-gnu  "; '
            "else echo fake-version; fi\n",
            encoding="utf-8",
        )
        tool.chmod(0o755)
        selected, _real, _stat, digest = _resolved_tool(str(tool))

        triple = _tool_target_triple(selected, digest)

        assert triple == "x86_64-linux-gnu"

    @pytest.mark.skipif(os.name == "nt", reason="POSIX executable-script fixture")
    def test_tool_target_triple_returns_none_on_timeout(self, tmp_path, monkeypatch):
        import subprocess as subprocess_module

        from abicheck import dumper_toolchain

        tool = tmp_path / "hanging-tool"
        tool.write_text("#!/bin/sh\nsleep 100\n", encoding="utf-8")
        tool.chmod(0o755)

        def _raise_timeout(*_args, **_kwargs):
            raise subprocess_module.TimeoutExpired(cmd="hanging-tool", timeout=10)

        monkeypatch.setattr(dumper_toolchain.subprocess, "run", _raise_timeout)

        assert _tool_target_triple(str(tool), "irrelevant-digest") is None


class TestCompilerFamilyFromToolchain:
    """ADR-050 D1's compiler_family extraction (Codex review, PR #624
    follow-up)."""

    def test_prefers_compiler_selected_over_bare_selected(self):
        # The bug this test pins: for a castxml-produced snapshot,
        # "selected" names castxml itself, not the host compiler.
        toolchain = {
            "selected": "/usr/bin/castxml",
            "compiler_selected": "/usr/bin/g++",
        }
        assert _compiler_family_from_toolchain(toolchain) == "gnu"

    def test_falls_back_to_bare_selected_for_clang_producer(self):
        # clang is both frontend and compiler, so there is no separate
        # "compiler_selected" key -- the fallback must still work.
        toolchain = {"selected": "/usr/bin/clang"}
        assert _compiler_family_from_toolchain(toolchain) == "clang"

    def test_recognizes_msvc_cl_exe(self):
        # Forward slashes so the path parses the same on POSIX and Windows
        # test runners (a literal backslash separator only splits under a
        # native WindowsPath, not this fast unit test's POSIX PurePath).
        toolchain = {"compiler_selected": "C:/VC/bin/cl.exe"}
        assert _compiler_family_from_toolchain(toolchain) == "msvc"

    def test_recognizes_gcc(self):
        toolchain = {"compiler_selected": "/usr/bin/x86_64-linux-gnu-gcc-13"}
        assert _compiler_family_from_toolchain(toolchain) == "gnu"

    def test_empty_toolchain_returns_none(self):
        assert _compiler_family_from_toolchain({}) is None

    def test_unrecognized_binary_returns_its_basename(self):
        toolchain = {"compiler_selected": "/opt/icx/bin/icx"}
        assert _compiler_family_from_toolchain(toolchain) == "icx"
