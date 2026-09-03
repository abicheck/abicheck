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

"""The typedef detector family, reading through
:class:`~abicheck.model.semantic_ir_index.SemanticIrIndex` (ADR-063 Phase 6's
first real checker cutover).

**Why typedefs are the first cohort.** Phase 6's own non-goal is "don't keep
widening producer coverage until one full vertical slice is proven", so the
first cohort has to be one ``SemanticIR`` already covers *completely* rather
than one that would need new extraction work to migrate. Typedefs are the
only family where the IR carries the whole of what the detector needs and
nothing it does not: identity (``EntityId``, resolved by both header-AST
backends since Phase 2's twelfth slice) plus exactly one payload fact
(``CanonicalEntity.canonical_spelling`` -- for a typedef, its resolved
underlying type, which is precisely the value this detector compares).
Records carry layout facts the IR does not yet model; functions need a
canonical signature spelling whose cross-backend agreement is its own open
question; constants' payload is a value literal, not a type spelling. Each
of those would have made the first slice about extraction coverage instead
of about proving the read path.

**This module may not read a legacy typedef collection.** Not by convention
-- ``scripts/semantic_ir_cutover.py`` enforces it as a real AST scan
(``semantic-ir-cutover`` in the AI-readiness gate), with no allowlist: this
family is freshly migrated, so there is nothing grandfathered to permit.
``AbiSnapshot.typedefs``/``typedefs_qualified``/``typedef_entity_ids`` are
read exactly once, inside ``model/semantic_ir_legacy_adapter.py``, which is
where a *projection* of the legacy shape belongs. The pair-wise
"which alias map does this comparison trust" decision
(``diff_helpers.typedef_diff_maps``) and the two snapshot-level suppression
flags stay with the caller in ``diff_types.py`` and arrive here as plain
values, for the same reason: they are questions about the comparison, not
about a typedef.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from ..checker_policy import ChangeKind
from ..diff_helpers import make_change
from ..model.identity import EntityId, EntityKind
from ..model.semantic_ir_index import SemanticIRIndex
from ..model.semantic_ir_legacy_adapter import producer_entity_id, render_display_name

if TYPE_CHECKING:
    from ..checker_types import Change

__all__ = ["diff_typedefs", "is_version_stamped_typedef"]


class _SurfacePredicate(Protocol):
    """``model.is_non_abi_surface_type``'s call shape, as this module needs
    it. Injected rather than imported so a detector states no opinion about
    surface policy (ADR-061 assigns that to ``policy``/``model``), and so a
    test can substitute one without patching a module global."""

    def __call__(self, name: str, *, exclude_stdlib_namespaces: bool) -> bool: ...


#: Both header-AST backends spell an unfollowable typedef chain with this
#: placeholder, and ``extract/semantic_normalizer.py`` deliberately records
#: it as ``Fact.failed(...)`` rather than a present spelling (a present
#: placeholder would permanently block a hybrid merge's backfill). This
#: detector maps a non-present spelling back onto the same placeholder so
#: its comparison is bit-for-bit what the legacy raw-string path did -- an
#: unresolved-vs-unresolved pair is still "unchanged", and an
#: unresolved-vs-resolved pair is still a base-type change.
_UNRESOLVED_TYPE_SENTINEL = "?"

_VERSION_STAMPED_TYPEDEF_RE = re.compile(r"^(.*?)_version_\d+_\d+_\d+$", re.IGNORECASE)
"""Pattern for version-stamped compile-time sentinel typedefs.

Some libraries (e.g. libpng) define typedefs whose names encode the library
version, e.g. ``typedef char* png_libpng_version_1_6_46``.  The name changes
every release by design -- this is NOT a binary ABI break because the typedef
is never exported as an ELF symbol; it exists solely to produce a
compile-time error if headers from different versions are mixed.

When such a typedef disappears (``typedef_removed``), abicheck would
otherwise report BREAKING.  This guard downgrades the change to
TYPEDEF_VERSION_SENTINEL (COMPATIBLE) instead.

Moved here verbatim from ``diff_types.py`` with this cohort -- the same
pattern, not a re-derived one.
"""


def is_version_stamped_typedef(name: str) -> bool:
    """True if *name* looks like a version-stamped sentinel typedef.

    Moved here from ``diff_types.py`` with this cohort: it is typedef-family
    logic with no other caller, and leaving it behind would have meant the
    migrated detector importing back into the module it was split out of.
    """
    return bool(_VERSION_STAMPED_TYPEDEF_RE.match(name))


def _has_version_family_successor(name: str, new_aliases: frozenset[str]) -> bool:
    """True if *new_aliases* contains another version-stamped typedef with the
    same family prefix (e.g. ``png_libpng_version_``).

    Distinguishes a sentinel rotation (old version removed, new version
    added) from a genuine removal whose name merely matches the pattern.
    Takes the alias *key set* rather than the alias map, since only the keys
    were ever consulted -- the narrower input is what lets this run off the
    index's display names with no legacy dict in scope.
    """
    m = _VERSION_STAMPED_TYPEDEF_RE.match(name)
    if not m:
        return False
    prefix = m.group(1).lower()
    # Require a non-empty family prefix so an unrelated sentinel whose own
    # name starts with `_version_` (e.g. `_version_1_0_0`) doesn't match.
    if not prefix:
        return False
    prefix = prefix + "_version_"
    return any(k.lower().startswith(prefix) for k in new_aliases)


def _underlying(index: SemanticIRIndex, entity_id: EntityId) -> str:
    """*entity_id*'s resolved underlying type, or the unresolved placeholder.

    See :data:`_UNRESOLVED_TYPE_SENTINEL`: a ``Fact`` that is not present
    means the producer could not follow the chain, which the legacy path
    represented as the literal ``"?"`` string. Collapsing the two back
    together here is what keeps the cutover behavior-preserving; a reader
    wanting the distinction still has the ``Fact``'s own ``.status`` via
    ``SemanticIRIndex.fact``.
    """
    spelling = index.fact(entity_id, "canonical_spelling")
    if spelling is not None and spelling.is_present and spelling.value is not None:
        value = spelling.value
        assert isinstance(value, str)
        return value
    return _UNRESOLVED_TYPE_SENTINEL


def _aliases(index: SemanticIRIndex) -> dict[str, EntityId]:
    """This index's typedef occurrences, keyed by their rendered alias.

    An identity with no faithful flat rendering is skipped -- it has no
    alias a ``Change.symbol`` could name. ``typedef_index_pair``'s own
    fidelity gate is what makes that skip unreachable on the ``SemanticIR``
    path (it falls back to the adapter rather than let a detector iterate a
    smaller set), so this is a defensive floor, not the mechanism.
    """
    by_alias: dict[str, EntityId] = {}
    for entity_id in index.entities_of_kind(EntityKind.TYPEDEF):
        alias = render_display_name(entity_id)
        if alias is not None:
            by_alias.setdefault(alias, entity_id)
    return by_alias


def diff_typedefs(
    old_index: SemanticIRIndex,
    new_index: SemanticIRIndex,
    *,
    exclude_stdlib_namespaces: bool,
    suppress_removed: bool,
    is_non_abi_surface_type: _SurfacePredicate,
) -> list[Change]:
    """Detect typedef removals, base-type changes, and version-sentinel
    rotations, reading only through the two indexes.

    *exclude_stdlib_namespaces* / *suppress_removed* are the comparison-level
    decisions the caller already made (``model.stdlib_namespaces_excluded``
    and ``diff_types._removals_are_unconfirmed``); *is_non_abi_surface_type*
    is injected rather than imported so this module states no opinion about
    surface policy, which ADR-061 assigns to ``policy``/``model``, not to a
    detector.

    Behavior is identical to the pre-cutover ``diff_types._diff_typedefs``,
    including the two spellings it emits: ``symbol``/``name`` stay *bare*
    (``diff_filtering._enrich_affected_symbols`` joins on the bare form),
    while the qualified spelling is appended to the description so dedup
    cannot collapse two same-leaf-named aliases in different scopes.
    """
    changes: list[Change] = []
    old_aliases = _aliases(old_index)
    new_aliases = _aliases(new_index)
    new_alias_keys = frozenset(new_aliases)

    for alias, old_id in old_aliases.items():
        # Full alias: correct for both legacy-DWARF's and the qualified
        # map's keys.
        if is_non_abi_surface_type(
            alias, exclude_stdlib_namespaces=exclude_stdlib_namespaces
        ):
            continue
        bare_alias = alias.rsplit("::", 1)[-1]
        qualified_suffix = f" ({alias})" if alias != bare_alias else ""
        new_id = new_aliases.get(alias)
        if new_id is None and suppress_removed:
            # RD2-5: don't manufacture a phantom removal when the new side
            # is stripped of type evidence entirely.
            continue
        old_type = _underlying(old_index, old_id)
        # Old-side-preferred with a new-side fallback, the convention every
        # other `entity_id` producer in this codebase uses.
        # `producer_entity_id`, never the raw id: the legacy adapter
        # synthesizes one for a declaration whose producer resolved none,
        # and stamping that onto a `Change` would present this index's own
        # bookkeeping as backend evidence -- and add a spurious `entity:`
        # alias to `finding_identity.resolve_change_identity`, which real,
        # stored suppression rules match against.
        eid = producer_entity_id(old_id) or (
            producer_entity_id(new_id) if new_id is not None else None
        )
        if new_id is None:
            if is_version_stamped_typedef(alias) and _has_version_family_successor(
                alias, new_alias_keys
            ):
                # A version-stamped typedef (e.g. png_libpng_version_1_6_46)
                # is a compile-time sentinel that changes every release by
                # design and is never exported as an ELF symbol -- not a
                # binary ABI break. Requiring a same-family successor avoids
                # hiding a genuine removal for a name that merely matches
                # the pattern.
                changes.append(
                    make_change(
                        ChangeKind.TYPEDEF_VERSION_SENTINEL,
                        symbol=bare_alias,
                        name=bare_alias,
                        old_value=old_type,
                        entity_id=eid,
                    )
                )
                continue
            changes.append(
                make_change(
                    ChangeKind.TYPEDEF_REMOVED,
                    symbol=bare_alias,
                    name=bare_alias,
                    old_value=old_type,
                    entity_id=eid,
                    description=f"Typedef removed: {bare_alias}{qualified_suffix}",
                )
            )
            continue
        new_type = _underlying(new_index, new_id)
        if new_type != old_type:
            changes.append(
                make_change(
                    ChangeKind.TYPEDEF_BASE_CHANGED,
                    symbol=bare_alias,
                    name=bare_alias,
                    old_value=old_type,
                    new_value=new_type,
                    entity_id=eid,
                    description=(
                        f"Typedef base type changed: {bare_alias}{qualified_suffix}"
                    ),
                )
            )
    return changes
