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

"""On-disk half of the file-digest memo (ADR-062 D11).

A fresh process would otherwise re-read every byte under the compiler's
system include directories (thousands of files) for each cache key. This
store keeps ``path -> (stat signature, digest)`` beside abicheck's other
caches, the way git's index does: an entry is reused only while the file's
full stat signature (device, inode, size, mtime_ns, ctime_ns) is unchanged,
so the stat fields still only decide *reuse* and never enter a digest.

Best-effort throughout: an unreadable, corrupt or foreign store is ignored
and the caller recomputes; a failed write is dropped. Concurrent writers
merge with what is on disk and replace the file atomically, so the worst
outcome of a race is a lost entry (one extra re-read later), never a wrong
digest.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..storage.ast_cache_location import backend_cache_dir
from ..storage.atomic_file import atomic_write

log = logging.getLogger(__name__)

__all__ = ["load_entries", "save_entries"]

_FORMAT = 1
_FILE_NAME = "file_digests.json"
#: Upper bound on stored entries; past it the store keeps only the newest
#: writer's entries, so a long-lived cache cannot grow without limit.
MAX_ENTRIES = 200_000

Entry = tuple[tuple[int, ...], str]


def _store_path() -> Path | None:
    try:
        return backend_cache_dir("digests") / _FILE_NAME
    except OSError:
        return None


def _read(path: Path) -> dict[str, Entry]:
    try:
        doc = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return {}
    if not isinstance(doc, dict) or doc.get("format") != _FORMAT:
        return {}
    raw = doc.get("entries")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, Entry] = {}
    for key, value in raw.items():
        if (
            isinstance(key, str)
            and isinstance(value, list)
            and len(value) == 2
            and isinstance(value[0], list)
            and all(isinstance(x, int) for x in value[0])
            and isinstance(value[1], str)
        ):
            out[key] = (tuple(value[0]), value[1])
    return out


def load_entries() -> dict[str, Entry]:
    """Every stored entry, or ``{}`` when there is no usable store."""
    path = _store_path()
    return _read(path) if path is not None else {}


def save_entries(new: dict[str, Entry]) -> None:
    """Merge *new* into the store; entries in *new* win."""
    if not new:
        return
    path = _store_path()
    if path is None:
        return
    merged = _read(path)
    merged.update(new)
    if len(merged) > MAX_ENTRIES:
        merged = dict(new)
    doc = {
        "format": _FORMAT,
        "entries": {k: [list(sig), dig] for k, (sig, dig) in merged.items()},
    }
    try:
        atomic_write(path, json.dumps(doc, separators=(",", ":")).encode())
    except OSError as exc:
        log.debug("file-digest store not written: %s", exc)
