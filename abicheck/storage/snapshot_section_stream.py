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

"""The sectioned snapshot document, encoded one section at a time
(storage plan A2.2, ADR-062 D12).

The whole-document path encoded the snapshot into one flat dict, ran every
post-pass over it, split it into sections and only then serialized it, so a
write peaked at the whole encoded tree. Here the document is a
:class:`~.json_stream.LazyItems` whose ``sections`` member encodes each
section only when the serializer reaches it: the flat keys that section owns
(`legacy_sections.section_fields`) go through
`snapshot_encode._snapshot_to_dict` restricted to them, are packaged by the
same per-section step (`import_v1.legacy_section_dto_dict`), get their D14/D15
carriers back, are written and dropped before the next section is encoded.
The peak is therefore the largest section's tree, not the document's.

Every per-section step is the one the whole-document path ran
(`sectioned_document.to_sectioned_document` and
`wire_extensions.merge_wire_extensions`), applied to one section, so the
bytes are the same; ``tests/test_snapshot_section_stream.py`` proves it
differentially against that path over generated snapshots and the fixture
corpus. Section order, key order and the ``section_schema_versions``/
``section_availability`` inventories are the merged document's: sections
sorted by kind, inventories filled as sections are written.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import fields as dataclass_fields
from typing import Any

from ..model import AbiSnapshot
from .canonical import CAPTURE_METADATA_KEY
from .dto import SECTION_SCHEMA_VERSIONS, SEMANTIC_IR_SECTION_KIND
from .import_v1 import legacy_section_dto_dict
from .json_stream import OMIT, LazyItems
from .legacy_sections import LEGACY_SECTION_KINDS, section_fields, section_of_field
from .section_payload import section_dto_dict
from .snapshot_schema_versions import SCHEMA_VERSION
from .wire_extensions import (
    AVAILABILITY_KEY,
    DOCUMENT_MUST_UNDERSTAND_KEY,
    WireExtensions,
    extensions_of,
    refuse_unwritable_extensions,
)
from .wire_record_extensions import restore_record_extras

__all__ = ["EncodeKeys", "sectioned_document_stream"]

#: Encodes the snapshot restricted to the given flat-document keys --
#: `snapshot_encode._snapshot_to_dict`, passed in so this module does not
#: import its own caller.
EncodeKeys = Callable[[frozenset[str]], dict[str, Any]]

_SECTIONS_KEY = "sections"
_VERSIONS_KEY = "section_schema_versions"
_IR_KEYS = frozenset({"semantic_ir", "semantic_ir_conflicts"})

#: ``AbiSnapshot`` fields the encoder writes under another name, or not at
#: all: they own no section themselves.
_RENAMED_OR_DROPPED = frozenset(
    {"stale_fact_families", "from_headers_inferred", "spellings_generation"}
)


def _refuse_unsectioned_fields(snap: AbiSnapshot, skip: frozenset[str]) -> None:
    """Refuse a snapshot field no section owns, before anything is written.

    The whole-document split (`legacy_sections.split_legacy_document`)
    raised for such a key; a per-section encode asks only for the keys each
    section owns, so without this check the field would be silently left
    out. Same error, raised by the same function.
    """
    stray = [
        f.name
        for f in dataclass_fields(snap)
        if f.name not in skip
        and f.name not in _RENAMED_OR_DROPPED
        and section_of_field(f.name) is None
    ]
    if stray:
        from .legacy_sections import split_legacy_document

        split_legacy_document(dict.fromkeys(stray))


class _SectionWriter:
    """Produces each section on demand and records the inventories the
    document's later members (`section_schema_versions`,
    `section_availability`) are built from."""

    def __init__(self, encode_keys: EncodeKeys, ext: WireExtensions | None) -> None:
        self._encode_keys = encode_keys
        self._ext = ext
        self._written: list[str] = []
        self._passthrough_versions: dict[str, Any] = (
            dict(ext.passthrough_versions) if ext is not None else {}
        )

    def kinds(self) -> list[str]:
        return sorted(
            {
                *LEGACY_SECTION_KINDS,
                SEMANTIC_IR_SECTION_KIND,
                *self._passthrough_versions,
            }
        )

    def _encoded(self, kind: str) -> dict[str, Any] | None:
        if kind == SEMANTIC_IR_SECTION_KIND:
            own = self._encode_keys(_IR_KEYS)
            if not own:
                return None
            dto = section_dto_dict(kind, own, shared=True)
        else:
            payload = self._encode_keys(frozenset(section_fields(kind)))
            if not payload:
                return None
            dto = legacy_section_dto_dict(kind, payload, shared=True)
            del payload
        return {k: dto[k] for k in sorted(dto) if k != CAPTURE_METADATA_KEY}

    def _section(self, kind: str) -> Any:
        ext = self._ext
        if kind in self._passthrough_versions:
            assert ext is not None
            if kind not in ext.passthrough_sections:
                return OMIT
            self._written.append(kind)
            return ext.passthrough_sections[kind]
        if (
            ext is not None
            and ext.availability.get(kind, {}).get("status") == "not_collected"
        ):
            # Never collected: written absent, as it was read (ADR-062 D15).
            return OMIT
        section = self._encoded(kind)
        if section is None:
            return OMIT
        if ext is not None:
            extras = ext.header_extras.get(kind)
            if extras is not None:
                section.update(extras)
                section = {k: section[k] for k in sorted(section)}
            if any(k == kind for k, _ in ext.record_extras):
                holder = {kind: section}
                restore_record_extras(holder, ext.record_extras)
                section = holder[kind]
        self._written.append(kind)
        return section

    def versions(self) -> dict[str, Any]:
        versions: dict[str, Any] = {
            kind: SECTION_SCHEMA_VERSIONS[kind]
            for kind in self._written
            if kind not in self._passthrough_versions
        }
        versions.update(self._passthrough_versions)
        return {k: versions[k] for k in sorted(versions)}

    def availability(self) -> dict[str, Any]:
        availability: dict[str, Any] = (
            dict(self._ext.availability) if self._ext is not None else {}
        )
        for kind in self._written:
            availability.setdefault(kind, {"diagnostics": [], "status": "present"})
        return {k: availability[k] for k in sorted(availability)}


def sectioned_document_stream(
    snap: AbiSnapshot, encode_keys: EncodeKeys, skip_fields: frozenset[str]
) -> LazyItems:
    """*snap*'s single-file sectioned document, as a :class:`LazyItems` the
    `json_stream` encoders serialize section by section.

    Serialize it exactly once: the inventories are filled while the sections
    are encoded, so the document is not reusable. Run it under
    `acyclic_json.gc_paused`, as the whole-document write did.

    *encode_keys* encodes *snap* restricted to a key set; *skip_fields* are
    the snapshot fields it never walks. Callers use
    `snapshot_encode.streamed_sectioned_document`, which supplies both.
    """
    refuse_unwritable_extensions(snap)
    _refuse_unsectioned_fields(snap, skip_fields)
    ext = extensions_of(snap)  # ADR-062 D14
    writer = _SectionWriter(encode_keys, ext)
    top: list[str] = ["schema_version", _SECTIONS_KEY, _VERSIONS_KEY]
    if ext is not None and ext.document_must_understand:
        top.append(DOCUMENT_MUST_UNDERSTAND_KEY)
    top.append(AVAILABILITY_KEY)

    def produce(key: str) -> Any:
        if key == "schema_version":
            return SCHEMA_VERSION
        if key == _SECTIONS_KEY:
            return LazyItems(writer.kinds(), writer._section)
        if key == _VERSIONS_KEY:
            return writer.versions()
        if key == DOCUMENT_MUST_UNDERSTAND_KEY:
            assert ext is not None
            return list(ext.document_must_understand)
        return writer.availability()

    return LazyItems(top, produce)
