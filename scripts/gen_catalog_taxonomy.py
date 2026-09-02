#!/usr/bin/env python3
"""Generate the `taxonomy` block of examples/ground_truth.json.

Phase 1 of the examples/catalog split (see
docs/contribute/plans/examples-catalog-split.md): the 197-entry catalog under
`examples/` currently presents a single flat "case" shape for several
genuinely different entities -- an atomic compatibility *rule*, a *scenario*
that composes several rules (an ecosystem case study, a multi-library project
topology, or a capability/evidence demonstration), and a *variant* that
re-demonstrates an existing rule under a different condition (language,
evidence tier, public-surface reachability, ...). Collapsing all of that into
one "case" count is what let a real duplicate (case01/case12, both a plain
exported-function removal) and a real variant (case08/case20, the same
enum-member-value-changed rule under public-surface scoping) sit in the
catalog as though they were three independent compatibility concepts.

This script does not move or rename anything -- it is additive metadata only,
generated into a `taxonomy` object in `ground_truth.json` (a sibling of the
existing `verdicts` object, never merged into it, so every existing consumer
of `verdicts` is unaffected). Each entry classifies its case along axes that
are orthogonal to implementation language and independent of the physical
`examples/caseNN_*/` directory layout:

    entity          "rule" | "scenario"
    scenario_kind   set only for entity == "scenario":
                     "case-study" | "project-topology" | "capability" | "audit"
    ecosystem       "generic" | "onetbb" | "sycl" | "onemkl" | "linux-kernel"
    topics          derived from expected_kinds via the change-catalog's own
                     symbols/types/platform/build/source split (AGENTS.md
                     "Adding a new ChangeKind" step 2) -- reusing that
                     existing, principled categorization rather than
                     re-inventing a second one from case names
    languages       derived from which source-file extensions the case's own
                     fixtures ship
    scope           "single-library" | "multi-library"
    artifact_shape  "compiled-pair" | "snapshot-pair" | "snapshot-audit" |
                     "stub-pair" | "btf-pair" | "kabi-pair" | "fixture-pair"
                     | "bundle"
    validation_owner the runner family that exercises this case (mirrors
                     examples/CLAUDE.md's "owner families" list)
    related_rules   rule slugs a scenario composes (only populated for the
                     handful of scenarios explicitly named in the design doc
                     so far -- an incomplete but honest starting set)
    rule_slug       canonical slug for a rule family (only populated for the
                     case01/12 and case08/20 worked-example consolidation --
                     see "Known gaps" in the design doc for the rest)
    variant_of      the canonical case this one is a variant of, or null

Run `python scripts/gen_catalog_taxonomy.py` to regenerate; `--check` fails
(exit 1) if regeneration would change the file, without writing anything.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
GROUND_TRUTH = EXAMPLES / "ground_truth.json"

# ---------------------------------------------------------------------------
# Explicit case-number groupings from the examples-catalog-split design doc.
# Keyed by case *number* (an int), not slug, so a future rename doesn't
# invalidate this table.
# ---------------------------------------------------------------------------

BUNDLE_SCENARIOS = {84, 90, 91, 92, 93}
CAPABILITY_SCENARIOS = {147, 148, 149, 150, 151, 191, 192, 193, 194, 195, 196, 197}
ONETBB_CASE_STUDIES = {78, 94, 107, 108, 109, 110, 111}
SYCL_CASE_STUDIES = {82, 126}
ONEMKL_CASE_STUDIES = {112}
LINUX_KERNEL_CASE_STUDIES = {121, 175, 176}

# Scenarios' related_rules -- hand-curated per the design doc's own worked
# examples. Deliberately small: only the cases the design doc names get a
# relation recorded here; the rest carry an empty list until a future pass
# reviews them (see the module docstring's "Known gaps" pointer).
RELATED_RULES: dict[str, list[str]] = {
    "case108_task_class_removed": [
        "exported-function-removed",
        "virtual-dispatch-contract-removed",
    ],
    "case112_lp64_ilp64": ["public-integer-model-width-changed"],
    "case126_sycl_device_impl_ptr": ["public-class-representation-changed"],
    "case94_empty_tag_gained_state": ["empty-tag-type-gains-state"],
}

# Worked rule/variant consolidation (Phase 2 slice): two duplicate/near-
# duplicate pairs the design doc names explicitly. `rule_slug` is the
# canonical family; `variant_of` names the canonical case within it (null on
# the canonical case itself).
RULE_FAMILIES: dict[str, tuple[str, str | None]] = {
    "case01_symbol_removal": ("exported-function-removed", None),
    "case12_function_removed": ("exported-function-removed", "case01_symbol_removal"),
    "case08_enum_value_change": ("enum-member-value-changed", None),
    "case20_enum_member_value_changed": (
        "enum-member-value-changed",
        "case08_enum_value_change",
    ),
}

_CASE_NUM_RE = re.compile(r"^case(\d+)")


def _case_number(case_name: str) -> int:
    m = _CASE_NUM_RE.match(case_name)
    if not m:
        raise ValueError(f"can't parse case number from {case_name!r}")
    return int(m.group(1))


def _kind_to_topic() -> dict[str, str]:
    from abicheck.model.change_catalog import build, platform, source, symbols, types

    entry_lists = {
        "symbols": symbols.SYMBOLS_ENTRIES,
        "types": types.TYPES_ENTRIES,
        "platform": platform.PLATFORM_ENTRIES,
        "build": build.BUILD_ENTRIES,
        "source": source.SOURCE_ENTRIES,
    }
    mapping: dict[str, str] = {}
    for topic, entries in entry_lists.items():
        for entry in entries:
            mapping[entry.kind] = topic
    return mapping


def _languages(case_dir: Path, has_committed_fixtures: bool) -> list[str]:
    """Derive language(s) from which source-file extensions the case's own
    fixtures ship. A case whose only committed artifacts are fixture data
    (a `fixtures:` list in ground_truth.json -- .abi.json/.symvers/.json
    snapshots, never compiled source) carries no source extension to derive
    a language from, so it's left `[]` rather than guessed at: several such
    cases (e.g. case192/193) model C++ constructs in their fixture despite
    shipping no .cpp file.
    """
    names = {p.name.lower() for p in case_dir.rglob("*") if p.is_file()}
    langs = []
    # C and C++ are detected independently -- a mixed-language case (e.g.
    # case66_language_linkage_changed, a C++ library exercised through a
    # .c consumer) legitimately carries both. `.h` is deliberately excluded
    # from the C signal: it's a language-neutral extension a C++-only case
    # (e.g. case09_cpp_vtable, .cpp implementation + .h headers, no .c file
    # anywhere) uses too, so its presence alone doesn't mean C.
    if any(n.endswith(".c") for n in names):
        langs.append("c")
    if any(n.endswith((".cpp", ".hpp", ".cc", ".hh", ".cxx")) for n in names):
        langs.append("cpp")
    if any(n.endswith(".pyi") for n in names) or any(
        "stub" in n for n in names if n.endswith(".py")
    ):
        langs.append("python")
    if any(n.endswith(".btf") for n in names):
        langs.append("c")  # kernel BTF fixtures describe C types
    if langs or has_committed_fixtures:
        return langs
    return ["c"]  # a compiled-pair case with no recognized source extension


def _artifact_shape(
    case_dir: Path, case_num: int, fixtures: list[str], mode: str | None
) -> str:
    """Derive the fixture shape. `fixtures` (ground_truth.json's own
    `fixtures:` list, when the case declares one) is authoritative over any
    file-scan heuristic -- it's what the case's own owner already committed
    to, and covers every naming convention a committed-fixture case uses
    (`snapshot.abi.json`, `old.abi.json`/`new.abi.json`,
    `v1.abi.json`/`v2.abi.json`, `v1.symvers`/`v2.symvers`,
    `old.json`/`new.json` build/source-graph fixtures) without hard-coding
    each one.
    """
    if case_num in BUNDLE_SCENARIOS:
        return "bundle"
    if fixtures:
        if any(f.endswith(".abi.json") for f in fixtures):
            if mode == "audit":
                # A single-release G20 audit scan (no `--against` baseline)
                # -- one committed snapshot.abi.json, not an old/new pair.
                # case151's second file (thin.abi.json) is a lower-evidence
                # variant of the same single release, not a comparison peer.
                return "snapshot-audit"
            return "snapshot-pair"
        if any(f.endswith(".symvers") for f in fixtures):
            return "kabi-pair"
        return "fixture-pair"  # committed non-.abi.json data (build/source-graph JSON, ...)
    names = {p.name for p in case_dir.iterdir() if p.is_file()}
    if any(n.endswith(".btf") for n in names):
        return "btf-pair"
    if any(n.endswith(".pyi") for n in names):
        # A Python-stub-only fixture (no v1/v2 or old/new *compiled* pair) --
        # currently just case163. `old`/`new` subdirectories are also used as
        # a plain compiled-pair layout alternative to v1/v2 filename
        # prefixes (examples/CLAUDE.md's "Per-case layout"), so their mere
        # presence doesn't imply a stub pair.
        return "stub-pair"
    return "compiled-pair"


def _validation_owner(artifact_shape: str, mode: str | None) -> str:
    """The runner family that exercises this case (examples/CLAUDE.md's
    "owner families"). Derived from `artifact_shape`/`mode` alone, never
    from a case-number bucket: `CAPABILITY_SCENARIOS` groups cases by what
    they *demonstrate* (a capability), which isn't the same split as which
    runner *executes* them --
    `validation/scripts/run_special_cli_examples.py`'s own
    `COMPARE_CASES`/`SCAN_CASES`/`EVIDENCE_CASES` split case191 (a real
    compiled pair, run through the live header-graph integration lane) and
    cases192/193 (`COMPARE_CASES`) out of the G20 `SCAN_CASES` set that
    cases143-151/181 alone belong to, even though all of them are
    `CAPABILITY_SCENARIOS`.
    """
    if artifact_shape == "bundle":
        return "bundle-runner"
    if mode == "reconcile":
        return "reconcile"
    if artifact_shape == "snapshot-audit":
        return "g20-audit"  # SCAN_CASES
    if artifact_shape == "snapshot-pair":
        return "compare-cases"  # COMPARE_CASES
    if artifact_shape == "fixture-pair":
        return "evidence-cases"  # EVIDENCE_CASES
    if artifact_shape == "btf-pair":
        return "btf"
    if artifact_shape == "stub-pair":
        return "python-api"
    if artifact_shape == "kabi-pair":
        return "kabi"
    return "compiler-pair"


def _entity_and_scenario_kind(case_num: int) -> tuple[str, str | None]:
    if case_num in BUNDLE_SCENARIOS:
        return "scenario", "project-topology"
    if case_num in CAPABILITY_SCENARIOS:
        return "scenario", "capability"
    if (
        case_num in ONETBB_CASE_STUDIES
        or case_num in SYCL_CASE_STUDIES
        or case_num in ONEMKL_CASE_STUDIES
        or case_num in LINUX_KERNEL_CASE_STUDIES
    ):
        return "scenario", "case-study"
    # A mode=="audit" case (e.g. 143-146) stays entity == "rule":
    # `scenario_kind` is only ever set for entity == "scenario" (contract
    # asserted by build_taxonomy's own invariant check below); audit-ness
    # is instead carried by the "audit" topics entry these cases already
    # get.
    return "rule", None


def _ecosystem(case_num: int) -> str:
    if case_num in ONETBB_CASE_STUDIES:
        return "onetbb"
    if case_num in SYCL_CASE_STUDIES:
        return "sycl"
    if case_num in ONEMKL_CASE_STUDIES:
        return "onemkl"
    if case_num in LINUX_KERNEL_CASE_STUDIES:
        return "linux-kernel"
    return "generic"


def _scope(case_num: int) -> str:
    return "multi-library" if case_num in BUNDLE_SCENARIOS else "single-library"


def build_taxonomy(gt: dict[str, object]) -> dict[str, dict[str, object]]:
    verdicts: dict[str, dict[str, object]] = gt["verdicts"]  # type: ignore[assignment]
    kind_to_topic = _kind_to_topic()

    taxonomy: dict[str, dict[str, object]] = {}
    for case_name, entry in verdicts.items():
        case_num = _case_number(case_name)
        case_dir = EXAMPLES / case_name
        entity, scenario_kind = _entity_and_scenario_kind(case_num)

        mode = entry.get("mode")
        fixtures = entry.get("fixtures") or []

        expected_kinds = entry.get("expected_kinds") or []
        topics = sorted(
            {kind_to_topic[k] for k in expected_kinds if k in kind_to_topic}
        )
        if mode == "audit" and "audit" not in topics:
            topics.append("audit")
            topics.sort()
        if not expected_kinds and entry.get("expected") == "NO_CHANGE":
            # A negative/safe-redesign control: no ChangeKind fires by
            # design, so there's no symbols/types/... topic to derive --
            # "controls" is its own topic (design doc section 5/8).
            topics = ["controls"]

        artifact_shape = _artifact_shape(case_dir, case_num, fixtures, mode)
        rule_slug, variant_of = RULE_FAMILIES.get(case_name, (None, None))

        assert scenario_kind is None or entity == "scenario", (
            f"{case_name}: scenario_kind is set ({scenario_kind!r}) but "
            f"entity is {entity!r}, not 'scenario' -- contract violation"
        )

        taxonomy[case_name] = {
            "entity": entity,
            "scenario_kind": scenario_kind,
            "ecosystem": _ecosystem(case_num),
            "topics": topics,
            "languages": _languages(case_dir, bool(fixtures))
            if case_dir.is_dir()
            else [],
            "scope": _scope(case_num),
            "artifact_shape": artifact_shape,
            "validation_owner": _validation_owner(artifact_shape, mode),
            "related_rules": RELATED_RULES.get(case_name, []),
            "rule_slug": rule_slug,
            "variant_of": variant_of,
        }
    # Preserve the existing verdicts iteration order (already caseNN-ordered
    # in the committed file) rather than re-sorting -- keeps a regeneration
    # diff scoped to the new `taxonomy` key alone.
    return taxonomy


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if regeneration would change ground_truth.json['taxonomy']",
    )
    args = parser.parse_args()

    # json.loads on a plain dict literal preserves file order (no sort_keys
    # anywhere in this repo's ground_truth.json -- verified against the
    # committed file's own byte-for-byte round trip through indent=1).
    gt: dict[str, object] = json.loads(GROUND_TRUTH.read_text())
    new_taxonomy = build_taxonomy(gt)

    if args.check:
        if gt.get("taxonomy") != new_taxonomy:
            print(
                "ground_truth.json['taxonomy'] is stale -- run "
                "`python scripts/gen_catalog_taxonomy.py` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print("ground_truth.json['taxonomy'] is up to date.")
        return 0

    if gt.get("taxonomy") == new_taxonomy:
        print("ground_truth.json['taxonomy'] already up to date; nothing to do.")
        return 0

    gt["taxonomy"] = new_taxonomy
    GROUND_TRUTH.write_text(json.dumps(gt, indent=1) + "\n")
    print(f"Wrote taxonomy for {len(new_taxonomy)} cases to {GROUND_TRUTH}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
