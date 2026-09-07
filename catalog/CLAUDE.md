# CLAUDE.md — `catalog/`

This is the **calibration and compatibility-knowledge tree** — the 208
`caseNN_*` fixtures the FP-rate, tier-accuracy, mutation, and
full-catalog-coverage gates all run against, physically split out of
`examples/` (Phase 4 of the [examples/catalog split]
(../docs/contribute/plans/examples-catalog-split.md)'s "Corrected Phase 4
target model"). It is not the curated, task-oriented tree — see
[`../examples/CLAUDE.md`](../examples/CLAUDE.md) for that.

The catalog has multiple owner families: ordinary single-library pairs,
multi-library bundles, G20 audit/cross-source fixtures, L3/L4/L5 fixtures, BTF,
Python API, reconcile, snapshot-pair, KABI, and other specialized cases. The
authoritative count is the number of entries in `ground_truth.json` — never
trust a hard-coded count over that file.

Before reporting catalog status, read
`../docs/contribute/examples-validation-runbook.md`. `validate_examples.py`
alone and ad-hoc pair scans are not full-catalog proof. Only collector output
with every row `COVERED` and no `UNRESOLVED`/`FAILED` cases supports that claim.
For trusted repository fixtures, preserve CI's explicit
`ABICHECK_TRUSTED_SOURCE_SMOKE_RUN=1` opt-in.

Read `README.md` in this directory first — it indexes every case and
explains the verdict taxonomy. Every case lives under `cases/<caseNN_name>/`
— `scripts/example_catalog.py`'s `case_dir()`/`CASES_DIR` is the one
resolver every consumer in the codebase routes through, so a case's
on-disk location never needs hand-rolling.

## Per-case layout

```
cases/caseNN_<short_name>/
├── v1.c|cpp + v1.h|hpp   # baseline source + headers
├── v2.c|cpp + v2.h|hpp   # changed source + headers
├── app.c|cpp             # runtime consumer that demonstrates the actual failure
├── README.md             # what breaks and why
└── (optional) CMakeLists.txt
```

Note: `v1`/`v2` are **filename prefixes**, not subdirectories. A few cases
deviate by design: BTF fixtures (e.g. `case121`) ship `v1.btf`/`v2.btf` +
a generator and no `app.*`; the 5 multi-library bundle cases
(`case84/90/91/92/93`) use a `gen_bundle.sh`-style generator to produce the
per-library binaries instead of a single `v1`/`v2` source pair.

### G20 audit / cross-source cases (143–151)

The ADR-035 G20 corpus demonstrates the **single-release audit** (one artifact,
no baseline) and **intra-version cross-source** machinery, which does not fit
the `v1`/`v2` binary-diff shape. Each ships a committed snapshot fixture instead
of a compilable pair:

```
cases/caseNN_<name>/
├── snapshot.abi.json   # committed AbiSnapshot — the fast-lane fixture
├── thin.abi.json       # (case151 only) a second, lower-evidence variant
└── README.md           # "sources combined" narrative + reproduce commands
```

`scripts/gen_g20_fixtures.py` is the single source of truth for the snapshot
content (hand-built `AbiSnapshot`s serialized to JSON); `tests/test_g20_catalog.py`
loads each fixture and asserts the case's canonical `expected_kinds` plus
`provider_assertions` (from `ground_truth.json`) via `run_crosschecks` — **no
compiler / castxml**, so the corpus runs in the default fast lane. The
`ground_truth.json` audit fields (`mode: audit`, canonical `expected_kinds`,
`provider_assertions`, `fixtures`) carry workflow assertions; `min_evidence` is
derived from the cross-check kinds, not hand-set.

Other build-emitted fixture types these cases may carry (ingested compiler-free
via the `merge` path): `abicheck_inputs/` (Flow-2 build-dropped facts pack),
`compile_commands.json` (L3 build context), `install_manifest.txt` (installed
public-header set), and `.abicheck.yml` (risk/cross-check config).

## README template (abicheck-first)

A per-case `README.md` should lead with **abicheck**, not a competing tool —
readers come here to learn what abicheck reports, and a reproduction using
`abidiff`/`abidw` alone (with no abicheck command anywhere in the page) was
a real recurring pattern in the older cases (a documentation review flagged
it; `case01`/`case02`/`case07` were rewritten as the reference examples).
Section order:

1. **Verdict and consumer impact** — what breaks for a real consumer, one
   paragraph.
2. **Old/new diff** — the minimal source change, usually a small table.
3. **abicheck command** — the actual `abicheck compare ...` invocation
   (built + run against the case's real fixtures, not paraphrased) at the
   evidence level the case actually needs — see `min_evidence` below;
   don't pass `-H`/headers if the ground-truth `min_evidence` is `L0`/`L1`.
4. **Expected abicheck finding** — the verdict, exit code, and the specific
   `ChangeKind`(s) abicheck reports, condensed from a real run (not the
   full report dump).
5. **Minimum evidence** — the case's `ground_truth.json` `min_evidence`
   value and a sentence on why that's the floor (what artifact/layer
   carries the fact abicheck needs).
6. **Why abicheck catches it** — one paragraph on the underlying mechanism
   (which parser/evidence layer surfaces the fact).
7. **Runtime failure demonstration** — the existing "build v1+app, swap in
   v2, observe the crash/corruption" demo.
8. **Safe redesign** — how to avoid the break, plus a real-world example if
   one is known.
9. **Cross-tool comparison** — `abidiff`/ABICC reproduction, kept as
   context *after* the abicheck-first sections above, not instead of them.

Not every case needs every section verbatim (e.g. a `COMPATIBLE_WITH_RISK`
case's "consumer impact" is a risk description, not a hard break) — keep
the order, adapt the content. This template isn't retroactively applied to
the whole catalog; existing cases migrate opportunistically, same as the
front-matter rollout in `docs/AGENTS.md`.

## Ground truth

The authoritative expected verdicts live in `ground_truth.json` at the
top of this directory. **If a per-case README disagrees with
`ground_truth.json`, `ground_truth.json` wins.**

`ground_truth.json` aligns with the 5-tier classification in
`abicheck/checker_policy.py`:
`BREAKING_KINDS` → `API_BREAK_KINDS` → `RISK_KINDS` → `QUALITY_KINDS`
→ `ADDITION_KINDS`.

## Taxonomy (rule / scenario / variant)

`taxonomy.json` at the top of this directory (generated by
`scripts/gen_catalog_taxonomy.py`, `--check` gates drift) is a sibling
*manifest* of `ground_truth.json` — not a key inside it — classifying each
case along axes orthogonal to implementation language: `entity` (`rule` vs.
`scenario`), `scenario_kind` (for a
scenario: `case-study`/`project-topology`/`capability` — never `audit`;
audit-ness doesn't determine `entity`, `operation` carries it instead,
below — a case can be `entity: rule` with `operation: audit` (case143-146,
case181) or `entity: scenario` with `operation: audit` (case147-151)), `operation`
(`compare` vs. `audit`), `ecosystem`, `topics`,
`languages`, `scope`, `artifact_shape`, `validation_owner`,
`related_rules`, `rule_slug`/`variant_of`/`relation_type`/
`relation_axis`, and `subjects`.

It was originally a `taxonomy` key inside `ground_truth.json`, sibling of
`verdicts`; it moved out into its own `taxonomy.json` file
(examples-catalog-split.md's "What is left" item 5) so a taxonomy-only edit
no longer changes `ground_truth.json`'s own bytes and therefore no longer
perturbs the whole-file digest `benchmark_comparison._ground_truth_digest()`
pins the frozen abidiff/ABICC competitor-result cache
(`scripts/frozen_competitor_results.json`) to. `scripts/example_catalog.py`'s
`load_taxonomy()`/`TAXONOMY_PATH` is the resolver every consumer routes
through, mirroring `load_ground_truth()`/`GROUND_TRUTH_PATH` for
`ground_truth.json` itself.

`entity`/`scenario_kind`/`ecosystem` are declarative, not derived from a
heuristic: [`catalog_classification.yaml`](catalog_classification.yaml)
carries one explicit entry per case (`scripts/catalog_classification.py`
loads and validates it), and a case missing from it fails
`gen_catalog_taxonomy.py` outright rather than silently defaulting to
`entity: rule`, `ecosystem: generic`.

**Every rule slug — a `rule_slug` or a `related_rules` entry — must resolve
to an entry in [`catalog_rules.yaml`](catalog_rules.yaml)**, the canonical
rule registry: one hand-authored title and definition per rule, all 188 of
them. `scripts/catalog_rule_registry.py` joins it against the taxonomy to
derive each rule's canonical case, variants, duplicates, composing
scenarios and demonstrated-vs-referenced-only status (all *derived*, never
restated in the YAML, so they cannot go stale). Both directions are
enforced — a slug with no definition, and a definition no case uses — by
`gen_catalog_taxonomy.py` and `tests/test_catalog_rule_registry.py`. Before
the registry these were unvalidated free-text strings that
`docs/contribute/catalog-coverage.md` counted as distinct compatibility
rules, so a typo or a synonym silently became one more "rule". See
[`docs/contribute/plans/examples-catalog-split.md`](../docs/contribute/plans/examples-catalog-split.md)
for the full rationale and remaining phases.

**A restatement of the same rule under a genuinely different condition is a
*variant*; the same demonstration restated with no distinguishing condition
is a *duplicate* — the two are not the same claim.** Every `rule`-entity
case carries a `rule_slug`: a mechanically derived, ecosystem-neutral name
by default, or a hand-reviewed shared slug when a genuine duplicate/variant
was found. Seven pairs share a slug so far — two duplicates, five
variants (`relation_type`/`relation_axis` records which; see
`scripts/gen_catalog_taxonomy.py`'s `RULE_FAMILIES` for the full read
behind each):

| Rule | Canonical | Relation | Other case |
|---|---|---|---|
| `exported-function-removed` | case01_symbol_removal | duplicate | case12_function_removed |
| `enum-member-value-changed` | case08_enum_value_change | variant (public-surface) | case20_enum_member_value_changed |
| `embedded-type-size-increased` | case07_struct_layout | variant (language) | case14_cpp_class_size |
| `inline-function-outlined` | case16_inline_to_non_inline | variant (callable-kind) | case47_inline_to_outlined |
| `executable-stack-flag-changed` | case49_executable_stack | duplicate | case136_executable_stack_removed |
| `symbol-version-node-removed` | case65_symbol_version_removed | variant (symbol-versioning) | case139_symbol_version_node_removed |
| `public-api-gains-internal-dependency` | case160_public_api_internal_dep_added | variant (specialization) | case190_public_inline_function_references_internal_constant |

None of these pairs was deleted or merged — every case remains an
independent, individually-gated calibration fixture; only the taxonomy
records that a pair encodes one rule, not two, and whether the second case
adds real robustness coverage (`variant`) or just restates the first
(`duplicate` — a candidate for eventual removal, not further "robustness"
credit; `docs/contribute/catalog-coverage.md`'s Rule coverage section
reports the two counts separately for exactly this reason).
**Sharing a `ChangeKind` is not the same as being a duplicate or variant**:
several clusters that share `expected_kinds` were reviewed and deliberately
*not* merged because they demonstrate different mechanisms or reach a
different verdict (e.g. case183_internal_version_node_churn shares
`symbol_version_node_removed` with the pair above but its
private-node-naming convention downgrades the verdict to
`COMPATIBLE_WITH_RISK`, so it stays its own rule) — see the `RULE_FAMILIES`
docstring for the full list of reviewed-and-rejected clusters. Don't
delete a case to "deduplicate" it without checking
`variant_of`/`relation_type`/`related_rules` first and updating every
consumer that counts cases.

## Subjects (reader-facing patterns)

`subjects` (examples-catalog-split.md "What is left" item 2) is the one
taxonomy field that is NOT a projection of `topics`/`rule_slug`/`ecosystem`
— those are all mechanical derivations from an existing field (the
change-catalog's detector-owner split, a mechanically-derived case-name
slug, a per-case classification entry). `subjects` is genuinely new,
hand-authored, reader-facing classification: which compatibility *pattern*
a maintainer would actually search for, independent of which detector or
`ChangeKind` produces it — e.g. `leaked-internal-types` groups
case74/75/76/77's four different C++ embedding mechanisms (inheritance,
by-value member, shared vtable, class template) under one lesson, something
no `topics` value could express (they'd scatter across `types`/`source`
depending on evidence tier).

[`catalog_subjects.yaml`](catalog_subjects.yaml) is the manifest — 25
subjects, one entry per subject (not per case, since a case may genuinely
belong to more than one — see `case181_xcheck_public_to_internal_dependency`,
both an `export-declaration-mismatches` audit case and an
`internal-dependency-reachability` case), each naming its member `cases`
and carrying a `title`/`blurb`, plus an optional hand-authored
`pattern_summary` for a subject with a rich enough shared mechanism to
warrant real prose (currently `leaked-internal-types` and
`internal-dependency-reachability`). `scripts/catalog_subjects.py` loads and
validates it — every case must appear in at least one subject's `cases`
list (a case with no subject is a hard error, the same anti-silent-default
discipline `catalog_classification.py` established for `entity`/
`ecosystem`), and a subject naming a stale/removed case is dead
configuration. `scripts/gen_examples_docs.py` publishes one
`docs/reference/examples/by-subject/<slug>.md` page per subject (rendering
`pattern_summary` verbatim when set) plus an index, and every case page's
meta table gains a **Subject** row linking to it.

## What NOT to do

- Don't modify a case's source or expected verdict without understanding
  what failure mode it encodes — these are calibration fixtures.
- Don't add a new case without:
  1. A per-case `README.md`.
  2. An entry in `ground_truth.json`.
  3. Regenerating `docs/reference/examples/` via `scripts/gen_examples_docs.py`.
- Don't rely on `catalog/cases/<case>/README.md` alone — always cross-check
  against `ground_truth.json`.

## Adding a new case

1. Pick the next free `caseNN` number.
2. Write `v1/`, `v2/`, `app.c|cpp`, and a README under `cases/caseNN_<name>/`.
3. Add the expected verdict to `ground_truth.json`.
4. Add an entry for the new case to `catalog_classification.yaml`, under
   `rules` (a plain rule case) or `scenarios` (an ecosystem case study,
   bundle, or capability demonstration, with its `scenario_kind`/
   `ecosystem`). This is required, not optional: `gen_catalog_taxonomy.py`
   fails outright on a case missing from that manifest rather than
   defaulting it to a generic rule. Then run
   `python scripts/gen_catalog_taxonomy.py`. If it reports an unknown rule
   slug, add the rule to `catalog_rules.yaml` with a title and a
   one-sentence definition (or fix the spelling).
   Also add the new case to at least one subject's `cases` list in
   `catalog_subjects.yaml` — `gen_catalog_taxonomy.py` fails the same way on
   a case with no subject assignment. Run
   `python scripts/catalog_subjects.py` to validate the manifest alone.
5. Run `python scripts/gen_examples_docs.py` and commit the regenerated
   `docs/reference/examples/caseNN_*.md` **and** the refreshed `catalog/README.md`
   (its headline/distribution/case-index regions are generated from
   `ground_truth.json`; don't hand-edit them).
6. Run `python scripts/gen_catalog_coverage_report.py` and commit
   `docs/contribute/catalog-coverage.md`.
7. Validate with `pytest tests/test_abi_examples.py -k caseNN -m integration`.
