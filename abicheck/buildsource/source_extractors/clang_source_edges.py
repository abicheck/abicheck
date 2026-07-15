# Copyright 2026 Nikolay Petrov
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

"""``SourceAbiTu.source_edges`` population from an already-parsed clang AST
(P1 #17-18, split out of ``clang.py`` to keep that module under the
AI-readiness line-count cap).

Reuses the existing pure parsers from ``call_graph.py``/``type_graph.py`` (the
same ones the live ``ClangCallGraphExtractor``/``ClangTypeGraphExtractor``
feed) on an AST dict the caller already parsed — never a second ``clang``
invocation ("Collect relationships during the existing compilation AST
traversal", not another frontend pass).
"""

from __future__ import annotations

from typing import Any


def build_source_edges(
    ast_root: dict[str, Any], diags: list[str]
) -> list[dict[str, Any]]:
    """Return deduplicated ``source_edges`` dicts for *ast_root*.

    Best-effort: any parse failure is recorded in *diags* (feeding the
    ``source_edges`` coverage state to ``partial``/``failed``) and yields an
    empty list rather than raising — a graph-edge failure must never abort
    the TU's other facts (ADR-028 D7 "extractor failures ... never abort").
    """
    try:
        from ..call_graph import CallEdge, parse_clang_ast_calls
        from ..type_graph import TypeEdge, parse_clang_ast_types

        call_edges: list[CallEdge] = parse_clang_ast_calls(ast_root)
        type_edges: list[TypeEdge] = parse_clang_ast_types(ast_root)
    except Exception as exc:  # noqa: BLE001 - never abort the TU over edges
        diags.append(f"source_edges unavailable: {exc}")
        return []

    edges: list[dict[str, Any]] = []
    for ce in call_edges:
        if not ce.caller or not ce.callee:
            continue
        edges.append(
            {
                "edge": "DECL_CALLS_DECL",
                "src": ce.caller,
                "dst": ce.callee,
                "provenance": "clang-ast-inline",
                "confidence": ce.confidence(),
                "attrs": {"call_kind": ce.call_kind, "resolution": ce.resolution},
            }
        )
    for te in type_edges:
        if not te.src or not te.dst:
            continue
        edges.append(
            {
                "edge": te.kind,
                "src": te.src,
                "dst": te.dst,
                "provenance": "clang-ast-inline",
                "confidence": te.confidence,
                "attrs": {"role": te.role} if te.role else {},
            }
        )

    # Deterministic edge identity + per-TU dedup (P1 #18): kind+src+dst.
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for e in edges:
        key = (str(e["edge"]), str(e["src"]), str(e["dst"]))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(e)
    return deduped
