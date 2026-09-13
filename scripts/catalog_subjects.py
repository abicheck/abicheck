#!/usr/bin/env python3
"""Declarative, hand-authored "subject" classification for the catalog.

The examples-catalog-split review's own remaining gap ("What is left" item
2, `docs/contribute/plans/examples-catalog-split.md`): every navigation
dimension `catalog/taxonomy.json` already generates (rule, ecosystem,
scenario kind, operation, evidence tier, language) is a mechanical
projection of a field the taxonomy already carries -- `topics` included,
which is derived from the change-catalog's own detector-owner split
(symbols/types/platform/build/source, AGENTS.md "Adding a new ChangeKind"
step 2) and is the right partition for a detector author, not a reader. A
`NO_CHANGE` control case and a C qualification-change case both land in
`topics: [controls]`/whatever their dominant `ChangeKind`'s topic happens to
be, with no dimension that groups "cases about the same underlying
mechanism a maintainer would actually search for" -- e.g. the plan's own
worked example, grouping case74/75/76/77 as "leaked internal types" (four
different embedding mechanisms -- inheritance, by-value, vtable, template --
unified by one lesson).

This module is that missing dimension's loader/validator, reading
`catalog/catalog_subjects.yaml` -- one entry per *subject* (not per case,
unlike `catalog_classification.py`'s `scenarios`/`rules` split), each naming
its member `cases`. A subject-keyed shape is the right one here rather than
mirroring `catalog_classification.py`'s case-keyed one: a case may
legitimately belong to more than one subject (see `case181_...` in the
manifest, which is both an "internal-dependency-reachability" case and an
"export-declaration-mismatches" audit variant of the same underlying
question), so the natural authoring unit is "list every case a subject
covers", not "list every subject a case belongs to" -- the same reasoning
`catalog_rules.yaml` already applies (`rules: {slug: {...}}`, membership
computed from the other direction).

`validate_subjects()` enforces the same anti-silent-default discipline
`catalog_classification.validate_classification()` established for
`entity`/`ecosystem`: a case with no subject assignment is a hard error, not
a silent default (there is no generic "uncategorized" subject to fall back
to), and a subject naming a case that no longer exists in
`ground_truth.json['verdicts']` (removed or renamed) is dead configuration
that looks like coverage -- both directions checked, mirroring
`catalog_rule_registry.validate_registry()`'s "used but undefined" /
"defined but unused" pattern for rule slugs.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import example_catalog  # noqa: E402

from abicheck.model.yaml_strict import load_strict_yaml  # noqa: E402

SUBJECTS_PATH = example_catalog.CATALOG_DIR / "catalog_subjects.yaml"


@dataclass(frozen=True)
class Subject:
    """One subject's manifest entry."""

    slug: str
    title: str
    blurb: str
    pattern_summary: str | None
    cases: tuple[str, ...] = field(default_factory=tuple)


def load_subjects(path: Path | None = None) -> dict[str, Subject]:
    """Parse `catalog/catalog_subjects.yaml` into slug -> Subject."""
    manifest = path or SUBJECTS_PATH
    text = manifest.read_text(encoding="utf-8")
    raw = load_strict_yaml(
        text, error=lambda msg: ValueError(f"{manifest}: invalid YAML ({msg})")
    )
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{manifest}: top level must be a mapping, got {raw!r}")

    subjects_raw = raw.get("subjects") or {}
    if not isinstance(subjects_raw, dict):
        raise ValueError(
            f"{manifest}: 'subjects' must be a mapping of slug -> "
            f"{{title, blurb, cases}}, got {subjects_raw!r}"
        )

    out: dict[str, Subject] = {}
    for slug, entry in subjects_raw.items():
        entry = entry or {}
        if not isinstance(entry, dict):
            raise ValueError(
                f"{manifest}: subject {slug!r} must be a mapping with "
                f"title/blurb/cases, got {entry!r}"
            )
        title = str(entry.get("title", "")).strip()
        blurb = str(entry.get("blurb", "")).strip()
        if not title:
            raise ValueError(f"{manifest}: subject {slug!r} has no 'title'")
        if not blurb:
            raise ValueError(f"{manifest}: subject {slug!r} has no 'blurb'")
        pattern_summary_raw = entry.get("pattern_summary")
        pattern_summary = (
            str(pattern_summary_raw).strip() if pattern_summary_raw else None
        ) or None
        cases_raw = entry.get("cases") or []
        if not isinstance(cases_raw, (list, tuple)):
            raise ValueError(
                f"{manifest}: subject {slug!r}'s 'cases' must be a list of "
                f"case names, got {cases_raw!r}"
            )
        seen_cases: set[str] = set()
        cases: list[str] = []
        for case_name in cases_raw:
            case_name = str(case_name)
            if case_name in seen_cases:
                raise ValueError(
                    f"{manifest}: {case_name!r} is listed more than once under "
                    f"subject {slug!r} -- a case belongs to a subject's "
                    "'cases' list at most once"
                )
            seen_cases.add(case_name)
            cases.append(case_name)
        out[str(slug)] = Subject(
            slug=str(slug),
            title=title,
            blurb=blurb,
            pattern_summary=pattern_summary,
            cases=tuple(cases),
        )
    return out


def case_subjects(subjects: dict[str, Subject]) -> dict[str, list[str]]:
    """The reverse mapping: case name -> sorted list of subject slugs it
    belongs to. Derived, never hand-maintained, so it can't disagree with
    the manifest's own `cases` lists."""
    out: dict[str, list[str]] = {}
    for slug, subject in subjects.items():
        for case_name in subject.cases:
            out.setdefault(case_name, []).append(slug)
    for case_name in out:
        out[case_name].sort()
    return out


def validate_subjects(
    subjects: dict[str, Subject],
    case_names: Iterable[str],
) -> list[str]:
    """One message per manifest/ground-truth disagreement; [] when clean.

    Both directions are errors, mirroring
    `catalog_classification.validate_classification()`'s own reasoning for
    `entity`/`ecosystem`: a case with no subject is the silent-default gap
    this module exists to close (there is no generic "uncategorized" subject
    to fall back to), and a subject naming a case that no longer exists in
    `ground_truth.json['verdicts']` (removed or renamed) is dead
    configuration that looks like coverage.
    """
    case_names = set(case_names)
    errors: list[str] = []

    for slug, subject in sorted(subjects.items()):
        if not subject.cases:
            errors.append(
                f"subject {slug!r} in {SUBJECTS_PATH.name} has no member cases "
                "-- every subject must have at least one"
            )
        for case_name in subject.cases:
            if case_name not in case_names:
                errors.append(
                    f"subject {slug!r} in {SUBJECTS_PATH.name} names "
                    f"{case_name!r}, which is not a case in "
                    "ground_truth.json['verdicts'] -- remove the stale entry"
                )

    by_case = case_subjects(subjects)
    for case_name in sorted(case_names - set(by_case)):
        errors.append(
            f"{case_name!r} has no entry in any subject's 'cases' list in "
            f"{SUBJECTS_PATH.name} -- add it to at least one subject before "
            "it can be classified"
        )
    return errors


def main() -> int:
    subjects = load_subjects()
    gt = example_catalog.load_ground_truth()
    errors = validate_subjects(subjects, gt["verdicts"].keys())
    if errors:
        print(f"{SUBJECTS_PATH} disagrees with ground_truth.json['verdicts']:")
        for message in errors:
            print(f"  - {message}")
        return 1
    total_cases = len(case_subjects(subjects))
    print(
        f"{SUBJECTS_PATH} is valid: {len(subjects)} subjects covering "
        f"{total_cases} cases."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
