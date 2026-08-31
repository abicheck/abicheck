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

"""ADR-064 stage 1b: shaping `service_scan.ScanResult` for an abort.

`abicheck.policy.exit_decision_precedence.resolve_scan_exit_decision`
decides *which axis* explains a `run_scan_core` abort -- that is a policy
question. Which verdict string/exit code `ScanResult` carries for each axis,
and how the decision nests under `report["exit"]`, is a *report-shape*
question `abicheck/policy/AGENTS.md` explicitly reserves for a different
layer ("never 'how is it reported' -- that is `report/`"; here, the
`workflows` layer, since `service_scan.ScanResult` is itself classified
`workflows-or-frontends` in `architecture/debt.yaml`). An earlier revision
of this function lived in `exit_decision_precedence.py` itself (Codex
review, PR #967, fresh evidence) -- moved here instead of merely trimmed,
since the shaping logic is real and still needed, just misplaced.

`service_scan.py` and `scan_engine.py` are both under an ADR-061 no-growth
debt entry (`architecture/debt.yaml`), so this could not be inlined at
either of their two `_BudgetOverflow`/`_EvidenceContractError` catch sites
either -- a new, small `workflows` leaf module is the only budget-neutral
home left.

`abicheck.schemas` (for `SCAN_SCHEMA_VERSION`) joined `architecture/
modules.yaml`'s `public_root_surfaces` for this module -- the same "a
genuinely public, stable surface reached from a migrated package" exemption
`abicheck.serialization` already uses, per ADR-061's own precedent.
"""

from __future__ import annotations

from typing import Literal, TypedDict

from ..policy.exit_decision import ExitDecision
from ..policy.exit_decision_precedence import resolve_scan_exit_decision
from ..schemas import SCAN_SCHEMA_VERSION

ScanAbortAxis = Literal["budget_overflow", "evidence_contract_error"]

#: `run_scan_core`'s two abort exceptions -> the verdict/exit_code pair
#: `service_scan.ScanResult` already used before it carried a `report` too.
_SCAN_ABORT_VERDICTS: dict[ScanAbortAxis, tuple[str, int]] = {
    "budget_overflow": ("BUDGET_OVERFLOW", 5),
    "evidence_contract_error": ("EVIDENCE_CONTRACT_ERROR", 1),
}


class ScanAbortResultFields(TypedDict):
    """``ScanResult(**scan_abort_result_fields(axis))`` -- a `TypedDict`
    (not a plain ``dict[str, object]``) so mypy checks each field's type
    against `service_scan.ScanResult`'s own constructor when ``**``-unpacked,
    instead of rejecting the unpack outright the way it does for an untyped
    dict (whose values it cannot attribute to individual parameters).
    """

    verdict: str
    exit_code: int
    report: dict[str, object]


def scan_abort_result_fields(
    axis: ScanAbortAxis, *, prior_decision: ExitDecision | None = None
) -> ScanAbortResultFields:
    """Every `ScanResult` field `service_scan.run_scan`/
    `_run_scan_one_member` need for one of `run_scan_core`'s two abort
    exceptions, so the verdict/exit_code pairing stays next to the
    `ExitDecision` that now explains it, instead of duplicated at each
    `except` site. `report["exit"]` mirrors what `scan_engine.py`'s own
    ``NOT_COMPARABLE`` outcome already persists via ``resolve_scan_exit_
    decision(not_comparable=True)``; `report["scan_schema_version"]`
    mirrors the same key every real (non-abort) `ScanResult.report` already
    carries (`ScanOutcome.to_dict()`'s own top-level stamp, per `tests/
    test_scan_estimate.py`'s documented "both the service envelope and the
    nested ... report carry the same scan schema version marker" contract
    -- Codex review, PR #967).

    *prior_decision* forwards to `resolve_scan_exit_decision`'s own
    parameter of the same name (used only for the `budget_overflow` axis) --
    a caller that already resolved a full gate/coverage/assurance decision
    before `_BudgetOverflow` fired (e.g. the *later* of `scan_engine.py`'s
    two raise sites, which runs after a comparable baseline compare) should
    pass it so the persisted report still shows those contributions,
    matching that resolver's own "budget discards, but preserves" contract.
    Neither of `service_scan.py`'s current call sites has one available --
    `run_scan_core` raises before returning anything they could recover a
    prior decision from -- so both pass none today; carrying one across that
    exception boundary is real, separate follow-up work, not something this
    function's own shape can supply on its own (Codex review, PR #967).
    """
    decision = resolve_scan_exit_decision(
        budget_overflow=axis == "budget_overflow",
        evidence_contract_error=axis == "evidence_contract_error",
        prior_decision=prior_decision,
    )
    assert decision is not None  # axis always selects one of the two above
    verdict, exit_code = _SCAN_ABORT_VERDICTS[axis]
    report: dict[str, object] = {
        "scan_schema_version": SCAN_SCHEMA_VERSION,
        "exit": decision.to_dict(),
    }
    return ScanAbortResultFields(verdict=verdict, exit_code=exit_code, report=report)
