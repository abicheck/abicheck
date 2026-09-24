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
        counts = {"ast_reads": 0, "projections": 0, "streams": 0}

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
        import abicheck.buildsource.header_graph_ast_stream as stream_mod

        real_stream = stream_mod.project_header_graph_ast_file

        def counting_stream(path):
            counts["streams"] += 1
            return real_stream(path)

        monkeypatch.setattr(proj_mod, "project_header_graph_ast", counting_project)
        monkeypatch.setattr(
            stream_mod, "project_header_graph_ast_file", counting_stream
        )
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
        assert counts == {"ast_reads": 1, "projections": 1, "streams": 0}, (
            "cold run must parse"
        )

        warm = self._graph_shape(self._attach(self._snapshot()))
        # The mechanism: no AST was produced and nothing was projected.
        assert counts == {"ast_reads": 1, "projections": 1, "streams": 0}, (
            "the warm run parsed an AST again — the projection cache did not engage"
        )
        # And the result is indistinguishable.
        assert warm == cold

    def test_a_discarded_sidecar_falls_back_to_re_deriving(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """The cache must be optional in both directions: losing it costs a
        re-derivation, never a wrong or missing graph.

        *Which* re-derivation is itself the assertion. The AST cache entry
        still exists, so the second run streams it
        (`header_graph_ast_stream`) rather than building the whole tree --
        which is the point of the streaming path: a lost sidecar costs a
        second pass over a file, not a second gigabyte of dicts. Counting
        both mechanisms separately is what stops this reading as a pass if
        the stream silently stopped being reachable and the tree parse
        quietly took over again.
        """
        # Pin the size threshold off: this test is about *which* mechanism
        # re-derives a lost sidecar, and the fixture AST is a few hundred
        # bytes, so at the production threshold it would never stream and
        # the assertion below would be about the fallback instead.
        monkeypatch.setenv("ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB", "0")
        counts = self._install(monkeypatch, tmp_path, AST_CASES["call_edge"])
        cold = self._graph_shape(self._attach(self._snapshot()))
        assert (counts["projections"], counts["streams"]) == (1, 0), (
            "the cold run here has no AST document to stream yet (the fake "
            "clang returns a tree directly), so it must project one"
        )

        for sidecar in (tmp_path / "cache").glob("*.projection.json"):
            sidecar.unlink()

        again = self._graph_shape(self._attach(self._snapshot()))
        assert counts["streams"] == 1, "a missing sidecar must re-derive"
        assert counts["projections"] == 1, (
            "re-deriving from an AST document on disk must stream it, never "
            "fall back to building the whole tree again"
        )
        assert again == cold

    @staticmethod
    def _primary_clang_pass(tmp_path: Path, ast: dict) -> None:
        """What a primary ``--ast-frontend clang`` pass leaves behind: the AST
        cache entry on disk and the parsed tree in this thread's memo slot."""
        import abicheck.dumper_cache as dumper_cache

        entry = tmp_path / "cache" / "abcdef.json"
        entry.write_text(json.dumps(ast))
        dumper_cache.store_cached_ast("k", "clang", json.loads(json.dumps(ast)))

    @pytest.mark.parametrize("threshold_mib", ["0", "4096"])
    def test_a_clang_frontend_memo_handoff_warms_and_then_hits_the_cache(
        self, monkeypatch, tmp_path: Path, threshold_mib: str
    ) -> None:
        """The clang-frontend path: the attach receives the primary pass's
        tree through the in-process memo, not through a disk read.

        It used to take that tree without ever being offered the cache
        entry, so it projected cold on every run and never stored a sidecar
        -- measured on oneDAL at 6.6 s per attach, every run. Now the first
        run projects the tree it was handed and stores the sidecar; the next
        run takes the sidecar and projects nothing. At no threshold may it
        stream the document off disk while the parsed tree is in hand.
        """
        monkeypatch.setenv("ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB", threshold_mib)
        import abicheck.dumper_cache as dumper_cache

        ast = AST_CASES["call_edge"]
        counts = self._install(monkeypatch, tmp_path, ast)
        try:
            self._primary_clang_pass(tmp_path, ast)
            first = self._graph_shape(self._attach(self._snapshot()))
            assert counts == {"ast_reads": 0, "projections": 1, "streams": 0}
            assert list((tmp_path / "cache").glob("*.projection.json")), (
                "the memo-handoff run must store a sidecar, or the cache never warms"
            )

            self._primary_clang_pass(tmp_path, ast)
            second = self._graph_shape(self._attach(self._snapshot()))
            assert counts == {"ast_reads": 0, "projections": 1, "streams": 0}, (
                "a warm clang-frontend run projected again"
            )
            assert second == first
        finally:
            dumper_cache._ast_memo_slot.set(None)

    def test_a_memo_handoff_warm_hit_equals_a_disk_path_cold_run(
        self, monkeypatch, tmp_path: Path
    ) -> None:
        """Independent oracle: the graph from a sidecar written on the memo
        path equals the graph a plain disk-path cold run builds."""
        import abicheck.dumper_cache as dumper_cache

        for name, ast in AST_CASES.items():
            base = tmp_path / name
            (base / "cache").mkdir(parents=True)
            with monkeypatch.context() as mp:
                self._install(mp, base, ast)
                reference = self._graph_shape(self._attach(self._snapshot()))
            with monkeypatch.context() as mp:
                other = tmp_path / (name + "-memo")
                (other / "cache").mkdir(parents=True)
                self._install(mp, other, ast)
                try:
                    self._primary_clang_pass(other, ast)
                    self._attach(self._snapshot())
                    self._primary_clang_pass(other, ast)
                    warm = self._graph_shape(self._attach(self._snapshot()))
                finally:
                    dumper_cache._ast_memo_slot.set(None)
            assert warm == reference, name

    @pytest.mark.parametrize(
        ("threshold_mib", "expect_stream"),
        [("0", True), ("4096", False)],
    )
    def test_only_a_large_enough_document_is_streamed(
        self, monkeypatch, tmp_path: Path, threshold_mib: str, expect_stream: bool
    ) -> None:
        """Streaming trades CPU for memory, so it must not run where there
        is no memory to save.

        Measured, the two ends are 100x apart: this repository's own
        header-graph perf fixtures produce 0.2-2.5 MiB documents, where the
        whole document and tree together are a few MiB, while the real case
        is 263 MiB. Streaming the small one cost 44-71% of attach wall time
        for nothing, which the PR-vs-base attach gate correctly rejected.

        Both sides are driven over the *same* document by moving the
        threshold rather than the input, so this tests the decision and not
        two different ASTs. Each side asserts which mechanism actually ran:
        the two paths produce the identical projection by construction, so
        the results alone cannot tell them apart.
        """
        monkeypatch.setenv("ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB", threshold_mib)
        counts = self._install(monkeypatch, tmp_path, AST_CASES["call_edge"])

        cold = self._graph_shape(self._attach(self._snapshot()))
        # The fake clang hands back a tree directly, so the stream is only
        # reachable on a second run, where the AST entry exists on disk.
        for sidecar in (tmp_path / "cache").glob("*.projection.json"):
            sidecar.unlink()
        again = self._graph_shape(self._attach(self._snapshot()))

        assert again == cold, "the two paths must agree whichever ran"
        if expect_stream:
            assert counts["streams"] == 1, "a large-enough document must stream"
        else:
            assert counts["streams"] == 0, (
                "a document below the threshold must not stream -- that is "
                "CPU spent with no memory saved"
            )
            assert counts["projections"] == 2, "it must re-project instead"


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

    @pytest.mark.parametrize(
        ("label", "ast"),
        [
            ("inner is a number", {"kind": "TranslationUnitDecl", "inner": 1}),
            (
                "a child's inner is a number",
                {
                    "kind": "TranslationUnitDecl",
                    "inner": [{"kind": "FunctionDecl", "name": "f", "inner": 7}],
                },
            ),
            (
                "a child's inner is a string",
                {
                    "kind": "TranslationUnitDecl",
                    "inner": [{"kind": "CXXRecordDecl", "name": "R", "inner": "x"}],
                },
            ),
            ("inner is a mapping", {"kind": "TranslationUnitDecl", "inner": {"a": 1}}),
        ],
    )
    def test_a_readable_but_misshapen_ast_still_attaches_a_graph(
        self, monkeypatch, label: str, ast: dict
    ) -> None:
        """A corrupt AST cache entry must cost the graph, not the dump.

        The readers walk a tree whose shape they trust -- `for child in
        node.get("inner", []) or []` raises `TypeError` when `inner` is a
        number -- and that is reachable from a cache file that decoded fine
        but holds the wrong shape. Uncontained, it escaped the projection
        step and aborted the whole dump, which is the failure ADR-028 D3
        names directly.

        Parametrized over several *independently-chosen* misshapen trees,
        not only the one reported: the bug class is "a shape the readers
        assume, violated anywhere in the walk", so a single fixture would
        foreclose exactly one node position.
        """
        import abicheck.service_header_graph_attach as attach_mod

        monkeypatch.setattr(
            attach_mod, "expand_header_inputs", lambda headers: list(headers)
        )
        monkeypatch.setattr(
            attach_mod, "resolve_inferred_header_roots", lambda *a, **k: ([], [])
        )
        monkeypatch.setattr(
            "abicheck.dumper._clang_header_dump",
            lambda *a, **k: (ast, None, True),
        )

        snap = self._attach(self._snapshot(), [Path(PUBLIC_HEADER)])
        assert snap.build_source is not None
        assert snap.build_source.source_graph is not None

    def test_the_misshapen_asts_really_do_break_the_readers(self) -> None:
        """Vacuity guard: each fixture above must actually raise.

        If a shape stopped reaching the walk, the containment test would
        pass while proving nothing about containment.
        """
        from abicheck.buildsource.header_graph_ast_projection import (
            project_header_graph_ast,
        )

        for ast in (
            {"kind": "TranslationUnitDecl", "inner": 1},
            {
                "kind": "TranslationUnitDecl",
                "inner": [{"kind": "FunctionDecl", "name": "f", "inner": 7}],
            },
        ):
            with pytest.raises((TypeError, AttributeError)):
                project_header_graph_ast(ast)


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

        import abicheck.buildsource.header_graph_ast_stream as stream_mod

        # Both ways a projection can be derived, counted apart. A single
        # combined counter would let the cold run's streaming path silently
        # revert to a whole-tree parse -- the exact regression this file's
        # memory work exists to prevent -- while this test still passed.
        projections = {"n": 0}
        streams = {"n": 0}
        real_project = proj_mod.project_header_graph_ast
        real_stream = stream_mod.project_header_graph_ast_file

        def counting_project(root):
            projections["n"] += 1
            return real_project(root)

        def counting_stream(path):
            streams["n"] += 1
            return real_stream(path)

        monkeypatch.setattr(proj_mod, "project_header_graph_ast", counting_project)
        monkeypatch.setattr(
            stream_mod, "project_header_graph_ast_file", counting_stream
        )
        # Pinned, not left to the fixture's size: whether this header's AST
        # clears the production threshold depends on the host's standard
        # library, and this test's claim is about the cache, not about
        # which side of the threshold a given libstdc++ lands on.
        monkeypatch.setenv("ABICHECK_HEADER_GRAPH_STREAM_MIN_MIB", "0")

        cold = self._shape(self._attach(self._snapshot(), header))
        assert streams["n"] == 1, (
            "the cold run did not stream a real clang AST document -- so this "
            "test would assert nothing about either the cache or the stream"
        )
        assert projections["n"] == 0, (
            "the cold run built the whole tree: the streaming path did not "
            "engage on a real clang run, which is where its whole saving is"
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
        assert (projections["n"], streams["n"]) == (0, 1), (
            "the warm run re-derived the projection -- the projection cache "
            "did not engage on the real dependency's output"
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


class TestTheCacheNeverRaises:
    """Every failure is a re-parse, stated as an invariant over injected faults.

    The bug class, from `manifest_caching`: a best-effort cache must not be
    able to turn its own failure into the run's failure (ADR-028 D3 —
    a header-graph dump degrades, never aborts). This file already covered
    *content* the codec cannot trust; it did not cover the **filesystem
    operations** around it, and a review found the gap: `read_text(encoding=
    "utf-8")` raises `UnicodeDecodeError`, which is a `ValueError` and not an
    `OSError`, so a sidecar holding invalid UTF-8 escaped and aborted the
    dump.

    A test pinned to that one exception on that one call would foreclose only
    the input the reviewer named. So this enumerates the whole small domain
    instead — every filesystem call either function makes × every exception
    it can plausibly see — which is what catches the *next* unguarded call
    (the `unlink` in the write path's own error handler was unguarded too,
    and no `UnicodeDecodeError` test would have said so).
    """

    #: The ordinary `OSError`s a full disk, a read-only mount, a sandbox or
    #: Windows file locking give. Every call below can raise any of these.
    _OS_FAULTS = [
        pytest.param(PermissionError(13, "Permission denied"), id="eacces"),
        pytest.param(OSError(28, "No space left on device"), id="enospc"),
        pytest.param(OSError(30, "Read-only file system"), id="erofs"),
        pytest.param(IsADirectoryError(21, "Is a directory"), id="eisdir"),
        pytest.param(OSError(5, "Input/output error"), id="eio"),
    ]

    #: The reviewed fault, deliberately **not** in the list above: decoding
    #: is what `read_text` does and no other call here performs, so pairing
    #: it with `unlink`/`mkdir`/`write_text` would assert that the module
    #: guards against something a filesystem cannot produce. Writing this as
    #: a cross product is how the first version of this test failed four
    #: cases for a fault none of those calls can raise -- a matrix has to
    #: enumerate *plausible* faults per call, not every pairing.
    _DECODE_FAULT = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    #: Every filesystem call the two entry points make, each with the faults
    #: reachable through it. Spelled out rather than discovered, so adding a
    #: call to the module without adding it here leaves the omission visible
    #: in the diff.
    _READ_CALLS = [
        pytest.param("read_text", _DECODE_FAULT, id="read_text-invalid-utf8"),
        *[
            pytest.param("read_text", f.values[0], id=f"read_text-{f.id}")
            for f in _OS_FAULTS
        ],
        *[pytest.param("unlink", f.values[0], id=f"unlink-{f.id}") for f in _OS_FAULTS],
    ]
    _WRITE_CALLS = [
        # `_OS_FAULTS` is the outer loop deliberately: in a class body only
        # the outermost iterable of a comprehension is evaluated in class
        # scope, so a nested `for f in _OS_FAULTS` is an `F821`.
        pytest.param(call, f.values[0], id=f"{call}-{f.id}")
        for f in _OS_FAULTS
        for call in ("mkdir", "write_text", "unlink")
    ]

    @staticmethod
    def _projection() -> HeaderGraphAstProjection:
        return project_header_graph_ast(AST_CASES["call_edge"])

    @pytest.mark.parametrize(("call", "fault"), _READ_CALLS)
    def test_loading_never_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        call: str,
        fault: Exception,
    ) -> None:
        entry = tmp_path / "abc.json"
        # A *rejected* body, so the unlink path is reached too -- with a
        # valid body, injecting into `unlink` would assert nothing.
        projection_sidecar_path(entry).write_text("not a projection")

        def boom(*_a: Any, **_k: Any):
            raise fault

        monkeypatch.setattr(Path, call, boom)
        assert load_cached_projection(entry) is None

    @pytest.mark.parametrize(("call", "fault"), _WRITE_CALLS)
    def test_storing_never_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        call: str,
        fault: Exception,
    ) -> None:
        def boom(*_a: Any, **_k: Any):
            raise fault

        monkeypatch.setattr(Path, call, boom)
        # Returns None on success too, so the claim is "does not raise".
        assert (
            store_cached_projection(tmp_path / "abc.json", self._projection()) is None
        )

    @pytest.mark.parametrize("fault", _OS_FAULTS)
    def test_a_failed_write_that_also_fails_to_clean_up_never_raises(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: Exception
    ) -> None:
        """The compound case, which single-call injection cannot reach.

        The write path's cleanup runs in precisely the conditions that made
        the write fail, so both failing at once is the realistic case, not a
        contrived one -- and it is the case a bare `unlink` in an `except`
        block gets wrong.
        """

        def boom_write(*_a: Any, **_k: Any):
            raise OSError(28, "No space left on device")

        def boom_unlink(*_a: Any, **_k: Any):
            raise fault

        monkeypatch.setattr(Path, "write_text", boom_write)
        monkeypatch.setattr(Path, "unlink", boom_unlink)
        assert (
            store_cached_projection(tmp_path / "abc.json", self._projection()) is None
        )

    def test_a_real_invalid_utf8_sidecar_is_a_miss_and_is_evicted(
        self, tmp_path: Path
    ) -> None:
        """The reviewed case end to end, with real bytes and no injection.

        Fault injection proves the handler covers the exception; only real
        bytes prove `read_text` actually raises it here, which is the half a
        mock cannot establish (AGENTS.md's real-dependency rule, applied to
        the filesystem).
        """
        entry = tmp_path / "abc.json"
        sidecar = projection_sidecar_path(entry)
        sidecar.write_bytes(
            b'{"schema": "abicheck-header-graph-projection/1", \xff\xfe}'
        )

        assert load_cached_projection(entry) is None
        assert not sidecar.exists(), "an undecodable sidecar must not be kept"

        # And the cache still works afterwards: eviction, not poisoning.
        store_cached_projection(entry, self._projection())
        restored = load_cached_projection(entry)
        assert restored is not None
        assert _as_tuple(restored) == _as_tuple(self._projection())
