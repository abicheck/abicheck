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
    _common_dependency_edge_kinds,
    _dependency_reachability,
    _public_entry_internal_reach,
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


@pytest.mark.parametrize(
    "msvc_flag,should_fire",
    [
        ("/Zp1", True),   # never the MSVC default → one-sided flip is real
        ("/Zp2", True),
        ("/Zp4", True),
        ("/Zp8", False),  # platform default → one-sided flip suppressed
        ("/Zp16", False),
    ],
)
def test_l3_msvc_packing_one_sided_reports_only_non_default_widths(msvc_flag, should_fire) -> None:
    kinds = _kinds(diff_build_evidence(_ev([]), _ev([msvc_flag])))
    assert (ChangeKind.STRUCT_PACKING_MODE_CHANGED.value in kinds) is should_fire


def test_l3_enum_size_explicit_default_is_noop() -> None:
    # -fno-short-enums == the compiler default (int), so omitted->explicit-default
    # is not a change.
    changes = diff_build_evidence(_ev([]), _ev(["-fno-short-enums"]))
    assert ChangeKind.ENUM_SIZE_FLAG_CHANGED.value not in _kinds(changes)


def test_l3_identical_flags_emit_nothing() -> None:
    assert diff_build_evidence(_ev(["-fshort-enums"]), _ev(["-fshort-enums"])) == []


def _evf(argv: list[str]) -> BuildEvidence:
    from abicheck.buildsource.adapters.base import extract_abi_relevant_flags
    cu = CompileUnit(id="t", source="a.cpp", language="CXX",
                     abi_relevant_flags=extract_abi_relevant_flags(argv))
    return BuildEvidence(build_options=derive_build_options([cu]))


@pytest.mark.parametrize(
    "old_flags,new_flags,expected",
    [
        # Known-default flips fire one-sided; float-abi (target-dependent) needs both.
        ([], ["-fwhole-program-vtables"], ChangeKind.WHOLE_PROGRAM_VTABLES_MODE_CHANGED),
        ([], ["-fsanitize=address"], ChangeKind.SANITIZER_MODE_CHANGED),
        (["-fsanitize=address"], ["-fsanitize=address,undefined"], ChangeKind.SANITIZER_MODE_CHANGED),
        (["-mfloat-abi=soft"], ["-mfloat-abi=hard"], ChangeKind.FLOAT_ABI_CHANGED),
        ([], ["-D_GLIBCXX_DEBUG"], ChangeKind.STDLIB_DEBUG_MODE_CHANGED),
        (["-D_ITERATOR_DEBUG_LEVEL=0"], ["-D_ITERATOR_DEBUG_LEVEL=2"], ChangeKind.STDLIB_DEBUG_MODE_CHANGED),
    ],
)
def test_l3_extra_flag_flip_emits_kind(old_flags, new_flags, expected) -> None:
    changes = diff_build_evidence(_evf(old_flags), _evf(new_flags))
    assert expected.value in _kinds(changes)
    assert expected in RISK_KINDS


@pytest.mark.parametrize("argv", [["-fno-sanitize=address"], ["-fsanitize=address", "-fno-sanitize=address"]])
def test_l3_sanitizer_disabling_flag_is_the_default(argv) -> None:
    # -fno-sanitize= on its own just spells the default (no sanitizer), and it
    # cancels an earlier -fsanitize= for the same set — neither must report.
    assert diff_build_evidence(_evf([]), _evf(argv)) == []


def test_l3_float_abi_needs_both_sides_explicit() -> None:
    # Target-dependent default → a one-sided -mfloat-abi must not read as a flip.
    assert ChangeKind.FLOAT_ABI_CHANGED.value not in _kinds(
        diff_build_evidence(_evf([]), _evf(["-mfloat-abi=hard"]))
    )


@pytest.mark.parametrize("tuning_flag", ["-flto-jobs=8", "-flto-partition=one"])
def test_l3_lto_tuning_flags_are_not_abi_relevant(tuning_flag) -> None:
    # A standalone LTO backend-tuning flag (no -flto) does not enable LTO and is
    # not ABI-relevant, so it must emit nothing — neither lto_mode_changed nor
    # the generic abi_relevant_build_flag_changed.
    from abicheck.buildsource.adapters.base import extract_abi_relevant_flags
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    def evf(argv):
        cu = CompileUnit(
            id="t", source="a.cpp", language="CXX",
            abi_relevant_flags=extract_abi_relevant_flags(argv),
        )
        return BuildEvidence(build_options=derive_build_options([cu]))

    assert diff_build_evidence(evf([]), evf([tuning_flag])) == []


# ---------------------------------------------------------------------------
# L4 — source-replay removals / constexpr body (source_diff)
# ---------------------------------------------------------------------------
def _surf(**kw) -> SourceAbiSurface:
    return SourceAbiSurface(**kw)


def _ent(kind: str, name: str, **kw) -> SourceEntity:
    return SourceEntity(id=name, kind=kind, qualified_name=name, **kw)


#: A persisting public *source* declaration so a removal is unambiguous — an
#: empty (or only relinked-export) new surface reads as failed L4 extraction.
def _keeper() -> SourceEntity:
    return SourceEntity(id="keep", kind="function", qualified_name="keep",
                        mangled_name="_Z4keepv", visibility="public_header")


def test_l4_public_macro_removed() -> None:
    old = _surf(reachable_macros=[_ent("macro", "FOO_MAX", value="64")],
                reachable_declarations=[_keeper()])
    new = _surf(reachable_declarations=[_keeper()])
    changes = diff_source_abi(old, new)
    assert ChangeKind.PUBLIC_MACRO_REMOVED.value in _kinds(changes)
    assert ChangeKind.PUBLIC_MACRO_REMOVED in API_BREAK_KINDS


def test_l4_inline_function_removed() -> None:
    old = _surf(reachable_inline_bodies=[_ent("inline", "clamp", body_hash="h1")],
                reachable_declarations=[_keeper()])
    new = _surf(reachable_declarations=[_keeper()])
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
    old = _surf(
        reachable_types=[_ent("typedef", "handle_t", type_hash="t1", value="int")],
        reachable_declarations=[_keeper()],
    )
    new = _surf(reachable_declarations=[_keeper()])
    changes = diff_source_abi(old, new)
    assert ChangeKind.PUBLIC_TYPEDEF_REMOVED.value in _kinds(changes)
    assert ChangeKind.PUBLIC_TYPEDEF_REMOVED in API_BREAK_KINDS


def test_l4_removals_suppressed_when_new_surface_empty() -> None:
    # An empty new surface means L4 extraction did not run (missing extractor),
    # not that every macro/typedef/inline was removed — no findings should fire.
    old = _surf(
        reachable_macros=[_ent("macro", "FOO", value="1")],
        reachable_types=[_ent("typedef", "t", type_hash="x", value="int")],
        reachable_inline_bodies=[_ent("inline", "f", body_hash="h")],
    )
    assert diff_source_abi(old, _surf()) == []


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


def test_l5_internal_dep_skipped_without_baseline_public_closure() -> None:
    # Baseline has call edges but no SOURCE_DECLARES public closure (evidence-poor
    # older graph): its internal-reach set is empty for lack of a closure, so the
    # new graph's pre-existing internal calls must NOT look newly added.
    nodes = [_N("pub", "source_decl"), _N("intn", "source_decl"), _N("sym", "binary_symbol")]
    old = SourceGraphSummary(nodes=nodes, edges=[
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("pub", "pub", "DECL_CALLS_DECL"),  # call edges present, but no SOURCE_DECLARES
    ])
    new = SourceGraphSummary(nodes=nodes + [_N("hdr", "header")], edges=[
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub", "pub", "DECL_CALLS_DECL"),
        _E("pub", "intn", "DECL_CALLS_DECL"),
    ])
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_public_type_gains_private_field_type() -> None:
    # ADR-041 P0: a public struct with a new private field type. No call graph
    # sees this at all — it is exactly the "not a call" case the ADR opens with.
    # A self-referential TYPE_HAS_FIELD_TYPE edge establishes that both graphs
    # already ran the semantic pass, so the coverage gate does not skip.
    nodes = [
        _N("pub_hdr", "header", "api.h"),
        _N("pub_type", "record_type", "Public"),
        _N("priv_type", "record_type", "detail::PrivateType"),
    ]
    base = [
        _E("pub_hdr", "pub_type", "SOURCE_DECLARES"),
        _E("pub_type", "pub_type", "TYPE_HAS_FIELD_TYPE"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub_type", "priv_type", "TYPE_HAS_FIELD_TYPE")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_public_type_gains_private_base_class() -> None:
    nodes = [
        _N("pub_hdr", "header", "api.h"),
        _N("pub_type", "record_type", "Public"),
        _N("priv_type", "record_type", "detail::Base"),
    ]
    base = [
        _E("pub_hdr", "pub_type", "SOURCE_DECLARES"),
        _E("pub_type", "pub_type", "TYPE_INHERITS"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub_type", "priv_type", "TYPE_INHERITS")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_public_fn_gains_private_parameter_type() -> None:
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("priv_type", "record_type", "detail::PrivateType"),
    ]
    base = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub", "pub", "DECL_HAS_TYPE"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub", "priv_type", "DECL_HAS_TYPE")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_public_fn_gains_private_constant_reference() -> None:
    # inline int f() { return detail::k; } — the ADR's own motivating example.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "f()"),
        _N("sym", "binary_symbol", "f"),
        _N("priv_const", "source_decl", "detail::k"),
    ]
    base = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub", "pub", "DECL_REFERENCES_DECL"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub", "priv_const", "DECL_REFERENCES_DECL")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_internal_type_dep_skipped_without_baseline_coverage() -> None:
    # Only the NEW graph carries a SOURCE_DECLARES public closure for the type;
    # the baseline has no public-type closure at all, so the pre-existing
    # TYPE_HAS_FIELD_TYPE edge must not look newly added.
    nodes = [
        _N("pub_hdr", "header", "api.h"),
        _N("pub_type", "record_type", "Public"),
        _N("priv_type", "record_type", "detail::PrivateType"),
    ]
    old = SourceGraphSummary(
        nodes=nodes, edges=[_E("pub_type", "priv_type", "TYPE_HAS_FIELD_TYPE")]
    )  # no SOURCE_DECLARES at all on the baseline
    new = SourceGraphSummary(nodes=nodes, edges=[
        _E("pub_hdr", "pub_type", "SOURCE_DECLARES"),
        _E("pub_type", "priv_type", "TYPE_HAS_FIELD_TYPE"),
    ])
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_skipped_on_collector_coverage_improvement() -> None:
    # Codex review: the baseline only ever ran the call graph (DECL_CALLS_DECL);
    # the new side additionally ran the ADR-041 type-graph pass for the first
    # time, so it carries TYPE_HAS_FIELD_TYPE edges the baseline could never
    # have collected. That must read as a coverage improvement, not a new
    # dependency — flagging it would fire on every pack collected before the
    # type-graph pass existed, purely from re-scanning unchanged source.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("priv_type", "record_type", "detail::PrivateType"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=[
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub", "pub", "DECL_CALLS_DECL"),  # only the call-graph pass ran
    ])
    new = SourceGraphSummary(nodes=nodes, edges=[
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub", "pub", "DECL_CALLS_DECL"),
        _E("pub", "priv_type", "DECL_HAS_TYPE"),  # type-graph pass, new on this side
    ])
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_flags_new_kind_within_already_covered_family() -> None:
    # Second Codex review: the type-graph pass already ran on both sides (an
    # unrelated DECL_HAS_TYPE edge exists on both), so a *first-ever*
    # TYPE_HAS_FIELD_TYPE edge on the new side is a real new dependency, not a
    # collector-coverage gap — it must not be dropped just because that exact
    # edge kind happens to be new. Coverage is judged per extractor-pass family
    # (type_graph.py emits all four type/reference kinds from one pass), not
    # per exact kind.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("pub_other", "source_decl", "other()"),
        _N("known_type", "record_type", "Known"),
        _N("priv_type", "record_type", "detail::PrivateType"),
    ]
    base = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        # Unrelated to "pub": establishes the type-graph pass ran on both sides.
        _E("pub_other", "known_type", "DECL_HAS_TYPE"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_common_dependency_edge_kinds_family_level() -> None:
    # Direct unit test of the family-grouping helper: an old graph with only a
    # DECL_HAS_TYPE edge and a new graph adding TYPE_HAS_FIELD_TYPE both belong
    # to the type-graph family, so the whole family (all four kinds) is common
    # even though DECL_CALLS_DECL (a different family, absent on both) is not.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "DECL_HAS_TYPE")],
    )
    new = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type"), _N("c", "record_type")],
        edges=[_E("a", "b", "DECL_HAS_TYPE"), _E("b", "c", "TYPE_HAS_FIELD_TYPE")],
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset({
        "DECL_REFERENCES_DECL", "DECL_HAS_TYPE", "TYPE_HAS_FIELD_TYPE", "TYPE_INHERITS",
    })


def test_dependency_reachability_empty_edge_kinds_returns_empty() -> None:
    # Direct unit test of the defensive early-return: an empty edge_kinds set
    # (e.g. _common_dependency_edge_kinds finding no overlap) must short-circuit
    # rather than walk a graph that does carry dependency edges of other kinds.
    g = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "source_decl")],
        edges=[_E("a", "b", "DECL_CALLS_DECL")],
    )
    assert _dependency_reachability(g, frozenset()) == {}


def test_public_entry_internal_reach_no_reach_returns_empty() -> None:
    # Direct unit test: empty edge_kinds means _dependency_reachability returns
    # {}, so _public_entry_internal_reach must short-circuit before ever
    # touching the public-closure computation.
    nodes = [_N("pub", "source_decl"), _N("sym", "binary_symbol"), _N("intn", "source_decl")]
    g = SourceGraphSummary(nodes=nodes, edges=[
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("pub", "intn", "DECL_CALLS_DECL"),
    ])
    assert _public_entry_internal_reach(g, frozenset()) == set()


def test_public_entry_internal_reach_no_public_closure_returns_empty() -> None:
    # Direct unit test: reach is non-empty but the graph has no public closure
    # at all (no SOURCE_DECLARES edges), so there is nothing to subtract over.
    nodes = [
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("intn", "source_decl", "intn()"),
    ]
    g = SourceGraphSummary(nodes=nodes, edges=[
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("pub", "intn", "DECL_CALLS_DECL"),
    ])
    assert _public_entry_internal_reach(g, frozenset({"DECL_CALLS_DECL"})) == set()


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
