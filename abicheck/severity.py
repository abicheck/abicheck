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

"""Severity configuration for issue categories.

Builds on top of the existing policy/verdict system in ``checker_policy``
to provide user-facing severity presets that control exit codes and report
presentation.

The canonical kind-set classification lives in ``checker_policy`` (single
source of truth).  This module provides:

- **SeverityLevel** — ``error`` / ``warning`` / ``info``.
- **IssueCategory** — the four high-level categories users interact with.
- **SeverityConfig** — maps each category to a severity level.
- **Presets** — ``default``, ``strict``, ``info-only``.
- **classify_change** / **categorize_changes** — thin wrappers over the
  canonical kind sets.
- **compute_exit_code** — severity-aware exit-code computation.

The four categories map to the canonical kind sets as follows:

1. **abi_breaking** → ``BREAKING_KINDS`` — clear ABI/API incompatibilities.
   Default: ``error``.
2. **potential_breaking** → ``API_BREAK_KINDS ∪ RISK_KINDS`` — potential
   incompatibilities that need manual review.  Default: ``warning``.
3. **quality_issues** → ``QUALITY_KINDS`` (= ``COMPATIBLE_KINDS − ADDITION_KINDS``)
   — problematic behaviors such as exposing std symbols, missing SONAME,
   toolchain flag drift.  Default: ``warning``.
4. **addition** → ``ADDITION_KINDS`` — new public symbols, types, enum
   members.  Default: ``info``.

Each category can be set to ``error``, ``warning``, or ``info``:

- ``error`` — flagged prominently in the report, contributes to non-zero exit code.
- ``warning`` — shown as a warning in the report, does NOT affect exit code.
- ``info`` — informational only, shown in the report but neutral.

Built-in presets:

- ``default`` — abi_breaking=error, potential_breaking=warning,
  quality_issues=warning, addition=info.
- ``strict`` — all categories set to error.
- ``info-only`` — all categories set to info (purely informational report).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

from .checker_policy import (
    ADDITION_KINDS,
    API_BREAK_KINDS,
    BREAKING_KINDS,
    COMPATIBLE_KINDS,
    RISK_KINDS,
    ChangeKind,
    HasKind,
    Verdict,
    effective_category,
    policy_kind_sets,
)
from .errors import PolicyError

#: Pre-computed (breaking, api_break, compatible, risk) kind sets.
KindSets = tuple[
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
]


class SeverityLevel(str, Enum):
    """Criticality level for an issue category."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class IssueCategory(str, Enum):
    """The four high-level issue categories."""

    ABI_BREAKING = "abi_breaking"
    POTENTIAL_BREAKING = "potential_breaking"
    QUALITY_ISSUES = "quality_issues"
    ADDITION = "addition"


# ---------------------------------------------------------------------------
# Change-kind -> IssueCategory classification
# ---------------------------------------------------------------------------
# Delegates entirely to the canonical kind sets in checker_policy.py.
# When a *policy* is provided, uses the policy-adjusted sets so that
# kinds downgraded/upgraded by the policy (e.g. sdk_vendor, plugin_abi)
# are classified correctly.


def _resolve_kind_sets(
    policy: str | None = None,
    kind_sets: KindSets | None = None,
) -> KindSets:
    """Return (breaking, api_break, compatible, risk) kind sets.

    *kind_sets* takes precedence when provided (e.g. from
    ``DiffResult._effective_kind_sets()`` which includes PolicyFile overrides).
    Falls back to ``policy_kind_sets(policy)`` or canonical sets.
    """
    if kind_sets is not None:
        return kind_sets
    if policy is None or policy == "strict_abi":
        return (
            frozenset(BREAKING_KINDS),
            frozenset(API_BREAK_KINDS),
            frozenset(COMPATIBLE_KINDS),
            RISK_KINDS,
        )
    return policy_kind_sets(policy)


def classify_change(
    kind: ChangeKind,
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
) -> IssueCategory:
    """Classify a ChangeKind into one of the four issue categories.

    Uses the canonical kind sets from ``checker_policy`` by default.

    When *kind_sets* is provided (e.g. from ``DiffResult._effective_kind_sets()``),
    those sets are used directly, which includes PolicyFile overrides.

    When only *policy* is provided, uses the built-in policy-adjusted sets.

    Unknown kinds default to ``ABI_BREAKING`` (fail-safe).

    Note: ``ADDITION_KINDS`` and ``QUALITY_KINDS`` are disjoint by
    construction (``QUALITY_KINDS = COMPATIBLE_KINDS - ADDITION_KINDS``),
    so the check order between them does not matter.
    """
    breaking, api_break, compatible, risk = _resolve_kind_sets(policy, kind_sets)
    if kind in breaking:
        return IssueCategory.ABI_BREAKING
    if kind in api_break or kind in risk:
        return IssueCategory.POTENTIAL_BREAKING
    # Within compatible, split into additions vs quality issues.
    if kind in ADDITION_KINDS:
        return IssueCategory.ADDITION
    if kind in compatible:
        return IssueCategory.QUALITY_ISSUES
    # Fail-safe: unclassified kinds are treated as breaking.
    return IssueCategory.ABI_BREAKING


def classify_change_object(
    change: HasKind,
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
) -> IssueCategory:
    """Classify a *change* honouring its per-finding ``effective_verdict`` (A4).

    Routes through :func:`checker_policy.effective_category` — the single place
    a finding's category is decided — so an ADR-027 pattern-aware demotion (a
    ``Change`` carrying ``effective_verdict``) reads compatible in the
    severity-aware exit code and category counts, not just the verdict. Falls
    back to kind-based ``classify_change`` for plain stubs with no override.
    """
    sets = _resolve_kind_sets(policy, kind_sets)
    verdict = effective_category(change, *sets)
    if verdict == Verdict.BREAKING:
        return IssueCategory.ABI_BREAKING
    if verdict in (Verdict.API_BREAK, Verdict.COMPATIBLE_WITH_RISK):
        return IssueCategory.POTENTIAL_BREAKING
    # COMPATIBLE (and the unreachable NO_CHANGE — effective_category only ever
    # returns BREAKING/API_BREAK/RISK/COMPATIBLE): an addition keeps its
    # ADDITION bucket; everything else (including a demoted break, or a
    # hypothetical NO_CHANGE) is a quality issue, never an addition.
    if change.kind in ADDITION_KINDS and change.kind in sets[2]:
        return IssueCategory.ADDITION
    return IssueCategory.QUALITY_ISSUES


# ---------------------------------------------------------------------------
# Severity configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SeverityConfig:
    """Maps each issue category to a criticality level.

    Attributes:
        abi_breaking: Severity for clear ABI/API incompatibilities.
        potential_breaking: Severity for potential incompatibilities needing review.
        quality_issues: Severity for problematic behaviors (e.g., std symbol leaks).
        addition: Severity for additive changes (new public API surface).
    """

    abi_breaking: SeverityLevel = SeverityLevel.ERROR
    potential_breaking: SeverityLevel = SeverityLevel.WARNING
    quality_issues: SeverityLevel = SeverityLevel.WARNING
    addition: SeverityLevel = SeverityLevel.INFO

    def level_for(self, category: IssueCategory) -> SeverityLevel:
        """Return the configured severity level for *category*.

        Works because SeverityConfig field names match IssueCategory values.
        """
        result: SeverityLevel = getattr(self, category.value)
        return result

    def level_for_kind(
        self,
        kind: ChangeKind,
        *,
        policy: str | None = None,
        kind_sets: KindSets | None = None,
    ) -> SeverityLevel:
        """Return the configured severity level for a specific ChangeKind."""
        return self.level_for(classify_change(kind, policy=policy, kind_sets=kind_sets))

    def has_errors(
        self,
        changes: Sequence[HasKind],
        *,
        policy: str | None = None,
        kind_sets: KindSets | None = None,
    ) -> bool:
        """Return True if any change falls into a category configured as error."""
        return any(
            self.level_for_kind(c.kind, policy=policy, kind_sets=kind_sets)
            == SeverityLevel.ERROR
            for c in changes
        )

    def describe(self, *, prefix: str = "", title: str | None = None) -> str:
        """Human-readable summary of this configuration.

        Args:
            prefix: String prepended to each line (e.g. indentation).
            title: Optional title line printed before the category listing.
        """
        lines: list[str] = []
        if title is not None:
            lines.append(f"{prefix}{title}")
        for cat in IssueCategory:
            lines.append(f"{prefix}  {cat.value}: {self.level_for(cat).value}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------

#: Default preset: breaks are errors, potential issues and quality are warnings,
#: additions are informational.
PRESET_DEFAULT = SeverityConfig()

#: Strict preset: everything is an error.
PRESET_STRICT = SeverityConfig(
    abi_breaking=SeverityLevel.ERROR,
    potential_breaking=SeverityLevel.ERROR,
    quality_issues=SeverityLevel.ERROR,
    addition=SeverityLevel.ERROR,
)

#: Info-only preset: everything is informational (no exit-code impact).
PRESET_INFO_ONLY = SeverityConfig(
    abi_breaking=SeverityLevel.INFO,
    potential_breaking=SeverityLevel.INFO,
    quality_issues=SeverityLevel.INFO,
    addition=SeverityLevel.INFO,
)

SEVERITY_PRESETS: dict[str, SeverityConfig] = {
    "default": PRESET_DEFAULT,
    "strict": PRESET_STRICT,
    "info-only": PRESET_INFO_ONLY,
    "info_only": PRESET_INFO_ONLY,  # underscore alias for programmatic use
}


def resolve_severity_config(
    preset: str | None = None,
    *,
    abi_breaking: str | None = None,
    potential_breaking: str | None = None,
    quality_issues: str | None = None,
    addition: str | None = None,
) -> SeverityConfig:
    """Build a SeverityConfig from a preset name and optional per-category overrides.

    Per-category overrides take precedence over the preset.

    Args:
        preset: One of ``default``, ``strict``, ``info-only``, or *None* for default.
        abi_breaking: Override for the abi_breaking category (``error``, ``warning``, ``info``).
        potential_breaking: Override for potential_breaking.
        quality_issues: Override for quality_issues.
        addition: Override for addition.

    Returns:
        A fully resolved SeverityConfig.

    Raises:
        ValueError: If the preset name or any override value is invalid.
    """
    if preset is None:
        base = PRESET_DEFAULT
    else:
        looked_up = SEVERITY_PRESETS.get(preset)
        if looked_up is None:
            raise PolicyError(
                f"Unknown severity preset {preset!r}. "
                f"Valid presets: {sorted(SEVERITY_PRESETS)}"
            )
        base = looked_up

    def _parse(name: str, raw: str | None, fallback: SeverityLevel) -> SeverityLevel:
        if raw is None:
            return fallback
        try:
            return SeverityLevel(raw.lower())
        except ValueError:
            raise PolicyError(
                f"Invalid severity level {raw!r} for {name}. "
                f"Valid values: error, warning, info"
            ) from None

    return SeverityConfig(
        abi_breaking=_parse("abi_breaking", abi_breaking, base.abi_breaking),
        potential_breaking=_parse(
            "potential_breaking", potential_breaking, base.potential_breaking
        ),
        quality_issues=_parse("quality_issues", quality_issues, base.quality_issues),
        addition=_parse("addition", addition, base.addition),
    )


# ---------------------------------------------------------------------------
# Exit code computation
# ---------------------------------------------------------------------------

# Severity-aware exit codes (used when any --severity-* flag is set):
#
#   0 — no error-level findings
#   1 — error-level findings in additions or quality_issues only
#   2 — error-level findings in potential_breaking (but not abi_breaking)
#   4 — error-level findings in abi_breaking
#
# The highest applicable code wins (e.g. both abi_breaking=error and
# quality_issues=error → exit 4).
#
# Note: exit codes 1 and 2 intentionally share a code between two
# categories each (additions/quality_issues → 1, potential_breaking → 2).
# Callers that need per-category granularity should inspect the JSON
# ``severity.categories`` output instead of the exit code.
#
# These codes align with the legacy verdict-based exits (BREAKING → 4,
# API_BREAK → 2) but are independent: the legacy path runs when no
# --severity-* flag is provided.  The two paths are mutually exclusive
# in cli.py.

_CATEGORY_EXIT_CODES: dict[IssueCategory, int] = {
    IssueCategory.ABI_BREAKING: 4,
    IssueCategory.POTENTIAL_BREAKING: 2,
    IssueCategory.QUALITY_ISSUES: 1,
    IssueCategory.ADDITION: 1,
}


def compute_exit_code(
    changes: Sequence[HasKind],
    config: SeverityConfig,
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
) -> int:
    """Compute the process exit code based on severity configuration.

    Returns the highest exit code among categories that have both:
    - at least one finding, AND
    - severity configured as ``error``.

    *kind_sets* (from ``DiffResult._effective_kind_sets()``) includes
    PolicyFile overrides and takes precedence over *policy*.

    Returns 0 if no category at error level has findings.
    """
    worst = 0
    for change in changes:
        cat = classify_change_object(change, policy=policy, kind_sets=kind_sets)
        if config.level_for(cat) == SeverityLevel.ERROR:
            code = _CATEGORY_EXIT_CODES[cat]
            if code > worst:
                worst = code
    return worst


@dataclass(frozen=True)
class CategorizedChanges:
    """Changes partitioned into the four issue categories.

    Fields use ``list[HasKind]`` intentionally so that any object with a
    ``.kind`` attribute can be categorized (e.g. ``Change``, ``AbiChange``,
    or lightweight stubs in tests).  Callers that need full change objects
    should cast the elements to the concrete type.
    """

    abi_breaking: list[HasKind]
    potential_breaking: list[HasKind]
    quality_issues: list[HasKind]
    addition: list[HasKind]


def categorize_changes(
    changes: Sequence[HasKind],
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
) -> CategorizedChanges:
    """Partition changes into the four issue categories."""
    abi: list[HasKind] = []
    potential: list[HasKind] = []
    quality: list[HasKind] = []
    adds: list[HasKind] = []

    for c in changes:
        cat = classify_change_object(c, policy=policy, kind_sets=kind_sets)
        if cat == IssueCategory.ABI_BREAKING:
            abi.append(c)
        elif cat == IssueCategory.POTENTIAL_BREAKING:
            potential.append(c)
        elif cat == IssueCategory.QUALITY_ISSUES:
            quality.append(c)
        else:
            adds.append(c)

    return CategorizedChanges(
        abi_breaking=abi,
        potential_breaking=potential,
        quality_issues=quality,
        addition=adds,
    )
