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
"""G29 Phase 6 header-graph fixture pair for
``PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED``.

Split out of ``gen_l3l4l5_examples.py`` the same way
``gen_l3l4l5_dispatch_cases.py`` is, to keep that generator under its line
cap. It is imported by that generator, which passes in its own
``_graph``/``_N``/``_E`` builders. Run ``gen_l3l4l5_examples.py`` (not this
file) to write the fixtures.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph_coverage import (
    HEADER_BODY_ATTR,
    HEADER_CALL_GRAPH_PASS,
    HEADER_DECLARATIONS_PASS,
    HEADER_TYPE_GRAPH_PASS,
)

Case = tuple[str, dict[str, Any], dict[str, Any]]


def consumer_cases(
    graph: Callable[..., dict[str, Any]],
    node: Callable[..., GraphNode],
    edge: Callable[..., GraphEdge],
) -> dict[str, Case]:
    """The header-graph consumer-compiled pair, built with the caller's helpers."""
    cases: dict[str, Case] = {}
    # ---- G29 Phase 6: PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED -----------
    # ``demo::scale()`` is an inline function written in the public header
    # ``include/demo/scale.h``, so its body is compiled into every consumer that
    # includes the header. v1 calls ``demo::detail::mul()``; v2 also calls
    # ``demo::detail::saturate()``. Nothing is exported, so there is no symbol
    # to change: the only evidence is the header-written body's dependency set.
    scale = "decl:demo::scale"
    mul = "decl:demo::detail::mul"
    saturate = "decl:demo::detail::saturate"
    header = "hdr:include/demo/scale.h"
    base = [
        node(
            scale,
            "source_decl",
            "demo::scale(int)",
            {"visibility": "public_header", HEADER_BODY_ATTR: True},
        ),
        node(
            mul,
            "source_decl",
            "demo::detail::mul(int, int)",
            {"visibility": "private_header"},
        ),
        node(header, "header", "demo/scale.h"),
    ]
    sat = node(
        saturate,
        "source_decl",
        "demo::detail::saturate(int)",
        {"visibility": "private_header"},
    )
    common = [edge(header, scale, "SOURCE_DECLARES")]
    # Only the header-only call pass is asserted: its absence of a further
    # dependency is then proven for every header-written body it parsed.
    passes = {
        HEADER_CALL_GRAPH_PASS: True,
        HEADER_TYPE_GRAPH_PASS: True,
        HEADER_DECLARATIONS_PASS: True,
    }
    old_edges = common + [edge(scale, mul, "DECL_CALLS_DECL")]
    new_edges = common + [
        edge(scale, mul, "DECL_CALLS_DECL"),
        edge(scale, saturate, "DECL_CALLS_DECL"),
    ]
    cases["case222_public_consumer_compiled_dependency_changed"] = (
        "L2",
        graph(base, old_edges, extractor_passes=passes),
        graph(base + [sat], new_edges, extractor_passes=passes),
    )
    return cases
