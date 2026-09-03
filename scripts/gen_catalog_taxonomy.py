#!/usr/bin/env python3
"""Generate the `taxonomy` block of examples/ground_truth.json.

Phase 1 (taxonomy metadata) and Phase 2 (rule/variant classification) of
the examples/catalog split (see
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
    related_rules   rule slugs a scenario composes -- populated for every
                     scenario case (see RELATED_RULES below)
    rule_slug       canonical slug for a rule family -- set on every
                     "rule"-entity case (mechanically derived by default,
                     see _default_rule_slug; a hand-reviewed shared slug for
                     a confirmed duplicate, see RULE_FAMILIES)
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

# Phase 3 resolver (scripts/CLAUDE.md). This script's own directory is
# already on sys.path when run directly, but not when imported as
# `scripts.gen_catalog_taxonomy` -- guard mirrors fact_detector_misuse.py's
# identical sibling-import guard for the identical reason.
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
import example_catalog  # noqa: E402

EXAMPLES = example_catalog.EXAMPLES_DIR
GROUND_TRUTH = example_catalog.GROUND_TRUTH_PATH

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
# examples, extended to every scenario case in this pass (the design doc's
# "Known gaps" -- only 4 of 30 scenarios carried a relation before this).
# Each entry names the generic, ecosystem-neutral compatibility rule(s) the
# scenario composes -- reusing an existing rule-entity case's own `rule_slug`
# when the same underlying mechanism already has one (e.g.
# "exported-function-removed", "public-api-gains-internal-dependency"), and
# otherwise a mechanically-parallel conceptual slug in the same style (mostly
# the scenario's own dominant `expected_kinds` entry, kebab-cased) for a
# mechanism no single-library rule case demonstrates on its own yet -- the
# same asymmetry the four original entries already establish (e.g.
# "virtual-dispatch-contract-removed" and "empty-tag-type-gains-state" name
# no catalog rule_slug either). `related_rules` is deliberately not
# validated against the rule_slug set for this reason -- see
# tests/test_catalog_taxonomy.py's own `test_related_rules_are_non_empty_strings`.
RELATED_RULES: dict[str, list[str]] = {
    # -- oneTBB case studies --
    "case78_task_arena_attach_tag": [
        "compat-addition",
        "compatible-type-added",
        "exported-function-removed",
        "exported-type-removed",
    ],
    "case94_empty_tag_gained_state": [
        "embedded-type-size-increased",
        "empty-tag-type-gains-state",
        "type-field-added-compatible",
    ],
    "case107_task_scheduler_init_removed": [
        "compat-addition",
        "exported-function-removed",
        "exported-type-removed",
    ],
    "case108_task_class_removed": [
        "compatible-type-added",
        "exported-function-removed",
        "exported-type-removed",
        "var-removed",
        "virtual-dispatch-contract-removed",
    ],
    "case109_flow_graph_policy_renames": [
        "compatible-type-added",
        "public-typedef-removed",
        "tag-struct-renamed",
    ],
    "case110_concurrent_unordered_map_api_drift": [
        "compat-addition",
        "exported-function-removed",
    ],
    "case111_enumerable_thread_specific_lambda_ambiguity": [
        "constructor-overload-ambiguity"
    ],
    # -- SYCL case studies --
    "case82_sycl_overload_set_removed": [
        "exported-function-removed",
        "exported-type-removed",
        "overload-set-removed",
    ],
    "case126_sycl_device_impl_ptr": ["public-class-representation-changed"],
    # -- oneMKL case study --
    "case112_lp64_ilp64": [
        "public-integer-model-width-changed",
        "typedef-underlying-changed",
    ],
    # -- Linux kernel case studies --
    "case121_kernel_btf_struct_field_added": ["embedded-type-size-increased"],
    "case175_kabi_crc_changed": ["symbol-type-signature-hash-changed"],
    "case176_kabi_symbol_namespace_changed": ["symbol-export-namespace-changed"],
    # -- multi-library bundle project-topology scenarios --
    "case84_bundle_soname_skew": ["soname-inconsistent"],
    "case90_bundle_intra_dep_removed": ["exported-function-removed"],
    "case91_bundle_intra_signature_drift": ["param-type-change", "return-type"],
    "case92_bundle_provider_changed": [
        "compat-addition",
        "exported-function-removed",
        "symbol-source-owner-changed",
    ],
    "case93_bundle_manifest_drift": [
        "exported-function-removed",
        "missing-template-instantiation",
    ],
    # -- G20 capability/audit scenarios --
    "case147_scan_depth_ladder": ["audit-private-header-leak"],
    "case148_xcheck_header_build_mismatch": ["header-build-context-mismatch"],
    "case149_xcheck_odr_variant": ["odr-type-variant"],
    "case150_xcheck_export_public_pair": [
        "audit-accidental-export",
        "audit-public-not-exported",
    ],
    "case151_xcheck_provider_matrix": ["audit-private-header-leak"],
    # -- call-graph / header-graph reconciliation capability scenarios --
    "case191_header_only_graph_field_type": [
        "compatible-type-added",
        "embedded-type-size-increased",
        "public-api-gains-internal-dependency",
        "type-field-added-compatible",
    ],
    "case192_call_graph_break_survives_suppression": [
        "exported-function-removed",
        "internal-symbol-required-by-public-api",
    ],
    "case193_ordinary_exported_fn_call_not_reachable": ["exported-function-removed"],
    "case194_header_graph_rename_reconciled": [
        "internal-declaration-renamed-reconciled",
        "public-api-gains-internal-dependency",
    ],
    "case195_header_graph_ambiguous_rename_not_reconciled": [
        "public-api-gains-internal-dependency"
    ],
    "case196_header_graph_move_reconciled": [
        "internal-declaration-moved-reconciled",
        "public-api-gains-internal-dependency",
    ],
    "case197_header_graph_identity_reconciled": [
        "internal-declaration-identity-reconciled",
        "public-api-gains-internal-dependency",
    ],
}

# Rule/variant consolidation (Phase 2). Every `rule`-entity case gets a
# `rule_slug` -- for a case with no known duplicate, `_default_rule_slug()`
# below derives one mechanically from the case's own name, so the family
# name always exists even for a rule nobody has found a sibling for yet.
# This table exists only for a case whose canonical family differs from
# that mechanical default: a genuine duplicate/near-duplicate pair, found by
# clustering every `rule`-entity case on its exact `expected_kinds` set and
# reading each candidate cluster's README to separate a true duplicate
# (same underlying mechanism, restated) from cases that merely share a
# `ChangeKind` while demonstrating a different one (which stay independent
# rules with their own default slug, not listed here). `rule_slug` is the
# canonical family name both sides of a pair share; `variant_of` names the
# canonical case within it (null on the canonical case itself, including a
# canonical case listed here only to pin a shared slug for its variant(s)).
#
# Pairs confirmed as genuine duplicates this pass, with the read that ruled
# each one in:
#   case01/case12  -- both a plain exported-function removal, same evidence.
#   case08/case20  -- same enum-member-value-changed rule; case20 adds
#                      public-surface scoping as a variant condition.
#   case16/case47  -- byte-for-byte the same mechanism (an inline method
#                      moved out-of-line gains a real exported symbol) under
#                      different demo names -- confirmed by diffing the two
#                      READMEs, not just matching expected_kinds.
#   case49/case136 -- case136's own README calls itself "the fix
#                      counterpart to case49", but the technical
#                      demonstration (GNU_STACK RWE -> RW) and even the
#                      library source are identical, not just complementary.
#   case65/case139 -- both a hard BREAKING symbol-version-node removal
#                      (consumer records a version dependency the loader can
#                      no longer satisfy); case139 adds the "old symbol name
#                      persists, folded into a different node" nuance as a
#                      variant, kept distinct from case183 (same ChangeKind
#                      but COMPATIBLE_WITH_RISK -- a private/internal-node
#                      naming convention changes the verdict, not just the
#                      demo, so it stays its own rule).
#   case160/case190 -- same L5 "public API gains an internal dependency"
#                      rule; case190 narrows it to the inline-function case
#                      (the dependency is invisible to every artifact-level
#                      diff, not just source-graph-level).
#
# Clusters reviewed and NOT merged (same ChangeKind, different mechanism or
# verdict, so each keeps its own default slug): case03/16/47/62/185 (func_added
# from four unrelated causes); case07/14/17/18/36/40/44/48 (type_size_changed
# from eight unrelated causes -- case07/case14 *are* a duplicate pair, C vs
# C++, see below); case09/case38; case46/case102; case74/75/76/77 (the
# "leaked internal types" pattern family -- four distinct embedding
# mechanisms, deliberately not collapsed, per AGENTS.md's own worked
# case01/case12 vs case08/case20 caution against over-consolidating); case97/182;
# case137/52 (RUNPATH changed vs. RUNPATH build-path leak -- different
# transition, different lesson); case43/77.
RULE_FAMILIES: dict[str, tuple[str, str | None]] = {
    "case01_symbol_removal": ("exported-function-removed", None),
    "case12_function_removed": ("exported-function-removed", "case01_symbol_removal"),
    "case08_enum_value_change": ("enum-member-value-changed", None),
    "case20_enum_member_value_changed": (
        "enum-member-value-changed",
        "case08_enum_value_change",
    ),
    "case07_struct_layout": ("embedded-type-size-increased", None),
    "case14_cpp_class_size": ("embedded-type-size-increased", "case07_struct_layout"),
    "case16_inline_to_non_inline": ("inline-function-outlined", None),
    "case47_inline_to_outlined": (
        "inline-function-outlined",
        "case16_inline_to_non_inline",
    ),
    "case49_executable_stack": ("executable-stack-flag-changed", None),
    "case136_executable_stack_removed": (
        "executable-stack-flag-changed",
        "case49_executable_stack",
    ),
    "case65_symbol_version_removed": ("symbol-version-node-removed", None),
    "case139_symbol_version_node_removed": (
        "symbol-version-node-removed",
        "case65_symbol_version_removed",
    ),
    "case160_public_api_internal_dep_added": (
        "public-api-gains-internal-dependency",
        None,
    ),
    "case190_public_inline_function_references_internal_constant": (
        "public-api-gains-internal-dependency",
        "case160_public_api_internal_dep_added",
    ),
}

_CASE_NUM_RE = re.compile(r"^case(\d+)")


def _case_number(case_name: str) -> int:
    m = _CASE_NUM_RE.match(case_name)
    if not m:
        raise ValueError(f"can't parse case number from {case_name!r}")
    return int(m.group(1))


def _default_rule_slug(case_name: str) -> str:
    """A rule case not listed in RULE_FAMILIES still gets a canonical
    `rule_slug` -- mechanically derived from its own case name (the part
    after `caseNN_`, hyphenated) rather than left null, so "does this rule
    have a name" never depends on whether a sibling duplicate happens to
    have been found yet. A case that *is* in RULE_FAMILIES uses that
    entry's hand-reviewed slug instead (set by the caller before falling
    back to this).
    """
    rest = _CASE_NUM_RE.sub("", case_name, count=1).lstrip("_")
    # A letter-suffixed case number (26b) leaves the letter on `rest`'s
    # front (b_union_field...) after the digit-only regex strips just the
    # digits -- drop a lone leading letter segment before the underscore.
    rest = re.sub(r"^[a-z]_", "", rest)
    return rest.replace("_", "-")


def _kind_to_topic() -> dict[str, str]:
    # Make the command work from a clean checkout (no install / no
    # PYTHONPATH): running `python scripts/gen_catalog_taxonomy.py` puts
    # scripts/ on sys.path, not the repo root, so `abicheck` wouldn't
    # otherwise resolve (mirrors gen_detector_spec.py's own bootstrap).
    sys.path.insert(0, str(ROOT))
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
        case_dir = example_catalog.case_dir(case_name)
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
        if case_name in RULE_FAMILIES:
            rule_slug, variant_of = RULE_FAMILIES[case_name]
        elif entity == "rule":
            rule_slug, variant_of = _default_rule_slug(case_name), None
        else:
            # A scenario composes rules via `related_rules` instead of
            # having one rule_slug of its own.
            rule_slug, variant_of = None, None

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
