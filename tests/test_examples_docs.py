"""Validate that examples docs are up to date and per-case READMEs are well-formed.

Three guarantees enforced here:

1. `scripts/gen_examples_docs.py --check` succeeds, so the rendered docs site
   tree under `docs/reference/examples/` is in sync with `examples/`.
2. Every case listed in `ground_truth.json` has a `README.md` whose first line
   is an `# H1`, plus at least three `## H2` sections — enough structure to
   render usefully on the docs site.
3. The set of cases on disk under `examples/case*/` matches the set listed in
   `ground_truth.json` (no orphaned dirs, no missing entries).
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Phase 3 resolver (scripts/CLAUDE.md, docs/contribute/plans/examples-catalog-split.md).
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
import example_catalog  # noqa: E402

EXAMPLES_DIR = example_catalog.EXAMPLES_DIR
GROUND_TRUTH = example_catalog.GROUND_TRUTH_PATH
GEN_SCRIPT = ROOT / "scripts" / "gen_examples_docs.py"


def _load_generator_module():
    spec = importlib.util.spec_from_file_location("gen_examples_docs", GEN_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules.pop("gen_examples_docs", None)
    sys.modules["gen_examples_docs"] = module
    spec.loader.exec_module(module)
    return module


def _ground_truth_cases() -> list[str]:
    data = json.loads(GROUND_TRUTH.read_text(encoding="utf-8"))
    return sorted(data["verdicts"].keys())


def _example_dirs() -> list[str]:
    return sorted(
        p.name
        for p in EXAMPLES_DIR.iterdir()
        if p.is_dir() and p.name.startswith("case")
    )


def test_ground_truth_matches_example_dirs() -> None:
    assert _example_dirs() == _ground_truth_cases(), (
        "Mismatch between examples/case*/ directories and ground_truth.json — "
        "every case directory must have a ground_truth.json entry and vice versa."
    )


@pytest.mark.parametrize("case_name", _ground_truth_cases())
def test_case_readme_has_required_structure(case_name: str) -> None:
    readme = example_catalog.case_dir(case_name) / "README.md"
    assert readme.exists(), f"missing README: {readme}"
    text = readme.read_text(encoding="utf-8")

    first_line = text.lstrip().splitlines()[0] if text.strip() else ""
    assert re.match(r"^#\s+\S", first_line), (
        f"{case_name}/README.md: first line must be an H1 (`# Title`), got: {first_line!r}"
    )

    h2_count = len(re.findall(r"^##\s+\S", text, re.M))
    assert h2_count >= 3, (
        f"{case_name}/README.md: needs at least 3 H2 sections to render usefully, "
        f"found {h2_count}"
    )


def test_generator_check_passes() -> None:
    """Running gen_examples_docs.py --check must succeed, i.e. docs/reference/examples/ is in sync."""
    result = subprocess.run(
        [sys.executable, str(GEN_SCRIPT), "--check"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, (
        "docs/reference/examples/ is out of date — run `python scripts/gen_examples_docs.py`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_generator_rewrites_source_links_without_mkdocs_broken_links() -> None:
    mod = _load_generator_module()

    rewritten = mod._rewrite_links(
        "[v1 header](v1.h) [guide](../docs/learn/abi-api-handling.md)"
    )

    assert "`v1 header`" in rewritten
    # Generated case pages live at docs/reference/examples/<case>.md -- two
    # levels below docs/ root -- so a docs/-relative target outside
    # reference/ needs two "../" segments, not the historical one.
    assert "[guide](../../learn/abi-api-handling.md)" in rewritten
    assert "../../examples/" not in rewritten


def _make_case(mod, **overrides):
    defaults = dict(
        name="case900_demo",
        title="Case 900: Demo",
        verdict="BREAKING",
        category="breaking",
        platforms=["linux"],
        abi_break=True,
        api_break=False,
        bad_practice=False,
        expected_kinds=[],
        body="",
    )
    defaults.update(overrides)
    return mod.Case(**defaults)


def test_meta_table_shows_rule_family_relation() -> None:
    """A case page's meta table surfaces the taxonomy's rule_slug/
    relation_type/relation_axis -- the review finding this generator now
    addresses: the taxonomy was invisible on the public docs site even
    though ground_truth.json already carried it."""
    mod = _load_generator_module()
    case = _make_case(
        mod,
        rule_slug="exported-function-removed",
        variant_of="case01_symbol_removal",
        relation_type="duplicate",
    )
    table = mod._meta_table(case)
    assert (
        "[`exported-function-removed`](by-rule/exported-function-removed.md)" in table
    )
    assert "Duplicate of [case01_symbol_removal](case01_symbol_removal.md)" in table

    variant_case = _make_case(
        mod,
        rule_slug="enum-member-value-changed",
        variant_of="case08_enum_value_change",
        relation_type="variant",
        relation_axis="public-surface",
    )
    variant_table = mod._meta_table(variant_case)
    assert "Variant (public-surface) of [case08_enum_value_change]" in variant_table


def test_meta_table_shows_scenario_classification_and_ecosystem() -> None:
    mod = _load_generator_module()
    case = _make_case(
        mod,
        entity="scenario",
        scenario_kind="case-study",
        ecosystem="onetbb",
        related_rules=["exported-function-removed"],
    )
    table = mod._meta_table(case)
    assert "Scenario — Ecosystem case study" in table
    assert "[oneTBB](by-ecosystem/onetbb.md)" in table
    assert (
        "[`exported-function-removed`](by-rule/exported-function-removed.md)" in table
    )


def test_build_rule_families_groups_canonical_duplicate_variant_and_scenarios() -> None:
    mod = _load_generator_module()
    canonical = _make_case(
        mod, name="case01_symbol_removal", rule_slug="exported-function-removed"
    )
    duplicate = _make_case(
        mod,
        name="case12_function_removed",
        rule_slug="exported-function-removed",
        variant_of="case01_symbol_removal",
        relation_type="duplicate",
    )
    scenario = _make_case(
        mod,
        name="case108_task_class_removed",
        entity="scenario",
        scenario_kind="case-study",
        related_rules=["exported-function-removed"],
    )
    families = mod._build_rule_families([canonical, duplicate, scenario])
    fam = families["exported-function-removed"]
    assert fam.canonical is canonical
    assert fam.duplicates == [duplicate]
    assert fam.variants == []
    assert fam.scenarios == [scenario]


def test_build_rule_families_includes_bundle_scenarios() -> None:
    """A multi-library bundle case (ADR-023) composing a rule via its own
    taxonomy `related_rules` must still show up on that rule's family page,
    even though bundle cases are excluded from `_load_cases()`/the `cases`
    list entirely (different ground-truth shape, no generated case page) --
    a Codex review found the original cut silently dropped this
    relationship, disagreeing with both ground_truth.json and the coverage
    report. Real ground_truth.json data: case90/92/93 all compose
    exported-function-removed via a multi-library bundle scenario."""
    mod = _load_generator_module()
    families = mod._build_rule_families([])
    fam = families["exported-function-removed"]
    bundle_names = {name for name, _title in fam.bundle_scenarios}
    assert "case90_bundle_intra_dep_removed" in bundle_names
    assert "case92_bundle_provider_changed" in bundle_names
    assert "case93_bundle_manifest_drift" in bundle_names
    # Never rendered as a dangling link -- bundle cases have no case page.
    page = mod._render_rule_family_page(fam)
    assert "](../case90_bundle_intra_dep_removed.md)" not in page
    assert "`case90_bundle_intra_dep_removed`" in page


def test_ecosystem_view_includes_bundle_cases() -> None:
    """The ecosystem index/pages must count multi-library bundle cases too
    (all five are ecosystem: generic in the real taxonomy), even though
    `_load_cases()` excludes them entirely -- a Codex review found the
    'generic' ecosystem page reporting 179 cases while
    catalog-coverage.md (which reads the taxonomy directly) reports 184,
    the same class of bug the by-rule bundle-scenario fix above closed."""
    mod = _load_generator_module()
    bundle_by_eco = mod._bundle_cases_by_ecosystem()
    generic_bundles = {name for name, _title in bundle_by_eco["generic"]}
    assert generic_bundles == {
        "case84_bundle_soname_skew",
        "case90_bundle_intra_dep_removed",
        "case91_bundle_intra_signature_drift",
        "case92_bundle_provider_changed",
        "case93_bundle_manifest_drift",
    }
    rendered = mod._render_group_index(
        title="Generic cases",
        blurb="Cases modeling the Generic ecosystem.",
        cases=[],
        extra_unlinked=bundle_by_eco["generic"],
    )
    assert "_5 case(s)._" in rendered
    assert "](../case84_bundle_soname_skew.md)" not in rendered
    assert "`case84_bundle_soname_skew` (bundle)" in rendered


def test_by_rule_index_lists_every_family_including_scenario_only_slugs() -> None:
    """A rule_slug named only in a scenario's related_rules (no rule-entity
    case demonstrates it alone yet) still gets a family entry, so every
    linked rule page actually exists -- a dangling by-rule link would fail
    mkdocs --strict."""
    mod = _load_generator_module()
    scenario = _make_case(
        mod,
        name="case192_demo",
        entity="scenario",
        related_rules=["scenario-only-rule"],
    )
    families = mod._build_rule_families([scenario])
    assert "scenario-only-rule" in families
    fam = families["scenario-only-rule"]
    assert fam.canonical is None
    assert fam.scenarios == [scenario]
    index = mod._render_by_rule_index(families)
    assert "[`scenario-only-rule`](scenario-only-rule.md)" in index
    page = mod._render_rule_family_page(fam)
    assert "No single-library case demonstrates this rule alone yet" in page


def test_generator_source_section_uses_code_literals() -> None:
    mod = _load_generator_module()
    case = mod.Case(
        name="case01_symbol_removal",
        title="Case 01: Symbol Removal",
        verdict="BREAKING",
        category="breaking",
        platforms=["linux"],
        abi_break=True,
        api_break=False,
        bad_practice=False,
        expected_kinds=[],
        body="",
    )

    source_section = mod._source_links(case)

    assert "- `v1.c`" in source_section
    assert "](" not in source_section


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # Leading caseNN prefix (various separators) is stripped for table rows.
        (
            "case101 — inline namespace version bumped (BREAKING)",
            "inline namespace version bumped (BREAKING)",
        ),
        (
            "Case 17 — Template Instantiation ABI Change",
            "Template Instantiation ABI Change",
        ),
        (
            "Case 26b — Union Field Added (No Size Change)",
            "Union Field Added (No Size Change)",
        ),
        ("Case 01: Symbol Removal", "Symbol Removal"),
        # Regression guard: a title with internal colons keeps the trailing text
        # (the old `split(':', 1)[-1]` heuristic mangled these).
        (
            "case98 — C++ standard floor raised (build-context risk)",
            "C++ standard floor raised (build-context risk)",
        ),
        (
            "case100 — experimental:: removed without replacement (API break)",
            "experimental:: removed without replacement (API break)",
        ),
        # Titles without a caseNN prefix are left untouched, even if they start
        # with the word "case" (no trailing number) or contain a colon.
        ("Symbol Removal", "Symbol Removal"),
        ("Case-insensitive lookup changed", "Case-insensitive lookup changed"),
    ],
)
def test_short_title_strips_case_prefix_but_keeps_inner_colons(
    title: str, expected: str
) -> None:
    mod = _load_generator_module()
    assert mod._short_title(title) == expected
