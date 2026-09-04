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

**What is actually migrated onto the new, D8-constrained representation.**
`semantic_ir`/`semantic_ir_conflicts` decode into a typed domain object
(`model.semantic_ir.SemanticIR`) and re-encode through `storage/dto.py`'s
`SectionDTO`, per ADR-063 Phase 8's own text: this phase *is* ADR-062 Phase 1
"executed with this plan's D8 constraint already in force". Every other key
in the document — symbols, types, layout, DWARF/PE/Mach-O facts — is split
across D8's named `binary`/`declarations`/`types`/`layout`/`debug`/`build`/
`graph`/`provenance` sections by `storage.legacy_sections
.split_legacy_document`: each section is its own independently-versioned,
content-addressed object, not one opaque blob. What this module still does
not attempt is decoding those sections' *internal* shape into typed domain
objects the way `semantic_ir` is — `elf`/`dwarf`/`build_source`/... stay
exactly the JSON `serialization.snapshot_to_dict()` already produced inside
their own section; only the *partition* is new here. That deeper split
remains real, separately-scoped future work, the same way
`storage.legacy_sections`'s own module docstring names splitting `dwarf`'s
internal shape further.

**A1.4's "fold baseline sets and `BundleFacts` into sections" is done**
(ADR-063 Track C 8B) — `storage.import_bundle_facts`/
`storage.import_baseline_set` are the sibling import/export pairs for a
persisted G38 `BundleFacts` document and an `actions/baseline`-produced
baseline set respectively. Both are built *on* this module, not alongside
it: each per-library snapshot inside either container still travels through
`import_legacy_snapshot`/`export_legacy_snapshot` completely unchanged, one
call per library, exactly the way this docstring's own next paragraph
already anticipated ("a caller importing several libraries into one
project calls this once per library... and merges the resulting manifests'
`variant_refs`/`artifact_refs`"). What's new in that pair is only what a
single-library import never needed: folding each *container's own*
composition-level facts (a `BundleFacts`'s `manifest`/`filesystem_aliases`/
`library_filenames`/`variant_fingerprint`; a baseline set's own
`manifest.json` metadata) onto the new `VariantRef.sections` field
(`storage.package`) that pair added, via two more independent section
kinds (`dto.BUNDLE_COMPOSITION_SECTION_KIND`/`dto.BASELINE_SET_SECTION_KIND`)
— see either module's own docstring for why that content belongs on the
variant rather than any one artifact, and for what a baseline set's own
`artifacts[].snapshot`/`.binary` relative paths deliberately are *not*
carried into the package (no physical writer exists yet to make those
paths meaningful).

**No existing baseline is rewritten by this module** — it only ever reads a
document and builds new, additional structures from it, per ADR-062 D13.

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

from collections.abc import Callable, Mapping
from typing import Any

from .dto import (
    BINARY_SECTION_KIND,
    BUILD_SECTION_KIND,
    DEBUG_SECTION_KIND,
    DECLARATIONS_SECTION_KIND,
    GRAPH_SECTION_KIND,
    LAYOUT_SECTION_KIND,
    PROVENANCE_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SEMANTIC_IR_SECTION_KIND,
    TYPES_SECTION_KIND,
    SectionDTO,
    binary_from_dto,
    binary_to_dto,
    build_from_dto,
    build_to_dto,
    debug_from_dto,
    debug_to_dto,
    declarations_from_dto,
    declarations_to_dto,
    graph_from_dto,
    graph_to_dto,
    layout_from_dto,
    layout_to_dto,
    legacy_section_from_dto,
    legacy_section_to_dto,
    provenance_from_dto,
    provenance_to_dto,
    semantic_ir_from_dto,
    semantic_ir_to_dto,
    types_from_dto,
    types_to_dto,
)
from .graph_section_codec import GraphSection
from .guards import mapping as _mapping
from .legacy_sections import (
    SCHEMA_VERSION_KEY,
    join_legacy_document,
    missing_required_section_fields,
    split_legacy_document,
)
from .package import ArtifactRef, ObjectRef, ObjectStore, PackageManifest, VariantRef
from .semantic_ir_codec import semantic_ir_from_document, semantic_ir_to_document
from .sparse_section_codec import (
    BinarySection,
    BuildSection,
    DebugSection,
    DeclarationsSection,
    LayoutSection,
    ProvenanceSection,
)
from .types_section_codec import TypesSection
from .versioning import StorageVersions

#: ADR-063 Track 4 (8B), third slice: one entry per `LEGACY_SECTION_KINDS`
#: member that has its own dedicated DTO -- every one of them, as of this
#: slice. Keyed by section kind, each value is `(to_dto, from_dto)`: `to_dto`
#: takes a section's own already-split payload mapping and returns a
#: `SectionDTO` (via that section's `from_document` + `*_to_dto`, mirroring
#: `types_to_dto(TypesSection.from_document(payload))`'s own shape);
#: `from_dto` is the matching `*_from_dto` function, taking a `SectionDTO`
#: back to the typed domain object. A registry here (rather than a growing
#: `if`/`elif` chain in both `import_legacy_snapshot` and
#: `export_legacy_snapshot`) is what keeps adding a ninth section's own DTO
#: a one-line addition instead of a second edit in two functions each time.
_LEGACY_SECTION_CODECS: Mapping[
    str, tuple[Callable[[Mapping[str, Any]], SectionDTO], Callable[[SectionDTO], Any]]
] = {
    TYPES_SECTION_KIND: (
        lambda payload: types_to_dto(TypesSection.from_document(payload)),
        types_from_dto,
    ),
    GRAPH_SECTION_KIND: (
        lambda payload: graph_to_dto(GraphSection.from_document(payload)),
        graph_from_dto,
    ),
    BINARY_SECTION_KIND: (
        lambda payload: binary_to_dto(BinarySection.from_document(payload)),
        binary_from_dto,
    ),
    DECLARATIONS_SECTION_KIND: (
        lambda payload: declarations_to_dto(DeclarationsSection.from_document(payload)),
        declarations_from_dto,
    ),
    LAYOUT_SECTION_KIND: (
        lambda payload: layout_to_dto(LayoutSection.from_document(payload)),
        layout_from_dto,
    ),
    DEBUG_SECTION_KIND: (
        lambda payload: debug_to_dto(DebugSection.from_document(payload)),
        debug_from_dto,
    ),
    BUILD_SECTION_KIND: (
        lambda payload: build_to_dto(BuildSection.from_document(payload)),
        build_from_dto,
    ),
    PROVENANCE_SECTION_KIND: (
        lambda payload: provenance_to_dto(ProvenanceSection.from_document(payload)),
        provenance_from_dto,
    ),
}

__all__ = [
    "export_legacy_snapshot",
    "import_legacy_snapshot",
]

#: The three keys never assigned to a legacy section — `semantic_ir`/
#: `semantic_ir_conflicts` get their own `SEMANTIC_IR_SECTION_KIND` section,
#: and `schema_version` (`SCHEMA_VERSION_KEY`) is carried on
#: `StorageVersions.source_schema_version` instead. `storage.legacy_sections
#: .split_legacy_document`/`.join_legacy_document` already enforce this
#: exclusion internally (its own `_PROMOTED_KEYS`); this module never
#: re-derives the exclusion itself, it only relies on the same three names.


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
    # This value gates the "too new to interpret" refusal below, so -- the
    # same as the document's own schema_version just below -- it is not
    # informational and must not be coerced: `bool` is rejected (a `bool`
    # is an `int` subclass in Python), and non-`int` numeric types are
    # rejected outright rather than truncated, since a caller passing
    # `float("nan")`/`float("inf")` would otherwise silently defeat the
    # ceiling entirely -- `source_schema_version > float("nan")` and
    # `... > float("inf")` are both always `False`, regardless of how new
    # the document's own schema_version actually is (CodeRabbit review).
    if isinstance(max_known_schema_version, bool) or not isinstance(
        max_known_schema_version, int
    ):
        raise ValueError(
            "max_known_schema_version must be an int, not "
            f"{type(max_known_schema_version).__name__} "
            f"({max_known_schema_version!r})"
        )
    if max_known_schema_version <= 0:
        raise ValueError(
            "max_known_schema_version must be a positive int, not "
            f"{max_known_schema_version!r}"
        )
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
    legacy_sections = split_legacy_document(legacy_document)

    sections: dict[str, ObjectRef] = {}
    section_schema_versions: dict[str, int] = {}
    for section_kind, payload in legacy_sections.items():
        # ADR-063 Track 4 (8B): every `LEGACY_SECTION_KINDS` member now has
        # its own dedicated DTO -- `_LEGACY_SECTION_CODECS` above -- instead
        # of the generic pass-through; the `else` branch is the fallback a
        # future, not-yet-specialized section kind would use.
        codec = _LEGACY_SECTION_CODECS.get(section_kind)
        if codec is not None:
            to_dto_fn, _from_dto_fn = codec
            section_dto = to_dto_fn(payload)
        else:
            section_dto = legacy_section_to_dto(section_kind, payload)
        sections[section_kind] = ObjectRef(
            kind=section_kind, digest=store.put(section_dto.to_dict())
        )
        section_schema_versions[section_kind] = SECTION_SCHEMA_VERSIONS[section_kind]
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


def export_legacy_snapshot(
    artifact: ArtifactRef, *, store: ObjectStore, source_schema_version: int
) -> dict[str, Any]:
    """The exact inverse of `import_legacy_snapshot`'s section-writing half:
    every section in *artifact.sections* is read back from *store*, migrated
    to its current version, and reassembled into one flat
    `snapshot_from_dict()`-shaped document — the same shape
    `import_legacy_snapshot` originally accepted.

    *source_schema_version* is the artifact's own `StorageVersions
    .source_schema_version` (the caller already has the owning
    `PackageManifest`/`ManifestSummary` in hand, the same "caller states
    what it already knows, this module does not reach for a different
    object to re-derive it" pattern `max_known_schema_version` uses on the
    import side) — written back onto the document's own `schema_version`
    key so a round-tripped document is byte-for-byte indistinguishable, on
    this axis, from the one that was originally imported.

    Raises `ValueError` if *artifact* names a section kind this module does
    not recognize (`legacy_section_from_dto`/`join_legacy_document` refuse
    it), if a section's own payload is missing a field a real write always
    includes (`missing_required_section_fields` -- a truncated/hand-edited
    section that hashed and decoded fine but has lost content within its
    own JSON, Codex review), if the store cannot produce a section's
    referenced object (surfaces as whatever `store.get()` itself raises —
    `KeyError`/`ValueError` for `DirectoryObjectStore`), or if
    *source_schema_version*
    is not a positive int -- `StorageVersions.source_schema_version`
    normalizes a missing or malformed `manifest.json` value to `0`, its own
    "unstated" sentinel (the same convention `import_legacy_snapshot`'s own
    schema_version validation documents); injecting that sentinel as if it
    were a real legacy `schema_version` would silently change which
    reliability backfills `serialization.snapshot_from_dict` applies (Codex
    review) rather than failing loudly on the corrupted/hand-edited manifest
    that produced it.
    """
    if not isinstance(source_schema_version, int) or isinstance(
        source_schema_version, bool
    ):
        raise ValueError(
            "source_schema_version must be an int, not "
            f"{type(source_schema_version).__name__} ({source_schema_version!r})"
        )
    if source_schema_version <= 0:
        raise ValueError(
            "source_schema_version must be a positive int, got "
            f"{source_schema_version!r} -- 0 is StorageVersions' own "
            "'unstated' sentinel, not a value this function may inject as a "
            "real legacy schema_version"
        )
    legacy_sections: dict[str, dict[str, Any]] = {}
    document: dict[str, Any] = {}
    for section_kind, ref in artifact.sections.items():
        raw = store.get(ref.digest)
        dto = SectionDTO.from_dict(raw)
        if dto.section_kind != section_kind:
            raise ValueError(
                f"artifact {artifact.artifact_id!r} section {section_kind!r} "
                f"-> {ref.digest!r} stores a SectionDTO for kind "
                f"{dto.section_kind!r} instead -- the package is corrupted "
                "or was hand-edited"
            )
        if section_kind == SEMANTIC_IR_SECTION_KIND:
            ir, conflicts = semantic_ir_from_dto(dto)
            document.update(semantic_ir_to_document(ir, conflicts))
            continue
        codec = _LEGACY_SECTION_CODECS.get(section_kind)
        if codec is not None:
            # Every dedicated section DTO's own `from_document` already
            # refuses a missing required field (or a malformed payload)
            # structurally -- `TypesSection`/`GraphSection`'s docstrings
            # made this point first; it generalizes identically to the
            # remaining six. The `missing_required_section_fields` check the
            # generic branch below still needs is therefore redundant here,
            # so there is nothing further to check before merging.
            _to_dto_fn, from_dto_fn = codec
            document.update(from_dto_fn(dto).to_document())
        else:
            payload = legacy_section_from_dto(dto)
            # A section whose *object* hashes and decodes fine can still
            # have lost a field within its own JSON content (a truncated or
            # hand-edited payload) -- `join_legacy_document` below only
            # checks that every *present* key belongs to this section, not
            # that every field a real write always includes is present.
            # Left unchecked, a missing field silently reads back as its
            # empty/default value once `snapshot_from_dict` parses the
            # rebuilt document, turning lost evidence into confirmed absence
            # (Codex review).
            missing = missing_required_section_fields(section_kind, payload)
            if missing:
                raise ValueError(
                    f"artifact {artifact.artifact_id!r} section "
                    f"{section_kind!r} -> {ref.digest!r} is missing "
                    f"field(s) {sorted(missing)} a real write always "
                    "includes -- the section's stored content is truncated "
                    "or was hand-edited"
                )
            legacy_sections[section_kind] = payload
    document.update(join_legacy_document(legacy_sections))
    document[SCHEMA_VERSION_KEY] = source_schema_version
    return document
