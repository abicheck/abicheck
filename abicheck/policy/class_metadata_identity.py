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

"""Which declared class a mangled identity names, and whether a header proves it.

Two questions, one owner. A class-metadata symbol (``_ZTV``/``_ZTI``/``_ZTS``/
``_ZTT``) is emitted *for* a class, so a finding on it is about that class and
the class's declaration decides whether it is in contract. This module answers
that for :mod:`abicheck.contract_evaluation` (confirmation) and
:mod:`abicheck.policy.public_surface_closure` (the declared-class evidence it
collects), and holds the one predicate both rely on: a mangled spelling is never
a source-level type name.

Inline namespaces are the reason the answer is not a plain name lookup. clang's
header ``qualified_name`` omits an inline namespace (``v0``) that the mangling
keeps, so a declared class can be spelled two ways. Both spellings are exact
scope paths; neither is matched by tail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..model.owner_recovery import (
    is_mangled_identity,
    itanium_class_metadata_owner,
    itanium_declaring_class_path,
)

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

# ``is_mangled_identity`` is owned by ``model.owner_recovery`` (where the
# mangling grammar lives); it is re-exported here for this module's callers.
__all__ = [
    "class_metadata_owner_confirmed",
    "is_mangled_identity",
    "vtable_slot_class_paths",
]


def class_metadata_owner_confirmed(
    symbol: str,
    exact_identities: set[str],
    class_owner_paths: set[str],
) -> bool:
    """Whether *symbol* is a class-metadata name whose owning class is declared.

    The owner is matched exactly, never by tail: a bare-tail match could let an
    unrelated same-named class confirm the finding. ``exact_identities`` is the
    path-aware, ambiguity-vetoing answer for a class spelled as the header spells
    it. ``class_owner_paths`` is the same class spelled as the mangler spells it,
    inline namespaces included (see :func:`vtable_slot_class_paths`).
    """
    owner = itanium_class_metadata_owner(symbol)
    return owner is not None and (
        owner in exact_identities or owner in class_owner_paths
    )


def vtable_slot_class_paths(snap: AbiSnapshot, exact_identities: set[str]) -> set[str]:
    """Exact class paths a public record's own virtual slots prove declared.

    A polymorphic class's vtable lists its virtual functions as mangled names,
    and each is spelled with its declaring class's full scope path -- inline
    namespaces included, because the mangler writes them. A slot
    ``_ZNK3svs7runtime2v015DynamicIVFIndex4sizeEv`` proves that
    ``svs::runtime::v0::DynamicIVFIndex`` is declared in the parsed headers, even
    when the header's own qualified name (``svs::runtime::DynamicIVFIndex``, which
    clang records without ``v0``) cannot be matched to it by spelling.

    Only slots of a record whose own identity is in *exact_identities* (declared,
    public, and unambiguous) count, so an unreachable or ambiguous record proves
    nothing. A slot's declaring class is declared by construction, and a base
    whose slot a public class inherits is reached through that class's closure.
    Template-specialization scopes are skipped (see
    :func:`~abicheck.model.owner_recovery.itanium_declaring_class_path`).
    Destructor slots are unmangled in the vtable and are skipped too, so a class
    whose only virtual is its destructor is not proven this way.
    """
    paths: set[str] = set()
    for rec in snap.declarations.types:
        identity = rec.qualified_name or rec.name
        if identity not in exact_identities:
            continue
        slots = rec.vtable_fact
        if slots is None or not slots.is_present or slots.value is None:
            continue
        for slot in slots.value:
            if is_mangled_identity(slot):
                path = itanium_declaring_class_path(slot)
                if path is not None:
                    paths.add(path)
    return paths
