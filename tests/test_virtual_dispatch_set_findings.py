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

"""``PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED`` (G29 Phase 6): the positive shape,
the negative twins that must stay silent, and every way the evidence can be
missing. The committed fixture pair (case219/case220) exercises the same
detector end to end; these tests pin the edge cases the fixtures do not."""

from __future__ import annotations

import pytest

from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, RISK_KINDS
from abicheck.compare.virtual_dispatch_set import virtual_dispatch_set_findings
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.graph_facts import GraphEdge, GraphFact, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.model.source_graph_coverage import (
    CALL_GRAPH_PASS,
    OVERRIDE_GRAPH_PASS,
    VIRTUAL_DISPATCH_GRAPH_PASS,
)

BASE = "decl:demo::Shape::area"
RENDER = "decl:demo::render"
SYMBOL = "sym:_ZNK4demo5Shape4areaEv"
_ALL_PASSES = (CALL_GRAPH_PASS, OVERRIDE_GRAPH_PASS, VIRTUAL_DISPATCH_GRAPH_PASS)


def _candidate(name: str) -> str:
    return f"decl:demo::{name}::area"


def _graph(
    candidates: list[str],
    *,
    passes: tuple[str, ...] = _ALL_PASSES,
    narrowed: tuple[str, ...] = (),
    degraded: tuple[str, ...] = (),
    exported: bool = True,
    virtual_call: bool = True,
    order: str = "forward",
) -> SourceGraphSummary:
    """A v1/v2-shaped graph: ``render`` virtually calls ``Shape::area``, whose
    override candidates are *candidates*, as virtual_dispatch_graph.py would
    materialize them."""
    base_attrs = {} if exported else {"visibility": "private_header"}
    nodes = [
        GraphNode(id=RENDER, kind="source_decl", label="demo::render()"),
        GraphNode(
            id=BASE, kind="source_decl", label="demo::Shape::area()", attrs=base_attrs
        ),
        GraphNode(id=SYMBOL, kind="binary_symbol", label="demo::Shape::area"),
    ]
    nodes += [
        GraphNode(id=c, kind="source_decl", label=f"demo::{c.split('::')[1]}::area()")
        for c in candidates
    ]
    edges: list[GraphEdge] = []
    if exported:
        edges.append(GraphEdge(src=BASE, dst=SYMBOL, kind="SOURCE_DECL_MAPS_TO_SYMBOL"))
    if virtual_call:
        fact = GraphFact(producer="call_graph", attrs={"call_kind": "virtual"})
        edges.append(
            GraphEdge(src=RENDER, dst=BASE, kind="DECL_CALLS_DECL", facts=[fact])
        )
    for c in candidates:
        edges.append(GraphEdge(src=c, dst=BASE, kind="METHOD_POSSIBLE_OVERRIDE"))
        edges.append(
            GraphEdge(
                src=RENDER,
                dst=c,
                kind="VIRTUAL_CALL_MAY_DISPATCH_TO",
                attrs={"base_method": BASE, "resolution": "overapprox"},
            )
        )
    if order == "reverse":
        edges.reverse()
    return SourceGraphSummary(
        nodes=nodes,
        edges=edges,
        extractor_passes=dict.fromkeys(passes, True),
        narrowed_passes=dict.fromkeys(narrowed, True),
        degraded_passes=dict.fromkeys(degraded, True),
    )


def _kinds(findings: list) -> list[ChangeKind]:
    return [f.kind for f in findings]


def test_gained_override_is_one_risk_finding_naming_the_new_target() -> None:
    old = _graph([_candidate("Circle")])
    new = _graph([_candidate("Circle"), _candidate("Square")])

    findings = virtual_dispatch_set_findings(old, new)

    assert _kinds(findings) == [ChangeKind.PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED]
    finding = findings[0]
    assert finding.symbol == "demo::Shape::area()"
    assert (finding.old_value, finding.new_value) == (
        "1 possible targets",
        "2 possible targets",
    )
    assert "Added: demo::Square::area()." in finding.description
    assert "Removed:" not in finding.description
    assert finding.source_location == "[L5_SOURCE_GRAPH]"


def test_lost_override_is_reported_as_removed() -> None:
    old = _graph([_candidate("Circle"), _candidate("Square")])
    new = _graph([_candidate("Circle")])

    findings = virtual_dispatch_set_findings(old, new)

    assert _kinds(findings) == [ChangeKind.PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED]
    assert "Removed: demo::Square::area()." in findings[0].description


def test_unchanged_override_set_is_silent() -> None:
    graph = _graph([_candidate("Circle"), _candidate("Square")])
    assert (
        virtual_dispatch_set_findings(
            graph, _graph([_candidate("Circle"), _candidate("Square")])
        )
        == []
    )


def test_reordered_override_set_is_silent() -> None:
    """Set semantics: the same candidates in the opposite edge order is no
    change, the negative twin of the positive case."""
    old = _graph([_candidate("Circle"), _candidate("Square")])
    new = _graph([_candidate("Circle"), _candidate("Square")], order="reverse")

    assert virtual_dispatch_set_findings(old, new) == []


def test_leaf_method_gaining_its_first_override_is_reported() -> None:
    """A base that is virtually called on both sides but had no candidates
    before has an observed empty set; gaining one is a set change."""
    old = _graph([])
    new = _graph([_candidate("Circle")])

    findings = virtual_dispatch_set_findings(old, new)

    assert _kinds(findings) == [ChangeKind.PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED]
    assert findings[0].old_value == "0 possible targets"


def test_base_with_no_virtual_call_site_on_one_side_is_not_compared() -> None:
    """Without a virtual call site a side records no dispatch set for the
    base, so an override appearing there is not a comparable change."""
    old = _graph([], virtual_call=False)
    new = _graph([_candidate("Circle")])

    assert virtual_dispatch_set_findings(old, new) == []


def test_private_base_on_either_side_is_not_compared() -> None:
    old = _graph([_candidate("Circle")], exported=False)
    new = _graph([_candidate("Circle"), _candidate("Square")], exported=False)

    assert virtual_dispatch_set_findings(old, new) == []


@pytest.mark.parametrize(
    ("old_kwargs", "new_kwargs"),
    [
        pytest.param(
            {"passes": (CALL_GRAPH_PASS,)}, {}, id="old-override-and-dispatch-not-run"
        ),
        pytest.param(
            {},
            {"passes": (CALL_GRAPH_PASS, OVERRIDE_GRAPH_PASS)},
            id="new-dispatch-not-run",
        ),
        pytest.param(
            {"narrowed": (OVERRIDE_GRAPH_PASS,)}, {}, id="old-override-narrowed"
        ),
        pytest.param(
            {}, {"degraded": (VIRTUAL_DISPATCH_GRAPH_PASS,)}, id="new-dispatch-degraded"
        ),
        pytest.param({"passes": ()}, {}, id="old-hand-built-no-pass-flags"),
    ],
)
def test_incomplete_evidence_on_either_side_yields_no_finding(
    old_kwargs: dict, new_kwargs: dict
) -> None:
    """Missing or partial graph evidence must not produce the finding. The
    detector also makes no clean claim: an empty result here means only that
    the set was not established."""
    old = _graph([_candidate("Circle")], **old_kwargs)
    new = _graph([_candidate("Circle"), _candidate("Square")], **new_kwargs)

    assert virtual_dispatch_set_findings(old, new) == []


def test_missing_call_edge_coverage_yields_no_finding() -> None:
    old = _graph([_candidate("Circle")])
    new = _graph(
        [_candidate("Circle"), _candidate("Square")],
        passes=(OVERRIDE_GRAPH_PASS, VIRTUAL_DISPATCH_GRAPH_PASS),
    )

    assert virtual_dispatch_set_findings(old, new) == []


def test_kind_is_a_compatible_risk_never_a_break() -> None:
    """Verdict contract: RISK, and never a break on its own. The override set
    is an over-approximation, so it may only ever be reviewed, not gated."""
    kind = ChangeKind.PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED
    assert kind in RISK_KINDS
    assert kind not in BREAKING_KINDS
    assert kind not in API_BREAK_KINDS


def test_stale_alias_spelling_of_base_method_still_joins() -> None:
    """``base_method`` is written at fold time. An identity alias recorded after
    the fold leaves it spelled the old way while the edge endpoints are already
    canonical, so the dispatch set must still join through the alias."""
    old = _graph([_candidate("Circle")])
    new = _graph([_candidate("Circle"), _candidate("Square")])
    alias = "decl:demo::Shape::area_stale"
    for e in new.edges:
        if e.kind == "VIRTUAL_CALL_MAY_DISPATCH_TO":
            e.attrs["base_method"] = alias
    new.add_identity_alias(alias, BASE)

    findings = virtual_dispatch_set_findings(old, new)

    assert _kinds(findings) == [ChangeKind.PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED]
    assert (findings[0].old_value, findings[0].new_value) == (
        "1 possible targets",
        "2 possible targets",
    )
    assert "Added: demo::Square::area()." in findings[0].description
