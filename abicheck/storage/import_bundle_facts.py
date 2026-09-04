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

from collections.abc import Callable, Mapping
from typing import Any

from ..errors import IncompatibleSnapshotSchemaError
from .dto import (
    BUNDLE_COMPOSITION_SECTION_KIND,
    SECTION_SCHEMA_VERSIONS,
    SectionDTO,
    bundle_composition_from_dto,
    bundle_composition_to_dto,
)
from .guards import mapping as _mapping
from .import_v1 import export_legacy_snapshot, import_legacy_snapshot
from .package import ArtifactRef, ObjectRef, ObjectStore, PackageManifest, VariantRef
from .ref_ids import resolve_ref_ids
from .versioning import StorageVersions

#: `native_identity` key `import_bundle_facts` stamps onto each per-library
#: `ArtifactRef`, recording the library's own real name -- needed because
#: `artifact_id` itself may be an opaque, `resolve_ref_ids`-generated id
#: rather than the literal name (see that function's own docstring for
#: when and why). `export_bundle_facts` reads this back to reconstruct
#: `per_library_snapshots`' real keys.
_LIBRARY_NAME_KEY = "library_name"

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


#: Sentinel distinguishing "key absent from the document" from "key present
#: with an explicit `None`/`null` value" -- `bundle_facts_from_dict`'s own
#: `validated_alias_map`/`validated_filename_map` reject a present `None`
#: (it fails their `isinstance(raw, dict)` check) while `.get(key, {})`
#: only ever defaults a truly *absent* key. Passing `None` through
#: unconditionally for both cases would silently launder an explicit-null
#: document -- one the canonical reader rejects -- into a valid empty
#: mapping (Codex review, fresh evidence beyond the non-mapping-value
#: finding this same pair of functions already fixed).
_ABSENT = object()


def _validated_filesystem_aliases(raw: Any) -> dict[str, list[str]]:
    """`bundle_facts_serialization.bundle_facts_to_dict()`'s own
    `filesystem_aliases` shape (`{library: [alias, ...]}`), validated rather
    than defaulted through: *absent* (`raw is _ABSENT`) means "no aliases
    captured" (a real, common case — `capture_bundle_facts` only populates
    this when given real on-disk paths), but any other falsey-but-present
    non-mapping (`None`, `[]`, `""`, `0`) is malformed input, not an empty
    collection, and must not be silently normalized to one via `... or {}`
    (Codex review) -- that would make a producer's genuine "no aliases"
    indistinguishable from a corrupted or explicitly-null document, the
    same distinction `validated_alias_map` itself draws by rejecting a
    non-`dict` (`None` included) outright."""
    if raw is _ABSENT:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"bundle_facts_document['filesystem_aliases'] must be a mapping, "
            f"not {type(raw).__name__} ({raw!r})"
        )
    validated: dict[str, list[str]] = {}
    for library, aliases in raw.items():
        if not isinstance(library, str):
            raise ValueError(
                "bundle_facts_document['filesystem_aliases'] has a non-string "
                f"key: {library!r}"
            )
        if not isinstance(aliases, list) or not all(
            isinstance(alias, str) for alias in aliases
        ):
            raise ValueError(
                f"bundle_facts_document['filesystem_aliases'][{library!r}] "
                f"must be a list of strings, not {aliases!r}"
            )
        validated[library] = list(aliases)
    return validated


def _validated_library_filenames(raw: Any) -> dict[str, str]:
    """`bundle_facts_to_dict()`'s own `library_filenames` shape
    (`{library: filename}`), validated the same way
    `_validated_filesystem_aliases` is, for the identical reason."""
    if raw is _ABSENT:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"bundle_facts_document['library_filenames'] must be a mapping, "
            f"not {type(raw).__name__} ({raw!r})"
        )
    validated: dict[str, str] = {}
    for library, filename in raw.items():
        if not isinstance(library, str) or not isinstance(filename, str):
            raise ValueError(
                "bundle_facts_document['library_filenames'] must map strings "
                f"to strings, got {library!r}: {filename!r}"
            )
        validated[library] = filename
    return validated


def _validated_manifest_entry(raw: Any) -> dict[str, Any]:
    """One `bundle_facts_document['manifest']['provides']` entry, checked
    against the same structural rules `bundle_manifest
    ._validate_manifest_entry_shape`/`_parse_manifest_entry`/
    `_parse_template_instantiations` enforce -- duplicated here for the
    identical layering reason `_validated_manifest`'s own docstring gives
    (`bundle_manifest.py` is a flat-root module `storage/` may not import)
    -- and returns a normalized copy with every decision-bearing identity
    value (`symbol`/`pattern`/`template`, `library`, and each template
    instantiation's own key/value) coerced to `str` the identical way
    `_parse_manifest_entry`/`_parse_template_instantiations` themselves
    unconditionally coerce them. A raw non-string value here (e.g. the
    JSON float `1.0`) must be stored as that exact coerced string, not
    passed through unvalidated: `SectionDTO` canonicalization could
    otherwise silently rewrite it in a way that changes what string the
    canonical reader would have produced (`1.0` -> int `1` -> `str(1)` ==
    `"1"`, vs. the canonical parser's own unconditional `str(1.0)` ==
    `"1.0"`) -- Codex review, fresh evidence beyond the entry-shape-only
    finding this same function already fixed (an earlier version validated
    the shape but stored every value verbatim)."""
    if not isinstance(raw, Mapping):
        raise ValueError(
            "bundle_facts_document['manifest']['provides'] entry must be a "
            f"mapping, not {type(raw).__name__} ({raw!r})"
        )
    shape_keys = [k for k in ("symbol", "pattern", "template") if k in raw]
    if len(shape_keys) != 1:
        raise ValueError(
            "bundle_facts_document['manifest']['provides'] entry must have "
            f"exactly one of 'symbol', 'pattern', or 'template': {raw!r}"
        )
    if "optional_provider" in raw and not isinstance(raw["optional_provider"], bool):
        raise ValueError(
            "bundle_facts_document['manifest']['provides'] entry's "
            "'optional_provider' must be a boolean, not "
            f"{raw['optional_provider']!r}"
        )
    shape = shape_keys[0]
    normalized = dict(raw)
    normalized[shape] = str(raw[shape])
    # `library = str(raw["library"]) if raw.get("library") else None` --
    # `_parse_manifest_entry`'s own coercion: a truthy value is stringified,
    # a falsey one (including an absent key) means "no library", dropped
    # here rather than stored as an explicit `null`/`""` the canonical
    # parser itself never produces.
    if raw.get("library"):
        normalized["library"] = str(raw["library"])
    else:
        normalized.pop("library", None)
    if shape == "template":
        instantiations = raw.get("instantiations", [])
        if not isinstance(instantiations, list) or not instantiations:
            raise ValueError(
                "bundle_facts_document['manifest']['provides'] template "
                f"entry needs a non-empty 'instantiations' list: {raw!r}"
            )
        if not all(isinstance(inst, Mapping) for inst in instantiations):
            raise ValueError(
                "bundle_facts_document['manifest']['provides'] template "
                f"entry's 'instantiations' must be a list of mappings: "
                f"{raw!r}"
            )
        # Stored as an ordered list of `[key, value]` pairs, not a plain
        # dict: `canonical.canonical_form` sorts every mapping's keys
        # alphabetically before storage, and `_expand_instantiations`
        # (`bundle_manifest.py`) builds each template's expanded signature
        # from `inst.values()` in *insertion* order -- a non-lexical
        # instantiation (e.g. `{"Z": "first", "A": "second"}`) would
        # silently reorder to `T<second, first>` after import/export,
        # potentially matching the wrong promised symbol (Codex review,
        # fresh evidence). `_manifest_entry_for_export` is the inverse.
        normalized["instantiations"] = [
            [[str(k), str(v)] for k, v in inst.items()] for inst in instantiations
        ]
    return normalized


def _decode_template_instantiation_pairs(pairs: Any) -> dict[str, str]:
    """One stored `[[key, value], ...]` template-instantiation pair list
    (`_validated_manifest_entry`'s own order-preserving encoding), decoded
    back to `{key: value}` -- strictly, since this reads untrusted stored
    content back into contract evidence a comparison scores findings
    against.

    A plain `dict(pairs)` conversion silently keeps only the *last* value
    for a repeated parameter name, collapsing e.g. two `T` entries into one
    and describing a different promised template signature than the one
    actually stored (Codex review) -- rejected here instead, alongside a
    malformed pair (not a two-element `[key, value]` list of strings).
    """
    if not isinstance(pairs, list):
        raise ValueError(
            "a stored template instantiation must be a list of [key, value] "
            f"pairs, not {type(pairs).__name__} ({pairs!r})"
        )
    decoded: dict[str, str] = {}
    for pair in pairs:
        if (
            not isinstance(pair, list)
            or len(pair) != 2
            or not isinstance(pair[0], str)
            or not isinstance(pair[1], str)
        ):
            raise ValueError(
                "a stored template instantiation pair must be "
                f"[<str parameter>, <str value>], got {pair!r}"
            )
        parameter, value = pair
        if parameter in decoded:
            raise ValueError(
                f"a stored template instantiation names parameter "
                f"{parameter!r} more than once: {pairs!r} -- the package is "
                "corrupted or was hand-edited"
            )
        decoded[parameter] = value
    return decoded


def _manifest_entry_for_export(entry: Mapping[str, Any]) -> dict[str, Any]:
    """Inverse of `_validated_manifest_entry`'s order-preserving
    `instantiations` encoding: reconstructs each *template* entry's
    instantiations as a plain `dict`, built from its stored `[[key,
    value], ...]` pair list in the same order, for callers of
    `export_bundle_facts` that expect the same shape `bundle_manifest
    .manifest_entry_from_dict` itself accepts.

    Only applied to a `template`-shaped entry: `_validated_manifest_entry`
    only produces (and validates) the pair-list encoding for that shape --
    a `symbol`/`pattern` entry's own `instantiations` key, if present, is
    an ignored extra field never rewritten at import time (this adapter's
    "not decoded, only partitioned" contract for content it doesn't
    itself own), so it is never in the pair-list shape here. Decoding it
    unconditionally would raise on export for a document that import
    itself accepted unchanged (Codex review, fresh evidence)."""
    exported = dict(entry)
    if "template" in exported and "instantiations" in exported:
        exported["instantiations"] = [
            _decode_template_instantiation_pairs(pairs)
            for pairs in exported["instantiations"]
        ]
    return exported


def _validated_manifest(raw: Any) -> Any:
    """*raw*, checked against the shape
    `bundle_manifest.manifest_from_dict` requires -- a mapping with a
    list-valued `"provides"` key, each entry itself validated and
    normalized via `_validated_manifest_entry` -- `None`/absent means "no
    instantiation manifest was captured", tolerated the same way that
    function tolerates it.

    A `manifest` that passes this check but still fails
    `manifest_from_dict`'s own decode still round-trips through this
    adapter unchanged -- the same "not decoded, only partitioned" contract
    `legacy_section_to_dto`'s own docstring states for every other section
    here; only structural rejections `manifest_from_dict` actually raises
    on are replicated, and only the specific decision-bearing values that
    function unconditionally coerces are normalized.
    """
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or not isinstance(raw.get("provides"), list):
        raise ValueError(
            "bundle_facts_document['manifest'] must be a mapping with a "
            f"list-valued 'provides' key, not {raw!r}"
        )
    normalized_provides = [
        _validated_manifest_entry(entry) for entry in raw["provides"]
    ]
    return {**raw, "provides": normalized_provides}


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

    The container's own `schema_version` (distinct from any per-library
    `AbiSnapshot.schema_version`) is validated exactly the way
    `bundle_facts_serialization.bundle_facts_from_dict` validates it: a
    value newer than `_BUNDLE_FACTS_SCHEMA_VERSION` is refused outright
    (`IncompatibleSnapshotSchemaError`) rather than silently accepted and
    reduced to today's four known composition keys, and a `schema_version`
    of 2 or newer requires the `artifact_type` marker (added at exactly that
    version) — either without the other is a self-contradictory,
    hand-edited-or-corrupted document. `artifact_type`, when required or
    merely present, is checked against `BUNDLE_FACTS_ARTIFACT_TYPE` and
    rejected on mismatch — whoever built the document declared it as
    something else, and importing it as bundle facts anyway would silently
    score a comparison against a document nobody asked to be read this way.
    A true legacy document (no `schema_version` key at all, or exactly `1`)
    needs neither.

    `max_known_schema_version` is forwarded unchanged to
    `import_legacy_snapshot` for every per-library snapshot — see that
    function's own docstring for why this module cannot default or derive
    it itself.

    Raises `ValueError` if `per_library_snapshots` is missing or not a
    mapping (an explicitly present but *empty* mapping is accepted -- a
    vacuous bundle is still a valid one), or if the per-library snapshots
    do not all declare the same `schema_version` (see the module
    docstring's "one `source_schema_version`" section).
    """
    _mapping(bundle_facts_document, "bundle_facts_document")
    raw_container_schema_version_value = bundle_facts_document.get(
        "schema_version", _BUNDLE_FACTS_SCHEMA_VERSION
    )
    # `int(...)`, not a strict `isinstance` gate: `bundle_facts_from_dict`
    # itself normalizes via a bare `int(...)` call (so a document spelling
    # this `"1"` -- still exactly what `int("1") == 1` accepts -- keeps
    # loading; `tests/test_bundle_facts_artifact_type.py::
    # test_missing_artifact_type_accepts_a_string_encoded_v1_version` pins
    # this). This adapter must accept exactly what that canonical reader
    # accepts, not a narrower set (Codex review) -- being stricter here
    # would refuse a real, already-supported persisted document.
    try:
        raw_container_schema_version = int(raw_container_schema_version_value)
    except (TypeError, ValueError, OverflowError):
        # `OverflowError`, not just `TypeError`/`ValueError`: a JSON number
        # like `1e999` decodes to the float `inf`, and `int(inf)` raises
        # `OverflowError`, not `ValueError` -- `bundle_facts_serialization
        # .looks_like_bundle_facts_document` already handles this exact
        # representation; this adapter must too, or malformed input is
        # misreported as an unhandled crash rather than the documented
        # `ValueError` (Codex review, fresh evidence).
        raise ValueError(
            "bundle_facts_document schema_version must be coercible to an "
            f"int, got {raw_container_schema_version_value!r}"
        ) from None
    if raw_container_schema_version > _BUNDLE_FACTS_SCHEMA_VERSION:
        raise IncompatibleSnapshotSchemaError(
            f"bundle_facts_document schema_version {raw_container_schema_version} "
            "is newer than this build knows how to interpret (supports up to "
            f"schema_version {_BUNDLE_FACTS_SCHEMA_VERSION}); refusing to import "
            "a document whose container semantics this build has not validated"
        )
    # `"artifact_type" in bundle_facts_document`, not `.get(...) is not
    # None`: the canonical `bundle_facts_from_dict` distinguishes the key's
    # *presence* from its value, so a document explicitly declaring
    # `"artifact_type": null` is a malformed marker to that reader, not an
    # absent one -- `.get()` alone would silently treat the two the same
    # and accept a document the canonical reader rejects (Codex review).
    if "artifact_type" in bundle_facts_document:
        artifact_type = bundle_facts_document["artifact_type"]
        if artifact_type != BUNDLE_FACTS_ARTIFACT_TYPE:
            raise ValueError(
                f"bundle facts: unexpected artifact_type {artifact_type!r} "
                f"(expected {BUNDLE_FACTS_ARTIFACT_TYPE!r})"
            )
        if raw_container_schema_version < 2:
            raise ValueError(
                "bundle_facts_document: schema_version "
                f"{raw_container_schema_version} predates artifact_type (added "
                "in schema_version 2) -- such a document may not declare it"
            )
    elif (
        "schema_version" in bundle_facts_document and raw_container_schema_version != 1
    ):
        raise ValueError(
            "bundle_facts_document: schema_version "
            f"{raw_container_schema_version} requires an 'artifact_type' key "
            "(added in schema_version 2); none was given"
        )
    if "per_library_snapshots" not in bundle_facts_document:
        raise ValueError(
            "bundle_facts_document is missing the required "
            "'per_library_snapshots' mapping"
        )
    raw_snapshots = bundle_facts_document["per_library_snapshots"]
    _mapping(raw_snapshots, "bundle_facts_document['per_library_snapshots']")
    # An explicitly *present*, empty mapping is a real, valid BundleFacts --
    # the canonical `bundle_facts_from_dict` reader accepts it (a bundle
    # with no libraries is not a contradiction, just a vacuous one), and a
    # dedicated regression test pins that acceptance. Only the key's
    # *absence* means malformed input, checked above -- Codex review, a
    # second finding: an earlier version of this function rejected an
    # empty-but-present mapping too, which this adapter's own claim to
    # "accept what the canonical reader accepts" cannot allow.

    # `resolve_ref_ids`, not the raw library name: unlike
    # `import_legacy_snapshot`'s own caller-supplied `artifact_id`, nothing
    # upstream of this adapter has ensured a `per_library_snapshots` key is
    # itself ref-id-safe or collision-free (Codex review). The real name is
    # preserved on the artifact's own `native_identity` for
    # `export_bundle_facts` to recover.
    artifact_ids_by_library = resolve_ref_ids(list(raw_snapshots), opaque_prefix="lib")

    artifact_refs = []
    section_schema_versions: dict[str, int] = {}
    source_schema_version: int | None = None
    for library_name, snapshot_document in raw_snapshots.items():
        member_manifest = import_legacy_snapshot(
            snapshot_document,
            store=store,
            artifact_id=artifact_ids_by_library[library_name],
            variant_id=variant_id,
            max_known_schema_version=max_known_schema_version,
        )
        (artifact,) = member_manifest.artifact_refs
        artifact = ArtifactRef(
            artifact_id=artifact.artifact_id,
            variant_id=artifact.variant_id,
            kind=artifact.kind,
            native_identity={_LIBRARY_NAME_KEY: library_name},
            sections=artifact.sections,
        )
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
    if source_schema_version is None:
        # A vacuous bundle (an empty, but present, `per_library_snapshots`)
        # has no per-library snapshot to derive a schema version from --
        # `StorageVersions`' own `0` "unstated" sentinel, not a guess.
        source_schema_version = 0

    if "variant_fingerprint" in bundle_facts_document:
        variant_fingerprint = bundle_facts_document["variant_fingerprint"]
        if not isinstance(variant_fingerprint, str):
            raise ValueError(
                "bundle_facts_document['variant_fingerprint'] must be a "
                f"string, not {type(variant_fingerprint).__name__} "
                f"({variant_fingerprint!r})"
            )
    else:
        variant_fingerprint = _DEFAULT_VARIANT_FINGERPRINT
    composition_payload = {
        "variant_fingerprint": variant_fingerprint,
        "manifest": _validated_manifest(bundle_facts_document.get("manifest")),
        "filesystem_aliases": _validated_filesystem_aliases(
            bundle_facts_document.get("filesystem_aliases", _ABSENT)
        ),
        "library_filenames": _validated_library_filenames(
            bundle_facts_document.get("library_filenames", _ABSENT)
        ),
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
    manifest: PackageManifest,
    *,
    store: ObjectStore,
    variant_id: str = "default",
    on_document: Callable[[Any, str], None] | None = None,
) -> dict[str, Any]:
    """The exact inverse of `import_bundle_facts`: every artifact under
    *variant_id* is read back via `export_legacy_snapshot`, the variant's own
    `BUNDLE_COMPOSITION_SECTION_KIND` section is read back via
    `bundle_composition_from_dto`, and both are reassembled into one
    `bundle_facts_serialization.bundle_facts_from_dict()`-shaped document.

    *on_document*, when given, is called once per reconstructed piece --
    the bundle-composition section first, then each artifact -- with a
    short description of what it is. This module has no size/count budget
    of its own (see its own module docstring); a caller wanting to bound
    aggregate decoded size *as* each piece is reconstructed, rather than
    only after every member of a possibly-untrusted `manifest` has already
    been retained in memory, raises from this callback to abort before the
    next piece is fetched. For the bundle-composition section, the callback
    receives that section's own decoded payload. For an artifact, it
    receives `{"library_name": <the recovered library name>,
    "snapshot": <the exported snapshot document>}` rather than the bare
    snapshot document -- the recovered library name becomes a
    `per_library_snapshots` key in the document this function returns, so a
    caller charging only the bare snapshot would never account for an
    arbitrarily large library name (Codex review).

    Raises `ValueError` if *variant_id* names no variant in *manifest*, if
    that variant carries no `BUNDLE_COMPOSITION_SECTION_KIND` section (never
    produced by anything but `import_bundle_facts` itself, so this means the
    manifest was not built by it, or was hand-edited), if that section (or
    any artifact's own section) is not advertised in this package's
    `section_schema_versions`, if the stored composition's
    `variant_fingerprint` is not a string, or if a stored template
    instantiation names the same parameter more than once -- or whatever
    *on_document* itself raises.
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
    # A package this module writes advertises the *union* of every library's
    # own section kinds, plus `BUNDLE_COMPOSITION_SECTION_KIND`, in
    # `manifest.versions.section_schema_versions` (a header-only library
    # legitimately has no "binary" section, one dumped at a shallower depth
    # legitimately has no "debug" section) -- so one artifact carrying
    # *fewer* kinds than the union is expected, not corruption. A kind the
    # union never advertises at all is unambiguous, though: no legitimate
    # write could have produced it, and `check_reader_compatibility` alone
    # does not catch a hand-edited package that keeps a recognized section
    # (on an artifact, or -- the identical risk one level up -- the
    # variant's own `bundle_composition` section) while dropping it from
    # the package-wide version map (Codex review, both directions). Checked
    # before this section is ever decoded, not after: unversioned contract
    # evidence (the variant fingerprint, the instantiation manifest) must
    # never reach a comparison even transiently.
    advertised_sections = set(manifest.versions.section_schema_versions)
    if BUNDLE_COMPOSITION_SECTION_KIND not in advertised_sections:
        raise ValueError(
            f"variant {variant_id!r}'s {BUNDLE_COMPOSITION_SECTION_KIND!r} "
            "section is not in this package's section_schema_versions -- "
            "the package is corrupted or was hand-edited"
        )
    composition_dto = SectionDTO.from_dict(store.get(composition_ref.digest))
    composition = bundle_composition_from_dto(composition_dto)
    if on_document is not None:
        on_document(
            composition,
            f"variant {variant_id!r}'s {BUNDLE_COMPOSITION_SECTION_KIND!r} section",
        )

    source_schema_version = manifest.versions.source_schema_version
    per_library_snapshots: dict[str, Any] = {}
    for artifact_id in variant.artifact_ids:
        artifact = next(
            a for a in manifest.artifact_refs if a.artifact_id == artifact_id
        )
        extra_sections = set(artifact.sections) - advertised_sections
        if extra_sections:
            raise ValueError(
                f"artifact {artifact_id!r} has section(s) {sorted(extra_sections)} "
                "that this package's section_schema_versions does not "
                "advertise -- the package is corrupted or was hand-edited"
            )
        # `artifact_id` itself may be an opaque `resolve_ref_ids`-generated
        # id, not the real library name -- `native_identity` is where
        # `import_bundle_facts` stashed the real one. Falling back to
        # `artifact_id` here (as an earlier version did) would export an
        # opaque hash as a library name whenever the id happens to be one
        # -- `import_bundle_facts` itself always sets this key, so its
        # absence means this manifest wasn't built by it (or was
        # hand-edited), and there is no real library name to recover
        # (CodeRabbit review). Checked by *presence*, not truthiness: the
        # empty string is itself a valid `per_library_snapshots` key the
        # canonical `bundle_facts_from_dict` reader neither rejects nor
        # special-cases (no key-shape validation exists for it at all),
        # so `import_bundle_facts` itself can produce a real, legitimate
        # `native_identity[_LIBRARY_NAME_KEY] == ""` -- rejecting it by
        # truthiness would make an import-produced package unexportable
        # (Codex review, fresh evidence).
        if _LIBRARY_NAME_KEY not in artifact.native_identity:
            raise ValueError(
                f"artifact {artifact_id!r} has no {_LIBRARY_NAME_KEY!r} "
                "native_identity -- this manifest was not produced by "
                "import_bundle_facts, or was hand-edited"
            )
        library_name = artifact.native_identity[_LIBRARY_NAME_KEY]
        if library_name in per_library_snapshots:
            # `import_bundle_facts` itself can never produce this --
            # `resolve_ref_ids` is keyed by the original, already-unique
            # `per_library_snapshots` dict keys -- but `PackageManifest`
            # only enforces unique `artifact_id`s, not unique recovered
            # `native_identity[_LIBRARY_NAME_KEY]` values, so a manifest
            # built or loaded some other way can still carry two artifacts
            # that recover the same library name. A plain dict assignment
            # would silently drop one artifact's snapshot rather than
            # surfacing the ambiguity (Codex review, fresh evidence).
            raise ValueError(
                f"variant {variant_id!r} has more than one artifact "
                f"recovering library name {library_name!r} -- this "
                "manifest was not produced by import_bundle_facts, or was "
                "hand-edited"
            )
        snapshot_document = export_legacy_snapshot(
            artifact, store=store, source_schema_version=source_schema_version
        )
        if on_document is not None:
            on_document(
                {"library_name": library_name, "snapshot": snapshot_document},
                f"artifact {artifact_id!r} (library {library_name!r})",
            )
        per_library_snapshots[library_name] = snapshot_document

    raw_manifest = composition.get("manifest")
    if raw_manifest is None:
        exported_manifest = None
    else:
        exported_manifest = {
            **raw_manifest,
            "provides": [
                _manifest_entry_for_export(entry) for entry in raw_manifest["provides"]
            ],
        }

    # `import_bundle_facts` itself rejects a non-string `variant_fingerprint`
    # outright (mirrored above), but that check runs only for a document
    # that actually went through `import_bundle_facts` -- a `VariantRef
    # .sections[BUNDLE_COMPOSITION_SECTION_KIND]` object built or stored some
    # other way is untrusted content this reader must not silently trust
    # either. `bundle_facts_serialization.bundle_facts_from_dict()`
    # unconditionally coerces this field via `str(...)`, so a stored
    # non-string value (e.g. the JSON number `1`) would otherwise silently
    # become the string `"1"` here -- possibly colliding with a genuinely
    # distinct, already-string `"1"` fingerprint from another variant and
    # letting `pair_variants()` compare the wrong variants (Codex review).
    raw_variant_fingerprint = composition.get(
        "variant_fingerprint", _DEFAULT_VARIANT_FINGERPRINT
    )
    if not isinstance(raw_variant_fingerprint, str):
        raise ValueError(
            f"variant {variant_id!r}'s stored variant_fingerprint must be a "
            f"string, not {type(raw_variant_fingerprint).__name__} "
            f"({raw_variant_fingerprint!r}) -- the package is corrupted or "
            "was hand-edited"
        )

    return {
        "artifact_type": BUNDLE_FACTS_ARTIFACT_TYPE,
        "schema_version": _BUNDLE_FACTS_SCHEMA_VERSION,
        "variant_fingerprint": raw_variant_fingerprint,
        "per_library_snapshots": per_library_snapshots,
        "filesystem_aliases": composition.get("filesystem_aliases", {}),
        "library_filenames": composition.get("library_filenames", {}),
        "manifest": exported_manifest,
    }
