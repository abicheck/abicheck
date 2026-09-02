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

"""ADR-063 Phase 8's `dump`/`compare`/`scan` storage-v2 wiring: the legacy
`AbiSnapshot` document <-> real, directory-backed `ProjectSnapshot` package
round trip, built on `project_snapshot_store.py`'s `DirectoryObjectStore`/
`write_project_manifest`/`read_manifest_summary`/`read_artifact_ref` and
`storage.import_v1`'s `import_legacy_snapshot`/`export_legacy_snapshot`.

Kept as its own sibling module, not added to `project_snapshot_store.py`
itself, purely for that module's own architecture-gate line budget (already
close to the 800-line production cap before this landed) — the same
mechanical-split reasoning `AGENTS.md`'s "Files that are large" section
gives for `diff_types_vtable.py` and similar splits: move responsibility
out, don't trim the file to fit.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import SnapshotError
from .project_snapshot_store import (
    DirectoryObjectStore,
    read_artifact_ref,
    read_manifest_summary,
    write_project_manifest,
)
from .storage.import_v1 import export_legacy_snapshot, import_legacy_snapshot
from .storage.package import InMemoryObjectStore, PackageManifest

__all__ = [
    "is_project_snapshot_package_dir",
    "read_legacy_snapshot_document",
    "write_legacy_snapshot_package",
]


def is_project_snapshot_package_dir(path: str | Path) -> bool:
    """Whether *path* is a directory holding a real, readable
    `ProjectSnapshot` package `manifest.json` -- the disambiguator a CLI
    operand classifier (`cli_resolve.classify_compare_operand`) needs, since
    a plain directory-of-libraries `compare`/`scan` operand and a
    `BuildSourcePack`'s own `manifest.json` (`buildsource/pack_io.py`) both
    use the identical filename at their own directory root.

    Reads and validates `manifest.json` (`read_manifest_summary`, D2's
    version-compatibility check included), not just a bare
    `(path / "manifest.json").exists()` -- a `BuildSourcePack` manifest
    parses as JSON too but fails `StorageVersions.from_dict`/
    `check_reader_compatibility` (no `versions`/`variant_ids`/`artifact_ids`
    keys, and no `package_format_version` value this reader recognizes), so
    the distinction is real content, not guessed from the filename alone.
    Returns `False` -- never raises -- for anything that doesn't parse as
    one: a missing/malformed/unreadable/incompatible manifest is simply "not
    a ProjectSnapshot package here", the classifier's other branches decide
    what it *is* instead.
    """
    path = Path(path)
    if not (path / "manifest.json").is_file():
        return False
    try:
        read_manifest_summary(path)
    except (SnapshotError, OSError, ValueError, TypeError):
        return False
    return True


def write_legacy_snapshot_package(
    document: Mapping[str, Any],
    root: str | Path,
    *,
    artifact_id: str,
    max_known_schema_version: int,
    variant_id: str = "default",
    artifact_kind: str | None = None,
) -> PackageManifest:
    """*document* (a `serialization.snapshot_to_dict()`-shaped mapping — the
    same document a real `dump` invocation already produces) written as a
    real, directory-backed `ProjectSnapshot` package at *root*, alongside
    whatever legacy `.abi.json` output the caller also writes.

    A thin composition of three already-independently-tested primitives —
    `storage.import_v1.import_legacy_snapshot` (build the manifest, populate
    an in-memory object buffer), `DirectoryObjectStore` (the real filesystem
    object store), `write_project_manifest` (fan the manifest out across
    D6's directory tree) — with one twist: `import_legacy_snapshot` writes
    into whatever `ObjectStore` it is given, so this function `put()`s every
    object *twice*: once into a throwaway `InMemoryObjectStore` (to let
    `import_legacy_snapshot`'s own content-addressing decide the manifest's
    digests before anything touches disk), and once for real into a
    `DirectoryObjectStore` rooted at *root* using those same digests --
    deliberately, not a wasted step: `write_project_manifest` itself
    requires every object it is about to publish a reference to be already
    durable and valid in the *target* store before it will write
    `manifest.json` (see its own docstring), so the manifest cannot be built
    and validated against a store that was never actually persisted.
    """
    staging = InMemoryObjectStore()
    manifest = import_legacy_snapshot(
        document,
        store=staging,
        artifact_id=artifact_id,
        max_known_schema_version=max_known_schema_version,
        variant_id=variant_id,
        artifact_kind=artifact_kind,
    )
    directory_store = DirectoryObjectStore(root)
    for artifact in manifest.artifact_refs:
        for ref in artifact.sections.values():
            directory_store.put(staging.get(ref.digest))
    write_project_manifest(root, manifest)
    return manifest


def read_legacy_snapshot_document(
    root: str | Path, *, artifact_id: str | None = None
) -> dict[str, Any]:
    """The inverse of `write_legacy_snapshot_package`: the single artifact's
    document (`serialization.snapshot_from_dict()`-shaped), read back from a
    real directory-backed `ProjectSnapshot` package at *root*.

    *artifact_id* names which artifact to read; omitted (the default), the
    package must publish exactly one artifact -- ADR-062 A1.3's
    "single-library snapshot as a one-artifact project" shape, the only one
    `write_legacy_snapshot_package`/`storage.import_v1.import_legacy_snapshot`
    ever produce today. Raises `ValueError` if *artifact_id* is omitted and
    the package holds zero or more than one artifact -- multi-artifact
    projects (a real multi-library `ProjectSnapshot`) are real,
    separately-scoped future work this function does not guess at.
    """
    summary = read_manifest_summary(root)
    if artifact_id is None:
        if len(summary.artifact_ids) != 1:
            raise ValueError(
                f"{root} publishes {len(summary.artifact_ids)} artifact(s) "
                f"({sorted(summary.artifact_ids)}) -- pass an explicit "
                "artifact_id to select one, or use a package with exactly "
                "one artifact"
            )
        artifact_id = summary.artifact_ids[0]
    artifact = read_artifact_ref(root, artifact_id)
    store = DirectoryObjectStore(root)
    return export_legacy_snapshot(
        artifact,
        store=store,
        source_schema_version=summary.versions.source_schema_version,
    )
