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

"""The release fan-out's ``run_outcome`` encoder -- ADR-063 D6's one
boundary decode for a directory/package release summary, split out of
:mod:`abicheck.policy.outcome` (which sat at this package's 800-line cap)
when ADR-065 S2 gave it a sixth axis to decode.

Same "read the already-computed ``exit`` block, never recompute it"
contract as before; what S2 adds is the two ADR-065 signals a release
decision can now carry -- ``no_comparison_completed_contribution`` (D7,
decoded onto the operational axis) and the caller's own
:class:`~abicheck.policy.outcome.ScopeCompleteness` (D6, decoded onto the
scope axis from the typed acquisition record, **not** from the exit block:
under the default ``warn`` policy the scope contributes ``0`` to the exit
code while the axis still reads ``incomplete``, which is exactly the
"never silently upgrades an incomplete scope into a clean one" rule).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..model.change_catalog.registry import Verdict
from .outcome import (
    _GATE_EXIT_CODE,
    OperationalStatus,
    PolicyGateDecision,
    RunOutcome,
    ScopeCompleteness,
    TargetLifecycle,
    policy_gate_decision_exit_code,
    policy_gate_decision_for_exit_code,
)

__all__ = ["run_outcome_dict_for_release"]


def run_outcome_dict_for_release(
    compatibility_verdict: str | None,
    exit_decision: object,
    *,
    scope: ScopeCompleteness = ScopeCompleteness.COMPLETE,
) -> dict[str, Any]:
    """Build the ``run_outcome`` dict for a directory/package release
    comparison's own summary JSON (``cli_compare_release_helpers.
    _format_release_json``, ``cli_compare_release_summary.
    _write_release_summary_file``) -- both claim ``run_outcome`` per the
    "every JSON report" contract (``docs/use/output-formats.md``) but never
    actually built one (Codex review).

    *compatibility_verdict* is deliberately **not** the release's own
    reported ``worst_verdict`` -- that string can legitimately be the
    ``"ERROR"``/``"not_comparable"``/``"unsupported"`` operational sentinels
    ``_RELEASE_VERDICT_ORDER`` ranks outside every real verdict, correct for
    the release's *reported* verdict but would erase a real, already-
    completed compatibility result from this separate axis. Callers pass
    the worst real ``Verdict`` among the release's library/global results
    with the sentinels excluded instead -- ``cli_compare_release_
    helpers._release_completed_compatibility_verdict(...)`` for a native
    writer, :func:`~abicheck.policy.outcome.worst_real_verdict` for the
    legacy-report backfill -- or ``None`` when no real result was observed
    at all (``compatibility`` stays unknown, never the dishonest floor
    ``"NO_CHANGE"``); either way this only ever parses a real ``Verdict``
    or ``None``, both handled by ``Verdict(...)``'s own ``ValueError``.

    *exit_decision* is the release's own already-computed ``exit`` block
    (``policy.exit_decision_precedence.resolve_release_exit_decision_for_
    report(...).to_dict()``) -- read, never recomputed.

    ``gate`` is derived from ``compatibility_contribution``, with one
    escalation: ``removed_required_library_contribution`` (exit 8) folds in
    as :attr:`PolicyGateDecision.ABI_BREAKING`, mirroring ``buildsource.
    check_report._escalate_removed_library_severity``. ``operational``
    reads ``not_comparable_contribution`` (preferred), then ``operational_
    error_contribution`` (:attr:`OperationalStatus.EXTRACTION_ERROR` --
    ``verdict: 'ERROR'`` means a library failed to dump/extract/compare),
    then ``no_comparison_completed_contribution`` (ADR-065 D7 --
    :attr:`OperationalStatus.NO_COMPARISON_COMPLETED`, the selected scope
    produced no valid comparison at all; ranked after the two above since
    each of those already says *why* nothing completed). The remaining
    ``ExitDecision`` contributions are always ``0`` for a release decision
    and are not consulted.

    *scope* (ADR-065 D6) is the caller's own completeness read of its typed
    acquisition record -- deliberately a parameter rather than a decode of
    ``incomplete_scope_contribution``, which is ``0`` under the default
    ``warn`` policy even when the scope is incomplete.
    """
    exit_block = exit_decision if isinstance(exit_decision, Mapping) else {}

    def _int_contribution(key: str) -> int:
        raw = exit_block.get(key, 0)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0

    compat_exit_code = _int_contribution("compatibility_contribution")
    if _int_contribution("removed_required_library_contribution") != 0:
        compat_exit_code = policy_gate_decision_exit_code(
            PolicyGateDecision.ABI_BREAKING
        )
    if compat_exit_code not in _GATE_EXIT_CODE.values():
        compat_exit_code = 0
    gate = policy_gate_decision_for_exit_code(compat_exit_code)

    operational = OperationalStatus.NONE
    if _int_contribution("not_comparable_contribution") != 0:
        operational = OperationalStatus.NOT_COMPARABLE
    elif _int_contribution("operational_error_contribution") != 0:
        operational = OperationalStatus.EXTRACTION_ERROR
    elif _int_contribution("no_comparison_completed_contribution") != 0:
        operational = OperationalStatus.NO_COMPARISON_COMPLETED

    compatibility: Verdict | None
    try:
        compatibility = Verdict(compatibility_verdict)
    except ValueError:
        compatibility = None

    return RunOutcome(
        compatibility=compatibility,
        assurance=None,
        gate=gate,
        operational=operational,
        lifecycle=TargetLifecycle.EXISTING,
        scope=scope,
    ).to_dict()
