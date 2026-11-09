"""Apply the invariant oracles to real catalog comparison runs.

Reuses the session-scoped ``lab`` fixture and ``CASES`` subset of
:mod:`tests.test_metamorphic_invariants`, so each catalog library is built and
compared once for both modules. The oracles themselves live in
:mod:`tests.oracles`; this module only feeds them public CLI results.
Relations the metamorphic suite already checks (operand routes, JSON vs SARIF
and oneline totals) are not repeated here.

Integration-marked and Linux-only, for the same reason as the metamorphic
suite: the builds need gcc/g++ and castxml, and gcc emits ELF there.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from functools import cache
from pathlib import Path

import pytest
from defusedxml.ElementTree import parse as defused_parse

from abicheck.checker import compare
from abicheck.dumper import dump
from tests import test_metamorphic_invariants as _metamorphic
from tests.oracles.conservation import (
    conservation_violations,
    disposition_violations,
    rendered_audit_violations,
)
from tests.oracles.producer import producer_violations, symbol_violations
from tests.oracles.routes import RouteOutcome, route_violations

CASES = _metamorphic.CASES
#: The session-scoped catalog build fixture, shared with the metamorphic suite.
lab = _metamorphic.lab

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "linux",
        reason="catalog builds are ELF on Linux only (gcc emits Mach-O/PE elsewhere)",
    ),
]

#: The producer allowlist, keyed by catalog case, and its baseline. The JSON
#: file is the one source; its growth check is in tests/test_invariant_oracles.py.
_ALLOWLIST_FILE = (
    Path(__file__).resolve().parent / "oracles" / "allowlist_baseline.json"
)
PRODUCER_ALLOWLIST: dict[str, frozenset[tuple[str, str]]] = {
    case: frozenset((kind, side) for kind, side in entries)
    for case, entries in json.loads(_ALLOWLIST_FILE.read_text(encoding="utf-8"))[
        "allowlist"
    ].items()
}

_NEEDS_CLANG = pytest.mark.skipif(
    shutil.which("clang++") is None,
    reason="clang++ is required: dump() defaults to the c++ compiler, which selects clang++",
)


# -- C: route equivalence ------------------------------------------------------
#
# The live, stored and mixed operand routes are checked by
# tests/test_metamorphic_invariants.py::test_operand_form_does_not_change_the_outcome.
# This module adds the routes that suite does not: the one-member package and
# the typed Python API.


def _package_route(lab: _metamorphic._Lab, case: str) -> RouteOutcome:
    """The case's two libraries compared as one-member package directories.

    Both sides keep the old library's file name, so the directory compare
    pairs them by name. Headers are passed exactly as the scalar run passes
    them.
    """
    build = lab.build(case)
    root = lab.workdir(case) / "package"
    old_dir, new_dir = root / "old", root / "new"
    for directory, lib in ((old_dir, build.v1), (new_dir, build.v2)):
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lib, directory / build.v1.name)
    run = lab._compare(
        case,
        "package",
        [
            str(old_dir),
            str(new_dir),
            *_metamorphic._headers("old", build.h1),
            *_metamorphic._headers("new", build.h2),
        ],
    )
    report = run.json()
    members = report["libraries"]
    assert [m["library"] for m in members] == [build.v1.name], (case, members)
    assert report["unmatched_old"] == [] and report["unmatched_new"] == [], case
    kinds = frozenset(f["kind"] for f in members[0]["findings"])
    return RouteOutcome(run.rc, report["verdict"], kinds)


@pytest.mark.parametrize("case", CASES)
def test_one_member_package_decides_like_the_scalar_route(
    case: str, lab: _metamorphic._Lab
) -> None:
    """C: a one-member package and the scalar path decide identically (one model, any cardinality)."""
    exit_code, verdict, kinds = lab.live(case).outcome()
    routes = {
        "scalar": RouteOutcome(exit_code, verdict, frozenset(kinds)),
        "one-member package": _package_route(lab, case),
    }
    assert route_violations(routes) == [], case


def _typed_outcome(lab: _metamorphic._Lab, case: str) -> tuple[str, frozenset[str]]:
    """Verdict and kinds from the typed Python API on the case's dumped libraries.

    The typed API has no exit code, so only verdict and kinds are compared
    against the CLI route. ``surface_metrics=True`` is the resolved request
    the CLI sends for its surface findings, so the bare default is not used.
    """
    build = lab.build(case)
    old = dump(build.v1, list(build.h1))
    new = dump(build.v2, list(build.h2))
    result = compare(old, new, surface_metrics=True)
    return str(result.verdict), frozenset(str(change.kind) for change in result.changes)


_TYPED_ROUTE_GAP = (
    "known gap (route divergence, wrong kind set): the typed compare() on the same "
    "dumped snapshots omits exported_not_public that the CLI reports on case09 "
    "(see docs/contribute/known-gaps.md)"
)


@pytest.mark.parametrize(
    "case",
    [
        "case01_symbol_removal",
        pytest.param(
            "case09_cpp_vtable",
            marks=pytest.mark.xfail(strict=True, reason=_TYPED_ROUTE_GAP),
        ),
        "case19_enum_member_removed",
    ],
)
def test_typed_api_route_matches_the_cli_route(
    case: str, lab: _metamorphic._Lab
) -> None:
    """C: the typed Python API decides the same verdict and kinds as the CLI."""
    _, cli_verdict, cli_kinds = lab.live(case).outcome()
    typed_verdict, typed_kinds = _typed_outcome(lab, case)
    assert typed_verdict == cli_verdict, case
    assert typed_kinds == frozenset(cli_kinds), case


@pytest.mark.known_gap_canary
def test_typed_api_omits_exported_not_public_on_case09(lab: _metamorphic._Lab) -> None:
    """Canary: the typed route's current answer on case09 (no exported_not_public)."""
    _, typed_kinds = _typed_outcome(lab, "case09_cpp_vtable")
    assert "exported_not_public" not in typed_kinds
    cli_kinds = set(lab.live("case09_cpp_vtable").outcome()[2])
    assert "exported_not_public" in cli_kinds


# -- B: producer equivalence ---------------------------------------------------


@cache
def _backend_pairs(
    lab: _metamorphic._Lab, case: str, backend: str
) -> frozenset[tuple[str, str]]:
    """``(kind, symbol)`` pairs one header backend reports for the case.

    Cached, so each backend dumps each case once for both the kind and the
    pair check; the kinds are the pairs' first elements.
    """
    build = lab.build(case)
    old = dump(build.v1, list(build.h1), header_backend=backend)
    new = dump(build.v2, list(build.h2), header_backend=backend)
    return frozenset((str(c.kind), str(c.symbol)) for c in compare(old, new).changes)


@_NEEDS_CLANG
@pytest.mark.parametrize("case", CASES)
def test_producer_invariant_holds_between_castxml_and_clang(
    case: str, lab: _metamorphic._Lab
) -> None:
    """B: both header backends report the same kinds, up to the allowlist.

    The comparison must be non-vacuous: each backend has to report at least
    one change, or two empty sets would agree trivially.
    """
    castxml = frozenset(kind for kind, _ in _backend_pairs(lab, case, "castxml"))
    clang = frozenset(kind for kind, _ in _backend_pairs(lab, case, "clang"))
    assert castxml and clang, f"{case}: a backend reported no changes"
    assert (
        producer_violations(castxml, clang, PRODUCER_ALLOWLIST.get(case, frozenset()))
        == []
    ), case


@_NEEDS_CLANG
@pytest.mark.parametrize("case", CASES)
def test_producer_symbol_pairs_agree_between_castxml_and_clang(
    case: str, lab: _metamorphic._Lab
) -> None:
    """B: beyond kinds, the (kind, symbol) pairs must agree, with no undeclared difference."""
    castxml = _backend_pairs(lab, case, "castxml")
    clang = _backend_pairs(lab, case, "clang")
    assert castxml and clang, f"{case}: a backend reported no changes"
    assert symbol_violations(castxml, clang) == [], case


# -- D: count conservation -----------------------------------------------------
#
# JSON vs SARIF and oneline totals are checked by
# tests/test_metamorphic_invariants.py (test_json_and_sarif_count_the_same_findings,
# test_oneline_total_counts_the_same_findings). This module checks the
# renderers that suite does not count, against the JSON disposition audit.


def _ints(pattern: str, text: str, what: str) -> tuple[int, ...]:
    match = re.search(pattern, text)
    assert match, f"no {what} in the rendered output"
    return tuple(int(group) for group in match.groups())


def _rendered_audit_totals(run) -> dict[str, dict[str, int]]:
    """Detected and gating totals as the Markdown, review and HTML reports print them."""
    markdown = run.files["markdown"].read_text(encoding="utf-8")
    review = run.files["review"].read_text(encoding="utf-8")
    html = re.sub(
        r"\s+",
        " ",
        re.sub(r"<[^>]+>", " ", run.files["html"].read_text(encoding="utf-8")),
    )
    table = r"\| Detected \(raw\) \| (\d+) \|\s*\n\| Effective \(gating\) \| (\d+) \|"
    md_detected, md_gating = _ints(table, markdown, "markdown audit table")
    rv_detected, rv_gating = _ints(table, review, "review audit table")
    pop_detected, pop_gating = _ints(
        r"\*\*Count populations:\*\* (\d+) detected; \d+ retained \((\d+) gating",
        review,
        "review count populations",
    )
    html_detected, html_gating = _ints(
        r"(\d+) detected · (\d+) gating", html, "html audit line"
    )
    return {
        "markdown": {"detected": md_detected, "gating": md_gating},
        "review audit table": {"detected": rv_detected, "gating": rv_gating},
        "review count populations": {"detected": pop_detected, "gating": pop_gating},
        "html": {"detected": html_detected, "gating": html_gating},
    }


@pytest.mark.parametrize("case", CASES)
def test_markdown_review_and_html_totals_equal_the_json_audit(
    case: str, lab: _metamorphic._Lab
) -> None:
    """D: every rendered detected and gating total is the JSON audit's."""
    run = lab.live(case)
    report = run.json()
    assert rendered_audit_violations(report, _rendered_audit_totals(run)) == [], case


@pytest.mark.parametrize("case", CASES)
def test_gating_total_agrees_with_the_change_list_by_disposition(
    case: str, lab: _metamorphic._Lab
) -> None:
    """D: effective_total, counts.gating and result_counts match the changes that gate."""
    report = lab.live(case).json()
    assert report["changes"], f"{case}: no changes, so the agreement is vacuous"
    assert disposition_violations(report) == [], case
    assert conservation_violations(report, {}) == [], case


@pytest.mark.parametrize("case", CASES)
def test_every_catalog_change_carries_a_finding_id(
    case: str, lab: _metamorphic._Lab
) -> None:
    """D: real reports always key findings by finding_id, so the identity fallback never runs."""
    changes = lab.live(case).json()["changes"]
    missing = [c.get("kind") for c in changes if not c.get("finding_id")]
    assert missing == [], f"{case}: changes without finding_id: {missing}"


@pytest.mark.parametrize("case", CASES)
def test_junit_detected_total_equals_the_json_audit(
    case: str, lab: _metamorphic._Lab
) -> None:
    """D: the JUnit renderer's detected total is the JSON audit's detected total."""
    run = lab.live(case)
    junit = defused_parse(run.files["junit"]).getroot()
    values = {
        p.get("name"): p.get("value")
        for p in junit.iter("property")
        if p.get("name") == "abicheck.detected_total"
    }
    expected = (run.json().get("disposition_audit") or {}).get("detected_total")
    assert values == {"abicheck.detected_total": str(expected)}, case
