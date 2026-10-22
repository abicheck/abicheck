# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
"""Per-member acquisition reasons shared by the release scope and plan."""

from __future__ import annotations

from collections.abc import Mapping

from ..model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    SideInventory,
)


def _unproduced_or_failed(
    key: str,
    old_unproduced: Mapping[str, str],
    new_unproduced: Mapping[str, str],
    old_failed: Mapping[str, str],
    new_failed: Mapping[str, str],
) -> tuple[AcquisitionState, str] | None:
    """EXPECTED_NOT_PRODUCED, else FAILED, for *key* with each side's reason;
    ``None`` when neither side reported it."""
    for state, old, new in (
        (AcquisitionState.EXPECTED_NOT_PRODUCED, old_unproduced, new_unproduced),
        (AcquisitionState.FAILED, old_failed, new_failed),
    ):
        if key in old or key in new:
            reasons = (("OLD", old.get(key)), ("NEW", new.get(key)))
            return state, "; ".join(f"{s}: {w}" for s, w in reasons if w is not None)
    return None


def _proven_absence_reason(
    reason: str, old_present: bool, old: SideInventory, new: SideInventory
) -> str:
    """*reason*, replaced when the side lacking a counterpart is proven complete."""
    lacking = new if old_present else old
    if lacking.completeness is not InventoryCompleteness.PROVEN:
        return reason
    side = "NEW" if old_present else "OLD"
    return (
        f"no counterpart on {side}, whose inventory is proven complete "
        f"({lacking.provenance})"
    )
