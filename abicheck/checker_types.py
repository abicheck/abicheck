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
    Verdict,
)
from .checker_policy import (
    policy_kind_sets as _policy_kind_sets,
)
from .detectors import DetectorResult
from .model import AbiSnapshot
from .policy_file import PolicyFile


@dataclass
class Change:
    kind: ChangeKind
    symbol: str               # mangled name or type name
    description: str          # human-readable
    old_value: str | None = None
    new_value: str | None = None
    source_location: str | None = None   # "header.h:42" if available
    affected_symbols: list[str] | None = None  # exported functions using this type
    caused_by_type: str | None = None    # root type that makes this change redundant
    caused_count: int = 0                # number of derived changes collapsed into this root
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

    path: str                     # file path as given on the CLI
    sha256: str                   # hex digest
    size_bytes: int               # file size in bytes
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
    suppression_file_provided: bool = False  # True when --suppress was passed, even if 0 matched
    detector_results: list[DetectorResult] = field(default_factory=list)
    policy: str = "strict_abi"  # active policy profile; drives breaking/source_breaks/compatible
    policy_file: PolicyFile | None = None  # custom policy with overrides (Bug 4)
    old_metadata: LibraryMetadata | None = None
    new_metadata: LibraryMetadata | None = None
    redundant_changes: list[Change] = field(default_factory=list)  # hidden by redundancy filter
    redundant_count: int = 0
    old_symbol_count: int | None = None  # public exported symbol count in old library
    # Evidence tier and confidence — helps users assess how much trust to
    # place in the verdict.  "high" means multiple evidence sources agree;
    # "low" means key detectors were disabled (e.g., DWARF stripped).
    confidence: Confidence = Confidence.HIGH
    evidence_tiers: list[str] = field(default_factory=list)  # e.g. ["elf", "dwarf", "header"]
    coverage_warnings: list[str] = field(default_factory=list)  # human-readable coverage gaps

    def _effective_kind_sets(
        self,
    ) -> tuple[frozenset[ChangeKind], frozenset[ChangeKind], frozenset[ChangeKind], frozenset[ChangeKind]]:
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
        breaking_set, _, _, _ = self._effective_kind_sets()
        return [c for c in self.changes if c.kind in breaking_set]

    @property
    def source_breaks(self) -> list[Change]:
        """Changes classified as API_BREAK under the active policy."""
        _, api_break_set, _, _ = self._effective_kind_sets()
        return [c for c in self.changes if c.kind in api_break_set]

    @property
    def compatible(self) -> list[Change]:
        """Changes classified as COMPATIBLE under the active policy."""
        _, _, compatible_set, _ = self._effective_kind_sets()
        return [c for c in self.changes if c.kind in compatible_set]

    @property
    def risk(self) -> list[Change]:
        """Changes classified as COMPATIBLE_WITH_RISK under the active policy."""
        _, _, _, risk_set = self._effective_kind_sets()
        return [c for c in self.changes if c.kind in risk_set]


@dataclass(frozen=True)
class DetectorSpec:
    """Specification for a single ABI change detector.

    Renamed from ``_DetectorSpec`` during architecture review Phase 1
    to serve as the official detector interface.
    """
    name: str
    run: Callable[[AbiSnapshot, AbiSnapshot], list[Change]]
    is_supported: Callable[[AbiSnapshot, AbiSnapshot], tuple[bool, str | None]] | None = None

    def support(self, old: AbiSnapshot, new: AbiSnapshot) -> tuple[bool, str | None]:
        if self.is_supported is None:
            return True, None
        return self.is_supported(old, new)
