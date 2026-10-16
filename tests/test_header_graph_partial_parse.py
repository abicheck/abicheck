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


def test_everything_blamed_reports_all_without_parsing() -> None:
    calls: list[list[str]] = []
    parts, failed = parse_header_groups_bisecting(
        ["a.h", "b.h"],
        _parser({"a.h", "b.h"}, calls),
        (_Boom,),
        known_failed=["b.h", "a.h"],
    )
    assert parts == [] and failed == ["a.h", "b.h"] and calls == []


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


def test_diagnostics_blamed_headers_cost_one_reparse_and_never_change_the_result() -> (
    None
):
    """With the failed batch's own diagnostics naming the bad headers, the rest
    parse in ONE call; when the diagnostics name only some of them, the bisect
    still finds the others. Oracle as above: lost iff it fails on its own."""
    rng = random.Random(99)
    for headers, bad in _cases():
        if not bad or len(bad) == len(headers):
            continue
        blamed_sets = [set(bad), set(rng.sample(sorted(bad), rng.randint(1, len(bad))))]
        for blamed in blamed_sets:
            calls: list[list[str]] = []
            parts, failed = parse_header_groups_bisecting(
                headers,
                _parser(bad, calls),
                (_Boom,),
                known_failed=[h for h in headers if h in blamed],
            )
            merged = merge_header_graph_ast_projections(parts)
            assert sorted(failed) == sorted(bad)
            assert set(merged.type_files.values()) == set(headers) - bad
            if blamed == bad:
                assert len(calls) == 1


@pytest.mark.integration
@pytest.mark.parametrize("bad_names", [("b.h",), ("a.h", "d.h"), ("c.h",)])
def test_real_clang_diagnostics_blame_exactly_the_failing_headers(
    tmp_path, bad_names
) -> None:
    """Against real clang: the failed batch's diagnostics name the headers that
    fail alone (direct ``#error`` and a failing private include alike), so the
    rest re-parses in one call."""
    import shutil

    from abicheck.buildsource.header_graph_ast_projection import (
        project_header_graph_ast,
    )
    from abicheck.errors import SnapshotError
    from abicheck.extract.headers.clang.backend import clang_header_dump
    from abicheck.extract.unparseable_header_fallback import (
        attribute_failing_headers,
    )

    if shutil.which("clang++") is None:
        pytest.skip("clang++ not installed")
    (tmp_path / "detail").mkdir()
    (tmp_path / "detail" / "broken.h").write_text("unknown_type_t x;\n")
    headers = []
    for i, name in enumerate(("a.h", "b.h", "c.h", "d.h")):
        body = f"int f{i}(int);\nstruct S{i} {{ int v; }};\n"
        if name in bad_names:
            body = (
                '#error "Unsupported compiler"\n'
                if i % 2
                else '#include "detail/broken.h"\n'
            ) + body
        (tmp_path / name).write_text(body)
        headers.append(tmp_path / name)

    def parse(group):
        tree, _kind, _force = clang_header_dump(group, [tmp_path], memoize=False)
        return project_header_graph_ast(tree)

    with pytest.raises(SnapshotError) as info:
        parse(headers)
    blamed = attribute_failing_headers(info.value, headers)
    assert [h.name for h in blamed] == sorted(bad_names)

    calls: list[list[Path]] = []

    def counting(group):
        calls.append(group)
        return parse(group)

    parts, failed = parse_header_groups_bisecting(
        headers, counting, (SnapshotError,), known_failed=blamed
    )
    assert len(calls) == 1 and sorted(h.name for h in failed) == sorted(bad_names)
    good = {h.name for h in headers} - set(bad_names)
    assert {
        Path(f).name
        for f in merge_header_graph_ast_projections(parts).type_files.values()
    } >= good
