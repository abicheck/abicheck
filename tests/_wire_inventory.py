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

"""ADR-062 D15: what a written sectioned document actually carries.

The checked-in inventory is generated from the encoders' declarations
(`abicheck.storage.wire_inventory.declared_field_inventory`), not from this
walk. :func:`observed_field_inventory` is the other half of the contract: it
reads a document the encoders just wrote and lists, per section, its header
keys and every object key path inside its payload (``functions[].params[]``
style, the union over every element of a list), so
`tests/unit/storage/test_wire_contract.py` can require each observed key to
be declared.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["observed_field_inventory"]


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
