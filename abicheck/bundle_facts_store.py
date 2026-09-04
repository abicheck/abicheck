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

"""`BundleFacts` <-> multi-artifact `ProjectSnapshot` package, for a live
in-process `BundleFacts` object (ADR-062 A1.4/A1.5).

**History.** Two tracks landed the identical plan item without visibility
into each other (the ledger's own flagged gap this module closes): this
module ("Track B") originally implemented its own multi-artifact writer
directly against `storage/package.py`'s object model, storing the
instantiation manifest as a `PackageManifest.project_sections` entry and
folding `library_filenames`/`filesystem_aliases`/the real library name onto
each `ArtifactRef.native_identity`. `storage/import_bundle_facts.py`
("Track C") landed the same "a `BundleFacts` document folds onto one
variant" design independently, against the *document* shape
(`bundle_facts_serialization.bundle_facts_to_dict()`) rather than the live
object -- storing the same four bundle-composition facts
(`variant_fingerprint`/`manifest`/`filesystem_aliases`/`library_filenames`)
as one `VariantRef.sections[BUNDLE_COMPOSITION_SECTION_KIND]` DTO instead of
scattering them across `project_sections`/`native_identity`.

Rather than keep both physical layouts alive, this module is now a thin
adapter over `storage.import_bundle_facts.import_bundle_facts`/
`export_bundle_facts` -- the same reasoning `write_bundle_facts_archive`'s
G40 container gives for reusing `bundle_facts_to_dict`/`_from_dict` rather
than inventing its own field-by-field encoding. `PackageManifest
.project_sections` and `ArtifactRef.native_identity`-for-filename/aliases
are retired *for this path*: `import_bundle_facts` never populates either,
so a package this module writes carries no `project_sections` entry and no
per-artifact filename/alias facts (`native_identity` still carries the real
library name, `import_bundle_facts`'s own `_LIBRARY_NAME_KEY`, since an
`artifact_id` may be an opaque `resolve_ref_ids`-generated id rather than
the literal name). `project_sections` and `native_identity` themselves stay
as general `PackageManifest`/`ArtifactRef` mechanisms -- `import_baseline_set
.py` still uses `native_identity` for its own per-artifact facts, and
`project_sections` remains available for a future genuinely cross-library
fact this path doesn't need.

**Why this lives at the flat root, not in `storage/`.** Same reason
`project_snapshot_store.py`/`project_snapshot_legacy.py` do (see their own
module docstrings): `storage/` may depend only on `model`
(`storage/AGENTS.md`, "Permitted imports"), so it cannot itself import
`bundle_facts.py`/`bundle_facts_serialization.py` (the live `BundleFacts`
type and its `to_dict`/`from_dict` this module bridges to
`storage.import_bundle_facts`'s document-shaped contract). Classified
`workflows` in `architecture/modules.yaml`, the same layer `bundle_facts.py`
itself is classified under.

**What this module still owns, that `import_bundle_facts`/`export_bundle_facts`
do not.** Those two functions have no size/count budget of their own --
their callers (this module, and `storage/import_baseline_set.py`'s own
sibling adapter) are expected to bound their own input/output. This module
keeps the exact budgets its own previous implementation enforced
(`DEFAULT_MAX_LIBRARY_COUNT`, `DEFAULT_MAX_BUNDLE_DECODED_BYTES`,
`DEFAULT_MAX_JSON_CONTAINER_NODES` for `filesystem_aliases`): checked
up front before handing a document to `import_bundle_facts` (so this writer
never produces a package its own reader would then refuse), and -- on the
way back in, where a `PackageManifest` may be untrusted, hand-assembled
input -- incrementally, via `export_bundle_facts`'s own `on_document` hook,
which charges the aggregate decoded-byte and alias-element budgets as each
piece (the bundle-composition section, then each artifact) is reconstructed,
rather than only after every member has already been retained in memory.
"""

from __future__ import annotations

from typing import Any

from .bundle_facts import (
    DEFAULT_MAX_BUNDLE_DECODED_BYTES,
    DEFAULT_MAX_LIBRARY_COUNT,
    BundleFacts,
)
from .bundle_facts_serialization import bundle_facts_from_dict, bundle_facts_to_dict
from .serialization import SCHEMA_VERSION
from .storage.bundle_archive_json_guard import bounded_encode_utf8
from .storage.import_bundle_facts import export_bundle_facts, import_bundle_facts
from .storage.json_budget import DEFAULT_MAX_JSON_CONTAINER_NODES
from .storage.package import ObjectStore, PackageManifest
from .storage.versioning import check_reader_compatibility

__all__ = [
    "read_bundle_facts_package",
    "write_bundle_facts_package",
]


def _charge_running_bytes(
    document: object, charged_so_far: int, *, context: str
) -> int:
    """*charged_so_far* plus *document*'s own encoded byte size, raising
    before materializing an oversized *aggregate* rather than after.

    `bounded_encode_utf8` -- the same primitive `bundle_facts.py`'s own G40
    archive uses -- streams the encode and aborts as soon as the running
    byte count would cross the *remaining* allowance, so a caller charging
    several documents in sequence (as `read_bundle_facts_package` does, one
    per reconstructed artifact/section) never fully materializes the one
    document that pushes the running total over the limit.
    """
    remaining = max(DEFAULT_MAX_BUNDLE_DECODED_BYTES - charged_so_far, 0)
    encoded = bounded_encode_utf8(document, remaining)
    if encoded is None:
        raise ValueError(
            f"{context} exceed DEFAULT_MAX_BUNDLE_DECODED_BYTES "
            f"({DEFAULT_MAX_BUNDLE_DECODED_BYTES} bytes)"
        )
    return charged_so_far + len(encoded)


def _charge_document_bytes(document: object, *, context: str) -> None:
    """`_charge_running_bytes` for a single, standalone document."""
    _charge_running_bytes(document, 0, context=context)


def _alias_element_count(aliases_by_library: object) -> int:
    """The total node count *aliases_by_library* (a `{library:
    [alias, ...]}`-shaped mapping, live or already-decoded) would cost to
    decode back (one node per alias element, plus one for each array
    itself) -- the same node-count amplification `DEFAULT_MAX_JSON_
    CONTAINER_NODES` guards against elsewhere (many short strings can stay
    well under a byte budget while still costing one Python-object
    allocation per element).
    """
    if not isinstance(aliases_by_library, dict):
        return 0
    return sum(len(aliases) + 1 for aliases in aliases_by_library.values())


def write_bundle_facts_package(
    facts: BundleFacts, *, store: ObjectStore, variant_id: str = "default"
) -> PackageManifest:
    """Write *facts* into *store*, returning the resulting multi-artifact
    `PackageManifest` -- one `ArtifactRef` per `facts.per_library_snapshots`
    entry, all under one `VariantRef` named *variant_id*, with
    `variant_fingerprint`/`manifest`/`filesystem_aliases`/`library_filenames`
    folded onto that variant's own `BUNDLE_COMPOSITION_SECTION_KIND` section
    (`storage.import_bundle_facts.import_bundle_facts`).

    Raises `ValueError` if *facts* itself already exceeds a limit
    `read_bundle_facts_package` enforces on the way back in
    (`DEFAULT_MAX_LIBRARY_COUNT`/`DEFAULT_MAX_BUNDLE_DECODED_BYTES`/
    `DEFAULT_MAX_JSON_CONTAINER_NODES`): this public writer must not hand
    back a `PackageManifest` its own promised inverse refuses to reopen.
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
        # empty `variant_fingerprint` outright; a directly-constructed
        # `BundleFacts(variant_fingerprint="")` must not silently pair with
        # a legitimate "default" variant it was never actually part of.
        raise ValueError(
            "facts.variant_fingerprint must not be empty -- "
            "bundle_multibuild._index_by_fingerprint already rejects an "
            "empty, non-identifying fingerprint the same way"
        )
    alias_nodes = _alias_element_count(facts.filesystem_aliases)
    if alias_nodes > DEFAULT_MAX_JSON_CONTAINER_NODES:
        raise ValueError(
            f"facts.filesystem_aliases' total element count exceeds "
            f"DEFAULT_MAX_JSON_CONTAINER_NODES "
            f"({DEFAULT_MAX_JSON_CONTAINER_NODES}) -- refusing to write a "
            "package read_bundle_facts_package would itself then refuse to "
            "reconstruct"
        )
    document = bundle_facts_to_dict(facts)
    _charge_document_bytes(document, context="facts' encoded document")
    return import_bundle_facts(
        document,
        store=store,
        max_known_schema_version=SCHEMA_VERSION,
        variant_id=variant_id,
    )


def read_bundle_facts_package(
    manifest: PackageManifest, *, store: ObjectStore, variant_id: str = "default"
) -> BundleFacts:
    """The exact inverse of `write_bundle_facts_package`: reconstruct a
    `BundleFacts` equivalent, at the semantic-digest level, to the one that
    was written -- reading *variant_id*'s artifacts and bundle-composition
    section back from *store* via `storage.import_bundle_facts
    .export_bundle_facts`.

    Raises `ValueError` if *variant_id* is not one of *manifest*'s
    `variant_refs`, if *manifest* itself is not readable by this build (D2's
    two fail-closed version axes), or if reconstructing it would cross one
    of this module's own read-side budgets -- checked here rather than only
    trusting a caller to have routed *manifest* through
    `project_snapshot_store.read_manifest_summary` first, since
    `PackageManifest`/`StorageVersions` are public and constructible
    directly.
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
    # full `AbiSnapshot` per artifact, all held at once. Bounding the
    # artifact count up front keeps a small, hand-edited package from
    # amplifying into unbounded parsing/memory purely by artifact *count*
    # before `export_bundle_facts` ever runs.
    if len(variant.artifact_ids) > DEFAULT_MAX_LIBRARY_COUNT:
        raise ValueError(
            f"variant {variant_id!r} names {len(variant.artifact_ids)} "
            f"artifact_ids, exceeding DEFAULT_MAX_LIBRARY_COUNT "
            f"({DEFAULT_MAX_LIBRARY_COUNT}) -- refusing to eagerly reconstruct "
            "every member into memory at once"
        )
    # A *few* individually large artifacts (or a large bundle-composition
    # section) can amplify past the count bound above just as well as many
    # small ones. `export_bundle_facts`'s own `on_document` hook charges each
    # reconstructed piece (the bundle-composition section, then each
    # artifact) against the running aggregate *as it is produced*, so the one
    # document that pushes the total over the limit is rejected on the spot
    # -- not after every member of a possibly-untrusted `manifest` has
    # already been retained in memory. The bundle-composition section is
    # also where `filesystem_aliases` lives, so its own node-count budget
    # (the same amplification concern `write_bundle_facts_package` guards on
    # the way in) is checked at that same point.
    decoded_bytes_so_far = 0
    alias_nodes_so_far = 0

    def _charge(piece: Any, context: str) -> None:
        nonlocal decoded_bytes_so_far, alias_nodes_so_far
        decoded_bytes_so_far = _charge_running_bytes(
            piece, decoded_bytes_so_far, context=context
        )
        if isinstance(piece, dict) and "filesystem_aliases" in piece:
            alias_nodes_so_far += _alias_element_count(piece["filesystem_aliases"])
            if alias_nodes_so_far > DEFAULT_MAX_JSON_CONTAINER_NODES:
                raise ValueError(
                    f"{context}'s filesystem_aliases total element count "
                    f"exceeds DEFAULT_MAX_JSON_CONTAINER_NODES "
                    f"({DEFAULT_MAX_JSON_CONTAINER_NODES})"
                )

    document = export_bundle_facts(
        manifest, store=store, variant_id=variant_id, on_document=_charge
    )
    return bundle_facts_from_dict(document)
