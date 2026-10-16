# SPDX-License-Identifier: Apache-2.0
"""The whole-snapshot cache is keyed by input *identity*, not spelling, and a
cache-only probe lets the compare pipeline fork only the sides that extract.

Field run (oneDNN): the same files given as relative vs absolute paths missed
the cache entirely, and a fully warm compare still forked two children --
2.6x the peak memory for no speed gain.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.model import AbiSnapshot
from abicheck.workflows import side_isolation
from abicheck.workflows.cache_probe import (
    CacheProbeMiss,
    cache_only_probe,
    refuse_dump_while_probing,
)
from abicheck.workflows.dump.cache.api import cached_run_dump


def _recording_dump(calls: list[tuple[Path, list[Path]]]):
    def run_dump(path, binary_fmt, headers, includes, version, lang, **kwargs):
        calls.append((path, list(headers or [])))
        return AbiSnapshot(library=path.name, version=version)

    return run_dump


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "inc" / "sub").mkdir(parents=True)
    (tmp_path / "lib.so").write_bytes(b"\x7fELF fake")
    (tmp_path / "inc" / "a.h").write_text("int f(void);\n")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _spellings(root: Path, rel: str) -> list[Path]:
    """Independent spellings of one file: relative, dotted, absolute, and a
    detour through a sibling directory."""
    parent, _, name = rel.rpartition("/")
    detour = f"{parent}/sub/../{name}" if parent else f"inc/../{name}"
    return [Path(rel), Path(f"./{rel}"), root / rel, Path(detour)]


def test_every_spelling_of_one_input_shares_one_entry(project):
    calls: list[tuple[Path, list[Path]]] = []
    dump = _recording_dump(calls)
    for binary in _spellings(project, "lib.so"):
        for header in _spellings(project, "inc/a.h"):
            cached_run_dump(dump, binary, "elf", [header], [], "1", "c")
    # One extraction for 16 spellings, and it saw the absolute form -- the
    # snapshot records what it was given, so warm and cold must agree.
    assert calls == [(project / "lib.so", [project / "inc" / "a.h"])]


def test_a_different_file_still_misses(project):
    calls: list[tuple[Path, list[Path]]] = []
    dump = _recording_dump(calls)
    (project / "inc" / "b.h").write_text("int g(void);\n")
    cached_run_dump(dump, Path("lib.so"), "elf", [Path("inc/a.h")], [], "1", "c")
    cached_run_dump(dump, Path("lib.so"), "elf", [Path("inc/b.h")], [], "1", "c")
    assert len(calls) == 2


def test_probe_misses_cold_then_serves_warm_without_dumping(project):
    calls: list[tuple[Path, list[Path]]] = []
    dump = _recording_dump(calls)
    args = (dump, Path("lib.so"), "elf", [Path("inc/a.h")], [], "1", "c")
    with cache_only_probe(), pytest.raises(CacheProbeMiss):
        cached_run_dump(*args)
    assert calls == []
    cached_run_dump(*args)  # outside the probe: extracts and stores
    with cache_only_probe():
        assert cached_run_dump(*args).library == "lib.so"
    assert len(calls) == 1


def test_probe_never_dumps_an_uncacheable_shape(project):
    calls: list[tuple[Path, list[Path]]] = []
    with cache_only_probe(), pytest.raises(CacheProbeMiss):
        cached_run_dump(
            _recording_dump(calls),
            Path("lib.so"),
            "elf",
            [],
            [],
            "1",
            "c",
            dwarf_only=True,
        )
    assert calls == []


def test_probe_signal_passes_broad_exception_fallbacks():
    # Side resolution wraps header parsing in `except Exception` fallbacks;
    # a probe miss must not be mistaken for a parse failure there.
    def side():
        try:
            refuse_dump_while_probing()
        except Exception:  # noqa: BLE001 -- the fallback under test
            return "degraded"
        return "dumped"

    assert side() == "dumped"
    with cache_only_probe(), pytest.raises(CacheProbeMiss):
        side()


@pytest.mark.parametrize(
    "warm_mask", [(True, True), (True, False), (False, True), (False, False)]
)
def test_only_cold_sides_are_forked_and_order_is_kept(monkeypatch, warm_mask):
    forked: list[list[str]] = []

    def fake_run_isolated(fns, *, concurrent):
        forked.append([fn() for fn in fns])
        return forked[-1]

    monkeypatch.setattr(side_isolation, "run_isolated", fake_run_isolated)

    def make(name: str, warm: bool):
        def fn() -> str:
            if not warm:
                refuse_dump_while_probing()
            return name

        return fn

    fns = [make(n, w) for n, w in zip(("old", "new"), warm_mask, strict=True)]
    wrapped: list[str] = []

    def wrap(fn):
        wrapped.append(fn.__name__)
        return fn

    assert side_isolation.run_cold_isolated(fns, wrap, concurrent=True) == [
        "old",
        "new",
    ]
    cold = [n for n, w in zip(("old", "new"), warm_mask, strict=True) if not w]
    assert forked == ([cold] if cold else [])
    assert len(wrapped) == len(cold)


def test_symlinked_prefix_keeps_its_spelling(project):
    # abspath, not resolve(): the name the user gave survives normalization.
    os.symlink(project / "inc", project / "include")
    calls: list[tuple[Path, list[Path]]] = []
    cached_run_dump(
        _recording_dump(calls),
        Path("lib.so"),
        "elf",
        [Path("include/a.h")],
        [],
        "1",
        "c",
    )
    assert calls[0][1] == [project / "include" / "a.h"]


@pytest.mark.parametrize("concurrent", [True, False])
def test_sequential_isolation_forks_even_warm_sides(monkeypatch, concurrent):
    """``low-memory`` (sequential isolation) keeps every side out of process."""
    forked: list[int] = []

    def fake_run_isolated(fns, *, concurrent):
        forked.append(len(fns))
        return [fn() for fn in fns]

    monkeypatch.setattr(side_isolation, "run_isolated", fake_run_isolated)
    side_isolation.run_cold_isolated(
        [lambda: "old", lambda: "new"], lambda fn: fn, concurrent=concurrent
    )
    assert forked == ([] if concurrent else [2])


@pytest.mark.parametrize("warm_mask", [(True, True), (True, False)])
def test_budget_spent_during_warm_loads_raises_before_later_work(
    monkeypatch, warm_mask
):
    """Warm sides load outside any worker's deadline re-check, so the budget
    is checked once they are done -- whether or not a cold side remains."""
    import time

    from abicheck import deadline

    monkeypatch.setattr(
        side_isolation, "run_isolated", lambda fns, **_: [f() for f in fns]
    )

    def make(name: str, warm: bool):
        def fn() -> str:
            if not warm:
                refuse_dump_while_probing()
            time.sleep(0.05)  # the slow cache read
            return name

        return fn

    fns = [make(n, w) for n, w in zip(("old", "new"), warm_mask, strict=True)]
    with deadline.deadline_scope(0.02), pytest.raises(deadline.DeadlineExceeded):
        side_isolation.run_cold_isolated(fns, lambda fn: fn, concurrent=True)
