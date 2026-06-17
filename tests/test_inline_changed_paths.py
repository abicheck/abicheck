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
    inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=("src/a.cpp",),
    )
    # Only the changed TU was parsed for call edges.
    assert seen_sources == ["src/a.cpp"]


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
    inline._build_inline_graph(
        merged,
        surface=None,
        with_call_graph=True,
        clang_bin="clang",
        extractors=[],
        changed_paths=("include/foo.h",),
    )
    # Header change → all TUs parsed for call edges.
    assert sorted(seen_sources) == ["src/a.cpp", "src/b.cpp"]
