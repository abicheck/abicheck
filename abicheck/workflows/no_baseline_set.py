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

"""``abicheck compare --no-baseline DIR`` -- the N-library candidate-only audit
(one-comparison-product acceptance F-23, ADR-068 D2).

Replaces the retired ``scan --artifact-set``. ADR-068 D2 states an N-library
audit is ``compare --no-baseline DIR``, resolving its members through
ADR-065's one acquisition/selection model -- so this module invents none of
that model, it composes it:

* **Members** come from :func:`~abicheck.workflows.release_inputs.
  resolve_release_side`, the one-sided resolver a two-sided release fan-out
  now calls twice. The same operand therefore yields the same member set
  whichever command reads it.
* **Inventory evidence** is ADR-065 D2's rule, read through
  :func:`~abicheck.workflows.release_scope.release_inventory_evidence`: a
  directory proves nothing (``unproven``); a fully extracted archive is
  ``proven``; a stored ``ProjectSnapshot`` package only when its capture
  asserted ``inventory_complete``.
* **Each member** is audited by :func:`~abicheck.workflows.
  no_baseline_compare.audit_no_baseline_candidate` -- exactly the sequence a
  scalar ``compare --no-baseline FILE`` runs, so a member's own report is
  the scalar report of that file.
* **The acquisition record** (:func:`build_no_baseline_set_record`) is a
  one-sided sibling of ``release_plan.build_declared_selection_record``.
  OLD is *declared absent* on every member -- never ``not_supplied``, so the
  record can never be read as a removal or an addition, whatever any
  inventory proves.

Framework-free: no Click, no rendering. Classifying a failure into a usage
error is the front end's job; reporting is :mod:`abicheck.report.
no_baseline_set`'s.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from ..model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    MemberAcquisition,
    ScopeAcquisitionRecord,
    SideInventory,
)
from .no_baseline_compare import (
    NO_BASELINE_SELECTION,
    NoBaselineAuditInputs,
    NoBaselineCompareResult,
    audit_no_baseline_candidate,
)

if TYPE_CHECKING:
    from ..model.release_selection import ReleaseSelection
    from .release_inputs import ReleaseSide

__all__ = [
    "MemberAudit",
    "MemberAuditStatus",
    "NoBaselineSetPlan",
    "NoBaselineSetPreviewEntry",
    "NoBaselineSetResult",
    "OLD_DECLARED_ABSENT_PROVENANCE",
    "build_no_baseline_set_record",
    "cleanup_no_baseline_set_plan",
    "member_audit_inputs",
    "preview_no_baseline_set",
    "resolve_no_baseline_set_plan",
    "run_no_baseline_set",
]

_log = logging.getLogger("abicheck.no_baseline_set")

#: OLD's inventory on every ``--no-baseline`` record, scalar or set: the
#: user declared there is no prior surface, which proves nothing about one.
OLD_DECLARED_ABSENT_PROVENANCE = "--no-baseline: OLD declared absent, not supplied"

#: The reason every audited member's ``declared_absent`` state carries.
_AUDITED_REASON = "--no-baseline: no prior surface declared; candidate audited"


@dataclass(frozen=True)
class NoBaselineSetPlan:
    """The resolved, pre-execution half of an N-library audit.

    Everything decidable before the first member is dumped: which members the
    operand ships (``side.members``), which of them the run selected, which
    failed acquisition before any audit (``failed``: ``--dso-only`` could not
    classify them, or a stored package marks them degraded), which the
    package declares but extraction did not produce (``unproduced``), and
    what the operand can prove about its own completeness.
    """

    operand: Path
    operand_kind: str
    side: ReleaseSide
    inventory: SideInventory
    selection: ReleaseSelection | None
    failed: Mapping[str, str]
    unproduced: Mapping[str, str]
    warnings: tuple[str, ...] = ()
    temp_dirs: tuple[Path, ...] = ()

    @property
    def audit_keys(self) -> list[str]:
        """The members this run audits, in member-key order: discovered,
        selected, and not already failed before any audit."""
        return [
            key
            for key in sorted(self.side.members)
            if key not in self.failed
            and (self.selection is None or key in self.selection)
        ]

    @property
    def include_roots(self) -> tuple[Path, ...]:
        """Include roots discovered inside an extracted package's header tree."""
        from .release_inputs import discover_include_roots

        return tuple(discover_include_roots(self.side.header_dir))


def resolve_no_baseline_set_plan(
    operand: Path,
    *,
    operand_kind: str,
    include_private_dso: bool = False,
    dso_only: bool = False,
    selection: ReleaseSelection | None = None,
    variant: str | None = None,
) -> NoBaselineSetPlan:
    """Resolve *operand* (a directory, package archive, or multi-artifact
    stored package) into a :class:`NoBaselineSetPlan`.

    Raises the release resolution's own typed errors unchanged
    (:class:`~abicheck.errors.ReleaseOperandContentError` for an operand
    holding nothing readable, :class:`~abicheck.errors.
    ReleaseOperandUsageError` for an unselectable stored variant,
    :class:`~abicheck.errors.AmbiguousLibraryMatchError`), so a front end
    translates them exactly as the two-sided release boundary does.
    Temporary directories are removed again if resolution fails partway.
    """
    from .extraction import (
        _is_elf_shared_object,
        detect_extractor,
        discover_shared_libraries,
        is_package,
    )
    from .release_inputs import extract_if_package, resolve_release_side
    from .release_scope import release_inventory_evidence
    from .release_stored_inventory import (
        stored_side_degraded_members,
        stored_side_inventory_complete,
    )

    allocated: list[Path] = []

    def _make_temp_dir(prefix: str) -> Path:
        path = Path(tempfile.mkdtemp(prefix=prefix))
        allocated.append(path)
        return path

    def _do_extract(
        input_path: Path, debug_pkg: Path | None, devel_pkg: Path | None
    ) -> tuple[Path, Path | None, Path | None, Path | None, bool]:
        return extract_if_package(
            input_path,
            debug_pkg,
            devel_pkg,
            _make_temp_dir,
            is_package,
            detect_extractor,
        )

    try:
        side = resolve_release_side(
            operand,
            side="new",
            debug_pkg=None,
            devel_pkg=None,
            include_private_dso=include_private_dso,
            dso_only=dso_only,
            headers=(),
            side_headers_only=(),
            includes=(),
            side_includes_only=(),
            config_includes=(),
            extract_if_package=_do_extract,
            discover_shared_libraries=discover_shared_libraries,
            is_package=is_package,
            is_elf_shared_object=_is_elf_shared_object,
            variant=variant,
            make_temp_dir=_make_temp_dir,
        )
        complete = side.stored and stored_side_inventory_complete(
            operand, variant_id=variant
        )
        inventory = release_inventory_evidence(
            old_stored=False,
            new_stored=side.stored,
            new_complete=complete,
            new_unclassified=side.unclassified,
            new_inventory=side.inventory,
        ).new
        degraded = {
            key: (f"candidate was captured degraded ({reason}); audit skipped")
            for key, reason in (
                stored_side_degraded_members(operand, variant_id=variant).items()
                if side.stored
                else ()
            )
            if key in side.members
        }
    except BaseException:
        import shutil

        for path in allocated:
            shutil.rmtree(path, ignore_errors=True)
        raise
    return NoBaselineSetPlan(
        operand=operand,
        operand_kind=operand_kind,
        side=side,
        inventory=inventory,
        selection=selection,
        failed={**side.unclassified, **degraded},
        unproduced=dict(side.inventory.unproduced) if side.inventory else {},
        warnings=tuple(
            [f"Warning: {w}" for w in side.match_warnings] + side.symbols_conflicts
        ),
        temp_dirs=tuple(allocated),
    )


def cleanup_no_baseline_set_plan(plan: NoBaselineSetPlan) -> None:
    """Remove every temporary directory *plan*'s resolution allocated."""
    import shutil

    for path in plan.temp_dirs:
        shutil.rmtree(path, ignore_errors=True)


class MemberAuditStatus(str, Enum):
    """How one selected member's audit ended -- the three outcomes the
    two-sided release path's ``release_member_errors`` distinguishes,
    minus ``not_comparable`` (a one-sided audit has no pair to refuse)."""

    COMPLETED = "completed"
    #: An artifact this build cannot analyze (ADR-065 D6): incompleteness,
    #: not an operational failure.
    UNSUPPORTED = "unsupported"
    #: An operational failure (SnapshotError or anything unexpected): the
    #: member is listed, and contributes the operational-error exit axis.
    FAILED = "failed"


@dataclass(frozen=True)
class MemberAudit:
    """One selected member's audit outcome. *result* is set only when
    *status* is :attr:`MemberAuditStatus.COMPLETED`."""

    member: str
    path: Path
    status: MemberAuditStatus
    result: NoBaselineCompareResult | None = None
    reason: str = ""
    error_type: str = ""


@dataclass(frozen=True)
class NoBaselineSetResult:
    """An N-library audit, run: the plan, one :class:`MemberAudit` per
    selected member, and the acquisition record built from both."""

    plan: NoBaselineSetPlan
    audits: tuple[MemberAudit, ...]
    record: ScopeAcquisitionRecord

    @property
    def completed(self) -> tuple[MemberAudit, ...]:
        """The members whose audit produced a result."""
        return tuple(a for a in self.audits if a.status is MemberAuditStatus.COMPLETED)

    @property
    def operationally_failed(self) -> tuple[MemberAudit, ...]:
        """The members whose audit failed operationally."""
        return tuple(a for a in self.audits if a.status is MemberAuditStatus.FAILED)


def member_audit_inputs(
    plan: NoBaselineSetPlan, inputs: NoBaselineAuditInputs
) -> NoBaselineAuditInputs:
    """*inputs* plus what the operand itself discovered.

    Additive, never a replacement: the shared ``-H``/``-I`` values reach every
    member exactly as they reach a scalar audit, and an extracted package's
    own header tree, its include roots, and its debug directory are appended
    -- so a plain directory's member is audited with *precisely* the inputs
    the same file would get on its own. The header tree is also public-header
    provenance, as a two-sided release side's headers are.
    """
    header_dir = plan.side.header_dir
    debug_dir = plan.side.debug_dir
    if header_dir is None and debug_dir is None:
        return inputs
    return replace(
        inputs,
        headers=inputs.headers + ((header_dir,) if header_dir else ()),
        includes=inputs.includes + plan.include_roots,
        public_header_dirs=inputs.public_header_dirs
        + ((header_dir,) if header_dir else ()),
        debug_roots=inputs.debug_roots + ((debug_dir,) if debug_dir else ()),
    )


def run_no_baseline_set(
    plan: NoBaselineSetPlan,
    inputs: NoBaselineAuditInputs,
    *,
    audit: Callable[
        [Path, NoBaselineAuditInputs], NoBaselineCompareResult
    ] = audit_no_baseline_candidate,
    notify: Callable[[str], None] | None = None,
) -> NoBaselineSetResult:
    """Audit every selected member of *plan*, sequentially, and record it.

    The error taxonomy mirrors the two-sided release path
    (``frontends/cli/release_member_errors.py``): an
    :class:`~abicheck.errors.UnsupportedArtifactError` (or a snapshot newer
    than this reader) makes the member ``unsupported`` -- the completeness
    axis; anything else -- a :class:`~abicheck.errors.ValidationError`
    included -- is an operational failure recorded against that member,
    exactly as the release fan-out records it (``"Error comparing ...``,
    verdict ``ERROR``). A ``ValidationError`` raised *here* is about this
    member's own content (e.g. a file whose format cannot be detected): the
    invocation-wide options were already validated before the loop, so
    aborting would let one unreadable file hide every other member's audit.
    No member is ever dropped: each selected member gets exactly one
    :class:`MemberAudit`.
    """
    from ..errors import IncompatibleSnapshotSchemaError, UnsupportedArtifactError

    per_member = member_audit_inputs(plan, inputs)
    audits: list[MemberAudit] = []
    for key in plan.audit_keys:
        path = plan.side.members[key]
        try:
            result = audit(path, per_member)
        except (UnsupportedArtifactError, IncompatibleSnapshotSchemaError) as exc:
            audits.append(
                MemberAudit(
                    member=key,
                    path=path,
                    status=MemberAuditStatus.UNSUPPORTED,
                    reason=str(exc),
                    error_type=type(exc).__name__,
                )
            )
        except Exception as exc:  # noqa: BLE001 -- recorded against the member
            _log.debug("audit of member %s failed", path.name, exc_info=exc)
            audits.append(
                MemberAudit(
                    member=key,
                    path=path,
                    status=MemberAuditStatus.FAILED,
                    reason=str(exc) or type(exc).__name__,
                    error_type=type(exc).__name__,
                )
            )
            if notify is not None:
                notify(f"Error auditing {path.name}: {type(exc).__name__}: {exc}")
        else:
            audits.append(
                MemberAudit(
                    member=key,
                    path=path,
                    status=MemberAuditStatus.COMPLETED,
                    result=result,
                )
            )
    return NoBaselineSetResult(
        plan=plan,
        audits=tuple(audits),
        record=build_no_baseline_set_record(plan, audits),
    )


def build_no_baseline_set_record(
    plan: NoBaselineSetPlan, audits: Sequence[MemberAudit]
) -> ScopeAcquisitionRecord:
    """The one-sided acquisition record of an N-library audit.

    Every member is ``old_present=False``: OLD was declared absent, so no
    member is ever ``not_supplied`` and the record can produce neither a
    proven removal nor a proven addition (both read ``not_supplied`` only).

    * audited and completed -> ``declared_absent`` (the audit *is* the
      completed outcome for a declared-absent OLD, ADR-068 D2);
    * discovered but not named by an explicit selection -> ``out_of_scope``;
    * declared but not produced by the candidate at all, or declared by the
      package inventory and absent after extraction -> ``expected_not_produced``;
    * unclassifiable under ``--dso-only``, captured degraded, or failed
      during its own audit -> ``failed``; an artifact this build cannot
      analyze -> ``unsupported``.
    """
    by_key = {a.member: a for a in audits}
    members_map = plan.side.members
    selection = plan.selection
    declared = selection.members if selection is not None else {}
    keys = set(members_map) | set(plan.failed) | set(plan.unproduced) | set(declared)
    members: list[MemberAcquisition] = []
    for key in sorted(keys):
        present = key in members_map or key in plan.failed or key in plan.unproduced
        path = members_map.get(key)
        display = path.name if path is not None else key
        state: AcquisitionState
        if selection is not None and key not in declared:
            state, reason = (
                AcquisitionState.OUT_OF_SCOPE,
                "discovered but not named in the explicit --select/"
                "--select-required selection",
            )
        elif not present:
            state, reason = (
                AcquisitionState.EXPECTED_NOT_PRODUCED,
                "declared expected member was not produced by the candidate",
            )
        elif key in plan.unproduced:
            state, reason = AcquisitionState.EXPECTED_NOT_PRODUCED, plan.unproduced[key]
        elif key in plan.failed:
            state, reason = AcquisitionState.FAILED, plan.failed[key]
        else:
            audit = by_key.get(key)
            if audit is None:
                state, reason = (
                    AcquisitionState.FAILED,
                    "no audit result was recorded",
                )
            elif audit.status is MemberAuditStatus.COMPLETED:
                state, reason = AcquisitionState.DECLARED_ABSENT, _AUDITED_REASON
            elif audit.status is MemberAuditStatus.UNSUPPORTED:
                state, reason = AcquisitionState.UNSUPPORTED, audit.reason
            else:
                state, reason = AcquisitionState.FAILED, audit.reason
        members.append(
            MemberAcquisition(
                member=key,
                state=state,
                old_present=False,
                new_present=present,
                reason=reason,
                display_name=display if display != key else "",
                required=declared.get(key, True)
                if state is not AcquisitionState.OUT_OF_SCOPE
                else True,
            )
        )
    if selection is not None:
        selection_kind = "declared"
        selection_reason = (
            f"{len(declared)} member(s) explicitly declared via --select/"
            f"--select-required: {len(selection.required_members)} required, "
            f"{len(selection.optional_members)} optional"
        )
    else:
        selection_kind = NO_BASELINE_SELECTION
        selection_reason = (
            f"abicheck compare --no-baseline: every member the candidate "
            f"{plan.operand_kind} ships ({len(members_map)} discovered)"
        )
    return ScopeAcquisitionRecord(
        members=tuple(members),
        old_inventory=SideInventory(
            completeness=InventoryCompleteness.UNPROVEN,
            provenance=OLD_DECLARED_ABSENT_PROVENANCE,
        ),
        new_inventory=plan.inventory,
        selection=selection_kind,
        selection_reason=selection_reason,
    )


@dataclass(frozen=True)
class NoBaselineSetPreviewEntry:
    """One member of the ``--dry-run`` preview: what a real run would do."""

    member: str
    name: str
    would_audit: bool
    required: bool
    note: str


def preview_no_baseline_set(
    operand: Path, *, operand_kind: str, selection: ReleaseSelection | None
) -> tuple[NoBaselineSetPreviewEntry, ...] | None:
    """What a real run would audit, discovered without extracting anything.

    ``None`` for an operand a preview cannot read without side effects -- a
    package archive (extraction) or a stored ``ProjectSnapshot`` package
    (unpacking into temporary directories). A plain directory is discovered
    with the same two primitives the real run's discovery uses
    (``is_supported_compare_input`` + ``build_match_map``), so the preview
    cannot list a member set the run would not also find.
    """
    from ..binary_utils import build_match_map
    from ..classify import is_supported_compare_input
    from .storage import is_project_snapshot_package_dir

    if operand_kind != "directory" or is_project_snapshot_package_dir(operand):
        return None
    files = [p for p in sorted(operand.rglob("*")) if is_supported_compare_input(p)]
    discovered, _warnings = build_match_map(files)
    declared = selection.members if selection is not None else {}
    entries: list[NoBaselineSetPreviewEntry] = []
    for key in sorted(set(discovered) | set(declared)):
        path = discovered.get(key)
        name = path.name if path is not None else key
        if selection is not None and key not in declared:
            entries.append(
                NoBaselineSetPreviewEntry(
                    key,
                    name,
                    False,
                    False,
                    "discovered but not declared -- out of scope",
                )
            )
        elif path is None:
            entries.append(
                NoBaselineSetPreviewEntry(
                    key,
                    name,
                    False,
                    declared.get(key, True),
                    "declared expected member not produced by the candidate",
                )
            )
        else:
            entries.append(
                NoBaselineSetPreviewEntry(
                    key, name, True, declared.get(key, True), "would be audited"
                )
            )
    return tuple(entries)
