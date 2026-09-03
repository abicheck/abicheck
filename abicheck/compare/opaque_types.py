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

"""``OpaqueTypeIndex`` — one snapshot's opaque-type set, in both identity
tiers (ADR-063 Phase 2's post-parse consumer migration).

Owned by ``compare/`` per ADR-061's routing table ("match old/new entities
or identify a raw change"): this is a matching index, not a filtering step.
``diff_filtering.py`` keeps the *policy* half — which types count as opaque,
and which findings that suppresses — and reads this type for the join.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..diff_helpers import depth_aware_bare_name
from ..diff_symbols import _PUBLIC_VIS
from ..model.identity_tiers import (
    SnapshotLocalIdentity,
    StableEntityId,
    snapshot_local_identity,
    stable_entity_id,
)

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model import AbiSnapshot, RecordType

__all__ = [
    "OpaqueTypeIndex",
    "find_by_value_types",
    "find_opaque_types",
    "is_impl_source",
]


@dataclass(frozen=True)
class OpaqueTypeIndex:
    """The opaque-type set of one snapshot, in both identity tiers.

    Replaces the bare ``set[str]`` of ``RecordType.name`` this consumer used
    to carry -- the exact site ADR-063 Phase 2 names as a known collision
    ("opaque-type suppression keyed by bare ``RecordType.name``",
    ``diff_filtering._find_opaque_types``). Both tiers are carried, never
    mixed:

    * *stable* -- one
      :class:`~abicheck.model.identity_tiers.StableEntityId` per opaque
      declaration whose producer resolved a cross-snapshot-stable
      ``EntityId``. Empty for a DWARF/PE/Mach-O-only snapshot, where no
      backend resolves one at all.
    * *local* -- one
      :class:`~abicheck.model.identity_tiers.SnapshotLocalIdentity` per
      opaque declaration, keyed on the same ``RecordType.name`` spelling the
      pre-migration ``set[str]`` held, so the string tier's matching
      behavior is bit-for-bit what it was.

    Every opaque declaration contributes to *local*; one additionally
    contributes to *stable* when it has a stable identity. Keeping both is
    what makes :meth:`intersect` and :meth:`contains` a strict *superset* of
    the pre-migration behavior rather than a narrowing one.
    """

    stable: frozenset[StableEntityId]
    local: frozenset[SnapshotLocalIdentity]

    def intersect(self, other: OpaqueTypeIndex) -> OpaqueTypeIndex:
        """Per-tier intersection -- a declaration must be opaque on *both*
        sides to suppress. The tiers intersect independently: a type opaque
        on both sides but carrying a stable identity on only one still meets
        in the *local* tier, exactly as the pre-migration string set did."""
        return OpaqueTypeIndex(
            stable=self.stable & other.stable, local=self.local & other.local
        )

    def __bool__(self) -> bool:
        return bool(self.stable or self.local)

    def contains(self, change: Change, spelling: str) -> bool:
        """Whether *change* names an opaque declaration.

        Stable tier first: when the change carries a cross-snapshot-stable
        ``EntityId`` this index holds, the declaration is *proven* to be the
        opaque one, regardless of how either side rendered its display
        spelling (a qualified ``Change.symbol`` against a bare
        ``RecordType.name`` misses under a string compare). Falling back to
        the spelling tier on a stable *miss* -- rather than treating the
        stable tier as authoritative and stopping -- is deliberate; see
        ``diff_filtering._downgrade_opaque_type_changes`` for what that
        would cost and what it would buy.
        """
        stable = stable_entity_id(change.entity_id)
        if stable is not None and stable in self.stable:
            return True
        return snapshot_local_identity(spelling) in self.local


_IMPL_EXTENSIONS = frozenset({".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm"})


def is_impl_source(source_location: str | None) -> bool:
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


def find_opaque_types(snap: AbiSnapshot) -> OpaqueTypeIndex:
    """Find types that are opaque to consumers.

    A type is opaque when:

    1. castxml marks it as ``incomplete`` (``is_opaque=True``) — the public
       header has only a forward declaration, OR
    2. The type definition is in an implementation file (.c/.cpp) AND all
       public-API references use pointers (never by value).  This handles
       DWARF mode where castxml is not used but DWARF's ``DW_AT_decl_file``
       reveals the type is implementation-private.

    Returns a two-tier :class:`~abicheck.compare.opaque_types.
    OpaqueTypeIndex`, not a ``set[str]``. Rule 2's by-value check
    (:func:`find_by_value_types`) still runs over ``RecordType.name``
    spellings against rendered signature text -- a spelling question, not
    an identity one, and ``EntityId`` has nothing to contribute to it --
    but now also tries the name's unqualified leaf spelling alongside the
    full one (see :func:`_type_is_by_value_referenced`), since a
    qualification mismatch there used to be silently absorbed by an equally
    spelling-based join and no longer is once :class:`OpaqueTypeIndex`'s
    stable tier can join across exactly that mismatch. Only the *result* is
    re-expressed as identities.
    """
    opaque: set[str] = set()
    declarations: dict[str, list[RecordType]] = {}

    for t in snap.types:
        if t.is_opaque or is_impl_source(t.source_location):
            # In the `is_impl_source` case the type is defined in an
            # implementation file — only consider it opaque if all API
            # references are through pointers (rule 2, resolved below).
            opaque.add(t.name)
            declarations.setdefault(t.name, []).append(t)

    if not opaque:
        return OpaqueTypeIndex(stable=frozenset(), local=frozenset())

    by_value_types = find_by_value_types(snap, opaque)
    surviving = opaque - by_value_types

    stable: set[StableEntityId] = set()
    local: set[SnapshotLocalIdentity] = set()
    for name in surviving:
        for t in declarations[name]:
            local.add(snapshot_local_identity(name, t.entity_id))
            resolved = stable_entity_id(t.entity_id)
            if resolved is not None:
                stable.add(resolved)
    return OpaqueTypeIndex(stable=frozenset(stable), local=frozenset(local))


def _references_type_token(spelling: str, text: str) -> bool:
    """Whether *text* references *spelling* as a whole type-name token,
    never as part of a longer identifier.

    A plain ``spelling in text`` substring test (this scan's original
    behavior) matches ``Handle`` inside an unrelated ``OtherHandle`` just as
    readily as inside a real ``ns::Handle`` reference -- harmless for a
    full, already-qualified name (a C/C++ identifier can never be a
    substring of another one without an intervening non-identifier
    character on both sides, so this predicate changes nothing for that
    candidate), but a real false-positive risk for the *leaf* spelling
    :func:`_type_is_by_value_referenced` widens the scan with (Codex review
    on PR #1041): matching ``Handle`` inside ``OtherHandle`` would wrongly mark
    the genuinely opaque ``ns::Handle`` as by-value exposed, dropping it
    from both identity tiers and reporting a private layout change as
    breaking. Boundaries are ``\\w``/non-``\\w`` only, via ``re``'s own
    zero-width lookaround.

    This alone does **not** stop a leaf spelling from matching inside a
    *different* qualified reference (``other::Handle`` still contains a
    token-bounded ``Handle``, since ``::`` is non-word on both sides) -- see
    :func:`_references_unqualified_type_token`, which the leaf candidate
    uses instead, for that closing half."""
    pattern = r"(?<!\w)" + re.escape(spelling) + r"(?!\w)"
    return re.search(pattern, text) is not None


def _references_unqualified_type_token(spelling: str, text: str) -> bool:
    """As :func:`_references_type_token`, plus refusing a match immediately
    preceded by ``::`` -- i.e. *spelling* must appear bare, never as the
    trailing segment of a different qualified name.

    This is what the *leaf* candidate in :func:`_type_is_by_value_referenced`
    scans with, instead of the plain token check: ``ns::Handle``'s leaf
    fallback ``Handle`` matching a real ``other::Handle`` reference would
    wrongly treat an unrelated scope's declaration as exposing ``ns::Handle``,
    dropping the genuinely opaque type out of both identity tiers and
    reporting its private layout change as breaking (Codex review on
    PR #1041, following up on the embedded-substring case
    :func:`_references_type_token` alone closes). The full, already-qualified
    candidate is unaffected -- it is never scanned through this function --
    so a genuine ``ns::Handle`` reference still matches regardless of what
    precedes it."""
    pattern = r"(?<!\w)(?<!:)" + re.escape(spelling) + r"(?!\w)"
    return re.search(pattern, text) is not None


def _type_is_by_value_referenced(tname: str, text: str) -> bool:
    """Whether *text* references *tname* as a by-value type, trying both
    the full name and (when applicable) its unqualified leaf spelling.

    *tname* is ``RecordType.name`` -- which may be qualified
    (``"ns::Handle"``) even when the signature text this function scans
    renders the identical type unqualified (``"Handle"``, when the
    reference sits inside the same namespace, or when the producer's own
    signature renderer simply drops a redundant qualifier). A plain
    ``tname in rendered_text`` substring test misses that case entirely --
    a real gap this function has always had, made consequential rather than
    cosmetic once :class:`~abicheck.compare.opaque_types.OpaqueTypeIndex`'s
    stable tier can reliably join the two sides' declarations across
    exactly that same qualification mismatch (Codex review on PR #1041):
    a by-value exposure this scan fails to see leaves the type wrongly
    ``opaque``, and the stable tier then suppresses the resulting finding
    with no spelling mismatch left to (accidentally) save it.

    The full name is checked via :func:`_references_type_token`; an
    unqualified leaf spelling (the segment after the last *depth-zero*
    ``"::"`` -- see :func:`~abicheck.diff_helpers.depth_aware_bare_name`,
    since a naive ``rsplit("::", 1)`` would cut inside a qualified template
    argument instead of at the real scope boundary, e.g. extracting
    ``"Tag>"`` out of ``"api::Wrapper<dep::Tag>"`` rather than
    ``"Wrapper<dep::Tag>"`` -- Codex review on PR #1041, follow-up round;
    tried only when the leaf differs from the full name -- an already-bare
    name gets no second candidate) is checked via the *stricter*
    :func:`_references_unqualified_type_token`, which additionally refuses a
    match immediately preceded by ``::`` -- otherwise the leaf widening
    would treat a real, separately-scoped ``other::Handle`` reference as
    exposing ``ns::Handle`` too.

    **Documented, still-open gap** (Codex review on PR #1041, follow-up
    round): rejecting a preceding ``::`` closes the *qualified*-collision
    case, but a *bare* same-leaf reference in a genuinely different scope
    (e.g. a function declared inside namespace ``other`` whose own
    unqualified ``Handle`` parameter really means ``other::Handle``) still
    matches ``ns::Handle``'s leaf candidate -- there is no textual marker
    left to reject on, since the reference carries no qualifier at all. This
    is the exposure-detection mirror of the identical, already-accepted
    bare-name-collision limitation on the *suppression* side (see
    ``TestKnownGapStaysDocumented`` in ``tests/test_opaque_identity_tiers.py``)
    -- both are the same underlying limitation of matching by rendered
    spelling instead of by an entity's actual owning scope, which is
    exactly what ``EntityId``-based identity resolution exists to close
    properly, not a heuristic patch here. See
    ``test_find_by_value_types_leaf_widening_bare_reference_in_another_scope_is_a_documented_gap``
    in ``tests/test_cov95_diff_filtering.py`` for the pinned reproduction."""
    if _references_type_token(tname, text):
        return True
    if "::" not in tname:
        return False
    leaf = depth_aware_bare_name(tname)
    if not leaf or leaf == tname:
        return False
    return _references_unqualified_type_token(leaf, text)


def _is_indirect_spelling(text: str) -> bool:
    """Whether rendered type *text* declares a pointer or reference (any
    indirection level, any cv-qualifier spelling).

    The original check here (``text.endswith("*")`` or ``"* " in text``)
    only recognized a pointer whose ``*`` sits at the very end or is
    followed by a space -- missing a cv-qualified pointer declarator like
    ``"Handle *const"`` or ``"Handle*const"`` (both clang and castxml can
    render the qualifier with no separating space), which wrongly counted
    as *not* a pointer and so as a by-value exposure. A real C/C++
    declarator can only contain ``*`` as a pointer/indirection marker --
    never as part of a bare type name -- so any ``*`` anywhere in the
    rendered text is sufficient evidence of at least one indirection level,
    with no false-positive risk from the type name itself (Codex review on
    PR #1041).

    A reference declarator (``Handle&``/``Handle&&``) is indirection too --
    it never exposes the pointee's own layout by value -- but carries no
    ``*`` at all, so it needs its own check: any ``&`` anywhere in the
    rendered text (the C/C++ reference sigil is likewise never part of a
    bare type name) is sufficient evidence, covering both an lvalue and an
    rvalue reference alike (Codex review on PR #1041, follow-up round --
    without this, a genuinely opaque type referenced only by ``Handle&``
    was wrongly counted as by-value exposed and dropped out of both
    identity tiers, reporting its private layout change as breaking)."""
    return "*" in text or "&" in text


def find_by_value_types(snap: AbiSnapshot, opaque: set[str]) -> set[str]:
    """Return the subset of *opaque* types that any public function/variable uses by value."""
    by_value_types: set[str] = set()
    for func in snap.functions:
        if func.visibility not in _PUBLIC_VIS:
            continue
        rt = func.return_type.strip()
        for tname in opaque:
            if tname in by_value_types:
                continue
            if _type_is_by_value_referenced(tname, rt) and not _is_indirect_spelling(
                rt
            ):
                by_value_types.add(tname)
        for param in func.params:
            pt = param.type.strip()
            for tname in opaque:
                if tname in by_value_types:
                    continue
                if (
                    _type_is_by_value_referenced(tname, pt)
                    and param.pointer_depth == 0
                    and not _is_indirect_spelling(pt)
                ):
                    by_value_types.add(tname)
    # Also check variables — a public variable of this type means it's by-value
    for var in snap.variables:
        if var.visibility not in _PUBLIC_VIS:
            continue
        vt = var.type.strip()
        for tname in opaque:
            if tname in by_value_types:
                continue
            if _type_is_by_value_referenced(tname, vt) and not _is_indirect_spelling(
                vt
            ):
                by_value_types.add(tname)
    return by_value_types
