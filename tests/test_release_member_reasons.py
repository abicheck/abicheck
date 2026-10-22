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

"""Per-member acquisition reasons shared by the release scope and plan.

Exhaustive over the small domain of which side reports a member as
unproduced/failed, and which side's inventory is proven complete, against a
hand-written oracle.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from abicheck.model.release_selection import ReleaseSelection
from abicheck.model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    SideInventory,
)
from abicheck.workflows.release_member_reasons import (
    _proven_absence_reason,
    _unproduced_or_failed,
)
from abicheck.workflows.release_plan import build_declared_selection_record
from abicheck.workflows.release_scope import ReleaseInventoryEvidence

_SIDES = list(itertools.product([False, True], repeat=4))


@pytest.mark.parametrize(("ou", "nu", "of", "nf"), _SIDES)
def test_unproduced_wins_over_failed_and_names_each_reporting_side(
    ou: bool, nu: bool, of: bool, nf: bool
) -> None:
    def side(on: bool, why: str) -> dict[str, str]:
        return {"m": why} if on else {}

    got = _unproduced_or_failed(
        "m",
        side(ou, "ou"),
        side(nu, "nu"),
        side(of, "of"),
        side(nf, "nf"),
    )
    if ou or nu:
        parts = [p for p, on in (("OLD: ou", ou), ("NEW: nu", nu)) if on]
        assert got == (AcquisitionState.EXPECTED_NOT_PRODUCED, "; ".join(parts))
    elif of or nf:
        parts = [p for p, on in (("OLD: of", of), ("NEW: nf", nf)) if on]
        assert got == (AcquisitionState.FAILED, "; ".join(parts))
    else:
        assert got is None


def _inv(proven: bool, where: str) -> SideInventory:
    c = InventoryCompleteness.PROVEN if proven else InventoryCompleteness.UNPROVEN
    return SideInventory(completeness=c, provenance=where)


@pytest.mark.parametrize(
    ("old_present", "old_proven", "new_proven"),
    list(itertools.product([False, True], repeat=3)),
)
def test_proven_absence_rewords_only_when_the_lacking_side_is_proven(
    old_present: bool, old_proven: bool, new_proven: bool
) -> None:
    old, new = _inv(old_proven, "old-src"), _inv(new_proven, "new-src")
    got = _proven_absence_reason("orig", old_present, old, new)
    lacking_proven = new_proven if old_present else old_proven
    if not lacking_proven:
        assert got == "orig"
    else:
        side, where = ("NEW", "new-src") if old_present else ("OLD", "old-src")
        assert got == (
            f"no counterpart on {side}, whose inventory is proven complete ({where})"
        )


@pytest.mark.parametrize("new_proven", [False, True])
@pytest.mark.parametrize("on_old", [False, True])
def test_declared_one_sided_member_is_not_supplied(
    on_old: bool, new_proven: bool
) -> None:
    sel = ReleaseSelection.from_lists(required=["libfoo.so"])
    side_map = {"libfoo.so": Path("/x/libfoo.so")}
    old_map, new_map = (side_map, {}) if on_old else ({}, side_map)
    evidence = ReleaseInventoryEvidence(
        old=_inv(new_proven, "old-src"), new=_inv(new_proven, "new-src")
    )
    record = build_declared_selection_record(
        old_map, new_map, [], [], evidence, sel, old_failed=None, new_failed=None
    )
    (member,) = record.members
    assert member.state is AcquisitionState.NOT_SUPPLIED
    side = "NEW" if on_old else "OLD"
    if new_proven:
        assert f"no counterpart on {side}, whose inventory is proven" in member.reason
    else:
        assert f"no counterpart on {side};" in member.reason
