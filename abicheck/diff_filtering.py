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

"""Post-processing enrichment and opaque-type filtering of raw findings.

Root-type redundancy and cross-tier/cross-detector deduplication moved to
their `compare/` owners (`compare.finding_redundancy`, `compare.finding_dedup`).
"""

from __future__ import annotations

import re
from collections import deque

from .compare.finding_dedup import (
    ENUM_QUALIFICATION_KINDS,
    canonicalize_enum_symbol,
    enum_canonical_names,
)
from .compare.finding_redundancy import root_type_name
from .compare.opaque_struct_downgrade import (
    OPAQUE_DOWNGRADEABLE_KINDS,
    downgrade_opaque_struct_changes,
    resolve_struct_change_entity_id,
    struct_change_record_name,
)
from .compare.opaque_struct_types import find_opaque_struct_types
from .compare.opaque_types import (
    find_by_value_types,
    find_opaque_types,
    is_impl_source,
)
from .compare.public_surface import impact_candidate_functions
from .model import AbiSnapshot, Function
from .model.change import Change
from .model.change_catalog.kinds import ChangeKind
from .model.name_heuristics import NameHeuristicEffect, register_name_heuristic
from .model.surface_facts import is_abi_visible

# Back-compat aliases: the ADR-063 Phase 2/10 migrations moved the
# opaque-type indices, their construction, and the struct-downgrade
# machinery that consumes them into their `compare/` owner (round 10:
# `opaque_struct_downgrade.py`, split out of this file once its own
# no-growth debt baseline was exceeded -- see that module's docstring),
# but `tests/test_cov95_diff_filtering.py`/`tests/test_opaque_identity_
# tiers.py` import several of them from here by their private names.
# Module-level bindings rather than `as`-aliased imports, since ruff only
# recognizes the identical-name form as an intentional re-export.
_find_opaque_types = find_opaque_types
_find_by_value_types = find_by_value_types
_find_opaque_struct_types = find_opaque_struct_types
_is_impl_source = is_impl_source
_OPAQUE_DOWNGRADEABLE = OPAQUE_DOWNGRADEABLE_KINDS
_struct_change_record_name = struct_change_record_name
_resolve_struct_change_entity_id = resolve_struct_change_entity_id
_downgrade_opaque_struct_changes = downgrade_opaque_struct_changes


# ── Post-processing: enrich and deduplicate ────────────────────────────────

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
        # Fine-grained class-layout descriptor kinds (layout-closure work): each carries the owner type name in Change.symbol, so affected-symbol enrichment must scan them too — otherwise a layout-only BREAKING finding gets no affected_symbols and app-compat filtering could mark a consumer as unaffected (Codex review #345).
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
    for t in old.declarations.types:
        if t.source_location:
            type_loc[t.name] = t.source_location
    for t in new.declarations.types:
        if t.source_location:
            type_loc.setdefault(t.name, t.source_location)

    func_loc: dict[str, str] = {}
    for f in old.declarations.functions:
        if f.source_location:
            func_loc[f.mangled] = f.source_location
    for f in new.declarations.functions:
        if f.source_location:
            func_loc.setdefault(f.mangled, f.source_location)

    var_loc: dict[str, str] = {}
    for v in old.declarations.variables:
        if v.source_location:
            var_loc[v.mangled] = v.source_location
    for v in new.declarations.variables:
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


def _qualified_by_mangled(entries: list[tuple[str, object]]) -> dict[str, str]:
    """Return mangled → qualified-name for declarations that carry C++ qualification.

    ``entries`` is a list of ``(mangled, decl)`` pairs (a ``Function`` or
    ``Variable``). Prefers ``decl.name`` when it already contains ``"::"``
    (a frontend/hand-built snapshot that qualifies names directly), but that
    is not the common case: the default CastXML backend stores only the
    bare declaration name (``dumper_castxml.py``'s ``parse_variables()``/
    ``function_display_name()`` both read the raw castxml ``name``
    attribute, never walking namespace ``context`` the way
    ``_CastxmlParser._qualified_name()`` does for constants) — so trusting
    ``decl.name`` alone left this whole mechanism a no-op against real
    CastXML dumps of a namespaced declaration (Codex review, fresh
    evidence). Falls back to demangling the *mangled* linker symbol
    directly, which is backend-independent: the Itanium mangling always
    encodes the true namespace/class qualification. Batched via
    ``demangle_batch`` (one ``c++filt``/``cxxfilt`` pass) rather than one
    subprocess per symbol.
    """
    from .demangle import demangle_batch, strip_signature

    qualified: dict[str, str] = {}
    to_demangle: list[str] = []
    for mangled, decl in entries:
        if mangled in qualified:
            continue
        name = getattr(decl, "name", None)
        if name and "::" in name:
            qualified[mangled] = name
        else:
            to_demangle.append(mangled)
    if to_demangle:
        demangled = demangle_batch(to_demangle)
        for mangled in to_demangle:
            d = demangled.get(mangled)
            if d:
                qualified[mangled] = strip_signature(d)
    return qualified


def _qualified_functions_by_mangled(snap: AbiSnapshot | None) -> dict[str, str]:
    """Return mangled/exported function names that have C++ qualification."""
    if snap is None or not _safe_index(snap):
        return {}
    return _qualified_by_mangled(
        list((getattr(snap, "_func_by_mangled", None) or {}).items())
    )


def _qualified_variables_by_mangled(snap: AbiSnapshot | None) -> dict[str, str]:
    """Return mangled/exported variable names that have C++ qualification."""
    if snap is None or not _safe_index(snap):
        return {}
    return _qualified_by_mangled(
        list((getattr(snap, "_var_by_mangled", None) or {}).items())
    )


def _qualified_name_for_change(
    c: Change,
    old_qualified: dict[str, str],
    new_qualified: dict[str, str],
) -> str | None:
    """Safely recover a C++ qualified name for a function or variable change.

    *old_qualified*/*new_qualified* are expected to already merge the
    function and variable mangled-name indices (a global symbol name is
    linker-unique regardless of whether it names a function or a variable,
    so the merge can never collide) — this covers every "changed in place"
    variable kind (``VAR_TYPE_CHANGED``, ``VAR_BECAME_CONST``,
    ``VAR_ACCESS_CHANGED``, ...), not just additions/removals, the same way
    it already did for functions (Codex review).
    """
    if c.kind in (
        ChangeKind.FUNC_ADDED,
        ChangeKind.FUNC_ADDED_ELF_ONLY,
        ChangeKind.VAR_ADDED,
    ):
        return new_qualified.get(c.symbol)
    if c.kind in (
        ChangeKind.FUNC_REMOVED,
        ChangeKind.FUNC_REMOVED_ELF_ONLY,
        ChangeKind.VAR_REMOVED,
        ChangeKind.VAR_REMOVED_ELF_ONLY,
    ):
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

    old_qualified = _qualified_functions_by_mangled(
        old
    ) | _qualified_variables_by_mangled(old)
    new_qualified = _qualified_functions_by_mangled(
        new
    ) | _qualified_variables_by_mangled(new)
    old_enum_names = enum_canonical_names(old)
    new_enum_names = enum_canonical_names(new)

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
                loc = type_loc.get(root_type_name(c))
            if loc:
                c.source_location = loc
        if not c.qualified_name:
            qual = _qualified_name_for_change(c, old_qualified, new_qualified)
            if not qual and c.kind in ENUM_QUALIFICATION_KINDS:
                # Scoped to the four enum kinds the bridge is meant for -- see ENUM_QUALIFICATION_KINDS's own docstring (Codex review, fresh evidence: a second unconditional call site, this one in the enrichment pass rather than the dedup pass, had the identical unrelated-symbol-collision risk).
                qual = canonicalize_enum_symbol(
                    c.symbol, old_enum_names
                ) or canonicalize_enum_symbol(c.symbol, new_enum_names)
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
    for t in old.declarations.types:
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
        type_name = root_type_name(c)
        funcs = type_to_funcs.get(type_name, set())
        mangled_funcs = type_to_mangled.get(type_name, set())
        if funcs:
            # Store both demangled and mangled names for cross-format matching
            c.affected_symbols = sorted(funcs | mangled_funcs)


def _enrich_affected_symbols(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot | None = None,
) -> None:
    """For type/enum changes, find exported functions that use the affected type."""
    # Only compute if there are type-related changes
    type_changes = [c for c in changes if c.kind in _TYPE_CHANGE_KINDS]
    if not type_changes:
        return

    # Collect affected type names; strip field qualifiers like
    # "ns::Container::flags" → "ns::Container"
    affected_types: set[str] = {root_type_name(c) for c in type_changes}
    if not affected_types:
        return

    old_pub = impact_candidate_functions(old)

    # One Aho-Corasick matcher over the affected type names, shared by both
    # index builders below — built once so neither pays the former
    # O(types × functions) / O(types × fields) substring cross-product.
    matcher = _SubstringMatcher(affected_types)

    # Build type→functions mapping from old snapshot, storing both demangled (display) and mangled (appcompat matching) names (FIX-A Part 3).
    type_to_funcs, type_to_mangled = _build_type_to_funcs(
        affected_types, old_pub, matcher
    )

    # Also check if types are embedded in struct fields used by functions (Container has a Leaf field → functions taking Container* are affected).
    type_embeds = _build_type_embed_index(affected_types, old, matcher)
    from .compare.type_symbol_disambiguation import attribute_scoped_types

    attribute_scoped_types(
        affected_types, old, new, old_pub, type_to_funcs, type_to_mangled, type_embeds
    )

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
    # ADR-063 2B: the owner by the finding's identity, never a last-wins
    # bare-name dict; an ambiguous owner gets no clause (informational only).
    from .compare.record_lookup import RecordLookup

    records = RecordLookup(new.declarations.types)
    # One scan per record, not per finding: a wide record emits a field-offset
    # change per field, and rescanning every field for each of them was
    # quadratic in the record's width. Keyed by identity -- the records live in
    # `new` for the whole loop, so an id cannot be reused meanwhile.
    embedded_by_record: dict[int, list[tuple[str, str]]] = {}
    for c in owner_changes:
        rec = records.resolve(root_type_name(c), c.entity_id)
        if rec is None:
            continue
        embedded = embedded_by_record.get(id(rec))
        if embedded is None:
            embedded = embedded_by_record[id(rec)] = _embedded_stdlib_fields(rec)
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


def _cached_bare_re(name: str, cache: dict[str, re.Pattern[str]]) -> re.Pattern[str]:
    """Return (and memoize in ``cache``) the word-boundary regex for ``name``."""
    r = cache.get(name)
    if r is None:
        r = re.compile(r"\b" + re.escape(name) + r"\b")
        cache[name] = r
    return r


def _cached_factory_re(name: str, cache: dict[str, re.Pattern[str]]) -> re.Pattern[str]:
    """Return (and memoize in ``cache``) the pointer-factory regex ``\\bname\\s*\\*``."""
    r = cache.get(name)
    if r is None:
        r = re.compile(r"\b" + re.escape(name) + r"\s*\*")
        cache[name] = r
    return r


def _record_by_value_uses(
    text: str | None,
    ac: _SubstringMatcher,
    bare_re_cache: dict[str, re.Pattern[str]],
    used_by_value: set[str],
) -> None:
    """Add to ``used_by_value`` every candidate that ``text`` uses by value."""
    if not text:
        return
    for c in ac.find(text):
        if c not in used_by_value and _type_used_by_value(
            text, _cached_bare_re(c, bare_re_cache)
        ):
            used_by_value.add(c)


def _record_factory_returns(
    return_type: str | None,
    ac: _SubstringMatcher,
    factory_re_cache: dict[str, re.Pattern[str]],
    has_factory: set[str],
) -> None:
    """Add to ``has_factory`` every candidate ``return_type`` returns as a pointer factory."""
    # Pointer factory: a public function returning ``T*`` (never ``T&``).
    rt = return_type or ""
    if not rt or "&" in rt:
        return
    for c in ac.find(rt):
        if c not in has_factory and _cached_factory_re(c, factory_re_cache).search(rt):
            has_factory.add(c)


def _opaque_usage_index(
    candidates: set[str],
    snap: AbiSnapshot,
    bare_re_cache: dict[str, re.Pattern[str]],
    factory_re_cache: dict[str, re.Pattern[str]],
) -> tuple[set[str], set[str]]:
    """Single pass over the public surface → ``(used_by_value, has_pointer_factory)``.

    ``_filter_opaque_size_changes`` previously called a pointer-only and a
    pointer-factory check *per candidate*, each rescanning every public
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

    for f in snap.declarations.functions:
        if not is_abi_visible(f):
            continue
        _record_factory_returns(f.return_type, ac, factory_re_cache, has_factory)
        _record_by_value_uses(f.return_type, ac, bare_re_cache, used_by_value)
        for p in f.params:
            _record_by_value_uses(p.type, ac, bare_re_cache, used_by_value)

    for v in snap.declarations.variables:
        if not is_abi_visible(v):
            continue
        _record_by_value_uses(v.type, ac, bare_re_cache, used_by_value)

    return used_by_value, has_factory


#: Registered name heuristics (design-hardening Phase 5). Both search a type
#: string for a declared type's *spelling* (word-boundary match), never its
#: identity, so both may only *lower*: one folds a derived finding into the
#: root-type change it mentions, the other drops an opaque handle's size
#: change. #1218 names the structural replacement (type identity and real
#: signature edges); until then they stay registered and lowering-only.
OPAQUE_HANDLE_USAGE = register_name_heuristic(
    "opaque_handle_usage",
    owner=__name__,
    effect=NameHeuristicEffect.LOWER_CONFIDENCE,
    lowers_from=(
        "STRUCT_ALIGNMENT_CHANGED",
        "STRUCT_FIELD_OFFSET_CHANGED",
        "STRUCT_FIELD_REMOVED",
        "STRUCT_FIELD_TYPE_CHANGED",
        "STRUCT_SIZE_CHANGED",
        "TYPE_BASE_CHANGED",
        "TYPE_FIELD_OFFSET_CHANGED",
        "TYPE_FIELD_REMOVED",
        "TYPE_FIELD_TYPE_CHANGED",
        "TYPE_SIZE_CHANGED",
        "TYPE_VTABLE_CHANGED",
    ),
    description=(
        "a type spelled only as T* in public signatures, with a T* factory, "
        "is an opaque handle whose size change is not consumer-visible"
    ),
    matcher=_opaque_usage_index,
    helpers=(_cached_bare_re, _cached_factory_re),
)


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
        root_type_name(c)
        for c in changes
        if c.kind in (ChangeKind.TYPE_SIZE_CHANGED, ChangeKind.STRUCT_SIZE_CHANGED)
    }
    if not size_change_types:
        return changes, []

    by_type: dict[str, set[ChangeKind]] = {t: set() for t in size_change_types}
    for c in changes:
        t = root_type_name(c)
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
    old_byval, old_factory = OPAQUE_HANDLE_USAGE.apply(
        candidates,
        snap=old,
        bare_re_cache=_bare_re_cache,
        factory_re_cache=_factory_re_cache,
    )
    new_byval, new_factory = OPAQUE_HANDLE_USAGE.apply(
        candidates,
        snap=new,
        bare_re_cache=_bare_re_cache,
        factory_re_cache=_factory_re_cache,
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
            and root_type_name(c) in opaque_types
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

    **Identity-tiered since ADR-063 Phase 2's post-parse consumer
    migration.** A change is matched against the opaque index by its own
    :class:`~abicheck.model.identity_tiers.StableEntityId` first and by its
    display spelling second (:meth:`~abicheck.compare.opaque_types.OpaqueTypeIndex.contains`). The stable
    tier closes a real false *negative*: ``Change.symbol`` for a type
    finding is rendered bare on some paths and qualified on others, while
    ``RecordType.name`` is bare on the header backends and namespace-baked
    on DWARF, so a string compare could miss a declaration both sides agree
    on -- where two matching stable ``EntityId``s prove it.

    **Visible-namesake exclusion (known-gap
    ``opaque-type-suppression-still-falls-back-to-bare-recordtypename``).** A
    bare-``RecordType.name`` spelling that also names a visible declaration
    is never used to suppress a change through the spelling tier
    (``OpaqueTypeIndex.shared_with_visible``, applied in ``intersect()`` for
    every comparison). The stable tier still decides on resolved identities,
    and a stable id that a visible declaration also carries is not proof of
    opacity. ``opaque.contains(c, ..., strict=opaque.complete)`` only narrows
    a *miss* on a stable identity; a change with no resolvable identity falls
    through to the spelling tier, which the rule above already narrows.
    """
    old_opaque = _find_opaque_types(old)
    new_opaque = _find_opaque_types(new)
    opaque = old_opaque.intersect(new_opaque)

    if not opaque:
        return changes

    result: list[Change] = []
    for c in changes:
        if c.kind in _STRUCTURAL_TYPE_CHANGE_KINDS:
            # Extract type name from symbol (may be "TypeName" or "ns::TypeName::field")
            if opaque.contains(c, root_type_name(c), strict=opaque.complete):
                # Suppress entirely: the type is opaque (forward-declared only)
                # so layout changes are invisible to consumers.
                continue
        result.append(c)
    return result
