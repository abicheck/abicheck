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

"""Internal-namespace leak detection.

Detects the detail-namespace leak pattern where a type living in an
"internal" namespace (``detail``, ``impl``, ``internal``) has changed and is
*reachable from the public ABI surface* via:

  - inheritance: ``class Public : public detail::Base``
  - embedded-by-value field: ``class Public { detail::Impl impl_; };``
  - template argument: ``Public<detail::Helper>``
  - function signature: ``detail::Result foo()`` or ``void foo(detail::T&)``

In all of these cases, layout / vtable / mangled-name changes to the
internal type propagate into the effective public ABI even though the
type is documented as "internal".

The detector consumes the change list (which already contains
``type_size_changed`` / ``type_field_*`` / ``type_vtable_changed`` etc.
for the internal type) and adds a synthetic
``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` finding describing the leak path
so users see the connection between the internal change and the public
surface.
"""

from __future__ import annotations

import collections
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind
from .checker_types import Change

if TYPE_CHECKING:
    from .model import AbiSnapshot, RecordType


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Namespace segments that mark a type as "internal" by convention.
# Matched as a name segment (between ``::``) — substring matches inside an
# identifier like ``DetailView`` are intentionally not flagged.
DEFAULT_INTERNAL_NAMESPACES: tuple[str, ...] = (
    "detail",
    "impl",
    "internal",
    "__detail",
    "_impl",
)


# Change kinds that represent a meaningful change to a type's binary layout
# or identity. If a *change of one of these kinds* applies to an internal
# type that's reachable from public API, we raise a leak finding.
_LEAK_TRIGGERING_KINDS: frozenset[ChangeKind] = frozenset({
    ChangeKind.TYPE_SIZE_CHANGED,
    ChangeKind.TYPE_ALIGNMENT_CHANGED,
    ChangeKind.TYPE_FIELD_REMOVED,
    ChangeKind.TYPE_FIELD_ADDED,
    ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
    ChangeKind.TYPE_FIELD_TYPE_CHANGED,
    ChangeKind.TYPE_BASE_CHANGED,
    ChangeKind.TYPE_VTABLE_CHANGED,
    ChangeKind.TYPE_REMOVED,
    ChangeKind.STRUCT_SIZE_CHANGED,
    ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
    ChangeKind.STRUCT_FIELD_REMOVED,
    ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
    ChangeKind.STRUCT_ALIGNMENT_CHANGED,
    # Fine-grained class-layout descriptor kinds (layout-closure work): like the
    # coarse type/struct kinds above, they carry an owner type name and are a
    # layout change on a type, so they must participate in the internal-leak
    # pipeline too — otherwise a private ``detail::Impl`` with only a
    # TRIVIALLY_COPYABLE_LOST / BASE_CLASS_OFFSET_CHANGED finding is neither
    # attributed to a real public leak nor demoted as unreachable internal churn
    # (Codex review #345).
    ChangeKind.BASE_CLASS_OFFSET_CHANGED,
    ChangeKind.VPTR_INTRODUCED,
    ChangeKind.TRIVIALLY_COPYABLE_LOST,
    ChangeKind.STANDARD_LAYOUT_LOST,
    ChangeKind.TAIL_PADDING_REUSE_CHANGED,
    ChangeKind.LAYOUT_UNVERIFIABLE,
})


# Splits a qualified C++ name into namespace segments, ignoring template
# argument lists. ``acme::lib::detail::pimpl<X>`` →
# ``["acme", "lib", "detail", "pimpl"]``.
_TEMPLATE_ARG_RE = re.compile(r"<[^<>]*>")


def _strip_template_args(name: str) -> str:
    """Collapse balanced ``<...>`` template arg lists out of *name*.

    Handles one level of nesting iteratively. Used only for splitting the
    name into ``::``-separated segments, not for canonicalisation.
    """
    prev = None
    cur = name
    # Iteratively strip innermost <...> until stable (handles nesting).
    while cur != prev:
        prev = cur
        cur = _TEMPLATE_ARG_RE.sub("", cur)
    return cur


def _name_segments(name: str) -> list[str]:
    """Return ``::``-separated identifier segments of *name*.

    Template arguments are stripped first so that
    ``acme::lib::detail::pimpl<Foo<int>>`` yields
    ``["acme", "lib", "detail", "pimpl"]``.
    """
    if not name:
        return []
    stripped = _strip_template_args(name)
    return [seg.strip() for seg in stripped.split("::") if seg.strip()]


def is_internal_type(
    name: str,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
) -> bool:
    """Return True if *name* lives in one of the *internal_namespaces*.

    The check is segment-based: a segment matches exactly (case-sensitive)
    one of *internal_namespaces*. Template arguments are stripped first.

    Examples (with default namespaces)::

        is_internal_type("acme::lib::detail::impl") -> True
        is_internal_type("acme::lib::detail::pimpl<X>") -> True
        is_internal_type("std::__detail::node") -> True
        is_internal_type("MyClass") -> False
        is_internal_type("Details") -> False   # not a segment match
    """
    needles = set(internal_namespaces)
    if not needles:
        return False
    return any(seg in needles for seg in _name_segments(name))


# ---------------------------------------------------------------------------
# Reachability
# ---------------------------------------------------------------------------

# Strip type decorators — copy of the helper used in dwarf_snapshot. Kept
# local so that this module has no circular import with dwarf_snapshot.
_DECORATOR_RE = re.compile(r"(\*|&{1,2}|\[\d*\]|\bconst\b|\bvolatile\b)")


def _strip_decorators(typename: str) -> str:
    """Strip pointer/reference/const/volatile/array suffixes from *typename*.

    Returns the bare type name (or template) suitable for lookup in the
    types map.
    """
    s = _DECORATOR_RE.sub("", typename or "").strip()
    # Collapse multiple spaces.
    return re.sub(r"\s+", " ", s)


def _candidate_type_names(typename: str) -> list[str]:
    """Yield candidate type names to look up for *typename*.

    For ``std::unique_ptr<acme::lib::detail::impl>`` we want to surface
    both the outer template and the inner type, because the inner
    ``detail::impl`` is what users will see leaking.
    """
    out: list[str] = []
    base = _strip_decorators(typename)
    if base:
        out.append(base)
        # Also yield template arguments, splitting on commas at the top level.
        # Cheap parser: find outermost balanced <...> and split its contents.
        depth = 0
        start = -1
        for i, ch in enumerate(base):
            if ch == "<":
                if depth == 0:
                    start = i + 1
                depth += 1
            elif ch == ">":
                depth -= 1
                if depth == 0 and start >= 0:
                    inner = base[start:i]
                    # Split commas only at top level inside `inner`.
                    parts = _split_top_level_commas(inner)
                    for p in parts:
                        sub = _strip_decorators(p)
                        if sub:
                            out.append(sub)
                            # Recurse one level for nested templates.
                            out.extend(_candidate_type_names(sub))
                    start = -1
    return out


def _split_top_level_commas(s: str) -> list[str]:
    """Split *s* on commas that are not nested inside ``<...>``."""
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in s:
        if ch == "<":
            depth += 1
            buf.append(ch)
        elif ch == ">":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


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
    out: dict[str, RecordType] = {t.name: t for t in snap.types}
    if out:
        return out, False
    dwarf = getattr(snap, "dwarf", None)
    if dwarf is None or not getattr(dwarf, "structs", None):
        return out, False
    from .model import RecordType as _RecordType
    from .model import TypeField as _TypeField

    for name, layout in dwarf.structs.items():
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


def _resolve_type_name(
    typename: str, type_map: dict[str, RecordType],
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
    """
    if not typename or typename in type_map:
        return typename
    if "::" in typename:
        return typename
    candidates = [
        name for name in type_map
        if name.rsplit("::", 1)[-1] == typename
    ]
    if len(candidates) == 1:
        return candidates[0]
    return typename


def _seed_queue_from_functions(
    snap: AbiSnapshot,
    queue: collections.deque[tuple[str, list[str]]],
) -> None:
    """Enqueue type candidates derived from all public function signatures."""
    from .diff_symbols import _public_functions

    for func in _public_functions(snap).values():
        seed_types = [func.return_type] + [p.type for p in func.params]
        for t in seed_types:
            if not t:
                continue
            for cand in _candidate_type_names(t):
                queue.append((cand, [f"fn:{func.name}"]))


def _seed_queue_from_variables(
    snap: AbiSnapshot,
    queue: collections.deque[tuple[str, list[str]]],
) -> None:
    """Enqueue type candidates derived from all public variable types."""
    from .diff_symbols import _public_variables

    for var in _public_variables(snap).values():
        if var.type:
            for cand in _candidate_type_names(var.type):
                queue.append((cand, [f"var:{var.name}"]))


def _seed_queue_from_public_types(
    type_map: dict[str, RecordType],
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
        if seed_name and not is_internal_type(seed_name, internal_set):
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
    for base in rec.bases:
        for cand in _candidate_type_names(base):
            queue.append((cand, new_path + [f"base:{base}"]))
    for vb in rec.virtual_bases:
        for cand in _candidate_type_names(vb):
            queue.append((cand, new_path + [f"vbase:{vb}"]))
    for fld in rec.fields:
        for cand in _candidate_type_names(fld.type):
            queue.append((cand, new_path + [f"field:{fld.name}"]))


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
    for cand in _candidate_type_names(target):
        if cand and cand != typename:
            queue.append((cand, path + [f"typedef:{typename}"]))


def _bfs_collect_paths(
    queue: collections.deque[tuple[str, list[str]]],
    type_map: dict[str, RecordType],
    internal_set: set[str],
    typedefs: dict[str, str] | None = None,
) -> dict[str, list[list[str]]]:
    """Drive the BFS walk; return raw (un-deduped) internal-type paths."""
    paths: dict[str, list[list[str]]] = collections.defaultdict(list)
    visited: set[tuple[str, str]] = set()

    while queue:
        typename, path = queue.popleft()
        if not typename:
            continue
        # DWARF can record base-class names un-qualified; resolve against
        # the type map before we record / enqueue children.
        typename = _resolve_type_name(typename, type_map)
        # Cycle protection: visit each (entry_point, typename) pair at
        # most once. We deliberately scope by entry point so that two
        # public roots reaching the same intermediate type each get
        # their children walked — otherwise the second root's path is
        # never extended past the shared intermediate, which would lose
        # by-value severity information for nested internal types.
        key: tuple[str, str] = (path[0] if path else "", typename)
        if key in visited:
            # Still record the leak if this typename is internal — paths
            # vary by entry point, but the *first* recorded one is enough
            # for user-facing reporting.
            if is_internal_type(typename, internal_set):
                paths[typename].append(list(path + [typename]))
            continue
        visited.add(key)

        _enqueue_typedef_targets(typename, typedefs or {}, path, queue)

        if is_internal_type(typename, internal_set):
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

    queue: collections.deque[tuple[str, list[str]]] = collections.deque()
    _seed_queue_from_functions(snap, queue)
    _seed_queue_from_variables(snap, queue)
    _seed_queue_from_public_types(type_map, internal_set, queue, is_dwarf_fallback=is_dwarf_fallback)

    paths = _bfs_collect_paths(queue, type_map, internal_set, snap.typedefs)
    return _dedup_paths(paths)


# ---------------------------------------------------------------------------
# Leak detection
# ---------------------------------------------------------------------------


def _format_path(path: list[str]) -> str:
    """Render a leak path as a single arrow-delimited string."""
    return " → ".join(path)


def _field_is_indirect(fld_type: str) -> bool:
    """Return True if *fld_type* is a pointer, reference, or smart-pointer wrapper.

    Indirect fields don't embed by value, so layout changes don't
    directly propagate through them.
    """
    if "*" in fld_type or "&" in fld_type:
        return True
    stripped = _strip_decorators(fld_type)
    return (
        "unique_ptr" in stripped
        or "shared_ptr" in stripped
        or "weak_ptr" in stripped
        or "pimpl" in stripped.lower()
    )


def _record_field_is_value_embedded(rec: RecordType, field_name: str) -> bool | None:
    """Check whether *field_name* in *rec* is embedded by value.

    Returns True if embedded-by-value, False if indirect, None if the field
    is not found in *rec*.
    """
    for fld in rec.fields:
        if fld.name == field_name:
            return not _field_is_indirect(fld.type)
    return None


def _path_describes_value_embedding(
    path: list[str], snap: AbiSnapshot,
) -> bool:
    """Return True if any ``field:`` step on *path* is an embedded-by-value
    field of the internal type (not a pointer / reference / smart pointer).

    Used to decide the severity hint in the leak's description.
    """
    type_map, _ = _build_type_map(snap)
    # Walk in pairs: when we see "field:<name>", the *previous* element is
    # the type containing the field; the field type is the *next* element
    # (or rather the next typename in the chain — fields don't show their
    # type literally, but the chain alternates "type → field:X → next-type").
    for i, step in enumerate(path):
        if not step.startswith("field:") or i == 0:
            continue
        containing_type = path[i - 1]
        field_name = step[len("field:"):]
        rec = type_map.get(containing_type)
        if rec is None:
            continue
        result = _record_field_is_value_embedded(rec, field_name)
        if result is not None:
            return result
    return False


def _collect_internal_changes(
    changes: list[Change],
    internal_set: tuple[str, ...],
) -> dict[str, list[Change]]:
    """Phase 1: bucket changes by internal type name.

    Only considers changes of a layout-affecting kind whose symbol resolves
    to an internal type.  Returns an empty dict when nothing qualifies.
    """
    internal_changes: dict[str, list[Change]] = collections.defaultdict(list)
    for c in changes:
        if c.kind not in _LEAK_TRIGGERING_KINDS:
            continue
        # ``symbol`` may be e.g. "ns::detail::Impl::field" — peel the field
        # qualifier so we look up the type itself.
        type_name = _root_type_name_for_change(c)
        if is_internal_type(type_name, internal_set):
            internal_changes[type_name].append(c)
    return internal_changes


def _merge_leak_paths(
    tname: str,
    old_paths: dict[str, list[list[str]]],
    new_paths: dict[str, list[list[str]]],
) -> list[list[str]]:
    """Merge reachability paths from both snapshots, deduplicating."""
    old_list = old_paths.get(tname, [])
    new_unique = [p for p in new_paths.get(tname, []) if p not in old_list]
    return old_list + new_unique


def _build_leak_change(
    tname: str,
    triggers: list[Change],
    paths: list[list[str]],
    sample_snap: AbiSnapshot,
) -> Change:
    """Build a single ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` Change entry."""
    embedded_by_value = any(
        _path_describes_value_embedding(p, sample_snap) for p in paths
    )
    kinds_seen = sorted({c.kind.value for c in triggers})
    path_strs = [_format_path(p) for p in paths[:3]]
    more = "" if len(paths) <= 3 else f" (+{len(paths) - 3} more paths)"
    sev_hint = (
        "embedded-by-value or via inheritance — layout change propagates "
        "to public type size/offset"
        if embedded_by_value
        else "reachable via pointer / template — identity/vtable changes "
             "propagate to consumers"
    )
    return Change(
        kind=ChangeKind.INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API,
        symbol=tname,
        description=(
            f"Internal type '{tname}' changed "
            f"({', '.join(kinds_seen)}) and is reachable from the public "
            f"ABI surface — {sev_hint}. Public-surface paths: "
            f"{'; '.join(path_strs)}{more}."
        ),
        caused_by_type=tname,
    )


def detect_internal_leaks(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
) -> list[Change]:
    """Return additional ``Change`` entries for internal-type leaks.

    For each change in *changes* of a layout-affecting kind whose ``symbol``
    refers to an internal type that's reachable from the *old* or *new*
    public ABI surface, emit one ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API``
    finding that describes the leak path.

    Multiple changes on the same internal type collapse into a single
    leak finding (we don't want N redundant findings for the same root
    cause). If the same internal type is reached via multiple public
    entry points, the description lists up to three of them.
    """
    internal_set = tuple(internal_namespaces)
    internal_changes = _collect_internal_changes(changes, internal_set)
    if not internal_changes:
        return []

    # Compute reachability on *both* snapshots (a type may be reachable
    # only in one direction, e.g. just-added internal type leaked by a
    # new public template).
    old_paths = compute_leak_paths(old, internal_set)
    new_paths = compute_leak_paths(new, internal_set)

    out: list[Change] = []
    for tname, triggers in internal_changes.items():
        paths = _merge_leak_paths(tname, old_paths, new_paths)
        if not paths:
            # Internal type changed but not reachable from public API in
            # either snapshot — this is the "truly private" case; skip.
            continue
        # Pick the snapshot whose path list to use for the value-embedding
        # heuristic. Prefer old (where the public API was already shipped).
        sample_snap = old if old_paths.get(tname) else new
        out.append(_build_leak_change(tname, triggers, paths, sample_snap))

    return out


# Change kinds whose ``symbol`` carries a ``Type::field`` form (i.e. the
# field name appended after the containing type). For these, the leading
# segment is the containing type and the trailing segment must be
# stripped.
#
# NOTE: ``TYPE_FIELD_*`` (emitted by ``diff_types``) and
# ``STRUCT_FIELD_*`` (emitted by ``diff_platform``) follow *different*
# symbol conventions:
#
#     diff_types:    symbol = "ns::Type"          (field name in description only)
#     diff_platform: symbol = "ns::Type::field"   (field name appended)
#
# Stripping the last segment for ``TYPE_FIELD_*`` would silently truncate
# legitimate namespaced type names like ``ns::detail::Impl`` into
# ``ns::detail``, breaking the reachability lookup. So only the
# ``STRUCT_FIELD_*`` kinds participate in stripping.
_FIELD_LEVEL_LEAK_KINDS: frozenset[ChangeKind] = frozenset({
    ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
    ChangeKind.STRUCT_FIELD_REMOVED,
    ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
})


def _root_type_name_for_change(c: Change) -> str:
    """Peel any "::field" suffix off *c*'s symbol to get the containing type.

    Only strips the final segment for change kinds where the emitter is
    known to put the field name into the symbol (``STRUCT_FIELD_*`` from
    ``diff_platform``). Other kinds — including the ``TYPE_FIELD_*``
    family from ``diff_types`` — carry the containing type name directly
    in ``symbol`` and must be returned as-is to preserve namespaced
    internal type names like ``ns::detail::Impl``.
    """
    sym = c.symbol or ""
    if "::" in sym and c.kind in _FIELD_LEVEL_LEAK_KINDS:
        return sym.rsplit("::", 1)[0]
    return sym
