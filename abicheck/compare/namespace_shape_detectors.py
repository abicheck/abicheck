# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Namespace-shape detectors split out of ``diff_namespaces``.

``STD_REEXPORT_REMOVED`` and the inline-namespace version-bump detector,
plus the qualified-name helper both they and the experimental-namespace
detector in ``diff_namespaces`` share. Moved here verbatim so
``diff_namespaces`` stays within its size budget; ``diff_namespaces``
re-exports every public name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..checker_policy import ChangeKind, ReachabilityState
from ..checker_types import Change
from ..diff_helpers import make_change
from ..model.surface_facts import in_source_declaration_index
from .qualified_name_normalization import (
    segments as _segments,
    version_strip_segments as _version_strip_segments,
)

if TYPE_CHECKING:
    from ..model import AbiSnapshot


def _qualified_function_name(
    name: str, mangled: str, demangled: dict[str, str] | None = None
) -> str:
    """Return the best-effort qualified declaration name for a function.

    Header-derived snapshots populate ``Function.name`` with the
    qualified declaration name (``acme::lib::sort``). ELF-only mode
    leaves ``Function.name`` set to the mangled string; in that case we
    fall back to demangling of the mangled name.

    When iterating all functions of a snapshot, pass a *demangled* map
    (from :func:`_batch_demangle_public`) so the whole snapshot is demangled in
    a single batched ``c++filt`` call instead of one subprocess per symbol —
    the per-symbol path is what makes namespace detection explode on large
    stripped libraries. The lazy single-symbol fallback is kept for callers
    that have no batch (and is itself memoised in ``demangle_batch``).

    A demangled string is a *full declaration* — return type, qualified
    name, parameter list, and trailing qualifiers (``ns::C::f(ns::T
    const&, long) const``) — not merely a qualified name. This is
    deliberately returned as-is, signature included: it is what
    distinguishes one overload from another for callers that index
    functions by qualified name (:func:`_func_index_items`) — a
    caller that needs only the *leaf* member name must strip the signature
    itself (via :func:`diff_templates._strip_param_signature`) before
    segmenting, rather than have it stripped here, or two overloads
    (``f(int)``, ``f(double)``) collapse onto one identity and an overload
    that is removed while a sibling survives goes unreported (Codex
    review).
    """
    if "::" in name or "<" in name:
        return name
    if mangled.startswith("_Z"):
        if demangled is not None:
            return demangled.get(mangled, name)
        from ..demangle import demangle_batch

        return demangle_batch([mangled]).get(mangled, name)
    return name


# ---------------------------------------------------------------------------
# Detector: experimental → stable graduation / removal
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Detector: std re-export removed
# ---------------------------------------------------------------------------

# Heuristic: a function whose declared qualified name resolves to a
# library namespace AND whose mangled name resolves to a name in
# ``std::`` is a re-export (the library names it via ``using std::X``).
#
# Concrete forms we accept:
#   - Function.name == "lib::ns::par"         (declared in library headers)
#   - Function.mangled demangles to a name beginning with "std::"
#     (the underlying definition belongs to the standard library).
#
# We DO NOT use libstdc++/libc++ internal-namespace heuristics here —
# false positives on real library functions would be worse than missing
# the occasional re-export. The detector therefore requires both halves
# of the signal to fire.

_STD_PREFIX = "std::"


def _looks_like_std_reexport(
    declared_qualified: str,
    underlying_qualified: str,
) -> bool:
    """Return True when declared_qualified is a non-std alias for underlying_qualified.

    Both names must be fully qualified. The underlying name must live in
    ``std::``; the declared name must live somewhere else (any library
    namespace). Identical names — i.e. the function genuinely lives in
    ``std::`` — are not re-exports.
    """
    if not declared_qualified or not underlying_qualified:
        return False
    declared_segs = _segments(declared_qualified)
    underlying_segs = _segments(underlying_qualified)
    if not declared_segs or not underlying_segs:
        return False
    # Declared must NOT be in std::; underlying MUST be in std::.
    if declared_segs[0] == "std":
        return False
    if underlying_segs[0] != "std":
        return False
    # Same leaf name on both sides — a using-declaration preserves the leaf.
    return declared_segs[-1] == underlying_segs[-1]


def _collect_public_declared_names(snap: AbiSnapshot) -> set[str]:
    """Return the set of qualified declared names of public functions in
    *snap* -- the source-declaration population, see
    :func:`_func_index_items`."""
    demangled = _batch_demangle_public(snap)
    out: set[str] = set()
    for f in snap.declarations.functions:
        if not in_source_declaration_index(f):
            continue
        qname = _qualified_function_name(f.name, f.mangled, demangled)
        if qname:
            out.add(qname)
    return out


def _batch_demangle_public(snap: AbiSnapshot) -> dict[str, str]:
    """Demangle every public mangled name in *snap* in one batch call --
    same population as :func:`_func_index_items`: a declaration whose export
    vanished is still declared."""
    from ..demangle import demangle_batch
    from .detection_memo import memoized

    def compute() -> dict[str, str]:
        mangled = [
            f.mangled
            for f in snap.declarations.functions
            if f.mangled.startswith("_Z") and in_source_declaration_index(f)
        ]
        return demangle_batch(mangled) if mangled else {}

    # Three namespace-shape detectors ask this of both snapshots in one
    # pass (``diff_namespaces.detect_namespace_patterns`` opens the scope);
    # outside a scope this calls straight through.
    return memoized("batch_demangle_public", snap, None, compute)


def _build_std_reexport_change(declared: str, underlying: str) -> Change:
    """Build a single ``STD_REEXPORT_REMOVED`` finding.

    ADR-044 D1 (Codex review): only ever emitted for a declaration that was a
    *public* function (``detect_std_reexport_removed`` filters on
    the source-declaration population before calling this) — same construction-time
    tagging rationale as ``_emit_experimental_change``.
    """
    return make_change(
        ChangeKind.STD_REEXPORT_REMOVED,
        symbol=declared,
        name=declared,
        detail=underlying,
        old_value=f"{declared} → {underlying}",
        new_value=None,
        public_reachable=True,
        reachability_state=ReachabilityState.PROVEN_REACHABLE,
        reachability_kind="direct_public_symbol",
    )


def detect_std_reexport_removed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report ``using std::X;`` re-exports that disappeared from public headers.

    A re-export is detected when the OLD snapshot has a public function
    whose declared qualified name lives in a library namespace but whose
    mangled name demangles to ``std::``. If the same declared qualified
    name is absent from the NEW snapshot's function set, we emit one
    ``STD_REEXPORT_REMOVED`` per missing declaration.

    The detector is intentionally narrow — it never fires when the
    declared name and the underlying name are identical, when the
    declared name is in ``std::``, or when the mangled name does not
    demangle to ``std::``.
    """
    demangled = _batch_demangle_public(old)
    new_declared = _collect_public_declared_names(new)

    changes: list[Change] = []
    seen: set[str] = set()
    for f in old.declarations.functions:
        if not in_source_declaration_index(f):
            continue
        declared = _qualified_function_name(f.name, f.mangled, demangled)
        if not declared or declared in seen or declared in new_declared:
            continue
        underlying = demangled.get(f.mangled, "")
        if not _looks_like_std_reexport(declared, underlying):
            continue
        seen.add(declared)
        changes.append(_build_std_reexport_change(declared, underlying))

    return changes


# ---------------------------------------------------------------------------
# Detector: versioned inline namespace bumped (header-declared)
# ---------------------------------------------------------------------------


def detect_inline_namespace_version_bump(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Detect declarations whose versioned inline-namespace segment shifted.

    Complementary to the existing symbol-level ``INLINE_NAMESPACE_MOVED``
    detector (``diff_platform._diff_inline_namespace``): that one needs
    ≥2 mangled-symbol moves and works only on built shared libraries;
    this one fires from declared qualified names so it works for header-
    only / template-library snapshots and on a single declaration.

    The detector matches old and new declarations by the *version-
    stripped* qualified name. If both sides have versioned segments AND
    the integer suffix changed, emit one finding per moved declaration.
    """
    old_idx = _index_versioned(_collect_versioned_entries(old))
    new_idx = _index_versioned(_collect_versioned_entries(new))
    return _emit_version_bumps(old_idx, new_idx)


def _index_versioned(
    items: list[tuple[str, bool, str]],
) -> dict[tuple[str, ...], list[tuple[str, int, bool, str]]]:
    """Map version-stripped segments → ``(qualified, version_int, is_public, entity)``.

    The trailing entity is carried because this index pools functions and
    record types (see :func:`_collect_versioned_entries`), so the kind is a
    property of the finding, not of the ChangeKind.
    """
    out: dict[tuple[str, ...], list[tuple[str, int, bool, str]]] = {}
    for qname, is_public, entity in items:
        segs = _segments(qname)
        stripped, ver = _version_strip_segments(segs)
        if ver is None:
            continue
        out.setdefault(stripped, []).append((qname, ver, is_public, entity))
    return out


def _collect_versioned_entries(snap: AbiSnapshot) -> list[tuple[str, bool, str]]:
    """Return ``[(qualified_name, is_reliably_public), …]`` for *snap*.

    A function entry is reliably public because it was filtered to the
    source-declaration population above (see :func:`_func_index_items`). A type entry is reliably public only when
    ``RecordType.origin == ScopeOrigin.PUBLIC_HEADER`` (ADR-024's opt-in
    public-header scoping via ``-H``/``--header``) —
    the one signal that *does* exist for a type in the absence of a
    visibility field (Codex review). Without that opt-in flag every type's
    ``origin`` is ``ScopeOrigin.UNKNOWN``, so this degrades to the prior
    untagged behavior automatically, not a regression for the common case.
    """
    from ..model import ScopeOrigin

    demangled = _batch_demangle_public(snap)
    items: list[tuple[str, bool, str]] = []
    for f in snap.declarations.functions:
        if not in_source_declaration_index(f):
            continue
        qname = _qualified_function_name(f.name, f.mangled, demangled)
        if qname:
            items.append((qname, True, "function"))
    for t in snap.declarations.types:
        if t.name:
            items.append((t.name, t.origin == ScopeOrigin.PUBLIC_HEADER, "type"))
    return items


def _emit_version_bumps(
    old_idx: dict[tuple[str, ...], list[tuple[str, int, bool, str]]],
    new_idx: dict[tuple[str, ...], list[tuple[str, int, bool, str]]],
) -> list[Change]:
    changes: list[Change] = []
    for stripped, old_list in old_idx.items():
        new_list = new_idx.get(stripped, [])
        if not new_list:
            continue
        old_versions = {v for _, v, _, _ in old_list}
        new_versions = {v for _, v, _, _ in new_list}
        if old_versions == new_versions:
            continue
        if max(new_versions) <= max(old_versions):
            continue
        old_q = old_list[0][0]
        new_q = new_list[0][0]
        # `_collect_versioned_entries` marks a function entry reliably
        # public (source-declaration filter) and a type entry reliably
        # public only when its origin is ScopeOrigin.PUBLIC_HEADER (Codex
        # review) — untagged otherwise, same as before public-header
        # scoping is used. `or`, not `and` (Codex review, fresh evidence):
        # old-side public evidence alone already proves an old-consumer
        # break (an application linked against the old public symbol),
        # regardless of whether the new symbol also has public-header
        # evidence — public-header scoping can be asymmetric between two
        # snapshots (a type moved out of the scoped header set, or the flag
        # only covers one side), so requiring both sides publicly-tagged
        # let a genuine old-consumer break stay untagged and suppressible.
        subject_is_public = old_list[0][2] or new_list[0][2]
        # Polymorphic: the pooled index holds functions and record types
        # alike; unstated when the two sides disagree.
        entities = {old_list[0][3], new_list[0][3]}
        changes.append(
            make_change(
                ChangeKind.INLINE_NAMESPACE_VERSION_BUMPED,
                symbol=new_q,
                entity_discriminator=(
                    next(iter(entities)) if len(entities) == 1 else None
                ),
                old=old_q,
                new=new_q,
                detail=f"{sorted(old_versions)} to {sorted(new_versions)}",
                public_reachable=subject_is_public,
                reachability_state=(
                    ReachabilityState.PROVEN_REACHABLE
                    if subject_is_public
                    else ReachabilityState.UNKNOWN
                ),
                reachability_kind="direct_public_symbol" if subject_is_public else None,
            )
        )
    return changes
