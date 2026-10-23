# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Markdown/digest gating counts equal the JSON's, over a corpus of runs.

Bug class ``report.gating_count_recomputed_from_verdict``: a renderer (or
the review-group builder feeding it) counted "gating" from a finding's raw
verdict instead of ADR-049 D1's ``gate_contribution``, so a group of
contract-excluded breaks read "6 gating finding(s)" over JSON
``gate_contribution: 0`` (SVS field report), and a severity preset demoting
ABI breaks read "2 gating finding(s)" under ``Gate: PASS (exit 0)``.

The corpus is every detector mutation (singly and all at once), the golden
report fixtures, and the committed real-binary snapshot pairs, each crossed
with five gate schemes and four contract states. The compiled-example lane
lives in ``tests/test_report_gating_consistency_examples.py``.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.model.change import Change
from abicheck.serialization import load_snapshot
from tests._detector_mutations import MUTATIONS, build_snapshot
from tests._report_gating_oracle import (
    CONTRACT_STATES,
    SEVERITY_CONFIGS,
    assert_gating_consistent,
    render_all,
    stamp_contract,
)
from tests._report_test_helpers import make_result
from tests.test_golden_output import _fn, _snap

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _mutation_pair(mutations: list[Callable]) -> tuple:
    old_extra: dict = {}
    new_extra: dict = {}
    for tag, mutation in enumerate(mutations, 1):
        old, new, _, _ = mutation(tag=tag)
        for side, extra in ((old_extra, old), (new_extra, new)):
            for key, value in extra.items():
                if isinstance(value, list):
                    side.setdefault(key, []).extend(value)
                else:
                    side.setdefault(key, value)
    return build_snapshot("1.0", {}, old_extra), build_snapshot("2.0", {}, new_extra)


#: Committed snapshot pairs dumped from real binaries (old, new).
_STORED_PAIRS = {
    "action_break": ("action/action_test_v1.json", "action/action_test_v2.json"),
    "action_compat": (
        "action/action_test_v1.json",
        "action/action_test_v1_compat.json",
    ),
    "action_addition": (
        "action/action_test_v1.json",
        "action/action_test_v1_with_addition.json",
    ),
    "contract_context": (
        "contract_context_v1/old.json",
        "contract_context_v1/new.json",
    ),
    **{
        case: (f"header_graph/{case}__v1.json", f"header_graph/{case}__v2.json")
        for case in (
            "case01_symbol_removal",
            "case07_struct_layout",
            "case09_cpp_vtable",
            "case14_cpp_class_size",
        )
    },
}


def _stored_pair(name: str) -> tuple:
    old, new = _STORED_PAIRS[name]
    return load_snapshot(_FIXTURES / old), load_snapshot(_FIXTURES / new)


_CASES: dict[str, Callable[[], tuple]] = {
    **{m.__name__: (lambda m=m: _mutation_pair([m])) for m in MUTATIONS},
    "all_mutations": lambda: _mutation_pair(MUTATIONS),
    "golden_func_removed": lambda: (
        _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei"), _fn("h", "_Z1hi")]),
        _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")]),
    ),
    "golden_no_change": lambda: (
        _snap(ver="1.0", funcs=[_fn("compute", "_Z7computei")]),
        _snap(ver="2.0", funcs=[_fn("compute", "_Z7computei")]),
    ),
    **{name: (lambda name=name: _stored_pair(name)) for name in _STORED_PAIRS},
}


@pytest.mark.parametrize("contract", CONTRACT_STATES)
@pytest.mark.parametrize("scheme", sorted(SEVERITY_CONFIGS))
@pytest.mark.parametrize("case", sorted(_CASES))
def test_markdown_gating_counts_equal_json(
    case: str, scheme: str, contract: str
) -> None:
    old, new = _CASES[case]()
    result = compare(old, new)
    stamp_contract(result, contract)
    total = assert_gating_consistent(
        result, SEVERITY_CONFIGS[scheme], contract=contract
    )
    if contract == "out" or scheme == "all_info":
        assert total == 0


def test_corpus_exercises_gating_and_non_gating_groups() -> None:
    # The parametrized oracle is only as strong as the corpus: it must reach
    # groups that gate and groups that do not under the same scheme.
    old, new = _CASES["all_mutations"]()
    result = compare(old, new)
    document, _, _ = render_all(result, SEVERITY_CONFIGS["default"])
    gating = [g["gating_findings"] for g in document["review_groups"]]
    assert max(gating) > 0
    assert min(gating) == 0


def _out_of_contract_breaks(count: int) -> list[Change]:
    changes = [
        Change(ChangeKind.VAR_REMOVED_ELF_ONLY, f"_ZZN3std1fIiEEvvE1x{i}", "static")
        for i in range(count)
    ]
    return changes


@pytest.mark.parametrize("scheme", sorted(SEVERITY_CONFIGS))
@pytest.mark.parametrize("count", [1, 2, 6])
def test_out_of_contract_breaking_group_renders_zero_everywhere(
    scheme: str, count: int
) -> None:
    from abicheck.checker_policy import Verdict

    result = make_result(Verdict.BREAKING, _out_of_contract_breaks(count))
    stamp_contract(result, "out")
    document, markdown, digest = render_all(result, SEVERITY_CONFIGS[scheme])
    assert {e["severity"] for e in document["changes"]} == {"breaking"}
    assert {e["gate_contribution"] for e in document["changes"]} == {0}
    groups = document["review_groups"]
    assert sum(len(g["member_finding_ids"]) for g in groups) == count
    assert {g["gating_findings"] for g in groups} == {0}
    assert document["result_counts"]["gating"] == 0
    assert document["result_counts"]["gating_review_groups"] == 0
    assert f"{len(groups)} review group(s) across" in markdown
    assert "; 0 gating finding(s)." in markdown
    assert "**Gate contribution:** 0 finding(s)" in markdown
    assert f"**Gate contribution:** {count} finding(s)" not in markdown
    assert "**Review:** 0 gating finding(s) in 0 gating group(s)" in digest
    assert "(0 gating," in digest


_ACTION = _FIXTURES / "action"


@pytest.mark.parametrize(
    ("preset", "exit_code", "gating"),
    [("default", 4, 1), ("strict", 4, 1), ("info-only", 0, 0)],
)
def test_cli_markdown_and_digest_gating_follow_the_preset(
    tmp_path: Path, preset: str, exit_code: int, gating: int
) -> None:
    # Public CLI route of the same class: on base, `info-only` printed
    # "1 gating finding(s) in 1 gating group(s)" under "Gate: PASS (exit 0)".
    from click.testing import CliRunner

    from abicheck.cli import main

    md, js, review = (tmp_path / n for n in ("r.md", "r.json", "d.md"))
    res = CliRunner().invoke(
        main,
        [
            "compare",
            str(_ACTION / "action_test_v1.json"),
            str(_ACTION / "action_test_v2.json"),
            "--severity-preset",
            preset,
            "-o",
            f"markdown={md}",
            "-o",
            f"json={js}",
            "-o",
            f"review={review}",
        ],
    )
    assert res.exit_code == exit_code, res.output
    document = json.loads(js.read_text())
    assert sum(g["gating_findings"] for g in document["review_groups"]) == gating
    assert document["result_counts"]["gating"] == gating
    groups = len(document["review_groups"])
    assert f"; {gating} gating finding(s)." in md.read_text()
    assert (
        f"**Review:** {gating} gating finding(s) in {gating} gating group(s); "
        f"{groups} retained group(s) total" in review.read_text()
    )
