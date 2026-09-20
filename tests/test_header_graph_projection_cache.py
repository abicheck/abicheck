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

"""The projection cache's contract: a warm run must be indistinguishable.

The bug class this states as an executable invariant: *a cache that serves a
derived artifact must serve exactly what recomputing would have produced, and
must refuse to serve anything it cannot prove it understands.* Both halves
matter and they fail differently — serving the wrong projection is silently
wrong evidence (fewer graph edges, fewer findings, no error anywhere), while
refusing too eagerly is only a slow run.

So the tests below check equality of the *result* over generated inputs, and
separately assert the **mechanism**: that a warm run really skipped the parse
rather than quietly recomputing and agreeing. Per AGENTS.md's differential
rule, every equality assertion here would pass just as happily against an
implementation with no cache at all.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
from _clang_ast_cache_isolation import _reset_ast_memo

from abicheck.buildsource.call_graph import CallEdge
from abicheck.buildsource.header_graph_ast_projection import (
    HeaderGraphAstProjection,
    project_header_graph_ast,
)
from abicheck.buildsource.header_graph_projection_cache import (
    PROJECTION_CACHE_SCHEMA,
    decode_projection,
    encode_projection,
    load_cached_projection,
    projection_sidecar_path,
    store_cached_projection,
)
from abicheck.buildsource.type_graph import TypeEdge

PUBLIC_HEADER = "/proj/include/pub.h"
PRIVATE_HEADER = "/proj/include/detail/impl.h"


def _loc(file: str) -> dict[str, Any]:
    return {"file": file, "line": 1, "col": 1}


def _record(name: str, *, file: str, inner: list[dict] | None = None) -> dict:
    return {
        "kind": "CXXRecordDecl",
        "name": name,
        "loc": _loc(file),
        "inner": inner or [],
    }


def _field(name: str, qual_type: str, *, init: dict | None = None) -> dict:
    d: dict[str, Any] = {
        "kind": "FieldDecl",
        "name": name,
        "type": {"qualType": qual_type},
    }
    if init is not None:
        d["inner"] = [init]
    return d


def _function(
    name: str, *, file: str, mangled: str | None = None, body: list[dict] | None = None
) -> dict:
    inner: list[dict] = []
    if body is not None:
        inner.append({"kind": "CompoundStmt", "inner": body})
    d: dict[str, Any] = {
        "kind": "FunctionDecl",
        "name": name,
        "loc": _loc(file),
        "type": {"qualType": "void ()"},
        "inner": inner,
    }
    if mangled:
        d["mangledName"] = mangled
    return d


def _tu(*decls: dict) -> dict:
    return {"kind": "TranslationUnitDecl", "inner": list(decls)}


#: Structurally distinct ASTs, so the round-trip is exercised over every
#: projection member rather than one shape — including the empty case, which
#: is the one most likely to round-trip by accident.
AST_CASES: dict[str, dict] = {
    "empty": _tu(),
    "one_record": _tu(_record("Public", file=PUBLIC_HEADER)),
    "private_field_type": _tu(
        {
            "kind": "NamespaceDecl",
            "name": "detail",
            "inner": [_record("Impl", file=PRIVATE_HEADER)],
        },
        _record("Public", file=PUBLIC_HEADER, inner=[_field("p", "detail::Impl *")]),
    ),
    "field_initializer_reference": _tu(
        {
            "kind": "VarDecl",
            "name": "k",
            "loc": _loc(PRIVATE_HEADER),
            "type": {"qualType": "int"},
        },
        _record(
            "Widget",
            file=PUBLIC_HEADER,
            inner=[
                _field(
                    "x",
                    "int",
                    init={
                        "kind": "DeclRefExpr",
                        "referencedDecl": {
                            "kind": "VarDecl",
                            "name": "k",
                            "loc": _loc(PRIVATE_HEADER),
                        },
                    },
                )
            ],
        ),
    ),
    "call_edge": _tu(
        _function("helper", file=PRIVATE_HEADER, mangled="_ZN6helperEv"),
        _function(
            "entry",
            file=PUBLIC_HEADER,
            mangled="_Z5entryv",
            body=[
                {
                    "kind": "CallExpr",
                    "inner": [
                        {
                            "kind": "DeclRefExpr",
                            "referencedDecl": _function("helper", file=PRIVATE_HEADER),
                        }
                    ],
                }
            ],
        ),
    ),
}


def _as_tuple(p: HeaderGraphAstProjection) -> tuple:
    return (
        tuple(sorted(p.type_files.items())),
        tuple(sorted(p.entity_files.items())),
        tuple(sorted(dataclasses.astuple(e) for e in p.type_edges)),
        tuple(sorted(dataclasses.astuple(e) for e in p.call_edges)),
    )


class TestRoundTrip:
    @pytest.mark.parametrize("name", sorted(AST_CASES))
    def test_a_projection_survives_the_cache_unchanged(self, name: str) -> None:
        original = project_header_graph_ast(AST_CASES[name])
        restored = decode_projection(encode_projection(original))
        assert restored is not None
        assert _as_tuple(restored) == _as_tuple(original)

    def test_the_cases_are_not_all_the_same_projection(self) -> None:
        """Vacuity guard: equality above means nothing if every case is empty."""
        shapes = {
            n: _as_tuple(project_header_graph_ast(a)) for n, a in AST_CASES.items()
        }
        assert len(set(shapes.values())) > 1
        assert any(
            p.type_edges
            for p in (project_header_graph_ast(a) for a in AST_CASES.values())
        )
        assert any(
            p.call_edges
            for p in (project_header_graph_ast(a) for a in AST_CASES.values())
        )

    def test_round_trip_through_a_real_file(self, tmp_path: Path) -> None:
        ast_entry = tmp_path / "deadbeef.json"
        ast_entry.write_text("{}")
        original = project_header_graph_ast(AST_CASES["call_edge"])
        store_cached_projection(ast_entry, original)
        restored = load_cached_projection(ast_entry)
        assert restored is not None
        assert _as_tuple(restored) == _as_tuple(original)


class TestItRefusesWhatItCannotTrust:
    """Every rejection must be a miss, never a wrong answer or a crash."""

    def test_a_missing_entry_is_a_miss(self, tmp_path: Path) -> None:
        assert load_cached_projection(tmp_path / "nothing.json") is None

    @pytest.mark.parametrize(
        "blob", ["", "not json", "[]", "null", '{"schema": "other/1"}']
    )
    def test_an_unusable_entry_is_a_miss(self, blob: str, tmp_path: Path) -> None:
        entry = tmp_path / "x.json"
        projection_sidecar_path(entry).write_text(blob)
        assert load_cached_projection(entry) is None

    def test_an_unusable_entry_is_discarded_not_kept(self, tmp_path: Path) -> None:
        """Otherwise a corrupt sidecar costs every future run a re-parse."""
        entry = tmp_path / "x.json"
        sidecar = projection_sidecar_path(entry)
        sidecar.write_text("garbage")
        assert load_cached_projection(entry) is None
        assert not sidecar.exists()

    @pytest.mark.parametrize(
        "mutate",
        [
            pytest.param(lambda d: d.update(schema="abicheck-x/99"), id="schema"),
            pytest.param(
                lambda d: d.update(type_edge_fields=["src", "dst"]), id="type_fields"
            ),
            pytest.param(
                lambda d: d.update(call_edge_fields=["caller"]), id="call_fields"
            ),
        ],
    )
    def test_a_schema_this_build_does_not_share_is_refused(self, mutate) -> None:
        """The self-describing half: a reordered or extended edge dataclass
        must invalidate its own cache, with no version number to remember."""
        doc = json.loads(
            encode_projection(project_header_graph_ast(AST_CASES["call_edge"]))
        )
        mutate(doc)
        assert decode_projection(json.dumps(doc)) is None

    def test_todays_document_is_accepted(self) -> None:
        """The complement — without it, a decoder that refused everything
        would pass every test above while disabling the cache entirely."""
        blob = encode_projection(project_header_graph_ast(AST_CASES["call_edge"]))
        assert json.loads(blob)["schema"] == PROJECTION_CACHE_SCHEMA
        assert decode_projection(blob) is not None

    def test_the_field_lists_are_read_from_the_dataclasses(self) -> None:
        """A hand-maintained copy would drift silently; this is what makes
        the rejection above automatic rather than remembered."""
        doc = json.loads(encode_projection(HeaderGraphAstProjection()))
        assert doc["type_edge_fields"] == [f.name for f in dataclasses.fields(TypeEdge)]
        assert doc["call_edge_fields"] == [f.name for f in dataclasses.fields(CallEdge)]


class TestSidecarIdentity:
    def test_the_sidecar_belongs_to_one_ast_entry(self, tmp_path: Path) -> None:
        a = projection_sidecar_path(tmp_path / "aaa.json")
        b = projection_sidecar_path(tmp_path / "bbb.json")
        assert a != b
        assert a.parent == (tmp_path / "aaa.json").parent

    def test_two_keys_do_not_share_a_projection(self, tmp_path: Path) -> None:
        """The correctness property the sidecar naming exists for: a
        different AST cache entry is a different projection, always."""
        first, second = tmp_path / "aaa.json", tmp_path / "bbb.json"
        store_cached_projection(first, project_header_graph_ast(AST_CASES["call_edge"]))
        assert load_cached_projection(second) is None
        restored = load_cached_projection(first)
        assert restored is not None and restored.call_edges


class TestTheWarmRunReallySkipsTheParse:
    """Prove the mechanism, not the agreement (AGENTS.md's differential rule).

    Every equality assertion above holds for an implementation that ignores
    the cache and recomputes — which is the behaviour this change exists to
    remove, and which would show up only as a gigabyte of RSS nobody
    measured. So these watch the AST itself: it must be read on the cold run
    and never touched on the warm one.
    """

    @staticmethod
    def _install(monkeypatch, tmp_path: Path, ast: dict) -> dict[str, int]:
        """Point the attach at a fake clang whose AST reads are counted."""
        import abicheck.dumper_cache as dumper_cache
        import abicheck.service_header_graph_attach as attach_mod

        entry = tmp_path / "cache" / "abcdef.json"
        entry.parent.mkdir(parents=True, exist_ok=True)
        counts = {"ast_reads": 0, "projections": 0}

        real_load = dumper_cache.load_cached_ast

        def fake_clang_header_dump(*_a: Any, **_k: Any):
            # Route through the real cache layer so the derived-artifact
            # scope is exercised exactly as production exercises it.
            got = real_load("k", "clang", entry, memoize=False)
            if got is not None:
                return got, None, True
            counts["ast_reads"] += 1
            # Write the real document, as a real clang run would: a later
            # fallback must find the AST itself here, not a placeholder.
            entry.write_text(json.dumps(ast))
            return ast, None, True

        import abicheck.buildsource.header_graph_ast_projection as proj_mod

        real_project = proj_mod.project_header_graph_ast

        def counting_project(root):
            counts["projections"] += 1
            return real_project(root)

        monkeypatch.setattr(
            "abicheck.dumper._clang_header_dump", fake_clang_header_dump
        )
        monkeypatch.setattr(proj_mod, "project_header_graph_ast", counting_project)
        monkeypatch.setattr(
            attach_mod, "expand_header_inputs", lambda headers: list(headers)
        )
        monkeypatch.setattr(
            attach_mod, "resolve_inferred_header_roots", lambda *a, **k: ([], [])
        )
        return counts

    @staticmethod
    def _attach(snapshot):
        import abicheck.service_header_graph_attach as attach_mod

        return attach_mod._attach_header_graph(
            snapshot,
            header_graph=True,
            header_graph_includes=False,
            headers=[Path(PUBLIC_HEADER)],
            includes=[],
            lang=None,
            compile=None,
            public_headers=None,
            public_header_dirs=None,
        )

    @staticmethod
    def _snapshot():
        from abicheck.model import AbiSnapshot

        return AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[],
            variables=[],
            types=[],
            enums=[],
        )

    def _graph_shape(self, snap) -> tuple:
        g = snap.build_source.source_graph
        return (
            tuple(
                sorted((n.id, n.kind, tuple(sorted(n.attrs.items()))) for n in g.nodes)
            ),
            tuple(sorted((e.src, e.dst, e.kind) for e in g.edges)),
            tuple(sorted(g.extractor_passes.items())),
        )

    def test_the_second_run_reads_no_ast_and_builds_the_same_graph(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        counts = self._install(monkeypatch, tmp_path, AST_CASES["call_edge"])

        cold = self._graph_shape(self._attach(self._snapshot()))
        assert counts == {"ast_reads": 1, "projections": 1}, "cold run must parse"

        warm = self._graph_shape(self._attach(self._snapshot()))
        # The mechanism: no AST was produced and nothing was projected.
        assert counts == {"ast_reads": 1, "projections": 1}, (
            "the warm run parsed an AST again — the projection cache did not engage"
        )
        # And the result is indistinguishable.
        assert warm == cold

    def test_a_discarded_sidecar_falls_back_to_parsing(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The cache must be optional in both directions: losing it costs a
        re-parse, never a wrong or missing graph."""
        counts = self._install(monkeypatch, tmp_path, AST_CASES["call_edge"])
        cold = self._graph_shape(self._attach(self._snapshot()))

        for sidecar in (tmp_path / "cache").glob("*.projection.json"):
            sidecar.unlink()

        again = self._graph_shape(self._attach(self._snapshot()))
        assert counts["projections"] == 2, "a missing sidecar must re-project"
        assert again == cold


class TestThePathsWhereNoAstIsAcquired:
    """The cache must not make a degraded attach worse than no cache at all.

    `_attach_header_graph` reaches its projection step on paths where the
    clang acquisition never ran: no header resolved to a real file, or the
    parse raised and was caught. The cache's bookkeeping is created *inside*
    that acquisition block, so a first version read it unconditionally
    afterwards and raised `UnboundLocalError` on both paths — turning a
    documented graceful degradation (ADR-028 D3: never abort the dump) into
    a crash. Caught by the existing `test_service_unit.py` wiring tests;
    stated here as the invariant rather than left to them, because they are
    about header wiring and would not obviously be the place someone looks
    when adding the next thing to that block.
    """

    @staticmethod
    def _attach(snapshot, headers):
        import abicheck.service_header_graph_attach as attach_mod

        return attach_mod._attach_header_graph(
            snapshot,
            header_graph=True,
            header_graph_includes=False,
            headers=headers,
            includes=[],
            lang=None,
            compile=None,
            public_headers=None,
            public_header_dirs=None,
        )

    def _snapshot(self):
        from abicheck.model import AbiSnapshot

        return AbiSnapshot(
            library="libfoo.so.1",
            version="1.0",
            functions=[],
            variables=[],
            types=[],
            enums=[],
        )

    def test_a_header_that_does_not_exist_still_attaches_a_graph(self) -> None:
        """The exact path the first version crashed on, with no mocks.

        A relative, nonexistent header makes `expand_header_inputs` raise
        *before* the clang acquisition block is entered, so nothing inside it
        — including the cache's own bookkeeping — is ever bound. Deliberately
        unmocked: patching the expansion away would describe the crash
        without reproducing how a caller reaches it.
        """
        snap = self._attach(self._snapshot(), [Path("no-such-header.h")])
        assert snap.build_source is not None
        assert snap.build_source.source_graph is not None

    @pytest.mark.parametrize("exc_name", ["SnapshotError", "ValidationError"])
    def test_a_failed_clang_acquisition_still_attaches_a_graph(
        self, monkeypatch, exc_name: str
    ) -> None:
        """ADR-028 D3: a failed parse degrades to a declaration-only graph,
        it does not abort the dump — and must not now crash instead."""
        import abicheck.errors as errors
        import abicheck.service_header_graph_attach as attach_mod

        exc = getattr(errors, exc_name)

        def raising(*_a: Any, **_k: Any):
            raise exc("clang is unavailable")

        monkeypatch.setattr(
            attach_mod, "expand_header_inputs", lambda headers: list(headers)
        )
        monkeypatch.setattr(
            attach_mod, "resolve_inferred_header_roots", lambda *a, **k: ([], [])
        )
        monkeypatch.setattr("abicheck.dumper._clang_header_dump", raising)

        snap = self._attach(self._snapshot(), [Path(PUBLIC_HEADER)])
        assert snap.build_source is not None
        assert snap.build_source.source_graph is not None


@pytest.mark.skipif(
    shutil.which("clang++") is None,
    reason="the real-dependency lane needs a live clang++",
)
@pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="clang L2 header backend is ELF/Linux-scoped (see test_clang_header_backend_integration.py)",
)
class TestAgainstRealClang:
    """The same invariant against a **live** ``clang -ast-dump=json``.

    Every other test in this file hands the cache a hand-built AST dict. That
    is precisely the shortcut ADR-059 §12 names: a test that constructs the
    dependency's output itself cannot fail on a disagreement between what the
    dependency really emits and what this code assumes it emits — the
    projection's field set, the shape of a ``loc``/``file`` entry, the node
    kinds a call edge is recovered from. A fixture drifts silently; clang does
    not.

    So this drives the real thing, through the real attach entry point, over a
    real STL-bearing header (the scale where the AST is large enough for the
    projection to be a meaningfully different object, which is the whole point
    of the cache), and asserts both halves: the graph a warm run builds is
    identical to the cold one's, **and** the warm run projected nothing.
    """

    _HEADER = """
#pragma once
#include <string>
#include <vector>
#include <memory>

namespace realdep {

struct Point {
    int x;
    int y;
};

class Shape {
public:
    virtual ~Shape();
    virtual double area() const = 0;
    std::string label() const;
private:
    std::vector<Point> pts_;
};

class Box : public Shape {
public:
    double area() const override;
    std::shared_ptr<Shape> clone() const;
};

std::vector<Point> collect(const Shape& s);
double total_area(const std::vector<std::unique_ptr<Shape>>& shapes);

}  // namespace realdep
"""

    @staticmethod
    def _snapshot():
        from abicheck.model import AbiSnapshot

        return AbiSnapshot(
            library="librealdep.so.1",
            version="1.0",
            functions=[],
            variables=[],
            types=[],
            enums=[],
        )

    @staticmethod
    def _attach(snapshot, header: Path):
        import abicheck.service_header_graph_attach as attach_mod

        return attach_mod._attach_header_graph(
            snapshot,
            header_graph=True,
            header_graph_includes=False,
            headers=[header],
            includes=[],
            lang="c++",
            compile=None,
            public_headers=[header],
            public_header_dirs=None,
        )

    @staticmethod
    def _shape(snap) -> tuple:
        g = snap.build_source.source_graph
        return (
            tuple(sorted((n.id, n.kind) for n in g.nodes)),
            tuple(sorted((e.src, e.dst, e.kind) for e in g.edges)),
            tuple(sorted(g.extractor_passes.items())),
        )

    def test_a_warm_run_over_a_real_clang_ast_skips_the_parse(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import abicheck.buildsource.header_graph_ast_projection as proj_mod

        header = tmp_path / "realdep.h"
        header.write_text(self._HEADER)

        # One shared AST cache root across both runs -- that sharing is the
        # mechanism under test, not an oversight (contrast
        # `_isolate_ast_cache`'s per-configuration roots, which exist for
        # differential tests whose two sides must *not* share). It is still
        # isolated from the developer's real `~/.cache`, so the cold run is
        # genuinely cold whatever ran before.
        cache_root = tmp_path / "xdg"
        cache_root.mkdir()
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))

        projections = {"n": 0}
        real_project = proj_mod.project_header_graph_ast

        def counting_project(root):
            projections["n"] += 1
            return real_project(root)

        monkeypatch.setattr(proj_mod, "project_header_graph_ast", counting_project)

        cold = self._shape(self._attach(self._snapshot(), header))
        assert projections["n"] == 1, (
            "the cold run did not project a real clang AST -- clang produced no "
            "tree, so this test would assert nothing about the cache"
        )
        # The cold run must have produced something worth caching: an empty
        # projection would make the warm comparison vacuous.
        sidecars = sorted(cache_root.rglob("*.projection.json"))
        assert sidecars, "the cold run wrote no projection sidecar"
        assert cold[0], "the cold run built no graph nodes from the real AST"

        # A disk-cache hit alone does not force a reparse; the in-process AST
        # memo has to go too, or the second run never reaches the cache layer.
        _reset_ast_memo()

        warm = self._shape(self._attach(self._snapshot(), header))
        assert projections["n"] == 1, (
            "the warm run projected a real clang AST again -- the projection "
            "cache did not engage on the real dependency's output"
        )
        assert warm == cold

    def test_the_cached_projection_round_trips_a_real_clang_ast_exactly(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """What clang really emits must survive the codec unchanged.

        The round-trip tests above prove the codec is faithful to *fixtures*.
        This proves it against the dependency's own output, which is where a
        field the projection carries but the codec forgets would actually
        show up -- with a real projection, not a two-edge hand-built one.
        """
        from abicheck.dumper import _clang_header_dump

        header = tmp_path / "realdep.h"
        header.write_text(self._HEADER)
        cache_root = tmp_path / "xdg"
        cache_root.mkdir()
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_root))

        ast_root, _kind, _forced = _clang_header_dump(
            [header], [], lang="c++", memoize=False
        )
        assert ast_root, "live clang produced no AST"

        projection = project_header_graph_ast(ast_root)

        # Vacuity guard on the oracle, per AGENTS.md's matrix-test rule. The
        # codec encodes each edge positionally against a field list derived
        # from the dataclass, so a *dropped trailing field* round-trips
        # perfectly whenever the data happens to leave it at its default --
        # which is the tautology ADR-059 §12 describes, and which a
        # hand-built two-edge fixture walks straight into. Requiring two
        # distinct observed values per field means the equality below
        # actually pins every field. (This header's real projection supplies
        # them comfortably: measured 15,267 type edges and 2,881 call edges,
        # every field 2-5,704 distinct values.)
        for edges in (projection.type_edges, projection.call_edges):
            assert edges, "the real AST projected no edges of one kind"
            for f in dataclasses.fields(edges[0]):
                distinct = {getattr(e, f.name) for e in edges}
                assert len(distinct) > 1, (
                    f"every {type(edges[0]).__name__}.{f.name} in the real "
                    "projection holds one value, so the round-trip below "
                    "would pass for a codec that drops this field"
                )

        restored = decode_projection(encode_projection(projection))
        assert restored is not None
        # `_as_tuple`, not `==`: `TypeEdge.resolution` is declared
        # `compare=False`, so dataclass equality is structurally blind to a
        # codec that drops it -- verified by mutation (truncating the encoded
        # field list by one passed an `==` oracle outright).
        assert _as_tuple(restored) == _as_tuple(projection)
