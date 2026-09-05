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

"""JUnit XML output for abicheck.

Produces a JUnit XML report suitable for CI systems (GitLab CI, Jenkins,
Azure DevOps) that display ABI check results as "test results" in their
standard dashboards.

Usage::

    abicheck compare old.so new.so --format junit -o results.xml

Mapping rules:

- Each library in a ``compare-release`` is a ``<testsuite>``
- Each exported symbol/type that was checked is a ``<testcase>``
- ``classname`` groups: ``functions``, ``variables``, ``types``,
  ``enums``, ``metadata``
- Changes with verdict BREAKING or API_BREAK → ``<failure>``
- Changes with verdict COMPATIBLE_WITH_RISK → ``<failure>`` only when
  the change kind has severity ``"error"`` (currently none do by default)
- COMPATIBLE changes → pass (testcase exists with no ``<failure>`` child)
- ``type`` attribute: the verdict level (``BREAKING``, ``API_BREAK``,
  ``COMPATIBLE_WITH_RISK``)
- ``message`` attribute: ``change_kind: one-line summary``
- Body text: detailed explanation + source location if available
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, cast

from .checker_policy import ChangeKind, Verdict
from .checker_types import Change, DiffResult
from .contract_gating import is_evaluated
from .junit_coverage_warnings import append_coverage_warnings_suite
from .reporter import _finding_id, _suppress_dangling_correlation_notes, apply_show_only
from .reporter_markdown import _root_cause_key_and_display

if TYPE_CHECKING:
    from .model import AbiSnapshot
    from .policy.severity import IssueCategory
    from .report.finding import ReportFinding
    from .severity import KindSets, SeverityConfig


# ---------------------------------------------------------------------------
# Classname mapping — groups symbols/types by element kind
# ---------------------------------------------------------------------------

_FUNC_KINDS = frozenset(k for k in ChangeKind if k.value.startswith("func_"))
_VAR_KINDS = frozenset(k for k in ChangeKind if k.value.startswith("var_"))
_TYPE_KINDS = frozenset(
    k for k in ChangeKind if k.value.startswith("type_") or k.value.startswith("union_")
)
_ENUM_KINDS = frozenset(k for k in ChangeKind if k.value.startswith("enum_"))


def _classname_for(change: Change) -> str:
    """Determine the JUnit classname group for a change."""
    if change.kind in _FUNC_KINDS:
        return "functions"
    if change.kind in _VAR_KINDS:
        return "variables"
    if change.kind in _TYPE_KINDS:
        return "types"
    if change.kind in _ENUM_KINDS:
        return "enums"
    return "metadata"


# ---------------------------------------------------------------------------
# Verdict → failure classification
# ---------------------------------------------------------------------------


_VERDICT_TO_JUNIT_TYPE: dict[Verdict, str] = {
    Verdict.BREAKING: "BREAKING",
    Verdict.API_BREAK: "API_BREAK",
    Verdict.COMPATIBLE_WITH_RISK: "COMPATIBLE_WITH_RISK",
}


def _resolved_verdict(
    change: Change, result: DiffResult, kind_sets: KindSets, finding: ReportFinding | None
) -> Verdict:
    """*finding*'s verdict if resolved (ADR-061 Phase 2 item 4b), else the
    direct resolver call every caller used before ``findings_by_id``."""
    if finding is not None:
        return finding.verdict
    from .severity import effective_verdict_for_change
    return effective_verdict_for_change(
        change, policy=result.policy, kind_sets=kind_sets, policy_file=result.policy_file
    )


def _resolved_category(
    change: Change, result: DiffResult, kind_sets: KindSets, finding: ReportFinding | None
) -> IssueCategory:
    """Category counterpart of :func:`_resolved_verdict`."""
    if finding is not None:
        return finding.category
    from .severity import classify_effective_change
    return classify_effective_change(
        change, policy=result.policy, kind_sets=kind_sets, policy_file=result.policy_file
    )


def _is_failure(
    change: Change,
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None = None,
    *,
    relevant_ids: frozenset[str] | None = None,
    findings_by_id: dict[int, ReportFinding] | None = None,
) -> bool:
    """Return True if the change should be a JUnit ``<failure>``.

    Routes through ``DiffResult._effective_verdict_for_change`` — the single
    canonical per-finding verdict, which honours PolicyFile overrides, the
    A4 per-finding ``effective_verdict`` (ADR-027), and frozen-namespace
    escalation guards — so the JUnit file can never disagree with the JSON
    report or the severity-aware exit code. *findings_by_id*, when given,
    resolves via :func:`_resolved_verdict`/:func:`_resolved_category`.

    When *severity_config* is given (from ``--severity-preset`` or
    ``severity:`` config overrides), it is the sole source of truth — a finding
    fails only when its effective category's configured level is
    ``"error"`` — mirroring :func:`abicheck.severity.compute_exit_code`
    exactly, so the JUnit file can never disagree with the severity-aware
    exit code. A demoted preset (e.g. ``--severity-preset info-only``) must
    make even a BREAKING/API_BREAK verdict pass here, just as it does for
    the exit code.

    Without a *severity_config* (legacy verdict-based scheme): BREAKING and
    API_BREAK verdicts always fail. COMPATIBLE_WITH_RISK fails only when its
    per-kind severity is ``"error"`` (currently all RISK_KINDS default to
    ``"warning"``, so they pass).

    *relevant_ids*, when not ``None``, means a ``--used-by``/``--required-symbol``
    gate is active: a change whose :func:`abicheck.reporter._finding_id` is
    absent from the set can never fail here regardless of its own severity --
    it is out of scope for the gate this testsuite now reports (CLI-audit P1:
    JUnit failures must follow the scoped gate, not just the full-library
    verdict).

    A finding compatibility policy never scored (ADR-049 D1's
    ``NOT_EVALUATED``, only reachable under ``--contract``) can
    never fail here either, for the same reason and by the same rule: it
    contributed nothing to the verdict or the exit code, so reporting one
    ``<failure>`` beside a ``NO_CHANGE`` verdict and a clean exit was the bug
    (Codex review, reproduced with a proven-out-of-contract type-size
    change). It still gets its own passing ``<testcase>`` -- D9 requires the
    fact to stay visible, it just is not a failure.
    """
    if not is_evaluated(change):
        return False
    if relevant_ids is not None and _finding_id(change) not in relevant_ids:
        return False
    finding = findings_by_id.get(id(change)) if findings_by_id is not None else None
    if severity_config is not None:
        from .severity import SeverityLevel

        cat = _resolved_category(change, result, kind_sets, finding)
        return severity_config.level_for(cat) == SeverityLevel.ERROR
    verdict = _resolved_verdict(change, result, kind_sets, finding)
    if verdict in (Verdict.BREAKING, Verdict.API_BREAK):
        return True
    # COMPATIBLE_WITH_RISK never fails without a severity_config: all
    # RISK_KINDS default to severity "warning" in the policy registry. This
    # must NOT consult policy_for(change.kind) directly — for a demoted
    # finding (A4 override or PolicyFile override) the *kind*'s own default
    # severity can be "error" (e.g. a BREAKING kind demoted to risk), which
    # would wrongly resurrect the pre-override severity.
    return False


_CATEGORY_TO_JUNIT_TYPE: dict[str, str] = {
    "abi_breaking": "BREAKING",
    "quality_issues": "QUALITY_ISSUE",
    "addition": "ADDITION",
}


def _failure_type(
    change: Change,
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None = None,
    *,
    findings_by_id: dict[int, ReportFinding] | None = None,
) -> str:
    """Return the ``type`` attribute for a ``<failure>`` element.

    Uses the same canonical per-finding verdict/category as ``_is_failure``,
    including *findings_by_id* (ADR-061 Phase 2 item 4b), so the reported
    type always matches why the finding failed.

    When *severity_config* is given, ``_is_failure`` decides pass/fail from
    the finding's effective *category* (:func:`classify_effective_change`),
    not its raw verdict — a COMPATIBLE addition promoted to ``error``
    fails even though its verdict is COMPATIBLE. Without also deriving
    ``type`` from that same category, such a failure would report
    ``type="COMPATIBLE"`` (``_VERDICT_TO_JUNIT_TYPE``'s fallback for any
    verdict it doesn't recognise), contradicting the very reason it failed.
    """
    finding = findings_by_id.get(id(change)) if findings_by_id is not None else None
    if severity_config is not None:
        from .severity import IssueCategory

        category = _resolved_category(change, result, kind_sets, finding)
        if category == IssueCategory.POTENTIAL_BREAKING:
            # IssueCategory doesn't itself distinguish API break from
            # deployment risk (both fold into POTENTIAL_BREAKING) — recover
            # that distinction from the finding's *effective* verdict, not
            # raw kind-set membership: a per-finding effective_verdict
            # override/modulation (pattern-verdicts, PolicyFile) can move a
            # change's verdict without changing which kind-set its raw kind
            # belongs to, so kind-set membership alone could contradict the
            # category already resolved above (CodeRabbit review, PR #557).
            verdict = _resolved_verdict(change, result, kind_sets, finding)
            if verdict == Verdict.API_BREAK:
                return "API_BREAK"
            if verdict == Verdict.COMPATIBLE_WITH_RISK:
                return "COMPATIBLE_WITH_RISK"
            return "POTENTIAL_BREAKING"
        return _CATEGORY_TO_JUNIT_TYPE.get(category.value, "COMPATIBLE")

    verdict = _resolved_verdict(change, result, kind_sets, finding)
    return _VERDICT_TO_JUNIT_TYPE.get(verdict, "COMPATIBLE")


# ---------------------------------------------------------------------------
# --report-mode root-cause (G29 Phase 3, ADR-052 follow-up)
# ---------------------------------------------------------------------------


def _root_cause_lookup(
    changes: list[Change],
    missing_labels: tuple[str, ...],
    gate_scope: str | None,
) -> dict[str, tuple[str, str]]:
    """Precompute ``finding_id -> (root_cause_id, root_display)`` for
    ``--report-mode root-cause`` (mirrors ``sarif.py``'s ``_root_cause_for``
    closure and the JSON/markdown ``_group_changes_by_root_cause`` grouping
    key, so all four formats never disagree about a finding's root cause).

    Keyed by finding id (:func:`~abicheck.reporter._finding_id` for a real
    ``Change``, the label itself for a missing-contract entry — a label has
    no backing ``Change``/finding id of its own) rather than restructuring
    the per-symbol ``<testcase>`` tree JUnit's schema already commits to:
    each ``<failure>`` attaches its *own* change's root cause independently,
    so the "what if a testcase's changes disagree on root cause" question
    ADR-052 raised for a symbol-keyed grouping never arises here — there is
    no merging, only a per-failure lookup.
    """
    referenced_causes = frozenset(c.caused_by_type for c in changes if c.caused_by_type)
    lookup: dict[str, tuple[str, str]] = {}
    for c in changes:
        key, root_display = _root_cause_key_and_display(
            c.caused_by_type,
            c.symbol,
            c.kind.value,
            _finding_id(c),
            referenced_causes=referenced_causes,
        )
        lookup[_finding_id(c)] = (
            hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
            root_display,
        )
    rule_id = (
        "used_by_missing_symbol"
        if gate_scope == "used_by"
        else "required_symbol_missing"
    )
    for label in missing_labels:
        # A missing-contract label has no caused_by_type; its own name only
        # becomes a *grouping* key when some other finding's caused_by_type
        # names it (the referenced_causes guard above) — mirrors
        # sarif._root_cause_for(None, label, rule_id, label) exactly.
        key, root_display = _root_cause_key_and_display(
            None, label, rule_id, label, referenced_causes=referenced_causes
        )
        lookup[label] = (
            hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
            root_display,
        )
    return lookup


# ---------------------------------------------------------------------------
# Single DiffResult → <testsuite>
# ---------------------------------------------------------------------------


def _partition_changes(
    changes: list[Change],
) -> tuple[dict[str, Change], list[Change]]:
    """Split *changes* into (first-change-per-symbol map, extra changes).

    The first change seen for each symbol becomes the primary testcase entry;
    subsequent changes on the same symbol are collected in *extra_changes* so
    they can be appended as additional ``<failure>`` children later.
    """
    change_by_symbol: dict[str, Change] = {}
    extra_changes: list[Change] = []
    for c in changes:
        if c.symbol not in change_by_symbol:
            change_by_symbol[c.symbol] = c
        else:
            extra_changes.append(c)
    return change_by_symbol, extra_changes


def _collect_all_symbols(
    old_snapshot: AbiSnapshot | None,
    show_only: str | None,
    change_by_symbol: dict[str, Change],
) -> dict[str, str]:
    """Build a symbol_name → classname map covering changed and unchanged symbols.

    When *old_snapshot* is provided and *show_only* is **not** active,
    unchanged symbols are included so the pass-rate is meaningful.  When
    *show_only* is active, only filtered changes should appear.
    """
    all_symbols: dict[str, str] = {}
    if old_snapshot is not None and not show_only:
        for f in old_snapshot.functions:
            all_symbols[f.mangled] = "functions"
        for v in old_snapshot.variables:
            all_symbols[v.mangled] = "variables"
        for t in old_snapshot.types:
            all_symbols[t.name] = "types"
        for e in old_snapshot.enums:
            all_symbols[e.name] = "enums"
    # Add changed symbols that might not be in old_snapshot (e.g. additions)
    for sym, c in change_by_symbol.items():
        if sym not in all_symbols:
            all_symbols[sym] = _classname_for(c)
    return all_symbols


def _count_failures(
    changes: list[Change],
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None,
    *,
    relevant_ids: frozenset[str] | None = None,
    findings_by_id: dict[int, ReportFinding] | None = None,
) -> int:
    """Count distinct symbols that have at least one failing change."""
    symbols_with_failure: set[str] = set()
    for c in changes:
        if _is_failure(
            c, result, kind_sets, severity_config,
            relevant_ids=relevant_ids, findings_by_id=findings_by_id,
        ):
            symbols_with_failure.add(c.symbol)
    return len(symbols_with_failure)


def _emit_testcases(
    ts: ET.Element,
    all_symbols: dict[str, str],
    change_by_symbol: dict[str, Change],
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None,
    *,
    relevant_ids: frozenset[str] | None = None,
    root_cause_lookup: dict[str, tuple[str, str]] | None = None,
    findings_by_id: dict[int, ReportFinding] | None = None,
) -> None:
    """Append ``<testcase>`` elements to *ts* for every symbol in *all_symbols*.

    When *all_symbols* is empty (no snapshot, no filter), fall back to
    emitting one testcase per changed symbol only.
    """
    if all_symbols:
        for sym, classname in sorted(all_symbols.items()):
            tc = ET.SubElement(ts, "testcase")
            tc.set("name", sym)
            tc.set("classname", classname)
            if sym in change_by_symbol:
                _maybe_add_failure(
                    tc, change_by_symbol[sym], result, kind_sets, severity_config,
                    relevant_ids=relevant_ids, root_cause_lookup=root_cause_lookup,
                    findings_by_id=findings_by_id)
    else:
        # No snapshot — only emit changed symbols
        for sym, c in sorted(change_by_symbol.items()):
            tc = ET.SubElement(ts, "testcase")
            tc.set("name", sym)
            tc.set("classname", _classname_for(c))
            _maybe_add_failure(
                tc, c, result, kind_sets, severity_config,
                relevant_ids=relevant_ids, root_cause_lookup=root_cause_lookup,
                findings_by_id=findings_by_id)


def _append_extra_failures(
    ts: ET.Element,
    extra_changes: list[Change],
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None,
    *,
    relevant_ids: frozenset[str] | None = None,
    root_cause_lookup: dict[str, tuple[str, str]] | None = None,
    findings_by_id: dict[int, ReportFinding] | None = None,
) -> None:
    """Append extra ``<failure>`` children -- and, regardless of pass/fail,
    a secondary change's ``correlated_change_kind`` -- to already-existing
    testcases.

    Handles symbols that have more than one change (e.g. multiple changes
    to the same symbol). For each extra change, find the existing
    ``<testcase>`` with the matching name; a failing one also gets a new
    ``<failure>`` child.

    Only ``_add_correlation_property`` is called here, not
    ``_add_contract_properties`` -- unlike the correlation flag (one bit of
    "this testcase has a paired finding", correct to merge into the shared
    block), ``_add_contract_properties`` renders a *whole per-change*
    contract decision (relevance/reason_code/assurance/gate_contribution)
    into flat, unprefixed property names. Two changes on the same symbol can
    each carry an independently-stamped, genuinely different decision under
    ``--contract``; naively adding a second call here would either
    duplicate the ``<properties>`` block (the exact bug just fixed for
    correlation) or, if merged into one block, silently overwrite the
    primary change's contract properties with the secondary's -- a data-loss
    bug, not a fix. That gap is real but pre-existing and out of scope here:
    it needs its own design (e.g. a per-change key prefix) before more
    call sites route through it, not a drive-by symmetrical extension of
    this correlation fix (Codex review only reported the correlation half;
    fixing it alone is what's verified below).
    """
    for c in extra_changes:
        _add_correlation_property_if_testcase_found(ts, c)
        if _is_failure(
            c, result, kind_sets, severity_config,
            relevant_ids=relevant_ids, findings_by_id=findings_by_id,
        ):
            for tc in ts:
                if tc.get("name") == c.symbol:
                    _add_failure(
                        tc, c, result, kind_sets, severity_config,
                        root_cause_lookup=root_cause_lookup, findings_by_id=findings_by_id)
                    break


def _add_correlation_property_if_testcase_found(ts: ET.Element, change: Change) -> None:
    """Find the ``<testcase>`` matching *change*'s symbol among *ts*'s
    children and call :func:`_add_correlation_property` on it.

    A small lookup wrapper rather than inlining the loop at both call sites
    in :func:`_append_extra_failures` above (one for the correlation
    property, one for the conditional ``<failure>``) -- the two must run
    independently (a non-failing secondary change still needs its
    correlation recorded), so they cannot share one ``for tc in ts`` loop
    the way the pre-existing failure-only loop did.
    """
    if not change.correlated_change_kind:
        return
    for tc in ts:
        if tc.get("name") == change.symbol:
            _add_correlation_property(tc, change)
            return


def _build_testsuite(
    result: DiffResult,
    old_snapshot: AbiSnapshot | None = None,
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
    report_mode: str = "full",
) -> ET.Element:
    """Build a ``<testsuite>`` element from a single DiffResult.

    Each changed symbol becomes a ``<testcase>``.  If *old_snapshot* is
    provided and *show_only* is **not** active, unchanged symbols are also
    emitted as passing test cases so that the pass-rate is meaningful.

    When *show_only* is active, only the filtered changes are emitted
    (no unchanged snapshot symbols) so the test count matches the filter.
    """
    kind_sets = result._effective_kind_sets()

    changes = list(result.changes)
    # Scoped-only changes: scope_diff_to_app/scope_diff_to_required_symbols
    # can synthesize a Change (e.g. PE_ORDINAL_RETARGETED) that is relevant
    # to the gate but was never added to result.changes -- fold them into the
    # same testcase pipeline (symbol grouping, failure decision via
    # relevant_ids below) rather than a bespoke path, so a --used-by run
    # that fails solely because of one of these still has a testcase/failure
    # to explain it (Codex review).
    changes += list(getattr(result, "scoped_only_changes", ()) or ())
    if show_only:
        changes = apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        changes = _suppress_dangling_correlation_notes(changes)

    change_by_symbol, extra_changes = _partition_changes(changes)
    all_symbols = _collect_all_symbols(old_snapshot, show_only, change_by_symbol)

    # ADR-061 Phase 2 item 4b: resolve every verdict/category once. Built
    # from *changes*, not just result.changes, since it also carries
    # scoped_only_changes.
    from .report.finding import build_report_findings, findings_by_change_id

    findings_by_id = findings_by_change_id(
        build_report_findings(
            changes, policy=result.policy, kind_sets=kind_sets, policy_file=result.policy_file
        )
    )

    # When --used-by/--required-symbol scoping is active, relevant_ids makes
    # failures follow the scoped gate rather than the full library verdict
    # (CLI-audit P1 fix); None means no scoping is active, so behavior below
    # is unchanged from before.
    relevant_ids = getattr(result, "scoped_relevant_finding_ids", None)
    failure_count = _count_failures(
        changes, result, kind_sets, severity_config,
        relevant_ids=relevant_ids, findings_by_id=findings_by_id,
    )
    missing_labels = getattr(result, "scoped_missing_labels", ()) or ()
    # The missing-contract failure decision must follow the same severity
    # decision as the gate's own exit code (severity.missing_contract_exit_code,
    # which _scoped_exit_code floors on): under the legacy scheme (no
    # severity_config) a missing contract member is unconditionally BREAKING,
    # but under a scheme that demotes abi_breaking the scoped exit code can be
    # 0 for the same missing member -- unconditionally failing here would mark
    # a JUnit-consuming CI run failed even though the gate itself passed
    # (Codex review).
    missing_blocks = severity_config is None
    if severity_config is not None:
        from .severity import missing_contract_exit_code

        missing_blocks = missing_contract_exit_code(severity_config) != 0
    # A missing-contract label has no backing Change/ChangeKind, so it can't
    # run through apply_show_only above -- but --show-only's severity
    # dimension still applies: without this, a --show-only run excluding
    # breaking findings would still count/emit a failing testcase for a
    # missing required symbol the filter was meant to hide (Codex review,
    # mirrors the identical sarif.to_sarif/_fold_scoped_compat_into_text
    # fix). Element/action tokens don't cleanly apply to "a symbol is simply
    # absent", so only the severity dimension is checked.
    if show_only and missing_labels:
        from .reporter_markdown import ShowOnlyFilter

        missing_severity_label = "breaking" if missing_blocks else "compatible"
        show_only_severities = ShowOnlyFilter.parse(show_only).severities
        if show_only_severities and missing_severity_label not in show_only_severities:
            missing_labels = ()
    total = (len(all_symbols) if all_symbols else len(change_by_symbol)) + len(
        missing_labels
    )
    if missing_blocks:
        failure_count += len(missing_labels)

    ts = ET.Element("testsuite")
    ts.set("name", result.library)
    ts.set("tests", str(total))
    ts.set("failures", str(failure_count))
    ts.set("errors", "0")

    _add_disposition_audit_properties(ts, result, severity_config)
    _add_scoped_properties(ts, result)

    # G29 Phase 3 (ADR-052 follow-up): --report-mode root-cause adds
    # rootCauseId/rootCause attributes to each <failure> rather than
    # restructuring the per-symbol <testcase> tree — see
    # _root_cause_lookup's docstring. None (the default "full" mode) keeps
    # every downstream call a no-op, matching pre-existing behavior exactly.
    root_cause_lookup = (
        _root_cause_lookup(changes, missing_labels, getattr(result, "gate_scope", None))
        if report_mode == "root-cause"
        else None
    )

    _emit_testcases(
        ts, all_symbols, change_by_symbol, result, kind_sets, severity_config,
        relevant_ids=relevant_ids, root_cause_lookup=root_cause_lookup,
        findings_by_id=findings_by_id,
    )
    _append_extra_failures(
        ts, extra_changes, result, kind_sets, severity_config,
        relevant_ids=relevant_ids, root_cause_lookup=root_cause_lookup,
        findings_by_id=findings_by_id,
    )
    _emit_missing_contract_testcases(
        ts,
        missing_labels,
        getattr(result, "gate_scope", None),
        blocks=missing_blocks,
        root_cause_lookup=root_cause_lookup,
    )

    return ts


def _emit_missing_contract_testcases(
    ts: ET.Element,
    missing_labels: tuple[str, ...],
    gate_scope: str | None,
    *,
    blocks: bool = True,
    root_cause_lookup: dict[str, tuple[str, str]] | None = None,
) -> None:
    """Emit a ``<testcase>`` per missing required symbol/version/entrypoint.

    A required contract member absent from the new library (--used-by's
    ``missing_symbols``/``missing_versions``, or --required-symbol's
    ``missing_entrypoints``) has no backing diff ``Change`` -- without a
    synthetic testcase the gate's own ``failures`` count could be nonzero
    while nothing in the XML explains why (CLI-audit P1, mirrors
    ``sarif._missing_contract_result``).

    *blocks* (the caller's severity-aware decision, mirroring
    ``sarif._missing_contract_result``) decides whether the testcase gets a
    ``<failure>`` child: a testcase always exists so the missing member is
    still visible in the report, but only fails when the gate itself
    considers it blocking.
    """
    classname = (
        "used_by_contract" if gate_scope == "used_by" else "required_symbol_contract"
    )
    for label in missing_labels:
        tc = ET.SubElement(ts, "testcase")
        tc.set("name", label)
        tc.set("classname", classname)
        if blocks:
            fail = ET.SubElement(tc, "failure")
            fail.set(
                "message",
                f"Required symbol/version '{label}' is missing from the new library.",
            )
            fail.set("type", "MISSING_CONTRACT_MEMBER")
            if root_cause_lookup is not None:
                entry = root_cause_lookup.get(label)
                if entry is not None:
                    fail.set("rootCauseId", entry[0])
                    fail.set("rootCause", entry[1])


def _add_disposition_audit_properties(
    ts: ET.Element, result: DiffResult, severity_config: object | None = None
) -> None:
    """Append ADR-067 D3's raw-versus-effective counts as testsuite properties.

    A JUnit consumer reads ``tests``/``failures``, which are the *effective*
    numbers by construction -- a fully suppressed comparison reports zero
    failures and would otherwise carry no trace that anything was detected at
    all. These properties are that trace, in the one mechanism JUnit gives for
    suite-level metadata; the per-disposition counts are emitted individually
    so a dashboard can chart one without parsing a blob.

    Its own ``<properties>`` element rather than sharing
    :func:`_add_scoped_properties`': that block is emitted only under
    ``--used-by``/``--required-symbol(s)`` scoping, and these counts are owed
    unconditionally. Multiple ``<properties>`` children are valid JUnit XML.
    """
    from .report.disposition_audit import compute_disposition_audit

    audit = compute_disposition_audit(result, severity_config)
    props = ET.SubElement(ts, "properties")

    def _prop(name: str, value: str) -> None:
        p = ET.SubElement(props, "property")
        p.set("name", name)
        p.set("value", value)

    _prop("abicheck.detected_total", str(audit.detected_total))
    _prop("abicheck.effective_total", str(audit.effective_total))
    for name, count in audit.counts:
        _prop(f"abicheck.disposition.{name}", str(count))
    for rule, count in audit.rules:
        _prop(
            f"abicheck.disposition_rule.{rule.rule_id or 'rule'}",
            f"{count} finding(s); reason={rule.reason or 'none'}; "
            f"intent={rule.intent}; source={rule.source_file or 'inline'}",
        )
    if audit.not_evaluated_detectors:
        _prop(
            "abicheck.not_evaluated_detectors",
            ",".join(d.name for d in audit.not_evaluated_detectors),
        )


def _add_scoped_properties(ts: ET.Element, result: DiffResult) -> None:
    """Append a ``<properties>`` block when ``--used-by``/``--required-symbol(s)``
    scoping was requested (ADR-043 + CLI-audit P1).

    The scoped gate is authoritative for this testsuite's own ``failures``
    count and each ``<testcase>``'s pass/fail status -- ``result.verdict``
    (the full, unscoped library verdict) is still reported here as
    ``abicheck.full_library_verdict`` for context, but no longer drives what
    a JUnit-consuming CI dashboard treats as failing.
    """
    scoped_verdict = getattr(result, "scoped_verdict", None)
    if scoped_verdict is None:
        return
    props = ET.SubElement(ts, "properties")

    def _prop(name: str, value: str) -> None:
        p = ET.SubElement(props, "property")
        p.set("name", name)
        p.set("value", value)

    gate_scope = getattr(result, "gate_scope", None)
    if gate_scope is not None:
        _prop("abicheck.gate_scope", gate_scope)
    _prop("abicheck.gate_verdict", scoped_verdict.value)
    _prop("abicheck.full_library_verdict", result.verdict.value)
    # Back-compat alias for the property's original name.
    _prop("abicheck.scoped_verdict", scoped_verdict.value)
    relevant_ids = getattr(result, "scoped_relevant_finding_ids", None) or frozenset()
    relevant_in_changes = sum(
        1 for c in result.changes if _finding_id(c) in relevant_ids
    )
    # Scoped-only changes and missing-contract members are relevant by
    # construction and never in result.changes, so they count toward
    # relevant_finding_count but not unrelated_finding_count, which only
    # counts irrelevant entries *within* result.changes (CodeRabbit review,
    # mirrors sarif._scoped_gate_properties).
    scoped_only_count = len(getattr(result, "scoped_only_changes", ()) or ())
    missing_count = len(getattr(result, "scoped_missing_labels", ()) or ())
    relevant_count = relevant_in_changes + scoped_only_count + missing_count
    _prop("abicheck.relevant_finding_count", str(relevant_count))
    _prop(
        "abicheck.unrelated_finding_count",
        str(len(result.changes) - relevant_in_changes),
    )
    scoped_exit_code = getattr(result, "scoped_exit_code", None)
    scoped_exit_code_scheme = getattr(result, "scoped_exit_code_scheme", None)
    if scoped_exit_code is not None:
        _prop("abicheck.gate_exit_code", str(scoped_exit_code))
        _prop("abicheck.gate_exit_code_scheme", str(scoped_exit_code_scheme))
        # Back-compat aliases.
        _prop("abicheck.scoped_exit_code", str(scoped_exit_code))
        _prop("abicheck.scoped_exit_code_scheme", str(scoped_exit_code_scheme))
    used_by = getattr(result, "used_by", None)
    if used_by is not None:
        _prop("abicheck.used_by_app_count", str(len(used_by)))
    required_symbols = getattr(result, "required_symbols", None)
    if required_symbols is not None:
        _prop(
            "abicheck.required_symbol_contract_verdict",
            str(required_symbols.get("verdict", "")),
        )


def _maybe_add_failure(
    tc: ET.Element,
    change: Change,
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None = None,
    *,
    relevant_ids: frozenset[str] | None = None,
    root_cause_lookup: dict[str, tuple[str, str]] | None = None,
    findings_by_id: dict[int, ReportFinding] | None = None,
) -> None:
    """Add a ``<failure>`` child to *tc* if the change is a failure, and
    ``<properties>`` blocks with ADR-049's per-finding contract decision
    (CLI-audit P1) and any cross-detector correlation, regardless of
    pass/fail.
    """
    _add_contract_properties(tc, change, result, severity_config)
    _add_correlation_property(tc, change)
    if _is_failure(
        change, result, kind_sets, severity_config,
        relevant_ids=relevant_ids, findings_by_id=findings_by_id,
    ):
        _add_failure(
            tc, change, result, kind_sets, severity_config,
            root_cause_lookup=root_cause_lookup, findings_by_id=findings_by_id,
        )


def _add_contract_properties(
    tc: ET.Element,
    change: Change,
    result: DiffResult,
    severity_config: SeverityConfig | None,
) -> None:
    """Append a ``<properties>`` block to testcase *tc* with the same
    canonical per-finding contract shape reporter.py's JSON output and
    sarif.py's ``properties`` already carry (contract_relevance/
    contract_reason_code/contract_assurance/compatibility_evaluation_status/
    compatibility_decision/gate_contribution/contract_evidence_refs).

    A finding whose ``contract_relevance`` was never stamped (every run
    without ``--contract``, the default) gets nothing appended --
    this keeps every pre-existing JUnit report byte-for-byte unchanged.
    """
    from .contract_gating import contract_relevance_of, evaluation_status_of
    from .contract_relevance_types import CompatibilityEvaluationStatus
    from .severity import gate_contribution_for_change

    relevance = contract_relevance_of(change)
    if relevance is None:
        return
    props = ET.SubElement(tc, "properties")

    def _prop(name: str, value: str) -> None:
        p = ET.SubElement(props, "property")
        p.set("name", name)
        p.set("value", value)

    _prop("abicheck.contract_relevance", relevance.value)
    if change.contract_reason_code:
        _prop("abicheck.contract_reason_code", change.contract_reason_code)
    if change.contract_assurance is not None:
        _prop("abicheck.contract_assurance", change.contract_assurance.value)
    # evaluation_status_of always resolves to a real status once `relevance`
    # is known non-None (it falls back to deriving one from the relevance
    # itself -- see its own docstring), so there is no reachable `None`
    # branch to guard here -- `cast` tells mypy that without adding one.
    status = cast(CompatibilityEvaluationStatus, evaluation_status_of(change))
    _prop("abicheck.compatibility_evaluation_status", status.value)
    decision = getattr(change, "compatibility_decision", None)
    _prop("abicheck.compatibility_decision", getattr(decision, "value", "") or "")
    _prop(
        "abicheck.gate_contribution",
        str(
            gate_contribution_for_change(
                change,
                severity_config,
                policy=result.policy,
                policy_file=result.policy_file,
            )
        ),
    )
    if change.contract_evidence_refs is not None:
        _prop(
            "abicheck.contract_evidence_refs", ",".join(change.contract_evidence_refs)
        )


def _add_correlation_property(tc: ET.Element, change: Change) -> None:
    """Append a ``abicheck.correlated_change_kind`` ``<property>`` recording
    ``Change.correlated_change_kind`` when set (e.g. a ``LAYOUT_UNVERIFIABLE``
    finding annotated by
    ``post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged`` as
    sharing its evidence gap with a co-reported ``TYPE_VTABLE_CHANGED``).

    Deliberately independent of ``--contract``, unlike
    ``_add_contract_properties`` above -- this correlation is stamped on
    every run, not only under contract evaluation, so gating it the same way
    would silently drop it on the default path. Before this, only JSON
    (reporter.py) and SARIF (sarif.py) rendered this field (Codex review).

    Must always run *after* ``_add_contract_properties`` and reuse whatever
    ``<properties>`` element it already appended, rather than creating a
    second sibling one -- a testcase carries at most one ``<properties>``
    block by JUnit convention, and every consumer (including this repo's
    own tests) looks it up via a single ``tc.find("properties")``, which
    only ever sees the first match (Codex review, fresh evidence: this
    silently dropped the correlation whenever contract evaluation had
    already stamped the same testcase).

    When creating a fresh ``<properties>`` element (none already present),
    it is inserted as *tc*'s first child rather than appended at the end.
    For a *secondary* same-symbol change (``_append_extra_failures``), the
    primary change's own ``<failure>`` child may already be present on
    *tc* by the time this runs -- appending here would then produce
    ``<testcase><failure/><properties/></testcase>``, which schema-
    validating JUnit consumers reject or ignore, since the JUnit XSD
    requires ``<properties>`` (if present) before any
    ``<failure>``/``<error>``/``<skipped>`` result element (Codex review,
    fresh evidence).
    """
    if not change.correlated_change_kind:
        return
    # Reuse the <properties> block _add_contract_properties may have already
    # appended (under --contract) rather than creating a second
    # sibling element -- JUnit consumers, including this repo's own tests,
    # look up a testcase's properties via `tc.find("properties")`, which
    # only ever sees the first such element (Codex review).
    props = tc.find("properties")
    if props is None:
        # tc.insert(0, ...), not ET.SubElement (which would append after any
        # <failure> a primary change already added) -- see this function's
        # own docstring for why ordering matters here.
        props = ET.Element("properties")
        tc.insert(0, props)
    p = ET.SubElement(props, "property")
    p.set("name", "abicheck.correlated_change_kind")
    p.set("value", change.correlated_change_kind)


def _add_failure(
    tc: ET.Element,
    change: Change,
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None = None,
    *,
    root_cause_lookup: dict[str, tuple[str, str]] | None = None,
    findings_by_id: dict[int, ReportFinding] | None = None,
) -> None:
    """Append a ``<failure>`` element to testcase *tc*."""
    ftype = _failure_type(
        change, result, kind_sets, severity_config, findings_by_id=findings_by_id
    )
    description = change.description or change.kind.value.replace("_", " ")
    message = f"{change.kind.value}: {description}"

    fail = ET.SubElement(tc, "failure")
    fail.set("message", message)
    fail.set("type", ftype)
    if root_cause_lookup is not None:
        entry = root_cause_lookup.get(_finding_id(change))
        if entry is not None:
            fail.set("rootCauseId", entry[0])
            fail.set("rootCause", entry[1])

    # Body text: detailed explanation + source location
    body_parts = [description]
    if change.old_value is not None or change.new_value is not None:
        old = change.old_value if change.old_value is not None else "?"
        new = change.new_value if change.new_value is not None else "?"
        body_parts.append(f"({old} \u2192 {new})")
    if change.source_location:
        body_parts.append(f"Source: {change.source_location}")
    fail.text = "\n".join(body_parts)


# ---------------------------------------------------------------------------
# Error testsuite — represent failed compare-release pairs
# ---------------------------------------------------------------------------


def _build_error_testsuite(library: str, error_msg: str) -> ET.Element:
    """Build a ``<testsuite>`` with a single errored testcase, used by ``to_junit_xml_multi`` for a library whose comparison failed (bad input, missing headers) so CI dashboards show the failure rather than silently omitting the library."""
    ts = ET.Element("testsuite")
    ts.set("name", library)
    ts.set("tests", "1")
    ts.set("failures", "0")
    ts.set("errors", "1")

    tc = ET.SubElement(ts, "testcase")
    tc.set("name", library)
    tc.set("classname", "metadata")

    err = ET.SubElement(tc, "error")
    err.set("message", f"Comparison failed: {error_msg}")
    err.set("type", "ERROR")
    err.text = error_msg

    return ts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def to_junit_xml(
    result: DiffResult,
    old_snapshot: AbiSnapshot | None = None,
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
    report_mode: str = "full",
) -> str:
    """Convert a single DiffResult to a JUnit XML string.

    Parameters
    ----------
    result:
        The comparison result.
    old_snapshot:
        When provided, all symbols from the old snapshot appear as test
        cases (unchanged symbols pass).  Without it, only changed symbols
        appear.
    show_only:
        Optional ``--show-only`` filter string.
    severity_config:
        Optional severity configuration (from ``--severity-preset`` or
        ``severity:`` config overrides).  When provided, the JUnit failure
        classification honours user-configured severity escalations.
    report_mode:
        ``"full"`` (default) or ``"root-cause"`` (G29 Phase 3, ADR-052
        follow-up) — the latter adds ``rootCauseId``/``rootCause``
        attributes to each ``<failure>`` element (see
        :func:`_root_cause_lookup`); it does not restructure the
        per-symbol ``<testcase>`` tree the way JSON/markdown/SARIF's
        root-cause mode regroups findings. Any other value (e.g.
        ``"leaf"``/``"impact"``) renders identically to ``"full"``, same as
        before this parameter existed.

    Returns
    -------
    str
        JUnit XML document as a string.
    """
    root = ET.Element("testsuites")
    root.set("name", "abicheck")

    ts = _build_testsuite(
        result,
        old_snapshot,
        show_only=show_only,
        severity_config=severity_config,
        report_mode=report_mode,
    )
    root.append(ts)

    # ADR-049 plan Section 6.1: "JUnit represents NOT_CHECKABLE according to
    # its coverage/error contract, never as a passed compatibility test."
    # A contract-coverage failure is not a compatibility result at all -- it
    # says the evidence needed to decide was missing -- so it is an <error>
    # in its own suite, which is exactly what JUnit's error-vs-failure split
    # means (an error is "the test could not run", a failure is "it ran and
    # the assertion failed"). Emitting it as a passing testcase, or omitting
    # it, would let a CI dashboard read "no evidence" as "compatible".
    errors = _append_coverage_suite(root, result)
    warnings = append_coverage_warnings_suite(root, result)
    # Roll up counts
    root.set("tests", str(int(ts.get("tests", "0")) + errors + warnings))
    root.set("failures", ts.get("failures", "0"))
    root.set("errors", str(errors))

    return _to_xml_string(root)


def _append_coverage_suite(root: ET.Element, result: DiffResult) -> int:
    """Append the contract-coverage suite, returning how many errors it holds.

    Nothing at all when the run computed no contract context (the default),
    so an ordinary report is unchanged. A run whose selected contract domain
    *closed* gets an empty suite rather than no suite: "checked, nothing
    missing" and "never checked" are different states, and a consumer must be
    able to tell them apart.
    """
    from .contract_coverage_ledger import coverage_failures_for_context

    ctx = getattr(result, "contract_context", None)
    if ctx is None:
        return 0
    failures = coverage_failures_for_context(ctx)
    # Qualified by library: a multi-library document appends one of these per
    # result, and a coverage failure identifies only its provider and side.
    # Two libraries failing the same provider on the same side produced two
    # indistinguishable suites, so a consumer could not attribute either
    # error to a library (CodeRabbit review).
    library = getattr(result, "library", "") or "unknown"
    suite = ET.SubElement(root, "testsuite")
    suite.set("name", f"abicheck.contract_coverage.{library}")
    suite.set("tests", str(len(failures)))
    suite.set("failures", "0")
    suite.set("errors", str(len(failures)))
    for failure in failures:
        case = ET.SubElement(suite, "testcase")
        case.set("classname", f"abicheck.contract_coverage.{library}")
        case.set("name", f"{failure.provider}:{failure.side}")
        error = ET.SubElement(case, "error")
        error.set("type", failure.reason)
        error.set(
            "message",
            f"contract coverage: {failure.provider} ({failure.side}) could not "
            f"close the {failure.mode} domain ({failure.reason}); this finding "
            "cannot be suppressed",
        )
    return len(failures)


def to_junit_xml_multi(
    results: list[tuple[DiffResult, AbiSnapshot | None]],
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
    error_libraries: list[dict[str, object]] | None = None,
    report_mode: str = "full",
) -> str:
    """Convert multiple DiffResults to a JUnit XML string (compare-release).

    Each ``(DiffResult, old_snapshot)`` pair becomes a ``<testsuite>``.

    *error_libraries* is a list of ``{"library": ..., "error": ...}``
    dicts for libraries whose comparison failed.  Each becomes a
    ``<testsuite>`` with a single ``<error>`` testcase so CI dashboards
    reflect the failure.

    *report_mode*: see :func:`to_junit_xml`.
    """
    root = ET.Element("testsuites")
    root.set("name", "abicheck")

    total_tests = 0
    total_failures = 0
    total_errors = 0

    for result, old_snap in results:
        ts = _build_testsuite(
            result,
            old_snap,
            show_only=show_only,
            severity_config=severity_config,
            report_mode=report_mode,
        )
        root.append(ts)
        total_tests += int(ts.get("tests", "0"))
        total_failures += int(ts.get("failures", "0"))
        # Per result, not once for the document: each library carries its own
        # contract context, so a multi-library run can have one uncheckable
        # comparison beside several closed ones. Wiring only the single-result
        # renderer left a multi-result document reporting errors="0" with no
        # coverage suite at all, so a consumer could read an uncheckable
        # comparison as having no coverage errors (Codex review).
        coverage_errors = _append_coverage_suite(root, result)
        total_tests += coverage_errors
        total_errors += coverage_errors
        # Per result, mirroring the coverage-error suite above: each library carries its own coverage_warnings, not a document-wide list.
        total_tests += append_coverage_warnings_suite(root, result)

    for entry in error_libraries or []:
        ts = _build_error_testsuite(
            str(entry.get("library", "unknown")),
            str(entry.get("error", "comparison failed")),
        )
        root.append(ts)
        total_tests += 1
        total_errors += 1

    root.set("tests", str(total_tests))
    root.set("failures", str(total_failures))
    root.set("errors", str(total_errors))

    return _to_xml_string(root)


def _to_xml_string(root: ET.Element) -> str:
    """Serialize a JUnit element tree to XML (via ``report.render_xml``)."""
    from .report.render_xml import render_element_as_xml

    return render_element_as_xml(root)


def to_junit_xml_not_comparable(
    library: str, old_version: str, new_version: str, kind: str, message: str
) -> str:
    """Render an ADR-050 D2 comparability-gate hard failure as JUnit XML.

    ``checker.compare``'s gate raises before any ``DiffResult`` exists, so
    there is nothing for :func:`to_junit_xml` to render — unlike an ordinary
    verdict, this has no changes/policy/scope to report. A single ``<testsuite
    errors="1">`` with one errored ``<testcase>`` mirrors
    :func:`_build_error_testsuite`'s existing "library whose comparison
    failed" shape (used by :func:`to_junit_xml_multi` for a genuine crash)
    rather than a bespoke structure, so any CI dashboard already parsing
    abicheck's JUnit output surfaces this the same way: a build-breaking
    error, not a silently-empty report.
    """
    root = ET.Element("testsuites")
    root.set("name", "abicheck")
    root.set("tests", "1")
    root.set("failures", "0")
    root.set("errors", "1")

    ts = ET.SubElement(root, "testsuite")
    ts.set("name", library)
    ts.set("tests", "1")
    ts.set("failures", "0")
    ts.set("errors", "1")

    tc = ET.SubElement(ts, "testcase")
    tc.set("name", f"{library} old={old_version!r} new={new_version!r}")
    tc.set("classname", "comparability")

    err = ET.SubElement(tc, "error")
    err.set(
        "message",
        f"Not comparable: '{library}' old={old_version!r} new={new_version!r} "
        f"were not extracted under a comparable profile/scope contract "
        f"(ADR-050 D1/D2): {message}",
    )
    err.set("type", kind)
    err.text = message

    return _to_xml_string(root)
