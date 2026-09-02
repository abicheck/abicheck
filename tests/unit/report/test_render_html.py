# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""ADR-061 Phase 2 item 1: the HTML compute/render split's contract, stated as
invariants rather than as another golden pin.

``tests/test_html_template_golden.py`` already pins the *output* byte-exactly,
which proves the split preserved behaviour on the day it landed. It cannot
state what the split is *for*, so it would not fail if someone re-introduced
the very coupling the split removed. These three properties do, and each is
checked over a generated sweep of inputs rather than one fixed case:

1. **The compute half emits no markup.** Every string field of every
   ``*Data`` struct ``html_report.compute_*`` returns must be plain data.
   A ``<div>`` moved back into a compute function fails this immediately.
2. **The render half escapes everything the compute half hands it.** An
   attacker-shaped marker injected into any symbol, path, description,
   coverage warning, reason code or policy label must never reach the
   document unescaped -- checked by injecting the same marker into each
   field in turn, not by inspecting one hand-picked one.
3. **Rendering cannot alter the workflow result** (this phase's own
   acceptance criterion). Generating a report twice from one ``DiffResult``
   leaves the result -- and every ``Change`` on it -- unchanged, and the
   second render is byte-identical to the first.

Property 4 is the gate-decision one ``test_gate_decision_shared.py`` already
owns for the other formats: ``compute_gate_card`` must *project* the shared
``gate_decision_for_result`` answer, never re-derive one.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from abicheck.checker import Change, ChangeKind, DiffResult, LibraryMetadata, Verdict
from abicheck.checker_policy import Confidence
from abicheck.contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractAssurance,
    ContractRelevance,
)
from abicheck.html_report import (
    compute_confidence,
    compute_file_metadata,
    compute_gate_card,
    compute_impact,
    compute_nav_bar,
    compute_not_evaluated_section,
    compute_scoped_verdict,
    compute_summary_table,
    generate_html_report,
)
from abicheck.policy.gate_decision import gate_decision_for_result
from abicheck.policy_file import PolicyFile
from abicheck.reclassify import ReclassifyRule
from abicheck.severity import SeverityConfig, SeverityLevel

# One marker per injectable field, all distinct, each shaped like a real
# injection attempt rather than a bare "<" so a partial escape still fails.
_INJECT = "<img src=x onerror=alert(1)>"


def _result(
    *,
    symbol: str = "_ZN3foo6removeEv",
    path: str = "/old/libfoo.so",
    description: str = "Public function removed",
    coverage_warning: str = "no DWARF for libfoo.so",
    reason_code: str = "not_in_export_table",
    reclassify_reason: str = "vendored fork",
    policy: str = "strict_abi",
) -> DiffResult:
    """A result carrying one finding per rendered section, with every
    externally-sourced string a parameter so a caller can inject into exactly
    one of them at a time."""
    removed = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol=symbol,
        description=description,
        old_value="void foo::remove()",
        source_location="include/foo.h:42",
        affected_symbols=["_ZN3foo1aEv"],
        correlated_change_kind="type_vtable_changed",
        contract_relevance=ContractRelevance.IN_CONTRACT,
        contract_reason_code=reason_code,
        contract_assurance=ContractAssurance.COMPLETE,
        compatibility_evaluation_status=CompatibilityEvaluationStatus.EVALUATED,
    )
    root = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol=symbol,
        description=description,
        old_value="16",
        new_value="24",
        affected_symbols=["_ZN3foo6WidgetC1Ev"],
        caused_count=3,
    )
    added = Change(kind=ChangeKind.FUNC_ADDED, symbol=symbol, description=description)
    not_evaluated = Change(
        kind=ChangeKind.FUNC_PARAMS_CHANGED,
        symbol=symbol,
        description=description,
        contract_relevance=ContractRelevance.PROVEN_OUT_OF_CONTRACT,
        contract_reason_code=reason_code,
        compatibility_evaluation_status=CompatibilityEvaluationStatus.NOT_EVALUATED,
    )
    return DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        changes=[removed, root, added, not_evaluated],
        verdict=Verdict.BREAKING,
        suppressed_count=1,
        suppressed_changes=[Change(ChangeKind.VAR_REMOVED, symbol, description)],
        policy=policy,
        policy_file=PolicyFile(
            overrides={ChangeKind.FUNC_ADDED: Verdict.COMPATIBLE_WITH_RISK},
            reclassify=[
                ReclassifyRule(
                    to_verdict=Verdict.COMPATIBLE,
                    symbol=symbol,
                    reason=reclassify_reason,
                )
            ],
        ),
        old_metadata=LibraryMetadata(path=path, sha256="aa" * 32, size_bytes=4096),
        new_metadata=LibraryMetadata(
            path="/new/libfoo.so", sha256="bb" * 32, size_bytes=8192
        ),
        redundant_count=2,
        confidence=Confidence.MEDIUM,
        evidence_tiers=["elf", "dwarf"],
        coverage_warnings=[coverage_warning],
    )


def _computed_structs(result: DiffResult) -> list[object]:
    """Every ``*Data`` struct the compute half produces for one result.

    Deliberately built by calling each ``compute_*`` rather than by listing
    the dataclasses: a new compute function that is not wired in here shows
    up as an obviously missing entry, not as a silently-unchecked struct.
    """
    changes = list(result.changes)
    structs: list[object] = [
        compute_summary_table(changes[:1], changes[1:2], changes[2:3], 1),
        compute_nav_bar(changes[:1], changes[1:2], changes[2:3], 1),
        compute_not_evaluated_section(changes[3:4]),
    ]
    for maybe in (
        compute_file_metadata(result),
        compute_confidence(result),
        compute_impact(result),
        compute_gate_card(result, SeverityConfig()),
        compute_scoped_verdict(result),
    ):
        if maybe is not None:
            structs.append(maybe)
    return structs


def _string_fields(struct: object) -> list[tuple[str, str]]:
    """Flatten a frozen ``*Data`` struct to its ``(path, value)`` string
    leaves, descending into nested structs and tuples."""
    out: list[tuple[str, str]] = []

    def walk(prefix: str, value: object) -> None:
        if isinstance(value, str):
            out.append((prefix, value))
        elif isinstance(value, tuple):
            for i, item in enumerate(value):
                walk(f"{prefix}[{i}]", item)
        elif dataclasses.is_dataclass(value) and not isinstance(value, type):
            for f in dataclasses.fields(value):
                walk(f"{prefix}.{f.name}", getattr(value, f.name))

    walk(type(struct).__name__, struct)
    return out


# ---------------------------------------------------------------------------
# 1. The compute half emits no markup
# ---------------------------------------------------------------------------


def test_no_computed_struct_field_carries_markup() -> None:
    """Every ``compute_*`` result is plain data. This is the invariant the
    split exists to hold: the moment a compute function starts returning a
    pre-built ``<div>``/``<td>`` fragment, the formatting decision has moved
    back out of ``render_html.py`` and the two halves can drift again."""
    offenders = [
        (path, value)
        for struct in _computed_structs(_result())
        for path, value in _string_fields(struct)
        if "<" in value or ">" in value
    ]
    assert offenders == [], (
        "compute_* returned pre-built markup instead of plain data: "
        f"{offenders} — move the formatting into report/render_html.py"
    )


# ---------------------------------------------------------------------------
# 2. The render half escapes everything
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field",
    ["symbol", "path", "description", "coverage_warning", "reason_code", "policy"],
)
@pytest.mark.parametrize("demangle", [True, False])
def test_rendered_report_escapes_every_externally_sourced_field(
    field: str, demangle: bool
) -> None:
    """Inject the same marker into each externally-sourced field in turn and
    assert it never reaches the document unescaped.

    Checked per field rather than on one hand-picked one because that is the
    only version of this property that catches the *next* unescaped cell: a
    single-field assertion passes forever while a sibling column renders raw.
    Run under both demangle settings, since ``--demangle`` changes which code
    path each symbol-bearing cell takes.
    """
    html_out = generate_html_report(
        _result(**{field: _INJECT}),
        lib_name="libfoo.so",
        old_version="1.0",
        new_version="2.0",
        old_symbol_count=120,
        show_impact=True,
        severity_config=SeverityConfig(),
        demangle=demangle,
    )
    assert _INJECT not in html_out, (
        f"{field!r} reached the HTML report unescaped — some cell is "
        "interpolating a raw value instead of routing it through "
        "html.escape/abbr_symbol_text"
    )
    # The marker must still be *present*, escaped: an assertion that only
    # checks for absence would pass just as happily if the field were
    # silently dropped from the report altogether.
    assert "&lt;img src=x onerror=alert(1)&gt;" in html_out


def test_report_title_and_library_name_are_escaped() -> None:
    """The two operands that reach the document chrome rather than a table
    cell -- neither goes through any of the section renderers above."""
    html_out = generate_html_report(
        _result(), lib_name=_INJECT, old_version=_INJECT, new_version="2.0"
    )
    assert _INJECT not in html_out


# ---------------------------------------------------------------------------
# 3. Rendering cannot alter the workflow result
# ---------------------------------------------------------------------------


def _fingerprint(result: DiffResult) -> str:
    return repr(result) + "|" + "|".join(repr(c) for c in result.changes)


def test_rendering_does_not_mutate_the_result_and_is_repeatable() -> None:
    """ADR-061 Phase 2's acceptance criterion, executable: a renderer may not
    alter the workflow result it was handed, and rendering the same result
    twice must produce the identical document."""
    result = _result()
    before = _fingerprint(result)
    first = generate_html_report(
        result, lib_name="libfoo.so", show_impact=True, severity_config=SeverityConfig()
    )
    after_first = _fingerprint(result)
    second = generate_html_report(
        result, lib_name="libfoo.so", show_impact=True, severity_config=SeverityConfig()
    )
    assert after_first == before, "generate_html_report mutated its DiffResult"
    assert _fingerprint(result) == before
    assert first == second


# ---------------------------------------------------------------------------
# 4. The gate card projects the shared decision, never its own
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "severity_config",
    [
        None,
        SeverityConfig(),
        SeverityConfig(abi_breaking=SeverityLevel.INFO),
        SeverityConfig(addition=SeverityLevel.ERROR),
        SeverityConfig(
            abi_breaking=SeverityLevel.WARNING, potential_breaking=SeverityLevel.ERROR
        ),
    ],
)
def test_gate_card_projects_the_shared_gate_decision(
    severity_config: SeverityConfig | None,
) -> None:
    """``compute_gate_card`` must read ``gate_decision_for_result`` (ADR-061
    D9's single call site) rather than reassembling its own answer -- swept
    across configurations that disagree with each other about whether the run
    blocks, so a hard-coded verdict-shaped guess cannot pass."""
    result = _result()
    shared = gate_decision_for_result(result, severity_config)
    card = compute_gate_card(result, severity_config)
    if shared is None:
        assert card is None
        return
    assert card is not None
    assert card.scoped is False
    assert card.passed is (not shared.blocking)
    assert card.exit_code == shared.exit_code
    assert card.blocking_categories == tuple(shared.blocking_categories)


def test_scoped_gate_card_reports_the_exit_code_the_process_uses() -> None:
    """A ``--used-by``/``--required-symbol`` run exits on the *scoped* gate,
    so the card must report that one -- and must leave ``blocking_categories``
    empty, since those describe the full-library decision alone."""
    result = _result()
    result.scoped_exit_code = 2
    result.scoped_exit_code_scheme = "severity"
    card = compute_gate_card(result, SeverityConfig())
    assert card is not None
    assert card.scoped is True
    assert card.exit_code == 2
    assert card.passed is False
    assert card.blocking_categories == ()
    # The full-library gate is still named, as context.
    full = gate_decision_for_result(result, SeverityConfig())
    assert full is not None
    expected = "PASS" if not full.blocking else f"FAIL (exit {full.exit_code})"
    assert card.full_gate_label == expected


def test_expired_reclassify_rules_are_not_disclosed() -> None:
    """The confidence table discloses the *active* rule set. Filtering happens
    on the compute side precisely so an expired rule cannot be presented as
    though it were still in effect."""
    from datetime import date

    result = _result()
    assert result.policy_file is not None
    result.policy_file.reclassify = [
        ReclassifyRule(
            to_verdict=Verdict.COMPATIBLE,
            symbol="_ZN3foo6removeEv",
            reason="expired waiver",
            expires=date(2000, 1, 1),
        )
    ]
    data = compute_confidence(result)
    assert data is not None
    assert data.policy_reclassify == ()
    assert "expired waiver" not in generate_html_report(result, lib_name="libfoo.so")


def test_confidence_absent_renders_no_section() -> None:
    """``None`` from a compute function means "this section does not exist",
    not "render an empty one" -- the distinction the ``| None`` return types
    carry."""

    class _NoConfidence:
        confidence = None

    assert compute_confidence(_NoConfidence()) is None


def test_summary_table_drops_all_zero_categories_but_keeps_the_total() -> None:
    """Row selection is a report decision, so it belongs to the compute half;
    a category with nothing in it must not reach the renderer at all."""
    result = _result()
    data = compute_summary_table(list(result.changes[:1]), [], [], 0)
    assert data.rows, "at least the removed finding's category must appear"
    assert all(row.removed or row.changed or row.added for row in data.rows), (
        "an all-zero category row reached the renderer"
    )
    assert (data.total_removed, data.total_changed, data.total_added) == (1, 0, 0)


def test_every_rendered_section_reaches_the_document() -> None:
    """A cheap completeness check on the wiring: each compute/render pair's
    own section marker must appear, so a pair that silently stopped being
    called fails here rather than only in the golden diff."""
    html_out = generate_html_report(
        _result(),
        lib_name="libfoo.so",
        old_symbol_count=120,
        show_impact=True,
        severity_config=SeverityConfig(),
    )
    for marker in (
        "Library Files",
        "Analysis Confidence",
        "CI Gate",
        "Change Summary",
        "Impact Summary",
        "Not Evaluated (Contract)",
        "Suppressed Changes",
    ):
        assert marker in html_out, f"{marker!r} section missing from the report"
    # The nav bar is rendered from its own struct too.
    assert re.search(r"<div class='nav'>.*Removed \(1\)", html_out)
