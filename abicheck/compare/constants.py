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
from ..model.semantic_ir import SemanticIR
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
    faithful flat rendering is skipped, which ``constant_index_pair``'s own
    fidelity gate is what makes unreachable on the ``SemanticIR`` path."""
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
    would need to compare against is not retained on the fact at all. This
    is never reachable on the ``SemanticIR`` path in practice:
    ``constant_index_pair``'s fidelity gate already falls back to the
    adapter for both sides whenever any entity's projected value would
    disagree with the legacy raw text this way (a ``None`` projection can
    never equal a real legacy string) -- kept as a defensive floor, not the
    mechanism, mirroring ``compare.typedefs._underlying``'s identical
    defensive floor for the unresolved-typedef-chain case.
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
        old_val = _value(old_index, old_id)
        if old_val is None:
            continue
        new_id = new_values.get(name)
        eid = producer_entity_id(old_id) or (
            producer_entity_id(new_id) if new_id is not None else None
        )
        if new_id is None:
            changes.append(
                make_change(
                    ChangeKind.CONSTANT_REMOVED,
                    symbol=name,
                    name=name,
                    old_value=old_val,
                    entity_id=eid,
                )
            )
            continue
        new_val = _value(new_index, new_id)
        if new_val is None or new_val == old_val:
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
        new_val = _value(new_index, new_id)
        if new_val is None:
            continue
        changes.append(
            make_change(
                ChangeKind.CONSTANT_ADDED,
                symbol=name,
                name=name,
                new_value=new_val,
                entity_id=producer_entity_id(new_id),
            )
        )
    return changes


def _constant_names_and_values(
    index: SemanticIRIndex,
) -> tuple[tuple[str, ...], tuple[str | None, ...]]:
    """The name keys *index* projects for constants, **in order**, paired
    with each one's value-text spelling. Mirrors ``compare.typedefs.
    _typedef_display_names_and_underlying`` -- see that function's own
    docstring for why order (not a set) and why the paired value (not the
    name alone) both matter to the gate below."""
    names: list[str] = []
    values: list[str | None] = []
    for entity_id, entity in index.entities_of_kind(EntityKind.CONSTANT).items():
        rendered = render_display_name(entity_id)
        if rendered is None:
            continue
        names.append(rendered)
        spelling = entity.canonical_spelling
        values.append(spelling.value if spelling.is_present else None)
    return tuple(names), tuple(values)


def _constant_identities_by_name(index: SemanticIRIndex) -> dict[str, EntityId]:
    """Name -> resolved ``EntityId`` for every constant entity *index*
    projects a faithful display name for. Mirrors ``compare.typedefs.
    _typedef_identities_by_alias``."""
    out: dict[str, EntityId] = {}
    for entity_id in index.entities_of_kind(EntityKind.CONSTANT):
        rendered = render_display_name(entity_id)
        if rendered is not None:
            out[rendered] = entity_id
    return out


def constant_index_pair(
    old: AbiSnapshot,
    new: AbiSnapshot,
    *,
    old_constants: dict[str, str],
    new_constants: dict[str, str],
) -> tuple[SemanticIRIndex, SemanticIRIndex]:
    """The constant cohort's index pair: ``SemanticIR``-backed when -- and
    only when -- that is provably equivalent to the legacy projection.

    Mirrors ``compare.typedefs.typedef_index_pair`` exactly, substituting
    the constant collections and ``EntityKind.CONSTANT``; see that
    function's own docstring for the full reasoning behind the strict,
    symmetric, both-or-neither gate (name-key-set equality, paired value
    equality, and paired identity equality, all checked on both sides).
    Nothing about the gate's shape differs between the two families -- only
    which legacy collections and which entity kind are being projected.
    """
    old_index = SemanticIRIndex(old.semantic_ir or SemanticIR())
    new_index = SemanticIRIndex(new.semantic_ir or SemanticIR())
    old_names, old_ir_values = _constant_names_and_values(old_index)
    new_names, new_ir_values = _constant_names_and_values(new_index)
    legacy_old_index = SemanticIRIndex(legacy_constant_ir(old, old_constants))
    legacy_new_index = SemanticIRIndex(legacy_constant_ir(new, new_constants))
    if (
        old_names == tuple(old_constants)
        and old_ir_values == tuple(old_constants.values())
        and new_names == tuple(new_constants)
        and new_ir_values == tuple(new_constants.values())
        and _constant_identities_by_name(old_index)
        == _constant_identities_by_name(legacy_old_index)
        and _constant_identities_by_name(new_index)
        == _constant_identities_by_name(legacy_new_index)
    ):
        return old_index, new_index
    return legacy_old_index, legacy_new_index
