# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Report view: the one place a renderer reads a result's findings from.

Every output format (JSON, Markdown, JUnit, HTML, SARIF) needs the same
presentation facts before it can render anything: which changes a
``--show-only`` filter keeps, which dangling cross-references that leaves,
the stable per-finding id, and how findings group under a root cause. This
module owns those facts. A renderer calls it once and formats the result; it
does not re-derive a filter, re-hash an id, or import another renderer's
private helpers to get one.

Everything here is a pure function of its arguments. It makes no
compatibility decision and never changes a finding's kind, severity, or
presence in the report: ``--show-only`` is display-only, and a finding it
hides keeps its disposition in the ledger (ADR-067 D2).

Imports nothing from ``reporter`` or ``reporter_markdown``, so both can depend
on it without an import cycle.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from ..compare.finding_identity import missing_contract_kind, report_finding_id
from ..policy.evaluate import effective_kind_sets
from ..policy.severity import Verdict
from .change_operation import (
    ACTION_TOKEN_OPERATIONS,
    ELEMENT_TOKEN_ENTITIES,
    entity_for_change,
    operation_for_change,
)
from .correlation import suppress_dangling_correlation_notes

if TYPE_CHECKING:
    from ..checker import Change, DiffResult
    from ..policy.severity import KindSets, SeverityConfig


#: The report's stable per-finding fingerprint. Implemented in the
#: dependency-free ``compare.finding_identity`` leaf so ``checker.py`` can key
#: ADR-049's decision receipt by the same id the report shows; re-exposed here
#: as the one name every renderer imports.
finding_id = report_finding_id


def root_cause_id_for_key(key: str) -> str:
    """The stable 16-hex-digit ``root_cause_id_for_key`` a root-cause grouping key
    publishes. JSON, Markdown, JUnit and SARIF derive it through this function,
    so the same group carries the same id in each."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ShowOnlyFilter:
    """Parsed --show-only tokens.

    Tokens fall into three dimensions; within each dimension OR logic applies,
    across dimensions AND logic applies.
    """

    severities: frozenset[str]  # breaking, api-break, risk, compatible
    elements: frozenset[str]  # functions, variables, types, enums, elf
    actions: frozenset[str]  # added, removed, changed, unchanged

    @classmethod
    def parse(cls, raw: str) -> ShowOnlyFilter:
        """Parse a comma-separated --show-only string into a filter."""
        severity_tokens = {"breaking", "api-break", "risk", "compatible"}
        element_tokens = set(ELEMENT_TOKEN_ENTITIES)
        action_tokens = set(ACTION_TOKEN_OPERATIONS)

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
                raise ValueError(f"Unknown output.view show= token: {tok!r}")

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
        today: date | None = None,
    ) -> bool:
        """Return True if *change* matches the severity filter.

        Resolves through ``severity.effective_verdict_for_change`` — the same
        canonical resolver ``policy.evaluate.effective_verdict`` uses —
        so both an A4 per-finding ``effective_verdict`` override (ADR-027) and
        a kind-level ``PolicyFile.overrides`` entry are honoured. Without this,
        `--show-only` could disagree with the JSON severity field and
        filtered_summary counts for any change whose effective category
        differs from its raw kind's policy bucket (a demoted opaque/PIMPL
        layout change, or a kind moved by a policy-file override). *today*:
        an envelope's ``resolved_today`` (ADR-061 gap C, Codex, fresh).
        """
        if not self.severities:
            return True
        from ..policy.severity import effective_verdict_for_change

        eff = effective_verdict_for_change(
            change,
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
            today=today,
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

    def _check_element(self, change: object, kind_val: str) -> bool:
        """Return True if *change* matches the element filter.

        Plan slice 7o: resolves through the change catalog's own declared
        :class:`ChangeEntity` (:func:`entity_for_change`), not through a
        second interpretation of the kind's *name*. Takes the whole finding
        rather than its kind because a polymorphic kind's entity is a
        property of the finding, not of the kind.
        """
        if not self.elements:
            return True
        entity = entity_for_change(change, kind_val)
        if entity is None:
            return False
        return any(
            ELEMENT_TOKEN_ENTITIES[elem].value == entity for elem in self.elements
        )

    @staticmethod
    def _check_action(
        kind_val: str, actions: frozenset[str], change: object | None = None
    ) -> bool:
        """Return True if the finding matches the action filter.

        Resolves through :func:`operation_for_change` -- the same per-finding
        operation the JSON report serializes -- so a cross-source finding
        stated ``introduced``/``resolved``/``persistent`` filters as
        ``added``/``removed``/``unchanged``, not as its kind's declared
        ``modified``. With no *change*, the kind's declared
        :class:`ChangeOperation` answers (plan slice 7o).
        """
        if not actions:
            return True
        operation = operation_for_change(change, kind_val)
        return any(ACTION_TOKEN_OPERATIONS[action] == operation for action in actions)

    def matches(
        self,
        change: Change,
        policy: str = "strict_abi",
        kind_sets: KindSets | None = None,
        policy_file: object | None = None,
        today: date | None = None,
    ) -> bool:
        """Return True if *change* passes this filter."""
        if not self._check_severity(change, policy, kind_sets, policy_file, today):
            return False
        if not self._check_element(change, change.kind.value):
            return False
        return self._check_action(change.kind.value, self.actions, change)


#: Separator between OR'd ``ShowOnlyFilter`` groups in a single ``show_only``
#: string (CodeRabbit/Codex review, PR #1154). ``output.view show=...`` is
#: repeatable; each occurrence is one AND-across-dimensions/OR-within-
#: dimension group (``ShowOnlyFilter``'s own pre-existing single-string
#: grammar, unchanged), and repeat occurrences must OR *those groups*
#: together -- joining them with "," instead (the bug this fixes) collapsed
#: two different-dimension groups into one AND-group instead, and two
#: same-dimension groups into a wider OR *within* that one dimension, either
#: way losing the promised "match either group" semantics. ";" was never a
#: legal character in a single group's own token grammar (severity/element/
#: action words only), so splitting on it first is a strict, backward-
#: compatible generalization: a string with no ";" is exactly one group,
#: identical to every pre-existing single-string caller/test.
SHOW_ONLY_GROUP_SEP = ";"


def parse_show_only_groups(show_only: str) -> tuple[ShowOnlyFilter, ...]:
    """Parse a ``show_only`` string into its OR'd ``ShowOnlyFilter`` groups.

    See ``SHOW_ONLY_GROUP_SEP``'s own comment for the grammar. Each group is parsed
    with the existing, unchanged ``ShowOnlyFilter.parse`` -- a bad token
    inside any group raises the identical ``ValueError`` it always did.
    """
    return tuple(
        ShowOnlyFilter.parse(part) for part in show_only.split(SHOW_ONLY_GROUP_SEP)
    )


def render_show_only_cli_hint(show_only: str) -> str:
    """Render *show_only* back as a literally re-runnable ``--set output.view=...``
    invocation.

    The raw *show_only* string joins repeated ``show=`` view tokens with
    :data:`SHOW_ONLY_GROUP_SEP` (``";"``), an internal transport separator --
    ``ShowOnlyFilter.parse`` rejects it as a single value, and an unquoted
    shell treats a bare ``;`` as a command separator. Every "how to reproduce
    this filter" hint (Markdown/HTML "Filtered by" notes) therefore renders
    one ``"show=..."`` list item per group inside a single quoted
    ``--set 'output.view=[...]'`` (ADR-091 Phase 5 moved ``--view`` to the
    ``output.view`` config key), never the internal separator.
    """
    groups = show_only.split(SHOW_ONLY_GROUP_SEP)
    # ADR-091 Phase 5: `--view` is the `output.view` config key now, reached
    # through `--set`. One value carries every OR'd group, as a YAML list.
    items = ", ".join(f'"show={group}"' for group in groups)
    return f"--set 'output.view=[{items}]'"


def show_only_matches(
    show_only: str,
    change: Change,
    policy: str = "strict_abi",
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    today: date | None = None,
) -> bool:
    """Return True if *change* matches ANY OR'd group of *show_only*."""
    return any(
        group.matches(change, policy, kind_sets, policy_file, today)
        for group in parse_show_only_groups(show_only)
    )


def show_only_matches_severity_label(show_only: str | None, label: str) -> bool:
    """Return True if *label* passes any OR'd group's severity dimension.

    For a finding with no backing ``Change`` (a missing-contract label) --
    ``apply_show_only``'s element/action dimensions don't apply to "a symbol
    is simply absent", so only the severity dimension is checked, the same
    narrowing every one of this function's call sites already documented
    for the single-group case. A group with no severity tokens at all
    matches every label (mirrors ``ShowOnlyFilter._check_severity``'s own
    "unconstrained dimension passes" rule) -- so this generalizes the
    pre-existing ``not show_only_severities or label in show_only_severities``
    check at each call site to OR across every group instead of just one.
    """
    if not show_only:
        return True
    return any(
        not group.severities or label in group.severities
        for group in parse_show_only_groups(show_only)
    )


def apply_show_only(
    changes: Sequence[Change],
    show_only: str,
    policy: str = "strict_abi",
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    today: date | None = None,
) -> list[Change]:
    """Filter changes according to a --show-only token string.

    *kind_sets* / *policy_file*, when supplied by the caller (typically
    ``policy.evaluate.effective_kind_sets(result)`` / ``result.policy_file``), let the
    severity dimension resolve through the same effective-verdict logic as
    the rest of the report — including kind-level ``PolicyFile.overrides``
    and per-finding ``effective_verdict`` — so the filter never disagrees
    with the JSON severity field for the same change.

    *show_only* may hold several ``SHOW_ONLY_GROUP_SEP``-joined OR'd groups (see
    :func:`parse_show_only_groups`) -- a change is kept if it matches ANY
    one of them. *today*: an envelope's ``resolved_today`` (ADR-061 gap C,
    Codex, fresh).
    """
    return [
        c
        for c in changes
        if show_only_matches(show_only, c, policy, kind_sets, policy_file, today)
    ]


def display_changes(
    changes: Sequence[Change],
    show_only: str | None,
    *,
    policy: str,
    kind_sets: KindSets | None,
    policy_file: object | None,
    today: date | None,
) -> list[Change]:
    """The changes a renderer should display for one ``--show-only`` run.

    Without *show_only* this is *changes* unchanged. With it, the changes are
    filtered by :func:`apply_show_only`, then each surviving
    ``correlated_change_kind`` that names a hidden change is cleared by
    :func:`~abicheck.report.correlation.suppress_dangling_correlation_notes`.
    Every format used to run those two steps itself, in its own order; a
    format that skipped the second one could render a reference to a finding
    it had just hidden.

    *policy*, *kind_sets*, *policy_file* and *today* are passed through to
    :func:`apply_show_only` unchanged; the caller resolves them, since a
    stub result and a live result resolve them differently.
    """
    if not show_only:
        return list(changes)
    filtered = apply_show_only(
        changes,
        show_only,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
        today=today,
    )
    return suppress_dangling_correlation_notes(filtered)


# ---------------------------------------------------------------------------
# Root-cause grouping
# ---------------------------------------------------------------------------


def root_cause_key_and_display(
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
    :func:`abicheck.report.diff_report._to_json_root_cause`, :func:`_to_markdown_root_cause`,
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


def group_changes_by_root_cause(
    changes: list[Change],
    *,
    extra_causes: frozenset[str] = frozenset(),
) -> list[tuple[str, str, list[Change]]]:
    """Group ``changes`` into root-cause buckets, in first-seen order.

    Returns ``(key, root_display, changes_in_group)`` triples. ``key`` is the
    raw grouping key (a stable hash of it becomes ``root_cause_id_for_key``);
    ``root_display`` is the human-readable root shown to a consumer. Shared
    by the JSON and markdown ``--report-mode root-cause`` renderers so the
    two formats can never disagree about which findings share a root cause
    (Codex review; see :func:`root_cause_key_and_display` for the key/display
    rules, including the ``referenced_causes`` guard against grouping
    independent findings that merely share a symbol).

    *extra_causes* folds in ``caused_by_type`` values from findings outside
    *changes* itself (e.g. JSON's scoped-only ``--used-by``/
    ``consumers.required_symbols`` changes, appended to the report only after this
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
        key, root_display = root_cause_key_and_display(
            c.caused_by_type,
            c.symbol,
            c.kind.value,
            finding_id(c),
            referenced_causes=referenced_causes,
        )
        if key not in groups:
            groups[key] = []
            roots[key] = root_display
            order.append(key)
        groups[key].append(c)
    return [(key, roots[key], groups[key]) for key in order]


# ---------------------------------------------------------------------------
# Scoped gate (--used-by / consumers.required_symbols)
# ---------------------------------------------------------------------------


def resolve_scoped_gate_findings(
    result: DiffResult,
    severity_config: SeverityConfig | None,
    show_only: str | None,
) -> tuple[list[Change], list[str], bool, str]:
    """Resolve the scoped-only ``Change``s and missing-contract labels relevant
    to the ``--used-by``/``consumers.required_symbols`` gate, deduped against
    ``result.changes`` and filtered by ``--show-only``.

    Factored out of ``cli_compare_fold.py``'s JSON branch so markdown/text/
    review output can render the identical actionable findings instead of
    only a bare count (Codex review: a scoped run whose only gated issue was
    a missing contract member or a scoped-only change like
    ``PE_ORDINAL_RETARGETED`` didn't name either one in the default text
    report, unlike JSON/SARIF/JUnit). Lives here (not ``cli_compare_fold.py``)
    so the Markdown and JSON root-cause builders can call it directly, without
    importing back from ``cli_compare_fold.py``, which is its caller.

    Returns ``(scoped_only_changes, missing_labels, blocks, missing_kind)``.
    """
    from ..policy.severity import missing_contract_exit_code

    existing_ids = {finding_id(c) for c in result.changes}
    eff_sets = effective_kind_sets(result)
    scoped_only = list(getattr(result, "scoped_only_changes", ()) or ())
    if show_only and scoped_only:
        scoped_only = apply_show_only(
            scoped_only,
            show_only,
            policy=result.policy,
            kind_sets=eff_sets,
            policy_file=result.policy_file,
        )
    scoped_only = [c for c in scoped_only if finding_id(c) not in existing_ids]

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
    missing_labels = list(
        getattr(result, "scoped_missing_labels", ()) or ()
        if show_only_matches_severity_label(show_only, missing_severity_label)
        else ()
    )
    return scoped_only, missing_labels, blocks, missing_kind
