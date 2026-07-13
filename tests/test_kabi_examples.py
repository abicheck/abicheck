# SPDX-License-Identifier: Apache-2.0
"""Fast-lane validation of the kABI (Module.symvers) example cases.

Cases 175-176 ship a checked-in ``v1.symvers``/``v2.symvers`` pair instead of
compilable v1/v2 source — Module.symvers is parsed directly (no compiler, no
castxml), so this corpus is validated compiler-free, mirroring
``tests/test_g20_catalog.py`` for the audit/cross-source corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.service import compare_snapshots, resolve_input

_REPO = Path(__file__).resolve().parent.parent
_EXAMPLES = _REPO / "examples"
_GT = json.loads((_EXAMPLES / "ground_truth.json").read_text())["verdicts"]

#: kABI cases: those shipping a v1.symvers/v2.symvers fixture pair. Order- and
#: duplicate-insensitive so this predicate can't drift from
#: tests/validate_examples.py's equivalent (set-based) precondition check.
_KABI_CASES = sorted(
    name
    for name, info in _GT.items()
    if set(info.get("fixtures") or []) == {"v1.symvers", "v2.symvers"}
)


def test_kabi_corpus_is_non_empty() -> None:
    assert len(_KABI_CASES) >= 2, f"expected the kABI cases, found {_KABI_CASES}"


@pytest.mark.parametrize("case_name", _KABI_CASES)
def test_case_matches_ground_truth(case_name: str) -> None:
    info = _GT[case_name]
    case_dir = _EXAMPLES / case_name
    old = resolve_input(case_dir / "v1.symvers", is_elf=False)
    new = resolve_input(case_dir / "v2.symvers", is_elf=False)
    assert old.kabi is not None and new.kabi is not None, (
        f"{case_name}: v1.symvers/v2.symvers did not parse as kABI manifests"
    )

    result = compare_snapshots(old, new)
    got_kinds = {c.kind.value for c in result.changes}
    want_kinds = set(info["expected_kinds"])
    assert want_kinds <= got_kinds, (
        f"{case_name}: expected kinds {want_kinds} not all present in {got_kinds}"
    )
    assert result.verdict.value == info["expected"], (
        f"{case_name}: verdict {result.verdict.value} != expected {info['expected']}"
    )
