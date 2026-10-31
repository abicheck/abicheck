"""``action/run.sh`` forwards the compile-context inputs as ``--set compile.*`` (ADR-091 D4).

The Action used to synthesize a ``compile:`` block into a temporary
``--config`` file. It now translates each input into one ``--set`` argument and
lets the CLI resolve it (ADR-070 D2). These tests drive the real script against
the same fake ``abicheck`` the ``set:`` tests use, so the assertions are about
the argv that actually runs. The value-level equivalence between ``--set`` and
the file form is pinned in ``tests/test_compile_context_set_equivalence.py``.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash
from test_action_run_sh_set_input import RUN_SH, _env, _set_pairs, _stub


def _run(tmp_path: Path, mode: str = "compare", **inputs: str) -> list[str]:
    """Run *mode* with *inputs*; return the stub's argv, one token per entry."""
    require_bash()
    fake_bin, captured = _stub(tmp_path)
    env = _env(tmp_path, fake_bin, INPUT_MODE=mode, **inputs)
    if mode == "dump":
        # dump takes one library and writes a snapshot; no old side.
        env.pop("INPUT_OLD_LIBRARY", None)
        env["INPUT_OUTPUT_FILE"] = str(tmp_path / "out.json")
    result = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert captured.is_file(), "abicheck stub was never invoked"
    return captured.read_text(encoding="utf-8").splitlines()


def _compile_sets(argv: list[str]) -> list[str]:
    return [value for value in _set_pairs(argv) if value.startswith("compile.")]


def _no_config_flag(argv: list[str]) -> None:
    """The compile inputs must not synthesize a ``--config`` any more."""
    assert "--config" not in argv


@pytest.mark.parametrize("mode", ["compare", "dump"])
def test_no_compile_inputs_forward_no_compile_set(tmp_path: Path, mode: str) -> None:
    argv = _run(tmp_path, mode)
    assert _compile_sets(argv) == []
    _no_config_flag(argv)


def test_auto_frontend_and_default_lang_forward_nothing(tmp_path: Path) -> None:
    """``auto`` and the ``c++`` default are no-ops, as they were in the overlay."""
    argv = _run(tmp_path, INPUT_FRONTEND="auto", INPUT_LANG="c++")
    assert _compile_sets(argv) == []


def test_gcc_path_forwards_compile_compiler(tmp_path: Path) -> None:
    argv = _run(tmp_path, INPUT_COMPILER="/opt/gcc-14/bin/g++")
    assert _compile_sets(argv) == ['compile.compiler="/opt/gcc-14/bin/g++"']
    _no_config_flag(argv)


def test_prefix_form_compiler_forwards_compile_compiler(tmp_path: Path) -> None:
    """ADR-091 R5: `compiler` takes a cross-toolchain prefix (ends with `-`)
    as well as a path; the retired gcc-prefix input folded into it."""
    argv = _run(tmp_path, INPUT_COMPILER="aarch64-linux-gnu-")
    assert _compile_sets(argv) == ['compile.compiler="aarch64-linux-gnu-"']


def test_path_form_compiler_forwards_unchanged(tmp_path: Path) -> None:
    """A full compiler path is forwarded verbatim, not treated as a prefix."""
    argv = _run(tmp_path, INPUT_COMPILER="/usr/bin/g++")
    assert _compile_sets(argv) == ['compile.compiler="/usr/bin/g++"']


def test_single_line_options_are_shell_split_into_a_list(tmp_path: Path) -> None:
    argv = _run(tmp_path, INPUT_COMPILE_OPTIONS='-DNAME="x" -fno-exceptions')
    (value,) = [v for v in _compile_sets(argv) if v.startswith("compile.options=")]
    assert json.loads(value.removeprefix("compile.options=")) == shlex.split(
        '-DNAME="x" -fno-exceptions'
    )


def test_multi_line_options_are_one_token_per_line(tmp_path: Path) -> None:
    argv = _run(tmp_path, INPUT_COMPILE_OPTIONS="-DA\n-DB=2\n")
    (value,) = [v for v in _compile_sets(argv) if v.startswith("compile.options=")]
    assert json.loads(value.removeprefix("compile.options=")) == ["-DA", "-DB=2"]


def test_sysroot_and_nostdinc_forward_as_typed_values(tmp_path: Path) -> None:
    argv = _run(tmp_path, INPUT_SYSROOT="/opt/sysroot", INPUT_NOSTDINC="true")
    sets = _compile_sets(argv)
    assert 'compile.sysroot="/opt/sysroot"' in sets
    assert "compile.nostdinc=true" in sets


def test_a_windows_sysroot_survives_as_a_yaml_string(tmp_path: Path) -> None:
    """A backslash and a bare number stay text: the value is a JSON string."""
    argv = _run(tmp_path, INPUT_SYSROOT="C:\\sdk\\123")
    (value,) = [v for v in _compile_sets(argv) if v.startswith("compile.sysroot=")]
    assert json.loads(value.removeprefix("compile.sysroot=")) == "C:\\sdk\\123"


def test_frontend_and_lang_forward(tmp_path: Path) -> None:
    argv = _run(tmp_path, INPUT_FRONTEND="clang", INPUT_LANG="c")
    sets = _compile_sets(argv)
    assert 'compile.frontend="clang"' in sets
    assert 'compile.lang="c"' in sets


def test_dump_forwards_lang_and_frontend(tmp_path: Path) -> None:
    """Dump forwards lang too (``add_compile_context_flags true``)."""
    argv = _run(tmp_path, "dump", INPUT_FRONTEND="clang", INPUT_LANG="c")
    assert 'compile.lang="c"' in _compile_sets(argv)
    assert 'compile.frontend="clang"' in _compile_sets(argv)


def test_derived_compile_sets_come_before_an_explicit_set_line(tmp_path: Path) -> None:
    """``set:`` is appended after the derived overrides, so it wins (CLI last-wins)."""
    argv = _run(
        tmp_path,
        INPUT_COMPILER="/usr/bin/g++",
        INPUT_SET="compile.compiler=/usr/bin/clang++\n",
    )
    sets = _set_pairs(argv)
    assert sets == [
        'compile.compiler="/usr/bin/g++"',
        "compile.compiler=/usr/bin/clang++",
    ]


def test_dump_applies_set_lines_after_its_compile_inputs(tmp_path: Path) -> None:
    argv = _run(
        tmp_path,
        "dump",
        INPUT_SYSROOT="/opt/sysroot",
        INPUT_SET="debug.dwarf_only=true\n",
    )
    sets = _set_pairs(argv)
    assert sets == ['compile.sysroot="/opt/sysroot"', "debug.dwarf_only=true"]


def test_set_on_dump_is_no_longer_refused(tmp_path: Path) -> None:
    """``set:`` on dump forwards instead of failing the step (ADR-091 D4)."""
    argv = _run(tmp_path, "dump", INPUT_SET="debug.dwarf_only=true\n")
    assert "debug.dwarf_only=true" in _set_pairs(argv)


def test_set_on_deps_tree_is_still_refused(tmp_path: Path) -> None:
    """Modes that carry no ``--set`` still fail loudly rather than drop it."""
    require_bash()
    fake_bin, _captured = _stub(tmp_path)
    env = _env(
        tmp_path, fake_bin, INPUT_MODE="scan", INPUT_SET="debug.dwarf_only=true\n"
    )
    result = subprocess.run(
        [bash_executable(), str(RUN_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=tmp_path,
        check=False,
    )
    assert result.returncode != 0
    assert (
        "set: applies to mode: compare and dump only" in result.stdout + result.stderr
    )


def test_no_overlay_temp_file_is_written_for_compile_inputs(tmp_path: Path) -> None:
    """The deleted overlay wrote ``abicheck-compile-context.*`` under RUNNER_TEMP."""
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    argv = _run(
        tmp_path,
        INPUT_COMPILER="/usr/bin/g++",
        INPUT_SYSROOT="/opt/sysroot",
        RUNNER_TEMP=str(runner_temp),
    )
    assert not any(
        p.name.startswith("abicheck-compile-context") for p in runner_temp.iterdir()
    )
    _no_config_flag(argv)
