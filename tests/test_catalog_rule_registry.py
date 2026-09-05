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

"""examples/catalog_rules.yaml is the canonical compatibility-rule registry:
every rule slug the catalog's taxonomy names resolves to a definition there,
and every definition there is used by at least one case.

Bug class this guards: `registry.kind_completeness`
(tests/regressions/manifest.py) -- "every declared vocabulary entry is
accounted for by every total downstream consumer, bidirectionally". Before the
registry, a `rule_slug` and every `related_rules` entry were unvalidated
strings that `docs/contribute/catalog-coverage.md` counted as *distinct
compatibility rules*. A typo, a synonym, or an accidental rename therefore
became one more rule in that headline with nothing anywhere to notice. The
invariant is not "case108's related_rules is spelled right" -- it is "no
reachable spelling of a rule slug, anywhere in the taxonomy, can escape the
registry", so the adversarial tests below inject a corruption into *every*
case that carries a slug rather than into one hand-picked case.
"""

from __future__ import annotations

import copy
import re
import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent
if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))

import catalog_rule_registry  # noqa: E402
import example_catalog  # noqa: E402

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@pytest.fixture(scope="module")
def taxonomy() -> dict[str, dict]:
    return example_catalog.load_ground_truth()["taxonomy"]  # type: ignore[index]


@pytest.fixture(scope="module")
def registry() -> dict[str, catalog_rule_registry.RuleDefinition]:
    return catalog_rule_registry.load_registry()


def test_registry_and_taxonomy_agree(taxonomy, registry):
    assert catalog_rule_registry.validate_registry(taxonomy, registry) == []


def test_every_entry_has_a_title_and_a_definition(registry):
    missing = [
        slug
        for slug, entry in registry.items()
        if not entry.title.strip() or not entry.definition.strip()
    ]
    assert not missing, f"registry entries missing title/definition: {missing}"


def test_every_slug_is_kebab_case(registry, taxonomy):
    """Both halves, so a slug can't be well-formed in one and not the other."""
    bad = sorted(
        s
        for s in set(registry) | catalog_rule_registry.taxonomy_rule_slugs(taxonomy)
        if not SLUG_RE.match(s)
    )
    assert not bad, f"rule slugs are kebab-case by convention; got {bad}"


def test_definitions_are_sentences_and_distinct(registry):
    """A copy-pasted definition is the failure mode that would make two
    genuinely different rules read as the same one on their generated pages."""
    not_sentences = sorted(
        slug
        for slug, e in registry.items()
        if not e.definition.endswith(".") or len(e.definition.split()) < 6
    )
    assert not not_sentences, f"definitions must be a real sentence: {not_sentences}"
    seen: dict[str, str] = {}
    duplicated: list[tuple[str, str]] = []
    for slug, e in sorted(registry.items()):
        key = e.definition.casefold()
        if key in seen:
            duplicated.append((seen[key], slug))
        seen[key] = slug
    assert not duplicated, f"rules share a verbatim definition: {duplicated}"


# --------------------------------------------------------------------------
# Adversarial: the registry must reject a corrupted slug wherever it appears.
# Generated over every case that carries one, not a hand-picked example --
# a fixed-input test would only foreclose the one case it names.
# --------------------------------------------------------------------------


def _cases_with_rule_slug(taxonomy) -> list[str]:
    return sorted(c for c, e in taxonomy.items() if e.get("rule_slug"))


def _cases_with_related_rules(taxonomy) -> list[str]:
    return sorted(c for c, e in taxonomy.items() if e.get("related_rules"))


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda s: s + "-typo", id="suffix"),
        pytest.param(lambda s: s.replace("-", "_"), id="underscored-synonym"),
        pytest.param(lambda s: s.upper(), id="case-renamed"),
        pytest.param(lambda s: s[:-1], id="truncated"),
    ],
)
def test_a_corrupted_rule_slug_is_rejected_for_every_case(taxonomy, registry, corrupt):
    for case_id in _cases_with_rule_slug(taxonomy):
        mutated = copy.deepcopy(taxonomy)
        original = mutated[case_id]["rule_slug"]
        bad = corrupt(original)
        if bad == original or bad in registry:
            continue
        mutated[case_id]["rule_slug"] = bad
        errors = catalog_rule_registry.validate_registry(mutated, registry)
        assert any(repr(bad) in e and "has no entry" in e for e in errors), (
            f"corrupting {case_id}'s rule_slug to {bad!r} was not reported"
        )


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda s: s + "-typo", id="suffix"),
        pytest.param(lambda s: s.replace("-", ""), id="dehyphenated-synonym"),
        pytest.param(lambda s: "removed-" + s, id="prefix-renamed"),
    ],
)
def test_a_corrupted_related_rule_is_rejected_for_every_scenario(
    taxonomy, registry, corrupt
):
    for case_id in _cases_with_related_rules(taxonomy):
        related = list(taxonomy[case_id]["related_rules"])
        for index, original in enumerate(related):
            bad = corrupt(original)
            if bad == original or bad in registry:
                continue
            mutated = copy.deepcopy(taxonomy)
            mutated[case_id]["related_rules"][index] = bad
            errors = catalog_rule_registry.validate_registry(mutated, registry)
            assert any(repr(bad) in e and "has no entry" in e for e in errors), (
                f"corrupting {case_id}'s related_rules[{index}] to {bad!r} "
                "was not reported"
            )


def test_an_unused_registry_entry_is_rejected(taxonomy, registry):
    """The opposite drift: a rule whose last case was removed or renamed
    leaves a definition nothing points at, which would keep inflating the
    registry's own count."""
    for slug in sorted(registry):
        mutated = copy.deepcopy(taxonomy)
        for entry in mutated.values():
            if entry.get("rule_slug") == slug:
                entry["rule_slug"] = None
            entry["related_rules"] = [
                r for r in (entry.get("related_rules") or []) if r != slug
            ]
        errors = catalog_rule_registry.validate_registry(mutated, registry)
        assert any(repr(slug) in e and "no case uses it" in e for e in errors), (
            f"dropping every use of {slug!r} was not reported as an unused entry"
        )


# --------------------------------------------------------------------------
# The derived join: status/canonical/variant/duplicate must follow from the
# taxonomy, never from a hand-stated field.
# --------------------------------------------------------------------------


def test_families_partition_every_rule_case_exactly_once(taxonomy, registry):
    families = catalog_rule_registry.build_families(taxonomy, registry)
    placed: list[str] = []
    for fam in families.values():
        placed.extend(fam.rule_cases)
    expected = sorted(c for c, e in taxonomy.items() if e.get("rule_slug"))
    assert sorted(placed) == expected
    assert len(placed) == len(set(placed)), "a case landed in two families"


def test_status_follows_from_whether_a_rule_case_exists(taxonomy, registry):
    families = catalog_rule_registry.build_families(taxonomy, registry)
    for slug, fam in families.items():
        demonstrated = any(
            e.get("rule_slug") == slug and not e.get("variant_of")
            for e in taxonomy.values()
        )
        expected = (
            catalog_rule_registry.STATUS_DEMONSTRATED
            if demonstrated
            else catalog_rule_registry.STATUS_REFERENCED_ONLY
        )
        assert fam.status == expected, slug


def test_every_scenario_reference_is_recorded_on_its_family(taxonomy, registry):
    families = catalog_rule_registry.build_families(taxonomy, registry)
    for case_id, entry in taxonomy.items():
        for slug in entry.get("related_rules") or []:
            assert case_id in families[slug].scenario_cases


def test_variants_carry_their_axis_and_duplicates_do_not(taxonomy, registry):
    families = catalog_rule_registry.build_families(taxonomy, registry)
    for fam in families.values():
        for case_id, axis in fam.variant_cases:
            assert axis, f"{case_id} is a variant with no relation_axis"
        for case_id in fam.duplicate_cases:
            assert taxonomy[case_id].get("relation_axis") is None, case_id
