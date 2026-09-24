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

"""`SectionDTO` payloads without the DTO's freeze/thaw round trip.

A `SectionDTO` deep-freezes its payload and `to_dict()` deep-thaws it back:
right for a DTO that is kept, pure cost for one the snapshot loader or writer
builds and discards in the same statement -- two full copies of every
section, the graph section alone ~136 MB on oneDAL. These return the same
values with the same validation, one `canonical_form` pass each.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .canonical import canonical_form
from .dto import SECTION_SCHEMA_VERSIONS, SectionDTO
from .guards import mapping as _mapping, required_field as _required_field

__all__ = ["current_section_payload", "section_dto_dict"]


def section_dto_dict(section_kind: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    """``SectionDTO(section_kind, <current version>, payload).to_dict()``
    without the freeze/thaw round trip -- the write-side counterpart of
    `current_section_payload`.

    Same validation (the header through `SectionDTO` itself, the payload
    through `_mapping` and `canonical_form`) and the same value:
    ``_unfreeze(_freeze(canonical_form(p)))`` is ``canonical_form(p)``. The
    payload is a fresh canonical copy, so every value below the returned
    dict's top level is already in canonical form.
    """
    header = SectionDTO(
        section_kind=section_kind,
        section_schema_version=SECTION_SCHEMA_VERSIONS[section_kind],
        payload={},
    )
    _mapping(payload, "payload")
    return {
        "section_kind": header.section_kind,
        "section_schema_version": header.section_schema_version,
        "payload": canonical_form(payload),
    }


def current_section_payload(
    raw: Mapping[str, Any],
) -> tuple[str, dict[str, Any]] | None:
    """``(kind, SectionDTO.from_dict(raw).to_dict()["payload"])`` for a
    section already at its kind's current version, without building the
    frozen DTO; ``None`` when a migration applies (use the DTO path then).

    The same validation runs -- the header through `SectionDTO` itself, the
    payload through `_mapping` and `canonical_form` -- and the result is the
    same value: ``_unfreeze(_freeze(canonical_form(p)))`` is
    ``canonical_form(p)`` for the ``dict``/``list``/scalar trees
    `canonical_form` produces. What is skipped is the round trip itself, two
    full copies of every section on every load (~6 s of a ~30 s oneDAL
    baseline load) for a DTO the loader discards immediately. The returned
    payload is a fresh copy the caller owns.
    """
    _mapping(raw, "a section DTO")
    payload = _required_field(raw, "payload", "a section DTO")
    header = SectionDTO.from_dict(
        {
            "section_kind": _required_field(raw, "section_kind", "a section DTO"),
            "section_schema_version": _required_field(
                raw, "section_schema_version", "a section DTO"
            ),
            "payload": {},
        }
    )
    _mapping(payload, "payload")
    if header.section_schema_version != SECTION_SCHEMA_VERSIONS.get(
        header.section_kind
    ):
        return None
    canonical = canonical_form(payload)
    assert isinstance(canonical, dict)
    return header.section_kind, canonical
