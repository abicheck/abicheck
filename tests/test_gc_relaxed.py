# SPDX-License-Identifier: Apache-2.0
"""Relaxed collector thresholds during a live dump: always restored, never on
a free-threaded build or under ``low-memory``, and never change the output.

Field run (oneCCL 2021.15): ``gc.set_threshold(100000, 50, 100)`` cut the dump
44 -> 37.5 s (3.12) and 41 -> 35.4 s (3.15) with identical output.
"""

from __future__ import annotations

import gc
import random
import shutil
import subprocess
import sys
import threading

import pytest

from abicheck.model.performance import (
    PerformanceProfile,
    performance_profile_scope,
)
from abicheck.storage import acyclic_json
from abicheck.storage.acyclic_json import RELAXED_GC_THRESHOLDS, gc_relaxed


class _Boom(Exception):
    pass


@pytest.fixture(autouse=True)
def _restore_thresholds():
    before = gc.get_threshold()
    yield
    gc.set_threshold(*before)


def _nest(rng: random.Random, depth: int) -> None:
    with gc_relaxed():
        if gc.get_threshold() != RELAXED_GC_THRESHOLDS:
            raise AssertionError(gc.get_threshold())
        if depth and rng.random() < 0.7:
            _nest(rng, depth - 1)
        if rng.random() < 0.3:
            raise _Boom


@pytest.mark.parametrize("seed", range(25))
def test_any_nesting_and_failure_restores_the_prior_thresholds(seed) -> None:
    rng = random.Random(seed)
    prior = (rng.randint(100, 5000), rng.randint(2, 20), rng.randint(2, 20))
    gc.set_threshold(*prior)
    for _ in range(5):
        try:
            _nest(rng, rng.randint(0, 4))
        except _Boom:
            pass
        assert gc.get_threshold() == prior


def test_overlapping_threads_restore_only_after_the_last() -> None:
    prior = gc.get_threshold()
    inside = threading.Barrier(4)
    seen: list[tuple[int, ...]] = []

    def worker() -> None:
        with gc_relaxed():
            inside.wait()
            seen.append(gc.get_threshold())
            inside.wait()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert seen == [RELAXED_GC_THRESHOLDS] * 4
    assert gc.get_threshold() == prior


@pytest.mark.parametrize(
    ("enabled", "gil"), [(False, True), (True, False), (False, False)]
)
def test_no_op_when_disabled_or_free_threaded(monkeypatch, enabled, gil) -> None:
    monkeypatch.setattr(sys, "_is_gil_enabled", lambda: gil, raising=False)
    prior = gc.get_threshold()
    with gc_relaxed(enabled):
        assert gc.get_threshold() == prior


@pytest.mark.parametrize(
    ("profile", "relaxed"),
    [(PerformanceProfile.BALANCED, True), (PerformanceProfile.LOW_MEMORY, False)],
)
def test_every_live_dump_runs_under_its_profiles_setting(monkeypatch, profile, relaxed):
    from abicheck.workflows.dump import native

    monkeypatch.setattr(acyclic_json, "_gil_enabled", lambda: True)
    seen = []
    monkeypatch.setattr(
        native, "_run_dump_uncached", lambda *a, **k: seen.append(gc.get_threshold())
    )
    prior = gc.get_threshold()
    with performance_profile_scope(profile):
        native._call_run_dump_uncached()
    assert seen == [RELAXED_GC_THRESHOLDS if relaxed else prior]
    assert gc.get_threshold() == prior


@pytest.mark.integration
def test_relaxed_and_default_collectors_produce_the_same_snapshot(tmp_path):
    """Scheduling only: the dump's content must not depend on when the
    collector runs (weakref/finalizer-driven state would show here)."""
    if shutil.which("gcc") is None:
        pytest.skip("gcc not installed")
    from abicheck.serialization import snapshot_to_json
    from abicheck.workflows.dump.native import _call_run_dump_uncached

    (tmp_path / "a.h").write_text(
        "struct S { int a; long b; };\nstruct T { struct S s; T* next; };\n"
        "int f(struct S*);\nint g(const struct T*);\n".replace(
            "T* next", "struct T* next"
        )
    )
    (tmp_path / "a.c").write_text(
        '#include "a.h"\nint f(struct S*s){return s->a;}\n'
        "int g(const struct T*t){return t->s.a;}\n"
    )
    so = tmp_path / "liba.so"
    subprocess.run(
        ["gcc", "-g", "-shared", "-fPIC", str(tmp_path / "a.c"), "-o", str(so)],
        check=True,
    )

    def dump(profile):
        with performance_profile_scope(profile):
            snap = _call_run_dump_uncached(so, "elf", [tmp_path / "a.h"], [], "1", "c")
        snap.created_at = None
        return snapshot_to_json(snap)

    assert dump(PerformanceProfile.BALANCED) == dump(PerformanceProfile.LOW_MEMORY)
