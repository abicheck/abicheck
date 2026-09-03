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

"""Two identity *tiers* for a post-parse consumer: :class:`StableEntityId`
and :class:`SnapshotLocalIdentity` (ADR-063 Phase 2, the post-parse consumer
migration).

**Why two tiers and not one globally-stable id.** ``EntityId`` is always
well-defined, but it is not always comparable *across* two snapshots: an
``Anonymous``/``LocalToFunction`` scope segment carries a per-parent ordinal
assigned at parse time, so inserting an anonymous sibling ahead of existing
ones shifts every later sibling's whole ``EntityId`` even though nothing
about those declarations changed. Two attempts to make that ordinal
globally stable were each designed and reverted (a source-location anchor,
unreliable across a rebuild; a structural fingerprint of the anonymous
scope's own members, circular -- those members' identity is what
``ScopePath`` exists to resolve). **This module does not attempt a third.**
It instead splits the consumers' need in half, so the unstable case is
*named* rather than silently trusted:

* :class:`StableEntityId` -- an ``EntityId`` that
  :func:`~abicheck.model.identity_stability.entity_id_is_cross_snapshot_stable`
  admits. Comparing two of these across an old/new snapshot pair (or against
  a stored suppression rule written against an earlier release) is
  meaningful: nothing in the id was derived from parse order. This is the
  tier a cross-release/suppression-alias consumer may use.
* :class:`SnapshotLocalIdentity` -- everything else: an ``EntityId``
  carrying an unstable ordinal, or a declaration with no resolved
  ``EntityId`` at all (a DWARF/PE/Mach-O-only snapshot, where no backend
  resolves one). Valid only *within* one snapshot or one comparison, and
  documented as such at the type level so no consumer can accidentally
  persist one.

**The two tiers never compare equal to each other**, by construction: they
are distinct dataclasses, so a set of one tier can never be satisfied by a
lookup in the other. That is deliberate. A consumer that wants both must
consult them in an explicit precedence order and say so
(``diff_filtering._OpaqueTypeIndex`` is the worked example), rather than
mixing tiers into one bag where a snapshot-local ordinal could silently
answer a cross-release question.

**A ``SnapshotLocalIdentity`` is keyed on a caller-chosen *spelling*, not on
the ``EntityId``.** The post-parse consumers migrating onto this module
already key on a declaration spelling today (``RecordType.name``,
``Change.symbol``), and that spelling is the only thing available for the
no-``EntityId`` case at all. Carrying the (unstable) ``EntityId`` alongside
as a non-identity payload keeps it available for diagnostics without
letting it participate in equality -- the same identity-vs-payload split
``identity.Record.access`` already establishes one level down.

Leaf module: depends only on ``model.identity``/``model.identity_stability``,
per ADR-063 D10.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .identity import EntityId
from .identity_stability import entity_id_is_cross_snapshot_stable

__all__ = [
    "ResolvedIdentity",
    "SnapshotLocalIdentity",
    "StableEntityId",
    "resolve_identity",
    "snapshot_local_identity",
    "stable_entity_id",
]


@dataclass(frozen=True)
class StableEntityId:
    """An ``EntityId`` established as comparable across snapshots.

    Only ever constructed through :func:`stable_entity_id`, which applies
    :func:`~abicheck.model.identity_stability.
    entity_id_is_cross_snapshot_stable` and answers ``None`` rather than
    wrapping an id that fails it -- so the type itself carries the
    guarantee, and a consumer holding one needs no second check.

    A ``True`` stability result is a *necessary, not sufficient*
    precondition for treating a cross-release match as authoritative --
    see ``identity_stability``'s own docstring for what a consumer still
    has to establish beyond it (this type makes the gate un-forgettable,
    it does not widen what the gate proves).
    """

    entity_id: EntityId

    @property
    def key(self) -> str:
        """A flat, collision-safe string for this identity.

        Tag-prefixed so a ``StableEntityId``'s key can never collide with a
        :class:`SnapshotLocalIdentity`'s even if a caller flattens both
        tiers into one string-keyed map (which the type system otherwise
        prevents -- this keeps the property true one layer down too).
        """
        return f"stable\x1f{self.entity_id.key}"


@dataclass(frozen=True)
class SnapshotLocalIdentity:
    """An identity valid only within one snapshot or one comparison.

    *spelling* is the identity; *entity_id* is a non-identity payload
    (``field(compare=False)``) carrying whatever unstable ``EntityId`` the
    producer did resolve, for diagnostics only. Excluding it from equality
    is load-bearing, not cosmetic: an entity whose ordinal shifted between
    two parses must still match itself by spelling, which is exactly the
    fallback this tier exists to provide.

    Never persist one. It is meaningful only against the snapshot(s) it was
    derived from -- a stored suppression rule, a baseline, or any other
    cross-release artifact needs :class:`StableEntityId`.
    """

    spelling: str
    entity_id: EntityId | None = field(default=None, compare=False)

    @property
    def key(self) -> str:
        """A flat string for this identity. See :attr:`StableEntityId.key`
        for why the tier tag is part of it."""
        return f"local\x1f{self.spelling}"


#: Either tier. A consumer accepting this must handle both -- there is no
#: implicit promotion from snapshot-local to stable anywhere in this module.
ResolvedIdentity = StableEntityId | SnapshotLocalIdentity


def stable_entity_id(entity_id: EntityId | None) -> StableEntityId | None:
    """Wrap *entity_id* as a :class:`StableEntityId`, or ``None`` when it is
    absent or fails the cross-snapshot stability gate.

    The single constructor for the stable tier: there is deliberately no way
    to build one that skips the gate, so "is this id safe to compare across
    releases" is answered once, here, rather than at each consumer.
    """
    if entity_id is None:
        return None
    if not entity_id_is_cross_snapshot_stable(entity_id):
        return None
    return StableEntityId(entity_id)


def snapshot_local_identity(
    spelling: str, entity_id: EntityId | None = None
) -> SnapshotLocalIdentity:
    """Build the fallback tier from *spelling* (the identity) and, when the
    producer resolved one, *entity_id* (diagnostics-only payload).

    Accepts an ``EntityId`` that is perfectly stable, deliberately: a caller
    that wants the stable tier asks for it explicitly via
    :func:`stable_entity_id` or :func:`resolve_identity`. Silently upgrading
    here would make the returned tier depend on the input's shape rather
    than on what the caller asked for, and a consumer's precedence order
    (which tier it consults first) is exactly the decision this module
    refuses to make on its behalf.
    """
    return SnapshotLocalIdentity(spelling=spelling, entity_id=entity_id)


def resolve_identity(*, entity_id: EntityId | None, spelling: str) -> ResolvedIdentity:
    """The best tier available for one declaration: :class:`StableEntityId`
    when *entity_id* passes the stability gate, else
    :class:`SnapshotLocalIdentity` keyed on *spelling*.

    Use this where a consumer holds exactly one identity per declaration and
    wants the strongest one available. Where a consumer needs *both* tiers
    for one declaration (an index that must answer a spelling-keyed lookup
    from a producer that resolved no ``EntityId``, as well as an
    identity-keyed one), build them separately -- this function returns one,
    on purpose, so "which tier is this?" always has a single answer.
    """
    stable = stable_entity_id(entity_id)
    if stable is not None:
        return stable
    return snapshot_local_identity(spelling, entity_id)
