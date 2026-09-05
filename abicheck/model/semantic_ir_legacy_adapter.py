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

**Display names.** The legacy typedef collections are keyed by a flat
qualified spelling; ``SemanticIR`` is keyed by ``EntityId``.
:func:`render_display_name` is the one projection between them, and it
deliberately answers ``None`` — never a best-effort string — for an
``EntityId`` whose ``ScopePath`` contains an ``Anonymous`` or
``LocalToFunction`` segment: both carry a parse-order ordinal that appears
in no source spelling, so any string produced for them would be an
invention, and two distinct such declarations would render alike. Before
ADR-063 Track T3, ``compare.typedefs.typedef_index_pair`` turned that
``None`` (and any other divergence from the legacy projection) into a
fallback to this adapter; since T3, both sides' real ``SemanticIR`` is used
directly whenever both carry one, and an entity that renders ``None`` is
simply invisible to a detector's own alias/name projection (it has no flat
spelling to key a finding under, on either backing). This module's own
identity primitives (:func:`producer_entity_id`, and the two Track T3
consistency checks below) are what a real ``SemanticIR`` is now checked
against instead: not a competing legacy projection, but the same
producer's own legacy sidecar identity for the same declaration. The
selector functions themselves (``compare.typedefs.typedef_index_pair``/
``compare.constants.constant_index_pair``) live in ``compare/`` rather than
here: choosing between two *snapshots'* representations is a
comparison-orchestration question, not a model shape or a single-snapshot
projection, per ADR-061's ownership split (Codex review on PR #1041) — this
module stays the single-snapshot projection (:func:`legacy_typedef_ir`)
plus the rendering/identity primitives every consumer of it, on either side
of that split, shares.

**Cohort 2 (constants) reuses this module verbatim.** ADR-063 Phase 6B's
second detector cohort (``compare/constants.py``) is the identical shape as
the typedef cohort above, substituting the constant collections
(``AbiSnapshot.constants``/``constant_entity_ids``) and ``EntityKind.
CONSTANT``: :func:`legacy_constant_ir` is :func:`legacy_typedef_ir` with a
value-literal payload instead of a resolved-type-spelling one, and
:func:`render_display_name`/:func:`producer_entity_id`/
:data:`SYNTHETIC_IDENTITY_EXTRA` are shared unchanged — a constant's
qualified name has exactly the same flat-spelling shape a typedef's alias
does, so nothing about the rendering or synthetic-identity story differs
between the two families.

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

from ..errors import SemanticIrAuthorityError
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
    "assert_constant_ir_consistent",
    "assert_snapshot_semantic_ir_consistent",
    "assert_typedef_ir_consistent",
    "legacy_constant_ir",
    "legacy_typedef_ir",
    "producer_entity_id",
    "render_display_name",
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


def legacy_constant_ir(snapshot: AbiSnapshot, constants: dict[str, str]) -> SemanticIR:
    """Project one snapshot's constant collection into a real ``SemanticIR``.

    The constant-family counterpart of :func:`legacy_typedef_ir` — same
    shape, substituting ``AbiSnapshot.constants``/``constant_entity_ids``
    and :attr:`~abicheck.model.identity.EntityKind.CONSTANT`. *constants* is
    ``AbiSnapshot.constants`` itself (there is no alias-map selection
    question here the way there is for typedefs' ``typedefs``/
    ``typedefs_qualified`` pair — a constant has exactly one legacy
    collection), passed in rather than read here because reading it is the
    caller's job, not a single-snapshot projection's.

    Each qualified name becomes one occurrence whose ``canonical_spelling``
    is its raw value text — the identical, deliberately-uncanonicalized
    payload ``extract/semantic_normalizer.py`` produces for a constant from
    a real header-AST backend (see that module's own "Scope of the fourth
    slice"), so the two sources are interchangeable to a reader. The
    ``entity_id`` sidecar is used when present *and* it renders back to the
    qualified name exactly; otherwise a synthetic, display-derived identity
    is used — identical reasoning to :func:`legacy_typedef_ir`'s own sidecar
    check, including why the render check matters (a sidecar id whose
    rendering disagrees with its own map key would key the occurrence under
    a name the detector then cannot find, turning a projection mismatch
    into a phantom removal).
    """
    occurrences: dict[OccurrenceId, CanonicalEntity] = {}
    for qualified_name, value in constants.items():
        sidecar = snapshot.constant_entity_ids.get(qualified_name)
        if sidecar is not None and render_display_name(sidecar) == qualified_name:
            entity_id = sidecar
        else:
            entity_id = _synthetic_entity_id(EntityKind.CONSTANT, qualified_name)
        occurrences[OccurrenceId(entity_id)] = CanonicalEntity(
            canonical_spelling=Fact.present(value)
        )
    return SemanticIR(occurrences=occurrences)


def _assert_sidecar_identity_consistent(
    snapshot: AbiSnapshot,
    *,
    kind: EntityKind,
    sidecar: dict[str, EntityId],
    family: str,
) -> None:
    """Raise :class:`~abicheck.errors.SemanticIrAuthorityError` if
    *snapshot*'s real ``SemanticIR`` resolves a different ``EntityId`` than
    *sidecar* for the same rendered alias/qualified name (ADR-063 Track T3).

    This is the one piece of the pre-T3 comparison-time fidelity gate that
    is still worth checking once IR is the sole comparison-time source: not
    ``typedefs``/``typedefs_qualified``/``constants``' *values* against the
    IR's own ``canonical_spelling`` (the whole point of the cutover is that
    ``SemanticIR``-only construction — no populated legacy dict at all — is
    now a completely ordinary, valid snapshot, not one this check should
    require a legacy companion for), but whether *two identity
    representations the same producer wrote for the same snapshot* agree
    with each other. ``typedef_entity_ids``/``constant_entity_ids`` and
    ``semantic_ir`` are populated from the same parse
    (``extract/semantic_normalizer.py``/``dumper_castxml.py``/
    ``dumper_clang.py``); a disagreement between them for the identical
    rendered name is not a legitimate difference in evidence the way
    "no ``SemanticIR`` at all" is — it is a producer bug in one of the two
    identity-resolution paths, and T3's whole point is that such a bug must
    fail loudly instead of being silently routed around by falling back to
    whichever representation happens to look consistent.

    Only entities with a *faithful* rendered name participate (an
    ``Anonymous``/``LocalToFunction`` scope segment renders ``None`` and is
    skipped, exactly as the comparison-time readers in
    ``compare/typedefs.py``/``compare/constants.py`` already do) — an
    unrenderable identity has no sidecar key it could possibly collide
    with, so there is nothing here for it to disagree about.
    """
    index = SemanticIRIndex(snapshot.semantic_ir) if snapshot.semantic_ir else None
    if index is None:
        return
    for entity_id in index.entities_of_kind(kind):
        rendered = render_display_name(entity_id)
        if rendered is None:
            continue
        sidecar_id = sidecar.get(rendered)
        if sidecar_id is not None and sidecar_id != entity_id:
            raise SemanticIrAuthorityError(
                f"{family} {rendered!r}: SemanticIR resolves entity_id "
                f"{entity_id!r}, but the snapshot's own "
                f"{family}_entity_ids sidecar records {sidecar_id!r} for "
                "the same name -- the producer's two identity "
                "representations disagree (ADR-063 Track T3: SemanticIR is "
                "the sole comparison-time source for this cohort, so this "
                "can no longer be silently resolved by falling back to a "
                "legacy projection)"
            )


def assert_typedef_ir_consistent(snapshot: AbiSnapshot) -> None:
    """Raise :class:`~abicheck.errors.SemanticIrAuthorityError` if
    *snapshot* carries a real ``SemanticIR`` whose typedef occurrences
    disagree, by identity, with its own ``typedef_entity_ids`` sidecar.

    Called once, from ``AbiSnapshot.__post_init__`` -- the load boundary --
    rather than by ``compare/typedefs.py`` on every comparison a snapshot
    participates in: the pre-T3 fidelity gate ran this class of check
    (among others) inside the comparison-time selector, once per
    comparison, for a value that only ever depends on one side. See
    :func:`_assert_sidecar_identity_consistent` for the full reasoning.
    """
    _assert_sidecar_identity_consistent(
        snapshot,
        kind=EntityKind.TYPEDEF,
        sidecar=snapshot.typedef_entity_ids,
        family="typedef",
    )


def assert_constant_ir_consistent(snapshot: AbiSnapshot) -> None:
    """The constant-family counterpart of :func:`assert_typedef_ir_consistent`
    -- same reasoning, substituting ``AbiSnapshot.constant_entity_ids`` and
    :attr:`~abicheck.model.identity.EntityKind.CONSTANT`."""
    _assert_sidecar_identity_consistent(
        snapshot,
        kind=EntityKind.CONSTANT,
        sidecar=snapshot.constant_entity_ids,
        family="constant",
    )


def assert_snapshot_semantic_ir_consistent(snapshot: AbiSnapshot) -> None:
    """Both families' Track T3 load-boundary check, in one call --
    ``AbiSnapshot.__post_init__``'s single entry point into this module (via
    ``importlib``, to avoid a real import cycle; see that method's own
    comment)."""
    assert_typedef_ir_consistent(snapshot)
    assert_constant_ir_consistent(snapshot)
