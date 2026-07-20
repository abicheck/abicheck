"""Exact AST producer/compiler identity and cache-key regression tests."""

from abicheck.dumper import _cache_key, _tool_identity


def test_frontend_binary_identity_is_part_of_key(tmp_path):
    header = tmp_path / "foo.h"
    header.write_text("int x;", encoding="utf-8")
    before = _cache_key(
        [header], [], "c++", frontend_identity="castxml 0.6; clang 17"
    )
    after = _cache_key(
        [header], [], "c++", frontend_identity="castxml 0.6; clang 21"
    )
    assert before != after


def test_host_compiler_identity_is_part_of_key(tmp_path):
    header = tmp_path / "foo.h"
    header.write_text("int x;", encoding="utf-8")
    before = _cache_key([header], [], "c++", compiler_identity="gcc 12")
    after = _cache_key([header], [], "c++", compiler_identity="gcc 13")
    assert before != after


def test_tool_identity_changes_for_replaced_same_version_binary(tmp_path):
    tool = tmp_path / "tool"
    tool.write_text("#!/bin/sh\necho tool-version-1\n# A\n", encoding="utf-8")
    tool.chmod(0o755)
    identity_a = _tool_identity(str(tool))

    replacement = tmp_path / "replacement"
    replacement.write_text(
        "#!/bin/sh\necho tool-version-1\n# B\n", encoding="utf-8"
    )
    replacement.chmod(0o755)
    replacement.replace(tool)
    identity_b = _tool_identity(str(tool))

    assert identity_a != identity_b
    assert "sha256=" in identity_a
    assert "sha256=" in identity_b
