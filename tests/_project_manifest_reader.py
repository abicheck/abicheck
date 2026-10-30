"""Test-only eager reader for a stored ``ProjectSnapshot`` package manifest.

Moved here from ``abicheck/project_snapshot_store.py``
(dead-code-and-single-owner, library-API pass): every production reader goes
through the lazy primitives (ADR-062 D8), and only tests loaded a whole
manifest at once. It is assembled from those same primitives, never a second
read path.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from abicheck.project_snapshot_store import (
    PackageManifest,
    read_artifact_ref,
    read_manifest_summary,
    read_variant_ref,
)
from abicheck.storage.canonical import canonical_form
from abicheck.storage.package import MANIFEST_RELPATH
from abicheck.storage.package_publish import (
    REF_ENTRY_FIELDS,
    ref_digest_of,
    ref_document as ref_document_for,
    versioned_ref_relpath,
)


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
        versions=summary.versions,
        variant_refs=variants,
        artifact_refs=artifacts,
        project_sections=summary.project_sections,
    )


def ref_document(root: str | Path, kind: str, ref_id: str) -> dict[str, Any]:
    """The record of one published `kind` ("variant"/"artifact") ref, for a
    test that corrupts it via `rewrite_ref`."""
    root_path = Path(root)
    if kind == "variant":
        return read_variant_ref(root_path, ref_id).to_dict()
    return read_artifact_ref(root_path, ref_id).to_dict()


def rewrite_ref(
    root: str | Path, kind: str, ref_id: str, record: Mapping[str, Any]
) -> None:
    """Simulate a stale, hand-edited or corrupted ref in a package-format-v2
    package: store *record* as a new, self-consistent ref document and
    repoint `manifest.json` at it, bypassing `write_project_manifest`'s own
    validation exactly the way an on-disk corruption would."""
    root_path = Path(root)
    document = canonical_form(ref_document_for(dict(record)))
    digest = ref_digest_of(document)
    path = root_path / versioned_ref_relpath(kind, ref_id, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    manifest_path = root_path / MANIFEST_RELPATH
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in data[REF_ENTRY_FIELDS[kind]]:
        if entry["id"] == ref_id:
            entry["ref_digest"] = digest
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
