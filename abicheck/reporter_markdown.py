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


def to_stat(result: DiffResult, *, severity_config: SeverityConfig | None = None) -> str:
    """One-line summary for CI gates.

    *severity_config*, when given, appends a ``gate: PASS|FAIL`` suffix
    reflecting the actual severity-aware exit code — without it, ``--stat``
    output has historically bypassed severity handling entirely (it
    short-circuits in ``service.render_output`` before format dispatch), so
    the verdict label alone could misreport whether the run actually blocks
    CI once severity configuration is in play.
    """
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
    gate_note = ""
    if severity_config is not None:
        from .severity import compute_exit_code

        exit_code = compute_exit_code(
            result.changes,
            severity_config,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        gate_note = (
            f" [gate: FAIL (exit {exit_code})]" if exit_code else " [gate: PASS]"
        )
    return (
        f"{label}: {detail} ({summary.total_changes} total)"
        f"{redundant_note}{gate_note}"
    )


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

# Kinds whose name doesn't end in one of the suffixes above but still name a
# concrete symbol/entity appearing or disappearing (Codex review on #557:
# operation_for_kind() reported these as "modified"). Checked before the
# suffix rule. Deliberately does NOT include kinds naming a *property*
# gained/lost on an entity that still exists — e.g. the "*_lost_*" family
# (`field_lost_const`, `func_lost_inline`, ...) or the "*_introduced" family
# (`vptr_introduced`, `static_tls_introduced`, ...): those are trait changes
# on a persisting entity, which is what "modified" means here, not an
# addition/removal of the entity itself.
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
            change, policy=policy, kind_sets=kind_sets, policy_file=policy_file,
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

    if severity_config is not None:
        lines += _build_severity_summary_md(
            changes,
            severity_config,
            all_changes=list(result.changes),
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )

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


def _finding_id(c: object) -> str:
    """Stable per-finding fingerprint (schema 2.3, additive).

    Deterministic across repeated runs of the same comparison, so a
    consumer can tell "is this the same finding" across two report runs
    (e.g. to correlate a suppression/waiver, or diff two CI runs' findings)
    without relying on array order or index — neither of which abicheck
    guarantees stays stable release to release.

    Derived only from fields that identify the finding's *identity* (kind,
    symbol, old/new value, source location, description) — deliberately
    excluding ``severity``/``evidence_status``, which are policy-derived and
    would make the same underlying finding hash differently under a
    different ``--policy``.

    ``description`` is included as a discriminator: two findings of the same
    kind on the same symbol with the same old/new value and no distinct
    source location (e.g. ``param_pointer_level_changed`` on two different
    parameters of one function, both going from pointer-depth 1 to 2) would
    otherwise collide on an identical id even though they are different
    findings — ``description`` embeds the per-finding detail (parameter
    name/index, member name, …) that disambiguates them.

    Lives here (not ``reporter.py``) so this leaf module can also key
    ``--report-mode root-cause`` markdown groups without importing back from
    ``reporter`` (which would form an import cycle); ``reporter`` re-exports
    it for backward compat, same as every other name in this module.
    """
    key = "\x1f".join(
        [
            str(getattr(getattr(c, "kind", None), "value", getattr(c, "kind", ""))),
            str(getattr(c, "symbol", None) or ""),
            str(getattr(c, "old_value", None) or ""),
            str(getattr(c, "new_value", None) or ""),
            str(getattr(c, "source_location", None) or ""),
            str(getattr(c, "description", None) or ""),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


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
    """
    referenced_causes = frozenset(c.caused_by_type for c in changes if c.caused_by_type)
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


def _to_markdown_root_cause(
    result: DiffResult,
    show_only: str | None = None,
    show_recommendation: bool = False,
    show_impact: bool = False,
    *,
    severity_config: SeverityConfig | None = None,
) -> str:
    """``--report-mode root-cause`` markdown rendering (G29 Phase 3 slice 4, ADR-051).

    Groups findings under one heading per root cause instead of full mode's
    severity-bucketed sections -- root-cause mode's point is "what's the
    minimal set of things that actually broke", not "what severity bucket
    does each finding independently fall into".
    """
    v = result.verdict
    emoji = _VERDICT_EMOJI[v]
    label = _VERDICT_LABEL[v]

    lines: list[str] = [
        f"# ABI Report: {result.library} (root-cause view)",
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

    if severity_config is not None:
        lines += _build_severity_summary_md(
            changes,
            severity_config,
            all_changes=list(result.changes),
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )

    groups = _group_changes_by_root_cause(changes)
    if groups:
        lines += [f"## Root Causes ({len(groups)})", ""]
        for _key, root_display, group_changes in groups:
            plural = "" if len(group_changes) == 1 else "s"
            lines.append(f"### `{root_display}` ({len(group_changes)} finding{plural})")
            lines.append("")
            for c in group_changes:
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
    all_changes: list[Change] | None = None,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
) -> list[str]:
    """Build a severity configuration summary table for markdown output.

    *changes* are the (possibly ``--show-only``-filtered) changes used for
    the displayed ``Count`` column. *all_changes*, when provided, is the
    unfiltered set used for the ``Exit Impact`` column so that filtering the
    display doesn't make this table claim "no exit impact" for a category
    that still fails the actual (unfiltered) severity gate.
    """
    from .severity import SeverityLevel, categorize_changes

    categorized = categorize_changes(
        changes,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
    )
    exit_categorized = (
        categorize_changes(
            all_changes,
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
        count = len(cat_changes)
        impact = (
            "causes non-zero exit"
            if level_val == "error" and len(exit_cat_changes) > 0
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
                # Same per-change detail as Breaking/Source-Level Breaks
                # (kind, location, impact) — a bare description dropped the
                # kind and any per-kind caveat (e.g. enum_member_added's
                # "may shift subsequent values" note), silently losing
                # information a reviewer needs to approve new public API
                # surface.
                lines.append(_format_change_md(c))
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

    # Top impacted symbols (breaking + API), capped for readability. Filters
    # by each change's *effective* verdict (DiffResult._effective_verdict_for_change)
    # rather than raw kind-set membership, so a per-finding override (A4
    # pattern-verdict modulation, frozen-namespace guard) is reflected here
    # the same way it already is in the counts table and merge-effect phrase
    # above — otherwise this section could list a finding the rest of the
    # digest reports as compatible, or omit one it reports as breaking.
    impacted = [
        c
        for c in result.changes
        if result._effective_verdict_for_change(c)
        in (Verdict.BREAKING, Verdict.API_BREAK)
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
        return _out(to_stat(result, severity_config=severity_config))

    if report_mode == "leaf":
        return _out(
            _to_markdown_leaf(
                result,
                show_impact=show_impact,
                show_only=show_only,
                show_recommendation=show_recommendation,
                severity_config=severity_config,
            )
        )

    if report_mode == "root-cause":
        return _out(
            _to_markdown_root_cause(
                result,
                show_only=show_only,
                show_recommendation=show_recommendation,
                show_impact=show_impact,
                severity_config=severity_config,
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
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )

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
