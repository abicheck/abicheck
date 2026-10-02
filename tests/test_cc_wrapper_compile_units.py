# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""``cc_wrapper.compile_units_from_command``: which compiler invocations
become a compile unit, read through its first TU."""

from __future__ import annotations

from pathlib import Path

from abicheck.cc_wrapper import compile_units_from_command


def compile_unit_from_command(command, directory):
    """The first TU of *command*, or ``None``."""
    units = compile_units_from_command(command, directory)
    return units[0] if units else None


def test_compile_unit_from_command_parses_flags(tmp_path: Path) -> None:
    cu = compile_unit_from_command(
        ["c++", "-std=c++17", "-DFOO=1", "-Iinc", "-c", "src/foo.cpp", "-o", "foo.o"],
        tmp_path,
    )
    assert cu is not None
    assert cu.source == "src/foo.cpp"
    assert cu.language == "CXX"
    assert cu.standard == "c++17"
    assert cu.defines.get("FOO") == "1"


def test_compile_unit_from_command_none_for_link_or_no_source(tmp_path: Path) -> None:
    assert (
        compile_unit_from_command(
            ["c++", "-shared", "foo.o", "-o", "libfoo.so"], tmp_path
        )
        is None
    )
    assert compile_unit_from_command(["c++"], tmp_path) is None


def test_compile_unit_skips_preprocess_only_invocations(tmp_path: Path) -> None:
    # Preprocess-/dependency-only runs produce no shipped object → no facts, so
    # a build that pipes -E/-M steps through the wrapper can't pollute the pack.
    assert compile_unit_from_command(["c++", "-E", "src/foo.cpp"], tmp_path) is None
    assert compile_unit_from_command(["c++", "-M", "src/foo.cpp"], tmp_path) is None
    assert compile_unit_from_command(["c++", "-MM", "src/foo.cpp"], tmp_path) is None
    # -MD/-MMD are additive with a real -c compile and must NOT be skipped.
    cu = compile_unit_from_command(["c++", "-MD", "-c", "src/foo.cpp"], tmp_path)
    assert cu is not None and cu.source == "src/foo.cpp"
