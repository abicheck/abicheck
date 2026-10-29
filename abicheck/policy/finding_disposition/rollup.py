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

"""Disposition of aggregate roll-up findings (``public_surface_shrank``).

A roll-up is computed from declaration counts, not from one symbol, so a
suppression or acknowledgment naming a removal never matches it directly. The
roll-up is still the same observation as the removals it counts: when the user
has accepted every removal it summarises, leaving the roll-up visible would
report a removal the user already disposed of (exit 0, but a non-empty
report and a verdict that reads as "something changed").

The membership relation is owned by
:data:`~abicheck.model.change_catalog.kind_variants.ROLLUP_MEMBER_KINDS`. This
module applies it, per symbol:

* the roll-up is **hidden** only when it covers at least one removal, every
  covered removal was suppressed, and no covered removal stayed visible (kept
  or verdict-redundant). A visible sibling keeps the roll-up visible, unchanged.
* a hidden roll-up is **recorded** in the disposition ledger under the rule
  that hid its first covered removal, with application point
  ``surface_rollup``, so the audit shows what disposed of it and why (ADR-067
  record-before-disposing). It is never silently dropped.

Acknowledgment and reclassification do not hide a finding (they keep it
visible, :mod:`~abicheck.policy.disposition_acknowledgment`), so they do not
hide the roll-up either; the roll-up stays a COMPATIBLE informational count.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...model.change_catalog.kind_variants import rollup_member_kinds
from ..disposition_ledger import Disposition
from .suppression import dispose_by_suppression

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ...model import AbiSnapshot
    from ...model.change import Change
    from ...model.identity import EntityId
    from ..disposition_ledger import DispositionLedger
    from ..public_surface_query import ComparisonPublicSurfaces
    from ..suppression.rules import SuppressionList

#: The application point the ledger records a derived suppression under.
SURFACE_ROLLUP_APPLICATION_POINT = "surface_rollup"


def dispose_surface_metric_rollups(
    old: AbiSnapshot,
    new: AbiSnapshot,
    suppression: SuppressionList | None,
    *,
    kept: list[Change],
    verdict_redundant: list[Change],
    suppressed: list[Change],
    ledger: DispositionLedger | None,
    old_public_entity_ids: frozenset[EntityId] | None = None,
    new_public_entity_ids: frozenset[EntityId] | None = None,
    public_surfaces: ComparisonPublicSurfaces | None = None,
) -> list[Change]:
    """Compute the run's surface roll-ups and dispose of them; return the visible ones.

    Resolves each side's public type closure here (policy's job, see
    :func:`~abicheck.diff_surface_metrics.diff_surface_metrics`), then applies
    :func:`dispose_surface_rollups`.
    """
    # Function-local: the metric module reads the surface graph, which the
    # disposition package must not import at load time.
    from ...diff_surface_metrics import diff_surface_metrics
    from ..public_surface_query import PublicSurfaceQuery

    rollups = diff_surface_metrics(
        old,
        new,
        old_public_entity_ids=old_public_entity_ids,
        new_public_entity_ids=new_public_entity_ids,
        old_public_type_names=None
        if old_public_entity_ids is not None
        else PublicSurfaceQuery.public_type_names(old, public_surfaces),
        new_public_type_names=None
        if new_public_entity_ids is not None
        else PublicSurfaceQuery.public_type_names(new, public_surfaces),
    )
    return dispose_surface_rollups(
        rollups,
        suppression,
        kept=kept,
        verdict_redundant=verdict_redundant,
        suppressed=suppressed,
        ledger=ledger,
    )


def dispose_surface_rollups(
    rollups: list[Change],
    suppression: SuppressionList | None,
    *,
    kept: list[Change],
    verdict_redundant: list[Change],
    suppressed: list[Change],
    ledger: DispositionLedger | None,
) -> list[Change]:
    """Dispose of *rollups* against their covered removals; return the visible ones.

    *kept*, *verdict_redundant* and *suppressed* are the run's removal
    population after the pipeline's own disposition. A roll-up hidden here is
    appended to *suppressed*, stamped with ``suppression_rule`` from the rule
    that hid its first covered removal, and recorded in *ledger*. A roll-up
    that stays visible then goes through the roll-up's own suppression rules
    (``suppression``), so a rule that names ``public_surface_shrank`` directly
    still applies.
    """
    kept_rollups: list[Change] = []
    for rollup in rollups:
        members = rollup_member_kinds(rollup.kind.value)
        blocking = [c for c in (*kept, *verdict_redundant) if c.kind.value in members]
        covered = [c for c in suppressed if c.kind.value in members]
        if members and covered and not blocking:
            _record_derived_suppression(rollup, covered, ledger)
            suppressed.append(rollup)
            continue
        kept_rollups.append(rollup)
    outcome = dispose_by_suppression(
        kept_rollups,
        suppression,
        ledger=ledger,
        application_point="surface_rollup_rule",
    )
    suppressed.extend(outcome.suppressed)
    return outcome.visible


def _record_derived_suppression(
    rollup: Change, covered: list[Change], ledger: DispositionLedger | None
) -> None:
    """Stamp *rollup* with the rule of its first covered removal and audit it."""
    first = covered[0]
    rollup.suppression_rule = first.suppression_rule
    symbols = ", ".join(sorted({c.symbol for c in covered}))
    rollup.description = (
        f"{rollup.description} (derived: {len(covered)} suppressed "
        f"removal(s) of {symbols})"
    )
    if ledger is None:
        return
    provenance = ledger.rule_for(first)
    ledger.record(
        rollup,
        Disposition.SUPPRESSED,
        application_point=SURFACE_ROLLUP_APPLICATION_POINT,
        rule=provenance,
    )


__all__ = [
    "SURFACE_ROLLUP_APPLICATION_POINT",
    "dispose_surface_metric_rollups",
    "dispose_surface_rollups",
]
