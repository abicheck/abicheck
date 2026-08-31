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

`attach_prior_on_budget_overflow` closes ADR-064's "preserve prior
contributions on a later budget overflow" follow-up (Codex review, PR #967):
`scan_engine.run_scan_core`'s one *late* `_check_scan_budget` call site (the
post-compare deadline check, which runs after a real gate/coverage/assurance
decision may already exist) needs to attach that decision to the
`_BudgetOverflow` it raises, so `scan_abort_result_fields` can thread it
through instead of reporting a budget-only decision that discards real,
already-computed contributions. It deliberately catches ``Exception`` and
discriminates via ``hasattr`` rather than importing `_BudgetOverflow` to
`isinstance`-check against: `scan_engine.py` is unclassified (`architecture/
modules.yaml`'s `legacy_root_modules`), and `_BudgetOverflow` is a private,
underscore-prefixed signal, not the kind of genuinely public, stable surface
`public_root_surfaces` exists for (unlike `abicheck.schemas` above) -- so
importing it here to satisfy a type check would misuse that exemption for a
private cross-module coupling. Attribute-based duck typing needs no import
at all, in either direction.

`audit_prior_decision` closes the sibling gap the same review found: a late
budget overflow with no baseline at all (`run_scan_core`'s ``else`` branch,
audit mode) had nothing in `diff_summary` to attach either, since that
branch never builds one -- `scan_engine._audit_exit_code` now returns this
dict as a third element alongside its existing verdict/exit_code, fed to
`attach_prior_on_budget_overflow` the same way the baseline-compare branch's
own `diff_summary` is, without changing audit mode's real (non-aborting)
report shape.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from ..policy.exit_decision import ExitDecision, resolve_exit_decision
from ..policy.exit_decision_precedence import resolve_scan_exit_decision
from ..schemas import SCAN_SCHEMA_VERSION

if TYPE_CHECKING:
    from collections.abc import Iterator

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


@contextmanager
def attach_prior_on_budget_overflow(
    diff_summary: dict[str, Any] | None,
) -> Iterator[None]:
    """Give a `_BudgetOverflow` raised inside this block its ``prior_decision``.

    `scan_engine.run_scan_core`'s one *late* budget check
    (``_check_scan_budget``'s single call site, after a baseline compare may
    already have resolved a full gate/coverage/assurance decision into
    *diff_summary*) wraps that call in ``with attach_prior_on_budget_overflow
    (diff_summary):`` instead of threading a new parameter through
    ``_check_scan_budget`` itself -- `scan_engine.py` carries its own tight
    ADR-061 no-growth budget, and catching-and-annotating here costs this
    module (uncapped) the lines instead. See this module's own docstring for
    why ``hasattr`` rather than `isinstance` against `_BudgetOverflow`.
    """
    try:
        yield
    except Exception as exc:
        if hasattr(exc, "prior_decision"):
            exc.prior_decision = diff_summary.get("exit") if diff_summary else None
        raise


def scan_abort_result_fields(
    axis: ScanAbortAxis, *, prior_decision: dict[str, Any] | None = None
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

    *prior_decision* is the raw ``ExitDecision.to_dict()`` form (not the
    dataclass itself) -- the only shape that survives the exception boundary
    `_BudgetOverflow.prior_decision` crosses (`attach_prior_on_budget_
    overflow`, above), since `scan_engine.py` cannot hold a typed
    `ExitDecision` reference without importing the `policy` package as an
    unclassified, legacy module (a real but separate cleanup). Reconstructed
    here via `ExitDecision.from_dict` and forwarded to `resolve_scan_exit_
    decision`'s own parameter of the same name (used only for the
    `budget_overflow` axis) -- a caller that already resolved a full gate/
    coverage/assurance decision before `_BudgetOverflow` fired should pass
    it so the persisted report still shows those contributions, matching
    that resolver's own "budget discards, but preserves" contract.
    """
    prior = ExitDecision.from_dict(prior_decision) if prior_decision else None
    decision = resolve_scan_exit_decision(
        budget_overflow=axis == "budget_overflow",
        evidence_contract_error=axis == "evidence_contract_error",
        prior_decision=prior,
    )
    assert decision is not None  # axis always selects one of the two above
    verdict, exit_code = _SCAN_ABORT_VERDICTS[axis]
    report: dict[str, object] = {
        "scan_schema_version": SCAN_SCHEMA_VERSION,
        "exit": decision.to_dict(),
    }
    return ScanAbortResultFields(verdict=verdict, exit_code=exit_code, report=report)


def audit_prior_decision(has_api_break: bool, crosscheck_exit: int) -> dict[str, Any]:
    """`scan_engine._audit_exit_code`'s own compatibility/crosscheck
    contributions, shaped as the ``{"exit": ...}`` dict
    `attach_prior_on_budget_overflow` expects -- so a *later* budget overflow
    in audit mode (no baseline at all, `run_scan_core`'s own ``else`` branch)
    preserves what the audit already found instead of reporting a bare
    budget-only decision (Codex review, PR #967, fresh evidence: the earlier
    fix only threaded a prior decision through the baseline-compare branch).

    Deliberately not persisted into `ScanOutcome.diff_summary` itself --
    audit mode's real report keeps ``diff: null`` on every non-aborting run,
    matching every consumer that treats its presence as "a baseline
    comparison ran" (`cli_scan_helpers.py`'s text renderer keys off exactly
    that, and would `KeyError` on the baseline-compare-shaped keys it reads
    once past that check). This dict exists only to feed the late-abort
    context manager, never the success-path outcome.
    """
    decision = resolve_exit_decision(
        compatibility_contribution=2 if has_api_break else 0,
        crosscheck_promotion_contribution=crosscheck_exit,
    )
    return {"exit": decision.to_dict()}
