# SPDX-License-Identifier: Apache-2.0
"""Unparseable headers must cost rounds that scale with log(n), not with count.

Field report (oneDAL): castxml stops at the first *fatal* error, so the
exclusion loop dropped one bad header per full parse -- 21 sequential rounds
per header tree -- and the header-graph clang pass then failed and bisected
the same tree again for every library on every side. These tests mock the
frontend to count invocations and state both bounds as invariants over
generated failure sets, with an oracle independent of the implementation:
a header is excluded iff it fails on its own.
"""

from __future__ import annotations

import itertools
import math
import random
from pathlib import Path

import pytest

import abicheck.extract.unparseable_header_fallback as fallback
from abicheck.buildsource.header_graph_ast_projection import (
    HEADER_GRAPH_BISECTION_MEMO,
)
from abicheck.errors import SnapshotError
from abicheck.extract.unparseable_header_fallback import (
    parse_excluding_unparseable_headers,
)


class _SerialPool:
    """Stand-in executor: runs serially, counts probe waves and probes."""

    waves = 0
    probes = 0

    def __init__(self, *_a, **_k) -> None:
        pass

    def __enter__(self) -> _SerialPool:
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def map(self, fn, items):
        items = list(items)
        type(self).waves += 1
        type(self).probes += len(items)
        return [fn(i) for i in items]


def _reset() -> None:
    _SerialPool.waves = 0
    _SerialPool.probes = 0


def _fatal_frontend(bad: set[Path], calls: list[list[Path]]):
    """castxml-shaped: the run stops at the FIRST bad header in TU order and
    reports only that one, as a fatal error."""

    def attempt(group: list[Path]) -> tuple[Path, ...]:
        calls.append(list(group))
        for h in group:
            if h in bad:
                exc = SnapshotError("castxml failed")
                exc.stderr = (
                    f"{h}:1:10: fatal error: 'sycl/sycl.hpp' file not found\n"
                    "1 error generated."
                )
                raise exc
        return tuple(group)

    return attempt


def _cases():
    rng = random.Random(4242)
    for n in range(2, 8):
        hs = [Path(f"/inc/h{i}.h") for i in range(n)]
        for k in range(n):  # leave at least one parseable header
            for bad in itertools.combinations(hs, k):
                yield hs, set(bad)
    for _ in range(40):
        n = rng.randint(8, 240)
        hs = [Path(f"/inc/h{i}.h") for i in range(n)]
        yield hs, set(rng.sample(hs, rng.randint(0, min(n - 1, 30))))


def test_fatal_errors_cost_logarithmic_rounds_and_exclude_exactly_the_bad(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fallback, "BudgetedExecutor", _SerialPool)
    for headers, bad in _cases():
        _reset()
        calls: list[list[Path]] = []
        result, excluded = parse_excluding_unparseable_headers(
            headers, _fatal_frontend(bad, calls)
        )
        # Oracle: excluded iff the header fails alone; the result is the
        # full-context parse of every other header, in input order.
        assert set(excluded) == bad
        assert list(result) == [h for h in headers if h not in bad]
        # Sequential latency = full-set rounds + probe waves. The old loop
        # took len(bad) + 1 full rounds; both are now bounded independently
        # of how many headers fail.
        log_n = math.ceil(math.log2(len(headers)))
        full_rounds = len(calls) - _SerialPool.probes
        assert full_rounds <= 3, (len(headers), len(bad), full_rounds)
        assert _SerialPool.waves <= log_n + 2, (len(headers), len(bad))
        # Total work stays O(f log n), never one parse per header.
        assert len(calls) <= 3 + 2 * (len(bad) + 1) * (log_n + 1) + len(bad)


def test_single_fatal_failure_costs_one_clean_probe_wave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fallback, "BudgetedExecutor", _SerialPool)
    _reset()
    hs = [Path(f"/inc/h{i}.h") for i in range(64)]
    calls: list[list[Path]] = []
    _result, excluded = parse_excluding_unparseable_headers(
        hs, _fatal_frontend({hs[10]}, calls)
    )
    assert excluded == [hs[10]]
    # Failed full parse, two clean half probes, then the final full parse.
    assert _SerialPool.waves == 1
    assert len(calls) == 4


@pytest.mark.parametrize(
    ("n", "bad", "needs"),
    [
        (10, {0}, {5: 2}),
        (16, {0, 11}, {9: 3, 14: 1}),
        (32, {4, 20, 27}, {8: 1, 30: 22}),
    ],
)
def test_header_failing_alone_but_parsing_in_context_is_not_excluded(
    monkeypatch: pytest.MonkeyPatch, n: int, bad: set[int], needs: dict[int, int]
) -> None:
    """``needs[d] = p``: header d parses only when header p precedes it in the
    TU (it relies on p's declarations), so an isolated probe blames d although
    the full-context parse accepts it. Only the genuinely bad are excluded."""
    monkeypatch.setattr(fallback, "BudgetedExecutor", _SerialPool)
    _reset()
    hs = [Path(f"/inc/h{i}.h") for i in range(n)]
    bad_paths = {hs[i] for i in bad}
    calls: list[list[Path]] = []

    def attempt(group: list[Path]) -> tuple[Path, ...]:
        calls.append(list(group))
        for pos, h in enumerate(group):
            i = int(h.stem[1:])
            dep = needs.get(i)
            if h in bad_paths or (dep is not None and hs[dep] not in group[:pos]):
                exc = SnapshotError("castxml failed")
                exc.stderr = f"{h}:1:10: fatal error: 'dep.h' file not found"
                raise exc
        return tuple(group)

    result, excluded = parse_excluding_unparseable_headers(hs, attempt)
    assert set(excluded) == bad_paths
    assert list(result) == [h for h in hs if h not in bad_paths]
    assert all(hs[d] in result for d in needs)


def test_non_fatal_diagnostics_never_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every error was reported, so the classic single retry is enough."""
    monkeypatch.setattr(fallback, "BudgetedExecutor", _SerialPool)
    _reset()
    hs = [Path(f"/inc/h{i}.h") for i in range(20)]
    bad = {hs[3], hs[7], hs[15]}
    calls: list[list[Path]] = []

    def attempt(group: list[Path]) -> int:
        calls.append(group)
        hit = [h for h in group if h in bad]
        if hit:
            exc = SnapshotError("castxml failed")
            exc.stderr = "\n".join(f"{h}:3:2: error: Unsupported compiler" for h in hit)
            raise exc
        return len(group)

    _r, excluded = parse_excluding_unparseable_headers(hs, attempt)
    assert set(excluded) == bad
    assert len(calls) == 2
    assert _SerialPool.waves == 0


# --- header-graph clang pass: one bisection per shared header tree ----------


@pytest.fixture(autouse=True)
def _clear_memo():
    HEADER_GRAPH_BISECTION_MEMO.clear()
    yield
    HEADER_GRAPH_BISECTION_MEMO.clear()


def _tree(tmp_path: Path, n: int, bad: set[int]) -> list[Path]:
    paths = []
    for i in range(n):
        p = tmp_path / f"h{i}.h"
        p.write_text("#error nope\n" if i in bad else f"struct S{i} {{ int x; }};\n")
        paths.append(p)
    return paths


def _fake_clang(bad_names: set[str], calls: list[list[str]]):
    def fake_dump(headers, *_a, **_k):
        names = [Path(h).name for h in headers]
        calls.append(names)
        if bad_names.intersection(names):
            raise SnapshotError("clang failed")
        return (
            {
                "kind": "TranslationUnitDecl",
                "inner": [
                    {
                        "kind": "CXXRecordDecl",
                        "name": f"S{Path(h).stem[1:]}",
                        "tagUsed": "struct",
                        "completeDefinition": True,
                        "id": f"0x{i + 1:x}",
                        "loc": {"file": str(h), "line": 1},
                        "inner": [],
                    }
                    for i, h in enumerate(headers)
                ],
            },
            "c++",
            False,
        )

    return fake_dump


@pytest.mark.parametrize(
    ("n", "bad", "libraries"),
    [(8, {2}, 4), (16, {1, 9, 14}, 3), (5, {0, 4}, 2), (12, set(range(0, 12, 2)), 4)],
)
def test_libraries_sharing_a_header_tree_bisect_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    n: int,
    bad: set[int],
    libraries: int,
) -> None:
    import abicheck.extract.headers.clang.backend as clang_backend
    from abicheck.service_header_graph_attach import acquire_header_graph_ast

    paths = _tree(tmp_path, n, bad)
    bad_names = {paths[i].name for i in bad}
    calls: list[list[str]] = []
    monkeypatch.setattr(
        clang_backend, "clang_header_dump", _fake_clang(bad_names, calls)
    )

    first = acquire_header_graph_ast(paths, [], "c++", None)
    assert len(calls) > 2  # whole-tree failure + bisection
    results = [first]
    for _ in range(libraries - 1):
        before = len(calls)
        results.append(acquire_header_graph_ast(paths, [], "c++", None))
        # A memo hit: no whole-tree failure, no bisect -- one parse of the rest.
        assert len(calls) - before == 1
        assert bad_names.isdisjoint(calls[-1])
    # Identical outcome for every library (the oracle: lost iff bad).
    for got in results:
        assert got.ast_failure == first.ast_failure
        assert got.projection is not None
        assert set(got.projection.type_files) == {
            f"S{i}" for i in range(n) if i not in bad
        }


def test_memo_misses_on_different_flags_or_edited_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import abicheck.extract.headers.clang.backend as clang_backend
    from abicheck.service_header_graph_attach import acquire_header_graph_ast

    paths = _tree(tmp_path, 6, {3})
    calls: list[list[str]] = []
    monkeypatch.setattr(
        clang_backend, "clang_header_dump", _fake_clang({"h3.h"}, calls)
    )
    acquire_header_graph_ast(paths, [], "c++", None)
    base = len(calls)
    # Different language mode: a different parse, so no sharing.
    acquire_header_graph_ast(paths, [], "c", None)
    assert len(calls) - base == base
    # An edited header changes the tree signature: no stale reuse.
    paths[0].write_text("struct S0 { int x; int y; };\n")
    before = len(calls)
    acquire_header_graph_ast(paths, [], "c++", None)
    assert len(calls) - before == base
