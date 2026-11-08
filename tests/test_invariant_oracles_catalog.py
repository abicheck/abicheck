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
    assert set(routes) == set(EQUIVALENT_ROUTES)
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
PRODUCER_ALLOWLIST: frozenset[tuple[str, str]] = frozenset()


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
    assert producer_violations(castxml, clang, PRODUCER_ALLOWLIST) == [], case
