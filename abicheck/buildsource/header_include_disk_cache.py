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

"""On-disk cache for the header-only ``clang -M`` include pass (G31 Phase D).

:class:`~abicheck.buildsource.header_graph.ClangHeaderIncludeExtractor` runs
one ``clang -M`` per top-level header. The in-process memo
(:mod:`~abicheck.buildsource.header_include_memo`) only helps a second call
inside one process; a fresh ``abicheck dump`` of the same headers used to
spawn every probe again. This module persists each header's result so a
warm run issues no ``clang -M`` at all.

One entry per header, named by a key over everything that reaches that
header's ``clang -M`` result:

* the cache schema and :func:`abicheck_code_fingerprint` (the depfile replay
  logic lives in this package, so a changed replay invalidates its entries);
* the clang binary identity (:func:`~abicheck.extract.headers.toolchain._tool_identity`,
  which folds in the binary's content digest);
* the language mode and the header's own content digest;
* the exact argv the probe is built from.

A key match alone is not enough: the stored include closure names other
files, and a changed *included* header changes the result without changing
the key. So an entry also carries a witness -- the content digest of every
file it reports, and the mtime of every include search directory (a new file
that shadows an older one changes its directory's mtime, the same freshness
signal the in-process memo uses). A witness mismatch is a miss.

A result carrying diagnostics is never stored: a partial run may be
transient, and serving it again would pin the header as failed after the
cause cleared. Reference mode (``ABICHECK_REFERENCE_MODE=1``) disables the
cache through :data:`INCLUDE_DISK_CACHE`, like every other disk cache here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Sequence
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..model.execution_cache_scoped import DiskCache
from ..model.graph_identity import header_node_id
from ..storage.ast_cache_budget import note_use
from ..storage.ast_cache_location import backend_cache_dir
from ..storage.code_identity import abicheck_code_fingerprint

if TYPE_CHECKING:
    from .build_evidence import CompileUnit

__all__ = [
    "INCLUDE_DISK_CACHE",
    "cached_include_pass",
    "file_digest",
    "include_cache_dir",
    "include_cache_key",
    "load_cached_includes",
    "store_cached_includes",
]

log = logging.getLogger(__name__)

INCLUDE_DISK_CACHE = DiskCache("abicheck.buildsource.header_include_disk")

#: Names the entry layout. Bump when the stored document changes shape; the
#: code fingerprint already covers edits to the replay logic itself.
INCLUDE_CACHE_SCHEMA = "abicheck-header-include-cache/1"

#: Witness value for a directory that does not exist (clang ignores it, so
#: creating it later changes the result and must invalidate the entry).
_MISSING_DIR = -1

_CHUNK = 1 << 20


def include_cache_dir() -> Path:
    """The directory holding include-pass entries (created on first use).

    A module-level seam so a test can point it at its own directory.
    """
    return backend_cache_dir("include")


def file_digest(path: str) -> str | None:
    """SHA-256 of *path*'s bytes, or ``None`` when it cannot be read."""
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(partial(fh.read, _CHUNK), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _dir_mtime(path: str) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return _MISSING_DIR


def include_cache_key(
    *,
    clang_identity: str,
    language: str,
    header: str,
    header_digest: str,
    argv: Sequence[str],
) -> str:
    """The entry key for one header's ``clang -M`` result."""
    h = hashlib.sha256()
    parts = (
        INCLUDE_CACHE_SCHEMA,
        abicheck_code_fingerprint(),
        clang_identity,
        language,
        header,
        header_digest,
        "\0".join(argv),
    )
    for part in parts:
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()


def _entry_path(key: str) -> Path:
    cache_dir = include_cache_dir()
    entry = cache_dir / f"{key}.json"
    note_use(cache_dir, entry)
    return entry


def load_cached_includes(key: str) -> list[str] | None:
    """The stored include list for *key*, or ``None`` on any miss.

    ``None`` covers an absent, unreadable or foreign entry, and a witness
    that no longer matches the filesystem. The caller re-runs ``clang -M``,
    which is what it would have done without this cache.
    """
    try:
        doc: Any = json.loads(_entry_path(key).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    try:
        if doc["schema"] != INCLUDE_CACHE_SCHEMA or doc["key"] != key:
            return None
        for path, digest in doc["files"]:
            if file_digest(path) != digest:
                return None
        for directory, mtime in doc["dirs"]:
            if _dir_mtime(directory) != mtime:
                return None
        includes = [str(p) for p in doc["includes"]]
    except (KeyError, TypeError, ValueError):
        return None
    return includes


def store_cached_includes(
    key: str, includes: Sequence[str], search_dirs: Sequence[str]
) -> None:
    """Persist *includes* for *key* with a witness over its closure.

    Silently skipped when a reported file cannot be read: an entry with no
    witness for one of its files could never be validated, so it is never
    trusted.
    """
    files: list[list[str]] = []
    for path in includes:
        digest = file_digest(path)
        if digest is None:
            return
        files.append([path, digest])
    doc = {
        "schema": INCLUDE_CACHE_SCHEMA,
        "key": key,
        "includes": list(includes),
        "files": files,
        "dirs": [[d, _dir_mtime(d)] for d in dict.fromkeys(search_dirs)],
    }
    try:
        _entry_path(key).write_text(json.dumps(doc, separators=(",", ":")))
    except OSError as exc:
        log.debug("header include cache write skipped for %s: %s", key, exc)


def cached_include_pass(
    headers: Sequence[str],
    compile_units: Sequence[CompileUnit],
    *,
    clang_bin: str,
    language: str,
    search_dirs: Sequence[str],
) -> tuple[dict[str, list[str]], list[str]]:
    """Return ``({header_node_id: depfile prerequisites}, diagnostics)``.

    *compile_units* holds one ``clang -M`` unit per entry of *headers*, in the
    same order. A header with a fresh entry is served from disk; the rest are
    probed as one batch and stored when that batch ran with no diagnostic.
    The lists are the raw depfile prerequisites, the header's own path
    included: the caller drops it, for cached and probed headers alike.
    """
    from ..extract.headers.toolchain import _tool_identity
    from .build_evidence import BuildEvidence
    from .include_graph import ClangIncludeExtractor

    clang_identity = _tool_identity(clang_bin)
    found: dict[str, list[str]] = {}
    misses: list[tuple[str, str | None, CompileUnit]] = []
    for header, unit in zip(headers, compile_units, strict=True):
        digest = file_digest(header)
        if digest is None:
            misses.append((header, None, unit))
            continue
        key = include_cache_key(
            clang_identity=clang_identity,
            language=language,
            header=header,
            header_digest=digest,
            argv=unit.argv,
        )
        hit = INCLUDE_DISK_CACHE.lookup(partial(load_cached_includes, key))
        if hit is None:
            misses.append((header, key, unit))
        else:
            found[header_node_id(header)] = hit

    diagnostics: list[str] = []
    if misses:
        extractor = ClangIncludeExtractor(clang_bin=clang_bin)
        include_map = extractor.extract_from_build(
            BuildEvidence(compile_units=[unit for _, _, unit in misses])
        )
        diagnostics = list(extractor.diagnostics)
        for header, miss_key, _unit in misses:
            node_id = header_node_id(header)
            paths = include_map.get(node_id, [])
            found[node_id] = paths
            # A run with any diagnostic is partial: none of its results is
            # stored, so a header whose probe failed is retried next time.
            if miss_key is not None and not diagnostics:
                INCLUDE_DISK_CACHE.store(
                    partial(store_cached_includes, miss_key, paths, search_dirs)
                )
    return found, diagnostics
