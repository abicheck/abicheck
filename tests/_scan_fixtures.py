# SPDX-License-Identifier: Apache-2.0
"""Shared assertion-surface helpers for the G20 scan / cross-source corpus.

ADR-035 cases assert on different *surfaces* than the classic ``v1``/``v2``
verdict: the cross-check findings + provider-agreement matrix
(:func:`crosscheck_surface`). These helpers expose that surface from a
snapshot fixture so the catalog (``test_g20_catalog``) and the scenario
suites share one loader instead of re-deriving it each time.

The D7 points-of-interest work-list (formerly ``poi_surface``, a thin
pass-through to ``buildsource.poi.build_points_of_interest``) was dropped
here when ``buildsource/poi.py`` was deleted with the ``scan`` command
(ADR-068 Phase 6) -- a call-site audit found no ``compare``-pipeline caller
for it, and this helper itself had no caller left in the suite either.

Non-``test_`` module (a helper, not a suite) so the test collector ignores it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
_REPO_DIR = Path(__file__).resolve().parent.parent
if str(_REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_DIR / "scripts"))
import example_catalog  # noqa: E402

from abicheck.buildsource.cross_source_checks import (  # noqa: E402
    CrosscheckConfig,
    run_crosschecks,
)
from abicheck.model import AbiSnapshot  # noqa: E402
from abicheck.serialization import load_snapshot  # noqa: E402


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
    path = example_catalog.case_dir(case_name) / filename
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
