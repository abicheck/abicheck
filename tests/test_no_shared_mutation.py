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

"""Functions that enrich a ``Change`` must not write to the caller's object.

A ``Change`` handed to :func:`enrich_covered_changes` is the same object as
``DiffResult.changes``' element. The library-wide report reads it, and every
other ``--used-by`` app scoped against that diff reads it too. Writing one
consumer's proof path onto it leaked that path into the unscoped view and
made the first app's walk win for every later app. The contract pinned here:

* the original ``Change`` is unchanged after the call;
* the enriched finding is in the returned/propagated list;
* two consumers enriching the same original each see only their own path.
"""

from __future__ import annotations

import dataclasses

import pytest

from abicheck.buildsource.source_graph import GraphNode, SourceGraphSummary
from abicheck.checker import Change
from abicheck.checker_policy import ChangeKind
from abicheck.impact.consumer_graph import ConsumerImpactPath
from abicheck.model.evidence_status import ReachabilityState
from abicheck.workflows.consumer_impact import enrich_covered_changes


def _graph(*public_roots: str) -> SourceGraphSummary:
    graph = SourceGraphSummary()
    for root in public_roots:
        graph.add_node(GraphNode(id=f"decl://{root}", kind="source_decl", label=root))
    return graph


def _explanation(symbol: str, root: str, consumer: str) -> ConsumerImpactPath:
    return ConsumerImpactPath(
        consumer=consumer,
        symbol=symbol,
        declarations=(symbol,),
        public_entries=(root,),
    )


def _removed(symbol: str = "_Z9dispatchv") -> Change:
    return Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol=symbol,
        description=f"Function removed: {symbol}",
    )


def test_enrich_leaves_original_change_unmodified():
    original = _removed()
    before = dataclasses.asdict(original)

    graph = _graph("run")
    explanations = {"_Z9dispatchv": _explanation("_Z9dispatchv", "run", "app-a")}

    changes = [original]
    enrich_covered_changes(changes, explanations, graph)

    # The original object is untouched: same fields, same values.
    assert dataclasses.asdict(original) == before
    assert original.reachability_state == ReachabilityState.UNKNOWN
    assert original.public_reachable is False
    assert original.reachability_proof_path is None
    assert original.impact_assessment is None

    # The caller's list carries the enriched copy, not the original.
    enriched = changes[0]
    assert enriched is not original
    assert enriched.kind == original.kind
    assert enriched.symbol == original.symbol
    assert enriched.reachability_state == ReachabilityState.PROVEN_REACHABLE
    assert enriched.public_reachable is True
    assert enriched.reachability_kind == "consumer_proven"
    assert enriched.reachability_proof_path
    assert enriched.affected_public_roots == ["run"]
    assert enriched.impact_assessment is not None
    assert enriched.impact_assessment.public_reachable is True


def test_second_consumer_does_not_inherit_first_consumers_path():
    original = _removed()
    graph = _graph("run", "train")

    first = [original]
    enrich_covered_changes(
        first,
        {"_Z9dispatchv": _explanation("_Z9dispatchv", "run", "app-a")},
        graph,
    )
    second = [original]
    enrich_covered_changes(
        second,
        {"_Z9dispatchv": _explanation("_Z9dispatchv", "train", "app-b")},
        graph,
    )

    assert first[0].affected_public_roots == ["run"]
    assert second[0].affected_public_roots == ["train"]
    assert original.affected_public_roots is None
    assert original.public_reachable is False


def _recorded_result():  # type: ignore[no-untyped-def]
    """A real compare() result with one removal recorded in its ledger."""
    from abicheck.checker import compare
    from abicheck.model import AbiSnapshot, Function
    from abicheck.policy.disposition_close import ledger_for

    old = AbiSnapshot(
        library="libx",
        version="1",
        functions=[Function(name="f", mangled="_Z1fv", return_type="void")],
    )
    new = AbiSnapshot(library="libx", version="2", functions=[])
    result = compare(old, new)
    result.disposition_ledger = ledger_for(result)
    assert result.changes
    return result


@pytest.mark.parametrize("aliased", [True, False])
def test_an_enriched_copy_is_not_a_second_detection(aliased: bool) -> None:
    """The copy enrich_covered_changes makes reaches close_consumer_scope as
    gating and also_detected (the standalone app-compat path passes the same
    list for both). Aliased to its original it is the same record; a bare copy
    would be counted twice -- the control shows the alias is what prevents it."""
    from abicheck.policy.disposition_close import (
        alias_recorded_change,
        close_consumer_scope,
    )

    result = _recorded_result()
    ledger = result.disposition_ledger
    before = ledger.detected_total
    original = result.changes[0]
    copy = dataclasses.replace(original)
    if aliased:
        alias_recorded_change(ledger, copy, original)
    close_consumer_scope(
        ledger, result, gating=[copy], also_detected=[copy], scope_decides_exit=True
    )
    if aliased:
        assert ledger.detected_total == before
    else:
        assert ledger.detected_total == before + 1


def test_alias_is_a_no_op_without_a_ledger_or_an_unrecorded_original() -> None:
    """No ledger, or an original the ledger never recorded: nothing aliased,
    so the copy still counts as its own detection."""
    from abicheck.policy.disposition_close import alias_recorded_change

    result = _recorded_result()
    ledger = result.disposition_ledger
    copy = dataclasses.replace(result.changes[0])
    alias_recorded_change(None, copy, result.changes[0])
    stranger = dataclasses.replace(result.changes[0])
    alias_recorded_change(ledger, copy, stranger)
    assert ledger.index_for(stranger) is None
    assert ledger.index_for(copy) is None
