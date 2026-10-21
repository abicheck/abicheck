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
"""G29 Phase 6 L5 fixture pairs for ``PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED``.

Split out of ``gen_l3l4l5_examples.py`` to keep that generator under its
800-line cap. It is imported by that generator, which passes in its own
``_graph``/``_N``/``_E`` builders, so the fixture bytes are unchanged by the
split. Run ``gen_l3l4l5_examples.py`` (not this file) to write them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from abicheck.model.graph_facts import GraphEdge, GraphFact, GraphNode
from abicheck.model.source_graph_coverage import (
    OVERRIDE_GRAPH_PASS,
    PASS_EDGE_KINDS,
    VIRTUAL_DISPATCH_GRAPH_PASS,
)

Case = tuple[str, dict[str, Any], dict[str, Any]]


def dispatch_cases(
    graph: Callable[..., dict[str, Any]],
    node: Callable[..., GraphNode],
    edge: Callable[..., GraphEdge],
) -> dict[str, Case]:
    """The two dispatch-set pairs, built with the caller's graph/node/edge helpers."""
    cases: dict[str, Case] = {}
    # ---- G29 Phase 6: PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED -------------------
    # Both cases share one shape: ``demo::render()`` calls the public virtual
    # ``demo::Shape::area()`` (exported), whose possible override set is
    # materialized as VIRTUAL_CALL_MAY_DISPATCH_TO edges by
    # virtual_dispatch_graph.py. case219 gains an override (Square); case220 keeps
    # the same override set and only lists its edges in the opposite order.
    vd_base = [
        node("decl:demo::render", "source_decl", "demo::render()"),
        node("decl:demo::Shape::area", "source_decl", "demo::Shape::area()"),
        node("decl:demo::Circle::area", "source_decl", "demo::Circle::area()"),
        node("sym:_ZNK4demo5Shape4areaEv", "binary_symbol", "demo::Shape::area"),
        node("hdr:include/demo/shape.h", "header", "demo/shape.h"),
    ]
    vd_square = node("decl:demo::Square::area", "source_decl", "demo::Square::area()")

    def _vd_call(caller: str, base: str) -> GraphEdge:
        # call_kind rides on a producer fact, as call_graph.py writes it; the
        # edge's resolved attrs are folded from facts on load.
        fact = GraphFact(producer="call_graph", attrs={"call_kind": "virtual"})
        return GraphEdge(src=caller, dst=base, kind="DECL_CALLS_DECL", facts=[fact])

    def _vd_override(override: str, base: str) -> GraphEdge:
        return edge(override, base, "METHOD_POSSIBLE_OVERRIDE")

    def _vd_dispatch(caller: str, target: str, base: str) -> GraphEdge:
        return edge(
            caller,
            target,
            "VIRTUAL_CALL_MAY_DISPATCH_TO",
            attrs={"base_method": base, "resolution": "overapprox"},
        )

    vd_shape = "decl:demo::Shape::area"
    vd_circle = "decl:demo::Circle::area"
    vd_squ = "decl:demo::Square::area"
    vd_render = "decl:demo::render"
    vd_common = [
        edge(
            "decl:demo::Shape::area",
            "sym:_ZNK4demo5Shape4areaEv",
            "SOURCE_DECL_MAPS_TO_SYMBOL",
        ),
        edge("hdr:include/demo/shape.h", vd_shape, "SOURCE_DECLARES"),
        _vd_call(vd_render, vd_shape),
        _vd_override(vd_circle, vd_shape),
    ]
    # The override and virtual-dispatch passes must have run for the set to be
    # trusted (source_graph_covers); the default flags predate them.
    vd_passes = {
        **dict.fromkeys(PASS_EDGE_KINDS, True),
        OVERRIDE_GRAPH_PASS: True,
        VIRTUAL_DISPATCH_GRAPH_PASS: True,
    }
    vd_old_edges = vd_common + [_vd_dispatch(vd_render, vd_circle, vd_shape)]
    vd_new_edges = vd_old_edges + [
        _vd_override(vd_squ, vd_shape),
        _vd_dispatch(vd_render, vd_squ, vd_shape),
    ]
    cases["case219_public_virtual_dispatch_set_changed"] = (
        "L5",
        graph(vd_base, vd_old_edges, extractor_passes=vd_passes),
        graph(vd_base + [vd_square], vd_new_edges, extractor_passes=vd_passes),
    )

    # case220: the same two overrides on both sides, listed in opposite order.
    vd_both = vd_common + [
        _vd_override(vd_squ, vd_shape),
        _vd_dispatch(vd_render, vd_circle, vd_shape),
        _vd_dispatch(vd_render, vd_squ, vd_shape),
    ]
    cases["case220_public_virtual_dispatch_set_reordered"] = (
        "L5",
        graph(vd_base + [vd_square], vd_both, extractor_passes=vd_passes),
        graph(
            vd_base + [vd_square], list(reversed(vd_both)), extractor_passes=vd_passes
        ),
    )

    return cases
