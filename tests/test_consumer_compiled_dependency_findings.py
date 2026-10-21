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
"""``PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED`` (G29 Phase 6, header-graph
tier): the positive shape, the negative twins that must stay silent, and every
way the header-only evidence can be missing. The committed fixture pair
(case221) exercises the same detector end to end; these tests pin the edge
cases the fixture does not.

No consumer build evidence exists, so no test here asserts which consumer
includes the header, and every finding must stay below the break tiers."""

from __future__ import annotations

import pytest

from abicheck.checker_policy import API_BREAK_KINDS, BREAKING_KINDS, RISK_KINDS
from abicheck.compare.consumer_compiled_dependency import (
    consumer_compiled_dependency_findings,
)
from abicheck.model.change_catalog.kinds import ChangeKind
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.model.source_graph_coverage import (
    HEADER_BODY_ATTR,
    HEADER_CALL_GRAPH_PASS,
)

BODY = "decl:demo::scale"
MUL = "decl:demo::detail::mul"
SATURATE = "decl:demo::detail::saturate"
HEADER = "hdr:include/demo/scale.h"
_PUBLIC = {"visibility": "public_header", HEADER_BODY_ATTR: True}


def _graph(
    deps: list[str],
    *,
    body_attrs: dict | None = None,
    passes: tuple[str, ...] = (HEADER_CALL_GRAPH_PASS,),
    narrowed: tuple[str, ...] = (),
    degraded: tuple[str, ...] = (),
    order: str = "forward",
) -> SourceGraphSummary:
    """A header-only graph whose inline body ``demo::scale`` calls *deps*."""
    nodes = [
        GraphNode(
            id=BODY,
            kind="source_decl",
            label="demo::scale(int)",
            attrs=_PUBLIC if body_attrs is None else body_attrs,
        ),
        GraphNode(id=HEADER, kind="header", label="demo/scale.h"),
    ]
    nodes += [
        GraphNode(
            id=d,
            kind="source_decl",
            label=f"demo::{d.split('::')[-1]}()",
            attrs={"visibility": "private_header"},
        )
        for d in deps
    ]
    edges = [GraphEdge(src=HEADER, dst=BODY, kind="SOURCE_DECLARES")]
    edges += [GraphEdge(src=BODY, dst=d, kind="DECL_CALLS_DECL") for d in deps]
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


def test_added_dependency_of_public_inline_body_is_one_risk_finding() -> None:
    old = _graph([MUL])
    new = _graph([MUL, SATURATE])

    findings = consumer_compiled_dependency_findings(old, new)

    assert _kinds(findings) == [ChangeKind.PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED]
    finding = findings[0]
    assert finding.symbol == "demo::scale(int)"
    assert "Added: demo::saturate()" in finding.description
    assert finding.old_value == "1 compiled-in dependencies"
    assert finding.new_value == "2 compiled-in dependencies"


def test_removed_dependency_is_named_as_removed() -> None:
    findings = consumer_compiled_dependency_findings(
        _graph([MUL, SATURATE]), _graph([MUL])
    )

    assert _kinds(findings) == [ChangeKind.PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED]
    assert "Removed: demo::saturate()" in findings[0].description


def test_body_gaining_its_first_dependency_is_a_change() -> None:
    # An observed "calls nothing" is a fact, not a missing entry.
    findings = consumer_compiled_dependency_findings(_graph([]), _graph([MUL]))

    assert _kinds(findings) == [ChangeKind.PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED]


def test_unchanged_dependency_set_is_silent() -> None:
    assert consumer_compiled_dependency_findings(_graph([MUL]), _graph([MUL])) == []


def test_reordered_dependency_set_is_not_a_change() -> None:
    old = _graph([MUL, SATURATE])
    new = _graph([MUL, SATURATE], order="reverse")

    assert consumer_compiled_dependency_findings(old, new) == []


def test_private_header_body_is_not_consumer_surface() -> None:
    private = {"visibility": "private_header", HEADER_BODY_ATTR: True}

    old = _graph([MUL], body_attrs=private)
    new = _graph([MUL, SATURATE], body_attrs=private)

    assert consumer_compiled_dependency_findings(old, new) == []


def test_out_of_line_definition_is_skipped() -> None:
    # An ordinary out-of-line body is never in a header, so the header pass
    # never marks it and its dependencies are not part of this tier at all.
    out_of_line = {"visibility": "public_header"}

    old = _graph([MUL], body_attrs=out_of_line)
    new = _graph([MUL, SATURATE], body_attrs=out_of_line)

    assert consumer_compiled_dependency_findings(old, new) == []


@pytest.mark.parametrize(
    "side",
    [
        pytest.param({"passes": ()}, id="header-pass-absent"),
        pytest.param(
            {"narrowed": (HEADER_CALL_GRAPH_PASS,)}, id="header-pass-narrowed"
        ),
        pytest.param(
            {"degraded": (HEADER_CALL_GRAPH_PASS,)}, id="header-pass-degraded"
        ),
    ],
)
def test_incomplete_header_evidence_on_either_side_yields_nothing(side: dict) -> None:
    complete_old = _graph([MUL])
    complete_new = _graph([MUL, SATURATE])
    incomplete = _graph([MUL, SATURATE], **side)

    # Missing or partial evidence on the new side hides the change ...
    assert consumer_compiled_dependency_findings(complete_old, incomplete) == []
    # ... and on the old side it is the same silence.
    assert consumer_compiled_dependency_findings(incomplete, complete_new) == []


def test_graph_without_any_pass_flag_yields_nothing() -> None:
    # A hand-built graph that recorded no pass flags cannot prove its absences.
    bare_old = SourceGraphSummary(nodes=_graph([MUL]).nodes, edges=_graph([MUL]).edges)
    bare_new = SourceGraphSummary(
        nodes=_graph([MUL, SATURATE]).nodes, edges=_graph([MUL, SATURATE]).edges
    )

    assert consumer_compiled_dependency_findings(bare_old, bare_new) == []


def test_finding_is_risk_never_a_break() -> None:
    findings = consumer_compiled_dependency_findings(
        _graph([MUL]), _graph([MUL, SATURATE])
    )

    kind = ChangeKind.PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED
    assert kind in RISK_KINDS
    assert kind not in API_BREAK_KINDS
    assert kind not in BREAKING_KINDS
    assert findings[0].source_location == "[L2_HEADER_GRAPH]"


def _reference(src: str, dst: str, resolution: str | None) -> GraphEdge:
    attrs = {} if resolution is None else {"resolution": resolution}
    return GraphEdge(src=src, dst=dst, kind="DECL_REFERENCES_DECL", attrs=attrs)


def test_unresolved_reference_skips_its_body_but_not_the_others() -> None:
    # The same unchanged reference is a bare name on one side and a declaration
    # id on the other; comparing it would read a non-change as add plus remove.
    unresolved_old = _graph([MUL])
    unresolved_new = _graph([MUL])
    unresolved_old.edges.append(_reference(BODY, "bare_name", "unresolved"))
    unresolved_new.edges.append(_reference(BODY, MUL, "unresolved"))
    assert consumer_compiled_dependency_findings(unresolved_old, unresolved_new) == []


def test_exact_reference_is_compared_like_a_call() -> None:
    old = _graph([MUL])
    new = _graph([MUL, SATURATE])
    new.edges.append(_reference(BODY, SATURATE, "exact"))

    findings = consumer_compiled_dependency_findings(old, new)

    assert _kinds(findings) == [ChangeKind.PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED]


def test_reference_without_a_resolver_verdict_is_not_trusted() -> None:
    old = _graph([MUL])
    new = _graph([MUL])
    new.edges.append(_reference(BODY, SATURATE, None))

    assert consumer_compiled_dependency_findings(old, new) == []
