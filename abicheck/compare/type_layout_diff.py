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

    from ..model.dwarf_facts import FieldInfo, StructLayout


def diff_struct_layouts(o: object, n: object) -> list[Change]:
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
    """Added, non-reserved fields indexed by ``byte_offset``.

    A list per offset, not a single value: two renamed bit-fields can
    legitimately share a byte_offset (e.g. two 1-bit flags in the same
    storage byte), and a plain ``dict[int, FieldInfo]`` comprehension would
    silently keep only the last one, leaving every other same-byte bit-field
    with no candidate to match against and falling through to a false
    STRUCT_FIELD_REMOVED (caught in review).
    """
    by_offset: dict[int, list[FieldInfo]] = {}
    for fn in added_names:
        if RESERVED_FIELD.matches(fn):
            continue
        by_offset.setdefault(new_fields[fn].byte_offset, []).append(new_fields[fn])
    return by_offset


def _removed_field_changes(
    name: str, old_fields: dict[str, FieldInfo], new_fields: dict[str, FieldInfo]
) -> list[Change]:
    """Fields gone from the new side: a reserved-field activation, a pure
    rename, or a genuine removal."""
    changes: list[Change] = []
    removed_names = sorted(old_fields.keys() - new_fields.keys())
    added_by_offset = _added_fields_by_offset(
        new_fields.keys() - old_fields.keys(), new_fields
    )
    reserved_matched: set[str] = set()
    # Tracks candidates already consumed by a pure-rename match below, so a
    # second removed field at the same offset (overlapping anonymous-union
    # members) can't also claim it -- otherwise both would report
    # FIELD_RENAMED to the same target, hiding that one was genuinely
    # dropped (caught in review).
    rename_matched: set[str] = set()

    for fname in removed_names:
        old_f = old_fields[fname]
        if RESERVED_FIELD.matches(fname):
            candidate = next(
                (
                    c
                    for c in added_by_offset.get(old_f.byte_offset, [])
                    if c.name not in rename_matched and old_f.type_name == c.type_name
                ),
                None,
            )
            if candidate is not None:
                changes.append(
                    make_change(
                        ChangeKind.USED_RESERVED_FIELD,
                        symbol=name,
                        name=name,
                        old=fname,
                        new=candidate.name,
                    )
                )
                reserved_matched.add(candidate.name)
                continue
        else:
            # Pure rename: same offset, identical type, different name.
            # Report FIELD_RENAMED (API_BREAK) directly instead of a
            # STRUCT_FIELD_REMOVED that would falsely claim the field no
            # longer exists — mirrors the rename-skip for enum members below
            # (ENUM_MEMBER_RENAMED). Doesn't depend on `_diff_field_renames`
            # (a different model, its own type-name strings) also firing for
            # the pair -- relying on that could silently drop the finding
            # when the two extractors spell the type differently (caught in
            # review); the post-processing dedup pass collapses a duplicate.
            # _normalize_type_name is lossy by design (it also strips
            # pointer/reference sigils to compare "struct Foo *" against
            # "Foo" for the *tag-spelling* case), so it alone would equate
            # "Foo *" with "Foo" and even a byte-size guard cannot save
            # it: a same-size pointer-to-inline-value retype (e.g.
            # "Handle *" (8B) -> an 8-byte-by-value "Handle") would still
            # pass a size check while being a real layout/representation
            # break (caught in review — twice). Require exact, non-lossy
            # equality of the raw type spelling instead of any
            # normalized comparison. Spelling alone is still not enough,
            # though: the *same* typedef name can resolve to a different
            # size/bit-layout across versions (e.g. "Word" widened
            # 4B->8B elsewhere, or a bit-field at the same byte offset
            # changing width) without the spelling changing at all, so
            # also require byte_size and bit_offset/bit_size to match
            # (caught in review — three times now). This is strictly
            # narrower than a real rename check would ideally be (a
            # harmless "struct Foo *" vs "Foo *" spelling difference now
            # falls through to STRUCT_FIELD_REMOVED instead of
            # FIELD_RENAMED) but never misclassifies a genuine type or
            # layout change as a bare rename. Scanning the full
            # same-offset candidate list (not just one arbitrary entry)
            # also matters when two bit-fields share a byte_offset —
            # each removed bit-field must find *its own* matching
            # candidate by bit position/width, not whichever one a
            # single-value dict happened to keep (caught in review —
            # four times now). And a matched candidate must be excluded
            # from further matches (`rename_matched`) — otherwise two
            # removed fields with identical layout at the same offset
            # (overlapping anonymous-union members collapsing to one
            # field) would both claim the same new field as their
            # rename target, hiding that one of them was genuinely
            # dropped (caught in review — five times now).
            candidate = next(
                (
                    c
                    for c in added_by_offset.get(old_f.byte_offset, [])
                    if (
                        c.name not in reserved_matched
                        and c.name not in rename_matched
                        and old_f.type_name == c.type_name
                        and old_f.byte_size == c.byte_size
                        and old_f.bit_offset == c.bit_offset
                        and old_f.bit_size == c.bit_size
                    )
                ),
                None,
            )
            if candidate is not None:
                rename_matched.add(candidate.name)
                changes.append(
                    make_change(
                        ChangeKind.FIELD_RENAMED,
                        symbol=name,
                        name=name,
                        old=fname,
                        new=candidate.name,
                    )
                )
                continue
        changes.append(
            make_change(
                ChangeKind.STRUCT_FIELD_REMOVED,
                symbol=f"{name}::{fname}",
                name=name,
                detail=fname,
                old_value=f"{old_fields[fname].type_name}",
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

        if old_f.byte_offset != new_f.byte_offset:
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
        type_size_changed = (
            old_f.byte_size > 0
            and new_f.byte_size > 0
            and old_f.byte_size != new_f.byte_size
        )
        if type_name_changed or type_size_changed:
            changes.append(
                make_change(
                    ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
                    symbol=f"{name}::{fname}",
                    name=name,
                    detail=fname,
                    old=f"{old_f.type_name}({old_f.byte_size}B)",
                    new=f"{new_f.type_name}({new_f.byte_size}B)",
                    old_value=old_f.type_name,
                    new_value=new_f.type_name,
                    field_name=fname,
                )
            )
    return changes


def diff_enum_layouts(o: object, n: object) -> list[Change]:
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
