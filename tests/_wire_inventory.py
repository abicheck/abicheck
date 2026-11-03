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

"""ADR-062 D15 (1): the wire-contract field inventory, and what a written
document actually carries.

:func:`declared_field_inventory` is generated from the `SectionDTO`
encoders' own declarations; nothing here encodes a snapshot:

* the section header -- `storage.dto.SectionDTO`'s fields, exactly the keys
  `SectionDTO.to_dict` emits (``must_understand``/``extensions`` only when
  non-empty);
* the payload keys -- the codec class behind each section
  (`storage.import_v1.LEGACY_SECTION_DOCUMENT_CODECS`): a sparse section's
  ``REQUIRED_FIELDS`` and ``OPTIONAL_FIELDS``, or a one-field codec's
  dataclass field;
* the record shapes inside a payload -- the model classes the record encoder
  walks (`storage.wire_record_extensions.RECORD_LISTS`/``RECORD_OBJECTS`` and,
  recursively, their list-of-record fields), each listed with
  `storage.wire_field_names.wire_field_names`, the key set the encoder
  writes and the D14 record reader compares against;
* the document keys and the ``section_availability`` entry, with its stored
  status vocabulary.

So an optional key the fixed corpus never sets is still listed. The checked-in
copy is `tests/fixtures/wire_contract/field_inventory.json`; a section whose
entry moves must bump its `section_schema_version` with a D13 migration.
``semantic_ir`` is not listed: its payload is the IR codec's own document,
versioned and tested there.

:func:`observed_field_inventory` is the conformance half: it reads a document
the encoders just wrote and lists, per section, its header keys and every
object key path inside its payload (``functions[].params[]`` style, the union
over every element of a list), so `tests/unit/storage/test_wire_contract.py`
can require every observed key to be declared.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from abicheck.model.section_availability import SectionStatus
from abicheck.storage.dto import SECTION_SCHEMA_VERSIONS, SectionDTO
from abicheck.storage.import_v1 import LEGACY_SECTION_DOCUMENT_CODECS
from abicheck.storage.sectioned_document import (
    SECTION_SCHEMA_VERSIONS_KEY,
    SECTIONS_KEY,
)
from abicheck.storage.wire_extensions import (
    AVAILABILITY_KEY,
    DOCUMENT_MUST_UNDERSTAND_KEY,
)
from abicheck.storage.wire_field_names import wire_field_names
from abicheck.storage.wire_record_extensions import (
    RECORD_LISTS,
    RECORD_OBJECTS,
    nested_record_classes,
)

__all__ = [
    "AVAILABILITY_ENTRY_KEYS",
    "declared_field_inventory",
    "observed_field_inventory",
]

#: The keys of one ``section_availability`` entry
#: (`wire_extensions.merge_wire_extensions` writes both on every entry).
AVAILABILITY_ENTRY_KEYS: tuple[str, ...] = ("diagnostics", "status")

#: The statuses a stored entry may carry; ``blocked`` and ``unrecorded`` are
#: decided by the reader and never stored.
_STORED_STATUSES = (
    SectionStatus.PRESENT,
    SectionStatus.NOT_COLLECTED,
    SectionStatus.FAILED,
)


def _payload_keys(codec: Any) -> list[str]:
    required = getattr(codec, "REQUIRED_FIELDS", None)
    if required is not None:
        return sorted({*required, *codec.OPTIONAL_FIELDS})
    return sorted(f.name for f in dataclasses.fields(codec))


def _records(
    path: str, cls: type, out: dict[str, list[str]], seen: frozenset[type]
) -> None:
    out[path] = sorted(wire_field_names(cls))
    if cls in seen:
        return
    for name, element in sorted(nested_record_classes(cls).items()):
        _records(f"{path}.{name}[]", element, out, seen | {cls})


def _section_fields(kind: str, codec: Any) -> dict[str, list[str]]:
    fields: dict[str, list[str]] = {"": _payload_keys(codec)}
    for (section, key), (cls, _identity) in RECORD_LISTS.items():
        if section == kind:
            _records(f"{key}[]", cls, fields, frozenset())
    for (section, key), cls in RECORD_OBJECTS.items():
        if section == kind:
            _records(key, cls, fields, frozenset())
    return {path: fields[path] for path in sorted(fields)}


def declared_field_inventory() -> dict[str, Any]:
    """The inventory, as plain sorted JSON."""
    header = sorted(f.name for f in dataclasses.fields(SectionDTO))
    return {
        "document": sorted(
            {
                "schema_version",
                SECTIONS_KEY,
                SECTION_SCHEMA_VERSIONS_KEY,
                AVAILABILITY_KEY,
                DOCUMENT_MUST_UNDERSTAND_KEY,
            }
        ),
        "availability_entry": list(AVAILABILITY_ENTRY_KEYS),
        "availability_statuses": sorted(str(s) for s in _STORED_STATUSES),
        "sections": {
            kind: {
                "header": header,
                "section_schema_version": SECTION_SCHEMA_VERSIONS[kind],
                "fields": _section_fields(kind, codec),
            }
            for kind, codec in sorted(LEGACY_SECTION_DOCUMENT_CODECS.items())
        },
    }


def _walk(value: Any, path: str, out: dict[str, set[str]]) -> None:
    if isinstance(value, Mapping):
        out.setdefault(path, set()).update(str(k) for k in value)
        for key, item in value.items():
            _walk(item, f"{path}.{key}" if path else str(key), out)
    elif isinstance(value, list):
        for item in value:
            _walk(item, f"{path}[]", out)


def observed_field_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    """The keys the sectioned *document* carries: plain, sorted JSON."""
    sections: dict[str, Any] = {}
    for kind, section in sorted(document.get("sections", {}).items()):
        paths: dict[str, set[str]] = {}
        _walk(section.get("payload", {}), "", paths)
        sections[kind] = {
            "header": sorted(section),
            "section_schema_version": section.get("section_schema_version"),
            "fields": {path: sorted(keys) for path, keys in sorted(paths.items())},
        }
    entries: set[str] = set()
    statuses: set[str] = set()
    for entry in document.get("section_availability", {}).values():
        entries.update(entry)
        statuses.add(entry.get("status"))
    return {
        "document": sorted(document),
        "availability_entry": sorted(entries),
        "availability_statuses": sorted(statuses),
        "sections": sections,
    }
