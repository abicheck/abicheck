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

"""G23 Phase B2 — L1 DWARF vtable-group reconstruction.

Phase B1 (``diff_elf_layout``) recovers multi-inheritance vtable breaks from
``.dynsym`` thunk/VTT symbols alone (L0). This module adds the *L1* view:
reconstructing the per-class vtable-group structure from DWARF inheritance
(``bases`` / ``virtual_bases`` / ``vtable`` on :class:`RecordType`) so we can
name two breaks the per-type field/base diff cannot see, both of which arise
from a change in a *base* rather than in the class itself:

* ``secondary_vtable_group_changed`` — in the Itanium C++ ABI a polymorphic
  non-primary base contributes its own *secondary* vtable group. Whether a
  direct or virtual base owns a secondary group depends on whether that base is
  polymorphic. If a base gains or loses virtual functions, the derived class's
  set of secondary groups changes **even though the derived class's own base
  declaration list is untouched** — a cross-type effect the per-type diff (which
  only compares the unchanged derived class) misses. Guarded so it never fires
  when the derived class's own bases moved (``base_class_position_changed`` /
  ``type_base_changed`` own that case).

* ``virtual_base_offset_changed`` — a pure reorder of virtual bases (same set,
  different order) shifts the virtual-base offset table, so ``this`` adjustments
  baked into old binaries reach the wrong subobject. The existing
  ``base_class_position_changed`` only inspects *non-virtual* bases, so a virtual
  reorder is invisible to it.

Everything is reconstructed from fields already on the snapshot, so no DWARF /
serialization re-plumbing is needed. Every comparison is tri-state guarded:
when a base's polymorphism cannot be determined (its type is absent on that
side — e.g. an evidence-tier downgrade to symbols-only), the reconstruction
returns ``None`` and no finding is emitted, degrading to B1's L0 view rather
than fabricating a break.
"""
from __future__ import annotations

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import make_change
from .model import (
    AbiSnapshot,
    RecordType,
    is_non_abi_surface_type,
    stdlib_namespaces_excluded,
)


def _type_map(snap: AbiSnapshot) -> dict[str, RecordType]:
    return {t.name: t for t in snap.types}


def _is_polymorphic(
    name: str,
    types: dict[str, RecordType],
    memo: dict[str, bool | None],
) -> bool | None:
    """Whether class *name* is polymorphic (owns/inherits a vtable).

    Returns ``None`` when it cannot be determined — the named type is absent
    from *types* (unknown), so its polymorphism (and thus the derived class's
    group structure) is indeterminate and callers must skip the finding.
    """
    if name in memo:
        return memo[name]
    rec = types.get(name)
    if rec is None:
        memo[name] = None
        return None
    if rec.vtable or rec.virtual_bases:
        memo[name] = True
        return True
    # Guard against inheritance cycles (malformed input): assume non-polymorphic
    # while resolving, overwrite below.
    memo[name] = False
    for base in rec.bases:
        sub = _is_polymorphic(base, types, memo)
        if sub is None:
            memo[name] = None
            return None
        if sub:
            memo[name] = True
            return True
    return False


def _secondary_groups(
    rec: RecordType,
    types: dict[str, RecordType],
    memo: dict[str, bool | None],
) -> list[str] | None:
    """Ordered list of base names that own a *secondary* vtable group.

    Itanium C++ ABI: the *primary* base is the first polymorphic direct
    non-virtual base (it shares the derived class's primary vtable). Every other
    polymorphic direct non-virtual base, then every polymorphic virtual base,
    contributes a secondary group in that order. Returns ``None`` if any base's
    polymorphism is indeterminate.
    """
    primary_taken = False
    groups: list[str] = []
    for base in rec.bases:  # direct, non-virtual, in declaration order
        poly = _is_polymorphic(base, types, memo)
        if poly is None:
            return None
        if not poly:
            continue
        if not primary_taken:
            primary_taken = True  # first polymorphic non-virtual base is primary
            continue
        groups.append(base)
    for vbase in rec.virtual_bases:
        poly = _is_polymorphic(vbase, types, memo)
        if poly is None:
            return None
        if poly:
            groups.append(vbase)
    return groups


@registry.detector(
    "vtable_layout",
    requires_support=lambda o, n: (
        not o.elf_only_mode and not n.elf_only_mode and bool(o.types) and bool(n.types),
        "missing DWARF/header type metadata (inheritance)",
    ),
)
def _diff_vtable_layout(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Reconstruct and diff per-class vtable-group structure (B2)."""
    old_types = _type_map(old)
    new_types = _type_map(new)
    old_memo: dict[str, bool | None] = {}
    new_memo: dict[str, bool | None] = {}
    changes: list[Change] = []
    # Keep *all* types in the maps above so polymorphism/base lookups can resolve
    # transitive std:: / anonymous bases, but only *emit* findings for classes on
    # the inspected library's own ABI surface — otherwise a reorder inside a
    # debug-only std:: record would surface as a BREAKING finding for a library
    # that does not own it (mirrors _is_abi_surface_type in diff_types.py).
    exclude_stdlib = stdlib_namespaces_excluded(old, new)

    for name in sorted(old_types.keys() & new_types.keys()):
        if is_non_abi_surface_type(name, exclude_stdlib_namespaces=exclude_stdlib):
            continue
        o, n = old_types[name], new_types[name]

        # ── virtual_base_offset_changed ──────────────────────────────────────
        # A same-set reorder of virtual bases; not covered by the non-virtual
        # base_class_position_changed check.
        if (
            len(o.virtual_bases) > 1
            and set(o.virtual_bases) == set(n.virtual_bases)
            and o.virtual_bases != n.virtual_bases
        ):
            changes.append(
                make_change(
                    ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED,
                    symbol=name,
                    name=name,
                    old=", ".join(o.virtual_bases),
                    new=", ".join(n.virtual_bases),
                )
            )

        # ── secondary_vtable_group_changed ───────────────────────────────────
        # Only when the class's own base declaration lists are unchanged — a
        # moved base is already reported by base_class_position_changed /
        # type_base_changed, so this is reserved for the cross-type case where a
        # base's *polymorphism* changed underneath an otherwise-stable class.
        if o.bases == n.bases and o.virtual_bases == n.virtual_bases:
            og = _secondary_groups(o, old_types, old_memo)
            ng = _secondary_groups(n, new_types, new_memo)
            if og is not None and ng is not None and og != ng:
                changes.append(
                    make_change(
                        ChangeKind.SECONDARY_VTABLE_GROUP_CHANGED,
                        symbol=name,
                        name=name,
                        old=", ".join(og) or "(none)",
                        new=", ".join(ng) or "(none)",
                    )
                )

    return changes
