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

"""G37 Phase 0 — the scenario/rubric contracts, checked without a model.

Phase 0's own definition of done names an unresolvable fixture reference as one
of the three things `pr` must fail on; that is this file. The rest is the same
principle one level up: a scenario whose `status` claims more than its fixtures
support, a Category A case the catalog does not carry, or a dimension-2
uncertainty kind no scenario exercises would each leave a gate looking present
while grading nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import jsonschema
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
if str(REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO / "scripts"))
import example_catalog  # noqa: E402

EVAL = REPO / "agent-evals" / "skills"
SCHEMA_DIR = EVAL / "schema"
SCENARIOS = EVAL / "scenarios.yaml"
RUBRIC = EVAL / "rubric.yaml"
GROUND_TRUTH = example_catalog.GROUND_TRUTH_PATH
PUBLISHED_SKILLS = REPO / ".agents" / "skills"


def _load(path: Path) -> dict:
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    return yaml.safe_load(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _load(SCENARIOS)


@pytest.fixture(scope="module")
def scenarios(manifest: dict) -> list[dict]:
    return manifest["scenarios"]


@pytest.fixture(scope="module")
def ground_truth() -> dict:
    return _load(GROUND_TRUTH)


# --- the schemas themselves ------------------------------------------------


@pytest.mark.parametrize(
    "name", sorted(p.name for p in SCHEMA_DIR.glob("*.schema.json"))
)
def test_schema_is_a_valid_json_schema(name: str) -> None:
    schema = _load(SCHEMA_DIR / name)
    jsonschema.Draft202012Validator.check_schema(schema)
    assert schema["$id"].endswith(".schema.json")


def test_every_schema_the_plan_names_exists() -> None:
    """The four contracts Phase 0 ships. A missing one is a runner free to emit
    whatever shape it likes, which is what schemas exist to stop."""
    present = {p.name for p in SCHEMA_DIR.glob("*.schema.json")}
    assert {
        "scenario.schema.json",
        "claim.schema.json",
        "transcript-bundle.schema.json",
        "rubric.schema.json",
    } <= present


# --- the manifest ----------------------------------------------------------


def test_manifest_validates(manifest: dict) -> None:
    jsonschema.validate(manifest, _load(SCHEMA_DIR / "scenario.schema.json"))


def test_scenario_ids_are_unique(scenarios: list[dict]) -> None:
    ids = [s["id"] for s in scenarios]
    assert len(ids) == len(set(ids)), f"duplicate scenario ids: {sorted(ids)}"


def test_every_scenario_names_a_published_skill(scenarios: list[dict]) -> None:
    published = {
        p.name for p in PUBLISHED_SKILLS.iterdir() if (p / "SKILL.md").is_file()
    }
    for scenario in scenarios:
        assert scenario["skill"] in published, (
            f"{scenario['id']} targets {scenario['skill']!r}, which is not published"
        )


def test_category_a_cases_resolve(scenarios: list[dict], ground_truth: dict) -> None:
    """The fixture-resolution half of Phase 0's definition of done: a Category A
    scenario names a real case directory *and* a real catalog entry, because it
    derives its expected outcome from the catalog and never restates it."""
    for scenario in scenarios:
        if scenario["category"] != "A":
            continue
        case = scenario["case"]
        assert example_catalog.case_dir(case).is_dir(), (
            f"{scenario['id']}: no examples/{case}/"
        )
        assert case in ground_truth["verdicts"], (
            f"{scenario['id']}: examples/{case} has no ground_truth.json entry, so its "
            f"expected outcome resolves to nothing"
        )


def test_ready_category_b_scenarios_have_their_fixtures(scenarios: list[dict]) -> None:
    for scenario in scenarios:
        if scenario["category"] != "B" or scenario["status"] != "ready":
            continue
        fixture = REPO / scenario["fixture"]
        assert fixture.is_dir(), (
            f"{scenario['id']} is `ready` but {scenario['fixture']} does not exist — "
            f"a ready scenario is one the runner can actually run"
        )


def test_planned_scenarios_do_not_already_resolve(scenarios: list[dict]) -> None:
    """`planned` excludes a scenario from the expected-evidence set. One whose
    fixture already exists is therefore a scenario silently not being run."""
    for scenario in scenarios:
        if scenario["status"] != "planned" or scenario["category"] != "B":
            continue
        fixture = REPO / scenario["fixture"]
        assert not fixture.is_dir(), (
            f"{scenario['id']} has a fixture at {scenario['fixture']} but is still "
            f"`planned` — flip it to `ready` or it is excluded from expectations"
        )


def test_every_dimension_two_uncertainty_kind_has_a_scenario(
    scenarios: list[dict],
) -> None:
    """D4's four uncertainty kinds each gate at zero tolerance. A kind with no
    scenario is a zero-tolerance rule gating on nothing at all — the failure
    mode that motivated building Category B first rather than last."""
    schema = _load(SCHEMA_DIR / "scenario.schema.json")
    kinds = set(
        schema["$defs"]["scenario"]["properties"]["expected"]["properties"][
            "uncertainty"
        ]["enum"]
    )
    covered = {
        s.get("expected", {}).get("uncertainty")
        for s in scenarios
        if s.get("expected", {}).get("uncertainty")
    }
    assert kinds == covered, (
        f"uncertainty kinds with no scenario: {sorted(kinds - covered)}"
    )


def _scoped(scenarios: list[dict]) -> list[dict]:
    """Scenarios whose invocation scopes the run to a consumer or entrypoint."""
    return [
        s
        for s in scenarios
        if s.get("invocation", {}).get("used_by")
        or s.get("invocation", {}).get("required_symbols")
    ]


def test_consumer_scoped_scenarios_state_both_verdicts(scenarios: list[dict]) -> None:
    """In a scoped run the top-level `verdict` is the *scoped* answer and
    `full_verdict` is the library-wide one. A scenario stating only one of them
    cannot distinguish a correct scoped answer from an agent reading the report
    the wrong way round — the inversion the skill explicitly warns against."""
    scoped = _scoped(scenarios)
    if not scoped:
        # `check-abi-compatibility` still explicitly directs
        # `--used-by`/`--required-symbol` for a named consumer (its own
        # workflow, citing `shared/consumer-scoping.md`), so this should not
        # be empty in practice — see the three `check-abi-compatibility`
        # consumer scenarios in scenarios.yaml. Kept as a skip rather than a
        # hard failure only so a future edit that legitimately drops the last
        # scoped scenario degrades to "no coverage" instead of a collection
        # error; the assertion below still applies in full whenever any
        # scoped scenario exists.
        pytest.skip("no consumer-scoped scenario is currently defined")
    for scenario in scoped:
        assert "full_verdict" in scenario["expected"], (
            f"{scenario['id']} is consumer-scoped but states no full_verdict"
        )


def test_at_least_one_scoped_scenario_diverges(scenarios: list[dict]) -> None:
    """The divergent case is the whole reason consumer scoping exists. If every
    scoped scenario had `verdict == full_verdict`, an agent that always reported
    the library-wide result would pass all of them."""
    scoped = _scoped(scenarios)
    if not scoped:
        # See test_consumer_scoped_scenarios_state_both_verdicts above.
        pytest.skip("no consumer-scoped scenario is currently defined")
    diverging = [
        s for s in scoped if s["expected"]["verdict"] != s["expected"]["full_verdict"]
    ]
    assert diverging, "no scoped scenario has verdict != full_verdict"


# --- the rubric ------------------------------------------------------------


def test_rubric_validates() -> None:
    jsonschema.validate(_load(RUBRIC), _load(SCHEMA_DIR / "rubric.schema.json"))


def test_zero_tolerance_dimensions_are_exactly_two_and_six() -> None:
    """D4's safety model in one assertion. Widening this set is a cost decision
    someone should make deliberately; narrowing it is how a false green ships."""
    rubric = _load(RUBRIC)
    zero = {d["id"] for d in rubric["dimensions"] if d["gating"] == "zero_tolerance"}
    assert zero == {2, 6}


def test_judged_dimensions_never_gate_a_merge() -> None:
    """No model runs in CI (D2), so a judged dimension gating merge evidence
    would leave CI either bypassing it silently or rejecting every bundle
    produced the everyday `--no-judge` way."""
    for dimension in _load(RUBRIC)["dimensions"]:
        if dimension["grader"] == "judged":
            assert dimension["gating"] == "publication_baseline", dimension["name"]


def test_dimension_two_states_a_rule_per_uncertainty_kind() -> None:
    schema = _load(SCHEMA_DIR / "scenario.schema.json")
    kinds = set(
        schema["$defs"]["scenario"]["properties"]["expected"]["properties"][
            "uncertainty"
        ]["enum"]
    )
    dimension = next(d for d in _load(RUBRIC)["dimensions"] if d["id"] == 2)
    assert set(dimension["uncertainty_rules"]) == kinds


def test_contract_coverage_rule_keeps_the_verdict() -> None:
    """ADR-049 Phase 7 makes coverage orthogonal to compatibility: it raises a
    clean 0 to 1 and never lowers a 2/4. A rubric that made this kind withhold
    the verdict would train the exact downgrade dimension 6 fails."""
    dimension = next(d for d in _load(RUBRIC)["dimensions"] if d["id"] == 2)
    assert dimension["uncertainty_rules"]["contract_coverage_incomplete"][
        "verdict"
    ] == ("must_be_kept")
