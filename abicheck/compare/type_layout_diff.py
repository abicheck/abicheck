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

"""Struct and enum layout diffing from DWARF-tier snapshot metadata.

Moved out of ``diff_platform`` (ADR-061): these are per-type raw-change
identifications over ``StructLayout``/``EnumInfo`` pairs and own no platform
or policy decision. The ``dwarf`` detector in ``diff_platform`` calls them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..diff_helpers import _normalize_type_name, make_change
from ..diff_types_surface import RESERVED_FIELD
from ..model import cv_qualifiers_only_differ
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from .enum_sentinel import is_confirmed_enum_sentinel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Set as AbstractSet

    from ..model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout


def diff_struct_layouts(o: DwarfMetadata, n: DwarfMetadata) -> list[Change]:
    old_structs: dict[str, StructLayout] = getattr(o, "structs", {})
    new_structs: dict[str, StructLayout] = getattr(n, "structs", {})
    changes: list[Change] = []

    for name, old_s in old_structs.items():
        if name not in new_structs:
            continue  # struct removed — caught by header-layer (castxml)
        new_s = new_structs[name]
        old_fields = {f.name: f for f in old_s.fields}
        new_fields = {f.name: f for f in new_s.fields}
        changes += _struct_size_and_alignment_changes(name, old_s, new_s)
        changes += _removed_field_changes(name, old_fields, new_fields)
        changes += _existing_field_changes(name, old_fields, new_fields)
    return changes


def _struct_size_and_alignment_changes(
    name: str, old_s: StructLayout, new_s: StructLayout
) -> list[Change]:
    """Whole-struct size, and alignment when DWARF 5 recorded it on both
    sides."""
    changes: list[Change] = []
    if old_s.byte_size != new_s.byte_size:
        changes.append(
            make_change(
                ChangeKind.STRUCT_SIZE_CHANGED,
                symbol=name,
                name=name,
                old=str(old_s.byte_size),
                new=str(new_s.byte_size),
            )
        )

    if old_s.alignment and new_s.alignment and old_s.alignment != new_s.alignment:
        changes.append(
            make_change(
                ChangeKind.STRUCT_ALIGNMENT_CHANGED,
                symbol=name,
                name=name,
                old=str(old_s.alignment),
                new=str(new_s.alignment),
            )
        )
    return changes


def _added_fields_by_offset(
    added_names: AbstractSet[str], new_fields: dict[str, FieldInfo]
) -> dict[int, list[FieldInfo]]:
    """Added, non-reserved fields indexed by ``byte_offset``, each group sorted
    by name.

    A list per offset, not a single value: two renamed bit-fields can
    legitimately share a byte_offset (e.g. two 1-bit flags in the same
    storage byte), and a plain ``dict[int, FieldInfo]`` comprehension would
    silently keep only the last one. The sort makes the candidate order a
    function of the field names, not of set iteration order, so the pairing
    below cannot depend on how the caller built its dicts.
    """
    by_offset: dict[int, list[FieldInfo]] = {}
    for fn in sorted(added_names):
        if RESERVED_FIELD.matches(fn):
            continue
        offset = new_fields[fn].byte_offset
        if offset is None:  # unknown offset: cannot be paired by offset
            continue
        by_offset.setdefault(offset, []).append(new_fields[fn])
    return by_offset


def _sizes_compatible(old: int | None, new: int | None) -> bool:
    """Rename pairing's size criterion: an unknown size on either side does
    not block a pair; two known sizes must be equal."""
    return old is None or new is None or old == new


def _size_text(size: int | None) -> str:
    return "unknown" if size is None else f"{size}B"


def _candidates_at(
    added_by_offset: dict[int, list[FieldInfo]], offset: int | None
) -> list[FieldInfo]:
    """Added fields at *offset*; none when the removed field's offset is unknown."""
    if offset is None:
        return []
    return added_by_offset.get(offset, [])


def _rename_pairs(
    removed: list[tuple[str, FieldInfo]],
    added_by_offset: dict[int, list[FieldInfo]],
    excluded: set[str],
) -> dict[str, str]:
    """One-to-one pure-rename pairs, as ``{removed name: added name}``.

    A pair needs the same offset, spelling and bit layout; its size must match
    when both sizes are known (an unknown size matches any size). The pairing
    is a maximum bipartite matching, so no pairing is lost that an alternative
    assignment would keep, and it is deterministic:

    * removed fields and candidates are visited in name order, so the result
      depends on the names, never on dict insertion order;
    * the matching is first grown over exact-size edges only, so a known size
      keeps its exact partner; then augmented over the wildcard edges, which
      only adds pairs and never breaks one (Kuhn's augmenting paths);
    * a candidate is used at most once, and *excluded* names are never used.
    """
    left = [(fname, old_f) for fname, old_f in removed if old_f.byte_offset is not None]

    def edges(exact_only: bool) -> dict[str, list[str]]:
        adjacency: dict[str, list[str]] = {}
        for fname, old_f in left:
            targets: list[str] = []
            for c in _candidates_at(added_by_offset, old_f.byte_offset):
                if c.name in excluded:
                    continue
                if not (
                    old_f.type_name == c.type_name
                    and old_f.bit_offset == c.bit_offset
                    and old_f.bit_size == c.bit_size
                ):
                    continue
                if exact_only:
                    exact = (
                        old_f.byte_size is not None and old_f.byte_size == c.byte_size
                    )
                    if not exact:
                        continue
                elif not _sizes_compatible(old_f.byte_size, c.byte_size):
                    continue
                targets.append(c.name)
            adjacency[fname] = targets
        return adjacency

    match_left: dict[str, str] = {}
    match_right: dict[str, str] = {}

    def augment(u: str, adjacency: dict[str, list[str]], seen: set[str]) -> bool:
        for v in adjacency[u]:
            if v in seen:
                continue
            seen.add(v)
            owner = match_right.get(v)
            if owner is None or augment(owner, adjacency, seen):
                match_right[v] = u
                match_left[u] = v
                return True
        return False

    exact_adjacency = edges(exact_only=True)
    for fname, _ in left:
        augment(fname, exact_adjacency, set())
    all_adjacency = edges(exact_only=False)
    for fname, _ in left:
        if fname not in match_left:
            augment(fname, all_adjacency, set())
    return match_left


def _removed_field_changes(
    name: str, old_fields: dict[str, FieldInfo], new_fields: dict[str, FieldInfo]
) -> list[Change]:
    """Fields gone from the new side: a reserved-field activation, a pure
    rename, or a genuine removal.

    Reserved fields are paired first (by offset and spelling, greedily in
    name order). Pure renames are then paired one-to-one over the candidates
    the reserved pass left free, by :func:`_rename_pairs`. Changes are emitted
    in the removed fields' name order.
    """
    removed_names = sorted(old_fields.keys() - new_fields.keys())
    added_by_offset = _added_fields_by_offset(
        new_fields.keys() - old_fields.keys(), new_fields
    )

    reserved_matched: dict[str, str] = {}
    for fname in removed_names:
        old_f = old_fields[fname]
        if not RESERVED_FIELD.matches(fname):
            continue
        candidate = next(
            (
                c
                for c in _candidates_at(added_by_offset, old_f.byte_offset)
                if c.name not in reserved_matched.values()
                and old_f.type_name == c.type_name
            ),
            None,
        )
        if candidate is not None:
            reserved_matched[fname] = candidate.name

    plain_removed = [
        (fname, old_fields[fname])
        for fname in removed_names
        if not RESERVED_FIELD.matches(fname)
    ]
    renames = _rename_pairs(
        plain_removed, added_by_offset, set(reserved_matched.values())
    )

    changes: list[Change] = []
    for fname in removed_names:
        old_f = old_fields[fname]
        if fname in reserved_matched:
            changes.append(
                make_change(
                    ChangeKind.USED_RESERVED_FIELD,
                    symbol=name,
                    name=name,
                    old=fname,
                    new=reserved_matched[fname],
                )
            )
        elif fname in renames:
            # Pure rename: same offset and spelling, different name. Reported
            # as FIELD_RENAMED (API_BREAK) rather than a STRUCT_FIELD_REMOVED
            # that would claim the field no longer exists. The raw type
            # spelling must match exactly: a normalized comparison would equate
            # "Handle *" with "Handle", a real representation change.
            changes.append(
                make_change(
                    ChangeKind.FIELD_RENAMED,
                    symbol=name,
                    name=name,
                    old=fname,
                    new=renames[fname],
                )
            )
        else:
            changes.append(
                make_change(
                    ChangeKind.STRUCT_FIELD_REMOVED,
                    symbol=f"{name}::{fname}",
                    name=name,
                    detail=fname,
                    old_value=f"{old_f.type_name}",
                    field_name=fname,
                )
            )
    return changes


def _existing_field_changes(
    name: str, old_fields: dict[str, FieldInfo], new_fields: dict[str, FieldInfo]
) -> list[Change]:
    """Offset and type drift for fields present on both sides."""
    changes: list[Change] = []
    for fname, old_f in old_fields.items():
        if fname not in new_fields:
            continue
        new_f = new_fields[fname]

        # Fires only when both offsets are known and differ (Lean rule,
        # formal/lean/AbiLean/FieldOffset.lean): unknown is never a change.
        if (
            old_f.byte_offset is not None
            and new_f.byte_offset is not None
            and old_f.byte_offset != new_f.byte_offset
        ):
            changes.append(
                make_change(
                    ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
                    symbol=f"{name}::{fname}",
                    name=name,
                    detail=fname,
                    old=str(old_f.byte_offset),
                    new=str(new_f.byte_offset),
                    field_name=fname,
                )
            )

        # Field type drift:
        # - catches same-size type substitutions (int→float, Foo*→Bar*)
        # - strip "struct "/"class "/"union " prefixes for stable comparison
        # - still includes explicit size drift when known on both sides
        # A pointee/by-value cv-qualifier change (``char *`` ->
        # ``const char *``) keeps the field's size and offset identical, so
        # it is not a binary layout break (ISSUE-30/35/65: libuv
        # ``uv_cpu_info_s::model`` const-pointer churn). A genuine size
        # change is still reported via ``type_size_changed`` below.
        type_name_changed = _normalize_type_name(
            old_f.type_name
        ) != _normalize_type_name(new_f.type_name) and not cv_qualifiers_only_differ(
            old_f.type_name, new_f.type_name
        )
        # Fires only when both sizes are known and differ: an unknown size
        # (None) is never a change, and a real 0 -> N is one.
        type_size_changed = (
            old_f.byte_size is not None
            and new_f.byte_size is not None
            and old_f.byte_size != new_f.byte_size
        )
        if type_name_changed or type_size_changed:
            changes.append(
                make_change(
                    ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
                    symbol=f"{name}::{fname}",
                    name=name,
                    detail=fname,
                    old=f"{old_f.type_name}({_size_text(old_f.byte_size)})",
                    new=f"{new_f.type_name}({_size_text(new_f.byte_size)})",
                    old_value=old_f.type_name,
                    new_value=new_f.type_name,
                    field_name=fname,
                )
            )
    return changes


def diff_enum_layouts(o: DwarfMetadata, n: DwarfMetadata) -> list[Change]:
    from ..model.dwarf_facts import EnumInfo

    old_enums: dict[str, EnumInfo] = getattr(o, "enums", {})
    new_enums: dict[str, EnumInfo] = getattr(n, "enums", {})
    changes: list[Change] = []

    for name, old_e in old_enums.items():
        if name not in new_enums:
            continue

        new_e = new_enums[name]

        # 1. Underlying size change (e.g. int8_t → int32_t)
        if old_e.underlying_byte_size != new_e.underlying_byte_size:
            changes.append(
                make_change(
                    ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED,
                    symbol=name,
                    name=name,
                    old=str(old_e.underlying_byte_size),
                    new=str(new_e.underlying_byte_size),
                )
            )

        # 2. Removed members — skip rename-only removals here.
        # A dedicated rename detector emits ENUM_MEMBER_RENAMED. Here we only
        # report truly removed values. Use one-to-one proof: a removal is a
        # rename candidate only when its value appears in exactly one new-only
        # member (CodeRabbit P1: avoid false suppression with alias-heavy enums).
        _removed_names = {m for m in old_e.members if m not in new_e.members}
        _added_names = {m for m in new_e.members if m not in old_e.members}
        # Build set of removed old-member names whose value uniquely maps to one new name
        _renamed_old: set[str] = set()
        _claimed_new: set[str] = set()
        for _rname in sorted(_removed_names):
            _rval = old_e.members[_rname]
            _candidates = [
                _n
                for _n in _added_names
                if new_e.members[_n] == _rval and _n not in _claimed_new
            ]
            if len(_candidates) == 1:
                _renamed_old.add(_rname)
                _claimed_new.add(_candidates[0])
        for mname in sorted(_removed_names):
            if mname in _renamed_old:
                continue
            old_val = old_e.members[mname]
            changes.append(
                make_change(
                    ChangeKind.ENUM_MEMBER_REMOVED,
                    symbol=f"{name}::{mname}",
                    name=name,
                    detail=mname,
                    old_value=str(old_val),
                )
            )

        # 3. Changed values
        for mname, old_val in old_e.members.items():
            if mname in new_e.members and new_e.members[mname] != old_val:
                kind = (
                    ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED
                    if is_confirmed_enum_sentinel(mname, old_e.members, new_e.members)
                    else ChangeKind.ENUM_MEMBER_VALUE_CHANGED
                )
                changes.append(
                    make_change(
                        kind,
                        symbol=f"{name}::{mname}",
                        description=(
                            f"Enum member value changed: {name}::{mname} "
                            f"({old_val} → {new_e.members[mname]})"
                        ),
                        old_value=str(old_val),
                        new_value=str(new_e.members[mname]),
                    )
                )

    return changes
