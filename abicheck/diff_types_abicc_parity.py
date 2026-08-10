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

"""ABICC full-parity type/variable detectors: global data value changes,
struct/union kind transitions, reserved-field use, and removed const
overloads (``Global_Data_Value_Changed``/``StructToUnion``/
``Used_Reserved_Field``/``Removed_Const_Overload`` in ABICC's own vocabulary).

Split out of ``diff_types.py`` to stay under its line-count cap — a genuine
leaf module (must not import from ``diff_types`` at all, to avoid an import
cycle: ``diff_types.py`` imports these detectors back for registration).
``_is_abi_surface_type``/``_directly_referenced``/``_RESERVED_FIELD_RE``
were originally private to ``diff_types.py``; an earlier version of this
split imported them back function-locally, which the AI-readiness
import-cycle-growth check correctly flagged as a real cycle in the static
import graph even though it never deadlocks at runtime — fixed by promoting
them to their own leaf module, ``diff_types_surface.py``, that both
``diff_types.py`` and this module import at the ordinary top level.
"""

from __future__ import annotations

from collections import defaultdict

from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .diff_helpers import (
    build_type_map as _build_type_map,
    lookup_matched_type as _lookup_matched_type,
    make_change,
)
from .diff_symbols import _PUBLIC_VIS, _public_variables
from .diff_types_surface import (
    _RESERVED_FIELD_RE,
    _directly_referenced,
    _is_abi_surface_type,
)
from .model import AbiSnapshot, Function, TypeField, stdlib_namespaces_excluded


@registry.detector("var_values")
def _diff_var_values(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect global data value changes (ABICC: Global_Data_Value_Changed).

    When a global const variable's initial value changes, old binaries may
    use stale compile-time-inlined values (constant propagation).
    """
    changes: list[Change] = []
    old_map = _public_variables(old)
    new_map = _public_variables(new)

    for mangled, v_old in old_map.items():
        v_new = new_map.get(mangled)
        if v_new is None:
            continue
        if (
            v_old.value is not None
            and v_new.value is not None
            and v_old.value != v_new.value
        ):
            changes.append(
                make_change(
                    ChangeKind.VAR_VALUE_CHANGED,
                    symbol=mangled,
                    name=v_old.name,
                    old=repr(v_old.value),
                    new=repr(v_new.value),
                    old_value=v_old.value,
                    new_value=v_new.value,
                )
            )
    return changes


@registry.detector("type_kind_changes")
def _diff_type_kind_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect struct↔union kind changes (ABICC: StructToUnion / DataType_Type)."""
    changes: list[Change] = []
    excl = stdlib_namespaces_excluded(old, new)
    directly_referenced = _directly_referenced(old, new)
    old_map = _build_type_map(
        t
        for t in old.types
        if _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )
    new_map = _build_type_map(
        t
        for t in new.types
        if _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )

    for t_old in old_map.values():
        t_new = _lookup_matched_type(old_map, new_map, t_old)
        if t_new is None:
            continue
        # Bare, not the qualified matching key — see _diff_types's comment.
        name = t_old.name
        if t_old.kind != t_new.kind:
            # Union-involving transitions are binary-breaking (layout changes);
            # struct↔class transitions are source-level only (identical ABI).
            union_involved = t_old.kind == "union" or t_new.kind == "union"
            ck = (
                ChangeKind.TYPE_KIND_CHANGED
                if union_involved
                else ChangeKind.SOURCE_LEVEL_KIND_CHANGED
            )
            changes.append(
                make_change(
                    ck,
                    symbol=name,
                    name=name,
                    old=t_old.kind,
                    new=t_new.kind,
                )
            )
    return changes


@registry.detector("reserved_fields")
def _diff_reserved_fields(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect reserved fields put into use (ABICC: Used_Reserved_Field).

    NOTE: Primary detection is now integrated into _diff_type_fields() which
    suppresses TYPE_FIELD_REMOVED + TYPE_FIELD_ADDED for reserved-field renames.
    This standalone detector is kept for backward compatibility but now requires
    both offset AND type match to avoid false positives (M5 fix).
    """
    changes: list[Change] = []
    excl = stdlib_namespaces_excluded(old, new)
    directly_referenced = _directly_referenced(old, new)
    old_map = _build_type_map(
        t
        for t in old.types
        if not t.is_union
        and _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )
    new_map = _build_type_map(
        t
        for t in new.types
        if not t.is_union
        and _is_abi_surface_type(
            t, exclude_stdlib=excl, directly_referenced=directly_referenced
        )
    )

    for t_old in old_map.values():
        t_new = _lookup_matched_type(old_map, new_map, t_old)
        if t_new is None or t_new.is_opaque:
            continue
        # Bare, not the qualified matching key — see _diff_types's comment.
        name = t_old.name

        old_names = {f.name for f in t_old.fields}
        new_names = {f.name for f in t_new.fields}

        removed = [
            f
            for f in t_old.fields
            if f.name not in new_names and _RESERVED_FIELD_RE.match(f.name)
        ]
        added = [
            f
            for f in t_new.fields
            if f.name not in old_names and not _RESERVED_FIELD_RE.match(f.name)
        ]

        added_by_offset = {f.offset_bits: f for f in added if f.offset_bits is not None}
        # Fallback index by type for DWARF-only mode (no offsets)
        added_by_type: dict[str, list[TypeField]] = {}
        for f in added:
            added_by_type.setdefault(f.type, []).append(f)
        matched: set[str] = set()
        for f_old in removed:
            candidate = None
            # Primary: match by offset + type
            if f_old.offset_bits is not None:
                c = added_by_offset.get(f_old.offset_bits)
                if c is not None and f_old.type == c.type:
                    candidate = c
            # Fallback: match by type when offsets unavailable (DWARF-only)
            if candidate is None and f_old.offset_bits is None:
                for c in added_by_type.get(f_old.type, []):
                    if c.name not in matched:
                        candidate = c
                        break
            if candidate is not None:
                matched.add(candidate.name)
                changes.append(
                    make_change(
                        ChangeKind.USED_RESERVED_FIELD,
                        symbol=name,
                        name=name,
                        old=f_old.name,
                        new=candidate.name,
                    )
                )
    return changes


@registry.detector("const_overloads")
def _diff_const_overloads(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect removed const method overloads (ABICC: Removed_Const_Overload).

    A const overload removal occurs when both const and non-const versions
    existed in old, but only the non-const version remains in new.
    """
    changes: list[Change] = []
    old_funcs = [f for f in old.functions if f.visibility in _PUBLIC_VIS]
    new_funcs = [f for f in new.functions if f.visibility in _PUBLIC_VIS]

    # Group by (name, param_signature) to find const/non-const pairs
    _ParamSig = tuple[str, int, str]  # (type, pointer_depth, kind)
    _GroupKey = tuple[str, tuple[_ParamSig, ...]]

    def _group_key(f: Function) -> _GroupKey:
        return (
            f.name,
            tuple((p.type, p.pointer_depth, p.kind.value) for p in f.params),
        )

    old_groups: dict[_GroupKey, list[Function]] = defaultdict(list)
    new_groups: dict[_GroupKey, list[Function]] = defaultdict(list)
    for f in old_funcs:
        old_groups[_group_key(f)].append(f)
    for f in new_funcs:
        new_groups[_group_key(f)].append(f)

    for key, old_fns in old_groups.items():
        old_const = [f for f in old_fns if f.is_const]
        old_nonconst = [f for f in old_fns if not f.is_const]
        if not old_const or not old_nonconst:
            continue  # no const overload pair in old

        new_fns = new_groups.get(key, [])
        new_const = [f for f in new_fns if f.is_const]
        new_nonconst = [f for f in new_fns if not f.is_const]
        if not new_const and new_nonconst:
            # Const overload removed, non-const kept
            f_removed = old_const[0]
            changes.append(
                make_change(
                    ChangeKind.REMOVED_CONST_OVERLOAD,
                    symbol=f_removed.mangled,
                    name=f_removed.name,
                    old_value="const overload present",
                    new_value="const overload removed",
                )
            )
    return changes
