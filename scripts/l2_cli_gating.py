# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""How the full-CLI L2 harness turns receipts into a pass or a failure.

Split out of `check_l2_cli_perf.py` once that file crossed the AI-readiness
`file-size` gate's 2000-line hard cap for the second time -- a mechanical
extraction (unchanged function bodies), not a redesign, and the third piece of
the same three-way split `tests/test_l2_cli_perf_contracts.py` and
`l2_cli_validation.py` already make: what it asserts, what it measures, and how
it gates.

Everything here reads only the receipt dicts a completed run produced, never a
`Step` or a `Scenario` -- which is the seam that lets the parent import this
module rather than the reverse.

Two rules are load-bearing enough to restate where they live:

* a point is only gated when its own scenario's ``status`` is ``ok``, applied to
  head and base alike. A failed run is typically *faster* than a correct one (it
  fell back, or rendered nothing), so taking its timings would gate against a
  number no correct run produces.
* a required scenario shape that was never measured is a failure, not a quiet
  pass. "Everything I ran was green" is not the same claim as "I ran the set".

Pure stdlib plus the shared `perf_measurement` primitives.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

# The same sys.path guard the parent uses, for the same reason: this module must
# resolve its siblings whether it is loaded directly or as a `scripts.` submodule
# by a test that never imported the parent first.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if importlib.util.find_spec("perf_measurement") is None:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))
if str(_SCRIPTS_DIR) not in sys.path:  # pragma: no cover
    sys.path.insert(0, str(_SCRIPTS_DIR))

from perf_measurement import GateThreshold, is_gateable  # noqa: E402


def gated_points(scenarios: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    """``(scenario id, step name) -> median wall seconds`` for gated steps only.

    Only ``scope == "full_cli"`` steps are gated. The nested startup and
    resolution windows are recorded for diagnosis but never gated: they are
    inside the number that *is* gated, so gating them too would charge one
    slowdown twice and make a single regression look like three.
    """
    out: dict[tuple[str, str], float] = {}
    for scenario in scenarios:
        # A scenario that did not pass is not a measurement. Its timings exist in
        # the receipt (the receipt is written before the exit code is decided, on
        # purpose -- a failed run's numbers are diagnostic), but using them as a
        # baseline would gate a PR against a base run whose L2 correctness
        # validation failed: a base that fell back to binary-only evidence is
        # *faster*, so the head would be measured against a number no correct run
        # produces. Skipped on both sides for symmetry -- the same filter runs
        # over this run's own scenarios, so a failed head scenario never
        # contributes a point either.
        if scenario.get("status") != "ok":
            continue
        for step in scenario.get("steps", []):
            if step.get("gated") and is_gateable(step.get("wall_seconds")):
                out[(scenario["id"], step["name"])] = float(step["wall_seconds"])
    return out


def rejected_baseline_scenarios(scenarios: list[dict[str, Any]]) -> list[str]:
    """Scenario ids a baseline carries but that :func:`gated_points` refuses.

    Reported rather than silently dropped: "the baseline had three scenarios and
    two are usable" is information a reader needs to judge the gate's coverage,
    and the whole-run "shares no gated point" failure only fires when *every*
    one is unusable.
    """
    return [
        str(scenario.get("id"))
        for scenario in scenarios
        if scenario.get("status") != "ok"
    ]


def load_baseline(path: Path) -> tuple[dict[tuple[str, str], float], list[str]]:
    """A baseline report's gated points, plus the scenario ids it refused.

    Returns both halves so the caller can state what it is gating against: a
    baseline whose scenarios failed validation contributes no points, and saying
    so is the difference between "nothing to gate" and "gated against a broken
    base".
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios", [])
    return gated_points(scenarios), rejected_baseline_scenarios(scenarios)


def check_regressions(
    current: dict[tuple[str, str], float],
    baseline: dict[tuple[str, str], float],
    threshold: GateThreshold,
) -> list[str]:
    failures = []
    for key, value in sorted(current.items()):
        base = baseline.get(key)
        if not is_gateable(base):
            continue
        allowed = threshold.allowed_delta(base)
        if value > base + allowed:
            failures.append(
                f"{key[0]} / {key[1]}: {value:.3f}s > baseline {base:.3f}s + "
                f"{allowed:.3f}s allowed ({(value / base - 1) * 100:+.0f}%) "
                f"[tolerance={threshold.tolerance} "
                f"min_delta_seconds={threshold.min_delta} source={threshold.source}]"
            )
    return failures


def required_coverage_failures(
    scenarios: list[dict[str, Any]], required_ids: list[str]
) -> list[str]:
    """Every required scenario shape must have actually been measured.

    A run missing one of the six CLI forms is **not** a clean pass, however
    green the forms it did run look. Matched on the scenario-id prefix before
    the profile suffix, so an id carrying a different fixture profile still
    counts as covering its shape.
    """
    measured = {s["id"].split("[", 1)[0] for s in scenarios if s.get("status") == "ok"}
    return [
        f"required scenario shape {shape!r} was not measured successfully"
        for shape in required_ids
        if shape not in measured
    ]
