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

"""catalog/catalog_classification.yaml -- the declarative per-case
entity/scenario_kind/ecosystem manifest scripts/gen_catalog_taxonomy.py
consumes instead of the six hard-coded case-number sets it used to carry
(docs/contribute/plans/examples-catalog-split.md's "Making the generator's
semantic classification declarative" item). The load-bearing property this
file pins is the one the old implementation lacked: a case with no manifest
entry fails loudly instead of silently becoming a generic rule."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent

if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))
import catalog_classification  # noqa: E402
import example_catalog  # noqa: E402


def test_committed_manifest_covers_every_case_with_no_stale_entries():
    gt = example_catalog.load_ground_truth()
    classification = catalog_classification.load_classification()
    errors = catalog_classification.validate_classification(
        classification, gt["verdicts"].keys()
    )
    assert errors == []


def test_committed_manifest_has_no_enum_violations():
    """Redundant with the committed-manifest test above in the passing case,
    but pins the *shape* of a violation independently of today's data --
    load_classification/validate_classification should reject a bad
    scenario_kind/ecosystem value even if every case happens to be listed."""
    classification = catalog_classification.load_classification()
    for entry in classification.values():
        if entry.entity == "scenario":
            assert entry.scenario_kind in catalog_classification.SCENARIO_KINDS
        else:
            assert entry.entity == "rule"
            assert entry.scenario_kind is None
        assert entry.ecosystem in catalog_classification.ECOSYSTEMS


def test_an_unclassified_case_is_rejected():
    """The gap this manifest exists to close: the previous case-number-set
    implementation silently classified an unlisted case as a generic rule.
    A case load_classification() has never heard of must fail validation,
    not fall back to a default."""
    classification = catalog_classification.load_classification()
    errors = catalog_classification.validate_classification(
        classification, [*classification.keys(), "case999_not_in_manifest"]
    )
    assert any("case999_not_in_manifest" in e and "no entry" in e for e in errors)


def test_a_stale_manifest_entry_is_rejected():
    """The mirror direction: a manifest entry naming a case no longer in
    ground_truth.json (removed or renamed) is dead configuration."""
    classification = dict(catalog_classification.load_classification())
    real_cases = set(classification)
    errors = catalog_classification.validate_classification(
        classification, real_cases - {next(iter(real_cases))}
    )
    assert any("not a case" in e for e in errors)


def test_a_duplicate_scenario_key_is_rejected(tmp_path):
    """`yaml.safe_load` silently keeps only the *last* value for a repeated
    mapping key -- a hand-edited manifest with two `case01_symbol_removal:`
    entries under `scenarios` would otherwise reclassify the case with no
    warning at all. Bug class: a duplicate-key manifest edit must fail
    loudly, not resolve to whichever entry happened to parse last."""
    bad = tmp_path / "catalog_classification.yaml"
    bad.write_text(
        "scenarios:\n"
        "  case01_symbol_removal: {scenario_kind: capability, ecosystem: generic}\n"
        "  case01_symbol_removal: {scenario_kind: case-study, ecosystem: sycl}\n"
    )
    with pytest.raises(ValueError, match="duplicate key"):
        catalog_classification.load_classification(bad)


def test_a_duplicate_rule_entry_is_rejected(tmp_path):
    """The `rules:` list sibling of the case above: a plain YAML sequence
    doesn't collapse duplicates the way a mapping does, so this needs its
    own explicit check rather than falling out of the duplicate-key
    loader."""
    bad = tmp_path / "catalog_classification.yaml"
    bad.write_text(
        "rules:\n  - case02_param_type_change\n  - case02_param_type_change\n"
    )
    with pytest.raises(ValueError, match="more than once under 'rules'"):
        catalog_classification.load_classification(bad)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("scenarios: [not, a, mapping]\n", id="scenarios-is-a-list"),
        pytest.param(
            "scenarios:\n  case01_symbol_removal: [not, a, mapping]\n",
            id="scenario-entry-is-a-list",
        ),
        pytest.param("rules: {not: a-list}\n", id="rules-is-a-mapping"),
        pytest.param("- top level is a list, not a mapping\n", id="root-is-a-list"),
    ],
)
def test_a_malformed_manifest_shape_raises_a_clear_error(tmp_path, content):
    """Every one of these used to reach a bare `.get()`/`.items()` call on
    the wrong container type and crash with an unrelated AttributeError
    instead of this module's own clear ValueError -- validate the shape
    before touching it, at each container level a hand-edited YAML file
    could get wrong."""
    bad = tmp_path / "catalog_classification.yaml"
    bad.write_text(content)
    with pytest.raises(ValueError):
        catalog_classification.load_classification(bad)


def test_a_case_listed_under_both_scenarios_and_rules_is_rejected(tmp_path):
    bad = tmp_path / "catalog_classification.yaml"
    bad.write_text(
        "scenarios:\n"
        "  case01_symbol_removal: {scenario_kind: capability, ecosystem: generic}\n"
        "rules:\n"
        "  - case01_symbol_removal\n"
    )
    with pytest.raises(ValueError, match="both 'scenarios' and 'rules'"):
        catalog_classification.load_classification(bad)


def test_rule_entity_must_have_generic_ecosystem():
    """A rule case naming a real ecosystem is exactly what should make it a
    scenario case-study instead -- validate_classification catches the
    contradiction rather than silently accepting it."""
    classification = {
        "case01_symbol_removal": catalog_classification.CaseClassification(
            entity="rule", scenario_kind=None, ecosystem="onetbb"
        )
    }
    errors = catalog_classification.validate_classification(
        classification, classification.keys()
    )
    assert any("must have ecosystem" in e for e in errors)
