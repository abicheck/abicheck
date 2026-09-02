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

"""ADR-062/ADR-063 Phase 8 (redesign): the D8 section split, packaged as one
JSON document instead of a directory-backed `ProjectSnapshot` package.

**Why this exists alongside the already-merged directory format.** The
directory package (`project_snapshot_store.py`'s `manifest.json`/`refs/`/
`objects/sha256/...`, ADR-062 D6) buys real content-addressing and
independent per-section objects -- but that value is only realized once a
project actually shares content across multiple artifacts, which nothing
produces yet. For the single-artifact case every `dump` today actually
performs, the directory shape is pure storage-UX cost (many small files
instead of one, awkward to `scp`/commit/upload as a CI artifact) for zero
present benefit. This module gets the D8 section split's real properties --
independently versioned sections, structural completeness checking
(`missing_required_section_fields`) -- into a single JSON file instead:

```json
{
  "schema_version": 42,
  "sections": {
    "declarations": {"section_kind": "declarations", "section_schema_version": 1, "payload": {...}},
    "types": {...},
    ...
  },
  "section_schema_versions": {"declarations": 1, "types": 1, ...}
}
```

**Why this duplicates almost no logic.** `to_sectioned_document`/
`from_sectioned_document` are thin wrappers over `storage.import_v1
.import_legacy_snapshot`/`export_legacy_snapshot` -- the same split/DTO-
encode/decode, schema-version validation, semantic_ir handling, and
completeness checking those functions already implement and this package's
own tests already exercise -- routed through a throwaway
`InMemoryObjectStore` instead of a real directory. Only the *packaging* step
(collect each section's DTO dict inline instead of publishing it to a
content-addressed store) is new here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .import_v1 import export_legacy_snapshot, import_legacy_snapshot
from .package import ArtifactRef, InMemoryObjectStore, ObjectRef

__all__ = [
    "SECTION_SCHEMA_VERSIONS_KEY",
    "SECTIONS_KEY",
    "from_sectioned_document",
    "is_sectioned_document",
    "to_sectioned_document",
]

#: The top-level key that marks a document as this module's sectioned shape
#: rather than a flat legacy document -- never a real `AbiSnapshot` field
#: (`tests/unit/storage/test_sectioned_document.py` pins this against the
#: real dataclass fields, the same way `legacy_sections.py`'s own
#: completeness test does for `_SECTION_FIELDS`), so a flat document from
#: any schema version 1-41 can never collide with it.
SECTIONS_KEY = "sections"

#: The top-level key recording which sections this document was *written*
#: with (`PackageManifest.versions.section_schema_versions`, D2) -- the
#: single-file counterpart of a directory package's `manifest.json`. Without
#: it, `from_sectioned_document` would only ever see the sections *present*
#: in a possibly-truncated/hand-edited `sections` map: `export_legacy_snapshot`
#: only iterates the sections it is handed, so an entire section silently
#: dropped from `sections` (as opposed to a field dropped from within one) is
#: invisible to it, and `snapshot_from_dict` then defaults that section's
#: fields to empty -- lost evidence read back as confirmed absence, a real
#: false removal/addition downstream (Codex review). `project_snapshot_legacy
#: .read_legacy_snapshot_document` closed the identical gap for the
#: directory format by cross-checking `manifest.json`'s own
#: `section_schema_versions` before export; this key is that same ground
#: truth, carried inline since a single-file document has no separate
#: manifest to check against.
SECTION_SCHEMA_VERSIONS_KEY = "section_schema_versions"

#: A fixed, meaningless artifact/variant id -- this module's documents are
#: always single-artifact and never expose `ArtifactRef`/`PackageManifest`
#: identity to any caller, so nothing outside this module ever reads these
#: values back.
_ARTIFACT_ID = "snapshot"
_VARIANT_ID = "default"


def is_sectioned_document(document: Mapping[str, Any]) -> bool:
    """Whether *document* is this module's sectioned shape rather than a
    flat legacy document -- checked once, so a reader branches on it before
    choosing which unwrap path to take."""
    return SECTIONS_KEY in document


def to_sectioned_document(
    legacy_document: Mapping[str, Any], *, max_known_schema_version: int
) -> dict[str, Any]:
    """*legacy_document* (a `serialization.snapshot_to_dict()`-shaped
    mapping) repackaged into this module's single-file sectioned shape.

    *max_known_schema_version* is the same ceiling `import_legacy_snapshot`
    already requires -- this build's own `serialization.SCHEMA_VERSION`, for
    every real caller (this function is always packaging a document this
    same build just produced, or one already validated readable by it).
    """
    store = InMemoryObjectStore()
    manifest = import_legacy_snapshot(
        legacy_document,
        store=store,
        artifact_id=_ARTIFACT_ID,
        max_known_schema_version=max_known_schema_version,
        variant_id=_VARIANT_ID,
    )
    artifact = manifest.artifact_refs[0]
    sections = {
        section_kind: store.get(ref.digest)
        for section_kind, ref in artifact.sections.items()
    }
    return {
        "schema_version": manifest.versions.source_schema_version,
        SECTIONS_KEY: sections,
        SECTION_SCHEMA_VERSIONS_KEY: dict(manifest.versions.section_schema_versions),
    }


def from_sectioned_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """The inverse of `to_sectioned_document`: *document*'s sections
    reassembled into the flat `snapshot_to_dict()`-shaped document
    `serialization.snapshot_from_dict` already knows how to read.

    Raises `ValueError` for a malformed `sections` map, an unrecognized
    section kind, a section missing a field every schema version has always
    carried (`missing_required_section_fields`, via `export_legacy_snapshot`)
    -- the same corruption checks a directory package's
    `read_legacy_snapshot_document` already applies, since both ultimately
    share `export_legacy_snapshot` -- or a section entirely absent from
    `sections` that `SECTION_SCHEMA_VERSIONS_KEY` records this document was
    actually written with (Codex review: `export_legacy_snapshot` only
    iterates the sections it is handed, so a whole section dropped from
    `sections` -- as opposed to a field dropped from within one -- is
    otherwise invisible to it, and reads back as empty/confirmed-absent
    rather than failing loudly).
    """
    sections_raw = document.get(SECTIONS_KEY)
    if not isinstance(sections_raw, Mapping):
        raise ValueError(
            f"sectioned document {SECTIONS_KEY!r} must be an object, got "
            f"{type(sections_raw).__name__}"
        )
    schema_version = document.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ValueError(
            f"sectioned document schema_version must be an int, got {schema_version!r}"
        )
    stated_sections = document.get(SECTION_SCHEMA_VERSIONS_KEY)
    if not isinstance(stated_sections, Mapping):
        raise ValueError(
            f"sectioned document {SECTION_SCHEMA_VERSIONS_KEY!r} must be an "
            f"object, got {type(stated_sections).__name__}"
        )
    missing_sections = set(stated_sections) - set(sections_raw)
    if missing_sections:
        raise ValueError(
            f"sectioned document is missing section(s) {sorted(missing_sections)} "
            f"that its own {SECTION_SCHEMA_VERSIONS_KEY!r} advertises -- the "
            "document is truncated or was hand-edited; refusing to silently "
            "synthesize empty defaults for lost evidence"
        )
    store = InMemoryObjectStore()
    sections: dict[str, ObjectRef] = {}
    for section_kind, dto_dict in sections_raw.items():
        digest = store.put(dto_dict)
        sections[section_kind] = ObjectRef(kind=section_kind, digest=digest)
    artifact = ArtifactRef(
        artifact_id=_ARTIFACT_ID,
        variant_id=_VARIANT_ID,
        kind="elf",
        sections=sections,
    )
    return export_legacy_snapshot(
        artifact, store=store, source_schema_version=schema_version
    )
