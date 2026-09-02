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

"""examples/ground_truth.json's `taxonomy` block stays in sync with
scripts/gen_catalog_taxonomy.py -- mirrors gen_platform_matrix.py's own
test_platform_matrix.py pattern so drift fails the ordinary fast pytest
lane, not just a --check someone has to remember to run (a real gap a
review flagged on the PR that introduced this generator)."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
GROUND_TRUTH = REPO_DIR / "examples" / "ground_truth.json"


def _load_gen():
    path = REPO_DIR / "scripts" / "gen_catalog_taxonomy.py"
    spec = importlib.util.spec_from_file_location("gen_catalog_taxonomy", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_ground_truth() -> dict[str, object]:
    return json.loads(GROUND_TRUTH.read_text())


def test_taxonomy_is_in_sync_with_generator():
    gen = _load_gen()
    gt = _load_ground_truth()
    current = gt.get("taxonomy")
    expected = gen.build_taxonomy(gt)
    assert current == expected, (
        "examples/ground_truth.json's 'taxonomy' block is stale -- "
        "regenerate with `python scripts/gen_catalog_taxonomy.py`"
    )


def test_taxonomy_covers_every_verdicts_case():
    gt = _load_ground_truth()
    assert set(gt["taxonomy"]) == set(gt["verdicts"])


def test_scenario_kind_only_set_for_scenario_entities():
    """The generator's own module docstring states this contract --
    scenario_kind is set only when entity == 'scenario' -- so a violation
    is a real classification bug, not just a stale-cache drift."""
    gt = _load_ground_truth()
    for case_name, entry in gt["taxonomy"].items():
        if entry["scenario_kind"] is not None:
            assert entry["entity"] == "scenario", (
                f"{case_name}: scenario_kind={entry['scenario_kind']!r} but "
                f"entity={entry['entity']!r}"
            )


def test_variant_of_names_a_real_case():
    gt = _load_ground_truth()
    taxonomy = gt["taxonomy"]
    for case_name, entry in taxonomy.items():
        variant_of = entry["variant_of"]
        if variant_of is not None:
            assert variant_of in taxonomy, f"{case_name}: variant_of={variant_of!r}"
            assert taxonomy[variant_of]["rule_slug"] == entry["rule_slug"], (
                f"{case_name} and its canonical case {variant_of} disagree on rule_slug"
            )
            assert taxonomy[variant_of]["variant_of"] is None, (
                f"{variant_of}: a canonical case can't itself be a variant"
            )


def test_related_rules_are_non_empty_strings():
    gt = _load_ground_truth()
    for case_name, entry in gt["taxonomy"].items():
        for rule in entry["related_rules"]:
            assert isinstance(rule, str) and rule, case_name
