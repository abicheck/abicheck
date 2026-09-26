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

"""Size budget for the on-disk header-AST caches (castxml XML / clang JSON).

``snapshot_cache`` has always bounded its own entries (``MAX_ENTRIES``); the
per-backend AST cache directories had no bound at all, and a long-lived
host accumulated tens of GiB (a profiled MKL host: 54 GB of clang JSON with
single entries over 4 GB). :func:`enforce_ast_cache_budget` trims one
backend directory back under a byte budget, least-recently-used first.

The budget is ``ABICHECK_AST_CACHE_MAX_BYTES`` (bytes, per backend
directory; ``0`` disables eviction), default :data:`DEFAULT_MAX_BYTES`.
Entries modified within :data:`MIN_AGE_SECONDS` are never evicted, so a
concurrent process's just-written (or just-read) entry cannot vanish under
it; a reader that loses a race to an older entry simply re-parses.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from . import cache_integrity

__all__ = [
    "DEFAULT_MAX_BYTES",
    "MIN_AGE_SECONDS",
    "configured_max_bytes",
    "enforce_ast_cache_budget",
    "enforce_once",
]

log = logging.getLogger(__name__)

DEFAULT_MAX_BYTES: int = 16 * 1024**3
MIN_AGE_SECONDS: float = 3600.0
_ENV = "ABICHECK_AST_CACHE_MAX_BYTES"
_ENTRY_SUFFIXES = (".xml", ".json")

_checked: set[Path] = set()
_lock = threading.Lock()


def configured_max_bytes() -> int:
    raw = os.environ.get(_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_BYTES
    try:
        value = int(raw)
    except ValueError:
        log.warning("ignoring non-integer %s=%r", _ENV, raw)
        return DEFAULT_MAX_BYTES
    return max(value, 0)


def enforce_ast_cache_budget(
    cache_dir: Path,
    *,
    max_bytes: int | None = None,
    now: float | None = None,
    min_age_seconds: float = MIN_AGE_SECONDS,
) -> list[Path]:
    """Evict least-recently-used entries until *cache_dir* fits *max_bytes*.

    Returns the evicted entry paths. Recency is ``max(atime, mtime)``; each
    entry is evicted together with its integrity sidecar and counted with
    it. Best-effort: a filesystem error on any one entry skips that entry.
    """
    budget = configured_max_bytes() if max_bytes is None else max_bytes
    if budget <= 0:
        return []
    current = time.time() if now is None else now
    entries: list[tuple[float, int, Path]] = []
    total = 0
    try:
        children = list(cache_dir.iterdir())
    except OSError:
        return []
    for child in children:
        if child.suffix not in _ENTRY_SUFFIXES or child.name.startswith("."):
            continue
        try:
            st = child.stat()
        except OSError:
            continue
        size = st.st_size
        try:
            size += cache_integrity.sidecar_path(child).stat().st_size
        except OSError:
            pass
        total += size
        entries.append((max(st.st_atime, st.st_mtime), size, child))
    if total <= budget:
        return []
    evicted: list[Path] = []
    for recency, size, entry in sorted(entries, key=lambda e: (e[0], str(e[2]))):
        if total <= budget:
            break
        if current - recency < min_age_seconds:
            continue
        try:
            cache_integrity.evict(entry)
        except OSError:
            continue
        total -= size
        evicted.append(entry)
    if evicted:
        log.info(
            "AST cache %s over budget: evicted %d entries (budget %d bytes)",
            cache_dir,
            len(evicted),
            budget,
        )
    return evicted


def enforce_once(cache_dir: Path) -> None:
    """Run :func:`enforce_ast_cache_budget` at most once per process per dir."""
    with _lock:
        if cache_dir in _checked:
            return
        _checked.add(cache_dir)
    enforce_ast_cache_budget(cache_dir)
