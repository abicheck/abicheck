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
from typing import TYPE_CHECKING

from .checker import _BREAKING_KINDS, DiffResult
from .checker_policy import HasKind

if TYPE_CHECKING:
    from .severity import KindSets

# Re-exported under its historical name; the implementation (and the prefix
# tables it relies on) now lives in the canonical name_classification module.
# Listed in __all__ so the re-export is explicit (not an implicit re-export).
from .name_classification import symbol_origin as classify_symbol_origin

__all__ = [
    "classify_symbol_origin",
    "SurfaceBreakdown",
    "surface_breakdown",
    "ReportSummary",
    "CompatibilityMetrics",
    "compatibility_metrics",
    "build_summary",
]


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
    changes: Sequence[HasKind],
    old_symbol_count: int | None = None,
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
) -> CompatibilityMetrics:
    """Compute canonical ABICC-style binary compatibility counters/percentages.

    Without *kind_sets*/*policy_file*, ``breaking_count`` counts raw
    ``ChangeKind`` membership in the canonical ``_BREAKING_KINDS`` set —
    which disagrees with a policy-demoted finding's *effective* verdict. A
    removal a policy file demotes to ``COMPATIBLE`` still carries its raw
    ``FUNC_REMOVED`` kind, so the canonical-kind count would report it as
    breaking (e.g. "0.0% binary compatibility") on the same page whose
    verdict banner reads ``COMPATIBLE`` (computed from the *effective*
    verdict) — a direct, visible contradiction. Passing *kind_sets* (from
    ``DiffResult._effective_kind_sets()``) and *policy_file* makes this
    metric agree with the verdict by counting each change's effective
    verdict instead of its raw kind.
    """
    if kind_sets is not None or policy_file is not None:
        from .checker_policy import Verdict as _Verdict
        from .severity import effective_verdict_for_change

        breaking_count = sum(
            1
            for c in changes
            if effective_verdict_for_change(
                c, policy=policy, kind_sets=kind_sets, policy_file=policy_file,
            )
            == _Verdict.BREAKING
        )
    else:
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
    metrics = compatibility_metrics(
        result.changes,
        result.old_symbol_count,
        policy=result.policy,
        kind_sets=result._effective_kind_sets(),
        policy_file=result.policy_file,
    )
    return ReportSummary(
        breaking=len(result.breaking),
        source_breaks=len(result.source_breaks),
        risk_count=len(result.risk),
        compatible_additions=len(result.compatible),
        total_changes=len(result.changes),
        binary_compatibility_pct=metrics.binary_compatibility_pct,
        affected_pct=metrics.affected_pct,
    )
