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

from ..model.fact import Fact
from .canonical import canonical_form
from .extension_fact_catalog import (
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

__all__ = ["read_extension_fact", "write_extension_fact"]

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
