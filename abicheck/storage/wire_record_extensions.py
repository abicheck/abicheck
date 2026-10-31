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

"""ADR-062 D14 at record level: unknown fields inside a DTO record.

A record of an identity-matched list (functions, variables, record types,
enums) may carry fields this build's encoder never writes: a newer writer's
optional field, or the record's ``extensions`` map. The decoder ignores
them; this module remembers them, keyed by the record's identity and its
ordinal among records sharing that identity, and writes them back into the
same record on re-save. A record the snapshot no longer holds takes its
extras with it.

The known fields of a record are exactly the keys its encoder writes
(`wire_field_names.wire_field_names`), so this cannot drift from the wire.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..model import EnumType, Function, RecordType, Variable
from .canonical import canonical_form
from .wire_field_names import wire_field_names

__all__ = [
    "RECORD_EXTENSIONS_KEY",
    "RECORD_LISTS",
    "RecordExtras",
    "collect_record_extras",
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

#: ``(section, key) -> [(identity, ordinal, {unknown field: value})]``.
RecordExtras = dict[tuple[str, str], list[tuple[str, int, dict[str, Any]]]]


def _known_fields(cls: type) -> frozenset[str]:
    return frozenset(wire_field_names(cls))


def _identity(record: Mapping[str, Any], key: str) -> str:
    value = record.get(key)
    return value if isinstance(value, str) else ""


def _payload(section: Any) -> Mapping[str, Any] | None:
    payload = section.get("payload") if isinstance(section, Mapping) else None
    return payload if isinstance(payload, Mapping) else None


def collect_record_extras(kind: str, section: Any, out: RecordExtras) -> None:
    """Record every unknown field of *section*'s identity-matched records."""
    payload = _payload(section)
    if payload is None:
        return
    for (section_kind, list_key), (cls, id_key) in RECORD_LISTS.items():
        records = payload.get(list_key) if section_kind == kind else None
        if not isinstance(records, list):
            continue
        known = _known_fields(cls)
        seen: dict[str, int] = {}
        for record in records:
            if not isinstance(record, Mapping):
                continue
            ident = _identity(record, id_key)
            ordinal = seen.get(ident, 0)
            seen[ident] = ordinal + 1
            extras = {k: v for k, v in record.items() if k not in known}
            if extras:
                if RECORD_EXTENSIONS_KEY in extras and not isinstance(
                    extras[RECORD_EXTENSIONS_KEY], Mapping
                ):
                    raise ValueError(
                        f"{kind}.{list_key} record {ident!r} "
                        f"{RECORD_EXTENSIONS_KEY!r} must be an object"
                    )
                out.setdefault((kind, list_key), []).append(
                    (ident, ordinal, canonical_form(extras))
                )


def restore_record_extras(sections: Mapping[str, Any], extras: RecordExtras) -> None:
    """Write *extras* back into the matching records of *sections* (in place)."""
    for (kind, list_key), items in extras.items():
        payload = _payload(sections.get(kind))
        records = payload.get(list_key) if payload is not None else None
        if not isinstance(records, list):
            continue
        _cls, id_key = RECORD_LISTS[(kind, list_key)]
        wanted = {(ident, ordinal): fields for ident, ordinal, fields in items}
        seen: dict[str, int] = {}
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                continue
            ident = _identity(record, id_key)
            ordinal = seen.get(ident, 0)
            seen[ident] = ordinal + 1
            fields = wanted.get((ident, ordinal))
            if fields:
                merged = {**fields, **record}
                records[index] = {k: merged[k] for k in sorted(merged)}
