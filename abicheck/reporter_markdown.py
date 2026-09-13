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
    from datetime import date

    from .bundle_models import BundleDiffResult
    from .policy.severity import GateDecision
    from .report.finding import ReportFinding
    from .severity import KindSets, SeverityConfig

from .change_registry import REGISTRY
from .checker import (
    Change,
    DiffResult,
    LibraryMetadata,
    Verdict,
)
from .finding_identity import missing_contract_kind, report_finding_id
from .model.change_catalog.kinds import HasKind
from .model.change_catalog.registry import ChangeEntity, ChangeOperation
from .policy.classification import (
    evidence_status_for_result,
    impact_for,
)
from .policy.contract_finding_relevance import is_evaluated
from .policy.evidence_status import EvidenceStatus
from .report import contract_conflicts_markdown as _ccm, render_markdown as _rmd
from .report.cross_source_evolution import (
    cross_source_evolution_md_suffix as _cross_source_evolution_md_suffix,
)
from .report.disposition_audit import (
    DispositionAudit,
    compute_disposition_audit,
    render_disposition_audit_note,
)
from .report.render_markdown import (
    _contract_decision_text as _contract_decision_text,
    _format_change_md as _format_change_md,
    _format_change_md_oneline as _format_change_md_oneline,
)
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
            "quality_issues": summary.quality_issues,
            "total_changes": summary.total_changes,
        },
        "redundant_count": result.redundant_count,
        # ADR-067 D3: even the one-line view carries the raw-versus-effective
        # counts -- a fully-suppressed comparison must not read as "no
        # changes".
        "disposition_audit_note": render_disposition_audit_note(
            compute_disposition_audit(result, severity_config)
        ),
    }
    # Codex review: oneline/`--stat` lacked the deployment digest.
    if (_dig := getattr(result, "env_matrix_source_sha256", None)) is not None:
        d["env_matrix_source_sha256"] = _dig
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

#: CLI ``--view show=`` element tokens -> the canonical
#: :class:`~abicheck.model.change_catalog.registry.ChangeEntity` each names.
#: The plural CLI spellings and the ``elf`` alias for ``BINARY`` are kept
#: exactly as the vocabulary has always spelled them (plan slice 7o's
#: acceptance bar: the old user task keeps a simple, supported invocation);
#: ``build``, ``source`` and ``analysis`` are new tokens for the three
#: dimensions the superseded name-prefix table could not express at all.
ELEMENT_TOKEN_ENTITIES: dict[str, ChangeEntity] = {
    "functions": ChangeEntity.FUNCTION,
    "variables": ChangeEntity.VARIABLE,
    "types": ChangeEntity.TYPE,
    "enums": ChangeEntity.ENUM,
    "elf": ChangeEntity.BINARY,
    "binary": ChangeEntity.BINARY,
    "build": ChangeEntity.BUILD,
    "source": ChangeEntity.SOURCE,
    "analysis": ChangeEntity.ANALYSIS,
}

#: CLI ``--view show=`` action tokens -> the canonical
#: :class:`~abicheck.model.change_catalog.registry.ChangeOperation`. The CLI
#: has always spelled ``ChangeOperation.MODIFIED`` "changed"; that stays.
ACTION_TOKEN_OPERATIONS: dict[str, ChangeOperation] = {
    "added": ChangeOperation.ADDED,
    "removed": ChangeOperation.REMOVED,
    "changed": ChangeOperation.MODIFIED,
}


def entity_for_kind(kind_val: str) -> str | None:
    """The declared :class:`ChangeEntity` value for *kind_val*, or ``None``.

    Plan slice 7o: reads the one registration in the change catalog. The
    superseded implementation was a table of name *prefixes*
    (``func_``/``var_``/``type_``/``enum_``/``soname_``...) plus an
    exact-match escape list for the kinds whose names those prefixes miss,
    maintained beside the catalog rather than in it -- which is why 238 of
    the 407 kinds matched no element at all and were invisible to every
    ``--view show=`` element token. ``None`` means the kind is not in the
    registry (a hand-built ``Change`` in a test), never "unclassified": a
    registered entry without a declared entity fails at import time.
    """
    entity = REGISTRY.entity_for(kind_val)
    return entity.value if entity is not None else None


def operation_for_kind(kind_val: str) -> str:
    """Classify a ``ChangeKind.value`` into "added"/"removed"/"modified".

    Plan slice 7o: this reads ``ChangeKindMeta.operation`` -- the same single
    registration that declares the kind's verdict and impact -- instead of
    matching name suffixes (``*_added``/``*_removed``) with a 30-entry
    override table for every kind whose name ends in ``_added`` while naming
    a trait *gained by a persisting entity* (``func_noexcept_added``,
    ``type_field_added``, ``virtual_method_added``, ...). That override table
    was itself the evidence that a name is not the fact.

    Shared, as before, between the display filter's action tokens and the
    JSON report's per-finding ``operation`` field, so the two cannot drift.
    An unregistered kind reads "modified", the same neutral answer the
    superseded suffix rule gave a name it did not recognize.
    """
    operation = REGISTRY.operation_for(kind_val)
    return operation.value if operation is not None else ChangeOperation.MODIFIED.value


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
        today: date | None = None,
    ) -> bool:
        """Return True if *change* matches the severity filter.

        Resolves through ``severity.effective_verdict_for_change`` — the same
        canonical resolver ``DiffResult._effective_verdict_for_change`` uses —
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
        from .severity import effective_verdict_for_change

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

    def _check_element(self, kind_val: str) -> bool:
        """Return True if *kind_val* matches the element filter.

        Plan slice 7o: resolves through the change catalog's own declared
        :class:`ChangeEntity` (:func:`entity_for_kind`), not through a
        second interpretation of the kind's *name*.
        """
        if not self.elements:
            return True
        entity = entity_for_kind(kind_val)
        if entity is None:
            return False
        return any(
            ELEMENT_TOKEN_ENTITIES[elem].value == entity for elem in self.elements
        )

    @staticmethod
    def _check_action(kind_val: str, actions: frozenset[str]) -> bool:
        """Return True if *kind_val* matches the action filter.

        Plan slice 7o: resolves through the catalog's declared
        :class:`ChangeOperation` (:func:`operation_for_kind`).
        """
        if not actions:
            return True
        operation = operation_for_kind(kind_val)
        return any(
            ACTION_TOKEN_OPERATIONS[action].value == operation for action in actions
        )

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
        if not self._check_element(change.kind.value):
            return False
        return self._check_action(change.kind.value, self.actions)


#: Separator between OR'd ``ShowOnlyFilter`` groups in a single ``show_only``
#: string (CodeRabbit/Codex review, PR #1154). ``--view show=...`` is
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
    """Render *show_only* back as a literally re-runnable ``--view show=...``
    invocation.

    Codex review (PR #1154 second follow-up: "Render repeated show groups
    as repeated view options"): the raw *show_only* string stores repeated
    ``--view show=...`` occurrences joined by :data:`SHOW_ONLY_GROUP_SEP`
    (``";"``), an internal transport separator -- ``ShowOnlyFilter.parse``
    rejects it as a single value, and an unquoted shell treats a bare ``;``
    as a command separator. Every "how to reproduce this filter" hint
    (Markdown/HTML "Filtered by" notes) must render one ``--view show=...``
    token per group instead of echoing the internal separator verbatim.
    A *show_only* with no ``;`` (the common case, and every pre-existing
    single-group caller) round-trips to exactly one ``--view show=...``
    token, unchanged from before this function existed.
    """
    groups = show_only.split(SHOW_ONLY_GROUP_SEP)
    return " ".join(f"--view show={group}" for group in groups)


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
    ``result._effective_kind_sets()`` / ``result.policy_file``), let the
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


def filter_release_bundle_findings(
    findings: Sequence[Any],
    show_only: str,
    policy: str = "strict_abi",
    policy_file: object | None = None,
) -> list[Any]:
    """Filter ``compare-release`` bundle (cross-library) findings via --show-only.

    Codex review, PR #1154 second follow-up ("Apply release show filters
    inside each renderer"): bundle findings are release-global, not tied to
    one library's own ``DiffResult``, so :func:`apply_show_only` (which
    expects a real :class:`Change`) cannot be called on them directly.
    Each finding is lowered through its own ``to_change()`` projection --
    the identical lowering
    ``cli_compare_release_helpers._fold_release_global_severity`` already
    uses to resolve a bundle finding's severity for the release's exit
    code -- so this display filter can never disagree with what that exit
    code already computed for the same finding. *kind_sets* is deliberately
    not accepted: bundle findings carry canonical, already-partitioned
    ``ChangeKind``s, matching that same existing severity fold's own
    omission of it.

    *findings* is typed ``Sequence[Any]`` rather than
    ``Sequence[BundleFinding]`` to avoid this leaf module importing
    :mod:`abicheck.bundle_models` at runtime for a type-only reference; any
    object exposing ``to_change() -> Change`` (every real
    :class:`~abicheck.bundle_models.BundleFinding` does) satisfies it.
    """
    return [
        f
        for f in findings
        if show_only_matches(
            show_only, f.to_change(), policy=policy, policy_file=policy_file
        )
    ]


def release_bundle_findings_for_view(
    bundle_result: BundleDiffResult, show_only: str | None
) -> list[Any]:
    """Return *bundle_result*'s findings, ``--view show=``-filtered when active.

    Codex review, PR #1154 second follow-up ("Apply release show filters
    inside each renderer"): shared by the release fan-out's JSON
    (``cli_compare_release_helpers._format_release_json``) and Markdown
    (``cli_compare_release_helpers._format_release_markdown``) bundle
    sections so the two formats can never disagree about which bundle
    findings a given ``show_only`` selection keeps. Both call sites live in
    ``cli_compare_release_helpers.py`` (a ``frontends`` module, which may
    import both this module and ``report``) as of Codex review, fresh
    evidence, PR #1154 follow-up ("Move release filtering out of the
    Markdown renderer") -- ``report/render_release_markdown.py``'s own
    Markdown section renderer used to call this function directly, which
    `report/AGENTS.md`'s renderer contract forbids (a renderer may only
    format an already-computed projection, never filter one itself); it now
    takes the pre-filtered result as a parameter instead. A no-op (returns
    every finding) when *show_only* is falsy.
    """
    if not show_only:
        return list(bundle_result.bundle_findings)
    return filter_release_bundle_findings(
        bundle_result.bundle_findings,
        show_only,
        policy=bundle_result.policy,
        policy_file=bundle_result.policy_file,
    )


def release_matrix_changes_for_view(
    matrix_result: DiffResult, show_only: str | None
) -> list[Change]:
    """Return *matrix_result*'s changes, ``--view show=``-filtered when active.

    Shared by the release fan-out's JSON and Markdown release-global matrix
    (build-configuration) sections, both called from
    ``cli_compare_release_helpers.py`` for the identical reason as
    :func:`release_bundle_findings_for_view` -- see that function's own
    docstring. Unlike a bundle finding, a matrix result is a real
    :class:`DiffResult`, so it is filtered the identical way a per-library
    one is (``kind_sets``/``policy_file`` included, so the severity
    dimension resolves consistently). A no-op (returns every change) when
    *show_only* is falsy.
    """
    if not show_only:
        return list(matrix_result.changes)
    return apply_show_only(
        matrix_result.changes,
        show_only,
        policy=matrix_result.policy or "strict_abi",
        kind_sets=matrix_result._effective_kind_sets(),
        policy_file=matrix_result.policy_file,
    )


# ---------------------------------------------------------------------------
# Impact summary
# ---------------------------------------------------------------------------


def compute_impact_table(
    result: DiffResult,
    displayed_changes: list[Change] | None = None,
) -> _rmd.ImpactTable | None:
    """Build the impact summary table's structured intermediate.

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
    root_entries: list[_rmd.ImpactRootEntry] = []
    for c in changes:
        if c.kind in _ROOT_TYPE_CHANGE_KINDS:
            affected_count = len(c.affected_symbols) if c.affected_symbols else 0
            if affected_count > 0 or c.caused_count > 0:
                root_entries.append(
                    _rmd.ImpactRootEntry(
                        symbol=c.symbol,
                        kind=c.kind.value,
                        iface_count=affected_count,
                        caused=c.caused_count,
                    )
                )

    # Count non-type direct changes
    direct_removals = sum(
        1
        for c in changes
        if c.kind.value.endswith("_removed") and c.kind not in _ROOT_TYPE_CHANGE_KINDS
    )

    if not root_entries and direct_removals == 0:
        return None

    return _rmd.ImpactTable(
        root_entries=tuple(root_entries), direct_removals=direct_removals
    )


def _build_impact_table(
    result: DiffResult,
    displayed_changes: list[Change] | None = None,
) -> list[str]:
    return _rmd.render_impact_table(compute_impact_table(result, displayed_changes))


# ---------------------------------------------------------------------------
# Leaf-change mode helpers
# ---------------------------------------------------------------------------


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
    missing_labels = list(
        getattr(result, "scoped_missing_labels", ()) or ()
        if show_only_matches_severity_label(show_only, missing_severity_label)
        else ()
    )
    return scoped_only, missing_labels, blocks, missing_kind


def compute_root_cause_section(
    changes: list[Change],
    scoped_only: list[Change],
    missing_labels: list[str],
    blocks: bool,
    missing_kind: str,
    *,
    contract_evaluation: bool,
    evidence_tiers: Sequence[str] = (),
) -> _rmd.RootCauseSectionData | None:
    """The structured intermediate for ``--report-mode root-cause``'s
    "## Root Causes" section.

    Groups *changes* + *scoped_only* by root cause (:func:`_group_changes_by_root_cause`)
    and merges each *missing_labels* entry into a matching group -- or starts
    a new singleton group -- by the identical key
    :func:`_root_cause_key_and_display` computes. Returns ``None`` (no
    section at all) only when there is neither a real group nor a missing
    label to show.

    *evidence_tiers* (``DiffResult.evidence_tiers``), when given, lets this
    function qualify an ``UNATTRIBUTED`` finding's impact text here too
    (Codex review, fresh evidence: this root-cause path kept the
    unconditional text even after the full/leaf Markdown views were
    fixed). The resolved impact string -- not just the evidence status --
    is computed here and handed to ``_format_change_md`` as a plain
    value: the earlier fix passed a resolved ``EvidenceStatus`` in and let
    the renderer call ``impact_for()`` itself, which still left that
    registry lookup on the render side (Codex review, fresh evidence --
    ``report/AGENTS.md``'s compute/render split treats a per-change
    ``impact_for()`` call as a report decision the compute half owes the
    renderer, not a formatting choice).

    A *scoped_only* member keeps its ``EvidenceStatus.CONSUMER_PROVEN``
    override rather than being re-scored from *evidence_tiers* like an
    ordinary comparison finding -- JSON (``reporter.to_json``'s
    ``_scoped_only_change_dict``) and SARIF already stamp scoped_only
    findings this way (they are proven by the supplied consumer's own
    import table, independent of what evidence the library-to-library
    comparison itself carries), so recomputing via
    ``evidence_status_for_result`` here would contradict those two formats
    and could demote a consumer-proven finding to "plausible, not
    confirmed" (Codex review, fresh evidence).
    """
    groups = _group_changes_by_root_cause(changes + scoped_only)
    if not groups and not missing_labels:
        return None

    scoped_only_ids = {_finding_id(c) for c in scoped_only}

    order: list[str] = []
    root_by_key: dict[str, str] = {}
    finding_lines_by_key: dict[str, list[str]] = {}
    count_by_key: dict[str, int] = {}

    def _resolved_impact(c: Change) -> str | None:
        kind = getattr(c, "kind", None)
        if kind is None:
            return None
        evidence_status = (
            EvidenceStatus.CONSUMER_PROVEN
            if _finding_id(c) in scoped_only_ids
            else evidence_status_for_result(c, evidence_tiers)
        )
        return impact_for(kind, evidence_status)

    for key, root_display, group_changes in groups:
        order.append(key)
        root_by_key[key] = root_display
        finding_lines_by_key[key] = [
            _format_change_md(c, _resolved_impact(c))
            + _cross_source_evolution_md_suffix(c)
            for c in group_changes
        ]
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

    return _rmd.RootCauseSectionData(
        groups=tuple(
            _rmd.RootCauseGroupData(
                root_display=root_by_key[key],
                count=count_by_key[key],
                finding_lines=tuple(finding_lines_by_key[key]),
            )
            for key in order
        )
    )


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


def compute_redundancy_note(result: DiffResult) -> _rmd.RedundancyNote | None:
    """The structured intermediate for :func:`_append_redundancy_note`."""
    if result.redundant_count > 0:
        return _rmd.RedundancyNote(redundant_count=result.redundant_count)
    return None


def compute_out_of_surface_note(result: DiffResult) -> _rmd.OutOfSurfaceNote | None:
    """The structured intermediate for :func:`_append_out_of_surface_note`."""
    if result.scope_to_public_surface and result.out_of_surface_count:
        return _rmd.OutOfSurfaceNote(count=result.out_of_surface_count)
    return None


def compute_suppression_note(result: DiffResult) -> _rmd.SuppressionNote | None:
    """The structured intermediate for :func:`_append_suppression_note`."""
    if not result.suppression_file_provided:
        return None
    entries: list[_rmd.SuppressedEntry] = []
    if result.suppressed_count != 0:
        for sc in result.suppressed_changes:
            contract_text = None
            relevance = getattr(sc, "contract_relevance", None)
            if relevance is not None:
                reason_code = getattr(sc, "contract_reason_code", None)
                assurance = getattr(sc, "contract_assurance", None)
                contract_text = _contract_decision_text(
                    relevance, reason_code, assurance
                )
            entries.append(
                _rmd.SuppressedEntry(
                    symbol=sc.symbol,
                    description=sc.description,
                    contract_text=contract_text,
                )
            )
    return _rmd.SuppressionNote(
        suppressed_count=result.suppressed_count, entries=tuple(entries)
    )


def _append_redundancy_note(lines: list[str], result: DiffResult) -> None:
    lines += _rmd.render_redundancy_note(compute_redundancy_note(result))


def _append_out_of_surface_note(lines: list[str], result: DiffResult) -> None:
    lines += _rmd.render_out_of_surface_note(compute_out_of_surface_note(result))


def _append_suppression_note(lines: list[str], result: DiffResult) -> None:
    lines += _rmd.render_suppression_note(compute_suppression_note(result))


# ---------------------------------------------------------------------------
# Severity section helpers
# ---------------------------------------------------------------------------

_BREAKING_ICON = "❌"  # ❌
_SOURCE_BREAK_ICON = "⚠️"  # ⚠️
_RISK_ICON = "⚠️"  # ⚠️
_HYGIENE_ICON = "\U0001f9f9"  # 🧹
_QUALITY_ICON = "\U0001f50d"  # 🔍
_ADDITION_ICON = "✅"  # ✅


def _hygiene_evolution_counts_line(hygiene: list[Change]) -> str:
    """``"introduced: 2 · resolved: 1 · persistent: 40016 · not evaluated: 0"``
    for a list of cross-source-evolution-stamped findings -- the Markdown
    counterpart of ``report.cross_source_evolution.
    CrossSourceEvolutionSummary`` (JSON's own per-state counts), so a
    reader can see the split without counting bullet points (ADR-068
    finding A)."""
    from .policy.evidence_status import CrossSourceEvolution

    counts = dict.fromkeys(CrossSourceEvolution, 0)
    for c in hygiene:
        cse = getattr(c, "cross_source_evolution", None)
        if cse is not None:
            counts[cse] += 1
    return (
        f"Introduced: {counts[CrossSourceEvolution.INTRODUCED]} · "
        f"Resolved: {counts[CrossSourceEvolution.RESOLVED]} · "
        f"Persistent: {counts[CrossSourceEvolution.PERSISTENT]} · "
        f"Not evaluated: {counts[CrossSourceEvolution.NOT_EVALUATED]}"
    )


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


def compute_severity_summary(
    changes: list[Change],
    severity_config: SeverityConfig,
    *,
    all_changes: list[Change] | None = None,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    scoped_counts: dict[str, int] | None = None,
    scoped_blocking_categories: tuple[str, ...] | None = None,
    today: date | None = None,
) -> _rmd.SeveritySummary:
    """Build the severity configuration summary table's structured intermediate.

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
    finding. *today*: an envelope's own ``resolved_today`` (ADR-061 gap C,
    Codex, fresh evidence).
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
        today=today,
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
            today=today,
        )
        if all_changes is not None
        else categorized
    )

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

    rows: list[_rmd.SeverityRow] = []
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
        rows.append(
            _rmd.SeverityRow(
                label=label,
                emoji=emoji,
                level_upper=level_val.upper(),
                count=count,
                impact=impact,
            )
        )

    return _rmd.SeveritySummary(rows=tuple(rows))


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
    return _rmd.render_severity_summary(
        compute_severity_summary(
            changes,
            severity_config,
            all_changes=all_changes,
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
            scoped_counts=scoped_counts,
            scoped_blocking_categories=scoped_blocking_categories,
        )
    )


def _footer_lines() -> list[str]:
    return _rmd.render_footer()


def compute_library_files(
    old_meta: LibraryMetadata | None, new_meta: LibraryMetadata | None
) -> _rmd.LibraryFilesSection | None:
    """The structured intermediate for the '## Library Files' section."""
    old_path = getattr(old_meta, "path", "—") if old_meta else "—"
    new_path = getattr(new_meta, "path", "—") if new_meta else "—"
    old_sha = getattr(old_meta, "sha256", "—")[:12] if old_meta else "—"
    new_sha = getattr(new_meta, "sha256", "—")[:12] if new_meta else "—"
    old_size = _fmt_size(old_meta.size_bytes) if old_meta else "—"
    new_size = _fmt_size(new_meta.size_bytes) if new_meta else "—"
    return _rmd.LibraryFilesSection(
        old_path=old_path,
        new_path=new_path,
        old_sha=old_sha,
        new_sha=new_sha,
        old_size=old_size,
        new_size=new_size,
    )


def compute_severity_sections(
    breaking: list[Change],
    source_breaks: list[Change],
    risk: list[Change],
    compatible: list[Change],
    *,
    severity_config: SeverityConfig | None = None,
) -> _rmd.SeveritySectionsData:
    """The structured intermediate for all severity-grouped sections."""
    groups: list[_rmd.ChangeGroup] = []

    if breaking:
        sev_label = _section_severity_label(severity_config, "abi_breaking")
        groups.append(
            _rmd.ChangeGroup(
                heading=f"## {_BREAKING_ICON} Breaking Changes{sev_label}",
                changes=tuple(breaking),
                oneline=False,
            )
        )

    if source_breaks:
        sev_label = _section_severity_label(severity_config, "potential_breaking")
        groups.append(
            _rmd.ChangeGroup(
                heading=f"## {_SOURCE_BREAK_ICON} Source-Level Breaks{sev_label}",
                changes=tuple(source_breaks),
                oneline=False,
            )
        )

    if risk:
        # ADR-068 finding A: RISK_KINDS mixes two different stories --
        # ordinary deployment-compatibility risk (a new GLIBC version
        # requirement, etc.) and the cross-source hygiene checks
        # (workflows.cross_source_evolution) `compare()` runs automatically
        # on every invocation. The latter are stamped with an OLD->NEW
        # evolution state (introduced/resolved/persistent/not_evaluated) --
        # a `persistent` finding is pre-existing hygiene debt, not new
        # drift, and lumping it under "Deployment Risk Changes" with that
        # section's GLIBC-oriented blurb both mislabels it and drowns any
        # genuine deployment-risk finding in the same section. Split them
        # into their own section instead.
        hygiene = [
            c for c in risk if getattr(c, "cross_source_evolution", None) is not None
        ]
        deployment_risk = [
            c for c in risk if getattr(c, "cross_source_evolution", None) is None
        ]
        sev_label = _section_severity_label(severity_config, "potential_breaking")
        if deployment_risk:
            groups.append(
                _rmd.ChangeGroup(
                    heading=f"## {_RISK_ICON} Deployment Risk Changes{sev_label}",
                    changes=tuple(deployment_risk),
                    oneline=True,
                    note_lines=(
                        "> These changes are **binary-compatible** but may cause the library to fail",
                        "> loading on older systems (e.g. a new GLIBC version requirement). Verify",
                        "> your target environment before deploying.",
                    ),
                )
            )
        if hygiene:
            groups.append(
                _rmd.ChangeGroup(
                    heading=f"## {_HYGIENE_ICON} Cross-Source Hygiene Findings{sev_label}",
                    changes=tuple(hygiene),
                    oneline=True,
                    note_lines=(
                        "> These findings compare each snapshot's own evidence sources against each",
                        "> other (e.g. an exported symbol missing from public headers) -- they are",
                        "> **not** OLD vs. NEW drift. Each carries its own evolution state relative to",
                        "> the OLD snapshot: `persistent` findings already existed before this change",
                        f"> and are not new; only `introduced` ones are new. {_hygiene_evolution_counts_line(hygiene)}",
                    ),
                )
            )

    if compatible:
        from .policy.classification import ADDITION_KINDS as _ADDITION_KINDS

        quality = [c for c in compatible if c.kind not in _ADDITION_KINDS]
        additions_list = [c for c in compatible if c.kind in _ADDITION_KINDS]
        if quality:
            sev_label = _section_severity_label(severity_config, "quality_issues")
            groups.append(
                _rmd.ChangeGroup(
                    heading=f"## {_QUALITY_ICON} Quality Issues{sev_label}",
                    changes=tuple(quality),
                    oneline=True,
                )
            )
        if additions_list:
            # Same per-change detail as Breaking/Source-Level Breaks
            # (kind, location, impact) — a bare description dropped the
            # kind and any per-kind caveat (e.g. enum_member_added's
            # "may shift subsequent values" note), silently losing
            # information a reviewer needs to approve new public API
            # surface.
            sev_label = _section_severity_label(severity_config, "addition")
            groups.append(
                _rmd.ChangeGroup(
                    heading=f"## {_ADDITION_ICON} Additions{sev_label}",
                    changes=tuple(additions_list),
                    oneline=False,
                )
            )

    return _rmd.SeveritySectionsData(groups=tuple(groups))


def compute_not_evaluated(
    not_evaluated: list[Change],
) -> _rmd.NotEvaluatedSection | None:
    """The structured intermediate for :func:`_build_not_evaluated_section`.

    Disclose the findings compatibility policy did not score (ADR-049 D1).

    These are real detector facts that carry no verdict: contract evaluation
    either proved the entity outside the declared contract, or could not
    resolve it from the evidence the run had. They are deliberately absent
    from the four verdict sections above -- filing an unscored finding under
    "Breaking Changes" would contradict the verdict printed at the top of the
    same report -- so this section is what keeps them visible, with the
    relevance and reason code that explain *why* they did not gate.

    ``None`` (and so entirely absent) unless the run opted into
    ``--contract``.
    """
    if not not_evaluated:
        return None
    entries = []
    for c in not_evaluated:
        relevance = getattr(c, "contract_relevance", None)
        reason = getattr(c, "contract_reason_code", None)
        label = getattr(relevance, "value", None) or "UNKNOWN"
        suffix = f" ({reason})" if reason else ""
        entries.append(_rmd.NotEvaluatedEntry(change=c, label=label, suffix=suffix))
    return _rmd.NotEvaluatedSection(entries=tuple(entries))


def _build_not_evaluated_section(not_evaluated: list[Change]) -> list[str]:
    return _rmd.render_not_evaluated_section(compute_not_evaluated(not_evaluated))


def compute_environment_drift(
    changes: list[Change],
) -> _rmd.EnvironmentDriftSection | None:
    """The structured intermediate for :func:`_build_environment_drift_section`.

    Group environment/toolchain-drift findings under one heading.

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
        return None
    return _rmd.EnvironmentDriftSection(
        entries=tuple(
            _rmd.EnvironmentDriftEntry(kind=c.kind.value, description=c.description)
            for c in drift
        )
    )


def _build_environment_drift_section(changes: list[Change]) -> list[str]:
    return _rmd.render_environment_drift_section(compute_environment_drift(changes))


# Verdict -> short merge-effect phrase for the reviewer digest.
_VERDICT_MERGE_EFFECT = {
    Verdict.NO_CHANGE: "no ABI/API change — safe to merge",
    Verdict.COMPATIBLE: "backward-compatible — safe to merge",
    Verdict.COMPATIBLE_WITH_RISK: "compatible but carries deployment risk — review advised",
    Verdict.API_BREAK: "source-level (API) break — consumers must recompile",
    Verdict.BREAKING: "binary (ABI) break — blocks merge under a strict gate",
}


def _merge_effect_from_exit_code(exit_code: int) -> str:
    """The two merge-effect phrases, keyed by the resolved severity exit code."""
    if exit_code == 0:
        return "no error-level findings under the configured severity policy — safe to merge"
    return "blocked by severity policy — review required before merge"


def _severity_merge_effect(result: DiffResult, severity_config: SeverityConfig) -> str:
    """Merge-effect phrase reflecting the actual severity-aware gate.

    Compatibility (``result.verdict``) and the CI gate are independent
    decisions once a severity configuration is in play — e.g. an ``addition``
    finding configured as ``error`` blocks the build even though the verdict
    is ``COMPATIBLE``, and an ``abi_breaking`` finding configured below
    ``error`` does not. The hard-coded ``_VERDICT_MERGE_EFFECT`` phrases would
    misreport both cases, so this asks the severity gate directly instead of
    inferring "safe to merge" from the verdict alone.

    Only called for a direct caller with no already-resolved
    :class:`~abicheck.policy.severity.GateDecision` -- an envelope-driven
    render passes one to :func:`compute_review_digest` instead (ADR-061 gap
    C; CodeRabbit review) so this digest's merge-effect phrase reads the same
    gate SARIF's/HTML's gate blocks do, rather than a second, independent
    ``compute_exit_code`` call that happens to agree.
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
    return _merge_effect_from_exit_code(exit_code)


def compute_review_digest(
    result: DiffResult,
    *,
    severity_config: SeverityConfig | None = None,
    disposition_audit: DispositionAudit | None = None,
    findings: Sequence[ReportFinding] | None = None,
    gate: GateDecision | None = None,
) -> _rmd.ReviewDigest:
    """The structured intermediate for :func:`to_review_digest`.

    *severity_config*, when given, drives the merge-effect phrase from the
    actual severity-aware CI gate instead of the raw compatibility verdict —
    compatibility and "blocks CI" are independent decisions once severity
    configuration is in play (see :func:`_severity_merge_effect`).

    *gate*, when given, is the already-resolved
    :class:`~abicheck.policy.severity.GateDecision` the merge-effect phrase
    reads instead of a second, independent ``compute_exit_code`` call --
    the ADR-061 gap C caller (``report/render_markdown_document.
    build_review_digest_document``) passes the ``ReportEnvelope``'s own gate
    (CodeRabbit review), the same decision SARIF's/HTML's gate blocks
    project. A direct caller with no envelope (``severity_config`` given,
    ``gate`` not) keeps the prior behaviour via :func:`_severity_merge_effect`.

    *disposition_audit*, when given, is used verbatim instead of resolving a
    fresh one from *result*/*severity_config* -- the ADR-061 gap C caller
    (``report/render_markdown_document.build_review_digest_document``) passes
    the one already computed by ``report/build.build_report_document``'s
    single shared call, rather than this function re-deriving an identical
    value from the same ledger a second time.

    *findings* is the same reuse for the per-change verdicts the impacted-
    symbols list below rests on: the ADR-061 gap C caller passes the
    ``ReportEnvelope``'s already-resolved set instead of leaving this
    function to call ``report_findings_for`` a second time for the same
    render.
    """
    from .report.finding import report_findings_for

    findings = findings if findings is not None else report_findings_for(result)
    summary = build_summary(result, findings=findings)
    v = result.verdict
    emoji = _VERDICT_EMOJI.get(v, "?")
    label = _VERDICT_LABEL.get(v, v.value)
    if gate is not None:
        effect = _merge_effect_from_exit_code(gate.exit_code)
    elif severity_config is not None:
        effect = _severity_merge_effect(result, severity_config)
    else:
        effect = _VERDICT_MERGE_EFFECT.get(v, "")

    # Manual-review banner: scoping requested but the public surface could not
    # be confirmed, so compatibility is unconfirmed (don't overclaim).
    manual_review_banner = bool(
        result.scope_to_public_surface and not result.scope_resolved
    )

    # Coverage-warning banner (Codex review): a clean verdict can still rest
    # on incomplete evidence -- e.g. compare.note_if_same_binary_compared's
    # byte-identical-inputs warning -- and this digest is exactly the
    # GitHub-facing summary a reviewer approves a merge from, so it must not
    # read as unconditionally clean when one of these is present.
    coverage_warnings = tuple(result.coverage_warnings or ())

    scoped = result.scope_to_public_surface
    additions_label = "Public additions" if scoped else "Additions"

    rec = recommend_release(result)

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
    from .report.surface_changes import compute_surface_changes

    impacted = [
        f.change
        for f in findings
        if is_evaluated(f.change)
        if f.verdict in (Verdict.BREAKING, Verdict.API_BREAK)
    ]

    return _rmd.ReviewDigest(
        library=result.library,
        old_version=result.old_version,
        new_version=result.new_version,
        verdict_emoji=emoji,
        verdict_label=label,
        effect=effect,
        manual_review_banner=manual_review_banner,
        coverage_warnings=coverage_warnings,
        additions_label=additions_label,
        breaking_count=summary.breaking,
        source_breaks_count=summary.source_breaks,
        risk_count=summary.risk_count,
        # additions_count/quality_issues_count are two rows in the same
        # table, so they must not overlap. `summary.compatible_additions`
        # (report_schema_version 4.0) already counts only genuine additions
        # -- use it directly, no second `quality_issues` subtraction (that
        # is `pr_comment.py`'s own release-field derivation, a separate,
        # differently-scoped field -- `cli_compare_release_pairwise.py`).
        additions_count=summary.compatible_additions,
        quality_issues_count=summary.quality_issues,
        scoped=bool(scoped),
        out_of_surface_count=result.out_of_surface_count,
        bump_value=rec.bump.value,
        soname_value=rec.soname.value,
        impacted=tuple(
            _rmd.ImpactedSymbol(symbol=c.symbol or "?", kind=c.kind.value)
            for c in impacted
        ),
        disposition_audit=(
            disposition_audit
            if disposition_audit is not None
            else compute_disposition_audit(result, severity_config)
        ),
        surface_changes=compute_surface_changes(result, findings),
        env_matrix_source_sha256=result.env_matrix_source_sha256,
    )


def compute_rtti_note(breaking: list[Change]) -> _rmd.RttiNote | None:
    """The structured intermediate for :func:`_build_internal_rtti_note`."""
    bd = surface_breakdown(breaking)
    if not (bd.rtti or bd.internal):
        return None
    return _rmd.RttiNote(
        rtti=bd.rtti, internal=bd.internal, total=bd.total, public=bd.public
    )


def _build_internal_rtti_note(breaking: list[Change]) -> list[str]:
    """Build the up-front note when breaking findings are mostly RTTI/internal
    churn. Returns an empty list when there is nothing to note."""
    return _rmd.render_rtti_note(compute_rtti_note(breaking))


def compute_headline_table(
    result: DiffResult, emoji: str, label: str
) -> _rmd.HeadlineTable:
    """The structured intermediate for the report's headline summary table.

    ADR-049 D1/D11: when contract evaluation excluded findings from the
    compatibility axis, say so in the headline table. The four counts above
    are now over the *evaluated* findings only, so a reader who sees a
    `NO_CHANGE` verdict beside a populated "Not Evaluated" section has the
    count that reconciles them rather than an apparent contradiction. The
    row is absent for every run that did not opt in, where it is always 0.
    """
    return _rmd.HeadlineTable(
        library=result.library,
        old_version=result.old_version,
        new_version=result.new_version,
        verdict_emoji=emoji,
        verdict_label=label,
        breaking=len(result.breaking),
        source_breaks=len(result.source_breaks),
        risk=len(result.risk),
        compatible=len(result.compatible),
        not_evaluated=len(result.not_evaluated),
    )


def compute_confidence_section(result: DiffResult) -> _rmd.ConfidenceSection | None:
    """The structured intermediate for :func:`_append_confidence_section`."""
    conf = getattr(result, "confidence", None)
    if conf is None:
        return None
    tiers = getattr(result, "evidence_tiers", None)
    cov_warns = getattr(result, "coverage_warnings", None)
    conf_val = conf.value if hasattr(conf, "value") else str(conf)
    tier_str = ", ".join(f"`{t}`" for t in tiers) if tiers else "_none_"
    etier = getattr(result, "evidence_tier", None)
    etier_val = (
        etier.value if (etier is not None and hasattr(etier, "value")) else str(etier)
    )
    comparability = getattr(result, "comparability_assurance", None)
    comparability_dimensions = (
        tuple(sorted(comparability.items())) if comparability else ()
    )
    return _rmd.ConfidenceSection(
        confidence_upper=conf_val.upper(),
        evidence_tier=etier_val,
        evidence_tiers_str=tier_str,
        coverage_warnings=tuple(cov_warns) if cov_warns else (),
        comparability_dimensions=comparability_dimensions,
    )


def _append_confidence_section(lines: list[str], result: DiffResult) -> None:
    """Append confidence/evidence metadata section to markdown lines."""
    lines += _rmd.render_confidence_section(compute_confidence_section(result))


def compute_contract_conflicts_section(
    result: DiffResult,
) -> _ccm.ContractConflictsSection | None:
    """The structured intermediate for Workstream E slice S3's Markdown
    section (:func:`_append_contract_conflicts_section`).

    ``None`` when ``contract_conflicts`` is not a list (contract evaluation
    never ran, or ran against a hand-built ``DiffResult`` that never set the
    field) — the same "typed field, defensive narrowing" pattern
    ``reporter_contract_blocks.add_contract_context`` already uses for
    ``contract_context``.
    """
    conflicts = getattr(result, "contract_conflicts", None)
    if not isinstance(conflicts, list):
        return None
    rows = tuple(
        _ccm.ContractConflictRow(
            conflict_kind=str(c.get("conflict_kind", "")),
            entity=str(c.get("entity", "")),
            side=(c.get("side") if isinstance(c.get("side"), str) else None),
            reason_code=str(c.get("reason_code", "")),
            source_lines=tuple(
                f"{s.get('source_kind', '?')}: {s.get('claim', '')}"
                for s in c.get("sources", ())
                if isinstance(s, dict)
            ),
        )
        for c in conflicts
        if isinstance(c, dict)
    )
    return _ccm.ContractConflictsSection(rows=rows)


def _append_contract_conflicts_section(lines: list[str], result: DiffResult) -> None:
    """Append Workstream E slice S3's contract-conflicts section, if any."""
    lines += _ccm.render_contract_conflicts_section(
        compute_contract_conflicts_section(result)
    )


def compute_policy_section(
    result: DiffResult, *, today: date | None = None
) -> _rmd.PolicySection:
    """The structured intermediate for :func:`_append_policy_section`.

    *today*: an envelope's ``resolved_today`` (ADR-061 gap C, Codex, fresh).
    """
    overrides_text = None
    if result.policy_file and result.policy_file.overrides:
        overrides_text = ", ".join(
            f"`{kind.value}` → `{severity.value}`"
            for kind, severity in result.policy_file.overrides.items()
        )
    reclassify_text = None
    if result.policy_file and result.policy_file.reclassify:
        # Codex review: mirrors the JSON `policy_reclassify` disclosure
        # (reporter.py's `_add_policy_overrides`) -- the active rule set,
        # not a per-finding "which rule fired" attribution (see that
        # function's docstring / schema 2.30 history entry). Filtered
        # through active_reclassify_rules so an expired rule -- which
        # ReclassifyRule.matches() would already refuse to apply -- isn't
        # disclosed as though it were still in effect.
        from .policy.reclassify import active_reclassify_rules

        active = active_reclassify_rules(result.policy_file.reclassify, today)
        if active:
            # CodeRabbit review: code-span-wrap describe()'s raw selector
            # text (e.g. `_ZN6oneapi3dal.*`) -- unescaped, `_`/`*` read as
            # Markdown emphasis, same as `Policy overrides` above already does.
            reclassify_text = "; ".join(f"`{rule.describe()}`" for rule in active)
    return _rmd.PolicySection(
        policy=result.policy or "strict_abi",
        overrides_text=overrides_text,
        reclassify_text=reclassify_text,
    )


def _append_policy_section(lines: list[str], result: DiffResult) -> None:
    """Append policy metadata section to markdown lines."""
    lines += _rmd.render_policy_section(compute_policy_section(result))


_BUMP_EMOJI = {"major": "🔴", "minor": "🟢", "patch": "🟢", "none": "✅"}

#: ``reporter_markdown._view_preamble`` (the opening block leaf/root-cause
#: mode share -- title/verdict table, coverage-warning banner, optional
#: recommendation section, the ``--show-only`` filter note) is retired.
#: ``report.render_markdown_alternate.build_leaf_document``/
#: ``build_root_cause_document`` call ``_view_preamble_mapping`` (that
#: module's own JSON-safe compute half) directly instead -- see this
#: module's own docstring and ``_to_markdown_leaf``/``_to_markdown_root_
#: cause``.


def compute_recommendation_section(result: DiffResult) -> _rmd.RecommendationSection:
    """The structured intermediate for :func:`_append_recommendation_section`."""
    rec = recommend_release(result)
    return _rmd.RecommendationSection(
        bump_emoji=_BUMP_EMOJI.get(rec.bump.value, ""),
        bump_upper=rec.bump.value.upper(),
        soname_value=rec.soname.value,
        state_value=rec.state.value,
        rationale=rec.rationale,
    )


def _append_recommendation_section(lines: list[str], result: DiffResult) -> None:
    """Append the release-recommendation section (semver bump + soname action)."""
    lines += _rmd.render_recommendation_section(compute_recommendation_section(result))


# ADR-063 T10: `to_markdown`/`to_review_digest`/`_to_markdown_leaf`/
# `_to_markdown_root_cause`/`_markdown_alternate_rendering` moved to
# `report/dispatch_markdown.py` (see that module's own docstring for why --
# this module used to import them back function-locally, which is exactly
# the cycle this move retires). This lazy `__getattr__` preserves every
# existing `from abicheck.reporter_markdown import to_markdown`-shaped call
# site (tests included) without reintroducing a module-level import back to
# `report/` here -- the same shim shape `cli_buildsource.py`'s own tail uses
# when a helper moves out from under a re-exporting module (root
# `AGENTS.md`'s "Moving helpers out of a module that re-exports them?").
_DISPATCH_MARKDOWN_NAMES = frozenset(
    {
        "to_markdown",
        "to_review_digest",
        "_to_markdown_root_cause",
        "_markdown_alternate_rendering",
    }
)


def __getattr__(name: str) -> Any:
    if name in _DISPATCH_MARKDOWN_NAMES:
        import importlib

        return getattr(
            importlib.import_module(".report.dispatch_markdown", __package__), name
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
