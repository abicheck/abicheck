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
from pathlib import Path

import pytest
from _family_f2_routes import CORPUS

from abicheck import checker
from abicheck.workflows.compare import (
    execute as compare_execute,
)
from abicheck.workflows.compare.execute import run_compare
from abicheck.workflows.compare_policy import compare_snapshots
from abicheck.workflows.history import HistoryEntry, build_longitudinal_history
from abicheck.workflows.request_inputs import InputSpec
from abicheck.workflows.service_requests import CompareRequest

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


def test_run_compare_builds_the_default_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Behavior-level pin: with no options given, the request `run_compare`
    # builds equals a bare `CompareRequest`. Catches a shim default that drifts
    # from its field, and a mapping slip (e.g. `scope_to_public_surface` onto
    # `scope_public`) that a signature comparison would not see.
    captured: list[CompareRequest] = []

    def _capture(request: CompareRequest) -> object:
        captured.append(request)
        return None

    monkeypatch.setattr(compare_execute, "run_compare_request", _capture)
    old, new = Path("old.so"), Path("new.so")
    run_compare(old, new)
    assert captured == [
        CompareRequest(old=InputSpec(path=old), new=InputSpec(path=new))
    ]


def test_chokepoint_defaults_like_compare_request() -> None:
    # The chokepoint still restates these two defaults as literals; this keeps
    # them equal to the request's until it derives them the same way.
    expected = _request_default("pattern_verdicts")
    assert (
        inspect.signature(compare_snapshots).parameters["pattern_verdicts"].default
        == expected
    )
    # ADR-091 R10: the request no longer carries contract_evaluation; contract
    # relevance is always evaluated, so the chokepoint's own default is True.
    assert (
        inspect.signature(compare_snapshots).parameters["contract_evaluation"].default
        is True
    )


def test_core_compare_keeps_its_raw_defaults() -> None:
    # Tier-1 core is raw by design (ADR-049: `compare()`'s own behavior is
    # unchanged without an explicit opt-in). Pinned so a change is deliberate.
    params = inspect.signature(checker.compare).parameters
    assert params["pattern_verdicts"].default is False
    assert params["contract_evaluation"].default is False
