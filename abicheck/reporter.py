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

"""Reporter — DiffResult → JSON / Markdown / stat output."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .checker_policy import HasKind
    from .severity import KindSets, SeverityConfig

from .checker import (
    Change,
    DiffResult,
    LibraryMetadata,
    Verdict,
)
from .checker_policy import (
    ChangeKind,
    impact_for,
)
from .checker_policy import (
    policy_kind_sets as _policy_kind_sets,
)
from .report_summary import build_summary
from .schemas import REPORT_SCHEMA_VERSION
from .semver import recommend_release


def _kind_to_severity(kind: ChangeKind, policy: str) -> str:
    """Map a ChangeKind to its severity label under the given policy (FIX-G)."""
    breaking, api_break, compatible, risk = _policy_kind_sets(policy)
    if kind in breaking:
        return "breaking"
    if kind in api_break:
        return "api_break"
    if kind in risk:
        return "risk"
    if kind in compatible:
        return "compatible"
    return "unknown"

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
# Show-only filter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ShowOnlyFilter:
    """Parsed --show-only tokens.

    Tokens fall into three dimensions; within each dimension OR logic applies,
    across dimensions AND logic applies.
    """
    severities: frozenset[str]  # breaking, api-break, risk, compatible
    elements: frozenset[str]    # functions, variables, types, enums, elf
    actions: frozenset[str]     # added, removed, changed

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
                "func_", "param_", "method_", "base_class_",
                "template_", "return_pointer_level_",
            ),
            "variables": ("var_", "constant_"),
            "types": ("type_", "struct_", "union_", "field_", "typedef_"),
            "enums": ("enum_",),
            "elf": (
                "soname_", "needed_", "symbol_", "rpath_", "runpath_",
                "ifunc_", "common_", "dwarf_", "calling_convention_",
                "compat_version_", "visibility_",
            ),
        }
        _ELEMENT_EXACT: dict[str, tuple[str, ...]] = {
            "functions": (
                "removed_const_overload", "anon_field_changed",
                "used_reserved_field", "frame_register_changed",
            ),
            "elf": (
                "toolchain_flag_drift", "source_level_kind_changed",
                "value_abi_trait_changed",
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
        _REMOVED_SUFFIXES = ("_removed", "_deleted", "_elf_only", "_elf_fallback", "_const_overload")
        if "added" in actions and any(kind_val.endswith(s) for s in _ADDED_SUFFIXES):
            return True
        if "removed" in actions and any(kind_val.endswith(s) for s in _REMOVED_SUFFIXES):
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
# Stat mode
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


def to_stat_json(result: DiffResult, indent: int = 2) -> str:
    """JSON output for --stat mode: summary only, no changes array."""
    summary = build_summary(result)
    effective_policy = result.policy or "strict_abi"
    d: dict[str, object] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "library": result.library,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict": result.verdict.value,
        "policy": effective_policy,
        "summary": {
            "breaking": summary.breaking,
            "source_breaks": summary.source_breaks,
            "risk_changes": summary.risk_count,
            "compatible_additions": summary.compatible_additions,
            "total_changes": summary.total_changes,
            "binary_compatibility_pct": round(summary.binary_compatibility_pct, 1),
            "affected_pct": round(summary.affected_pct, 1),
        },
    }
    d["release_recommendation"] = recommend_release(result).to_dict()
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    # Confidence & evidence metadata
    d["confidence"] = result.confidence.value
    d["evidence_tier"] = result.evidence_tier.value
    d["evidence_tiers"] = list(result.evidence_tiers)
    if result.coverage_warnings:
        d["coverage_warnings"] = list(result.coverage_warnings)
    return json.dumps(d, indent=indent)


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

    changes = displayed_changes if displayed_changes is not None else list(result.changes)

    # Collect root type changes with their impact
    root_entries: list[tuple[str, str, int, int]] = []
    for c in changes:
        if c.kind in _ROOT_TYPE_CHANGE_KINDS:
            affected_count = len(c.affected_symbols) if c.affected_symbols else 0
            if affected_count > 0 or c.caused_count > 0:
                root_entries.append((c.symbol, c.kind.value, affected_count, c.caused_count))

    # Count non-type direct changes
    direct_removals = sum(
        1 for c in changes
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
    other_types = [c for c in type_changes if c.kind not in breaking_set and c.kind not in api_break_set]

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
        lines.append(f"> Filtered by: `--show-only {show_only}` ({len(changes)} of {len(result.changes)} changes shown)")
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


def _add_surface_scope(d: dict[str, object], result: DiffResult) -> None:
    """Attach the ADR-024 §D4/D5 public-surface scope ledger to a JSON dict.

    When header scoping is active, findings that fall outside the public ABI
    surface are demoted to this audit ledger rather than dropped — disclosed
    here (not just on stderr) so the "why was this excluded" trail is
    machine-readable. Shared by the full and leaf JSON paths so both formats
    carry the ledger consistently.
    """
    if not result.scope_to_public_surface:
        return
    d["surface_scope"] = {
        "enabled": True,
        "out_of_surface_count": result.out_of_surface_count,
        "out_of_surface_changes": [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
                "source_location": c.source_location,
                "reason": getattr(c, "surface_exclusion_reason", None),
            }
            for c in result.out_of_surface_changes
        ],
    }


def _to_json_leaf(
    result: DiffResult,
    indent: int = 2,
    show_only: str | None = None,
) -> str:
    """Leaf-change mode JSON output."""
    from .checker import _ROOT_TYPE_CHANGE_KINDS

    summary = build_summary(result)
    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(changes, show_only, policy=result.policy)
    type_changes = [c for c in changes if c.kind in _ROOT_TYPE_CHANGE_KINDS]
    non_type_changes = [c for c in changes if c.kind not in _ROOT_TYPE_CHANGE_KINDS]

    effective_policy = result.policy or "strict_abi"
    eff_sets = result._effective_kind_sets()

    def _severity_from_sets(kind: object) -> str:
        breaking, api_break, compatible, risk = eff_sets
        if kind in breaking:
            return "breaking"
        if kind in api_break:
            return "api_break"
        if kind in risk:
            return "risk"
        if kind in compatible:
            return "compatible"
        return "unknown"

    leaf_changes_list = [
        {
            "kind": c.kind.value,
            "symbol": c.symbol,
            "description": c.description,
            "severity": _severity_from_sets(c.kind),
            "affected_count": len(c.affected_symbols) if c.affected_symbols else 0,
            "affected_symbols": c.affected_symbols or [],
            "caused_count": c.caused_count,
            "old_value": getattr(c, "old_value", None),
            "new_value": getattr(c, "new_value", None),
        }
        for c in type_changes
    ]
    non_type_list = [_change_to_dict(c, policy=effective_policy, kind_sets=eff_sets) for c in non_type_changes]

    d: dict[str, object] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "library": result.library,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict": result.verdict.value,
        "policy": effective_policy,
        "summary": {
            "breaking": summary.breaking,
            "source_breaks": summary.source_breaks,
            "risk_changes": summary.risk_count,
            "compatible_additions": summary.compatible_additions,
            "total_changes": summary.total_changes,
        },
        "leaf_changes": leaf_changes_list,
        "non_type_changes": non_type_list,
        # FIX-H: populate changes with union for backward-compat consumers
        "changes": leaf_changes_list + non_type_list,
    }
    # Release recommendation — always present in JSON, including leaf mode.
    d["release_recommendation"] = recommend_release(result).to_dict()
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    # Confidence & evidence metadata
    d["confidence"] = result.confidence.value
    d["evidence_tier"] = result.evidence_tier.value
    d["evidence_tiers"] = list(result.evidence_tiers)
    if result.coverage_warnings:
        d["coverage_warnings"] = list(result.coverage_warnings)
    _add_surface_scope(d, result)
    return json.dumps(d, indent=indent)


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

def _metadata_dict(meta: object | None) -> dict[str, object] | None:
    if meta is None:
        return None
    return {
        "path": getattr(meta, "path", ""),
        "sha256": getattr(meta, "sha256", ""),
        "size_bytes": getattr(meta, "size_bytes", 0),
    }


def to_json(
    result: DiffResult,
    indent: int = 2,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
    severity_config: SeverityConfig | None = None,
) -> str:
    if stat:
        return to_stat_json(result, indent=indent)

    if report_mode == "leaf":
        return _to_json_leaf(result, indent=indent, show_only=show_only)

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(changes, show_only, policy=result.policy)

    summary = build_summary(result)
    d: dict[str, object] = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "library": result.library,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict": result.verdict.value,
    }
    # Library file metadata (path, SHA-256, size) — always present for schema consistency
    d["old_file"] = _metadata_dict(getattr(result, "old_metadata", None))
    d["new_file"] = _metadata_dict(getattr(result, "new_metadata", None))
    d["summary"] = {
        "breaking": summary.breaking,
        "source_breaks": summary.source_breaks,
        "risk_changes": summary.risk_count,
        "compatible_additions": summary.compatible_additions,
        "total_changes": summary.total_changes,
        "binary_compatibility_pct": round(summary.binary_compatibility_pct, 1),
        "affected_pct": round(summary.affected_pct, 1),
    }
    # Release recommendation (semver bump + soname action) — additive, machine-facing.
    d["release_recommendation"] = recommend_release(result).to_dict()
    effective_policy = result.policy or "strict_abi"
    d["policy"] = effective_policy
    eff_sets = result._effective_kind_sets()

    if show_only:
        eff_breaking, eff_api_break, _, eff_risk = eff_sets
        d["show_only_filter"] = show_only
        d["filtered_summary"] = {
            "breaking": sum(1 for c in changes if c.kind in eff_breaking),
            "source_breaks": sum(1 for c in changes if c.kind in eff_api_break),
            "risk_changes": sum(1 for c in changes if c.kind in eff_risk),
            "total_changes": len(changes),
        }

    # Severity-categorized summary when severity config is provided
    if severity_config is not None:
        d["severity"] = _build_severity_json(
            changes, severity_config,
            all_changes=list(result.changes),
            kind_sets=eff_sets,
        )

    d["changes"] = [_change_to_dict(c, policy=effective_policy, kind_sets=eff_sets) for c in changes]
    if result.redundant_count > 0:
        d["redundant_count"] = result.redundant_count
    d["suppression"] = {
        "file_provided": result.suppression_file_provided,
        "suppressed_count": result.suppressed_count,
        "suppressed_changes": [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
            }
            for c in result.suppressed_changes
        ],
    }
    _add_surface_scope(d, result)
    d["detectors"] = [
        {
            "name": det.name,
            "changes_count": det.changes_count,
            "enabled": det.enabled,
            "coverage_gap": det.coverage_gap,
        }
        for det in result.detector_results
        if det.changes_count > 0 or det.coverage_gap is not None
    ]
    # Confidence & evidence metadata — helps users assess verdict trust level
    d["confidence"] = result.confidence.value
    d["evidence_tier"] = result.evidence_tier.value
    d["evidence_tiers"] = list(result.evidence_tiers)
    if result.coverage_warnings:
        d["coverage_warnings"] = list(result.coverage_warnings)
    # Policy file overrides (custom re-classifications)
    if result.policy_file and result.policy_file.overrides:
        d["policy_overrides"] = {
            kind.value: verdict.value
            for kind, verdict in result.policy_file.overrides.items()
        }
        if result.policy_file.source_path:
            d["policy_file"] = str(result.policy_file.source_path)
    if show_impact:
        d["show_only_applied"] = show_only is not None
    return json.dumps(d, indent=indent)


def _change_to_dict(
    c: object,
    *,
    policy: str = "strict_abi",
    kind_sets: tuple[frozenset[ChangeKind], frozenset[ChangeKind], frozenset[ChangeKind], frozenset[ChangeKind]] | None = None,
) -> dict[str, object]:
    """Convert a Change to a JSON-serializable dict with impact and metadata."""
    kind = getattr(c, "kind", None)
    if kind and kind_sets:
        breaking, api_break, compatible, risk = kind_sets
        if kind in breaking:
            severity = "breaking"
        elif kind in api_break:
            severity = "api_break"
        elif kind in risk:
            severity = "risk"
        elif kind in compatible:
            severity = "compatible"
        else:
            severity = "unknown"
    elif kind:
        severity = _kind_to_severity(kind, policy)
    else:
        severity = "unknown"
    d: dict[str, object] = {
        "kind": kind.value if kind else "",
        "symbol": getattr(c, "symbol", ""),
        "description": getattr(c, "description", ""),
        "old_value": getattr(c, "old_value", None),
        "new_value": getattr(c, "new_value", None),
        "severity": severity,
    }
    # Impact explanation
    if kind:
        impact = impact_for(kind)
        if impact:
            d["impact"] = impact
    # Source location
    loc = getattr(c, "source_location", None)
    if loc:
        d["source_location"] = loc
    # Affected symbols
    affected = getattr(c, "affected_symbols", None)
    if affected:
        d["affected_symbols"] = affected
    # Redundancy annotation
    caused_by = getattr(c, "caused_by_type", None)
    if caused_by:
        d["caused_by_type"] = caused_by
    caused_count = getattr(c, "caused_count", 0)
    if caused_count > 0:
        d["caused_count"] = caused_count
    return d


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

_BREAKING_ICON = "\u274c"  # ❌
_SOURCE_BREAK_ICON = "\u26a0\ufe0f"  # ⚠️
_RISK_ICON = "\u26a0\ufe0f"  # ⚠️
_QUALITY_ICON = "\U0001f50d"  # 🔍
_ADDITION_ICON = "\u2705"  # ✅

_SEVERITY_EMOJI = {
    "error": "\u274c",  # ❌
    "warning": "\u26a0\ufe0f",  # ⚠️
    "info": "\u2139\ufe0f",  # ℹ️
}


def _section_severity_label(severity_config: SeverityConfig | None, category_attr: str) -> str:
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
    kind_sets: KindSets | None = None,
) -> list[str]:
    """Build a severity configuration summary table for markdown output."""
    from .severity import SeverityLevel, categorize_changes

    categorized = categorize_changes(changes, kind_sets=kind_sets)
    lines = [
        "## Severity Configuration",
        "",
        "| Category | Severity | Count | Exit Impact |",
        "|----------|----------|-------|-------------|",
    ]

    _CATEGORY_INFO: list[tuple[str, str, list[HasKind]]] = [
        ("ABI/API Incompatibilities", "abi_breaking", categorized.abi_breaking),
        ("Potential Incompatibilities", "potential_breaking", categorized.potential_breaking),
        ("Quality Issues", "quality_issues", categorized.quality_issues),
        ("Additions", "addition", categorized.addition),
    ]

    for label, attr, cat_changes in _CATEGORY_INFO:
        level = getattr(severity_config, attr, SeverityLevel.INFO)
        level_val = level.value if hasattr(level, "value") else str(level)
        emoji = _SEVERITY_EMOJI.get(level_val, "")
        count = len(cat_changes)
        impact = "causes non-zero exit" if level_val == "error" and count > 0 else "no exit impact"
        lines.append(
            f"| {label} | {emoji} `{level_val.upper()}` | {count} | {impact} |"
        )

    lines.append("")
    return lines


def _build_severity_json(
    changes: list[Change],
    severity_config: SeverityConfig,
    *,
    all_changes: list[Change] | None = None,
    kind_sets: KindSets | None = None,
) -> dict[str, object]:
    """Build severity information for JSON output.

    *changes* are the (possibly filtered) changes for display counts.
    *all_changes*, when provided, is the unfiltered set used to compute
    the exit code so that ``--show-only`` does not affect the exit code.
    *kind_sets* from ``DiffResult._effective_kind_sets()`` includes
    PolicyFile overrides.
    """
    from .severity import SeverityLevel, categorize_changes, compute_exit_code

    categorized = categorize_changes(changes, kind_sets=kind_sets)

    config_dict: dict[str, str] = {}
    for attr in ("abi_breaking", "potential_breaking", "quality_issues", "addition"):
        level = getattr(severity_config, attr, SeverityLevel.INFO)
        config_dict[attr] = level.value if hasattr(level, "value") else str(level)

    categories: dict[str, object] = {
        "abi_breaking": {
            "severity": config_dict["abi_breaking"],
            "count": len(categorized.abi_breaking),
        },
        "potential_breaking": {
            "severity": config_dict["potential_breaking"],
            "count": len(categorized.potential_breaking),
        },
        "quality_issues": {
            "severity": config_dict["quality_issues"],
            "count": len(categorized.quality_issues),
        },
        "addition": {
            "severity": config_dict["addition"],
            "count": len(categorized.addition),
        },
    }

    # Exit code uses the full unfiltered change set so --show-only
    # does not affect it.
    exit_changes = all_changes if all_changes is not None else changes
    exit_code = compute_exit_code(exit_changes, severity_config, kind_sets=kind_sets)

    return {
        "config": config_dict,
        "categories": categories,
        "exit_code": exit_code,
    }


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
        "_Generated by [abicheck](https://github.com/napetrov/abicheck)_",
    ]


def _build_library_files_section(old_meta: LibraryMetadata | None, new_meta: LibraryMetadata | None) -> list[str]:
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


def to_markdown(
    result: DiffResult,
    *,
    show_only: str | None = None,
    report_mode: str = "full",
    show_impact: bool = False,
    stat: bool = False,
    severity_config: SeverityConfig | None = None,
    show_recommendation: bool = False,
) -> str:
    if stat:
        return to_stat(result)

    if report_mode == "leaf":
        return _to_markdown_leaf(
            result, show_impact=show_impact, show_only=show_only,
            show_recommendation=show_recommendation,
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

    # Classify filtered changes using effective kind sets (respects PolicyFile overrides)
    breaking_set, api_break_set, compat_set, risk_set = result._effective_kind_sets()
    breaking = [c for c in changes if c.kind in breaking_set]
    source_breaks = [c for c in changes if c.kind in api_break_set]
    risk = [c for c in changes if c.kind in risk_set]
    compatible = [c for c in changes if c.kind in compat_set]

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

    _append_confidence_section(lines, result)

    _append_policy_section(lines, result)

    if show_recommendation:
        _append_recommendation_section(lines, result)

    # Severity configuration summary when provided
    if severity_config is not None:
        lines += _build_severity_summary_md(
            changes, severity_config, kind_sets=result._effective_kind_sets(),
        )

    if show_only:
        lines.append(f"> Filtered by: `--show-only {show_only}` ({len(changes)} of {len(result.changes)} changes shown)")
        lines.append("")

    if old_meta or new_meta:
        lines += _build_library_files_section(old_meta, new_meta)

    lines += _build_severity_sections(
        breaking, source_breaks, risk, compatible,
        severity_config=severity_config,
    )

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
    return "\n".join(lines)


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
    etier_val = etier.value if (etier is not None and hasattr(etier, "value")) else str(etier)
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


# ---------------------------------------------------------------------------
# Application compatibility reporters (ADR-005)
# ---------------------------------------------------------------------------

def appcompat_to_json(result: object, indent: int = 2) -> str:
    """Render an AppCompatResult as JSON."""
    import json as _json

    verdict = getattr(result, "verdict", None)
    full_diff = getattr(result, "full_diff", None)

    d: dict[str, object] = {
        "application": getattr(result, "app_path", ""),
        "old_library": getattr(result, "old_lib_path", ""),
        "new_library": getattr(result, "new_lib_path", ""),
        "verdict": verdict.value if verdict else "UNKNOWN",
        "symbol_coverage_pct": round(getattr(result, "symbol_coverage", 0.0), 1),
        "required_symbol_count": getattr(result, "required_symbol_count", 0),
    }

    missing = getattr(result, "missing_symbols", [])
    d["missing_symbols"] = list(missing)

    missing_ver = getattr(result, "missing_versions", [])
    d["missing_versions"] = list(missing_ver)

    breaking = getattr(result, "breaking_for_app", [])
    appcompat_policy = getattr(getattr(result, "full_diff", None), "policy", "strict_abi") or "strict_abi"
    d["relevant_changes"] = [_change_to_dict(c, policy=appcompat_policy) for c in breaking]
    d["relevant_change_count"] = len(breaking)

    irrelevant = getattr(result, "irrelevant_for_app", [])
    d["irrelevant_change_count"] = len(irrelevant)

    total = len(breaking) + len(irrelevant)
    d["total_library_changes"] = total

    if full_diff:
        d["full_library_verdict"] = full_diff.verdict.value
        # Traceability: file metadata from the underlying library diff
        d["old_file"] = _metadata_dict(getattr(full_diff, "old_metadata", None))
        d["new_file"] = _metadata_dict(getattr(full_diff, "new_metadata", None))
        # Confidence & evidence
        conf = getattr(full_diff, "confidence", None)
        if conf is not None:
            d["confidence"] = conf.value if hasattr(conf, "value") else str(conf)
            etier = getattr(full_diff, "evidence_tier", None)
            if etier is not None:
                d["evidence_tier"] = etier.value if hasattr(etier, "value") else str(etier)
            d["evidence_tiers"] = list(getattr(full_diff, "evidence_tiers", []) or [])
            cov_warns = getattr(full_diff, "coverage_warnings", []) or []
            if cov_warns:
                d["coverage_warnings"] = list(cov_warns)

    return _json.dumps(d, indent=indent)


def appcompat_to_markdown(result: object, *, show_irrelevant: bool = False) -> str:
    """Render an AppCompatResult as Markdown."""
    verdict = getattr(result, "verdict", None)
    v_label = verdict.value if verdict else "UNKNOWN"
    v_emoji = _VERDICT_EMOJI.get(verdict, "?") if verdict else "?"

    app_path = getattr(result, "app_path", "")
    old_lib = getattr(result, "old_lib_path", "")
    new_lib = getattr(result, "new_lib_path", "")
    required_count = getattr(result, "required_symbol_count", 0)
    coverage = getattr(result, "symbol_coverage", 0.0)
    missing = getattr(result, "missing_symbols", [])
    missing_ver = getattr(result, "missing_versions", [])
    breaking = getattr(result, "breaking_for_app", [])
    irrelevant = getattr(result, "irrelevant_for_app", [])

    total_changes = len(breaking) + len(irrelevant)

    lines: list[str] = [
        "# Application Compatibility Report",
        "",
    ]

    lines += _appcompat_header_lines(app_path, old_lib, new_lib, v_emoji, v_label)

    # File metadata (traceability)
    full_diff = getattr(result, "full_diff", None)
    old_meta = getattr(full_diff, "old_metadata", None) if full_diff else None
    new_meta = getattr(full_diff, "new_metadata", None) if full_diff else None
    if old_meta or new_meta:
        lines += ["## Library Files", "", "| | Old | New |", "|---|---|---|"]
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

    # Confidence info
    conf = getattr(full_diff, "confidence", None) if full_diff else None
    if conf is not None:
        conf_val = conf.value if hasattr(conf, "value") else str(conf)
        tiers = getattr(full_diff, "evidence_tiers", []) or []
        tier_str = ", ".join(f"`{t}`" for t in tiers) if tiers else "_none_"
        policy_val = getattr(full_diff, "policy", None) or "strict_abi"
        lines += [
            f"> **Confidence**: {conf_val.upper()} | **Evidence**: {tier_str} | **Policy**: `{policy_val}`",
            "",
        ]
    else:
        # Still show policy when confidence is absent
        policy_val = getattr(full_diff, "policy", None) if full_diff else None
        if policy_val:
            lines += [f"> **Policy**: `{policy_val}`", ""]

    lines += _appcompat_coverage_lines(required_count, coverage, missing)
    lines += _appcompat_missing_lines(missing, missing_ver)
    lines += _appcompat_relevant_lines(breaking, total_changes)
    lines += _appcompat_irrelevant_lines(irrelevant, show_irrelevant)

    lines += [
        "---",
        "_Generated by [abicheck](https://github.com/napetrov/abicheck)_",
    ]
    return "\n".join(lines)


def _appcompat_header_lines(
    app_path: str, old_lib: str, new_lib: str, v_emoji: str, v_label: str,
) -> list[str]:
    """Build the report header lines for appcompat markdown."""
    header = [
        f"**Application:** `{app_path}`",
        f"**Verdict:** {v_emoji} `{v_label}`",
        "",
    ]
    if old_lib:
        header.insert(1, f"**Library:** `{old_lib}` → `{new_lib}`")
        return header
    header.insert(1, f"**Library:** `{new_lib}`")
    return header


def _appcompat_coverage_lines(
    required_count: int,
    coverage: float,
    missing: list[object],
) -> list[str]:
    """Build symbol coverage section lines."""
    lines = ["## Symbol Coverage", "", f"App requires **{required_count}** library symbols."]
    if missing:
        lines.append(
            f"**{len(missing)}** required symbol(s) missing from new version "
            f"({coverage:.0f}% coverage).",
        )
    elif required_count > 0:
        lines.append(
            f"All {required_count} required symbols present in new version "
            f"({coverage:.0f}% coverage).",
        )
    lines.append("")
    return lines


def _appcompat_missing_lines(
    missing: list[object],
    missing_ver: list[object],
) -> list[str]:
    """Build missing symbol/version sections."""
    lines: list[str] = []
    if missing:
        lines += ["## Missing Symbols", ""]
        lines.append("These symbols are required by the application but absent from the new library:")
        lines.append("")
        for sym in missing:
            lines.append(f"- `{sym}`")
        lines.append("")
    if missing_ver:
        lines += ["## Missing Symbol Versions", ""]
        for ver in missing_ver:
            lines.append(f"- `{ver}`")
        lines.append("")
    return lines


def _appcompat_relevant_lines(breaking: list[Change], total_changes: int) -> list[str]:
    """Build relevant changes section lines."""
    if breaking:
        lines: list[str] = [
            f"## Relevant Changes ({len(breaking)} of {total_changes} total)",
            "",
            "These library changes affect symbols your application uses:",
            "",
            "| Kind | Symbol | Description |",
            "|------|--------|-------------|",
        ]
        for change in breaking:
            kind_val = change.kind.value if change.kind else ""
            lines.append(f"| `{kind_val}` | `{change.symbol}` | {change.description} |")
        lines.append("")
        return lines
    if total_changes > 0:
        return [
            f"## Relevant Changes (0 of {total_changes} total)",
            "",
            "None of the library's ABI changes affect your application.",
            "",
        ]
    return []


def _appcompat_irrelevant_lines(irrelevant: list[Change], show_irrelevant: bool) -> list[str]:
    """Build irrelevant changes section/note lines."""
    if irrelevant and not show_irrelevant:
        return [
            f"_{len(irrelevant)} library ABI change(s) do NOT affect your application. "
            "Use `--show-irrelevant` to see them._",
            "",
        ]
    if irrelevant and show_irrelevant:
        lines = [
            f"## Irrelevant Changes ({len(irrelevant)})",
            "",
            "These library changes do NOT affect your application:",
            "",
        ]
        for change in irrelevant:
            kind_val = change.kind.value if change.kind else ""
            lines.append(f"- **{kind_val}**: {change.description}")
        lines.append("")
        return lines
    return []
