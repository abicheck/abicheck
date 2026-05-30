# Copyright 2026 Nikolay Petrov
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

"""Symbol-level ABI diff detectors (functions, variables, parameters)."""
from __future__ import annotations

import logging
from typing import Any

from .binary_fingerprint import (
    _MIN_SYMBOL_SIZE,
    FunctionFingerprint,
    match_renamed_functions,
)
from .checker_policy import ChangeKind
from .checker_types import Change
from .detector_registry import registry
from .elf_metadata import SymbolType
from .model import (
    AbiSnapshot,
    Function,
    Param,
    Variable,
    Visibility,
    canonicalize_type_name,
)

_log = logging.getLogger(__name__)

# Visibility levels that constitute the public ABI surface.
_PUBLIC_VIS = (Visibility.PUBLIC, Visibility.ELF_ONLY)


def _public_functions(snap: AbiSnapshot) -> dict[str, Function]:
    """Return public/ELF-only functions from *snap*."""
    return {k: v for k, v in snap.function_map.items() if v.visibility in _PUBLIC_VIS}


def _public_variables(snap: AbiSnapshot) -> dict[str, Variable]:
    """Return public/ELF-only variables from *snap*."""
    return {k: v for k, v in snap.variable_map.items() if v.visibility in _PUBLIC_VIS}



def _format_params(params: list[Param]) -> str:
    """Format a parameter list as a human-readable string.

    ``Param.type`` already carries pointer/reference sigils (e.g. ``int *``,
    ``Foo &``), so we use it directly — appending ``_KIND_SUFFIX`` would
    duplicate them.
    """
    parts = [p.type for p in params]
    return ", ".join(parts) if parts else "(none)"


def _check_removed_function(
    mangled: str, f_old: Function, new_all: dict[str, Function],
    elf_only_mode: bool,
) -> Change:
    """Create a Change for a function that was removed or hidden."""
    f_hidden = new_all.get(mangled)
    if (
        f_hidden is not None
        and f_hidden.visibility == Visibility.HIDDEN
        and not (elf_only_mode and f_old.visibility == Visibility.ELF_ONLY)
    ):
        return Change(
            kind=ChangeKind.FUNC_VISIBILITY_CHANGED,
            symbol=mangled,
            description=f"Function visibility changed to hidden: {f_old.name}",
            old_value=f_old.visibility.value,
            new_value=f_hidden.visibility.value,
        )
    removed_kind = (
        ChangeKind.FUNC_REMOVED_ELF_ONLY
        if (elf_only_mode and f_old.visibility == Visibility.ELF_ONLY)
        else ChangeKind.FUNC_REMOVED
    )
    return Change(
        kind=removed_kind,
        symbol=mangled,
        description=f"{f_old.visibility.value.capitalize()} function removed: {f_old.name}",
        old_value=f_old.name,
    )


def _check_return_type_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the return type was modified."""
    if canonicalize_type_name(f_old.return_type) == canonicalize_type_name(f_new.return_type):
        return []
    return [Change(
        kind=ChangeKind.FUNC_RETURN_CHANGED,
        symbol=mangled,
        description=f"Return type changed: {f_old.name}",
        old_value=f_old.return_type,
        new_value=f_new.return_type,
    )]


def _check_params_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the parameter list was modified."""
    old_params = [(canonicalize_type_name(p.type), p.kind) for p in f_old.params]
    new_params = [(canonicalize_type_name(p.type), p.kind) for p in f_new.params]
    if old_params == new_params:
        return []
    return [Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED,
        symbol=mangled,
        description=f"Parameters changed: {f_old.name}",
        old_value=_format_params(f_old.params),
        new_value=_format_params(f_new.params),
    )]


def _check_ref_qualifier_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the ref-qualifier (&/&&) was modified."""
    old_rq = f_old.ref_qualifier or ""
    new_rq = f_new.ref_qualifier or ""
    if old_rq == new_rq:
        return []
    return [Change(
        kind=ChangeKind.FUNC_REF_QUAL_CHANGED,
        symbol=mangled,
        description=f"Ref-qualifier changed: {f_old.name} ({old_rq!r} → {new_rq!r})",
        old_value=old_rq or "(none)",
        new_value=new_rq or "(none)",
    )]


def _check_linkage_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the language linkage (extern \"C\" ↔ C++) was modified."""
    if f_old.is_extern_c == f_new.is_extern_c:
        return []
    old_linkage = 'extern "C"' if f_old.is_extern_c else "C++"
    new_linkage = 'extern "C"' if f_new.is_extern_c else "C++"
    return [Change(
        kind=ChangeKind.FUNC_LANGUAGE_LINKAGE_CHANGED,
        symbol=mangled,
        description=f"Language linkage changed: {f_old.name} ({old_linkage} → {new_linkage})",
        old_value=old_linkage,
        new_value=new_linkage,
    )]


def _check_noexcept_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the noexcept specifier was added or removed."""
    if f_old.is_noexcept and not f_new.is_noexcept:
        return [Change(
            kind=ChangeKind.FUNC_NOEXCEPT_REMOVED,
            symbol=mangled,
            description=f"noexcept specifier removed: {f_old.name}",
        )]
    if not f_old.is_noexcept and f_new.is_noexcept:
        return [Change(
            kind=ChangeKind.FUNC_NOEXCEPT_ADDED,
            symbol=mangled,
            description=f"noexcept specifier added: {f_old.name}",
        )]
    return []


def _check_virtual_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the virtual specifier was added or removed."""
    if not f_old.is_virtual and f_new.is_virtual:
        return [Change(
            kind=ChangeKind.FUNC_VIRTUAL_ADDED,
            symbol=mangled,
            description=f"Function became virtual: {f_old.name}",
        )]
    if f_old.is_virtual and not f_new.is_virtual:
        return [Change(
            kind=ChangeKind.FUNC_VIRTUAL_REMOVED,
            symbol=mangled,
            description=f"Function is no longer virtual: {f_old.name}",
        )]
    return []


def _check_hidden_friend_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the hidden-friend status transitioned.

    Hidden-friend transitions: an in-class ``friend`` declaration was
    added or removed across versions. Tri-state — skip when either
    side's snapshot did not record the flag (e.g. DWARF-only path or
    an older snapshot). The matched-mangled iteration here handles
    the case where the friend has an out-of-line definition (i.e.
    a real symbol). Inline-only hidden friends never appear here
    because they have no symbol on either side; those transitions
    are picked up by ``_check_hidden_friend_additions_removals``
    below by matching on (name, params) rather than mangled name.
    """
    if f_old.is_hidden_friend is None or f_new.is_hidden_friend is None:
        return []
    if not f_old.is_hidden_friend and f_new.is_hidden_friend:
        return [Change(
            kind=ChangeKind.HIDDEN_FRIEND_ADDED,
            symbol=mangled,
            description=f"Function became an in-class friend declaration: {f_old.name}",
            old_value="non-friend",
            new_value="hidden friend",
        )]
    if f_old.is_hidden_friend and not f_new.is_hidden_friend:
        return [Change(
            kind=ChangeKind.HIDDEN_FRIEND_REMOVED,
            symbol=mangled,
            description=f"Function is no longer an in-class friend declaration: {f_old.name}",
            old_value="hidden friend",
            new_value="non-friend",
        )]
    return []


def _check_explicit_change(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Emit a change if the explicit specifier was added or removed.

    Tri-state: only fire when BOTH sides record explicit data. None means
    the dumper/loader couldn't determine it — typically an older snapshot
    that predates the field, or a Function/Destructor where ``explicit`` is
    N/A. Skipping in that case avoids false API_BREAK findings produced
    purely by snapshot schema evolution.
    """
    if f_old.is_explicit is None or f_new.is_explicit is None:
        return []
    if not f_old.is_explicit and f_new.is_explicit:
        return [Change(
            kind=ChangeKind.CTOR_EXPLICIT_ADDED,
            symbol=mangled,
            description=f"Constructor/conversion gained `explicit` specifier: {f_old.name}",
            old_value="implicit",
            new_value="explicit",
        )]
    if f_old.is_explicit and not f_new.is_explicit:
        return [Change(
            kind=ChangeKind.CTOR_EXPLICIT_REMOVED,
            symbol=mangled,
            description=f"Constructor/conversion lost `explicit` specifier: {f_old.name}",
            old_value="explicit",
            new_value="implicit",
        )]
    return []


def _check_function_signature(mangled: str, f_old: Function, f_new: Function) -> list[Change]:
    """Compare signatures and qualifiers of two matched functions."""
    changes: list[Change] = []
    changes.extend(_check_return_type_change(mangled, f_old, f_new))
    changes.extend(_check_params_change(mangled, f_old, f_new))
    changes.extend(_check_ref_qualifier_change(mangled, f_old, f_new))
    changes.extend(_check_linkage_change(mangled, f_old, f_new))
    changes.extend(_check_noexcept_change(mangled, f_old, f_new))
    changes.extend(_check_virtual_change(mangled, f_old, f_new))
    changes.extend(_check_hidden_friend_change(mangled, f_old, f_new))
    changes.extend(_check_explicit_change(mangled, f_old, f_new))
    return changes


def _check_inline_transitions(
    old_map: dict[str, Function], new_map: dict[str, Function],
    new_snapshot: AbiSnapshot,
) -> list[Change]:
    """Detect inline/non-inline transitions for functions present in both snapshots."""
    changes: list[Change] = []
    for mangled in set(old_map) & set(new_map):
        f_old = old_map[mangled]
        f_new = new_map[mangled]
        if not f_old.is_inline and f_new.is_inline:
            new_elf = new_snapshot.elf
            still_exported = (
                new_elf is not None
                and any(s.name == mangled for s in new_elf.symbols)
            )
            changes.append(Change(
                kind=ChangeKind.FUNC_BECAME_INLINE,
                symbol=mangled,
                description=(
                    f"Function became inline, symbol still exported: {f_old.name}"
                    if still_exported
                    else f"Function became inline (symbol may be removed from DSO): {f_old.name}"
                ),
                old_value="non-inline",
                new_value="inline",
            ))
        elif f_old.is_inline and not f_new.is_inline:
            changes.append(Change(
                kind=ChangeKind.FUNC_LOST_INLINE,
                symbol=mangled,
                description=f"Function lost inline attribute (now has external linkage): {f_old.name}",
                old_value="inline",
                new_value="non-inline",
            ))
    return changes


def _match_old_function(
    mangled: str,
    f_old: Function,
    new_map: dict[str, Function],
    new_by_name: dict[str, list[Function]],
    new_all: dict[str, Function],
    matched_by_name: set[str],
    elf_only_mode: bool,
) -> list[Change]:
    """Classify a single old function: matched by mangled, extern-C fallback, or removed."""
    if mangled in new_map:
        return list(_check_function_signature(mangled, f_old, new_map[mangled]))

    # Fallback by plain name when either side uses extern "C".
    # The name->Function mapping is a MULTIMAP: only fall back when there is
    # EXACTLY ONE extern-C candidate for this name, to avoid mis-pairing
    # overloaded or templated functions that share a display name.
    candidates = new_by_name.get(f_old.name, [])
    extern_c_candidates = [f for f in candidates if f.is_extern_c]
    if f_old.is_extern_c:
        # Old side is extern "C": match against the unique new extern-C peer.
        extern_c_candidates = candidates  # any single candidate is acceptable
    if len(extern_c_candidates) == 1:
        f_new = extern_c_candidates[0]
        result = list(_check_function_signature(f_old.name, f_old, f_new))
        matched_by_name.add(f_old.name)
        return result

    return [_check_removed_function(mangled, f_old, new_all, elf_only_mode)]


def _detect_newly_deleted_functions(
    old_all: dict[str, Function],
    new_all: dict[str, Function],
) -> list[Change]:
    """Detect functions that gained ``= delete`` between snapshots.

    FUNC_DELETED: detected via castxml is_deleted attribute (header analysis).
    FUNC_DELETED_DWARF: detected via DWARF DW_AT_deleted attribute (binary analysis).

    Only ABI-visible (PUBLIC / ELF_ONLY) functions are reported; hidden or
    internal functions are not part of the public ABI surface and must not
    produce spurious BREAKING findings.
    """
    changes: list[Change] = []
    for mangled, f_new in new_all.items():
        if not f_new.is_deleted:
            continue
        # Skip functions that are not part of the public ABI surface.
        if f_new.visibility not in _PUBLIC_VIS:
            continue
        f_old_any = old_all.get(mangled)
        if f_old_any is not None and not f_old_any.is_deleted:
            kind = (
                ChangeKind.FUNC_DELETED_DWARF
                if f_new.deleted_from_dwarf
                else ChangeKind.FUNC_DELETED
            )
            changes.append(Change(
                kind=kind,
                symbol=mangled,
                description=f"Function explicitly deleted (= delete): {f_new.name}",
                old_value="callable",
                new_value="deleted",
            ))
    return changes


@registry.detector("functions")
def _diff_functions(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    elf_only_mode = getattr(old, "elf_only_mode", False)
    changes: list[Change] = []
    old_map = {k: v for k, v in old.function_map.items() if v.visibility in (Visibility.PUBLIC, Visibility.ELF_ONLY)}
    new_map = {k: v for k, v in new.function_map.items() if v.visibility in (Visibility.PUBLIC, Visibility.ELF_ONLY)}

    # Build a lookup of ALL functions in new snapshot (including hidden).
    new_all = new.function_map

    # Build secondary index by plain name for extern-C fallback matching when
    # mangled names differ due to C/C++ compilation mode mismatch.
    # Use a multimap (name -> list) so overloaded/templated functions sharing a
    # display name are not silently collapsed to one candidate.
    new_by_name: dict[str, list[Function]] = {}
    for f in new_map.values():
        new_by_name.setdefault(f.name, []).append(f)
    matched_by_name: set[str] = set()

    for mangled, f_old in old_map.items():
        changes.extend(
            _match_old_function(mangled, f_old, new_map, new_by_name, new_all, matched_by_name, elf_only_mode)
        )

    for mangled, f_new in new_map.items():
        if mangled not in old_map and f_new.name not in matched_by_name:
            changes.append(Change(
                kind=ChangeKind.FUNC_ADDED,
                symbol=mangled,
                description=f"New public function: {f_new.name}",
                new_value=f_new.name,
            ))

    old_all = old.function_map
    new_all_map = new.function_map
    changes.extend(_detect_newly_deleted_functions(old_all, new_all_map))

    # FUNC_BECAME_INLINE / FUNC_LOST_INLINE: detect inline↔non-inline transitions
    changes.extend(_check_inline_transitions(old_map, new_map, new))

    # HIDDEN_FRIEND_ADDED / HIDDEN_FRIEND_REMOVED for the inline-only case.
    # Inline hidden friends have no external symbol (visibility=HIDDEN) so
    # the public-symbol diff above does not see them. Match across versions
    # by mangled name across the FULL function map (not just public).
    changes.extend(_diff_inline_hidden_friends(old_all, new_all_map))

    return changes


def _diff_inline_hidden_friends(
    old_all: dict[str, Function], new_all: dict[str, Function],
) -> list[Change]:
    """Pick up hidden-friend additions/removals that have no public symbol.

    Inline-defined hidden friends never appear in the .so dynsym (the
    compiler emits them as `linkonce_odr`, often inlined into callers).
    They show up in the castxml snapshot with ``visibility=HIDDEN`` and
    ``is_hidden_friend=True``. The public-symbol diff above skips them.
    This pass compares across the full function map and only fires for
    functions that are flagged as hidden friends on one side.
    """
    changes: list[Change] = []
    for mangled, f_old in old_all.items():
        if not f_old.is_hidden_friend:
            continue
        if mangled in new_all:
            continue
        changes.append(Change(
            kind=ChangeKind.HIDDEN_FRIEND_REMOVED,
            symbol=mangled,
            description=f"Hidden friend declaration removed: {f_old.name}",
            old_value=f_old.name,
        ))
    for mangled, f_new in new_all.items():
        if not f_new.is_hidden_friend:
            continue
        if mangled in old_all:
            continue
        changes.append(Change(
            kind=ChangeKind.HIDDEN_FRIEND_ADDED,
            symbol=mangled,
            description=f"Hidden friend declaration added: {f_new.name}",
            new_value=f_new.name,
        ))
    return changes


@registry.detector("variables")
def _diff_variables(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    changes: list[Change] = []
    old_map = _public_variables(old)
    new_map = _public_variables(new)

    for mangled, v_old in old_map.items():
        if mangled not in new_map:
            changes.append(Change(
                kind=ChangeKind.VAR_REMOVED,
                symbol=mangled,
                description=f"Public variable removed: {v_old.name}",
            ))
        elif canonicalize_type_name(old_map[mangled].type) != canonicalize_type_name(new_map[mangled].type):
            changes.append(Change(
                kind=ChangeKind.VAR_TYPE_CHANGED,
                symbol=mangled,
                description=f"Variable type changed: {v_old.name}",
                old_value=v_old.type, new_value=new_map[mangled].type,
            ))
        else:
            v_new = new_map[mangled]
            if not v_old.is_const and v_new.is_const:
                changes.append(Change(
                    kind=ChangeKind.VAR_BECAME_CONST,
                    symbol=mangled,
                    description=f"Variable became const-qualified: {v_old.name} (writes now → SIGSEGV)",
                    old_value="non-const",
                    new_value="const",
                ))
            elif v_old.is_const and not v_new.is_const:
                changes.append(Change(
                    kind=ChangeKind.VAR_LOST_CONST,
                    symbol=mangled,
                    description=f"Variable lost const qualifier: {v_old.name} (ODR / inlining break)",
                    old_value="const",
                    new_value="non-const",
                ))

    for mangled, v_new in new_map.items():
        if mangled not in old_map:
            changes.append(Change(
                kind=ChangeKind.VAR_ADDED,
                symbol=mangled,
                description=f"New public variable: {v_new.name}",
            ))
    return changes


@registry.detector("param_defaults")
def _diff_param_defaults(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect parameter default value changes/removals."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        # Compare parameter defaults pairwise
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.default is not None and p_new.default is None:
                changes.append(Change(
                    kind=ChangeKind.PARAM_DEFAULT_VALUE_REMOVED,
                    symbol=mangled,
                    description=f"Parameter default removed: {f_old.name} param {p_old.name or i}",
                    old_value=p_old.default,
                    new_value=None,
                ))
            elif p_old.default is not None and p_new.default is not None and p_old.default != p_new.default:
                changes.append(Change(
                    kind=ChangeKind.PARAM_DEFAULT_VALUE_CHANGED,
                    symbol=mangled,
                    description=f"Parameter default changed: {f_old.name} param {p_old.name or i}",
                    old_value=p_old.default,
                    new_value=p_new.default,
                ))

    return changes


@registry.detector("param_renames")
def _diff_param_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect parameter renames (same type+position, different name)."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.type == p_new.type and p_old.name and p_new.name and p_old.name != p_new.name:
                changes.append(Change(
                    kind=ChangeKind.PARAM_RENAMED,
                    symbol=mangled,
                    description=f"Parameter renamed: {f_old.name} param {i}: {p_old.name} → {p_new.name}",
                    old_value=p_old.name,
                    new_value=p_new.name,
                ))

    return changes


@registry.detector("pointer_levels")
def _diff_pointer_levels(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect pointer level changes in params and return types."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue

        # Return pointer depth
        if f_old.return_pointer_depth != f_new.return_pointer_depth and (
            f_old.return_pointer_depth > 0 or f_new.return_pointer_depth > 0
        ):
            changes.append(Change(
                kind=ChangeKind.RETURN_POINTER_LEVEL_CHANGED,
                symbol=mangled,
                description=f"Return pointer level changed: {f_old.name} (depth {f_old.return_pointer_depth} → {f_new.return_pointer_depth})",
                old_value=str(f_old.return_pointer_depth),
                new_value=str(f_new.return_pointer_depth),
            ))

        # Param pointer depths
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.pointer_depth != p_new.pointer_depth and (
                p_old.pointer_depth > 0 or p_new.pointer_depth > 0
            ):
                changes.append(Change(
                    kind=ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
                    symbol=mangled,
                    description=f"Parameter pointer level changed: {f_old.name} param {p_old.name or i} (depth {p_old.pointer_depth} → {p_new.pointer_depth})",
                    old_value=str(p_old.pointer_depth),
                    new_value=str(p_new.pointer_depth),
                ))

    return changes


def _is_access_narrowing(old_access: Any, new_access: Any) -> bool:
    """Return True if the access level transition is narrowing (breaking).

    Narrowing = less accessible: public→protected, public→private, protected→private.
    Widening (e.g., private→public) is backward-compatible and should NOT be flagged.
    """
    from .model import AccessLevel
    _RANK = {AccessLevel.PUBLIC: 0, AccessLevel.PROTECTED: 1, AccessLevel.PRIVATE: 2}  # pylint: disable=invalid-name
    return _RANK.get(new_access, 0) > _RANK.get(old_access, 0)


def _check_method_access_changes(
    old_map: dict[str, Function],
    new_map: dict[str, Function],
) -> list[Change]:
    """Emit METHOD_ACCESS_CHANGED for narrowing method access transitions."""
    changes: list[Change] = []
    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        if f_old.access != f_new.access and _is_access_narrowing(f_old.access, f_new.access):
            changes.append(Change(
                kind=ChangeKind.METHOD_ACCESS_CHANGED,
                symbol=mangled,
                description=f"Method access level narrowed: {f_old.name} ({f_old.access.value} → {f_new.access.value})",
                old_value=f_old.access.value,
                new_value=f_new.access.value,
            ))
    return changes


def _check_field_access_changes(
    old_types: dict[str, Any],
    new_types: dict[str, Any],
) -> list[Change]:
    """Emit FIELD_ACCESS_CHANGED for narrowing field access transitions."""
    changes: list[Change] = []
    for name, t_old in old_types.items():
        t_new = new_types.get(name)
        if t_new is None:
            continue
        old_fields = {f.name: f for f in t_old.fields}
        new_fields = {f.name: f for f in t_new.fields}
        for fname, f_old_f in old_fields.items():
            f_new_f = new_fields.get(fname)
            if f_new_f is None:
                continue
            if f_old_f.access != f_new_f.access and _is_access_narrowing(f_old_f.access, f_new_f.access):
                changes.append(Change(
                    kind=ChangeKind.FIELD_ACCESS_CHANGED,
                    symbol=name,
                    description=f"Field access level narrowed: {name}::{fname} ({f_old_f.access.value} → {f_new_f.access.value})",
                    old_value=f_old_f.access.value,
                    new_value=f_new_f.access.value,
                ))
    return changes


@registry.detector("access_levels")
def _diff_access_levels(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect narrowing access level changes on methods and fields.

    Only flags narrowing transitions (public→protected/private, protected→private).
    Widening (e.g., private→public) is backward-compatible and not reported.
    """
    changes: list[Change] = []
    changes.extend(_check_method_access_changes(_public_functions(old), _public_functions(new)))
    old_types = {t.name: t for t in old.types if not t.is_union}
    new_types = {t.name: t for t in new.types if not t.is_union}
    changes.extend(_check_field_access_changes(old_types, new_types))
    return changes


def _is_anon_field(f: Any) -> bool:
    """Return True for compiler-generated anonymous/unnamed fields."""
    return not f.name or f.name.startswith("__anon")


def _check_anon_field_at_offset(
    name: str,
    offset: int,
    f_old: Any,
    new_by_offset: dict[int, Any],
) -> Change | None:
    """Compare a single anonymous field (by offset) to what the new type has."""
    f_new = new_by_offset.get(offset)
    if f_new is None:
        return Change(
            kind=ChangeKind.ANON_FIELD_CHANGED,
            symbol=name,
            description=f"Anonymous field removed at offset {offset} in {name}",
            old_value=f_old.type,
        )
    if f_old.type != f_new.type:
        return Change(
            kind=ChangeKind.ANON_FIELD_CHANGED,
            symbol=name,
            description=f"Anonymous field type changed at offset {offset} in {name}",
            old_value=f_old.type,
            new_value=f_new.type,
        )
    return None


def _anon_fields_by_offset(fields: list[Any]) -> dict[int, Any]:
    """Index anonymous fields (no name or __anon prefix) by their bit offset."""
    return {f.offset_bits: f for f in fields if _is_anon_field(f) and f.offset_bits is not None}


def _check_anon_fields_for_type(name: str, t_old: Any, t_new: Any) -> list[Change]:
    """Compare anonymous fields by offset for a single matched type pair."""
    old_by_offset = _anon_fields_by_offset(t_old.fields)
    new_by_offset = _anon_fields_by_offset(t_new.fields)

    if not old_by_offset and not new_by_offset:
        return []

    changes: list[Change] = []
    for offset, f_old in old_by_offset.items():
        ch = _check_anon_field_at_offset(name, offset, f_old, new_by_offset)
        if ch is not None:
            changes.append(ch)
    return changes


@registry.detector("anon_fields")
def _diff_anon_fields(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect changes in anonymous struct/union members."""
    changes: list[Change] = []
    old_map = {t.name: t for t in old.types}
    new_map = {t.name: t for t in new.types}

    for name, t_old in old_map.items():
        t_new = new_map.get(name)
        if t_new is None:
            continue
        changes.extend(_check_anon_fields_for_type(name, t_old, t_new))

    return changes


def _find_rename_pairs(
    removed: set[str],
    added: set[str],
    old_map: dict[str, Function],
    new_map: dict[str, Function],
) -> list[tuple[str, str]]:
    """Return (old_name, new_name) pairs where new_name has a common prefix added to old_name."""
    pairs: list[tuple[str, str]] = []
    for r_sym in removed:
        r_name = old_map[r_sym].name
        for a_sym in added:
            a_name = new_map[a_sym].name
            if a_name.endswith(r_name) and len(a_name) > len(r_name):
                pairs.append((r_name, a_name))
                break
            if a_name.endswith("_" + r_name) and len(a_name) > len(r_name) + 1:
                pairs.append((r_name, a_name))
                break
    return pairs


def _emit_batch_rename(rename_pairs: list[tuple[str, str]]) -> list[Change]:
    """Emit a SYMBOL_RENAMED_BATCH change if all pairs share a single common prefix."""
    if len(rename_pairs) < 2:
        return []
    prefixes = {new_name[: new_name.rfind(old_name)] for old_name, new_name in rename_pairs}
    if len(prefixes) != 1:
        return []
    prefix = prefixes.pop()
    pair_desc = ", ".join(f"{o} → {n}" for o, n in rename_pairs[:5])
    if len(rename_pairs) > 5:
        pair_desc += f", ... ({len(rename_pairs)} total)"
    return [Change(
        kind=ChangeKind.SYMBOL_RENAMED_BATCH,
        symbol=f"batch_rename:{prefix}*",
        description=(
            f"Batch symbol rename detected (namespace refactoring): "
            f"prefix '{prefix}' added to {len(rename_pairs)} symbols ({pair_desc})"
        ),
        old_value=", ".join(o for o, _ in rename_pairs),
        new_value=", ".join(n for _, n in rename_pairs),
    )]


@registry.detector("symbol_renames")
def _diff_symbol_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect batch symbol renames (namespace refactoring).

    When multiple symbols are removed and corresponding prefixed versions are
    added (e.g. ``init`` → ``mylib_init``), this indicates a namespace
    refactoring that breaks all existing consumers.

    Heuristic: if 2+ removed symbols each have a matching added symbol where
    the added name ends with the removed name (common prefix pattern), emit
    a SYMBOL_RENAMED_BATCH change.
    """
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    removed = set(old_map.keys()) - set(new_map.keys())
    added = set(new_map.keys()) - set(old_map.keys())

    if len(removed) < 2 or not added:
        return []

    rename_pairs = _find_rename_pairs(removed, added, old_map, new_map)
    return _emit_batch_rename(rename_pairs)


@registry.detector("param_restrict")
def _diff_param_restrict(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect restrict qualifier changes on parameters (ABICC: Parameter_Became_Restrict)."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if p_old.is_restrict != p_new.is_restrict:
                direction = "added" if p_new.is_restrict else "removed"
                changes.append(Change(
                    kind=ChangeKind.PARAM_RESTRICT_CHANGED,
                    symbol=mangled,
                    description=f"Parameter restrict qualifier {direction}: {f_old.name} param {p_old.name or i}",
                    old_value=f"restrict={p_old.is_restrict}",
                    new_value=f"restrict={p_new.is_restrict}",
                ))
    return changes


@registry.detector("param_va_list")
def _diff_param_va_list(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect va_list parameter changes (ABICC: Parameter_Became_VaList/Non_VaList)."""
    changes: list[Change] = []
    old_map = _public_functions(old)
    new_map = _public_functions(new)

    for mangled, f_old in old_map.items():
        f_new = new_map.get(mangled)
        if f_new is None:
            continue
        for i, (p_old, p_new) in enumerate(zip(f_old.params, f_new.params)):
            if not p_old.is_va_list and p_new.is_va_list:
                changes.append(Change(
                    kind=ChangeKind.PARAM_BECAME_VA_LIST,
                    symbol=mangled,
                    description=f"Parameter became va_list: {f_old.name} param {p_old.name or i}",
                    old_value=p_old.type,
                    new_value="va_list",
                ))
            elif p_old.is_va_list and not p_new.is_va_list:
                changes.append(Change(
                    kind=ChangeKind.PARAM_LOST_VA_LIST,
                    symbol=mangled,
                    description=f"Parameter was va_list, now fixed: {f_old.name} param {p_old.name or i}",
                    old_value="va_list",
                    new_value=p_new.type,
                ))
    return changes


@registry.detector("constants")
def _diff_constants(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect preprocessor constant (#define) changes (ABICC: Changed/Added/Removed_Constant)."""
    changes: list[Change] = []
    old_consts = old.constants
    new_consts = new.constants

    for name, old_val in old_consts.items():
        new_val = new_consts.get(name)
        if new_val is None:
            changes.append(Change(
                kind=ChangeKind.CONSTANT_REMOVED,
                symbol=name,
                description=f"Preprocessor constant removed: {name}",
                old_value=old_val,
            ))
        elif new_val != old_val:
            changes.append(Change(
                kind=ChangeKind.CONSTANT_CHANGED,
                symbol=name,
                description=f"Preprocessor constant value changed: {name} ({old_val!r} → {new_val!r})",
                old_value=old_val,
                new_value=new_val,
            ))

    for name, new_val in new_consts.items():
        if name not in old_consts:
            changes.append(Change(
                kind=ChangeKind.CONSTANT_ADDED,
                symbol=name,
                description=f"New preprocessor constant: {name}",
                new_value=new_val,
            ))
    return changes


@registry.detector("var_access")
def _diff_var_access(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect global data access level changes (ABICC: Global_Data_Became_Private/Protected/Public)."""
    changes: list[Change] = []
    old_map = _public_variables(old)
    new_map = _public_variables(new)

    for mangled, v_old in old_map.items():
        v_new = new_map.get(mangled)
        if v_new is None:
            continue
        if v_old.access != v_new.access:
            if _is_access_narrowing(v_old.access, v_new.access):
                changes.append(Change(
                    kind=ChangeKind.VAR_ACCESS_CHANGED,
                    symbol=mangled,
                    description=f"Variable access level narrowed: {v_old.name} ({v_old.access.value} → {v_new.access.value})",
                    old_value=v_old.access.value,
                    new_value=v_new.access.value,
                ))
            else:
                changes.append(Change(
                    kind=ChangeKind.VAR_ACCESS_WIDENED,
                    symbol=mangled,
                    description=f"Variable access level widened: {v_old.name} ({v_old.access.value} → {v_new.access.value})",
                    old_value=v_old.access.value,
                    new_value=v_new.access.value,
                ))
    return changes


_FUNC_LIKE_TYPES = frozenset({SymbolType.FUNC, SymbolType.IFUNC, SymbolType.NOTYPE})


def _fingerprints_from_elf(snap: AbiSnapshot) -> dict[str, FunctionFingerprint]:
    """Build FunctionFingerprint dict from ELF metadata (size-only, no code hash).

    Uses ElfSymbol.size from .dynsym to create fingerprints for rename matching.
    Includes FUNC, IFUNC, and NOTYPE symbols — matching dumper.py's
    ``exported_dynamic_funcs`` categorization for elf_only_mode snapshots.
    Code hashing requires the binary file and is handled by
    ``binary_fingerprint.compute_function_fingerprints()`` when a path is available.
    """
    if snap.elf is None:
        return {}
    result: dict[str, FunctionFingerprint] = {}
    for sym in snap.elf.symbols:
        if sym.sym_type not in _FUNC_LIKE_TYPES:
            continue
        if sym.size < _MIN_SYMBOL_SIZE:
            continue
        result[sym.name] = FunctionFingerprint(
            name=sym.name,
            size=sym.size,
            code_hash="",  # no code hash from metadata alone
        )
    return result


@registry.detector(
    "fingerprint_renames",
    requires_support=lambda o, n: (
        o.elf is not None and n.elf is not None
        and (o.elf_only_mode or n.elf_only_mode),
        "requires ELF metadata in elf_only_mode",
    ),
)
def _diff_fingerprint_renames(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """Detect likely function renames using binary fingerprint matching.

    Only runs in elf_only_mode (stripped binaries without debug info or headers),
    where rename churn is most problematic.  Uses function code size from
    ELF .dynsym to find removed+added pairs that likely represent the same
    function under a different name.

    Fires when *either* snapshot is elf_only — the rename churn problem exists
    even if only one side is stripped.
    """
    changes: list[Change] = []

    old_fps = _fingerprints_from_elf(old)
    new_fps = _fingerprints_from_elf(new)

    if not old_fps or not new_fps:
        return changes

    candidates = match_renamed_functions(old_fps, new_fps)
    for c in candidates:
        conf_pct = int(c.confidence * 100)
        changes.append(Change(
            kind=ChangeKind.FUNC_LIKELY_RENAMED,
            symbol=c.old_name,
            description=(
                f"Function likely renamed: {c.old_name} → {c.new_name} "
                f"(size={c.old_fingerprint.size}B, confidence={conf_pct}%)"
            ),
            old_value=c.old_name,
            new_value=c.new_name,
        ))

    if candidates:
        _log.info(
            "Fingerprint rename detection: %d candidate(s) found",
            len(candidates),
        )

    return changes

