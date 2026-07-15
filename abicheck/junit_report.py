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
from .reporter import apply_show_only

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
    """
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


def _failure_type(change: Change, result: DiffResult, kind_sets: KindSets) -> str:
    """Return the ``type`` attribute for a ``<failure>`` element.

    Uses the same canonical per-finding verdict as ``_is_failure`` so the
    reported type always matches why the finding failed. Takes the caller's
    precomputed *kind_sets* rather than recomputing them (``_build_testsuite``
    already builds them once per report; ``DiffResult._effective_verdict_for_change``
    would otherwise rebuild them per finding).
    """
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
) -> int:
    """Count distinct symbols that have at least one failing change."""
    symbols_with_failure: set[str] = set()
    for c in changes:
        if _is_failure(c, result, kind_sets, severity_config):
            symbols_with_failure.add(c.symbol)
    return len(symbols_with_failure)


def _emit_testcases(
    ts: ET.Element,
    all_symbols: dict[str, str],
    change_by_symbol: dict[str, Change],
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None,
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
            )


def _append_extra_failures(
    ts: ET.Element,
    extra_changes: list[Change],
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None,
) -> None:
    """Append extra ``<failure>`` children to already-existing testcases.

    Handles symbols that have more than one change (e.g. multiple changes
    to the same symbol).  For each extra failing change, find the existing
    ``<testcase>`` with the matching name and attach a new ``<failure>``.
    """
    for c in extra_changes:
        if _is_failure(c, result, kind_sets, severity_config):
            for tc in ts:
                if tc.get("name") == c.symbol:
                    _add_failure(tc, c, result, kind_sets)
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
    failure_count = _count_failures(changes, result, kind_sets, severity_config)

    total = len(all_symbols) if all_symbols else len(change_by_symbol)

    ts = ET.Element("testsuite")
    ts.set("name", result.library)
    ts.set("tests", str(total))
    ts.set("failures", str(failure_count))
    ts.set("errors", "0")

    _emit_testcases(
        ts,
        all_symbols,
        change_by_symbol,
        result,
        kind_sets,
        severity_config,
    )
    _append_extra_failures(ts, extra_changes, result, kind_sets, severity_config)

    return ts


def _maybe_add_failure(
    tc: ET.Element,
    change: Change,
    result: DiffResult,
    kind_sets: KindSets,
    severity_config: SeverityConfig | None = None,
) -> None:
    """Add a ``<failure>`` child to *tc* if the change is a failure."""
    if _is_failure(change, result, kind_sets, severity_config):
        _add_failure(tc, change, result, kind_sets)


def _add_failure(
    tc: ET.Element,
    change: Change,
    result: DiffResult,
    kind_sets: KindSets,
) -> None:
    """Append a ``<failure>`` element to testcase *tc*."""
    ftype = _failure_type(change, result, kind_sets)
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
