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

"""``abicheck compare --no-baseline NEW`` -- ADR-068 D2, plan §6 Phase 2e.

Replaces `scan`'s audit-only mode (plan §3 #2) and ADR-047 §8's S5
("single-build audit, no baseline"). The OLD side of this run carries
ADR-065's new :attr:`~abicheck.model.scope_acquisition.AcquisitionState.
DECLARED_ABSENT` acquisition state (plan §5 P1) -- an explicit user
declaration that no prior surface exists, never inferred from a missing
argument and never confused with :attr:`~abicheck.model.scope_acquisition.
AcquisitionState.NOT_SUPPLIED` (an *unproven*, possibly-incomplete-scope
absence).

**How this reports candidate-side facts without a real OLD snapshot**:
:func:`run_no_baseline_compare` diffs *new* against itself. Two identical
:class:`~abicheck.model.AbiSnapshot` objects can never produce an addition,
a removal, or a modification -- so the real detector/suppression/policy
pipeline (:func:`abicheck.checker.compare`) runs completely unmodified, and
every candidate-side fact the resulting :class:`~abicheck.checker_types.
DiffResult` carries (evidence tiers, coverage, analysis assurance) is
genuine evidence about *new*, not a synthesized stand-in. The empty change
set falls out of the identity comparison by construction, rather than from
a change-kind filter that could silently drift out of sync with the
detector registry (a filter is a second place a new ``ChangeKind`` would
need to be taught about "no-baseline mode"; identity needs no such
maintenance).

The one thing this module does *not* do is decide how the resulting
:class:`~abicheck.checker_types.DiffResult` gets reported: ``NO_CHANGE`` is
the *true* verdict of "new compared to itself", but the whole point of
``--no-baseline`` is that no compatibility comparison was ever intended --
so a caller (:mod:`abicheck.frontends.cli.commands.compare_no_baseline`)
must null the compatibility-shaped fields (``verdict``,
``run_outcome.compatibility``) before this reaches a report; ADR-068 D2:
"never emits an addition, removal, or compatibility verdict -- run_outcome
carries no compatibility contribution".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..checker import compare as _diff_pair
from ..model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    MemberAcquisition,
    ScopeAcquisitionRecord,
    SideInventory,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ..checker_types import DiffResult
    from ..model import AbiSnapshot
    from ..policy_file import PolicyFile
    from ..suppression import SuppressionList

__all__ = [
    "NoBaselineCompareResult",
    "declared_absent_acquisition_record",
    "resolve_no_baseline_candidate",
    "run_no_baseline_compare",
]


def resolve_no_baseline_candidate(
    path: Path,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    lang: str = "c++",
    lang_explicit: bool = False,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
) -> AbiSnapshot:
    """Resolve the sole ``--no-baseline`` operand into an
    :class:`~abicheck.model.AbiSnapshot`.

    A thin, workflows-internal call to :func:`abicheck.workflows.
    input_resolution.resolve_input` -- the single-input primitive `dump`
    and `compare`'s own per-side resolution are both ultimately built on
    (``service_input_resolution.resolve_side_snapshot``, ADR-061's `service_
    input_resolution.py` module docstring). Called from here rather than
    from the CLI layer directly so the ``cli-contract`` AI-readiness gate's
    "front end may not call Tier-1 core directly" rule is satisfied by
    construction: the CLI module calls this workflow, this workflow calls
    the resolver."""
    from .input_resolution import resolve_input

    return resolve_input(
        path,
        headers=headers or [],
        includes=includes or [],
        lang=lang,
        lang_explicit=lang_explicit,
        public_headers=public_headers or [],
        public_header_dirs=public_header_dirs or [],
    )


#: The one selection value a ``--no-baseline`` run's
#: :class:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord` carries
#: -- distinct from ``"direct_pair"`` (a real two-sided scalar comparison,
#: which today never even builds a record) and from the release fan-out's
#: ``"all_expected"``/``"current_artifact"``.
NO_BASELINE_SELECTION = "no_baseline"


@dataclass(frozen=True)
class NoBaselineCompareResult:
    """The two things a ``--no-baseline`` run produces: the candidate-side
    :class:`~abicheck.checker_types.DiffResult` (self-diffed; always an
    empty change set) and the :class:`~abicheck.model.scope_acquisition.
    ScopeAcquisitionRecord` recording OLD's ``declared_absent`` state."""

    diff: DiffResult
    acquisition: ScopeAcquisitionRecord


def declared_absent_acquisition_record(new: AbiSnapshot) -> ScopeAcquisitionRecord:
    """The one-member :class:`ScopeAcquisitionRecord` a ``--no-baseline``
    run carries: OLD is ``declared_absent`` (not "unmatched" -- the user
    said so), NEW is present. Never incomplete
    (:attr:`~abicheck.model.scope_acquisition.AcquisitionState.
    DECLARED_ABSENT` is excluded from ``UNCHECKED_STATES``) and never a
    proven removal/addition input (only ``NOT_SUPPLIED`` members are read
    by :attr:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord.
    proven_removed_members`/``proven_added_members``)."""
    member_key = new.library or "candidate"
    member = MemberAcquisition(
        member=member_key,
        state=AcquisitionState.DECLARED_ABSENT,
        old_present=False,
        new_present=True,
        reason="--no-baseline: no prior surface declared for this run",
        display_name=new.library,
    )
    return ScopeAcquisitionRecord(
        members=(member,),
        old_inventory=SideInventory(
            completeness=InventoryCompleteness.UNPROVEN,
            provenance="--no-baseline: OLD declared absent, not supplied",
        ),
        new_inventory=SideInventory(
            completeness=InventoryCompleteness.UNPROVEN,
            provenance="candidate build",
        ),
        selection=NO_BASELINE_SELECTION,
        selection_reason="abicheck compare --no-baseline: single candidate audit",
    )


def run_no_baseline_compare(
    new: AbiSnapshot,
    *,
    suppression: SuppressionList | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
    force_public_symbols: set[str] | None = None,
    pattern_verdicts: bool = False,
    collapse_versioned_symbols: bool = False,
    contract_evaluation: bool = False,
    contract_mode: str | None = None,
) -> NoBaselineCompareResult:
    """Audit *new* alone via a self-diff (see module docstring for why this
    is exact, not an approximation) and pair it with OLD's
    ``declared_absent`` acquisition record."""
    diff = _diff_pair(
        new,
        new,
        suppression=suppression,
        policy=policy,
        policy_file=policy_file,
        scope_to_public_surface=scope_to_public_surface,
        force_public_symbols=force_public_symbols,
        pattern_verdicts=pattern_verdicts,
        collapse_versioned_symbols=collapse_versioned_symbols,
        contract_evaluation=contract_evaluation,
        contract_mode=contract_mode,
    )
    assert not diff.changes, (
        "a snapshot compared against itself must never produce a change -- "
        "if this fires, a detector is reading non-identity state"
    )
    return NoBaselineCompareResult(
        diff=diff, acquisition=declared_absent_acquisition_record(new)
    )
