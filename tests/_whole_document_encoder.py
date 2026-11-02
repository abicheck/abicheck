# SPDX-License-Identifier: Apache-2.0
"""The whole-document snapshot encoder the streamed write replaced (plan
A2.2) -- kept as the differential oracle for
``storage.snapshot_section_stream``. Composed only of functions production
still uses for other callers: the sorted whole-document encode and
``to_sectioned_document``'s split, packaging and D14/D15 merge."""

from __future__ import annotations

from typing import Any

from abicheck.model import AbiSnapshot
from abicheck.storage import snapshot_encode as se
from abicheck.storage.sectioned_document import to_sectioned_document
from abicheck.storage.snapshot_schema_versions import SCHEMA_VERSION
from abicheck.storage.wire_extensions import (
    extensions_of,
    refuse_unwritable_extensions,
)


def whole_sectioned_document(snap: AbiSnapshot) -> dict[str, Any]:
    """The sectioned document, built whole (the pre-A2.2 write path)."""
    refuse_unwritable_extensions(snap)
    return to_sectioned_document(
        se._snapshot_to_dict(snap, se._encode_value_sorted),
        max_known_schema_version=SCHEMA_VERSION,
        document_encoded_here=True,
        extensions=extensions_of(snap),
    )
