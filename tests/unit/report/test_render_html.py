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

Property 5 (added when ADR-061 Phase 2 item 1 closed for HTML) is the
document boundary itself: ``html_report.build_html_document`` must produce a
genuinely JSON-shaped :class:`~abicheck.report.document.ReportDocument` --
one that survives its own ``from_mapping``/``to_mapping`` round trip -- and
``report.render_html_document.render_html_document`` must be a pure,
deterministic function of that document alone, for both the native and
ABICC-compatible (``compat_html=True``) layouts.
"""

from __future__ import annotations

import ast
import dataclasses
import re
from pathlib import Path

import pytest

import abicheck.report.render_html
import abicheck.report.render_html_document
from abicheck.checker import Change, ChangeKind, DiffResult, LibraryMetadata, Verdict
from abicheck.checker_policy import Confidence
from abicheck.contract_relevance_types import (
    CompatibilityEvaluationStatus,
    ContractAssurance,
    ContractRelevance,
)
from abicheck.html_report import (
    build_html_document,
    compute_confidence,
    compute_file_metadata,
    compute_full_change_rows,
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
from abicheck.report.document import ReportDocument
from abicheck.report.render_html_document import render_html_document
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
# 0. The render half imports no decision-making module
# ---------------------------------------------------------------------------

_DECISION_MODULES = {
    "checker_policy",
    "reclassify",
    "report_classifications",
    "severity",
    "policy",
    "checker",
    "contract_gating",
    "checker_types",
}


@pytest.mark.parametrize(
    "module",
    [abicheck.report.render_html, abicheck.report.render_html_document],
    ids=["render_html", "render_html_document"],
)
def test_render_html_imports_no_decision_making_module(module: object) -> None:
    """The structural half of "a renderer decides nothing".

    Every property below checks the *output* of some function; this one checks
    what the module is even able to reach. A Codex review on the split's own
    PR found `render_changes_table` calling `report_classifications.category`
    and `checker_policy.impact_for` mid-render, and
    `render_compat_changes_table` calling `severity` -- registry lookups, so
    each is a decision the compute half owes the renderer, not a formatting
    choice. Those moved into `ChangeRow`. Asserting on rendered strings
    would not have caught it and would not catch the next one, because the
    output is identical either way; the import list is what actually changes.

    Covers both render modules: `render_html.py`'s reusable per-section
    formatters and `render_html_document.py`'s whole-document projection --
    the latter split out once the former grew past the architecture check's
    size ceiling, and the guard must follow the responsibility, not the file.
    """
    source = Path(module.__file__).read_text(encoding="utf-8")  # type: ignore[attr-defined]
    tree = ast.parse(source)
    reached: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            reached.add(node.module.split(".")[-1])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                reached.add(alias.name.split(".")[-1])
    offenders = sorted(reached & _DECISION_MODULES)
    assert offenders == [], (
        f"{module.__name__} reaches {offenders} — a renderer must consume "  # type: ignore[attr-defined]
        "already-decided facts (see ChangeRow / the compute_* half in "
        "html_report.py), not resolve them itself"
    )


def test_change_row_facts_carry_the_lookups_the_renderer_no_longer_makes() -> None:
    """The other half of the same finding: hoisting the lookups is only a fix
    if the values actually arrive. Checked against the registries directly,
    not against the renderer's own former call — the oracle must not be the
    implementation."""
    from abicheck.checker_policy import impact_for
    from abicheck.report_classifications import category, kind_str, severity

    changes = list(_result().changes)
    rows = compute_full_change_rows(changes)
    assert len(rows) == len(changes)
    for ch, row in zip(changes, rows, strict=True):
        ks = kind_str(ch)
        assert row.kind == ks
        assert row.category == category(ks)
        assert row.severity == severity(ks)
        assert row.impact == (impact_for(ch.kind) or "")


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


# ---------------------------------------------------------------------------
# 5. The document boundary: build_html_document -> ReportDocument -> render
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("compat_html", [False, True])
def test_build_html_document_is_a_genuine_report_document(compat_html: bool) -> None:
    """``build_html_document`` must return a :class:`ReportDocument` whose
    content is entirely JSON-safe scalars/lists/objects -- not merely an
    object that happens to render once. Round-tripping it through its own
    ``to_mapping``/``from_mapping`` (what every real consumer -- JSON
    serialization, a second render -- actually does) must reproduce an
    identical document, for both the native and ABICC-compatible layouts.
    """
    result = _result()
    document = build_html_document(
        result,
        lib_name="libfoo.so",
        old_version="1.0",
        new_version="2.0",
        old_symbol_count=120,
        show_impact=True,
        severity_config=SeverityConfig(),
        compat_html=compat_html,
    )
    assert isinstance(document, ReportDocument)
    mapping = document.to_mapping()
    round_tripped = ReportDocument.from_mapping(mapping)
    assert round_tripped.to_mapping() == mapping


@pytest.mark.parametrize("compat_html", [False, True])
def test_render_html_document_is_pure_and_deterministic(compat_html: bool) -> None:
    """``render_html_document`` must be a pure function of the document
    alone: rendering the same document twice -- including a document that
    has been through a ``to_mapping``/``from_mapping`` round trip, i.e. a
    fresh object carrying equal but non-identical data -- produces
    byte-identical output, and rendering must not mutate the document (a
    ``ReportDocument`` is frozen, so a mutation attempt would raise, but a
    hidden side channel -- e.g. reading global state -- could still make two
    renders disagree without ever touching the document itself)."""
    result = _result()
    document = build_html_document(
        result,
        lib_name="libfoo.so",
        show_impact=True,
        severity_config=SeverityConfig(),
        compat_html=compat_html,
    )
    first = render_html_document(document)
    second = render_html_document(document)
    assert first == second

    reconstructed = ReportDocument.from_mapping(document.to_mapping())
    assert render_html_document(reconstructed) == first


def test_generate_html_report_is_build_then_render() -> None:
    """``generate_html_report`` must not do anything ``build_html_document``
    + ``render_html_document`` don't already do -- it is a thin composition,
    not a third code path that could silently drift from the two halves."""
    result = _result()
    kwargs = dict(
        lib_name="libfoo.so",
        old_version="1.0",
        new_version="2.0",
        old_symbol_count=120,
        show_impact=True,
        severity_config=SeverityConfig(),
    )
    direct = generate_html_report(result, **kwargs)
    composed = render_html_document(build_html_document(result, **kwargs))
    assert direct == composed


def test_render_html_document_batches_demangling_standalone_on_a_cold_cache() -> None:
    """Codex review, fresh evidence: ``render_html_document`` can run
    standalone -- on a document built in an earlier process, or simply
    re-rendered after the demangle cache was never warmed in this one -- and
    must still batch every symbol into one subprocess call rather than
    paying a fresh ``c++filt`` fork per row. Reproduces the reported
    scenario directly: build the document, force the cache cold, call only
    ``render_html_document`` (never ``generate_html_report``, which would
    render in the same call as the compute-side work), and count subprocess
    invocations across several distinct symbols spread across different
    rows and sections -- not just the one field a narrower repro would
    cover.
    """
    import subprocess as _subprocess
    from unittest.mock import patch

    import abicheck.demangle as _demangle_mod

    symbols = {
        "_ZN3foo6removeEv": "foo::remove()",
        "_ZN3bar3addEv": "bar::add()",
        "_ZN3baz6changeEv": "baz::change()",
        "_ZN3qux3newEv": "qux::new()",
    }
    removed = Change(
        kind=ChangeKind.FUNC_REMOVED,
        symbol="_ZN3foo6removeEv",
        description="removed",
    )
    added = Change(
        kind=ChangeKind.FUNC_ADDED, symbol="_ZN3bar3addEv", description="added"
    )
    changed = Change(
        kind=ChangeKind.TYPE_SIZE_CHANGED,
        symbol="_ZN3baz6changeEv",
        description="changed",
        old_value="_ZN3qux3newEv",
    )
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libfoo.so",
        changes=[removed, added, changed],
        verdict=Verdict.BREAKING,
    )
    document = build_html_document(result, lib_name="libfoo.so")

    # Simulate a cold cache in a fresh process: no compute-side prewarm ever
    # ran against these symbols here.
    _demangle_mod.demangle.cache_clear()
    _demangle_mod._reset_demangle_batch_cache()

    with patch.dict("sys.modules", {"cxxfilt": None}):
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = _subprocess.CompletedProcess(
                args=["c++filt"],
                returncode=0,
                stdout="\n".join(symbols[s] for s in sorted(symbols)) + "\n",
                stderr="",
            )
            html_out = render_html_document(document)
            assert mock_run.call_count == 1, (
                "render_html_document paid a subprocess call per symbol "
                f"instead of one batched call: {mock_run.call_count} calls"
            )

    for demangled in symbols.values():
        assert demangled in html_out

    _demangle_mod.demangle.cache_clear()
    _demangle_mod._reset_demangle_batch_cache()
