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

"""Layout and type-graph reachability from the public ABI surface.

``compute_leak_paths`` walks the public functions, variables and types of one
snapshot and records the paths that reach an internal-namespace type. The
path predicates here (``_format_path``, ``select_preferred_path``) are shared
by the leak detector and its finding builder.
"""

from __future__ import annotations

import collections
from collections.abc import Iterable
from typing import TYPE_CHECKING

from .internal_leak_names import (
    INTERNAL_NAMESPACE,
    _build_qualified_index,
    _candidate_type_names_indirect,
)
from .internal_namespaces import DEFAULT_INTERNAL_NAMESPACES

if TYPE_CHECKING:
    from ..model import AbiSnapshot, RecordType


def _build_type_map(snap: AbiSnapshot) -> tuple[dict[str, RecordType], bool]:
    """Build a type-name → RecordType map for *snap*.

    Returns a ``(type_map, is_dwarf_fallback)`` tuple.

    Primary source is ``snap.types`` (populated by header parsing or
    the DWARF snapshot builder). When that's empty but ``snap.dwarf``
    has structs (typical for the dumper's symbol-only fallback path),
    we synthesise minimal ``RecordType`` entries from
    ``DwarfMetadata.structs`` so the reachability walk can still see
    field-based embedding paths. Inheritance is not recovered from
    ``DwarfMetadata`` (it lacks base-class info), but
    ``DwarfMetadata.structs`` still gives us field types — enough to
    flag the *embedded-by-value* leak pattern.

    ``is_dwarf_fallback`` is ``True`` when the returned map was built
    from ``snap.dwarf.structs`` rather than ``snap.types``.  Callers
    use this flag to skip public-type BFS seeding: the DWARF-only
    record set is not filtered to the public ABI surface, so seeding
    from it would produce spurious ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API``
    findings with no real public entry point.
    """
    out: dict[str, RecordType] = {t.name: t for t in snap.declarations.types}
    if out:
        return out, False
    from .debug_layout_view import debug_layout_view

    debug_records = debug_layout_view(snap).records
    if not debug_records:
        return out, False
    from ..model import RecordType as _RecordType, TypeField as _TypeField

    for name, layout in debug_records.items():
        fields = [
            _TypeField(
                name=fi.name,
                type=fi.type_name,
                offset_bits=fi.byte_offset * 8,
            )
            for fi in layout.fields
        ]
        out[name] = _RecordType(
            name=name,
            kind="union" if layout.is_union else "class",
            size_bits=layout.byte_size * 8 if layout.byte_size else None,
            fields=fields,
            is_union=layout.is_union,
        )
    return out, True


def _build_suffix_index(
    type_map: dict[str, RecordType],
) -> dict[str, list[str]]:
    """Index *type_map* keys by their final ``::``-segment.

    Precomputing this once turns :func:`_resolve_type_name`'s unqualified-name
    lookup from an O(N) scan of the whole type map into an O(1) dict hit. On a
    large C++ surface (thousands of types) the BFS in
    :func:`_bfs_collect_paths` calls the resolver for every visited node, so the
    scan was quadratic; the index removes that. Mirrors the ``by_short`` index
    already used in :mod:`abicheck.idioms`.
    """
    index: dict[str, list[str]] = {}
    for name in type_map:
        index.setdefault(name.rsplit("::", 1)[-1], []).append(name)
    return index


def _resolve_type_name(
    typename: str,
    type_map: dict[str, RecordType],
    suffix_index: dict[str, list[str]] | None = None,
) -> str:
    """Best-effort canonicalisation of *typename* against *type_map*.

    DWARF snapshot extraction can record base-class names un-qualified
    (e.g. ``"descriptor_base"`` instead of
    ``"mylib::detail::descriptor_base"``). When the literal name isn't
    found, this helper searches the type map for an entry whose final
    ``::``-segment matches *typename*, returning the fully qualified
    name if exactly one such match exists. Ambiguous matches keep the
    literal name (so the caller falls through to its "missing type"
    branch rather than guessing).

    *suffix_index* is an optional precomputed final-segment index (see
    :func:`_build_suffix_index`); when omitted the map is scanned directly so
    the helper stays correct for standalone/test callers.
    """
    if not typename or typename in type_map:
        return typename
    if "::" in typename:
        return typename
    if suffix_index is not None:
        candidates: list[str] = suffix_index.get(typename, [])
    else:
        candidates = [name for name in type_map if name.rsplit("::", 1)[-1] == typename]
    if len(candidates) == 1:
        return candidates[0]
    return typename


def _seed_queue_from_functions(
    snap: AbiSnapshot,
    queue: collections.deque[tuple[str, list[str]]],
) -> None:
    """Enqueue type candidates derived from all public function signatures."""
    from .public_surface import public_functions

    for func in public_functions(snap).values():
        # (type-spelling, reached-through-pointer?) for the return + each param.
        # A type reached only through a pointer/reference in a public signature
        # (the opaque-handle pattern ``void use(ns::detail::Impl*)``) does not
        # embed its layout — record the indirection so a layout-only change is
        # demoted, mirroring the pointer-field case (Codex review). The seed path
        # otherwise drops the ``*`` (``_candidate_type_names_indirect`` strips decorators).
        seeds = [(func.return_type, (func.return_pointer_depth or 0) > 0)]
        seeds += [(p.type, (p.pointer_depth or 0) > 0) for p in func.params]
        for t, top_ptr in seeds:
            if not t:
                continue
            # Mark each candidate per template argument: a pointer buried in the
            # signature type (``std::pair<int, ns::detail::Impl*> get()``) reaches
            # that argument only through the pointer (Codex review).
            for cand, arg_ptr in _candidate_type_names_indirect(t):
                step = [f"fn:{func.name}"]
                if top_ptr or arg_ptr:
                    step.append("indirect:signature")
                queue.append((cand, step))


def _seed_queue_from_variables(
    snap: AbiSnapshot,
    queue: collections.deque[tuple[str, list[str]]],
) -> None:
    """Enqueue type candidates derived from all public variable types."""
    from .public_surface import public_variables

    for var in public_variables(snap).values():
        if var.type:
            for cand, arg_ptr in _candidate_type_names_indirect(var.type):
                step = [f"var:{var.name}"]
                if arg_ptr:
                    step.append("indirect:signature")
                queue.append((cand, step))


def _seed_queue_from_public_types(
    type_map: dict[str, RecordType],
    qualified_index: dict[str, set[str | None]],
    internal_set: set[str],
    queue: collections.deque[tuple[str, list[str]]],
    *,
    is_dwarf_fallback: bool = False,
) -> None:
    """Enqueue all public (non-internal-namespace) types from *type_map*.

    This catches classes declared in public headers but never referenced by
    an exported function symbol (e.g. inline-only templates).  The walk
    uses the header-derived type map (``snap.types``) so it only seeds
    from types on the genuine public ABI surface.

    When *is_dwarf_fallback* is ``True`` the map was synthesised from
    ``snap.dwarf.structs``, which is NOT filtered to the public ABI
    surface.  In that case seeding is skipped entirely to avoid spurious
    ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` findings that have no real
    public entry point.  Function- and variable-based seeding
    (``_seed_queue_from_functions`` / ``_seed_queue_from_variables``)
    still runs on the DWARF-only path and provides the real public
    surface anchors.
    """
    if is_dwarf_fallback:
        return
    for seed_name in type_map:
        if seed_name and not INTERNAL_NAMESPACE.matches(
            seed_name, internal_namespaces=internal_set, qualified_index=qualified_index
        ):
            queue.append((seed_name, [f"type:{seed_name}"]))


def _enqueue_record_children(
    rec: RecordType,
    new_path: list[str],
    queue: collections.deque[tuple[str, list[str]]],
) -> None:
    """Enqueue bases (and virtual bases) and field types of *rec*.

    Inheritance always carries ABI through.  Fields are included
    regardless of whether they are pointers/references — identity/vtable
    changes propagate via those too; the reporter can downgrade if needed.
    """
    # Inheritance embeds the base subobject by value, but a pointer *template
    # argument* of the base (e.g. inheriting ``_Head_base<0, Proxy*, false>``,
    # libstdc++'s decomposed unique_ptr) reaches that argument through a pointer —
    # mark it per-hop, like fields.
    for base in rec.resolved_bases():
        for cand, via_ptr in _candidate_type_names_indirect(base):
            step = [f"base:{base}"]
            if via_ptr:
                step.append("indirect:edge")
            queue.append((cand, new_path + step))
    for vb in rec.resolved_virtual_bases():
        for cand, via_ptr in _candidate_type_names_indirect(vb):
            step = [f"vbase:{vb}"]
            if via_ptr:
                step.append("indirect:edge")
            queue.append((cand, new_path + step))
    # Fields: mark the edge indirect per template argument (accurate per-hop).
    for fld in rec.fields:
        for cand, via_ptr in _candidate_type_names_indirect(fld.type):
            step = [f"field:{fld.name}"]
            if via_ptr:
                step.append("indirect:edge")
            queue.append((cand, new_path + step))


def _enqueue_typedef_targets(
    typename: str,
    typedefs: dict[str, str],
    path: list[str],
    queue: collections.deque[tuple[str, list[str]]],
) -> None:
    """Enqueue the underlying type candidates for a typedef alias."""
    target = typedefs.get(typename)
    if not target:
        return
    for cand, via_ptr in _candidate_type_names_indirect(target):
        if cand and cand != typename:
            step = [f"typedef:{typename}"]
            if via_ptr:
                step.append("indirect:edge")
            queue.append((cand, path + step))


def _bfs_collect_paths(
    queue: collections.deque[tuple[str, list[str]]],
    type_map: dict[str, RecordType],
    qualified_index: dict[str, set[str | None]],
    internal_set: set[str],
    typedefs: dict[str, str] | None = None,
) -> dict[str, list[list[str]]]:
    """Drive the BFS walk; return raw (un-deduped) internal-type paths."""
    paths: dict[str, list[list[str]]] = collections.defaultdict(list)
    visited: set[tuple[str, str, bool]] = set()
    # Precompute the final-segment index once; the resolver is called for every
    # dequeued node, so a per-call scan of *type_map* would be quadratic on a
    # large surface (see _build_suffix_index).
    suffix_index = _build_suffix_index(type_map)

    while queue:
        typename, path = queue.popleft()
        if not typename:
            continue
        # DWARF can record base-class names un-qualified; resolve against
        # the type map before we record / enqueue children.
        typename = _resolve_type_name(typename, type_map, suffix_index)
        # Cycle protection: visit each (entry_point, typename, behind_pointer)
        # triple at most once. The entry-point scope lets two public roots each
        # walk a shared intermediate; the behind-pointer bit additionally lets the
        # SAME intermediate be walked once via a pointer and once by value, so a
        # by-value alternative path to a nested child is never dropped by dedup
        # (Codex review / per-hop path model).
        behind_ptr = any(s.startswith("indirect:") for s in path)
        key: tuple[str, str, bool] = (path[0] if path else "", typename, behind_ptr)
        if key in visited:
            # Still record the leak if this typename is internal — paths
            # vary by entry point, but the *first* recorded one is enough
            # for user-facing reporting.
            if INTERNAL_NAMESPACE.matches(
                typename,
                internal_namespaces=internal_set,
                qualified_index=qualified_index,
            ):
                paths[typename].append(list(path + [typename]))
            continue
        visited.add(key)

        _enqueue_typedef_targets(typename, typedefs or {}, path, queue)

        if INTERNAL_NAMESPACE.matches(
            typename, internal_namespaces=internal_set, qualified_index=qualified_index
        ):
            paths[typename].append(list(path + [typename]))

        rec = type_map.get(typename)
        if rec is None:
            continue
        _enqueue_record_children(rec, path + [typename], queue)

    return paths


def _dedup_paths(
    paths: dict[str, list[list[str]]],
) -> dict[str, list[list[str]]]:
    """Drop duplicate paths per internal type, keeping the shortest."""
    deduped: dict[str, list[list[str]]] = {}
    for tname, plist in paths.items():
        unique: list[list[str]] = []
        seen: set[tuple[str, ...]] = set()
        for p in sorted(plist, key=len):
            key_t = tuple(p)
            if key_t not in seen:
                seen.add(key_t)
                unique.append(p)
        deduped[tname] = unique
    return deduped


def compute_leak_paths(
    snap: AbiSnapshot,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
) -> dict[str, list[list[str]]]:
    """Walk the public ABI surface; record paths reaching internal types.

    Returns a mapping ``internal_type_name -> list of paths``, where each
    path is an ordered list of type names starting from a *public*
    type/function and ending at the internal type.

    The walk visits:

      - Every public function's return type and parameter types
      - Every public variable's type
      - Typedef/using targets reached from those public signatures
      - For each visited type, its bases (and virtual bases) and the types
        of its non-pointer, non-reference fields

    Pointer / reference field types are visited but only contribute the
    template-argument expansion (e.g. ``unique_ptr<detail::Impl>`` reveals
    ``detail::Impl``); embedded-by-value is what actually breaks ABI on
    layout change, while pointer-to-internal still breaks on type-identity
    or vtable changes.
    """
    internal_set = set(internal_namespaces)
    type_map, is_dwarf_fallback = _build_type_map(snap)
    # Built from snap.types directly (not the deduped type_map) so an
    # ambiguous bare name is still detectable; naturally empty/inert in the
    # DWARF-fallback case (snap.types is empty there and DWARF carries no
    # qualified_name anyway).
    qualified_index = _build_qualified_index(snap.declarations.types)

    queue: collections.deque[tuple[str, list[str]]] = collections.deque()
    _seed_queue_from_functions(snap, queue)
    _seed_queue_from_variables(snap, queue)
    _seed_queue_from_public_types(
        type_map,
        qualified_index,
        internal_set,
        queue,
        is_dwarf_fallback=is_dwarf_fallback,
    )

    paths = _bfs_collect_paths(
        queue, type_map, qualified_index, internal_set, snap.declarations.typedefs
    )
    return _dedup_paths(paths)


def _format_path(path: list[str]) -> str:
    """Render a leak path as a single arrow-delimited string.

    Synthetic ``indirect:`` markers (seed-time pointer evidence) are internal and
    not shown.
    """
    return " → ".join(s for s in path if not s.startswith("indirect:"))


def _path_has_indirection(path: list[str]) -> bool:
    """Return True if *path* crosses a pointer / reference / smart-pointer hop.

    Per-hop path model: indirection is recorded **at enqueue time** as an
    ``indirect:`` marker on the edge that crosses a pointer (computed per
    template argument by :func:`_candidate_type_names_indirect`, plus the seed
    ``indirect:signature`` for pointer params/returns). A leaf reached through any
    such edge sits behind a pointer, so a *layout* change to it does not propagate
    to the public holder — including the oneTBB ``thread_request_serializer`` case
    where libstdc++ decomposes the ``unique_ptr`` into ``_Tuple_impl``/
    ``_Head_base`` (the ``proxy*`` argument marks the edge). A pointer buried in
    an unrelated template argument (``pair<Impl, int*>``) does **not** mark the
    ``Impl`` edge, so by-value members still propagate.
    """
    return any(s.startswith("indirect:") for s in path)


def _path_is_value_propagating(path: list[str]) -> bool:
    """Return True if a layout change on the leaf propagates *by value* to the
    public root along *path* — a value-embedding / inheritance chain with no
    pointer edge. Drives the leak's severity-hint wording.
    """
    if _path_has_indirection(path):
        return False
    return any(s.startswith(("field:", "base:", "vbase:")) for s in path)


def select_preferred_path(paths: list[list[str]]) -> list[str]:
    """Pick the strongest layout-walk proof path among *paths* (ADR-046 D6,
    partial — the two tiers this walk's own per-path signals already support:
    "exact" (2) and "virtual/indirect over-approximation" (6) of the ADR's
    six-tier order).

    Plain ``min(paths, key=len)`` (what this replaces) can pick an
    indirect-only path over an available value-propagating one just because
    it's shorter — a real path exists that proves the change propagates by
    value, but the proof shown is the weaker "reached through a pointer"
    one, silently hiding the stronger evidence
    ``reachability_kind="value_embedding"`` already claims exists elsewhere
    on the same finding. Preferring a value-propagating path first (falling
    back to length only within a tier) means the shown path always matches
    the strongest evidence actually available, not just the shortest.

    The other four tiers (consumer-proven, public-header structural,
    multi-producer-confirmed, reduced-confidence name resolution) need
    structured per-hop evidence (confidence, producer, ``ScopeOrigin``) this
    walk's plain ``list[str]`` path representation doesn't carry — not
    implemented here.
    """

    def _tier(p: list[str]) -> int:
        if _path_is_value_propagating(p):
            return 0
        if _path_has_indirection(p):
            return 2
        return 1

    return min(paths, key=lambda p: (_tier(p), len(p)))
