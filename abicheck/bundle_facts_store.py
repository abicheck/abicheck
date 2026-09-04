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

"""The first real multi-artifact `ProjectSnapshot` package writer — ADR-062
A1.4/A1.5 (`docs/contribute/plans/storage-format-v2.md`).

`storage/package.py`'s manifest/refs/`ObjectStore` object model and
`storage/import_v1.py`'s per-artifact section split have existed since
ADR-062 Phase 1 landed, but nothing before this module actually assembled a
*multi*-artifact package from them: every existing caller of
`import_legacy_snapshot` builds exactly one `ArtifactRef` under exactly one
`VariantRef`. A real release is N libraries under one shared variant, plus
cross-library evidence (today: an `InstantiationManifest`) that belongs to
the *project*, not to any one library — that is what `BundleFacts`
(`abicheck/bundle_facts.py`) already models for the live-`.so` comparison
path, and what this module gives a stored, content-addressed counterpart to.

`write_bundle_facts_package` takes a real `BundleFacts` (already carrying N
already-dumped `AbiSnapshot`s, exactly as `capture_bundle_facts` produces)
and an `ObjectStore`, and returns one `PackageManifest` naming N
`ArtifactRef`s under one `VariantRef` — calling `import_legacy_snapshot`
once per library against the *same* store (so byte-identical section
content across libraries collapses to one stored object automatically, the
same digest-addressing property A1.5's own design note relies on) and
merging the resulting one-artifact manifests' `StorageVersions` into a
single package-wide record. `read_bundle_facts_package` is the exact
inverse: given a `PackageManifest` and the `ObjectStore` it was written
into, it reconstructs an equivalent `BundleFacts` via `export_legacy_snapshot`
per artifact — the same round-trip guarantee A1.3's one-artifact case
already has, generalized to N.

**What is genuinely project-level vs. per-artifact, per the plan's own
split.** `BundleFacts.manifest` (an `InstantiationManifest`) is the first
real `PackageManifest.project_sections` entry: it promises entries across
the *whole* bundle, not one library, so it is stored once and referenced
from the manifest itself rather than duplicated into every artifact's own
sections. `BundleFacts.filesystem_aliases`/`.library_filenames`, despite
living on `BundleFacts` today keyed by library name, are genuinely
per-artifact facts (real on-disk symlink/filename evidence for *one*
library) — they move onto that library's own `ArtifactRef.native_identity`
instead, using string keys this module defines
(`_NATIVE_IDENTITY_FILENAME_KEY`/`_NATIVE_IDENTITY_ALIASES_KEY`) rather than
a new schema field, since `native_identity` is already exactly the
`str -> str` per-artifact fact map D6 designates for this. The real
library name itself is a third such fact
(`_NATIVE_IDENTITY_LIBRARY_NAME_KEY`): a library name is arbitrary
ELF/PE/Mach-O SONAME/basename content (case-sensitive siblings, a `:`, a
non-UTF-8 byte), not a safe `ArtifactRef.artifact_id` -- see
`_artifact_id_for_library`'s own docstring for why the two cannot be the
same string.

**Why this lives at the flat root, not in `storage/`.** Same reason
`project_snapshot_store.py`/`project_snapshot_legacy.py` do (see their own
module docstrings): `storage/` may depend only on `model`
(`storage/AGENTS.md`, "Permitted imports"), so it cannot itself import
`serialization.py` (to turn a live `AbiSnapshot` into the legacy document
`import_legacy_snapshot`/`export_legacy_snapshot` operate on) or
`bundle_facts.py`/`bundle_manifest.py` (the live `BundleFacts`/
`InstantiationManifest` types this module bridges). Classified `workflows`
in `architecture/modules.yaml`, the same layer `bundle_facts.py` itself is
classified under — this module coordinates dump/compare-shaped state across
`model`/`storage`/`extract`/`compare`/`policy`, which is exactly what that
layer is for.

**A schema-version consistency requirement this module adds, not one
`StorageVersions` already had.** `StorageVersions.source_schema_version` is
one value for the *whole package* (D2), but `import_legacy_snapshot` is
called once per library, each returning its own opinion of that value. A
real bundle capture emits every library under one abicheck build, so they
always agree in practice; `write_bundle_facts_package` still checks this
explicitly and raises rather than silently picking one, since a package
that silently discarded a genuine per-library disagreement would let a
`export_legacy_snapshot` reader apply the wrong producer-epoch semantics to
the libraries whose real version it discarded.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .bundle_facts import (
    DEFAULT_MAX_BUNDLE_DECODED_BYTES,
    DEFAULT_MAX_LIBRARY_COUNT,
    DEFAULT_VARIANT_FINGERPRINT,
    BundleFacts,
)
from .bundle_manifest import InstantiationManifest, manifest_from_dict, manifest_to_dict
from .serialization import SCHEMA_VERSION, snapshot_from_dict, snapshot_to_dict
from .storage.bundle_archive_json_guard import bounded_encode_utf8
from .storage.import_v1 import export_legacy_snapshot, import_legacy_snapshot
from .storage.json_budget import DEFAULT_MAX_JSON_CONTAINER_NODES
from .storage.native_identity_aliases import (
    NATIVE_IDENTITY_ALIASES_KEY as _NATIVE_IDENTITY_ALIASES_KEY,
    NATIVE_IDENTITY_FILENAME_KEY as _NATIVE_IDENTITY_FILENAME_KEY,
    decode_native_identity_aliases as _decode_aliases,
    encode_native_identity_aliases as _encode_aliases,
)
from .storage.package import (
    ArtifactRef,
    ObjectRef,
    ObjectStore,
    PackageManifest,
    VariantRef,
)
from .storage.versioning import StorageVersions, check_reader_compatibility

if TYPE_CHECKING:
    from .model import AbiSnapshot

__all__ = [
    "INSTANTIATION_MANIFEST_SECTION_KIND",
    "read_bundle_facts_package",
    "read_embedded_instantiation_manifest",
    "write_bundle_facts_package",
]

#: `PackageManifest.project_sections` key for `BundleFacts.manifest` — the
#: one project-level fact this module knows how to store today (A1.5's own
#: "the instantiation manifest is the first real `project_sections` entry").
INSTANTIATION_MANIFEST_SECTION_KIND = "instantiation_manifest"

#: `ArtifactRef.native_identity` keys `BundleFacts.library_filenames`/
#: `.filesystem_aliases` are folded onto, per library -- see the module
#: docstring's "genuinely project-level vs. per-artifact" section.
#: `_NATIVE_IDENTITY_FILENAME_KEY`/`_NATIVE_IDENTITY_ALIASES_KEY` themselves
#: (plus the encode/decode pair below) now live in `storage.
#: native_identity_aliases` -- see that module's own docstring for why
#: (`abicheck.bundle` needs to read the identical evidence back without a
#: `bundle -> bundle_facts_store` import-cycle edge) -- and are imported
#: here under their original names purely so every reference in this
#: module's own docstrings/comments above stays accurate without a
#: repo-wide rename.
#: The real `BundleFacts.per_library_snapshots` dict key, recorded here
#: because `ArtifactRef.artifact_id` cannot hold it directly -- see
#: `_artifact_id_for_library`'s own docstring.
_NATIVE_IDENTITY_LIBRARY_NAME_KEY = "library_name"

#: `VariantRef.captured` coordinate key `BundleFacts.variant_fingerprint` is
#: folded onto -- `captured`, not `declared`, since a fingerprint is what a
#: real capture run actually observed, never a value `.abicheck.yml` states
#: ahead of time (`VariantRef`'s own docstring distinguishes the two maps).
_VARIANT_FINGERPRINT_KEY = "variant_fingerprint"


def _artifact_id_for_library(library_name: str) -> str:
    """A `_safe_ref_id`-valid, deterministic `ArtifactRef.artifact_id` for
    *library_name*.

    A library name is an arbitrary ELF/PE/Mach-O SONAME or basename --
    legally containing a `:`, a `/`-free but otherwise unrestricted byte
    sequence, a case-only distinction from a sibling (`libFoo.so` vs.
    `libfoo.so`, which ELF matching keeps deliberately case-sensitive), or
    a surrogate-escaped non-UTF-8 byte -- none of which
    `ArtifactRef.artifact_id`'s own `_safe_ref_id`/filesystem-collision
    validation accepts. Passing it straight through as `artifact_id` (as
    an earlier revision did) made this writer unable to store some bundles
    `BundleFacts` itself accepts as input (Codex review). The real name is
    instead recorded on `native_identity`
    (`_NATIVE_IDENTITY_LIBRARY_NAME_KEY`) and this opaque, content-derived
    id becomes the artifact_id -- deterministic (not a counter) so writing
    the same facts twice against the same store produces the same id,
    consistent with `ObjectStore`'s own content-addressing.
    """
    digest = hashlib.sha256(
        library_name.encode("utf-8", errors="surrogateescape")
    ).hexdigest()
    return f"lib-{digest[:32]}"


def _charge_document_bytes(
    document: Any, decoded_bytes_so_far: int, *, context: str
) -> int:
    """*decoded_bytes_so_far* plus *document*'s encoded byte size, raising
    before materializing an oversized document rather than after.

    A plain `len(json.dumps(document).encode("utf-8"))` builds a complete
    JSON string and then a complete second byte buffer before any
    caller-side budget check runs, so the safety guard itself could
    exhaust memory on one single huge document (Codex review). `bounded_
    encode_utf8` -- the same primitive `bundle_facts.py`'s own G40 archive
    uses for the identical reason -- streams via `JSONEncoder.iterencode()`
    and aborts as soon as the running byte count would cross the
    *remaining* allowance, never building the oversized whole. Its default
    `ensure_ascii=True` also sidesteps a second, unrelated failure a plain
    `.encode("utf-8")` hits: a real POSIX filename surviving as a lone
    surrogate (`os.fsdecode`'s `surrogateescape`) inside the document
    raises `UnicodeEncodeError` under strict UTF-8, but round-trips as a
    plain ASCII `\\udcXX` escape under JSON's own default escaping.
    """
    remaining = max(DEFAULT_MAX_BUNDLE_DECODED_BYTES - decoded_bytes_so_far, 0)
    encoded = bounded_encode_utf8(document, remaining)
    if encoded is None:
        raise ValueError(
            f"{context} exceed DEFAULT_MAX_BUNDLE_DECODED_BYTES "
            f"({DEFAULT_MAX_BUNDLE_DECODED_BYTES} bytes)"
        )
    return decoded_bytes_so_far + len(encoded)


def _text_byte_length(text: str) -> int:
    """*text*'s real byte length -- `surrogateescape`, not strict UTF-8, so
    a real POSIX filename/alias surviving as a lone surrogate
    (`os.fsdecode`'s own decoding convention) is measured, not rejected
    with `UnicodeEncodeError` (Codex review)."""
    return len(text.encode("utf-8", errors="surrogateescape"))


def _manifest_document_for_storage(manifest: InstantiationManifest) -> dict[str, Any]:
    """`manifest_to_dict(manifest)`, with each template entry's
    `instantiations` re-expressed as an order-preserving array.

    `ObjectStore.put()` canonicalizes every mapping by sorting its keys
    (ADR-062 D5), but `bundle_manifest._expand_instantiations()` reads an
    instantiation's *insertion* order as template-argument order -- exactly
    the "one template-instantiation mapping uses insertion order to carry
    template-argument order" case D5 itself names as needing an array, never
    a map, ahead of the canonicalizing store (Codex review: an
    out-of-alphabetical-order instantiation like `{"Z": "int", "A":
    "float"}` silently became `T<float, int>` instead of the promised
    `T<int, float>`). Encoded as `[{"parameter": k, "value": v}, ...]` per
    instantiation -- a list is never key-sorted, only a mapping is.
    """
    document: dict[str, Any] = manifest_to_dict(manifest)
    for entry in document.get("provides", []):
        instantiations = entry.get("instantiations")
        if instantiations:
            entry["instantiations"] = [
                [{"parameter": key, "value": value} for key, value in inst.items()]
                for inst in instantiations
            ]
    return document


def _decode_instantiation(inst: Any) -> dict[str, str]:
    """One `_manifest_document_for_storage`-encoded instantiation
    (`[{"parameter": k, "value": v}, ...]`), decoded back to `{k: v}` --
    strictly, since this reads untrusted stored content back into contract
    evidence a comparison scores findings against.

    A plain `{pair["parameter"]: pair["value"] for pair in inst}`
    comprehension silently keeps only the *last* pair for a repeated
    parameter name, collapsing e.g. two `T` entries into one and describing
    a different promised template signature than the one actually stored
    (Codex review) -- rejected here instead, alongside a malformed pair
    (not a mapping, or a non-string `parameter`/`value`).
    """
    decoded: dict[str, str] = {}
    for pair in inst:
        if (
            not isinstance(pair, dict)
            or not isinstance(pair.get("parameter"), str)
            or not isinstance(pair.get("value"), str)
        ):
            raise ValueError(
                "a stored template instantiation pair must be "
                f'{{"parameter": <str>, "value": <str>}}, got {pair!r}'
            )
        parameter = pair["parameter"]
        if parameter in decoded:
            raise ValueError(
                f"a stored template instantiation names parameter "
                f"{parameter!r} more than once: {inst!r} -- the package is "
                "corrupted or was hand-edited"
            )
        decoded[parameter] = pair["value"]
    return decoded


def _manifest_document_from_storage(document: Any) -> Any:
    """The exact inverse of `_manifest_document_for_storage`."""
    if isinstance(document, dict):
        for entry in document.get("provides") or ():
            if not isinstance(entry, dict):
                continue
            instantiations = entry.get("instantiations")
            if instantiations:
                entry["instantiations"] = [
                    _decode_instantiation(inst) for inst in instantiations
                ]
    return document


def write_bundle_facts_package(
    facts: BundleFacts, *, store: ObjectStore, variant_id: str = "default"
) -> PackageManifest:
    """Write *facts* into *store*, returning the resulting multi-artifact
    `PackageManifest` — one `ArtifactRef` per `facts.per_library_snapshots`
    entry, all under one `VariantRef` named *variant_id*.

    Each library is imported via `import_legacy_snapshot` against the same
    *store*, so content shared byte-for-byte across libraries (a common
    header, an identical DWARF-derived section) is written once regardless
    of how many libraries reference it — `ObjectStore` addressing is by
    digest, not by declared kind or caller, so no separate dedup pass is
    needed beyond this loop's own repeated `store.put()` calls.

    Raises `ValueError` if the per-library imports disagree on
    `source_schema_version` — see the module docstring's "schema-version
    consistency requirement" section for why that is checked explicitly
    rather than silently resolved — or if *facts* itself already exceeds
    either limit `read_bundle_facts_package` enforces on the way back in
    (`DEFAULT_MAX_LIBRARY_COUNT`/`DEFAULT_MAX_BUNDLE_DECODED_BYTES`): this
    public writer must not hand back a `PackageManifest` its own promised
    inverse refuses to reopen (Codex review).
    """
    if len(facts.per_library_snapshots) > DEFAULT_MAX_LIBRARY_COUNT:
        raise ValueError(
            f"facts.per_library_snapshots names "
            f"{len(facts.per_library_snapshots)} libraries, exceeding "
            f"DEFAULT_MAX_LIBRARY_COUNT ({DEFAULT_MAX_LIBRARY_COUNT}) -- "
            "refusing to write a package read_bundle_facts_package would "
            "itself then refuse to reconstruct"
        )
    if not facts.variant_fingerprint:
        # `bundle_multibuild._index_by_fingerprint` already rejects an
        # empty `variant_fingerprint` outright ("variant_fingerprint()
        # itself never produces one"); this writer previously *omitted* the
        # coordinate instead, so it round-tripped silently into
        # `DEFAULT_VARIANT_FINGERPRINT` on read -- a directly-constructed
        # `BundleFacts(variant_fingerprint="")` could then pair with a
        # legitimate "default" variant it was never actually part of
        # (Codex review). Rejected here for the identical reason, rather
        # than normalized.
        raise ValueError(
            "facts.variant_fingerprint must not be empty -- "
            "bundle_multibuild._index_by_fingerprint already rejects an "
            "empty, non-identifying fingerprint the same way"
        )
    artifact_refs: list[ArtifactRef] = []
    artifact_ids: list[str] = []
    artifact_id_owners: dict[str, str] = {}
    section_schema_versions: dict[str, int] = {}
    source_schema_version: int | None = None
    decoded_bytes_so_far = 0
    alias_nodes_so_far = 0
    for library_name, snapshot in facts.per_library_snapshots.items():
        artifact_id = _artifact_id_for_library(library_name)
        # sha256 collisions are astronomically unlikely, but this is a
        # cheap, defensive check rather than an assumption -- two distinct
        # library names must never silently collapse onto one artifact_id.
        existing_owner = artifact_id_owners.setdefault(artifact_id, library_name)
        if existing_owner != library_name:
            raise ValueError(
                f"library names {existing_owner!r} and {library_name!r} both "
                f"hash to artifact_id {artifact_id!r} -- refusing to collapse "
                "two distinct libraries onto one artifact"
            )
        document = snapshot_to_dict(snapshot)
        library_manifest = import_legacy_snapshot(
            document,
            store=store,
            artifact_id=artifact_id,
            max_known_schema_version=SCHEMA_VERSION,
            variant_id=variant_id,
        )
        (artifact,) = library_manifest.artifact_refs

        native_identity = dict(artifact.native_identity)
        native_identity[_NATIVE_IDENTITY_LIBRARY_NAME_KEY] = library_name
        filename = facts.library_filenames.get(library_name)
        if filename:
            native_identity[_NATIVE_IDENTITY_FILENAME_KEY] = filename
        aliases = facts.filesystem_aliases.get(library_name)
        encoded_aliases: str | None = None
        if aliases:
            encoded_aliases = _encode_aliases(aliases)
            native_identity[_NATIVE_IDENTITY_ALIASES_KEY] = encoded_aliases
            # `read_bundle_facts_package`'s own `_decode_aliases` enforces a
            # bundle-wide node total, not just a per-array cap -- this
            # writer must not hand back a package that check then refuses
            # (Codex review, fresh evidence on this same guard, a third
            # time): `+1` for the array node itself, matching what
            # `check_json_container_budget` counts.
            alias_nodes_so_far += len(aliases) + 1
            if alias_nodes_so_far > DEFAULT_MAX_JSON_CONTAINER_NODES:
                raise ValueError(
                    f"facts.filesystem_aliases' total element count exceeds "
                    f"DEFAULT_MAX_JSON_CONTAINER_NODES "
                    f"({DEFAULT_MAX_JSON_CONTAINER_NODES}) -- reached while "
                    f"encoding {library_name!r}; refusing to write a package "
                    "read_bundle_facts_package would itself then refuse to "
                    "reconstruct"
                )
        if native_identity != dict(artifact.native_identity):
            artifact = replace(artifact, native_identity=native_identity)
        artifact_ids.append(artifact_id)

        # `native_identity` lives outside `document` entirely, and
        # `read_bundle_facts_package` charges it too (Codex review, fresh
        # evidence) -- mirrored here for the identical "writer must not
        # produce what the reader refuses" reason the count/document-size
        # mirroring above already gives.
        decoded_bytes_so_far = _charge_document_bytes(
            document,
            decoded_bytes_so_far,
            context=(
                "facts.per_library_snapshots' encoded documents "
                f"(reached while encoding {library_name!r})"
            ),
        )
        decoded_bytes_so_far += _text_byte_length(library_name)
        if filename:
            decoded_bytes_so_far += _text_byte_length(filename)
        if encoded_aliases:
            decoded_bytes_so_far += _text_byte_length(encoded_aliases)
        if decoded_bytes_so_far > DEFAULT_MAX_BUNDLE_DECODED_BYTES:
            raise ValueError(
                f"facts.per_library_snapshots' encoded documents exceed "
                f"DEFAULT_MAX_BUNDLE_DECODED_BYTES "
                f"({DEFAULT_MAX_BUNDLE_DECODED_BYTES} bytes) -- reached "
                f"while encoding {library_name!r}; refusing to write a "
                "package read_bundle_facts_package would itself then "
                "refuse to reconstruct"
            )
        artifact_refs.append(artifact)

        for kind, version in library_manifest.versions.section_schema_versions.items():
            existing = section_schema_versions.get(kind)
            if existing is not None and existing != version:
                raise ValueError(
                    f"library {library_name!r} reports section_schema_versions"
                    f"[{kind!r}] = {version}, which disagrees with {existing} "
                    "already recorded from an earlier library in the same "
                    "bundle -- a package's StorageVersions carries one schema "
                    "version per section for the whole package"
                )
            section_schema_versions[kind] = version

        library_source_schema_version = library_manifest.versions.source_schema_version
        if source_schema_version is None:
            source_schema_version = library_source_schema_version
        elif source_schema_version != library_source_schema_version:
            raise ValueError(
                f"library {library_name!r} has source_schema_version "
                f"{library_source_schema_version}, which disagrees with "
                f"{source_schema_version} already recorded from an earlier "
                "library in the same bundle -- a package's StorageVersions "
                "carries one schema version for the whole package, so a "
                "bundle whose members were captured under different abicheck "
                "producer epochs cannot be represented as one package"
            )

    variant = VariantRef(
        variant_id=variant_id,
        captured={_VARIANT_FINGERPRINT_KEY: facts.variant_fingerprint},
        artifact_ids=tuple(artifact_ids),
    )

    project_sections: dict[str, ObjectRef] = {}
    if facts.manifest is not None:
        # Round-trip-validate the manifest structurally before ever storing
        # it: `manifest_from_dict` enforces constraints (e.g. a template
        # entry needs a non-empty `instantiations` list) that `ManifestEntry`
        # 's own dataclass construction does not, so a directly-constructed
        # `InstantiationManifest` violating one could otherwise write
        # successfully here and only fail `read_bundle_facts_package`'s own
        # `manifest_from_dict` call later (Codex review). Reusing
        # `manifest_to_dict`/`manifest_from_dict` -- the same pair the
        # reader's own decode ultimately goes through, on the plain (not
        # storage-transformed) shape -- rather than re-deriving their
        # constraints here.
        manifest_from_dict(manifest_to_dict(facts.manifest))
        manifest_document = _manifest_document_for_storage(facts.manifest)
        decoded_bytes_so_far = _charge_document_bytes(
            manifest_document,
            decoded_bytes_so_far,
            context=("facts' encoded documents plus its instantiation manifest"),
        )
        digest = store.put(manifest_document)
        project_sections[INSTANTIATION_MANIFEST_SECTION_KIND] = ObjectRef(
            kind=INSTANTIATION_MANIFEST_SECTION_KIND, digest=digest
        )

    versions = StorageVersions(
        section_schema_versions=section_schema_versions,
        source_schema_version=source_schema_version or 0,
    )
    return PackageManifest(
        versions=versions,
        variant_refs=(variant,),
        artifact_refs=tuple(artifact_refs),
        project_sections=project_sections,
    )


def read_bundle_facts_package(
    manifest: PackageManifest, *, store: ObjectStore, variant_id: str = "default"
) -> BundleFacts:
    """The exact inverse of `write_bundle_facts_package`: reconstruct a
    `BundleFacts` equivalent, at the semantic-digest level, to the one that
    was written -- reading *variant_id*'s artifacts back from *store* via
    `export_legacy_snapshot`.

    Raises `ValueError` if *variant_id* is not one of *manifest*'s
    `variant_refs`, if any of its member artifacts' sections cannot be
    read back from *store* (surfaces whatever `export_legacy_snapshot`
    itself raises), or if *manifest* itself is not readable by this build
    (D2's two fail-closed version axes) -- checked here rather than only
    trusting a caller to have routed *manifest* through
    `project_snapshot_store.read_manifest_summary` first, since
    `PackageManifest`/`StorageVersions` are public and constructible
    directly (Codex review; the identical reasoning `check_reader_
    compatibility`'s own docstring already states for exactly this "public
    reader boundary" gap).
    """
    compatibility = check_reader_compatibility(manifest.versions)
    if not compatibility.readable:
        raise ValueError(
            f"this PackageManifest is not readable by this build: "
            f"{compatibility.reason}"
        )
    variant = next(
        (v for v in manifest.variant_refs if v.variant_id == variant_id), None
    )
    if variant is None:
        raise ValueError(
            f"{variant_id!r} is not a variant_id in this PackageManifest "
            f"(known: {sorted(v.variant_id for v in manifest.variant_refs)})"
        )
    # A `PackageManifest` may come from another producer (a directory package
    # read off disk, not one this process itself wrote), so its declared
    # membership is untrusted input -- reconstructing eagerly materializes one
    # full `AbiSnapshot` per artifact, all held at once, with no lazy-loading
    # escape hatch yet (that is Phase 2's A2.1). Bounding the artifact count
    # against the same `DEFAULT_MAX_LIBRARY_COUNT` the G40 bundle-facts
    # archive already enforces keeps a small package from amplifying into
    # unbounded parsing/memory purely by artifact *count* (Codex review).
    if len(variant.artifact_ids) > DEFAULT_MAX_LIBRARY_COUNT:
        raise ValueError(
            f"variant {variant_id!r} names {len(variant.artifact_ids)} "
            f"artifact_ids, exceeding DEFAULT_MAX_LIBRARY_COUNT "
            f"({DEFAULT_MAX_LIBRARY_COUNT}) -- refusing to eagerly reconstruct "
            "every member into memory at once"
        )
    artifacts_by_id = {
        artifact.artifact_id: artifact for artifact in manifest.artifact_refs
    }
    source_schema_version = manifest.versions.source_schema_version

    per_library_snapshots: dict[str, AbiSnapshot] = {}
    filesystem_aliases: dict[str, tuple[str, ...]] = {}
    library_filenames: dict[str, str] = {}
    # A *few* individually large artifacts can amplify past the count bound
    # above just as well as many small ones (Codex review, fresh evidence):
    # `ObjectStore.get()` returns an already-decoded structure, not raw
    # bytes, so this charges each artifact's own reassembled document size
    # (measured via its JSON encoding, the same way `bundle_facts.py`'s own
    # `bounded_encode_utf8` callers measure decoded size elsewhere) against
    # `DEFAULT_MAX_BUNDLE_DECODED_BYTES` -- the identical aggregate ceiling
    # the G40 bundle-facts archive already enforces. This is necessarily
    # post-hoc, not pre-emptive: unlike G40's `read_blob(max_decoded_bytes=
    # ...)`, `ObjectStore.get()` has no bounded-read parameter to abort a
    # single oversized fetch mid-decode (that would be a real `ObjectStore`
    # protocol change -- ADR-062 Phase 2's A2.1 scope, not this fix), so one
    # single artifact's own sections can still be fully parsed before its own
    # decoded size is known. The budget is checked *immediately* after that
    # parse, before the artifact's snapshot is retained in
    # `per_library_snapshots` -- checking only ahead of the *next* iteration
    # (an earlier revision of this fix) let the one artifact that actually
    # crosses the budget, including the last artifact in a variant, be
    # retained and returned successfully with no subsequent iteration left to
    # catch it (Codex review, second finding on this same guard).
    # Cross-check each artifact's own `sections` against the package-wide
    # `section_schema_versions` `project_snapshot_legacy.py`'s single-
    # artifact reader already applies (Codex review) -- but only the
    # "extra" direction, safely generalizable to N artifacts: a package
    # this writer produced advertises the *union* of every library's own
    # section kinds (a header-only library legitimately has no "binary"
    # section, a library dumped at a shallower depth legitimately has no
    # "debug" section), so one artifact carrying *fewer* kinds than the
    # union is expected, not corruption -- exactly why that sibling
    # function's own check is gated to a genuinely single-artifact package.
    # An artifact carrying a kind the union never advertises at all,
    # though, is unambiguous: no legitimate write of this package could
    # have produced it.
    advertised_sections = set(manifest.versions.section_schema_versions)
    decoded_bytes_so_far = 0
    alias_nodes_so_far = 0
    for artifact_id in variant.artifact_ids:
        artifact = artifacts_by_id[artifact_id]
        extra_sections = set(artifact.sections) - advertised_sections
        if extra_sections:
            raise ValueError(
                f"artifact {artifact_id!r} has section(s) {sorted(extra_sections)} "
                "that this package's section_schema_versions does not "
                "advertise -- the package is corrupted or was hand-edited"
            )
        document = export_legacy_snapshot(
            artifact, store=store, source_schema_version=source_schema_version
        )
        decoded_bytes_so_far = _charge_document_bytes(
            document,
            decoded_bytes_so_far,
            context=(
                f"variant {variant_id!r}'s reconstructed artifacts "
                f"(reached while reconstructing {artifact_id!r})"
            ),
        )
        # `native_identity` (the library name/filename/aliases facts folded
        # on at write time -- see the module docstring and
        # `_artifact_id_for_library`) lives outside `document` entirely, so
        # it must be charged too: a budget sized exactly to the snapshot
        # content alone still let an arbitrarily large alias array through
        # for free (Codex review, fresh evidence beyond the artifact/
        # project-section budget fixes above).
        library_name = artifact.native_identity.get(_NATIVE_IDENTITY_LIBRARY_NAME_KEY)
        if not library_name:
            raise ValueError(
                f"artifact {artifact_id!r} has no "
                f"{_NATIVE_IDENTITY_LIBRARY_NAME_KEY!r} in its native_identity "
                "-- the package is corrupted or was hand-edited"
            )
        if library_name in per_library_snapshots:
            raise ValueError(
                f"library name {library_name!r} is claimed by more than one "
                "artifact in this variant -- the package is corrupted or "
                "was hand-edited"
            )
        filename = artifact.native_identity.get(_NATIVE_IDENTITY_FILENAME_KEY)
        aliases_text = artifact.native_identity.get(_NATIVE_IDENTITY_ALIASES_KEY)
        decoded_bytes_so_far += _text_byte_length(library_name)
        if filename:
            decoded_bytes_so_far += _text_byte_length(filename)
        if aliases_text:
            decoded_bytes_so_far += _text_byte_length(aliases_text)
        if decoded_bytes_so_far > DEFAULT_MAX_BUNDLE_DECODED_BYTES:
            raise ValueError(
                f"variant {variant_id!r}'s reconstructed artifacts exceed "
                f"DEFAULT_MAX_BUNDLE_DECODED_BYTES "
                f"({DEFAULT_MAX_BUNDLE_DECODED_BYTES} bytes) -- reached while "
                f"reconstructing {artifact_id!r}; refusing to retain it or "
                "reconstruct further members"
            )
        per_library_snapshots[library_name] = snapshot_from_dict(document)
        if filename:
            library_filenames[library_name] = filename
        if aliases_text:
            aliases, alias_nodes_so_far = _decode_aliases(
                aliases_text, alias_nodes_so_far
            )
            filesystem_aliases[library_name] = aliases

    variant_fingerprint = variant.captured.get(
        _VARIANT_FINGERPRINT_KEY, DEFAULT_VARIANT_FINGERPRINT
    )

    instantiation_manifest = None
    manifest_ref = manifest.project_sections.get(INSTANTIATION_MANIFEST_SECTION_KIND)
    if manifest_ref is not None:
        # `manifest_ref.kind` is a caller-controlled label, not verified by
        # `PackageManifest`/`ObjectRef` construction -- a corrupted or
        # hand-assembled package could map this key to an `ObjectRef` whose
        # own `kind` names something else entirely, the same
        # stored-kind-vs-requested-kind mismatch `export_legacy_snapshot`
        # already guards against for per-artifact sections (Codex review:
        # "differs from per-artifact section reconstruction, which validates
        # the stored section kind").
        if manifest_ref.kind != INSTANTIATION_MANIFEST_SECTION_KIND:
            raise ValueError(
                f"project_sections[{INSTANTIATION_MANIFEST_SECTION_KIND!r}] "
                f"names an ObjectRef of kind {manifest_ref.kind!r}, not "
                f"{INSTANTIATION_MANIFEST_SECTION_KIND!r} -- the package is "
                "corrupted or was hand-edited"
            )
        raw: Any = store.get(manifest_ref.digest)
        # The project-level manifest object is untrusted decoded content
        # too, and reaches here *after* the per-artifact budget loop above
        # -- it must be charged against the same aggregate ceiling, not
        # fetched and parsed for free once the artifacts alone already
        # passed (Codex review, third finding on this same guard).
        decoded_bytes_so_far = _charge_document_bytes(
            raw,
            decoded_bytes_so_far,
            context=(
                f"variant {variant_id!r}'s reconstructed artifacts plus its "
                f"{INSTANTIATION_MANIFEST_SECTION_KIND!r} project section"
            ),
        )
        instantiation_manifest = manifest_from_dict(
            _manifest_document_from_storage(raw)
        )

    return BundleFacts(
        variant_fingerprint=variant_fingerprint,
        per_library_snapshots=per_library_snapshots,
        manifest=instantiation_manifest,
        filesystem_aliases=filesystem_aliases,
        library_filenames=library_filenames,
    )


def read_embedded_instantiation_manifest(
    root: str | Path,
) -> InstantiationManifest | None:
    """Best-effort read of a `ProjectSnapshot` package (or a single-artifact
    sub-package materialized from one)'s own embedded `InstantiationManifest`
    -- `PackageManifest.project_sections[INSTANTIATION_MANIFEST_SECTION_KIND]`,
    the exact shape `write_bundle_facts_package` stores it under, decoded the
    same way `read_bundle_facts_package` decodes it for its own
    reconstruction (Codex review: ADR-062 A1.4/A1.5's own project-level
    manifest evidence otherwise went completely unconsulted during ordinary
    `compare-release` bundle analysis of a stored side that carries it --
    `materialize_release_variant_artifacts` already preserves the section on
    disk, but nothing read it back).

    Falls back to a package written by `storage.import_bundle_facts`
    instead -- whose own captured manifest, if any, lives per-variant in
    the `BUNDLE_COMPOSITION_SECTION_KIND` composition payload rather than
    this project-level section (Codex review, fresh evidence: this
    fallback previously read only the project-level section, so a stored
    side sourced from that writer still lost its own manifest-drift
    evidence here even after the composition section itself was preserved
    through materialization).

    Returns `None` for anything genuinely *absent*: no `manifest.json`, or
    a package that simply never had an instantiation manifest to begin
    with. Once a section is confirmed *declared* (a project-level ref, or a
    variant's own composition section naming a manifest), any failure to
    read or decode it propagates instead of degrading to `None` -- a
    corrupted/hand-edited section must not silently read the same as "no
    manifest was ever recorded" (CodeRabbit review, security finding: this
    previously let a corrupted section silently disable the manifest-drift
    check it was meant to enforce, rather than surfacing as a usage error).
    """
    from .project_snapshot_store import DirectoryObjectStore, read_project_manifest
    from .storage.import_bundle_facts import read_variant_composition_manifest_payload

    root_path = Path(root)
    try:
        manifest = read_project_manifest(root_path)
    except Exception:
        return None
    manifest_ref = manifest.project_sections.get(INSTANTIATION_MANIFEST_SECTION_KIND)
    project_level = (
        manifest_ref is not None
        and manifest_ref.kind == INSTANTIATION_MANIFEST_SECTION_KIND
    )
    if project_level:
        assert manifest_ref is not None
        raw = DirectoryObjectStore(root_path).get(manifest_ref.digest)
        return manifest_from_dict(_manifest_document_from_storage(raw))
    for variant in manifest.variant_refs:
        payload = read_variant_composition_manifest_payload(
            root_path, variant.variant_id
        )
        if payload is not None:
            return manifest_from_dict(payload)
    return None
