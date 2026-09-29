"""The header graph's clang parse may start before the primary dump finishes.

`prefetch_graph_if_useful` moves only *when* the header graph's own clang
parse runs, never *what* it produces. These tests state that as an
equivalence: a real castxml dump with the prefetch and the same dump forced
sequential serialize identically, whatever the thread budget, and the
prefetch engages only where it cannot parse the same headers twice.
"""

from __future__ import annotations

import shutil
import subprocess
from concurrent.futures import Future
from pathlib import Path

import pytest

from abicheck import (
    service_dump_native as native,
    service_header_graph_attach as attach,
)
from abicheck.serialization import snapshot_to_dict

_HEADER = """\
#pragma once
namespace demo {
struct Point { int x; int y; };
class Shape {
 public:
  virtual ~Shape();
  virtual double area() const;
  Point origin() const;
 private:
  Point p_;
};
double total(const Shape* shapes, int n);
}
"""

_SOURCE = """\
#include "demo.h"
namespace demo {
Shape::~Shape() {}
double Shape::area() const { return 0.0; }
Point Shape::origin() const { return p_; }
double total(const Shape* s, int n) { double t = 0; for (int i = 0; i < n; ++i) t += s[i].area(); return t; }
}
"""


def _needs_tools() -> None:
    for tool in ("castxml", "g++", "clang++"):
        if shutil.which(tool) is None:
            pytest.skip(f"{tool} not available")


@pytest.fixture
def library(tmp_path: Path) -> tuple[Path, Path]:
    _needs_tools()
    inc = tmp_path / "include"
    inc.mkdir()
    (inc / "demo.h").write_text(_HEADER, encoding="utf-8")
    src = tmp_path / "demo.cpp"
    src.write_text(_SOURCE, encoding="utf-8")
    lib = tmp_path / "libdemo.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-g", f"-I{inc}", str(src), "-o", str(lib)],
        check=True,
    )
    return lib, inc / "demo.h"


def _dump(lib: Path, header: Path) -> dict:
    snap = native._run_dump_uncached(
        lib, "elf", headers=[header], includes=[header.parent], header_backend="castxml"
    )
    doc = snapshot_to_dict(snap)
    doc.pop("created_at", None)
    return doc


@pytest.mark.integration
@pytest.mark.parametrize("max_threads", ["", "1"])
def test_prefetched_and_sequential_dumps_are_identical(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    library: tuple[Path, Path],
    max_threads: str,
) -> None:
    lib, header = library
    monkeypatch.setenv("ABICHECK_MAX_THREADS", max_threads)
    # Distinct cache roots so neither run is served the other's parse
    # (AGENTS.md: a differential test must prove both configurations ran).
    calls: list[str] = []
    real_acquire = attach.acquire_header_graph_ast

    def counting(*args: object, **kwargs: object) -> attach.HeaderGraphAst:
        import threading

        calls.append(threading.current_thread().name)
        return real_acquire(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(attach, "acquire_header_graph_ast", counting)

    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-prefetch"))
    prefetched = _dump(lib, header)
    assert len(calls) == 1, "the prefetch path must acquire exactly once"
    if max_threads == "":
        # Observe the mechanism, not just the output: it ran on the prefetch
        # thread, i.e. alongside the primary dump.
        assert calls[0].startswith("abicheck-hgraph"), calls

    calls.clear()
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache-sequential"))
    monkeypatch.setattr(native, "prefetch_graph_if_useful", lambda *a, **k: None)
    sequential = _dump(lib, header)
    assert len(calls) == 1, "the sequential path must acquire exactly once"
    assert not calls[0].startswith("abicheck-hgraph"), calls

    assert prefetched["build_source"] is not None, "the header graph was not built"
    assert prefetched == sequential


@pytest.mark.parametrize(
    ("wanted", "backend", "headers", "engages"),
    [
        (True, "castxml", [Path("a.h")], True),
        (True, "clang", [Path("a.h")], False),  # would parse the headers twice
        (True, "hybrid", [Path("a.h")], False),
        (False, "castxml", [Path("a.h")], False),
        (True, "castxml", [], False),
    ],
)
def test_prefetch_engages_only_where_it_cannot_parse_twice(
    monkeypatch: pytest.MonkeyPatch,
    wanted: bool,
    backend: str,
    headers: list[Path],
    engages: bool,
) -> None:
    started: list[object] = []

    def fake_prefetch(*args: object) -> Future[attach.HeaderGraphAst]:
        started.append(args)
        return Future()

    monkeypatch.setattr(attach, "prefetch_header_graph_ast", fake_prefetch)
    result = attach.prefetch_graph_if_useful(wanted, backend, headers, [], "c++", None)
    assert (result is not None) is engages
    assert bool(started) is engages


@pytest.mark.parametrize("max_threads", ["", "1", "0"])
def test_prefetch_runs_the_acquisition_on_its_own_thread_and_returns_it(
    monkeypatch: pytest.MonkeyPatch, max_threads: str
) -> None:
    import threading

    monkeypatch.setenv("ABICHECK_MAX_THREADS", max_threads)
    sentinel = attach.HeaderGraphAst(None, None, [], [], ())
    seen: list[tuple[str, tuple[object, ...]]] = []

    def fake_acquire(*args: object) -> attach.HeaderGraphAst:
        seen.append((threading.current_thread().name, args))
        return sentinel

    monkeypatch.setattr(attach, "acquire_header_graph_ast", fake_acquire)
    future = attach.prefetch_header_graph_ast([Path("a.h")], [Path("inc")], "c++", None)
    assert future.result(timeout=10) is sentinel
    ((thread_name, args),) = seen
    assert args == ([Path("a.h")], [Path("inc")], "c++", None)
    # A fresh budget always has a thread for it ("0"/unset mean unlimited).
    assert thread_name.startswith("abicheck-hgraph")


def test_a_spent_budget_runs_the_prefetch_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    from abicheck import process_resources as pr

    monkeypatch.setenv("ABICHECK_MAX_THREADS", "1")
    monkeypatch.setattr(pr, "THREAD_BUDGET", pr._ThreadBudget())
    names: list[str] = []
    monkeypatch.setattr(
        attach,
        "acquire_header_graph_ast",
        lambda *a: names.append(threading.current_thread().name),
    )
    with pr.BudgetedExecutor(1):  # takes the only budgeted thread
        attach.prefetch_header_graph_ast([Path("a.h")], [], "c++", None).result(
            timeout=10
        )
    assert names == [threading.current_thread().name]
    assert pr.THREAD_BUDGET.in_use == 0


@pytest.mark.parametrize("exc", [RuntimeError("boom"), OSError("boom")])
def test_a_failing_dump_settles_its_prefetch_before_returning(
    monkeypatch: pytest.MonkeyPatch, exc: BaseException
) -> None:
    """A dump that fails after starting a prefetch must not return while the
    orphaned parse still holds a thread-budget slot: the budget is back to
    where it started the moment the failure propagates, whatever it is."""
    import threading

    from abicheck.process_resources import THREAD_BUDGET

    release = threading.Event()

    def slow_acquire(*_args: object) -> attach.HeaderGraphAst:
        release.wait(5)
        return attach.HeaderGraphAst(None, None, [], [], ())

    monkeypatch.setenv("ABICHECK_MAX_THREADS", "")
    monkeypatch.setattr(attach, "acquire_header_graph_ast", slow_acquire)
    before = THREAD_BUDGET.in_use
    future = attach.prefetch_header_graph_ast([Path("a.h")], [], "c++", None)
    assert THREAD_BUDGET.in_use == before + 1
    threading.Timer(0.05, release.set).start()
    with pytest.raises(type(exc)):
        with attach.prefetch_settled_on_failure(future):
            raise exc
    assert future.done()
    # The done-callback's shutdown may run a moment after result() returns.
    for _ in range(100):
        if THREAD_BUDGET.in_use == before:
            break
        threading.Event().wait(0.01)
    assert THREAD_BUDGET.in_use == before


def test_a_successful_block_does_not_wait_on_the_prefetch() -> None:
    """On success the block must not block on (or consume) the prefetch --
    the header-graph attach does that later."""
    from concurrent.futures import Future

    pending: Future[attach.HeaderGraphAst] = Future()
    with attach.prefetch_settled_on_failure(pending):
        pass
    assert not pending.done()
    with attach.prefetch_settled_on_failure(None):
        pass


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(1)])
def test_an_interrupt_never_waits_on_the_prefetch(exc: BaseException) -> None:
    """Ctrl-C / exit must propagate at once, not after a background parse."""
    from concurrent.futures import Future

    never: Future[attach.HeaderGraphAst] = Future()  # would block forever
    with pytest.raises(type(exc)):
        with attach.prefetch_settled_on_failure(never):
            raise exc
    assert not never.done()


def test_a_stuck_prefetch_bounds_the_failure_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ordinary failure waits at most the settle bound for a stuck parse."""
    import time
    from concurrent.futures import Future

    monkeypatch.setattr(attach, "_PREFETCH_SETTLE_TIMEOUT_S", 0.05)
    stuck: Future[attach.HeaderGraphAst] = Future()
    start = time.monotonic()
    with pytest.raises(RuntimeError):
        with attach.prefetch_settled_on_failure(stuck):
            raise RuntimeError("boom")
    assert time.monotonic() - start < 2.0
