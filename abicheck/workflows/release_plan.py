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

"""ADR-065 S1: the declared-selection scope builder and the ``--dry-run``
"comparison plan" preview.

Split out of :mod:`abicheck.workflows.release_scope` rather than added to it
-- that module has no ``architecture/debt.yaml`` ``no_growth`` entry yet
(it is a genuinely new-to-the-ledger file, held to the strict 800-line
production cap), and was already close to that cap before this slice. New
S1 behavior therefore gets its own sibling module instead of pushing the
existing one over the cap, the same "prefer extending a split-out module"
guidance the root ``CLAUDE.md`` gives for legacy oversized files, applied
here to a fresh one before it becomes one.

:func:`build_release_plan_from_directories` discovers a plain directory's
comparable inputs the same way the real release fan-out's own
``cli_helpers_compare._collect_release_inputs``/``_build_match_map`` do --
``classify.is_supported_compare_input`` (extract-classified: any file
format `compare` accepts, not only a real ELF shared object) plus
``binary_utils.build_match_map`` (the same version-aware canonical-key
dedup) -- rather than :func:`abicheck.package.discover_shared_libraries`,
which only recognizes ELF shared objects and would silently show an empty
or wrong plan for a directory of ``.json`` snapshots or another supported
non-ELF input (an earlier version of this preview made exactly that
mistake). ``cli_helpers_compare.py`` itself is ``frontends``-classified and
Click-coupled (it turns an ambiguous match into ``click.ClickException``),
so this module calls the two leaf, ``extract``-classified primitives
underneath it directly instead.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..model.release_selection import ReleaseSelection
from ..model.scope_acquisition import (
    AcquisitionState,
    InventoryCompleteness,
    MemberAcquisition,
    ScopeAcquisitionRecord,
)
from .release_scope import ReleaseInventoryEvidence, _state_for_result

__all__ = [
    "ReleasePlan",
    "ReleasePlanEntry",
    "build_release_plan",
    "build_declared_selection_record",
]


def build_declared_selection_record(
    old_map: Mapping[str, Path],
    new_map: Mapping[str, Path],
    matched_keys: Sequence[str],
    library_results: Sequence[Mapping[str, object]],
    evidence: ReleaseInventoryEvidence,
    selection: ReleaseSelection,
    *,
    old_failed: Mapping[str, str] | None,
    new_failed: Mapping[str, str] | None,
) -> ScopeAcquisitionRecord:
    """ADR-065 S1: the ``ScopeAcquisitionRecord`` for an explicit
    :class:`~abicheck.model.release_selection.ReleaseSelection`.

    Shares ``_state_for_result``/the proven-inventory refinement with
    :func:`~abicheck.workflows.release_scope.build_release_scope_record`'s
    own ``all_expected``/``current_artifact`` path rather than
    reimplementing them -- only the partition rule differs (declared vs.
    inferred), not how a matched or unmatched member's state is read.
    """
    old_failed = dict(old_failed or {})
    new_failed = dict(new_failed or {})
    results_by_name: dict[str, Mapping[str, object]] = {}
    for entry in library_results:
        name = entry.get("library")
        if isinstance(name, str):
            results_by_name.setdefault(name, entry)

    matched = set(matched_keys)
    declared = selection.members
    all_keys = (
        set(old_map) | set(new_map) | set(old_failed) | set(new_failed) | set(declared)
    )
    members: list[MemberAcquisition] = []
    for key in sorted(all_keys):
        old_present = key in old_map or key in old_failed
        new_present = key in new_map or key in new_failed
        display = (old_map.get(key) or new_map.get(key) or Path(key)).name
        required = declared.get(key, True)
        if key not in declared:
            state, reason = (
                AcquisitionState.OUT_OF_SCOPE,
                "discovered but not named in the explicit --select/"
                "--select-required selection (ADR-065 S1)",
            )
        elif not old_present and not new_present:
            state, reason = (
                AcquisitionState.EXPECTED_NOT_PRODUCED,
                "declared expected member was not produced on either side (ADR-065 S1)",
            )
        elif key in old_failed or key in new_failed:
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
        elif old_present:
            state, reason = (
                AcquisitionState.NOT_SUPPLIED,
                "declared member has no counterpart on NEW; NEW's inventory "
                "is not proven complete, so this is unmatched, not removed "
                "(ADR-065 D2)",
            )
        else:
            state, reason = (
                AcquisitionState.NOT_SUPPLIED,
                "declared member has no counterpart on OLD; OLD's inventory "
                "is not proven complete, so this is unmatched, not added "
                "(ADR-065 D2)",
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
                required=required
                if state is not AcquisitionState.OUT_OF_SCOPE
                else True,
            )
        )
    required_count = len(selection.required_members)
    optional_count = len(selection.optional_members)
    return ScopeAcquisitionRecord(
        members=tuple(members),
        old_inventory=evidence.old,
        new_inventory=evidence.new,
        selection="declared",
        selection_reason=(
            f"{len(declared)} member(s) explicitly declared via "
            f"--select/--select-required (ADR-065 S1): {required_count} required, "
            f"{optional_count} optional"
        ),
    )


@dataclass(frozen=True)
class ReleasePlanEntry:
    """One member's ``--dry-run`` preview: would it be compared, and why not
    if not (ADR-065 S1)."""

    member: str
    name: str
    would_compare: bool
    old_present: bool
    new_present: bool
    required: bool
    #: ``False`` only for a member discovered on either side but not named
    #: by an explicit :class:`~abicheck.model.release_selection.
    #: ReleaseSelection` -- always ``True`` when no selection was given.
    declared: bool
    note: str


@dataclass(frozen=True)
class ReleasePlan:
    """The ``--dry-run`` plan view: what a real run would pair, before any
    per-library dump/compare ever runs (ADR-065 S1's own deliverable). Not
    a :class:`~abicheck.model.scope_acquisition.ScopeAcquisitionRecord` --
    that type's ``available``/``failed`` states describe a run that already
    happened; a plan only ever knows presence, not outcome."""

    entries: tuple[ReleasePlanEntry, ...]
    #: ``"declared"`` when built from a :class:`ReleaseSelection`, else
    #: ``"discovered"`` (mirrors ``all_expected`` -- every member either
    #: side discovered).
    selection_kind: str

    @property
    def would_compare_members(self) -> tuple[ReleasePlanEntry, ...]:
        return tuple(e for e in self.entries if e.would_compare)

    @property
    def missing_required(self) -> tuple[ReleasePlanEntry, ...]:
        """Required members (declared, or -- with no selection -- every
        discovered member, matching D6's existing default) that would not
        be compared."""
        return tuple(e for e in self.entries if e.required and not e.would_compare)

    @property
    def out_of_scope(self) -> tuple[ReleasePlanEntry, ...]:
        """Members discovered but excluded by an explicit selection."""
        return tuple(e for e in self.entries if not e.declared)


def build_release_plan(
    old_map: Mapping[str, Path],
    new_map: Mapping[str, Path],
    *,
    selection: ReleaseSelection | None = None,
) -> ReleasePlan:
    """The ``--dry-run`` preview of what :func:`~abicheck.workflows.
    release_scope.build_release_scope_record` would later decide, computed
    from *old_map*/*new_map* alone (whatever a dry run could discover
    without running any per-library dump/compare -- for a package operand a
    dry run does not extract, this is *selection* alone with both maps
    empty, so every declared member reads "not yet known"; see the CLI's
    own dry-run renderer for that framing).

    With no *selection*, mirrors ``all_expected``: every discovered key on
    either side is a required candidate pair, matching today's actual
    fan-out behavior exactly (this is a preview, so it must never claim a
    plan the real run would not also produce). With one, every declared key
    plus every discovered key not declared (reported as an explicit
    out-of-scope note) is listed.
    """
    if selection is not None:
        keys = sorted(set(selection.members) | set(old_map) | set(new_map))
    else:
        keys = sorted(set(old_map) | set(new_map))
    entries: list[ReleasePlanEntry] = []
    for key in keys:
        old_present = key in old_map
        new_present = key in new_map
        name = (old_map.get(key) or new_map.get(key) or Path(key)).name
        if selection is not None and key not in selection.members:
            entries.append(
                ReleasePlanEntry(
                    member=key,
                    name=name,
                    would_compare=False,
                    old_present=old_present,
                    new_present=new_present,
                    required=False,
                    declared=False,
                    note="discovered but not declared in the explicit selection "
                    "-- out of scope (ADR-065 S1)",
                )
            )
            continue
        required = selection.members[key] if selection is not None else True
        would_compare = old_present and new_present
        if would_compare:
            note = "matched on both sides -- would be compared"
        elif not old_present and not new_present:
            note = (
                "declared expected member not produced on either side"
                if selection is not None
                else "not discovered on either side"
            )
        elif old_present:
            note = "present only on OLD; no NEW counterpart"
        else:
            note = "present only on NEW; no OLD counterpart"
        entries.append(
            ReleasePlanEntry(
                member=key,
                name=name,
                would_compare=would_compare,
                old_present=old_present,
                new_present=new_present,
                required=required,
                declared=True,
                note=note,
            )
        )
    kind = "declared" if selection is not None else "discovered"
    return ReleasePlan(entries=tuple(entries), selection_kind=kind)


def build_release_plan_from_directories(
    old_dir: Path,
    new_dir: Path,
    *,
    selection: ReleaseSelection | None = None,
) -> ReleasePlan:
    """:func:`build_release_plan` for two plain, on-disk directories -- the
    ``compare --dry-run`` preview's own entry point. Discovers each side the
    same way the real live-directory fan-out does (see module docstring):
    every file under *old_dir*/*new_dir* that ``is_supported_compare_input``
    accepts, canonically keyed by ``build_match_map`` -- so the preview can
    never claim a plan the real run would not also produce. Never extracts a
    package: a caller with a package operand should not call this at all
    (see the CLI renderer's own package-operand branch).
    """
    from ..binary_utils import build_match_map
    from ..classify import is_supported_compare_input

    old_files = [p for p in sorted(old_dir.rglob("*")) if is_supported_compare_input(p)]
    new_files = [p for p in sorted(new_dir.rglob("*")) if is_supported_compare_input(p)]
    old_map, _old_warnings = build_match_map(old_files)
    new_map, _new_warnings = build_match_map(new_files)
    return build_release_plan(old_map, new_map, selection=selection)
