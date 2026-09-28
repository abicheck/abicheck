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

"""Process-wide memo for the header-only ``clang -M`` include pass.

:meth:`~abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor.extract`
is a pure function of its arguments *and* of the files it reads. None of
those arguments depend on which library of a release is being dumped, so a
directory/package fan-out used to re-run the identical ``clang -M`` set once
per side per member -- measured at ~24% of a 28-library release's wall time,
54 of 56 passes redundant.

The memo is keyed on every argument that reaches the ``clang -M`` argv, and
each entry carries a *freshness witness*: the ``(mtime_ns, size)`` of every
header parsed, every file the pass reported as included, and every include
search directory (a file added to a directory changes that directory's
mtime, which covers a new header shadowing an older one). An entry whose
witness no longer matches the filesystem is recomputed, so a long-lived
process never serves an include map for files that changed under it.

Concurrent callers of the same key wait for the one computation in flight
rather than each launching their own ``clang -M`` burst.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass

IncludeResult = tuple[dict[str, list[str]], list[str]]

# Distinct include-pass requests in one process are few (one per distinct
# header set x toolchain); a small bound keeps a long-lived process flat.
_MAX_ENTRIES = 32

_Witness = tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class _Entry:
    include_map: dict[str, list[str]]
    diagnostics: tuple[str, ...]
    witness: _Witness


_lock = threading.Lock()
_entries: OrderedDict[tuple[object, ...], _Entry] = OrderedDict()
_inflight: dict[tuple[object, ...], threading.Lock] = {}


def _stat_witness(paths: Iterable[str]) -> _Witness:
    out: list[tuple[str, int, int]] = []
    for p in sorted(set(paths)):
        try:
            st = os.stat(p)
        except OSError:
            out.append((p, -1, -1))
            continue
        out.append((p, st.st_mtime_ns, st.st_size))
    return tuple(out)


def _witness_paths(
    headers: list[str], includes: list[str], include_map: dict[str, list[str]]
) -> list[str]:
    paths = [*headers, *includes]
    for deps in include_map.values():
        paths.extend(deps)
    return paths


def _copy(entry: _Entry) -> IncludeResult:
    # Callers own what they receive; the memoized value must stay intact.
    return (
        {k: list(v) for k, v in entry.include_map.items()},
        list(entry.diagnostics),
    )


def memoized_include_extract(
    key: tuple[object, ...],
    headers: list[str],
    includes: list[str],
    compute: Callable[[], IncludeResult],
) -> IncludeResult:
    """Return *compute()*'s result for *key*, reusing a still-fresh entry.

    *key* must name every input that reaches the ``clang -M`` argv;
    *headers*/*includes* seed the freshness witness alongside the included
    files the result itself reports.
    """
    with _lock:
        gate = _inflight.setdefault(key, threading.Lock())
    with gate:
        with _lock:
            entry = _entries.get(key)
        if entry is not None:
            if _stat_witness(p for p, _, _ in entry.witness) == entry.witness:
                with _lock:
                    _entries.move_to_end(key)
                return _copy(entry)
        include_map, diagnostics = compute()
        fresh = _Entry(
            include_map={k: list(v) for k, v in include_map.items()},
            diagnostics=tuple(diagnostics),
            witness=_stat_witness(_witness_paths(headers, includes, include_map)),
        )
        with _lock:
            _entries[key] = fresh
            _entries.move_to_end(key)
            while len(_entries) > _MAX_ENTRIES:
                _entries.popitem(last=False)
        return _copy(fresh)


def clear_include_memo() -> None:
    """Drop every memoized include map (tests; explicit invalidation)."""
    with _lock:
        _entries.clear()
