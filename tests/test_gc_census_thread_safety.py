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

"""A whole-heap GC census must never run beside another Python thread.

Bug class ``gc-census-concurrent-thread``: ``gc.get_objects()`` /
``gc.get_referrers()`` return a list holding a new reference to every
GC-tracked object, including a tuple another thread is still building in
``PySequence_Tuple``. That builder requires refcount 1 when it grows the
tuple, so it fails with ``tupleobject.c: bad argument to internal
function``. Reproduced on CPython 3.10-3.13; 3.14 builds ``tuple(iterable)``
without growing an allocated tuple, so the negative control below is skipped
there, while the guard stays unconditional (a census still hands out
references to objects other threads are mid-way through building). The
oneDAL release fan-out lost a different member to this in
three of four benchmark runs, because the benchmark's attach hook took a
census inside a worker thread.

The invariants:

* the mechanism is real (a census alive across a resize breaks ``tuple()``),
  so the guard is not guarding against nothing;
* :func:`memory_trace.gc_object_count` declines (``None``) whenever another
  thread exists, and ``tuple()`` built concurrently with it always equals a
  serial oracle;
* no first-party file enumerates the heap except through that guard.
"""

from __future__ import annotations

import ast
import random
import sys
import threading
from pathlib import Path

import pytest

from abicheck.workflows import memory_trace

_REPO = Path(__file__).resolve().parent.parent

# Sizes above `PySequence_Tuple`'s initial guess of 10, so every one forces
# at least one `_PyTuple_Resize`; seeded so a failure names reproducible inputs.
_SIZES = [11, 12, 37, 100, 257] + random.Random(20260924).sample(range(11, 400), 15)


def _gen_holding(n: int, census):  # type: ignore[no-untyped-def]
    """Yield ``range(n)`` while keeping *census*'s result alive throughout.

    Single-threaded and deterministic: it places a live census across every
    resize ``tuple()`` performs, which is the interleaving a second thread
    produces nondeterministically.
    """
    held = census()
    yield from range(n)
    del held


def _raw_census():  # type: ignore[no-untyped-def]
    import gc

    return gc.get_objects()


class TestMechanism:
    @pytest.mark.skipif(
        sys.version_info >= (3, 14),
        reason="CPython 3.14 no longer grows an allocated tuple in place in "
        "tuple(iterable); reproduced on 3.10-3.13 only",
    )
    @pytest.mark.parametrize("n", _SIZES)
    def test_a_live_raw_census_breaks_tuple_construction(self, n: int) -> None:
        """Negative control: without the guard the class reproduces exactly."""
        with pytest.raises(SystemError, match="bad argument to internal function"):
            tuple(_gen_holding(n, _raw_census))

    @pytest.mark.parametrize("n", _SIZES)
    def test_the_guarded_census_holds_no_heap_reference(self, n: int) -> None:
        assert tuple(_gen_holding(n, memory_trace.gc_object_count)) == tuple(range(n))


class TestGuard:
    def test_declines_while_another_thread_exists(self) -> None:
        started, release = threading.Event(), threading.Event()

        def park() -> None:
            started.set()
            release.wait(10)

        t = threading.Thread(target=park)
        t.start()
        try:
            started.wait(10)
            assert memory_trace.gc_census_is_safe() is False
            assert memory_trace.gc_object_count() is None
        finally:
            release.set()
            t.join(10)

    def test_counts_when_single_threaded(self) -> None:
        if threading.active_count() != 1:
            pytest.skip("another thread is alive in this test process")
        count = memory_trace.gc_object_count()
        assert isinstance(count, int) and count > 0

    def test_concurrent_builders_match_the_serial_oracle(self) -> None:
        """N builder threads + a census thread through the guard.

        Replacing ``gc_object_count`` with a raw ``len(gc.get_objects())``
        makes this fail within milliseconds (``SystemError`` in a builder),
        which is how the fix was demonstrated.
        """
        oracle = {n: tuple(range(n)) for n in _SIZES}
        stop = threading.Event()
        failures: list[BaseException] = []
        mismatches: list[int] = []

        def build() -> None:
            try:
                for _ in range(30):
                    for n in _SIZES:
                        if tuple(i for i in range(n)) != oracle[n]:
                            mismatches.append(n)
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)

        def census() -> None:
            while not stop.is_set():
                memory_trace.gc_object_count()

        old = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            builders = [threading.Thread(target=build) for _ in range(4)]
            scanner = threading.Thread(target=census)
            scanner.start()
            for b in builders:
                b.start()
            for b in builders:
                b.join(60)
            stop.set()
            scanner.join(60)
        finally:
            sys.setswitchinterval(old)
        assert not failures, failures[:1]
        assert not mismatches, mismatches[:5]


#: Files allowed to enumerate the heap directly, each with the reason it is
#: safe. Everything else must call `memory_trace.gc_object_count`.
_CENSUS_ALLOWLIST: dict[str, str] = {
    "abicheck/workflows/memory_trace.py": "the guard itself",
    "scripts/perf_cache_reset.py": (
        "clears lru_caches before a traced run; heap census only when gc_census_is_safe(), else a namespace walk"
    ),
    "tests/test_gc_census_thread_safety.py": "the negative control above",
}
_CENSUS_CALLS = {"get_objects", "get_referrers"}


def _first_party_files() -> list[Path]:
    roots = ("abicheck", "scripts", "tests", "eval", "validation", "action")
    return sorted(
        p for r in roots if (_REPO / r).is_dir() for p in (_REPO / r).rglob("*.py")
    )


def _census_sites(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _CENSUS_CALLS:
            if isinstance(node.value, ast.Name) and node.value.id == "gc":
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "gc":
            if any(a.name in _CENSUS_CALLS for a in node.names):
                lines.append(node.lineno)
    return sorted(lines)


class TestNoUnguardedCensus:
    def test_scanner_finds_both_spellings(self, tmp_path: Path) -> None:
        f = tmp_path / "x.py"
        f.write_text("import gc\nlen(gc.get_objects())\nfrom gc import get_referrers\n")
        assert _census_sites(f) == [2, 3]

    def test_no_first_party_file_enumerates_the_heap_unguarded(self) -> None:
        offenders = {}
        for path in _first_party_files():
            rel = path.relative_to(_REPO).as_posix()
            sites = _census_sites(path)
            if sites and rel not in _CENSUS_ALLOWLIST:
                offenders[rel] = sites
        assert not offenders, (
            f"use abicheck.workflows.memory_trace.gc_object_count(): {offenders}"
        )

    def test_allowlisted_benchmark_still_checks_the_guard(self) -> None:
        text = (_REPO / "scripts/perf_cache_reset.py").read_text(encoding="utf-8")
        assert "gc_census_is_safe()" in text


def test_benchmark_cache_reset_still_clears_with_another_thread_alive() -> None:
    """The fallback path must still reach a module-level ``lru_cache``."""
    import importlib.util

    bench = sys.modules.get("benchmark_scaling")
    if bench is None:
        spec = importlib.util.spec_from_file_location(
            "benchmark_scaling", _REPO / "scripts/benchmark_scaling.py"
        )
        assert spec and spec.loader
        bench = importlib.util.module_from_spec(spec)
        sys.modules["benchmark_scaling"] = bench  # dataclasses resolve via it
        spec.loader.exec_module(bench)
    from abicheck import demangle

    demangle.demangle("x")  # a non-mangled name: cached without any subprocess
    assert demangle.demangle.cache_info().currsize > 0
    started, release = threading.Event(), threading.Event()

    def park() -> None:
        started.set()
        release.wait(10)

    t = threading.Thread(target=park)
    t.start()
    try:
        started.wait(10)
        assert not memory_trace.gc_census_is_safe()
        bench._clear_process_caches()
        assert demangle.demangle.cache_info().currsize == 0
    finally:
        release.set()
        t.join(10)


def test_harness_copy_of_the_guard_agrees_with_memory_trace() -> None:
    """`perf_cache_reset` restates the guard (it must run against an older
    installed package); the two must answer alike with and without a thread."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_perf_cache_reset_under_test", _REPO / "scripts/perf_cache_reset.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    started, release = threading.Event(), threading.Event()

    def park() -> None:
        started.set()
        release.wait(10)

    t = threading.Thread(target=park)
    t.start()
    try:
        started.wait(10)
        assert mod.gc_census_is_safe() is memory_trace.gc_census_is_safe() is False
    finally:
        release.set()
        t.join(10)
    assert mod.gc_census_is_safe() is memory_trace.gc_census_is_safe()
