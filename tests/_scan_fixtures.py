# SPDX-License-Identifier: Apache-2.0
"""Shared assertion-surface helpers for the G20 scan / cross-source corpus.

ADR-035 cases assert on different *surfaces* than the classic ``v1``/``v2``
verdict: the cross-check findings + provider-agreement matrix
(:func:`crosscheck_surface`) and the D7 points-of-interest work-list
(:func:`poi_surface`). These helpers expose those surfaces from a snapshot
fixture so the catalog (``test_g20_catalog``) and the scenario suites share one
loader instead of re-deriving it each time.

Non-``test_`` module (a helper, not a suite) so the test collector ignores it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from abicheck.buildsource.crosscheck import CrosscheckConfig, run_crosschecks
from abicheck.buildsource.poi import PointsOfInterest, build_points_of_interest
from abicheck.model import AbiSnapshot
from abicheck.serialization import load_snapshot

_EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@dataclass(frozen=True)
class CrosscheckSurface:
    """The cross-check outcome a G20 audit/cross-source case asserts on."""

    kinds: frozenset[str]
    providers: dict[str, list[str]]
    coverage: dict[str, str]  # check name -> status ("present"|"skipped"|...)


def load_case_snapshot(
    case_name: str, filename: str = "snapshot.abi.json"
) -> AbiSnapshot:
    """Load a committed snapshot fixture from ``examples/<case_name>/``."""
    path = _EXAMPLES / case_name / filename
    if not path.is_file():
        raise FileNotFoundError(f"missing G20 fixture: {path}")
    return load_snapshot(path)


def crosscheck_surface(
    snapshot: AbiSnapshot, config: CrosscheckConfig | None = None
) -> CrosscheckSurface:
    """Run ``run_crosschecks`` and project it to the surface cases assert on."""
    res = run_crosschecks(snapshot, config) if config else run_crosschecks(snapshot)
    coverage = {
        row["layer"].split("crosscheck:", 1)[-1]: row["status"]
        for row in res.coverage
        if str(row.get("layer", "")).startswith("crosscheck:")
    }
    return CrosscheckSurface(
        kinds=frozenset(c.kind.value for c in res.findings),
        providers={k: list(v) for k, v in res.providers.items()},
        coverage=coverage,
    )


def poi_surface(**kwargs) -> PointsOfInterest:
    """Thin pass-through to :func:`build_points_of_interest` (the D7 work-list)."""
    return build_points_of_interest(**kwargs)
