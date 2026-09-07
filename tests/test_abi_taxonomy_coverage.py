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

"""The `abi-taxonomy-coverage` gate and its generated report.

Bug class this guards: `registry.kind_completeness`
(tests/regressions/manifest.py) -- "every declared vocabulary entry is
accounted for by every total downstream consumer, bidirectionally". Here the
declared vocabulary is the 88 leaf mechanisms of
`docs/contribute/abi-api-failure-taxonomy.md` and the total consumer is
`docs/_meta/abi-taxonomy-coverage.json`'s Phase 3 classification: a leaf added
to the taxonomy but never classified would otherwise vanish silently from the
coverage denominator, which is exactly the "no coverage denominator" failure
the plan behind this matrix exists to close.

The adversarial tests below therefore corrupt *every* leaf in turn (drop it,
blank its status, contradict its own columns) rather than one hand-picked
leaf, so the invariant tested is "no reachable leaf can escape
classification", not "this one leaf is spelled right".
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))

import abi_taxonomy_coverage as atc  # noqa: E402
import gen_abi_taxonomy_coverage as gen  # noqa: E402


@pytest.fixture(scope="module")
def leaves() -> list[atc.Leaf]:
    return atc.parse_taxonomy()


@pytest.fixture(scope="module")
def mapping() -> dict[str, atc.Mapping]:
    return atc.load_mapping()


def test_committed_mapping_is_clean(leaves, mapping):
    assert atc.validate(leaves, mapping) == []


def test_taxonomy_parses_to_unique_dotted_ids(leaves):
    ids = [leaf.leaf_id for leaf in leaves]
    assert len(ids) == len(set(ids))
    assert ids, "the Phase 1 taxonomy parsed to zero leaves"
    for leaf in leaves:
        branch, _, rest = leaf.leaf_id.partition(".")
        assert branch == leaf.branch.slug
        assert rest, f"{leaf.leaf_id} has an empty leaf slug"
        assert leaf.mechanism.strip()
        assert leaf.description.strip()
        assert leaf.platforms.strip()


def test_every_leaf_is_classified(leaves, mapping):
    assert set(mapping) == {leaf.leaf_id for leaf in leaves}
    assert all(m.status in atc.STATUS_ORDER for m in mapping.values())


def test_status_counts_partition_the_taxonomy(leaves, mapping):
    counts = atc.status_counts(mapping)
    assert sum(counts.values()) == len(leaves)


@pytest.mark.parametrize("index", range(88))
def test_dropping_any_single_leaf_is_an_error(leaves, mapping, index):
    """Every leaf, not one sampled leaf, must be reachable by the gate."""
    if index >= len(leaves):
        pytest.skip("taxonomy has fewer leaves than the pinned parametrization")
    victim = leaves[index].leaf_id
    broken = {k: v for k, v in mapping.items() if k != victim}
    errors = atc.validate(leaves, broken)
    assert any(victim in e and "no entry" in e for e in errors)


@pytest.mark.parametrize("bad_status", ["", "covered", "UNKNOWN", "MISSING"])
def test_a_status_outside_the_vocabulary_is_an_error(leaves, mapping, bad_status):
    for leaf in leaves:
        broken = copy.deepcopy(mapping)
        broken[leaf.leaf_id].status = bad_status
        errors = atc.validate(leaves, broken)
        assert any(leaf.leaf_id in e for e in errors)


def test_a_status_contradicting_its_own_columns_is_an_error(leaves, mapping):
    """A hand-written status must agree with the plan's first-match-wins order.

    Exercised against every leaf, in both directions: a `COVERED` leaf whose
    cases were removed, and a `MISSING_CASE` leaf that has one.
    """
    for leaf in leaves:
        broken = copy.deepcopy(mapping)
        entry = broken[leaf.leaf_id]
        entry.status = "COVERED"
        entry.reason = ""
        entry.catalog_cases = []
        errors = atc.validate(leaves, broken)
        assert any(leaf.leaf_id in e and "first-match-wins" in e for e in errors), (
            f"{leaf.leaf_id}: a case-less COVERED leaf was accepted"
        )

        broken = copy.deepcopy(mapping)
        entry = broken[leaf.leaf_id]
        entry.status = "MISSING_CASE"
        entry.reason = "synthetic"
        entry.detector_kinds = ["func_removed"]
        entry.catalog_cases = ["case01_symbol_removal"]
        entry.cases_demonstrate = True
        entry.learn_pages = ["learn/limitations.md"]
        errors = atc.validate(leaves, broken)
        assert any(leaf.leaf_id in e and "first-match-wins" in e for e in errors), (
            f"{leaf.leaf_id}: a MISSING_CASE leaf that has a case was accepted"
        )


def test_a_dangling_reference_is_an_error(leaves, mapping):
    for field, value, needle in (
        ("learn_pages", "learn/does-not-exist.md", "missing learn page"),
        ("catalog_cases", "case999_nope", "missing catalog case"),
        ("detector_kinds", "not_a_change_kind", "which no abicheck"),
        ("topics", "not-a-topic", "absent from"),
    ):
        for leaf in leaves:
            broken = copy.deepcopy(mapping)
            setattr(broken[leaf.leaf_id], field, [value])
            # Keep the status consistent so the contradiction check does not
            # mask the reference check we are actually exercising.
            broken[leaf.leaf_id].status = atc.expected_status(broken[leaf.leaf_id])
            broken[leaf.leaf_id].reason = "synthetic"
            errors = atc.validate(leaves, broken)
            assert any(leaf.leaf_id in e and needle in e for e in errors)


def test_missing_crossreference_is_an_error(leaves, mapping):
    for leaf_id, m in mapping.items():
        if m.status not in ("KNOWN_UNDETECTABLE", "NOT_IMPLEMENTED"):
            continue
        broken = copy.deepcopy(mapping)
        broken[leaf_id].limitations_ref = ""
        broken[leaf_id].known_gaps_ref = ""
        errors = atc.validate(leaves, broken)
        assert any(leaf_id in e and "_ref" in e for e in errors)

        broken = copy.deepcopy(mapping)
        if m.status == "KNOWN_UNDETECTABLE":
            broken[leaf_id].limitations_ref = "a heading no page carries"
        else:
            broken[leaf_id].known_gaps_ref = "a heading no page carries"
        errors = atc.validate(leaves, broken)
        assert any(leaf_id in e and "does not appear in" in e for e in errors)


def test_derived_facts_come_from_their_own_sources(mapping):
    """Module/tier are derived, never stored -- so every mapped kind resolves."""
    modules = atc.kind_modules()
    tiers = atc.kind_tiers()
    assert modules, "no ChangeKind entries parsed from the change catalog"
    for m in mapping.values():
        for kind in m.detector_kinds:
            assert modules[kind] in {
                "symbols",
                "types",
                "platform",
                "build",
                "source",
            }
            assert tiers.get(kind) in {None, *["L0", "L1", "L2", "L3", "L4", "L5"]}


def test_generated_report_is_committed_and_idempotent():
    rendered = gen.render()
    assert gen.OUT_PATH.read_text(encoding="utf-8") == rendered, (
        "docs/contribute/abi-taxonomy-coverage.md is stale -- run "
        "`python scripts/gen_abi_taxonomy_coverage.py`"
    )
    assert gen.render() == rendered
    assert "GENERATED by scripts/gen_abi_taxonomy_coverage.py" in rendered
    counts = atc.status_counts(atc.load_mapping())
    assert f"{counts['COVERED']} of {len(atc.parse_taxonomy())}" in rendered
