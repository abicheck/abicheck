"""A request reads each projection sidecar once, not once per library.

The bug class: a release fans one header tree out to every member, so the
same sidecar was decoded once per member (48 reads for 2 distinct entries on
a 28-member release). The invariant stated here is *the result is the same
whether a load is memoized or not, and a memoized load touches the disk at
most once per key per request*. Equality is checked against the value that
was stored, not against the memo's own helper.
"""

from __future__ import annotations

import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from abicheck.buildsource import header_graph_projection_cache as cache
from abicheck.buildsource.header_graph_ast_projection import (
    HeaderGraphAstProjection,
    header_graph_bisection_scope,
)
from abicheck.buildsource.header_graph_projection_cache import (
    load_cached_projection,
    store_cached_projection,
)


def _projection(name: str) -> HeaderGraphAstProjection:
    return HeaderGraphAstProjection(type_files={f"ns::{name}": f"/inc/{name}.h"})


def _entry(tmp_path: Path, name: str, proj: HeaderGraphAstProjection) -> Path:
    ast_entry = tmp_path / f"{name}.json"
    ast_entry.write_text("{}")
    store_cached_projection(ast_entry, proj)
    return ast_entry


@pytest.fixture
def reads(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record every disk read of a sidecar, delegating to the real reader."""
    seen: list[Path] = []
    real = cache._read_sidecar

    def counting(path: Path) -> HeaderGraphAstProjection | None:
        seen.append(path)
        return real(path)

    monkeypatch.setattr(cache, "_read_sidecar", counting)
    return seen


def test_repeated_loads_in_one_scope_read_the_sidecar_once(
    tmp_path: Path, reads: list[Path]
) -> None:
    proj = _projection("A")
    entry = _entry(tmp_path, "aaaa", proj)
    reads.clear()

    with header_graph_bisection_scope():
        loaded = [load_cached_projection(entry) for _ in range(5)]

    assert len(reads) == 1
    assert all(p is loaded[0] for p in loaded)
    assert loaded[0] == proj


def test_distinct_keys_each_read_once_in_one_scope(
    tmp_path: Path, reads: list[Path]
) -> None:
    a = _entry(tmp_path, "aaaa", _projection("A"))
    b = _entry(tmp_path, "bbbb", _projection("B"))
    reads.clear()

    with header_graph_bisection_scope():
        for _ in range(4):
            assert load_cached_projection(a) is not None
            assert load_cached_projection(b) is not None

    assert sorted(reads) == sorted(
        [cache.projection_sidecar_path(a), cache.projection_sidecar_path(b)]
    )


def test_outside_a_scope_every_load_reads_the_disk(
    tmp_path: Path, reads: list[Path]
) -> None:
    entry = _entry(tmp_path, "aaaa", _projection("A"))
    reads.clear()

    for _ in range(3):
        assert load_cached_projection(entry) is not None

    assert len(reads) == 3


def test_a_miss_is_not_memoized(tmp_path: Path, reads: list[Path]) -> None:
    ast_entry = tmp_path / "cccc.json"
    ast_entry.write_text("{}")
    proj = _projection("C")

    with header_graph_bisection_scope():
        assert load_cached_projection(ast_entry) is None
        # A sibling stores the entry after the miss; the next load must see it.
        store_cached_projection(ast_entry, proj)
        assert load_cached_projection(ast_entry) == proj


def test_concurrent_loads_of_one_key_read_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = _entry(tmp_path, "aaaa", _projection("A"))
    real = cache._read_sidecar
    calls: list[Path] = []

    def slow(path: Path) -> HeaderGraphAstProjection | None:
        calls.append(path)
        time.sleep(0.05)
        return real(path)

    monkeypatch.setattr(cache, "_read_sidecar", slow)

    with header_graph_bisection_scope():
        # Each worker inherits the request's context, as the release fan-out does.
        jobs = [contextvars.copy_context() for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(
                pool.map(lambda ctx: ctx.run(load_cached_projection, entry), jobs)
            )

    assert len(calls) == 1
    assert all(r is results[0] for r in results)
