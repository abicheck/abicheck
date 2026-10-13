# Copyright 2026 Nikolay Petrov
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

"""On-disk entries of the dump cache: layout, reads and writes, LRU eviction and
the write policy. Keys come from :mod:`.key`. See the package docstring."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ....extract import progress
from ....model.execution_cache_scoped import DiskCache

if TYPE_CHECKING:
    from ....model import AbiSnapshot

_logger = logging.getLogger("abicheck.cache")

#: Maximum number of cached snapshots (LRU eviction by mtime).
MAX_ENTRIES: int = 100


def _get_cache_dir() -> Path:
    """Return the cache directory, deferring Path.home() to call time."""
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    if xdg:
        base = Path(xdg)
    else:
        try:
            base = Path.home() / ".cache"
        except RuntimeError:
            base = Path(tempfile.gettempdir())
    return base / "abi_check" / "snapshots"


# Module-level reference (can be monkeypatched in tests).
_CACHE_DIR: Path = _get_cache_dir()

#: The whole-snapshot disk cache's policy and counters (design-hardening
#: plan, Phase 4): ``ABICHECK_REFERENCE_MODE=1`` makes every lookup a miss
#: and every store a no-op, so a reference run always dumps afresh.
SNAPSHOT_DISK_CACHE = DiskCache("abicheck.snapshot_cache.disk")


def _write_enabled() -> bool:
    """``ABICHECK_SNAPSHOT_CACHE_WRITE`` (default on): ``0`` keeps reading the
    whole-snapshot cache but never populates it -- for a one-shot run (CI on
    a fresh runner) that would pay to serialize and write snapshots no later
    run reads. Measured on a 28-member release: populating cost ~30% wall and
    ~37% peak RSS."""
    from ....extract.env_flags import env_flag

    return env_flag("ABICHECK_SNAPSHOT_CACHE_WRITE")


def _lookup_key(key: str, binary_path: Path) -> AbiSnapshot | None:
    """Look up an entry using a key already bound to validated inputs.

    ADR-059: new entries are written zstd-compressed (``<key>.json.zst``);
    this checks that first, then falls back to a legacy plain ``<key>.json``
    entry so a cache warmed by an older abicheck is not thrown away wholesale
    on upgrade. A corrupt/truncated compressed entry is a cache miss, same as
    any other read problem here -- never a caller-visible failure."""
    if not key:
        return None
    from ....workflows import memory_trace

    started = time.monotonic()
    # Traced: on a hit this load replaces the whole dump, so without it
    # `ABICHECK_MEMORY_TRACE` would see none of a warm run's `dump.*` marks.
    with memory_trace.phase("dump.cache.lookup"):
        snap = SNAPSHOT_DISK_CACHE.lookup(lambda: _read_entry(key, binary_path))
    if snap is not None:
        # A hit replaces every extraction phase, each of which reports its own
        # progress line. Say so, or a warm run's redirected log is silent --
        # indistinguishable from progress reporting being broken.
        progress.note(
            f"{binary_path.name}: snapshot cache hit, extraction skipped "
            f"(loaded in {time.monotonic() - started:.1f}s)"
        )
    return snap


def _read_entry(key: str, binary_path: Path) -> AbiSnapshot | None:
    from ....serialization import load_snapshot

    for cache_file in (_CACHE_DIR / f"{key}.json.zst", _CACHE_DIR / f"{key}.json"):
        if not cache_file.exists():
            continue
        try:
            snap = load_snapshot(cache_file)
        except Exception:
            _logger.debug("Cache read error for %s, treating as miss", key[:12])
            continue
        # Touch mtime for LRU
        try:
            cache_file.touch()
        except OSError:
            pass
        _logger.debug("Cache hit: %s → %s", binary_path.name, key[:12])
        return snap
    return None


def _store_key(snap: AbiSnapshot, key: str, binary_path: Path) -> None:
    """Store under a previously computed, post-execution-validated key.

    ADR-059: written zstd-compressed at the fast, cache-tuned level (this
    runs on nearly every ``dump``/``compare`` invocation, unlike a baseline/
    release write) via the canonical atomic writer -- a plain-JSON entry
    from before this cache was compression-aware is never written again,
    only read as a fallback by :func:`lookup_key`. If a stale legacy
    ``<key>.json`` for the same key exists, it's removed so a lookup doesn't
    keep preferring an old snapshot over a freshly stored one."""
    if not key:
        return
    from ....workflows import memory_trace

    with memory_trace.phase("dump.cache.store"):
        SNAPSHOT_DISK_CACHE.store(lambda: _write_entry(snap, key, binary_path))


def _write_entry(snap: AbiSnapshot, key: str, binary_path: Path) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _CACHE_DIR / f"{key}.json.zst"
        from ....serialization import write_snapshot
        from ....snapshot_io import ZSTD_LEVEL_CACHE, SnapshotCompression

        write_snapshot(
            snap,
            cache_file,
            compression=SnapshotCompression.ZSTD.value,
            zstd_level=ZSTD_LEVEL_CACHE,
            # Streamed: a cache entry is private and any read failure is
            # already a miss, so the declared-size cross-check a one-shot
            # zstd frame carries buys nothing here, while holding the whole
            # JSON text and its bytes cost ~0.5 GiB on a large snapshot.
            zstd_content_size=False,
        )
        legacy_file = _CACHE_DIR / f"{key}.json"
        if legacy_file.exists():
            try:
                legacy_file.unlink()
            except OSError:
                pass
        _logger.debug("Cache store: %s → %s", binary_path.name, key[:12])
        _evict_if_needed()
    except Exception as exc:
        # Caching is a pure optimization layered on top of a real dump that
        # already succeeded — any failure here (disk full, an unserializable
        # field, ...) must never surface as a caller-visible error. Broad
        # except is deliberate (mirrors lookup()'s "any read problem is a
        # miss" stance): a write-time TypeError from an unusual snapshot is
        # exactly as harmless to swallow as an OSError.
        _logger.debug("Cache write failed: %s", exc)


def _safe_mtime(p: Path) -> float:
    """Return file mtime, or 0.0 if stat fails (e.g. concurrent deletion)."""
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _evict_if_needed() -> None:
    """Remove oldest entries if cache exceeds MAX_ENTRIES.

    Globs both the current ``*.json.zst`` suffix and the legacy plain
    ``*.json`` suffix (ADR-059) so LRU eviction accounts for a cache
    directory that still has pre-upgrade entries mixed in with new ones."""
    try:
        entries = sorted(
            (*_CACHE_DIR.glob("*.json.zst"), *_CACHE_DIR.glob("*.json")),
            key=_safe_mtime,
        )
    except OSError:
        return
    excess = len(entries) - MAX_ENTRIES
    if excess <= 0:
        return
    for p in entries[:excess]:
        try:
            p.unlink()
            _logger.debug("Cache evict: %s", p.name[:12])
        except OSError:
            pass
