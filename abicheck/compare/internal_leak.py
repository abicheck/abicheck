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
from collections.abc import Iterable
from typing import TYPE_CHECKING

from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.entity_identity import candidate_lookup_keys
from ..model.evidence_status import ReachabilityState
from .internal_leak_graph import (
    _build_call_graph_leak_change,
    compute_call_graph_leak_paths,
)
from .internal_leak_names import (
    INTERNAL_NAMESPACE,
    INTERNAL_TYPE_LEAK,
    _root_type_name_for_change,
)
from .internal_leak_paths import (
    _format_path,
    _path_has_indirection,
    _path_is_value_propagating,
    compute_leak_paths,
    select_preferred_path,
)
from .internal_namespaces import DEFAULT_INTERNAL_NAMESPACES

if TYPE_CHECKING:
    from ..model import AbiSnapshot


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

# Change kinds that represent a meaningful change to a type's binary layout
# or identity. If a *change of one of these kinds* applies to an internal
# type that's reachable from public API, we raise a leak finding.
LEAK_TRIGGERING_KINDS: frozenset[ChangeKind] = frozenset(
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
    }
)


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
        if c.kind not in LEAK_TRIGGERING_KINDS:
            continue
        # ``symbol`` may be e.g. "ns::detail::Impl::field" — peel the field
        # qualifier so we look up the type itself.
        type_name = _root_type_name_for_change(c)
        if INTERNAL_NAMESPACE.matches(
            type_name, internal_namespaces=internal_set, qualified_index=qualified_index
        ):
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
IDENTITY_VTABLE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_VTABLE_CHANGED,
        ChangeKind.VPTR_INTRODUCED,
        ChangeKind.TYPE_BASE_CHANGED,
        ChangeKind.BASE_CLASS_OFFSET_CHANGED,
        ChangeKind.TYPE_REMOVED,
    }
)


def _build_leak_change(
    tname: str,
    triggers: list[Change],
    paths: list[list[str]],
    sample_snap: AbiSnapshot,
    embedded_by_value: bool,
) -> Change:
    """Build a single ``INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`` Change entry."""
    kinds_seen = sorted({c.kind.value for c in triggers})
    # CodeRabbit review: this finding's own displayed proof path must prefer
    # value-propagating evidence too, the same way MarkReachability's
    # separate layout walk already does via select_preferred_path (ADR-046
    # D6) -- otherwise a shorter indirect-only path could still be shown
    # here even when a stronger value-propagating one exists among `paths`.
    ordered_paths = paths
    if paths:
        preferred = select_preferred_path(paths)
        ordered_paths = [preferred] + [p for p in paths if p is not preferred]
    path_strs = [_format_path(p) for p in ordered_paths[:3]]
    more = "" if len(paths) <= 3 else f" (+{len(paths) - 3} more paths)"
    sev_hint = (
        "embedded-by-value or via inheritance — layout change propagates "
        "to public type size/offset"
        if embedded_by_value
        else "reachable via pointer / template — identity/vtable changes "
        "propagate to consumers"
    )
    change = Change(
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
        reachability_state=ReachabilityState.PROVEN_REACHABLE,
        reachability_kind="value_embedding"
        if embedded_by_value
        else "pointer_or_signature",
        reachability_proof_path=path_strs[0] if path_strs else None,
    )
    return change


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
    for t in (*old.declarations.types, *new.declarations.types):
        merged_qualified_index[t.name].add(t.qualified_name)
    internal_changes = _collect_internal_changes(
        changes, internal_set, merged_qualified_index
    )
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
        if not INTERNAL_TYPE_LEAK.confirmed(
            tname,
            paths,
            internal_namespaces=internal_set,
            qualified_index=merged_qualified_index,
        ):
            # Internal type changed but not reachable from public API in
            # either snapshot — this is the "truly private" case; skip.
            continue
        # Evaluate every path from both snapshots, each carrying the per-hop
        # ``indirect:`` markers recorded when it was discovered: the *same*
        # ``field:<name>`` chain can be a pointer in old but an embedded value
        # in new (a pimpl that switched to by-value), and ``_merge_leak_
        # paths`` dedups the identical chain — so checking only the preferred
        # sample would mis-read the indirection (Codex review). A side with no
        # paths contributes nothing.
        side_paths = [*old_pl, *new_pl]
        identity_or_vtable = any(c.kind in IDENTITY_VTABLE_KINDS for c in triggers)
        # P2 (UXL field run), narrowed by decision 2A of the design-hardening
        # plan: an internal type reached **only** behind a pointer
        # (per-hop ``indirect:`` markers recorded at enqueue) whose change is pure
        # layout is not consumer-visible — the public holder embeds only the
        # pointer, not the changed layout. Suppress when every path on *both*
        # snapshots is behind a pointer and the change is not identity/vtable
        # (vtable dispatch / RTTI / base-subobject still propagate through a
        # pointer). Any value/inheritance path — in either snapshot — keeps the
        # finding (a by-value member, or a just-embedded type, carries the layout).
        # The leniency needs a structural fact, not the namespace's name: the
        # layout must be proven invisible to consumers (see
        # compare.layout_visibility). Unknown visibility keeps the finding.
        value_prop = any(_path_is_value_propagating(p) for p in side_paths)
        all_indirect = bool(side_paths) and all(
            _path_has_indirection(p) for p in side_paths
        )
        if (
            all_indirect
            and not identity_or_vtable
            and INTERNAL_NAMESPACE.confirmed(
                tname,
                (tname, old, new),
                internal_namespaces=internal_set,
                qualified_index=merged_qualified_index,
            )
        ):
            continue
        out.append(
            _build_leak_change(
                tname, triggers, paths, old if old_pl else new, value_prop
            )
        )

    return out


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
    kinds (:data:`LEAK_TRIGGERING_KINDS`) applied to a type *embedding* the
    internal root, this triggers directly on any already artifact-proven
    ``BREAKING_KINDS`` change whose own subject *is* the internal decl (e.g.
    ``func_removed`` on an internal template specialization) — the
    ``DECL_CALLS_DECL``/``DECL_REFERENCES_DECL`` evidence explains *why* a
    public consumer is affected; per the authority rule (ADR-028 D3/ADR-041)
    it never manufactures the break itself, since the triggering change is
    already independently artifact-proven.

    Deliberately does **not** also trigger on ``API_BREAK_KINDS`` (Codex
    review, fresh evidence): that tier is the ``SOURCE_CONTRACT`` evidence
    class (``checker_policy.py``'s own docstring — "a source-level break that
    needs a recompile... not necessarily a shipped ABI break"), and most of
    its members (e.g. ``inline_function_removed``, whose own inline comment
    reads "no exported symbol") have no removed linker symbol at all —
    composing one into this overlay's "can fail to resolve this symbol at
    load time" description would be a false binary-load-time claim for a
    change that was never one. Mirrors :data:`LEAK_TRIGGERING_KINDS`'s own
    precedent of a hand-curated, binary-observable-only trigger set, rather
    than the full breaking/API-break union.

    Requires an embedded L5 graph on at least one snapshot (see
    :func:`compute_call_graph_leak_paths`); returns ``[]`` otherwise.
    """
    from ..model.change_catalog.assembled import BREAKING_KINDS

    internal_set = tuple(internal_namespaces)
    triggering_kinds = BREAKING_KINDS
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
        # Codex review (fresh evidence): compute_call_graph_leak_paths's
        # mangled-symbol key requires a SOURCE_DECL_MAPS_TO_SYMBOL edge,
        # which only the build-integrated L4/L5 path creates — the
        # header-only path (header_graph.py) never does. Each trigger's own
        # c.qualified_name (set by EnrichSourceLocations from Function.name,
        # independent of graph provenance) is a reliable fallback key that
        # works in both modes. Built via the shared candidate-key helper
        # (G31 Phase B B1: generalizes what used to be an ad hoc
        # per-call-site {dname, qualified_name, ...} set literal — see
        # entity_identity.candidate_lookup_keys).
        keys = candidate_lookup_keys(
            dname, *(t.qualified_name for t in triggers if t.qualified_name)
        )
        # Ordered de-duplication through dicts: a list-membership scan per
        # path is quadratic in the number of proof paths a trigger collects.
        old_pp: dict[str, None] = {}
        new_pp: dict[str, None] = {}
        for key in keys:
            old_pp.update(dict.fromkeys(old_call_paths.get(key, [])))
            new_pp.update(dict.fromkeys(new_call_paths.get(key, [])))
        proof_paths = list(old_pp) + [p for p in new_pp if p not in old_pp]
        if not proof_paths:
            continue
        out.append(_build_call_graph_leak_change(dname, triggers, proof_paths))

    return out


def detect_leak_findings(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
    internal_namespaces: Iterable[str] = DEFAULT_INTERNAL_NAMESPACES,
) -> list[Change]:
    """Both leak overlays: the layout/type-graph walk and the call-graph walk."""
    return detect_internal_leaks(
        changes, old, new, internal_namespaces
    ) + detect_call_graph_leaks(changes, old, new, internal_namespaces)
