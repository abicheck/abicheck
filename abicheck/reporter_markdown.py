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

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
from .contract_gating import is_evaluated
from .finding_identity import missing_contract_kind, report_finding_id
from .report_correlation import (
    _suppress_dangling_correlation_notes as _suppress_dangling_correlation_notes,
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


def to_stat(
    result: DiffResult, *, severity_config: SeverityConfig | None = None
) -> str:
    """One-line summary for CI gates.

    *severity_config*, when given, appends a ``gate: PASS|FAIL`` suffix
    reflecting the actual severity-aware exit code — without it, ``--stat``
    output has historically bypassed severity handling entirely (it
    short-circuits in ``service.render_output`` before format dispatch), so
    the verdict label alone could misreport whether the run actually blocks
    CI once severity configuration is in play.
    """
    from .report.document import ReportDocument
    from .report.render_text import render_stat_document

    summary = build_summary(result)
    d: dict[str, object] = {
        "verdict_label": _VERDICT_LABEL[result.verdict],
        "summary": {
            "breaking": summary.breaking,
            "source_breaks": summary.source_breaks,
            "risk_changes": summary.risk_count,
            "compatible_additions": summary.compatible_additions,
            "total_changes": summary.total_changes,
        },
        "redundant_count": result.redundant_count,
    }
    if severity_config is not None:
        from .severity import compute_exit_code

        exit_code = compute_exit_code(
            result.changes,
            severity_config,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        d["severity"] = {"exit_code": exit_code}
    return render_stat_document(ReportDocument.from_mapping(d))


# ---------------------------------------------------------------------------
# Show-only filter
# ---------------------------------------------------------------------------

# Kind-name suffixes that identify an additive vs. a removal finding — shared
# between ShowOnlyFilter's "added"/"removed"/"changed" action tokens and the
# JSON report's structured per-finding "operation" field (schema 2.3), so the
# two never drift apart.
_ADDED_SUFFIXES = ("_added", "_added_compatible")
_REMOVED_SUFFIXES = (
    "_removed",
    "_deleted",
    "_elf_only",
    "_elf_fallback",
    "_const_overload",
)

# Kinds whose name doesn't end in one of the suffixes above but still name a concrete symbol/entity appearing or disappearing (Codex review on #557: operation_for_kind() reported these as "modified"). Checked before the suffix rule. Deliberately does NOT include kinds naming a *property* gained/lost on an entity that still exists — e.g. the "*_lost_*" family (`field_lost_const`, `func_lost_inline`, ...) or the "*_introduced" family (`vptr_introduced`, `static_tls_introduced`, ...): those are trait changes on a persisting entity, which is what "modified" means here, not an addition/removal of the entity itself.
_OPERATION_OVERRIDES: dict[str, str] = {
    # Ends in "_added_compat", not "_added"/"_added_compatible".
    "symbol_version_required_added_compat": "added",
    # Ends in "_removed_without_replacement", not "_removed".
    "experimental_removed_without_replacement": "removed",
    # Ends in "_deleted_dwarf", not "_deleted".
    "func_deleted_dwarf": "removed",
    # A whole ISA-dispatch family's concrete symbols vanish (case83), not a
    # property change on a persisting symbol.
    "cpu_dispatch_isa_dropped": "removed",
    # A stable name is added alongside the still-present experimental alias
    # (case99) -- without the dedicated detector this would just be a plain
    # func_added; ADDITION_KINDS already classifies it as an addition
    # (Codex review on #557).
    "experimental_graduated": "added",
    # These four end in "_added" but each names a trait *gained by an
    # existing, persisting function* ("Function became virtual: {name}",
    # "noexcept specifier added: {name}", "Function became variadic (gained
    # ...): {name}" -- verified against their diff_symbols.py descriptions
    # and change_registry.py entries, none of which set is_addition=True /
    # belong to ADDITION_KINDS) -- the same "*_lost_*"/"*_introduced" trait-
    # change pattern above, just spelled with "_added" (Codex review, PR
    # #557). `func_pure_virtual_added` ("Function became pure virtual:
    # {name}") is the identical pattern applied to its sibling kind
    # `func_virtual_became_pure`, which already classifies correctly as
    # "modified" since it doesn't end in "_added".
    "func_noexcept_added": "modified",
    "func_virtual_added": "modified",
    "func_variadic_added": "modified",
    "func_pure_virtual_added": "modified",
    # A field inserted into an existing struct/class shifts every
    # subsequent field's offset -- this modifies the *layout of the
    # existing type*, not merely a new field appearing in isolation.
    # `type_field_added_compatible` (append-at-end, no offset shift) is the
    # dedicated addition-kind carve-out and is unaffected by this override
    # (it doesn't end in plain "_added"). (Codex review, PR #557.)
    "type_field_added": "modified",
    # The identical layout-modification pattern applied to virtual methods
    # instead of fields: a new virtual method on an already-existing class
    # grows/relayouts the vtable (gains a hidden vtable pointer if it had
    # none, or a new slot otherwise), breaking derived classes compiled
    # against the old layout -- KDE's "do not add virtuals to a non-leaf
    # class" rule. Not in ADDITION_KINDS (Codex review, PR #557).
    "virtual_method_added": "modified",
    # More of the same trait-gained-by-a-persisting-entity pattern, found on
    # a second audit pass (Codex review, PR #557): a constructor/conversion
    # operator gaining `explicit` (`ctor_explicit_added`), a template
    # parameter that was defaulted/deduced becoming mandatory
    # (`mandatory_template_param_added`), a Python-visible function gaining
    # a new *required* parameter (`python_api_parameter_added`), and a
    # function gaining a semantic contract attribute like nonnull/noreturn
    # (`func_contract_attribute_added`) all describe an already-existing
    # callable/template's signature or contract changing, not a new one
    # appearing. None of these four is in ADDITION_KINDS either.
    "ctor_explicit_added": "modified",
    "mandatory_template_param_added": "modified",
    "python_api_parameter_added": "modified",
    "func_contract_attribute_added": "modified",
    # Removed-side counterparts of the trait-change pattern: these end in
    # plain "_removed" (so the suffix rule alone reports "removed"), but
    # each names a trait *lost by* an entity that still exists — mirroring
    # `func_noexcept_added`/`func_variadic_added`/etc. above, just the
    # opposite direction of the same specifier gain/loss (Codex review, PR
    # #557).
    "func_noexcept_removed": "modified",
    "func_variadic_removed": "modified",
    "func_contract_attribute_removed": "modified",
    "ctor_explicit_removed": "modified",
    # A third audit pass turned up more of the same (Codex review, PR #557):
    # `func_virtual_removed` ("Vtable entry removed" -- the sibling of
    # `func_virtual_added` above, an existing function losing its
    # virtual-ness) and `param_default_value_removed`/
    # `python_api_default_removed` (an existing parameter of an existing
    # function/method losing its default value, making a previously
    # optional argument mandatory) all describe a trait lost by a
    # persisting entity, not the entity itself disappearing.
    "func_virtual_removed": "modified",
    "param_default_value_removed": "modified",
    "python_api_default_removed": "modified",
}


def operation_for_kind(kind_val: str) -> str:
    """Classify a ``ChangeKind.value`` string into "added"/"removed"/"modified".

    A kind is "added"/"removed" when it is listed in ``_OPERATION_OVERRIDES``
    or its name ends with one of the corresponding suffixes above; every
    other kind (parameter/type/layout changes, renames, trait gained/lost on
    a persisting entity, etc.) is "modified".
    """
    override = _OPERATION_OVERRIDES.get(kind_val)
    if override is not None:
        return override
    if any(kind_val.endswith(s) for s in _ADDED_SUFFIXES):
        return "added"
    if any(kind_val.endswith(s) for s in _REMOVED_SUFFIXES):
        return "removed"
    return "modified"


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

    def _check_severity(
        self,
        change: Change,
        policy: str,
        kind_sets: KindSets | None = None,
        policy_file: object | None = None,
    ) -> bool:
        """Return True if *change* matches the severity filter.

        Resolves through ``severity.effective_verdict_for_change`` — the same
        canonical resolver ``DiffResult._effective_verdict_for_change`` uses —
        so both an A4 per-finding ``effective_verdict`` override (ADR-027) and
        a kind-level ``PolicyFile.overrides`` entry are honoured. Without this,
        `--show-only` could disagree with the JSON severity field and
        filtered_summary counts for any change whose effective category
        differs from its raw kind's policy bucket (a demoted opaque/PIMPL
        layout change, or a kind moved by a policy-file override).
        """
        if not self.severities:
            return True
        from .severity import effective_verdict_for_change

        eff = effective_verdict_for_change(
            change,
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
        )
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
        op = operation_for_kind(kind_val)
        # NB: "changed" (the --show-only token) maps to operation "modified".
        return (
            (op == "added" and "added" in actions)
            or (op == "removed" and "removed" in actions)
            or (op == "modified" and "changed" in actions)
        )

    def matches(
        self,
        change: Change,
        policy: str = "strict_abi",
        kind_sets: KindSets | None = None,
        policy_file: object | None = None,
    ) -> bool:
        """Return True if *change* passes this filter."""
        if not self._check_severity(change, policy, kind_sets, policy_file):
            return False
        if not self._check_element(change.kind.value):
            return False
        return self._check_action(change.kind.value, self.actions)


def apply_show_only(
    changes: Sequence[Change],
    show_only: str,
    policy: str = "strict_abi",
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
) -> list[Change]:
    """Filter changes according to a --show-only token string.

    *kind_sets* / *policy_file*, when supplied by the caller (typically
    ``result._effective_kind_sets()`` / ``result.policy_file``), let the
    severity dimension resolve through the same effective-verdict logic as
    the rest of the report — including kind-level ``PolicyFile.overrides``
    and per-finding ``effective_verdict`` — so the filter never disagrees
    with the JSON severity field for the same change.
    """
    filt = ShowOnlyFilter.parse(show_only)
    return [
        c
        for c in changes
        if filt.matches(c, policy=policy, kind_sets=kind_sets, policy_file=policy_file)
    ]


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


def _contract_decision_text(
    relevance: Any, reason_code: str | None, assurance: Any
) -> str:
    """Core ``<relevance> (<reason_code>), assurance: <level>`` text, shared
    by every already-stamped-``Change`` rendering site in this module
    (CodeRabbit review: the same tag-building pattern was duplicated at
    several call sites). Deliberately excludes any ``Contract:``/``[contract:
    ...]`` wrapper -- callers render in visibly different shapes (a leading
    ``"Contract: "``, a bracketed ``"[contract: ...]"``), so each keeps its
    own exact prefix/suffix and casing."""
    tag = str(relevance.value)
    if reason_code:
        tag += f" ({reason_code})"
    if assurance is not None:
        tag += f", assurance: {assurance.value}"
    return tag


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
    # ADR-049 Phase 3 (Codex review, fresh evidence): --report-mode leaf
    # routes root TYPE_* changes through this function, never through
    # _format_change_md -- unlike the full/root-cause views, a leaf-mode
    # type finding's own contract decision (already stamped when
    # --contract was requested) was silently dropped. Mirrors
    # _format_change_md's own "no-op unless already stamped" idiom.
    if c.contract_relevance is not None:
        text = _contract_decision_text(
            c.contract_relevance, c.contract_reason_code, c.contract_assurance
        )
        lines.append(f"\n> Contract: {text}")
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
    *,
    severity_config: SeverityConfig | None = None,
) -> str:
    """Leaf-change mode: root type changes with affected interface lists.

    *severity_config*, when given, adds the same "Severity Configuration"
    summary section the full-mode report has (see
    :func:`_build_severity_summary_md`) — without it, ``report_mode="leaf"``
    returned before that section was ever built, so it silently had no
    severity information even when a caller passed ``severity_config``
    through :func:`to_markdown`.
    """
    from .checker import _ROOT_TYPE_CHANGE_KINDS

    lines, changes = _view_preamble(
        result,
        "leaf-change view",
        show_only=show_only,
        show_recommendation=show_recommendation,
    )

    if severity_config is not None:
        lines += _build_severity_summary_md(
            changes,
            severity_config,
            all_changes=list(result.changes),
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )

    # ADR-049 D1: leaf mode groups purely by ChangeKind, so without this a finding compatibility policy never scored still rendered under "Breaking Type Changes" beside a NO_CHANGE verdict -- the same contradiction the full-mode partition exists to prevent, reached by a different renderer (Codex review, fresh evidence). Partitioned before the kind grouping, and disclosed in its own non-verdict section below.
    from .report_model import ReportModel

    not_evaluated = ReportModel.classify_not_evaluated(changes)
    # Identity, not equality: `Change` is a plain dataclass, so two distinct
    # findings can compare equal and an `in`-based split would drop the wrong
    # one (and cost O(n^2) doing it).
    _excluded_ids = {id(c) for c in not_evaluated}
    scored = [c for c in changes if id(c) not in _excluded_ids]

    # Group root type changes by severity
    type_changes = [c for c in scored if c.kind in _ROOT_TYPE_CHANGE_KINDS]
    non_type_changes = [c for c in scored if c.kind not in _ROOT_TYPE_CHANGE_KINDS]

    if type_changes:
        lines += _build_leaf_type_sections(type_changes, result.policy)

    if non_type_changes:
        lines += ["## Non-Type Changes", ""]
        for c in non_type_changes:
            lines.append(_format_change_md(c))
        lines.append("")

    lines += _build_not_evaluated_section(not_evaluated)

    if not changes:
        if show_only and result.changes:
            lines.append("_No changes match the current filter._")
        else:
            lines.append("_No ABI changes detected._")

    _append_redundancy_note(lines, result)
    _append_suppression_note(lines, result)
    _append_out_of_surface_note(lines, result)

    if show_impact:
        lines += _build_impact_table(result, displayed_changes=changes)

    lines += _footer_lines()
    return "\n".join(lines)


#: The report's stable per-finding fingerprint. The implementation moved to
#: the dependency-free ``finding_identity`` leaf module so ``checker.py`` can
#: key ADR-049's decision receipt by the *same* id the report shows without
#: importing this module -- which would close a ``checker ->
#: reporter_markdown -> checker`` cycle the ``import-cycle-growth`` gate
#: rejects. Re-exported here (and, transitively, from ``reporter``) so every
#: existing import path keeps working unchanged.
_finding_id = report_finding_id


def _root_cause_key_and_display(
    caused_by_type: str | None,
    symbol: str | None,
    kind_value: str,
    finding_id: str,
    *,
    referenced_causes: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """Grouping key + display root for one root-cause finding: ``caused_by_type``
    when set, else its own ``symbol`` -- but only as a *grouping* key when
    some other finding's ``caused_by_type`` actually names that symbol
    (Codex review: two independent findings that merely share a symbol with
    no producer-set correlation, e.g. ``func_return_changed`` and
    ``func_params_changed`` both on ``foo``, must stay singleton -- the
    first-slice contract is that only ``caused_by_type`` correlates
    findings). Otherwise a unique per-finding key, with the symbol (or, if
    empty, the kind) still used as the *display* root. Shared by
    :func:`abicheck.reporter._to_json_root_cause`, :func:`_to_markdown_root_cause`,
    and the scoped-gate fold-in in ``cli_compare_fold.py``, which appends
    synthetic findings afterwards.
    """
    if caused_by_type:
        return caused_by_type, caused_by_type
    if symbol:
        if symbol in referenced_causes:
            return symbol, symbol
        return f"finding:{finding_id}", symbol
    return f"finding:{finding_id}", kind_value


def _group_changes_by_root_cause(
    changes: list[Change],
    *,
    extra_causes: frozenset[str] = frozenset(),
) -> list[tuple[str, str, list[Change]]]:
    """Group ``changes`` into root-cause buckets, in first-seen order.

    Returns ``(key, root_display, changes_in_group)`` triples. ``key`` is the
    raw grouping key (a stable hash of it becomes ``root_cause_id``);
    ``root_display`` is the human-readable root shown to a consumer. Shared
    by the JSON and markdown ``--report-mode root-cause`` renderers so the
    two formats can never disagree about which findings share a root cause
    (Codex review; see :func:`_root_cause_key_and_display` for the key/display
    rules, including the ``referenced_causes`` guard against grouping
    independent findings that merely share a symbol).

    *extra_causes* folds in ``caused_by_type`` values from findings outside
    *changes* itself (e.g. JSON's scoped-only ``--used-by``/
    ``--required-symbol`` changes, appended to the report only after this
    grouping runs) -- without this, a change in *changes* whose symbol only
    became a real correlation via one of those later-appended findings would
    already be locked into its own singleton group, unable to join the
    combined root cause the scoped-gate fold-in later assembles (Codex
    review: JSON's two-phase build let a scoped-only finding's
    ``caused_by_type`` disagree with SARIF's single-pass grouping of the
    identical changes).
    """
    referenced_causes = (
        frozenset(c.caused_by_type for c in changes if c.caused_by_type) | extra_causes
    )
    groups: dict[str, list[Change]] = {}
    roots: dict[str, str] = {}
    order: list[str] = []
    for c in changes:
        key, root_display = _root_cause_key_and_display(
            c.caused_by_type,
            c.symbol,
            c.kind.value,
            _finding_id(c),
            referenced_causes=referenced_causes,
        )
        if key not in groups:
            groups[key] = []
            roots[key] = root_display
            order.append(key)
        groups[key].append(c)
    return [(key, roots[key], groups[key]) for key in order]


def root_cause_for_change(
    c: Change, *, referenced_causes: frozenset[str] = frozenset()
) -> tuple[str, str] | None:
    """This change's ``(root_cause_id, root_display)``, or ``None`` when it
    has no real correlation signal (G29 Phase 3 follow-up, ADR-052).

    Uses the exact same grouping key ``--report-mode root-cause`` computes
    (:func:`_root_cause_key_and_display`), hashed the same way
    :func:`~abicheck.reporter._to_json_root_cause`/``sarif._root_cause_for``
    already do — so a finding's ``root_cause_id`` here is always identical to
    its ``root_causes[].root_cause_id`` in JSON root-cause mode, or its
    ``properties.rootCauseId`` in SARIF root-cause mode, for the same report.

    Deliberately returns ``None`` for the trivial self-referencing singleton
    case (no ``caused_by_type``, and *c*'s own ``symbol`` isn't referenced by
    any other finding's ``caused_by_type``) — unlike ``--report-mode
    root-cause``'s own grouping (which buckets *every* finding, including
    singletons, since that mode's whole point is showing the full grouping
    structure), a per-finding ``ImpactAssessment.root_cause_id`` naming
    nothing but the finding's own identity is not real information; see
    :func:`root_cause_lookup_for_changes`.
    """
    fid = _finding_id(c)
    key, root_display = _root_cause_key_and_display(
        c.caused_by_type,
        c.symbol,
        c.kind.value,
        fid,
        referenced_causes=referenced_causes,
    )
    if key == f"finding:{fid}":
        return None
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], root_display


def root_cause_lookup_for_changes(
    changes: list[Change], *, extra_causes: frozenset[str] = frozenset()
) -> dict[str, tuple[str, str]]:
    """``finding_id -> (root_cause_id, root_display)`` for every change in
    *changes* that has one (G29 Phase 3 follow-up, ADR-052).

    Built once per report (or per self-contained scope, e.g. a suppressed-
    changes list or a scoped-only fold-in) so per-change lookup during
    serialization is O(1) rather than re-deriving ``referenced_causes`` per
    finding. Feeds ``impact.engine.assess_change``'s ``root_cause`` parameter
    — see :func:`root_cause_for_change` for why a finding with no real
    correlation signal is simply absent from the returned dict rather than
    mapped to a self-referencing singleton id.
    """
    referenced_causes = (
        frozenset(c.caused_by_type for c in changes if c.caused_by_type) | extra_causes
    )
    lookup: dict[str, tuple[str, str]] = {}
    for c in changes:
        rc = root_cause_for_change(c, referenced_causes=referenced_causes)
        if rc is not None:
            lookup[_finding_id(c)] = rc
    return lookup


def root_cause_evidence_lookup_for_changes(
    changes: list[Change],
) -> dict[str, dict[str, object]]:
    """``finding_id -> root_cause_evidence`` for every change that is a
    member of a G29 Phase 6 ``RootCauseCorrelator`` group (G29 Phase 6
    follow-up: wiring the correlator's output into the JSON/SARIF
    ``impact_assessment`` surface).

    Deliberately independent of :func:`root_cause_lookup_for_changes` above:
    that function's ``root_cause_id``/``root_cause_display`` grouping covers
    *any* two findings sharing a ``caused_by_type``/``symbol``, for every
    ``ChangeKind``; :func:`~abicheck.impact.correlation.correlate_root_causes`
    covers only the load-failure kinds its own module docstring names
    (``FUNC_REMOVED``/``INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API``/
    ``CONSUMER_REQUIRED_SYMBOL_REMOVED``),
    ranked by evidence strength, and drops a symbol with only one correlated
    piece. Built directly from the actual ``Change`` objects the correlator
    grouped — not by matching root-cause ids after the fact — so this stays
    correct independent of whether the two functions' grouping keys happen
    to agree for a given finding.

    Scoped to *changes* only: unlike ``root_cause_lookup_for_changes``, this
    has no ``extra_causes`` parameter, so a correlated sibling that exists
    only as a scoped-only (``--used-by``/``--required-symbol``) finding
    appended to a report after this runs is not seen — deliberately left for
    a follow-up, mirroring this module's own "Deliberately not implemented
    this slice" precedent rather than growing this helper's contract
    unverified.
    """
    from .impact.correlation import correlate_root_causes

    lookup: dict[str, dict[str, object]] = {}
    for group in correlate_root_causes(changes):
        for member_change, level in group.members:
            lookup[_finding_id(member_change)] = {
                "evidence_level": level,
                "strongest_evidence_level": group.strongest_evidence_level,
                "evidence_levels": list(group.evidence_levels),
            }
    return lookup


def _resolve_scoped_gate_findings(
    result: DiffResult,
    severity_config: SeverityConfig | None,
    show_only: str | None,
) -> tuple[list[Change], list[str], bool, str]:
    """Resolve the scoped-only ``Change``s and missing-contract labels relevant
    to the ``--used-by``/``--required-symbol`` gate, deduped against
    ``result.changes`` and filtered by ``--show-only``.

    Factored out of ``cli_compare_fold.py``'s JSON branch so markdown/text/
    review output can render the identical actionable findings instead of
    only a bare count (Codex review: a scoped run whose only gated issue was
    a missing contract member or a scoped-only change like
    ``PE_ORDINAL_RETARGETED`` didn't name either one in the default text
    report, unlike JSON/SARIF/JUnit). Lives here (not ``cli_compare_fold.py``)
    so ``_to_markdown_root_cause`` below can also call it directly to merge
    these into its own root-cause groups, without ``cli_compare_fold``
    importing back into this leaf module -- ``cli_compare_fold.py`` imports
    it from ``reporter``'s re-export, same as every other name in this
    module.

    Returns ``(scoped_only_changes, missing_labels, blocks, missing_kind)``.
    """
    from .severity import missing_contract_exit_code

    existing_ids = {_finding_id(c) for c in result.changes}
    eff_sets = result._effective_kind_sets()
    scoped_only = list(getattr(result, "scoped_only_changes", ()) or ())
    if show_only and scoped_only:
        scoped_only = apply_show_only(
            scoped_only,
            show_only,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
    scoped_only = [c for c in scoped_only if _finding_id(c) not in existing_ids]

    gate_scope = getattr(result, "gate_scope", None)
    missing_kind = missing_contract_kind(gate_scope)
    blocks = severity_config is None or missing_contract_exit_code(severity_config) != 0
    # A missing-contract label has no backing Change/ChangeKind, so it can't
    # run through apply_show_only -- but --show-only's severity dimension
    # still applies: without this, a --show-only run that excludes breaking
    # findings would still include a blocking missing-contract entry the
    # filter was meant to exclude (Codex review, mirrors the identical
    # sarif.to_sarif fix). Element/action tokens don't cleanly apply to "a
    # symbol is simply absent", so only the severity dimension is checked.
    missing_severity_label = "breaking" if blocks else "compatible"
    show_only_severities = (
        ShowOnlyFilter.parse(show_only).severities if show_only else frozenset()
    )
    missing_labels = list(
        getattr(result, "scoped_missing_labels", ()) or ()
        if not show_only_severities or missing_severity_label in show_only_severities
        else ()
    )
    return scoped_only, missing_labels, blocks, missing_kind


def _to_markdown_root_cause(
    result: DiffResult,
    show_only: str | None = None,
    show_recommendation: bool = False,
    show_impact: bool = False,
    *,
    severity_config: SeverityConfig | None = None,
    contract_evaluation: bool = False,
) -> str:
    """``--report-mode root-cause`` markdown rendering (G29 Phase 3 slice 4, ADR-052).

    Groups findings under one heading per root cause instead of full mode's
    severity-bucketed sections -- root-cause mode's point is "what's the
    minimal set of things that actually broke", not "what severity bucket
    does each finding independently fall into".
    """
    lines, changes = _view_preamble(
        result,
        "root-cause view",
        show_only=show_only,
        show_recommendation=show_recommendation,
    )

    # G29 Phase 3 slice 3 follow-up (Codex review): a --used-by/
    # --required-symbol scoped-only change or missing-contract label whose
    # caused_by_type/symbol correlates with a change above must join that
    # same root-cause group here, not only appear separately in
    # cli_compare_fold.py's "## Additional scoped-gate findings" appendix --
    # otherwise the grouped section under-reports finding_count and hides
    # the correlation, unlike the JSON/SARIF paths (which fold these in).
    # Real Change objects (scoped_only) can simply be grouped alongside
    # `changes` in one pass; missing_labels have no Change to group with, so
    # they're keyed and merged in separately below. Resolved before the
    # severity table (Codex review, further follow-up) so a scoped run whose
    # only gating issue is one of these can pass the scoped counts below
    # instead of the table always reading the pre-scoped `result.changes`.
    scoped_only, missing_labels, blocks, missing_kind = _resolve_scoped_gate_findings(
        result,
        severity_config,
        show_only,
    )

    if severity_config is not None:
        lines += _build_severity_summary_md(
            changes,
            severity_config,
            all_changes=list(result.changes),
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
            scoped_counts=getattr(result, "scoped_severity_counts", None),
            scoped_blocking_categories=getattr(
                result, "scoped_blocking_categories", None
            ),
        )
    groups = _group_changes_by_root_cause(changes + scoped_only)
    has_root_cause_entries = bool(groups or missing_labels)
    if has_root_cause_entries:
        order: list[str] = []
        root_by_key: dict[str, str] = {}
        finding_lines_by_key: dict[str, list[str]] = {}
        count_by_key: dict[str, int] = {}
        for key, root_display, group_changes in groups:
            order.append(key)
            root_by_key[key] = root_display
            finding_lines_by_key[key] = [_format_change_md(c) for c in group_changes]
            count_by_key[key] = len(group_changes)

        if missing_labels:
            referenced_causes = frozenset(
                c.caused_by_type for c in changes + scoped_only if c.caused_by_type
            )
            severity_tag = "breaking" if blocks else "compatible"
            for label in missing_labels:
                key, root_display = _root_cause_key_and_display(
                    None,
                    label,
                    missing_kind,
                    label,
                    referenced_causes=referenced_causes,
                )
                line = (
                    f"- `{label}` is required but missing from the new "
                    f"library ({severity_tag})"
                )
                # ADR-049 Phase 3 (Codex review, fresh evidence): the
                # non-root-cause markdown/text/review fold-in
                # (cli_compare_fold._fold_scoped_compat_into_text) already
                # tags a missing-contract label with its stamped decision;
                # this root-cause path builds the identical label shape
                # independently and was missing the same treatment, so
                # --report-mode root-cause silently dropped the contract
                # decision for this one finding shape. A missing-contract
                # label has no Change object of its own to read an
                # already-stamped decision off of (unlike scoped_only,
                # rendered via _format_change_md above), so unlike every
                # other contract-rendering site in this fix, this one
                # genuinely needs the caller's own --contract
                # intent threaded through explicitly.
                if contract_evaluation:
                    from .contract_scoped_promotion import (
                        stamp_explicit_scope_contract_evaluation,
                    )

                    label_decision: dict[str, object] = {}
                    stamp_explicit_scope_contract_evaluation(label_decision)
                    line += (
                        f" [contract: {label_decision['contract_relevance']} "
                        f"({label_decision['contract_reason_code']}), "
                        f"assurance: {label_decision['contract_assurance']}]"
                    )
                if key in finding_lines_by_key:
                    finding_lines_by_key[key].append(line)
                    count_by_key[key] += 1
                else:
                    order.append(key)
                    root_by_key[key] = root_display
                    finding_lines_by_key[key] = [line]
                    count_by_key[key] = 1

        lines += [f"## Root Causes ({len(order)})", ""]
        for key in order:
            n = count_by_key[key]
            plural = "" if n == 1 else "s"
            lines.append(f"### `{root_by_key[key]}` ({n} finding{plural})")
            lines.append("")
            lines.extend(finding_lines_by_key[key])
            lines.append("")

    # Codex review: a scoped-only change or missing-contract label can be the
    # *only* displayed finding (result.changes itself empty/filtered out) --
    # gating this purely on `changes` produced a contradictory report with a
    # populated "## Root Causes" section immediately followed by "No ABI
    # changes detected."
    if not changes and not has_root_cause_entries:
        if show_only and result.changes:
            lines.append("_No changes match the current filter._")
        else:
            lines.append("_No ABI changes detected._")

    _append_redundancy_note(lines, result)
    _append_suppression_note(lines, result)
    _append_out_of_surface_note(lines, result)

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
            "(derived from root type changes). Set `scope.show_redundant: true` in\n"
            "> `.abicheck.yml` to show all."
        )


def _append_out_of_surface_note(lines: list[str], result: DiffResult) -> None:
    if result.scope_to_public_surface and result.out_of_surface_count:
        lines += ["", f"> ℹ️ {result.out_of_surface_count} finding(s) filtered as non-public ABI surface (`--scope-public-headers`). Pass `--show-filtered` to list them."]


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
                line = f">   - `{sc.symbol}` — {sc.description}"
                relevance = getattr(sc, "contract_relevance", None)
                if relevance is not None:
                    reason_code = getattr(sc, "contract_reason_code", None)
                    assurance = getattr(sc, "contract_assurance", None)
                    line += f" [contract: {_contract_decision_text(relevance, reason_code, assurance)}]"
                lines.append(line)


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
    all_changes: list[Change] | None = None,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    scoped_counts: dict[str, int] | None = None,
    scoped_blocking_categories: tuple[str, ...] | None = None,
) -> list[str]:
    """Build a severity configuration summary table for markdown output.

    *changes* are the (possibly ``--show-only``-filtered) changes used for
    the displayed ``Count`` column. *all_changes*, when provided, is the
    unfiltered set used for the ``Exit Impact`` column so that filtering the
    display doesn't make this table claim "no exit impact" for a category
    that still fails the actual (unfiltered) severity gate.

    *scoped_counts*/*scoped_blocking_categories* (Codex review), when given
    (from ``result.scoped_severity_counts``/``scoped_blocking_categories``
    on a ``--used-by``/``--required-symbol`` run), override both columns
    with the scoped gate's own numbers -- otherwise this table always
    reflects the full-library ``changes``, so a scoped run whose only
    gating issue is a scoped-only change or missing-contract label (neither
    of which is in ``result.changes``) would show every category at 0 and
    "no exit impact" while the report elsewhere names a real, blocking
    finding.
    """
    from .severity import (
        SeverityLevel,
        categorize_changes,
        gate_eligible_changes,
    )

    categorized = categorize_changes(
        changes,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )
    # ADR-049 D1: the `Count` column above is factual over what is
    # displayed, but `Exit Impact` is a claim about the *gate* -- so it has
    # to be classified over the same set `severity.compute_exit_code` scores.
    # Without this, a comparison whose only finding is a proven-out-of-contract
    # TYPE_SIZE_CHANGED rendered "causes non-zero exit" beside an exit code of
    # 0 and a NO_CHANGE verdict (Codex review, fresh evidence).
    exit_categorized = (
        categorize_changes(
            gate_eligible_changes(all_changes),
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
        )
        if all_changes is not None
        else categorized
    )
    lines = [
        "## Severity Configuration",
        "",
        "| Category | Severity | Count | Exit Impact |",
        "|----------|----------|-------|-------------|",
    ]

    _CATEGORY_INFO: list[tuple[str, str, list[HasKind], list[HasKind]]] = [
        (
            "ABI/API Incompatibilities",
            "abi_breaking",
            categorized.abi_breaking,
            exit_categorized.abi_breaking,
        ),
        (
            "Potential Incompatibilities",
            "potential_breaking",
            categorized.potential_breaking,
            exit_categorized.potential_breaking,
        ),
        (
            "Quality Issues",
            "quality_issues",
            categorized.quality_issues,
            exit_categorized.quality_issues,
        ),
        (
            "Additions",
            "addition",
            categorized.addition,
            exit_categorized.addition,
        ),
    ]

    for label, attr, cat_changes, exit_cat_changes in _CATEGORY_INFO:
        level = getattr(severity_config, attr, SeverityLevel.INFO)
        level_val = level.value if hasattr(level, "value") else str(level)
        emoji = _SEVERITY_EMOJI.get(level_val, "")
        count = (
            scoped_counts.get(attr, 0)
            if scoped_counts is not None
            else len(cat_changes)
        )
        impact = (
            "causes non-zero exit"
            if (
                attr in scoped_blocking_categories
                if scoped_blocking_categories is not None
                else level_val == "error" and len(exit_cat_changes) > 0
            )
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
        "| ✅ COMPATIBLE | No incompatible ABI/API changes — may include additions and quality findings (backward compatible) |",
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
            lines.append(_format_change_md_oneline(c))
        lines.append("")

    if compatible:
        from .checker_policy import ADDITION_KINDS as _ADDITION_KINDS

        quality = [c for c in compatible if c.kind not in _ADDITION_KINDS]
        additions_list = [c for c in compatible if c.kind in _ADDITION_KINDS]
        if quality:
            sev_label = _section_severity_label(severity_config, "quality_issues")
            lines += [f"## {_QUALITY_ICON} Quality Issues{sev_label}", ""]
            for c in quality:
                lines.append(_format_change_md_oneline(c))
            lines.append("")
        if additions_list:
            sev_label = _section_severity_label(severity_config, "addition")
            lines += [f"## {_ADDITION_ICON} Additions{sev_label}", ""]
            for c in additions_list:
                # Same per-change detail as Breaking/Source-Level Breaks
                # (kind, location, impact) — a bare description dropped the
                # kind and any per-kind caveat (e.g. enum_member_added's
                # "may shift subsequent values" note), silently losing
                # information a reviewer needs to approve new public API
                # surface.
                lines.append(_format_change_md(c))
            lines.append("")

    return lines


def _build_not_evaluated_section(not_evaluated: list[Change]) -> list[str]:
    """Disclose the findings compatibility policy did not score (ADR-049 D1).

    These are real detector facts that carry no verdict: contract evaluation
    either proved the entity outside the declared contract, or could not
    resolve it from the evidence the run had. They are deliberately absent
    from the four verdict sections above -- filing an unscored finding under
    "Breaking Changes" would contradict the verdict printed at the top of the
    same report -- so this section is what keeps them visible, with the
    relevance and reason code that explain *why* they did not gate.

    Empty (and so entirely absent) unless the run opted into
    ``--contract``.
    """
    if not not_evaluated:
        return []
    lines: list[str] = [
        "## 🔍 Not Evaluated (Contract)",
        "",
        "> These findings were detected but **not scored** by compatibility",
        "> policy: each is either proven outside the declared contract or",
        "> unresolved for want of evidence (ADR-049). They contribute nothing",
        "> to the verdict or the gate. Incomplete evidence is reported",
        "> separately on the contract-coverage axis, which has its own exit",
        "> code — uncertainty is never silently treated as compatible.",
        "",
    ]
    for c in not_evaluated:
        relevance = getattr(c, "contract_relevance", None)
        reason = getattr(c, "contract_reason_code", None)
        label = getattr(relevance, "value", None) or "UNKNOWN"
        suffix = f" ({reason})" if reason else ""
        lines.append(_format_change_md_oneline(c))
        lines.append(f"  > Contract: {label}{suffix}")
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
    result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
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

    # Coverage-warning banner (Codex review): a clean verdict can still rest on incomplete evidence -- e.g. compare.note_if_same_binary_compared's byte-identical-inputs warning -- and this digest is exactly the GitHub-facing summary a reviewer approves a merge from, so it must not read as unconditionally clean when one of these is present.
    if result.coverage_warnings:
        lines += [f"> ⚠️ {w}" for w in result.coverage_warnings]
        lines.append("")

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

    # Top impacted symbols (breaking + API), capped for readability. Filters
    # by each change's *effective* verdict (DiffResult._effective_verdict_for_change)
    # rather than raw kind-set membership, so a per-finding override (A4
    # pattern-verdict modulation, frozen-namespace guard) is reflected here
    # the same way it already is in the counts table and merge-effect phrase
    # above — otherwise this section could list a finding the rest of the
    # digest reports as compatible, or omit one it reports as breaking.
    # ADR-049 D1: and over the findings compatibility policy actually scored,
    # for the same reason -- the merge-effect phrase above is derived from the
    # verdict, which a NOT_EVALUATED finding did not reach, so listing one
    # here printed "safe to merge" directly above the symbol it says is
    # impacted (Codex review). The excluded finding keeps its own disclosed
    # section elsewhere in the report; this list is the digest of what gated.
    from .report.finding import report_findings_for
    impacted = [
        f.change
        for f in report_findings_for(result)
        if is_evaluated(f.change)
        if f.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
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


def _markdown_alternate_rendering(
    result: DiffResult,
    *,
    stat: bool,
    report_mode: str,
    show_impact: bool,
    show_only: str | None,
    show_recommendation: bool,
    severity_config: Any,
    contract_evaluation: bool,
) -> str | None:
    """Render one of the non-default markdown views, or ``None`` for the default.

    ``--stat`` and the ``leaf`` / ``root-cause`` report modes each produce a
    complete document of their own; the caller returns it as-is (after its own
    demangling pass) rather than continuing into the full report.
    """
    if stat:
        return to_stat(result, severity_config=severity_config)
    if report_mode == "leaf":
        return _to_markdown_leaf(
            result,
            show_impact=show_impact,
            show_only=show_only,
            show_recommendation=show_recommendation,
            severity_config=severity_config,
        )
    if report_mode == "root-cause":
        return _to_markdown_root_cause(
            result,
            show_only=show_only,
            show_recommendation=show_recommendation,
            show_impact=show_impact,
            severity_config=severity_config,
            contract_evaluation=contract_evaluation,
        )
    return None


def _markdown_headline_table(result: DiffResult, emoji: str, label: str) -> list[str]:
    """The report's headline summary table.

    ADR-049 D1/D11: when contract evaluation excluded findings from the
    compatibility axis, say so in the headline table. The four counts above
    are now over the *evaluated* findings only, so a reader who sees a
    `NO_CHANGE` verdict beside a populated "Not Evaluated" section has the
    count that reconciles them rather than an apparent contradiction. The
    row is absent for every run that did not opt in, where it is always 0.
    """
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
    ]
    not_evaluated_total = len(result.not_evaluated)
    if not_evaluated_total:
        lines.append(f"| Not evaluated (contract) | {not_evaluated_total} |")
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
    demangle: bool = False,
    contract_evaluation: bool = False,
) -> str:
    # Human-facing only: optionally demangle Itanium C++ symbols in the rendered
    # output. Machine formats (JSON/SARIF/JUnit) keep the raw mangled symbols.
    def _out(text: str) -> str:
        if not demangle:
            return text
        from .demangle import demangle_text

        return demangle_text(text)

    alternate = _markdown_alternate_rendering(
        result,
        stat=stat,
        report_mode=report_mode,
        show_impact=show_impact,
        show_only=show_only,
        show_recommendation=show_recommendation,
        severity_config=severity_config,
        contract_evaluation=contract_evaluation,
    )
    if alternate is not None:
        return _out(alternate)

    v = result.verdict
    emoji = _VERDICT_EMOJI[v]
    label = _VERDICT_LABEL[v]

    old_meta = getattr(result, "old_metadata", None)
    new_meta = getattr(result, "new_metadata", None)

    # Apply show-only filter if provided (display-only, does not affect verdict)
    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        # A filter can keep a finding while dropping the co-reported one its
        # own correlated_change_kind names -- clear the now-dangling note
        # rather than reference a finding this view no longer shows.
        changes = _suppress_dangling_correlation_notes(changes)

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

    lines = _markdown_headline_table(result, emoji, label)
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
            all_changes=list(result.changes),
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

    lines += _build_not_evaluated_section(model.not_evaluated)

    lines += _build_environment_drift_section(changes)

    if not changes:
        if show_only and result.changes:
            lines.append("_No changes match the current filter._")
        else:
            lines.append("_No ABI changes detected._")

    _append_redundancy_note(lines, result)
    _append_suppression_note(lines, result)
    _append_out_of_surface_note(lines, result)

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
    if result.policy_file and result.policy_file.reclassify:
        # Codex review: mirrors the JSON `policy_reclassify` disclosure
        # (reporter.py's `_add_policy_overrides`) -- the active rule set,
        # not a per-finding "which rule fired" attribution (see that
        # function's docstring / schema 2.30 history entry). Filtered
        # through active_reclassify_rules so an expired rule -- which
        # ReclassifyRule.matches() would already refuse to apply -- isn't
        # disclosed as though it were still in effect.
        from .reclassify import active_reclassify_rules

        active = active_reclassify_rules(result.policy_file.reclassify)
        if active:
            # CodeRabbit review: code-span-wrap describe()'s raw selector
            # text (e.g. `_ZN6oneapi3dal.*`) -- unescaped, `_`/`*` read as
            # Markdown emphasis, same as `Policy overrides` above already does.
            rules = "; ".join(f"`{rule.describe()}`" for rule in active)
            lines.append(f"> **Policy reclassify**: {rules}")
    lines.append("")


_BUMP_EMOJI = {"major": "🔴", "minor": "🟢", "patch": "🟢", "none": "✅"}


def _view_preamble(
    result: DiffResult,
    view_label: str,
    *,
    show_only: str | None,
    show_recommendation: bool,
) -> tuple[list[str], list[Change]]:
    """Opening block shared by the leaf-change and root-cause markdown views.

    Both views open identically — the titled version/verdict table, the optional
    recommendation section, then the ``--show-only`` filter applied to the
    change list with its "Filtered by" note. Stated once so the two views cannot
    drift apart in what a filter does or how the header reads (CodeFactor:
    duplicate code). Full mode deliberately does not share this: its table
    carries four extra count rows and it applies ``--show-only`` silently, with
    no note line.

    Returns the opening lines and the (possibly filtered) changes to render.
    """
    lines: list[str] = [
        f"# ABI Report: {result.library} ({view_label})",
        "",
        "| | |",
        "|---|---|",
        f"| **Old version** | `{result.old_version}` |",
        f"| **New version** | `{result.new_version}` |",
        f"| **Verdict** | {_VERDICT_EMOJI[result.verdict]} `{_VERDICT_LABEL[result.verdict]}` |",
        "",
    ]

    # Coverage-warning banner (Codex review): _append_confidence_section only runs in full mode, so leaf/root-cause views otherwise never surface a coverage_warnings entry (e.g. note_if_same_binary_compared's byte-identical-inputs warning) at all -- shared here rather than duplicated into both _to_markdown_leaf and _to_markdown_root_cause.
    if result.coverage_warnings:
        lines += [f"> ⚠️ {w}" for w in result.coverage_warnings]
        lines.append("")

    if show_recommendation:
        _append_recommendation_section(lines, result)

    changes = list(result.changes)
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        lines.append(
            f"> Filtered by: `--show-only {show_only}` ({len(changes)} of {len(result.changes)} changes shown)"
        )
        lines.append("")
        # A filter can keep a finding while dropping the co-reported one its
        # own correlated_change_kind names -- clear the now-dangling note
        # rather than reference a finding this view no longer shows.
        changes = _suppress_dangling_correlation_notes(changes)

    return lines, changes


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
        f"| Recommendation state | `{rec.state.value}` |",
        "",
        f"{rec.rationale}",
        "",
    ]


def _format_change_md_oneline(c: object) -> str:
    """Format a single change as a bare ``- **kind**: description`` line, plus
    a "See also" correlation note when ``correlated_change_kind`` is set.

    Used by the sections (Deployment Risk, Quality Issues, Not Evaluated)
    that deliberately render a change as a single terse line rather than
    routing through the fuller :func:`_format_change_md` (impact/affected-
    symbols/contract detail) -- but the cross-detector correlation must
    still reach every section a correlated finding (currently only
    ``LAYOUT_UNVERIFIABLE``) can land in, or a policy/contract
    configuration that routes it into one of these terse sections silently
    drops the "See also" note the fuller formatter carries (Codex review,
    fresh evidence).
    """
    kind = getattr(c, "kind", None)
    kind_val = kind.value if kind else ""
    desc = getattr(c, "description", "")
    line = f"- **{kind_val}**: {desc}"
    correlated = getattr(c, "correlated_change_kind", None)
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"
    return line


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

    # ADR-049 Phase 3 (Codex review, fresh evidence): --contract's
    # own help text promises every finding is stamped with a contract
    # decision, but only the JSON report (reporter.py's
    # _add_contract_evaluation_fields) ever rendered it -- an ordinary
    # `compare --contract` run (default markdown format) was
    # byte-for-byte identical to one without the flag. A no-op when *c* was
    # never stamped (contract_evaluation not requested), mirroring that
    # helper's own documented default.
    contract_relevance = getattr(c, "contract_relevance", None)
    if contract_relevance is not None:
        reason_code = getattr(c, "contract_reason_code", None)
        contract_assurance = getattr(c, "contract_assurance", None)
        line += f"\n  > Contract: {_contract_decision_text(contract_relevance, reason_code, contract_assurance)}"

    # Cross-detector correlation (e.g. LAYOUT_UNVERIFIABLE annotated by
    # post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged as
    # sharing its evidence gap with a co-reported TYPE_VTABLE_CHANGED). Only
    # JSON (reporter.py) and SARIF (sarif.py) rendered this field before —
    # the default `compare --format markdown` report showed the two findings
    # with no visible link between them (Codex review).
    correlated = getattr(c, "correlated_change_kind", None)
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"

    return line
