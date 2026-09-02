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

"""ADR-063 Phase 7: build the ``run_outcome`` block for a real ``DiffResult``.

A leaf split out of ``reporter.py`` purely to keep that already-at-the-line
-cap file from growing (mirrors ``report_summary.py``/
``report_classifications.py``'s own split-for-size precedent) -- this
module's only job is turning one ``DiffResult`` + the caller's own
``SeverityConfig`` into a :class:`~abicheck.policy.outcome.RunOutcome`
dict, shared by ``reporter.py``'s four JSON entry points (full/leaf/
root-cause/stat). Lives under ``abicheck/report/`` (not a flat
``report_*.py`` module) per AGENTS.md's routing table: a new report field
belongs to the ``report`` responsibility package, not a legacy flat root
module (Codex review -- a first draft added this as
``abicheck/report_run_outcome.py``, growing ``report``'s ``legacy_paths``
migration debt instead of routing to its canonical owner).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..policy.gate_decision import gate_decision_for_result
from ..policy.outcome import (
    OperationalStatus,
    RunOutcome,
    TargetLifecycle,
    policy_gate_decision_for_exit_code,
)

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from ..severity import SeverityConfig

__all__ = ["run_outcome_dict_for_diff_result"]


def run_outcome_dict_for_diff_result(
    result: DiffResult,
    severity_config: SeverityConfig | None,
) -> dict[str, Any]:
    """The ``run_outcome`` block (ADR-063 Phase 7 / D6) for *result*.

    Additive alongside the existing ``severity``/``verdict``/``exit_code``
    fields, never a replacement for them -- see ``policy/outcome.py``'s own
    module docstring. Recomputes the gate decision via
    :func:`gate_decision_for_result` (cheap, and it's already the one
    canonical reconstruction site) rather than threading an
    already-computed value through each of ``reporter.py``'s four JSON
    entry points.

    An ordinary ``compare``/``--report-mode`` render never carries an
    operational failure of its own (that axis is populated only by the
    synthetic report builders and the not-comparable/scan writers) and has
    no ``aggregate`` target-lifecycle concept, so ``operational``/
    ``lifecycle`` stay at their fixed defaults here.
    """
    from ..severity import legacy_exit_code

    gate = gate_decision_for_result(result, severity_config)
    exit_code = gate.exit_code if gate is not None else legacy_exit_code(result.verdict)
    outcome = RunOutcome(
        compatibility=result.verdict,
        assurance=getattr(result, "analysis_assurance", None),
        gate=policy_gate_decision_for_exit_code(exit_code),
        operational=OperationalStatus.NONE,
        lifecycle=TargetLifecycle.EXISTING,
    )
    return outcome.to_dict()
