# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Fact x format matrix: every incomplete-analysis fact is visible in every
output format, and no format calls such a run clean without the caveat.

Bug class ``evidence.silent_degradation_to_clean_verdict``. The extractors and
the assurance layer record failed and unknown facts in the canonical
``ReportDocument`` (``section_availability`` status ``failed``, a comparison
forced through as not comparable, a finding contract evaluation left
``NOT_EVALUATED``, an unresolved public surface, an incomplete contract
coverage). Before this test nothing checked that each output format *shows*
them: a failed ``types`` section reached JSON, the review digest and the
terminal view, but not the full Markdown/HTML headline, JUnit, ``--stat`` or
the Action summary, and the review digest headline read "safe to merge" over
all five facts.

**Domain.** Every subset of the five facts (24 runs: all 32 but the eight
holding a pair this construction cannot co-plant, see ``_EXCLUSIVE``), each
rendered into every format a pair comparison produces: JSON, SARIF, HTML,
JUnit, the review digest, the terminal view, full and root-cause Markdown,
``--stat``, and the GitHub Action comment/job summary (rendered from the
written JSON report, the way ``actions/report`` does).

**Oracle.** :func:`_document_facts`, a hand-written reading of the JSON
report's own fields, written against the report schema -- not
``report/analysis_caveats.py``, the projection the formats now share. Each
scenario also states which facts its inputs plant, and the test first
checks the document holds exactly those, so a scenario that stopped planting
its fact fails here instead of passing vacuously.
"""

from __future__ import annotations

import itertools
import json
import re
import tempfile
from collections.abc import Callable
from functools import cache
from pathlib import Path

import pytest
from defusedxml.ElementTree import fromstring as xml_fromstring

from abicheck.checker import compare
from abicheck.checker_types import DiffResult
from abicheck.comparability import IncludeDir, compute_extraction_contract
from abicheck.frontends.action.report_publication import (
    PublicationIdentity,
    render_report,
)
from abicheck.frontends.render import render_envelope, to_stat
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.section_availability import SectionAvailability, SectionStatus
from abicheck.report.document_builder import build_report_envelope
from abicheck.report.envelope import RenderOptions

FACTS = (
    "section_unavailable",
    "not_comparable",
    "contract_coverage_partial",
    "unresolved_relevance",
    "scope_unresolved",
)

_DIAG = "types extractor crashed: boom-7f3a"

#: The qualification every format must carry beside a clean-looking verdict.
_CAVEAT = "analysis incomplete"

#: Wording that, alone, claims a clean result.
_CLEAN_CLAIMS = re.compile(
    r"safe to merge|no abi changes|no changes|no abi/api change|"
    r"no compatibility impact|compatible",
    re.IGNORECASE,
)

#: Human-format tokens that identify each fact, case-insensitively, after
#: ``_`` and ``-`` fold to spaces. Taken from the inputs, not the renderers.
_HUMAN_TOKENS: dict[str, tuple[str, ...]] = {
    "section_unavailable": ("types", _DIAG),
    "not_comparable": ("not comparable",),
    "contract_coverage_partial": ("contract coverage",),
    "unresolved_relevance": ("not evaluated",),
    "scope_unresolved": ("public surface",),
}


def _fn(name: str) -> Function:
    return Function(
        name=name,
        mangled=f"_Z{len(name)}{name}v",
        return_type="int",
        visibility=Visibility.PUBLIC,
    )


@cache
def _profile_contracts() -> tuple[object, object]:
    """Two extraction contracts that differ in their include sequence -- a
    fatal profile mismatch only ``diagnostic_comparison`` forces through."""
    root = Path(tempfile.mkdtemp(prefix="abicheck-failed-facts-"))
    contracts = []
    for side, body in (("d1", "int x;"), ("d2", "int x; int y;")):
        (root / side).mkdir()
        dep = root / side / "dep.h"
        dep.write_text(f"struct Dep {{ {body} }};\n")
        contracts.append(
            compute_extraction_contract(
                l2_frontend_ran=True,
                declared_includes=[IncludeDir(root / side)],
                depfile_resolved_paths=[dep],
            )
        )
    return contracts[0], contracts[1]


def _run(facts: frozenset[str]) -> tuple[DiffResult, AbiSnapshot, AbiSnapshot]:
    """One comparison planting exactly *facts*.

    Three facts come from real ``compare`` inputs: a ``failed`` section on
    the new snapshot, a fatal profile mismatch forced through, and a removal
    under ``contract=exports`` with no export table (contract relevance
    unresolved). ``scope_resolved`` and ``contract_coverage`` are set on the
    result, the way ``tests/test_cli_comparability_gate.py`` does: building
    an unresolvable header scope or a contract-coverage gap needs headers and
    a compiler, and the report reads only the result fields either way.
    """
    old_kw: dict[str, object] = {}
    new_kw: dict[str, object] = {}
    if "section_unavailable" in facts:
        new_kw["unreadable_sections"] = (("types", _DIAG),)
        new_kw["section_availability"] = (
            SectionAvailability("types", SectionStatus.FAILED, (_DIAG,)),
        )
    if "not_comparable" in facts:
        old_kw["contract"], new_kw["contract"] = _profile_contracts()
    removal = "unresolved_relevance" in facts
    old = AbiSnapshot(
        library="libx.so.1",
        version="1",
        functions=[_fn("f"), *([_fn("g")] if removal else [])],
        **old_kw,
    )
    new = AbiSnapshot(library="libx.so.1", version="2", functions=[_fn("f")], **new_kw)
    contract_kw: dict[str, object] = (
        {"contract_evaluation": True, "contract_mode": "exports"} if removal else {}
    )
    result = compare(
        old, new, diagnostic_comparison="not_comparable" in facts, **contract_kw
    )
    if "scope_unresolved" in facts:
        result.scope_resolved = False
    if "contract_coverage_partial" in facts:
        result.contract_coverage = "partial"
    return result, old, new


def _document_facts(doc: dict[str, object]) -> set[str]:
    """The oracle: which facts a JSON report holds, read from its schema
    fields directly (``report/compare_report.schema.json``)."""
    found: set[str] = set()
    assurance = doc.get("analysis_assurance") or {}
    assert isinstance(assurance, dict)
    for entry in assurance.get("section_availability") or ():
        if entry["status"] in ("failed", "blocked", "not_collected"):
            found.add("section_unavailable")
    if assurance.get("status") == "not_comparable" or doc.get("assurance") == "none":
        found.add("not_comparable")
    if doc.get("contract_coverage") == "partial":
        found.add("contract_coverage_partial")
    audit = doc.get("disposition_audit") or {}
    assert isinstance(audit, dict)
    if audit.get("counts", {}).get("unresolved_relevance", 0) > 0:
        found.add("unresolved_relevance")
    scope = doc.get("scope") or {}
    assert isinstance(scope, dict)
    if scope.get("manual_review_required") is True:
        found.add("scope_unresolved")
    return found


def _render_all(
    result: DiffResult, old: AbiSnapshot, new: AbiSnapshot
) -> dict[str, str]:
    envelope = build_report_envelope(result, old, new, options=RenderOptions())
    out = {
        fmt: render_envelope(fmt, envelope)
        for fmt in ("json", "sarif", "html", "junit", "review", "terminal", "markdown")
    }
    out["markdown@root-cause"] = render_envelope(
        "markdown",
        build_report_envelope(
            result, old, new, options=RenderOptions(report_mode="root-cause")
        ),
    )
    out["stat"] = to_stat(result, envelope=envelope)
    out["action"] = render_report(
        json.loads(out["json"]),
        identity=PublicationIdentity(identity="t", run_id=1, run_attempt=1),
        on="always",
    ).body
    return out


#: ``contract=exports`` (how :func:`_run` plants ``unresolved_relevance``)
#: turns header scoping off (``checker.compare``), and an unscoped run has
#: no public surface to fail to resolve -- so that one pair cannot co-occur
#: in this construction. Every other subset is rendered.
_EXCLUSIVE = frozenset({"scope_unresolved", "unresolved_relevance"})

_SUBSETS = [
    frozenset(c)
    for n in range(len(FACTS) + 1)
    for c in itertools.combinations(FACTS, n)
    if not _EXCLUSIVE <= set(c)
]


@cache
def _rendered(facts: frozenset[str]) -> dict[str, str]:
    return _render_all(*_run(facts))


def _fold(text: str) -> str:
    return re.sub(r"[_\-]", " ", text).lower()


def _ids(facts: frozenset[str]) -> str:
    return "+".join(sorted(facts)) or "complete"


HUMAN_FORMATS = (
    "html",
    "review",
    "terminal",
    "markdown",
    "markdown@root-cause",
    "action",
)

#: The digest views fold an unresolved scope and a failed section into the
#: banners they already carried (the manual-review banner; the
#: "Analysis assurance PARTIAL: not comparable" note), so their wording for
#: those two is the banner's.
_BANNER_TOKENS: dict[str, dict[str, tuple[str, ...]]] = {
    "review": {
        "scope_unresolved": ("manual review required",),
        "section_unavailable": ("not comparable", _DIAG),
    },
    "terminal": {
        "scope_unresolved": ("manual review required",),
        "section_unavailable": ("not comparable", _DIAG),
    },
}


@pytest.mark.parametrize("facts", _SUBSETS, ids=_ids)
def test_scenario_plants_exactly_its_facts(facts: frozenset[str]) -> None:
    doc = json.loads(_rendered(facts)["json"])
    assert _document_facts(doc) == set(facts)


@pytest.mark.parametrize("fmt", HUMAN_FORMATS)
@pytest.mark.parametrize("facts", _SUBSETS, ids=_ids)
def test_human_format_shows_every_fact(facts: frozenset[str], fmt: str) -> None:
    text = _fold(_rendered(facts)[fmt])
    for fact in facts:
        tokens = _BANNER_TOKENS.get(fmt, {}).get(fact, _HUMAN_TOKENS[fact])
        missing = [t for t in tokens if _fold(t) not in text]
        assert not missing, f"{fmt} hides {fact}: missing {missing}"


@pytest.mark.parametrize("facts", _SUBSETS, ids=_ids)
def test_stat_line_names_every_fact(facts: frozenset[str]) -> None:
    line = _rendered(facts)["stat"]
    assert "\n" not in line.strip()
    for fact in facts:
        assert fact in line, f"--stat hides {fact}: {line!r}"


@pytest.mark.parametrize("facts", _SUBSETS, ids=_ids)
def test_sarif_carries_every_fact_machine_readably(facts: frozenset[str]) -> None:
    run = json.loads(_rendered(facts)["sarif"])["runs"][0]
    notifications = run["invocations"][0].get("toolExecutionNotifications", [])
    ids = {n["descriptor"]["id"] for n in notifications}
    # A partial contract coverage whose contract context names the failing
    # providers is already one `abicheck.coverage.<reason>` error each.
    if any(i.startswith("abicheck.coverage.") for i in ids):
        ids.add("abicheck.analysis_incomplete.contract_coverage_partial")
    assert {f"abicheck.analysis_incomplete.{f}" for f in facts} <= ids
    for n in notifications:
        if n["descriptor"]["id"].startswith("abicheck.analysis_incomplete."):
            assert n["level"] == "warning"
    stray = {
        i.removeprefix("abicheck.analysis_incomplete.")
        for i in {n["descriptor"]["id"] for n in notifications}
        if i.startswith("abicheck.analysis_incomplete.")
    } - set(facts)
    assert not stray, f"SARIF reports facts the run does not hold: {stray}"


@pytest.mark.parametrize("facts", _SUBSETS, ids=_ids)
def test_junit_reports_every_fact_as_skipped(facts: frozenset[str]) -> None:
    root = xml_fromstring(_rendered(facts)["junit"])
    skipped = {
        case.get("name")
        for suite in root.iter("testsuite")
        if (suite.get("name") or "").startswith("abicheck.analysis_incomplete.")
        for case in suite.iter("testcase")
        if case.find("skipped") is not None
    }
    assert skipped == set(facts)
    # Never a pass and never a failure: a check that did not run.
    for suite in root.iter("testsuite"):
        if (suite.get("name") or "").startswith("abicheck.analysis_incomplete."):
            assert suite.get("failures") == "0"
            assert suite.get("errors") == "0"


@pytest.mark.parametrize("fmt", (*HUMAN_FORMATS, "stat", "junit", "sarif"))
@pytest.mark.parametrize("facts", _SUBSETS[1:], ids=_ids)
def test_no_clean_wording_without_the_caveat(facts: frozenset[str], fmt: str) -> None:
    text = _rendered(facts)[fmt]
    assert "safe to merge" not in text.lower(), f"{fmt} calls an incomplete run safe"
    if _CLEAN_CLAIMS.search(text):
        assert _CAVEAT in _fold(text), (
            f"{fmt} renders clean wording for an incomplete run "
            f"({sorted(facts)}) without the caveat"
        )


@pytest.mark.parametrize("fmt", (*HUMAN_FORMATS, "stat", "junit", "sarif"))
def test_complete_run_carries_no_caveat(fmt: str) -> None:
    """The negative control: a complete run is not qualified anywhere, so the
    caveat cannot pass the tests above by being printed unconditionally."""
    text = _rendered(frozenset())[fmt]
    assert _CAVEAT not in _fold(text)
    assert "analysis_incomplete" not in text


def test_review_headline_still_reads_safe_for_a_complete_run() -> None:
    assert "safe to merge" in _rendered(frozenset())["review"]


_PROBE: dict[str, Callable[[str], bool]] = {
    "review": lambda t: "⚠️ `NO_CHANGE`" in t,
    "action": lambda t: (
        not next(line for line in t.splitlines() if line.startswith("## ")).startswith(
            "## ✅"
        )
    ),
}


@pytest.mark.parametrize("fmt", sorted(_PROBE))
@pytest.mark.parametrize("facts", _SUBSETS[1:], ids=_ids)
def test_headline_glyph_is_not_a_green_check(facts: frozenset[str], fmt: str) -> None:
    assert _PROBE[fmt](_rendered(facts)[fmt])


def _suppressed_run() -> dict[str, str]:
    from abicheck.policy.suppression.rules import Suppression, SuppressionList

    old = AbiSnapshot(library="libx.so.1", version="1", functions=[_fn("f"), _fn("g")])
    new = AbiSnapshot(library="libx.so.1", version="2", functions=[_fn("f")])
    rules = SuppressionList(
        suppressions=[Suppression(symbol="_Z1gv", reason="accepted removal")]
    )
    return _render_all(compare(old, new, suppression=rules), old, new)


@pytest.mark.parametrize("fmt", (*HUMAN_FORMATS, "stat", "junit", "sarif", "json"))
def test_suppressed_and_not_evaluated_detectors_stay_visible(fmt: str) -> None:
    """ADR-067 D3's recorded-before-disposed facts, the two visibility-only
    rows of the matrix: a suppressed removal and the detectors that did not
    run are stated by every format on an otherwise clean-reading run."""
    text = _fold(_suppressed_run()[fmt])
    assert "suppressed" in text
    assert "not evaluated" in text
