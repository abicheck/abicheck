"""Retired ADR-091 options name their replacement instead of a fuzzy match."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.frontends.cli.runtime import RETIRED_OPTIONS

# Literal expectations, independent of RETIRED_OPTIONS: a dropped or
# reworded entry fails here instead of silently losing its case.
_CASES = [
    (["compare", "a.so", "b.so"], "--version", "`--label old=1.0 --label new=1.1`"),
    (["dump", "a.so"], "--version", "`--label 1.1`"),
    (["project", "history"], "--version", "one `--label` per SNAPSHOT, in order"),
    (["compare", "a.so", "b.so"], "--abi3", "renamed to `--abi3-floor`"),
    (["deps", "compare", "a.so", "b.so"], "--old-root", "use `--root old=PATH`."),
    (["deps", "compare", "a.so", "b.so"], "--new-root", "use `--root new=PATH`."),
    (["deps", "tree", "a.so"], "--sysroot", "use `--root PATH`."),
]


@pytest.mark.parametrize(("argv", "option", "hint"), _CASES)
def test_retired_option_names_replacement_and_exits_64(
    argv: list[str], option: str, hint: str
) -> None:
    result = CliRunner().invoke(main, [*argv, option, "x"])
    assert result.exit_code == 64
    assert f"No such option: {option}" in result.output
    assert hint in result.output
    assert "Did you mean" not in result.output


def test_every_retired_option_has_a_case() -> None:
    covered = {
        (argv[1] if argv[0] in ("deps", "project") else argv[0], opt)
        for argv, opt, _ in _CASES
    }
    assert covered == set(RETIRED_OPTIONS)


def test_unrelated_unknown_option_keeps_click_suggestion() -> None:
    result = CliRunner().invoke(main, ["compare", "a.so", "b.so", "--verbos"])
    assert result.exit_code == 64
    assert "Did you mean" in result.output


def test_root_version_still_prints_tool_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
