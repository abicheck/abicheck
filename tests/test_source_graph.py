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

"""Tests for ADR-031 L5 source graph: schema round-trip, graph-derived risk
findings, and pack + CLI wiring. build_source_graph()'s own construction
tests moved to test_source_graph_build.py and diff_source_graph()/
localize_symbol()'s comparison tests moved to test_source_graph_compare.py
(ADR-061 Phase 5 item 2's production module split)."""

from __future__ import annotations

import json

from abicheck.buildsource import pack_io
from abicheck.buildsource.build_evidence import (
    BuildEvidence,
    CompileUnit,
    Confidence,
    Target,
    TargetKind,
)
from abicheck.buildsource.model import CoverageStatus, DataLayer, LayerConfidence
from abicheck.buildsource.pack import BuildSourcePack
from abicheck.buildsource.source_abi import (
    SourceAbiSurface,
    SourceEntity,
    SourceLocation,
)
from abicheck.buildsource.source_graph import (
    EVIDENCE_TIER_L5,
    SOURCE_GRAPH_VERSION,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    build_source_graph,
    diff_source_graph,
    diff_source_graph_findings,
)
from abicheck.checker_policy import RISK_KINDS, ChangeKind


def _sample_build() -> BuildEvidence:
    b = BuildEvidence(generated_files=["gen/config.h"])
    b.targets.append(
        Target(
            id="target://libfoo",
            name="foo",
            kind=TargetKind.SHARED_LIBRARY,
            source_files=["src/foo.cpp", "gen/config.h"],
            public_headers=["include/foo.h"],
            dependencies=["target://libbar", "sys://pthread"],
            confidence=Confidence.HIGH,
        )
    )
    b.targets.append(Target(id="target://libbar", name="bar"))
    b.compile_units.append(
        CompileUnit(
            id="cu://foo",
            source="src/foo.cpp",
            output="foo.o",
            target_id="target://libfoo",
            abi_relevant_flags=["-fvisibility=hidden", "-std=c++20"],
        )
    )
    return b


# Shared builders for the tests below (also duplicated, since these are
# small, in test_source_graph_build.py and test_source_graph_compare.py --
# see those modules for the construction/comparison tests that used to sit
# here before the ADR-061 Phase 5 item 2 production module split).


def _entity(
    qn: str,
    kind: str,
    *,
    mangled: str = "",
    path: str = "include/foo.h",
    origin: str = "PUBLIC_HEADER",
    conf: LayerConfidence = LayerConfidence.HIGH,
) -> SourceEntity:
    return SourceEntity(
        id=qn,
        kind=kind,
        qualified_name=qn,
        mangled_name=mangled,
        source_location=SourceLocation(path=path, line=1, origin=origin),
        visibility="public_header",
        confidence=conf,
    )


def _sample_surface() -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    s.reachable_declarations.append(
        _entity("foo::bar", "function", mangled="_ZN3foo3barEv")
    )
    s.reachable_types.append(_entity("foo::Widget", "record"))
    s.reachable_types.append(_entity("foo::Color", "enum"))
    s.reachable_types.append(_entity("foo::Alias", "typedef"))
    s.reachable_macros.append(
        _entity("FOO_VERSION", "macro", conf=LayerConfidence.REDUCED)
    )
    # Keyed by entity identity (the mangled name for C++), exactly as
    # link_source_abi/relink_surface_exports persist it — not by qualified_name.
    s.mappings["source_decl_to_binary_symbol"] = {"_ZN3foo3barEv": "_ZN3foo3barEv"}
    s.mappings["source_type_to_debug_type"] = {"foo::Widget": "struct foo::Widget"}
    return s


# ── Phase 5: graph-derived risk findings (D6) ───────────────────────────────


def _surface_with(
    decls, mapping, *, generated_header=None, target="target://libfoo"
) -> SourceAbiSurface:
    s = SourceAbiSurface(library="libfoo.so", target_id=target)
    for qn, path in decls:
        s.reachable_declarations.append(
            SourceEntity(
                id=qn,
                kind="function",
                qualified_name=qn,
                source_location=SourceLocation(
                    path=path, line=1, origin="PUBLIC_HEADER"
                ),
                visibility="public_header",
                confidence=LayerConfidence.HIGH,
            )
        )
    s.mappings["source_decl_to_binary_symbol"] = dict(mapping)
    return s


def _build_with_public_header(headers=("inc/foo.h",), generated=()) -> BuildEvidence:
    b = BuildEvidence(generated_files=list(generated))
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=list(headers),
            confidence=Confidence.HIGH,
        )
    )
    return b


def test_all_three_graph_kinds_are_risk() -> None:
    for k in (
        ChangeKind.PUBLIC_REACHABILITY_CHANGED,
        ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED,
        ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API,
    ):
        assert k in RISK_KINDS


def test_findings_mapping_changed_for_persisting_decl() -> None:
    b = _build_with_public_header()
    old = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb"})
    )
    new = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb2"})
    )
    findings = diff_source_graph_findings(old, new)
    assert len(findings) == 1
    c = findings[0]
    assert c.kind == ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED
    assert c.old_value == "_Zb" and c.new_value == "_Zb2"
    # CLI audit finding: source_location should localize to the declaration's
    # actual declaring file, not the generic evidence-tier tag, when the
    # graph resolves one via a SOURCE_DECLARES edge (it does here).
    assert c.source_location == "inc/foo.h"


def test_findings_reachability_ignores_brand_new_or_removed_decls() -> None:
    # A decl id absent from the OTHER side entirely (not merely absent from
    # its public closure) is a brand-new/removed declaration, not a
    # persisting one whose reachability state changed. "Entering the
    # closure" is a trivial, expected consequence of being newly added —
    # nothing risky about a symbol being public from birth — and that event
    # is already reported (at the correct COMPATIBLE severity) by the
    # ordinary addition/removal findings elsewhere in the pipeline.
    b = _build_with_public_header()
    old = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::gone", "inc/foo.h")], {"foo::a": "_Za"}
        ),
    )
    new = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::new", "inc/foo.h")], {"foo::a": "_Za"}
        ),
    )
    kinds_syms = {(c.kind, c.symbol) for c in diff_source_graph_findings(old, new)}
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::new") not in kinds_syms
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::gone") not in kinds_syms


def test_findings_reachability_fires_for_persisting_decl_crossing_boundary() -> None:
    # foo::b exists on BOTH sides (same identity, so the same "decl://foo::b"
    # node id) but is only linked to a public header on the new side — an
    # existing declaration crossing the public/private boundary, the
    # genuinely risk-worthy signal this finding exists for.
    b = _build_with_public_header()
    old = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::b", "")], {"foo::a": "_Za"}
        ),
    )
    new = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::b", "inc/foo.h")], {"foo::a": "_Za"}
        ),
    )
    kinds_syms = {(c.kind, c.symbol) for c in diff_source_graph_findings(old, new)}
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::b") in kinds_syms


def test_findings_reachability_fires_when_persisting_decl_leaves_closure() -> None:
    b = _build_with_public_header()
    old = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::b", "inc/foo.h")], {"foo::a": "_Za"}
        ),
    )
    new = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "inc/foo.h"), ("foo::b", "")], {"foo::a": "_Za"}
        ),
    )
    kinds_syms = {(c.kind, c.symbol) for c in diff_source_graph_findings(old, new)}
    assert (ChangeKind.PUBLIC_REACHABILITY_CHANGED, "foo::b") in kinds_syms


def test_findings_empty_baseline_does_not_spam_reachability() -> None:
    # An empty old graph must not flag every new declaration as "entered".
    b = _build_with_public_header()
    new = build_source_graph(
        b, source_abi=_surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"})
    )
    findings = diff_source_graph_findings(SourceGraphSummary(), new)
    assert not any(c.kind == ChangeKind.PUBLIC_REACHABILITY_CHANGED for c in findings)


def test_findings_generated_header_reaches_public_api() -> None:
    # A public header that is also a generated file → reaches public API.
    old = build_source_graph(_build_with_public_header(headers=("inc/foo.h",)))
    new = build_source_graph(
        _build_with_public_header(
            headers=("inc/foo.h", "gen/config.h"), generated=("gen/config.h",)
        )
    )
    findings = diff_source_graph_findings(old, new)
    gen = [
        c for c in findings if c.kind == ChangeKind.GENERATED_HEADER_REACHES_PUBLIC_API
    ]
    assert len(gen) == 1
    assert "gen/config.h" in gen[0].symbol


def test_owner_unchanged_across_different_absolute_checkout_roots() -> None:
    # Two independent checkouts of the *same* tree (e.g. a benchmark's old/
    # new directories, or two separate CI job workspaces) share no absolute
    # root. The declaring file's path relative to its own tree is identical
    # ("inc/foo.h"/"inc/bar.h" in both), so this must NOT look like every
    # file moved just because the checkout root differs (regression for the
    # false positive this produced across most of examples/, since the
    # catalog's own v1/v2 fixture convention is exactly this shape).
    old = build_source_graph(
        _build_with_public_header(
            headers=("/old_root/inc/foo.h", "/old_root/inc/bar.h")
        ),
        source_abi=_surface_with(
            [("foo::a", "/old_root/inc/foo.h"), ("foo::c", "/old_root/inc/bar.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    new = build_source_graph(
        _build_with_public_header(
            headers=("/new_root/inc/foo.h", "/new_root/inc/bar.h")
        ),
        source_abi=_surface_with(
            [("foo::a", "/new_root/inc/foo.h"), ("foo::c", "/new_root/inc/bar.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    assert not any(
        c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED for c in findings
    )


def test_owner_changed_when_relative_path_actually_moves() -> None:
    # A genuine relocation *within* the same tree (same root, different
    # relative path) must still fire — only the checkout-root difference is
    # meant to be ignored, not a real declaration move.
    b = _build_with_public_header(
        headers=("/root/inc/foo.h", "/root/inc/bar.h", "/root/inc/baz.h"),
    )
    old = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "/root/inc/foo.h"), ("foo::c", "/root/inc/bar.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    new = build_source_graph(
        b,
        source_abi=_surface_with(
            [("foo::a", "/root/inc/foo.h"), ("foo::c", "/root/inc/baz.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    owner = [
        c for c in findings if c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED
    ]
    assert len(owner) == 1
    assert owner[0].symbol == "_Zc"
    # CLI audit finding: source_location should localize to the symbol's new
    # declaring file, not the generic evidence-tier tag -- this family always
    # has one on hand (it's the whole point of the finding).
    assert owner[0].source_location == "/root/inc/baz.h"


def test_owner_changed_when_sole_declaring_file_is_renamed_both_sides() -> None:
    # When every exported symbol on a side declares in the SAME file, the
    # common prefix spans the whole path including the filename. If
    # _common_prefix_len didn't reserve the filename segment, both sides
    # would strip down to an empty "scheme://" key and a same-shape rename
    # (foo.h -> bar.h on both sides) would be missed entirely.
    old = build_source_graph(
        _build_with_public_header(headers=("/root/inc/foo.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/inc/foo.h"), ("foo::c", "/root/inc/foo.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    new = build_source_graph(
        _build_with_public_header(headers=("/root/inc/bar.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/inc/bar.h"), ("foo::c", "/root/inc/bar.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    owner_syms = {
        c.symbol
        for c in findings
        if c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED
    }
    assert owner_syms == {"_Za", "_Zc"}


def test_owner_unchanged_when_one_side_single_file_other_multi_file() -> None:
    # Asymmetric shapes: old has every symbol in one file (so its own common
    # prefix would include the filename before the fix), new spreads them
    # across two files. Declarations didn't actually move, so nothing should
    # fire even though the two sides' "common prefix" lengths differ.
    old = build_source_graph(
        _build_with_public_header(headers=("/root/inc/foo.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/inc/foo.h"), ("foo::c", "/root/inc/foo.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    new = build_source_graph(
        _build_with_public_header(headers=("/root2/inc/foo.h", "/root2/inc/bar.h")),
        source_abi=_surface_with(
            [("foo::a", "/root2/inc/foo.h"), ("foo::c", "/root2/inc/foo.h")],
            {"foo::a": "_Za", "foo::c": "_Zc"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    assert not any(
        c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED for c in findings
    )


def test_owner_unchanged_when_only_one_persisting_symbol_declares() -> None:
    # A side with exactly ONE declaring file has no sibling entry to compute
    # a shared directory prefix against, so the "reserve the filename" rule
    # (case04-style) never engaged and the raw absolute path was compared —
    # "case03/old/lib.h" vs "case03/new/lib.h" looked like a real move for
    # any case whose only persisting exported symbol shares its header with
    # no other symbol (a single-symbol library, or a brand-new symbol added
    # alongside the one persisting symbol). Must fall back to basename-only
    # identity, same as the multi-symbol cases below it.
    old = build_source_graph(
        _build_with_public_header(headers=("/root/old/lib.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/old/lib.h")],
            {"foo::a": "_Za"},
        ),
    )
    new = build_source_graph(
        _build_with_public_header(headers=("/root/new/lib.h",)),
        source_abi=_surface_with(
            [("foo::a", "/root/new/lib.h"), ("foo::b", "/root/new/lib.h")],
            {"foo::a": "_Za", "foo::b": "_Zb"},
        ),
    )
    findings = diff_source_graph_findings(old, new)
    assert not any(
        c.kind == ChangeKind.EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED for c in findings
    )


def test_findings_identical_graphs_yield_nothing() -> None:
    b = _build_with_public_header()
    g = build_source_graph(
        b, source_abi=_surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"})
    )
    assert diff_source_graph_findings(g, g) == []


def test_compare_graph_cli_surfaces_findings() -> None:
    # `graph compare` (deleted CLI command, ADR-043) was a thin wrapper over
    # `diff_source_graph`/`diff_source_graph_findings` — exercise those
    # directly; the L5 graph is now an internal consequence of `--depth
    # source` rather than a separate command.
    b = _build_with_public_header()
    old = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb"})
    )
    new = build_source_graph(
        b, source_abi=_surface_with([("foo::b", "inc/foo.h")], {"foo::b": "_Zb2"})
    )
    findings = diff_source_graph_findings(old, new)
    assert findings
    assert findings[0].kind == ChangeKind.SOURCE_TO_BINARY_MAPPING_CHANGED


# ── Finalize: build-option→symbol flow, include drift, localization ─────────


def test_build_option_reaches_public_symbol_edges_and_finding() -> None:
    def _build(flags):
        b = BuildEvidence()
        b.targets.append(
            Target(
                id="target://libfoo",
                public_headers=["inc/foo.h"],
                confidence=Confidence.HIGH,
            )
        )
        b.compile_units.append(
            CompileUnit(
                id="cu://foo",
                source="src/foo.cpp",
                target_id="target://libfoo",
                abi_relevant_flags=flags,
            )
        )
        return b

    surf = _surface_with([("foo::a", "inc/foo.h")], {"foo::a": "_Za"})
    old = build_source_graph(_build(["-std=c++20"]), source_abi=surf)
    new = build_source_graph(
        _build(["-std=c++20", "-fvisibility=hidden"]), source_abi=surf
    )
    assert any(e.kind == "BUILD_OPTION_AFFECTS_SYMBOL" for e in new.edges)
    bo = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL
    ]
    assert len(bo) == 1
    assert "-fvisibility=hidden" in bo[0].symbol
    assert bo[0].source_location == f"[{EVIDENCE_TIER_L5}]"


def test_build_option_reaches_public_symbol_ignores_reused_flag_on_new_target() -> None:
    # A new target reusing a pre-existing flag must NOT raise the finding — that
    # is symbol-level churn, not flag drift (only a *new* flag is interesting).
    def _build(targets):
        b = BuildEvidence()
        for tid, hdr in targets:
            b.targets.append(
                Target(id=tid, public_headers=[hdr], confidence=Confidence.HIGH)
            )
            b.compile_units.append(
                CompileUnit(
                    id=f"cu://{tid}",
                    source=f"src/{tid}.cpp",
                    target_id=tid,
                    abi_relevant_flags=["-std=c++20"],
                )
            )
        return b

    old_surf = _surface_with(
        [("foo::a", "inc/foo.h")], {"foo::a": "_Za"}, target="target://foo"
    )
    new_surf = _surface_with(
        [("bar::b", "inc/bar.h")], {"bar::b": "_Zb"}, target="target://bar"
    )
    old = build_source_graph(
        _build([("target://foo", "inc/foo.h")]), source_abi=old_surf
    )
    new = build_source_graph(
        _build([("target://foo", "inc/foo.h"), ("target://bar", "inc/bar.h")]),
        source_abi=new_surf,
    )
    bo = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.BUILD_OPTION_REACHES_PUBLIC_SYMBOL
    ]
    # -std=c++20 already existed in the old graph → no flag-drift finding.
    assert bo == []


def test_include_graph_public_header_drift_finding() -> None:
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["inc/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    b.compile_units.append(
        CompileUnit(id="cu://foo", source="src/foo.cpp", target_id="target://libfoo")
    )
    old = build_source_graph(b)
    # The old side must have *confirmed* include-graph coverage (a pass that
    # ran and genuinely found nothing) for its absence to be trusted evidence
    # — otherwise this is indistinguishable from an older snapshot that never
    # collected include data at all, and "entered" would be a coverage
    # artifact, not a real drift (Codex review; _include_graph_covered).
    old.extractor_passes["include_graph"] = True
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.finalize()
    inc = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT
    ]
    assert len(inc) == 1
    assert inc[0].symbol == "inc/foo.h"


def test_include_graph_public_header_drift_suppressed_without_old_coverage() -> None:
    # The exact false-positive Codex flagged: an old snapshot with no
    # include-graph data at all (never collected, or clang unavailable) vs a
    # new one that has it must NOT report every header in the new side as
    # newly "entered" — that's a coverage artifact, not a real change.
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["inc/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    b.compile_units.append(
        CompileUnit(id="cu://foo", source="src/foo.cpp", target_id="target://libfoo")
    )
    old = build_source_graph(b)  # no include data, no confirmed pass at all
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.finalize()
    inc = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT
    ]
    assert inc == []


def test_include_graph_public_header_drift_suppressed_for_narrowed_new_side() -> None:
    # A narrowed new side (a PR/--since scan folding only the changed compile
    # units) only examined a subset of the project. It must not report public
    # headers outside that subset as having "left" the include graph just
    # because its narrowed pass has real, but partial, edges (Codex review;
    # _include_graph_fully_covered).
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["inc/foo.h", "inc/bar.h"],
            confidence=Confidence.HIGH,
        )
    )
    b.compile_units.append(
        CompileUnit(id="cu://foo", source="src/foo.cpp", target_id="target://libfoo")
    )
    b.compile_units.append(
        CompileUnit(id="cu://bar", source="src/bar.cpp", target_id="target://libfoo")
    )
    old = build_source_graph(b)
    augment_graph_with_includes(
        old, {"cu://foo": ["inc/foo.h"], "cu://bar": ["inc/bar.h"]}
    )
    old.finalize()
    # New side only re-examined src/foo.cpp (a narrowed PR-diff scan) — its
    # include graph genuinely has "inc/bar.h" missing, but only because that
    # TU was never walked, not because the header stopped being included.
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.narrowed_passes["include_graph"] = True
    new.narrowed_scope["include_graph"] = frozenset({"src/foo.cpp"})
    new.finalize()
    inc = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT
    ]
    assert inc == []


def test_include_graph_public_header_drift_trusted_for_matching_narrowed_scope() -> (
    None
):
    # Two sides narrowed to the *identical* scope examined the exact same
    # compile units, so a header appearing in one but not the other within
    # that shared scope is real drift, not a coverage gap.
    from abicheck.buildsource.include_graph import augment_graph_with_includes

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["inc/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    b.compile_units.append(
        CompileUnit(id="cu://foo", source="src/foo.cpp", target_id="target://libfoo")
    )
    old = build_source_graph(b)
    old.narrowed_passes["include_graph"] = True
    old.narrowed_scope["include_graph"] = frozenset({"src/foo.cpp"})
    old.finalize()
    new = build_source_graph(b)
    augment_graph_with_includes(new, {"cu://foo": ["inc/foo.h"]})
    new.narrowed_passes["include_graph"] = True
    new.narrowed_scope["include_graph"] = frozenset({"src/foo.cpp"})
    new.finalize()
    inc = [
        c
        for c in diff_source_graph_findings(old, new)
        if c.kind == ChangeKind.INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT
    ]
    assert len(inc) == 1
    assert inc[0].symbol == "inc/foo.h"


def test_explain_finding_cli() -> None:
    # `graph explain` (deleted CLI command, ADR-043) was a thin wrapper over
    # `localize_symbol` (+ `_resolve_symbol_from_report` for --finding-id) —
    # exercise those directly.
    from abicheck.buildsource.source_graph import localize_symbol

    b = BuildEvidence()
    b.targets.append(
        Target(
            id="target://libfoo",
            public_headers=["include/foo.h"],
            confidence=Confidence.HIGH,
        )
    )
    g = build_source_graph(b, source_abi=_sample_surface())

    result = localize_symbol(g, "_ZN3foo3barEv")
    assert result["found"] is True
    assert "target://libfoo" in result["exported_by_targets"]
    assert "foo::bar" in result["source_declarations"]


def test_explain_finding_resolves_symbol_from_report(tmp_path) -> None:
    from abicheck.cli_graph import _resolve_symbol_from_report

    report = tmp_path / "report.json"
    report.write_text(json.dumps({"changes": [{"symbol": "_ZN3foo3barEv"}]}))

    assert _resolve_symbol_from_report(report, "0") == "_ZN3foo3barEv"


# The deleted `graph explain` command's "no --symbol and no resolvable
# --report/--finding-id" usage error (`test_explain_finding_requires_a_symbol`)
# was pure CLI-argument plumbing with no surviving entry point —
# `localize_symbol`/`_resolve_symbol_from_report` both already require a
# symbol string to be passed in, so there's nothing left to call directly for
# this scenario.


def test_resolve_symbol_from_report_variants(tmp_path) -> None:
    from abicheck.cli_graph import _resolve_symbol_from_report

    report = tmp_path / "r.json"
    report.write_text(
        json.dumps(
            {
                "changes": [
                    {"symbol": "_ZN3foo3barEv"},
                    {"symbol": "_ZN3foo3bazEv"},
                ]
            }
        )
    )
    # index lookup
    assert _resolve_symbol_from_report(report, "1") == "_ZN3foo3bazEv"
    # substring match
    assert _resolve_symbol_from_report(report, "bar") == "_ZN3foo3barEv"
    # out-of-range index → empty
    assert _resolve_symbol_from_report(report, "9") == ""
    # no match → empty
    assert _resolve_symbol_from_report(report, "nope") == ""


def test_resolve_symbol_from_report_unreadable(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_graph import _resolve_symbol_from_report

    with pytest.raises(click.ClickException):
        _resolve_symbol_from_report(tmp_path / "missing.json", "0")


def test_resolve_symbol_from_report_non_object(tmp_path) -> None:
    # A valid-but-non-object report (a bare JSON list) must raise a Click error,
    # not an unhandled AttributeError from `.get(...)`.
    import click
    import pytest

    from abicheck.cli_graph import _resolve_symbol_from_report

    report = tmp_path / "list.json"
    report.write_text(json.dumps([{"symbol": "_Zx"}]))
    with pytest.raises(click.ClickException, match="must contain a JSON object"):
        _resolve_symbol_from_report(report, "0")


def test_resolve_symbol_from_report_non_list_changes(tmp_path) -> None:
    from abicheck.cli_graph import _resolve_symbol_from_report

    report = tmp_path / "r.json"
    report.write_text(json.dumps({"changes": "not-a-list"}))
    assert _resolve_symbol_from_report(report, "0") == ""


def test_explain_finding_text_symbol_absent() -> None:
    # `graph explain`'s text-mode "not present" notice was just a `found`-flag
    # check on `localize_symbol`'s result — covered directly by
    # `test_localize_symbol_absent_returns_empty` above (`result["found"] is
    # False`); no CLI-level replacement needed.
    from abicheck.buildsource.source_graph import localize_symbol

    result = localize_symbol(build_source_graph(BuildEvidence()), "_Zmissing")
    assert result["found"] is False


def test_load_source_graph_invalid_pack_dir(tmp_path) -> None:
    # A directory that is not a valid evidence pack yields an actionable error.
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    with pytest.raises(click.ClickException):
        _load_source_graph(tmp_path)


def test_graph_helpers_backcompat_reexport_from_cli_buildsource() -> None:
    """The helpers moved to ``cli_graph`` when the graph group was extracted, but
    the historical ``from abicheck.cli_buildsource import _load_source_graph``
    path stays alive via a lazy ``__getattr__`` shim (no import cycle). Pin it so
    the back-compat surface can't silently regress; an unknown attr still raises.
    """
    import pytest

    from abicheck import cli_buildsource, cli_graph

    assert cli_buildsource._load_source_graph is cli_graph._load_source_graph
    assert (
        cli_buildsource._resolve_symbol_from_report
        is cli_graph._resolve_symbol_from_report
    )
    with pytest.raises(AttributeError):
        cli_buildsource._definitely_not_a_real_attr


# ── Phase 1: schema round-trip + content addressing ─────────────────────────


def test_round_trip_preserves_graph_id() -> None:
    g = build_source_graph(_sample_build())
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.compute_graph_id() == g.compute_graph_id()
    assert len(restored.nodes) == len(g.nodes)
    assert len(restored.edges) == len(g.edges)


def test_extractor_passes_round_trips() -> None:
    # ADR-041 P0 slice 2 follow-up: extractor_passes must survive to_dict/
    # from_dict so a version diff loaded from a pack can still tell "the pass
    # ran, zero edges" from "the pass never ran".
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.extractor_passes["type_graph"] = True
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.extractor_passes == {"type_graph": True}


def test_narrowed_passes_round_trips() -> None:
    # Eleventh Codex review: narrowed_passes must survive to_dict/from_dict so
    # a version diff loaded from a pack can still tell a narrowed (PR/--since
    # -scoped) pass's edges from a confirmed full pass's.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.narrowed_passes["type_graph"] = True
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.narrowed_passes == {"type_graph": True}
    assert restored.extractor_passes == {}


def test_narrowed_scope_round_trips() -> None:
    # Fourteenth Codex review: narrowed_scope must survive to_dict/from_dict so
    # a version diff loaded from a pack can still tell "narrowed to the same
    # TUs" from "narrowed but to different, disjoint code".
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.narrowed_passes["type_graph"] = True
    g.narrowed_scope["type_graph"] = frozenset({"src/a.cpp", "src/b.cpp"})
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.narrowed_scope == {
        "type_graph": frozenset({"src/a.cpp", "src/b.cpp"})
    }


def test_degraded_passes_round_trips() -> None:
    # Sixteenth Codex review: degraded_passes must survive to_dict/from_dict so
    # a version diff loaded from a pack can still tell "ran unnarrowed but hit
    # per-TU diagnostics" from a clean confirmed pass.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.degraded_passes["type_graph"] = True
    restored = SourceGraphSummary.from_dict(g.to_dict())
    assert restored.degraded_passes == {"type_graph": True}
    assert restored.extractor_passes == {}
    assert restored.narrowed_passes == {}


def test_graph_id_order_independent() -> None:
    a = SourceGraphSummary()
    a.add_node(GraphNode(id="x", kind="target"))
    a.add_node(GraphNode(id="y", kind="source"))
    a.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    b = SourceGraphSummary()
    b.add_node(GraphNode(id="y", kind="source"))
    b.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    b.add_node(GraphNode(id="x", kind="target"))
    assert a.compute_graph_id() == b.compute_graph_id()


def test_add_node_and_edge_dedupe() -> None:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    g.add_node(GraphNode(id="x", kind="target"))
    g.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    g.add_edge(GraphEdge(src="x", dst="y", kind="TARGET_HAS_SOURCE"))
    assert len(g.nodes) == 1
    assert len(g.edges) == 1


def test_from_dict_forward_compatible_with_unknown_fields() -> None:
    # A hand-edited / newer summary with an unknown node kind and extra keys
    # must load, not abort (evidence/CLAUDE.md forward-compat rule).
    data = {
        "schema_version": SOURCE_GRAPH_VERSION + 99,
        "nodes": [{"id": "n1", "kind": "future_kind", "future_attr": 1}],
        "edges": [{"edge": "FUTURE_EDGE", "src": "n1", "dst": "n2"}],
        "unknown_top_level": True,
    }
    g = SourceGraphSummary.from_dict(data)
    assert g.nodes[0].kind == "future_kind"
    assert g.edges[0].kind == "FUTURE_EDGE"


def test_indexes_localize_by_target_and_file() -> None:
    g = build_source_graph(_sample_build())
    idx = g.to_dict()["indexes"]
    assert "target://libfoo" in idx["by_target"]
    assert any(k.startswith("header://") for k in idx["by_file"])


def test_indexes_cover_forward_looking_symbol_and_decl_kinds() -> None:
    # Phases 3-4 will emit binary_symbol / source_decl nodes; the index already
    # localizes by them so a finding can be traced once those land.
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://foo", kind="source_decl"))
    g.add_node(GraphNode(id="sym://_Z3foov", kind="binary_symbol"))
    g.add_edge(
        GraphEdge(
            src="decl://foo", dst="sym://_Z3foov", kind="SOURCE_DECL_MAPS_TO_SYMBOL"
        )
    )
    idx = g.indexes()
    assert "sym://_Z3foov" in idx["by_binary_symbol"]
    assert "decl://foo" in idx["by_source_decl"]


def test_to_dict_fills_graph_id_when_unset() -> None:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="x", kind="target"))
    assert g.graph_id == ""  # not finalized
    assert g.to_dict()["graph_id"].startswith("sha256:")


# ── Pack + CLI wiring ───────────────────────────────────────────────────────


def test_pack_round_trips_source_graph(tmp_path) -> None:
    pack = BuildSourcePack.empty(tmp_path / "p.evidence")
    pack.source_graph = build_source_graph(_sample_build())
    pack_io.write(pack)
    loaded = pack_io.load(tmp_path / "p.evidence")
    assert loaded.source_graph is not None
    assert loaded.source_graph.graph_id == pack.source_graph.graph_id


def test_pack_drops_stale_graph_when_recollected(tmp_path) -> None:
    root = tmp_path / "p.evidence"
    pack = BuildSourcePack.empty(root)
    pack.source_graph = build_source_graph(_sample_build())
    pack_io.write(pack)
    # Re-write without a graph: the stale file must be removed.
    pack2 = pack_io.load(root)
    pack2.source_graph = None
    pack_io.write(pack2)
    assert not (root / "graph" / "source_graph_summary.json").is_file()
    assert pack_io.load(root).source_graph is None


def _collect_graph_pack(
    tmp_path, name: str, *, two_units: bool = False, source_graph: str = "summary"
):
    """Build a BuildSourcePack the way the deleted `collect --compile-db ...
    --source-graph summary -o <dir>` command used to, via the still-live
    `_run_adapters`/`_collect_source_graph`/`_build_coverage` engine functions
    (orphaned from any CLI command but otherwise unchanged, ADR-043)."""
    import datetime as _dt

    from abicheck import __version__ as _abicheck_version
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.cli_buildsource_helpers import (
        _build_coverage,
        _collect_source_graph,
        _run_adapters,
    )

    src = tmp_path / f"{name}.cpp"
    src.write_text("int x(){return 1;}\n")
    entries = [
        {
            "directory": str(tmp_path),
            "file": str(src),
            "command": f"c++ -std=c++20 -fvisibility=hidden -c {src} -o {name}.o",
        }
    ]
    if two_units:
        src2 = tmp_path / f"{name}2.cpp"
        src2.write_text("int y(){return 2;}\n")
        entries.append(
            {
                "directory": str(tmp_path),
                "file": str(src2),
                "command": f"c++ -std=c++20 -c {src2} -o {name}2.o",
            }
        )
    cdb = tmp_path / f"{name}_cc.json"
    cdb.write_text(json.dumps(entries))

    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []
    _run_adapters(
        merged,
        extractors,
        compile_db=cdb,
        build_dir=None,
        cmake=False,
        ninja=False,
        ninja_compdb=None,
        bazel_cquery=None,
        bazel_aquery=None,
        make_dry_run=None,
        binary=None,
        read_compiler_record=False,
        build_system="generic",
        record_bazel_inputs=False,
        verbose=False,
    )
    has_build = bool(merged.compile_units or merged.targets)
    graph, graph_detail = _collect_source_graph(
        merged,
        extractors,
        source_graph=source_graph,
        changed_paths=(),
        kythe_entries=None,
        codeql_results=None,
        codeql_extends_results=None,
        surface=None,
        clang_bin="clang",
    )

    out = tmp_path / f"{name}.evidence"
    pack = BuildSourcePack.empty(
        out,
        abicheck_version=_abicheck_version,
        created_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    pack.manifest.extractors = extractors
    if has_build:
        pack.build_evidence = merged
    if graph is not None:
        pack.source_graph = graph
    pack.manifest.coverage = _build_coverage(
        merged, has_build, None, "", graph, graph_detail
    )
    pack_io.write(pack)
    return pack, out


def test_collect_evidence_summary_writes_graph_and_coverage(tmp_path) -> None:
    pack, out = _collect_graph_pack(tmp_path, "foo")
    assert (out / "graph" / "source_graph_summary.json").is_file()
    reloaded = pack_io.load(out)
    assert reloaded.source_graph is not None
    l5 = reloaded.manifest.coverage_for(DataLayer.L5_SOURCE_GRAPH)
    assert l5 is not None
    assert l5.status == CoverageStatus.PRESENT


def test_compare_graph_cli_reports_diff() -> None:
    # `graph compare` (deleted CLI command) was a thin wrapper over
    # `diff_source_graph` — exercise it directly.
    old = SourceGraphSummary()
    old.add_node(GraphNode(id="target://a", kind="target", label="a"))
    new = build_source_graph(_sample_build())

    delta = diff_source_graph(old, new)
    assert delta.changed
    assert len(delta.added_nodes) >= 1


def test_compare_graph_identical() -> None:
    g = build_source_graph(_sample_build())
    delta = diff_source_graph(g, g)
    assert not delta.changed


def test_compare_graph_missing_graph_errors(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    with pytest.raises(click.ClickException):
        _load_source_graph(tmp_path / "nope.json")


def test_compare_graph_accepts_pack_directories_and_shows_removals(tmp_path) -> None:
    # The richer pack as OLD and the smaller as NEW exercises the removed-node /
    # removed-edge branches of the structural diff.
    from abicheck.cli_graph import _load_source_graph

    big_pack, big_dir = _collect_graph_pack(tmp_path, "big", two_units=True)
    small_pack, small_dir = _collect_graph_pack(tmp_path, "small", two_units=False)
    old_graph = _load_source_graph(big_dir)
    new_graph = _load_source_graph(small_dir)
    delta = diff_source_graph(old_graph, new_graph)
    assert delta.removed_nodes or delta.removed_edges


def test_compare_graph_pack_without_graph_errors(tmp_path) -> None:
    # A pack collected without a source graph has no L5 graph → actionable error.
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    _pack, out = _collect_graph_pack(tmp_path, "nograph", source_graph="off")
    with pytest.raises(click.ClickException, match="no L5 source graph"):
        _load_source_graph(out)


def test_compare_graph_malformed_json_errors(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    with pytest.raises(click.ClickException, match="Cannot read source graph"):
        _load_source_graph(bad)


def test_compare_graph_non_object_json_errors(tmp_path) -> None:
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    arr = tmp_path / "arr.json"
    arr.write_text("[1, 2, 3]")
    with pytest.raises(click.ClickException, match="must contain a JSON object"):
        _load_source_graph(arr)


def test_compare_graph_rejects_non_graph_json_object(tmp_path) -> None:
    # An unrelated JSON object (e.g. a pack manifest) must fail with an
    # actionable error, not be read as an empty graph (CodeRabbit review).
    import click
    import pytest

    from abicheck.cli_graph import _load_source_graph

    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"build_source_pack_version": 1, "coverage": []}))
    with pytest.raises(click.ClickException, match="not a source graph summary"):
        _load_source_graph(manifest)


def test_collect_evidence_summary_without_build_is_partial(tmp_path) -> None:
    # --source-graph summary with no build adapter inputs yields an empty graph;
    # the L5 coverage row must read PARTIAL (ran, produced nothing), not PRESENT.
    from abicheck.buildsource.build_evidence import BuildEvidence
    from abicheck.buildsource.model import ExtractorRecord
    from abicheck.cli_buildsource_helpers import _build_coverage, _collect_source_graph

    merged = BuildEvidence()
    extractors: list[ExtractorRecord] = []
    graph, graph_detail = _collect_source_graph(
        merged,
        extractors,
        source_graph="summary",
        changed_paths=(),
        kythe_entries=None,
        codeql_results=None,
        codeql_extends_results=None,
        surface=None,
        clang_bin="clang",
    )
    coverage = _build_coverage(merged, False, None, "", graph, graph_detail)
    l5 = next(c for c in coverage if c.layer == DataLayer.L5_SOURCE_GRAPH.value)
    assert l5.status == CoverageStatus.PARTIAL
