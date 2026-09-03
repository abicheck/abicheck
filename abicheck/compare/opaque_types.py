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

from dataclasses import dataclass
from typing import TYPE_CHECKING

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
    full one (see :func:`_by_value_scan_spellings`), since a qualification
    mismatch there used to be silently absorbed by an equally spelling-based
    join and no longer is once :class:`OpaqueTypeIndex`'s stable tier can
    join across exactly that mismatch. Only the *result* is re-expressed
    as identities.
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


def _by_value_scan_spellings(tname: str) -> tuple[str, ...]:
    """The spelling(s) of *tname* to search a rendered signature string for.

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

    Returns *tname* itself plus its unqualified leaf spelling (the segment
    after the last ``"::"``) when the two differ -- never only the leaf, so
    an already-bare name is unaffected and this stays a pure widening of
    what the old single-spelling scan already caught."""
    if "::" not in tname:
        return (tname,)
    leaf = tname.rsplit("::", 1)[-1]
    return (tname, leaf) if leaf and leaf != tname else (tname,)


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
            if any(
                spelling in rt for spelling in _by_value_scan_spellings(tname)
            ) and not (rt.endswith("*") or "* " in rt):
                by_value_types.add(tname)
        for param in func.params:
            pt = param.type.strip()
            for tname in opaque:
                if tname in by_value_types:
                    continue
                if (
                    any(spelling in pt for spelling in _by_value_scan_spellings(tname))
                    and param.pointer_depth == 0
                    and not pt.endswith("*")
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
            if any(
                spelling in vt for spelling in _by_value_scan_spellings(tname)
            ) and not (vt.endswith("*") or "* " in vt):
                by_value_types.add(tname)
    return by_value_types
