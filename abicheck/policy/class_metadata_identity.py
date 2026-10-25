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

from ..model.mangled_name import itanium_scope_components_with_template_positions
from ..model.owner_recovery import (
    is_mangled_identity,
    itanium_class_metadata_owner,
    itanium_special_name_owner_scope_components,
)

if TYPE_CHECKING:
    from ..model.declarations import Function
    from ..model.entities import RecordType
    from ..model.snapshot import AbiSnapshot

# ``is_mangled_identity`` is owned by ``model.owner_recovery`` (where the
# mangling grammar lives); it is re-exported here for this module's callers.
__all__ = [
    "class_metadata_owner_confirmed",
    "class_metadata_owner_scope",
    "declared_class_owner_scopes",
    "is_mangled_identity",
]

#: A class's scope as the mangler writes it: one raw component per nesting
#: level, inline namespaces included and a specialization's template-argument
#: text (``BoxIilE``) kept verbatim. Two encodings with the same components name
#: the same class: the class prefix is the first thing either encoding writes,
#: so its substitution numbering is identical in a ``_ZTV`` operand and in a
#: member function's nested name.
MangledClassScope = tuple[str, ...]

#: The component the scope parser emits for an ``operator=`` leaf (``aS``).
_COPY_ASSIGN_LEAF = "{op:aS}"


def class_metadata_owner_scope(symbol: str) -> MangledClassScope | None:
    """The raw mangled scope of the class a class-metadata symbol describes.

    ``_ZTVN3api2v13BoxIilEE`` is ``("api", "v1", "BoxIilE")``. Unlike
    :func:`~abicheck.model.owner_recovery.itanium_class_metadata_owner`, a
    template specialization keeps its argument text, so the scope names the
    specialization itself and never its primary template.
    """
    scope = itanium_special_name_owner_scope_components(symbol)
    if scope is None or not scope[0]:
        return None
    return tuple(scope[0])


def class_metadata_owner_confirmed(
    symbol: str,
    exact_identities: set[str],
    class_owner_scopes: set[MangledClassScope],
) -> bool:
    """Whether *symbol* is a class-metadata name whose owning class is declared.

    The owner is matched exactly, never by tail: a bare-tail match could let an
    unrelated same-named class confirm the finding. ``exact_identities`` is the
    path-aware, ambiguity-vetoing answer for a class spelled as the header spells
    it (non-template owners only). ``class_owner_scopes`` is the same class
    spelled as the mangler spells it -- inline namespaces and specialization
    arguments included (see :func:`declared_class_owner_scopes`).
    """
    owner = itanium_class_metadata_owner(symbol)
    if owner is not None and owner in exact_identities:
        return True
    scope = class_metadata_owner_scope(symbol)
    return scope is not None and scope in class_owner_scopes


def _declaring_scope(mangled: str) -> tuple[MangledClassScope, str] | None:
    """A mangled member's declaring-class scope and its own leaf component."""
    if not is_mangled_identity(mangled):
        return None
    parsed = itanium_scope_components_with_template_positions(mangled)
    if parsed is None or len(parsed[0]) < 2:
        return None
    components = parsed[0]
    return tuple(components[:-1]), components[-1]


def _implicit_copy_assign_owner(func: Function) -> str | None:
    """The header identity of the class an implicit ``operator=`` belongs to.

    A compiler-generated copy/move assignment operator is always
    ``X& X::operator=(const X&)`` / ``(X&&)``: its one parameter's type is its
    own class. That parameter's identity is the header's spelling of the class,
    while the function's mangled name spells the same class as the mangler
    does, so the pair links the two spellings without matching names. A
    user-declared ``operator=`` may take any type and proves nothing.
    """
    if func.is_compiler_generated is not True or func.name != "operator=":
        return None
    if len(func.params) != 1:
        return None
    fact = func.params[0].type_identities_fact
    if fact is None or not fact.is_present or fact.value is None:
        return None
    if len(fact.value) != 1:
        return None
    return fact.value[0]


def declared_class_owner_scopes(
    snap: AbiSnapshot, exact_identities: set[str]
) -> set[MangledClassScope]:
    """Mangled scopes of classes a public record's own members prove declared.

    Two header facts carry a class's mangled spelling:

    - a polymorphic record's virtual slots, each mangled with its declaring
      class's full scope (``_ZNK3svs7runtime2v015DynamicIVFIndex4sizeEv`` proves
      ``svs::runtime::v0::DynamicIVFIndex``, which clang's qualified name spells
      without ``v0``; ``_ZN3api2v13BoxIilE3getEv`` proves the specialization
      ``api::v1::Box<int, long>``). A slot inherited from a base proves that
      base, which the public class's closure reaches.
    - the record's implicit copy/move assignment operator, whose parameter is
      the record itself (:func:`_implicit_copy_assign_owner`). This is what
      proves a class whose only virtual is its destructor: destructor slots are
      unmangled in the vtable.

    Only records whose own identity is in *exact_identities* (declared, public,
    and unambiguous) count, and never a primary template's pattern record: a
    pattern is not evidence about any of its specializations.
    """
    records: dict[str, RecordType] = {}
    for rec in snap.declarations.types:
        identity = rec.qualified_name or rec.name
        if identity in exact_identities and not rec.is_template_pattern:
            records[identity] = rec

    scopes: set[MangledClassScope] = set()
    for rec in records.values():
        slots = rec.vtable_fact
        if slots is None or not slots.is_present or slots.value is None:
            continue
        for slot in slots.value:
            declared = _declaring_scope(slot)
            if declared is not None:
                scopes.add(declared[0])
    for func in snap.declarations.functions:
        owner = _implicit_copy_assign_owner(func)
        if owner is None or owner not in records:
            continue
        declared = _declaring_scope(func.mangled)
        if declared is not None and declared[1] == _COPY_ASSIGN_LEAF:
            scopes.add(declared[0])
    return scopes
