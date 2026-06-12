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

"""Core data types for checker results.

Extracted from ``checker.py`` to break the circular dependency between
``checker`` and ``suppression`` modules (architecture review Phase 1).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .checker_policy import (
    ChangeKind,
    Confidence,
    EvidenceTier,
    Verdict,
    effective_category,
)
from .checker_policy import (
    policy_kind_sets as _policy_kind_sets,
)
from .detectors import DetectorResult
from .model import AbiSnapshot
from .policy_file import PolicyFile

# Marker appended to a ``SYMBOL_VERSION_ALIAS_CHANGED`` description when the old
# default symbol version is NOT retained as a non-default alias (so consumers of
# the old version fail to resolve). Shared between the producer
# (``diff_platform._diff_symbol_version_aliases``) and the cross-detector dedup
# (``diff_filtering._deduplicate_cross_detector``), which only collapses an
# alias-change into a co-reported node-move in this not-retained case — when the
# old alias IS retained the alias-change is compatible and must survive.
SYMBOL_VERSION_ALIAS_NOT_RETAINED_MARKER = "old version NOT retained as alias"


@dataclass
class Change:
    kind: ChangeKind
    symbol: str  # mangled name or type name
    description: str  # human-readable
    old_value: str | None = None
    new_value: str | None = None
    source_location: str | None = None  # "header.h:42" if available
    affected_symbols: list[str] | None = None  # exported functions using this type
    caused_by_type: str | None = None  # root type that makes this change redundant
    caused_count: int = 0  # number of derived changes collapsed into this root
    # Set by EscalateFrozenNamespaceViolations when the change's symbol /
    # caused_by_type matches a namespace declared as "frozen" in the policy
    # file (`frozen_namespaces:`). Carries the matching glob pattern so the
    # reporter can name the policy. Verdict computation blocks any
    # policy_override that would downgrade a change with this field set.
    frozen_namespace_violation: str | None = None
    # Filled in by the source-location enrichment step from the snapshot's
    # function index — the C++-qualified declared name (e.g.
    # ``mylib::detail::r1::dispatch``) for symbols whose ``symbol`` field
    # carries only the mangled/exported form. ``None`` when no matching
    # Function record was found (e.g. type-level changes). Lets namespace
    # selectors match ``extern "C"`` entries whose export name is unqualified.
    qualified_name: str | None = None
    # Set by FilterNonPublicSurface (ADR-024 §D5.1) when --scope-public-headers
    # demotes this finding off the public surface. Carries a stable reason code
    # (e.g. "not-exported", "non-public-type") for the audit ledger. None for
    # in-surface findings and when scoping is off.
    surface_exclusion_reason: str | None = None
    # Per-finding pattern-aware modulation (ADR-025 A4/D4.1). All default to
    # the no-op state, so a snapshot/diff with no modulation behaves exactly as
    # before.
    # - effective_verdict: when set, overrides this finding's *category* (the
    #   verdict it contributes) — consulted by effective_category() at every
    #   classification site. None = classify by ``kind``.
    # - modulation_reason / modulation_rule: the disclosed reason code and the
    #   rule id that produced the override, for the pattern-modulation ledger.
    # - confidence: this finding's own trust level (distinct from the
    #   verdict-level DiffResult.confidence).
    effective_verdict: Verdict | None = None
    modulation_reason: str | None = None
    modulation_rule: str | None = None
    confidence: Confidence = Confidence.HIGH
    # ADR-033 D9 — which build/source evidence bucket this finding belongs to
    # ("build_context" or "source_only"), set when it is produced from L3/L4/L5
    # evidence. Lets the metrics count *retained* (post-suppression) findings per
    # bucket so the D9 split partitions the reported findings. ``None`` for
    # ordinary artifact-backed findings.
    evidence_category: str | None = None


@dataclass
class LibraryMetadata:
    """File-level metadata for a library artifact (path, hash, size).

    The optional ``tbb_interface_version`` field captures
    ``TBB_INTERFACE_VERSION`` from oneTBB's ``oneapi/tbb/version.h`` when
    a TBB-shaped header set is supplied to the dumper. It is reported as
    a first-class signal in ``appcompat`` so users can spot
    forward-compatibility violations (binary's
    ``TBB_runtime_interface_version()`` < headers' compile-time
    ``TBB_INTERFACE_VERSION``) without having to read the symbol table.
    None when the dumper did not see a TBB version header.
    """

    path: str  # file path as given on the CLI
    sha256: str  # hex digest
    size_bytes: int  # file size in bytes
    tbb_interface_version: int | None = None


@dataclass
class DiffResult:
    old_version: str
    new_version: str
    library: str
    changes: list[Change] = field(default_factory=list)
    verdict: Verdict = Verdict.NO_CHANGE
    suppressed_count: int = 0
    suppressed_changes: list[Change] = field(default_factory=list)  # full audit trail
    suppression_file_provided: bool = (
        False  # True when --suppress was passed, even if 0 matched
    )
    detector_results: list[DetectorResult] = field(default_factory=list)
    policy: str = (
        "strict_abi"  # active policy profile; drives breaking/source_breaks/compatible
    )
    policy_file: PolicyFile | None = None  # custom policy with overrides (Bug 4)
    old_metadata: LibraryMetadata | None = None
    new_metadata: LibraryMetadata | None = None
    redundant_changes: list[Change] = field(
        default_factory=list
    )  # hidden by redundancy filter
    redundant_count: int = 0
    old_symbol_count: int | None = None  # public exported symbol count in old library
    # Evidence tier and confidence — helps users assess how much trust to
    # place in the verdict.  "high" means multiple evidence sources agree;
    # "low" means key detectors were disabled (e.g., DWARF stripped).
    confidence: Confidence = Confidence.HIGH
    evidence_tiers: list[str] = field(
        default_factory=list
    )  # e.g. ["elf", "dwarf", "header"]
    coverage_warnings: list[str] = field(
        default_factory=list
    )  # human-readable coverage gaps
    # ADR-024: findings excluded because they are not on the public-header
    # ABI surface (only populated when scope_to_public_surface is enabled).
    # Recorded for audit — surfaced under --show-filtered — never dropped.
    out_of_surface_changes: list[Change] = field(default_factory=list)
    out_of_surface_count: int = 0
    scope_to_public_surface: bool = False
    # False only when --scope-public-headers was requested but the public
    # surface could not be resolved, so scoping fell back to the full export
    # table. A False value means compatibility is *unconfirmed* and the result
    # needs manual review — it must never read as a confidently-clean public
    # surface (issue #235).
    scope_resolved: bool = True
    # ADR-024 §D5.3 — structured confidence in the surface resolution itself
    # (distinct from ``confidence`` above, which is the overall verdict trust).
    # "high" with no notes = clean header-scoped run; "reduced" with one or more
    # structured note codes (e.g. "mangling-fallback", "no-provenance") when the
    # surface had to be resolved less reliably. Disclosed in the JSON/SARIF
    # surface ledger so the "demote + disclose" promise stays auditable.
    surface_scope_confidence: str = "high"
    surface_scope_notes: list[str] = field(default_factory=list)
    # Canonical analysis depth (ordered): ELF_ONLY < DWARF_AWARE < HEADER_AWARE.
    # Distinct from the raw ``evidence_tiers`` list above — this is the single
    # scalar consumers should key trust decisions off of. See EvidenceTier.
    evidence_tier: EvidenceTier = EvidenceTier.ELF_ONLY
    # ADR-027 A4 — pattern-aware verdict modulation ledger. Each entry records a
    # demotion/raise the pattern pass made (symbol, original→new category, the
    # rule id, the disclosed reason, the evidence tier, and the graph edges that
    # matched). Empty unless --pattern-verdicts was enabled. Findings themselves
    # carry the override on Change.effective_verdict; this is the audit trail.
    pattern_modulations: list[dict[str, object]] = field(default_factory=list)
    # ADR-028 D7 — evidence-coverage rows (L0–L5) for the compare, when an
    # BuildSourcePack was supplied. Each entry is a serialized LayerCoverage
    # ({layer, status, confidence, detail}). Surfaced in the JSON report so
    # machine consumers can tell artifact-proven from build-context-only
    # findings; empty when no evidence was involved.
    layer_coverage: list[dict[str, object]] = field(default_factory=list)
    # ADR-033 D6/D9 — evidence-collection timing and observability metrics for
    # the compare. Populated only when build-info/source facts were involved
    # (mirrors ``layer_coverage``). Keys follow the D9 metric names (e.g.
    # ``extractor.duration_seconds``, ``findings.source_only.count``); surfaced
    # in the JSON report so CI can tune mode selection. Empty otherwise.
    evidence_metrics: dict[str, object] = field(default_factory=dict)

    def _effective_kind_sets(
        self,
    ) -> tuple[
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
        frozenset[ChangeKind],
    ]:
        """Return (breaking, api_break, compatible, risk) kind sets with overrides applied."""
        breaking, api_break, compatible, risk = _policy_kind_sets(self.policy)
        if not self.policy_file or not self.policy_file.overrides:
            return breaking, api_break, compatible, risk

        # Apply overrides: move kinds between sets
        b, a, c, r = set(breaking), set(api_break), set(compatible), set(risk)
        _VERDICT_TO_SET_IDX = {
            Verdict.BREAKING: 0,
            Verdict.API_BREAK: 1,
            Verdict.COMPATIBLE: 2,
            Verdict.COMPATIBLE_WITH_RISK: 3,
        }
        sets = [b, a, c, r]
        for kind, verdict in self.policy_file.overrides.items():
            # Remove from all sets
            for s in sets:
                s.discard(kind)
            # Add to target set
            idx = _VERDICT_TO_SET_IDX.get(verdict)
            if idx is not None:
                sets[idx].add(kind)
        return frozenset(b), frozenset(a), frozenset(c), frozenset(r)

    @property
    def breaking(self) -> list[Change]:
        """Changes classified as BREAKING under the active policy."""
        sets = self._effective_kind_sets()
        return [
            c for c in self.changes if effective_category(c, *sets) == Verdict.BREAKING
        ]

    @property
    def source_breaks(self) -> list[Change]:
        """Changes classified as API_BREAK under the active policy."""
        sets = self._effective_kind_sets()
        return [
            c for c in self.changes if effective_category(c, *sets) == Verdict.API_BREAK
        ]

    @property
    def compatible(self) -> list[Change]:
        """Changes classified as COMPATIBLE under the active policy."""
        sets = self._effective_kind_sets()
        return [
            c
            for c in self.changes
            if effective_category(c, *sets) == Verdict.COMPATIBLE
        ]

    @property
    def risk(self) -> list[Change]:
        """Changes classified as COMPATIBLE_WITH_RISK under the active policy."""
        sets = self._effective_kind_sets()
        return [
            c
            for c in self.changes
            if effective_category(c, *sets) == Verdict.COMPATIBLE_WITH_RISK
        ]


@dataclass(frozen=True)
class DetectorSpec:
    """Specification for a single ABI change detector.

    Renamed from ``_DetectorSpec`` during architecture review Phase 1
    to serve as the official detector interface.
    """

    name: str
    run: Callable[[AbiSnapshot, AbiSnapshot], list[Change]]
    is_supported: (
        Callable[[AbiSnapshot, AbiSnapshot], tuple[bool, str | None]] | None
    ) = None

    def support(self, old: AbiSnapshot, new: AbiSnapshot) -> tuple[bool, str | None]:
        if self.is_supported is None:
            return True, None
        return self.is_supported(old, new)
