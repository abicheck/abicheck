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

"""Content-addressed cache for normalized L3 ``BuildEvidence`` (ADR-033 D5).

The ADR-033 D5 cache table calls for a ``BuildEvidence`` cache keyed on the
build-system raw inputs + adapter version. This is the small, deterministic
counterpart to the per-TU ``SourceAbiCache`` (ADR-030 D8): normalizing a compile
DB is cheap, but caching it keeps repeated dumps over an unchanged build tree
free. Invalidation **prefers false misses over false hits** (ADR-033 D5): the key
folds the compile-DB content hash, the adapter hint, and ``BUILD_EVIDENCE_VERSION``,
so any input or schema change misses; a corrupt/partial entry also misses.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .build_evidence import BUILD_EVIDENCE_VERSION, BuildEvidence


def compute_build_cache_key(compile_db: Path, adapter_hint: str) -> str | None:
    """Content-address a compile DB for the L3 cache, or ``None`` if unreadable.

    Folds the file content, the adapter hint, ``BUILD_EVIDENCE_VERSION``, **and the
    resolved compile-DB location**. The location matters because a compile DB may
    use omitted/relative ``directory``/``file`` fields that the adapter resolves
    against the DB's parent dir — so two trees with byte-identical relative DBs but
    different roots normalize to *different* paths and must not share a cache entry
    (Codex review). Returns ``None`` when the DB cannot be read (false miss, never
    a false hit).
    """
    try:
        data = compile_db.read_bytes()
    except OSError:
        return None
    try:
        location = str(compile_db.resolve())
    except OSError:
        location = str(compile_db)
    h = hashlib.sha256()
    h.update(f"v{BUILD_EVIDENCE_VERSION}\0{adapter_hint}\0{location}\0".encode())
    h.update(data)
    return h.hexdigest()


class BuildEvidenceCache:
    """On-disk cache of normalized :class:`BuildEvidence` keyed by content hash."""

    def __init__(self, cache_dir: Path | str) -> None:
        self.cache_dir = Path(cache_dir)
        self.hits = 0
        self.misses = 0

    @property
    def hit_rate(self) -> float | None:
        total = self.hits + self.misses
        return self.hits / total if total else None

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"build-{key}.json"

    def get(self, key: str | None) -> BuildEvidence | None:
        if not key:
            return None
        ev = self._load(key)
        if ev is not None:
            self.hits += 1
        else:
            self.misses += 1
        return ev

    def _load(self, key: str) -> BuildEvidence | None:
        path = self._path(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None  # corrupt/partial entry → miss, never a failure
        if not isinstance(data, dict):
            return None
        try:
            return BuildEvidence.from_dict(data)
        except (KeyError, TypeError, ValueError):
            return None

    def put(self, key: str | None, evidence: BuildEvidence) -> None:
        if not key:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".json.tmp")
        try:
            tmp.write_text(json.dumps(evidence.to_dict()), encoding="utf-8")
            tmp.replace(self._path(key))
        except OSError:
            # A cache write failure must never break collection (best-effort).
            tmp.unlink(missing_ok=True)
