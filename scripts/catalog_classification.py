#!/usr/bin/env python3
"""Declarative per-case semantic classification for `gen_catalog_taxonomy.py`.

Before this module existed, `gen_catalog_taxonomy.py` classified every
scenario (bundle, capability, ecosystem case-study) via six hard-coded
case-*number* sets (`BUNDLE_SCENARIOS`, `CAPABILITY_SCENARIOS`, and one set
per ecosystem), with every case not in one of those sets silently defaulting
to `entity: rule, ecosystem: generic`. That silent default is exactly the gap
the "Making the generator's semantic classification declarative" item in
`docs/contribute/plans/examples-catalog-split.md`'s "What is left" section
names: a new oneTBB/SYCL/oneMKL/Linux-kernel case study, bundle, or
capability scenario is misclassified as a generic rule unless someone
remembers to edit the generator's source.

This module replaces those six sets with one declarative manifest,
`catalog/catalog_classification.yaml`, and makes the previously-silent
default a hard error instead: every case in `ground_truth.json["verdicts"]`
must have exactly one entry in the manifest, under either `scenarios` (an
explicit `scenario_kind`/`ecosystem` pair) or `rules` (a plain list — every
rule-entity case's ecosystem is always `"generic"`, since a rule case naming
a real ecosystem is exactly what makes it a scenario case-study instead).
`validate_classification()` checks both directions, the same "used but
undefined" / "defined but unused" pattern `catalog_rule_registry.py`'s
`validate_registry()` already establishes for rule slugs -- a case with no
entry fails loudly, and a stale entry naming a removed/renamed case fails
too, rather than silently accumulating dead configuration.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import example_catalog  # noqa: E402

from abicheck.model.yaml_strict import load_strict_yaml  # noqa: E402

CLASSIFICATION_PATH = example_catalog.CATALOG_DIR / "catalog_classification.yaml"


#: Mirrors gen_catalog_taxonomy.py's own docstring enumeration of the values
#: `scenario_kind`/`ecosystem` may take.
SCENARIO_KINDS = {"case-study", "project-topology", "capability"}
ECOSYSTEMS = {"generic", "onetbb", "sycl", "onemkl", "linux-kernel"}


@dataclass(frozen=True)
class CaseClassification:
    """One case's manifest entry, resolved to the shape the taxonomy needs."""

    entity: str  # "rule" | "scenario"
    scenario_kind: str | None  # set only for entity == "scenario"
    ecosystem: str


def load_classification(
    path: Path | None = None,
) -> dict[str, CaseClassification]:
    """Parse `catalog/catalog_classification.yaml` into case_name -> entry."""
    manifest = path or CLASSIFICATION_PATH
    text = manifest.read_text(encoding="utf-8")
    raw = load_strict_yaml(
        text, error=lambda msg: ValueError(f"{manifest}: invalid YAML ({msg})")
    )
    raw = raw or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{manifest}: top level must be a mapping, got {raw!r}")

    scenarios = raw.get("scenarios") or {}
    if not isinstance(scenarios, dict):
        raise ValueError(
            f"{manifest}: 'scenarios' must be a mapping of case name -> "
            f"{{scenario_kind, ecosystem}}, got {scenarios!r}"
        )

    out: dict[str, CaseClassification] = {}
    for case_name, entry in scenarios.items():
        entry = entry or {}
        if not isinstance(entry, dict):
            raise ValueError(
                f"{manifest}: 'scenarios' entry {case_name!r} must be a mapping "
                f"with scenario_kind/ecosystem, got {entry!r}"
            )
        out[case_name] = CaseClassification(
            entity="scenario",
            scenario_kind=str(entry.get("scenario_kind", "")).strip() or None,
            ecosystem=str(entry.get("ecosystem", "generic")).strip() or "generic",
        )

    rules = raw.get("rules") or []
    if not isinstance(rules, (list, tuple)):
        raise ValueError(
            f"{manifest}: 'rules' must be a list of case names, got {rules!r}"
        )
    seen_rules: set[str] = set()
    for case_name in rules:
        if case_name in seen_rules:
            raise ValueError(
                f"{case_name!r} is listed more than once under 'rules' in "
                f"{manifest.name} -- a case belongs to exactly one entry"
            )
        seen_rules.add(case_name)
        if case_name in out:
            raise ValueError(
                f"{case_name!r} is listed under both 'scenarios' and 'rules' in "
                f"{manifest.name} -- a case belongs to exactly one"
            )
        out[case_name] = CaseClassification(
            entity="rule", scenario_kind=None, ecosystem="generic"
        )
    return out


def validate_classification(
    classification: dict[str, CaseClassification],
    case_names: Iterable[str],
) -> list[str]:
    """One message per manifest/ground-truth disagreement; [] when clean.

    Both directions are errors. A case with no entry is the silent-default
    gap this module exists to close -- classifying it requires an explicit
    decision, not a fallback. A stale entry naming a case that no longer
    exists in `ground_truth.json["verdicts"]` (removed or renamed) is dead
    configuration that looks like coverage, the same reasoning
    `catalog_rule_registry.validate_registry()` applies to an unused rule
    slug.
    """
    case_names = set(case_names)
    classified = set(classification)
    errors: list[str] = []

    for case_name in sorted(case_names - classified):
        errors.append(
            f"{case_name!r} has no entry in {CLASSIFICATION_PATH.name} -- add it "
            "under 'scenarios' (with scenario_kind/ecosystem) or 'rules' before "
            "it can be classified"
        )
    for case_name in sorted(classified - case_names):
        errors.append(
            f"{CLASSIFICATION_PATH.name} classifies {case_name!r}, which is not "
            "a case in ground_truth.json['verdicts'] -- remove the stale entry"
        )
    for case_name, c in sorted(classification.items()):
        if c.entity == "scenario" and c.scenario_kind not in SCENARIO_KINDS:
            errors.append(
                f"{case_name!r}: scenario_kind {c.scenario_kind!r} is not one of "
                f"{sorted(SCENARIO_KINDS)}"
            )
        if c.ecosystem not in ECOSYSTEMS:
            errors.append(
                f"{case_name!r}: ecosystem {c.ecosystem!r} is not one of "
                f"{sorted(ECOSYSTEMS)}"
            )
        if c.entity == "rule" and c.ecosystem != "generic":
            errors.append(
                f"{case_name!r}: a rule-entity case must have ecosystem "
                f"'generic' (got {c.ecosystem!r}) -- a rule case naming a real "
                "ecosystem should be classified as a scenario case-study instead"
            )
    return errors
