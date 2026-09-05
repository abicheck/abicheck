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

"""ADR-065 S2: the release fan-out's acquisition record.

``cli_compare_release_helpers._match_release_keys`` pairs libraries by
canonical filename key and used to define ``removed = old_keys - new_keys``
-- one state for a name-normalization miss, a SONAME bump, a failed
extraction, a partial local build, and a genuine deletion. This module
replaces that reading with D1's per-member acquisition record: the raw set
difference still exists (it is what the JSON key ``unmatched_old`` has
always reported, and keeps reporting), but a removal *finding* and exit
``8`` now read :attr:`~abicheck.model.scope_acquisition.
ScopeAcquisitionRecord.proven_removed_members`, which is empty unless the
NEW side's inventory is proven complete (D2).

**D9's narrow-task inference** is applied here, deliberately conservative:
when NEW supplied exactly one artifact and it matched exactly one OLD
member, the run is a *current-artifact* task -- every other OLD member is
``out_of_scope`` and contributes nothing (the "one candidate against a
twelve-variant baseline yields one comparison and eleven out-of-scope
members, zero removals" acceptance case). Any other shape selects every
discovered member on either side, so an unmatched member is
``not_supplied`` and flows into D6's incompleteness outcome. S1 replaces
the filename tier with identity/coordinate selection and a ``--dry-run``
plan view; the record shape it populates is this one.

**Inventory proof in S2.** A stored ``ProjectSnapshot`` package operand
carries its own declared variant composition, which is a trusted complete
inventory for the selected variant; a live directory, a direct file pair,
and an extracted archive (``package.py`` returns directories, never a
declared component inventory -- S3) are all ``UNPROVEN``.

Order-independent by construction: members are emitted in sorted key
order, so the record -- and everything derived from it -- cannot depend on
directory listing order (an ADR-065 acceptance invariant).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    MemberAcquisition,
    ScopeAcquisitionRecord,
    SideInventory,
)
from ..model.snapshot import AbiSnapshot

__all__ = [
    "DIRECT_PAIR_KEY",
    "RELEASE_OPERATIONAL_VERDICTS",
    "ReleaseInventoryEvidence",
    "StrandedLibraryResolution",
    "build_release_scope_record",
    "release_global_ran",
    "release_inventory_evidence",
    "unmatched_names",
]

#: ``_match_release_keys``'s own sentinel key for two bare files.
DIRECT_PAIR_KEY = "__direct_pair__"

#: The per-library ``verdict`` strings that mean "this member did not reach
#: a completed comparison", mapped to the acquisition state each records.
#: ``ERROR`` (a dump/extract/compare crash) and ``not_comparable`` (ADR-050
#: D2's refusal) are both ``FAILED`` -- D8's "also an operational error";
#: ``unsupported`` (an artifact this build cannot analyze at all) is D6's
#: own ``UNSUPPORTED``, an incompleteness signal and nothing more.
RELEASE_OPERATIONAL_VERDICTS: Mapping[str, AcquisitionState] = {
    "ERROR": AcquisitionState.FAILED,
    "not_comparable": AcquisitionState.FAILED,
    "unsupported": AcquisitionState.UNSUPPORTED,
}

_STORED_PACKAGE_PROVENANCE = (
    "stored project snapshot package: the selected variant's declared "
    "composition is a complete inventory"
)
_LIVE_PROVENANCE = (
    "live directory/archive listing: no declared inventory, so absence cannot be proven"
)
_DIRECT_PAIR_PROVENANCE = "direct file pair: the one artifact is the whole scope"


@dataclass(frozen=True)
class ReleaseInventoryEvidence:
    """What each side can prove about its own completeness (D2)."""

    old: SideInventory
    new: SideInventory
    direct_pair: bool = False


@dataclass(frozen=True)
class StrandedLibraryResolution:
    """`cli_compare_release._resolve_stranded_library`'s answer for one
    OLD-only or matched-but-failed member captured into ``--bundle-facts-
    out`` (D8): the snapshot to persist, plus -- when the full dump failed
    and the snapshot is the ELF-only degradation -- *why*, so the storage
    document carries the member's ``failed`` status in-band instead of a
    silently impoverished old side."""

    snapshot: AbiSnapshot
    failure: str | None = None

    @property
    def degraded(self) -> bool:
        return self.failure is not None


def release_inventory_evidence(
    *, old_stored: bool, new_stored: bool, direct_pair: bool = False
) -> ReleaseInventoryEvidence:
    """S2's inventory-proof rule, in one place."""

    def _side(stored: bool) -> SideInventory:
        if direct_pair:
            return SideInventory(
                InventoryCompleteness.UNPROVEN, _DIRECT_PAIR_PROVENANCE
            )
        if stored:
            return SideInventory(
                InventoryCompleteness.PROVEN, _STORED_PACKAGE_PROVENANCE
            )
        return SideInventory(InventoryCompleteness.UNPROVEN, _LIVE_PROVENANCE)

    return ReleaseInventoryEvidence(
        old=_side(old_stored), new=_side(new_stored), direct_pair=direct_pair
    )


def _state_for_result(
    entry: Mapping[str, object] | None,
) -> tuple[AcquisitionState, str]:
    """The acquisition state one matched member's ``library_results``
    entry records -- ``AVAILABLE`` for a completed comparison (any real
    verdict), else the operational verdict's own state and reason."""
    if entry is None:
        return AcquisitionState.FAILED, "no comparison result was recorded"
    verdict = str(entry.get("verdict", ""))
    state = RELEASE_OPERATIONAL_VERDICTS.get(verdict)
    if state is None:
        return AcquisitionState.AVAILABLE, ""
    reason = entry.get("error") if "error" in entry else entry.get("reason")
    text = str(reason) if reason else verdict
    if verdict == "not_comparable":
        text = f"not comparable: {text}"
    return state, text


def build_release_scope_record(
    old_map: Mapping[str, Path],
    new_map: Mapping[str, Path],
    matched_keys: Sequence[str],
    library_results: Sequence[Mapping[str, object]],
    evidence: ReleaseInventoryEvidence,
) -> ScopeAcquisitionRecord:
    """The release's :class:`ScopeAcquisitionRecord` (see module docstring).

    *library_results* entries are keyed by ``old_map[key].name`` (their
    ``"library"`` field), the same join ``write_bundle_facts_out`` performs
    -- a member with no entry at all is recorded ``FAILED`` rather than
    silently ``AVAILABLE``.
    """
    results_by_name: dict[str, Mapping[str, object]] = {}
    for entry in library_results:
        name = entry.get("library")
        if isinstance(name, str):
            results_by_name.setdefault(name, entry)

    matched = set(matched_keys)
    if evidence.direct_pair or list(matched_keys) == [DIRECT_PAIR_KEY]:
        old_path = old_map.get(DIRECT_PAIR_KEY) or next(iter(old_map.values()))
        state, reason = _state_for_result(results_by_name.get(old_path.name))
        return ScopeAcquisitionRecord(
            members=(
                MemberAcquisition(
                    member=DIRECT_PAIR_KEY,
                    state=state,
                    old_present=True,
                    new_present=True,
                    reason=reason,
                    display_name=old_path.name,
                ),
            ),
            old_inventory=evidence.old,
            new_inventory=evidence.new,
            selection="direct_pair",
            selection_reason="two files: the one pair is the whole scope",
        )

    # D9's narrow inference applies only while NEW's inventory is *unproven*:
    # a proven-complete NEW side that lists exactly one member is a
    # one-member release, and its unmatched OLD members are exactly what
    # D2 lets a proof turn into removals -- never demoted to out-of-scope.
    narrow = (
        len(new_map) == 1
        and len(matched) == 1
        and len(old_map) > 1
        and evidence.new.completeness is not InventoryCompleteness.PROVEN
    )
    candidate = next(iter(new_map.values())).name if narrow else ""
    members: list[MemberAcquisition] = []
    for key in sorted(set(old_map) | set(new_map)):
        old_present = key in old_map
        new_present = key in new_map
        display = (old_map[key] if old_present else new_map[key]).name
        if key in matched:
            state, reason = _state_for_result(results_by_name.get(old_map[key].name))
        elif old_present and narrow:
            state, reason = (
                AcquisitionState.OUT_OF_SCOPE,
                f"unselected baseline member: NEW supplied one artifact ({candidate}), "
                "so this run is a current-artifact comparison (ADR-065 D9)",
            )
        elif old_present:
            state, reason = (
                AcquisitionState.NOT_SUPPLIED,
                "no counterpart supplied on NEW; NEW's inventory is not proven "
                "complete, so this is unmatched, not removed (ADR-065 D2)",
            )
        else:
            state, reason = (
                AcquisitionState.NOT_SUPPLIED,
                "no counterpart supplied on OLD; OLD's inventory is not proven "
                "complete, so this is unmatched, not added (ADR-065 D2)",
            )
        if state is AcquisitionState.NOT_SUPPLIED:
            lacking = evidence.new if old_present else evidence.old
            if lacking.completeness is InventoryCompleteness.PROVEN:
                side = "NEW" if old_present else "OLD"
                reason = (
                    f"no counterpart on {side}, whose inventory is proven complete "
                    f"({lacking.provenance})"
                )
        members.append(
            MemberAcquisition(
                member=key,
                state=state,
                old_present=old_present,
                new_present=new_present,
                reason=reason,
                display_name=display if display != key else "",
            )
        )
    if narrow:
        selection, selection_reason = (
            "current_artifact",
            f"NEW supplied exactly one artifact ({candidate}) with exactly one OLD "
            f"counterpart; the other {len(old_map) - 1} OLD member(s) are out of "
            "scope (ADR-065 D9)",
        )
    else:
        selection, selection_reason = (
            "all_expected",
            "every library discovered on either side is selected",
        )
    return ScopeAcquisitionRecord(
        members=tuple(members),
        old_inventory=evidence.old,
        new_inventory=evidence.new,
        selection=selection,
        selection_reason=selection_reason,
    )


def release_global_ran(
    bundle_result: object, matrix_result: object, record: ScopeAcquisitionRecord | None
) -> bool:
    """Whether a release-global (bundle/probe-matrix) comparison genuinely
    ran, for ``run_outcome.compatibility``'s "worst real verdict" fold.

    A probe-matrix result always counts (it compares build configurations,
    independent of any library pair). A bundle result counts unless the run
    completed **no** per-library comparison and the bundle verdict is the
    vacuous ``NO_CHANGE`` (D7: a graph walk over zero compared pairs is not
    evidence of compatibility -- it would float ``compatibility: NO_CHANGE``
    onto a run whose operational outcome says nothing was compared); a real
    bundle *break* over zero pairs is still reported.
    """
    if matrix_result is not None:
        return True
    if bundle_result is None:
        return False
    if record is None or not record.no_comparison_completed:
        return True
    verdict = getattr(getattr(bundle_result, "bundle_verdict", None), "value", None)
    return verdict != "NO_CHANGE"


def unmatched_names(record: ScopeAcquisitionRecord, *, side: str) -> list[str]:
    """The JSON ``unmatched_old``/``unmatched_new`` lists: members present
    on *side* (``"old"``/``"new"``) with no counterpart, whatever their
    state -- the raw set difference, named as what it is."""
    if side == "old":
        return [m.name for m in record.members if m.old_present and not m.new_present]
    return [m.name for m in record.members if m.new_present and not m.old_present]
