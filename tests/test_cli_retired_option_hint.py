"""Retired ADR-091 options name their replacement instead of a fuzzy match."""

from __future__ import annotations

import pytest
from click.testing import CliRunner

from abicheck.cli import main
from abicheck.frontends.cli.runtime import RETIRED_OPTIONS

_ARGV = {
    "compare": ["compare", "a.so", "b.so"],
    "dump": ["dump", "a.so"],
    "history": ["project", "history"],
    "tree": ["deps", "tree", "a.so"],
}


@pytest.mark.parametrize(("command", "option"), sorted(RETIRED_OPTIONS))
def test_retired_option_names_replacement_and_exits_64(
    command: str, option: str
) -> None:
    result = CliRunner().invoke(
        main, [*_ARGV[command], option, "x"], standalone_mode=True
    )
    assert result.exit_code == 64
    assert f"No such option: {option}" in result.output
    assert RETIRED_OPTIONS[command, option] in result.output
    assert "Did you mean" not in result.output


def test_unrelated_unknown_option_keeps_click_suggestion() -> None:
    result = CliRunner().invoke(main, ["compare", "a.so", "b.so", "--verbos"])
    assert result.exit_code == 64
    assert "Did you mean" in result.output


def test_root_version_still_prints_tool_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
