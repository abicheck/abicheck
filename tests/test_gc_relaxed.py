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

"""`process_resources.gc_relaxed`: a scoped, overlap-safe gen-0 threshold raise.

The oracle is ``gc.get_threshold()`` itself: whatever the caller had before
the outermost use must be back afterwards, whatever the nesting, threading or
exception path, and a free-threaded interpreter must never be touched.
"""

from __future__ import annotations

import gc
import threading

import pytest

from abicheck import process_resources as pr
from abicheck.process_resources import RELAXED_GC_GEN0_THRESHOLD, gc_relaxed


@pytest.fixture
def restore_threshold():
    saved = gc.get_threshold()
    yield
    gc.set_threshold(*saved)


@pytest.fixture
def gil(monkeypatch):
    monkeypatch.setattr(pr, "gil_enabled", lambda: True)


@pytest.mark.parametrize("before", [(700, 10, 10), (2000, 10, 10), (5, 1, 1)])
def test_raised_inside_and_restored_after(restore_threshold, gil, before):
    gc.set_threshold(*before)
    with gc_relaxed():
        assert gc.get_threshold() == (RELAXED_GC_GEN0_THRESHOLD, *before[1:])
    assert gc.get_threshold() == before


@pytest.mark.parametrize(
    "before", [(RELAXED_GC_GEN0_THRESHOLD * 2, 10, 10), (0, 10, 10)]
)
def test_higher_or_disabled_threshold_is_kept(restore_threshold, gil, before):
    # 0 disables automatic collection; raising it would *enable* collection.
    gc.set_threshold(*before)
    with gc_relaxed():
        assert gc.get_threshold() == before
    assert gc.get_threshold() == before


def test_nested_and_exception_restore(restore_threshold, gil):
    gc.set_threshold(700, 10, 10)
    with pytest.raises(RuntimeError), gc_relaxed():
        with gc_relaxed():
            pass
        assert gc.get_threshold()[0] == RELAXED_GC_GEN0_THRESHOLD
        raise RuntimeError
    assert gc.get_threshold() == (700, 10, 10)


def test_overlapping_threads_restore_once_last_exits(restore_threshold, gil):
    gc.set_threshold(700, 10, 10)
    inside = threading.Barrier(4)
    leave = threading.Event()
    seen: list[int] = []

    def worker() -> None:
        with gc_relaxed():
            inside.wait()
            leave.wait()
            seen.append(gc.get_threshold()[0])

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    inside.wait()
    assert gc.get_threshold()[0] == RELAXED_GC_GEN0_THRESHOLD
    leave.set()
    for t in threads:
        t.join()
    assert gc.get_threshold() == (700, 10, 10)
    assert seen and all(v in (700, RELAXED_GC_GEN0_THRESHOLD) for v in seen)


def test_noop_without_gil(restore_threshold, monkeypatch):
    monkeypatch.setattr(pr, "gil_enabled", lambda: False)
    gc.set_threshold(700, 10, 10)
    with gc_relaxed():
        assert gc.get_threshold() == (700, 10, 10)
    assert gc.get_threshold() == (700, 10, 10)
