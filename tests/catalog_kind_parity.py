# SPDX-License-Identifier: Apache-2.0
"""Catalog-wide change-kind gates across evidence lanes and header frontends.

``expected_kinds_gate`` judges each lane cell against the kinds a case was
*written* to calibrate. Two classes of miss slip past it, because they are
relations *between* cells, not properties of one:

**A. Evidence monotonicity.** Adding evidence (``debug-headers`` ->
``build-source``, i.e. ``--build-info``/``--sources`` on top of DWARF+headers)
may sharpen a finding, never silently drop one. For each case and header
frontend the build-source kind set must be a superset of the debug-headers
set. A kind the richer lane legitimately stops reporting is declared either
in :data:`EVIDENCE_REFINED_KINDS` (shared, with a reason: kinds that richer
evidence refines into a more specific kind or proves irrelevant) or per case
in ``ground_truth.json``::

    "lane_kind_exceptions": [
      {"kinds": ["func_removed"], "reason": "...",
       "frontends": ["clang"]}                    # optional; default: both
    ]

(The motivating miss: build-source lost
``public_api_internal_dependency_added`` for case187/188/189/191.)

**B. Frontend parity.** castxml and clang read the same headers; for each case
and native lane their kind sets must match. A difference is explained when the
kind is already declared in ``expected_kinds_gaps`` for either frontend in that
lane (a tracked per-frontend gap), or declared per case::

    "frontend_kind_differences": [
      {"kinds": ["func_visibility_changed"], "only_in": "clang",
       "modes": ["debug-headers"],                # optional; default: both
       "reason": "...", "known_gap": "<known-gaps.md heading prefix>"}
    ]

(The motivating miss: case85 reported ``func_removed`` on castxml and
``func_visibility_changed`` on clang while only verdict parity was checked.)

A per-case declaration that no longer matches an observed difference is
**stale** and fails, so the lists can only shrink.

The comparison functions are pure (unit-tested in
``tests/test_catalog_kind_parity.py``). The gate itself consumes the
``validate_examples.py --json`` artifacts the catalog nightly already writes::

    python tests/catalog_kind_parity.py \\
        castxml:debug-headers=results/validate_examples-debug-headers.json \\
        castxml:build-source=results/validate_examples-build-source.json \\
        clang:debug-headers=results/clang-frontend-validate_examples-debug-headers.json \\
        clang:build-source=results/clang-frontend-validate_examples-build-source.json
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import expected_kinds_gate  # noqa: E402

FRONTENDS = expected_kinds_gate.FRONTENDS
#: (poorer, richer) evidence lanes compared by gate A.
LANE_PAIRS: tuple[tuple[str, str], ...] = (("debug-headers", "build-source"),)
NATIVE_LANES = expected_kinds_gate.ENFORCED_MODES
LANE_FIELD = "lane_kind_exceptions"
FRONTEND_FIELD = "frontend_kind_differences"
#: Cell statuses whose kind set is a real observation (SKIP/ERROR/N-A are not).
COMPARABLE_STATUSES = frozenset({"PASS", "FAIL", "XFAIL"})

#: Kinds richer evidence legitimately stops reporting, catalog-wide.
EVIDENCE_REFINED_KINDS: Mapping[str, str] = {}


class Cell(NamedTuple):
    status: str
    kinds: frozenset[str]


class GateResult(NamedTuple):
    """Undeclared differences and stale declarations of one comparison."""

    undeclared: tuple[str, ...]
    stale: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.undeclared and not self.stale


# -- declarations ---------------------------------------------------------------


def _objects(value: Any) -> list[Mapping[str, Any]]:
    if value is None:
        return []
    return [value] if isinstance(value, Mapping) else list(value)


def validate_entry(case: str, entry: Mapping[str, Any]) -> list[str]:
    """Schema errors of one case's two declaration fields."""
    errors: list[str] = []
    for field, required, allowed in (
        (
            LANE_FIELD,
            {"kinds", "reason"},
            {"kinds", "reason", "frontends", "known_gap"},
        ),
        (
            FRONTEND_FIELD,
            {"kinds", "only_in", "reason", "known_gap"},
            {"kinds", "only_in", "modes", "reason", "known_gap"},
        ),
    ):
        for i, obj in enumerate(_objects(entry.get(field))):
            where = f"{case}: {field}[{i}]"
            if not isinstance(obj, Mapping):
                errors.append(f"{where}: not an object")
                continue
            if missing := required - set(obj):
                errors.append(f"{where}: missing {sorted(missing)}")
            if extra := set(obj) - allowed:
                errors.append(f"{where}: unknown keys {sorted(extra)}")
            if not str(obj.get("reason", "")).strip():
                errors.append(f"{where}: empty reason")
            if not obj.get("kinds"):
                errors.append(f"{where}: empty kinds")
            for fe in list(obj.get("frontends", [])) + (
                [obj["only_in"]] if "only_in" in obj else []
            ):
                if fe not in FRONTENDS:
                    errors.append(f"{where}: unknown frontend {fe!r}")
            for mode in obj.get("modes", []):
                if mode not in NATIVE_LANES:
                    errors.append(f"{where}: unknown mode {mode!r}")
    return errors


def declared_lane_losses(entry: Mapping[str, Any], frontend: str) -> set[str]:
    return {
        kind
        for obj in _objects(entry.get(LANE_FIELD))
        if frontend in obj.get("frontends", FRONTENDS)
        for kind in obj["kinds"]
    }


def declared_frontend_differences(
    entry: Mapping[str, Any], mode: str
) -> set[tuple[str, str]]:
    """``(kind, only_in)`` pairs *entry* declares for lane *mode*."""
    return {
        (kind, obj["only_in"])
        for obj in _objects(entry.get(FRONTEND_FIELD))
        if mode in obj.get("modes", NATIVE_LANES)
        for kind in obj["kinds"]
    }


# -- the two relations -----------------------------------------------------------


def evidence_monotonicity(
    poorer: Iterable[str],
    richer: Iterable[str],
    declared: Iterable[str] = (),
    shared: Mapping[str, str] = EVIDENCE_REFINED_KINDS,
) -> GateResult:
    """Kinds *richer* lost without a declaration, and per-case declarations
    that no longer match a loss (stale). Shared refinements are never stale:
    they hold catalog-wide, not per case."""
    lost = set(poorer) - set(richer)
    declared = set(declared)
    undeclared = sorted(k for k in lost if k not in declared and k not in shared)
    stale = sorted(declared - lost)
    return GateResult(tuple(undeclared), tuple(stale))


def frontend_parity(
    castxml: Iterable[str],
    clang: Iterable[str],
    declared: Iterable[tuple[str, str]] = (),
    tracked: Iterable[str] = (),
) -> GateResult:
    """Kinds only one frontend reports that nothing declares, and declared
    ``(kind, only_in)`` pairs that no longer hold (stale). *tracked* kinds
    (an ``expected_kinds_gaps`` declaration for either frontend in this lane)
    explain a difference but are owned, and staled, by that gate."""
    cx, cl = set(castxml), set(clang)
    observed = {(k, "castxml") for k in cx - cl} | {(k, "clang") for k in cl - cx}
    declared = set(declared)
    tracked = set(tracked)
    undeclared = sorted(
        f"{k} (only {fe})"
        for k, fe in observed
        if (k, fe) not in declared and k not in tracked
    )
    stale = sorted(f"{k} (only {fe})" for k, fe in declared - observed)
    return GateResult(tuple(undeclared), tuple(stale))


def tracked_kinds(entry: Mapping[str, Any], mode: str) -> set[str]:
    return set().union(
        *(expected_kinds_gate.declared_kinds(entry, fe, mode) for fe in FRONTENDS)
    )


# -- the catalog gate --------------------------------------------------------------

Cells = Mapping[tuple[str, str], Mapping[str, Cell]]  # (frontend, mode) -> case -> cell


def load_cells(path: Path) -> dict[str, Cell]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        r["case_id"]: Cell(r["status"], frozenset(r.get("actual_kinds") or ()))
        for r in data.get("results", [])
    }


def _comparable(*cells: Cell | None) -> bool:
    return all(c is not None and c.status in COMPARABLE_STATUSES for c in cells)


def check_catalog(cells: Cells, verdicts: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Every violation of gates A and B over the loaded lane artifacts."""
    problems: list[str] = []
    for case in sorted(verdicts):
        entry = verdicts[case]
        problems.extend(validate_entry(case, entry))
        any_lane_pair = False
        for fe in FRONTENDS:
            for poor, rich in LANE_PAIRS:
                p = cells.get((fe, poor), {}).get(case)
                r = cells.get((fe, rich), {}).get(case)
                if not _comparable(p, r):
                    continue
                assert p is not None and r is not None
                any_lane_pair = True
                res = evidence_monotonicity(
                    p.kinds, r.kinds, declared_lane_losses(entry, fe)
                )
                if res.undeclared:
                    problems.append(
                        f"{case} [{fe}] {rich} lost {list(res.undeclared)} "
                        f"reported by {poor} (declare under {LANE_FIELD} with a reason)"
                    )
                if res.stale:
                    problems.append(
                        f"{case} [{fe}] stale {LANE_FIELD}: {list(res.stale)} "
                        f"no longer lost by {rich}; remove the declaration"
                    )
        any_fe_pair = False
        for mode in NATIVE_LANES:
            cx = cells.get(("castxml", mode), {}).get(case)
            cl = cells.get(("clang", mode), {}).get(case)
            if not _comparable(cx, cl):
                continue
            assert cx is not None and cl is not None
            any_fe_pair = True
            res = frontend_parity(
                cx.kinds,
                cl.kinds,
                declared_frontend_differences(entry, mode),
                tracked_kinds(entry, mode),
            )
            if res.undeclared:
                problems.append(
                    f"{case} [{mode}] castxml/clang kinds differ: {list(res.undeclared)} "
                    f"(declare under {FRONTEND_FIELD} with reason + known_gap)"
                )
            if res.stale:
                problems.append(
                    f"{case} [{mode}] stale {FRONTEND_FIELD}: {list(res.stale)}"
                )
        # A declaration on a case no lane pair could exercise is unverifiable,
        # not stale: say nothing (the case may be platform-scoped).
        del any_lane_pair, any_fe_pair
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        print(__doc__)
        return 64
    cells: dict[tuple[str, str], dict[str, Cell]] = {}
    for arg in args:
        key, _, path = arg.partition("=")
        fe, _, mode = key.partition(":")
        if fe not in FRONTENDS or mode not in NATIVE_LANES or not path:
            print(f"bad argument {arg!r}; want FRONTEND:MODE=path.json")
            return 64
        cells[(fe, mode)] = load_cells(Path(path))
    import example_catalog

    verdicts = json.loads(example_catalog.GROUND_TRUTH_PATH.read_text())["verdicts"]
    problems = check_catalog(cells, verdicts)
    for p in problems:
        print(f"  - {p}")
    print(f"catalog kind parity: {len(problems)} violation(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
