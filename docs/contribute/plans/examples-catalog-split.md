# Examples/catalog split — taxonomy first, no directory move yet

**Effort:** XL (six phases) · **Status:** Phase 1 implemented + a worked Phase 2 slice; Phases 3-6 not started.

## Problem

`examples/` is simultaneously the user-facing "how do I use abicheck"
catalog and the calibration corpus of 197 `caseNN_*` fixtures the FP-rate,
tier-accuracy, mutation, and full-catalog-coverage gates all run against
(`examples/CLAUDE.md`). Those are different audiences with different
quality contracts, and even within the 197-case corpus, "case" hides at
least four different entities:

- a **rule** — an atomic, ecosystem-neutral compatibility proposition
  ("removing an exported function breaks existing binary consumers");
- a **variant** — the same rule demonstrated under a different condition
  (language, evidence tier, public-surface reachability);
- a **scenario** — several rules composed into a realistic problem: an
  ecosystem case study (oneTBB/SYCL/oneMKL/Linux-kernel), a multi-library
  project topology (the bundle cases), or a capability/evidence
  demonstration (the G20 audit/cross-source cases);
- a **fixture** — implementation-only proof data backing a rule or
  scenario, not itself a user-facing example.

Treating all 197 as interchangeable "examples" both weakens the user
learning path (a C++ oneTBB case study reads the same as an atomic C rule)
and inflates coverage claims (three demonstrations of the same rule under
three languages count as three ABI concepts, not one rule with three
variants). Concretely: `case01_symbol_removal` and `case12_function_removed`
are the same rule (a plain exported-function removal) with no real
distinguishing variant; `case08_enum_value_change` and
`case20_enum_member_value_changed` are the same rule, but case20's
public-header-not-reachable-from-an-exported-signature condition is a
genuine variant (it exercises public-surface scoping) worth keeping
separate.

## Non-goals for this change

Per the phased migration below, this change is deliberately **Phase 1 (full)
plus a small Phase 2 slice** — additive metadata only:

- No `examples/case*/` directory is moved, renamed, or deleted.
- No case is removed from `ground_truth.json["verdicts"]` or from any gate
  that counts cases.
- `examples/` is not yet split into a curated user-facing tree and a
  separate `catalog/` tree (Phases 4-5).

## What Phase 1 implements

`scripts/gen_catalog_taxonomy.py` generates a `taxonomy` object in
`examples/ground_truth.json` — a sibling of the existing `verdicts` object,
never merged into it, so every existing consumer of `verdicts` is
unaffected. Each of the 197 entries carries:

| Field | Meaning |
|---|---|
| `entity` | `rule` or `scenario` |
| `scenario_kind` | (scenarios only) `case-study`, `project-topology`, `capability`, or `audit` |
| `ecosystem` | `generic`, `onetbb`, `sycl`, `onemkl`, or `linux-kernel` |
| `topics` | derived from `expected_kinds` via the existing `abicheck/model/change_catalog/{symbols,types,platform,build,source}.py` split (AGENTS.md's own "Adding a new ChangeKind" categorization) — reused rather than re-invented; `controls` for a `NO_CHANGE` case with no kinds to derive from; `audit` added for the four G20 audit rules |
| `languages` | derived from which source-file extensions the case's own fixtures ship |
| `scope` | `single-library` or `multi-library` (the five bundle cases) |
| `artifact_shape` | `compiled-pair`, `snapshot-pair`, `snapshot-audit`, `stub-pair`, `btf-pair`, `kabi-pair`, `fixture-pair`, or `bundle` — derived from ground_truth.json's own `fixtures:`/`mode:` fields when a case declares them (authoritative over any file-scan heuristic; `snapshot-audit` is a single-release G20 scan, `snapshot-pair` an old/new comparison), else from what the case directory actually ships |
| `validation_owner` | which runner family exercises the case (mirrors `examples/CLAUDE.md`'s "owner families" list) |
| `related_rules` | rule slugs a scenario composes — populated only for the scenarios the design discussion named explicitly (see the module docstring's "Known gaps") |
| `rule_slug` / `variant_of` | the Phase 2 worked example below |

Entity/scenario_kind/ecosystem classification for the scenario families
(bundles, G20 audit, oneTBB/SYCL/oneMKL/Linux-kernel case studies) is by
explicit case-number lookup table in the generator, not a heuristic — every
other case defaults to `entity: rule`, `ecosystem: generic`.

Run `python scripts/gen_catalog_taxonomy.py` to regenerate; `--check` gates
drift the same way every other `gen_*.py` generator in this repo does.

## What the Phase 2 slice implements

A worked, non-destructive consolidation of the two duplicate/near-duplicate
pairs named above, via `rule_slug`/`variant_of` rather than deleting either
case (these are calibration fixtures — `examples/CLAUDE.md`'s "don't modify
a case's source or expected verdict without understanding what failure mode
it encodes"):

- `exported-function-removed`: `case01_symbol_removal` (canonical) ←
  `case12_function_removed` (variant).
- `enum-member-value-changed`: `case08_enum_value_change` (canonical) ←
  `case20_enum_member_value_changed` (variant — public-header enum not
  reachable from an exported signature, exercising public-surface scoping).

Each variant case's README gained a short "Related rule" cross-reference to
its canonical case (see `examples/case12_function_removed/README.md`,
`examples/case20_enum_member_value_changed/README.md`).

The remaining 195 cases' `rule_slug`/`variant_of` are `null` — this slice
does not attempt full rule-family classification of the whole catalog; see
"Known gaps" below.

## Known gaps / remaining phases

Not attempted in this change (from the original six-phase migration):

- **Phase 2, remainder** — review the rest of the catalog for further
  true duplicates vs. legitimate variants, and populate `rule_slug`/
  `variant_of` catalog-wide. `related_rules` on scenario entities is
  similarly incomplete.
- **Phase 3** — make every path resolver in the codebase
  (`benchmark_comparison.py`, `gen_examples_docs.py`,
  `check_ai_readiness.py`, the various validators) go through a declarative
  `catalog.resolve(case_id)` rather than a hard-coded `EXAMPLES_DIR /
  case_name`, so Phase 4 doesn't require touching every consumer at once.
- **Phase 4** — the physical directory split (`catalog/rules/`,
  `catalog/patterns/`, `catalog/case-studies/`, `catalog/capabilities/`),
  with redirects for existing doc URLs.
- **Phase 5** — rebuild `examples/` as a small, curated, task-oriented set
  (compare one library, audit a release, multi-library project, evidence
  depth, build/source evidence, Python API, suppressions, GitHub Actions).
- **Phase 6** — split benchmark/coverage reporting into separate rule,
  variant, scenario, ecosystem, and workflow dimensions instead of one flat
  case count, per this plan's "stop reporting all cases as semantically
  equal" motivation above.

## Files & surfaces

- `scripts/gen_catalog_taxonomy.py` (new) — the taxonomy generator.
- `examples/ground_truth.json` — new `taxonomy` top-level key.
- `examples/case12_function_removed/README.md`,
  `examples/case20_enum_member_value_changed/README.md` — added "Related
  rule" cross-references.
- `examples/CLAUDE.md` — documents the new taxonomy block and the
  rule/variant relationship.
- `scripts/CLAUDE.md` — inventory row for the new script.

## Tests

`tests/test_catalog_taxonomy.py` mirrors `gen_platform_matrix.py`/
`test_platform_matrix.py`'s pattern: it re-derives the taxonomy from
`gen_catalog_taxonomy.build_taxonomy()` and asserts it matches the
committed block, so drift fails the ordinary fast pytest lane rather than
only a `--check` someone has to remember to run. It also pins the
classification invariants directly — `scenario_kind` is set only for
`entity == "scenario"`, every `variant_of` names a real case whose own
`rule_slug` matches and which is not itself a variant, and `related_rules`
entries are non-empty strings. `scripts/gen_catalog_taxonomy.py --check` is
the equivalent manual/CI drift gate; run either after any
`ground_truth.json`/case change.

## Out of scope

Everything under "Known gaps" above.
