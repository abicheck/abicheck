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

"""Inline-collection changed-path threading (ADR-035 D7 POI focusing, G19.3).

Verifies that an explicit changed-path set threaded by the `scan` orchestrator
keeps the inline L4 replay at `changed` scope (narrow, POI-focused) instead of
falling back to a broad replay — and that an empty set still falls back to the
non-empty `headers-only` public-API surface (ADR-035 P3: never the full-target,
== s6, cost). No compiler needed: the extractor and the replay
driver are stubbed so only the scope-selection decision is exercised.
"""

from __future__ import annotations

from pathlib import Path

import abicheck.buildsource.inline as inline
from abicheck.buildsource import source_replay
from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
from abicheck.buildsource.source_abi import SourceAbiSurface


class _FakeExtractor:
    def available(self) -> bool:
        return True


def _build_with_one_unit() -> BuildEvidence:
    return BuildEvidence(
        compile_units=[CompileUnit(id="cu://src/foo.cpp", source="src/foo.cpp")]
    )


def _capture_scope(monkeypatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def _fake_replay(build, extractor, *, scope="target", changed_paths=(), **kw):
        captured["scope"] = scope
        captured["changed_paths"] = tuple(changed_paths)
        return SourceAbiSurface(), []

    monkeypatch.setattr(
        inline, "_make_source_extractor", lambda *a, **k: (_FakeExtractor(), "fake")
    )
    monkeypatch.setattr(source_replay, "run_source_replay", _fake_replay)
    return captured


def test_cache_stats_threaded_into_surface_coverage(monkeypatch, tmp_path: Path):
    # ADR-035 P5: an L4 run with a cache dir records its hit/miss tally into the
    # surface coverage so the live L4 coverage row can report it (not only
    # `scan --estimate`). No compiler needed — the replay is stubbed.
    def _fake_replay(build, extractor, *, scope="target", changed_paths=(), **kw):
        # Exercise the cache so hit_rate is non-None (one recorded miss), which
        # also drives the cache-hit-rate diagnostic branch.
        cache = kw.get("cache")
        if cache is not None:
            cache.get("nonexistent-key")  # → one miss
        return SourceAbiSurface(), []

    monkeypatch.setattr(
        inline, "_make_source_extractor", lambda *a, **k: (_FakeExtractor(), "fake")
    )
    monkeypatch.setattr(source_replay, "run_source_replay", _fake_replay)
    surface, _ = inline._run_inline_source_abi(
        tmp_path,
        _build_with_one_unit(),
        [],
        extractor="clang",
        scope="changed",
        clang_bin="clang",
        changed_paths=("src/foo.cpp",),
        source_abi_cache_dir=tmp_path / "l4cache",
    )
    assert surface is not None
    assert surface.coverage["cache_hits"] == 0
    assert surface.coverage["cache_misses"] == 1


def test_changed_paths_keep_changed_scope(monkeypatch, tmp_path: Path):
    captured = _capture_scope(monkeypatch)
    inline._run_inline_source_abi(
        tmp_path,
        _build_with_one_unit(),
        [],
        extractor="clang",
        scope="changed",
        clang_bin="clang",
        changed_paths=("src/foo.cpp",),
    )
    # An explicit changed set is honoured: the replay stays narrow (D7 focusing).
    assert captured["scope"] == "changed"
    assert captured["changed_paths"] == ("src/foo.cpp",)


def test_changed_scope_in_collect_pack_falls_back_without_paths(monkeypatch, tmp_path):
    captured = _capture_scope(monkeypatch)
    inline.collect_inline_pack(
        sources=tmp_path,
        build_info=None,
        base_build=_build_with_one_unit(),
        scope="changed",
        layers=("L3", "L4"),
        changed_paths=(),
    )
    # No changed set → fall back to the non-empty public-API surface
    # (headers-only), NOT a whole-target replay: an unseeded s5/pr run must not
    # silently pay full-target (== s6) replay cost (ADR-035 P3 cliff).
    assert captured["scope"] == "headers-only"


def test_changed_scope_in_collect_pack_narrows_with_paths(monkeypatch, tmp_path):
    captured = _capture_scope(monkeypatch)
    inline.collect_inline_pack(
        sources=tmp_path,
        build_info=None,
        base_build=_build_with_one_unit(),
        scope="changed",
        layers=("L3", "L4"),
        changed_paths=("src/foo.cpp",),
    )
    # An explicit changed set narrows the inline replay to the affected TUs.
    assert captured["scope"] == "changed"
    assert captured["changed_paths"] == ("src/foo.cpp",)


# --------------------------------------------------------------------------- #
# call-graph wiring into the inline L5 graph (ADR-035 D4 reviewer request)
# --------------------------------------------------------------------------- #


def test_inline_graph_folds_call_edges_for_l4_l5_mode(monkeypatch):
    # When L4 + L5 are both collected (a semantic source mode), the inline graph
    # build folds a call graph so the decl-dependency cross-checks are reachable
    # from `scan`. The clang extractor is stubbed (no compiler needed).
    from abicheck.buildsource import call_graph
    from abicheck.buildsource.call_graph import CallEdge

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build) -> list[CallEdge]:
            return [CallEdge("caller", "callee", "direct", "exact")]

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)
    merged = _build_with_one_unit()
    graph = inline._build_inline_graph(
        merged, surface=None, with_call_graph=True, clang_bin="clang", extractors=[]
    )
    assert graph is not None
    assert any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)
    # Unscoped (whole compile DB) run: confirmed pass coverage is recorded
    # (ADR-041 P0 slice 2/3; sixth Codex review — only an unscoped run may).
    assert graph.extractor_passes["call_graph"] is True


def test_inline_graph_no_call_edges_when_clang_absent(monkeypatch):
    # Best-effort: a missing clang++ records a failed extractor row and leaves the
    # graph without call edges — never raises.
    from abicheck.buildsource import call_graph

    class _Unavailable:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return False

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _Unavailable)
    merged = _build_with_one_unit()
    rows: list = []
    graph = inline._build_inline_graph(
        merged, surface=None, with_call_graph=True, clang_bin="clang", extractors=rows
    )
    assert graph is not None
    assert not any(e.kind == "DECL_CALLS_DECL" for e in graph.edges)
    assert any(r.name == "call_graph:clang" and r.status == "failed" for r in rows)


# --------------------------------------------------------------------------- #
# PR1: fold_source_edges augments the graph; the call/type-graph replay
# passes still always run (ADR-038 C.10 / latest-main Clang plugin review)
# --------------------------------------------------------------------------- #


def test_inline_graph_folds_source_edges_and_still_runs_replay(monkeypatch):
    """fold_source_edges (inside build_source_graph) adds the surface's
    source_edges to the graph, but the separate call/type-graph replay
    passes must still run unconditionally -- even when source_edges rolls
    up as confirmed-complete.

    An earlier revision skipped the replay in that case as a performance
    optimization, but the raw source_edges wire format carries only bare
    endpoint identities, not the dst_file/project-file provenance
    fold_call_graph/fold_type_graph attach via `project_files`
    (`defined_in_project`). Without that provenance,
    crosscheck.public_to_internal_dependency cannot classify an unannotated
    callee/referenced node as internal, so skipping the replay would
    silently miss a public-to-internal dependency addition (Codex review on
    PR #560). This test locks in that the replay is never skipped this way."""
    from abicheck.buildsource import call_graph
    from abicheck.buildsource.call_graph import CallEdge

    called = {"call": False}

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            called["call"] = True
            return True

        def extract_from_build(self, build) -> list[CallEdge]:
            return [CallEdge("caller", "callee", "direct", "exact")]

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)
    merged = _build_with_one_unit()
    surface = SourceAbiSurface(library="libfoo.so", target_id="target://libfoo")
    surface.source_edges = [{"edge": "DECL_CALLS_DECL", "src": "a", "dst": "b"}]
    surface.coverage["fact_family_states"] = {"source_edges": "complete"}
    graph = inline._build_inline_graph(
        merged, surface=surface, with_call_graph=True, clang_bin="clang", extractors=[]
    )
    assert graph is not None
    # fold_source_edges's edge is present...
    assert any(e.src == "decl://a" and e.dst == "decl://b" for e in graph.edges)
    # ...and the replay still ran (not skipped) and folded its own edge too.
    assert called["call"] is True
    assert any(
        e.src == "decl://caller" and e.dst == "decl://callee" for e in graph.edges
    )
    assert graph.extractor_passes["call_graph"] is True


def test_inline_call_graph_scoped_to_changed_tus(monkeypatch):
    # A PR/--since scan scopes the call-graph pass to the changed compile units —
    # parsing every TU of a large compile DB would defeat the targeted PR cost
    # model (ADR-035 D7 / Codex review).
    from abicheck.buildsource import call_graph
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.call_graph import CallEdge

    seen_sources: list[str] = []

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build) -> list[CallEdge]:
            seen_sources.extend(cu.source for cu in build.compile_units)
            return []

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    graph = inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=("src/a.cpp",),
    )
    # Only the changed TU was parsed for call edges.
    assert seen_sources == ["src/a.cpp"]
    # Narrowed (changed-path-scoped) run: does NOT claim confirmed pass
    # coverage — it only examined a subset of TUs, so "found nothing" there
    # says nothing about the rest of the codebase (sixth Codex review).
    assert graph is not None
    assert "call_graph" not in graph.extractor_passes
    # But the narrowed run's own scope is recorded, so a comparison against a
    # confirmed full pass on the other side can discount this run's edges as
    # non-representative coverage (eleventh Codex review).
    assert graph.narrowed_passes["call_graph"] is True
    # And the *actual* scope (not just the boolean) is recorded, so two
    # narrowed runs are only trusted against each other when identically
    # scoped (fourteenth Codex review).
    assert graph.narrowed_scope["call_graph"] == frozenset({"src/a.cpp"})


def test_inline_call_graph_scoped_with_diagnostics_does_not_confirm_narrowed_pass(
    monkeypatch,
):
    # Fifteenth Codex review: narrowed_passes now doubles as "this narrowed
    # scope's zero-edge family is trustworthy" (not just "discount this run's
    # edges elsewhere"), so a narrowed run that hit a per-TU diagnostic (a
    # clang crash/timeout/degenerate AST inside the scope) must NOT claim
    # narrowed_passes — the scope was not examined cleanly, mirroring the
    # seventh review's rationale for the full-pass case.
    from abicheck.buildsource import call_graph
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.call_graph import CallEdge

    class _FlakyCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = ["clang timed out on src/a.cpp"]

        def available(self) -> bool:
            return True

        def extract_from_build(self, build) -> list[CallEdge]:
            return []

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FlakyCallExtractor)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    graph = inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=("src/a.cpp",),
    )
    assert graph is not None
    assert "call_graph" not in graph.extractor_passes
    assert "call_graph" not in graph.narrowed_passes
    assert "call_graph" not in graph.narrowed_scope
    # A narrowed run with diagnostics is even less trustworthy than a clean
    # narrowed one, so it also lands in degraded_passes (sixteenth Codex
    # review) — its surviving edges must not vouch for coverage either.
    assert graph.degraded_passes["call_graph"] is True


def test_inline_call_graph_scoped_no_diagnostics_does_not_mark_degraded(monkeypatch):
    # Contrast case: a clean narrowed run (no diagnostics) must NOT be marked
    # degraded — only narrowed_passes/narrowed_scope, per the fourteenth/
    # fifteenth review.
    from abicheck.buildsource import call_graph
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.call_graph import CallEdge

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build) -> list[CallEdge]:
            return []

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    graph = inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=("src/a.cpp",),
    )
    assert graph is not None
    assert graph.narrowed_passes["call_graph"] is True
    assert "call_graph" not in graph.degraded_passes


def test_inline_call_graph_header_change_fans_out_to_all_tus(monkeypatch):
    # A changed *header* has no compile unit of its own; the call-graph pass must
    # fan out to all TUs (like the L4 selector) rather than match cu.source and
    # drop everything — else public_to_internal_dependency is skipped exactly for
    # header-only API changes (Codex review).
    from abicheck.buildsource import call_graph
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit
    from abicheck.buildsource.call_graph import CallEdge

    seen_sources: list[str] = []

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build) -> list[CallEdge]:
            seen_sources.extend(cu.source for cu in build.compile_units)
            return []

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    graph = inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=("include/foo.h",),
    )
    # Header change → all TUs parsed for call edges.
    assert sorted(seen_sources) == ["src/a.cpp", "src/b.cpp"]
    # Not narrowed (fanned out to the whole compile DB despite changed_paths
    # being set) — confirmed pass coverage is still recorded.
    assert graph is not None
    assert graph.extractor_passes["call_graph"] is True
    assert "call_graph" not in graph.narrowed_passes


def _fake_call_extractor(monkeypatch, seen_sources: list[str]):
    from abicheck.buildsource import call_graph
    from abicheck.buildsource.call_graph import CallEdge

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build) -> list[CallEdge]:
            seen_sources.extend(cu.source for cu in build.compile_units)
            return []

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)


def test_inline_unseeded_call_graph_scoped_to_l4_units(monkeypatch):
    # Gap-1: an unseeded run (no changed_paths) must scope the call-graph pass to
    # the same compile-unit set the L4 replay used (headers-only) rather than
    # fanning out to the whole compile DB — that asymmetry made seedless
    # `--depth source` cost scale with the whole tree.
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    seen_sources: list[str] = []
    _fake_call_extractor(monkeypatch, seen_sources)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
            CompileUnit(id="cu://src/c.cpp", source="src/c.cpp"),
        ]
    )
    # The L4 replay selected only a.cpp (the headers-only representative subset).
    l4_units = [CompileUnit(id="cu://src/a.cpp", source="src/a.cpp")]
    rows: list = []
    graph = inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=rows,
        changed_paths=(),
        call_graph_units=l4_units,
    )
    # Only the L4-selected TU is parsed — not all three.
    assert seen_sources == ["src/a.cpp"]
    row = next(r for r in rows if r.name == "call_graph:clang")
    assert "headers-only scope" in row.detail
    assert "from 1 compile unit" in row.detail
    # Narrowed (headers-only scope, matching L4): no confirmed pass coverage.
    assert graph is not None
    assert "call_graph" not in graph.extractor_passes
    assert graph.narrowed_passes["call_graph"] is True
    assert graph.narrowed_scope["call_graph"] == frozenset({"src/a.cpp"})


def test_inline_unseeded_call_graph_broad_without_scoped_units(monkeypatch):
    # Without scoped units (e.g. --depth full / s6, scope=full) the unseeded pass
    # keeps the broad contract: every TU is parsed.
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    seen_sources: list[str] = []
    _fake_call_extractor(monkeypatch, seen_sources)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    graph = inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=(),
        call_graph_units=None,
    )
    assert sorted(seen_sources) == ["src/a.cpp", "src/b.cpp"]
    # Fully unscoped: confirmed pass coverage is recorded.
    assert graph is not None
    assert graph.extractor_passes["call_graph"] is True
    assert "call_graph" not in graph.narrowed_passes


def test_run_inline_source_abi_no_sources_returns_empty_selection():
    # No --sources tree: returns (None, []) so the caller keeps a broad
    # call-graph pass rather than scoping to an empty (unavailable) selection.
    surface, units = inline._run_inline_source_abi(
        None,
        _build_with_one_unit(),
        [],
        extractor="clang",
        scope="headers-only",
        clang_bin="clang",
    )
    assert surface is None
    assert units == []


def test_run_inline_source_abi_no_compile_units_returns_empty_selection():
    # A source tree but no L3 compile units: nothing to replay/select.
    surface, units = inline._run_inline_source_abi(
        Path("/no-such-tree/x"),
        BuildEvidence(),
        [],
        extractor="clang",
        scope="headers-only",
        clang_bin="clang",
    )
    assert surface is None
    assert units == []


def test_run_inline_source_abi_hybrid_extractor_skipped_not_silently_clang(
    monkeypatch, tmp_path
):
    # Codex review: L4 source-ABI replay has no dual-backend hybrid extractor
    # (unlike the L2 header-AST snapshot dumper_hybrid.py provides) --
    # `--ast-frontend hybrid` reaches here unchanged via the shared
    # compile_context_options flag, and `_make_source_extractor` would
    # otherwise silently pick clang while the ExtractorRecord's name still
    # claims "source_abi:hybrid", implying both backends ran when only one
    # did. It must be recorded as skipped instead, even with real L3 compile
    # units present (not merely falling through the "no compile units" path).
    called = False

    def _fail_if_called(*_a, **_k):
        nonlocal called
        called = True
        raise AssertionError("_make_source_extractor must not run for hybrid")

    monkeypatch.setattr(inline, "_make_source_extractor", _fail_if_called)
    extractors: list = []
    surface, units = inline._run_inline_source_abi(
        tmp_path,
        _build_with_one_unit(),
        extractors,
        extractor="hybrid",
        scope="headers-only",
        clang_bin="clang",
    )
    assert not called
    assert surface is None
    assert units == []
    assert len(extractors) == 1
    assert extractors[0].name == "source_abi:hybrid"
    assert extractors[0].status == "skipped"


def test_run_inline_source_abi_returns_selected_units(monkeypatch, tmp_path):
    # A real build + stubbed replay: select_compile_units runs for real and its
    # result is returned alongside the surface (fed to the call-graph scope).
    _capture_scope(monkeypatch)
    surface, units = inline._run_inline_source_abi(
        tmp_path,
        _build_with_one_unit(),
        [],
        extractor="clang",
        scope="headers-only",
        clang_bin="clang",
    )
    assert surface is not None
    assert [cu.source for cu in units] == ["src/foo.cpp"]


def test_run_inline_source_abi_extractor_unavailable_returns_empty_selection(
    monkeypatch, tmp_path
):
    class _Unavailable:
        def available(self) -> bool:
            return False

    monkeypatch.setattr(
        inline, "_make_source_extractor", lambda *a, **k: (_Unavailable(), "fake")
    )
    surface, units = inline._run_inline_source_abi(
        tmp_path,
        _build_with_one_unit(),
        [],
        extractor="clang",
        scope="headers-only",
        clang_bin="clang",
    )
    assert surface is not None  # empty SourceAbiSurface, not None
    assert units == []


# ── ADR-041 P0: type-graph folding alongside the call graph ─────────────────


def test_inline_graph_has_type_edges_when_clang_available(monkeypatch):
    from abicheck.buildsource import call_graph, type_graph
    from abicheck.buildsource.type_graph import TypeEdge

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build):
            return []

    class _FakeTypeExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []
            self.last_jobs = 0
            self.last_elapsed_s = 0.0

        def available(self) -> bool:
            return True

        def extract_from_build(self, build):
            return [TypeEdge("ns::Widget", "ns::Base", "TYPE_INHERITS")]

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)
    monkeypatch.setattr(type_graph, "ClangTypeGraphExtractor", _FakeTypeExtractor)
    merged = _build_with_one_unit()
    graph = inline._build_inline_graph(
        merged, surface=None, with_call_graph=True, clang_bin="clang", extractors=[]
    )
    assert graph is not None
    assert any(e.kind == "TYPE_INHERITS" for e in graph.edges)


def test_inline_graph_type_pass_marks_role_coverage_matrix(monkeypatch):
    # ADR-046 D3: a confirmed full type-graph pass earns the finer
    # per-(kind, role) extractor_passes keys alongside the family key.
    from abicheck.buildsource import call_graph, type_graph
    from abicheck.buildsource.inline_graph_fold import role_pass_covered
    from abicheck.buildsource.type_graph import TypeEdge

    class _FakeCallExtractor:
        def __init__(self, *a: object, **k: object) -> None:
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build: BuildEvidence) -> list[object]:
            return []

    class _FakeTypeExtractor:
        def __init__(self, *a: object, **k: object) -> None:
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []
            self.last_jobs = 0
            self.last_elapsed_s = 0.0

        def available(self) -> bool:
            return True

        def extract_from_build(self, build: BuildEvidence) -> list[TypeEdge]:
            return [TypeEdge("ns::Widget", "ns::Base", "TYPE_INHERITS")]

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)
    monkeypatch.setattr(type_graph, "ClangTypeGraphExtractor", _FakeTypeExtractor)
    merged = _build_with_one_unit()
    graph = inline._build_inline_graph(
        merged, surface=None, with_call_graph=True, clang_bin="clang", extractors=[]
    )
    assert graph is not None
    assert graph.extractor_passes["type_graph"] is True
    assert graph.extractor_passes["type_graph:TYPE_INHERITS:base"] is True
    assert graph.extractor_passes["type_graph:TYPE_HAS_FIELD_TYPE:field"] is True
    assert graph.extractor_passes["type_graph:TYPE_HAS_FIELD_TYPE:alias"] is True
    assert graph.extractor_passes["type_graph:DECL_HAS_TYPE:var"] is True
    assert graph.extractor_passes["type_graph:DECL_HAS_TYPE:return"] is True
    assert graph.extractor_passes["type_graph:DECL_HAS_TYPE:param"] is True
    assert graph.extractor_passes["type_graph:DECL_REFERENCES_DECL:ref"] is True
    # role_pass_covered() reads the finer key directly...
    assert role_pass_covered(graph, "type_graph", "DECL_HAS_TYPE", "param") is True
    # ...and falls back to the family-level flag for a role this matrix
    # doesn't track (e.g. a made-up role) or a made-up pass name.
    assert (
        role_pass_covered(graph, "type_graph", "DECL_HAS_TYPE", "made_up_role") is True
    )
    assert role_pass_covered(graph, "made_up_pass", "DECL_HAS_TYPE", "param") is False


def test_inline_graph_no_type_edges_when_clang_absent(monkeypatch):
    # Best-effort: a missing clang++ records a failed extractor row and leaves the
    # graph without type edges — never raises.
    from abicheck.buildsource import call_graph, type_graph

    class _Unavailable:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return False

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _Unavailable)
    monkeypatch.setattr(type_graph, "ClangTypeGraphExtractor", _Unavailable)
    merged = _build_with_one_unit()
    rows: list = []
    graph = inline._build_inline_graph(
        merged, surface=None, with_call_graph=True, clang_bin="clang", extractors=rows
    )
    assert graph is not None
    assert not any(e.kind == "TYPE_INHERITS" for e in graph.edges)
    assert any(r.name == "type_graph:clang" and r.status == "failed" for r in rows)


def test_inline_type_graph_scoped_to_changed_tus(monkeypatch):
    # Mirrors the call-graph scoping: a PR/--since scan narrows the type-graph
    # pass to the changed compile units, not the whole compile DB.
    from abicheck.buildsource import call_graph, type_graph
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    seen_sources: list[str] = []

    class _FakeCallExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build):
            return []

    class _FakeTypeExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []
            self.last_jobs = 0
            self.last_elapsed_s = 0.0

        def available(self) -> bool:
            return True

        def extract_from_build(self, build):
            seen_sources.extend(cu.source for cu in build.compile_units)
            return []

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeCallExtractor)
    monkeypatch.setattr(type_graph, "ClangTypeGraphExtractor", _FakeTypeExtractor)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    graph = inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=("src/a.cpp",),
    )
    assert seen_sources == ["src/a.cpp"]
    assert graph is not None
    assert "type_graph" not in graph.extractor_passes
    assert graph.narrowed_passes["type_graph"] is True
    assert graph.narrowed_scope["type_graph"] == frozenset({"src/a.cpp"})
    # ADR-046 D3: the narrowed family flag also earns the finer per-(kind,
    # role) narrowed_passes keys.
    assert graph.narrowed_passes["type_graph:DECL_HAS_TYPE:param"] is True
    assert "type_graph:DECL_HAS_TYPE:param" not in graph.extractor_passes


def test_inline_include_graph_scoped_to_changed_tus(monkeypatch):
    # Mirrors the call/type-graph scoping tests above: a PR/--since scan
    # narrows the (now-automatic, ADR-041 header-only-graph addendum
    # follow-up) include-graph pass to the changed compile units too, not
    # the whole compile DB — and, since it went through a live clang -M
    # extractor here (no recorded build-tool inputs), records the same
    # narrowed-pass coverage bookkeeping call/type graph already do.
    from abicheck.buildsource import call_graph, include_graph, type_graph
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    seen_sources: list[str] = []

    class _FakeNoEdgeExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []
            self.last_jobs = 0
            self.last_elapsed_s = 0.0

        def available(self) -> bool:
            return True

        def extract_from_build(self, build):
            return []

    class _FakeIncludeExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []

        def available(self) -> bool:
            return True

        def extract_from_build(self, build):
            seen_sources.extend(cu.source for cu in build.compile_units)
            return {}

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeNoEdgeExtractor)
    monkeypatch.setattr(type_graph, "ClangTypeGraphExtractor", _FakeNoEdgeExtractor)
    monkeypatch.setattr(include_graph, "ClangIncludeExtractor", _FakeIncludeExtractor)
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(id="cu://src/a.cpp", source="src/a.cpp"),
            CompileUnit(id="cu://src/b.cpp", source="src/b.cpp"),
        ]
    )
    graph = inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=("src/a.cpp",),
    )
    assert seen_sources == ["src/a.cpp"]
    assert graph is not None
    assert "include_graph" not in graph.extractor_passes
    assert graph.narrowed_passes["include_graph"] is True
    assert graph.narrowed_scope["include_graph"] == frozenset({"src/a.cpp"})


def test_inline_include_graph_prefers_recorded_inputs_over_live_clang(monkeypatch):
    # When compile units already carry recorded build-tool inputs (Bazel
    # aquery, etc.), the include-graph fold must use them directly rather
    # than shelling out to clang -M — cheaper, and works for hermetic builds
    # a live clang invocation cannot reach (Codex review context: the
    # record_bazel_inputs wiring in cli_buildsource.py exists precisely so
    # this path is taken instead of the live-clang one).
    from abicheck.buildsource import call_graph, include_graph, type_graph
    from abicheck.buildsource.build_evidence import BuildEvidence, CompileUnit

    class _FakeNoEdgeExtractor:
        def __init__(self, *a, **k):
            self.clang_bin = "clang++"
            self.diagnostics: list[str] = []
            self.last_jobs = 0
            self.last_elapsed_s = 0.0

        def available(self) -> bool:
            return True

        def extract_from_build(self, build):
            return []

    class _ExplodingIncludeExtractor:
        def available(self) -> bool:
            raise AssertionError("must not shell out when inputs were recorded")

    monkeypatch.setattr(call_graph, "ClangCallGraphExtractor", _FakeNoEdgeExtractor)
    monkeypatch.setattr(type_graph, "ClangTypeGraphExtractor", _FakeNoEdgeExtractor)
    monkeypatch.setattr(
        include_graph, "ClangIncludeExtractor", _ExplodingIncludeExtractor
    )
    merged = BuildEvidence(
        compile_units=[
            CompileUnit(
                id="cu://src/a.cpp",
                source="src/a.cpp",
                input_files=["src/a.cpp", "src/a.h"],
            ),
        ]
    )
    graph = inline._build_inline_graph(
        merged, surface=None, with_call_graph=True, clang_bin="clang", extractors=[]
    )
    assert graph is not None
    assert any(e.kind == "COMPILE_UNIT_INCLUDES_FILE" for e in graph.edges)
