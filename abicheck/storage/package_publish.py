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

"""ADR-062 D17's publication rules for a directory-backed package: one
writer per root, and the manifest field that names each ref document by
its content digest.

Package format v2 stores every `VariantRef`/`ArtifactRef` record as an
ordinary content-addressed JSON object under `objects/`, and `manifest.json`
maps each id to that object's digest (`variant_refs`/`artifact_refs`). A
republish therefore writes new ref objects beside the old ones and never
overwrites a file the live `manifest.json` names; the single atomic replace
of `manifest.json` is the commit point. A v1 package (refs at the mutable
`refs/<kind>/<id>.json` paths, no digest map) is still read.

The filesystem writer itself is `abicheck/project_snapshot_store.py`'s
`write_project_manifest`; this leaf holds the parts that need no envelope.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

__all__ = [
    "PUBLISH_LOCK_NAME",
    "REF_DIGEST_FIELDS",
    "ConcurrentPublishError",
    "publish_lock",
    "ref_digest_map",
]

#: The lock file a writer holds inside the package root for the whole
#: publish. Not part of the D6 layout: the archive packer skips it.
PUBLISH_LOCK_NAME = ".publish.lock"

#: `manifest.json` field per ref kind (package format v2 and later).
REF_DIGEST_FIELDS = {"variant": "variant_refs", "artifact": "artifact_refs"}


class ConcurrentPublishError(ValueError):
    """Another writer holds this package root (D17: refused, never
    interleaved)."""


def _refuse(root: Path, detail: str) -> ConcurrentPublishError:
    return ConcurrentPublishError(
        f"refusing to publish into {root}: another writer holds "
        f"{root / PUBLISH_LOCK_NAME} ({detail}); concurrent publishers to one "
        "package root are not supported"
    )


@contextmanager
def publish_lock(root: str | Path) -> Iterator[None]:
    """Hold the exclusive writer lock on *root* for the block, refusing --
    never waiting -- when another writer already holds it.

    POSIX uses a non-blocking `flock`, which the OS releases when the holder
    dies, so a crashed publish never leaves a stale lock. The lock file is
    unlinked on release; a writer that opened the old inode before that
    unlink re-checks that the path still names the inode it locked, so two
    writers can never both believe they hold the lock. Without `fcntl`
    (Windows) the lock is an `O_EXCL` create: a crash there leaves the file
    behind, and the refusal names it so it can be removed by hand.
    """
    root_path = Path(root)
    root_path.mkdir(parents=True, exist_ok=True)
    lock_path = root_path / PUBLISH_LOCK_NAME
    try:
        import fcntl
    except ImportError:  # pragma: no cover - exercised only on Windows
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise _refuse(root_path, "remove it if no publish is running") from None
        try:
            yield
        finally:
            os.close(fd)
            lock_path.unlink(missing_ok=True)
        return
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise _refuse(root_path, "a publish is in progress") from None
        try:
            same_inode = os.stat(lock_path).st_ino == os.fstat(fd).st_ino
        except FileNotFoundError:
            same_inode = False
        if not same_inode:
            raise _refuse(root_path, "the lock was released and re-taken meanwhile")
        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)
    finally:
        os.close(fd)


def ref_digest_map(
    data: Mapping[str, Any], kind: str, ids: Sequence[str], record: str
) -> dict[str, str]:
    """The `{id: digest}` map a v2 `manifest.json` states for *kind* refs,
    required to name exactly *ids* -- the membership list the same document
    states -- so no published member is left without a ref and no ref
    names a member the manifest never published."""
    field = REF_DIGEST_FIELDS[kind]
    if field not in data:
        raise ValueError(f"{record} is missing required field {field!r}")
    raw = data[field]
    if not isinstance(raw, dict):
        raise ValueError(
            f"{record} {field} must be a JSON object, not {type(raw).__name__}"
        )
    for key, value in raw.items():
        if not isinstance(value, str) or ":" not in value:
            raise ValueError(
                f"{record} {field}[{key!r}] must be an '<algorithm>:<hex>' digest "
                f"string, got {value!r}"
            )
    if set(raw) != set(ids):
        raise ValueError(
            f"{record} {field} names {sorted(raw)}, but the package publishes "
            f"{sorted(ids)} -- every published {kind} needs exactly one ref"
        )
    return dict(raw)
