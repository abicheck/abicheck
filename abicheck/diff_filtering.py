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

"Post-processing: enrichment, redundancy filtering, and deduplication."

from __future__ import annotations

import re
from collections import deque

from .checker_policy import ChangeKind
from .checker_types import SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER, Change
from .diff_symbols import _PUBLIC_VIS, _public_functions
from .model import AbiSnapshot, Function

# ── Post-processing: enrich and deduplicate ────────────────────────────────

# Mapping from DWARF change kinds to their AST equivalents for deduplication.
_DWARF_TO_AST_EQUIV: dict[ChangeKind, set[ChangeKind]] = {
    ChangeKind.STRUCT_SIZE_CHANGED: {ChangeKind.TYPE_SIZE_CHANGED},
    ChangeKind.STRUCT_ALIGNMENT_CHANGED: {ChangeKind.TYPE_ALIGNMENT_CHANGED},
    ChangeKind.STRUCT_FIELD_OFFSET_CHANGED: {ChangeKind.TYPE_FIELD_OFFSET_CHANGED},
    ChangeKind.STRUCT_FIELD_REMOVED: {ChangeKind.TYPE_FIELD_REMOVED},
    ChangeKind.STRUCT_FIELD_TYPE_CHANGED: {ChangeKind.TYPE_FIELD_TYPE_CHANGED},
}

# Type/enum/struct change kinds for which affected-symbol enrichment makes sense.
_TYPE_CHANGE_KINDS: frozenset[ChangeKind] = frozenset(
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
        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
        ChangeKind.TYPE_BECAME_OPAQUE,
        ChangeKind.BASE_CLASS_POSITION_CHANGED,
        ChangeKind.BASE_CLASS_VIRTUAL_CHANGED,
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_MEMBER_ADDED,
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED,
        ChangeKind.UNION_FIELD_ADDED,
        ChangeKind.UNION_FIELD_REMOVED,
        ChangeKind.UNION_FIELD_TYPE_CHANGED,
        ChangeKind.TYPEDEF_BASE_CHANGED,
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_ALIGNMENT_CHANGED,
        # Fine-grained class-layout descriptor kinds (layout-closure work): each
        # carries the owner type name in Change.symbol, so affected-symbol
        # enrichment must scan them too — otherwise a layout-only BREAKING
        # finding (e.g. TRIVIALLY_COPYABLE_LOST on a size-stable type used by an
        # exported by-value API) gets no affected_symbols and app-compat
        # filtering could mark a consumer as unaffected (Codex review #345).
        ChangeKind.BASE_CLASS_OFFSET_CHANGED,
        ChangeKind.VPTR_INTRODUCED,
        ChangeKind.TRIVIALLY_COPYABLE_LOST,
        ChangeKind.STANDARD_LAYOUT_LOST,
        ChangeKind.TAIL_PADDING_REUSE_CHANGED,
        ChangeKind.LAYOUT_UNVERIFIABLE,
    }
)


def _build_location_index(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Build type, function, and variable location lookup dicts from snapshots."""
    type_loc: dict[str, str] = {}
    for t in old.types:
        if t.source_location:
            type_loc[t.name] = t.source_location
    for t in new.types:
        if t.source_location:
            type_loc.setdefault(t.name, t.source_location)

    func_loc: dict[str, str] = {}
    for f in old.functions:
        if f.source_location:
            func_loc[f.mangled] = f.source_location
    for f in new.functions:
        if f.source_location:
            func_loc.setdefault(f.mangled, f.source_location)

    var_loc: dict[str, str] = {}
    for v in old.variables:
        if v.source_location:
            var_loc[v.mangled] = v.source_location
    for v in new.variables:
        if v.source_location:
            var_loc.setdefault(v.mangled, v.source_location)

    return type_loc, func_loc, var_loc


def _safe_index(snap: AbiSnapshot) -> bool:
    """Index ``snap`` for lookups, tolerating partial snapshots seen in tests.

    Returns ``True`` if the snapshot was indexed successfully and is safe to
    read from, ``False`` otherwise. Keeping the swallowed exception out of a
    ``try/except/continue`` loop body avoids a silently-ignored-error pattern.
    """
    try:
        snap.index()
    except Exception:  # noqa: BLE001 — partial snapshots in some tests
        return False
    return True


def _qualified_functions_by_mangled(snap: AbiSnapshot | None) -> dict[str, str]:
    """Return mangled/exported function names that have C++ qualification."""
    if snap is None or not _safe_index(snap):
        return {}

    qualified: dict[str, str] = {}
    for mangled, fn in (getattr(snap, "_func_by_mangled", None) or {}).items():
        fname = getattr(fn, "name", None)
        if fname and "::" in fname and mangled not in qualified:
            qualified[mangled] = fname
    return qualified


def _qualified_name_for_change(
    c: Change,
    old_qualified: dict[str, str],
    new_qualified: dict[str, str],
) -> str | None:
    """Safely recover a C++ qualified name for a function change."""
    if c.kind == ChangeKind.FUNC_ADDED:
        return new_qualified.get(c.symbol)
    if c.kind in (ChangeKind.FUNC_REMOVED, ChangeKind.FUNC_REMOVED_ELF_ONLY):
        return old_qualified.get(c.symbol)

    old_name = old_qualified.get(c.symbol)
    new_name = new_qualified.get(c.symbol)
    if old_name and old_name == new_name:
        return old_name
    return None


def _enrich_source_locations(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> None:
    """Fill in source_location and qualified_name on Changes from the model data."""
    type_loc, func_loc, var_loc = _build_location_index(old, new)

    old_qualified = _qualified_functions_by_mangled(old)
    new_qualified = _qualified_functions_by_mangled(new)

    for c in changes:
        if not c.source_location:
            # Try function/variable first (symbol is mangled name), then type name
            loc = (
                func_loc.get(c.symbol)
                or var_loc.get(c.symbol)
                or type_loc.get(c.symbol)
            )
            # For qualified symbols like "ns::MyStruct::field", fall back to root type name
            if not loc and "::" in c.symbol:
                loc = type_loc.get(_root_type_name(c))
            if loc:
                c.source_location = loc
        if not c.qualified_name:
            qual = _qualified_name_for_change(c, old_qualified, new_qualified)
            if qual:
                c.qualified_name = qual


def _all_ancestors(
    tname: str,
    type_embeds: dict[str, set[str]],
) -> set[str]:
    """BFS over type_embeds to find all transitive parent types."""
    visited: set[str] = set()
    queue = list(type_embeds.get(tname, set()))
    while queue:
        parent = queue.pop()
        if parent in visited:
            continue
        visited.add(parent)
        queue.extend(type_embeds.get(parent, set()))
    return visited


def _resolve_ancestor_functions(
    tname: str,
    ancestors: set[str],
    type_to_funcs: dict[str, set[str]],
    type_to_mangled: dict[str, set[str]],
    old_pub: dict[str, Function],
    ancestor_func_cache: dict[str, list[tuple[str, str]]],
) -> None:
    """Union ancestor-type functions into type_to_funcs/type_to_mangled for *tname*.

    Sets (not lists) are used so a type reachable through many ancestors does
    not accumulate the same function name repeatedly — list accumulation here
    made deeply-nested type graphs grow super-quadratically.
    """
    for parent in ancestors:
        if parent in type_to_funcs:
            type_to_funcs[tname].update(type_to_funcs[parent])
            type_to_mangled[tname].update(type_to_mangled.get(parent, set()))
        elif parent in ancestor_func_cache:
            for fname, mname in ancestor_func_cache[parent]:
                type_to_funcs[tname].add(fname)
                type_to_mangled[tname].add(mname)
        else:
            parent_funcs: list[tuple[str, str]] = []
            for _m, func in old_pub.items():
                func_types_used = {func.return_type} | {p.type for p in func.params}
                if any(parent in ft for ft in func_types_used if ft):
                    parent_funcs.append((func.name, func.mangled))
            ancestor_func_cache[parent] = parent_funcs
            for fname, mname in parent_funcs:
                type_to_funcs[tname].add(fname)
                type_to_mangled[tname].add(mname)


class _SubstringMatcher:
    """Aho-Corasick multi-substring matcher over a fixed needle set.

    :meth:`find` returns every needle that occurs as a substring of the queried
    haystack — the exact semantics of ``{n for n in needles if n in haystack}``,
    but in O(len(haystack)) per query after an O(Σ len(needle)) build, instead
    of O(needles × len(haystack)). Used by the affected-symbol enrichment to
    relate many changed type names to the functions/fields that reference them
    without the former quadratic ``any(tname in ft ...)`` cross-product.
    """

    __slots__ = ("_goto", "_fail", "_out")

    def __init__(self, needles: set[str]) -> None:
        goto: list[dict[str, int]] = [{}]
        out: list[set[str]] = [set()]
        for needle in needles:
            if not needle:
                continue
            node = 0
            for ch in needle:
                nxt = goto[node].get(ch)
                if nxt is None:
                    nxt = len(goto)
                    goto.append({})
                    out.append(set())
                    goto[node][ch] = nxt
                node = nxt
            out[node].add(needle)
        # Failure links via BFS; merge each node's output with its fail target's
        # so a single query walk collects all substrings ending at a position.
        fail = [0] * len(goto)
        queue: deque[int] = deque()
        for child in goto[0].values():
            queue.append(child)  # depth-1 nodes fail to root (already 0)
        while queue:
            node = queue.popleft()
            for ch, nxt in goto[node].items():
                queue.append(nxt)
                f = fail[node]
                while f and ch not in goto[f]:
                    f = fail[f]
                fail[nxt] = goto[f].get(ch, 0) if f or ch in goto[0] else 0
                if out[fail[nxt]]:
                    out[nxt] |= out[fail[nxt]]
        self._goto = goto
        self._fail = fail
        self._out = out

    def find(self, haystack: str) -> set[str]:
        """Return all needles that are substrings of *haystack*."""
        if not haystack:
            return set()
        goto, fail, out = self._goto, self._fail, self._out
        node = 0
        result: set[str] = set()
        for ch in haystack:
            while node and ch not in goto[node]:
                node = fail[node]
            node = goto[node].get(ch, 0)
            if out[node]:
                result |= out[node]
        return result


def _collect_function_type_refs(func: Function) -> set[str]:
    """Return the set of type strings referenced in *func*'s signature."""
    out: set[str] = set()
    if func.return_type:
        out.add(func.return_type)
    for p in func.params:
        if p.type:
            out.add(p.type)
    return out


def _build_type_to_funcs(
    affected_types: set[str],
    old_pub: dict[str, Function],
    matcher: _SubstringMatcher | None = None,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Build type→(demangled, mangled) function name sets from public functions.

    The naive form scanned every affected type against every function's type
    refs — ``any(tname in ft ...)`` nested in two loops — which is
    O(types × functions × refs) and goes quadratic when a header refactor or a
    versioned upgrade churns many distinct types (typedef/union/vtable churn was
    ≈O(n²), ~7–9 s at n=4000). Replace the inner type loop with a single
    Aho-Corasick pass per ref (:class:`_SubstringMatcher`): the set of affected
    types occurring as a substring of a ref is found in O(len(ref)) regardless
    of how many types are tracked, with **identical** substring semantics.
    """
    type_to_funcs: dict[str, set[str]] = {t: set() for t in affected_types}
    type_to_mangled: dict[str, set[str]] = {t: set() for t in affected_types}
    ac = matcher if matcher is not None else _SubstringMatcher(affected_types)
    for _mangled, func in old_pub.items():
        matched: set[str] = set()
        for ft in _collect_function_type_refs(func):
            matched |= ac.find(ft)
        for tname in matched:
            type_to_funcs[tname].add(func.name)
            type_to_mangled[tname].add(func.mangled)
    return type_to_funcs, type_to_mangled


def _build_type_embed_index(
    affected_types: set[str],
    old: AbiSnapshot,
    matcher: _SubstringMatcher | None = None,
) -> dict[str, set[str]]:
    """Build a child_type→{parent_type} embedding index from old snapshot fields."""
    type_embeds: dict[str, set[str]] = {}
    ac = matcher if matcher is not None else _SubstringMatcher(affected_types)
    for t in old.types:
        for fld in t.fields:
            for tname in ac.find(fld.type):
                type_embeds.setdefault(tname, set()).add(t.name)
    return type_embeds


def _assign_affected_symbols_to_changes(
    type_changes: list[Change],
    type_to_funcs: dict[str, set[str]],
    type_to_mangled: dict[str, set[str]],
) -> None:
    """Populate Change.affected_symbols from the type-to-function mappings."""
    for c in type_changes:
        type_name = _root_type_name(c)
        funcs = type_to_funcs.get(type_name, set())
        mangled_funcs = type_to_mangled.get(type_name, set())
        if funcs:
            # Store both demangled and mangled names for cross-format matching
            c.affected_symbols = sorted(funcs | mangled_funcs)


def _enrich_affected_symbols(
    changes: list[Change],
    old: AbiSnapshot,
) -> None:
    """For type/enum changes, find exported functions that use the affected type."""
    # Only compute if there are type-related changes
    type_changes = [c for c in changes if c.kind in _TYPE_CHANGE_KINDS]
    if not type_changes:
        return

    # Collect affected type names; strip field qualifiers like
    # "ns::Container::flags" → "ns::Container"
    affected_types: set[str] = {_root_type_name(c) for c in type_changes}
    if not affected_types:
        return

    old_pub = _public_functions(old)

    # One Aho-Corasick matcher over the affected type names, shared by both
    # index builders below — built once so neither pays the former
    # O(types × functions) / O(types × fields) substring cross-product.
    matcher = _SubstringMatcher(affected_types)

    # Build type→functions mapping from old snapshot (FIX-A Part 3).
    # Store both demangled names (for display) and mangled names (for appcompat matching).
    type_to_funcs, type_to_mangled = _build_type_to_funcs(affected_types, old_pub, matcher)

    # Also check if types are embedded in struct fields used by functions
    # (e.g., Container has a Leaf field → functions taking Container* are affected by Leaf changes)
    type_embeds = _build_type_embed_index(affected_types, old, matcher)

    # Compute transitive closure: if Leaf is in Container is in Wrapper,
    # functions using Wrapper are also affected by Leaf changes.
    # Cache: ancestor type → list of (func_name, mangled) so each ancestor
    # is scanned at most once across all affected types.
    ancestor_func_cache: dict[str, list[tuple[str, str]]] = {}
    for tname in affected_types:
        ancestors = _all_ancestors(tname, type_embeds)
        _resolve_ancestor_functions(
            tname,
            ancestors,
            type_to_funcs,
            type_to_mangled,
            old_pub,
            ancestor_func_cache,
        )

    # Assign to changes — include both demangled names (display) and
    # mangled names (appcompat matching, FIX-A Part 3).
    _assign_affected_symbols_to_changes(type_changes, type_to_funcs, type_to_mangled)


# Owner size/offset changes that the embedding attribution can annotate.
_LAYOUT_OWNER_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_SIZE_CHANGED,
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
    }
)


def _embedded_stdlib_fields(rec: object) -> list[tuple[str, str]]:
    """Return ``(field_name, field_type)`` for by-value ``std::`` members of *rec*.

    A by-value field whose type names a standard-library namespace makes the
    owner's layout depend on the stdlib implementation/version. Pointer/reference
    fields are layout-neutral and skipped (only a top-level ``*``/``&`` counts —
    a ``*`` inside template args, e.g. ``std::vector<int*>`` held by value, still
    embeds the container).
    """
    from .model import is_non_abi_surface_type

    out: list[tuple[str, str]] = []
    for fld in getattr(rec, "fields", []):
        tname = (fld.type or "").strip()
        if not tname or tname.endswith("*") or tname.endswith("&"):
            continue
        if is_non_abi_surface_type(tname.replace("const ", "").strip()):
            out.append((fld.name, tname))
    return out


def _attribute_stdlib_embedding(changes: list[Change], new: AbiSnapshot) -> None:
    """Attribute an owner type's size/offset change to an embedded ``std::`` member.

    The redundancy filter links a derived change to a root type change only when
    that root *emits its own change*. A standard-library member embedded by value
    is filtered out (toolchain-owned) and emits no change, so an owner whose
    layout shifted because of it is left unattributed. This appends a concise,
    non-escalating clause naming the responsible member so the root cause is not
    lost — purely informational: it does not alter the verdict, the kind, or the
    redundancy linkage (it only touches already-unattributed owner changes).
    """
    owner_changes = [
        c for c in changes if c.kind in _LAYOUT_OWNER_KINDS and c.caused_by_type is None
    ]
    if not owner_changes:
        return
    by_name = {t.name: t for t in new.types}
    for c in owner_changes:
        rec = by_name.get(_root_type_name(c))
        if rec is None:
            continue
        embedded = _embedded_stdlib_fields(rec)
        if not embedded:
            continue
        members = ", ".join(f"{fname} ({ftype})" for fname, ftype in embedded)
        clause = (
            f" This type embeds a standard-library type by value ({members}); "
            "its layout depends on the standard-library implementation and "
            "version, so the change may originate there."
        )
        if clause.strip() not in c.description:
            c.description += clause


# Change kinds that represent root type/enum changes (for redundancy filtering).
_ROOT_TYPE_CHANGE_KINDS: frozenset[ChangeKind] = frozenset(
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
_ALWAYS_INDEPENDENT_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_ADDED,
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ChangeKind.VAR_REMOVED,
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


def _root_type_name(c: Change) -> str:
    """Extract the root type name from a change's symbol.

    For field-level changes (e.g. ``Container::flags``), strip the last
    ``::field`` component.  For all other changes (including namespaced
    types like ``ns::MyType``), keep the full symbol to avoid collapsing
    distinct types in the same namespace.
    """
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
        if c.kind in _ROOT_TYPE_CHANGE_KINDS:
            type_name = _root_type_name(c)
            if type_name not in root_types:
                root_types[type_name] = c
    return root_types


def _compile_root_patterns(root_types: dict[str, Change]) -> dict[str, re.Pattern[str]]:
    """Pre-compile word-boundary regex patterns for each root type name."""
    return {
        name: re.compile(r"(?<![A-Za-z0-9_])" + re.escape(name) + r"(?![A-Za-z0-9_])")
        for name in root_types
    }


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
        if c.kind not in _ROOT_TYPE_CHANGE_KINDS:
            continue
        if c.kind in _DERIVED_CHANGE_KINDS:
            type_name = _root_type_name(c)
            other_roots = {k: v for k, v in root_types.items() if k != type_name}
            matched_root = _match_root_type(c, other_roots, compiled_patterns)
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
        if c.kind in _ROOT_TYPE_CHANGE_KINDS:
            continue  # already handled in first pass
        if c.kind in _ALWAYS_INDEPENDENT_KINDS or c.kind not in _DERIVED_CHANGE_KINDS:
            kept.append(c)
            continue
        # Check if this change references a (kept) root type
        matched_root = _match_root_type(c, root_types, compiled_patterns)
        if matched_root is not None:
            _mark_as_redundant(c, matched_root, root_types, redundant)
        else:
            kept.append(c)


def _filter_redundant(changes: list[Change]) -> tuple[list[Change], list[Change]]:
    """Identify changes that are consequences of a root type change.

    Returns (kept, redundant) — redundant changes are still available for audit.
    Root changes are annotated with ``caused_count`` and ``derived_symbols``.
    """
    root_types = _collect_root_types(changes)
    if not root_types:
        return changes, []

    # Pre-compile word-boundary regex patterns for all root type names once,
    # instead of recompiling per (_match_root_type call * root type) pair.
    compiled_patterns = _compile_root_patterns(root_types)

    kept: list[Change] = []
    redundant: list[Change] = []

    # First pass: classify root type changes (some may be redundant
    # if they reference another root type — nested type propagation).
    _classify_root_pass(changes, root_types, compiled_patterns, kept, redundant)

    # Second pass: classify non-root changes
    _classify_derived_pass(changes, root_types, compiled_patterns, kept, redundant)

    return kept, redundant


def _match_root_type(
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

    When *compiled_patterns* is provided, uses pre-compiled regex objects
    instead of recompiling per call (performance optimisation).
    """
    for type_name in root_types:
        if compiled_patterns is not None and type_name in compiled_patterns:
            pat = compiled_patterns[type_name]
        else:
            pat = re.compile(
                r"(?<![A-Za-z0-9_])" + re.escape(type_name) + r"(?![A-Za-z0-9_])"
            )
        if c.old_value and pat.search(c.old_value):
            return type_name
        if c.new_value and pat.search(c.new_value):
            return type_name
        if pat.search(c.description):
            return type_name
    return None


# Enum change kinds eligible for same-kind symbol-based dedup (FIX-C).
# Scoped to enum kinds only to avoid incorrectly merging legitimately
# different changes that share the same kind+symbol.
_ENUM_DEDUP_KINDS = frozenset(
    {
        ChangeKind.ENUM_MEMBER_VALUE_CHANGED,
        ChangeKind.ENUM_MEMBER_REMOVED,
        ChangeKind.ENUM_LAST_MEMBER_VALUE_CHANGED,
    }
)


def _type_used_by_value(type_str: str, bare_re: re.Pattern[str]) -> bool:
    """True if ``type_str`` names the type without a trailing ``*``."""
    if not bare_re.search(type_str):
        return False
    stripped = type_str.replace("const", "").replace("volatile", "").strip()
    for token in stripped.split(","):
        token = token.strip()
        if bare_re.search(token) and "*" not in token:
            # Token contains the bare type name without a pointer dereference.
            # This covers both by-value (T) and reference (T&) semantics —
            # both allow the caller to hold/inspect the type's size.
            return True
    return False


def _public_function_uses_type_by_value(
    snap: AbiSnapshot, bare_re: re.Pattern[str]
) -> bool:
    """True if any PUBLIC function uses the type (matched by *bare_re*) by value."""
    for f in snap.functions:
        if f.visibility not in _PUBLIC_VIS:
            continue
        if _type_used_by_value(f.return_type, bare_re):
            return True
        for p in f.params:
            if _type_used_by_value(p.type, bare_re):
                return True
    return False


def _public_variable_uses_type_by_value(
    snap: AbiSnapshot, bare_re: re.Pattern[str]
) -> bool:
    """True if any PUBLIC variable uses the type (matched by *bare_re*) by value."""
    for v in snap.variables:
        if v.visibility not in _PUBLIC_VIS:
            continue
        if _type_used_by_value(v.type, bare_re):
            return True
    return False


def _is_pointer_only_type(
    type_name: str,
    snap: AbiSnapshot,
    _re_cache: dict[str, re.Pattern[str]] | None = None,
) -> bool:
    """Return True if all PUBLIC API functions/variables use this type via pointer only.

    A type is pointer-only (opaque-handle pattern) when every function param/return
    that references it uses a raw pointer (`T*`) — never a bare by-value or reference
    (`T`, `T&`) occurrence.  References are treated as non-opaque usage because a
    caller could still hold the referent by value.

    Uses pre-compiled word-boundary regex to avoid substring false-positives.
    *_re_cache* can supply a shared regex cache to avoid recompilation across calls.
    """
    if _re_cache is not None and type_name in _re_cache:
        bare_re = _re_cache[type_name]
    else:
        bare_re = re.compile(r"\b" + re.escape(type_name) + r"\b")
        if _re_cache is not None:
            _re_cache[type_name] = bare_re

    if _public_function_uses_type_by_value(
        snap, bare_re
    ) or _public_variable_uses_type_by_value(snap, bare_re):
        return False
    return True


def _has_public_pointer_factory(
    type_name: str,
    snap: AbiSnapshot,
    _factory_re_cache: dict[str, re.Pattern[str]] | None = None,
) -> bool:
    """True if snapshot has at least one PUBLIC function returning exactly ``type_name*``.

    Uses word-boundary regex to avoid substring false-positives such as
    ``type_name="Context"`` matching ``SSLContext*``.
    """
    # Match: optional const/volatile, then word-boundary type name, then `*`
    if _factory_re_cache is not None and type_name in _factory_re_cache:
        factory_re = _factory_re_cache[type_name]
    else:
        factory_re = re.compile(r"\b" + re.escape(type_name) + r"\s*\*")
        if _factory_re_cache is not None:
            _factory_re_cache[type_name] = factory_re
    for f in snap.functions:
        if f.visibility not in _PUBLIC_VIS:
            continue
        rt = f.return_type or ""
        if factory_re.search(rt) and "&" not in rt:
            return True
    return False


def _opaque_usage_index(
    candidates: set[str],
    snap: AbiSnapshot,
    bare_re_cache: dict[str, re.Pattern[str]],
    factory_re_cache: dict[str, re.Pattern[str]],
) -> tuple[set[str], set[str]]:
    """Single pass over the public surface → ``(used_by_value, has_pointer_factory)``.

    ``_filter_opaque_size_changes`` previously called ``_is_pointer_only_type`` and
    ``_has_public_pointer_factory`` *per candidate*, each rescanning every public
    function/variable with a word-boundary regex — O(candidates × functions) with
    a regex per pair (``type_churn`` n=4000: ~3.2 M regex searches). This walks the
    surface once and uses an Aho-Corasick prefilter (:class:`_SubstringMatcher`)
    to narrow each type string to the candidates that actually occur in it; the
    *decision* is still made by the same ``_type_used_by_value`` / factory regex
    oracle, so the result is identical — the prefilter only drops pairs that could
    never match.
    """
    used_by_value: set[str] = set()
    has_factory: set[str] = set()
    if not candidates:
        return used_by_value, has_factory
    ac = _SubstringMatcher(candidates)

    def _bare(c: str) -> re.Pattern[str]:
        r = bare_re_cache.get(c)
        if r is None:
            r = re.compile(r"\b" + re.escape(c) + r"\b")
            bare_re_cache[c] = r
        return r

    def _factory(c: str) -> re.Pattern[str]:
        r = factory_re_cache.get(c)
        if r is None:
            r = re.compile(r"\b" + re.escape(c) + r"\s*\*")
            factory_re_cache[c] = r
        return r

    def _scan_by_value(text: str | None) -> None:
        if not text:
            return
        for c in ac.find(text):
            if c not in used_by_value and _type_used_by_value(text, _bare(c)):
                used_by_value.add(c)

    for f in snap.functions:
        if f.visibility not in _PUBLIC_VIS:
            continue
        rt = f.return_type or ""
        # Pointer factory: a public function returning ``T*`` (never ``T&``).
        if rt and "&" not in rt:
            for c in ac.find(rt):
                if c not in has_factory and _factory(c).search(rt):
                    has_factory.add(c)
        _scan_by_value(f.return_type)
        for p in f.params:
            _scan_by_value(p.type)

    for v in snap.variables:
        if v.visibility not in _PUBLIC_VIS:
            continue
        _scan_by_value(v.type)

    return used_by_value, has_factory


def _filter_opaque_size_changes(
    changes: list[Change], old: AbiSnapshot, new: AbiSnapshot
) -> tuple[list[Change], list[Change]]:
    """Suppress size-only growth for pointer-only opaque-handle patterns.

    Narrow rule (case62):
    - type has TYPE_SIZE_CHANGED/STRUCT_SIZE_CHANGED
    - API usage is pointer-only in both old/new snapshots
    - and change set for that type indicates *compatible append* pattern only:
      at least one TYPE_FIELD_ADDED_COMPATIBLE, and no remove/offset/type/base
      drift changes for that type.
    """
    size_change_types: set[str] = {
        _root_type_name(c)
        for c in changes
        if c.kind in (ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.STRUCT_SIZE_CHANGED)
    }
    if not size_change_types:
        return changes, []

    by_type: dict[str, set[ChangeKind]] = {t: set() for t in size_change_types}
    for c in changes:
        t = _root_type_name(c)
        if t in by_type:
            by_type[t].add(c.kind)

    forbidden = {
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_BASE_CHANGED,
        ChangeKind.TYPE_VTABLE_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_ALIGNMENT_CHANGED,
    }

    # Candidate types: a size change whose change set is a *compatible append*
    # only (the narrow case62 rule). Resolve this cheaply first so the usage
    # index below is built over the small candidate set, not every changed type.
    candidates = {
        t
        for t in size_change_types
        if ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE in by_type[t]
        and not (by_type[t] & forbidden)
    }

    # One pass per snapshot instead of a per-candidate full rescan: opaque ⟺
    # the type is never used by value in either snapshot *and* has a pointer
    # factory (T*) in both (the case07 regression guard). Shared regex caches so
    # each candidate's patterns compile once across both snapshots.
    _bare_re_cache: dict[str, re.Pattern[str]] = {}
    _factory_re_cache: dict[str, re.Pattern[str]] = {}
    old_byval, old_factory = _opaque_usage_index(
        candidates, old, _bare_re_cache, _factory_re_cache
    )
    new_byval, new_factory = _opaque_usage_index(
        candidates, new, _bare_re_cache, _factory_re_cache
    )
    opaque_types: set[str] = {
        t
        for t in candidates
        if t not in old_byval
        and t not in new_byval
        and t in old_factory
        and t in new_factory
    }

    if not opaque_types:
        return changes, []

    # Only SIZE changes are moved to the filtered list; TYPE_FIELD_ADDED_COMPATIBLE
    # for the same type intentionally passes through — it is informational and helps
    # reviewers understand *why* the size grew, while not affecting the verdict.
    kept = []
    filtered = []
    for c in changes:
        if (
            c.kind in (ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.STRUCT_SIZE_CHANGED)
            and _root_type_name(c) in opaque_types
        ):
            filtered.append(c)
        else:
            kept.append(c)
    return kept, filtered


def _filter_reserved_field_renames(changes: list[Change]) -> list[Change]:
    """Suppress TYPE_FIELD_REMOVED / STRUCT_FIELD_REMOVED for reserved-field renames.

    When _diff_reserved_fields emits USED_RESERVED_FIELD for a struct field
    that was renamed (e.g. __reserved1 -> priority), the _diff_types and
    _diff_dwarf detectors also emit TYPE_FIELD_REMOVED / STRUCT_FIELD_REMOVED
    for the old name.  These are false positives: the layout is unchanged
    (same offset, same size — enforced by _diff_reserved_fields).

    Suppression rule (narrow, per plan v2):
      For each USED_RESERVED_FIELD(symbol=S, old_value=F_old):
        remove TYPE_FIELD_REMOVED(symbol=S) with description containing F_old
        remove STRUCT_FIELD_REMOVED(symbol="S::F_old") or similar
      Also remove the redundant TYPE_FIELD_ADDED_COMPATIBLE that fires because
      the field was detected as "added" with the new name.
    """
    _SUPPRESS_ON_RESERVED = frozenset(
        {
            ChangeKind.TYPE_FIELD_REMOVED,
            ChangeKind.STRUCT_FIELD_REMOVED,
            ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
            # TYPE_FIELD_ADDED: class types and polymorphic records emit this instead of
            # TYPE_FIELD_ADDED_COMPATIBLE; must suppress here too.
            ChangeKind.TYPE_FIELD_ADDED,
            # FIELD_RENAMED: suppressed only when the exact old_value matches the reserved
            # field name (see == check below — not substring). Safe for structs with both
            # reserved and non-reserved renames in the same diff.
            ChangeKind.FIELD_RENAMED,
        }
    )

    # Collect (struct_name, old_field_name, new_field_name) for each USED_RESERVED_FIELD
    reserved_renames: list[tuple[str, str, str]] = []
    for c in changes:
        if c.kind == ChangeKind.USED_RESERVED_FIELD:
            reserved_renames.append((c.symbol, c.old_value or "", c.new_value or ""))

    if not reserved_renames:
        return changes

    result: list[Change] = []
    for c in changes:
        if c.kind not in _SUPPRESS_ON_RESERVED:
            result.append(c)
            continue

        suppressed = False
        for struct_name, old_field, new_field in reserved_renames:
            if c.symbol != struct_name and not c.symbol.startswith(f"{struct_name}::"):
                continue
            # Exact field-name match via symbol suffix to avoid substring
            # false-positives (e.g. "flag" matching inside "flags").
            if old_field and c.symbol == f"{struct_name}::{old_field}":
                suppressed = True
                break
            if (
                new_field
                and c.kind
                in (ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE, ChangeKind.TYPE_FIELD_ADDED)
                and (c.symbol == f"{struct_name}::{new_field}")
            ):
                suppressed = True
                break
        if not suppressed:
            result.append(c)

    return result


def _dedup_exact(changes: list[Change]) -> list[Change]:
    """Pass 1: collapse entries with the same (kind, description)."""
    result: list[Change] = []
    seen: set[tuple[str, str]] = set()
    for c in changes:
        key = (c.kind.value, c.description)
        if key in seen:
            continue
        seen.add(key)
        result.append(c)
    return result


def _dedup_enum_same_kind(changes: list[Change]) -> list[Change]:
    """Pass 2: for enum change kinds, keep the best entry per (kind, symbol).

    Prefers entries with populated ``old_value``/``new_value`` fields,
    using longer description as tiebreaker.
    """
    best_enum: dict[tuple[str, str], Change] = {}
    for c in changes:
        if c.kind not in _ENUM_DEDUP_KINDS:
            continue
        key = (c.kind.value, c.symbol)
        if key not in best_enum:
            best_enum[key] = c
        else:
            existing = best_enum[key]
            c_has_vals = bool(c.old_value) or bool(c.new_value)
            e_has_vals = bool(existing.old_value) or bool(existing.new_value)
            if c_has_vals and not e_has_vals:
                best_enum[key] = c
            elif not c_has_vals and e_has_vals:
                pass  # keep existing
            elif len(c.description) > len(existing.description):
                best_enum[key] = c

    result: list[Change] = []
    for c in changes:
        if c.kind in _ENUM_DEDUP_KINDS:
            key = (c.kind.value, c.symbol)
            if best_enum.get(key) is not c:
                continue  # not the winner — drop
        result.append(c)
    return result


def _dedup_cross_kind(changes: list[Change]) -> list[Change]:
    """Pass 3: drop a DWARF finding when an equivalent AST finding exists.

    Handles both exact symbol matches and parent-type matches for
    field-qualified symbols (FIX-F).
    """
    ast_findings: set[tuple[str, str]] = set()
    for c in changes:
        ast_findings.add((c.kind.value, c.symbol))

    _DWARF_FIELD_LEVEL_KINDS = {
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
    }

    result: list[Change] = []
    for c in changes:
        equiv_ast_kinds = _DWARF_TO_AST_EQUIV.get(c.kind)
        if equiv_ast_kinds:
            # Exact symbol match
            if any((ak.value, c.symbol) in ast_findings for ak in equiv_ast_kinds):
                continue

            # Parent-type match (FIX-F): "Point::x" → check "Point"
            # Only for field-level changes; type-level changes (size, alignment)
            # must not match parent — "Outer::Inner" is a nested type, not a field.
            if c.kind in _DWARF_FIELD_LEVEL_KINDS and "::" in c.symbol:
                parent = c.symbol.rsplit("::", 1)[0]
                if any((ak.value, parent) in ast_findings for ak in equiv_ast_kinds):
                    continue

        result.append(c)
    return result


def _deduplicate_ast_dwarf(changes: list[Change]) -> list[Change]:
    """Remove DWARF findings that duplicate an AST finding for the same symbol.

    Three dedup passes:

    1. **Exact dedup** — collapses entries with the same ``(kind, description)``.

    2. **Same-kind symbol dedup** (FIX-C) — for enum change kinds only,
       collapses entries with the same ``(kind, symbol)`` but different
       descriptions (e.g. AST says "Color::GREEN" while DWARF says
       "Color::GREEN (1 → 2)"). Keeps the entry with populated
       ``old_value``/``new_value`` fields, or the longer description as
       tiebreaker.

    3. **Cross-kind dedup** — drops a DWARF finding when an equivalent AST
       finding exists for the *same full symbol* (e.g. STRUCT_SIZE_CHANGED for
       ``S`` is dropped when TYPE_SIZE_CHANGED for ``S`` is already present).
       Also handles parent-type matching for field-qualified symbols (FIX-F):
       ``STRUCT_FIELD_OFFSET_CHANGED`` for ``Point::x`` is dropped when
       ``TYPE_FIELD_OFFSET_CHANGED`` for ``Point`` is already present.
    """
    stage1 = _dedup_exact(changes)
    stage2 = _dedup_enum_same_kind(stage1)
    return _dedup_cross_kind(stage2)


def _deduplicate_cross_detector(changes: list[Change]) -> list[Change]:
    """Remove cross-detector duplicates that the per-detector guards may miss.

    Centralised dedup applied after all detectors have run.  Uses
    (change_category, symbol) as the dedup key to collapse overlapping
    reports from different detectors for the same logical event.

    Categories:
    - "func_removal": FUNC_REMOVED, FUNC_REMOVED_ELF_ONLY
    - "func_addition": FUNC_ADDED
    - "var_removal": VAR_REMOVED
    - "var_addition": VAR_ADDED

    Within each category, the first occurrence wins (preserving detector
    priority order from the ``compare()`` spec list).
    """
    _DEDUP_CATEGORIES: dict[ChangeKind, str] = {
        ChangeKind.FUNC_REMOVED: "func_removal",
        ChangeKind.FUNC_REMOVED_ELF_ONLY: "func_removal",
        ChangeKind.FUNC_ADDED: "func_addition",
        ChangeKind.VAR_REMOVED: "var_removal",
        ChangeKind.VAR_ADDED: "var_addition",
        # Version node removal and version definition removal both fire for
        # the same version string.  Keep the more specific node-level change.
        ChangeKind.SYMBOL_VERSION_NODE_REMOVED: "version_def_removal",
        ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED: "version_def_removal",
    }
    # A symbol-version-node bump (e.g. LLVM_17 -> LLVM_18.1 applied to every
    # symbol during a major release) makes BOTH version detectors fire per
    # symbol with the same old->new transition: SYMBOL_MOVED_VERSION_NODE (the
    # node label moved) and SYMBOL_VERSION_ALIAS_CHANGED (the default version
    # changed, old not retained as an alias). They describe one event; drop the
    # alias-change duplicate where a node move already covers the same
    # (symbol, old -> new), keeping the node-level change. Halves the
    # version-bump noise on real libraries (libLLVM 17->18: ~46k instead of
    # ~92k risk findings).
    #
    # The match keys on (symbol, old_value, new_value): both detectors live in
    # diff_versioning.py and populate old_value/new_value with the same version
    # node labels for one bump, so the tuples coincide. If that ever diverges
    # the dedup simply no-ops (both findings are kept) — a missed dedup, never a
    # dropped real change — so this stays a safe, best-effort filter.
    moved_transitions: set[tuple[str, str | None, str | None]] = {
        (c.symbol, c.old_value, c.new_value)
        for c in changes
        if c.kind is ChangeKind.SYMBOL_MOVED_VERSION_NODE
    }

    seen: set[tuple[str, str]] = set()
    result: list[Change] = []
    for c in changes:
        # Only collapse the alias-change into a co-reported node-move when the
        # old default version is NOT retained as an alias — that is the case the
        # node-move already fully describes. When the old alias IS retained the
        # alias-change carries distinct, *compatible* information (old consumers
        # still resolve) that the node-move's "will not find this symbol"
        # wording would otherwise misrepresent, so it must survive.
        if (
            c.kind is ChangeKind.SYMBOL_VERSION_ALIAS_CHANGED
            and SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER in (c.description or "")
            and (c.symbol, c.old_value, c.new_value) in moved_transitions
        ):
            continue
        cat = _DEDUP_CATEGORIES.get(c.kind)
        if cat is not None:
            key = (cat, c.symbol)
            if key in seen:
                continue
            seen.add(key)
        result.append(c)
    return result


_STRUCTURAL_TYPE_CHANGE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_SIZE_CHANGED,
        ChangeKind.TYPE_ALIGNMENT_CHANGED,
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_ALIGNMENT_CHANGED,
    }
)


# Source file extensions that indicate an implementation (non-header) file.
_IMPL_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm"})


def _is_impl_source(source_location: str | None) -> bool:
    """Check if a source_location path refers to an implementation file."""
    if not source_location:
        return False
    # source_location may be "foo.c:42" — strip line number
    path = source_location.split(":")[0] if ":" in source_location else source_location
    # Get file extension
    dot = path.rfind(".")
    if dot < 0:
        return False
    ext = path[dot:].lower()
    return ext in _IMPL_EXTENSIONS


def _find_opaque_types(snap: AbiSnapshot) -> set[str]:
    """Find types that are opaque to consumers.

    A type is opaque when:

    1. castxml marks it as ``incomplete`` (``is_opaque=True``) — the public
       header has only a forward declaration, OR
    2. The type definition is in an implementation file (.c/.cpp) AND all
       public-API references use pointers (never by value).  This handles
       DWARF mode where castxml is not used but DWARF's ``DW_AT_decl_file``
       reveals the type is implementation-private.
    """
    opaque: set[str] = set()

    for t in snap.types:
        if t.is_opaque:
            opaque.add(t.name)
        elif _is_impl_source(t.source_location):
            # Type is defined in an implementation file — only consider it
            # opaque if all API references are through pointers.
            opaque.add(t.name)

    if not opaque:
        return set()

    by_value_types = _find_by_value_types(snap, opaque)

    return opaque - by_value_types


def _find_by_value_types(snap: AbiSnapshot, opaque: set[str]) -> set[str]:
    """Return the subset of *opaque* types that any public function/variable uses by value."""
    by_value_types: set[str] = set()
    for func in snap.functions:
        if func.visibility not in _PUBLIC_VIS:
            continue
        rt = func.return_type.strip()
        for tname in opaque:
            if tname in rt and not (rt.endswith("*") or "* " in rt):
                by_value_types.add(tname)
        for param in func.params:
            pt = param.type.strip()
            for tname in opaque:
                if tname in pt and param.pointer_depth == 0 and not pt.endswith("*"):
                    by_value_types.add(tname)
    # Also check variables — a public variable of this type means it's by-value
    for var in snap.variables:
        if var.visibility not in _PUBLIC_VIS:
            continue
        vt = var.type.strip()
        for tname in opaque:
            if tname in vt and not (vt.endswith("*") or "* " in vt):
                by_value_types.add(tname)
    return by_value_types


def _downgrade_opaque_type_changes(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Suppress structural type changes for opaque types.

    When a type is opaque in both old and new snapshots (forward-declared only
    in headers, or defined in an implementation file with pointer-only API),
    consumers never see its layout.  Size/field changes are invisible and
    should not be classified as BREAKING.
    """
    opaque_old = _find_opaque_types(old)
    opaque_new = _find_opaque_types(new)
    # Type must be opaque in BOTH snapshots to suppress changes
    opaque = opaque_old & opaque_new

    if not opaque:
        return changes

    result: list[Change] = []
    for c in changes:
        if c.kind in _STRUCTURAL_TYPE_CHANGE_KINDS:
            # Extract type name from symbol (may be "TypeName" or "ns::TypeName::field")
            type_name = _root_type_name(c)
            if type_name in opaque:
                # Suppress entirely: the type is opaque (forward-declared only)
                # so layout changes are invisible to consumers.
                continue
        result.append(c)
    return result


# ChangeKinds that should be downgraded when the type is opaque in both snapshots.
_OPAQUE_DOWNGRADEABLE: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_SIZE_CHANGED,
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_ALIGNMENT_CHANGED,
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_ALIGNMENT_CHANGED,
    }
)


def _downgrade_opaque_struct_changes(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Downgrade BREAKING changes for types that are opaque in both snapshots.

    If a type is forward-declared only (is_opaque=True) in both old and new
    snapshots, consumers cannot allocate, embed, or sizeof the type — they
    only hold pointers. Layout changes detected via DWARF are invisible to
    consumers and should be classified as compatible field additions.
    """
    # Build set of types that are opaque in both snapshots.
    old_opaque = {t.name for t in old.types if t.is_opaque}
    new_opaque = {t.name for t in new.types if t.is_opaque}
    # Also check: type exists in one but not the other (forward-decl only in header,
    # full definition only in DWARF) — treat as opaque if the header-level type
    # doesn't exist OR is opaque.
    old_type_names = {t.name for t in old.types}
    new_type_names = {t.name for t in new.types}

    # A type is "opaque to consumers" if:
    # - It's opaque in both old and new, OR
    # - It doesn't appear in the header-level type list at all (DWARF-only)
    #   AND it's not embedded by-value in any non-opaque exported struct
    opaque_types = (old_opaque & new_opaque) | (
        (old_opaque - new_type_names) | (new_opaque - old_type_names)
    )

    if not opaque_types:
        return changes

    # Check that opaque types are not embedded by-value in non-opaque structs
    non_opaque_old = {t.name: t for t in old.types if not t.is_opaque}
    non_opaque_new = {t.name: t for t in new.types if not t.is_opaque}
    embedded_types: set[str] = set()
    for type_map in (non_opaque_old, non_opaque_new):
        for t in type_map.values():
            for f in t.fields:
                # If a field type matches an opaque type name (not as pointer),
                # the type is embedded by-value and layout changes matter
                ftype = f.type.rstrip(" *&")
                if ftype in opaque_types and "*" not in f.type:
                    embedded_types.add(ftype)

    truly_opaque = opaque_types - embedded_types
    if not truly_opaque:
        return changes

    result: list[Change] = []
    for c in changes:
        if c.kind in _OPAQUE_DOWNGRADEABLE and c.symbol in truly_opaque:
            # Downgrade: replace with TYPE_FIELD_ADDED_COMPATIBLE
            result.append(
                Change(
                    kind=ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
                    symbol=c.symbol,
                    description=f"(opaque struct) {c.description}",
                    old_value=c.old_value,
                    new_value=c.new_value,
                    source_location=c.source_location,
                )
            )
        else:
            result.append(c)
    return result
