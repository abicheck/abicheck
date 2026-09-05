#!/usr/bin/env python3
"""The canonical compatibility-rule registry and its join against the
calibration catalog's taxonomy.

Phase 2/6 of the examples/catalog split
(docs/contribute/plans/examples-catalog-split.md). `examples/catalog_rules.yaml`
is the hand-authored half: one `title` + `definition` per rule slug. This
module is the derived half -- it joins that registry against
`ground_truth.json["taxonomy"]` to answer, per rule, which case is the
canonical demonstration, which cases are variants or duplicates of it, which
scenarios compose it, and therefore whether the rule is *demonstrated* by a
real rule-entity case or merely *referenced* by a scenario.

Why the registry exists at all: before it, a rule slug was an unvalidated
free-text string in two places -- a rule case's own `rule_slug` and a
scenario's `related_rules` list -- and `catalog-coverage.md` counted the
distinct set of them as "distinct compatibility rules". A typo, a synonym, or
an accidental rename in either place therefore became one more rule in that
headline with nothing anywhere to notice. `validate_registry()` closes that
in both directions: every slug the taxonomy uses must be defined here, and
every slug defined here must be used by at least one case (so the registry
cannot quietly accumulate rules the catalog does not actually name).

`status`/`canonical_case`/`cases`/`scenarios` are deliberately *derived*
rather than stated in the YAML: they are facts about the taxonomy, and
restating them by hand is exactly the kind of second copy that goes stale
the first time a case is added or reclassified.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import example_catalog  # noqa: E402

REGISTRY_PATH = example_catalog.EXAMPLES_DIR / "catalog_rules.yaml"

#: A rule with at least one rule-entity case carrying it as `rule_slug`.
STATUS_DEMONSTRATED = "demonstrated"
#: A rule named only by a scenario's `related_rules` -- a real compatibility
#: mechanism the catalog composes but does not yet demonstrate on its own.
STATUS_REFERENCED_ONLY = "referenced-only"


@dataclass(frozen=True)
class RuleDefinition:
    """The hand-authored half: one entry of `examples/catalog_rules.yaml`."""

    slug: str
    title: str
    definition: str


@dataclass
class RuleFamily:
    """A rule definition joined against every case that names it."""

    slug: str
    title: str
    definition: str
    #: The non-variant rule-entity case demonstrating the rule, if any.
    canonical_case: str | None = None
    #: `(case_id, relation_axis)` for each `relation_type == "variant"` case.
    variant_cases: list[tuple[str, str | None]] = field(default_factory=list)
    #: Each `relation_type == "duplicate"` case.
    duplicate_cases: list[str] = field(default_factory=list)
    #: Scenario-entity cases naming this rule in their `related_rules`.
    scenario_cases: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return (
            STATUS_DEMONSTRATED
            if self.canonical_case is not None
            else STATUS_REFERENCED_ONLY
        )

    @property
    def rule_cases(self) -> list[str]:
        """Every rule-entity case in the family, canonical first."""
        cases = [self.canonical_case] if self.canonical_case else []
        cases += [c for c, _ in self.variant_cases]
        cases += self.duplicate_cases
        return cases


def load_registry(path: Path | None = None) -> dict[str, RuleDefinition]:
    """Parse `examples/catalog_rules.yaml` into slug -> definition."""
    raw = yaml.safe_load((path or REGISTRY_PATH).read_text(encoding="utf-8"))
    rules = (raw or {}).get("rules") or {}
    out: dict[str, RuleDefinition] = {}
    for slug, entry in rules.items():
        entry = entry or {}
        out[slug] = RuleDefinition(
            slug=slug,
            title=str(entry.get("title", "")).strip(),
            definition=str(entry.get("definition", "")).strip(),
        )
    return out


def taxonomy_rule_slugs(taxonomy: dict[str, dict]) -> set[str]:
    """Every rule slug the taxonomy names, from either of its two sources."""
    slugs: set[str] = set()
    for entry in taxonomy.values():
        if entry.get("rule_slug"):
            slugs.add(str(entry["rule_slug"]))
        for related in entry.get("related_rules") or []:
            slugs.add(str(related))
    return slugs


def build_families(
    taxonomy: dict[str, dict],
    registry: dict[str, RuleDefinition] | None = None,
) -> dict[str, RuleFamily]:
    """Join the registry against the taxonomy, slug -> `RuleFamily`.

    Slugs the taxonomy names but the registry does not define still get a
    family (titled from the slug), so a caller rendering a page or a report
    never crashes on unvalidated data -- `validate_registry()` is what turns
    that state into an error, in one place, rather than each consumer
    deciding for itself.
    """
    registry = registry if registry is not None else load_registry()
    families: dict[str, RuleFamily] = {}

    def family(slug: str) -> RuleFamily:
        if slug not in families:
            definition = registry.get(slug)
            families[slug] = RuleFamily(
                slug=slug,
                title=definition.title if definition else _title_from_slug(slug),
                definition=definition.definition if definition else "",
            )
        return families[slug]

    for case_id, entry in sorted(taxonomy.items()):
        slug = entry.get("rule_slug")
        if slug:
            fam = family(str(slug))
            relation = entry.get("relation_type")
            if relation == "variant":
                fam.variant_cases.append((case_id, entry.get("relation_axis")))
            elif relation == "duplicate":
                fam.duplicate_cases.append(case_id)
            else:
                fam.canonical_case = case_id
        for related in entry.get("related_rules") or []:
            family(str(related)).scenario_cases.append(case_id)

    # Deliberately *not* seeded from the registry's own keys: a family exists
    # because a case names it. A definition no case uses is an error
    # `validate_registry()` reports, not a rule to render a page or a
    # coverage row for -- and seeding from the registry would make this
    # function's answer depend on the whole committed registry even when the
    # caller passed a small, self-contained taxonomy.
    return families


def validate_registry(
    taxonomy: dict[str, dict],
    registry: dict[str, RuleDefinition] | None = None,
) -> list[str]:
    """Return one message per registry/taxonomy disagreement; [] when clean.

    Both directions are errors, for different reasons. An *undefined* slug
    is the typo/synonym case the registry exists to catch. An *unused* entry
    is the opposite drift -- a rule that was renamed or whose case was
    removed, leaving a definition nothing points at, which would keep
    inflating the registry's own count.
    """
    registry = registry if registry is not None else load_registry()
    used = taxonomy_rule_slugs(taxonomy)
    errors: list[str] = []

    for slug in sorted(used - set(registry)):
        users = sorted(
            case_id
            for case_id, entry in taxonomy.items()
            if entry.get("rule_slug") == slug
            or slug in (entry.get("related_rules") or [])
        )
        errors.append(
            f"rule slug {slug!r} is used by {', '.join(users)} but has no entry "
            f"in {REGISTRY_PATH.name} -- add one, or fix the spelling"
        )
    for slug in sorted(set(registry) - used):
        errors.append(
            f"rule slug {slug!r} is defined in {REGISTRY_PATH.name} but no case "
            f"uses it as a rule_slug or in related_rules -- remove the entry"
        )
    for slug, definition in sorted(registry.items()):
        if not definition.title:
            errors.append(f"rule slug {slug!r} has an empty title")
        if not definition.definition:
            errors.append(f"rule slug {slug!r} has an empty definition")

    errors.extend(validate_relations(taxonomy))
    return errors


def validate_relations(taxonomy: dict[str, dict]) -> list[str]:
    """Check that every `variant_of` points somewhere coherent.

    A slug being defined and used says nothing about whether a relation is
    sound: a `variant_of` naming a case in a *different* rule family would
    make `build_families()` group the variant under its declared slug while
    its generated page links to an unrelated case -- contradictory catalog
    and coverage data that no link checker can see, because both pages
    exist.

    `tests/test_catalog_taxonomy.py::test_variant_of_names_a_real_case`
    already pins these three properties in the fast lane. They are repeated
    here because `gen_catalog_taxonomy.py` calls this function *before
    writing*, so a bad relation fails at generation rather than being
    committed to disk first and caught a test run later.
    """
    errors: list[str] = []
    for case_id, entry in sorted(taxonomy.items()):
        target = entry.get("variant_of")
        if not target:
            continue
        if target not in taxonomy:
            errors.append(
                f"{case_id}: variant_of names {target!r}, which is not a case"
            )
            continue
        target_entry = taxonomy[target]
        if target_entry.get("rule_slug") != entry.get("rule_slug"):
            errors.append(
                f"{case_id}: variant_of names {target!r}, but they disagree on "
                f"rule_slug ({entry.get('rule_slug')!r} vs "
                f"{target_entry.get('rule_slug')!r}) -- a variant must belong to "
                "the family it points at"
            )
        if target_entry.get("variant_of"):
            errors.append(
                f"{case_id}: variant_of names {target!r}, which is itself a "
                "variant -- a family's canonical case must be canonical"
            )
    return errors


def _title_from_slug(slug: str) -> str:
    words = slug.replace("-", " ")
    return words[:1].upper() + words[1:]
