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
when the caller *named* exactly one NEW artifact (a single-file operand,
not a directory or archive that happened to contain one member) and it
matched exactly one OLD member, the run is a *current-artifact* task --
every other OLD member is ``out_of_scope`` and contributes nothing (the
"one candidate against a twelve-variant baseline yields one comparison and
eleven out-of-scope members, zero removals" acceptance case). The intent
has to be explicit in the operand shape: a one-member *discovered* NEW
directory selects every member on either side like any other directory,
so its unmatched OLD members are ``not_supplied`` and flow into D6's
incompleteness outcome -- otherwise a PR-controlled NEW tree could trim
itself to one library and turn ``--on-incomplete-scope block`` into a
clean pass. S1 replaces the filename tier with identity/coordinate
selection and a ``--dry-run`` plan view; the record shape it populates is
this one.

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

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..bundle_facts import BundleFacts
    from ..bundle_manifest import InstantiationManifest

from ..errors import SnapshotError
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
    "build_stored_baseline_scope_record",
    "bundle_analysis_members",
    "out_of_scope_provider_names",
    "release_global_ran",
    "release_inventory_evidence",
    "restrict_bundle_facts",
    "scope_manifest_to_members",
    "stored_degraded_matched_members",
    "stored_side_degraded_members",
    "scoped_bundle_maps",
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
    # ADR-065 D8: a member a stored package marks degraded (its capture's
    # dump failed) -- skipped before comparison, never an ELF-only diff.
    "failed": AcquisitionState.FAILED,
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
    #: The caller named NEW as one explicit artifact (a file operand), the
    #: only shape D9's current-artifact narrowing may read intent from.
    new_single_artifact: bool = False


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
        """Whether the full dump failed and this snapshot is the ELF-only stand-in."""
        return self.failure is not None


def release_inventory_evidence(
    *,
    old_stored: bool,
    new_stored: bool,
    direct_pair: bool = False,
    new_single_artifact: bool = False,
    old_unclassified: Mapping[str, str] | None = None,
    new_unclassified: Mapping[str, str] | None = None,
) -> ReleaseInventoryEvidence:
    """S2's inventory-proof rule, in one place.

    *old_unclassified*/*new_unclassified* (ADR-065 D2, Codex review) name
    the stored members a lossy selection (``--dso-only``) could not
    classify on that side: the declared composition is still complete, but
    the run could not tell which of those members it *selected*, so the
    proof is withheld -- their counterparts on the other side stay
    unmatched, never proven removed/added.
    """

    def _side(stored: bool, unclassified: Mapping[str, str] | None) -> SideInventory:
        """One side's inventory proof from whether it is a stored package (or a direct file pair)."""
        if direct_pair:
            return SideInventory(
                InventoryCompleteness.UNPROVEN, _DIRECT_PAIR_PROVENANCE
            )
        if stored and unclassified:
            return SideInventory(
                InventoryCompleteness.UNPROVEN,
                "stored project snapshot package, but --dso-only could not "
                f"classify {len(unclassified)} declared member(s) "
                f"({', '.join(sorted(unclassified))}), so the proof is withheld",
            )
        if stored:
            return SideInventory(
                InventoryCompleteness.PROVEN, _STORED_PACKAGE_PROVENANCE
            )
        return SideInventory(InventoryCompleteness.UNPROVEN, _LIVE_PROVENANCE)

    return ReleaseInventoryEvidence(
        old=_side(old_stored, old_unclassified),
        new=_side(new_stored, new_unclassified),
        direct_pair=direct_pair,
        new_single_artifact=new_single_artifact,
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
    *,
    old_failed: Mapping[str, str] | None = None,
    new_failed: Mapping[str, str] | None = None,
) -> ScopeAcquisitionRecord:
    """The release's :class:`ScopeAcquisitionRecord` (see module docstring).

    *library_results* entries are keyed by ``old_map[key].name`` (their
    ``"library"`` field), the same join ``write_bundle_facts_out`` performs
    -- a member with no entry at all is recorded ``FAILED`` rather than
    silently ``AVAILABLE``. *old_failed*/*new_failed* (D1) are declared
    members absent from *old_map*/*new_map* because their acquisition on
    that side failed before matching (``--dso-only`` could not classify
    them): recorded ``FAILED``, present on that side, never as an unmatched
    or removed member.
    """
    old_failed = dict(old_failed or {})
    new_failed = dict(new_failed or {})
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

    # D9's narrow inference reads intent from the operand shape only: the
    # caller named one NEW artifact explicitly. Discovered cardinality is
    # not intent -- a one-member NEW directory is a partial release until
    # proven otherwise, and demoting its unmatched OLD members to
    # out-of-scope would let a PR-controlled tree bypass `block`. It also
    # applies only while NEW's inventory is *unproven*: a proven-complete
    # NEW side that lists exactly one member is a one-member release, and
    # its unmatched OLD members are exactly what D2 lets a proof turn into
    # removals -- never demoted to out-of-scope.
    narrow = (
        evidence.new_single_artifact
        and len(new_map) == 1
        and len(matched) == 1
        and len(old_map) > 1
        and evidence.new.completeness is not InventoryCompleteness.PROVEN
    )
    candidate = next(iter(new_map.values())).name if narrow else ""
    members: list[MemberAcquisition] = []
    for key in sorted(set(old_map) | set(new_map) | set(old_failed) | set(new_failed)):
        old_present = key in old_map or key in old_failed
        new_present = key in new_map or key in new_failed
        display = (old_map.get(key) or new_map.get(key) or Path(key)).name
        if key in old_failed or key in new_failed:
            state, reason = (
                AcquisitionState.FAILED,
                "; ".join(
                    f"{side}: {why}"
                    for side, why in (
                        ("OLD", old_failed.get(key)),
                        ("NEW", new_failed.get(key)),
                    )
                    if why is not None
                ),
            )
        elif key in matched:
            state, reason = _state_for_result(results_by_name.get(old_map[key].name))
        elif old_present and narrow:
            state, reason = (
                AcquisitionState.OUT_OF_SCOPE,
                f"unselected baseline member: NEW named one artifact ({candidate}) "
                "explicitly, so this run is a current-artifact comparison (ADR-065 D9)",
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
            f"NEW named exactly one artifact ({candidate}) explicitly, with exactly "
            f"one OLD counterpart; the other {len(old_map) - 1} OLD member(s) are "
            "out of scope (ADR-065 D9)",
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


def build_stored_baseline_scope_record(
    old_keys: Iterable[str],
    new_keys: Iterable[str],
    *,
    compared: Iterable[str],
    degraded: Mapping[str, str],
    old_provenance: str,
    new_provenance: str,
    new_single_artifact: bool = False,
    unsupported: Mapping[str, str] | None = None,
) -> ScopeAcquisitionRecord:
    """The record for a stored-baseline driver (`bundle_side_input` /
    `bundle_stored_pair_compare`), through the same builder the live
    fan-out uses so D9's narrowing and D2's reading are one rule.

    *compared* are the matched keys whose per-library diff ran; *degraded*
    maps a matched key skipped for a D8 marker (on either side) to the
    reason, recorded `failed`. Both inventories are unproven in S2: a
    captured `BundleFacts` document records what was captured, not that
    the capture was complete (S3 owns declared inventories).
    *new_single_artifact* is the stored/live driver's "NEW was named as one
    file" signal, the only shape D9's narrowing may read intent from.
    *unsupported* maps a matched key whose NEW artifact this build cannot
    analyze (an unsupported container format, a stored snapshot newer than
    this reader) to the reason, recorded `unsupported` -- the same state
    the live fan-out's per-member handler assigns (D6).
    """
    old_map = {k: Path(k) for k in old_keys}
    new_map = {k: Path(k) for k in new_keys}
    matched = sorted(set(old_map) & set(new_map))
    unsupported = dict(unsupported or {})
    compared_set = set(compared)
    results: list[Mapping[str, object]] = []
    for k in matched:
        if k in degraded:
            results.append({"library": k, "verdict": "ERROR", "error": degraded[k]})
        elif k in unsupported:
            results.append(
                {"library": k, "verdict": "unsupported", "reason": unsupported[k]}
            )
        elif k in compared_set:
            results.append({"library": k, "verdict": "NO_CHANGE"})
    evidence = ReleaseInventoryEvidence(
        old=SideInventory(InventoryCompleteness.UNPROVEN, old_provenance),
        new=SideInventory(InventoryCompleteness.UNPROVEN, new_provenance),
        new_single_artifact=new_single_artifact,
    )
    return build_release_scope_record(old_map, new_map, matched, results, evidence)


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


def bundle_analysis_members(record: ScopeAcquisitionRecord) -> frozenset[str]:
    """The members bundle-level (cross-library) analysis may see: every
    matched member whose own comparison *completed* (``available``), plus a
    *proven* removal or addition.

    An unmatched member whose lacking side's inventory is unproven, and an
    ``out_of_scope`` member, are absent from the bundle graph rather than
    present on one side only -- otherwise ``BUNDLE_LIBRARY_REMOVED`` and the
    intra-bundle dependency-removal detectors would read a partial local
    build (or a deliberately narrowed comparison) as a provider deleted from
    the release and score it breaking, which is exactly the D2 reading this
    record replaces (Codex review). A *matched* member that never reached a
    completed comparison (``unsupported``, ``failed``) is excluded for the
    same reason: its NEW artifact is not usable bundle evidence (an
    unsupported container is dropped by the live ELF parse, a degraded
    capture is an ELF-only stand-in), so keeping it would make the OLD
    provider look deleted (Codex review, second finding). What such a
    member *is* -- unchecked -- is carried by the completeness axis instead.
    """
    keep = {m.member for m in record.members if m.state is AcquisitionState.AVAILABLE}
    keep.update(m.member for m in record.proven_removed_members)
    keep.update(m.member for m in record.proven_added_members)
    return frozenset(keep)


def out_of_scope_provider_names(
    record: ScopeAcquisitionRecord | None,
) -> tuple[str, ...]:
    """The names (canonical key and on-disk filename) of every member the
    bundle graph does not hold (the complement of
    :func:`bundle_analysis_members`), for the bundle analysis' external-
    provider allow-list: a surviving consumer whose ``DT_NEEDED`` names an
    unchecked member is depending on something *outside this run's scope*,
    not on a provider the release dropped -- so its unresolved import is not
    ``BUNDLE_INTRA_DEP_REMOVED`` (the same detector Codex named alongside
    ``BUNDLE_LIBRARY_REMOVED``). A proven removal stays in the graph and is
    not listed here, so that consumer's break is still reported.
    """
    if record is None:
        return ()
    keep = bundle_analysis_members(record)
    names: list[str] = []
    for m in record.members:
        if m.member in keep:
            continue
        names.append(m.member)
        if m.name != m.member:
            names.append(m.name)
    return tuple(names)


def scoped_bundle_maps(
    old_map: Mapping[str, Path],
    new_map: Mapping[str, Path],
    record: ScopeAcquisitionRecord | None,
) -> tuple[dict[str, Path], dict[str, Path]]:
    """*old_map*/*new_map* restricted to :func:`bundle_analysis_members`
    (both returned unchanged when there is no record)."""
    if record is None:
        return dict(old_map), dict(new_map)
    keep = bundle_analysis_members(record)
    return (
        {k: v for k, v in old_map.items() if k in keep},
        {k: v for k, v in new_map.items() if k in keep},
    )


def restrict_bundle_facts(
    facts: BundleFacts, record: ScopeAcquisitionRecord
) -> BundleFacts:
    """A copy of *facts* carrying only :func:`bundle_analysis_members` --
    the stored-side counterpart of :func:`scoped_bundle_maps` for a driver
    whose OLD (or NEW) bundle is reconstructed from a ``BundleFacts``
    document. The captured manifest is scoped the same way
    (:func:`scope_manifest_to_members`), so a driver falling back to
    ``facts.manifest`` cannot enforce a promise the retained members were
    never the ones to answer."""
    members = bundle_analysis_members(record)
    manifest, _note = scope_manifest_to_members(facts.manifest, record)
    if members >= set(facts.per_library_snapshots) and manifest is facts.manifest:
        return facts
    return replace(
        facts,
        per_library_snapshots={
            k: v for k, v in facts.per_library_snapshots.items() if k in members
        },
        library_filenames={
            k: v for k, v in facts.library_filenames.items() if k in members
        },
        degraded_members={
            k: v for k, v in facts.degraded_members.items() if k in members
        },
        manifest=manifest,
    )


def scope_manifest_to_members(
    manifest: InstantiationManifest | None,
    record: ScopeAcquisitionRecord | None,
) -> tuple[InstantiationManifest | None, str | None]:
    """*manifest* restricted to the promises the retained bundle members
    can answer, plus a note naming what was withheld (``None`` when nothing
    was).

    The manifest drift check asks "does the NEW bundle still provide each
    promise?", and the bundle graph sees :func:`bundle_analysis_members`
    only. Once any expected member is missing from that graph -- unchecked
    (D6) or deliberately out of scope -- an unanswered promise is
    undecidable: the excluded member may be the one providing it, so
    ``BUNDLE_MANIFEST_INSTANTIATION_REMOVED``/``_ADDED`` would be a
    manufactured finding (ADR-065 D2, Codex review). Only an entry pinned
    to a *retained* provider (``optional_provider=False`` naming it) is
    still decidable by that provider alone and stays. A complete scope
    keeps the manifest untouched. The withheld entries are named in the
    returned note for ``BundleDiffResult.analysis_errors``; the excluded
    members themselves are already on the completeness axis.
    """
    if manifest is None or record is None:
        return manifest, None
    keep = bundle_analysis_members(record)
    excluded = [m for m in record.members if m.member not in keep]
    if not excluded:
        return manifest, None
    from .extraction import _canonical_library_key

    retained_names = {m.member for m in record.members if m.member in keep}
    retained_names |= {m.name for m in record.members if m.member in keep}

    def _pinned_to_retained(library: str | None) -> bool:
        if library is None:
            return False
        return (
            library in retained_names
            or _canonical_library_key(Path(library)) in retained_names
        )

    kept = tuple(
        e
        for e in manifest.entries
        if not e.optional_provider and _pinned_to_retained(e.library)
    )
    withheld = [e.display_name() for e in manifest.entries if e not in kept]
    if not withheld:
        return manifest, None
    note = (
        "manifest drift check withheld for "
        f"{len(withheld)} promise(s) ({', '.join(withheld)}): "
        f"{len(excluded)} member(s) absent from bundle analysis "
        f"({', '.join(m.name for m in excluded)}) may provide them (ADR-065 D2)"
    )
    return (replace(manifest, entries=kept) if kept else None), note


def stored_side_degraded_members(
    side_dir: Path, *, variant_id: str | None
) -> dict[str, str]:
    """One side's own persisted ADR-065 D8 marker, ``{release match key:
    reason}`` -- non-empty only for a stored ``ProjectSnapshot`` package
    (a live directory or archive carries none)."""
    from .release_package import resolve_release_package_degraded_members
    from .storage import is_project_snapshot_package_dir

    if not (side_dir.is_dir() and is_project_snapshot_package_dir(side_dir)):
        return {}
    try:
        return resolve_release_package_degraded_members(side_dir, variant_id=variant_id)
    except (SnapshotError, OSError, ValueError, TypeError, KeyError) as exc:
        # Fail closed (Codex review): a damaged marker section must not
        # read as "no member is degraded".
        raise SnapshotError(
            f"{side_dir}: the stored package's degraded-member marker could not "
            f"be read ({exc}); refusing to compare its members as complete evidence"
        ) from exc


def stored_degraded_matched_members(
    old_dir: Path,
    new_dir: Path,
    matched_keys: Iterable[str],
    *,
    old_variant: str | None,
    new_variant: str | None,
) -> dict[str, str]:
    """``{matched key: reason}`` for every matched member either side's
    stored ``ProjectSnapshot`` package marks degraded (ADR-065 D8), so the
    fan-out skips it and records it ``failed`` -- the same treatment the
    stored/stored and stored/live drivers give the marker. A live directory
    or archive side carries no marker and contributes nothing.
    """
    found: dict[str, str] = {}
    for label, side_dir, variant in (
        ("OLD", old_dir, old_variant),
        ("NEW", new_dir, new_variant),
    ):
        degraded = stored_side_degraded_members(side_dir, variant_id=variant)
        for key, reason in degraded.items():
            found.setdefault(
                key,
                f"{label} side was captured degraded ({reason}); comparison skipped (ADR-065 D8)",
            )
    matched = set(matched_keys)
    return {k: v for k, v in found.items() if k in matched}


def unmatched_names(record: ScopeAcquisitionRecord, *, side: str) -> list[str]:
    """The JSON ``unmatched_old``/``unmatched_new`` lists: members present
    on *side* (``"old"``/``"new"``) with no counterpart, whatever their
    state -- the raw set difference, named as what it is."""
    if side == "old":
        return [m.name for m in record.members if m.old_present and not m.new_present]
    return [m.name for m in record.members if m.new_present and not m.old_present]
