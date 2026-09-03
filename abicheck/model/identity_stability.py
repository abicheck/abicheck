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

"""Cross-snapshot stability gate for :class:`~.identity.EntityId` (ADR-063
Phase 2's third design for the ``entity:`` promotion question).

Split out of ``identity.py`` itself purely to stay under that module's
800-line production cap (``architecture/debt.yaml`` tracks no growth
baseline for it) -- not a redesign; a plain, dependency-free predicate over
``EntityId``'s own public shape.

See ``docs/contribute/plans/one-semantic-pipeline.md``'s Phase 2 Design
section for the full history: ``EntityId.key`` is always well-defined and
stable *within one parse*, but any ``Anonymous``/``LocalToFunction``
segment's own ``ordinal``/``block_ordinal`` -- a deterministic per-parent
sequence number assigned at parse time -- is not stable *across* two
snapshots (inserting or removing an earlier anonymous/local sibling shifts
every later one's ordinal, and therefore its whole ``EntityId``, even
though nothing about those later declarations changed). Two prior attempts
to stabilize that ordinal were each independently reverted: a source-
location anchor (unreliable across a rebuild) and a structural fingerprint
of the anonymous scope's own members (circular -- the members' own
identity is what ``ScopePath`` exists to resolve in the first place). This
module does not attempt a third fix. It makes no claim about the unstable
cases at all; it only lets a caller *exclude* them from any behavior that
requires comparing an ``EntityId`` across two different snapshots, rather
than silently trusting an ordinal that may have shifted for an unrelated
reason.

A ``True`` result is a necessary, not sufficient, precondition for treating
an ``entity:``-keyed match as authoritative anywhere -- this predicate has
no call site anywhere in the codebase yet (only its own definition and
tests), deliberately: a real consumer (e.g. ``diff_filtering.
_deduplicate_cross_detector``) needs the same adversarial review rigor
that file's prior identity/dedup fixes required, not a drive-by wiring in
the same slice that first makes the primitive available. In particular,
this predicate says nothing about whether ``entity:`` is safe to promote
into ``finding_identity.report_canonical_finding_id`` -- the hash source
for a user's persisted ``--suppress`` ``finding_id:`` selector -- since
changing what that hash covers for an already-shipped finding shape would
silently invalidate existing stored suppression rules for it, a backward-
compatibility decision this module deliberately leaves to a separate,
explicit call.

Leaf module: depends only on ``.identity``, per ADR-063 D10.
"""

from __future__ import annotations

from .identity import Anonymous, EntityId, LocalToFunction

__all__ = ["entity_id_is_cross_snapshot_stable"]


def entity_id_is_cross_snapshot_stable(entity_id: EntityId) -> bool:
    """Whether *entity_id* is safe to compare across two different
    snapshots rather than only within the one parse that produced it. See
    this module's own docstring for the full design and its limits.
    """
    if entity_id.extra[:1] == ("anonymous",):
        return False
    return not any(
        isinstance(segment, (Anonymous, LocalToFunction)) for segment in entity_id.scope
    )
