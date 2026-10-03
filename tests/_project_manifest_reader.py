"""Test-only eager reader for a stored ``ProjectSnapshot`` package manifest.

Moved here from ``abicheck/project_snapshot_store.py``
(dead-code-and-single-owner, library-API pass): every production reader goes
through the lazy primitives (ADR-062 D8), and only tests loaded a whole
manifest at once. It is assembled from those same primitives, never a second
read path.
"""

from __future__ import annotations

from pathlib import Path

from abicheck.project_snapshot_store import (
    PackageManifest,
    read_artifact_ref,
    read_manifest_summary,
    read_variant_ref,
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
