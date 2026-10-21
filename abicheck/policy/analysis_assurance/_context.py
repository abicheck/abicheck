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
"""Context-status axes: header, DWARF and L3 build-context evidence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...checker_types import DiffResult
from ...model import AbiSnapshot
from ...model.change_catalog.kinds import ChangeKind

if TYPE_CHECKING:
    from ...buildsource.pack import BuildSourcePack


def _header_context_status(
    result: DiffResult, old: AbiSnapshot, new: AbiSnapshot
) -> tuple[str, list[str]]:
    """Header/API-level evidence status for this comparison.

    Finding (review, P1): the previous implementation used ``or`` to decide
    whether header evidence was "present" -- so a genuinely asymmetric
    comparison (one side header-derived, the other ELF-only/binary-only)
    read as fully evaluated, since only *one* side needs ``from_headers`` for
    the ``or`` to be true. That let a header-snapshot-vs-ELF-only comparison
    report ``header_context_status="clean"`` (no drift finding to raise,
    because the ELF-only side has no headers to drift against) even though
    only half the comparison actually has header/API-level evidence backing
    it. Fixed by checking ``old.from_headers != new.from_headers`` explicitly
    first and reporting the asymmetric case on its own, distinct from both
    "neither side has headers" (``not_evaluated``) and "both sides do, and
    they agree" (``clean``/``drift_detected``).
    """
    old_headers = bool(getattr(old, "from_headers", False))
    new_headers = bool(getattr(new, "from_headers", False))
    if not old_headers and not new_headers:
        return "not_evaluated", []
    if old_headers != new_headers:
        missing = "new" if old_headers else "old"
        return "asymmetric", [
            f"header context asymmetric: the {missing} side carries no "
            "header/API-level evidence (from_headers=False) -- only one "
            "side of this comparison was extracted from headers"
        ]
    drifted = any(
        c.kind == ChangeKind.HEADER_PARSE_CONTEXT_DRIFT
        for c in (
            result.changes
            + result.out_of_surface_changes
            + result.reconciled_changes
            + result.suppressed_changes
        )
    )
    if drifted:
        return "drift_detected", [
            "header_parse_context_drift: at least one header was parsed "
            "under a different build context than the compiled binary"
        ]
    return "clean", []


def _dwarf_context_status(old: AbiSnapshot, new: AbiSnapshot) -> tuple[str, list[str]]:
    """Per-side DWARF/DWARF-advanced evidence status -- the DWARF analogue of
    :func:`_header_context_status`'s presence-then-asymmetry shape.

    Finding (review, P1): ``confidence._detect_evidence_tiers()`` combines
    both sides' DWARF availability with OR semantics
    (``has_dwarf = (old.dwarf is not None and old.dwarf.has_dwarf) or
    (new.dwarf is not None and new.dwarf.has_dwarf)``) when it promotes the
    aggregate ``result.evidence_tier`` to ``DWARF_AWARE`` -- so a comparison
    where only *one* side actually carries usable debug info still reads as
    DWARF-aware overall. That made ``nothing_requested`` false (since
    ``evidence_tier != EvidenceTier.ELF_ONLY``) without recording the
    asymmetry anywhere else this rollup checked, and with no other partial
    predicate tripping, the overall status fell through to ``"complete"`` --
    even though the real DWARF-based detectors explicitly skip their own
    comparison whenever either side lacks usable DWARF
    (``diff_platform.py``'s struct/enum layout diff: "Neither side has
    DWARF -> skip silently" / "Only new has DWARF -> can't compare without
    old baseline -> skip"; ``dwarf_advanced.diff_advanced_dwarf``: "Returns
    [] gracefully if either side has no DWARF"). Struct/enum layout changes
    on the DWARF-lacking side could therefore be silently missed while
    ``--require-complete-analysis`` still exited 0. Fixed by checking each
    side's own ``dwarf.has_dwarf``/``dwarf_advanced.has_dwarf`` directly,
    independent of the OR-combined aggregate tier.

    Finding (review, P1, round 7): the fix above still folded
    ``dwarf.has_dwarf`` and ``dwarf_advanced.has_dwarf`` into ONE combined
    per-side boolean via ``or`` before comparing the two sides -- so an old
    snapshot with only basic ``dwarf`` and a new snapshot with only
    ``dwarf_advanced`` both read as "this side has SOME dwarf evidence", the
    two combined per-side booleans compare equal (``True == True``), and the
    status read ``"clean"``. But ``dwarf`` and ``dwarf_advanced`` are two
    entirely independent evidence channels, each compared by its own,
    separate detector family: ``diff_platform.py``'s struct/enum layout diff
    reads only basic ``dwarf`` and ``dwarf_advanced.diff_advanced_dwarf``
    reads only ``dwarf_advanced`` -- and each detector independently skips
    ITS OWN comparison whenever EITHER side lacks ITS channel specifically,
    regardless of what the other channel has. Old-basic-only +
    new-advanced-only therefore means BOTH detector families silently
    skipped their comparison, on opposite sides, with the OR-combined
    boolean never noticing either gap. Fixed by checking ``dwarf.has_dwarf``
    and ``dwarf_advanced.has_dwarf`` as two independent, per-channel
    asymmetry checks rather than one combined presence check -- either
    channel disagreeing between the two sides now reports ``"asymmetric"``,
    with a note naming which channel(s) specifically.
    """

    from ...model.debug_evidence import debug_info_evidence
    from ...model.dwarf_facts import AdvancedExtractionStatus

    old_ev, new_ev = debug_info_evidence(old), debug_info_evidence(new)
    old_basic, new_basic = old_ev.basic, new_ev.basic
    old_advanced, new_advanced = old_ev.advanced, new_ev.advanced

    # A failed advanced extraction is evidence the run tried and could not read
    # the side. It is reported as incomplete coverage, never as "no debug info".
    failed_sides = [
        (side, snap)
        for side, snap in (("old", old), ("new", new))
        if (adv := snap.declarations.debug_advanced) is not None
        and adv.extraction is AdvancedExtractionStatus.FAILED
    ]
    if failed_sides:
        failed_notes = [
            f"DWARF advanced extraction failed on the {side} side: its facts are "
            "unknown, so dwarf_advanced findings for that side cannot be "
            "established"
            for side, _snap in failed_sides
        ]
        return "asymmetric", failed_notes

    if not (old_basic or new_basic or old_advanced or new_advanced):
        return "not_evaluated", []

    notes: list[str] = []
    if old_basic != new_basic:
        missing = "new" if old_basic else "old"
        notes.append(
            f"DWARF context asymmetric (basic dwarf channel): the {missing} "
            "side carries no usable basic DWARF debug info -- "
            "diff_platform.py's DWARF-based struct/enum layout comparison "
            "was skipped for that side entirely"
        )
    if old_advanced != new_advanced:
        missing = "new" if old_advanced else "old"
        notes.append(
            f"DWARF context asymmetric (dwarf_advanced channel): the "
            f"{missing} side carries no usable DWARF-advanced debug info -- "
            "dwarf_advanced.diff_advanced_dwarf was skipped for that side "
            "entirely"
        )
    if notes:
        return "asymmetric", notes
    return "clean", []


def _l3_context_status(
    old_pack: BuildSourcePack | None, new_pack: BuildSourcePack | None
) -> tuple[str, list[str]]:
    """Per-side L3 ``BuildEvidence`` presence status -- the L3 analogue of
    :func:`_header_context_status`'s/:func:`_dwarf_context_status`'s
    presence-then-asymmetry shape.

    Finding (review, P1): when the baseline carries L3 ``BuildEvidence`` but
    the target has none (or vice versa), ``cli_buildsource_helpers.
    prepare_embedded_build_source()`` already emits a prose-only
    ``EVIDENCE_COVERAGE_ASYMMETRIC``/``layer_coverage_asymmetric`` finding
    explaining that build-only changes were not checked for the side
    lacking L3 evidence -- but nothing in this rollup's own predicates
    examined L3 presence by side, so an otherwise-matching header-scoped
    comparison fell through to ``status="complete"`` regardless, even while
    the report itself carried that incomplete-coverage finding. Fixed by
    checking each side's own ``BuildSourcePack.build_evidence is not None``
    directly, mirroring the same presence-then-asymmetry check
    ``cli_buildsource_helpers._layer_presence`` already uses for L3.
    """
    old_l3 = getattr(old_pack, "build_evidence", None) is not None
    new_l3 = getattr(new_pack, "build_evidence", None) is not None
    if not old_l3 and not new_l3:
        return "not_evaluated", []
    if old_l3 != new_l3:
        missing = "new" if old_l3 else "old"
        return "asymmetric", [
            f"L3 build-evidence context asymmetric: the {missing} side "
            "carries no BuildEvidence at all -- build-only changes were not "
            "checked for that side (see the layer_coverage_asymmetric finding)"
        ]
    return "clean", []
