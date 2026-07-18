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


def _build_qualified_index(types: Iterable[RecordType]) -> dict[str, set[str | None]]:
    """Bare ``RecordType.name`` -> ``{distinct qualified_name}`` over *types*.

    Built directly from the source type list (before any bare-name dict
    collapses duplicates), so an ambiguous bare name shared by two distinct
    types (e.g. both ``api::Foo`` and ``api::detail::Foo``) stays detectable
    — a plain ``{t.name: t for t in types}`` map (:func:`_build_type_map`)
    would silently keep only whichever record was inserted last.
    """
    idx: dict[str, set[str | None]] = collections.defaultdict(set)
    for t in types:
        idx[t.name].add(t.qualified_name)
    return idx


def _typename_is_internal(
    typename: str,
    qualified_index: dict[str, set[str | None]],
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

    Only applies the qualified-name fallback when *typename* unambiguously
    names one distinct qualified type: if two records share this bare name
    (e.g. a public ``api::Foo`` and an internal ``api::detail::Foo``), which
    one is "the" match can't be resolved from the bare name alone, so this
    returns ``False`` (no fallback) rather than guessing via whichever
    record happened to be indexed.
    """
    if is_internal_type(typename, internal_namespaces):
        return True
    qnames = qualified_index.get(typename)
    if not qnames or len(qnames) != 1:
        return False
    (qname,) = qnames
    if not qname:
        return False
    return is_internal_type(qname, internal_namespaces)


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
        if seed_name and not _typename_is_internal(seed_name, qualified_index, internal_set):
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
            if _typename_is_internal(typename, qualified_index, internal_set):
                paths[typename].append(list(path + [typename]))
            continue
        visited.add(key)

        _enqueue_typedef_targets(typename, typedefs or {}, path, queue)

        if _typename_is_internal(typename, qualified_index, internal_set):
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
    qualified_index = _build_qualified_index(snap.types)

    queue: collections.deque[tuple[str, list[str]]] = collections.deque()
    _seed_queue_from_functions(snap, queue)
    _seed_queue_from_variables(snap, queue)
    _seed_queue_from_public_types(
        type_map, qualified_index, internal_set, queue, is_dwarf_fallback=is_dwarf_fallback
    )

    paths = _bfs_collect_paths(queue, type_map, qualified_index, internal_set, snap.typedefs)
    return _dedup_paths(paths)


def compute_call_graph_leak_paths(
    snap: AbiSnapshot,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
) -> dict[str, list[str]]:
    """Call-graph analogue of :func:`compute_leak_paths` (ADR-044 P1 item 1).

    Walks the optional L5 source graph's ``DECL_CALLS_DECL``/
    ``DECL_REFERENCES_DECL`` edges from every public entry (exported-symbol
    decl or public-header-visible decl/type, per
    :func:`~abicheck.buildsource.source_graph.is_public_dependency_node`),
    returning a mapping ``lookup_key -> list of formatted proof-path
    strings`` (one per public entry that reaches it, edge-kind-annotated via
    :func:`~abicheck.buildsource.source_graph_findings._format_dependency_path`,
    e.g. ``"pub() --[DECL_CALLS_DECL]--> detail::helper()"``).

    Each reachable internal target is recorded under **two** keys when both
    are available (Codex review, fresh evidence): the graph node's own
    ``label`` (a demangled qualified name, e.g. ``ns::detail::helper`` — the
    format ``compute_leak_paths``'s type-layout walk already keys by, and
    what a hand-authored/synthetic ``Change`` uses), and, when the node has
    its own ``SOURCE_DECL_MAPS_TO_SYMBOL`` edge, the exported **mangled**
    symbol name that edge maps to (the same ``binary_symbol://`` identity
    :func:`~abicheck.buildsource.source_graph.localize_symbol` already
    resolves for the reverse direction). The latter matters because
    ``diff_symbols.py`` builds a real ``FUNC_REMOVED``/similar ``Change`` with
    ``symbol=`` the **mangled** linker name, not the demangled qualified name
    — a call-graph-only node's ``label`` can also be the mangled name or a
    hash-suffixed qualified name depending on provenance (see
    :mod:`abicheck.buildsource.call_graph`), so keying by ``label`` alone
    would silently never match a real, compiled C++ removal.

    This is a *symbol-availability* signal, distinct from
    :func:`compute_leak_paths`'s layout/type-graph walk: a public inline
    function's body calling into a removed/changed internal template
    specialization has no field/base/signature evidence at all (nothing a
    layout walk can see) but is real to a linker — the exact oneDAL
    dispatcher gap this ADR's P0 slice explicitly left open (see the ADR's
    "What this ADR does not fix" section).

    Requires an embedded L5 graph (``--sources``/``--build-info``/
    ``--header-graph``) with at least one relevant edge; returns ``{}``
    otherwise — never an error, mirroring
    :func:`~abicheck.buildsource.poi.resolve_changed_paths_public_impact`'s
    degrade contract, so a project with no build-source evidence sees no
    behavior change at all.
    """
    build_source = getattr(snap, "build_source", None)
    graph = build_source.source_graph if build_source is not None else None
    if graph is None or not getattr(graph, "nodes", None):
        return {}

    from .buildsource.source_graph import is_public_dependency_node
    from .buildsource.source_graph_findings import (
        _dependency_path,
        _dependency_reachability,
        _format_dependency_path,
    )

    edge_kinds = frozenset({"DECL_CALLS_DECL", "DECL_REFERENCES_DECL"})
    if not any(e.kind in edge_kinds for e in graph.edges):
        return {}

    node_by_id = {n.id: n for n in graph.nodes}
    decl_to_symbol: dict[str, str] = {}
    symbol_prefix = "binary_symbol://"
    for e in graph.edges:
        if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" and e.dst.startswith(symbol_prefix):
            decl_to_symbol[e.src] = e.dst[len(symbol_prefix):]
    exported_decls = set(decl_to_symbol)
    entries = [
        n.id
        for n in graph.nodes
        if is_public_dependency_node(n.id, node_by_id, exported_decls)
    ]
    if not entries:
        return {}

    internal_set = set(internal_namespaces)
    reachability = _dependency_reachability(graph, edge_kinds)
    result: dict[str, list[str]] = collections.defaultdict(list)
    for entry in entries:
        for target in reachability.get(entry, frozenset()):
            node = node_by_id.get(target)
            if node is None:
                continue
            name = node.label or target
            if not is_internal_type(name, internal_set):
                continue
            path_edges = _dependency_path(graph, edge_kinds, entry, target)
            if not path_edges:
                continue
            formatted = _format_dependency_path(graph, path_edges)
            result[name].append(formatted)
            mangled = decl_to_symbol.get(target)
            if mangled and mangled != name:
                result[mangled].append(formatted)
    return dict(result)


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
    qualified_index: dict[str, set[str | None]],
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
        if _typename_is_internal(type_name, qualified_index, internal_set):
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
        # ADR-044 D1/D2: this finding exists *because* tname is reachable from
        # the public surface — mark it so a broad suppression rule's default
        # reachability gate (which would otherwise see an untagged synthetic
        # Change and wrongly treat it as unreachable) cannot suppress it
        # either, mirroring the raw trigger changes MarkReachability tags.
        public_reachable=True,
        reachability_kind="value_embedding" if embedded_by_value else "pointer_or_signature",
        reachability_proof_path=path_strs[0] if path_strs else None,
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
    # Merge both sides' qualified-name indexes so a change's bare symbol
    # resolves to its RecordType.qualified_name regardless of which snapshot
    # it belongs to (an added/removed type only appears on one side). Built
    # from snap.types directly (not a bare-name-deduped type map), so a bare
    # name shared by two distinct types across old+new stays detectable as
    # ambiguous rather than silently resolved via one arbitrary record.
    merged_qualified_index: dict[str, set[str | None]] = collections.defaultdict(set)
    for t in (*old.types, *new.types):
        merged_qualified_index[t.name].add(t.qualified_name)
    internal_changes = _collect_internal_changes(changes, internal_set, merged_qualified_index)
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


def _build_call_graph_leak_change(
    dname: str,
    triggers: list[Change],
    proof_paths: list[str],
) -> Change:
    """Build a single ``INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API`` Change entry.

    The call-graph analogue of :func:`_build_leak_change`: composes an
    already artifact-proven ``BREAKING``/``API_BREAK`` finding on an internal
    decl with a ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` proof path from
    a public entry, instead of a layout/type-graph reachability path.
    """
    kinds_seen = sorted({c.kind.value for c in triggers})
    path_strs = proof_paths[:3]
    more = "" if len(proof_paths) <= 3 else f" (+{len(proof_paths) - 3} more paths)"
    return Change(
        kind=ChangeKind.INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API,
        symbol=dname,
        description=(
            f"Internal symbol '{dname}' changed ({', '.join(kinds_seen)}) and "
            "is called/referenced from the public ABI surface — an "
            "application built against the old public entry point can fail "
            f"to resolve this symbol at load time. Call/reference paths: "
            f"{'; '.join(path_strs)}{more}."
        ),
        caused_by_type=dname,
        # ADR-044 D1/D2: same reasoning as _build_leak_change — this finding
        # exists *because* dname is reachable from the public surface via the
        # call graph, so a broad suppression rule's reachability gate must
        # not treat it as unreachable either.
        public_reachable=True,
        reachability_kind="symbol_availability",
        reachability_proof_path=path_strs[0] if path_strs else None,
    )


def detect_call_graph_leaks(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
) -> list[Change]:
    """Return additional ``Change`` entries for call-graph-reachable internal
    symbol changes (ADR-044 P1 items 1-2) — the call-graph analogue of
    :func:`detect_internal_leaks`.

    Unlike the layout walk, which triggers on a fixed set of layout-affecting
    kinds (:data:`_LEAK_TRIGGERING_KINDS`) applied to a type *embedding* the
    internal root, this triggers directly on any already artifact-proven
    ``BREAKING_KINDS``/``API_BREAK_KINDS`` change whose own subject *is* the
    internal decl (e.g. ``func_removed`` on an internal template
    specialization) — the ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL``
    evidence explains *why* a public consumer is affected; per the authority
    rule (ADR-028 D3/ADR-041) it never manufactures the break itself, since
    the triggering change is already independently artifact-proven.

    Requires an embedded L5 graph on at least one snapshot (see
    :func:`compute_call_graph_leak_paths`); returns ``[]`` otherwise.
    """
    from .checker_policy import API_BREAK_KINDS, BREAKING_KINDS

    internal_set = tuple(internal_namespaces)
    triggering_kinds = BREAKING_KINDS | API_BREAK_KINDS
    # Deliberately NOT pre-filtered by is_internal_type(root, ...) here
    # (Codex review, fresh evidence): _root_type_name_for_change(c) is
    # c.symbol verbatim for a function-shaped kind like FUNC_REMOVED, and
    # diff_symbols.py sets that to the *mangled* linker name (no "::"
    # segments at all) — is_internal_type would reject every real C++
    # removal before it ever reached the call-path lookup below, exactly the
    # bug this review round caught. compute_call_graph_leak_paths already
    # gates its own keys on is_internal_type(node.label, ...) (the qualified
    # name, which does have "::" segments), so a dict hit below is already
    # sufficient proof of "internal and call-graph-reachable" — checking it
    # again here on the wrong string would only reintroduce the bug.
    by_symbol: dict[str, list[Change]] = collections.defaultdict(list)
    for c in changes:
        if c.kind not in triggering_kinds:
            continue
        by_symbol[_root_type_name_for_change(c)].append(c)
    if not by_symbol:
        return []

    old_call_paths = compute_call_graph_leak_paths(old, internal_set)
    new_call_paths = compute_call_graph_leak_paths(new, internal_set)
    if not old_call_paths and not new_call_paths:
        return []

    out: list[Change] = []
    for dname, triggers in by_symbol.items():
        old_pp = old_call_paths.get(dname, [])
        new_pp = new_call_paths.get(dname, [])
        proof_paths = old_pp + [p for p in new_pp if p not in old_pp]
        if not proof_paths:
            continue
        out.append(_build_call_graph_leak_change(dname, triggers, proof_paths))

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
