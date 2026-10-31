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

"""ADR-062 D14/D15 carriers of the single-file sectioned document.

**What a reader keeps that it does not understand (D14).** The sectioned
document (`sectioned_document.py`) may carry, beyond the sections this build
decodes:

* an unknown section kind -- preserved verbatim, never a reason to refuse
  the document;
* on a section header, ``must_understand`` (names the reader must recognize
  before using that section for comparison) and ``extensions`` (a map of
  canonical JSON values);
* inside a record, unknown fields and an ``extensions`` map
  (`wire_record_extensions.py`).

All of it is collected into one :class:`WireExtensions` on read and merged
back on write, so a load -> save keeps it byte-equivalent. It is never a
domain-model fact (ADR-063 D8): the decoded snapshot holds it on its
runtime-only, ``compare=False`` ``wire_extensions`` field
(:func:`attach`/:func:`extensions_of`), never serialized as itself, so a
``dataclasses.replace``/copy/pickle keeps it beside ``unreadable_sections``.

**Package path.** A ProjectSnapshot package or bundle archive is built from
the flat `snapshot_to_dict` document, which cannot carry any of this, so
:func:`refuse_package_flattening` refuses such a snapshot there (a recorded
gap) rather than storing a blocked section as present and empty.

**What blocks.** A section of a known kind whose header names a
``must_understand`` entry this build does not understand, or which is
written at a ``section_schema_version`` newer than this build, is not
decoded: it is kept verbatim and reported in :attr:`WireExtensions.unreadable`
as ``(section kind, diagnostic)``. The loader copies that onto
``AbiSnapshot.unreadable_sections`` and the comparison resolves exactly the
analyses that read that section to *not comparable*
(`compare/section_readers.py`). The ``provenance`` section holds the
snapshot's identity, which every analysis reads, so blocking it refuses the
document instead. The document-level ``must_understand`` list (the
counterpart of the package manifest's) applies to the whole document: an
unknown name there refuses it.

**Section availability (D15).** ``section_availability`` maps a section kind
to its D3 `FactAvailability` status: ``present``, ``failed`` (with
diagnostics and no payload), or ``not_collected``. A ``failed`` section is
still listed in ``section_schema_versions``, so a reader that predates this
table refuses the document (its missing-section check) instead of reading the
lost evidence as empty; this build reads it as ``unreadable``. A section
missing from both tables is ``not_collected``, never ``present``.

**Schema decision.** None of these carriers bumps a version: each is written
only when non-empty except ``section_availability``, a document-level key a
pre-D14 reader ignores. A pre-D14 reader still never misreads the new
states: it refuses an unknown section kind, a ``failed`` entry (listed but
absent) and a newer section version on its own. The one state it would
ignore is a ``must_understand`` name on a *known* section's header, so a
writer that adds one must also bump that section's ``section_schema_version``
-- this build only ever re-emits names it read.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ..errors import IncompatibleSnapshotSchemaError, SnapshotError
from .canonical import canonical_form
from .dto import SECTION_SCHEMA_VERSIONS
from .wire_record_extensions import (
    RecordExtras,
    collect_record_extras,
    restore_record_extras,
)

__all__ = [
    "AVAILABILITY_KEY",
    "DOCUMENT_MUST_UNDERSTAND_KEY",
    "EXTENSIONS_KEY",
    "IDENTITY_SECTION_KIND",
    "MUST_UNDERSTAND_KEY",
    "UNDERSTOOD_EXTENSIONS",
    "WIRE_EXTENSIONS_KEY",
    "WireExtensions",
    "attach",
    "check_must_understand",
    "extensions_of",
    "merge_wire_extensions",
    "record_section_failure",
    "refuse_package_flattening",
    "refuse_unwritable_extensions",
    "split_wire_extensions",
]

MUST_UNDERSTAND_KEY = "must_understand"
EXTENSIONS_KEY = "extensions"
DOCUMENT_MUST_UNDERSTAND_KEY = "must_understand"
AVAILABILITY_KEY = "section_availability"
#: The private key `from_sectioned_document` hands the collected carriers to
#: the decoder under. Never written to disk.
WIRE_EXTENSIONS_KEY = "\x00wire_extensions"
#: The section holding the snapshot's identity (`library`, `version`).
IDENTITY_SECTION_KIND = "provenance"
#: Extension names this build understands. Empty: no must-understand
#: extension has been defined yet. A section kind this build decodes is
#: understood too.
UNDERSTOOD_EXTENSIONS: frozenset[str] = frozenset()

_SECTIONS_KEY = "sections"
_VERSIONS_KEY = "section_schema_versions"
_STATUSES = frozenset({"present", "failed", "not_collected"})


@dataclass
class WireExtensions:
    """Everything a sectioned document carried that this build kept but did
    not decode. Plain JSON values throughout."""

    #: Sections kept verbatim (unknown kinds, and blocked known kinds), with
    #: their `section_schema_versions` entry.
    passthrough_sections: dict[str, Any] = field(default_factory=dict)
    passthrough_versions: dict[str, Any] = field(default_factory=dict)
    #: ``kind -> {"must_understand": [...], "extensions": {...}}`` for a
    #: section that was decoded.
    header_extras: dict[str, dict[str, Any]] = field(default_factory=dict)
    record_extras: RecordExtras = field(default_factory=dict)
    #: Non-``present`` `section_availability` entries, re-emitted on write.
    availability: dict[str, dict[str, Any]] = field(default_factory=dict)
    document_must_understand: list[str] = field(default_factory=list)
    #: ``(section kind, diagnostic)`` for each section that blocks its readers.
    unreadable: list[tuple[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.passthrough_sections
            or self.passthrough_versions
            or self.header_extras
            or self.record_extras
            or self.availability
            or self.document_must_understand
        )

    def evidence_form(self) -> dict[str, Any]:
        """The canonical form the `evidence` digest hashes (D14: extensions
        are inside the evidence and publication digests)."""
        if self.is_empty():
            return {}
        form: dict[str, Any] = canonical_form(
            {
                "sections": self.passthrough_sections,
                "versions": self.passthrough_versions,
                "headers": self.header_extras,
                "records": {
                    f"{kind}/{key}": [list(item) for item in items]
                    for (kind, key), items in self.record_extras.items()
                },
                "availability": self.availability,
                "must_understand": self.document_must_understand,
            }
        )
        return form


def attach(snap: Any, extensions: WireExtensions) -> None:
    """Associate *extensions* with the decoded snapshot *snap*: stored on its
    runtime-only ``wire_extensions`` field, so `dataclasses.replace`, `copy`
    and pickle carry it with ``unreadable_sections``."""
    snap.wire_extensions = extensions


def extensions_of(snap: Any) -> WireExtensions | None:
    """The carriers *snap* was loaded with, if any."""
    value = getattr(snap, "wire_extensions", None)
    return value if isinstance(value, WireExtensions) else None


def refuse_unwritable_extensions(snap: Any) -> None:
    """Refuse to write *snap* when it names an unreadable section but no
    longer holds the carriers that section's stored state lives in: the
    writer would otherwise emit the blocked section as ``present`` with this
    build's empty defaults."""
    if getattr(snap, "unreadable_sections", ()) and extensions_of(snap) is None:
        raise SnapshotError(
            "Snapshot has unreadable section(s) "
            f"{', '.join(repr(kind) for kind, _ in snap.unreadable_sections)} but "
            "no stored D14/D15 carriers to write them back from; refusing to "
            "write it as if those sections were present."
        )


def refuse_package_flattening(snap: Any) -> None:
    """Refuse to flatten *snap* into a `snapshot_to_dict` document bound for
    a ProjectSnapshot package or a G40 bundle archive.

    Those paths are built from the flat document, which cannot express a
    ``failed``/blocked section, an unknown section, a header ``extensions``
    or ``must_understand`` entry, or an unknown record field. Writing it
    anyway would store a blocked section as present-and-empty -- a false
    clean -- and drop the extensions from the stored evidence digest. Until
    the package path carries them (`docs/contribute/known-gaps.md`), it
    fails closed. Save such a snapshot as a single-file snapshot instead."""
    refuse_unwritable_extensions(snap)
    carried = extensions_of(snap)
    if carried is None:
        return
    # A ``not_collected`` section is simply absent from a package too.
    lost = sorted(
        {kind for kind, _ in carried.unreadable}
        | set(carried.passthrough_sections)
        | set(carried.passthrough_versions)
        | set(carried.header_extras)
        | {kind for kind, _ in carried.record_extras}
        | {k for k, v in carried.availability.items() if v["status"] == "failed"}
    )
    if not lost and not carried.document_must_understand:
        return
    detail = f" (section(s) {', '.join(repr(k) for k in lost)})" if lost else ""
    raise SnapshotError(
        "Snapshot carries stored state a ProjectSnapshot package or bundle "
        "archive cannot represent yet -- a failed or blocked section, an "
        "unknown section, section/record extensions or a must-understand "
        f"list{detail}. Writing it would lose that state; save it as a "
        "single-file snapshot instead."
    )


def record_section_failure(
    snap: Any, section_kind: str, diagnostics: list[str]
) -> None:
    """Producer side of D15: *section_kind* was attempted and its extractor
    failed. The writer stores a ``failed`` header entry with no payload."""
    if (
        section_kind not in SECTION_SCHEMA_VERSIONS
        or section_kind == IDENTITY_SECTION_KIND
    ):
        raise ValueError(f"section {section_kind!r} cannot be recorded as failed")
    if not diagnostics or not all(isinstance(d, str) and d for d in diagnostics):
        raise ValueError("a failed section needs at least one diagnostic")
    ext = extensions_of(snap)
    if ext is None:
        ext = WireExtensions()
        attach(snap, ext)
    ext.availability[section_kind] = {
        "diagnostics": list(diagnostics),
        "status": "failed",
    }
    ext.passthrough_versions[section_kind] = SECTION_SCHEMA_VERSIONS[section_kind]
    ext.passthrough_sections.pop(section_kind, None)


def _names(raw: Any, where: str) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list) or not all(isinstance(n, str) and n for n in raw):
        raise ValueError(f"{where} {MUST_UNDERSTAND_KEY!r} must be a list of names")
    return list(raw)


def _understood(name: str) -> bool:
    return name in UNDERSTOOD_EXTENSIONS or name in SECTION_SCHEMA_VERSIONS


def check_must_understand(raw: Any, where: str) -> list[str]:
    """Validate a document- or manifest-level ``must_understand`` list and
    refuse it when it names anything this build does not understand: those
    names apply to the whole document or package."""
    names = _names(raw, where)
    unknown = [n for n in names if not _understood(n)]
    if unknown:
        raise IncompatibleSnapshotSchemaError(
            f"{where} requires must-understand extension(s) "
            f"{', '.join(repr(n) for n in unknown)} that this abicheck does not "
            "understand. Upgrade abicheck to read it."
        )
    return names


def _availability_table(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    raw = document.get(AVAILABILITY_KEY)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(f"{AVAILABILITY_KEY!r} must be an object")
    table: dict[str, dict[str, Any]] = {}
    for kind, entry in raw.items():
        if not isinstance(entry, Mapping) or entry.get("status") not in _STATUSES:
            raise ValueError(
                f"{AVAILABILITY_KEY!r} entry {kind!r} must be an object whose "
                f"status is one of {sorted(_STATUSES)}"
            )
        diagnostics = entry.get("diagnostics", [])
        if not isinstance(diagnostics, list) or not all(
            isinstance(d, str) for d in diagnostics
        ):
            raise ValueError(
                f"{AVAILABILITY_KEY!r} entry {kind!r} diagnostics must be strings"
            )
        if entry["status"] == "failed" and not diagnostics:
            raise ValueError(f"failed section {kind!r} must carry its diagnostics")
        table[kind] = dict(entry)
    return table


def _blocking_reason(kind: str, section: Any, stated: Any) -> str | None:
    current = SECTION_SCHEMA_VERSIONS[kind]
    if isinstance(stated, int) and not isinstance(stated, bool) and stated > current:
        return (
            f"section {kind!r} is written at section_schema_version {stated}, newer "
            f"than this build's {current}"
        )
    if not isinstance(section, Mapping):
        return None
    unknown = [
        n
        for n in _names(section.get(MUST_UNDERSTAND_KEY), f"section {kind!r}")
        if not _understood(n)
    ]
    if unknown:
        return (
            f"section {kind!r} requires must-understand extension(s) "
            f"{', '.join(repr(n) for n in unknown)} this build does not understand"
        )
    return None


def split_wire_extensions(document: Mapping[str, Any]) -> dict[str, Any]:
    """*document* with every D14/D15 carrier removed and collected under
    `WIRE_EXTENSIONS_KEY`, ready for the existing validation and decode.

    A malformed document (no `sections`/`section_schema_versions` mapping) is
    returned unchanged for the caller's own checks to refuse."""
    sections = document.get(_SECTIONS_KEY)
    stated = document.get(_VERSIONS_KEY)
    if not isinstance(sections, Mapping) or not isinstance(stated, Mapping):
        return dict(document)
    ext = WireExtensions()
    ext.document_must_understand = check_must_understand(
        document.get(DOCUMENT_MUST_UNDERSTAND_KEY), "Snapshot document"
    )
    availability = _availability_table(document)
    kept: dict[str, Any] = {}
    kept_versions: dict[str, Any] = {}
    for kind in sorted(set(sections) | set(stated) | set(availability)):
        section = sections.get(kind)
        version = stated.get(kind)
        entry = availability.get(kind)
        status = entry["status"] if entry is not None else None
        if status in ("failed", "not_collected"):
            if section is not None:
                raise ValueError(
                    f"section {kind!r} is {status} in {AVAILABILITY_KEY!r} but carries "
                    "a payload -- the document is corrupted or was hand-edited"
                )
            if (status == "failed") != (kind in stated):
                raise ValueError(
                    f"section {kind!r} availability {status!r} disagrees with "
                    f"{_VERSIONS_KEY!r}"
                )
            assert entry is not None
            diagnostics = list(entry.get("diagnostics", []))
            ext.availability[kind] = dict(entry)
            if status == "failed":
                if kind == IDENTITY_SECTION_KIND:
                    raise ValueError("the provenance section cannot be failed")
                ext.passthrough_versions[kind] = version
                ext.unreadable.append(
                    (
                        kind,
                        f"section {kind!r} failed at extraction: "
                        + "; ".join(diagnostics),
                    )
                )
            continue
        if section is None or version is None:
            # Let `from_sectioned_document`'s missing/extra checks refuse it.
            if section is not None:
                kept[kind] = section
            if version is not None:
                kept_versions[kind] = version
            continue
        if kind not in SECTION_SCHEMA_VERSIONS:
            ext.passthrough_sections[kind] = section
            ext.passthrough_versions[kind] = version
            continue
        own = (
            section.get("section_schema_version")
            if isinstance(section, Mapping)
            else None
        )
        if own != version:
            raise ValueError(
                f"sectioned document section {kind!r} carries "
                f"section_schema_version {own!r} but {_VERSIONS_KEY!r} states "
                f"{version!r} -- the document is corrupted or was hand-edited"
            )
        reason = _blocking_reason(kind, section, version)
        if reason is not None:
            if kind == IDENTITY_SECTION_KIND:
                raise IncompatibleSnapshotSchemaError(
                    f"Snapshot {reason}; it holds the snapshot's identity, so "
                    "nothing can be compared. Upgrade abicheck to read this snapshot."
                )
            ext.passthrough_sections[kind] = section
            ext.passthrough_versions[kind] = version
            ext.unreadable.append((kind, reason))
            continue
        kept[kind] = _split_header(kind, section, ext)
        kept_versions[kind] = version
    out = {
        key: value
        for key, value in document.items()
        if key not in (DOCUMENT_MUST_UNDERSTAND_KEY, AVAILABILITY_KEY)
    }
    out[_SECTIONS_KEY] = kept
    out[_VERSIONS_KEY] = kept_versions
    if not ext.is_empty() or ext.unreadable:
        out[WIRE_EXTENSIONS_KEY] = ext
    return out


def _split_header(kind: str, section: Any, ext: WireExtensions) -> Any:
    if not isinstance(section, Mapping):
        return section
    extras: dict[str, Any] = {}
    if MUST_UNDERSTAND_KEY in section:
        extras[MUST_UNDERSTAND_KEY] = list(section[MUST_UNDERSTAND_KEY])
    if EXTENSIONS_KEY in section:
        value = section[EXTENSIONS_KEY]
        if not isinstance(value, Mapping):
            raise ValueError(f"section {kind!r} {EXTENSIONS_KEY!r} must be an object")
        extras[EXTENSIONS_KEY] = canonical_form(value)
    if extras:
        ext.header_extras[kind] = extras
        section = {
            k: v
            for k, v in section.items()
            if k not in (MUST_UNDERSTAND_KEY, EXTENSIONS_KEY)
        }
    collect_record_extras(kind, section, ext.record_extras)
    return section


def merge_wire_extensions(
    document: dict[str, Any], ext: WireExtensions | None
) -> dict[str, Any]:
    """Write *ext* back into the freshly encoded sectioned *document* (in
    place), and stamp `section_availability` for every section written."""
    sections: dict[str, Any] = document[_SECTIONS_KEY]
    versions: dict[str, Any] = document[_VERSIONS_KEY]
    availability: dict[str, Any] = {}
    if ext is not None:
        for kind, extras in ext.header_extras.items():
            section = sections.get(kind)
            if isinstance(section, dict) and kind not in ext.passthrough_versions:
                section.update(extras)
                sections[kind] = {k: section[k] for k in sorted(section)}
        restore_record_extras(sections, ext.record_extras)
        for kind, version in ext.passthrough_versions.items():
            if kind in ext.passthrough_sections:
                sections[kind] = ext.passthrough_sections[kind]
            else:
                sections.pop(kind, None)
            versions[kind] = version
        availability.update(ext.availability)
        if ext.document_must_understand:
            document[DOCUMENT_MUST_UNDERSTAND_KEY] = list(ext.document_must_understand)
    for kind in sections:
        availability.setdefault(kind, {"diagnostics": [], "status": "present"})
    document[_SECTIONS_KEY] = {k: sections[k] for k in sorted(sections)}
    document[_VERSIONS_KEY] = {k: versions[k] for k in sorted(versions)}
    document[AVAILABILITY_KEY] = {k: availability[k] for k in sorted(availability)}
    return document
