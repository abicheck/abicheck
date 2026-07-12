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
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.buildsource.source_graph_findings import (
    _common_dependency_edge_kinds,
    _dependency_kinds_covered,
    _dependency_path,
    _dependency_reachability,
    _format_dependency_path,
    _public_entry_internal_reach,
    _public_types,
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
        (
            ["-fpack-struct=8"],
            ["-fpack-struct=1"],
            ChangeKind.STRUCT_PACKING_MODE_CHANGED,
        ),
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
        (
            ChangeKind.STRUCT_PACKING_MODE_CHANGED,
            "/Zp8",
        ),  # MSVC only; GNU one-sided does fire
    ],
)
def test_l3_target_dependent_flags_need_both_sides_explicit(
    kind, one_sided_flag
) -> None:
    changes = diff_build_evidence(_ev([]), _ev([one_sided_flag]))
    assert kind.value not in _kinds(changes)


@pytest.mark.parametrize(
    "msvc_flag,should_fire",
    [
        ("/Zp1", True),  # never the MSVC default → one-sided flip is real
        ("/Zp2", True),
        ("/Zp4", True),
        ("/Zp8", False),  # platform default → one-sided flip suppressed
        ("/Zp16", False),
    ],
)
def test_l3_msvc_packing_one_sided_reports_only_non_default_widths(
    msvc_flag, should_fire
) -> None:
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

    cu = CompileUnit(
        id="t",
        source="a.cpp",
        language="CXX",
        abi_relevant_flags=extract_abi_relevant_flags(argv),
    )
    return BuildEvidence(build_options=derive_build_options([cu]))


@pytest.mark.parametrize(
    "old_flags,new_flags,expected",
    [
        # Known-default flips fire one-sided; float-abi (target-dependent) needs both.
        (
            [],
            ["-fwhole-program-vtables"],
            ChangeKind.WHOLE_PROGRAM_VTABLES_MODE_CHANGED,
        ),
        ([], ["-fsanitize=address"], ChangeKind.SANITIZER_MODE_CHANGED),
        (
            ["-fsanitize=address"],
            ["-fsanitize=address,undefined"],
            ChangeKind.SANITIZER_MODE_CHANGED,
        ),
        (["-mfloat-abi=soft"], ["-mfloat-abi=hard"], ChangeKind.FLOAT_ABI_CHANGED),
        ([], ["-D_GLIBCXX_DEBUG"], ChangeKind.STDLIB_DEBUG_MODE_CHANGED),
        (
            ["-D_ITERATOR_DEBUG_LEVEL=0"],
            ["-D_ITERATOR_DEBUG_LEVEL=2"],
            ChangeKind.STDLIB_DEBUG_MODE_CHANGED,
        ),
    ],
)
def test_l3_extra_flag_flip_emits_kind(old_flags, new_flags, expected) -> None:
    changes = diff_build_evidence(_evf(old_flags), _evf(new_flags))
    assert expected.value in _kinds(changes)
    assert expected in RISK_KINDS


@pytest.mark.parametrize(
    "argv", [["-fno-sanitize=address"], ["-fsanitize=address", "-fno-sanitize=address"]]
)
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
            id="t",
            source="a.cpp",
            language="CXX",
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
    return SourceEntity(
        id="keep",
        kind="function",
        qualified_name="keep",
        mangled_name="_Z4keepv",
        visibility="public_header",
    )


def test_l4_public_macro_removed() -> None:
    old = _surf(
        reachable_macros=[_ent("macro", "FOO_MAX", value="64")],
        reachable_declarations=[_keeper()],
    )
    new = _surf(reachable_declarations=[_keeper()])
    changes = diff_source_abi(old, new)
    assert ChangeKind.PUBLIC_MACRO_REMOVED.value in _kinds(changes)
    assert ChangeKind.PUBLIC_MACRO_REMOVED in API_BREAK_KINDS


def test_l4_inline_function_removed() -> None:
    old = _surf(
        reachable_inline_bodies=[_ent("inline", "clamp", body_hash="h1")],
        reachable_declarations=[_keeper()],
    )
    new = _surf(reachable_declarations=[_keeper()])
    changes = diff_source_abi(old, new)
    assert ChangeKind.INLINE_FUNCTION_REMOVED.value in _kinds(changes)
    assert ChangeKind.INLINE_FUNCTION_REMOVED in API_BREAK_KINDS


def test_l4_removed_export_backed_body_is_not_an_inline_removal() -> None:
    # case83 regression: a source extractor may register an "inline" entity
    # for any function with a body in its defining TU, not only a genuinely
    # inline-qualified one with no linkage of its own. An ordinary exported
    # free function's removal is already the artifact diff's job
    # (func_removed / cpu_dispatch_isa_dropped); reporting it again here
    # would be redundant and factually wrong ("no exported binary symbol").
    old = _surf(
        reachable_inline_bodies=[
            _ent(
                "inline",
                "kmeans_compute_avx512",
                mangled_name="_Z21kmeans_compute_avx512i",
                body_hash="h1",
            )
        ],
        reachable_declarations=[_keeper()],
        roots={"exported_symbols": ["_Z21kmeans_compute_avx512i"]},
    )
    new = _surf(reachable_declarations=[_keeper()])
    assert ChangeKind.INLINE_FUNCTION_REMOVED.value not in _kinds(
        diff_source_abi(old, new)
    )


def test_l4_inline_to_out_of_line_is_not_a_removal() -> None:
    # A header inline turned into an out-of-line exported function leaves the
    # inline bucket but stays a callable declaration — not a source break.
    old = _surf(reachable_inline_bodies=[_ent("inline", "demo::f", body_hash="h1")])
    new = _surf(
        reachable_declarations=[
            SourceEntity(id="demo::f", kind="function", qualified_name="demo::f")
        ]
    )
    assert ChangeKind.INLINE_FUNCTION_REMOVED.value not in _kinds(
        diff_source_abi(old, new)
    )


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
def _N(nid: str, kind: str, label: str = "", **attrs: object) -> GraphNode:
    return GraphNode(id=nid, kind=kind, label=label or nid, attrs=dict(attrs))


def _E(src: str, dst: str, kind: str) -> GraphEdge:
    return GraphEdge(src=src, dst=dst, kind=kind)


def _graph_kinds(old, new) -> list[str]:
    return [c.kind.value for c in diff_source_graph_findings(old, new)]


def test_l5_public_api_internal_dependency_added() -> None:
    nodes = [
        _N("pub", "source_decl", "pub()"),
        _N("intn", "source_decl", "intn()", visibility="private_header"),
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
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub", "intn", "DECL_CALLS_DECL")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED in RISK_KINDS


def test_l5_internal_dep_skipped_without_baseline_call_coverage() -> None:
    # If only the NEW graph ran the call-graph pass, the baseline has no call
    # edges — every internal callee would look newly-added. The check must skip.
    nodes = [
        _N("pub", "source_decl", "pub()"),
        _N("intn", "source_decl", "intn()", visibility="private_header"),
        _N("sym", "binary_symbol", "pub"),
        _N("hdr", "header", "api.h"),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
        ],
    )  # no DECL_CALLS_DECL edges at all
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "intn", "DECL_CALLS_DECL"),
        ],
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_skipped_without_baseline_public_closure() -> None:
    # Baseline has call edges but no SOURCE_DECLARES public closure (evidence-poor
    # older graph): its internal-reach set is empty for lack of a closure, so the
    # new graph's pre-existing internal calls must NOT look newly added.
    nodes = [
        _N("pub", "source_decl"),
        _N("intn", "source_decl", visibility="private_header"),
        _N("sym", "binary_symbol"),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E(
                "pub", "pub", "DECL_CALLS_DECL"
            ),  # call edges present, but no SOURCE_DECLARES
        ],
    )
    new = SourceGraphSummary(
        nodes=nodes + [_N("hdr", "header")],
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "pub", "DECL_CALLS_DECL"),
            _E("pub", "intn", "DECL_CALLS_DECL"),
        ],
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_public_type_gains_private_field_type() -> None:
    # ADR-041 P0: a public struct with a new private field type. No call graph
    # sees this at all — it is exactly the "not a call" case the ADR opens with.
    # A self-referential TYPE_HAS_FIELD_TYPE edge establishes that both graphs
    # already ran the semantic pass, so the coverage gate does not skip.
    nodes = [
        _N("pub_hdr", "header", "api.h"),
        _N("pub_type", "record_type", "Public", visibility="public_header"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
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


def test_l5_public_inline_fn_with_no_exported_symbol_gains_private_dependency() -> None:
    # Tenth Codex review: the ADR's own headline example
    # (`inline int f() { return detail::SECRET; }`) commonly has no exported
    # binary symbol at all — an inline/template/constexpr function is inlined
    # at every call site rather than separately emitted. A public entry must
    # be seeded from public-header *visibility* alone (matching
    # crosscheck.py's is_public_dependency_node), not only from
    # SOURCE_DECL_MAPS_TO_SYMBOL, or this exact scenario is never flagged.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "f()", visibility="public_header"),
        _N("priv_const", "source_decl", "detail::SECRET", visibility="private_header"),
    ]
    base = [
        _E("hdr", "pub", "SOURCE_DECLARES"),
        # No SOURCE_DECL_MAPS_TO_SYMBOL edge at all — "pub" is never exported.
        _E("pub", "pub", "DECL_REFERENCES_DECL"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub", "priv_const", "DECL_REFERENCES_DECL")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_public_type_gains_thirdparty_field_type_not_flagged() -> None:
    # Fourth Codex review: "not declared by a public header" alone is not
    # internal. A third-party/stdlib type used as a new field type carries no
    # visibility and no project provenance (augment_graph_with_types only
    # marks defined_in_project when the type's dst_file is one of the
    # project's own files) — it must not be conflated with a genuinely
    # private project entity just because it also isn't public.
    nodes = [
        _N("pub_hdr", "header", "api.h"),
        _N("pub_type", "record_type", "Public", visibility="public_header"),
        _N(
            "ext_type", "record_type", "std::vector<int>"
        ),  # no visibility/provenance at all
    ]
    base = [
        _E("pub_hdr", "pub_type", "SOURCE_DECLARES"),
        _E("pub_type", "pub_type", "TYPE_HAS_FIELD_TYPE"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub_type", "ext_type", "TYPE_HAS_FIELD_TYPE")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_public_type_gains_unannotated_project_field_type_flagged() -> None:
    # The positive counterpart: an unannotated type (no SOURCE_DECLARES from a
    # header, so no `visibility` attr) but marked `defined_in_project` by the
    # type-graph extractor (its dst_file is a project source/private header)
    # IS internal — project-source-location provenance is exactly the signal
    # crosscheck.py's `_is_internal_decl` already accepts for this case.
    nodes = [
        _N("pub_hdr", "header", "api.h"),
        _N("pub_type", "record_type", "Public", visibility="public_header"),
        _N("impl_type", "record_type", "detail::Impl", defined_in_project=True),
    ]
    base = [
        _E("pub_hdr", "pub_type", "SOURCE_DECLARES"),
        _E("pub_type", "pub_type", "TYPE_HAS_FIELD_TYPE"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes, edges=base + [_E("pub_type", "impl_type", "TYPE_HAS_FIELD_TYPE")]
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_private_type_gaining_own_dependency_not_flagged() -> None:
    # Sixth Codex review: a private-header type must not be treated as a
    # dependency-closure *entry*. _augment_with_source_abi's header_declares
    # creates a `header`-kind node for EVERY declaring file, public or
    # private — privacy lives on the type's own `visibility` attr, not the
    # node kind. Without checking that attr, a private type gaining its own
    # new private field/base would wrongly emit
    # PUBLIC_API_INTERNAL_DEPENDENCY_ADDED even though no public API is
    # involved at all.
    nodes = [
        _N("priv_hdr", "header", "detail/impl.h"),
        _N("priv_type", "record_type", "detail::Impl", visibility="private_header"),
        _N(
            "priv_field_type",
            "record_type",
            "detail::Helper",
            visibility="private_header",
        ),
    ]
    base = [
        _E("priv_hdr", "priv_type", "SOURCE_DECLARES"),
        _E("priv_type", "priv_type", "TYPE_HAS_FIELD_TYPE"),
    ]
    old = SourceGraphSummary(nodes=nodes, edges=base)
    new = SourceGraphSummary(
        nodes=nodes,
        edges=base + [_E("priv_type", "priv_field_type", "TYPE_HAS_FIELD_TYPE")],
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_public_types_requires_own_visibility_not_just_declaring_header() -> None:
    # Direct unit test of the helper itself: a type declared by a `header`-kind
    # node with no (or non-public) visibility on the type's own attrs is not
    # public, even though the declaring file node kind is indistinguishable
    # from a public header's.
    nodes = [
        _N("hdr", "header", "some/path.h"),
        _N("pub", "record_type", "Public", visibility="public_header"),
        _N("priv", "record_type", "Private", visibility="private_header"),
        _N("unannotated", "record_type", "Unannotated"),
    ]
    g = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("hdr", "priv", "SOURCE_DECLARES"),
            _E("hdr", "unannotated", "SOURCE_DECLARES"),
        ],
    )
    assert _public_types(g) == {"pub"}


def test_l5_public_type_gains_private_base_class() -> None:
    nodes = [
        _N("pub_hdr", "header", "api.h"),
        _N("pub_type", "record_type", "Public", visibility="public_header"),
        _N("priv_type", "record_type", "detail::Base", visibility="private_header"),
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
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
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
        _N("priv_const", "source_decl", "detail::k", visibility="private_header"),
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
        _N("pub_type", "record_type", "Public", visibility="public_header"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    old = SourceGraphSummary(
        nodes=nodes, edges=[_E("pub_type", "priv_type", "TYPE_HAS_FIELD_TYPE")]
    )  # no SOURCE_DECLARES at all on the baseline
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub_hdr", "pub_type", "SOURCE_DECLARES"),
            _E("pub_type", "priv_type", "TYPE_HAS_FIELD_TYPE"),
        ],
    )
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
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "pub", "DECL_CALLS_DECL"),  # only the call-graph pass ran
        ],
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "pub", "DECL_CALLS_DECL"),
            _E(
                "pub", "priv_type", "DECL_HAS_TYPE"
            ),  # type-graph pass, new on this side
        ],
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_flags_new_kind_within_already_covered_family() -> None:
    # Second Codex review: the type-graph pass already ran on both sides
    # (recorded via extractor_passes, mirroring what inline.py/
    # cli_buildsource_helpers.py always stamp when the pass genuinely runs),
    # so a *first-ever* TYPE_HAS_FIELD_TYPE edge on the new side is a real new
    # dependency, not a collector-coverage gap — it must not be dropped just
    # because that exact edge kind happens to be new. Coverage is judged per
    # extractor-pass family (type_graph.py emits all four type/reference kinds
    # from one pass), not per exact kind — but (fifth Codex review) *only* when
    # the pass is actually confirmed to have run, not merely inferred from an
    # unrelated sibling edge's presence.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    base = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
    ]
    old = SourceGraphSummary(
        nodes=nodes, edges=base, extractor_passes={"type_graph": True}
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=base + [_E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE")],
        extractor_passes={"type_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_common_dependency_edge_kinds_falls_back_to_exact_kind_without_pass_confirmation() -> (
    None
):
    # Fifth Codex review: without a *confirmed* extractor_passes record on
    # both sides, mere sibling-edge presence must not widen coverage to the
    # whole family — a Kythe-ingested pack only ever produces
    # DECL_REFERENCES_DECL (never the other three type-graph kinds), so a
    # single such edge is not evidence that a base-class/field-type check ever
    # ran. Falls back to exact per-kind presence intersection: only
    # DECL_HAS_TYPE (present on both) is common, not TYPE_HAS_FIELD_TYPE
    # (present on new only) or the other untouched kinds.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "DECL_HAS_TYPE")],
    )
    new = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type"), _N("c", "record_type")],
        edges=[_E("a", "b", "DECL_HAS_TYPE"), _E("b", "c", "TYPE_HAS_FIELD_TYPE")],
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset({"DECL_HAS_TYPE"})


def test_common_dependency_edge_kinds_family_widened_with_confirmed_passes() -> None:
    # The positive counterpart: once both sides *confirm* the type-graph pass
    # ran (extractor_passes), the whole family is common regardless of which
    # specific kinds happen to have edges.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "DECL_HAS_TYPE")],
        extractor_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type"), _N("c", "record_type")],
        edges=[_E("a", "b", "DECL_HAS_TYPE"), _E("b", "c", "TYPE_HAS_FIELD_TYPE")],
        extractor_passes={"type_graph": True},
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset(
        {
            "DECL_REFERENCES_DECL",
            "DECL_HAS_TYPE",
            "TYPE_HAS_FIELD_TYPE",
            "TYPE_INHERITS",
        }
    )


def test_common_dependency_edge_kinds_one_sided_pass_covers_exact_kind_only() -> None:
    # Ninth Codex review: a mixed-format comparison must not require *both*
    # sides to confirm the pass. An old pack that ran the type-graph pass and
    # confirmed zero type edges, compared against a pre-slice-2 (or
    # Kythe-only) new pack with no pass marker at all but a first-ever
    # TYPE_HAS_FIELD_TYPE edge, must still treat that exact kind as common —
    # old's confirmed pass makes its own absence of the kind a real,
    # verified zero. But it must NOT widen to sibling kinds (e.g.
    # TYPE_INHERITS) that neither side has an edge of.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl")],
        edges=[],  # confirmed pass, zero type edges
        extractor_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "TYPE_HAS_FIELD_TYPE")],  # no extractor_passes at all
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset({"TYPE_HAS_FIELD_TYPE"})


def test_l5_internal_dep_flagged_with_one_sided_confirmed_pass() -> None:
    # End-to-end version of the above through diff_source_graph_findings.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
        ],
        extractor_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
        ],
    )  # no extractor_passes on the new side
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_internal_dep_skipped_for_kythe_only_baseline_type_edge() -> None:
    # Fifth Codex review, end-to-end: the baseline was collected via
    # `collect --kythe-entries` (a lone DECL_REFERENCES_DECL edge, no
    # extractor_passes — graph_backends.ingest_kythe_entries never sets it).
    # The new pack ran the real Clang type-graph pass and found a first-ever
    # private field type. The lone Kythe ref must not be read as "the
    # type-graph pass ran on the baseline too" — the finding must skip.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("other", "source_decl", "other()"),
        _N("ref_target", "source_decl", "k"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("other", "ref_target", "DECL_REFERENCES_DECL"),  # from Kythe ingestion
        ],
    )  # no extractor_passes: Kythe ingestion never stamps it
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("other", "ref_target", "DECL_REFERENCES_DECL"),
            _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
        ],
        extractor_passes={"type_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_common_dependency_edge_kinds_uses_extractor_passes_over_zero_edges() -> None:
    # Third Codex review: the type-graph pass ran to completion on *both* sides
    # (recorded via extractor_passes) but genuinely found zero type/reference
    # edges on the old side — e.g. no public struct anywhere had a private
    # field yet. Edge presence alone would read that identically to "the pass
    # never ran"; extractor_passes must break the tie so the whole family is
    # still common.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl")],
        edges=[],  # zero type/reference edges, despite the pass having run
        extractor_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "TYPE_HAS_FIELD_TYPE")],
        extractor_passes={"type_graph": True},
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset(
        {
            "DECL_REFERENCES_DECL",
            "DECL_HAS_TYPE",
            "TYPE_HAS_FIELD_TYPE",
            "TYPE_INHERITS",
        }
    )


def test_common_dependency_edge_kinds_narrowed_edge_not_credited_against_confirmed_full_pass() -> (
    None
):
    # Eleventh Codex review: a baseline collected from a *narrowed* (PR/
    # --since-scoped) inline run never sets extractor_passes for that name, but
    # it still serializes whatever edges it happened to collect from the
    # subset it walked. If the old side's TYPE_HAS_FIELD_TYPE edge came from a
    # narrowed pass, and the new side confirms a full, unnarrowed type-graph
    # pass, the old side's edge must not count as evidence that kind was
    # covered — the narrowed pass never examined the rest of the project the
    # full pass now sees, so treating it as comparable coverage would let
    # genuinely-new dependencies elsewhere in the project pass the gate.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "TYPE_HAS_FIELD_TYPE")],
        narrowed_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[],
        extractor_passes={"type_graph": True},
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset()


def test_common_dependency_edge_kinds_matched_narrowed_scope_widens_to_family() -> None:
    # The common, intended PR-diff workflow scopes *both* sides identically
    # (e.g. comparing two narrowed runs over the same changed TUs — the same
    # ``narrowed_scope``). Per the fifteenth Codex review, this is trusted the
    # same way a confirmed full pass on both sides already is: the *whole*
    # family widens, not just the exact kinds each side happens to have an
    # edge of — a matched-scope pass examines every kind in its family
    # together within that shared region, same as a full pass does
    # project-wide.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "DECL_HAS_TYPE")],
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": frozenset({"src/a.cpp"})},
    )
    new = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type"), _N("c", "record_type")],
        edges=[_E("a", "b", "DECL_HAS_TYPE"), _E("b", "c", "TYPE_HAS_FIELD_TYPE")],
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": frozenset({"src/a.cpp"})},
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset(
        {
            "DECL_REFERENCES_DECL",
            "DECL_HAS_TYPE",
            "TYPE_HAS_FIELD_TYPE",
            "TYPE_INHERITS",
        }
    )


def test_common_dependency_edge_kinds_narrowed_same_boolean_different_scope() -> None:
    # Fourteenth Codex review: narrowed_passes alone is just a boolean — "both
    # narrowed" does not mean "narrowed to the same TUs". An old run scoped to
    # src/a.cpp and a new run scoped to a disjoint src/b.cpp are each
    # individually narrow but examine different code; old's edge must not be
    # credited as coverage for a kind new happens to also have an edge of.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "TYPE_HAS_FIELD_TYPE")],
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": frozenset({"src/a.cpp"})},
    )
    new = SourceGraphSummary(
        nodes=[_N("c", "source_decl"), _N("d", "record_type")],
        edges=[_E("c", "d", "TYPE_HAS_FIELD_TYPE")],
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": frozenset({"src/b.cpp"})},
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset()


def test_common_dependency_edge_kinds_narrowed_edge_not_credited_against_unmarked_pack() -> (
    None
):
    # Twelfth Codex review: the eleventh-round fix only excluded a narrowed
    # side's edge when the *other* side confirmed a full pass — but an
    # unmarked pack (no extractor_passes, no narrowed_passes at all, e.g. a
    # pre-slice-2 pack or one built from an externally-ingested backend) is
    # not evidence it was equally narrow either; its true scope is simply
    # unknown. If a narrowed baseline's one TYPE_HAS_FIELD_TYPE edge (from the
    # small subset it examined) is compared against an unmarked candidate's
    # edge of the same exact kind — from a wholly different, unexamined part
    # of the project — the kind must not read as common, or dependencies the
    # narrowed baseline never inspected could pass the coverage gate.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "TYPE_HAS_FIELD_TYPE")],
        narrowed_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[_N("c", "source_decl"), _N("d", "record_type")],
        edges=[_E("c", "d", "TYPE_HAS_FIELD_TYPE")],
        # No extractor_passes, no narrowed_passes: an unmarked/legacy pack.
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset()


def test_l5_internal_dep_not_flagged_for_narrowed_baseline_vs_unmarked_candidate() -> (
    None
):
    # End-to-end version of the twelfth-round fix: the narrowed baseline's
    # pre-existing dependency is unrelated to the new public entry the
    # unmarked candidate reveals a private dependency for — the unmarked
    # side's edge must not be trusted as proof the narrowed baseline's
    # per-kind coverage extends there.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("pub2", "source_decl", "pub2()"),
        _N("sym2", "binary_symbol", "pub2"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
        _N("other_priv", "record_type", "detail::Other", visibility="private_header"),
    ]
    shared_edges = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub2", "sym2", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub2", "SOURCE_DECLARES"),
        _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=list(shared_edges),
        narrowed_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[*shared_edges, _E("pub2", "other_priv", "TYPE_HAS_FIELD_TYPE")],
        # No extractor_passes, no narrowed_passes: unmarked candidate.
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_not_flagged_for_narrowed_baseline_vs_differently_narrowed_candidate() -> (
    None
):
    # End-to-end version of the fourteenth-round fix: both packs are narrowed
    # (narrowed_passes=True on each), but to different, disjoint scopes — the
    # baseline only ever examined pub's TU, the candidate only pub2's. The
    # candidate's real dependency in pub2's TU must not be trusted as proof the
    # baseline's coverage extends there too, just because both are "narrowed".
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("pub2", "source_decl", "pub2()"),
        _N("sym2", "binary_symbol", "pub2"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
        _N("other_priv", "record_type", "detail::Other", visibility="private_header"),
    ]
    shared_edges2 = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub2", "sym2", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub2", "SOURCE_DECLARES"),
        _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=list(shared_edges2),
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": frozenset({"src/pub.cpp"})},
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[*shared_edges2, _E("pub2", "other_priv", "TYPE_HAS_FIELD_TYPE")],
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": frozenset({"src/pub2.cpp"})},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_flagged_for_matched_narrowed_scope_zero_edge_baseline() -> (
    None
):
    # End-to-end version of the fifteenth-round fix: both packs are narrowed
    # to the *identical* scope, and the baseline's narrowed pass genuinely
    # found zero type-graph edges within it — a real, verified zero, since
    # both sides examined the exact same TU. The candidate's first-ever
    # DECL_HAS_TYPE edge in that same shared TU is real, newly-added evidence
    # and must be flagged, not silently dropped for lack of coverage.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    shared_scope = {"type_graph": frozenset({"src/pub.cpp"})}
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
        ],
        narrowed_passes={"type_graph": True},
        narrowed_scope=shared_scope,
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "priv_type", "DECL_HAS_TYPE"),
        ],
        narrowed_passes={"type_graph": True},
        narrowed_scope=shared_scope,
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_dependency_kinds_covered_accepts_narrowed_pass_with_zero_edges() -> None:
    # Fifteenth Codex review: a narrowed pass with zero edges of the family
    # must not read as "no semantic pass at all" for the coarse per-graph
    # coverage gate — narrowed_passes counts the same way extractor_passes
    # already does, since the fine-grained per-kind trust decision (whether
    # this specific zero-edge family is safe to compare) lives entirely in
    # _common_dependency_edge_kinds, not here.
    g = SourceGraphSummary(
        nodes=[_N("a", "source_decl")],
        edges=[],
        narrowed_passes={"type_graph": True},
        narrowed_scope={"type_graph": frozenset({"src/a.cpp"})},
    )
    assert _dependency_kinds_covered(g, frozenset({"DECL_HAS_TYPE"})) is True


def test_common_dependency_edge_kinds_degraded_pass_edge_not_credited_as_coverage() -> (
    None
):
    # Sixteenth Codex review: a pass that ran unnarrowed but hit per-TU
    # diagnostics still folds edges from the TUs that parsed cleanly — those
    # edges must not vouch for "this kind was examined project-wide" any more
    # than a narrowed pass's edges may. A degraded old side's TYPE_HAS_FIELD_TYPE
    # edge (from a surviving TU) must not make that kind common against a
    # confirmed-full-pass new side's edge of the same kind elsewhere.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "TYPE_HAS_FIELD_TYPE")],
        degraded_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[_N("c", "source_decl"), _N("d", "record_type")],
        edges=[_E("c", "d", "TYPE_HAS_FIELD_TYPE")],
        extractor_passes={"type_graph": True},
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset()


def test_l5_internal_dep_not_flagged_for_degraded_baseline_vs_confirmed_full_candidate() -> (
    None
):
    # End-to-end version of the sixteenth-round fix: the baseline's type-graph
    # pass hit a diagnostic on some TU (degraded) but still folded a private
    # field-type edge from a TU that parsed. A second public entry, pub2, was
    # never examined by the baseline's degraded pass (its TU is the one that
    # failed). The candidate ran a confirmed full pass and found pub2 also
    # depends on a private type — that must not be trusted as newly added,
    # since the degraded baseline never had a chance to prove it wasn't there.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("pub2", "source_decl", "pub2()"),
        _N("sym2", "binary_symbol", "pub2"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
        _N("other_priv", "record_type", "detail::Other", visibility="private_header"),
    ]
    shared_edges3 = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub2", "sym2", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub2", "SOURCE_DECLARES"),
        _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=list(shared_edges3),
        degraded_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[*shared_edges3, _E("pub2", "other_priv", "TYPE_HAS_FIELD_TYPE")],
        extractor_passes={"type_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_common_dependency_edge_kinds_narrowed_new_still_credited_against_confirmed_full_old() -> (
    None
):
    # Thirteenth Codex review: the twelfth-round fix over-corrected by gating
    # NEW's own presence on its narrowing too, symmetrically with OLD. But this
    # closure only ever detects *additions* (new vs old's reach) — the
    # false-positive risk lives entirely in whether OLD's absence of a kind is
    # trustworthy, not in NEW's scope. When OLD ran a confirmed full pass and
    # genuinely found zero edges of this kind anywhere, that absence is
    # authoritative regardless of how narrow NEW's own scan was: a real edge
    # NEW observes, even from a changed-scoped run, is still real, newly-added
    # evidence within the region NEW examined. Excluding it created a false
    # negative with no offsetting false-positive protection.
    old = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[],  # confirmed full pass, zero type edges anywhere
        extractor_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "record_type")],
        edges=[_E("a", "b", "TYPE_HAS_FIELD_TYPE")],
        narrowed_passes={"type_graph": True},
    )
    common = _common_dependency_edge_kinds(old, new)
    assert common == frozenset({"TYPE_HAS_FIELD_TYPE"})


def test_l5_internal_dep_flagged_for_narrowed_candidate_against_confirmed_full_baseline() -> (
    None
):
    # End-to-end version of the thirteenth-round fix: a confirmed-full-pass
    # baseline that genuinely has zero dependency edges of this family proves
    # the dependency did not exist anywhere in the old version. A narrowed
    # candidate that observes a first-ever private-field dependency within the
    # TU it examined is real, newly-added evidence and must still be flagged —
    # narrowing the candidate's own scan must not suppress it.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
        ],
        extractor_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
        ],
        narrowed_passes={"type_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_internal_dep_not_flagged_for_dependency_outside_narrowed_baseline_scope() -> (
    None
):
    # End-to-end version of Codex's exact scenario: the baseline was collected
    # by a narrowed (PR/--since-scoped) inline run that only ever examined
    # ``pub``'s TU, where it found (and still has, unchanged) a
    # TYPE_HAS_FIELD_TYPE dependency on `priv_type`. A second public entry,
    # `pub2`, lives in a TU the narrowed baseline never inspected. The
    # candidate ran a confirmed full pass and found `pub2` also depends on a
    # private type there. Before the eleventh-round fix, the baseline's
    # unrelated `priv_type` edge would have made TYPE_HAS_FIELD_TYPE read as
    # "common" coverage, letting `pub2`'s dependency — from a TU the baseline
    # never saw — be reported as newly added. It must not be: the baseline
    # cannot vouch for a TU it never examined.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("pub2", "source_decl", "pub2()"),
        _N("sym2", "binary_symbol", "pub2"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
        _N("other_priv", "record_type", "detail::Other", visibility="private_header"),
    ]
    shared_edges = [
        _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub", "SOURCE_DECLARES"),
        _E("pub2", "sym2", "SOURCE_DECL_MAPS_TO_SYMBOL"),
        _E("hdr", "pub2", "SOURCE_DECLARES"),
        _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=list(shared_edges),
        narrowed_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[*shared_edges, _E("pub2", "other_priv", "TYPE_HAS_FIELD_TYPE")],
        extractor_passes={"type_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_flags_first_ever_family_edge_via_extractor_passes() -> None:
    # End-to-end version of the above through diff_source_graph_findings: the
    # baseline genuinely has zero type-graph edges (pass ran, nothing to find
    # yet), so only extractor_passes tells the diff the coverage is comparable.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
        ],
        extractor_passes={"type_graph": True},
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
        ],
        extractor_passes={"type_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_l5_internal_dep_skipped_when_pass_never_ran_on_baseline() -> None:
    # Contrast case: extractor_passes absent on the old side (a pre-slice-2
    # pack, or a pass that genuinely never ran) with zero type edges must still
    # skip — only a *recorded* pass run justifies treating zero edges as
    # comparable coverage.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
        ],
    )  # no extractor_passes recorded, no type edges either
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "priv_type", "TYPE_HAS_FIELD_TYPE"),
        ],
        extractor_passes={"type_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_not_flagged_when_edge_unchanged_but_provenance_improves() -> (
    None
):
    # Eighth Codex review: the pub -> target edge already existed in the old
    # graph (a Kythe/older-pack callee with no SOURCE_DECLARES/
    # defined_in_project provenance, so old could not classify it internal),
    # and still exists unchanged in the new graph, where it has *also* gained
    # provenance (a SOURCE_DECLARES edge marking it private_header). Only the
    # classification evidence improved — the dependency itself is not new —
    # so this must not fire.
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("priv_hdr", "header", "detail/impl.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        # No visibility/defined_in_project on the old side's copy of this
        # node — unclassifiable, exactly like a Kythe-ingested callee.
        _N("target", "source_decl", "detail::helper()"),
    ]
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "target", "DECL_CALLS_DECL"),
        ],
        extractor_passes={"call_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[
            *nodes[:-1],
            _N(
                "target", "source_decl", "detail::helper()", visibility="private_header"
            ),
        ],
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("priv_hdr", "target", "SOURCE_DECLARES"),
            _E("pub", "target", "DECL_CALLS_DECL"),
        ],
        extractor_passes={"call_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value not in kinds


def test_l5_internal_dep_flagged_when_edge_is_genuinely_new_even_with_provenance_gain() -> (
    None
):
    # Contrast case: the pub -> target edge is genuinely NEW in this version
    # (unreachable at all in the old graph) and the target also happens to be
    # classifiable as internal in the new graph — this must still fire, since
    # the fix above must not overcorrect into silence for real new edges.
    nodes_old = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
    ]
    old = SourceGraphSummary(
        nodes=nodes_old,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "pub", "DECL_CALLS_DECL"),
        ],
        extractor_passes={"call_graph": True},
    )
    new = SourceGraphSummary(
        nodes=[
            *nodes_old,
            _N("priv_hdr", "header", "detail/impl.h"),
            _N(
                "target", "source_decl", "detail::helper()", visibility="private_header"
            ),
        ],
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr", "pub", "SOURCE_DECLARES"),
            _E("pub", "pub", "DECL_CALLS_DECL"),
            _E("priv_hdr", "target", "SOURCE_DECLARES"),
            _E("pub", "target", "DECL_CALLS_DECL"),
        ],
        extractor_passes={"call_graph": True},
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED.value in kinds


def test_dependency_path_same_node_returns_empty_list() -> None:
    g = SourceGraphSummary(
        nodes=[_N("a", "source_decl")], edges=[_E("a", "a", "DECL_CALLS_DECL")]
    )
    assert _dependency_path(g, frozenset({"DECL_CALLS_DECL"}), "a", "a") == []


def test_dependency_path_returns_none_when_unreachable() -> None:
    # "b" exists but has no incoming edge from "a" within the given edge kinds.
    g = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "source_decl")], edges=[]
    )
    assert _dependency_path(g, frozenset({"DECL_CALLS_DECL"}), "a", "b") is None


def test_dependency_path_reconstructs_multi_hop_chain() -> None:
    g = SourceGraphSummary(
        nodes=[_N("a", "source_decl"), _N("b", "source_decl"), _N("c", "record_type")],
        edges=[
            _E("a", "b", "DECL_CALLS_DECL"),
            _E("b", "c", "DECL_HAS_TYPE"),
        ],
    )
    path = _dependency_path(
        g, frozenset({"DECL_CALLS_DECL", "DECL_HAS_TYPE"}), "a", "c"
    )
    assert path is not None
    assert [(e.src, e.kind, e.dst) for e in path] == [
        ("a", "DECL_CALLS_DECL", "b"),
        ("b", "DECL_HAS_TYPE", "c"),
    ]
    assert _format_dependency_path(g, path) == (
        "a --[DECL_CALLS_DECL]--> b --[DECL_HAS_TYPE]--> c"
    )


def test_format_dependency_path_empty_list_returns_empty_string() -> None:
    g = SourceGraphSummary(nodes=[_N("a", "source_decl")], edges=[])
    assert _format_dependency_path(g, []) == ""


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
    nodes = [
        _N("pub", "source_decl"),
        _N("sym", "binary_symbol"),
        _N("intn", "source_decl", visibility="private_header"),
    ]
    g = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("pub", "intn", "DECL_CALLS_DECL"),
        ],
    )
    assert _public_entry_internal_reach(g, frozenset()) == set()


def test_public_entry_internal_reach_no_public_closure_returns_empty() -> None:
    # Direct unit test: reach is non-empty but the graph has no public closure
    # at all (no SOURCE_DECLARES edges), so there is nothing to subtract over.
    nodes = [
        _N("pub", "source_decl", "pub()"),
        _N("sym", "binary_symbol", "pub"),
        _N("intn", "source_decl", "intn()", visibility="private_header"),
    ]
    g = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("pub", "sym", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("pub", "intn", "DECL_CALLS_DECL"),
        ],
    )
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
    old = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr:a.h", "d", "SOURCE_DECLARES"),
        ],
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("hdr:b.h", "d", "SOURCE_DECLARES"),
        ],
    )
    assert ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED.value in _graph_kinds(
        old, new
    )


def test_l5_target_dependency_added() -> None:
    nodes = [_N("t:libA", "target", "libA"), _N("t:libB", "target", "libB")]
    old = SourceGraphSummary(nodes=nodes, edges=[])
    new = SourceGraphSummary(
        nodes=nodes, edges=[_E("t:libA", "t:libB", "TARGET_DEPENDS_ON")]
    )
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
        edges=[
            _E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("src:a", "d", "SOURCE_DECLARES"),
        ],
    )
    new = SourceGraphSummary(
        nodes=nodes,
        edges=[
            _E("d", "s", "SOURCE_DECL_MAPS_TO_SYMBOL"),
            _E("src:b", "d", "SOURCE_DECLARES"),
        ],
    )
    kinds = _graph_kinds(old, new)
    assert ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED.value in kinds
    assert ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED in RISK_KINDS


def test_l5_identical_graph_emits_nothing() -> None:
    nodes = [_N("t:libA", "target", "libA"), _N("t:libB", "target", "libB")]
    g = SourceGraphSummary(
        nodes=nodes, edges=[_E("t:libA", "t:libB", "TARGET_DEPENDS_ON")]
    )
    assert diff_source_graph_findings(g, g) == []


def _internal_dep_scenario() -> tuple[SourceGraphSummary, SourceGraphSummary]:
    nodes = [
        _N("hdr", "header", "api.h"),
        _N("pub", "source_decl", "pub"),
        _N("sym", "binary_symbol", "pub"),
        _N(
            "priv_type",
            "record_type",
            "detail::PrivateType",
            visibility="private_header",
        ),
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
    return old, new


def test_l5_internal_dependency_correlates_with_own_body_hash_change() -> None:
    # ADR-041 P0 roadmap item 2: "same public decl, different body_hash/
    # type_hash combined with a new/changed graph edge" — when the L4 surface
    # diff (source_diff.diff_source_abi) proves the SAME public entry also had
    # its own implementation change this version, PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    # should say so, instead of leaving two disjoint findings (INLINE_BODY_CHANGED
    # for "pub", PUBLIC_API_INTERNAL_DEPENDENCY_ADDED for "pub" -> priv_type) for
    # a reader to connect by hand.
    old_graph, new_graph = _internal_dep_scenario()
    old_surface = _surf(reachable_inline_bodies=[_ent("inline", "pub", body_hash="h1")])
    new_surface = _surf(reachable_inline_bodies=[_ent("inline", "pub", body_hash="h2")])
    src_changes = diff_source_abi(old_surface, new_surface)
    assert ChangeKind.INLINE_BODY_CHANGED.value in _kinds(src_changes)

    findings = diff_source_graph_findings(
        old_graph, new_graph, source_diff_changes=src_changes
    )
    dep_finding = next(
        f for f in findings if f.kind == ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    )
    assert "own implementation also changed" in dep_finding.description
    assert "'h1'" in dep_finding.description
    assert "'h2'" in dep_finding.description


def test_l5_internal_dependency_uncorrelated_without_source_diff_changes() -> None:
    # Same scenario, but the caller passes no L4 surface diff (e.g. `abicheck
    # graph compare`, which only ever has bare SourceGraphSummary files, no
    # build-source facts) — behavior is unchanged from before this roadmap item:
    # no correlation text, since there is nothing to correlate against.
    old_graph, new_graph = _internal_dep_scenario()
    findings = diff_source_graph_findings(old_graph, new_graph)
    dep_finding = next(
        f for f in findings if f.kind == ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    )
    assert "own implementation also changed" not in dep_finding.description


def test_l5_internal_dependency_not_correlated_with_unrelated_decls_change() -> None:
    # The correlation must be keyed on the *same* public entry, not any change
    # in the source_diff result set — an unrelated decl's body change must not
    # be attached to this entry's finding.
    old_graph, new_graph = _internal_dep_scenario()
    old_surface = _surf(
        reachable_inline_bodies=[_ent("inline", "other", body_hash="h1")]
    )
    new_surface = _surf(
        reachable_inline_bodies=[_ent("inline", "other", body_hash="h2")]
    )
    src_changes = diff_source_abi(old_surface, new_surface)
    assert ChangeKind.INLINE_BODY_CHANGED.value in _kinds(src_changes)

    findings = diff_source_graph_findings(
        old_graph, new_graph, source_diff_changes=src_changes
    )
    dep_finding = next(
        f for f in findings if f.kind == ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    )
    assert "own implementation also changed" not in dep_finding.description
