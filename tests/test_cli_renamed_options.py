"""ADR-091 Phase 4 (CLI): renamed options and their one-release hidden aliases.

Each rename has three contracts, checked here per spelling:

* the new spelling parses to the parameter the command body reads;
* the retired spelling parses to the same value and prints exactly one
  deprecation line on stderr naming the new spelling (D7);
* passing both spellings for the same thing is a usage error, exit 64.

Parsing goes through ``make_context`` so the value the callback would see is
asserted directly. The end-to-end exit code and the root ``--version`` are
checked once through the real entry point.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import click
import pytest

import abicheck
from abicheck.cli import main
from tests._abicheck_subprocess import abicheck_command

#: Positional operands the parser checks for existence; created under tmp_path.
_OPERAND_SUFFIXES = (".so", ".h", ".json")
_OPERAND_NAMES = ("app",)


def _parse(
    tmp_path: Path, command_path: tuple[str, ...], args: list[str]
) -> dict[str, Any]:
    """The parameter values a command's callback would receive for ``args``."""
    argv: list[str] = []
    for token in args:
        if token.endswith(_OPERAND_SUFFIXES) or token in _OPERAND_NAMES:
            path = tmp_path / token
            path.touch()
            argv.append(str(path))
        else:
            argv.append(token)
    cmd: click.Command = main
    for name in command_path:
        assert isinstance(cmd, click.Group)
        cmd = cmd.commands[name]
    ctx = cmd.make_context(command_path[-1], argv)
    values = dict(ctx.params)
    ctx.close()
    return values


def _deprecations(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [
        line
        for line in capsys.readouterr().err.splitlines()
        if "DeprecationWarning" in line
    ]


# (command path, new-spelling argv, retired-spelling argv, parameter, expected value)
LABEL_CASES: list[tuple[tuple[str, ...], list[str], list[str], str, object]] = [
    (
        ("dump",),
        ["-H", "x.h", "--label", "1.2.3", "a.so"],
        ["-H", "x.h", "--version", "1.2.3", "a.so"],
        "version",
        "1.2.3",
    ),
    (
        ("compare",),
        ["--label", "old=1.0", "--label", "new=2.0", "a.so", "b.so"],
        ["--version", "old=1.0", "--version", "new=2.0", "a.so", "b.so"],
        "version",
        (("old", "1.0"), ("new", "2.0")),
    ),
    (
        ("project", "history"),
        ["--label", "1.0", "--label", "2.0", "a.json", "b.json"],
        ["--version", "1.0", "--version", "2.0", "a.json", "b.json"],
        "versions",
        ("1.0", "2.0"),
    ),
]


@pytest.mark.parametrize(
    ("command", "new_argv", "param", "expected"),
    [(c, n, p, e) for c, n, _o, p, e in LABEL_CASES],
)
def test_label_new_spelling_parses(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: tuple[str, ...],
    new_argv: list[str],
    param: str,
    expected: object,
) -> None:
    assert _parse(tmp_path, command, new_argv)[param] == expected
    assert _deprecations(capsys) == []


@pytest.mark.parametrize(
    ("command", "old_argv", "param", "expected"),
    [(c, o, p, e) for c, _n, o, p, e in LABEL_CASES],
)
def test_version_alias_parses_identically_and_warns_once(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    command: tuple[str, ...],
    old_argv: list[str],
    param: str,
    expected: object,
) -> None:
    assert _parse(tmp_path, command, old_argv)[param] == expected
    warnings = _deprecations(capsys)
    assert len(warnings) == 1, warnings
    assert "'--version'" in warnings[0]
    assert "Use --label instead" in warnings[0]


def test_abi3_floor_new_spelling_parses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values = _parse(tmp_path, ("compare",), ["--abi3-floor", "3.9", "a.so", "b.so"])
    assert values["abi3"] == "3.9"
    assert _deprecations(capsys) == []


def test_abi3_alias_parses_identically_and_warns_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values = _parse(tmp_path, ("compare",), ["--abi3", "3.9", "a.so", "b.so"])
    assert values["abi3"] == "3.9"
    warnings = _deprecations(capsys)
    assert len(warnings) == 1, warnings
    assert "'--abi3'" in warnings[0]
    assert "Use --abi3-floor instead" in warnings[0]


def test_deps_compare_sided_root_parses_both_sides(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    values = _parse(
        tmp_path,
        ("deps", "compare"),
        ["app", "--root", f"old={old}", "--root", f"new={new}"],
    )
    assert values["root"] == (("old", old), ("new", new))


def test_deps_compare_legacy_roots_translate_per_side(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    values = _parse(
        tmp_path,
        ("deps", "compare"),
        ["app", "--old-root", str(old), "--new-root", str(new)],
    )
    assert values["root"] == (("old", old), ("new", new))
    warnings = _deprecations(capsys)
    assert len(warnings) == 2, warnings
    assert any("'--old-root'" in w and "--root old=PATH" in w for w in warnings)
    assert any("'--new-root'" in w and "--root new=PATH" in w for w in warnings)


def test_deps_compare_root_is_empty_when_absent(tmp_path: Path) -> None:
    assert _parse(tmp_path, ("deps", "compare"), ["app"])["root"] == ()


def test_deps_tree_root_new_spelling_parses(tmp_path: Path) -> None:
    values = _parse(tmp_path, ("deps", "tree"), ["app", "--root", str(tmp_path)])
    assert values["sysroot"] == tmp_path


def test_deps_tree_sysroot_alias_parses_identically_and_warns_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    values = _parse(tmp_path, ("deps", "tree"), ["app", "--sysroot", str(tmp_path)])
    assert values["sysroot"] == tmp_path
    warnings = _deprecations(capsys)
    assert len(warnings) == 1, warnings
    assert "'--sysroot'" in warnings[0]
    assert "Use --root PATH instead" in warnings[0]


@pytest.mark.parametrize(
    ("command", "argv"),
    [
        (("dump",), ["-H", "x.h", "--label", "1", "--version", "2", "a.so"]),
        (("compare",), ["--label", "old=1", "--version", "old=2", "a.so", "b.so"]),
        (("compare",), ["--abi3-floor", "3.9", "--abi3", "3.8", "a.so", "b.so"]),
        (("project", "history"), ["--label", "1", "--version", "2", "a.json"]),
        (("deps", "compare"), ["app", "--root", "old=/", "--old-root", "/"]),
        (("deps", "tree"), ["app", "--root", "/", "--sysroot", "/"]),
    ],
)
def test_both_spellings_are_a_usage_error(
    tmp_path: Path, command: tuple[str, ...], argv: list[str]
) -> None:
    with pytest.raises(click.UsageError, match="name the same option"):
        _parse(tmp_path, command, argv)


def test_both_spellings_exit_64_through_the_entry_point(tmp_path: Path) -> None:
    operand = tmp_path / "a.so"
    operand.touch()
    result = subprocess.run(
        abicheck_command("dump", "--label", "1", "--version", "2", operand),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 64, result.stderr
    assert "--version and --label name the same option" in result.stderr


def test_root_version_still_reports_the_tool_version() -> None:
    result = subprocess.run(
        abicheck_command("--version"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("abicheck ")
    assert abicheck.__version__.split("+")[0] in result.stdout
