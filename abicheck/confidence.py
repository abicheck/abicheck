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

from typing import TYPE_CHECKING

from .checker_policy import Confidence, EvidenceTier
from .detectors import DetectorResult
from .model import AbiSnapshot

if TYPE_CHECKING:
    from .checker_types import DiffResult

__all__ = [
    "compute_confidence",
    "_compute_confidence",
    "_detect_evidence_tiers",
    "_determine_evidence_tier",
    "_determine_confidence_level",
    "note_if_same_binary_compared",
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


#: Substring every ``note_if_same_binary_compared`` message shares, regardless
#: of which variant fires -- the one stable marker a consumer can filter
#: `coverage_warnings` on to isolate this specific warning from the rest
#: (detector-disabled notices, missing-metadata notes), used by both
#: ``cli_compare_options.echo_coverage_warnings``'s ``--profile quick`` filter
#: and ``junit_coverage_warnings``'s JUnit rendering.
SAME_BINARY_WARNING_MARKER = "byte-identical"


def note_if_same_binary_compared(result: DiffResult) -> None:
    """Append an L0 coverage warning when *result*'s two compared binaries
    are byte-for-byte identical.

    A comparison against the identical file content necessarily reports
    ``NO_CHANGE`` (there is nothing to diff), and that is the *correct*
    verdict for the bytes actually given -- but it silently reads the same
    as "these two builds genuinely have no ABI-visible differences" from
    the report alone. A user who intended to compare two distinct
    releases and instead passed a stale/duplicate artifact (a build that
    didn't actually rerun, a symlink resolving both `--old`/`--new` to the
    same file, a packaging step that copied the wrong binary) gets a clean
    report with no signal that the comparison itself couldn't have caught
    anything either way -- the under-reporting is silent specifically
    because the correct verdict and the "nothing was actually compared"
    case are indistinguishable without this warning.

    Uses ``LibraryMetadata.sha256`` (populated post-``compare()`` by each
    caller that has real file paths -- ``cli._finalize_compare_result``,
    ``service_compare_pipeline.classify_compare_pair``) rather than any
    ELF-level identity (build-id, soname, symbol-table digest): the sha256
    is the only signal available that is unconditionally exact regardless
    of binary format (ELF/PE/Mach-O) or whether the snapshot carries ELF
    metadata at all, and it needs no new model field or extraction work.
    A no-op whenever either side's metadata is absent (a pure two-snapshot
    Python-API comparison never populates ``old_metadata``/
    ``new_metadata`` at all) or the two digests differ.

    Idempotent and additive: appends to the existing
    ``DiffResult.coverage_warnings`` list already surfaced by every
    report format (JSON/SARIF/text/HTML/Markdown), so this needed no new
    field on the ADR-061 no-growth-baselined ``DiffResult``/``checker.py``.
    """
    old_meta, new_meta = result.old_metadata, result.new_metadata
    if old_meta is None or new_meta is None:
        return
    if old_meta.sha256 != new_meta.sha256:
        return
    # The two *binaries* being byte-identical says nothing about whether a
    # real change could still be caught: a comparison that also analyzed
    # header/AST evidence (e.g. --old-header/--new-header, --build-info,
    # or --sources pointing at genuinely different content than what
    # produced this identical .so/.dll/.dylib) can still detect a real
    # API/source-level difference even though the binary content is the
    # same -- so the stronger "this comparison cannot detect a change"
    # claim is only true when no such evidence was in play (Codex review,
    # fresh evidence: the original wording overclaimed for exactly this
    # case).
    # Also true whenever the comparison already produced a real finding:
    # L3-L5 build/source-pack evidence can detect and report a change
    # without ever setting "header" in evidence_tiers (that list only
    # reflects snapshot-level elf/dwarf/header/pe/macho facts), so a
    # non-empty result.changes directly contradicts "cannot detect a
    # change" regardless of which tier produced it (Codex review, fresh
    # evidence).
    header_evidence_used = "header" in result.evidence_tiers or bool(result.changes)
    if header_evidence_used:
        detection_note = (
            "any ABI/API difference this run could still catch would have "
            "to come from the header/build evidence supplied alongside "
            "these binaries, not from the binaries themselves"
        )
    else:
        detection_note = (
            "this comparison cannot detect a change even if one was "
            "intended -- verify the correct build artifacts were provided"
        )
    result.coverage_warnings.append(
        f"old and new binaries are {SAME_BINARY_WARNING_MARKER} (sha256 "
        f"{old_meta.sha256[:12]}...); {detection_note}"
    )
