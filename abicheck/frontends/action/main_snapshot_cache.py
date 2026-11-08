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

"""``storage: cache`` for the default-branch snapshot a PR delta is measured against.

Plan: ``docs/contribute/plans/pr-delta-vs-main-snapshot.md`` (§ Storage
backends). The artifact backend (:mod:`.main_snapshot`) proves *which run*
produced a snapshot. An Actions cache entry carries no such provenance: the
cache service records only the key, its version (the ``path`` list) and the
ref scope it was written in, and ``actions/cache/restore`` reports none of the
scope. What this backend can establish, and therefore what ``resolved`` means
here, is narrower:

- the entry was restored under the **exact** key bound to the full base SHA
  (and profile) -- never a ``restore-keys`` prefix match, so no other commit's
  snapshot can stand in;
- it holds exactly ``snapshot.json`` and ``provenance.json``, as regular
  files, within the same size limits as the artifact path;
- ``provenance.json`` -- written by ``actions/main-snapshot`` and *self-
  reported*, not authenticated -- names this repository, the default-branch
  ref and the base SHA, and the key it was saved under;
- ``snapshot.json`` is a JSON object.

Who could have written the entry is GitHub's cache-scope rule, not something
checked here: a PR job reads its own ref's caches and the default branch's,
and ``actions/main-snapshot`` refuses to save from any other ref. The
producing workflow, its event and its conclusion are **not** verified.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .main_snapshot import DeltaOutcome, outcome_message, snapshot_artifact_name

__all__ = [
    "CACHE_PROVENANCE_SCHEMA",
    "CACHE_STAGING_DIR",
    "CachedSnapshot",
    "CacheExpectation",
    "SNAPSHOT_CACHE_PREFIX",
    "provenance_record",
    "snapshot_cache_key",
    "verify_cached_snapshot",
]

#: Every cache key is ``<prefix><full sha>[-<profile>]``. A namespace of its
#: own: ``update-main-baseline.yml``'s ``accepted-main`` keys hold a
#: baseline-set, not one snapshot, and are looked up by prefix.
SNAPSHOT_CACHE_PREFIX = "abicheck-main-snapshot-"

#: The cache ``path``, relative to ``$GITHUB_WORKSPACE``. The cache version
#: hashes the literal path list, so producer and consumer must spell it
#: identically; both Actions use this one value.
CACHE_STAGING_DIR = ".abicheck-main-snapshot-cache"

CACHE_PROVENANCE_SCHEMA = "abicheck-main-snapshot-cache/1"

_SNAPSHOT = "snapshot.json"
_PROVENANCE = "provenance.json"
_PROVENANCE_MAX_BYTES = 64 * 1024
#: The artifact path's extraction limits (``fetch-main-snapshot/run.sh``).
DEFAULT_MAX_ENTRY_BYTES = 1 << 30
DEFAULT_MAX_TOTAL_BYTES = 1 << 30


def snapshot_cache_key(sha: str, profile: str = "") -> str:
    """The cache key a snapshot of commit *sha* is saved under.

    Same validation as :func:`.snapshot_artifact_name` (a full 40-hex SHA, an
    ASCII ``[A-Za-z0-9._-]`` profile), so the key cannot name anything but
    one exact commit.
    """
    name = snapshot_artifact_name(sha, profile)
    return SNAPSHOT_CACHE_PREFIX + name.removeprefix("abicheck-main-")


def provenance_record(
    *,
    sha: str,
    key: str,
    repository: str,
    ref: str,
    run_id: str = "",
    run_attempt: str = "",
    workflow_ref: str = "",
    event: str = "",
) -> dict[str, str]:
    """The ``provenance.json`` the producer stores beside the snapshot.

    ``run_id``/``run_attempt``/``workflow_ref``/``event`` are recorded for an
    auditor to follow up; the consumer cannot verify them and does not.
    """
    return {
        "schema": CACHE_PROVENANCE_SCHEMA,
        "repository": repository,
        "ref": ref,
        "sha": sha,
        "key": key,
        "run_id": run_id,
        "run_attempt": run_attempt,
        "workflow_ref": workflow_ref,
        "event": event,
    }


@dataclass(frozen=True)
class CacheExpectation:
    repository: str
    branch: str
    base_sha: str
    key: str


@dataclass(frozen=True)
class CachedSnapshot:
    outcome: DeltaOutcome
    #: The comment/summary line.
    message: str
    #: Why, for the log; may quote names from the entry, so never the comment.
    reason: str = ""
    #: The verified snapshot's copy under the destination (``resolved`` only).
    snapshot_path: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is DeltaOutcome.RESOLVED


def _refused(outcome: DeltaOutcome, line: str, reason: str) -> CachedSnapshot:
    return CachedSnapshot(outcome, f"Delta unavailable: {line}", reason)


def _entry_problem(directory: Path, max_entry_bytes: int, max_total_bytes: int) -> str:
    """Why the restored directory is not a well-formed entry, or ``""``."""
    try:
        if not stat.S_ISDIR(os.lstat(directory).st_mode):
            return "the cache path is not a directory"
        names = set(os.listdir(directory))
    except OSError as exc:
        return f"cannot read the cache path: {exc.strerror or exc}"
    unexpected = sorted(names - {_SNAPSHOT, _PROVENANCE})
    if unexpected:
        return f"unexpected entries {unexpected[:5]!r}"
    total = 0
    for name in (_SNAPSHOT, _PROVENANCE):
        if name not in names:
            return f"no {name}"
        info = os.lstat(directory / name)
        if not stat.S_ISREG(info.st_mode):
            return f"{name} is not a regular file"
        if info.st_size > max_entry_bytes:
            return (
                f"{name} is {info.st_size} bytes, over the {max_entry_bytes}-byte limit"
            )
        total += info.st_size
    if total > max_total_bytes:
        return f"the entry is {total} bytes, over the {max_total_bytes}-byte limit"
    return ""


def _load_object(path: Path, limit: int) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
        if len(raw) > limit:
            return None
        document = json.loads(raw)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _provenance_problem(
    record: dict[str, Any] | None, expected: CacheExpectation
) -> str:
    if record is None:
        return "provenance.json is not a JSON object"
    wanted = {
        "schema": CACHE_PROVENANCE_SCHEMA,
        "repository": expected.repository,
        "ref": f"refs/heads/{expected.branch}",
        "sha": expected.base_sha,
        "key": expected.key,
    }
    for name, value in wanted.items():
        if record.get(name) != value:
            return f"provenance {name} is {record.get(name)!r}, needed {value!r}"
    return ""


def verify_cached_snapshot(
    directory: Path,
    expected: CacheExpectation,
    *,
    cache_hit: bool,
    matched_key: str,
    destination: Path,
    max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> CachedSnapshot:
    """Decide what the restored cache entry at *directory* may be used for.

    On ``resolved`` the snapshot is copied into a new subdirectory of
    *destination*; nothing already there is touched. A miss is
    ``base_not_snapshotted``: the cache cannot tell "not written yet" from
    "evicted" or "never written", and ``actions/cache/restore`` reports a
    cache-service error as a miss too.
    """
    sha7 = expected.base_sha[:7] or "?"
    where = f"`{expected.branch}@{sha7}`"
    if not cache_hit:
        return CachedSnapshot(
            DeltaOutcome.BASE_NOT_SNAPSHOTTED,
            f"Delta unavailable: no cache entry for {where} (not saved yet, "
            "evicted, or never saved; the cache cannot tell which). Re-run once "
            "the default-branch snapshot job has saved it.",
            "cache miss",
        )
    if matched_key != expected.key:
        return _refused(
            DeltaOutcome.UNTRUSTED_SOURCE,
            f"the cache entry restored for {where} is not the exact key for that commit.",
            f"restored key {matched_key!r}, requested {expected.key!r}",
        )
    problem = _entry_problem(directory, max_entry_bytes, max_total_bytes)
    if problem:
        return _refused(
            DeltaOutcome.LOOKUP_FAILED,
            f"the cache entry for {where} is not a well-formed snapshot entry.",
            problem,
        )
    problem = _provenance_problem(
        _load_object(directory / _PROVENANCE, _PROVENANCE_MAX_BYTES), expected
    )
    if problem:
        return _refused(
            DeltaOutcome.UNTRUSTED_SOURCE,
            f"the cache entry for {where} does not record this repository, "
            f"`{expected.branch}` and that commit as its origin.",
            problem,
        )
    if _load_object(directory / _SNAPSHOT, max_entry_bytes) is None:
        return _refused(
            DeltaOutcome.LOOKUP_FAILED,
            f"the cache entry for {where} holds no readable snapshot.",
            "snapshot.json is not a JSON object",
        )
    destination.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="snapshot.", dir=destination)) / _SNAPSHOT
    shutil.copyfile(directory / _SNAPSHOT, target)
    return CachedSnapshot(
        DeltaOutcome.RESOLVED,
        outcome_message(
            DeltaOutcome.RESOLVED, sha=expected.base_sha, branch=expected.branch
        ),
        f"cache entry {expected.key}",
        str(target),
    )
