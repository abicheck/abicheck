# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""``PublicSurfaceQuery`` (ADR-063 Phase 3 D5): the one orchestrator that
answers "is this declaration on the public surface / exports surface"
across both relevance domains.

**Split out of ``policy/public_surface.py`` itself, not merely alongside
it, for a real dependency-direction reason, not organizational taste.**
``policy/public_surface.py`` is the leaf module housing the actual graph
traversal (:func:`~abicheck.policy.public_surface.resolve_public_surface`)
that ``export_surface.py`` itself now imports from (its own type-closure
step shares that module's ``_walk_type_closure`` verbatim -- see that
module's own docstring). If this class's :meth:`PublicSurfaceQuery.
resolve_export_domain` lived in that same leaf module, its own need to call
``export_surface.compute_export_surface()`` would close a real, two-node
import cycle (``policy.public_surface -> export_surface -> policy.
public_surface``) -- one the AI-readiness `import-cycle-growth` gate
catches by *static* analysis of every ``import``/``from ... import``
statement, including one written inside a function body specifically to
dodge a *runtime* circular-import error; a deferred import only defers
the problem past that gate, it does not solve it. This module is the
actual fix AGENTS.md's own "What NOT to do" section prescribes for exactly
this shape: shared logic moved to a leaf module (``policy/public_surface.
py``) both sides can depend on, with the orchestrator that genuinely needs
*both* sides moved to its own separate module that neither leaf imports
back.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..export_surface import ExportSurface, compute_export_surface
from .public_surface import PublicSurface
from .public_surface_closure import resolve_public_surface

if TYPE_CHECKING:
    from ..model.identity import EntityId
    from ..model.snapshot import AbiSnapshot

__all__ = ["PublicSurfaceQuery"]


def _linker_key_is_public(mangled: str, name: str, public_symbols: set[str]) -> bool:
    """Whether a function/variable counts as public per *public_symbols*,
    preferring its own mangled linker identity (unambiguous per overload)
    over its bare demangled name whenever the two genuinely differ.

    When two C++ overloads share one demangled name and only one is
    public, ``policy.public_surface._seed_public_roots`` unions *both* the
    mangled name and the bare name into ``public_symbols`` for the public
    overload alone -- but a plain ``mangled in ... or name in ...`` check
    still matches the *other*, non-public overload via that shared bare
    name, since nothing about the bare-name membership test can tell which
    specific overload it was recorded for. Falling back to the bare name is
    only safe when it *is* the linker identity (``mangled == name``, e.g. C
    code or an unmangled backend) -- there the two checks are equivalent
    and no ambiguity exists.
    """
    if mangled and mangled != name:
        return mangled in public_symbols
    return mangled in public_symbols or name in public_symbols


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
        all, per :class:`~abicheck.policy.public_surface.PublicSurface`'s
        own docstring, "scoping is skipped entirely"), or the *public*
        portion of the surface carries no ``entity_id``-bearing declaration
        at all (a pre-ADR-063-Phase-2 snapshot, schema < 28, or a
        header-AST backend gap wide enough that nothing public resolved
        one). That availability check is deliberately scoped to *public*
        declarations only (CodeRabbit review, PR #979) — a snapshot where
        every public declaration lacks an ``entity_id`` but some unrelated
        *private* declaration happens to carry one is exactly the
        "unavailable" case, not a legitimate empty answer; checking
        ``entity_id`` presence across the whole snapshot would wrongly let
        that private-only id mask the missing public coverage, since the
        loops below only ever add public ids and would then silently
        return an empty (non-``None``) set instead of falling back. Every
        ``*_public_entity_ids`` consumer downstream (``build_
        surface_graph``/``compute_surface_metrics``/``compare()``) treats
        ``None`` as "fall back to the legacy ``Visibility.PUBLIC``-only
        answer" — collapsing either unavailability case to an empty
        ``frozenset`` instead would make every one of them read a real,
        non-empty public surface as confirmed-empty, silently zeroing
        ``--surface-metrics``/``--pattern-verdicts`` output whenever this
        query cannot actually answer. A single declaration missing its own
        ``entity_id`` while siblings have theirs is a different,
        already-accepted degradation — that declaration alone drops out of
        the (non-``None``) set below, unchanged.
        """
        surf = resolve_public_surface(snapshot)
        if not surf.resolvable:
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
        if not ids and (surf.public_symbols or surf.public_types):
            return None
        return frozenset(ids)

    @staticmethod
    def resolve_public_domain(snapshot: AbiSnapshot) -> PublicSurface:
        """The structured replacement for ``compute_public_surface()``'s
        result — ``resolvable``/``has_typed_roots``/``has_provenance``/
        ``ambiguous_type_names``/``exact_type_identities``/both origin
        indices, none of which a bare id set can express. Today this *is*
        :func:`~abicheck.policy.public_surface.resolve_public_surface`'s own
        return value."""
        return resolve_public_surface(snapshot)

    @staticmethod
    def resolve_export_domain(snapshot: AbiSnapshot) -> ExportSurface:
        """The ``contract=exports`` domain's structured result
        (``resolvable``/``exclusion_is_provable`` and the rest) — does
        **not** collapse into :meth:`resolve`'s bare ``frozenset[EntityId]``,
        since ``contract_evaluation.py``/``contract_evidence_collect.py``
        consume exactly the completeness state a membership set has
        nowhere to carry.

        ``export_surface.py``'s own export-table-matching root-seeding
        stays its own, independently proven implementation unchanged; its
        final type-closure step shares
        ``policy.public_surface._walk_type_closure`` with this class's own
        public-domain query and is therefore graph-native too. This is a
        real, top-level import (not deferred): this module is the one
        place that may depend on both ``export_surface.py`` and
        ``policy/public_surface.py`` at once -- see this module's own
        docstring for why neither of *those* two may import this one back.
        """
        return compute_export_surface(snapshot)
