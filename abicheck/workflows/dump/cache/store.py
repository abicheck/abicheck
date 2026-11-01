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

from ....extract import cache_dep_manifest, progress
from ....extract.cache_dep_manifest import DepManifest
from ....model.execution_cache_scoped import DiskCache
from ....storage.cache_integrity import DEPS_SUFFIX
from . import declaring_headers

if TYPE_CHECKING:
    from ....model import AbiSnapshot

_logger = logging.getLogger("abicheck.cache")

#: Byte quota per cache tier (ADR-062 D11: byte-quota LRU, not an entry
#: count). Recency is the entry's mtime, refreshed on every hit. Internal
#: policy like D12's compression levels, not a user knob.
MAX_BYTES: int = 4 * 1024**3


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

#: The manifest of a snapshot no header parse fed: nothing to re-check.
_NO_DEPS = DepManifest()


def _write_enabled() -> bool:
    """``ABICHECK_SNAPSHOT_CACHE_WRITE`` (default on): ``0`` keeps reading the
    whole-snapshot cache but never populates it -- for a one-shot run (CI on
    a fresh runner) that would pay to serialize and write snapshots no later
    run reads. Measured on a 28-member release: populating cost ~30% wall and
    ~37% peak RSS."""
    from ....extract.env_flags import env_flag

    return env_flag("ABICHECK_SNAPSHOT_CACHE_WRITE")


def _lookup_key(key: str, binary_path: Path, subdir: str = "") -> AbiSnapshot | None:
    """Look up an entry using a key already bound to validated inputs.

    ADR-059: new entries are written zstd-compressed (``<key>.json.zst``);
    this checks that first, then falls back to a legacy plain ``<key>.json``
    entry so a cache warmed by an older abicheck is not thrown away wholesale
    on upgrade. A corrupt/truncated compressed entry is a cache miss, same as
    any other read problem here -- never a caller-visible failure."""
    if not key:
        return None
    from ... import memory_trace

    started = time.monotonic()
    # Traced: on a hit this load replaces the whole dump, so without it
    # `ABICHECK_MEMORY_TRACE` would see none of a warm run's `dump.*` marks.
    with memory_trace.phase("dump.cache.lookup"):
        snap = SNAPSHOT_DISK_CACHE.lookup(lambda: _read_entry(key, binary_path, subdir))
    if snap is not None:
        # A hit replaces every extraction phase, each of which reports its own
        # progress line. Say so, or a warm run's redirected log is silent --
        # indistinguishable from progress reporting being broken.
        progress.note(
            f"{binary_path.name}: snapshot cache hit, extraction skipped "
            f"(loaded in {time.monotonic() - started:.1f}s)"
        )
    return snap


def _entry_dir(subdir: str) -> Path:
    """The directory holding one tier's entries. A tier in its own
    sub-directory keeps its own LRU budget (:func:`_evict_if_needed` globs
    one directory), so finished entries (:mod:`.finished`) never evict the
    raw entries they are derived from, nor the reverse."""
    return _CACHE_DIR / subdir if subdir else _CACHE_DIR


def _read_entry(key: str, binary_path: Path, subdir: str = "") -> AbiSnapshot | None:
    from ....serialization import load_snapshot

    base = _entry_dir(subdir)
    # ADR-062 D11: the key folds system include dirs by spelling only; the
    # system headers the entry's parses read are checked here, against the
    # manifest stored beside it. No manifest, or a changed file, is a miss.
    manifest_entry = base / key
    if not cache_dep_manifest.entry_valid(manifest_entry):
        return None
    for cache_file in (base / f"{key}.json.zst", base / f"{key}.json"):
        if not cache_file.exists():
            continue
        try:
            snap = declaring_headers.attach(load_snapshot(cache_file), base, key)
        except Exception:
            _logger.debug(
                "Cache read error for %s, treating as miss", key[:12], exc_info=True
            )
            continue
        if snap is None:  # header-derived entry without its attribution
            continue
        cache_dep_manifest.note(
            cache_dep_manifest.recall(str(manifest_entry), manifest_entry)
        )
        # Touch mtime for LRU (the sidecars too, so they age with their entry)
        for touched in (
            cache_file,
            declaring_headers.sidecar_path(base, key),
            cache_dep_manifest.sidecar_path(manifest_entry),
        ):
            try:
                os.utime(touched)  # never creates a missing sidecar
            except OSError:
                pass
        _logger.debug("Cache hit: %s → %s", binary_path.name, key[:12])
        return snap
    return None


def _store_key(
    snap: AbiSnapshot,
    key: str,
    binary_path: Path,
    subdir: str = "",
    *,
    deps: DepManifest | None = _NO_DEPS,
) -> None:
    """Store under a previously computed, post-execution-validated key.

    ADR-059: written zstd-compressed at the fast, cache-tuned level (this
    runs on nearly every ``dump``/``compare`` invocation, unlike a baseline/
    release write) via the canonical atomic writer -- a plain-JSON entry
    from before this cache was compression-aware is never written again,
    only read as a fallback by :func:`lookup_key`. If a stale legacy
    ``<key>.json`` for the same key exists, it's removed so a lookup doesn't
    keep preferring an old snapshot over a freshly stored one.

    *deps* is the dependency manifest of the parses that produced *snap*
    (:mod:`abicheck.extract.cache_dep_manifest`), written beside the entry;
    ``None`` -- dependencies unknown or changed mid-dump -- stores nothing.
    The default, no dependencies, fits a snapshot no header parse fed."""
    if not key or deps is None:
        return
    from ... import memory_trace

    with memory_trace.phase("dump.cache.store"):
        SNAPSHOT_DISK_CACHE.store(
            lambda: _write_entry(snap, key, binary_path, subdir, deps=deps)
        )


def _write_entry(
    snap: AbiSnapshot,
    key: str,
    binary_path: Path,
    subdir: str = "",
    *,
    deps: DepManifest = _NO_DEPS,
) -> None:
    try:
        base = _entry_dir(subdir)
        base.mkdir(parents=True, exist_ok=True)
        cache_file = base / f"{key}.json.zst"
        declaring_headers.write_sidecar(snap, base, key)
        # Before the entry is published: an entry never exists without the
        # manifest it was written with.
        if not cache_dep_manifest.write_sidecar(base / key, deps):
            return
        from ....snapshot_io import ZSTD_LEVEL_CACHE, SnapshotCompression
        from ....storage.snapshot_codec import write_snapshot_compact

        # Compact single-shot JSON: a cache entry is machine-only, so the
        # indented streamed writer's whitespace is pure cost (see
        # ``write_snapshot_compact``). Entries are private and any read
        # failure is already a miss.
        write_snapshot_compact(
            snap,
            cache_file,
            compression=SnapshotCompression.ZSTD.value,
            zstd_level=ZSTD_LEVEL_CACHE,
        )
        legacy_file = base / f"{key}.json"
        if legacy_file.exists():
            try:
                legacy_file.unlink()
            except OSError:
                pass
        _logger.debug("Cache store: %s → %s", binary_path.name, key[:12])
        _evict_if_needed(base, protect=cache_file.name)
    except Exception as exc:
        # Caching is a pure optimization layered on top of a real dump that
        # already succeeded — any failure here (disk full, an unserializable
        # field, ...) must never surface as a caller-visible error. Broad
        # except is deliberate (mirrors lookup()'s "any read problem is a
        # miss" stance): a write-time TypeError from an unusual snapshot is
        # exactly as harmless to swallow as an OSError.
        _logger.debug("Cache write failed: %s", exc, exc_info=True)


def _evict_if_needed(base: Path | None = None, *, protect: str = "") -> None:
    """Evict least-recently-used entries until the tier fits :data:`MAX_BYTES`
    (``storage.byte_quota_lru``), never the entry just written (*protect*),
    and sweep temp files a crashed write left behind.

    Counts both the current ``*.json.zst`` suffix and the legacy plain
    ``*.json`` suffix (ADR-059) so a directory with pre-upgrade entries is
    bounded too."""
    from ....storage.byte_quota_lru import evict_directory_to_quota

    evict_directory_to_quota(
        _CACHE_DIR if base is None else base,
        suffixes=(
            ".json.zst",
            ".json",
            declaring_headers.SIDECAR_SUFFIX,
            DEPS_SUFFIX,
        ),
        quota_bytes=MAX_BYTES,
        protected=(protect,) if protect else (),
    )
