# CLAUDE.md — `examples/`

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
explains the verdict taxonomy.

## Per-case layout

```
caseNN_<short_name>/
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
caseNN_<name>/
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

`ground_truth.json["taxonomy"]` (generated by
`scripts/gen_catalog_taxonomy.py`, `--check` gates drift) is a sibling of
`verdicts` classifying each case along axes orthogonal to implementation
language: `entity` (`rule` vs. `scenario`), `scenario_kind` (for a
scenario: `case-study`/`project-topology`/`capability`/`audit`),
`ecosystem`, `topics`, `languages`, `scope`, `artifact_shape`,
`validation_owner`, `related_rules`, and `rule_slug`/`variant_of`. See
[`docs/contribute/plans/examples-catalog-split.md`](../docs/contribute/plans/examples-catalog-split.md)
for the full rationale and remaining phases.

**A duplicate demonstration of the same rule is a *variant*, not a second
concept.** `case01_symbol_removal`/`case12_function_removed` (both a plain
exported-function removal) and `case08_enum_value_change`/
`case20_enum_member_value_changed` (the same enum-member-value-change rule,
case20 additionally exercising public-surface scoping) are the worked
example: `rule_slug` names the shared family, `variant_of` points a variant
back at its canonical case. Neither pair was deleted or merged — these
remain independent, individually-gated calibration fixtures; only the
taxonomy records that they encode one rule, not two. Don't delete a case to
"deduplicate" it without checking `variant_of`/`related_rules` first and
updating every consumer that counts cases.

## What NOT to do

- Don't modify a case's source or expected verdict without understanding
  what failure mode it encodes — these are calibration fixtures.
- Don't add a new case without:
  1. A per-case `README.md`.
  2. An entry in `ground_truth.json`.
  3. Regenerating `docs/reference/examples/` via `scripts/gen_examples_docs.py`.
- Don't rely on `examples/<case>/README.md` alone — always cross-check
  against `ground_truth.json`.

## Adding a new case

1. Pick the next free `caseNN` number.
2. Write `v1/`, `v2/`, `app.c|cpp`, and a README.
3. Add the expected verdict to `ground_truth.json`.
4. Run `python scripts/gen_examples_docs.py` and commit the regenerated
   `docs/reference/examples/caseNN_*.md` **and** the refreshed `README.md` catalog
   (its headline/distribution/case-index regions are generated from
   `ground_truth.json`; don't hand-edit them).
5. Validate with `pytest tests/test_abi_examples.py -k caseNN -m integration`.
