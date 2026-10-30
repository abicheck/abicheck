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

"""ADR-062 D17's ref layout (amends D6): immutable, digest-named ref
documents, and the `manifest.json` entries that name them.

Package format v2 writes each `VariantRef`/`ArtifactRef` record to
`refs/<kind>/<id>/<ref-digest-hex>.json` and never overwrites it.
`manifest.json` records `{id, ref_digest}` per ref, so a republish writes
new ref files beside the old ones and the single atomic replace of
`manifest.json` stays the only commit point. `ref_digest` is
`canonical.semantic_digest` over the complete ref document -- its id, its
`ref_schema_version` and every object digest it names; a reader recomputes
it and fails closed on a mismatch with the manifest entry or the path.

A v1 package (refs at the mutable `refs/<kind>/<id>.json`, bare id lists)
is still read: that is the D13 adapter for the id-path layout. The
filesystem writer itself is `abicheck/project_snapshot_store.py`'s
`write_project_manifest`; this leaf holds the parts that need no envelope.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .canonical import semantic_digest
from .package import object_relpath
from .ref_ids import REF_SUFFIX, safe_ref_id

__all__ = [
    "REF_DIRS",
    "REF_ENTRY_FIELDS",
    "REF_SCHEMA_VERSION",
    "REF_SCHEMA_VERSION_KEY",
    "publish_lock_name",
    "ref_digest_entries",
    "ref_digest_of",
    "ref_document",
    "ref_record",
    "versioned_ref_relpath",
]

#: Layout version of one ref document, stored inside it (and so hashed).
REF_SCHEMA_VERSION = 1
REF_SCHEMA_VERSION_KEY = "ref_schema_version"

#: `manifest.json` field listing `{id, ref_digest}` per ref, by ref kind.
REF_ENTRY_FIELDS = {"variant": "variant_refs", "artifact": "artifact_refs"}

#: Package-relative directory holding each ref kind's documents.
REF_DIRS = {"variant": "refs/variants", "artifact": "refs/artifacts"}


def ref_document(record: Mapping[str, Any]) -> dict[str, Any]:
    """The stored ref document for a `VariantRef`/`ArtifactRef.to_dict()`
    *record*: the record plus its own layout version."""
    if REF_SCHEMA_VERSION_KEY in record:
        raise ValueError(f"a ref record may not carry {REF_SCHEMA_VERSION_KEY!r}")
    return {**record, REF_SCHEMA_VERSION_KEY: REF_SCHEMA_VERSION}


def ref_digest_of(document: Mapping[str, Any]) -> str:
    """`ref_digest`: `semantic_digest` over the whole ref document."""
    return semantic_digest(document)


def ref_record(document: Any, where: str) -> dict[str, Any]:
    """The record inside a stored ref *document*, refusing one whose layout
    version this build does not read -- never guessing at a newer layout."""
    if not isinstance(document, dict):
        raise ValueError(f"{where} is not a JSON object")
    version = document.get(REF_SCHEMA_VERSION_KEY)
    if version != REF_SCHEMA_VERSION or isinstance(version, bool):
        raise ValueError(
            f"{where} states {REF_SCHEMA_VERSION_KEY} {version!r}; this build "
            f"reads only {REF_SCHEMA_VERSION}"
        )
    return {k: v for k, v in document.items() if k != REF_SCHEMA_VERSION_KEY}


def versioned_ref_relpath(kind: str, ref_id: str, ref_digest: str) -> str:
    """`refs/<kind>/<id>/<hex>.json` -- the immutable path a ref document is
    stored at, derived from its own digest (validated like an object's)."""
    object_relpath(ref_digest)  # refuses a malformed or unknown-algorithm digest
    hexdigest = ref_digest.split(":", 1)[1]
    return (
        f"{REF_DIRS[kind]}/{safe_ref_id(ref_id, f'{kind}_id')}/{hexdigest}{REF_SUFFIX}"
    )


def ref_digest_entries(
    data: Mapping[str, Any], kind: str, record: str
) -> dict[str, str]:
    """`{id: ref_digest}`, in manifest order, from a v2 `manifest.json`'s
    `{id, ref_digest}` list for *kind* -- the package's published membership
    of that kind, each id named once with exactly one ref."""
    field = REF_ENTRY_FIELDS[kind]
    if field not in data:
        raise ValueError(f"{record} is missing required field {field!r}")
    raw = data[field]
    if not isinstance(raw, list):
        raise ValueError(
            f"{record} {field} must be a JSON array, not {type(raw).__name__}"
        )
    entries: dict[str, str] = {}
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict) or set(entry) != {"id", "ref_digest"}:
            raise ValueError(
                f"{record} {field}[{index}] must be an object with exactly "
                f"'id' and 'ref_digest', got {entry!r}"
            )
        ref_id, digest = entry["id"], entry["ref_digest"]
        if not isinstance(ref_id, str) or ref_id in entries:
            raise ValueError(
                f"{record} {field}[{index}] repeats or misspells id {ref_id!r}"
            )
        if not isinstance(digest, str):
            raise ValueError(f"{record} {field}[{index}] ref_digest must be a string")
        try:
            object_relpath(digest)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{record} {field}[{index}] ref_digest: {exc}") from None
        entries[ref_id] = digest
    return entries


def publish_lock_name(resolved_root: str) -> str:
    """The `cross_process_lock.single_flight` name serializing writers to
    one package root (D17)."""
    return f"project-package-publish:{resolved_root}"
