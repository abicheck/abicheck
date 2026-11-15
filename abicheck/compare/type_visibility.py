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

"""``TYPE_VISIBILITY_CHANGED``: a C++ class's own visibility narrowed.

A class's visibility (``__attribute__((visibility(...)))`` on the class, or
``-fvisibility=hidden`` with only its members marked ``default``) decides
whether the library exports the class's *own* symbols: its typeinfo
(``_ZTI``), typeinfo name (``_ZTS``), vtable (``_ZTV``) and VTT (``_ZTT``).
Members can stay exported one by one while those symbols go hidden, and then
a consumer that throws, catches, ``dynamic_cast``\\s or ``typeid``\\s the class
across the library boundary, or constructs it from an inline constructor,
no longer binds to the library's copy.

The evidence is the dynamic symbol table, read per class:

* the old library exported the class's typeinfo/vtable family with default
  visibility, and the new library exports none of it (a hidden symbol never
  reaches ``.dynsym``) or exports it with a narrower visibility;
* the class is still there: the new library still exports one of its member
  symbols (so this is not a removed class, whose removals report themselves),
  and, when declarations are available, its new record is still polymorphic
  (a class that lost its last virtual loses its vtable for that reason, which
  ``TYPE_VTABLE_CHANGED`` reports).

The per-symbol ``VAR_REMOVED_ELF_ONLY`` findings stay: they are the observed
removals; this finding names the type-level cause. A widening (hidden to
default) adds exports and is not reported as this breaking kind.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..demangle import demangle
from ..detector_registry import registry
from ..diff_helpers import make_change
from ..model.availability import FactStatus
from ..model.change_catalog.kinds import ChangeKind

if TYPE_CHECKING:
    from ..model import AbiSnapshot
    from ..model.change import Change

__all__ = ["type_visibility_changes"]

#: Itanium special names a class owns: typeinfo, typeinfo name, vtable, VTT.
_CLASS_SYMBOL_PREFIXES = ("_ZTI", "_ZTS", "_ZTV", "_ZTT")
_EXPORTED_VISIBILITIES = ("default", "protected")


def _class_families(snapshot: AbiSnapshot) -> dict[str, dict[str, str]]:
    """Mangled class tail -> {special symbol: visibility} for exported symbols."""
    families: dict[str, dict[str, str]] = {}
    elf = snapshot.elf
    if elf is None:
        return families
    for sym in elf.symbols:
        name = sym.name
        if name[:4] not in _CLASS_SYMBOL_PREFIXES or len(name) <= 4:
            continue
        visibility = str(sym.visibility or "default")
        if visibility not in _EXPORTED_VISIBILITIES:
            continue
        families.setdefault(name[4:], {})[name] = visibility
    return families


def _member_prefixes(tail: str) -> tuple[str, ...] | None:
    """Mangled prefixes of the class's member symbols, or None when the class
    name is not a plain (non-substituted) Itanium name we can scope by."""
    if tail.startswith("N") and tail.endswith("E"):
        inner = tail[1:-1]
    else:
        inner = tail
    if not inner or not inner[0].isdigit():
        return None
    return (f"_ZN{inner}", f"_ZNK{inner}")


def _exports_member(names: set[str], prefixes: tuple[str, ...]) -> bool:
    return any(n.startswith(prefixes) for n in names)


def _summary(visibilities: dict[str, str]) -> str:
    return "default" if "default" in visibilities.values() else "protected"


def _record_lost_vtable(snapshot: AbiSnapshot, type_name: str) -> bool:
    """True when the snapshot declares *type_name* without any vtable entry."""
    for record in snapshot.declarations.types:
        if (record.qualified_name or record.name) != type_name:
            continue
        fact = record.vtable_fact
        if fact is not None and fact.status is FactStatus.PRESENT and not fact.value:
            return True
    return False


def type_visibility_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """One ``TYPE_VISIBILITY_CHANGED`` per class whose own symbols narrowed."""
    if old.elf is None or new.elf is None:
        return []
    old_families = _class_families(old)
    if not old_families:
        return []
    new_families = _class_families(new)
    old_names = {s.name for s in old.elf.symbols}
    new_names = {s.name for s in new.elf.symbols}
    changes: list[Change] = []
    for tail, old_vis in sorted(old_families.items()):
        old_level = _summary(old_vis)
        new_vis = new_families.get(tail)
        new_level = "hidden" if not new_vis else _summary(new_vis)
        if old_level == new_level or new_level == "default":
            continue
        prefixes = _member_prefixes(tail)
        if prefixes is None:
            continue
        if not (
            _exports_member(old_names, prefixes)
            and _exports_member(new_names, prefixes)
        ):
            continue
        demangled = demangle(f"_ZTI{tail}") or ""
        type_name = demangled.removeprefix("typeinfo for ") or tail
        if _record_lost_vtable(new, type_name):
            continue
        lost = sorted(set(old_vis) - set(new_vis or {}))
        changes.append(
            make_change(
                ChangeKind.TYPE_VISIBILITY_CHANGED,
                symbol=type_name,
                name=type_name,
                description=(
                    f"Type visibility narrowed: {type_name} ({old_level} → "
                    f"{new_level}); its members stay exported but its "
                    f"typeinfo/vtable symbols {'are no longer exported' if lost else 'changed visibility'}"
                    + (f" ({', '.join(lost)})" if lost else "")
                ),
                old_value=old_level,
                new_value=new_level,
                affected_symbols=lost or sorted(old_vis),
            )
        )
    return changes


@registry.detector("type_visibility")
def _diff_type_visibility(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """``TYPE_VISIBILITY_CHANGED`` -- see the module docstring."""
    return type_visibility_changes(old, new)
