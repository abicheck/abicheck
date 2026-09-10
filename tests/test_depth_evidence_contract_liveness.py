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

"""``depth_evidence_contract_satisfied``'s liveness rule, exhaustively.

The primitive both halves of ADR-064's depth axis call. Its whole point is
the distinction the hard floor it replaced did not make: a side that falls
short of the pinned rung is only a contract failure when *this run actually
extracted it*, since a pre-serialized snapshot was never asked to reach any
depth (`policy/depth_evidence_contract.py`'s "Live extraction only" note).

Lives in its own module rather than in ``test_service_unit.py``, where the
test it replaced sat: it exercises a `policy` primitive directly, not the
service layer, and that file is at its `architecture/debt.yaml` baseline.
"""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("old_live", (True, False))
@pytest.mark.parametrize("new_live", (True, False))
@pytest.mark.parametrize("failing_side", ("old", "new"))
def test_only_a_live_short_side_trips_the_contract(
    old_live: bool, new_live: bool, failing_side: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Which side is short, crossed with which sides this run extracted.

    Replaces a test that asserted the *message* named the failing side
    ("new side"), which only meant something while the shortfall raised.
    The contract is now a boolean on the result, so the invariant worth
    stating is the one the old hard fail had wrong: a short side is only
    a contract failure when this run actually extracted it. Exercised on
    the shared primitive both halves of the axis call
    (`depth_evidence_contract_satisfied`), across all eight
    combinations, rather than through one hand-picked case.
    """
    from abicheck.model import AbiSnapshot
    from abicheck.policy.depth_evidence_contract import (
        depth_evidence_contract_satisfied,
    )

    short = AbiSnapshot(library="libtest", version="short")
    deep = AbiSnapshot(library="libtest", version="deep")
    old_snap = short if failing_side == "old" else deep
    new_snap = short if failing_side == "new" else deep

    def _by_version(build_source, snap):
        return "binary" if snap.version == "short" else "source"

    monkeypatch.setattr(
        "abicheck.policy.depth_evidence_contract.gated_source_label", _by_version
    )
    satisfied = depth_evidence_contract_satisfied(
        "source", old_snap, new_snap, old_is_live=old_live, new_is_live=new_live
    )

    short_side_is_live = old_live if failing_side == "old" else new_live
    assert satisfied is not short_side_is_live
