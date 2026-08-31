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


def _linker_key_is_public(mangled: str, name: str, public_symbols: set[str]) -> bool:
    """Whether a function/variable counts as public per *public_symbols*,
    preferring its own mangled linker identity (unambiguous per overload)
    over its bare demangled name whenever the two genuinely differ.

    When two C++ overloads share one demangled name and only one is
    public, ``surface.py``'s own ``_seed_public_roots`` unions *both* the
    mangled name and the bare name into ``public_symbols`` for the public
    overload alone -- but a plain ``mangled in ... or name in ...`` check
    still matches the *other*, non-public overload via that shared bare
    name, since nothing about the bare-name membership test can tell which
    specific overload it was recorded for (Codex review, PR #962). Falling
    back to the bare name is only safe when it *is* the linker identity
    (``mangled == name``, e.g. C code or an unmangled backend) -- there the
    two checks are equivalent and no ambiguity exists.
    """
    if mangled and mangled != name:
        return mangled in public_symbols
    return mangled in public_symbols or name in public_symbols


def _has_any_entity_id(snapshot: AbiSnapshot) -> bool:
    """Whether *any* function/variable/type/enum on *snapshot* carries a
    resolved ``entity_id`` -- the whole-snapshot availability signal
    :meth:`PublicSurfaceQuery.resolve` needs to distinguish "entity-id
    resolution is unavailable on this snapshot" (fall back to ``None``)
    from "resolution ran and genuinely found zero public declarations"
    (a real, non-``None`` empty ``frozenset``)."""
    return (
        any(fn.entity_id is not None for fn in snapshot.functions)
        or any(var.entity_id is not None for var in snapshot.variables)
        or any(rec.entity_id is not None for rec in snapshot.types)
        or any(en.entity_id is not None for en in snapshot.enums)
    )


class PublicSurfaceQuery:
    """Bare-membership and structured relevance queries over one
    snapshot's public/exports surface (ADR-063 Phase 3 D5)."""

    @staticmethod
    def resolve(snapshot: AbiSnapshot) -> frozenset[EntityId] | None:
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

        Returns ``None`` — not an empty ``frozenset`` — whenever this
        snapshot cannot support a real ``EntityId``-based answer: either
        ``resolve_public_surface()`` itself is unresolvable (``surf.
        resolvable`` is ``False`` — no header-derived visibility exists at
        all, per :class:`~abicheck.surface.PublicSurface`'s own docstring,
        "scoping is skipped entirely"), or *snapshot* carries no
        ``entity_id``-bearing declaration at all (a pre-ADR-063-Phase-2
        snapshot, schema < 28, or a header-AST backend gap wide enough that
        nothing on the whole snapshot resolved one). Every
        ``*_public_entity_ids`` consumer downstream (``build_
        surface_graph``/``compute_surface_metrics``/``compare()``) treats
        ``None`` as "fall back to the legacy ``Visibility.PUBLIC``-only
        answer" — collapsing either unavailability case to an empty
        ``frozenset`` instead would make every one of them read a real,
        non-empty public surface as confirmed-empty, silently zeroing
        ``--surface-metrics``/``--pattern-verdicts`` output whenever this
        query cannot actually answer (Codex review, PR #962). A single
        declaration missing its own ``entity_id`` while siblings have
        theirs is a different, already-accepted degradation — that
        declaration alone drops out of the (non-``None``) set below,
        unchanged.
        """
        surf = resolve_public_surface(snapshot)
        if not surf.resolvable:
            return None
        if not _has_any_entity_id(snapshot):
            return None
        ids: set[EntityId] = set()
        for fn in snapshot.functions:
            if fn.entity_id is not None and _linker_key_is_public(
                fn.mangled, fn.name, surf.public_symbols
            ):
                ids.add(fn.entity_id)
        for var in snapshot.variables:
            if var.entity_id is not None and _linker_key_is_public(
                var.mangled, var.name, surf.public_symbols
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
