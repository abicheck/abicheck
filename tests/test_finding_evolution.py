# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""ADR-068 Phase 1 item 2: ``FindingEvolution`` on the canonical finding
model, and its ``policy``/``report`` compute halves.

``TestFindingEvolutionProperties`` follows this repository's "Primitive-level
property tests" convention (root ``AGENTS.md``): the correspondence
algorithm in ``policy.finding_evolution`` is a general-purpose two-collection
matching primitive (the same shape as a merge/dedupe/grouping helper), so its
contract is stated as invariants over generated inputs -- "the classification
is exactly the identity-keyed set partition of previous vs. current, nothing
more and nothing less" -- rather than only a handful of fixed examples.
"""

from __future__ import annotations

import string

from hypothesis import given, strategies as st

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind, FindingEvolution
from abicheck.checker_types import Change, DiffResult
from abicheck.finding_identity import report_finding_id
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.policy.finding_evolution import (
    apply_finding_evolution,
    compute_finding_evolution,
    compute_resolved_findings,
)
from abicheck.report.finding_evolution import (
    FindingEvolutionSummary,
    compute_finding_evolution_summary,
)

_ALPHABET = list(string.ascii_lowercase[:8])


def _change_for(symbol: str) -> Change:
    return Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol=symbol,
        description=f"removed {symbol}",
    )


def _result_for(symbols: set[str], *, tag: str) -> DiffResult:
    return DiffResult(
        old_version=f"{tag}-old",
        new_version=f"{tag}-new",
        library="lib",
        changes=[_change_for(s) for s in sorted(symbols)],
    )


_symbol_sets = st.sets(st.sampled_from(_ALPHABET))


# ---------------------------------------------------------------------------
# Defaults: a plain, single comparison never sets this field.
# ---------------------------------------------------------------------------


def test_change_default_evolution_is_not_evaluated() -> None:
    c = Change(kind=ChangeKind.FUNC_ADDED, symbol="foo", description="added foo")
    assert c.evolution is FindingEvolution.NOT_EVALUATED


def test_diff_result_default_resolved_findings_is_empty() -> None:
    r = DiffResult(old_version="1.0", new_version="2.0", library="lib")
    assert r.resolved_findings == []


def test_real_compare_never_sets_evolution() -> None:
    """``checker.compare()`` itself is not an N>1-comparison consumer -- its
    output must be indistinguishable from any other pre-ADR-068 result."""
    old = AbiSnapshot(library="libfoo", version="1.0")
    new = AbiSnapshot(library="libfoo", version="2.0")
    old.functions.append(
        Function(
            name="foo::gone",
            mangled="_ZN3foo4goneEv",
            return_type="void",
            visibility=Visibility.PUBLIC,
        )
    )
    result = compare(old, new)
    assert result.resolved_findings == []
    assert all(c.evolution is FindingEvolution.NOT_EVALUATED for c in result.changes)
    assert len(result.changes) >= 1  # sanity: the removal was actually detected


# ---------------------------------------------------------------------------
# compute_finding_evolution / compute_resolved_findings / apply_finding_evolution
# ---------------------------------------------------------------------------


def test_no_previous_comparison_is_not_evaluated_for_every_current_finding() -> None:
    current = _result_for({"a", "b", "c"}, tag="current")
    evolutions = compute_finding_evolution(current, None)
    assert set(evolutions.values()) == {FindingEvolution.NOT_EVALUATED}
    assert compute_resolved_findings(current, None) == []


def test_introduced_persistent_resolved_concrete_example() -> None:
    previous = _result_for({"old_only", "both"}, tag="previous")
    current = _result_for({"both", "new_only"}, tag="current")

    apply_finding_evolution(current, previous)

    by_symbol = {c.symbol: c.evolution for c in current.changes}
    assert by_symbol["both"] is FindingEvolution.PERSISTENT
    assert by_symbol["new_only"] is FindingEvolution.INTRODUCED

    resolved_symbols = {c.symbol for c in current.resolved_findings}
    assert resolved_symbols == {"old_only"}
    assert all(
        c.evolution is FindingEvolution.RESOLVED for c in current.resolved_findings
    )


def test_resolved_findings_are_independent_copies() -> None:
    """A resolved entry must never be the *same* object the previous
    ``DiffResult`` still references -- mutating one must not reach back into
    a comparison chain's earlier step."""
    previous = _result_for({"gone"}, tag="previous")
    current = _result_for(set(), tag="current")

    resolved = compute_resolved_findings(current, previous)
    assert len(resolved) == 1
    assert resolved[0] is not previous.changes[0]
    resolved[0].description = "mutated"
    assert previous.changes[0].description != "mutated"


class TestFindingEvolutionProperties:
    """The correspondence primitive states an exact set partition -- see the
    module docstring."""

    @given(previous_symbols=_symbol_sets, current_symbols=_symbol_sets)
    def test_partition_matches_set_difference(
        self, previous_symbols: set[str], current_symbols: set[str]
    ) -> None:
        previous = _result_for(previous_symbols, tag="previous")
        current = _result_for(current_symbols, tag="current")

        evolutions = compute_finding_evolution(current, previous)
        resolved = compute_resolved_findings(current, previous)

        introduced = {
            s
            for s in current_symbols
            if evolutions[report_finding_id(_change_for(s))]
            is FindingEvolution.INTRODUCED
        }
        persistent = {
            s
            for s in current_symbols
            if evolutions[report_finding_id(_change_for(s))]
            is FindingEvolution.PERSISTENT
        }
        resolved_symbols = {c.symbol for c in resolved}

        assert introduced == current_symbols - previous_symbols
        assert persistent == current_symbols & previous_symbols
        assert resolved_symbols == previous_symbols - current_symbols
        # Every current finding is classified exactly once, as one of the
        # two states reachable when a previous comparison IS supplied.
        assert introduced | persistent == current_symbols
        assert introduced.isdisjoint(persistent)

    @given(previous_symbols=_symbol_sets, current_symbols=_symbol_sets)
    def test_never_classifies_a_current_finding_as_resolved(
        self, previous_symbols: set[str], current_symbols: set[str]
    ) -> None:
        """``RESOLVED`` is never a value ``compute_finding_evolution`` (or
        therefore ``apply_finding_evolution``) assigns to a *current*
        finding -- it only ever appears on ``resolved_findings`` entries,
        which are never members of ``changes``."""
        previous = _result_for(previous_symbols, tag="previous")
        current = _result_for(current_symbols, tag="current")

        apply_finding_evolution(current, previous)

        assert all(
            c.evolution is not FindingEvolution.RESOLVED for c in current.changes
        )
        assert all(
            c.evolution is FindingEvolution.RESOLVED for c in current.resolved_findings
        )

    @given(previous_symbols=_symbol_sets, current_symbols=_symbol_sets)
    def test_order_independence(
        self, previous_symbols: set[str], current_symbols: set[str]
    ) -> None:
        """Classification depends only on identity membership, never on the
        order ``changes`` happens to list findings in."""
        previous_a = _result_for(previous_symbols, tag="previous")
        current_a = _result_for(current_symbols, tag="current")
        evolutions_a = compute_finding_evolution(current_a, previous_a)

        previous_b = _result_for(previous_symbols, tag="previous")
        previous_b.changes.reverse()
        current_b = _result_for(current_symbols, tag="current")
        current_b.changes.reverse()
        evolutions_b = compute_finding_evolution(current_b, previous_b)

        assert evolutions_a == evolutions_b

    @given(symbols=_symbol_sets)
    def test_identical_chain_step_is_fully_persistent(self, symbols: set[str]) -> None:
        """Comparing a step against an identical predecessor: every current
        finding is persistent, nothing is introduced or resolved."""
        previous = _result_for(symbols, tag="previous")
        current = _result_for(symbols, tag="current")

        apply_finding_evolution(current, previous)

        assert all(c.evolution is FindingEvolution.PERSISTENT for c in current.changes)
        assert current.resolved_findings == []


# ---------------------------------------------------------------------------
# report/finding_evolution.py -- the compute/render half
# ---------------------------------------------------------------------------


class TestFindingEvolutionSummary:
    def test_not_evaluated_run_reports_every_finding_explicitly(self) -> None:
        """ADR-067 D3's convention, reused: no baseline means every finding
        states ``not_evaluated`` -- it is never silently omitted."""
        current = _result_for({"a", "b"}, tag="current")
        summary = compute_finding_evolution_summary(current)
        counts = dict(summary.counts)
        assert counts["not_evaluated"] == 2
        assert counts["introduced"] == counts["persistent"] == counts["resolved"] == 0
        assert summary.resolved == ()

    @given(previous_symbols=_symbol_sets, current_symbols=_symbol_sets)
    def test_counts_conserve_against_the_underlying_result(
        self, previous_symbols: set[str], current_symbols: set[str]
    ) -> None:
        previous = _result_for(previous_symbols, tag="previous")
        current = _result_for(current_symbols, tag="current")
        apply_finding_evolution(current, previous)

        summary = compute_finding_evolution_summary(current)
        counts = dict(summary.counts)
        assert counts["introduced"] + counts["persistent"] + counts[
            "not_evaluated"
        ] == len(current.changes)
        assert counts["resolved"] == len(current.resolved_findings)
        assert len(summary.resolved) == len(current.resolved_findings)

    def test_json_round_trip(self) -> None:
        previous = _result_for({"old_only", "both"}, tag="previous")
        current = _result_for({"both", "new_only"}, tag="current")
        apply_finding_evolution(current, previous)

        summary = compute_finding_evolution_summary(current)
        rebuilt = FindingEvolutionSummary.from_dict(summary.to_dict())
        assert rebuilt == summary

    def test_add_finding_evolution_is_unconditional(self) -> None:
        """Matches ``add_disposition_audit``'s own "never omitted" rule."""
        from abicheck.report.finding_evolution import add_finding_evolution

        current = _result_for(set(), tag="current")
        d: dict[str, object] = {}
        add_finding_evolution(d, current)
        assert "finding_evolution" in d
        assert d["finding_evolution"]["counts"] == {
            "introduced": 0,
            "resolved": 0,
            "persistent": 0,
            "not_evaluated": 0,
        }
