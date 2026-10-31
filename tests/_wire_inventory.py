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

"""ADR-062 D15: the per-section field inventory, generated from the encoders.

:func:`field_inventory` reads a sectioned document the `SectionDTO`
encoders just wrote (`snapshot_encode.snapshot_to_json`) and lists, per
section, its header keys and every object key path inside its payload --
``functions[].params[]`` style, the union over every element of a list.
Header keys include the D14 carriers a writer emits only when non-empty
(:data:`OPTIONAL_HEADER_KEYS`), and the document level lists its own keys and
the `section_availability` entry keys, so the inventory covers the header
entry D15 asks for.

The checked-in copy (`tests/fixtures/wire_contract/field_inventory.json`) is
regenerated from a fixed corpus and diffed in CI
(`tests/unit/storage/test_wire_contract.py`): a section whose inventory moved
must also have moved its `section_schema_version`, with a registered D13
migration from the pinned version.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["OPTIONAL_HEADER_KEYS", "field_inventory"]

#: Header keys written only when non-empty (ADR-062 D14).
OPTIONAL_HEADER_KEYS = ("extensions", "must_understand")


def _walk(value: Any, path: str, out: dict[str, set[str]]) -> None:
    if isinstance(value, Mapping):
        out.setdefault(path, set()).update(str(k) for k in value)
        for key, item in value.items():
            _walk(item, f"{path}.{key}" if path else str(key), out)
    elif isinstance(value, list):
        for item in value:
            _walk(item, f"{path}[]", out)


def field_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    """The field inventory of the sectioned *document*: plain, sorted JSON."""
    sections: dict[str, Any] = {}
    for kind, section in sorted(document.get("sections", {}).items()):
        paths: dict[str, set[str]] = {}
        _walk(section.get("payload", {}), "", paths)
        sections[kind] = {
            "header": sorted(set(section) | set(OPTIONAL_HEADER_KEYS)),
            "section_schema_version": section.get("section_schema_version"),
            "fields": {path: sorted(keys) for path, keys in sorted(paths.items())},
        }
    entries: set[str] = set()
    for entry in document.get("section_availability", {}).values():
        entries.update(entry)
    return {
        "document": sorted(set(document) | {"must_understand"}),
        "availability_entry": sorted(entries),
        "sections": sections,
    }
