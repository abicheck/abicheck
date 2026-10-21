"""Branch coverage for the use-case report projections and the required-symbol
config fold: the paths the end-to-end use-case tests don't reach."""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest

from abicheck.policy.use_case_attribution import UseCaseImpact
from abicheck.report.use_case_impact_render import (
    annotate_affected_use_cases,
    render_use_case_impact_lines,
    use_case_impact_block,
    use_case_impact_text_lines,
)
from abicheck.workflows.compare.configuration import resolve_required_symbol_policy


def _impact() -> UseCaseImpact:
    return UseCaseImpact(
        manifest="uc.yaml",
        use_case_count=1,
        total_changes=0,
        unattributed_changes=0,
    )


def test_block_and_text_are_none_without_an_attribution() -> None:
    result = SimpleNamespace()
    assert use_case_impact_block(result) is None
    assert use_case_impact_block(result, displayed=[]) is None
    assert use_case_impact_text_lines(result) is None
    assert use_case_impact_text_lines(result, displayed=[]) is None


def test_block_and_text_project_onto_an_empty_displayed_set() -> None:
    result = SimpleNamespace(use_case_impact=_impact())

    block = use_case_impact_block(result, displayed=[])
    assert block is not None
    assert block["total_changes"] == 0

    impact = _impact()
    assert use_case_impact_text_lines(
        SimpleNamespace(use_case_impact=impact), displayed=[]
    ) == render_use_case_impact_lines(impact.restricted_to([]))


def test_block_and_text_without_projection_describe_the_full_attribution() -> None:
    impact = _impact()
    result = SimpleNamespace(use_case_impact=impact)

    block = use_case_impact_block(result)
    assert block is not None
    assert block["manifest"] == "uc.yaml"
    assert use_case_impact_text_lines(result) == render_use_case_impact_lines(impact)


@pytest.mark.parametrize("entries", [None, "not-a-list", {"finding_id": "x"}])
def test_annotate_leaves_anything_but_a_list_of_entries_untouched(
    entries: object,
) -> None:
    before = copy.deepcopy(entries)
    annotate_affected_use_cases(entries, _impact())
    assert entries == before


def test_annotate_skips_non_dict_and_id_less_entries_and_stamps_the_rest() -> None:
    entries: list[object] = ["row", {"kind": "no-id"}, {"finding_id": "f1"}]
    annotate_affected_use_cases(entries, _impact())
    assert entries[0] == "row"
    assert entries[1] == {"kind": "no-id"}
    assert entries[2] == {"finding_id": "f1", "affected_use_cases": []}


def test_required_symbols_passthrough_without_a_contract() -> None:
    assert resolve_required_symbol_policy(
        "strict_abi",
        required_symbols=(),
        policy_from_commandline=False,
        required_symbols_from_file=(),
        required_symbols_path=None,
        required_symbols_sha=None,
    ) == ("strict_abi", None, None, None)


def test_explicit_policy_wins_over_a_required_symbol_contract() -> None:
    assert resolve_required_symbol_policy(
        "strict_abi",
        required_symbols=("foo",),
        policy_from_commandline=True,
        required_symbols_from_file=(),
        required_symbols_path=None,
        required_symbols_sha=None,
    ) == ("strict_abi", None, None, None)


def test_required_symbol_on_the_command_line_selects_plugin_abi() -> None:
    assert resolve_required_symbol_policy(
        "strict_abi",
        required_symbols=("foo",),
        policy_from_commandline=False,
        required_symbols_from_file=(),
        required_symbols_path=None,
        required_symbols_sha=None,
    ) == ("plugin_abi", "--required-symbol", None, None)


def test_required_symbol_file_that_contributed_records_its_path_and_digest(
    tmp_path: Path,
) -> None:
    path = str(tmp_path / "symbols.txt")
    assert resolve_required_symbol_policy(
        "strict_abi",
        required_symbols=("foo",),
        policy_from_commandline=False,
        required_symbols_from_file=("foo",),
        required_symbols_path=path,
        required_symbols_sha="abc123",
    ) == ("plugin_abi", "--required-symbol", Path(path), "abc123")


def test_required_symbol_file_that_parsed_empty_records_no_path() -> None:
    # A file that contributed nothing selected nothing: the receipt names
    # the spelling that really contributed, not the file it was given.
    assert resolve_required_symbol_policy(
        "strict_abi",
        required_symbols=("foo",),
        policy_from_commandline=False,
        required_symbols_from_file=(),
        required_symbols_path="symbols.txt",
        required_symbols_sha="abc123",
    ) == ("plugin_abi", "--required-symbol", None, None)
