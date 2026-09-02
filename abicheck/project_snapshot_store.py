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

"""The directory-backed half of ADR-062 A1.1 — `storage/package.py` owns the
`ProjectSnapshot` package's *logical* object model (`PackageManifest`,
`VariantRef`, `ArtifactRef`, `ObjectRef`, the `ObjectStore` protocol); this
module is the concrete, filesystem-backed `ObjectStore` implementation and
the manifest/ref writer/reader over ADR-059's physical envelope
(`abicheck/snapshot_io.py`) — the "real writer" that module's own docstring
says lives outside `storage/` because that package may depend only on
`model` (`storage/AGENTS.md`, "Permitted imports") and therefore cannot
itself import `snapshot_io`.

D6's layout, exactly:

    project.abicheck/
      manifest.json            # small; loads immediately
      refs/variants/<variant-id>.json
      refs/artifacts/<artifact-id>.json
      objects/sha256/<aa>/<digest>.json.zst

`manifest.json` deliberately does **not** embed full `variant_refs`/
`artifact_refs` records the way `PackageManifest.to_dict()` does — that
in-memory convenience is explicitly what `package.py`'s own docstring flags
as "premature" until a real writer exists to make the split meaningful. This
module is that writer: `manifest.json` carries only `versions` plus the two
id lists, and each variant/artifact's full record lives at its own
`refs/*.json` path (`variant_ref_relpath`/`artifact_ref_relpath`), read
lazily — `read_variant_ref`/`read_artifact_ref` load exactly one, and
`read_manifest_summary` loads only the small root document, matching D8's
"a project comparison loads two manifests... then one matched library pair
at a time" access pattern. `read_project_manifest` assembles a full,
in-memory `PackageManifest` (every ref eagerly loaded) purely as a
convenience for a caller — a test, a one-shot inspection tool — that wants
the whole thing at once; it is built from the same lazy primitives, not a
second read path.

The transport `.tar.zst` form D6 also describes is not implemented here —
see `docs/contribute/adr/062-project-snapshot-storage-v2.md`'s Status for
what remains open.

**Registers `docs/_meta/topics.yaml`'s `project-snapshot-storage` topic**
(`docs/reference/project-snapshot-format.md`) — this is the module that
first actually persists a `ProjectSnapshot`, the trigger `storage-format-
v2.md`'s "Documentation ownership" section names explicitly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .snapshot_io import (
    SnapshotCompression,
    read_snapshot_bytes,
    write_snapshot_bytes,
    write_snapshot_text,
)
from .storage.canonical import (
    canonical_form,
    canonical_json,
    raw_digest,
    semantic_digest,
    strip_capture_metadata,
)
from .storage.package import (
    MANIFEST_RELPATH,
    ArtifactRef,
    PackageManifest,
    VariantRef,
    artifact_ref_relpath,
    object_relpath,
    variant_ref_relpath,
)
from .storage.versioning import StorageVersions

__all__ = [
    "DirectoryObjectStore",
    "ManifestSummary",
    "read_artifact_ref",
    "read_manifest_summary",
    "read_project_manifest",
    "read_variant_ref",
    "variant_and_artifact_ids",
    "write_project_manifest",
]

#: `zstd` level object content is written at. D12 reserves a real policy
#: table (extraction-cache/CI-baseline/archival levels); this module writes
#: everything at the middle, ordinary-CI-baseline level until a caller that
#: actually distinguishes those three cases exists to pick between them —
#: inventing that distinction here, with nothing yet calling it, would be
#: exactly the guessed-shape risk `package.py`'s own deferred-gap notes warn
#: against.
_OBJECT_ZSTD_LEVEL = 10


def _object_json_relpath(digest: str) -> str:
    return f"{object_relpath(digest)}.zst"


def _object_raw_relpath(digest: str) -> str:
    json_relpath = object_relpath(digest)
    assert json_relpath.endswith(".json")
    return f"{json_relpath[: -len('.json')]}.bin.zst"


def _is_binary_buffer(value: Any) -> bool:
    return isinstance(value, (bytes, bytearray, memoryview))


class DirectoryObjectStore:
    """A real, `ObjectStore`-conforming filesystem directory — D7's
    `put`/`get`/`has` over ADR-059's compressed, atomic, decompression-bomb-
    guarded envelope.

    Every object is written zstd-compressed, matching D6's own example
    layout (`objects/sha256/<aa>/<digest>.json.zst`) — plain/gzip storage is
    a snapshot-envelope choice this store does not expose, since nothing
    about a content-addressed, deduplicated object benefits from the
    per-file choice a top-level snapshot's `--compression` flag exists for.

    A JSON-shaped and a raw-binary object can share one hex digest only in
    the same astronomically unlikely case `storage/canonical.py`'s own
    domain separation already accepts (two different domain tags hashed
    ahead of unrelated payloads) — this store additionally never writes both
    a `.json.zst` and a `.bin.zst` object under the same digest, so `get()`
    can distinguish them by which file exists rather than needing a separate
    index.
    """

    def __init__(
        self, root: str | Path, *, zstd_level: int = _OBJECT_ZSTD_LEVEL
    ) -> None:
        self._root = Path(root)
        self._zstd_level = zstd_level

    def _json_path(self, digest: str) -> Path:
        return self._root / _object_json_relpath(digest)

    def _raw_path(self, digest: str) -> Path:
        return self._root / _object_raw_relpath(digest)

    def put(self, content: Any, *, algorithm: str = "sha256") -> str:
        if _is_binary_buffer(content):
            payload = bytes(content)
            digest = raw_digest(payload, algorithm=algorithm)
            path = self._raw_path(digest)
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                write_snapshot_bytes(
                    payload,
                    path,
                    compression=SnapshotCompression.ZSTD,
                    zstd_level=self._zstd_level,
                )
            return digest
        stripped = strip_capture_metadata(content)
        digest = semantic_digest(stripped, algorithm=algorithm)
        path = self._json_path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            write_snapshot_text(
                canonical_json(stripped),
                path,
                compression=SnapshotCompression.ZSTD,
                zstd_level=self._zstd_level,
            )
        return digest

    def get(self, digest: str) -> Any:
        if not isinstance(digest, str):
            raise TypeError(f"digest must be a string, not {type(digest).__name__}")
        json_path = self._json_path(digest)
        if json_path.exists():
            return canonical_form(json.loads(read_snapshot_bytes(json_path)))
        raw_path = self._raw_path(digest)
        if raw_path.exists():
            return read_snapshot_bytes(raw_path)
        raise KeyError(f"no object stored under digest {digest!r} in {self._root}")

    def has(self, digest: str) -> bool:
        if not isinstance(digest, str):
            raise TypeError(f"digest must be a string, not {type(digest).__name__}")
        return self._json_path(digest).exists() or self._raw_path(digest).exists()


def write_project_manifest(root: str | Path, manifest: PackageManifest) -> None:
    """Fan *manifest* out across the D6 directory tree rooted at *root*:
    the small `manifest.json` plus one `refs/variants/*.json`/
    `refs/artifacts/*.json` document per record. Does not touch `objects/` —
    a caller populates those separately (typically via `DirectoryObjectStore
    .put`, e.g. through `storage.import_v1.import_legacy_snapshot`) before
    or after writing the manifest; nothing here depends on ordering, since
    `ObjectStore.put` is idempotent and content-addressed.
    """
    root_path = Path(root)
    summary = {
        "versions": manifest.versions.to_dict(),
        "variant_ids": [variant.variant_id for variant in manifest.variant_refs],
        "artifact_ids": [artifact.artifact_id for artifact in manifest.artifact_refs],
    }
    manifest_path = root_path / MANIFEST_RELPATH
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_snapshot_text(
        canonical_json(summary, indent=2),
        manifest_path,
        compression=SnapshotCompression.NONE,
    )
    for variant in manifest.variant_refs:
        variant_path = root_path / variant_ref_relpath(variant.variant_id)
        variant_path.parent.mkdir(parents=True, exist_ok=True)
        write_snapshot_text(
            canonical_json(variant.to_dict(), indent=2),
            variant_path,
            compression=SnapshotCompression.NONE,
        )
    for artifact in manifest.artifact_refs:
        artifact_path = root_path / artifact_ref_relpath(artifact.artifact_id)
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        write_snapshot_text(
            canonical_json(artifact.to_dict(), indent=2),
            artifact_path,
            compression=SnapshotCompression.NONE,
        )


class ManifestSummary:
    """`manifest.json`'s own small, always-loaded content — `versions` plus
    which variant/artifact ids exist, without either's full record."""

    __slots__ = ("versions", "variant_ids", "artifact_ids")

    def __init__(
        self,
        versions: StorageVersions,
        variant_ids: tuple[str, ...],
        artifact_ids: tuple[str, ...],
    ) -> None:
        self.versions = versions
        self.variant_ids = variant_ids
        self.artifact_ids = artifact_ids


def read_manifest_summary(root: str | Path) -> ManifestSummary:
    """Load only `manifest.json` — the one document D8 requires be small
    enough to load unconditionally."""
    root_path = Path(root)
    data = json.loads(read_snapshot_bytes(root_path / MANIFEST_RELPATH))
    if not isinstance(data, dict):
        raise ValueError(f"{root_path / MANIFEST_RELPATH} is not a JSON object")
    return ManifestSummary(
        versions=StorageVersions.from_dict(data.get("versions", {})),
        variant_ids=tuple(data.get("variant_ids", ())),
        artifact_ids=tuple(data.get("artifact_ids", ())),
    )


def read_variant_ref(root: str | Path, variant_id: str) -> VariantRef:
    """Load exactly one variant's own ref document."""
    root_path = Path(root)
    data = json.loads(read_snapshot_bytes(root_path / variant_ref_relpath(variant_id)))
    if not isinstance(data, dict):
        raise ValueError(f"variant ref for {variant_id!r} is not a JSON object")
    return VariantRef.from_dict(data)


def read_artifact_ref(root: str | Path, artifact_id: str) -> ArtifactRef:
    """Load exactly one artifact's own ref document."""
    root_path = Path(root)
    data = json.loads(
        read_snapshot_bytes(root_path / artifact_ref_relpath(artifact_id))
    )
    if not isinstance(data, dict):
        raise ValueError(f"artifact ref for {artifact_id!r} is not a JSON object")
    return ArtifactRef.from_dict(data)


def variant_and_artifact_ids(
    root: str | Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """`(variant_ids, artifact_ids)` from `manifest.json` alone — the
    membership a caller needs before deciding which `refs/*.json` documents
    to load next."""
    summary = read_manifest_summary(root)
    return summary.variant_ids, summary.artifact_ids


def read_project_manifest(root: str | Path) -> PackageManifest:
    """The whole package's `PackageManifest`, every ref eagerly loaded.

    A convenience assembled from `read_manifest_summary`/`read_variant_ref`/
    `read_artifact_ref` — the same lazy primitives a real, section-aware
    reader uses — never a second, independent read path. Prefer the lazy
    primitives directly for anything that does not genuinely need every
    variant and artifact in memory at once (D8's whole reason for existing).
    """
    root_path = Path(root)
    summary = read_manifest_summary(root_path)
    variants = tuple(
        read_variant_ref(root_path, variant_id) for variant_id in summary.variant_ids
    )
    artifacts = tuple(
        read_artifact_ref(root_path, artifact_id)
        for artifact_id in summary.artifact_ids
    )
    return PackageManifest(
        versions=summary.versions, variant_refs=variants, artifact_refs=artifacts
    )
