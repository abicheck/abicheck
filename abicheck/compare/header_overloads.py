# SPDX-License-Identifier: Apache-2.0
"""Compare public overload declarations that share a linker-name placeholder."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePath

from ..diff_helpers import make_change
from ..model import AbiSnapshot, Function
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.export_entity_name import entity_name_components
from ..model.surface_facts import (
    has_observed_contract_evidence,
    is_export_confirmed_absent,
)
from ..model.type_label import qualified_spelling
from ..model.vocabulary import AccessLevel
from .template_surface import reconciled_public_functions


def _families(functions: list[Function], leaves: set[str]) -> dict[str, list[Function]]:
    by_name: dict[str, list[Function]] = defaultdict(list)
    for fn in functions:
        if fn.entity_id is None or (
            fn.entity_id.leaf_name not in leaves and fn.name not in leaves
        ):
            continue
        name = qualified_spelling(fn.entity_id)
        if not fn.entity_id.scope and fn.mangled != name:
            parsed = entity_name_components(fn.mangled)
            if parsed:
                name = "::".join(parsed.components)
        if name:
            by_name[name].append(fn)
    return by_name


def _fixed_arity(fn: Function) -> bool:
    return fn.is_variadic is False and all("..." not in p.type for p in fn.params)


def _header_pattern(fn: Function) -> bool:
    eid = fn.entity_id
    return (
        eid is not None
        and eid.extra[:1] == ("sig",)
        and fn.mangled == eid.leaf_name
        and fn.access == AccessLevel.PUBLIC
        and has_observed_contract_evidence(fn)
        and is_export_confirmed_absent(fn)
    )


def align_header_overloads(
    old_map: dict[str, Function],
    new_map: dict[str, Function],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> tuple[dict[str, Function], dict[str, Function]]:
    """Do not diff two surviving siblings just because clang reordered them."""
    collisions = {
        key
        for key, lhs in old_map.items()
        if lhs.mangled == lhs.name
        and _header_pattern(lhs)
        and (rhs := new_map.get(key)) is not None
        and _header_pattern(rhs)
        and lhs.entity_id != rhs.entity_id
    }
    if not collisions:
        return old_map, new_map
    left, right = (
        {f.entity_id: f for f in side if f.mangled in collisions and _header_pattern(f)}
        for side in reconciled_public_functions(old, new)
    )
    old_map, new_map = dict(old_map), dict(new_map)
    for key in collisions:
        lhs, rhs = old_map[key], new_map[key]
        if lhs.entity_id in right:
            new_map[key] = right[lhs.entity_id]
        elif rhs.entity_id in left:
            old_map[key] = left[rhs.entity_id]
    return old_map, new_map


def lost_header_overloads(
    old: AbiSnapshot, new: AbiSnapshot, existing: list[Change]
) -> list[Change]:
    """Report withdrawn declarations and unambiguous contractions of call arity.

    For dependent functions clang may supply the same bare name for several
    declarations. The symbol index can retain only one. Compare *declared*
    families by scoped identity and require a contraction past every observed
    new candidate. A pack or unknown variadic status prevents this proof.
    """
    left, right = reconciled_public_functions(old, new)
    leaves = {f.mangled for f in left if _header_pattern(f)}
    if not leaves:
        return []
    before, after = _families(left, leaves), _families(right, leaves)
    # Require a common declaration in the same header as a witness that
    # both scans collected that header. SDK installation roots can differ.
    right_headers = {
        (f.entity_id, PurePath(f.source_header).name)
        for f in right
        if f.entity_id and f.source_header and has_observed_contract_evidence(f)
    }
    observed_headers = {
        f.source_header
        for f in left
        if f.source_header
        and (f.entity_id, PurePath(f.source_header).name) in right_headers
        and has_observed_contract_evidence(f)
    }
    all_new = {f.entity_id for f in new.declarations.functions if f.mangled in leaves}
    uncertain_leaves = {
        f.mangled
        for f in new.declarations.functions
        if f.mangled in leaves
        and (not f.entity_id or f.entity_id.extra[:1] != ("sig",))
    }
    reported = {
        c.entity_id
        for c in existing
        if c.kind
        in {
            ChangeKind.INLINE_FUNCTION_REMOVED,
            ChangeKind.FUNC_REMOVED,
            ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED,
        }
    }
    changes = []
    for name, lhs in sorted(before.items()):
        rhs = after.get(name, [])
        old_by_id = {f.entity_id: f for f in lhs}
        new_by_id = {f.entity_id: f for f in rhs}
        removed = old_by_id.keys() - new_by_id.keys()
        added = new_by_id.keys() - old_by_id.keys()
        if not added:
            for fn in lhs:
                eid = fn.entity_id
                if (
                    eid is None
                    or eid not in removed
                    or eid in reported
                    or eid in all_new
                    or fn.mangled in uncertain_leaves
                    or fn.source_header not in observed_headers
                    or not _header_pattern(fn)
                ):
                    continue
                changes.append(
                    make_change(
                        ChangeKind.INLINE_FUNCTION_REMOVED,
                        symbol=name,
                        description=f"Public header overload removed: {name}({', '.join(p.type for p in fn.params)})",
                        old_value=", ".join(p.type for p in fn.params),
                        entity_id=eid,
                        source_location=fn.source_location,
                    )
                )
                reported.add(eid)
            continue
        if len(lhs) < 2 or len(removed) != 1 or len(added) != 1:
            continue
        previous = old_by_id[next(iter(removed))]
        current = new_by_id[next(iter(added))]
        if (
            previous.entity_id in reported
            or previous.is_deleted
            or not _header_pattern(previous)
            or not _header_pattern(current)
            or previous.mangled in uncertain_leaves
            or not _fixed_arity(previous)
            or not all(_fixed_arity(f) for f in rhs)
        ):
            continue
        maximum = max(len(f.params) for f in rhs)
        if len(previous.params) <= maximum:
            continue
        changes.append(
            make_change(
                ChangeKind.HEADER_ONLY_FUNCTION_PARAMS_CHANGED,
                symbol=name,
                description=(
                    f"{name} previously declared an overload with {len(previous.params)} "
                    f"arguments; the largest remaining overload declares {maximum}."
                ),
                old_value=", ".join(p.type for p in previous.params),
                new_value=", ".join(p.type for p in current.params),
                entity_id=previous.entity_id,
                source_location=previous.source_location,
            )
        )
    return changes
