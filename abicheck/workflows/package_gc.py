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

"""ADR-062 D17: collect a ProjectSnapshot package's superseded refs and
objects.

A publish never rewrites a file the live ``manifest.json`` names: it writes
new digest-named ref documents and objects beside the old ones and then
atomically replaces the manifest. What the previous manifest named stays
behind, unnamed. :func:`collect_package_garbage` removes it.

**Safe against writers.** It runs under the same
``cross_process_lock.single_flight`` lock every publish holds
(`package_publish.publish_lock_name`), so no publish is between its manifest
read and its rename while it decides what is live, and the live set is read
from the manifest that lock protects. An object a writer stored but has not
yet published is unnamed too; the grace period below keeps it, and
``DirectoryObjectStore.put`` refreshes the mtime of an object it reuses, so
a writer re-staging an old object protects it. If a writer's object is
collected anyway, that writer's publish refuses (it re-verifies every object
under the lock) -- it never publishes a dangling reference.

**Safe against readers.** Readers take no lock. A reader that read the
previous ``manifest.json`` may still be reading the refs and objects it
named, so nothing younger than *min_age_seconds* (default one hour) is
removed. A reader holding an older manifest than that fails closed -- a
missing ref or object is an error, every read is digest-verified -- never a
wrong result.

Only files under ``refs/`` and ``objects/`` that the live manifest does not
name are candidates; ``manifest.json`` and anything else is never touched.
Directories are left in place (a writer may be about to write into one),
and a writer's in-flight temporary file is never collected before the
default grace period, whatever *min_age_seconds* says.
A v1 package (mutable ``refs/<kind>/<id>.json``) is refused: its refs are
named by path, not digest, and it has nothing superseded to collect.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from ..errors import SnapshotError
from ..project_snapshot_store import (
    read_artifact_ref,
    read_manifest_summary,
    read_variant_ref,
)
from ..storage.cross_process_lock import single_flight
from ..storage.package import artifact_object_refs, object_relpath
from ..storage.package_publish import publish_lock_name, versioned_ref_relpath

__all__ = [
    "DEFAULT_MIN_AGE_SECONDS",
    "PackageGcResult",
    "collect_package_garbage",
    "live_package_paths",
]

#: How old an unnamed file must be before it is collected.
DEFAULT_MIN_AGE_SECONDS = 3600.0

_COLLECTED_DIRS = ("refs", "objects")


@dataclass(frozen=True)
class PackageGcResult:
    """What one collection did, as package-relative POSIX paths."""

    removed: tuple[str, ...]
    #: Unnamed files kept because they are younger than the grace period.
    kept_recent: tuple[str, ...]
    bytes_freed: int
    dry_run: bool


def _object_paths(digest: str) -> tuple[str, ...]:
    json_relpath = object_relpath(digest)
    stem = json_relpath[: -len(".json")]
    # Every spelling an object can be stored under (`DirectoryObjectStore`).
    return (json_relpath, f"{json_relpath}.zst", f"{stem}.bin.zst")


def live_package_paths(root: str | Path) -> frozenset[str]:
    """Every ``refs/``/``objects/`` path the live manifest at *root* names,
    directly or through its refs. Each ref is read digest-verified, so a
    corrupted package raises instead of being collected around."""
    root_path = Path(root)
    summary = read_manifest_summary(root_path)
    if not summary.ref_digests:
        raise SnapshotError(
            f"{root_path} is a package_format_version 1 package (mutable "
            "refs); it has no superseded refs to collect"
        )
    live: set[str] = set()
    objects = list(summary.project_sections.values())
    for kind, digests in summary.ref_digests.items():
        for ref_id, digest in digests.items():
            live.add(versioned_ref_relpath(kind, ref_id, digest))
    for variant_id in summary.variant_ids:
        objects += read_variant_ref(
            root_path, variant_id, summary=summary
        ).sections.values()
    for artifact_id in summary.artifact_ids:
        artifact = read_artifact_ref(root_path, artifact_id, summary=summary)
        objects += artifact_object_refs(artifact)
    for ref in objects:
        live.update(_object_paths(ref.digest))
    return frozenset(live)


def _is_temp(path: Path) -> bool:
    return path.name.startswith(".") and path.name.endswith(".tmp")


def _candidates(root: Path) -> list[Path]:
    found: list[Path] = []
    for top in _COLLECTED_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_symlink() or path.is_file():
                found.append(path)
    return sorted(found)


def collect_package_garbage(
    root: str | Path,
    *,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    dry_run: bool = False,
    now: float | None = None,
) -> PackageGcResult:
    """Remove the refs and objects the live manifest at *root* no longer
    names, older than *min_age_seconds*, holding the publish lock (see the
    module docstring). *dry_run* reports without removing."""
    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must not be negative")
    root_path = Path(root)
    with single_flight(publish_lock_name(str(root_path.resolve()))):
        live = live_package_paths(root_path)
        clock = time.time() if now is None else now
        cutoff = clock - min_age_seconds
        temp_cutoff = clock - DEFAULT_MIN_AGE_SECONDS
        removed: list[str] = []
        kept: list[str] = []
        freed = 0
        for path in _candidates(root_path):
            rel = path.relative_to(root_path).as_posix()
            if rel in live:
                continue
            try:
                info = path.lstat()
            except FileNotFoundError:
                # Gone since the listing: a writer staging objects takes no
                # lock and renames its `.tmp` file into place at any time.
                continue
            # An in-flight atomic write (`.<name>.<unique>.tmp`) belongs to a
            # writer that takes no lock while it stages objects: never cut
            # under it, whatever *min_age_seconds* says.
            limit = min(cutoff, temp_cutoff) if _is_temp(path) else cutoff
            if info.st_mtime > limit:
                kept.append(rel)
                continue
            removed.append(rel)
            freed += info.st_size
            if not dry_run:
                path.unlink(missing_ok=True)
    return PackageGcResult(
        removed=tuple(removed),
        kept_recent=tuple(kept),
        bytes_freed=freed,
        dry_run=dry_run,
    )
