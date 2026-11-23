# SPDX-License-Identifier: Apache-2.0
"""Compare public overload declarations that share a linker-name placeholder."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from pathlib import PurePath

from ..diff_helpers import make_change
from ..model import AbiSnapshot, Function
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.export_entity_name import entity_name_components
from ..model.identity import EntityId
from ..model.surface_facts import (
    has_observed_contract_evidence,
    is_export_confirmed_absent,
)
from ..model.type_label import qualified_spelling
from ..model.vocabulary import AccessLevel
from .function_signature import function_signature_index
from .parameter_facts import ParameterView
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


def _default_patterns(
    functions: list[Function], wanted: set[EntityId] | None = None
) -> dict[EntityId, Function]:
    """Keep one representative only when redeclarations agree on defaults."""
    by_id: dict[EntityId, Function] = {}
    ambiguous: set[EntityId] = set()
    for fn in functions:
        eid = fn.entity_id
        if (
            eid is None
            or (wanted is not None and eid not in wanted)
            or not _header_pattern(fn)
        ):
            continue
        other = by_id.setdefault(eid, fn)
        if other is not fn and tuple(p.default for p in other.params) != tuple(
            p.default for p in fn.params
        ):
            ambiguous.add(eid)
    return {eid: fn for eid, fn in by_id.items() if eid not in ambiguous}


def additional_default_views(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_map: dict[str, Function],
    new_map: dict[str, Function],
) -> Iterator[tuple[str, Function, Function, ParameterView, ParameterView]]:
    """Exact-identity pairs with defaults omitted by the one-row-per-symbol index."""
    # Only a bare-key representative can hide an additional overload.
    if not any(key == fn.name for key, fn in old_map.items()):
        return
    candidates = {
        eid: fn
        for eid, fn in _default_patterns(old.declarations.functions).items()
        if any(p.default is not None for p in fn.params)
    }
    if not candidates:
        return
    pairs = []
    for eid, current in _default_patterns(
        new.declarations.functions, set(candidates)
    ).items():
        previous = candidates[eid]
        old_rep, new_rep = old_map.get(previous.mangled), new_map.get(current.mangled)
        if old_rep and new_rep and old_rep.entity_id == new_rep.entity_id == eid:
            continue
        name = qualified_spelling(eid)
        if name:
            pairs.append((name, previous, current))
    if pairs:
        left = function_signature_index(old.canonical_ir, (a for _, a, _ in pairs))
        right = function_signature_index(new.canonical_ir, (b for _, _, b in pairs))
        for name, previous, current in pairs:
            yield (
                name,
                previous,
                current,
                left.parameters_for(previous),
                right.parameters_for(current),
            )


def lost_header_overloads(
    old: AbiSnapshot,
    new: AbiSnapshot,
    existing: list[Change],
    functions: list[Function] | None = None,
) -> list[Change]:
    """Report withdrawn declarations and unambiguous contractions of call arity.

    For dependent functions clang may supply the same bare name for several
    declarations. The symbol index can retain only one. Compare *declared*
    families by scoped identity and require a contraction past every observed
    new candidate. A pack or unknown variadic status prevents this proof.
    """
    if functions is None:
        functions = old.declarations.functions
    leaves = {f.mangled for f in functions if _header_pattern(f)}
    if not leaves:
        return []
    left, right = reconciled_public_functions(old, new)
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
