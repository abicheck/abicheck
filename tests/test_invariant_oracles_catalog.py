"""Apply the invariant oracles to real catalog comparison runs.

Reuses the session-scoped ``lab`` fixture and ``CASES`` subset of
:mod:`tests.test_metamorphic_invariants`, so each catalog library is built and
compared once for both modules. The oracles themselves live in
:mod:`tests.oracles`; this module only feeds them public CLI results.

Integration-marked and Linux-only, for the same reason as the metamorphic
suite: the builds need gcc/g++ and castxml, and gcc emits ELF there.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

from tests import test_metamorphic_invariants as _metamorphic
from tests.oracles.absence import absence_violations
from tests.oracles.conservation import conservation_violations
from tests.oracles.producer import producer_violations
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

#: Equivalent routes to one comparison. The reverse run is a different
#: comparison and is deliberately not a route.
EQUIVALENT_ROUTES = ("live .so pair", "dumped snapshot pair", "live old, snapshot new")


def _route(run) -> RouteOutcome:
    exit_code, verdict, kinds = run.outcome()
    return RouteOutcome(exit_code, verdict, frozenset(kinds))


@pytest.mark.parametrize("case", CASES)
def test_absence_invariant_holds_on_live_report(
    case: str, lab: _metamorphic._Lab
) -> None:
    """A: a fresh full-depth catalog report never claims compatibility on absent evidence."""
    assert absence_violations(lab.live(case).json()) == [], case


@pytest.mark.parametrize("case", CASES)
def test_route_invariant_holds_across_operand_routes(
    case: str, lab: _metamorphic._Lab
) -> None:
    """C: live, stored and mixed operand routes decide identically."""
    routes = {
        "live .so pair": _route(lab.live(case)),
        "dumped snapshot pair": _route(lab.stored(case)),
        "live old, snapshot new": _route(lab.mixed(case)),
    }
    assert route_violations(routes) == [], case


@pytest.mark.parametrize("case", CASES)
def test_conservation_invariant_holds_across_renderers(
    case: str, lab: _metamorphic._Lab
) -> None:
    """D: SARIF and oneline totals equal the JSON change population, each finding once."""
    run = lab.live(case)
    sarif = json.loads(run.files["sarif"].read_text(encoding="utf-8"))
    sarif_total = sum(len(r.get("results", [])) for r in sarif["runs"])
    text = run.files["oneline"].read_text(encoding="utf-8")
    match = re.search(r"\((\d+) total\)", text)
    assert match, f"{case}: no total in oneline output: {text.strip()}"
    problems = conservation_violations(
        run.json(),
        {"sarif": sarif_total, "oneline": int(match.group(1))},
    )
    assert problems == [], case


#: Kinds the castxml and clang backends are known to disagree on for a catalog
#: case, as ``(kind, only_in)`` pairs. Starts empty: every entry must be a
#: recorded known-gap, and the list may only shrink (see ``allowlist_growth``).
PRODUCER_ALLOWLIST: dict[str, frozenset[tuple[str, str]]] = {}


def _backend_pairs(
    lab: _metamorphic._Lab, case: str, backend: str
) -> frozenset[tuple[str, str]]:
    """``(kind, symbol)`` pairs one header backend reports for the case."""
    from abicheck.checker import compare
    from abicheck.dumper import dump

    build = lab.build(case)
    old = dump(build.v1, list(build.h1), header_backend=backend)
    new = dump(build.v2, list(build.h2), header_backend=backend)
    return frozenset((str(c.kind), str(c.symbol)) for c in compare(old, new).changes)


def _backend_kinds(lab: _metamorphic._Lab, case: str, backend: str) -> frozenset[str]:
    """Kinds one header backend reports for the case's old-to-new comparison."""
    from abicheck.checker import compare
    from abicheck.dumper import dump

    build = lab.build(case)
    old = dump(build.v1, list(build.h1), header_backend=backend)
    new = dump(build.v2, list(build.h2), header_backend=backend)
    return frozenset(str(change.kind) for change in compare(old, new).changes)


@pytest.mark.parametrize("case", CASES)
def test_producer_invariant_holds_between_castxml_and_clang(
    case: str, lab: _metamorphic._Lab
) -> None:
    """B: both header backends report the same kinds, up to the allowlist.

    The comparison must be non-vacuous: each backend has to report at least
    one change, or two empty sets would agree trivially.
    """
    if shutil.which("clang++") is None:
        pytest.skip(
            "clang++ is required: dump() defaults to the c++ compiler, which selects clang++"
        )
    castxml = _backend_kinds(lab, case, "castxml")
    clang = _backend_kinds(lab, case, "clang")
    assert castxml and clang, f"{case}: a backend reported no changes"
    assert (
        producer_violations(castxml, clang, PRODUCER_ALLOWLIST.get(case, frozenset()))
        == []
    ), case


#: The committed baseline of the producer allowlist, keyed by catalog case.
_BASELINE = json.loads(
    (Path(__file__).resolve().parent / "oracles" / "allowlist_baseline.json").read_text(
        encoding="utf-8"
    )
)["allowlist"]


def _typed_outcome(lab: _metamorphic._Lab, case: str) -> tuple[str, frozenset[str]]:
    """Verdict and kinds from the typed Python API on the case's dumped libraries.

    The typed API has no exit code, so only verdict and kinds are compared
    against the CLI route. ``surface_metrics=True`` is the resolved request
    the CLI sends for its surface findings, so the bare default is not used.
    """
    from abicheck.checker import compare
    from abicheck.dumper import dump

    build = lab.build(case)
    old = dump(build.v1, list(build.h1))
    new = dump(build.v2, list(build.h2))
    result = compare(old, new, surface_metrics=True)
    return str(result.verdict), frozenset(str(change.kind) for change in result.changes)


_TYPED_ROUTE_GAP = (
    "known gap: the typed compare() on the same dumped snapshots omits exported_not_public "
    "that the CLI reports on case09 (see docs/contribute/known-gaps.md)"
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
    cli = lab.live(case).outcome()
    _, cli_verdict, cli_kinds = cli
    typed_verdict, typed_kinds = _typed_outcome(lab, case)
    assert typed_verdict == cli_verdict, case
    assert typed_kinds == frozenset(cli_kinds), case


@pytest.mark.parametrize("case", CASES)
def test_every_catalog_change_carries_a_finding_id(
    case: str, lab: _metamorphic._Lab
) -> None:
    """D: real reports always key findings by finding_id, so the identity fallback never runs."""
    changes = lab.live(case).json()["changes"]
    missing = [c.get("kind") for c in changes if not c.get("finding_id")]
    assert missing == [], f"{case}: changes without finding_id: {missing}"


@pytest.mark.parametrize("case", CASES)
def test_disposition_audit_conserves_on_the_live_report(
    case: str, lab: _metamorphic._Lab
) -> None:
    """D: effective_total is the gating subset (schema), and the counts sum to detected_total."""
    from tests.oracles.conservation import conservation_violations

    report = lab.live(case).json()
    audit = report.get("disposition_audit") or {}
    assert audit.get("effective_total") == audit["counts"]["gating"], case
    assert conservation_violations(report, {}) == [], case


@pytest.mark.parametrize("case", CASES)
def test_junit_detected_total_equals_the_json_audit(
    case: str, lab: _metamorphic._Lab
) -> None:
    """D: the JUnit renderer's detected total is the JSON audit's detected total."""
    import xml.etree.ElementTree as ET

    run = lab.live(case)
    junit = ET.parse(run.files["junit"]).getroot()
    values = {
        p.get("name"): p.get("value")
        for p in junit.iter("property")
        if p.get("name") == "abicheck.detected_total"
    }
    expected = (run.json().get("disposition_audit") or {}).get("detected_total")
    assert values == {"abicheck.detected_total": str(expected)}, case


@pytest.mark.parametrize("case", CASES)
def test_producer_symbol_pairs_agree_between_castxml_and_clang(
    case: str, lab: _metamorphic._Lab
) -> None:
    """B: beyond kinds, the (kind, symbol) pairs must agree, with no undeclared difference."""
    from tests.oracles.producer import symbol_violations

    castxml = _backend_pairs(lab, case, "castxml")
    clang = _backend_pairs(lab, case, "clang")
    assert castxml and clang, f"{case}: a backend reported no changes"
    assert symbol_violations(castxml, clang) == [], case


def test_producer_allowlist_has_not_grown_past_its_baseline() -> None:
    """B: the allowlist may only shrink. Every entry must be in the committed baseline."""
    from tests.oracles.producer import allowlist_growth

    current = {
        (case, kind, side)
        for case, entries in PRODUCER_ALLOWLIST.items()
        for kind, side in entries
    }
    recorded = {
        (case, kind, side)
        for case, entries in _BASELINE.items()
        for kind, side in entries
    }
    assert allowlist_growth(current, recorded) == []


@pytest.mark.known_gap_canary
def test_typed_api_omits_exported_not_public_on_case09(lab: _metamorphic._Lab) -> None:
    """Canary: the typed route's current answer on case09 (no exported_not_public)."""
    _, typed_kinds = _typed_outcome(lab, "case09_cpp_vtable")
    assert "exported_not_public" not in typed_kinds
    cli_kinds = set(lab.live("case09_cpp_vtable").outcome()[2])
    assert "exported_not_public" in cli_kinds
