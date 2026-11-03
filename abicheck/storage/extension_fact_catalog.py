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

"""ADR-062 D19: the declared catalog of optional extension facts.

A new optional fact -- one whose absence an older reader already reads as
"not collected" -- is stored as a named entry of its section header's D14
``extensions`` map, not as a new top-level field behind a global
``SCHEMA_VERSION`` bump. Each such fact is declared here once, as an
:class:`ExtensionFact`, with the semantics an *older* reader gets when it
meets the fact without knowing it (:class:`ReaderSemantics`):

* ``OPTIONAL`` -- the older reader keeps the value verbatim and reads the
  fact as not collected. Its result stays right, only less informed.
* ``SECTION_MUST_UNDERSTAND`` -- ignoring the value would make an older
  reader's result *wrong* for the analyses reading the fact's section, so
  the writer also names the fact in that section header's
  ``must_understand`` list: the older reader blocks exactly that section's
  readers (``not comparable``) and compares everything else.
* ``DOCUMENT_MUST_UNDERSTAND`` -- ignoring it would make every analysis
  wrong, so the name goes on the document-level ``must_understand`` list
  and the older reader refuses the document.

``must_understand_when`` narrows a must-understand fact to the values that
need it (ADR-079's ``complete`` export read needs no understanding; a
``partial`` one does), so the common case costs an older reader nothing.

This build *understands* exactly the names in :data:`EXTENSION_FACTS`
(`wire_extensions.UNDERSTOOD_EXTENSIONS` is derived from it). The catalog
is a static tuple in this module, never filled by registration calls from
elsewhere: what a reader understands must not depend on import order.

Writing and reading a fact on a snapshot is `extension_facts.py`. The
global-bump side of the rule is `snapshot_schema_versions.SCHEMA_BUMP_REASONS`,
which `scripts/schema_bump_gate.py` checks.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, TypeVar

from .dto import SECTION_SCHEMA_VERSIONS

__all__ = [
    "EXTENSION_FACTS",
    "ExtensionFact",
    "ExtensionFactRegistry",
    "ReaderSemantics",
]

T = TypeVar("T")

#: ``<namespace>.<name>``: lowercase dotted, at least one dot. A section kind
#: never contains a dot, so a fact name can never be read as one -- the two
#: share the ``must_understand`` namespace (D14).
_NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_OWNER = re.compile(r"^ADR-\d{3}\b")
_IDENTITY_SECTION = "provenance"


class ReaderSemantics(StrEnum):
    """What a reader that does not know the fact does when it meets it."""

    OPTIONAL = "optional"
    SECTION_MUST_UNDERSTAND = "section_must_understand"
    DOCUMENT_MUST_UNDERSTAND = "document_must_understand"


@dataclass(frozen=True, eq=False)
class ExtensionFact(Generic[T]):
    """One optional fact stored in its section header's ``extensions`` map.

    *encode* turns the runtime value into canonical JSON (D5); *decode* is
    its inverse and raises ``TypeError``/``ValueError`` on a malformed
    stored value. *absent_reads_as* states, for a reviewer, what a reader
    concludes when the fact is missing -- the claim that makes the fact
    extension-eligible. *owner* names the deciding ADR.
    """

    name: str
    section: str
    semantics: ReaderSemantics
    absent_reads_as: str
    owner: str
    encode: Callable[[T], Any]
    decode: Callable[[Any], T]
    must_understand_when: Callable[[T], bool] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME.match(self.name):
            raise ValueError(
                f"extension fact name {self.name!r} must be a lowercase dotted "
                "'<namespace>.<name>' (it shares must_understand with section kinds)"
            )
        if (
            not isinstance(self.section, str)
            or self.section not in SECTION_SCHEMA_VERSIONS
        ):
            raise ValueError(
                f"extension fact {self.name!r}: unknown section {self.section!r}"
            )
        if not isinstance(self.semantics, ReaderSemantics):
            raise TypeError(
                f"extension fact {self.name!r}: semantics must be a ReaderSemantics"
            )
        if (
            self.semantics is ReaderSemantics.SECTION_MUST_UNDERSTAND
            and self.section == _IDENTITY_SECTION
        ):
            raise ValueError(
                f"extension fact {self.name!r}: blocking the identity section "
                "refuses the document; declare DOCUMENT_MUST_UNDERSTAND instead"
            )
        if (
            not isinstance(self.absent_reads_as, str)
            or not self.absent_reads_as.strip()
        ):
            raise ValueError(
                f"extension fact {self.name!r} must state what its absence reads as"
            )
        if not isinstance(self.owner, str) or not _OWNER.match(self.owner):
            raise ValueError(
                f"extension fact {self.name!r}: owner {self.owner!r} must name an ADR"
            )
        if not callable(self.encode) or not callable(self.decode):
            raise TypeError(
                f"extension fact {self.name!r}: encode/decode must be callable"
            )
        if self.must_understand_when is not None:
            if self.semantics is ReaderSemantics.OPTIONAL:
                raise ValueError(
                    f"extension fact {self.name!r}: must_understand_when needs "
                    "a must-understand semantics"
                )
            if not callable(self.must_understand_when):
                raise TypeError(
                    f"extension fact {self.name!r}: must_understand_when must be callable"
                )

    def needs_understanding(self, value: T) -> bool:
        """Whether writing *value* must name this fact in ``must_understand``."""
        if self.semantics is ReaderSemantics.OPTIONAL:
            return False
        if self.must_understand_when is None:
            return True
        decision = self.must_understand_when(value)
        if not isinstance(decision, bool):
            raise TypeError(
                f"extension fact {self.name!r}: must_understand_when returned "
                f"{type(decision).__name__}, not bool"
            )
        return decision


class ExtensionFactRegistry:
    """An immutable set of :class:`ExtensionFact` declarations keyed by name."""

    __slots__ = ("_facts",)

    def __init__(self, facts: Iterable[ExtensionFact[Any]] = ()) -> None:
        if isinstance(facts, (str, bytes)) or not isinstance(facts, Iterable):
            raise TypeError("an extension fact registry takes an iterable of facts")
        table: dict[str, ExtensionFact[Any]] = {}
        for fact in facts:
            if not isinstance(fact, ExtensionFact):
                raise TypeError(f"not an ExtensionFact: {fact!r}")
            if fact.name in table:
                raise ValueError(f"extension fact {fact.name!r} is declared twice")
            table[fact.name] = fact
        self._facts = table

    def __contains__(self, fact: object) -> bool:
        return isinstance(fact, ExtensionFact) and self._facts.get(fact.name) is fact

    def __iter__(self) -> Iterator[ExtensionFact[Any]]:
        return iter(self._facts[name] for name in sorted(self._facts))

    def __len__(self) -> int:
        return len(self._facts)

    def names(self) -> frozenset[str]:
        return frozenset(self._facts)

    def extended(self, *facts: ExtensionFact[Any]) -> ExtensionFactRegistry:
        """A registry holding this one's facts plus *facts* (a newer build's)."""
        return ExtensionFactRegistry((*self._facts.values(), *facts))


#: This build's optional extension facts. Empty: the rule (ADR-062 D19)
#: applies going forward. Add a fact by declaring it as a module constant
#: above and listing it here.
EXTENSION_FACTS = ExtensionFactRegistry(())
