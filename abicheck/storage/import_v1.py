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

"""The v1-v25 import adapter — ADR-062 A1.2, `storage-format-v2.md` Phase 1
step 2. Maps one already-serialized legacy `AbiSnapshot` document (the
`serialization.snapshot_to_dict()` shape, at any schema version this build
can still read) into a one-artifact `ProjectSnapshot` package — A1.3's
"a single-library snapshot is representable as a one-artifact project".

**Why this takes a document, not a live `AbiSnapshot`.** `storage/` may
depend only on `model` (`storage/AGENTS.md`, "Permitted imports") — it
cannot import `serialization.py` (flat-root) to build that document itself,
so the caller (a workflow, a CLI, or a future `service_dump_pipeline` step —
none of them this package's concern) builds the legacy document the way it
always has and hands it here. This keeps the layering ADR-061 D1 requires:
this module answers "how is a v1-v25 document reshaped into a v2 package",
never "how is an `AbiSnapshot` serialized" — that question already has one
owner (`serialization.py`) and this module does not become a second one.

**What is actually migrated onto the new, D8-constrained representation,
and what is not — yet.** `semantic_ir`/`semantic_ir_conflicts` are the one
part of a legacy document this adapter decodes into a typed domain object
(`model.semantic_ir.SemanticIR`) and re-encodes through `storage/dto.py`'s
`SectionDTO`, per ADR-063 Phase 8's own text: this phase *is* ADR-062 Phase 1
"executed with this plan's D8 constraint already in force". Every other key
in the document — symbols, types, layout, DWARF/PE/Mach-O facts, the whole
of what D8 eventually splits into `binary`/`declarations`/`types`/`layout`/
`debug`/... sections — has no typed, per-field domain representation to
target yet outside the legacy dataclasses `serialization.py` already owns,
so it travels as one opaque `"legacy_document"` object: the exact bytes
`snapshot_to_dict()` produced, minus the two keys promoted above. This is
not a placeholder bug; it is A1.4's own explicitly scheduled future work
("fold baseline sets and `BundleFacts` into sections... coordinating with
G38 Phase 2") and D8's full per-category split, neither of which this
adapter attempts. **No existing baseline is rewritten by this module** — it
only ever reads a document and builds new, additional structures from it,
per ADR-062 D13.

`source_schema_version` is read from the document's own `schema_version`
key (defaulting to 1, `serialization.snapshot_from_dict`'s own pre-
versioning convention) and carried into the package's `StorageVersions`
unchanged, so a migration or an audit can always answer "what producer
epoch actually emitted this" without reverse-engineering it from which
fields are present — the exact problem D2 exists to close.

**A document newer than this build knows how to interpret is refused, not
silently accepted.** `max_known_schema_version` is a required parameter
(never a default this module invents) — `storage/` cannot import
`serialization.SCHEMA_VERSION` itself (the same layering reason documented
above), so the caller, who already knows what `serialization.py`'s own
reader considers current, states it explicitly. Without this check, a
document at a schema version this build has never seen would still import
cleanly, recording that unknown version only on the informational
`source_schema_version` axis while the *rest* of the package states this
build's own current `comparison_contract_version` — exactly the "readable
but produced under semantics this build never validated" gap D2's two
fail-closed axes exist to prevent (Codex review). This mirrors, at the
document-adapter boundary, the same refusal `serialization.snapshot_from_dict`
already applies at its own boundary — not a second, independently-tuned
threshold, since it uses whatever ceiling the caller passes rather than a
value hard-coded here.

**A known, deliberately deferred gap** (the same shape `package.py`'s own
"known, deliberately deferred gap" note already documents for
`ArtifactRef.sections`): `ArtifactRef.native_identity` is empty here. A
legacy document's `build_id` field means an opaque CI identifier and is
explicitly not reused for this (ADR-062 D6) — a real content-hash/ELF-
build-ID/Mach-O-UUID/PE-PDB identity needs the artifact's own binary, which
this adapter, operating on an already-serialized document, does not have
access to. Populating it is real, separately-scoped future work for
whichever caller has the binary in hand at import time, not a gap this
adapter can close from a document alone.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .dto import SECTION_SCHEMA_VERSIONS, SEMANTIC_IR_SECTION_KIND, semantic_ir_to_dto
from .guards import mapping as _mapping
from .package import ArtifactRef, ObjectRef, ObjectStore, PackageManifest, VariantRef
from .semantic_ir_codec import semantic_ir_from_document
from .versioning import StorageVersions

__all__ = ["LEGACY_DOCUMENT_SECTION_KIND", "import_legacy_snapshot"]

#: The one opaque, not-yet-split section kind this adapter writes for
#: everything the legacy document carries beyond `semantic_ir`/
#: `semantic_ir_conflicts` — deliberately outside `package.SECTION_KINDS`'s
#: D8 vocabulary (see the module docstring's "What is actually migrated"
#: section): it names *provenance* ("this is an unmigrated v1-v25
#: document"), not a D8 content category, and inventing a category for it
#: would misrepresent it as more structured than it is.
LEGACY_DOCUMENT_SECTION_KIND = "legacy_document"

#: Keys promoted out of the legacy document onto their own, D8-constrained
#: `SectionDTO` rather than left inside the `legacy_document` object —
#: exactly the two keys `storage.semantic_ir_codec` owns.
_PROMOTED_KEYS = ("semantic_ir", "semantic_ir_conflicts")


def import_legacy_snapshot(
    legacy_document: Mapping[str, Any],
    *,
    store: ObjectStore,
    artifact_id: str,
    max_known_schema_version: int,
    variant_id: str = "default",
    artifact_kind: str | None = None,
) -> PackageManifest:
    """Import *legacy_document* (a `snapshot_to_dict()`-shaped mapping) as a
    one-artifact, one-variant `ProjectSnapshot` package, writing its content
    into *store* and returning the resulting `PackageManifest` — A1.2/A1.3.

    `artifact_id`/`variant_id` name the single artifact/variant this
    document becomes; a caller importing several libraries into one project
    calls this once per library against the same `store` and merges the
    resulting manifests' `variant_refs`/`artifact_refs` (each manifest here
    is deliberately self-contained and constructible independently, so nothing
    about this function needs to know whether it is the only library in the
    project or one of many).

    `artifact_kind` defaults to `None`, meaning "derive it from the document
    itself": the legacy document's own `platform` field (`AbiSnapshot.
    platform`, `"elf"`/`"pe"`/`"macho"`) is used when stated, and only a
    document that never states a platform at all (a pre-Phase-3 snapshot, or
    a synthetic one built without it) falls back to `"elf"`. An explicit
    `artifact_kind` argument always wins over the document — a caller that
    already knows the real kind (or is intentionally overriding it) is never
    second-guessed. What this closes: silently mislabeling a PE or Mach-O
    snapshot's `ArtifactRef.kind` as `"elf"` just because the caller took the
    default, corrupting the package's own artifact identity even though the
    document plainly states otherwise (Codex review).

    `max_known_schema_version` is the caller's own `serialization.
    SCHEMA_VERSION` (or an explicit lower ceiling) — see the module
    docstring's "A document newer than this build knows how to interpret is
    refused" section for why this module cannot default or derive it itself.
    Raises `ValueError` if the document's own `schema_version` exceeds it.
    """
    _mapping(legacy_document, "legacy_document")
    if artifact_kind is None:
        stated_platform = legacy_document.get("platform")
        artifact_kind = (
            stated_platform
            if isinstance(stated_platform, str) and stated_platform
            else "elf"
        )
    if "schema_version" in legacy_document:
        raw_schema_version = legacy_document["schema_version"]
        # `int(38.9)` truncates to `38` -- a non-integral or otherwise
        # malformed value would then silently pass as a smaller, fabricated
        # version, defeating the max_known_schema_version check just added
        # above it rather than being caught by it (Codex review, a second
        # finding on this same field: the ceiling closed "too new", but a
        # coercion ahead of it could still manufacture "not too new" from a
        # value that was never a real schema version at all). `bool` is
        # rejected too, per this package's own "never coerce a value a
        # decision reads" convention (`storage/AGENTS.md` invariant 6) --
        # this value gates the refusal above, so it is not informational.
        if isinstance(raw_schema_version, bool) or not isinstance(
            raw_schema_version, int
        ):
            raise ValueError(
                "legacy_document schema_version must be an int, not "
                f"{type(raw_schema_version).__name__} ({raw_schema_version!r})"
            )
        if raw_schema_version <= 0:
            # `StorageVersions.source_schema_version` is one of the
            # *informational* axes (`versioning._stated_count`), which
            # treats `0` (and, since it clamps a negative value to `0` too,
            # anything non-positive) as "this axis was never stated" --
            # correct for a field a real writer simply never populated, but
            # wrong here: this branch only runs when the document *did*
            # state `schema_version` explicitly. Passing a non-positive
            # value through would silently degrade a legacy document's own
            # claim about which producer epoch (and therefore which
            # semantics) governed it into "unstated", discarding the exact
            # provenance this field exists to preserve while still having
            # gated the refusal above on that same value (Codex review, a
            # second finding on this field: the non-integral-coercion fix
            # closed one way to manufacture a fabricated version, this
            # closes another).
            raise ValueError(
                "legacy_document schema_version must be a positive int, not "
                f"{raw_schema_version!r} -- 0 or negative is this format's own "
                "'unstated' sentinel, not a value a document may claim for "
                "itself"
            )
        source_schema_version = raw_schema_version
    else:
        # Absent key: `serialization.snapshot_from_dict`'s own pre-
        # versioning convention (every snapshot predates schema_version is
        # read as v1).
        source_schema_version = 1
    if source_schema_version > max_known_schema_version:
        raise ValueError(
            f"legacy_document schema_version {source_schema_version} is newer "
            f"than this build knows how to interpret (max_known_schema_version="
            f"{max_known_schema_version}); refusing to import a document whose "
            "semantics this build has not validated"
        )

    ir, conflicts = semantic_ir_from_document(legacy_document)
    remainder = {
        key: value
        for key, value in legacy_document.items()
        if key not in _PROMOTED_KEYS
    }

    sections: dict[str, ObjectRef] = {
        LEGACY_DOCUMENT_SECTION_KIND: ObjectRef(
            kind=LEGACY_DOCUMENT_SECTION_KIND, digest=store.put(remainder)
        )
    }
    section_schema_versions: dict[str, int] = {}
    if ir is not None or conflicts:
        dto = semantic_ir_to_dto(ir, conflicts)
        sections[SEMANTIC_IR_SECTION_KIND] = ObjectRef(
            kind=SEMANTIC_IR_SECTION_KIND, digest=store.put(dto.to_dict())
        )
        section_schema_versions[SEMANTIC_IR_SECTION_KIND] = SECTION_SCHEMA_VERSIONS[
            SEMANTIC_IR_SECTION_KIND
        ]

    artifact = ArtifactRef(
        artifact_id=artifact_id,
        variant_id=variant_id,
        kind=artifact_kind,
        sections=sections,
    )
    variant = VariantRef(variant_id=variant_id, artifact_ids=(artifact_id,))
    versions = StorageVersions(
        section_schema_versions=section_schema_versions,
        source_schema_version=source_schema_version,
    )
    return PackageManifest(
        versions=versions, variant_refs=(variant,), artifact_refs=(artifact,)
    )
