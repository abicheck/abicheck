# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Every in-tree comparison path runs under the same defaults.

ADR-037 D1/D2: the Tier-2 chokepoint (``workflows.compare_policy.compare_snapshots``)
is where a comparison's options are decided, and the Tier-1 core
(``checker.compare``) keeps its own, deliberately raw defaults. The longitudinal
``history`` workflow composes pairwise comparisons; it must reach the chokepoint,
or its pairwise verdicts and findings drift from the ``compare`` result for the
same two snapshots (``PUBLIC_SURFACE_GREW``/``SHRANK`` were missing before).

The oracle is the chokepoint itself, not ``history``'s own helper.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest
from _family_f2_routes import CORPUS

from abicheck import checker
from abicheck.service_compare_pipeline import run_compare
from abicheck.workflows.compare_policy import compare_snapshots
from abicheck.workflows.contracts import CompareRequest
from abicheck.workflows.history import HistoryEntry, build_longitudinal_history

#: ``scope_mismatch`` is a refused comparison (comparability gate), not a verdict.
_COMPARABLE = sorted(c for c in CORPUS if c != "scope_mismatch")


def _request_default(name: str) -> object:
    field = next(f for f in dataclasses.fields(CompareRequest) if f.name == name)
    return field.default


@pytest.mark.parametrize("case", _COMPARABLE)
def test_history_pair_matches_the_chokepoint(case: str) -> None:
    old, new = CORPUS[case]
    expected = compare_snapshots(
        old, new, policy="strict_abi", scope_to_public_surface=True
    )
    history = build_longitudinal_history(
        [
            HistoryEntry(index=0, version="1.0", path="old.json", snapshot=old),
            HistoryEntry(index=1, version="2.0", path="new.json", snapshot=new),
        ]
    )
    (pair,) = history.pairwise
    assert pair.verdict == expected.verdict.value
    assert pair.change_count == len(expected.changes)


@pytest.mark.parametrize("name", ["pattern_verdicts", "contract_evaluation"])
def test_chokepoint_and_run_compare_default_like_compare_request(name: str) -> None:
    # S1 pin: `run_compare` and the chokepoint repeat `CompareRequest`'s
    # defaults as literals; this test is what keeps those literals honest.
    expected = _request_default(name)
    assert inspect.signature(run_compare).parameters[name].default == expected
    assert inspect.signature(compare_snapshots).parameters[name].default == expected


def test_core_compare_keeps_its_raw_defaults() -> None:
    # Tier-1 core is raw by design (ADR-049: `compare()`'s own behavior is
    # unchanged without an explicit opt-in). Pinned so a change is deliberate.
    params = inspect.signature(checker.compare).parameters
    assert params["pattern_verdicts"].default is False
    assert params["contract_evaluation"].default is False
