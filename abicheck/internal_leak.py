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


def _is_known_pointer_wrapper(outer: str) -> bool:
    """Return True only for pointer-owning wrapper spellings we know.

    This deliberately avoids substring matches such as
    ``acme::unique_ptr_value<T>``: a user-defined type can embed ``T`` by
    value while having a smart-pointer-like name.  In ambiguous cases, keep the
    path value-propagating so the ABI break is visible.
    """
    clean = _strip_template_args(_strip_decorators(outer)).strip()
    if not clean:
        return False
    leaf = clean.rsplit("::", 1)[-1]
    leaf_l = leaf.lower()
    if leaf_l in {"unique_ptr", "shared_ptr", "weak_ptr", "__uniq_ptr_impl"}:
        return True
    # oneDAL exposes detail::pimpl<T> as its public pimpl alias.  Keep this
    # project-specific alias narrow; a bare ``acme::pimpl<T>`` may be an
    # arbitrary by-value template and must not suppress layout leaks.
    return clean == "oneapi::dal::detail::pimpl"

def _candidate_type_names_indirect(typename: str) -> list[tuple[str, bool]]:
    """Yield ``(candidate_type_name, reached_through_pointer)`` pairs for *typename*.

    The per-hop path model: indirection is computed **per template argument**, so
    the pointer in ``std::_Tuple_impl<0, proxy*, deleter>`` is attributed to
    ``proxy`` (the pointee) and not to ``deleter`` — and a by-value argument in
    ``std::pair<ns::detail::Impl, int*>`` (``Impl``) is correctly *not* indirect
    even though the spelling contains a ``*``. A smart-pointer wrapper
    (``unique_ptr``/``shared_ptr``/``weak_ptr``) makes its argument the pointee.

    For ``std::unique_ptr<acme::lib::detail::impl>`` we surface both the outer
    template and the inner ``detail::impl`` (what users see leaking); the inner
    one carries ``reached_through_pointer=True``.
    """
    out: list[tuple[str, bool]] = []
    base = _strip_decorators(typename)
    if not base:
        return out
    # Base candidate: indirect iff the whole spelling is a top-level pointer/ref
    # (collapse template args so a pointer buried in an argument doesn't count).
    top = _strip_template_args(typename)
    top_ptr = "*" in top or "&" in top
    out.append((base, top_ptr))
    # Smart-pointer wrapper is decided on the OUTER type only (template args
    # already collapsed in `top`), so a nested ``pimpl<Other>`` / ``unique_ptr``
    # in an unrelated argument of a by-value template does not mark its siblings
    # indirect (Codex review).
    outer = _strip_decorators(top)
    smart = _is_known_pointer_wrapper(outer)
    # Walk the outermost <...> of the ORIGINAL spelling (keeps inner */&).
    depth = 0
    start = -1
    for i, ch in enumerate(typename):
        if ch == "<":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0 and start >= 0:
                for p in _split_top_level_commas(typename[start:i]):
                    p_top = _strip_template_args(p)
                    # A top-level pointer/ref on the enclosing template
                    # (``pair<Impl, int>*``) puts every argument behind it.
                    arg_ptr = top_ptr or smart or "*" in p_top or "&" in p_top
                    sub = _strip_decorators(p)
                    if sub:
                        out.append((sub, arg_ptr))
                        for c2, ind2 in _candidate_type_names_indirect(sub):
                            out.append((c2, ind2 or arg_ptr))
                start = -1
    return out


def _candidate_type_names(typename: str) -> list[str]:
    """Names only (drops the per-hop pointer flag); back-compat for callers that
    just need reachability, not indirection."""
    return [name for name, _ in _candidate_type_names_indirect(typename)]


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


def _typename_is_internal(
    typename: str,
    type_map: dict[str, RecordType],
    internal_namespaces: Iterable[str],
) -> bool:
    """Like :func:`is_internal_type`, but also consults the matching
    ``RecordType.qualified_name`` when the bare *typename* itself carries no
    namespace segment.

    ``RecordType.name`` (and every bare spelling derived from it — bases,
    field types, param/return types) is deliberately unqualified so it keeps
    matching the DWARF backend's equally-bare struct names (see
    ``RecordType.qualified_name``'s docstring in model.py); castxml is the
    only source that can currently recover the real namespace path, and only
    via that separate field. Without this fallback, a type genuinely declared
    in an internal namespace (``mylib::detail::descriptor_base``) is invisible
    to :func:`is_internal_type` once reduced to its bare spelling
    (``descriptor_base``), silently disabling the leak check for it.
    """
    if is_internal_type(typename, internal_namespaces):
        return True
    rec = type_map.get(typename)
    if rec is not None and rec.qualified_name:
        return is_internal_type(rec.qualified_name, internal_namespaces)
    return False


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
    from .model import RecordType as _RecordType, TypeField as _TypeField

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
        # (type-spelling, reached-through-pointer?) for the return + each param.
        # A type reached only through a pointer/reference in a public signature
        # (the opaque-handle pattern ``void use(ns::detail::Impl*)``) does not
        # embed its layout — record the indirection so a layout-only change is
        # demoted, mirroring the pointer-field case (Codex review). The seed path
        # otherwise drops the ``*`` (``_candidate_type_names`` strips decorators).
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
    from .diff_symbols import _public_variables

    for var in _public_variables(snap).values():
        if var.type:
            for cand, arg_ptr in _candidate_type_names_indirect(var.type):
                step = [f"var:{var.name}"]
                if arg_ptr:
                    step.append("indirect:signature")
                queue.append((cand, step))


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
        if seed_name and not _typename_is_internal(seed_name, type_map, internal_set):
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
    for base in rec.bases:
        for cand, via_ptr in _candidate_type_names_indirect(base):
            step = [f"base:{base}"]
            if via_ptr:
                step.append("indirect:edge")
            queue.append((cand, new_path + step))
    for vb in rec.virtual_bases:
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
            if _typename_is_internal(typename, type_map, internal_set):
                paths[typename].append(list(path + [typename]))
            continue
        visited.add(key)

        _enqueue_typedef_targets(typename, typedefs or {}, path, queue)

        if _typename_is_internal(typename, type_map, internal_set):
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
    """Render a leak path as a single arrow-delimited string.

    Synthetic ``indirect:`` markers (seed-time pointer evidence) are internal and
    not shown.
    """
    return " → ".join(s for s in path if not s.startswith("indirect:"))


def _field_is_indirect(fld_type: str) -> bool:
    """Return True if *fld_type* is a pointer, reference, or smart-pointer wrapper.

    Indirect fields don't embed by value, so layout changes don't
    directly propagate through them.
    """
    # Only a TOP-LEVEL pointer / reference / smart-pointer wrapper counts —
    # collapse template args first so a pointer buried in an unrelated argument
    # (e.g. ``std::pair<ns::detail::Impl, int*>``, whose ``Impl`` is a by-value
    # member) is NOT mistaken for indirection (Codex review). Per the maintainer
    # decision, suppression only fires on the unambiguous pimpl shape; any nested
    # / mixed spelling keeps the finding.
    no_targs = _strip_template_args(fld_type)  # collapse <...> (drops nested *)
    if "*" in no_targs or "&" in no_targs:  # top-level pointer / reference only
        return True
    outer = _strip_decorators(no_targs)
    if (
        "unique_ptr" in outer
        or "uniq_ptr" in outer  # libstdc++ internals: std::__uniq_ptr_impl
        or "shared_ptr" in outer
        or "weak_ptr" in outer
    ):
        return True
    # ``pimpl`` only as an alias-*template* usage (``pimpl<T>`` = the oneDAL
    # smart-pointer alias, case80) — NOT a by-value struct literally named
    # ``Pimpl``, which embeds its layout and must stay a leak.
    return "pimpl<" in _strip_decorators(fld_type).lower()


def _typedef_target_is_indirect(
    name: str, typedefs: dict[str, str], _seen: frozenset[str] = frozenset()
) -> bool:
    """Return True if alias *name* resolves (transitively) to a pointer / smart
    pointer — e.g. ``using Handle = ns::detail::Impl*;`` (Codex review). Without
    this, a pointer-typedef field reads as by-value and surfaces a spurious leak.
    """
    if name in _seen:
        return False
    target = typedefs.get(name)
    if not target:
        return False
    if _field_is_indirect(target):
        return True
    return _typedef_target_is_indirect(
        _strip_decorators(target), typedefs, _seen | {name}
    )


def _typenode_is_indirection_wrapper(name: str) -> bool:
    """Return True if a *type node* on a leak path is itself a pointer/reference
    or a smart-pointer wrapper type.

    Stricter than :func:`_field_is_indirect`: it must NOT fire on a regular
    record/function name that merely *contains* a wrapper-ish substring. A
    public type named ``PimplHandle`` that embeds an internal type **by value**
    is a real layout leak, not an indirection (Codex review) — so the loose
    ``pimpl``/``unique_ptr`` substring match used for *field declared types* is
    not applied to path labels. Only a raw ``*``/``&`` or a qualified
    ``std::``/libstdc++ smart-pointer spelling counts.
    """
    # Top-level only (collapse template args): a wrapper node like
    # ``std::__uniq_ptr_impl<...>`` is indirection, but ``std::array<int*, 4>``
    # (a pointer in an unrelated arg) is not (Codex review).
    no_targs = _strip_template_args(name)
    if "*" in no_targs or "&" in no_targs:
        return True
    outer = _strip_decorators(no_targs)
    return any(
        marker in outer
        for marker in (
            "std::unique_ptr", "std::shared_ptr", "std::weak_ptr",
            "__uniq_ptr", "__shared_ptr", "__weak_ptr",
        )
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


def _path_has_indirection(path: list[str], snap: AbiSnapshot | None = None) -> bool:
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

    *snap* is unused now (the marker is precomputed); kept for call-site compat.
    """
    return any(s.startswith("indirect:") for s in path)


def _path_is_value_propagating(path: list[str], snap: AbiSnapshot | None = None) -> bool:
    """Return True if a layout change on the leaf propagates *by value* to the
    public root along *path* — a value-embedding / inheritance chain with no
    pointer edge. Drives the leak's severity-hint wording.
    """
    if _path_has_indirection(path):
        return False
    return any(s.startswith(("field:", "base:", "vbase:")) for s in path)


def _collect_internal_changes(
    changes: list[Change],
    internal_set: tuple[str, ...],
    type_map: dict[str, RecordType],
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
        if _typename_is_internal(type_name, type_map, internal_set):
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


# Change kinds that alter a type's *identity* or *vtable* rather than only its
# in-memory layout. These still break consumers when the internal type is reached
# only through a pointer/reference — vtable dispatch, RTTI, and base-subobject
# offsets propagate through indirection — so they keep firing regardless of the
# value-embedding analysis. Every other triggering kind is a pure layout change
# (size/offset/padding/field add-remove), which does NOT reach the public holder
# through a pointer and is suppressed for pointer-only leaks (UXL field run P2).
_IDENTITY_VTABLE_KINDS: frozenset[ChangeKind] = frozenset({
    ChangeKind.TYPE_VTABLE_CHANGED,
    ChangeKind.VPTR_INTRODUCED,
    ChangeKind.TYPE_BASE_CHANGED,
    ChangeKind.BASE_CLASS_OFFSET_CHANGED,
    ChangeKind.TYPE_REMOVED,
})


def _build_leak_change(
    tname: str,
    triggers: list[Change],
    paths: list[list[str]],
    sample_snap: AbiSnapshot,
    embedded_by_value: bool,
) -> Change:
    """Build a single ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` Change entry."""
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
    # Merge both sides' type maps so a change's bare symbol resolves to its
    # RecordType.qualified_name regardless of which snapshot it belongs to
    # (an added/removed type only appears on one side).
    old_type_map, _ = _build_type_map(old)
    new_type_map, _ = _build_type_map(new)
    merged_type_map = {**old_type_map, **new_type_map}
    internal_changes = _collect_internal_changes(changes, internal_set, merged_type_map)
    if not internal_changes:
        return []

    # Compute reachability on *both* snapshots (a type may be reachable
    # only in one direction, e.g. just-added internal type leaked by a
    # new public template).
    old_paths = compute_leak_paths(old, internal_set)
    new_paths = compute_leak_paths(new, internal_set)

    out: list[Change] = []
    for tname, triggers in internal_changes.items():
        old_pl = old_paths.get(tname, [])
        new_pl = new_paths.get(tname, [])
        paths = _merge_leak_paths(tname, old_paths, new_paths)
        if not paths:
            # Internal type changed but not reachable from public API in
            # either snapshot — this is the "truly private" case; skip.
            continue
        # Evaluate every path against the snapshot it was discovered in: the
        # *same* ``field:<name>`` chain can be a pointer in old but an embedded
        # value in new (a pimpl that switched to by-value), and ``_merge_leak_
        # paths`` dedups the identical chain — so checking only the preferred
        # sample snapshot would mis-read the indirection (Codex review). A side
        # with no paths contributes nothing.
        side_paths = [(p, old) for p in old_pl] + [(p, new) for p in new_pl]
        identity_or_vtable = any(
            c.kind in _IDENTITY_VTABLE_KINDS for c in triggers
        )
        # P2 (UXL field run): an internal type reached **only** behind a pointer
        # (per-hop ``indirect:`` markers recorded at enqueue) whose change is pure
        # layout is not consumer-visible — the public holder embeds only the
        # pointer, not the changed layout. Suppress when every path on *both*
        # snapshots is behind a pointer and the change is not identity/vtable
        # (vtable dispatch / RTTI / base-subobject still propagate through a
        # pointer). Any value/inheritance path — in either snapshot — keeps the
        # finding (a by-value member, or a just-embedded type, carries the layout).
        value_prop = any(_path_is_value_propagating(p, s) for p, s in side_paths)
        all_indirect = bool(side_paths) and all(
            _path_has_indirection(p) for p, _ in side_paths
        )
        if all_indirect and not identity_or_vtable:
            continue
        out.append(
            _build_leak_change(
                tname, triggers, paths, old if old_pl else new, value_prop
            )
        )

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
