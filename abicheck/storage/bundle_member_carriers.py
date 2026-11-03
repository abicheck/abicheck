# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-062 D14/D15 carriers of a bundle member snapshot.

A G38 bundle document and a G40 bundle archive store each member as the
flat `snapshot_to_dict` document. That document has no place for a failed
or blocked section, an unknown section, header or record extensions, or a
document-level must-understand list. A member that holds any of them
(`wire_extensions.flattening_loses`) is stored instead as its single-file
sectioned document, which carries every one of them and which
`snapshot_from_dict` already reads; every other member keeps its flat bytes.

*Versioning (D2/D13).* A document or archive holding a sectioned member
declares bundle ``schema_version`` `SECTIONED_MEMBERS_SCHEMA_VERSION` (5), so a
reader that predates it refuses the whole bundle instead of decoding the
sectioned member as a flat snapshot with every field defaulted.
`require_sectioned_member_version` is the reader-side check that a sectioned
member never appears under a lower declared version (a hand-edited or
corrupted document).

A bundle *package* has `ArtifactRef.carriers` and stores them there
(`carriers_by_member`), as a directory package does.

``--bundle-facts-out`` spools each OLD member through the same
`encode_bundle_member` (`bundle_facts_spool.BlobSpool.put_snapshot`); the
spooled handle records whether its blob is sectioned, so both forms written
from the spool declare the same version as the in-memory writers.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..model.bundle_facts import SECTIONED_MEMBERS_SCHEMA_VERSION
from ..model.snapshot import AbiSnapshot
from .extension_facts import sync_export_read_fact
from .sectioned_document import is_sectioned_document
from .snapshot_encode import sectioned_document_for_write
from .wire_extensions import (
    WireExtensions,
    extensions_of,
    flattening_loses,
    refuse_unwritable_extensions,
)

__all__ = [
    "carriers_by_member",
    "encode_bundle_member",
    "require_sectioned_member_version",
]


def encode_bundle_member(
    snap: AbiSnapshot,
    *,
    flat: bool = False,
    flat_encode: Callable[[AbiSnapshot], dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], bool]:
    """*snap*'s stored member document and whether it is sectioned: the flat
    `snapshot_to_dict` document, or the sectioned one when the flat one
    would lose D14/D15 carriers. *flat* forces the flat document, for a
    caller that stores the carriers beside it (`carriers_by_member`).
    *flat_encode* is the flat encoder (default `serialization.snapshot_to_dict`),
    for a writer that is handed its own."""
    sync_export_read_fact(snap)  # ADR-079: the model field -> its fact
    refuse_unwritable_extensions(snap)
    if not flat and flattening_loses(snap):
        return sectioned_document_for_write(snap), True
    if flat_encode is not None:
        return flat_encode(snap), False
    # Resolved through `serialization`, as the bundle codec always has, so
    # that module stays the one flat-encoding seam (tests observe it there).
    from ..serialization import snapshot_to_dict

    return snapshot_to_dict(snap), False


def carriers_by_member(
    members: Mapping[str, AbiSnapshot],
) -> dict[str, WireExtensions]:
    """The D14/D15 carriers of every member that has any, by member name,
    for a bundle package's `ArtifactRef.carriers`."""
    out: dict[str, WireExtensions] = {}
    for name, snap in members.items():
        sync_export_read_fact(snap)
        refuse_unwritable_extensions(snap)
        carried = extensions_of(snap)
        if carried is not None:
            out[name] = carried
    return out


def require_sectioned_member_version(
    members: Mapping[str, Any], declared_schema_version: int, *, what: str
) -> None:
    """Refuse a sectioned member in a bundle declaring a ``schema_version``
    below `SECTIONED_MEMBERS_SCHEMA_VERSION`: no writer produces it, and a
    reader of that version would have read the member as empty."""
    if declared_schema_version >= SECTIONED_MEMBERS_SCHEMA_VERSION:
        return
    for name, member in members.items():
        if isinstance(member, Mapping) and is_sectioned_document(member):
            raise ValueError(
                f"{what}: member {name!r} is a sectioned snapshot document, "
                f"which needs schema_version {SECTIONED_MEMBERS_SCHEMA_VERSION}; "
                f"the document declares {declared_schema_version} -- it is "
                "corrupted or was hand-edited"
            )
