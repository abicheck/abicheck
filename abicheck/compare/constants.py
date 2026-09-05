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

"""The constant detector family, reading through
:class:`~abicheck.model.semantic_ir_index.SemanticIRIndex` (ADR-063 Phase 6B,
cohort 2 -- see ``compare/typedefs.py``'s own docstring for cohort 1 and why
typedefs went first).

**Why constants are the second cohort.** Cohort 1's own reasoning ruled out
records (layout facts the IR does not yet model) and functions (a canonical
signature spelling whose cross-backend agreement is still an open question)
as the next-simplest family, leaving constants: ``extract/
semantic_normalizer.py``'s fourth slice already gave every public constant a
real ``EntityId`` (Phase 2's ``parse_constant_entity_ids()``) plus exactly
one payload fact -- its raw, deliberately-uncanonicalized value text
(``CanonicalEntity.canonical_spelling``, matching ``diff_symbols.
_diff_constants``'s own long-standing raw-string ``!=`` comparison). Like
typedefs, this is a family the IR already covers completely rather than one
whose migration would really be extraction work.

**This module may not read a legacy constant collection.** Not by
convention -- ``scripts/semantic_ir_cutover.py`` enforces it as a real AST
scan (``semantic-ir-cutover`` in the AI-readiness gate), with no allowlist:
this family is freshly migrated, so there is nothing grandfathered to
permit. ``AbiSnapshot.constants``/``constant_entity_ids`` are read exactly
once, inside ``model/semantic_ir_legacy_adapter.py``, which is where a
*projection* of the legacy shape belongs. The comparison-level decisions --
which raw map the pair trusts, and whether a value disagreement is a
fingerprint-comparison artifact rather than a real edit -- stay with the
caller in ``diff_symbols.py`` and arrive here as plain values/an injected
predicate, for the same reason typedefs' surface-filter predicate does: they
are questions about the comparison, not about a constant.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from ..diff_helpers import make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import EntityId, EntityKind
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import (
    legacy_constant_ir,
    producer_entity_id,
    render_display_name,
)

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model import AbiSnapshot

__all__ = ["constant_index_pair", "diff_constants"]


class _ReliabilityPredicate(Protocol):
    """``diff_default_value_reliability.
    constant_value_fingerprint_comparison_unreliable``'s call shape, with
    both snapshots already closed over by the caller. Injected rather than
    imported so this module states no opinion about cross-generation
    fingerprint reliability -- a comparison-level concern, not a per-constant
    one -- and so a test can substitute one without constructing two full
    ``AbiSnapshot``\\ s. Mirrors ``compare.typedefs._SurfacePredicate``'s own
    injection reasoning."""

    def __call__(self, old_value: str, new_value: str) -> bool: ...


def _values(index: SemanticIRIndex) -> dict[str, EntityId]:
    """This index's constant occurrences, keyed by their rendered qualified
    name. Mirrors ``compare.typedefs._aliases``: an identity with no
    faithful flat rendering is skipped -- since ADR-063 Track T3 made
    ``SemanticIR`` the sole comparison-time source for this cohort (see
    ``constant_index_pair``), this is the real mechanism for an
    anonymous/local-to-function-scoped constant, not a defensive floor
    behind a gate that used to fall back first."""
    by_name: dict[str, EntityId] = {}
    for entity_id in index.entities_of_kind(EntityKind.CONSTANT):
        name = render_display_name(entity_id)
        if name is not None:
            by_name.setdefault(name, entity_id)
    return by_name


def _value(index: SemanticIRIndex, entity_id: EntityId) -> str | None:
    """*entity_id*'s value text, or ``None`` when this producer has no
    comparable spelling for it (``Fact.unsupported()`` -- a clang compound-
    initializer fingerprint or Python-bool-derived literal spelling, see
    ``extract/semantic_normalizer.py``'s "Scope of the fourth slice").

    Unlike a typedef's unresolved-chain placeholder (``"?"``, a real string
    both backends agree on), a constant carries no legacy sentinel for this
    case -- the raw fingerprint text a ``Fact.unsupported()`` occurrence
    would need to compare against is not retained on the fact at all.
    Since ADR-063 Track T3 made ``SemanticIR`` the sole comparison-time
    source for this cohort, ``diff_constants`` genuinely reaches this
    ``None`` for a real clang compound-initializer/bool-literal constant --
    it is no longer routed around by falling back to the legacy adapter's
    always-a-raw-string projection first. ``diff_constants`` skips only the
    *value comparison* (``CONSTANT_CHANGED``) when either side's value is
    unsupported this way -- fabricating a value comparison against an
    evidence gap is exactly what this codebase's "weaker evidence narrows
    conclusions" principle (AGENTS.md) forbids, a documented limitation
    this family already accepted (see this module's own docstring, "except
    clang's own compound-initializer value"). A membership change
    (``CONSTANT_ADDED``/``CONSTANT_REMOVED``) is unaffected: whether a
    constant exists at all does not depend on whether its value can be
    rendered, so those findings still fire with a ``None`` old/new value
    text (Codex review, PR #1078).
    """
    spelling = index.fact(entity_id, "canonical_spelling")
    if spelling is not None and spelling.is_present and spelling.value is not None:
        value = spelling.value
        assert isinstance(value, str)
        return value
    return None


def diff_constants(
    old_index: SemanticIRIndex,
    new_index: SemanticIRIndex,
    *,
    is_fingerprint_comparison_unreliable: _ReliabilityPredicate,
) -> list[Change]:
    """Detect constant additions, removals, and value changes, reading only
    through the two indexes.

    An addition or removal fires regardless of whether the constant's own
    value can be rendered (``_value`` returning ``None`` for a
    ``Fact.unsupported()`` occurrence) -- only a value-*comparison*
    (``CONSTANT_CHANGED``) requires both sides' values to actually be
    comparable text (Codex review, PR #1078: this used to skip the
    membership check too for an unsupported old-side value, silently
    dropping a real removal).

    *is_fingerprint_comparison_unreliable* is the comparison-level decision
    the caller already makes (``diff_default_value_reliability.
    constant_value_fingerprint_comparison_unreliable``, closed over both
    snapshots) -- injected for the same reason ``diff_typedefs``'s own
    ``is_non_abi_surface_type`` is.

    Behavior is identical to the pre-cutover ``diff_symbols._diff_constants``,
    including which two spellings a ``CONSTANT_CHANGED`` finding carries
    (``old``/``new`` as ``repr()`` text alongside ``old_value``/``new_value``
    as the raw strings).
    """
    changes: list[Change] = []
    old_values = _values(old_index)
    new_values = _values(new_index)

    for name, old_id in old_values.items():
        new_id = new_values.get(name)
        eid = producer_entity_id(old_id) or (
            producer_entity_id(new_id) if new_id is not None else None
        )
        if new_id is None:
            # A membership change (removed) is real regardless of whether
            # this constant's own value was ever comparable -- checked
            # before the `old_val is None` unsupported-value skip below, so
            # a clang compound-initializer/bool-literal constant (or any
            # future Fact.unsupported() producer) still reports its
            # removal, just with no recoverable old_value text (Codex
            # review: this used to `continue` here before ever reaching the
            # membership check, silently dropping the removal).
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_REMOVED,
                    symbol=name,
                    name=name,
                    old_value=_value(old_index, old_id),
                    entity_id=eid,
                )
            )
            continue
        old_val = _value(old_index, old_id)
        new_val = _value(new_index, new_id)
        if old_val is None or new_val is None or new_val == old_val:
            continue
        if is_fingerprint_comparison_unreliable(old_val, new_val):
            continue
        changes.append(
            make_change(
                ChangeKind.CONSTANT_CHANGED,
                symbol=name,
                name=name,
                old=repr(old_val),
                new=repr(new_val),
                old_value=old_val,
                new_value=new_val,
                entity_id=eid,
            )
        )

    for name, new_id in new_values.items():
        if name in old_values:
            continue
        # Mirrors the removal side above: an addition is real regardless of
        # whether the new value is itself comparable.
        changes.append(
            make_change(
                ChangeKind.CONSTANT_ADDED,
                symbol=name,
                name=name,
                new_value=_value(new_index, new_id),
                entity_id=producer_entity_id(new_id),
            )
        )
    return changes


def constant_index_pair(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_constants: dict[str, str],
    new_constants: dict[str, str],
) -> tuple[SemanticIRIndex, SemanticIRIndex]:
    """The constant cohort's index pair: ``SemanticIR`` is the sole source
    whenever both sides carry one (ADR-063 Track T3, "typedef/constant
    authority cutover").

    Mirrors ``compare.typedefs.typedef_index_pair`` exactly, substituting
    the constant collections, ``EntityKind.CONSTANT``, and
    :func:`~abicheck.model.semantic_ir_legacy_adapter.assert_constant_ir_consistent`
    as the construction-time identity check -- see that function's own
    docstring for the full before/after reasoning. Nothing about the shape
    differs between the two families -- only which legacy collections and
    which entity kind are being projected.
    """
    if old.semantic_ir is not None and new.semantic_ir is not None:
        return SemanticIRIndex(old.semantic_ir), SemanticIRIndex(new.semantic_ir)
    return (
        SemanticIRIndex(legacy_constant_ir(old, old_constants)),
        SemanticIRIndex(legacy_constant_ir(new, new_constants)),
    )
