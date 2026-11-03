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

"""Section-level availability: from the stored carriers to the snapshot, and
from a producer to both (ADR-062 D15).

The stored ``section_availability`` table and the reader's own decisions
(`wire_extensions.py`) end up in two runtime-only `AbiSnapshot` fields that
must agree: ``unreadable_sections`` (what the comparison blocks) and
``section_availability`` (what the assurance layer and the report show, one
`model.section_availability.SectionAvailability` per section that is not
``present``). :func:`apply_section_states` sets both from the carriers a
load kept; :func:`record_section_availability` is the producer side, which
sets the carriers too, so a live snapshot and its stored dump agree.
"""

from __future__ import annotations

from typing import Any

from ..model.section_availability import SectionAvailability, SectionStatus
from .dto import SECTION_SCHEMA_VERSIONS
from .wire_extensions import (
    DEFAULT_FILLED_SECTION_KINDS,
    IDENTITY_SECTION_KIND,
    WireExtensions,
    attach,
    extensions_of,
    failed_reason,
    not_collected_reason,
)

__all__ = [
    "apply_section_states",
    "record_section_availability",
    "record_section_failure",
    "section_states",
]

#: The statuses a producer may record. ``blocked`` and ``unrecorded`` are
#: decided by the reading build, never produced.
_PRODUCED = frozenset({SectionStatus.FAILED, SectionStatus.NOT_COLLECTED})


def section_states(ext: WireExtensions) -> tuple[SectionAvailability, ...]:
    """Every section *ext* says is not ``present``, sorted by kind.

    A recorded ``failed``/``not_collected`` entry wins; a section blocked for
    any other reason (an unknown must-understand name, a newer version, a
    kept section this build knows but the writer did not decode) is
    ``blocked``; an unrecorded absent section is ``unrecorded``. A
    ``not_collected`` entry for a kind that decodes absence as ``None``
    (``graph``, ``semantic_ir``) is listed too: the model already holds it as
    absent, and the report still names it."""
    states: dict[str, SectionAvailability] = {}
    for kind, entry in ext.availability.items():
        status = SectionStatus(entry["status"])
        if status is SectionStatus.PRESENT:
            continue
        states[kind] = SectionAvailability(
            kind, status, tuple(entry.get("diagnostics", ()))
        )
    for kind, diagnostic in ext.unreadable:
        states.setdefault(
            kind, SectionAvailability(kind, SectionStatus.BLOCKED, (diagnostic,))
        )
    for kind in ext.unrecorded:
        states.setdefault(
            kind,
            SectionAvailability(
                kind,
                SectionStatus.UNRECORDED,
                (
                    f"section {kind!r} is absent from the stored document and "
                    "has no availability entry; read as its defaults",
                ),
            ),
        )
    return tuple(states[kind] for kind in sorted(states))


def apply_section_states(snap: Any, ext: WireExtensions) -> None:
    """Set *snap*'s ``unreadable_sections`` and ``section_availability`` from
    the carriers *ext* a load kept."""
    snap.unreadable_sections = tuple(ext.unreadable)
    snap.section_availability = section_states(ext)


def record_section_availability(
    snap: Any, section_kind: str, status: SectionStatus, diagnostics: list[str]
) -> None:
    """Producer side of D15: *section_kind* was not collected, or was
    attempted and its extractor failed. The writer stores the header entry
    with no payload; the live snapshot blocks the same readers a reloaded
    one would, so a dump compared in-process and the stored dump agree.

    ``failed`` needs at least one diagnostic. ``not_collected`` is accepted
    only for a default-filled kind (`wire_extensions.DEFAULT_FILLED_SECTION_KINDS`):
    ``graph`` and ``semantic_ir`` say "not collected" by being ``None``."""
    status = SectionStatus(status)
    if (
        status not in _PRODUCED
        or section_kind not in SECTION_SCHEMA_VERSIONS
        or section_kind == IDENTITY_SECTION_KIND
        or (
            status is SectionStatus.NOT_COLLECTED
            and section_kind not in DEFAULT_FILLED_SECTION_KINDS
        )
    ):
        raise ValueError(f"section {section_kind!r} cannot be recorded as {status}")
    if not all(isinstance(d, str) and d for d in diagnostics):
        raise ValueError("section diagnostics must be non-empty strings")
    if status is SectionStatus.FAILED and not diagnostics:
        raise ValueError("a failed section needs at least one diagnostic")
    ext = extensions_of(snap)
    if ext is None:
        ext = WireExtensions()
        attach(snap, ext)
    ext.availability[section_kind] = {
        "diagnostics": list(diagnostics),
        "status": str(status),
    }
    ext.passthrough_sections.pop(section_kind, None)
    if status is SectionStatus.FAILED:
        # Listed in `section_schema_versions` without a payload, so a reader
        # that predates the table refuses the document.
        ext.passthrough_versions[section_kind] = SECTION_SCHEMA_VERSIONS[section_kind]
        reason = failed_reason(section_kind, list(diagnostics))
    else:
        ext.passthrough_versions.pop(section_kind, None)
        reason = not_collected_reason(section_kind, list(diagnostics))
    ext.unreadable = [u for u in ext.unreadable if u[0] != section_kind]
    ext.unreadable.append((section_kind, reason))
    ext.unrecorded = [k for k in ext.unrecorded if k != section_kind]
    # Earlier blocks a load decided stay; only this section's entry moves.
    kept = tuple(u for u in snap.unreadable_sections if u[0] != section_kind)
    snap.unreadable_sections = (*kept, (section_kind, reason))
    snap.section_availability = section_states(ext)


def record_section_failure(
    snap: Any, section_kind: str, diagnostics: list[str]
) -> None:
    """*section_kind* was attempted and its extractor failed."""
    record_section_availability(snap, section_kind, SectionStatus.FAILED, diagnostics)
