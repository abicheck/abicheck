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

"""The baseline-set import/export adapter — ADR-063 Track C 8B, the second
half of `storage/import_v1.py`'s own promised follow-up ("fold baseline sets
and `BundleFacts` into sections... coordinating with G38 Phase 2").

**What a baseline set is, on disk.** `actions/baseline/build_manifest.py`
writes a directory carrying one `manifest.json` (parsed today by
`abicheck.buildsource.baseline_set.load_baseline_manifest`) whose
`artifacts[]` list names, per library, a *relative path* to that library's
own already-serialized `AbiSnapshot` document (`snapshot`), an optional
staged ELF binary (`binary`), and the sha256 digests of each
(`sha256`/`binary_sha256`) — unlike `BundleFacts`
(`storage.import_bundle_facts`), a baseline set's per-library content is
never embedded inline; it is always a sibling file the manifest merely
points at.

**Why this module never touches a filesystem.** `storage/` has no I/O
primitives of its own (`storage/AGENTS.md`) — the identical reasoning
`import_v1.py`/`import_bundle_facts.py` already state for taking an
already-serialized document instead of a live object. A baseline set adds
one more layer to that: the caller (who does have filesystem access) must
resolve every `artifacts[].snapshot` path into its already-parsed JSON
document *before* calling `import_baseline_set`, passing all of them in one
`{library: snapshot_document}` mapping alongside the raw `manifest.json`
mapping itself.

**What is baseline-set-*level* content, not per-library content.**
`manifest_version`, `project_ref`, `profile`, `snapshot_schema`, `fact_set`,
`baseline_generation`, and `generator` name no single library — the same
"variant-level, not artifact-level" content
`storage.import_bundle_facts`'s own module docstring describes for
`BundleFacts`'s own container facts, folded here via the identical
`VariantRef.sections` mechanism (`dto.baseline_set_metadata_to_dto`) rather
than a second, independently-invented scheme.

**What is deliberately not represented.** `artifacts[].snapshot`/`.artifact`/
`.binary` are relative *paths* naming where a specific physical layout put
each file — `storage.package`'s own module docstring is explicit that
nothing in this package model "reads or writes a byte of an actual file",
so there is no slot for a path here. `export_baseline_set` returns the
metadata document and the per-library snapshot documents; a caller that
owns path assignment (the actual `actions/baseline` writer, or a future one)
builds `manifest.json`'s own `artifacts[]` list from those two return
values itself. `artifacts[].binary`/`.sha256` (a staged binary's own path
and the *snapshot's* stable-content digest) are likewise not carried
through — the latter is exactly what `ObjectRef.digest` already gives once
the snapshot is stored, made redundant rather than dropped; only
`binary_sha256` (the staged binary's own content identity, independent of
anything this package format already derives) is preserved, on the owning
`ArtifactRef.native_identity` — the field ADR-062 D6 names for exactly this
kind of artifact-level content/build identity fact.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .dto import (
    BASELINE_SET_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SectionDTO,
    baseline_set_metadata_from_dto,
    baseline_set_metadata_to_dto,
)
from .guards import mapping as _mapping
from .import_v1 import export_legacy_snapshot, import_legacy_snapshot
from .package import ArtifactRef, ObjectRef, ObjectStore, PackageManifest, VariantRef
from .versioning import StorageVersions

__all__ = [
    "export_baseline_set",
    "import_baseline_set",
]

#: `manifest.json`'s own top-level metadata keys other than `artifacts[]` —
#: `abicheck.buildsource.baseline_set.BaselineManifest`'s own field set,
#: duplicated here as plain strings (never imported: `buildsource/` is a
#: flat-root package `storage/` may not depend on) since this is the exact
#: partition `import_baseline_set`/`export_baseline_set` fold onto
#: `BASELINE_SET_SECTION_KIND`.
_METADATA_KEYS = (
    "manifest_version",
    "project_ref",
    "profile",
    "snapshot_schema",
    "fact_set",
    "baseline_generation",
    "generator",
)

#: `abicheck.buildsource.baseline_set.SUPPORTED_MANIFEST_VERSIONS`, duplicated
#: for the identical layering reason `import_bundle_facts.py`'s own
#: duplicated constants give: `storage/` may not import `buildsource/` (a
#: flat-root package). A `manifest_version` outside this set is a shape this
#: adapter's own fixed `_METADATA_KEYS` partition has never been validated
#: against — importing it anyway would silently retain only today's known
#: keys and re-export the unsupported version unchanged, discarding whatever
#: fields or semantics that future version actually added.
_SUPPORTED_MANIFEST_VERSIONS = frozenset({1})


def import_baseline_set(
    manifest_document: Mapping[str, Any],
    snapshot_documents: Mapping[str, Mapping[str, Any]],
    *,
    store: ObjectStore,
    max_known_schema_version: int,
    variant_id: str = "default",
) -> PackageManifest:
    """Import a baseline set's already-loaded `manifest.json` mapping
    (*manifest_document*) plus the already-loaded per-library `AbiSnapshot`
    documents each of its `artifacts[]` entries names (*snapshot_documents*,
    keyed by `BaselineArtifact.library`) as a one-variant, multi-artifact
    `ProjectSnapshot` package.

    `manifest_version` is validated against `_SUPPORTED_MANIFEST_VERSIONS`
    the same way `buildsource.baseline_set.resolve_target`/`resolve_bundle`
    already validate it — a version this adapter's fixed `_METADATA_KEYS`
    partition was never checked against is refused rather than silently
    imported and re-exported unchanged, discarding whatever that future
    version actually added.

    Raises `ValueError` if `manifest_version` is missing or not a supported
    version; if `manifest_document['artifacts']` is missing, empty, or not a
    list; if an entry names no (or a duplicate) `library`; if
    `snapshot_documents` has no entry for a library `artifacts[]` names; or
    if the per-library snapshots do not all declare the same `schema_version`
    (see `import_bundle_facts`'s own docstring for why one
    `source_schema_version` is required — the identical constraint applies
    here for the identical reason).
    """
    _mapping(manifest_document, "manifest_document")
    _mapping(snapshot_documents, "snapshot_documents")
    manifest_version = manifest_document.get("manifest_version")
    # `bool` is an `int` subclass -- `True in {1}` is `True` -- so it must be
    # excluded explicitly, or a manifest declaring `"manifest_version": true`
    # would silently pass as version 1 (this package's own "never coerce a
    # value a decision reads" convention, `storage/AGENTS.md` invariant 6).
    if isinstance(manifest_version, bool) or manifest_version not in (
        _SUPPORTED_MANIFEST_VERSIONS
    ):
        raise ValueError(
            f"manifest_document manifest_version {manifest_version!r} is not "
            "one this adapter understands (supported: "
            f"{sorted(_SUPPORTED_MANIFEST_VERSIONS)}) -- upgrade this build, "
            "or regenerate the baseline set with a compatible actions/baseline "
            "version"
        )
    raw_artifacts = manifest_document.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ValueError(
            "manifest_document['artifacts'] must be a non-empty list, got "
            f"{raw_artifacts!r}"
        )

    artifact_refs: list[ArtifactRef] = []
    section_schema_versions: dict[str, int] = {}
    source_schema_version: int | None = None
    seen_libraries: set[str] = set()
    for index, entry in enumerate(raw_artifacts):
        _mapping(entry, f"manifest_document['artifacts'][{index}]")
        library = entry.get("library")
        if not isinstance(library, str) or not library:
            raise ValueError(
                f"manifest_document['artifacts'][{index}] has no non-empty "
                "'library' name"
            )
        if library in seen_libraries:
            raise ValueError(
                f"manifest_document['artifacts'] names library {library!r} "
                "more than once"
            )
        seen_libraries.add(library)
        if library not in snapshot_documents:
            raise ValueError(
                f"snapshot_documents is missing an entry for library "
                f"{library!r}, named in manifest_document['artifacts']"
            )
        member_manifest = import_legacy_snapshot(
            snapshot_documents[library],
            store=store,
            artifact_id=library,
            variant_id=variant_id,
            max_known_schema_version=max_known_schema_version,
        )
        (artifact,) = member_manifest.artifact_refs
        if "binary_sha256" in entry:
            binary_sha256 = entry["binary_sha256"]
            if not isinstance(binary_sha256, str):
                raise ValueError(
                    f"manifest_document['artifacts'][{index}]['binary_sha256'] "
                    f"must be a string, not {type(binary_sha256).__name__} "
                    f"({binary_sha256!r})"
                )
        else:
            binary_sha256 = ""
        if binary_sha256:
            # `""` (absent key, or `BaselineArtifact.binary_sha256`'s own
            # documented default) means "no staged binary" -- not an error,
            # and not a value worth carrying as a native-identity fact.
            # A fresh `ArtifactRef` carrying the same identity/sections plus
            # this one extra native-identity fact -- `ArtifactRef` is a
            # frozen dataclass, so this is reconstruction, not mutation.
            artifact = ArtifactRef(
                artifact_id=artifact.artifact_id,
                variant_id=artifact.variant_id,
                kind=artifact.kind,
                native_identity={"binary_sha256": binary_sha256},
                sections=artifact.sections,
            )
        artifact_refs.append(artifact)
        section_schema_versions.update(member_manifest.versions.section_schema_versions)
        member_schema_version = member_manifest.versions.source_schema_version
        if source_schema_version is None:
            source_schema_version = member_schema_version
        elif member_schema_version != source_schema_version:
            raise ValueError(
                "manifest_document's per-library snapshots do not all "
                f"declare the same schema_version ({source_schema_version} "
                f"vs {member_schema_version} for library {library!r}) -- a "
                "ProjectSnapshot package's manifest tracks one "
                "source_schema_version for the whole package, so a "
                "baseline set mixing schema versions across libraries "
                "cannot be represented"
            )
    assert source_schema_version is not None  # non-empty loop guarantees this

    # Only keys actually present in *manifest_document* -- `.get(key)` would
    # store an absent optional field (`fact_set`/`baseline_generation`/
    # `generator`, all legitimately absent on a real manifest) as an
    # explicit `None`, so `export_baseline_set` would then re-export a
    # `null` key the original document never had (CodeRabbit review).
    metadata_payload = {
        key: manifest_document[key]
        for key in _METADATA_KEYS
        if key in manifest_document
    }
    metadata_dto = baseline_set_metadata_to_dto(metadata_payload)
    metadata_ref = ObjectRef(
        kind=BASELINE_SET_SECTION_KIND, digest=store.put(metadata_dto.to_dict())
    )
    section_schema_versions[BASELINE_SET_SECTION_KIND] = SECTION_SCHEMA_VERSIONS[
        BASELINE_SET_SECTION_KIND
    ]

    variant = VariantRef(
        variant_id=variant_id,
        artifact_ids=tuple(artifact.artifact_id for artifact in artifact_refs),
        sections={BASELINE_SET_SECTION_KIND: metadata_ref},
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


def export_baseline_set(
    manifest: PackageManifest, *, store: ObjectStore, variant_id: str = "default"
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """The exact inverse of `import_baseline_set`'s two input mappings:
    returns `(metadata_document, snapshot_documents)`, where
    *metadata_document* is `manifest.json`'s own metadata (everything but
    `artifacts[]`) and *snapshot_documents* is `{library: snapshot_document}`
    for every artifact under *variant_id*.

    Deliberately does NOT reconstruct `manifest.json`'s own `artifacts[]`
    list — see the module docstring's "What is deliberately not
    represented" section for why no path is recoverable from this package
    alone. A caller that owns path assignment builds that list itself from
    this function's two return values (plus, for `binary_sha256`, each
    artifact's own `native_identity`).

    Raises `ValueError` if *variant_id* names no variant in *manifest*, or
    if that variant carries no `BASELINE_SET_SECTION_KIND` section (never
    produced by anything but `import_baseline_set` itself).
    """
    variant = next(
        (v for v in manifest.variant_refs if v.variant_id == variant_id), None
    )
    if variant is None:
        raise ValueError(f"no variant {variant_id!r} in this manifest")
    metadata_ref = variant.sections.get(BASELINE_SET_SECTION_KIND)
    if metadata_ref is None:
        raise ValueError(
            f"variant {variant_id!r} has no {BASELINE_SET_SECTION_KIND!r} "
            "section -- this manifest was not produced by import_baseline_set"
        )
    metadata_dto = SectionDTO.from_dict(store.get(metadata_ref.digest))
    metadata_document = baseline_set_metadata_from_dto(metadata_dto)

    source_schema_version = manifest.versions.source_schema_version
    snapshot_documents: dict[str, dict[str, Any]] = {}
    for artifact_id in variant.artifact_ids:
        artifact = next(
            a for a in manifest.artifact_refs if a.artifact_id == artifact_id
        )
        snapshot_documents[artifact_id] = export_legacy_snapshot(
            artifact, store=store, source_schema_version=source_schema_version
        )
    return metadata_document, snapshot_documents
