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

"""ADR-063 Phase 7's ``run_outcome`` block for ``check_report.py``'s three
synthetic report builders.

Split out of ``check_report.py`` purely to keep that already-near-the-cap
module from growing (mirrors ``check_report_exit_backfill.py``'s own
split-for-size precedent) -- each of ``build_operational_error_report()``/
``build_bootstrap_report()``/``build_new_target_report()`` represents a run
that never computed a real compatibility verdict at all, so
``compatibility``/``assurance``/``gate`` all stay at their honest "nothing
computed" values and only the one axis the builder actually represents
(``operational`` for a resolve-baseline failure, ``lifecycle`` for a
bootstrap/new-target pass) is populated.
"""

from __future__ import annotations

from typing import Any

from ..policy.outcome import (
    OperationalStatus,
    PolicyGateDecision,
    RunOutcome,
    TargetLifecycle,
)

__all__ = ["synthetic_run_outcome"]


def synthetic_run_outcome(
    *,
    operational: OperationalStatus = OperationalStatus.NONE,
    lifecycle: TargetLifecycle = TargetLifecycle.EXISTING,
) -> dict[str, Any]:
    return RunOutcome(
        compatibility=None,
        assurance=None,
        gate=PolicyGateDecision.NONE,
        operational=operational,
        lifecycle=lifecycle,
    ).to_dict()
