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
    CU_STACK_PROTECTOR,
    CXX_CONCEPT_CONSTRAINTS,
    CXX_CONCEPT_HEADER_SCAN,
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
    "apply_model_extension_facts",
    "read_extension_fact",
    "sync_export_read_fact",
    "sync_model_extension_facts",
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


def sync_model_extension_facts(snap: Any) -> None:
    """Record every model field this build persists as an extension fact:
    ADR-079's ``export_read`` and ``cxx_concept_header_scan``. Each write
    path and the evidence digest call this first; it is idempotent."""
    sync_export_read_fact(snap)
    scan = getattr(snap, "cxx_concept_header_scan", None)
    if _blocked_reason(snap, CXX_CONCEPT_HEADER_SCAN.section) is not None:
        return
    if isinstance(scan, bool):
        write_extension_fact(snap, CXX_CONCEPT_HEADER_SCAN, scan)
    elif scan is None:
        _drop_extension_fact(snap, CXX_CONCEPT_HEADER_SCAN)
    table = getattr(snap, "cxx_concept_constraints", None)
    if isinstance(table, dict):
        write_extension_fact(snap, CXX_CONCEPT_CONSTRAINTS, table)
    elif table is None:
        _drop_extension_fact(snap, CXX_CONCEPT_CONSTRAINTS)
    census = getattr(snap, "cu_stack_protector", None)
    if isinstance(census, dict) and census:
        write_extension_fact(snap, CU_STACK_PROTECTOR, census)
    elif not census:
        # None or {}: no unit is claimed, so a stale stored census must go.
        _drop_extension_fact(snap, CU_STACK_PROTECTOR)


def _drop_extension_fact(snap: Any, fact: ExtensionFact[Any]) -> None:
    """Remove *fact* from *snap*'s carried extensions (a model field reset to
    ``None`` claims no value, so a stale stored one must not be re-saved).
    Other facts and their ``must_understand`` entries are left alone."""
    ext = extensions_of(snap)
    if ext is None:
        return
    extras = ext.header_extras.get(fact.section)
    if not extras:
        return
    stored = dict(extras.get(EXTENSIONS_KEY, {}))
    if stored.pop(fact.name, None) is None and fact.name not in extras.get(
        MUST_UNDERSTAND_KEY, ()
    ):
        return
    if stored:
        extras[EXTENSIONS_KEY] = stored
    else:
        extras.pop(EXTENSIONS_KEY, None)
    names = [n for n in extras.get(MUST_UNDERSTAND_KEY, ()) if n != fact.name]
    if names:
        extras[MUST_UNDERSTAND_KEY] = names
    else:
        extras.pop(MUST_UNDERSTAND_KEY, None)
    ext.document_must_understand = [
        n for n in ext.document_must_understand if n != fact.name
    ]
    if not extras:
        ext.header_extras.pop(fact.section, None)


def apply_model_extension_facts(snap: Any) -> None:
    """The inverse of :func:`sync_model_extension_facts` on load. A missing
    or blocked concept scan leaves the field ``None`` (no scan), which names
    no gap -- what every older snapshot reads as. A stored value that does
    not decode is refused rather than read as that no-gap answer."""
    apply_export_read_fact(snap)
    _apply_concept_constraints(snap)
    _apply_stack_protector_census(snap)
    scan = read_extension_fact(snap, CXX_CONCEPT_HEADER_SCAN)
    if scan.status is FactStatus.PRESENT and isinstance(scan.value, bool):
        snap.cxx_concept_header_scan = scan.value
        return
    if scan.status is FactStatus.NOT_COLLECTED:
        return
    if _blocked_reason(snap, CXX_CONCEPT_HEADER_SCAN.section) is not None:
        return
    # A stored value that does not decode would read as "no scan" -- the
    # same no-gap answer as a clean run. Refuse it, as for export_read.
    raise SnapshotError(
        "snapshot's cxx_concept_header_scan extension fact is unreadable: "
        + "; ".join(str(d) for d in scan.diagnostics)
    )


def _apply_concept_constraints(snap: Any) -> None:
    """Restore ``cxx_concept_constraints``. Missing or blocked leaves it
    ``None`` (not modelled: the gap stays named); a stored value that does not
    decode is refused rather than read as "not modelled"."""
    table = read_extension_fact(snap, CXX_CONCEPT_CONSTRAINTS)
    if table.status is FactStatus.PRESENT and isinstance(table.value, dict):
        snap.cxx_concept_constraints = table.value
        return
    if table.status is FactStatus.NOT_COLLECTED:
        return
    if _blocked_reason(snap, CXX_CONCEPT_CONSTRAINTS.section) is not None:
        return
    raise SnapshotError(
        "snapshot's cxx_concept_constraints extension fact is unreadable: "
        + "; ".join(str(d) for d in table.diagnostics)
    )


def _apply_stack_protector_census(snap: Any) -> None:
    """Restore ``cu_stack_protector``. Missing or blocked leaves it ``None``
    (no unit read: a lost canary's cause stays unknown); a stored value that
    does not decode is refused rather than read as that unknown."""
    census = read_extension_fact(snap, CU_STACK_PROTECTOR)
    if census.status is FactStatus.PRESENT and isinstance(census.value, dict):
        snap.cu_stack_protector = census.value
        return
    if census.status is FactStatus.NOT_COLLECTED:
        return
    if _blocked_reason(snap, CU_STACK_PROTECTOR.section) is not None:
        return
    raise SnapshotError(
        "snapshot's cu_stack_protector extension fact is unreadable: "
        + "; ".join(str(d) for d in census.diagnostics)
    )
