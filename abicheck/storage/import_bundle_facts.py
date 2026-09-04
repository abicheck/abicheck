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

"""The `BundleFacts` import/export adapter — ADR-063 Track C 8B, the first
half of `storage/import_v1.py`'s own promised follow-up ("fold baseline sets
and `BundleFacts` into sections... coordinating with G38 Phase 2").

**Why a `BundleFacts` document folds naturally onto one variant.** A G38
`BundleFacts` (`abicheck/bundle_facts.py`) is, at its core, exactly what
`storage.import_v1.import_legacy_snapshot`'s own docstring already
anticipated: "a caller importing several libraries into one project calls
[it] once per library... and merges the resulting manifests' `variant_refs`/
`artifact_refs`". `BundleFacts.per_library_snapshots` is precisely that
per-library map, and every one of its values is the exact
`snapshot_to_dict()` shape `import_legacy_snapshot` already accepts —
`import_bundle_facts` below calls it once per library, reusing the D8
section split unchanged, and folds the resulting `ArtifactRef`s under one
shared `VariantRef` rather than growing a second per-library import path.

**Why this module takes a document, not a live `BundleFacts`.** `storage/`
may depend only on `model` (`storage/AGENTS.md`) — it cannot import
`abicheck.bundle_facts`/`abicheck.bundle_facts_serialization`/
`abicheck.bundle_manifest` (all flat-root modules) to build or interpret a
live `BundleFacts` object, the identical layering reason `import_v1.py`'s
own module docstring gives for taking an already-serialized `AbiSnapshot`
document. The caller — who already has `bundle_facts_serialization
.bundle_facts_to_dict()` in hand — passes the resulting mapping here
unchanged.

**What is bundle-*composition* content, not per-library content.**
`variant_fingerprint`, `manifest` (an `InstantiationManifest`, itself already
flattened to JSON by the caller's own `bundle_manifest.manifest_to_dict()`),
`filesystem_aliases`, and `library_filenames` name no single library — they
describe the bundle as a whole, the way `storage.package.VariantRef.declared`/
`.captured` already describe a variant's own coordinates rather than any one
artifact's. `dto.bundle_composition_to_dto` wraps them as one more
`SectionDTO`, attached to `VariantRef.sections` (ADR-063 Track C 8B's own
addition to that dataclass) rather than squeezed into any single
`ArtifactRef`.

**Why every per-library snapshot must agree on one `source_schema_version`.**
`PackageManifest.versions` carries exactly one `StorageVersions` for the
whole package — there is no per-artifact schema-version axis for
`export_bundle_facts` to read back from later. Every real `BundleFacts`
producer (`bundle_facts.capture_bundle_facts`) captures every member
snapshot from the same in-process `dump`/`compare` run, so this is not a
narrowing of what can actually occur — a hand-edited or corrupted document
mixing schema versions across libraries is refused outright (fail closed,
matching this package's own established convention) rather than silently
picking one arbitrarily and lying about the other library's real
provenance.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .dto import (
    BUNDLE_COMPOSITION_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SectionDTO,
    bundle_composition_from_dto,
    bundle_composition_to_dto,
)
from .guards import mapping as _mapping
from .import_v1 import export_legacy_snapshot, import_legacy_snapshot
from .package import ObjectRef, ObjectStore, PackageManifest, VariantRef
from .versioning import StorageVersions

__all__ = [
    "BUNDLE_FACTS_ARTIFACT_TYPE",
    "export_bundle_facts",
    "import_bundle_facts",
]

#: Self-describing document-type marker, duplicated here rather than
#: imported — the same reason `storage.bundle_facts_validation
#: .BUNDLE_ARCHIVE_ARTIFACT_TYPE` duplicates the plain-JSON marker's sibling
#: value for the G40 archive *container* instead of importing
#: `bundle_facts.py`: `storage/` may depend only on `model`
#: (`storage/AGENTS.md`), so it cannot import the module that owns this
#: constant. Must always equal `abicheck.bundle_facts
#: .BUNDLE_FACTS_ARTIFACT_TYPE` — pinned by
#: `tests/unit/storage/test_import_bundle_facts.py`'s own cross-check so the
#: two cannot silently drift apart.
BUNDLE_FACTS_ARTIFACT_TYPE = "abicheck.bundle-facts"

#: `abicheck.bundle_facts.BUNDLE_FACTS_SCHEMA_VERSION`, duplicated for the
#: identical reason as `BUNDLE_FACTS_ARTIFACT_TYPE` above -- required
#: alongside it, since `bundle_facts_serialization.bundle_facts_from_dict`
#: rejects a document declaring `artifact_type` at a `schema_version` below
#: 2 (the version `artifact_type` itself was introduced at) as
#: self-contradictory. `export_bundle_facts` always emits the *current*
#: shape, exactly like `bundle_facts_to_dict()` itself does — never
#: whatever version happened to be recorded on the package's own
#: `StorageVersions.source_schema_version` axis, which tracks each
#: per-library `AbiSnapshot`'s schema, a wholly independent axis from the
#: `BundleFacts` container's own shape.
_BUNDLE_FACTS_SCHEMA_VERSION = 2

#: `abicheck.bundle_facts.DEFAULT_VARIANT_FINGERPRINT`, duplicated for the
#: identical reason as `BUNDLE_FACTS_ARTIFACT_TYPE` above.
_DEFAULT_VARIANT_FINGERPRINT = "default"


def import_bundle_facts(
    bundle_facts_document: Mapping[str, Any],
    *,
    store: ObjectStore,
    max_known_schema_version: int,
    variant_id: str = "default",
) -> PackageManifest:
    """Import *bundle_facts_document* (a `bundle_facts_serialization
    .bundle_facts_to_dict()`-shaped mapping) as a one-variant,
    multi-artifact `ProjectSnapshot` package, writing its content into
    *store* and returning the resulting `PackageManifest`.

    `artifact_type`, when present, is checked against
    `BUNDLE_FACTS_ARTIFACT_TYPE` and rejected on mismatch — whoever built the
    document declared it as something else, and importing it as bundle
    facts anyway would silently score a comparison against a document
    nobody asked to be read this way, mirroring
    `bundle_facts_serialization.bundle_facts_from_dict`'s own check. Absent
    entirely (a pre-marker, schema_version 1 `BundleFacts` document) is
    tolerated the same way that function tolerates it.

    `max_known_schema_version` is forwarded unchanged to
    `import_legacy_snapshot` for every per-library snapshot — see that
    function's own docstring for why this module cannot default or derive
    it itself.

    Raises `ValueError` if `per_library_snapshots` is missing, empty, or not
    a mapping, or if the per-library snapshots do not all declare the same
    `schema_version` (see the module docstring's "one `source_schema_version`"
    section).
    """
    _mapping(bundle_facts_document, "bundle_facts_document")
    artifact_type = bundle_facts_document.get("artifact_type")
    if artifact_type is not None and artifact_type != BUNDLE_FACTS_ARTIFACT_TYPE:
        raise ValueError(
            f"bundle facts: unexpected artifact_type {artifact_type!r} "
            f"(expected {BUNDLE_FACTS_ARTIFACT_TYPE!r})"
        )
    if "per_library_snapshots" not in bundle_facts_document:
        raise ValueError(
            "bundle_facts_document is missing the required "
            "'per_library_snapshots' mapping"
        )
    raw_snapshots = bundle_facts_document["per_library_snapshots"]
    _mapping(raw_snapshots, "bundle_facts_document['per_library_snapshots']")
    if not raw_snapshots:
        raise ValueError(
            "bundle_facts_document['per_library_snapshots'] must not be empty"
        )

    artifact_refs = []
    section_schema_versions: dict[str, int] = {}
    source_schema_version: int | None = None
    for library_name, snapshot_document in raw_snapshots.items():
        member_manifest = import_legacy_snapshot(
            snapshot_document,
            store=store,
            artifact_id=library_name,
            variant_id=variant_id,
            max_known_schema_version=max_known_schema_version,
        )
        (artifact,) = member_manifest.artifact_refs
        artifact_refs.append(artifact)
        section_schema_versions.update(member_manifest.versions.section_schema_versions)
        member_schema_version = member_manifest.versions.source_schema_version
        if source_schema_version is None:
            source_schema_version = member_schema_version
        elif member_schema_version != source_schema_version:
            raise ValueError(
                "bundle_facts_document's per-library snapshots do not all "
                f"declare the same schema_version ({source_schema_version} "
                f"vs {member_schema_version} for library {library_name!r}) "
                "-- a ProjectSnapshot package's manifest tracks one "
                "source_schema_version for the whole package, so a bundle "
                "mixing schema versions across libraries cannot be "
                "represented; every real BundleFacts producer captures all "
                "of its member snapshots from one build, so this should "
                "never occur outside a hand-edited or corrupted document"
            )
    assert source_schema_version is not None  # non-empty loop guarantees this

    composition_payload = {
        "variant_fingerprint": str(
            bundle_facts_document.get("variant_fingerprint")
            or _DEFAULT_VARIANT_FINGERPRINT
        ),
        "manifest": bundle_facts_document.get("manifest"),
        "filesystem_aliases": dict(
            bundle_facts_document.get("filesystem_aliases") or {}
        ),
        "library_filenames": dict(bundle_facts_document.get("library_filenames") or {}),
    }
    composition_dto = bundle_composition_to_dto(composition_payload)
    composition_ref = ObjectRef(
        kind=BUNDLE_COMPOSITION_SECTION_KIND,
        digest=store.put(composition_dto.to_dict()),
    )
    section_schema_versions[BUNDLE_COMPOSITION_SECTION_KIND] = SECTION_SCHEMA_VERSIONS[
        BUNDLE_COMPOSITION_SECTION_KIND
    ]

    variant = VariantRef(
        variant_id=variant_id,
        artifact_ids=tuple(artifact.artifact_id for artifact in artifact_refs),
        sections={BUNDLE_COMPOSITION_SECTION_KIND: composition_ref},
    )
    versions = StorageVersions(
        section_schema_versions=section_schema_versions,
        source_schema_version=source_schema_version,
    )
    return PackageManifest(
        versions=versions,
        variant_refs=(variant,),
        artifact_refs=tuple(artifact_refs),
    )


def export_bundle_facts(
    manifest: PackageManifest, *, store: ObjectStore, variant_id: str = "default"
) -> dict[str, Any]:
    """The exact inverse of `import_bundle_facts`: every artifact under
    *variant_id* is read back via `export_legacy_snapshot`, the variant's own
    `BUNDLE_COMPOSITION_SECTION_KIND` section is read back via
    `bundle_composition_from_dto`, and both are reassembled into one
    `bundle_facts_serialization.bundle_facts_from_dict()`-shaped document.

    Raises `ValueError` if *variant_id* names no variant in *manifest*, or if
    that variant carries no `BUNDLE_COMPOSITION_SECTION_KIND` section (never
    produced by anything but `import_bundle_facts` itself, so this means the
    manifest was not built by it, or was hand-edited).
    """
    variant = next(
        (v for v in manifest.variant_refs if v.variant_id == variant_id), None
    )
    if variant is None:
        raise ValueError(f"no variant {variant_id!r} in this manifest")
    composition_ref = variant.sections.get(BUNDLE_COMPOSITION_SECTION_KIND)
    if composition_ref is None:
        raise ValueError(
            f"variant {variant_id!r} has no {BUNDLE_COMPOSITION_SECTION_KIND!r} "
            "section -- this manifest was not produced by import_bundle_facts"
        )
    composition_dto = SectionDTO.from_dict(store.get(composition_ref.digest))
    composition = bundle_composition_from_dto(composition_dto)

    source_schema_version = manifest.versions.source_schema_version
    per_library_snapshots: dict[str, Any] = {}
    for artifact_id in variant.artifact_ids:
        artifact = next(
            a for a in manifest.artifact_refs if a.artifact_id == artifact_id
        )
        per_library_snapshots[artifact_id] = export_legacy_snapshot(
            artifact, store=store, source_schema_version=source_schema_version
        )

    return {
        "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
        "schema_version": _BUNDLE_FACTS_SCHEMA_VERSION,
        "variant_fingerprint": composition.get(
            "variant_fingerprint", _DEFAULT_VARIANT_FINGERPRINT
        ),
        "per_library_snapshots": per_library_snapshots,
        "filesystem_aliases": composition.get("filesystem_aliases", {}),
        "library_filenames": composition.get("library_filenames", {}),
        "manifest": composition.get("manifest"),
    }
