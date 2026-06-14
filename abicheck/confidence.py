# Copyright 2026 Nikolay Petrov
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

"""Evidence-tier and confidence computation for a comparison.

This is *orchestration* logic, not filtering: it reads the per-detector results
produced by the registry plus the snapshots' available metadata and collapses
them into the analysis-depth tier, the confidence level, and coverage warnings
attached to the :class:`~abicheck.checker_types.DiffResult`. It previously lived
in ``diff_filtering`` (which only owns dedup/redundancy), forcing a cross-module
hop to follow the ``compare()`` flow; it now sits in its own module that both
``checker`` and the tests import directly.

The module depends only on the snapshot model and the policy enums, so it stays
at the bottom of the dependency graph (no cycle with ``checker``).
"""

from __future__ import annotations

from .checker_policy import Confidence, EvidenceTier
from .detectors import DetectorResult
from .model import AbiSnapshot

__all__ = [
    "compute_confidence",
    "_compute_confidence",
    "_detect_evidence_tiers",
    "_determine_evidence_tier",
    "_determine_confidence_level",
]


def _detect_evidence_tiers(
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> tuple[list[str], bool, bool, bool, bool, bool, bool]:
    """Detect which evidence tiers are available from the snapshots.

    Returns (tiers, has_elf, has_dwarf, has_dwarf_advanced, has_pe, has_macho, has_headers).
    """
    has_elf = old.elf is not None or new.elf is not None
    has_dwarf = (old.dwarf is not None and old.dwarf.has_dwarf) or (
        new.dwarf is not None and new.dwarf.has_dwarf
    )
    has_dwarf_advanced = (
        old.dwarf_advanced is not None and old.dwarf_advanced.has_dwarf
    ) or (new.dwarf_advanced is not None and new.dwarf_advanced.has_dwarf)
    has_pe = (
        getattr(old, "pe", None) is not None or getattr(new, "pe", None) is not None
    )
    has_macho = (
        getattr(old, "macho", None) is not None
        or getattr(new, "macho", None) is not None
    )
    # HEADER_AWARE requires that the surface was actually parsed from public
    # headers (castxml/AST). DWARF-only and symbols-only dumps populate the
    # same functions/types lists, so the mere presence of declarations is not
    # evidence of header analysis — only the ``from_headers`` provenance flag
    # set by the dumper distinguishes them. When a snapshot carries any
    # binary-derived metadata (ELF/PE/Mach-O/DWARF) but no ``from_headers``
    # flag, its surface came from DWARF or the symbol table, not headers.
    # A snapshot with no binary metadata at all is a pure in-memory/header
    # surface (the library-API and unit-test construction path), so the
    # presence of declarations is taken as header-level evidence there.
    from_headers = bool(
        getattr(old, "from_headers", False) or getattr(new, "from_headers", False)
    )
    has_declarations = bool(
        old.functions
        or old.types
        or old.enums
        or old.typedefs
        or old.variables
        or new.functions
        or new.types
        or new.enums
        or new.typedefs
        or new.variables
    )
    has_binary_metadata = (
        has_elf
        or has_pe
        or has_macho
        or has_dwarf
        or has_dwarf_advanced
        or getattr(old, "elf_only_mode", False)
        or getattr(new, "elf_only_mode", False)
    )
    if from_headers:
        has_headers = True
    elif has_binary_metadata:
        has_headers = False
    else:
        has_headers = has_declarations

    tiers: list[str] = []
    if has_elf:
        tiers.append("elf")
    if has_dwarf:
        tiers.append("dwarf")
    if has_dwarf_advanced:
        tiers.append("dwarf_advanced")
    if has_headers:
        tiers.append("header")
    if has_pe:
        tiers.append("pe")
    if has_macho:
        tiers.append("macho")

    return tiers, has_elf, has_dwarf, has_dwarf_advanced, has_pe, has_macho, has_headers


def _determine_evidence_tier(
    has_dwarf: bool,
    has_dwarf_advanced: bool,
    has_headers: bool,
) -> EvidenceTier:
    """Collapse the raw evidence booleans into the canonical analysis tier.

    See :class:`EvidenceTier` for the semantics of each level. Header/AST
    surface always wins (it is the richest signal); DWARF debug info is the
    middle tier; everything else (symbol-table-only ELF/PE/Mach-O) is the
    floor.
    """
    if has_headers:
        return EvidenceTier.HEADER_AWARE
    if has_dwarf or has_dwarf_advanced:
        return EvidenceTier.DWARF_AWARE
    return EvidenceTier.ELF_ONLY


def _determine_confidence_level(
    has_elf: bool,
    has_dwarf: bool,
    has_pe: bool,
    has_macho: bool,
    has_headers: bool,
    detector_results: list[DetectorResult],
    warnings: list[str],
) -> Confidence:
    """Compute the confidence level based on available evidence and detector state.

    Appends appropriate warnings to *warnings* as a side effect.
    """
    if has_headers and (has_elf or has_dwarf or has_pe or has_macho):
        confidence = Confidence.HIGH
    elif has_headers:
        confidence = Confidence.MEDIUM
        if not has_elf and not has_pe and not has_macho:
            warnings.append(
                "No binary metadata available; verdict is based on header analysis only"
            )
    elif has_elf and has_dwarf:
        confidence = Confidence.MEDIUM
        if not has_headers:
            warnings.append("No header/AST data; type-level changes may be missed")
    elif has_elf or has_pe or has_macho:
        confidence = Confidence.LOW
        warnings.append(
            "Binary-only analysis without debug info; many ABI changes "
            "cannot be detected (struct layout, enum values, type changes)"
        )
    else:
        confidence = Confidence.LOW
        warnings.append("Very limited data available; results may be incomplete")

    # DWARF-specific warning: if DWARF is expected but stripped.
    dwarf_detector = next(
        (dr for dr in detector_results if dr.name == "dwarf"),
        None,
    )
    if dwarf_detector and not dwarf_detector.enabled:
        if confidence == Confidence.HIGH:
            confidence = Confidence.MEDIUM

    return confidence


def compute_confidence(
    detector_results: list[DetectorResult],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> tuple[list[str], Confidence, list[str], EvidenceTier]:
    """Compute evidence tiers, confidence level, and coverage warnings.

    Returns (evidence_tiers, confidence, coverage_warnings, evidence_tier).

    ``evidence_tier`` is the canonical, ordered analysis depth (see
    :class:`EvidenceTier`); ``evidence_tiers`` remains the raw list of
    available data sources for backward compatibility.

    Evidence tiers:
    - "elf": ELF metadata present and analyzed
    - "dwarf": DWARF debug info present
    - "header": Header/AST information (functions/types/enums)
    - "pe": PE metadata present
    - "macho": Mach-O metadata present

    Confidence:
    - "high": headers + at least one binary metadata source (ELF/DWARF/PE/Mach-O)
    - "medium": headers only, or binary-only with ELF+DWARF
    - "low": binary-only without DWARF, or very limited data
    """
    tiers, has_elf, has_dwarf, has_dwarf_adv, has_pe, has_macho, has_headers = (
        _detect_evidence_tiers(old, new)
    )

    evidence_tier = _determine_evidence_tier(has_dwarf, has_dwarf_adv, has_headers)

    warnings: list[str] = []

    # Check for disabled detectors and generate warnings.
    for dr in detector_results:
        if not dr.enabled and dr.coverage_gap:
            warnings.append(f"Detector '{dr.name}' disabled: {dr.coverage_gap}")

    confidence = _determine_confidence_level(
        has_elf,
        has_dwarf,
        has_pe,
        has_macho,
        has_headers,
        detector_results,
        warnings,
    )

    return tiers, confidence, warnings, evidence_tier


# Back-compat alias: the function was historically named ``_compute_confidence``
# and imported under that name by checker and tests.
_compute_confidence = compute_confidence
