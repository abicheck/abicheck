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

"""Snapshot-level cache for avoiding redundant binary analysis.

Cache key = SHA-256 of (binary content hash + header mtimes + compiler params).
Cache location = ``$XDG_CACHE_HOME/abi_check/snapshots/<key>.json`` or
``~/.cache/abi_check/snapshots/<key>.json``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .model import AbiSnapshot

_logger = logging.getLogger("abicheck.cache")

#: Maximum number of cached snapshots (LRU eviction by mtime).
MAX_ENTRIES: int = 100

#: Bumped whenever a change to the dumping/provenance pipeline could alter a
#: snapshot's content without changing any of the caller-supplied cache-key
#: inputs (headers/includes/version/lang/``extra``) — folding it into every
#: key invalidates all previously-cached entries on upgrade rather than risk
#: serving a stale snapshot computed by an older, behaviorally-different
#: abicheck version.
_SNAPSHOT_CACHE_VERSION: str = "3"
# v2: castxml's CvQualifiedType type-name spelling changed for a
# volatile-qualified pointer/reference VALUE (now a suffix, "T * volatile",
# matching clang's own convention, rather than always a prefix) -- an
# unconditional change to the default/most common cacheable dump path (Codex
# review). Any G28 Phase 3/4 hybrid-provenance or clang-layout-tool fact this
# PR also introduced is additionally covered by AbiSnapshot.SCHEMA_VERSION
# (serialization.py) for the on-disk snapshot JSON format itself; this
# constant is specifically for the separate whole-snapshot disk cache
# (snapshot_cache.py), which persists across process invocations and isn't
# gated by that schema version at all.
#
# v3 (G29 Phase A): the L2 header-only semantic graph became unconditional
# (previously gated behind the now-removed --header-graph/--header-graph-includes
# flags). service_dump_cache._dump_is_cacheable() allows the same plain
# "binary + public headers" shape onto this cache that a pre-upgrade,
# no-graph dump would already have stored under v2 -- without this bump, a
# warm cache from before the upgrade would be replayed verbatim and silently
# omit the new default-on graph until manually cleared (Codex review).
#
# Also folded into v3's key computation (_cache_key below): before G31 Phase
# A, a header-graph-enabled dump was *always* uncacheable, so a transitively
# included header changing under one of the ``-I``/``includes`` directories
# (e.g. a public header pulling in ``inc/detail.h``) always forced a live
# re-dump. Now that the same plain shape is cacheable, ``_cache_key`` walks
# each include directory and folds in the (path, mtime) of every header-like
# file found there -- not just the directory's own path -- so a transitive
# header edit invalidates the cache the same way editing an explicitly
# passed header already does (Codex review).


def _get_cache_dir() -> Path:
    """Return the cache directory, deferring Path.home() to call time."""
    xdg = os.environ.get("XDG_CACHE_HOME", "")
    if xdg:
        base = Path(xdg)
    else:
        try:
            base = Path.home() / ".cache"
        except RuntimeError:
            import tempfile

            base = Path(tempfile.gettempdir())
    return base / "abi_check" / "snapshots"


# Module-level reference (can be monkeypatched in tests).
_CACHE_DIR: Path = _get_cache_dir()

#: Extensions treated as headers when walking an include directory for cache
#: invalidation (:func:`_cache_key`) -- deliberately the same "looks like a
#: header" set other extractors in this codebase already use, not an attempt
#: at a fully general build-system-aware header classifier.
_HEADER_EXTENSIONS: frozenset[str] = frozenset(
    {".h", ".hh", ".hpp", ".hxx", ".h++", ".inl", ".ipp", ".tcc"}
)


def _hash_include_dir_headers(h: hashlib._Hash, inc: Path) -> None:
    """Fold the (relative path, mtime) of every header-like file under
    ``inc`` into ``h``, so an edit to a header reached only transitively
    through an ``-I``/``--include`` directory (never itself passed as an
    explicit ``headers`` entry) still invalidates the whole-snapshot cache.

    Best-effort and bounded by whatever is actually on disk under ``inc`` --
    a missing/unreadable directory degrades to hashing nothing extra (same
    as before this function existed) rather than raising, matching this
    module's existing "any read problem is cache-safe, never a crash"
    stance (see ``lookup``/``store``).
    """
    try:
        entries = sorted(
            p for p in inc.rglob("*") if p.suffix.lower() in _HEADER_EXTENSIONS
        )
    except OSError:
        return
    for p in entries:
        try:
            h.update(str(p.relative_to(inc)).encode())
            h.update(str(p.stat().st_mtime_ns).encode())
        except OSError:
            h.update(b"MISSING")


def _cache_key(
    binary_path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    extra: str = "",
) -> str:
    """Compute a deterministic cache key from all inputs that affect the snapshot.

    ``extra`` is an opaque, caller-assembled string folding in any additional
    inputs that affect the resulting snapshot but aren't one of this
    function's named parameters (e.g. the binary format, header-AST backend,
    or public-header scoping set) — kept generic here so this module doesn't
    need to know every option a caller's dump pipeline exposes.
    """
    h = hashlib.sha256()
    # Binary content hash — chunked to avoid loading huge files into memory
    try:
        with open(binary_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""  # uncacheable
    # Header mtimes (sorted for determinism). A `headers` entry can itself be
    # a directory (`-H include/`) -- `_attach_header_graph`/the header-AST
    # parser expand that into every header file found under it, so the
    # directory's own mtime alone (which most filesystems only bump when an
    # entry is added/removed, not when an existing file's contents change)
    # is not enough to invalidate the cache on an in-place edit; walk it the
    # same way an `includes` directory is walked below (Codex review).
    for hdr in sorted(headers):
        try:
            h.update(str(hdr).encode())
            h.update(str(hdr.stat().st_mtime_ns).encode())
            if hdr.is_dir():
                _hash_include_dir_headers(h, hdr)
        except OSError:
            h.update(b"MISSING")
    # Include dirs: the directory's own path, plus the (relative path, mtime)
    # of every header-like file found under it -- a transitively included
    # header never passed as an explicit `headers` entry must still
    # invalidate the cache when it changes (Codex review, see the v3 note
    # on _SNAPSHOT_CACHE_VERSION above).
    for inc in sorted(includes):
        h.update(str(inc).encode())
        _hash_include_dir_headers(h, inc)
    # Compiler params
    h.update(version.encode())
    h.update(lang.encode())
    h.update(extra.encode())
    h.update(_SNAPSHOT_CACHE_VERSION.encode())
    return h.hexdigest()


def lookup(
    binary_path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    extra: str = "",
) -> AbiSnapshot | None:
    """Look up a cached snapshot. Returns None on miss."""
    key = _cache_key(binary_path, headers, includes, version, lang, extra=extra)
    if not key:
        return None
    cache_file = _CACHE_DIR / f"{key}.json"
    try:
        from .serialization import load_snapshot

        snap = load_snapshot(cache_file)
        # Touch mtime for LRU
        cache_file.touch()
        _logger.debug("Cache hit: %s → %s", binary_path.name, key[:12])
        return snap
    except Exception:
        _logger.debug("Cache read error for %s, treating as miss", key[:12])
        return None


def store(
    snap: AbiSnapshot,
    binary_path: Path,
    headers: list[Path],
    includes: list[Path],
    version: str,
    lang: str,
    *,
    extra: str = "",
) -> None:
    """Store a snapshot in the cache (atomic write via rename)."""
    key = _cache_key(binary_path, headers, includes, version, lang, extra=extra)
    if not key:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _CACHE_DIR / f"{key}.json"
        from .serialization import snapshot_to_json

        # Write to temp file then atomic rename to avoid corruption
        fd, tmp_path = tempfile.mkstemp(dir=_CACHE_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(snapshot_to_json(snap))
            os.replace(tmp_path, cache_file)
        except BaseException:
            os.unlink(tmp_path)
            raise
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
    """Remove oldest entries if cache exceeds MAX_ENTRIES."""
    try:
        entries = sorted(_CACHE_DIR.glob("*.json"), key=_safe_mtime)
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
