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

"""Parse a large JSON document with the cyclic garbage collector paused.

``json.loads`` of a clang AST allocates millions of dicts and lists, and
every allocation counts toward CPython's generation-0 threshold, so the
cyclic collector runs over and over *while the tree is being built* --
each pass traversing the ever-growing set of young containers the parse has
just made. None of that work can free anything: a decoded JSON document is
a tree, and a tree has no reference cycles to collect.

Measured on a 246 MiB ``clang -ast-dump=json`` document (the header-graph
memory fixture), ``json.loads`` takes 1.15-1.77 s with the collector
running and 0.83-0.87 s with it paused -- the same objects, the same
result. Pausing it is purely a scheduling decision.

The pause is process-wide (``gc.disable`` has no per-thread form), bounded
by the parse, and restores the setting in effect before the first
overlapping pause once the last one ends, so a caller that had already
disabled collection keeps it disabled. Another
thread that allocates during the pause loses nothing but timeliness: its
cyclic garbage is collected at the next collection after the pause ends.

Overlapping pauses can chain indefinitely: with members loading snapshots on
parallel threads (a free-threaded build runs up to eight at once), a new
pause began before the last one ended, the count never reached zero, and
collection stayed off for a whole warm release run while compare threads
piled up cyclic garbage (28-member warm run: 28 GiB vs 7.9 GiB cold on
3.15t). So the *continuous* off-time is bounded: once collection has been
off for :data:`MAX_CONTINUOUS_PAUSE_S`, the next pause to end runs one
``gc.collect()`` and starts a new window.
"""

from __future__ import annotations

import gc
import json
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = ["RELAXED_GC_THRESHOLDS", "gc_paused", "gc_relaxed", "loads_acyclic"]


#: Overlapping pauses (two dumps parsing on two threads) share one pause:
#: the setting in effect when the first began is restored when the last
#: ends. Restoring per pause would let an inner pause record "disabled" as
#: its prior state and leave collection off for the rest of the process.
_lock = threading.Lock()
_active = 0
_restore_enabled = False

#: Longest stretch, in seconds, that overlapping pauses keep collection off
#: before an exiting pause forces one collection. Long enough that a single
#: large parse never pays for a pass; short enough that chained pauses cannot
#: disable collection for a whole run.
MAX_CONTINUOUS_PAUSE_S = 0.5
_clock = time.monotonic
_off_since = 0.0


@contextmanager
def gc_paused() -> Iterator[None]:
    """Pause cyclic garbage collection for the ``with`` body, then restore
    whatever setting was in effect before the outermost overlapping pause."""
    global _active, _restore_enabled, _off_since
    with _lock:
        if _active == 0:
            _restore_enabled = gc.isenabled()
            _off_since = _clock()
            gc.disable()
        _active += 1
    try:
        yield
    finally:
        overdue = False
        with _lock:
            _active -= 1
            if _active == 0:
                if _restore_enabled:
                    gc.enable()
            elif _restore_enabled and _clock() - _off_since >= MAX_CONTINUOUS_PAUSE_S:
                # Other pauses are still live and will keep collection off:
                # collect once now rather than never (see module docstring).
                _off_since = _clock()
                overdue = True
        if overdue:
            gc.collect()


def loads_acyclic(document: str | bytes | bytearray, **kwargs: Any) -> Any:
    """``json.loads(document, **kwargs)`` with the collector paused.

    For a document that decodes to a *tree* -- any JSON without an
    ``object_hook`` that stitches decoded values into a cycle, which is
    every caller here."""
    with gc_paused():
        return json.loads(document, **kwargs)


#: Generation thresholds while a live dump runs. A dump builds millions of
#: long-lived model objects, and at CPython's default 2000-allocation
#: generation-0 threshold the collector rescans them over and over without
#: freeing anything. Measured on oneCCL 2021.15 (field run, identical output):
#: dump 44 -> 37.5 s on 3.12 and 41 -> 35.4 s on 3.15, for 20-90 MB more
#: peak. No effect on a free-threaded build, which skips it.
RELAXED_GC_THRESHOLDS = (100_000, 50, 100)

_relax_lock = threading.Lock()
_relax_active = 0
_relax_restore: tuple[int, ...] = ()


def _gil_enabled() -> bool:
    probe = getattr(sys, "_is_gil_enabled", None)
    return True if probe is None else bool(probe())


@contextmanager
def gc_relaxed(enabled: bool = True) -> Iterator[None]:
    """Raise the collector's thresholds for the ``with`` body, then restore
    the ones in effect before the outermost overlapping scope.

    Same overlap rule as :func:`gc_paused` (two sides dumping on two
    threads share one scope). A no-op when *enabled* is false (the
    ``low-memory`` profile keeps the default) or on a free-threaded build.
    """
    global _relax_active, _relax_restore
    if not enabled or not _gil_enabled():
        yield
        return
    with _relax_lock:
        if _relax_active == 0:
            _relax_restore = gc.get_threshold()
            gc.set_threshold(*RELAXED_GC_THRESHOLDS)
        _relax_active += 1
    try:
        yield
    finally:
        with _relax_lock:
            _relax_active -= 1
            if _relax_active == 0:
                gc.set_threshold(*_relax_restore)
