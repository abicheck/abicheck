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

"""Per-member type attribution for a release fan-out.

Computes each member's export surfaces once and asks
:func:`abicheck.policy.member_type_attribution.attribute_type` about every
type its findings name; after the fan-out,
:func:`dispose_unreached_type_findings` takes a type a member provably does
not reach -- and a sibling does -- off that member's verdict. Lives in ``workflows`` because the release fan-out
(a frontend) may not import ``policy`` directly (ADR-061).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING

from ..policy.member_type_attribution import (
    attribute_type,
    unreached_types_owned_elsewhere,
)

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..model import AbiSnapshot
    from ..model.change import Change

__all__ = [
    "UNREACHED_TYPE_APPLICATION_POINT",
    "dispose_unreached_type_findings",
    "member_type_attribution",
    "unreached_types_owned_elsewhere",
]


def member_type_attribution(
    symbols: Iterable[str],
    old_snapshot: AbiSnapshot | None,
    new_snapshot: AbiSnapshot | None,
) -> Mapping[str, str]:
    """``{type name: attribution}`` for each of *symbols* naming a type.

    The export surface is computed only when at least one symbol names a
    type either snapshot carries, so a member with no type findings pays
    nothing.
    """
    snapshots = [s for s in (old_snapshot, new_snapshot) if s is not None]
    known: set[str] = set()
    for snap in snapshots:
        known.update(t.name for t in snap.declarations.types)
        known.update(e.name for e in snap.declarations.enums)
        known.update(snap.declarations.typedefs)
    wanted = sorted({sym for sym in symbols if sym in known})
    if not wanted:
        return {}
    from ..export_surface import compute_export_surface

    surfaces = [compute_export_surface(snap) for snap in snapshots]
    out: dict[str, str] = {}
    for name in wanted:
        verdict = attribute_type(name, surfaces)
        if verdict is not None:
            out[name] = verdict
    return out


#: The ledger application point of :func:`dispose_unreached_type_findings`.
UNREACHED_TYPE_APPLICATION_POINT = "release_member_type_attribution"


def _names_type(symbol: str, name: str) -> bool:
    return symbol == name or symbol.startswith(f"{name}::")


def dispose_unreached_type_findings(
    result: DiffResult, owners_by_type: Mapping[str, tuple[str, ...]]
) -> list[Change]:
    """Move *result*'s findings on each type in *owners_by_type* off its
    verdict, recording why; returns the moved findings.

    "Record before disposing" (ADR-067): each finding lands in
    ``out_of_surface_changes`` with a ``surface_exclusion_reason`` naming
    the members that do reach the type, and its ledger record becomes
    ``out_of_contract`` under a named rule. The verdict is re-derived from
    what is left and may only go down: the population ``checker.compare``
    scored is not fully recoverable from a ``DiffResult`` (see
    ``contract_scoped_promotion.recompute_verdict_after_promotion``), so the
    recomputation over-approximates it and is capped at the old verdict.
    """
    if not owners_by_type:
        return []

    def owners_of(change: Change) -> tuple[str, ...] | None:
        symbol = change.symbol or ""
        for name, owners in owners_by_type.items():
            if _names_type(symbol, name):
                return owners
        return None

    moved: list[Change] = []
    for attr in ("changes", "redundant_changes"):
        kept: list[Change] = []
        for change in getattr(result, attr):
            owners = owners_of(change)
            if owners is None:
                kept.append(change)
                continue
            change.surface_exclusion_reason = (
                "type not reached by this library's exports; reached by "
                + ", ".join(owners)
            )
            moved.append(change)
        if attr == "redundant_changes":
            result.redundant_count = max(
                0, result.redundant_count - (len(result.redundant_changes) - len(kept))
            )
        setattr(result, attr, kept)
    if not moved:
        return []
    result.out_of_surface_changes = [*result.out_of_surface_changes, *moved]
    result.out_of_surface_count += len(moved)
    _relabel(result, moved)
    _lower_verdict(result)
    return moved


def _relabel(result: DiffResult, moved: list[Change]) -> None:
    from ..policy.disposition_ledger import (
        Disposition,
        DispositionLedger,
        RuleProvenance,
    )

    ledger = getattr(result, "disposition_ledger", None)
    if isinstance(ledger, DispositionLedger):
        result.disposition_ledger = ledger.with_suppressed(
            moved,
            application_point=UNREACHED_TYPE_APPLICATION_POINT,
            rule=RuleProvenance(
                rule_id="member_type_unreached",
                reason="type proven unreachable from this member's exports "
                "and reached by a sibling member",
            ),
            disposition=Disposition.OUT_OF_CONTRACT,
        )


def _lower_verdict(result: DiffResult) -> None:
    from ..model.contract_finding_relevance import is_evaluated
    from ..policy.classification import compute_verdict
    from ..policy.release_outcome import compatibility_rank

    scored = [
        c for c in (*result.changes, *result.redundant_changes) if is_evaluated(c)
    ]
    pf = result.policy_file
    recomputed = (
        pf.compute_verdict(scored)
        if pf is not None
        else compute_verdict(scored, policy=result.policy or "strict_abi")
    )
    if compatibility_rank(recomputed) < compatibility_rank(result.verdict):
        result.verdict = recomputed
