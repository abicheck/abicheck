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
"""Projections of the assurance block: the report dict, the exit contribution and the floor diagnostic."""

from __future__ import annotations

from typing import Any

from ...checker_types import DiffResult
from ._result import AnalysisAssurance


def analysis_assurance_report_dict(result: DiffResult) -> dict[str, Any] | None:
    """``result.analysis_assurance.to_dict()``, or ``None`` when absent/wrong
    type -- the one narrowing implementation ``reporter._add_analysis_assurance``
    calls from all four JSON paths (full/leaf/root-cause/stat), so the
    ``DiffResult.analysis_assurance: object`` circular-import workaround (see
    that field's own comment in ``checker_types.py``) has exactly one place
    that unwraps it.
    """
    aa = getattr(result, "analysis_assurance", None)
    if not isinstance(aa, AnalysisAssurance):
        return None
    return aa.to_dict()


#: Exit contribution when ``--require-complete-analysis`` is set and the
#: status fell short of ``complete``. Deliberately the same value
#: ``contract_coverage_exit.py`` uses for its own orthogonal axis: both are
#: "the evidence behind this verdict is not fully trustworthy" signals, and
#: ``max``-folding several such axes onto the same floor is exactly what
#: keeps them from stacking into a make-believe higher severity than any one
#: of them actually earned.
INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION = 1


def analysis_assurance_exit_contribution(
    result: DiffResult, *, require_complete: bool
) -> int:
    """The exit floor ``--require-complete-analysis`` imposes (``0``/``1``).

    ``0`` unconditionally when *require_complete* is False (the default) --
    this is what keeps the flag purely additive: a caller that never opts in
    sees no change to any exit code, ever, regardless of what
    ``analysis_assurance.status`` says. ``0`` also when *result* carries no
    ``AnalysisAssurance`` at all (defensive; ``checker.compare()`` always
    attaches one, but a hand-built ``DiffResult`` in a test or an older
    in-memory object might not).
    """
    if not require_complete:
        return 0
    aa = getattr(result, "analysis_assurance", None)
    if not isinstance(aa, AnalysisAssurance):
        return 0
    return 0 if aa.status == "complete" else INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION


def assurance_floor_diagnostic(
    result: DiffResult, *, require_complete: bool, base_exit: int
) -> str | None:
    """Why this run's exit code was (or wasn't) affected by
    ``--require-complete-analysis``, or ``None`` when the flag wasn't set or
    the status was already ``complete``. Mirrors
    ``contract_coverage_exit.coverage_failure_diagnostic``'s wording style so
    the two orthogonal-axis notices read as one family.
    """
    if not require_complete:
        return None
    aa = getattr(result, "analysis_assurance", None)
    if not isinstance(aa, AnalysisAssurance) or aa.status == "complete":
        return None
    floor = INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION
    if base_exit < floor:
        effect = f"Exit code floored to {floor}"
    elif base_exit == floor:
        effect = f"Contributes {floor} to an exit that was already {base_exit}"
    else:
        effect = (
            f"Contributes {floor}, below the compatibility axis's own exit "
            f"{base_exit}, which stands"
        )
    where = "; ".join(aa.notes) if aa.notes else "no further detail recorded"
    return (
        f"Analysis assurance incomplete (status={aa.status!r}) under "
        f"assurance.require_complete: {where}. {effect} (P0.4 "
        "analysis-assurance axis). Use -o json=... for the full "
        "analysis_assurance block."
    )
