"""Exact AST producer/compiler identity and cache-key regression tests."""

import os

import pytest

from abicheck.dumper import _cache_key, _tool_identity
from abicheck.dumper_toolchain import _compiler_family_from_toolchain, _resolved_tool


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
