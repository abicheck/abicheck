"""ADR-091 CLI renames: the new spelling parses; the retired spelling is gone.

ADR-091 D7 (2026-10-30 policy amendment) removes each renamed CLI spelling
outright, with no transition window. Each rename is checked per spelling:

* the new spelling parses to the parameter the command body reads;
* the retired spelling is an unknown option: it fails in the parser, so
  ``make_context`` raises ``click.NoSuchOption`` and the entry point exits 64.

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
    command: tuple[str, ...],
    new_argv: list[str],
    param: str,
    expected: object,
) -> None:
    assert _parse(tmp_path, command, new_argv)[param] == expected


@pytest.mark.parametrize(
    ("command", "old_argv"),
    [(c, o) for c, _n, o, _p, _e in LABEL_CASES],
)
def test_version_spelling_is_an_unknown_option(
    tmp_path: Path, command: tuple[str, ...], old_argv: list[str]
) -> None:
    with pytest.raises(click.NoSuchOption, match="--version"):
        _parse(tmp_path, command, old_argv)


def test_abi3_floor_new_spelling_parses(tmp_path: Path) -> None:
    values = _parse(tmp_path, ("compare",), ["--abi3-floor", "3.9", "a.so", "b.so"])
    assert values["abi3"] == "3.9"


def test_abi3_retired_spelling_is_an_unknown_option(tmp_path: Path) -> None:
    with pytest.raises(click.NoSuchOption, match="--abi3"):
        _parse(tmp_path, ("compare",), ["--abi3", "3.9", "a.so", "b.so"])


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


@pytest.mark.parametrize("retired", ["--old-root", "--new-root"])
def test_deps_compare_retired_roots_are_unknown_options(
    tmp_path: Path, retired: str
) -> None:
    with pytest.raises(click.NoSuchOption, match=retired):
        _parse(tmp_path, ("deps", "compare"), ["app", retired, str(tmp_path)])


def test_deps_compare_root_is_empty_when_absent(tmp_path: Path) -> None:
    assert _parse(tmp_path, ("deps", "compare"), ["app"])["root"] == ()


def test_deps_tree_root_new_spelling_parses(tmp_path: Path) -> None:
    values = _parse(tmp_path, ("deps", "tree"), ["app", "--root", str(tmp_path)])
    assert values["sysroot"] == tmp_path


def test_deps_tree_sysroot_retired_spelling_is_an_unknown_option(
    tmp_path: Path,
) -> None:
    with pytest.raises(click.NoSuchOption, match="--sysroot"):
        _parse(tmp_path, ("deps", "tree"), ["app", "--sysroot", str(tmp_path)])


@pytest.mark.parametrize(
    ("command", "argv", "retired"),
    [
        (("dump",), ["-H", "x.h", "--version", "1", "a.so"], "--version"),
        (("compare",), ["--version", "old=1", "a.so", "b.so"], "--version"),
        (("compare",), ["--abi3", "3.9", "a.so", "b.so"], "--abi3"),
        (("project", "history"), ["--version", "1", "a.json"], "--version"),
        (("deps", "compare"), ["app", "--old-root", "/"], "--old-root"),
        (("deps", "tree"), ["app", "--sysroot", "/"], "--sysroot"),
    ],
)
def test_retired_spelling_exits_64_through_the_entry_point(
    tmp_path: Path, command: tuple[str, ...], argv: list[str], retired: str
) -> None:
    result = subprocess.run(
        abicheck_command(*command, *argv),
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
    )
    assert result.returncode == 64, result.stderr
    assert "No such option" in result.stderr, result.stderr
    assert retired in result.stderr, result.stderr


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
