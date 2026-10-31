"""The compile-context inputs are retired; their replacement is ``set:`` (ADR-091 D7).

The Action used to translate ``lang``/``frontend``/``compiler``/``compile-options``
/``sysroot``/``nostdinc`` into ``--set compile.*`` arguments. Those inputs are now
RETIRED tombstones: ``action/validate-inputs.sh`` fails the run before analysis
and names the ``set:`` line that replaces each one, and ``run.sh`` forwards only
the ``set:`` input. These tests pin both halves: each retired input is refused
with its replacement named, and a ``set:`` line reaches the CLI verbatim.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from _workflow_exec import bash_executable, require_bash
from test_action_run_sh_set_input import RUN_SH, _env, _set_pairs, _stub

VALIDATE_SH = RUN_SH.parent / "validate-inputs.sh"


def _run(tmp_path: Path, mode: str = "compare", **inputs: str) -> list[str]:
    """Run *mode* with *inputs*; return the stub's argv, one token per entry."""
    require_bash()
    fake_bin, captured = _stub(tmp_path)
    env = _env(tmp_path, fake_bin, INPUT_MODE=mode, **inputs)
    if mode == "dump":
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


def _validate(**inputs: str) -> subprocess.CompletedProcess:
    """Run the pre-install validator, the first gate a real run hits."""
    require_bash()
    env = {k: v for k, v in os.environ.items() if not k.startswith("INPUT_")}
    env.update(
        {"INPUT_MODE": "compare", "INPUT_NEW_LIBRARY": "libfoo.so", **inputs}
    )
    return subprocess.run(
        [bash_executable(), str(VALIDATE_SH)],
        capture_output=True,
        text=True,
        env=env,
        cwd=RUN_SH.parent.parent,
        check=False,
    )


def _no_config_flag(argv: list[str]) -> None:
    """The retired inputs no longer synthesize a ``--config`` document."""
    assert "--config" not in argv


# (input name, value a workflow would set, the set: line that replaces it)
RETIRED_COMPILE_INPUTS = [
    ("frontend", "clang", "compile.frontend"),
    ("compile-options", "-DA=1", "compile.options"),
    ("nostdinc", "true", "compile.nostdinc"),
]


@pytest.mark.parametrize(("name", "value", "replacement"), RETIRED_COMPILE_INPUTS)
def test_retired_compile_input_is_refused_with_its_set_replacement(
    name: str, value: str, replacement: str
) -> None:
    env_name = "INPUT_" + name.upper().replace("-", "_")
    result = _validate(**{env_name: value})
    combined = result.stdout + result.stderr
    assert result.returncode != 0, combined
    assert "::error::" in combined, combined
    assert name in combined, combined
    assert f"set: {replacement}=" in combined, combined


@pytest.mark.parametrize("mode", ["compare", "dump"])
def test_no_compile_inputs_forward_no_compile_set(tmp_path: Path, mode: str) -> None:
    argv = _run(tmp_path, mode)
    assert [v for v in _set_pairs(argv) if v.startswith("compile.")] == []
    _no_config_flag(argv)


def test_compiler_set_line_is_forwarded_verbatim(tmp_path: Path) -> None:
    argv = _run(tmp_path, INPUT_SET="compile.compiler=/opt/gcc-14/bin/g++\n")
    assert _set_pairs(argv) == ["compile.compiler=/opt/gcc-14/bin/g++"]
    _no_config_flag(argv)


def test_prefix_form_compiler_set_line_is_forwarded(tmp_path: Path) -> None:
    """ADR-091 R5: ``compile.compiler`` takes a cross-toolchain prefix too."""
    argv = _run(tmp_path, INPUT_SET="compile.compiler=aarch64-linux-gnu-\n")
    assert _set_pairs(argv) == ["compile.compiler=aarch64-linux-gnu-"]


def test_options_list_set_line_is_forwarded_verbatim(tmp_path: Path) -> None:
    argv = _run(tmp_path, INPUT_SET='compile.options=["-DNAME=\\"x\\"", "-fno-exceptions"]\n')
    (value,) = [v for v in _set_pairs(argv) if v.startswith("compile.options=")]
    assert json.loads(value.removeprefix("compile.options=")) == [
        '-DNAME="x"',
        "-fno-exceptions",
    ]


def test_sysroot_and_nostdinc_set_lines_are_forwarded(tmp_path: Path) -> None:
    argv = _run(
        tmp_path,
        INPUT_SET='compile.sysroot="/opt/sysroot"\ncompile.nostdinc=true\n',
    )
    assert _set_pairs(argv) == [
        'compile.sysroot="/opt/sysroot"',
        "compile.nostdinc=true",
    ]


def test_lang_and_frontend_set_lines_are_forwarded(tmp_path: Path) -> None:
    argv = _run(
        tmp_path,
        INPUT_SET='compile.frontend="clang"\ncompile.lang="c"\n',
    )
    assert _set_pairs(argv) == ['compile.frontend="clang"', 'compile.lang="c"']


def test_dump_forwards_set_lines(tmp_path: Path) -> None:
    argv = _run(
        tmp_path,
        "dump",
        INPUT_SET='compile.frontend="clang"\ncompile.lang="c"\n',
    )
    assert _set_pairs(argv) == ['compile.frontend="clang"', 'compile.lang="c"']


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


def test_no_overlay_temp_file_is_written_for_set_compile_lines(tmp_path: Path) -> None:
    """The deleted overlay wrote ``abicheck-compile-context.*`` under RUNNER_TEMP."""
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    argv = _run(
        tmp_path,
        INPUT_SET="compile.compiler=/usr/bin/g++\ncompile.sysroot=\"/opt/sysroot\"\n",
        RUNNER_TEMP=str(runner_temp),
    )
    assert not any(
        p.name.startswith("abicheck-compile-context") for p in runner_temp.iterdir()
    )
    _no_config_flag(argv)


@pytest.mark.parametrize(
    ("name", "value", "argv_value"),
    [
        ("compiler", "/opt/gcc-14/bin/g++", 'compile.compiler="/opt/gcc-14/bin/g++"'),
        ("sysroot", "/opt/sysroot", 'compile.sysroot="/opt/sysroot"'),
        ("lang", "c", 'compile.lang="c"'),
    ],
)
def test_live_compile_input_still_forwards_as_a_set_pair(
    tmp_path: Path, name: str, value: str, argv_value: str
) -> None:
    """compiler, sysroot and lang are live inputs (not retired): each still
    reaches the CLI as its compile.* --set pair."""
    argv = _run(tmp_path, **{"INPUT_" + name.upper(): value})
    assert argv_value in _set_pairs(argv)


def test_live_severity_preset_forwards_as_the_core_flag(tmp_path: Path) -> None:
    """--severity-preset is a core CLI flag (not a config key), so it is
    forwarded as the flag itself, never as a --set pair."""
    argv = _run(tmp_path, INPUT_SEVERITY_PRESET="strict")
    assert argv[argv.index("--severity-preset") + 1] == "strict"
    assert not any(v.startswith("severity.preset") for v in _set_pairs(argv))
