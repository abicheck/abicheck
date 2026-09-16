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

"""What evidence a comparison had, and what it could not check.

Report-owned (ADR-061) projection of the evidence facts an already-serialized
report carries -- ``confidence``, ``evidence_tier``/``evidence_tiers``,
``coverage_warnings``, and the per-detector applicability ledger
``detectors[]`` (``not_evaluated`` / ``coverage_gap``, ADR-067 D3). Every
field here is *read*; nothing is recomputed, inferred from another field, or
defaulted to a reassuring value.

Three rules this module enforces on its consumers by construction:

* **Absence is absence.** A report that states no ``confidence`` yields
  :attr:`EvidenceSummary.confidence` ``None``. There is no "high" default:
  inventing one would be the single worst failure mode a reporting layer
  has, because it is indistinguishable from a real high-confidence run.
* **"Did not run" is not "found nothing".** ``detectors[]``'s
  ``not_evaluated`` flag exists precisely because ``changes_count: 0``
  cannot tell those apart, so it is carried through as its own list rather
  than folded into a count.
* **Missing optional evidence is not a break.** Nothing here contributes to
  a verdict, a gate, or an exit code; it is descriptive only. A renderer
  showing "no header/AST data" is stating a limit on what was checked, not
  a finding against the library.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ..confidence import detector_disablement_warning

#: Human-readable label per ``evidence_tiers`` token, where the raw token is
#: not already self-explanatory to a maintainer. An unrecognised tier is
#: rendered verbatim, never dropped.
EVIDENCE_TIER_LABELS: dict[str, str] = {
    "L0": "L0 symbols",
    "L1": "L1 debug info",
    "L2": "L2 headers",
    "L3": "L3 build data",
    "L4": "L4 source facts",
    "L5": "L5 source graph",
    "elf": "ELF",
    "pe": "PE/COFF",
    "macho": "Mach-O",
    "dwarf": "DWARF",
    "dwarf_advanced": "DWARF (advanced)",
    "header": "public headers",
    "btf": "BTF",
    "ctf": "CTF",
    "pdb": "PDB",
    "headers": "headers",
    "symbols": "symbols",
}


@dataclass(frozen=True)
class DetectorGap:
    """One detector's stated applicability, as the producer recorded it."""

    name: str
    #: ``True`` when the detector's ``requires_support`` gate refused it, so
    #: it never ran (ADR-067 D3). ``False`` means it ran; it may still carry
    #: a ``coverage_gap`` describing partial evidence.
    not_evaluated: bool = False
    #: ``False`` when the detector was disabled for this comparison -- the
    #: PE/Mach-O/kABI/SYCL detectors on an ELF run, permanently. Kept
    #: distinct from :attr:`not_evaluated` rather than merged: "does not
    #: apply to this artifact kind" and "applies, but its gate refused it
    #: here" are different statements about assurance, and only the second
    #: is ever news.
    enabled: bool = True
    #: The producer's own reason text. Rendered verbatim -- this module does
    #: not parse it to recover a semantic state, because the state is already
    #: carried structurally by ``not_evaluated``.
    reason: str = ""


@dataclass(frozen=True)
class EvidenceSummary:
    """Evidence facts read off one serialized report. All fields optional."""

    #: ``"high"``/``"medium"``/``"low"``… exactly as the producer stated it,
    #: or ``None`` when the report carries none. Never defaulted.
    confidence: str | None = None
    #: The single headline analysis depth (``evidence_tier``), or ``None``.
    evidence_tier: str | None = None
    #: Every evidence source the comparison actually had.
    evidence_tiers: tuple[str, ...] = ()
    #: The producer's ``coverage_warnings`` **minus** the routine
    #: detector-disablement notes, which are reported structurally by
    #: :attr:`detector_gaps` instead. See :func:`_material_warnings`.
    coverage_warnings: tuple[str, ...] = ()
    #: The detector-disablement notes that were split out, kept so nothing
    #: the producer said is lost -- only re-filed under the axis it belongs
    #: to.
    detector_disablement_warnings: tuple[str, ...] = ()
    #: Detectors that did not run, or ran with a stated coverage gap.
    detector_gaps: tuple[DetectorGap, ...] = ()

    @property
    def has_limitations(self) -> bool:
        """Whether anything here narrows what the comparison could conclude.

        Deliberately narrower than "there is something here to display".
        This is the predicate a "should this comment post at all?" decision
        uses, so it must mean "a maintainer must act on this".

        * :attr:`confidence` is excluded: it is a *summary* of limitations
          that are themselves listed, not an extra one.
        * :attr:`evidence_tiers` is excluded: having fewer optional layers
          is not by itself a diagnostic (AGENTS.md: "Optional inputs stay
          optional").
        * :attr:`detector_gaps` is excluded: on an ELF comparison the
          PE and Mach-O detectors are legitimately not evaluated, on every
          run, forever. Treating that as actionable would post routine
          inapplicable-detector noise on every clean run -- it is shown when
          a comment is rendered for another reason, never a reason of its
          own. ``coverage_warnings`` is where the producer states the
          limitations it considers material.
        """
        return bool(self.coverage_warnings)

    @property
    def is_empty(self) -> bool:
        return not (
            self.confidence
            or self.evidence_tier
            or self.evidence_tiers
            or self.coverage_warnings
            or self.detector_gaps
        )


def _detector_gaps(report: Mapping[str, object]) -> tuple[DetectorGap, ...]:
    raw = report.get("detectors")
    if not isinstance(raw, list):
        return ()
    gaps: list[DetectorGap] = []
    for det in raw:
        if not isinstance(det, Mapping):
            continue
        not_evaluated = bool(det.get("not_evaluated", False))
        enabled = bool(det.get("enabled", True))
        reason = str(det.get("coverage_gap") or "")
        # `_add_detectors` emits a detector only when it has findings or a
        # coverage gap, so a plain "ran, found nothing" entry never reaches
        # here at all. Filter anyway rather than relying on that: a detector
        # that ran cleanly is not a limitation, and listing one would be
        # exactly the routine inapplicable-detector noise this reporting is
        # required not to post on every clean run.
        if not not_evaluated and enabled and not reason:
            continue
        name = str(det.get("name") or "")
        if not name:
            continue
        gaps.append(
            DetectorGap(
                name=name,
                not_evaluated=not_evaluated,
                enabled=enabled,
                reason=reason,
            )
        )
    return tuple(gaps)


def _material_warnings(
    warnings: tuple[str, ...], gaps: tuple[DetectorGap, ...]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Split ``coverage_warnings`` into (material, detector-disablement).

    A real ELF comparison of two ordinary shared libraries emits eight
    ``coverage_warnings``, every one of them "Detector 'pe' disabled:
    missing PE metadata" and its siblings -- permanent, uninteresting facts
    about an ELF run that are not limitations on what the comparison could
    conclude. Treating the raw list as "material limitations" would put a
    "Limits on what was checked (8)" block, and under ``--on=changes`` a
    whole comment, on every clean Linux PR.

    The split is *structural*, not textual: each disablement note is
    reconstructed from the report's own ``detectors[]`` ledger through
    :func:`~abicheck.confidence.detector_disablement_warning`, the single
    function that formats it in the first place. Nothing here reads the
    prose. A note this cannot account for stays material, which is the safe
    direction: an unrecognised warning is shown, never silently dropped.
    """
    disablement = {
        detector_disablement_warning(g.name, g.reason)
        for g in gaps
        if g.reason and not g.enabled
    }
    material = tuple(w for w in warnings if w not in disablement)
    split_out = tuple(w for w in warnings if w in disablement)
    return material, split_out


def _str_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(str(v) for v in value if v is not None and str(v).strip())


def evidence_summary(report: Mapping[str, object]) -> EvidenceSummary:
    """Project one serialized report's evidence facts. Reads only."""
    confidence = report.get("confidence")
    tier = report.get("evidence_tier")
    gaps = _detector_gaps(report)
    material, disablement = _material_warnings(
        _str_tuple(report.get("coverage_warnings")), gaps
    )
    return EvidenceSummary(
        confidence=str(confidence)
        if isinstance(confidence, str) and confidence
        else None,
        evidence_tier=str(tier) if isinstance(tier, str) and tier else None,
        evidence_tiers=_str_tuple(report.get("evidence_tiers")),
        coverage_warnings=material,
        detector_disablement_warnings=disablement,
        detector_gaps=gaps,
    )


def tier_label(tier: str) -> str:
    """Display label for one ``evidence_tiers`` token (verbatim fallback)."""
    return EVIDENCE_TIER_LABELS.get(tier, tier)
