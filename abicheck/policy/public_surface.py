# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""The public-surface/exports relevance query (ADR-063 Phase 3 D5).

**Scoping decision, stated plainly, not silently claimed complete.** This
module is the new, forward-facing entry point every Phase 3 consumer
threads through — but its actual relevance computation is not yet a
literal traversal over ``compare.surface_graph``'s own nodes/edges. It
delegates to ``surface.py``'s/``export_surface.py``'s existing,
extensively-reviewed closure-walk algorithms unchanged, via
:func:`resolve_public_surface`/:meth:`PublicSurfaceQuery.resolve_export_domain`.
Migrating those closure walks onto a literal graph traversal (this
phase's own stated Acceptance criteria: "``surface.py``'s own traversal
implementation ... deleted, not kept alongside") is real, scoped,
follow-up work — see ``docs/contribute/plans/one-semantic-pipeline.md``'s
Phase 3 section for the full accounting of what remains open. Attempting
that migration blind, in one pass, against an algorithm whose own design
text needed a dozen review rounds to state correctly, risks a silent
regression in a real ABI-compatibility verdict; reusing the proven
implementation here is the safer, honestly-scoped choice for this slice.

What **is** real and complete: the unified ``compare/surface_graph.py``
evidence graph (populated unconditionally from L0-L2 facts, one
representation, no second dataclass hierarchy — the Governing Invariant
this phase exists to defend), the ``AbiSnapshot.surface_graph`` field/
schema/persistence, and this module's own ``EntityId``-based query
surface, which every new Phase 3 consumer now threads through instead of
each independently re-deriving a ``Visibility.PUBLIC``-only answer.

``policy -> compare``/``model`` is an already-allowed ADR-061 import edge;
``surface.py``/``export_surface.py`` are themselves already classified
``policy`` layer in ``architecture/modules.yaml`` (ahead of this phase, via
an unrelated prior classification pass), so importing them from here is an
ordinary same-layer sibling import, not a residual future-migration risk
the phase's own design text once worried about.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..export_surface import ExportSurface, compute_export_surface
from ..surface import PublicSurface as PublicSurfaceResolution, compute_public_surface

if TYPE_CHECKING:
    from ..model.identity import EntityId
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "PublicSurfaceQuery",
    "PublicSurfaceResolution",
    "resolve_public_surface",
]


def resolve_public_surface(
    snapshot: AbiSnapshot, explicit_roots: object = None
) -> PublicSurfaceResolution:
    """The one place every new Phase 3 consumer resolves a snapshot's
    public surface from. *explicit_roots* is accepted for this function's
    own future graph-native signature (a real traversal takes explicit
    root ids rather than re-deriving ``Visibility.PUBLIC`` internally) but
    is not yet consulted — see module docstring's scoping note; the
    resolution today comes entirely from *snapshot*'s own already-computed
    ``Visibility``/origin fields, the same inputs :func:`compute_public_surface`
    already reads.
    """
    return compute_public_surface(snapshot)


class PublicSurfaceQuery:
    """Bare-membership and structured relevance queries over one
    snapshot's public/exports surface (ADR-063 Phase 3 D5)."""

    @staticmethod
    def resolve(snapshot: AbiSnapshot) -> frozenset[EntityId]:
        """Which declarations' resolved ``EntityId`` are on *snapshot*'s
        public surface — the bare-membership convenience a caller that
        only needs set membership reaches for, never
        :meth:`resolve_public_domain`'s richer result. **Not** a
        function/variable-only set — it genuinely includes record/enum
        ``EntityId``s too (a public function's return type, a publicly
        reachable struct), matching ``PublicSurface.public_symbols``
        *and* ``public_types`` alike. A downstream consumer that wants
        only roots (e.g. ``surface_graph.py``'s ``public_roots()``)
        filters this set to ``kind in (FUNCTION, VARIABLE)`` itself — a
        type-kind id here is correct data for that consumer to drop, not
        something this method should pre-filter away for every caller.

        A declaration whose parse-time ``entity_id`` carrier is
        unpopulated (a pre-ADR-063-Phase-2 snapshot, or a kind the
        header-AST backends don't resolve one for yet) is silently
        excluded, not attempted-and-failed — the identical, already-
        accepted degradation ``public_roots()``'s own ``Visibility.PUBLIC``
        fallback already uses when no resolved id set is available at all.
        """
        surf = resolve_public_surface(snapshot)
        if not surf.resolvable:
            return frozenset()
        ids: set[EntityId] = set()
        for fn in snapshot.functions:
            if fn.entity_id is not None and (
                fn.mangled in surf.public_symbols or fn.name in surf.public_symbols
            ):
                ids.add(fn.entity_id)
        for var in snapshot.variables:
            if var.entity_id is not None and (
                var.mangled in surf.public_symbols or var.name in surf.public_symbols
            ):
                ids.add(var.entity_id)
        for rec in snapshot.types:
            if rec.entity_id is not None and (
                rec.name in surf.public_types
                or (rec.qualified_name or "") in surf.public_types
            ):
                ids.add(rec.entity_id)
        for en in snapshot.enums:
            if en.entity_id is not None and (
                en.name in surf.public_types
                or (en.qualified_name or "") in surf.public_types
            ):
                ids.add(en.entity_id)
        return frozenset(ids)

    @staticmethod
    def resolve_public_domain(snapshot: AbiSnapshot) -> PublicSurfaceResolution:
        """The structured replacement for ``compute_public_surface()``'s
        result — ``resolvable``/``has_typed_roots``/``has_provenance``/
        ``ambiguous_type_names``/``exact_type_identities``/both origin
        indices, none of which a bare id set can express. Today this *is*
        :func:`resolve_public_surface`'s own return value — see module
        docstring."""
        return resolve_public_surface(snapshot)

    @staticmethod
    def resolve_export_domain(snapshot: AbiSnapshot) -> ExportSurface:
        """The ``contract=exports`` domain's structured result
        (``resolvable``/``exclusion_is_provable`` and the rest) — does
        **not** collapse into :meth:`resolve`'s bare ``frozenset[EntityId]``,
        since ``contract_evaluation.py``/``contract_evidence_collect.py``
        consume exactly the completeness state a membership set has
        nowhere to carry."""
        return compute_export_surface(snapshot)


# ``type_reachability.directly_referenced_stdlib_types()`` migrating here
# (D5's own closing note: "itself a relevance decision... becomes a second,
# narrower query in policy/public_surface.py") is deliberately NOT done in
# this slice, for a reason specific to this one function rather than this
# module's general scoping note above: `type_reachability.py` itself
# imports `diff_cxx_rules.py`/`type_reachability_spelling.py`, both already
# classified `extract` layer in `architecture/modules.yaml` — reclassifying
# `type_reachability.py` as `policy` (this module's own layer) would
# introduce a genuine new `policy -> extract` dependency-direction
# violation for those two pre-existing imports, not merely thread a
# passthrough. Resolving that needs its own real investigation (does
# `diff_cxx_rules.py`/`type_reachability_spelling.py` belong in `policy`
# too, or does `type_reachability.py` itself not actually belong here),
# not a same-slice reclassification alongside everything else in this
# file. Left as a named, scoped-out follow-up.
