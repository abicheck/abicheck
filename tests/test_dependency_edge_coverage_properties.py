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

"""Metamorphic property tests for the ADR-041 dependency-edge coverage logic.

``_common_dependency_edge_kinds`` (``source_graph_findings.py``) decides
whether the *absence* of a ``DEPENDENCY_EDGE_KINDS`` edge on the old side of
a version diff is trustworthy evidence a dependency genuinely did not exist
before, or merely a coverage gap a collector improvement papered over. Its
docstring documents roughly sixteen Codex-review-caught bugs in this exact
bookkeeping (narrowed/degraded/scope-matching interactions) — a strong signal
this is exactly the kind of logic hand-picked example tests under-cover: each
existing unit test in ``test_l3l4l5_new_kinds.py`` fixes *one* combination of
pass-confirmation/narrowed/degraded flags. This file instead randomizes the
combination space against an oracle derived independently from the
*documented* rules (read from the docstring, not from tracing the
implementation), so a regression in an untested combination is caught even
when no hand-written case happens to hit it — the same "two independent
implementations of one spec must agree" pattern
``test_detector_properties.py`` uses for the main detector pipeline.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.buildsource.header_graph import HEADER_TYPE_GRAPH_PASS
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.buildsource.source_graph_findings import (
    _common_dependency_edge_kinds,
    _internal_dependency_findings,
)

pytestmark = pytest.mark.slow

#: The four non-call edge kinds ``type_graph.py`` populates in one AST pass
#: (ADR-041 P0 slice 1) — the family this module's coverage logic governs.
_TYPE_GRAPH_FAMILY = frozenset(
    {"DECL_REFERENCES_DECL", "DECL_HAS_TYPE", "TYPE_HAS_FIELD_TYPE", "TYPE_INHERITS"}
)
#: Structural kinds a header-only pass has true project-wide visibility of —
#: mirrors ``source_graph_findings._HEADER_FULL_VISIBILITY_KINDS`` (kept as an
#: independent literal here rather than imported, so the oracle is derived
#: from the documented rule, not from the module under test).
_HEADER_FULL_VISIBILITY = frozenset(
    {"DECL_HAS_TYPE", "TYPE_HAS_FIELD_TYPE", "TYPE_INHERITS"}
)

_KIND = st.sampled_from(sorted(_TYPE_GRAPH_FAMILY))


def _N(nid: str, kind: str, **attrs: object) -> GraphNode:
    return GraphNode(id=nid, kind=kind, label=nid, attrs=dict(attrs))


def _E(src: str, dst: str, kind: str) -> GraphEdge:
    return GraphEdge(src=src, dst=dst, kind=kind)


def _base_nodes() -> list[GraphNode]:
    return [_N("a", "source_decl"), _N("b", "record_type")]


# ---------------------------------------------------------------------------
# Scenario 1 — pass-confirmation dimension.
#
# Old carries ZERO edges of *kind*; new carries a confirmed full pass and one
# edge of *kind*. Randomize only old's confirmation flags and check the
# result against the documented rule: a kind is trusted (i.e. old's absence
# counts as a real, verified zero) iff old ran a confirmed *full*
# (build-integrated) pass, OR old ran a confirmed *header-only* pass AND
# *kind* is one of the three structural kinds a header-only scan can fully
# vouch for (never the body-dependent DECL_REFERENCES_DECL).
# ---------------------------------------------------------------------------
@given(
    kind=_KIND,
    old_full_confirmed=st.booleans(),
    old_header_confirmed=st.booleans(),
)
@settings(max_examples=200, deadline=None)
def test_pass_confirmation_oracle(
    kind: str, old_full_confirmed: bool, old_header_confirmed: bool
) -> None:
    old_passes: dict[str, bool] = {}
    if old_full_confirmed:
        old_passes["type_graph"] = True
    if old_header_confirmed:
        old_passes[HEADER_TYPE_GRAPH_PASS] = True

    old = SourceGraphSummary(nodes=_base_nodes(), edges=[], extractor_passes=old_passes)
    new = SourceGraphSummary(
        nodes=_base_nodes(),
        edges=[_E("a", "b", kind)],
        extractor_passes={"type_graph": True},
    )

    expected_trusted = old_full_confirmed or (
        old_header_confirmed and kind in _HEADER_FULL_VISIBILITY
    )
    common = _common_dependency_edge_kinds(old, new)
    assert (kind in common) == expected_trusted, (
        f"kind={kind} old_full={old_full_confirmed} old_header={old_header_confirmed}: "
        f"expected trusted={expected_trusted}, got common={sorted(common)}"
    )


# ---------------------------------------------------------------------------
# Scenario 2 — matched-narrowed-scope dimension.
#
# Both sides run a narrowed pass; old carries zero edges of *kind*, new
# carries one. Oracle: trusted iff the two sides' ``narrowed_scope`` are
# identical AND non-empty — "both narrowed" alone (the fourteenth Codex
# review's counter-example) is documented as insufficient.
# ---------------------------------------------------------------------------
_SCOPE = st.frozensets(
    st.sampled_from(["src/a.cpp", "src/b.cpp", "src/c.cpp"]), max_size=2
)


@given(kind=_KIND, old_scope=_SCOPE, new_scope=_SCOPE)
@settings(max_examples=200, deadline=None)
def test_narrowed_scope_matching_oracle(
    kind: str, old_scope: frozenset[str], new_scope: frozenset[str]
) -> None:
    old = SourceGraphSummary(
        nodes=_base_nodes(),
        edges=[],
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": old_scope},
    )
    new = SourceGraphSummary(
        nodes=_base_nodes(),
        edges=[_E("a", "b", kind)],
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": new_scope},
    )

    expected_trusted = bool(old_scope) and old_scope == new_scope
    common = _common_dependency_edge_kinds(old, new)
    assert (kind in common) == expected_trusted, (
        f"kind={kind} old_scope={sorted(old_scope)} new_scope={sorted(new_scope)}: "
        f"expected trusted={expected_trusted}, got common={sorted(common)}"
    )


# ---------------------------------------------------------------------------
# Scenario 3 — symmetric-edges invariant.
#
# When the SAME edge is present on both sides — i.e. nothing about the actual
# dependency changed — no combination of coverage bookkeeping may manufacture
# a finding: whether *kind* counts as "common" or not is irrelevant precisely
# because old already reaches the same target via the same edge, so
# ``_internal_dependency_findings`` can never legitimately report it as new.
# This guards the *symmetric* half of the documented one-directional-risk
# design ("the false-positive risk ... lives entirely in whether old's
# absence ... is trustworthy, not in new's own scope"): a future regression
# that started treating `new`'s own narrowing/degraded state as equally
# untrustworthy would still pass every existing hand-written case (none of
# them hold edges identical on both sides) but would break this property.
# ---------------------------------------------------------------------------
_BOOL = st.booleans()


@given(
    kind=_KIND,
    old_full=_BOOL,
    old_header=_BOOL,
    old_narrowed=_BOOL,
    old_degraded=_BOOL,
    new_full=_BOOL,
    new_header=_BOOL,
    new_narrowed=_BOOL,
    new_degraded=_BOOL,
)
@settings(max_examples=300, deadline=None)
def test_identical_edges_never_read_as_new_dependency(
    kind: str,
    old_full: bool,
    old_header: bool,
    old_narrowed: bool,
    old_degraded: bool,
    new_full: bool,
    new_header: bool,
    new_narrowed: bool,
    new_degraded: bool,
) -> None:
    def _passes(full: bool, header: bool) -> dict[str, bool]:
        p: dict[str, bool] = {}
        if full:
            p["type_graph"] = True
        if header:
            p[HEADER_TYPE_GRAPH_PASS] = True
        return p

    shared_scope = frozenset({"src/a.cpp"})
    shared_nodes = [
        _N("a", "source_decl", visibility="public_header"),
        _N("b", "record_type", visibility="private_header"),
        _N("hdr:h", "header"),
    ]
    shared_edges = [_E("hdr:h", "a", "SOURCE_DECLARES"), _E("a", "b", kind)]

    old = SourceGraphSummary(
        nodes=shared_nodes,
        edges=shared_edges,
        extractor_passes=_passes(old_full, old_header),
        narrowed_passes={"type_graph": True} if old_narrowed else {},
        narrowed_scope={"type_graph": shared_scope} if old_narrowed else {},
        degraded_passes={"type_graph": True} if old_degraded else {},
    )
    new = SourceGraphSummary(
        nodes=shared_nodes,
        edges=shared_edges,
        extractor_passes=_passes(new_full, new_header),
        narrowed_passes={"type_graph": True} if new_narrowed else {},
        narrowed_scope={"type_graph": shared_scope} if new_narrowed else {},
        degraded_passes={"type_graph": True} if new_degraded else {},
    )

    findings = _internal_dependency_findings(old, new, {}, "boundary")
    assert findings == [], (
        f"kind={kind} old=(full={old_full},header={old_header},"
        f"narrowed={old_narrowed},degraded={old_degraded}) "
        f"new=(full={new_full},header={new_header},narrowed={new_narrowed},"
        f"degraded={new_degraded}): identical edges on both sides must never "
        f"produce a finding, got {findings}"
    )
