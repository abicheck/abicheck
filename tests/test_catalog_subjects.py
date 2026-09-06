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

"""catalog/catalog_subjects.yaml -- the hand-authored, reader-facing
"subject" classification scripts/gen_catalog_taxonomy.py consumes for the
`subjects` taxonomy field (docs/contribute/plans/examples-catalog-split.md's
"What is left" item 2). Mirrors test_catalog_classification.py's own shape
for the sibling `catalog_classification.yaml` manifest: bidirectional
validation, duplicate-key rejection, malformed-shape handling."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_DIR = Path(__file__).resolve().parent.parent

if str(REPO_DIR / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_DIR / "scripts"))
import catalog_subjects  # noqa: E402
import example_catalog  # noqa: E402


def test_committed_manifest_covers_every_case_with_no_stale_entries():
    gt = example_catalog.load_ground_truth()
    subjects = catalog_subjects.load_subjects()
    errors = catalog_subjects.validate_subjects(subjects, gt["verdicts"].keys())
    assert errors == []


def test_committed_manifest_has_at_least_one_subject_per_case():
    gt = example_catalog.load_ground_truth()
    subjects = catalog_subjects.load_subjects()
    by_case = catalog_subjects.case_subjects(subjects)
    for case_name in gt["verdicts"]:
        assert case_name in by_case, f"{case_name}: no subject assigned"
        assert by_case[case_name], f"{case_name}: empty subject list"


def test_committed_manifest_every_subject_is_a_coherent_size():
    """Not a hard contract (no code depends on the granularity), but pins
    the plan's own design intent: a subject with zero members can't happen
    (validate_subjects already rejects it), and this also documents that no
    subject has grown into an unhelpful catch-all -- see the plan's "not so
    many that most categories have 1 case, and not so few that a category
    has 40+ unrelated cases" guidance."""
    subjects = catalog_subjects.load_subjects()
    for slug, subject in subjects.items():
        assert 1 <= len(subject.cases) <= 39, (
            f"{slug}: {len(subject.cases)} cases -- outside the intended "
            "granularity band"
        )


def test_committed_manifest_has_reasonable_total_subject_count():
    """A sanity bound on the number of subjects itself -- too few and the
    dimension is useless (indistinguishable from `topics`), too many and it
    stops being reader-navigable."""
    subjects = catalog_subjects.load_subjects()
    assert 10 <= len(subjects) <= 60


def test_an_unclassified_case_is_rejected():
    """The gap this manifest exists to close (mirrors
    catalog_classification's own identical-purpose test): a case
    load_subjects() has never heard of must fail validation, not silently
    read as "no subjects" (there's no such state -- every real case must
    appear in some subject's `cases` list)."""
    subjects = catalog_subjects.load_subjects()
    by_case = catalog_subjects.case_subjects(subjects)
    errors = catalog_subjects.validate_subjects(
        subjects, [*by_case.keys(), "case999_not_in_any_subject"]
    )
    assert any("case999_not_in_any_subject" in e and "no entry" in e for e in errors)


def test_a_stale_subject_entry_is_rejected():
    """The mirror direction: a subject naming a case no longer in
    ground_truth.json (removed or renamed) is dead configuration."""
    subjects = catalog_subjects.load_subjects()
    by_case = catalog_subjects.case_subjects(subjects)
    real_cases = set(by_case)
    dropped = next(iter(real_cases))
    errors = catalog_subjects.validate_subjects(subjects, real_cases - {dropped})
    assert any("not a case" in e for e in errors)


def test_a_duplicate_subject_key_is_rejected(tmp_path):
    """`yaml.safe_load` silently keeps only the *last* value for a repeated
    mapping key -- a hand-edited manifest with two `leaked-internal-types:`
    entries would otherwise drop the first entry's `cases` with no warning.
    Bug class: a duplicate-key manifest edit must fail loudly, not resolve
    to whichever entry happened to parse last."""
    bad = tmp_path / "catalog_subjects.yaml"
    bad.write_text(
        "subjects:\n"
        "  leaked-internal-types:\n"
        "    title: A\n"
        "    blurb: b1\n"
        "    cases: [case74_detail_base_class_changed]\n"
        "  leaked-internal-types:\n"
        "    title: B\n"
        "    blurb: b2\n"
        "    cases: [case75_detail_embedded_by_value]\n"
    )
    with pytest.raises(ValueError, match="duplicate key"):
        catalog_subjects.load_subjects(bad)


def test_a_duplicate_case_within_one_subject_is_rejected(tmp_path):
    bad = tmp_path / "catalog_subjects.yaml"
    bad.write_text(
        "subjects:\n"
        "  leaked-internal-types:\n"
        "    title: A\n"
        "    blurb: b1\n"
        "    cases:\n"
        "      - case74_detail_base_class_changed\n"
        "      - case74_detail_base_class_changed\n"
    )
    with pytest.raises(ValueError, match="more than once"):
        catalog_subjects.load_subjects(bad)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param("subjects: [not, a, mapping]\n", id="subjects-is-a-list"),
        pytest.param(
            "subjects:\n  foo: [not, a, mapping]\n", id="subject-entry-is-a-list"
        ),
        pytest.param(
            "subjects:\n  foo: {title: t, blurb: b, cases: {not: a-list}}\n",
            id="cases-is-a-mapping",
        ),
        pytest.param("- top level is a list, not a mapping\n", id="root-is-a-list"),
        pytest.param(
            "subjects:\n  foo: {blurb: b, cases: [case74_detail_base_class_changed]}\n",
            id="missing-title",
        ),
        pytest.param(
            "subjects:\n  foo: {title: t, cases: [case74_detail_base_class_changed]}\n",
            id="missing-blurb",
        ),
    ],
)
def test_a_malformed_manifest_shape_raises_a_clear_error(tmp_path, content):
    """Every one of these should reach a clear ValueError from this module
    rather than an unrelated AttributeError/TypeError from a bare
    `.get()`/`.items()` call on the wrong container type."""
    bad = tmp_path / "catalog_subjects.yaml"
    bad.write_text(content)
    with pytest.raises(ValueError):
        catalog_subjects.load_subjects(bad)


def test_a_subject_with_no_cases_fails_validation():
    subjects = {
        "empty-subject": catalog_subjects.Subject(
            slug="empty-subject",
            title="Empty",
            blurb="blurb",
            pattern_summary=None,
            cases=(),
        )
    }
    errors = catalog_subjects.validate_subjects(subjects, [])
    assert any("no member cases" in e for e in errors)


def test_case_subjects_is_the_derived_reverse_mapping():
    """A case may belong to more than one subject (e.g. case181, both an
    export-declaration-mismatch audit case and an internal-dependency-
    reachability case) -- case_subjects() must surface every one, sorted."""
    subjects = catalog_subjects.load_subjects()
    by_case = catalog_subjects.case_subjects(subjects)
    dual_membership_cases = [c for c in by_case.values() if len(c) > 1]
    assert dual_membership_cases, (
        "expected at least one case with more than one subject in the "
        "committed manifest"
    )
    for slugs in by_case.values():
        assert slugs == sorted(slugs)


def test_pattern_summary_is_optional_and_only_hand_authored_where_set():
    subjects = catalog_subjects.load_subjects()
    for subject in subjects.values():
        if subject.pattern_summary is not None:
            assert isinstance(subject.pattern_summary, str)
            assert subject.pattern_summary.strip()


def test_main_exits_zero_on_the_committed_manifest():
    assert catalog_subjects.main() == 0
