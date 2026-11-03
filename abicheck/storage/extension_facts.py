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

"""ADR-062 D19: write and read a declared extension fact on a snapshot.

The value lives on the snapshot's D14 carriers (`wire_extensions`), in the
``extensions`` map of its section's header, never on a domain-model field
(ADR-063 D8). So it is written back on every re-save -- by this build and by
an older one that does not know it -- is inside the evidence digest, and
travels with ``dataclasses.replace``/copy/pickle like every other carrier.

:func:`read_extension_fact` returns a `Fact`:

* ``PRESENT`` -- the stored value, decoded;
* ``NOT_COLLECTED`` -- the section header holds no value for it (a writer
  that predates the fact, or one that did not produce it);
* ``FAILED`` -- the fact's section is blocked for this reader, or the stored
  value does not decode; the diagnostic says which.
"""

from __future__ import annotations

from typing import Any, TypeVar

from ..errors import SnapshotError
from ..model.availability import FactStatus
from ..model.export_read import ExportReadState
from ..model.fact import Fact
from .canonical import canonical_form
from .extension_fact_catalog import (
    EXPORT_READ,
    EXTENSION_FACTS,
    ExtensionFact,
    ExtensionFactRegistry,
    ReaderSemantics,
)
from .wire_extensions import (
    EXTENSIONS_KEY,
    MUST_UNDERSTAND_KEY,
    WireExtensions,
    attach,
    extensions_of,
)

__all__ = [
    "apply_export_read_fact",
    "read_extension_fact",
    "sync_export_read_fact",
    "write_extension_fact",
]

T = TypeVar("T")


def _blocked_reason(snap: Any, section: str) -> str | None:
    for kind, reason in getattr(snap, "unreadable_sections", ()):
        if kind == section:
            return str(reason)
    return None


def write_extension_fact(
    snap: Any,
    fact: ExtensionFact[T],
    value: T,
    *,
    registry: ExtensionFactRegistry = EXTENSION_FACTS,
) -> None:
    """Record *value* for *fact* on *snap*, to be written in its section's
    header ``extensions`` map, naming the fact in the section's (or the
    document's) ``must_understand`` list when its semantics require it for
    this value. Overwrites an earlier value, including its
    ``must_understand`` entry.

    *fact* must be declared in *registry* (this build's catalog unless a
    caller states a newer one): an undeclared name has no stated reader
    semantics, which is the decision this API exists to force.
    """
    if fact not in registry:
        raise ValueError(
            f"extension fact {getattr(fact, 'name', fact)!r} is not declared in "
            "the extension fact catalog (storage/extension_fact_catalog.py)"
        )
    blocked = _blocked_reason(snap, fact.section)
    if blocked is not None:
        raise ValueError(
            f"cannot write extension fact {fact.name!r}: its section is not "
            f"decoded here ({blocked})"
        )
    encoded = canonical_form(fact.encode(value))
    needs = fact.needs_understanding(value)
    ext = extensions_of(snap)
    if ext is None:
        ext = WireExtensions()
        attach(snap, ext)
    extras = ext.header_extras.setdefault(fact.section, {})
    stored = dict(extras.get(EXTENSIONS_KEY, {}))
    stored[fact.name] = encoded
    extras[EXTENSIONS_KEY] = {k: stored[k] for k in sorted(stored)}
    section_names = set(extras.get(MUST_UNDERSTAND_KEY, ()))
    document_names = set(ext.document_must_understand)
    section_names.discard(fact.name)
    document_names.discard(fact.name)
    if needs and fact.semantics is ReaderSemantics.SECTION_MUST_UNDERSTAND:
        section_names.add(fact.name)
    if needs and fact.semantics is ReaderSemantics.DOCUMENT_MUST_UNDERSTAND:
        document_names.add(fact.name)
    if section_names:
        extras[MUST_UNDERSTAND_KEY] = sorted(section_names)
    else:
        extras.pop(MUST_UNDERSTAND_KEY, None)
    ext.document_must_understand = sorted(document_names)


def read_extension_fact(snap: Any, fact: ExtensionFact[T]) -> Fact[T]:
    """The stored value of *fact* on *snap* as a `Fact` (see the module
    docstring for the three outcomes)."""
    blocked = _blocked_reason(snap, fact.section)
    if blocked is not None:
        return Fact.failed(
            f"extension fact {fact.name!r} unreadable: {blocked}",
            producer=None,
        )
    ext = extensions_of(snap)
    extras = ext.header_extras.get(fact.section, {}) if ext is not None else {}
    stored = extras.get(EXTENSIONS_KEY, {})
    if fact.name not in stored:
        return Fact.not_collected()
    try:
        value = fact.decode(stored[fact.name])
    except (TypeError, ValueError) as exc:
        return Fact.failed(
            f"extension fact {fact.name!r} does not decode: {exc}", producer=None
        )
    return Fact.present(value)


_PLATFORMS = ("elf", "pe", "macho")


def _platform_blocks(snap: Any) -> dict[str, Any]:
    return {p: m for p in _PLATFORMS if (m := getattr(snap, p, None)) is not None}


def sync_export_read_fact(snap: Any) -> None:
    """Record *snap*'s platform blocks' ``export_read`` as the
    :data:`~.extension_fact_catalog.EXPORT_READ` fact (ADR-079), so every
    write and the evidence digest carry it.

    A no-op when no block records its read (all ``unrecorded``): there is
    nothing to say, and a fact a loaded document already carries is kept.
    Otherwise the value lists every block that is not ``complete``. Each
    write path and the digest call this first; it is idempotent.
    """
    blocks = _platform_blocks(snap)
    states = {
        p: ExportReadState(getattr(m, "export_read", ExportReadState.UNRECORDED))
        for p, m in blocks.items()
    }
    if all(s is ExportReadState.UNRECORDED for s in states.values()):
        return
    if _blocked_reason(snap, EXPORT_READ.section) is not None:
        return
    value = {p: s.value for p, s in states.items() if s is not ExportReadState.COMPLETE}
    write_extension_fact(snap, EXPORT_READ, value)


def apply_export_read_fact(snap: Any) -> None:
    """Set each loaded platform block's ``export_read`` from the stored fact:
    a listed platform reads its state, an unlisted one ``complete``. With no
    fact (or a ``binary`` section this build cannot read) the blocks keep
    ``unrecorded``. A value that does not decode is refused: reading it as
    complete is the false-clean path the fact exists to close."""
    fact = read_extension_fact(snap, EXPORT_READ)
    if fact.status is FactStatus.NOT_COLLECTED:
        return
    if fact.status is not FactStatus.PRESENT or fact.value is None:
        if _blocked_reason(snap, EXPORT_READ.section) is not None:
            return
        raise SnapshotError(
            "snapshot's export_read extension fact is unreadable: "
            + "; ".join(str(d) for d in fact.diagnostics)
        )
    for platform, meta in _platform_blocks(snap).items():
        meta.export_read = ExportReadState(fact.value.get(platform, "complete"))
