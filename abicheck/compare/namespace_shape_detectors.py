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

from ..diff_helpers import make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.evidence_status import ReachabilityState
from ..model.source_contract_evidence import (
    public_reachability,
)
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


def _declared_names(snap: AbiSnapshot) -> set[str]:
    """Qualified names *snap* declares itself (functions without their
    parameter list, records, enums, variables): a library that replaces a
    ``using std::f;`` with its own ``lib::f`` still offers the name."""
    names: set[str] = set()
    for f in snap.declarations.functions:
        if "::" in f.name:
            names.add(f.name.split("(", 1)[0])
    for v in snap.declarations.variables:
        names.add(v.name)
    for t in snap.declarations.types:
        names.add(t.qualified_name or t.name)
    for e in snap.declarations.enums:
        names.add(getattr(e, "qualified_name", None) or e.name)
    # An alias (``using string = std::string;`` / ``typedef``) replacing a
    # ``using std::string;`` keeps ``lib::string`` available.
    names.update(snap.declarations.typedefs_qualified)
    names.update(k for k in snap.declarations.typedefs if "::" in k)
    return names


def _build_std_reexport_change(declared: str, underlying: str) -> Change:
    """Build a single ``STD_REEXPORT_REMOVED`` finding.

    The re-export table holds only using-declarations a public header
    declares (``extract/headers/std_reexports.py``), so the declaration is
    observed as contract and its reachability is proven (ADR-084).
    """
    return make_change(
        ChangeKind.STD_REEXPORT_REMOVED,
        symbol=declared,
        name=declared,
        detail=underlying,
        old_value=f"{declared} → {underlying}",
        new_value=None,
        **public_reachability(True),
    )


def detect_std_reexport_removed(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> list[Change]:
    """Report ``using std::X;`` re-exports that disappeared from public headers.

    Reads both sides' re-export table (``AbiSnapshot.std_reexports``). A
    side that did not model it (``None``: no header backend, no public
    set, an older dump) proves nothing either way, so nothing is reported.
    A re-export is removed when its declared name is in the old table, not
    in the new one, and the new headers do not declare that name themselves.
    """
    before, after = old.std_reexports, new.std_reexports
    if before is None or after is None:
        return []
    own = _declared_names(new)
    return [
        _build_std_reexport_change(declared, target)
        for declared, target in sorted(before.items())
        if declared not in after and declared not in own
    ]


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
