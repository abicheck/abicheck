# SPDX-License-Identifier: Apache-2.0
"""Content-addressed per-invocation parse store (plan ``l4-l2-extraction-convergence`` Phase 6).

One store holds the results of parsing one translation unit under one parsed
invocation. The *caller* derives the key: :func:`content_key` folds the parts it
hands in (tool identity, input digests, the parsed invocation) into a single
digest, and this module never interprets them. That keeps key derivation with
the layer that knows the invocation (``buildsource``) while the storage layer
owns the entry format, dependency re-validation and atomic publish.

An entry records the content digest of every file the parse read. A lookup
re-hashes those files and misses on any difference, including a file that has
vanished: a false miss is preferred to a false hit (ADR-033 D5).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .guards import decision_key as _decision_key

__all__ = ["TuParseStore", "content_key", "digest_file", "dep_digest"]

#: Separator folded between key parts. A NUL cannot occur in a path or in any
#: flag spelling, so two different part sequences cannot collide by concatenation.
_PART_SEPARATOR = "\x00"

#: Domain tag folded first into every key, so a digest from this store can never
#: equal one a different content-addressed store computed over the same parts.
_KEY_DOMAIN = "abicheck-tu-parse-store"


def digest_file(path: Path) -> str:
    """SHA-256 of a file's bytes, read in chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def dep_digest(path: str, memo: dict[str, str | None] | None = None) -> str | None:
    """Content digest of a dependency file, or ``None`` if it cannot be read.

    ``memo`` (optional) caches the digest per path for one pass: a header included
    by N units is hashed once. A pass must not outlive the files it describes, so
    callers create the memo per pass and discard it afterwards.
    """
    if memo is not None and path in memo:
        return memo[path]
    try:
        fp = Path(os.path.expanduser(path)) if path else None
        digest = digest_file(fp) if (fp is not None and fp.is_file()) else None
    except OSError:
        digest = None
    if memo is not None:
        memo[path] = digest
    return digest


def content_key(parts: Iterable[str]) -> str:
    """The store key for an ordered sequence of canonical key parts."""
    blob = _PART_SEPARATOR.join((_KEY_DOMAIN, *parts)).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class TuParseStore:
    """An on-disk ``key -> payload`` map that refuses entries whose inputs changed.

    Each entry is ``{"deps": {path: digest}, "payload": ...}``. The payload is
    opaque to this module; it must be JSON-serializable.
    """

    def __init__(self, cache_dir: Path | str) -> None:
        self.cache_dir = Path(cache_dir)

    def path(self, key: str) -> Path:
        key = _decision_key(key, "store key")
        return self.cache_dir / f"{key}.json"

    def lookup(self, key: str, memo: dict[str, str | None] | None = None) -> Any | None:
        """The stored payload for *key*, or ``None`` on any kind of miss.

        A missing, unreadable, malformed or dependency-stale entry is a miss,
        never an error. A payload that the caller later fails to decode is the
        caller's miss to count.
        """
        path = self.path(key)
        if not path.is_file():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(entry, dict):
            return None
        deps = entry.get("deps") or {}
        if not isinstance(deps, dict):
            return None
        for dep_path, dep_hash in deps.items():
            if dep_digest(dep_path, memo) != dep_hash:
                return None
        if "payload" not in entry:
            return None
        return entry["payload"]

    def publish(
        self,
        key: str,
        payload: Any,
        dep_paths: Iterable[str],
        memo: dict[str, str | None] | None = None,
    ) -> None:
        """Write *payload* under *key*, recording the digest of every dependency.

        If any dependency cannot be read, nothing is written. An entry that
        omitted that dependency would serve its payload unchecked once the
        path became readable, so a partial record is worse than no record
        (ADR-033 D5: prefer a false miss over a false hit).

        Best-effort: a write failure leaves no entry and raises nothing, because
        the parse already produced its result.
        """
        final = self.path(key)
        deps: dict[str, str] = {}
        for dep in dep_paths:
            digest = dep_digest(dep, memo)
            if digest is None:
                return
            deps[dep] = digest
        entry = {"deps": deps, "payload": payload}
        tmp = final.with_suffix(".json.tmp")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            # Atomic publish so a concurrent reader never sees a partial file.
            tmp.replace(final)
        except OSError:
            tmp.unlink(missing_ok=True)
