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

"""ADR-065 D6/D7: the completeness axis, applied.

The typed :class:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord`
says *what* was and was not compared; this module answers the two policy
questions a run has about it, and only those:

* **Is the scope complete?** :func:`scope_completeness_for_record` -- the
  :class:`~abicheck.policy.outcome.ScopeCompleteness` value ``run_outcome``
  carries. Never depends on the policy setting: ``warn`` accepts an
  incomplete scope, it does not hide one.
* **Does it block?** :func:`incomplete_scope_exit_contribution` -- ``0``
  under ``warn`` (the default), ``1`` under ``block``, folded with ``max``
  by ``exit_decision`` exactly like ADR-049 Phase 7's coverage axis
  (``contract_coverage_exit.py``), so there is one fold shape, not two: a
  clean ``0`` becomes ``1``, a real ``2``/``4`` is never lowered, and no
  finding's compatibility decision or gate contribution is ever rewritten.

D7 is the one thing the policy cannot touch:
:func:`no_comparison_completed_exit_contribution` is ``1`` whenever the
selected scope produced no valid comparison, under every setting -- a
permissive policy can downgrade *missing members*, never *nothing compared*.

A leaf: imports ``model`` and the sibling ``outcome`` enum only, so the CLI
can reach it through ``workflows.gate`` without a new import-cycle member.
"""

from __future__ import annotations

from ..model.scope_acquisition import (
    AcquisitionState,
    MemberAcquisition,
    ScopeAcquisitionRecord,
)
from .outcome import ScopeCompleteness

__all__ = [
    "CLI_MITIGATION",
    "DEFAULT_INCOMPLETE_SCOPE_POLICY",
    "INCOMPLETE_SCOPE_POLICIES",
    "incomplete_scope_diagnostic",
    "incomplete_scope_exit_contribution",
    "no_comparison_completed_exit_contribution",
    "scope_completeness_for_record",
    "validate_incomplete_scope_policy",
]

#: The two settings D6 defines, exactly like ``contract.unresolved``'s
#: ``warn`` (accept, contribute ``0``) -- ``block`` contributes ``1``.
INCOMPLETE_SCOPE_POLICIES: tuple[str, ...] = ("warn", "block")
DEFAULT_INCOMPLETE_SCOPE_POLICY = "warn"

#: How a CLI user turns the warning into a gate, or closes the gap.
CLI_MITIGATION = (
    "Supply the missing members (or compare one artifact against its own "
    "counterpart), or pass --on-incomplete-scope block to fail the run on "
    "an incompletely checked scope. --format json carries the full "
    "comparison_scope record."
)


def validate_incomplete_scope_policy(value: str | None) -> str:
    """The effective policy for *value* (``None`` means the default), or
    ``ValueError`` for anything outside :data:`INCOMPLETE_SCOPE_POLICIES`."""
    if value is None:
        return DEFAULT_INCOMPLETE_SCOPE_POLICY
    if value not in INCOMPLETE_SCOPE_POLICIES:
        raise ValueError(
            "incomplete-scope policy must be one of "
            f"{', '.join(INCOMPLETE_SCOPE_POLICIES)}, got {value!r}"
        )
    return value


def scope_completeness_for_record(
    record: ScopeAcquisitionRecord | None,
) -> ScopeCompleteness:
    """The ``run_outcome.scope`` axis for *record*.

    ``COMPLETE`` when no record exists (a scalar comparison: the one pair it
    ran is the whole scope) or when every selected member reached a
    completed comparison; ``INCOMPLETE`` otherwise -- including when nothing
    completed at all, since an empty selection is not a fully checked scope.
    """
    if record is None or not record.is_incomplete:
        return ScopeCompleteness.COMPLETE
    return ScopeCompleteness.INCOMPLETE


def incomplete_scope_exit_contribution(
    record: ScopeAcquisitionRecord | None, policy: str | None
) -> int:
    """D6's ``0``/``1`` exit contribution for *record* under *policy*.

    ``0`` whenever the scope is complete or *policy* is ``warn``; ``1``
    only under ``block`` with an incomplete scope. A missing record is a
    scalar comparison and contributes ``0`` regardless -- there is no
    inventory for it to fall short of.
    """
    effective = validate_incomplete_scope_policy(policy)
    if record is None or effective != "block":
        return 0
    return 1 if record.is_incomplete else 0


def no_comparison_completed_exit_contribution(
    record: ScopeAcquisitionRecord | None,
) -> int:
    """D7's ``0``/``1``: ``1`` iff *record* completed no comparison at all.
    Policy-independent by design; ``0`` for a scalar run (no record)."""
    if record is None:
        return 0
    return 1 if record.no_comparison_completed else 0


def incomplete_scope_diagnostic(
    record: ScopeAcquisitionRecord | None,
    policy: str | None,
    *,
    base_exit: int = 0,
    mitigation: str = CLI_MITIGATION,
) -> str | None:
    """The one stderr wording for an incomplete scope, or ``None`` when the
    scope is complete -- mirrors ``contract_coverage_exit._coverage_message``
    so a Markdown/JUnit consumer with no JSON record still learns why the
    run was, or was not, floored.

    Names the unchecked members and their states rather than a bare count:
    "libfoo.so (not_supplied: no counterpart on NEW)" is actionable, "2
    members unchecked" is not. *base_exit* is the compatibility axis's own
    code and decides the wording, never merely whether to speak: beside a
    real break, claiming the exit "was floored to 1" would be false.
    """
    if record is None or not record.is_incomplete:
        return None
    effective = validate_incomplete_scope_policy(policy)
    floor = max(
        incomplete_scope_exit_contribution(record, effective),
        no_comparison_completed_exit_contribution(record),
    )
    unchecked = record.unchecked_members
    if unchecked:
        what = (
            "Comparison scope incompletely checked -- unchecked: "
            + _grouped_members(unchecked)
            + "."
        )
    else:
        what = "Comparison scope incompletely checked -- no member was compared."
    if record.no_comparison_completed:
        what += " No comparison completed (ADR-065 D7), which is never a clean pass."
    cause = (
        "no comparison completed (never accepted by --on-incomplete-scope)"
        if record.no_comparison_completed
        else "--on-incomplete-scope block"
    )
    if floor == 0:
        effect = (
            f"Accepted by --on-incomplete-scope {effective}, so the scope axis "
            "contributes 0 to the exit code"
        )
    elif base_exit < floor:
        effect = f"Exit code floored to {floor} by {cause}"
    elif base_exit == floor:
        effect = f"Contributes {floor} to an exit that was already {base_exit}"
    else:
        effect = (
            f"Contributes {floor}, below the compatibility axis's own exit "
            f"{base_exit}, which stands"
        )
    return f"{what} {effect} (ADR-065 completeness axis). {mitigation}"


#: How many member names one state's group spells out before "+N more".
_DIAGNOSTIC_NAMES_PER_STATE = 6


def _grouped_members(members: tuple[MemberAcquisition, ...]) -> str:
    """``state (one shared reason): a, b, c (+N more); state2 ...`` -- one
    group per acquisition state, names capped, the reason stated once per
    group (a twelve-member matrix must not print twelve identical
    sentences)."""
    groups: dict[AcquisitionState, list[MemberAcquisition]] = {}
    for m in members:
        groups.setdefault(m.state, []).append(m)
    parts: list[str] = []
    for state, group in groups.items():
        names = [m.name for m in group[:_DIAGNOSTIC_NAMES_PER_STATE]]
        more = len(group) - len(names)
        listed = ", ".join(names) + (f" (+{more} more)" if more > 0 else "")
        reasons = {m.reason for m in group if m.reason}
        reason = next(iter(reasons)) if len(reasons) == 1 else ""
        parts.append(f"{state.value}{' [' + reason + ']' if reason else ''}: {listed}")
    return "; ".join(parts)
