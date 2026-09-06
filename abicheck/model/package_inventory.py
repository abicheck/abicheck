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

"""ADR-065 D1/D2 (S3): a package's **declared component inventory**.

``abicheck/package.py`` used to answer "what does this package ship?" with a
directory -- so a component the extractor never produced and a component the
package no longer ships were one state, and no package operand could ever
prove the absence D2 requires before an unmatched member becomes a
*removal*. This module is the shape that closes it: the component set a
container **declares**, read from the container's own member table, kept
apart from whatever a later filesystem walk happens to find.

Three distinct answers live here, and collapsing any two of them is the
failure this module exists to prevent:

* **Declared** (:attr:`PackageInventory.components`) -- the container says it
  ships this component. Absence *from this list*, when the inventory is
  :attr:`~PackageInventory.complete`, is the receipt D2's removal finding
  rests on.
* **Complete** (:attr:`PackageInventory.complete`) -- the enumeration covered
  the whole container. An archive whose member table was read end to end is
  complete; a plain directory operand never is (a user may have populated it
  partially, which is exactly the "absent is not removed" case), and neither
  is a container this build cannot enumerate.
* **Unproduced** (:attr:`PackageInventory.unproduced`) -- declared, but not
  present after extraction. That is an *acquisition* failure
  (``AcquisitionState.EXPECTED_NOT_PRODUCED``), never evidence the package
  stopped shipping the component: a complete inventory that names it is
  positive evidence it is still promised.

A leaf ``model`` module -- standard library only -- so ``storage``
(``package.py``'s extractors), ``workflows`` (the release scope builder) and
``policy`` (the support-promise evaluator) can all depend on it without a
cycle. Deriving the canonical member key from a path is deliberately *not*
done here: that rule is ``binary_utils._canonical_library_key``'s, and one
copy of it is the point.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = [
    "PACKAGE_INVENTORY_SCHEMA_VERSION",
    "ComponentKind",
    "PackageComponent",
    "PackageInventory",
    "merge_unproduced",
]

#: Self-contained sub-object version, the convention
#: ``scope_acquisition.SCOPE_ACQUISITION_SCHEMA_VERSION`` uses.
PACKAGE_INVENTORY_SCHEMA_VERSION = "1.0"


class ComponentKind(str, Enum):
    """What a declared component is, as far as the container's own member
    table can tell -- a name-shape judgement, never an opened file."""

    #: A shared library by filename shape (``.so``/``.so.N``/``.dylib``/``.dll``).
    SHARED_LIBRARY = "shared_library"
    #: Declared, enumerated, but not a comparison operand (a header, a
    #: licence file, a static archive). Kept so ``complete`` means "the whole
    #: container was read", not "the shared libraries were".
    OTHER = "other"


@dataclass(frozen=True)
class PackageComponent:
    """One component a container declares it ships."""

    #: The canonical release match key (``binary_utils._canonical_library_key``
    #: of the declared path's basename), so an inventory joins against the
    #: release fan-out's own maps without a second naming rule.
    member: str
    #: The container-relative path exactly as the container declared it.
    path: str
    kind: ComponentKind = ComponentKind.SHARED_LIBRARY

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of this value."""
        return {"member": self.member, "path": self.path, "kind": self.kind.value}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackageComponent:
        """Parse the JSON shape :meth:`to_dict` produces."""
        return cls(
            member=str(data["member"]),
            path=str(data.get("path", "")),
            kind=ComponentKind(data.get("kind", ComponentKind.SHARED_LIBRARY.value)),
        )


@dataclass(frozen=True)
class PackageInventory:
    """One side's declared component inventory (see the module docstring).

    *complete* is the only field D2 reads as a completeness proof, and it is
    never inferred from the container's *type*: an unreadable member table,
    a format this build cannot enumerate, and a plain directory all produce
    ``complete=False`` with a *provenance* saying so, exactly as ADR-065 S2
    already requires of a stored package whose capture made no
    ``inventory_complete`` assertion.
    """

    components: tuple[PackageComponent, ...] = ()
    complete: bool = False
    #: Human-readable "where this inventory came from, and why it does (or
    #: does not) prove completeness" -- carried verbatim into
    #: ``SideInventory.provenance``, which is what a reviewer reads.
    provenance: str = ""
    #: Declared members that were not present after extraction, mapped to
    #: why. An acquisition failure, never an absence (see the docstring).
    unproduced: Mapping[str, str] = field(default_factory=dict)
    schema_version: str = PACKAGE_INVENTORY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        """Reject an inventory declaring the same member twice -- two
        container entries canonicalizing to one release key (``libfoo.so.1``
        beside ``libfoo.so.2``) is an ambiguity (D3), not a component this
        list may silently name once."""
        seen: set[str] = set()
        for c in self.components:
            if c.member in seen:
                raise ValueError(
                    f"package inventory declares member {c.member!r} twice -- "
                    "two container entries canonicalize to one release key"
                )
            seen.add(c.member)

    @property
    def library_components(self) -> tuple[PackageComponent, ...]:
        """Only the declared components that are shared libraries."""
        return tuple(
            c for c in self.components if c.kind is ComponentKind.SHARED_LIBRARY
        )

    @property
    def members(self) -> frozenset[str]:
        """Every declared shared-library member key."""
        return frozenset(c.member for c in self.library_components)

    def declares(self, member: str) -> bool:
        """Whether *member* is a declared shared-library component."""
        return member in self.members

    def to_dict(self) -> dict[str, Any]:
        """The JSON shape of this value."""
        return {
            "schema_version": self.schema_version,
            "complete": self.complete,
            "provenance": self.provenance,
            "components": [c.to_dict() for c in self.components],
            "unproduced": dict(self.unproduced),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> PackageInventory:
        """Parse the JSON shape :meth:`to_dict` produces. ``complete`` must
        be a real boolean when present: coercing a truthy string would let a
        malformed document manufacture the completeness proof a removal
        finding rests on."""
        complete = data.get("complete", False)
        if not isinstance(complete, bool):
            raise ValueError(
                "package inventory: 'complete' must be a boolean, got "
                f"{type(complete).__name__}"
            )
        raw = data.get("components", [])
        if not isinstance(raw, list):
            raise ValueError("package inventory: 'components' must be a list")
        return cls(
            components=tuple(PackageComponent.from_dict(c) for c in raw),
            complete=complete,
            provenance=str(data.get("provenance", "")),
            unproduced={
                str(k): str(v) for k, v in dict(data.get("unproduced", {})).items()
            },
            schema_version=str(
                data.get("schema_version", PACKAGE_INVENTORY_SCHEMA_VERSION)
            ),
        )


def merge_unproduced(
    inventory: PackageInventory | None, produced: Iterable[str]
) -> PackageInventory | None:
    """*inventory* with :attr:`~PackageInventory.unproduced` recomputed
    against the members actually *produced* on disk.

    The extractor declares; the discovery pass produces. A declared
    shared-library member missing from *produced* is recorded here as
    unproduced, which is what makes it ``EXPECTED_NOT_PRODUCED`` on the
    acquisition record instead of vanishing -- the exact state ADR-065 S2
    reserved and left without a producer. ``None`` passes through unchanged
    (no inventory, nothing to reconcile).
    """
    if inventory is None:
        return None
    have = set(produced)
    missing = {
        c.member: (
            f"declared by the package inventory as {c.path!r} but not present "
            "after extraction (ADR-065 D1: expected, not produced)"
        )
        for c in inventory.library_components
        if c.member not in have
    }
    if missing == dict(inventory.unproduced):
        return inventory
    return PackageInventory(
        components=inventory.components,
        complete=inventory.complete,
        provenance=inventory.provenance,
        unproduced=missing,
        schema_version=inventory.schema_version,
    )
