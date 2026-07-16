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

import io
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .checker_policy import ChangeKind, Verdict
from .checker_types import Change, DiffResult
from .reporter import _finding_id, apply_show_only

if TYPE_CHECKING:
    from .model import AbiSnapshot
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


def _is_failure(
    change: Change,
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None = None,
    *,
    relevant_ids: frozenset[str] | None = None,
) -> bool:
    """Return True if the change should be a JUnit ``<failure>``.

    Routes through ``DiffResult._effective_verdict_for_change`` — the single
    canonical per-finding verdict, which honours PolicyFile overrides, the
    A4 per-finding ``effective_verdict`` (ADR-027), and frozen-namespace
    escalation guards — so the JUnit file can never disagree with the JSON
    report or the severity-aware exit code.

    When *severity_config* is given (from ``--severity-preset`` or
    ``--severity-*`` overrides), it is the sole source of truth — a finding
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
    """
    if relevant_ids is not None and _finding_id(change) not in relevant_ids:
        return False
    if severity_config is not None:
        from .severity import SeverityLevel, classify_effective_change

        cat = classify_effective_change(
            change,
            policy=result.policy,
            kind_sets=kind_sets,
            policy_file=result.policy_file,
        )
        return severity_config.level_for(cat) == SeverityLevel.ERROR
    verdict = result._effective_verdict_for_change(change)
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
) -> str:
    """Return the ``type`` attribute for a ``<failure>`` element.

    Uses the same canonical per-finding verdict/category as ``_is_failure``
    so the reported type always matches why the finding failed. Takes the
    caller's precomputed *kind_sets* rather than recomputing them
    (``_build_testsuite`` already builds them once per report;
    ``DiffResult._effective_verdict_for_change`` would otherwise rebuild them
    per finding).

    When *severity_config* is given, ``_is_failure`` decides pass/fail from
    the finding's effective *category* (:func:`classify_effective_change`),
    not its raw verdict — a COMPATIBLE addition promoted to ``error``
    fails even though its verdict is COMPATIBLE. Without also deriving
    ``type`` from that same category, such a failure would report
    ``type="COMPATIBLE"`` (``_VERDICT_TO_JUNIT_TYPE``'s fallback for any
    verdict it doesn't recognise), contradicting the very reason it failed.
    """
    if severity_config is not None:
        from .severity import IssueCategory, classify_effective_change

        category = classify_effective_change(
            change,
            policy=result.policy,
            kind_sets=kind_sets,
            policy_file=result.policy_file,
        )
        if category == IssueCategory.POTENTIAL_BREAKING:
            # IssueCategory doesn't itself distinguish API break from
            # deployment risk (both fold into POTENTIAL_BREAKING) — recover
            # that distinction from the finding's *effective* verdict, not
            # raw kind-set membership: a per-finding effective_verdict
            # override/modulation (pattern-verdicts, PolicyFile) can move a
            # change's verdict without changing which kind-set its raw kind
            # belongs to, so kind-set membership alone could contradict the
            # category already resolved above (CodeRabbit review, PR #557).
            from .severity import effective_verdict_for_change

            verdict = effective_verdict_for_change(
                change,
                policy=result.policy,
                kind_sets=kind_sets,
                policy_file=result.policy_file,
            )
            if verdict == Verdict.API_BREAK:
                return "API_BREAK"
            if verdict == Verdict.COMPATIBLE_WITH_RISK:
                return "COMPATIBLE_WITH_RISK"
            return "POTENTIAL_BREAKING"
        return _CATEGORY_TO_JUNIT_TYPE.get(category.value, "COMPATIBLE")

    from .severity import effective_verdict_for_change

    verdict = effective_verdict_for_change(
        change,
        policy=result.policy,
        kind_sets=kind_sets,
        policy_file=result.policy_file,
    )
    return _VERDICT_TO_JUNIT_TYPE.get(verdict, "COMPATIBLE")


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
) -> int:
    """Count distinct symbols that have at least one failing change."""
    symbols_with_failure: set[str] = set()
    for c in changes:
        if _is_failure(c, result, kind_sets, severity_config, relevant_ids=relevant_ids):
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
                    tc,
                    change_by_symbol[sym],
                    result,
                    kind_sets,
                    severity_config,
                    relevant_ids=relevant_ids,
                )
    else:
        # No snapshot — only emit changed symbols
        for sym, c in sorted(change_by_symbol.items()):
            tc = ET.SubElement(ts, "testcase")
            tc.set("name", sym)
            tc.set("classname", _classname_for(c))
            _maybe_add_failure(
                tc,
                c,
                result,
                kind_sets,
                severity_config,
                relevant_ids=relevant_ids,
            )


def _append_extra_failures(
    ts: ET.Element,
    extra_changes: list[Change],
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None,
    *,
    relevant_ids: frozenset[str] | None = None,
) -> None:
    """Append extra ``<failure>`` children to already-existing testcases.

    Handles symbols that have more than one change (e.g. multiple changes
    to the same symbol).  For each extra failing change, find the existing
    ``<testcase>`` with the matching name and attach a new ``<failure>``.
    """
    for c in extra_changes:
        if _is_failure(c, result, kind_sets, severity_config, relevant_ids=relevant_ids):
            for tc in ts:
                if tc.get("name") == c.symbol:
                    _add_failure(tc, c, result, kind_sets, severity_config)
                    break


def _build_testsuite(
    result: DiffResult,
    old_snapshot: AbiSnapshot | None = None,
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
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

    change_by_symbol, extra_changes = _partition_changes(changes)
    all_symbols = _collect_all_symbols(old_snapshot, show_only, change_by_symbol)

    # When --used-by/--required-symbol scoping is active, relevant_ids makes
    # failures follow the scoped gate rather than the full library verdict
    # (CLI-audit P1 fix); None means no scoping is active, so behavior below
    # is unchanged from before.
    relevant_ids = getattr(result, "scoped_relevant_finding_ids", None)
    failure_count = _count_failures(
        changes, result, kind_sets, severity_config, relevant_ids=relevant_ids
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
    total = (len(all_symbols) if all_symbols else len(change_by_symbol)) + len(missing_labels)
    if missing_blocks:
        failure_count += len(missing_labels)

    ts = ET.Element("testsuite")
    ts.set("name", result.library)
    ts.set("tests", str(total))
    ts.set("failures", str(failure_count))
    ts.set("errors", "0")

    _add_scoped_properties(ts, result)

    _emit_testcases(
        ts,
        all_symbols,
        change_by_symbol,
        result,
        kind_sets,
        severity_config,
        relevant_ids=relevant_ids,
    )
    _append_extra_failures(
        ts, extra_changes, result, kind_sets, severity_config, relevant_ids=relevant_ids
    )
    _emit_missing_contract_testcases(
        ts, missing_labels, getattr(result, "gate_scope", None), blocks=missing_blocks
    )

    return ts


def _emit_missing_contract_testcases(
    ts: ET.Element, missing_labels: tuple[str, ...], gate_scope: str | None,
    *, blocks: bool = True,
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
    classname = "used_by_contract" if gate_scope == "used_by" else "required_symbol_contract"
    for label in missing_labels:
        tc = ET.SubElement(ts, "testcase")
        tc.set("name", label)
        tc.set("classname", classname)
        if blocks:
            fail = ET.SubElement(tc, "failure")
            fail.set("message", f"Required symbol/version '{label}' is missing from the new library.")
            fail.set("type", "MISSING_CONTRACT_MEMBER")


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
    relevant_in_changes = sum(1 for c in result.changes if _finding_id(c) in relevant_ids)
    # Scoped-only changes and missing-contract members are relevant by
    # construction and never in result.changes, so they count toward
    # relevant_finding_count but not unrelated_finding_count, which only
    # counts irrelevant entries *within* result.changes (CodeRabbit review,
    # mirrors sarif._scoped_gate_properties).
    scoped_only_count = len(getattr(result, "scoped_only_changes", ()) or ())
    missing_count = len(getattr(result, "scoped_missing_labels", ()) or ())
    relevant_count = relevant_in_changes + scoped_only_count + missing_count
    _prop("abicheck.relevant_finding_count", str(relevant_count))
    _prop("abicheck.unrelated_finding_count", str(len(result.changes) - relevant_in_changes))
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
) -> None:
    """Add a ``<failure>`` child to *tc* if the change is a failure."""
    if _is_failure(change, result, kind_sets, severity_config, relevant_ids=relevant_ids):
        _add_failure(tc, change, result, kind_sets, severity_config)


def _add_failure(
    tc: ET.Element,
    change: Change,
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None = None,
) -> None:
    """Append a ``<failure>`` element to testcase *tc*."""
    ftype = _failure_type(change, result, kind_sets, severity_config)
    description = change.description or change.kind.value.replace("_", " ")
    message = f"{change.kind.value}: {description}"

    fail = ET.SubElement(tc, "failure")
    fail.set("message", message)
    fail.set("type", ftype)

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
    """Build a ``<testsuite>`` with a single errored testcase.

    Used by ``to_junit_xml_multi`` to represent libraries whose comparison
    failed (e.g. bad input, missing headers) so that CI dashboards show
    the failure rather than silently omitting the library.
    """
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
        ``--severity-*`` overrides).  When provided, the JUnit failure
        classification honours user-configured severity escalations.

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
    )
    root.append(ts)

    # Roll up counts
    root.set("tests", ts.get("tests", "0"))
    root.set("failures", ts.get("failures", "0"))
    root.set("errors", "0")

    return _to_xml_string(root)


def to_junit_xml_multi(
    results: list[tuple[DiffResult, AbiSnapshot | None]],
    *,
    show_only: str | None = None,
    severity_config: SeverityConfig | None = None,
    error_libraries: list[dict[str, object]] | None = None,
) -> str:
    """Convert multiple DiffResults to a JUnit XML string (compare-release).

    Each ``(DiffResult, old_snapshot)`` pair becomes a ``<testsuite>``.

    *error_libraries* is a list of ``{"library": ..., "error": ...}``
    dicts for libraries whose comparison failed.  Each becomes a
    ``<testsuite>`` with a single ``<error>`` testcase so CI dashboards
    reflect the failure.
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
        )
        root.append(ts)
        total_tests += int(ts.get("tests", "0"))
        total_failures += int(ts.get("failures", "0"))

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
    """Serialize an ElementTree element to an XML string with declaration."""
    ET.indent(root)
    tree = ET.ElementTree(root)
    buf = io.BytesIO()
    tree.write(buf, encoding="UTF-8", xml_declaration=True)
    return buf.getvalue().decode("UTF-8")
