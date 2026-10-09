# SPDX-License-Identifier: Apache-2.0
"""A failing header must not cost every other header its graph edges.

The header graph's clang pass used to be all-or-nothing: one header the
frontend rejected dropped the whole tree to a declaration-only graph.
``parse_header_groups_bisecting`` re-parses in halves; these tests state its
contract as invariants over generated failure sets, with an oracle (the set
of headers that fail *on their own*) that is independent of the bisection.
"""

from __future__ import annotations

import itertools
import random
from pathlib import Path

import pytest

from abicheck.buildsource.call_graph import CallEdge
from abicheck.buildsource.header_graph_ast_projection import (
    HeaderGraphAstProjection,
    merge_header_graph_ast_projections,
    parse_header_groups_bisecting,
)
from abicheck.buildsource.type_graph import TypeEdge


class _Boom(Exception):
    pass


def _parser(bad: set[str], calls: list[list[str]]):
    """A parse that fails iff the group contains a bad header (clang's rule:
    one hard error fails the whole translation unit)."""

    def parse(group: list[str]) -> HeaderGraphAstProjection:
        calls.append(list(group))
        if bad.intersection(group):
            raise _Boom(group)
        return HeaderGraphAstProjection(
            type_files={f"T_{h}": h for h in group},
            type_edges=[
                TypeEdge(src=f"d_{h}", dst=f"T_{h}", kind="DECL_HAS_TYPE")
                for h in group
            ],
            call_edges=[CallEdge(caller=f"d_{h}", callee="shared") for h in group],
            special_member_names=frozenset(f"C_{h}" for h in group),
        )

    return parse


def _cases():
    rng = random.Random(1234)
    # Exhaustive over small domains, then random larger ones.
    for n in range(2, 7):
        headers = [f"h{i}.h" for i in range(n)]
        for k in range(n + 1):
            for bad in itertools.combinations(headers, k):
                yield headers, set(bad)
    for _ in range(60):
        n = rng.randint(2, 120)
        headers = [f"h{i}.h" for i in range(n)]
        bad = set(rng.sample(headers, rng.randint(0, min(n, 6))))
        yield headers, bad


def test_bisection_recovers_exactly_the_headers_that_parse_alone() -> None:
    disagreements = []
    for headers, bad in _cases():
        calls: list[list[str]] = []
        parts, failed = parse_header_groups_bisecting(
            headers, _parser(bad, calls), (_Boom,)
        )
        merged = merge_header_graph_ast_projections(parts)
        good = [h for h in headers if h not in bad]
        # Oracle: a header is lost iff it fails on its own.
        if sorted(failed) != sorted(bad) or set(merged.type_files.values()) != set(
            good
        ):
            disagreements.append((len(headers), sorted(bad), sorted(failed)))
        # Every header is attempted in some group; none is parsed twice in a
        # successful group (no double counting).
        ok_groups = [g for g in calls if not bad.intersection(g)]
        flat = [h for g in ok_groups for h in g]
        assert len(flat) == len(set(flat))
        # Cost bound: never worse than ~2 parses per header.
        assert len(calls) <= 2 * len(headers)
    assert not disagreements, disagreements[:5]


def test_single_header_is_reported_failed_without_reparse() -> None:
    calls: list[list[str]] = []
    parts, failed = parse_header_groups_bisecting(
        ["a.h"], _parser({"a.h"}, calls), (_Boom,)
    )
    assert parts == [] and failed == ["a.h"] and calls == []


def test_unexpected_exception_type_propagates() -> None:
    def parse(group):
        raise KeyError("not a parse failure")

    with pytest.raises(KeyError):
        parse_header_groups_bisecting(["a.h", "b.h"], parse, (_Boom,))


def test_merge_is_order_independent_up_to_first_wins_and_dedups() -> None:
    e = TypeEdge(src="x", dst="y", kind="DECL_HAS_TYPE")
    c = CallEdge(caller="x", callee="z")
    p1 = HeaderGraphAstProjection(
        type_files={"A": "a.h"},
        type_edges=[e],
        call_edges=[c],
        entity_files={"x": "a.h"},
        special_member_names=frozenset({"C1"}),
    )
    p2 = HeaderGraphAstProjection(
        type_files={"A": "dup.h", "B": "b.h"},
        type_edges=[e],
        call_edges=[c],
        special_member_names=frozenset({"C2"}),
    )
    m = merge_header_graph_ast_projections([p1, p2])
    assert m.type_files == {"A": "a.h", "B": "b.h"}
    assert m.type_edges == [e] and m.call_edges == [c]
    assert m.entity_files == {"x": "a.h"}
    assert m.special_member_names == {"C1", "C2"}
    r = merge_header_graph_ast_projections([p2, p1])
    assert set(r.type_files) == set(m.type_files)
    assert set(r.type_edges) == set(m.type_edges)


def test_attach_keeps_edges_of_headers_that_parse(monkeypatch, tmp_path: Path) -> None:
    """Through the real acquisition entry point: the batch parse fails, the
    bisection recovers the good header, and the failure stays recorded."""
    import abicheck.extract.headers.clang.backend as clang_backend
    from abicheck.errors import SnapshotError
    from abicheck.service_header_graph_attach import acquire_header_graph_ast

    good = tmp_path / "good.h"
    bad = tmp_path / "bad.h"
    good.write_text("struct Good { int x; };\n")
    bad.write_text("#error nope\n")

    def fake_dump(headers, *_a, **_k):
        if any(Path(h).name == "bad.h" for h in headers):
            raise SnapshotError("bad.h:1: error: nope")
        return (
            {
                "kind": "TranslationUnitDecl",
                "inner": [
                    {
                        "kind": "CXXRecordDecl",
                        "name": "Good",
                        "tagUsed": "struct",
                        "completeDefinition": True,
                        "id": "0x1",
                        "loc": {"file": str(good), "line": 1},
                        "inner": [],
                    }
                ],
            },
            "c++",
            False,
        )

    monkeypatch.setattr(clang_backend, "clang_header_dump", fake_dump)
    got = acquire_header_graph_ast([good, bad], [], "c++", None)
    assert got.projection is not None
    assert "Good" in got.projection.type_files
    assert got.ast_failure is not None and "1 of 2" in got.ast_failure


def test_attach_reports_every_failed_header_when_none_recover(
    monkeypatch, tmp_path: Path
) -> None:
    import abicheck.extract.headers.clang.backend as clang_backend
    from abicheck.errors import SnapshotError
    from abicheck.service_header_graph_attach import acquire_header_graph_ast

    paths = [tmp_path / f"h{i}.h" for i in range(3)]
    for p in paths:
        p.write_text("#error nope\n")

    def always_fail(headers, *_a, **_k):
        raise SnapshotError("boom")

    monkeypatch.setattr(clang_backend, "clang_header_dump", always_fail)
    got = acquire_header_graph_ast(paths, [], "c++", None)
    assert got.projection is None
    assert got.ast_failure is not None
    assert "3 of 3" in got.ast_failure
    assert "no header could be recovered" in got.ast_failure
    for p in paths:
        assert p.name in got.ast_failure


def test_recovery_parses_run_with_streaming_prune_suppressed(
    monkeypatch, tmp_path: Path
) -> None:
    import abicheck.extract.headers.clang.backend as clang_backend
    from abicheck.errors import SnapshotError
    from abicheck.extract.headers.clang.streaming import streaming_prune_suppressed
    from abicheck.service_header_graph_attach import acquire_header_graph_ast

    monkeypatch.setenv("ABICHECK_CLANG_PRUNE_DEPENDENCY_DECLS", "1")
    good, bad = tmp_path / "good.h", tmp_path / "bad.h"
    good.write_text("int g;\n")
    bad.write_text("#error\n")
    prune_seen: list[bool] = []

    def fake_dump(headers, *_a, **_k):
        prune_seen.append(streaming_prune_suppressed())
        if any(Path(h).name == "bad.h" for h in headers):
            raise SnapshotError("bad")
        return {"kind": "TranslationUnitDecl", "inner": []}, "c++", False

    monkeypatch.setattr(clang_backend, "clang_header_dump", fake_dump)
    acquire_header_graph_ast([good, bad], [], "c++", None)
    assert len(prune_seen) == 3  # batch + two halves
    assert all(prune_seen)
