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

"""Reporter (Markdown) — DiffResult → Markdown / review-digest output.

Leaf module: holds the Markdown rendering path plus the shared --show-only
filter and verdict-label maps it depends on. Imports nothing from ``reporter``
so it stays a leaf; ``reporter`` re-exports these names for backward compat.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .severity import KindSets, SeverityConfig

from .checker import (
    Change,
    DiffResult,
    LibraryMetadata,
    Verdict,
)
from .checker_policy import (
    HasKind,
    impact_for,
    policy_kind_sets as _policy_kind_sets,
)
from .report_summary import build_summary, surface_breakdown
from .semver import recommend_release

_VERDICT_EMOJI = {
    Verdict.NO_CHANGE: "✅",
    Verdict.COMPATIBLE: "✅",
    Verdict.COMPATIBLE_WITH_RISK: "⚠️",
    Verdict.API_BREAK: "⚠️",
    Verdict.BREAKING: "❌",
}

_VERDICT_LABEL = {
    Verdict.NO_CHANGE: "NO_CHANGE",
    Verdict.COMPATIBLE: "COMPATIBLE",
    Verdict.COMPATIBLE_WITH_RISK: "COMPATIBLE_WITH_RISK",
    Verdict.API_BREAK: "API_BREAK",
    Verdict.BREAKING: "BREAKING",
}


# ---------------------------------------------------------------------------
# Stat mode (text)
# ---------------------------------------------------------------------------


def to_stat(result: DiffResult) -> str:
    """One-line summary for CI gates."""
    summary = build_summary(result)
    label = _VERDICT_LABEL[result.verdict]
    parts = []
    if summary.breaking:
        parts.append(f"{summary.breaking} breaking")
    if summary.source_breaks:
        parts.append(f"{summary.source_breaks} source-level breaks")
    if summary.risk_count:
        parts.append(f"{summary.risk_count} risk")
    if summary.compatible_additions:
        parts.append(f"{summary.compatible_additions} compatible")
    detail = ", ".join(parts) if parts else "no changes"
    redundant_note = ""
    if result.redundant_count > 0:
        redundant_note = f" [{result.redundant_count} redundant hidden]"
    return f"{label}: {detail} ({summary.total_changes} total){redundant_note}"


# ---------------------------------------------------------------------------
# Show-only filter
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ShowOnlyFilter:
    """Parsed --show-only tokens.

    Tokens fall into three dimensions; within each dimension OR logic applies,
    across dimensions AND logic applies.
    """

    severities: frozenset[str]  # breaking, api-break, risk, compatible
    elements: frozenset[str]  # functions, variables, types, enums, elf
    actions: frozenset[str]  # added, removed, changed

    @classmethod
    def parse(cls, raw: str) -> ShowOnlyFilter:
        """Parse a comma-separated --show-only string into a filter."""
        severity_tokens = {"breaking", "api-break", "risk", "compatible"}
        element_tokens = {"functions", "variables", "types", "enums", "elf"}
        action_tokens = {"added", "removed", "changed"}

        severities: set[str] = set()
        elements: set[str] = set()
        actions: set[str] = set()

        for tok in raw.split(","):
            tok = tok.strip().lower()
            if not tok:
                continue
            if tok in severity_tokens:
                severities.add(tok)
            elif tok in element_tokens:
                elements.add(tok)
            elif tok in action_tokens:
                actions.add(tok)
            else:
                raise ValueError(f"Unknown --show-only token: {tok!r}")

        return cls(
            severities=frozenset(severities),
            elements=frozenset(elements),
            actions=frozenset(actions),
        )

    def _check_severity(self, change: Change, policy: str) -> bool:
        """Return True if *change* matches the severity filter."""
        if not self.severities:
            return True
        breaking_set, api_break_set, compat_set, risk_set = _policy_kind_sets(policy)
        # Honour an A4 per-finding effective_verdict override (ADR-027): a
        # demoted opaque/PIMPL layout change must be filtered by its *effective*
        # category, so `--show-only=breaking` excludes it — consistent with the
        # JSON severity field and filtered_summary counts (which already route
        # through effective_category). Without this the severity filter would
        # leak a demoted finding it was meant to exclude.
        eff = getattr(change, "effective_verdict", None)
        if isinstance(eff, Verdict):
            # NB: this maps to the CLI --show-only token vocabulary (hyphenated
            # "api-break"), which intentionally differs from the JSON-field
            # labels in _VERDICT_TO_SEVERITY_LABEL (underscored "api_break").
            # The two are deliberately separate label spaces — keep them in sync
            # by intent, not by sharing a dict.
            label = {
                Verdict.BREAKING: "breaking",
                Verdict.API_BREAK: "api-break",
                Verdict.COMPATIBLE_WITH_RISK: "risk",
                Verdict.COMPATIBLE: "compatible",
            }.get(eff)
            return label in self.severities
        severity_map = {
            "breaking": breaking_set,
            "api-break": api_break_set,
            "risk": risk_set,
            "compatible": compat_set,
        }
        return any(
            sev in self.severities and change.kind in kind_set
            for sev, kind_set in severity_map.items()
        )

    def _check_element(self, kind_val: str) -> bool:
        """Return True if *kind_val* matches the element filter."""
        if not self.elements:
            return True
        _ELEMENT_PREFIXES: dict[str, tuple[str, ...]] = {
            "functions": (
                "func_",
                "param_",
                "method_",
                "base_class_",
                "template_",
                "return_pointer_level_",
            ),
            "variables": ("var_", "constant_"),
            "types": ("type_", "struct_", "union_", "field_", "typedef_"),
            "enums": ("enum_",),
            "elf": (
                "soname_",
                "needed_",
                "symbol_",
                "rpath_",
                "runpath_",
                "ifunc_",
                "common_",
                "dwarf_",
                "calling_convention_",
                "compat_version_",
                "visibility_",
            ),
        }
        _ELEMENT_EXACT: dict[str, tuple[str, ...]] = {
            "functions": (
                "removed_const_overload",
                "anon_field_changed",
                "used_reserved_field",
                "frame_register_changed",
                # ADR-027 anti-pattern: a function exposing std:: by value.
                "public_api_exposes_stl_by_value",
            ),
            "types": (
                # ADR-027 type-level idiom transitions / anti-patterns whose
                # kind names don't match the type_/struct_/... prefixes.
                "opaque_invariant_broken",
                "polymorphic_type_non_virtual_dtor",
                "handle_type_changed",
            ),
            "elf": (
                "toolchain_flag_drift",
                "source_level_kind_changed",
                "value_abi_trait_changed",
                "struct_return_convention_changed",
            ),
        }
        for elem in self.elements:
            prefixes = _ELEMENT_PREFIXES.get(elem, ())
            if prefixes and any(kind_val.startswith(p) for p in prefixes):
                return True
            exact = _ELEMENT_EXACT.get(elem, ())
            if exact and kind_val in exact:
                return True
        return False

    @staticmethod
    def _check_action(kind_val: str, actions: frozenset[str]) -> bool:
        """Return True if *kind_val* matches the action filter."""
        if not actions:
            return True
        _ADDED_SUFFIXES = ("_added", "_added_compatible")
        _REMOVED_SUFFIXES = (
            "_removed",
            "_deleted",
            "_elf_only",
            "_elf_fallback",
            "_const_overload",
        )
        if "added" in actions and any(kind_val.endswith(s) for s in _ADDED_SUFFIXES):
            return True
        if "removed" in actions and any(
            kind_val.endswith(s) for s in _REMOVED_SUFFIXES
        ):
            return True
        if "changed" in actions and not (
            any(kind_val.endswith(s) for s in _ADDED_SUFFIXES)
            or any(kind_val.endswith(s) for s in _REMOVED_SUFFIXES)
        ):
            return True
        return False

    def matches(self, change: Change, policy: str = "strict_abi") -> bool:
        """Return True if *change* passes this filter."""
        if not self._check_severity(change, policy):
            return False
        if not self._check_element(change.kind.value):
            return False
        return self._check_action(change.kind.value, self.actions)


def apply_show_only(
    changes: Sequence[Change],
    show_only: str,
    policy: str = "strict_abi",
) -> list[Change]:
    """Filter changes according to a --show-only token string."""
    filt = ShowOnlyFilter.parse(show_only)
    return [c for c in changes if filt.matches(c, policy=policy)]


# ---------------------------------------------------------------------------
# Impact summary
# ---------------------------------------------------------------------------


def _build_impact_table(
    result: DiffResult,
    displayed_changes: list[Change] | None = None,
) -> list[str]:
    """Build impact summary table rows.

    When *displayed_changes* is given (e.g. after ``--show-only`` filtering),
    only those changes are considered.  Interface counts use unique
    ``affected_symbols`` names; ``caused_count`` is shown separately to
    avoid double-counting.
    """
    from .checker import _ROOT_TYPE_CHANGE_KINDS

    changes = (
        displayed_changes if displayed_changes is not None else list(result.changes)
    )

    # Collect root type changes with their impact
    root_entries: list[tuple[str, str, int, int]] = []
    for c in changes:
        if c.kind in _ROOT_TYPE_CHANGE_KINDS:
            affected_count = len(c.affected_symbols) if c.affected_symbols else 0
            if affected_count > 0 or c.caused_count > 0:
                root_entries.append(
                    (c.symbol, c.kind.value, affected_count, c.caused_count)
                )

    # Count non-type direct changes
    direct_removals = sum(
        1
        for c in changes
        if c.kind.value.endswith("_removed") and c.kind not in _ROOT_TYPE_CHANGE_KINDS
    )

    if not root_entries and direct_removals == 0:
        return []

    lines = [
        "## Impact Summary",
        "",
        "| Root Change | Kind | Affected Interfaces | Derived |",
        "|-------------|------|---------------------|---------|",
    ]
    for symbol, kind, iface_count, caused in root_entries:
        iface_str = f"{iface_count} functions" if iface_count > 0 else "—"
        caused_str = f"+{caused} collapsed" if caused > 0 else "—"
        lines.append(f"| {symbol} | {kind} | {iface_str} | {caused_str} |")
    if direct_removals > 0:
        lines.append(f"| — | removals ({direct_removals}) | direct | — |")
    lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Leaf-change mode helpers
# ---------------------------------------------------------------------------


def _format_leaf_type_change(c: Change) -> list[str]:
    """Format a single leaf-mode type change entry."""
    lines = [f"### {c.symbol} — {c.description}"]
    if c.affected_symbols:
        lines.append(f"\n**Affected interfaces ({len(c.affected_symbols)}):**")
        for sym in c.affected_symbols[:10]:
            lines.append(f"- `{sym}`")
        if len(c.affected_symbols) > 10:
            lines.append(f"- ... ({len(c.affected_symbols) - 10} more)")
    if c.caused_count > 0:
        lines.append(f"\n> {c.caused_count} derived change(s) collapsed")
    lines.append("")
    return lines


def _build_leaf_type_sections(type_changes: list[Change], policy: str) -> list[str]:
    """Build severity-grouped type-change sections for leaf-change view."""
    breaking_set, api_break_set, _, _ = _policy_kind_sets(policy)
    breaking_types = [c for c in type_changes if c.kind in breaking_set]
    api_break_types = [c for c in type_changes if c.kind in api_break_set]
    other_types = [
        c
        for c in type_changes
        if c.kind not in breaking_set and c.kind not in api_break_set
    ]

    lines: list[str] = []
    for section_label, section_changes in [
        ("## Breaking Type Changes", breaking_types),
        ("## Source-Level Type Breaks", api_break_types),
        ("## Other Type Changes", other_types),
    ]:
        if not section_changes:
            continue
        lines += [section_label, ""]
        for c in section_changes:
            lines += _format_leaf_type_change(c)
    return lines


def _to_markdown_leaf(
    result: DiffResult,
    show_impact: bool = False,
    show_only: str | None = None,
    show_recommendation: bool = False,
) -> str:
    """Leaf-change mode: root type changes with affected interface lists."""
    from .checker import _ROOT_TYPE_CHANGE_KINDS

    v = result.verdict
    emoji = _VERDICT_EMOJI[v]
    label = _VERDICT_LABEL[v]

    lines: list[str] = [
        f"# ABI Report: {result.library} (leaf-change view)",
        "",
        "| | |",
        "|---|---|",
        f"| **Old version** | `{result.old_version}` |",
        f"| **New version** | `{result.new_version}` |",
        f"| **Verdict** | {emoji} `{label}` |",
        "",
    ]

    if show_recommendation:
        _append_recommendation_section(lines, result)

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(changes, show_only, policy=result.policy)
        lines.append(
            f"> Filtered by: `--show-only {show_only}` ({len(changes)} of {len(result.changes)} changes shown)"
        )
        lines.append("")

    # Group root type changes by severity
    type_changes = [c for c in changes if c.kind in _ROOT_TYPE_CHANGE_KINDS]
    non_type_changes = [c for c in changes if c.kind not in _ROOT_TYPE_CHANGE_KINDS]

    if type_changes:
        lines += _build_leaf_type_sections(type_changes, result.policy)

    if non_type_changes:
        lines += ["## Non-Type Changes", ""]
        for c in non_type_changes:
            lines.append(_format_change_md(c))
        lines.append("")

    if not changes:
        if show_only and result.changes:
            lines.append("_No changes match the current filter._")
        else:
            lines.append("_No ABI changes detected._")

    _append_redundancy_note(lines, result)
    _append_suppression_note(lines, result)

    if show_impact:
        lines += _build_impact_table(result, displayed_changes=changes)

    lines += _footer_lines()
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------


def _fmt_size(size_bytes: int) -> str:
    """Format file size in human-readable form."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _append_redundancy_note(lines: list[str], result: DiffResult) -> None:
    if result.redundant_count > 0:
        lines.append("")
        lines.append(
            f"> ℹ️ {result.redundant_count} redundant change(s) hidden "
            "(derived from root type changes). Use `--show-redundant` to show all."
        )


def _append_suppression_note(lines: list[str], result: DiffResult) -> None:
    if result.suppression_file_provided:
        lines.append("")
        if result.suppressed_count == 0:
            lines.append(
                "> ℹ️ Suppression file active — 0 changes matched (nothing suppressed)"
            )
        else:
            lines.append(
                f"> ℹ️ {result.suppressed_count} change(s) suppressed via suppression file"
            )
            for sc in result.suppressed_changes:
                lines.append(f">   - `{sc.symbol}` — {sc.description}")


# ---------------------------------------------------------------------------
# Severity section helpers
# ---------------------------------------------------------------------------

_BREAKING_ICON = "❌"  # ❌
_SOURCE_BREAK_ICON = "⚠️"  # ⚠️
_RISK_ICON = "⚠️"  # ⚠️
_QUALITY_ICON = "\U0001f50d"  # 🔍
_ADDITION_ICON = "✅"  # ✅

_SEVERITY_EMOJI = {
    "error": "❌",  # ❌
    "warning": "⚠️",  # ⚠️
    "info": "ℹ️",  # ℹ️
}


def _section_severity_label(
    severity_config: SeverityConfig | None, category_attr: str
) -> str:
    """Return a severity label suffix like ' [ERROR]' for a report section header."""
    if severity_config is None:
        return ""
    level = getattr(severity_config, category_attr, None)
    if level is None:
        return ""
    level_val = level.value if hasattr(level, "value") else str(level)
    emoji = _SEVERITY_EMOJI.get(level_val, "")
    return f" {emoji} `{level_val.upper()}`"


def _build_severity_summary_md(
    changes: list[Change],
    severity_config: SeverityConfig,
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
) -> list[str]:
    """Build a severity configuration summary table for markdown output."""
    from .severity import SeverityLevel, categorize_changes

    categorized = categorize_changes(
        changes,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )
    lines = [
        "## Severity Configuration",
        "",
        "| Category | Severity | Count | Exit Impact |",
        "|----------|----------|-------|-------------|",
    ]

    _CATEGORY_INFO: list[tuple[str, str, list[HasKind]]] = [
        ("ABI/API Incompatibilities", "abi_breaking", categorized.abi_breaking),
        (
            "Potential Incompatibilities",
            "potential_breaking",
            categorized.potential_breaking,
        ),
        ("Quality Issues", "quality_issues", categorized.quality_issues),
        ("Additions", "addition", categorized.addition),
    ]

    for label, attr, cat_changes in _CATEGORY_INFO:
        level = getattr(severity_config, attr, SeverityLevel.INFO)
        level_val = level.value if hasattr(level, "value") else str(level)
        emoji = _SEVERITY_EMOJI.get(level_val, "")
        count = len(cat_changes)
        impact = (
            "causes non-zero exit"
            if level_val == "error" and count > 0
            else "no exit impact"
        )
        lines.append(
            f"| {label} | {emoji} `{level_val.upper()}` | {count} | {impact} |"
        )

    lines.append("")
    return lines


def _footer_lines() -> list[str]:
    return [
        "---",
        "## Legend",
        "",
        "| Verdict | Meaning |",
        "|---------|---------|",
        "| ✅ NO_CHANGE | Identical ABI |",
        "| ✅ COMPATIBLE | Only additions (backward compatible) |",
        "| ⚠️ COMPATIBLE_WITH_RISK | Binary-compatible; verify target environment |",
        "| ⚠️ API_BREAK | Source-level API change — recompilation required |",
        "| ❌ BREAKING | Binary ABI break — recompilation required |",
        "",
        "_Generated by [abicheck](https://github.com/abicheck/abicheck)_",
    ]


def _build_library_files_section(
    old_meta: LibraryMetadata | None, new_meta: LibraryMetadata | None
) -> list[str]:
    """Build the '## Library Files' markdown section."""
    lines = ["## Library Files", "", "| | Old | New |", "|---|---|---|"]
    old_path = getattr(old_meta, "path", "—") if old_meta else "—"
    new_path = getattr(new_meta, "path", "—") if new_meta else "—"
    old_sha = getattr(old_meta, "sha256", "—")[:12] if old_meta else "—"
    new_sha = getattr(new_meta, "sha256", "—")[:12] if new_meta else "—"
    old_size = _fmt_size(old_meta.size_bytes) if old_meta else "—"
    new_size = _fmt_size(new_meta.size_bytes) if new_meta else "—"
    lines += [
        f"| **Path** | `{old_path}` | `{new_path}` |",
        f"| **SHA-256** | `{old_sha}…` | `{new_sha}…` |",
        f"| **Size** | {old_size} | {new_size} |",
        "",
    ]
    return lines


def _build_severity_sections(
    breaking: list[Change],
    source_breaks: list[Change],
    risk: list[Change],
    compatible: list[Change],
    *,
    severity_config: SeverityConfig | None = None,
) -> list[str]:
    """Build all severity-grouped markdown sections."""
    lines: list[str] = []

    if breaking:
        sev_label = _section_severity_label(severity_config, "abi_breaking")
        lines += [f"## {_BREAKING_ICON} Breaking Changes{sev_label}", ""]
        for c in breaking:
            lines.append(_format_change_md(c))
        lines.append("")

    if source_breaks:
        sev_label = _section_severity_label(severity_config, "potential_breaking")
        lines += [f"## {_SOURCE_BREAK_ICON} Source-Level Breaks{sev_label}", ""]
        for c in source_breaks:
            lines.append(_format_change_md(c))
        lines.append("")

    if risk:
        sev_label = _section_severity_label(severity_config, "potential_breaking")
        lines += [f"## {_RISK_ICON} Deployment Risk Changes{sev_label}", ""]
        lines += [
            "> These changes are **binary-compatible** but may cause the library to fail",
            "> loading on older systems (e.g. a new GLIBC version requirement). Verify",
            "> your target environment before deploying.",
            "",
        ]
        for c in risk:
            lines.append(f"- **{c.kind.value}**: {c.description}")
        lines.append("")

    if compatible:
        from .checker_policy import ADDITION_KINDS as _ADDITION_KINDS

        quality = [c for c in compatible if c.kind not in _ADDITION_KINDS]
        additions_list = [c for c in compatible if c.kind in _ADDITION_KINDS]
        if quality:
            sev_label = _section_severity_label(severity_config, "quality_issues")
            lines += [f"## {_QUALITY_ICON} Quality Issues{sev_label}", ""]
            for c in quality:
                lines.append(f"- **{c.kind.value}**: {c.description}")
            lines.append("")
        if additions_list:
            sev_label = _section_severity_label(severity_config, "addition")
            lines += [f"## {_ADDITION_ICON} Additions{sev_label}", ""]
            for c in additions_list:
                lines.append(f"- {c.description}")
            lines.append("")

    return lines


def _build_environment_drift_section(changes: list[Change]) -> list[str]:
    """Group environment/toolchain-drift findings under one heading.

    These findings share a root cause the severity sections cannot express:
    the *build environment* moved (compiler, binutils/linker defaults,
    glibc/sysroot), not the library's declared interface. Summarizing them
    together answers the reviewer's first question — "was this diff caused by
    a source change or by a rebuild?" — without duplicating the per-finding
    details already listed in the severity sections above.
    """
    from .report_classifications import ENVIRONMENT_DRIFT_KINDS

    drift = [c for c in changes if c.kind.value in ENVIRONMENT_DRIFT_KINDS]
    if not drift:
        return []
    lines = [
        "## 🛠️ Environment & Toolchain Drift",
        "",
        "> The findings below are artifacts of the **build environment** — a",
        "> different compiler, binutils/linker default, or glibc/sysroot —",
        "> rather than a change to the library's declared interface. They also",
        "> appear in their severity sections above; this view groups them by",
        "> root cause. If the source did not change, review the build",
        "> environment first.",
        "",
    ]
    for c in drift:
        lines.append(f"- **{c.kind.value}**: {c.description}")
    lines.append("")
    return lines


# Verdict -> short merge-effect phrase for the reviewer digest.
_VERDICT_MERGE_EFFECT = {
    Verdict.NO_CHANGE: "no ABI/API change — safe to merge",
    Verdict.COMPATIBLE: "backward-compatible — safe to merge",
    Verdict.COMPATIBLE_WITH_RISK: "compatible but carries deployment risk — review advised",
    Verdict.API_BREAK: "source-level (API) break — consumers must recompile",
    Verdict.BREAKING: "binary (ABI) break — blocks merge under a strict gate",
}


def _severity_merge_effect(result: DiffResult, severity_config: SeverityConfig) -> str:
    """Merge-effect phrase reflecting the actual severity-aware gate.

    Compatibility (``result.verdict``) and the CI gate are independent
    decisions once a severity configuration is in play — e.g. an ``addition``
    finding configured as ``error`` blocks the build even though the verdict
    is ``COMPATIBLE``, and an ``abi_breaking`` finding configured below
    ``error`` does not. The hard-coded ``_VERDICT_MERGE_EFFECT`` phrases would
    misreport both cases, so this asks the severity gate directly instead of
    inferring "safe to merge" from the verdict alone.
    """
    from .severity import compute_exit_code

    eff_sets = result._effective_kind_sets()
    exit_code = compute_exit_code(
        result.changes,
        severity_config,
        policy=result.policy,
        kind_sets=eff_sets,
        policy_file=result.policy_file,
    )
    if exit_code == 0:
        return "no error-level findings under the configured severity policy — safe to merge"
    return "blocked by severity policy — review required before merge"


def to_review_digest(
    result: DiffResult, *, severity_config: SeverityConfig | None = None,
) -> str:
    """Compact GitHub-facing review digest (Markdown).

    A single, reviewer-oriented summary suitable for a job summary
    ($GITHUB_STEP_SUMMARY) or a PR comment body: verdict + merge effect, a
    counts table that separates breaking / API / risk / public additions /
    filtered-internal, the release recommendation, a manual-review banner when
    public-header scoping fell back (issue #235), and the top impacted symbols.
    Distinct from to_markdown (the full report) — this is the "presentation"
    layer over the same machine-readable decision contract.

    *severity_config*, when given, drives the merge-effect phrase from the
    actual severity-aware CI gate instead of the raw compatibility verdict —
    compatibility and "blocks CI" are independent decisions once severity
    configuration is in play (see :func:`_severity_merge_effect`).
    """
    summary = build_summary(result)
    v = result.verdict
    emoji = _VERDICT_EMOJI.get(v, "?")
    label = _VERDICT_LABEL.get(v, v.value)
    effect = (
        _severity_merge_effect(result, severity_config)
        if severity_config is not None
        else _VERDICT_MERGE_EFFECT.get(v, "")
    )

    lines: list[str] = [
        f"## ABI review — `{result.library}` {result.old_version} → {result.new_version}",
        "",
        f"**Verdict:** {emoji} `{label}` — {effect}",
        "",
    ]

    # Manual-review banner: scoping requested but the public surface could not
    # be confirmed, so compatibility is unconfirmed (don't overclaim).
    if result.scope_to_public_surface and not result.scope_resolved:
        lines += [
            "> ⚠️ **Manual review required.** `--scope-public-headers` could not "
            "resolve the public surface, so analysis fell back to the full export "
            "table. Treat this result as *unconfirmed*, not a clean public surface.",
            "",
        ]

    scoped = result.scope_to_public_surface
    additions_label = "Public additions" if scoped else "Additions"
    lines += [
        "| Category | Count |",
        "|---|---|",
        f"| ❌ Breaking (ABI) | {summary.breaking} |",
        f"| ⚠️ API breaks (source) | {summary.source_breaks} |",
        f"| ⚠️ Risk findings | {summary.risk_count} |",
        f"| ✅ {additions_label} | {summary.compatible_additions} |",
    ]
    if scoped:
        lines.append(
            f"| 🔒 Filtered (internal/private) | {result.out_of_surface_count} |"
        )
    lines.append("")

    rec = recommend_release(result)
    lines += [
        f"**Release recommendation:** `{rec.bump.value}` version bump · "
        f"SONAME `{rec.soname.value}`",
        "",
    ]

    # Top impacted symbols (breaking + API), capped for readability.
    breaking_set, api_break_set, _, _ = result._effective_kind_sets()
    impacted = [
        c for c in result.changes if c.kind in breaking_set or c.kind in api_break_set
    ]
    if impacted:
        lines += ["**Top impacted symbols:**", ""]
        for c in impacted[:10]:
            sym = c.symbol or "?"
            lines.append(f"- `{sym}` — {c.kind.value}")
        if len(impacted) > 10:
            lines.append(f"- … and {len(impacted) - 10} more")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _build_internal_rtti_note(breaking: list[Change]) -> list[str]:
    """Build the up-front note when breaking findings are mostly RTTI/internal
    churn. Returns an empty list when there is nothing to note."""
    _bd = surface_breakdown(breaking)
    if not (_bd.rtti or _bd.internal):
        return []
    return [
        f"> ℹ️ **{_bd.rtti + _bd.internal} of {_bd.total} breaking findings are "
        f"internal/RTTI churn** ({_bd.rtti} RTTI, {_bd.internal} "
        "internal-namespace) — typically a missing `-fvisibility=hidden`, not "
        f"public-API breaks. Genuine public-surface breaking findings: "
        f"**{_bd.public}**.",
        "",
    ]


def to_markdown(
    result: DiffResult,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
    severity_config: SeverityConfig | None = None,
    show_recommendation: bool = False,
    demangle: bool = False,
) -> str:
    # Human-facing only: optionally demangle Itanium C++ symbols in the rendered
    # output. Machine formats (JSON/SARIF/JUnit) keep the raw mangled symbols.
    def _out(text: str) -> str:
        if not demangle:
            return text
        from .demangle import demangle_text

        return demangle_text(text)

    if stat:
        return _out(to_stat(result))

    if report_mode == "leaf":
        return _out(
            _to_markdown_leaf(
                result,
                show_impact=show_impact,
                show_only=show_only,
                show_recommendation=show_recommendation,
            )
        )

    v = result.verdict
    emoji = _VERDICT_EMOJI[v]
    label = _VERDICT_LABEL[v]

    old_meta = getattr(result, "old_metadata", None)
    new_meta = getattr(result, "new_metadata", None)

    # Apply show-only filter if provided (display-only, does not affect verdict)
    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(changes, show_only, policy=result.policy)

    # Build the render-ready view once (C2/ADR-036): canonical verdict-axis
    # classification + summary in one place, shared across formats.
    from .report_model import ReportModel

    model = ReportModel.from_result(result, changes=changes)
    breaking, source_breaks, risk, compatible = (
        model.breaking,
        model.source_breaks,
        model.risk,
        model.compatible,
    )

    lines: list[str] = [
        f"# ABI Report: {result.library}",
        "",
        "| | |",
        "|---|---|",
        f"| **Old version** | `{result.old_version}` |",
        f"| **New version** | `{result.new_version}` |",
        f"| **Verdict** | {emoji} `{label}` |",
        f"| Breaking changes | {len(result.breaking)} |",
        f"| Source-level breaks | {len(result.source_breaks)} |",
        f"| Deployment risk changes | {len(result.risk)} |",
        f"| Compatible changes | {len(result.compatible)} |",
        "",
    ]

    # When most of the breaking count is RTTI / internal-namespace churn, say so
    # up front — otherwise a huge count from a library lacking -fvisibility=hidden
    # buries the handful of genuine public-API breaks.
    lines += _build_internal_rtti_note(breaking)

    _append_confidence_section(lines, result)

    _append_policy_section(lines, result)

    if show_recommendation:
        _append_recommendation_section(lines, result)

    # Severity configuration summary when provided
    if severity_config is not None:
        lines += _build_severity_summary_md(
            changes,
            severity_config,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )

    if show_only:
        lines.append(
            f"> Filtered by: `--show-only {show_only}` ({len(changes)} of {len(result.changes)} changes shown)"
        )
        lines.append("")

    if old_meta or new_meta:
        lines += _build_library_files_section(old_meta, new_meta)

    lines += _build_severity_sections(
        breaking,
        source_breaks,
        risk,
        compatible,
        severity_config=severity_config,
    )

    lines += _build_environment_drift_section(changes)

    if not changes:
        if show_only and result.changes:
            lines.append("_No changes match the current filter._")
        else:
            lines.append("_No ABI changes detected._")

    _append_redundancy_note(lines, result)
    _append_suppression_note(lines, result)

    if show_impact:
        lines.append("")
        lines += _build_impact_table(result, displayed_changes=changes)

    lines += _footer_lines()
    return _out("\n".join(lines))


def _append_confidence_section(lines: list[str], result: DiffResult) -> None:
    """Append confidence/evidence metadata section to markdown lines."""
    conf = getattr(result, "confidence", None)
    if conf is None:
        return
    tiers = getattr(result, "evidence_tiers", None)
    cov_warns = getattr(result, "coverage_warnings", None)
    conf_val = conf.value if hasattr(conf, "value") else str(conf)
    tier_str = ", ".join(f"`{t}`" for t in tiers) if tiers else "_none_"
    etier = getattr(result, "evidence_tier", None)
    etier_val = (
        etier.value if (etier is not None and hasattr(etier, "value")) else str(etier)
    )
    lines += [
        "## Analysis Confidence",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Confidence | {conf_val.upper()} |",
        f"| Evidence tier | `{etier_val}` |",
        f"| Evidence tiers | {tier_str} |",
    ]
    if cov_warns:
        for warning in cov_warns:
            lines.append(f"| Coverage gap | {warning} |")
    lines.append("")


def _append_policy_section(lines: list[str], result: DiffResult) -> None:
    """Append policy metadata section to markdown lines."""
    lines.append(f"> **Policy**: `{result.policy or 'strict_abi'}`")
    if result.policy_file and result.policy_file.overrides:
        overrides = ", ".join(
            f"`{kind.value}` → `{severity.value}`"
            for kind, severity in result.policy_file.overrides.items()
        )
        lines.append(f"> **Policy overrides**: {overrides}")
    lines.append("")


_BUMP_EMOJI = {"major": "🔴", "minor": "🟢", "patch": "🟢", "none": "✅"}


def _append_recommendation_section(lines: list[str], result: DiffResult) -> None:
    """Append the release-recommendation section (semver bump + soname action)."""
    rec = recommend_release(result)
    emoji = _BUMP_EMOJI.get(rec.bump.value, "")
    lines += [
        "## Release Recommendation",
        "",
        "| Field | Value |",
        "|---|---|",
        f"| Version bump | {emoji} **{rec.bump.value.upper()}** |",
        f"| SONAME action | `{rec.soname.value}` |",
        "",
        f"{rec.rationale}",
        "",
    ]


def _format_change_md(c: object) -> str:
    """Format a single change as a markdown list item with impact and metadata."""
    kind = getattr(c, "kind", None)
    kind_val = kind.value if kind else ""
    desc = getattr(c, "description", "")
    old_val = getattr(c, "old_value", None)
    new_val = getattr(c, "new_value", None)
    loc = getattr(c, "source_location", None)
    affected = getattr(c, "affected_symbols", None)
    caused_count = getattr(c, "caused_count", 0)

    # Base line
    old_new = ""
    if old_val is not None and new_val is not None:
        old_new = f" (`{old_val}` → `{new_val}`)"
    elif old_val is not None:
        old_new = f" (`{old_val}`)"
    elif new_val is not None:
        old_new = f" (`{new_val}`)"
    line = f"- **{kind_val}**: {desc}{old_new}"

    # Source location
    if loc:
        line += f" — `{loc}`"

    # Impact
    if kind:
        impact = impact_for(kind)
        if impact:
            line += f"\n  > {impact}"

    # Collapsed derived changes
    if caused_count > 0:
        line += f"\n  > {caused_count} derived change(s) collapsed"

    # Affected functions
    if affected:
        names = ", ".join(f"`{s}`" for s in affected[:5])
        suffix = f" (+{len(affected) - 5} more)" if len(affected) > 5 else ""
        line += f"\n  > Affected symbols: {names}{suffix}"

    return line
