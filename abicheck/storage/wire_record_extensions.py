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

"""ADR-062 D14 at record level: unknown fields inside a DTO record, at any
nesting depth.

A record of an identity-matched list (functions, variables, record types,
enums) or a section's single metadata object (the ELF, PE and Mach-O
metadata of the ``binary`` section) may carry fields this build's encoder
never writes: a newer writer's optional field, or an ``extensions`` map. So
may any record nested inside one through a list-of-records field
(parameters, record fields, enum members, ELF symbols and imports, PE and
Mach-O exports). The decoder ignores them; this module remembers them as a
tree keyed by the top-level record's identity and its ordinal among records
sharing that identity, with nested records addressed by their position in
their (semantically ordered) list, and writes them back into the same
places on re-save. A record the snapshot no longer holds takes its extras
with it; a nested position the re-encoded record no longer has does too.

The known fields of a record are exactly the keys its encoder writes
(`wire_field_names.wire_field_names`), and the nested record classes are the
list-of-dataclass fields of its type hints, so neither can drift from the
model.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from collections.abc import Mapping
from typing import Any

from ..model import EnumType, Function, RecordType, Variable
from ..model.execution_cache import memoized
from .canonical import canonical_form
from .wire_field_names import wire_field_names

__all__ = [
    "RECORD_EXTENSIONS_KEY",
    "RECORD_LISTS",
    "RECORD_OBJECTS",
    "RecordExtras",
    "collect_record_extras",
    "nested_record_classes",
    "restore_record_extras",
]

RECORD_EXTENSIONS_KEY = "extensions"

#: ``(section kind, payload key) -> (record class, identity field)``.
RECORD_LISTS: Mapping[tuple[str, str], tuple[type, str]] = {
    ("declarations", "functions"): (Function, "mangled"),
    ("declarations", "variables"): (Variable, "mangled"),
    ("declarations", "enums"): (EnumType, "name"),
    ("types", "types"): (RecordType, "name"),
}


def _binary_objects() -> dict[tuple[str, str], type]:
    from ..model.elf_facts import ElfMetadata
    from ..model.macho_facts import MachoMetadata
    from ..model.pe_facts import PeMetadata

    return {
        ("binary", "elf"): ElfMetadata,
        ("binary", "pe"): PeMetadata,
        ("binary", "macho"): MachoMetadata,
    }


#: ``(section kind, payload key) -> record class`` for a single object.
RECORD_OBJECTS: Mapping[tuple[str, str], type] = _binary_objects()

#: ``(section, key) -> [(identity, ordinal, extras tree)]``. A tree is
#: ``{"fields": {unknown field: value}, "items": {list field: {"<index>":
#: tree}}}``, each part present only when non-empty.
RecordExtras = dict[tuple[str, str], list[tuple[str, int, dict[str, Any]]]]

_FIELDS = "fields"
_ITEMS = "items"


@memoized
def _known_fields(cls: type) -> frozenset[str]:
    return frozenset(wire_field_names(cls))


@memoized
def nested_record_classes(cls: type) -> Mapping[str, type]:
    """*cls*'s list-of-records fields: ``field -> element dataclass``."""
    out: dict[str, type] = {}
    for name, hint in typing.get_type_hints(cls).items():
        union = typing.get_origin(hint) in (typing.Union, types.UnionType)
        for arm in typing.get_args(hint) if union else (hint,):
            if typing.get_origin(arm) is not list:
                continue
            (element,) = typing.get_args(arm) or (None,)
            if isinstance(element, type) and dataclasses.is_dataclass(element):
                out[name] = element
    return out


def _identity(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) else ""


def _payload(section: Any) -> Mapping[str, Any] | None:
    payload = section.get("payload") if isinstance(section, Mapping) else None
    return payload if isinstance(payload, Mapping) else None


def _extras_tree(record: Mapping[str, Any], cls: type, where: str) -> dict[str, Any]:
    known = _known_fields(cls)
    tree: dict[str, Any] = {}
    own = {k: v for k, v in record.items() if k not in known}
    if own:
        if RECORD_EXTENSIONS_KEY in own and not isinstance(
            own[RECORD_EXTENSIONS_KEY], Mapping
        ):
            raise ValueError(f"{where} {RECORD_EXTENSIONS_KEY!r} must be an object")
        tree[_FIELDS] = canonical_form(own)
    items: dict[str, Any] = {}
    for name, element in nested_record_classes(cls).items():
        nested = record.get(name)
        if not isinstance(nested, list):
            continue
        found = {
            str(index): sub
            for index, value in enumerate(nested)
            if isinstance(value, Mapping)
            and (sub := _extras_tree(value, element, f"{where}.{name}[{index}]"))
        }
        if found:
            items[name] = found
    if items:
        tree[_ITEMS] = items
    return tree


def collect_record_extras(kind: str, section: Any, out: RecordExtras) -> None:
    """Record every unknown field of *section*'s records, at any depth."""
    payload = _payload(section)
    if payload is None:
        return
    for (section_kind, list_key), (cls, id_key) in RECORD_LISTS.items():
        records = payload.get(list_key) if section_kind == kind else None
        if not isinstance(records, list):
            continue
        seen: dict[str, int] = {}
        for record in records:
            if not isinstance(record, Mapping):
                continue
            ident = _identity(record, id_key)
            ordinal = seen.get(ident, 0)
            seen[ident] = ordinal + 1
            tree = _extras_tree(record, cls, f"{kind}.{list_key} record {ident!r}")
            if tree:
                out.setdefault((kind, list_key), []).append((ident, ordinal, tree))
    for (section_kind, key), cls in RECORD_OBJECTS.items():
        record = payload.get(key) if section_kind == kind else None
        if isinstance(record, Mapping):
            tree = _extras_tree(record, cls, f"{kind}.{key}")
            if tree:
                out.setdefault((kind, key), []).append(("", 0, tree))


def _merged(record: dict[str, Any], tree: Mapping[str, Any]) -> dict[str, Any]:
    for name, positions in tree.get(_ITEMS, {}).items():
        nested = record.get(name)
        if not isinstance(nested, list):
            continue
        for index_text, sub in positions.items():
            index = int(index_text)
            if index < len(nested) and isinstance(nested[index], dict):
                nested[index] = _merged(nested[index], sub)
    merged = {**tree.get(_FIELDS, {}), **record}
    return {k: merged[k] for k in sorted(merged)}


def restore_record_extras(sections: Mapping[str, Any], extras: RecordExtras) -> None:
    """Write *extras* back into the matching records of *sections* (in place)."""
    for (kind, list_key), items in extras.items():
        payload = _payload(sections.get(kind))
        if payload is None:
            continue
        if (kind, list_key) in RECORD_OBJECTS:
            record = payload.get(list_key)
            if isinstance(record, dict) and items:
                assert isinstance(payload, dict)
                payload[list_key] = _merged(record, items[0][2])
            continue
        records = payload.get(list_key)
        if not isinstance(records, list):
            continue
        _cls, id_key = RECORD_LISTS[(kind, list_key)]
        wanted = {(ident, ordinal): tree for ident, ordinal, tree in items}
        seen: dict[str, int] = {}
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            ident = _identity(record, id_key)
            ordinal = seen.get(ident, 0)
            seen[ident] = ordinal + 1
            tree = wanted.get((ident, ordinal))
            if tree:
                records[index] = _merged(record, tree)
