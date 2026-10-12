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

"""Root-type redundancy: a finding that is a consequence of another.

When a type's layout changes, every function, variable or field whose
signature spells that type reports a derived change too. :func:`split_redundant`
folds each derived finding into the root-type change it names, annotating the
root with ``caused_count``/``affected_symbols`` and the derived finding with
``caused_by_type`` -- the finding moves to the redundant bucket, it is never
dropped (ADR-067: deduplication is a disposition, not a deletion).

Moved out of the legacy ``diff_filtering.py`` (ADR-061: identifying how two
raw changes relate is ``compare/``'s job).
"""

from __future__ import annotations

import re

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.name_heuristics import NameHeuristicEffect, register_name_heuristic
from ..model.qualified_name_split import split_top_level_scopes

# Change kinds that represent root type/enum changes (for redundancy filtering).
ROOT_TYPE_CHANGE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_SIZE_CHANGED,
        ChangeKind.TYPE_ALIGNMENT_CHANGED,
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_BASE_CHANGED,
        ChangeKind.TYPE_VTABLE_CHANGED,
        ChangeKind.TYPE_REMOVED,
        ChangeKind.TYPE_BECAME_OPAQUE,
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_MEMBER_ADDED,
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED,
        ChangeKind.ENUM_MEMBER_RENAMED,
        ChangeKind.UNION_FIELD_REMOVED,
        ChangeKind.UNION_FIELD_TYPE_CHANGED,
        ChangeKind.TYPEDEF_BASE_CHANGED,
        ChangeKind.TYPE_KIND_CHANGED,
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_ALIGNMENT_CHANGED,
        ChangeKind.STRUCT_PACKING_CHANGED,
    }
)

# Change kinds that are always independent (never considered redundant).
ALWAYS_INDEPENDENT_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_ADDED,
        ChangeKind.FUNC_ADDED_ELF_ONLY,
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ChangeKind.VAR_REMOVED,
        ChangeKind.VAR_REMOVED_ELF_ONLY,
        ChangeKind.VAR_ADDED,
        ChangeKind.SONAME_CHANGED,
        ChangeKind.SONAME_MISSING,
        ChangeKind.NEEDED_ADDED,
        ChangeKind.NEEDED_REMOVED,
        ChangeKind.RPATH_CHANGED,
        ChangeKind.RUNPATH_CHANGED,
        ChangeKind.SYMBOL_BINDING_CHANGED,
        ChangeKind.SYMBOL_BINDING_STRENGTHENED,
        ChangeKind.SYMBOL_TYPE_CHANGED,
        ChangeKind.SYMBOL_SIZE_CHANGED,
        ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED,
        ChangeKind.SYMBOL_VERSION_DEFINED_ADDED,
        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
        ChangeKind.SYMBOL_VERSION_REQUIRED_REMOVED,
        ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED_COMPAT,
        ChangeKind.RUNTIME_FLOOR_RAISED,
        ChangeKind.DT_RELR_INTRODUCED,
        ChangeKind.DT_RELR_REMOVED,
        ChangeKind.RPATH_TYPE_CHANGED,
        ChangeKind.HASH_STYLE_REMOVED,
        ChangeKind.TIME64_ABI_CHANGED,
        ChangeKind.IFUNC_INTRODUCED,
        ChangeKind.IFUNC_REMOVED,
        ChangeKind.COMMON_SYMBOL_RISK,
        ChangeKind.DWARF_INFO_MISSING,
        ChangeKind.TOOLCHAIN_FLAG_DRIFT,
        ChangeKind.COMPAT_VERSION_CHANGED,
        ChangeKind.MACHO_CPU_TYPE_CHANGED,
        ChangeKind.PE_FORWARDER_CHANGED,
        ChangeKind.PE_MACHINE_CHANGED,
        ChangeKind.VISIBILITY_LEAK,
        ChangeKind.FUNC_DELETED,
        ChangeKind.FUNC_DELETED_ELF_FALLBACK,
        ChangeKind.CONSTANT_CHANGED,
        ChangeKind.CONSTANT_ADDED,
        ChangeKind.CONSTANT_REMOVED,
    }
)

# Derived change kinds that may be caused by a root type change.
_DERIVED_CHANGE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_PARAMS_CHANGED,
        ChangeKind.FUNC_RETURN_CHANGED,
        ChangeKind.VAR_TYPE_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.UNION_FIELD_TYPE_CHANGED,
        ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
        ChangeKind.TEMPLATE_RETURN_TYPE_CHANGED,
        ChangeKind.PARAM_POINTER_LEVEL_CHANGED,
        ChangeKind.RETURN_POINTER_LEVEL_CHANGED,
    }
)


# Field-level change kinds where the symbol is "TypeName::fieldName".
# For these, the root type is the part before the *last* "::".
_FIELD_LEVEL_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.UNION_FIELD_REMOVED,
        ChangeKind.UNION_FIELD_TYPE_CHANGED,
        ChangeKind.UNION_FIELD_ADDED,
        ChangeKind.FIELD_BITFIELD_CHANGED,
        ChangeKind.FIELD_RENAMED,
        ChangeKind.FIELD_BECAME_CONST,
        ChangeKind.FIELD_LOST_CONST,
        ChangeKind.FIELD_BECAME_VOLATILE,
        ChangeKind.FIELD_LOST_VOLATILE,
        ChangeKind.FIELD_BECAME_MUTABLE,
        ChangeKind.FIELD_LOST_MUTABLE,
        ChangeKind.FIELD_ACCESS_CHANGED,
        ChangeKind.ANON_FIELD_CHANGED,
    }
)


def root_type_name(c: Change) -> str:
    """Extract the root type name from a change's symbol.

    For field-level changes (e.g. ``Container::flags``), strip the last
    ``::field`` component.  For all other changes (including namespaced
    types like ``ns::MyType``), keep the full symbol to avoid collapsing
    distinct types in the same namespace.

    A finding that carries its type's own ``qualified_name`` and whose symbol
    is that name (or that name plus ``::member``) is answered from it: a
    field-level kind is labelled with the bare type on some paths and with
    ``Type::field`` on others, so stripping the last component of
    ``ns::Type`` would otherwise yield the namespace ``ns``.
    """
    qualified = c.qualified_name
    if qualified:
        q_parts = split_top_level_scopes(qualified)
        if split_top_level_scopes(c.symbol)[: len(q_parts)] == q_parts:
            return qualified
    if "::" in c.symbol and c.kind in _FIELD_LEVEL_KINDS:
        return c.symbol.rsplit("::", 1)[0]
    return c.symbol


def _mark_as_redundant(
    c: Change,
    matched_root: str,
    root_types: dict[str, Change],
    redundant: list[Change],
) -> None:
    """Classify *c* as redundant: annotate both *c* and its root change."""
    c.caused_by_type = matched_root
    root_change = root_types[matched_root]
    root_change.caused_count += 1
    if root_change.affected_symbols is None:
        root_change.affected_symbols = []
    sym = c.symbol
    if sym and sym not in root_change.affected_symbols:
        root_change.affected_symbols.append(sym)
    redundant.append(c)


def _collect_root_types(changes: list[Change]) -> dict[str, Change]:
    """Return a mapping of type_name → first root-type Change found in *changes*."""
    root_types: dict[str, Change] = {}
    for c in changes:
        if c.kind in ROOT_TYPE_CHANGE_KINDS:
            type_name = root_type_name(c)
            if type_name not in root_types:
                root_types[type_name] = c
    return root_types


def _root_pattern(name: str) -> re.Pattern[str]:
    """The word-boundary pattern for one root type name. Deliberately not
    memoised process-wide: a cross-comparison cache makes a second run look
    cheaper than the first, which the call-count gates read as growth."""
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")


def _compile_root_patterns(root_types: dict[str, Change]) -> dict[str, re.Pattern[str]]:
    """Pre-compile word-boundary regex patterns for each root type name."""
    return {name: _root_pattern(name) for name in root_types}


def _classify_root_pass(
    changes: list[Change],
    root_types: dict[str, Change],
    compiled_patterns: dict[str, re.Pattern[str]],
    kept: list[Change],
    redundant: list[Change],
) -> None:
    """First pass: classify root-type changes, marking cross-referencing ones redundant.

    Mutates *root_types* (removes redundant roots), *kept*, and *redundant* in place.
    """
    removed_roots: set[str] = set()
    for c in changes:
        if c.kind not in ROOT_TYPE_CHANGE_KINDS:
            continue
        if c.kind in _DERIVED_CHANGE_KINDS:
            type_name = root_type_name(c)
            other_roots = {k: v for k, v in root_types.items() if k != type_name}
            matched_root = ROOT_TYPE_REFERENCE.apply(
                c, root_types=other_roots, compiled_patterns=compiled_patterns
            )
            if matched_root is not None:
                _mark_as_redundant(c, matched_root, root_types, redundant)
                # Remove this root so derived changes won't point at a
                # root that is itself redundant.
                removed_roots.add(type_name)
                continue
        kept.append(c)
    for name in removed_roots:
        root_types.pop(name, None)


def _classify_derived_pass(
    changes: list[Change],
    root_types: dict[str, Change],
    compiled_patterns: dict[str, re.Pattern[str]],
    kept: list[Change],
    redundant: list[Change],
) -> None:
    """Second pass: classify non-root changes, marking derived ones redundant."""
    for c in changes:
        if c.kind in ROOT_TYPE_CHANGE_KINDS:
            continue  # already handled in first pass
        if c.kind in ALWAYS_INDEPENDENT_KINDS or c.kind not in _DERIVED_CHANGE_KINDS:
            kept.append(c)
            continue
        # Check if this change references a (kept) root type
        matched_root = ROOT_TYPE_REFERENCE.apply(
            c, root_types=root_types, compiled_patterns=compiled_patterns
        )
        if matched_root is not None:
            _mark_as_redundant(c, matched_root, root_types, redundant)
        else:
            kept.append(c)


def split_redundant(changes: list[Change]) -> tuple[list[Change], list[Change]]:
    """Identify changes that are consequences of a root type change.

    Returns (kept, redundant) — redundant changes are still available for audit.
    Root changes are annotated with ``caused_count`` and ``derived_symbols``.
    """
    root_types = _collect_root_types(changes)
    if not root_types:
        return changes, []

    # Pre-compile word-boundary regex patterns for all root type names once,
    # instead of recompiling per (match_root_type call * root type) pair.
    compiled_patterns = _compile_root_patterns(root_types)

    kept: list[Change] = []
    redundant: list[Change] = []

    # First pass: classify root type changes (some may be redundant
    # if they reference another root type — nested type propagation).
    _classify_root_pass(changes, root_types, compiled_patterns, kept, redundant)

    # Second pass: classify non-root changes
    _classify_derived_pass(changes, root_types, compiled_patterns, kept, redundant)

    return kept, redundant


def match_root_type(
    c: Change,
    root_types: dict[str, Change],
    compiled_patterns: dict[str, re.Pattern[str]] | None = None,
) -> str | None:
    """Check if a derived change references a known root type.

    Returns the root type name if found, None otherwise.
    Uses word-boundary matching to avoid false positives where a type
    name is a prefix of another (e.g. ``Config`` must not match
    ``Config2``).

    Conservative: false negatives (showing too much) are safer than false
    positives (hiding real changes).

    When both ``old_value`` and ``new_value`` are present, the root type name
    must appear in *both* to match — not just either one. A change whose
    value moves to a wholly different type that merely shares a name with a
    root type (e.g. a covariant return override switching ``Circle*`` to
    ``Drawable*``, where "Circle" is coincidentally also a root type from an
    unrelated field change) is an independent finding, not a side effect of
    that root type's own layout change, and must not be swallowed just
    because the *old* value happens to mention it (case72). When only one of
    old/new is set (e.g. a pure addition/removal with no "before" or
    "after"), that one value is still sufficient, matching prior behavior.

    When *compiled_patterns* is provided, uses pre-compiled regex objects
    instead of recompiling per call (performance optimisation).
    """
    if compiled_patterns is None:
        compiled_patterns = _compile_root_patterns(root_types)
    for type_name in root_types:
        pat = compiled_patterns.get(type_name) or _root_pattern(type_name)
        if c.old_value and c.new_value:
            if pat.search(c.old_value) and pat.search(c.new_value):
                return type_name
            # Both sides are known and don't jointly confirm this root type — the description (which may just restate the same old/new transition text) must not override that, or the both-sides guard above no-ops whenever a covariant-return-style description echoes the old/new value's mention (case72).
            continue
        if c.old_value and pat.search(c.old_value):
            return type_name
        if c.new_value and pat.search(c.new_value):
            return type_name
        if pat.search(c.description):
            return type_name
    return None


#: Registered name heuristic (design-hardening Phase 5): searches a type
#: string for a declared type's *spelling* (word-boundary match), never its
#: identity, so it may only *lower* -- it folds a derived finding into the
#: root-type change it mentions. #1218 names the structural replacement.
ROOT_TYPE_REFERENCE = register_name_heuristic(
    "root_type_reference",
    owner=__name__,
    effect=NameHeuristicEffect.LOWER_CONFIDENCE,
    description=(
        "a derived finding whose old/new spelling names a changed root type "
        "is folded into that root change"
    ),
    matcher=match_root_type,
    helpers=(_compile_root_patterns, _root_pattern),
)
