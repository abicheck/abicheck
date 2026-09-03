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

"""The legacy-flat-snapshot adapter for
:class:`~abicheck.model.semantic_ir_index.SemanticIRIndex` (ADR-063 Phase 6B,
"PR 2" second slice — the half that makes the first detector cutover
possible).

``SemanticIRIndex`` landed with no live caller by design, and one thing
still stood between it and a real detector: a detector reading only through
the index would see **nothing at all** on a snapshot that carries no
``SemanticIR`` — a DWARF-only or PE-only extraction, or any snapshot written
before schema v38. Every such comparison would silently lose a whole
detector family. This module removes that obstacle by projecting the legacy
flat collections into a real ``SemanticIR``, so the *same* index class reads
both and a migrated detector cannot tell which it was handed.

**Producing a real ``SemanticIR`` rather than a parallel "index-like"
type is the point.** A second read shape would be a second thing to keep in
agreement with the first — exactly the duplication ADR-063's governing
invariant forbids. The adapter's output goes through
``SemanticIRIndex(ir)`` unchanged.

**Display names, and why they need a fidelity gate.** The legacy typedef
collections are keyed by a flat qualified spelling; ``SemanticIR`` is keyed
by ``EntityId``. :func:`render_display_name` is the one projection between
them, and it deliberately answers ``None`` — never a best-effort string —
for an ``EntityId`` whose ``ScopePath`` contains an ``Anonymous`` or
``LocalToFunction`` segment: both carry a parse-order ordinal that appears
in no source spelling, so any string produced for them would be an
invention, and two distinct such declarations would render alike.
:func:`typedef_index_pair` turns that ``None`` (and any other divergence)
into a fallback to this adapter rather than into a silently smaller set for
a detector to iterate.

**Synthetic identity is marked, not hidden.** A legacy declaration whose
producer resolved no ``EntityId`` still needs one to key an occurrence by,
so the adapter derives one from the display spelling and tags it
:data:`SYNTHETIC_IDENTITY_EXTRA`. :func:`producer_entity_id` is how a
consumer asks "did a backend actually resolve this?" — a synthetic id must
never reach a ``Change.entity_id``, because ``finding_identity.
resolve_change_identity`` folds that into an ``entity:`` alias that real,
stored suppression rules match against. Passing this index's own
bookkeeping off as backend evidence would change which suppressions fire.

Leaf module: depends only on other ``model`` modules, per ADR-061 D1's
``model/`` import ceiling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fact import Fact
from .identity import (
    EntityId,
    EntityKind,
    InlineNamespace,
    Namespace,
    Record,
    ScopeSegment,
)
from .occurrence import OccurrenceId
from .semantic_ir import CanonicalEntity, SemanticIR
from .semantic_ir_index import SemanticIRIndex

if TYPE_CHECKING:
    from .snapshot import AbiSnapshot

__all__ = [
    "SYNTHETIC_IDENTITY_EXTRA",
    "legacy_typedef_ir",
    "producer_entity_id",
    "render_display_name",
    "typedef_index_pair",
]

#: ``EntityId.extra`` marker for an identity this adapter derived from a
#: display spelling because the producer resolved none. Chosen to be
#: unmistakable and unreachable by any real constructor in
#: ``model/identity.py`` (whose ``extra`` values are ``()``,
#: ``("mangled", ...)``, ``("extern_c",)``, ``("sig", ...)`` or
#: ``("anonymous", ...)``), so :func:`producer_entity_id` cannot
#: misclassify a genuine backend identity as synthetic.
SYNTHETIC_IDENTITY_EXTRA = ("legacy-adapter-synthetic-identity",)


def render_display_name(entity_id: EntityId) -> str | None:
    """The flat ``"a::b::Leaf"`` spelling *entity_id* names, or ``None`` when
    its scope contains a segment with no faithful flat rendering.

    ``Anonymous``/``LocalToFunction`` are exactly those segments — see this
    module's docstring for why ``None`` rather than a best-effort string is
    the load-bearing answer. A ``Record`` segment renders by name alone,
    matching ``EntityId.key``'s own exclusion of ``Record.access``: access
    is payload, not identity, and never appears in a qualified spelling.
    """
    parts: list[str] = []
    for segment in entity_id.scope:
        rendered = _render_segment(segment)
        if rendered is None:
            return None
        parts.append(rendered)
    parts.append(entity_id.leaf_name)
    return "::".join(parts)


def _render_segment(segment: ScopeSegment) -> str | None:
    if isinstance(segment, Namespace | Record | InlineNamespace):
        return segment.name
    # `Anonymous`/`LocalToFunction`, and anything a later slice adds: no
    # faithful rendering. Fail closed rather than guess.
    return None


def producer_entity_id(entity_id: EntityId) -> EntityId | None:
    """*entity_id* if a real backend resolved it, ``None`` if this adapter
    synthesized it from a display spelling.

    The one predicate a consumer needs before stamping an index-derived
    identity onto anything durable (a ``Change``, a receipt, a persisted
    node id) — see this module's docstring for what goes wrong otherwise.
    """
    if tuple(entity_id.extra) == SYNTHETIC_IDENTITY_EXTRA:
        return None
    return entity_id


def _synthetic_entity_id(kind: EntityKind, display_name: str) -> EntityId:
    """An identity derived purely from *display_name*, tagged synthetic.

    ``scope=()`` with the whole flat spelling as ``leaf_name`` — so
    :func:`render_display_name` reproduces *display_name* exactly, which is
    what keeps a detector reading through the adapter emitting the same
    ``Change.symbol``/description text it emitted before its migration.
    """
    return EntityId(
        scope=(), kind=kind, leaf_name=display_name, extra=SYNTHETIC_IDENTITY_EXTRA
    )


def legacy_typedef_ir(snapshot: AbiSnapshot, typedefs: dict[str, str]) -> SemanticIR:
    """Project one snapshot's typedef collection into a real ``SemanticIR``.

    *typedefs* is the alias -> underlying-type map the *comparison* already
    chose (``diff_helpers.typedef_diff_maps``), not one this function picks:
    which of ``AbiSnapshot.typedefs``/``typedefs_qualified`` to trust is a
    pair-wise decision a single-snapshot projection cannot make, and
    re-deriving it here would be a second opinion about it.

    Each alias becomes one occurrence whose ``canonical_spelling`` is its
    underlying type — the identical payload
    ``extract/semantic_normalizer.py`` produces for a typedef from a real
    header-AST backend, so the two sources are interchangeable to a reader.

    The ``entity_id`` sidecar is used when it is present *and* renders back
    to the alias exactly; otherwise a synthetic, display-derived identity is
    used (see :func:`producer_entity_id`). The render check is not paranoia:
    a sidecar id whose rendering disagrees with its own map key would key
    the occurrence under a name the detector then cannot find, turning a
    projection mismatch into a phantom removal.
    """
    occurrences: dict[OccurrenceId, CanonicalEntity] = {}
    for alias, underlying in typedefs.items():
        sidecar = snapshot.typedef_entity_ids.get(alias)
        if sidecar is not None and render_display_name(sidecar) == alias:
            entity_id = sidecar
        else:
            entity_id = _synthetic_entity_id(EntityKind.TYPEDEF, alias)
        occurrences[OccurrenceId(entity_id)] = CanonicalEntity(
            canonical_spelling=Fact.present(underlying)
        )
    return SemanticIR(occurrences=occurrences)


def _typedef_display_names(index: SemanticIRIndex) -> tuple[str, ...]:
    """The alias keys *index* projects for typedefs, **in order**, dropping
    any identity with no faithful rendering (which is what makes an
    unrenderable scope visible to the gate below as a missing key).

    Ordered, not a set, because nothing between a detector and a report
    re-sorts findings -- emission order *is* output order. Two projections
    holding the same aliases in a different order would therefore produce
    the same findings in a different sequence, which is a real (if cosmetic)
    output difference the gate below would otherwise wave through. Both
    orders derive from the same header-AST element pass in practice, so
    requiring equality here costs nothing real and removes the assumption.
    """
    names: list[str] = []
    for entity_id in index.entities_of_kind(EntityKind.TYPEDEF):
        rendered = render_display_name(entity_id)
        if rendered is not None:
            names.append(rendered)
    return tuple(names)


def typedef_index_pair(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_typedefs: dict[str, str],
    new_typedefs: dict[str, str],
) -> tuple[SemanticIRIndex, SemanticIRIndex]:
    """The typedef cohort's index pair: ``SemanticIR``-backed when — and only
    when — that is provably equivalent to the legacy projection.

    The gate is strict and symmetric: **both** sides' IR-backed typedef
    display-name key sets must exactly equal the alias maps this comparison
    already resolved. Any difference at all — an unrenderable anonymous
    scope, a producer that resolved identity for only some typedefs, a
    DWARF-only side with no IR, a pre-v38 reload — and both sides fall back
    to :func:`legacy_typedef_ir`.

    Both-or-neither matters, and is not merely tidiness: pairing an
    IR-backed old side with an adapted new side would compare two
    differently-derived key spaces, which fabricates a removal or an
    addition out of a projection difference rather than a real ABI change.

    The comparison is over ordered alias *sequences*, never a count and
    never an unordered set: two producers can agree on how many typedefs
    exist while disagreeing about which, and two that agree on which can
    still disagree about the order findings would be emitted in (see
    :func:`_typedef_display_names`).
    """
    old_index = SemanticIRIndex(old.semantic_ir or SemanticIR())
    new_index = SemanticIRIndex(new.semantic_ir or SemanticIR())
    if _typedef_display_names(old_index) == tuple(
        old_typedefs
    ) and _typedef_display_names(new_index) == tuple(new_typedefs):
        return old_index, new_index
    return (
        SemanticIRIndex(legacy_typedef_ir(old, old_typedefs)),
        SemanticIRIndex(legacy_typedef_ir(new, new_typedefs)),
    )
