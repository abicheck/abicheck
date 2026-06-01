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

"""Canonical summary metric computation for all report formats."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .checker import _BREAKING_KINDS, DiffResult
from .checker_policy import HasKind

# RTTI artifact prefixes (Itanium ABI): vtables, VTT, typeinfo objects/names,
# and virtual/covariant thunks. Churn in these mirrors churn in their owning
# type rather than representing independent public-API breaks.
_RTTI_PREFIXES = ("_ZTV", "_ZTT", "_ZTI", "_ZTS", "_ZTc", "_ZTh", "_ZTv")
# Length-prefixed Itanium namespace components for the conventional internal
# namespaces (``<len><name>``). Matching the length prefix avoids false hits on
# unrelated identifiers that merely contain the substring.
_INTERNAL_COMPONENTS = ("8internal", "6detail", "4impl", "8__detail", "5_impl")


def classify_symbol_origin(symbol: str) -> str:
    """Best-effort origin of a (usually mangled) symbol: 'rtti'/'internal'/'public'.

    Used to explain why a large C++ ``breaking`` count is dominated by churn in
    RTTI artifacts or internal-namespace symbols rather than genuine public-API
    breaks (a common pattern in libraries built without ``-fvisibility=hidden``).
    """
    if symbol.startswith(_RTTI_PREFIXES):
        return "rtti"
    if any(comp in symbol for comp in _INTERNAL_COMPONENTS):
        return "internal"
    return "public"


@dataclass(frozen=True)
class SurfaceBreakdown:
    """Split of a change set by symbol origin (see ``classify_symbol_origin``)."""
    total: int
    rtti: int
    internal: int
    public: int


def surface_breakdown(changes: Sequence[HasKind]) -> SurfaceBreakdown:
    """Classify *changes* by the origin of their symbol."""
    rtti = internal = public = 0
    for c in changes:
        origin = classify_symbol_origin(getattr(c, "symbol", "") or "")
        if origin == "rtti":
            rtti += 1
        elif origin == "internal":
            internal += 1
        else:
            public += 1
    return SurfaceBreakdown(total=rtti + internal + public, rtti=rtti,
                            internal=internal, public=public)


@dataclass(frozen=True)
class ReportSummary:
    breaking: int
    source_breaks: int
    risk_count: int
    compatible_additions: int
    total_changes: int
    binary_compatibility_pct: float
    affected_pct: float


@dataclass(frozen=True)
class CompatibilityMetrics:
    breaking_count: int
    binary_compatibility_pct: float
    affected_pct: float


def compatibility_metrics(
    changes: Sequence[HasKind], old_symbol_count: int | None = None
) -> CompatibilityMetrics:
    """Compute canonical ABICC-style binary compatibility counters/percentages."""
    breaking_count = sum(1 for c in changes if c.kind in _BREAKING_KINDS)

    if breaking_count == 0:
        bc_pct = 100.0
    elif old_symbol_count and old_symbol_count > 0:
        bc_pct = max(0.0, (old_symbol_count - breaking_count) / old_symbol_count * 100)
    else:
        total = len(changes)
        bc_pct = max(0.0, (total - breaking_count) / total * 100) if total > 0 else 0.0

    if old_symbol_count and old_symbol_count > 0:
        affected_pct = min(100.0, breaking_count / old_symbol_count * 100)
    else:
        affected_pct = 0.0

    return CompatibilityMetrics(
        breaking_count=breaking_count,
        binary_compatibility_pct=bc_pct,
        affected_pct=affected_pct,
    )


def build_summary(result: DiffResult) -> ReportSummary:
    metrics = compatibility_metrics(result.changes, result.old_symbol_count)
    return ReportSummary(
        breaking=len(result.breaking),
        source_breaks=len(result.source_breaks),
        risk_count=len(result.risk),
        compatible_additions=len(result.compatible),
        total_changes=len(result.changes),
        binary_compatibility_pct=metrics.binary_compatibility_pct,
        affected_pct=metrics.affected_pct,
    )
