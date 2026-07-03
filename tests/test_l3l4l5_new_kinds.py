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

"""Detector tests for the L3/L4/L5 build/source-only ChangeKinds.

These kinds are discoverable *only* from build context (L3), source-replay
surfaces (L4), or the derived source graph (L5) — no artifact layer sees them.
Each test drives the relevant diff over hand-built evidence models (no compiler
/ castxml) and asserts the exact new ChangeKind plus its partition, so the fast
lane covers them end-to-end.
"""
from __future__ import annotations

import pytest

from abicheck.buildsource.adapters.base import derive_build_options
from abicheck.buildsource.build_diff import diff_build_evidence
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.source_abi import SourceAbiSurface, SourceEntity
from abicheck.buildsource.source_diff import diff_source_abi
from abicheck.buildsource.source_graph import (
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    diff_source_graph_findings,
)
from abicheck.checker_policy import API_BREAK_KINDS, RISK_KINDS, ChangeKind


# ---------------------------------------------------------------------------
# L3 — build-context flag flips (build_diff)
# ---------------------------------------------------------------------------
def _ev(flags: list[str], lang: str = "CXX") -> BuildEvidence:
    cu = CompileUnit(id="tu", source="a.cpp", language=lang, abi_relevant_flags=flags)
    return BuildEvidence(build_options=derive_build_options([cu]))


def _kinds(changes) -> list[str]:
    return [c.kind.value for c in changes]


@pytest.mark.parametrize(
    "old_flags,new_flags,expected",
    [
        ([], ["-fshort-enums"], ChangeKind.ENUM_SIZE_FLAG_CHANGED),
        (["-fshort-enums"], [], ChangeKind.ENUM_SIZE_FLAG_CHANGED),
        # GNU packing default is known (natural), so a one-sided flip fires.
        ([], ["-fpack-struct=1"], ChangeKind.STRUCT_PACKING_MODE_CHANGED),
        (["-fpack-struct=8"], ["-fpack-struct=1"], ChangeKind.STRUCT_PACKING_MODE_CHANGED),
        # MSVC packing default is target-dependent, so it needs both sides.
        (["/Zp8"], ["/Zp1"], ChangeKind.STRUCT_PACKING_MODE_CHANGED),
        ([], ["-flto"], ChangeKind.LTO_MODE_CHANGED),
        (["-flto=thin"], [], ChangeKind.LTO_MODE_CHANGED),
        (["-fsigned-char"], ["-funsigned-char"], ChangeKind.CHAR_SIGNEDNESS_CHANGED),
    ],
)
def test_l3_flag_flip_emits_kind(old_flags, new_flags, expected) -> None:
    changes = diff_build_evidence(_ev(old_flags), _ev(new_flags))
    assert expected.value in _kinds(changes)
    assert expected in RISK_KINDS


@pytest.mark.parametrize(
    "kind,one_sided_flag",
    [
        # Target-dependent defaults: an omitted side is unknown, so a one-sided
        # flag must NOT read as a flip (avoids MSVC-default / ARM-default FPs).
        (ChangeKind.CHAR_SIGNEDNESS_CHANGED, "-funsigned-char"),
        (ChangeKind.STRUCT_PACKING_MODE_CHANGED, "/Zp8"),  # MSVC only; GNU one-sided does fire
    ],
)
def test_l3_target_dependent_flags_need_both_sides_explicit(kind, one_sided_flag) -> None:
    changes = diff_build_evidence(_ev([]), _ev([one_sided_flag]))
    assert kind.value not in _kinds(changes)


def test_l3_enum_size_explicit_default_is_noop() -> None:
    # -fno-short-enums == the compiler default (int), so omitted->explicit-default
    # is not a change.
    changes = diff_build_evidence(_ev([]), _ev(["-fno-short-enums"]))
    assert ChangeKind.ENUM_SIZE_FLAG_CHANGED.value not in _kinds(changes)


def test_l3_identical_flags_emit_nothing() -> None:
    assert diff_build_evidence(_ev(["-fshort-enums"]), _ev(["-fshort-enums"])) == []


# ---------------------------------------------------------------------------
# L4 — source-replay removals / constexpr body (source_diff)
# ---------------------------------------------------------------------------
def _surf(**kw) -> SourceAbiSurface:
    return SourceAbiSurface(**kw)


def _ent(kind: str, name: str, **kw) -> SourceEntity:
    return SourceEntity(id=name, kind=kind, qualified_name=name, **kw)


def test_l4_public_macro_removed() -> None:
    old = _surf(reachable_macros=[_ent("macro", "FOO_MAX", value="64")])
    new = _surf()
    changes = diff_source_abi(old, new)
    assert ChangeKind.PUBLIC_MACRO_REMOVED.value in _kinds(changes)
    assert ChangeKind.PUBLIC_MACRO_REMOVED in API_BREAK_KINDS


def test_l4_inline_function_removed() -> None:
    old = _surf(reachable_inline_bodies=[_ent("inline", "clamp", body_hash="h1")])
    new = _surf()
    changes = diff_source_abi(old, new)
    assert ChangeKind.INLINE_FUNCTION_REMOVED.value in _kinds(changes)
    assert ChangeKind.INLINE_FUNCTION_REMOVED in API_BREAK_KINDS


def test_l4_inline_to_out_of_line_is_not_a_removal() -> None:
    # A header inline turned into an out-of-line exported function leaves the
    # inline bucket but stays a callable declaration — not a source break.
    old = _surf(reachable_inline_bodies=[_ent("inline", "demo::f", body_hash="h1")])
    new = _surf(reachable_declarations=[
        SourceEntity(id="demo::f", kind="function", qualified_name="demo::f")
    ])
    assert ChangeKind.INLINE_FUNCTION_REMOVED.value not in _kinds(diff_source_abi(old, new))


def test_l4_public_typedef_removed() -> None:
    old = _surf(reachable_types=[_ent("typedef", "handle_t", type_hash="t1", value="int")])
    new = _surf()
    changes = diff_source_abi(old, new)
    assert ChangeKind.PUBLIC_TYPEDEF_REMOVED.value in _kinds(changes)
    assert ChangeKind.PUBLIC_TYPEDEF_REMOVED in API_BREAK_KINDS


def test_l4_unchanged_surface_emits_nothing() -> None:
    surf = _surf(
        reachable_macros=[_ent("macro", "FOO", value="1")],
        reachable_inline_bodies=[_ent("inline", "f", body_hash="h")],
        reachable_types=[_ent("typedef", "t", type_hash="x", value="int")],
    )
    assert diff_source_abi(surf, surf) == []


# ---------------------------------------------------------------------------
# L5 — source-graph deltas (source_graph)
# ---------------------------------------------------------------------------
def _N(nid: str, kind: str, label: str = "") -> GraphNode:
    return GraphNode(id=nid, kind=kind, label=label or nid)


def _E(src: str, dst: str, kind: str) -> GraphEdge:
    return GraphEdge(src=src, dst=dst, kind=kind)


def _graph_kinds(old, new) -> list[str]:
    return [c.kind.value for c in diff_source_graph_findings(old, new)]


def test_l5_public_api_internal_dependency_added() -> None:
    nodes = [
        _N("pub", "source_decl", "pub()"),
        _N("intn", "source_decl", "intn()"),
        _N("sym", "binary_symbol", "pub"),
        _N("hdr", "header", "api.h"),
    ]
    # Public entry maps to a symbol and is declared by a public header; it already
    # calls itself (so the call graph is non-empty on both sides).
    base = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub", "pub", "DECL_CALLS_DECL"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(nodes=nodes, edges=base + [_E("pub", "intn", "DECL_CALLS_DECL")])
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED in RISK_KINDS


def test_l5_internal_dep_skipped_without_baseline_call_coverage() -> None:
    # If only the NEW graph ran the call-graph pass, the baseline has no call
    # edges — every internal callee would look newly-added. The check must skip.
    nodes = [
        _N("pub", "source_decl", "pub()"),
        _N("intn", "source_decl", "intn()"),
        _N("sym", "binary_symbol", "pub"),
        _N("hdr", "header", "api.h"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=[
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
    ])  # no DECL_CALLS_DECL edges at all
    new = SourceGraphSummary(nodes=nodes, edges=[
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub", "intn", "DECL_CALLS_DECL"),
    ])
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_owner_changed_reads_header_declaring_nodes() -> None:
    # Production graphs attach SOURCE_DECLARES from a `header`-kind node
    # (build_source_graph.header_declares), so the owner map must read those.
    nodes = [
        _N("d", "source_decl", "d()"),
        _N("s", "binary_symbol", "d"),
        _N("hdr:a.h", "header", "a.h"),
        _N("hdr:b.h", "header", "b.h"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=[
        _E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL"), _E("hdr:a.h", "d", "SOURCE_DECLARES"),
    ])
    new = SourceGraphSummary(nodes=nodes, edges=[
        _E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL"), _E("hdr:b.h", "d", "SOURCE_DECLARES"),
    ])
    assert ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED.value in _graph_kinds(old, new)


def test_l5_target_dependency_added() -> None:
    nodes = [_N("t:libA", "target", "libA"), _N("t:libB", "target", "libB")]
    old = SourceGraphSummary(nodes=nodes, edges=[])
    new = SourceGraphSummary(nodes=nodes, edges=[_E("t:libA", "t:libB", "TARGET_DEPENDS_ON")])
    kinds = _graph_kinds(old, new)
    assert ChangeKind.TARGET_DEPENDENCY_ADDED.value in kinds
    assert ChangeKind.TARGET_DEPENDENCY_ADDED in RISK_KINDS


def test_l5_exported_symbol_source_owner_changed() -> None:
    nodes = [
        _N("d", "source_decl", "d()"),
        _N("s", "binary_symbol", "d"),
        _N("src:a", "source", "a.cpp"),
        _N("src:b", "source", "b.cpp"),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[_E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL"), _E("src:a", "d", "SOURCE_DECLARES")],
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[_E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL"), _E("src:b", "d", "SOURCE_DECLARES")],
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED.value in kinds
    assert ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED in RISK_KINDS


def test_l5_identical_graph_emits_nothing() -> None:
    nodes = [_N("t:libA", "target", "libA"), _N("t:libB", "target", "libB")]
    g = SourceGraphSummary(nodes=nodes, edges=[_E("t:libA", "t:libB", "TARGET_DEPENDS_ON")])
    assert diff_source_graph_findings(g, g) == []
