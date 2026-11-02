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

"""Byte-quota LRU eviction for an on-disk cache directory (ADR-062 D11, A2.3).

D11: "Eviction uses a byte quota with LRU rather than an entry count." An
entry count lets one 500 MB snapshot and one 50 KB snapshot cost the same,
so a cache of large release members could grow without bound in bytes while
staying under its count. This module owns only the *policy*; the caller owns
the directory layout and which file patterns are entries.

**Recency** is the entry file's mtime, which a cache hit refreshes
(``touch``). Ties break on the file name so the victim order is a function of
directory state alone, never of ``os.scandir`` order.

**Protected entries** (the one just written) are never evicted, even when
they alone exceed the quota: evicting the entry a caller has just stored
would make every write of a snapshot larger than the quota a guaranteed
miss, which is a cache that silently does nothing. The quota is therefore a
bound on everything *except* the protected entries.

**Crash residue.** A write interrupted between temp-file creation and
``os.replace`` leaves a hidden ``.<name>.<random>.tmp`` next to the entries
(``snapshot_io._open_unique_temp``/``atomic_file``). Such a file is never an
entry -- no reader opens it -- and nothing else removes it, so
:func:`evict_directory_to_quota` deletes one once it is older than
*stale_temp_seconds*; a younger one may belong to a write still in flight
in another process and is left alone.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "CacheEntry",
    "evict_directory_to_quota",
    "select_victims",
]

_logger = logging.getLogger("abicheck.cache")


@dataclass(frozen=True)
class CacheEntry:
    """One cache file as eviction sees it."""

    name: str
    size: int
    last_used: float


def select_victims(
    entries: Iterable[CacheEntry],
    quota_bytes: int,
    *,
    protected: Collection[str] = (),
) -> list[str]:
    """Names to delete, least recently used first, so that the unprotected
    entries that remain total at most *quota_bytes*.

    Evicts the minimum LRU prefix: walking unprotected entries oldest first
    (ties by name), it stops as soon as the remaining unprotected bytes fit.
    Protected entries are neither counted nor evicted.
    """
    if quota_bytes < 0:
        raise ValueError(f"quota_bytes must be >= 0, got {quota_bytes}")
    candidates = sorted(
        (e for e in entries if e.name not in protected),
        key=lambda e: (e.last_used, e.name),
    )
    total = sum(e.size for e in candidates)
    victims: list[str] = []
    for entry in candidates:
        if total <= quota_bytes:
            break
        victims.append(entry.name)
        total -= entry.size
    return victims


def _is_stale_temp(name: str, mtime: float, now: float, grace: float) -> bool:
    return name.startswith(".") and name.endswith(".tmp") and now - mtime > grace


def evict_directory_to_quota(
    directory: Path,
    *,
    suffixes: Sequence[str],
    quota_bytes: int,
    protected: Collection[str] = (),
    stale_temp_seconds: float = 3600.0,
) -> list[str]:
    """Apply :func:`select_victims` to the files in *directory* whose names
    end with one of *suffixes*, and sweep stale crash residue.

    Returns the entry names actually removed. Every filesystem error is
    swallowed per file: a cache's eviction must never fail the run that
    triggered it, and an entry removed concurrently by another process is
    already the desired outcome.
    """
    now = time.time()
    entries: list[CacheEntry] = []
    try:
        scan = list(os.scandir(directory))
    except OSError:
        return []
    for item in scan:
        try:
            if not item.is_file(follow_symlinks=False):
                continue
            st = item.stat(follow_symlinks=False)
        except OSError:
            continue
        if _is_stale_temp(item.name, st.st_mtime, now, stale_temp_seconds):
            _unlink(directory / item.name)
            continue
        if item.name.startswith(".") or not item.name.endswith(tuple(suffixes)):
            continue
        entries.append(CacheEntry(item.name, st.st_size, st.st_mtime))
    removed: list[str] = []
    for name in select_victims(entries, quota_bytes, protected=protected):
        if _unlink(directory / name):
            removed.append(name)
            _logger.debug("Cache evict: %s", name[:12])
    return removed


def _unlink(path: Path) -> bool:
    try:
        path.unlink()
    except OSError:
        return False
    return True
