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

"""``compare --dry-run``'s "Cost preview" section -- the *render* half
(one-comparison-product.md #35, Phase 2f).

Takes the caller's already-computed
:func:`~abicheck.workflows.compare_cost_preview.estimate_compare_dry_run_cost`
result (a list of :class:`~abicheck.service_scan.CostEstimate` rows, or an
error string) and turns it into ``DryRunResult`` section lines -- the same
split :mod:`abicheck.frontends.cli.scan_dry_run` already applies between a
workflow's computed estimate and its CLI rendering, and for the identical
reason: this module lives under ``frontends/cli`` (may import ``model``/
``workflows``/``report`` only, per ``architecture/modules.yaml``), so the
*computation* -- which needs ``service_scan`` -- stays in
:mod:`abicheck.workflows.compare_cost_preview` (the ``workflows`` layer,
where ``service_scan.py`` itself is classified).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...dry_run import DryRunResult
    from ...service_scan import CostEstimate


def add_compare_cost_preview_section(
    result: DryRunResult,
    estimates: list[CostEstimate] | None,
    estimate_error: str | None,
) -> None:
    """Append the "Cost preview" section (or a warning) to *result*."""
    if estimate_error is not None:
        result.warn(
            f"could not project per-layer evidence-collection cost: {estimate_error}"
        )
        return
    assert estimates is not None
    total = sum(e.est_seconds for e in estimates)
    result.add(
        "Cost preview",
        *(
            f"{e.layer}: TU count/cost unknown -- {e.note}"
            if "[UNKNOWN" in e.note
            else f"{e.layer}: {e.tus} TU(s), ~{e.est_seconds:.2f}s -- {e.note}"
            for e in estimates
        ),
        f"projected total (old + new sides): {total:.2f}s",
        "note: at least one layer's TU count/cost is unknown (see above) "
        "-- it contributes 0.0s to the projected total, understating it"
        if any("[UNKNOWN" in e.note for e in estimates)
        else None,
    )
