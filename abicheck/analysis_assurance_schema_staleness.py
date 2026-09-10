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

"""Whether either side of a comparison carries a ``*_facts_reliable`` flag
``model.snapshot_reliability.degraded_reliability_facts`` marks stale.

Split out of ``analysis_assurance.py`` (which sits at this repo's
``architecture/debt.yaml`` no-growth baseline) rather than added there --
mirrors ``analysis_assurance_layout.py``'s own split for the identical
reason, and this module depends only on ``model.AbiSnapshot`` plus
``model.snapshot_reliability``, a real leaf.

**The gap this closes:** loading a snapshot whose own ``schema_version``
predates this abicheck's, or one re-saved since without ever being
regenerated, already produced a load-time ``UserWarning`` naming the
degraded fact (``serialization.decode_snapshot``) -- but that warning is
stderr-only, invisible to any programmatic consumer of the JSON report, and
``analysis_assurance``'s own completeness rollup had no signal for it at
all: a run with one or more degraded facts still read
``run_outcome.assurance.status == "complete"``. This module is that
missing signal, computed from the exact same table the load-time warning
uses (via ``degraded_reliability_facts``) so the two can never
independently drift on what counts as "degraded".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .model.snapshot_reliability import degraded_reliability_facts

if TYPE_CHECKING:
    from .model import AbiSnapshot

__all__ = ["schema_staleness_status"]


def schema_staleness_status(
    old: AbiSnapshot, new: AbiSnapshot
) -> tuple[str, list[str]]:
    """``"clean"``/``"degraded"`` plus human-readable notes -- the
    ``analysis_assurance.AnalysisAssurance.schema_staleness_status`` value
    for this *old*/*new* pair.

    Same shape as ``analysis_assurance.py``'s other context-status helpers
    (``_l0_context_status``/``_header_context_status``/etc.), but with no
    ``"asymmetric"`` state of its own: unlike header/DWARF/L3 evidence (each
    gated on BOTH sides carrying the same channel), a *single* side's stale
    fact already means the affected detector(s) declined to trust it for
    THIS comparison, whether or not the other side is current.
    """
    old_degraded = degraded_reliability_facts(old)
    new_degraded = degraded_reliability_facts(new)
    if not old_degraded and not new_degraded:
        return "clean", []
    notes: list[str] = []
    if old_degraded:
        notes.append(
            "old snapshot carries schema-vintage-degraded facts (regenerate "
            f"with the current abicheck to restore full detection): "
            f"{', '.join(old_degraded)}"
        )
    if new_degraded:
        notes.append(
            "new snapshot carries schema-vintage-degraded facts (regenerate "
            f"with the current abicheck to restore full detection): "
            f"{', '.join(new_degraded)}"
        )
    return "degraded", notes
