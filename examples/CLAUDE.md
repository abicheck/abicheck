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

## `workflows/` — curated user-facing examples (Phase 5, in progress)

`examples/workflows/` is a **separate tree from the `caseNN_*` calibration
catalog** below — Phase 5 of the [examples/catalog split]
(../docs/contribute/plans/examples-catalog-split.md). Its entries are
small, complete, task-oriented projects a new user runs end-to-end
(`cd examples/workflows/<name>`, build, run one `abicheck` command, read
the output) — not calibration fixtures a gate scores. Consequently:

- **No `ground_truth.json` entry, no `caseNN` prefix, no taxonomy
  classification** — the "examples-ground-truth" AI-readiness check and
  every other gate that walks `case*` directories deliberately never sees
  this tree (it filters on the `case` name prefix).
- Verify every command and every excerpted output block against a real run
  before writing it down — see `compare-release/README.md` for the
  pattern (a real `gcc`+`abicheck compare` invocation, output excerpted,
  not paraphrased).
- Link out to the relevant `docs/use/*.md`/`docs/learn/*.md` page for
  anything beyond that one task — a workflow example teaches "how do I run
  this", not "how does this work" (that's the docs' job, see
  `docs/AGENTS.md`'s ownership split).

See the plan doc's Phase 5 row for the target set (compare one library
[done], audit a release, multi-library project, evidence depth,
build/source evidence, Python API, suppressions, GitHub Actions) and which
of them remain.

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
scenario: `case-study`/`project-topology`/`capability` — never `audit`;
audit-ness doesn't determine `entity`, `operation` carries it instead,
below — a case can be `entity: rule` with `operation: audit` (case143-146)
or `entity: scenario` with `operation: audit` (case147/148/151)), `operation`
(`compare` vs. `audit`), `ecosystem`, `topics`,
`languages`, `scope`, `artifact_shape`, `validation_owner`,
`related_rules`, and `rule_slug`/`variant_of`/`relation_type`/
`relation_axis`. See
[`docs/contribute/plans/examples-catalog-split.md`](../docs/contribute/plans/examples-catalog-split.md)
for the full rationale and remaining phases.

**A restatement of the same rule under a genuinely different condition is a
*variant*; the same demonstration restated with no distinguishing condition
is a *duplicate* — the two are not the same claim.** Every `rule`-entity
case carries a `rule_slug`: a mechanically derived, ecosystem-neutral name
by default, or a hand-reviewed shared slug when a genuine duplicate/variant
was found. Seven pairs share a slug so far — three duplicates, four
variants (`relation_type`/`relation_axis` records which; see
`scripts/gen_catalog_taxonomy.py`'s `RULE_FAMILIES` for the full read
behind each):

| Rule | Canonical | Relation | Other case |
|---|---|---|---|
| `exported-function-removed` | case01_symbol_removal | duplicate | case12_function_removed |
| `enum-member-value-changed` | case08_enum_value_change | variant (public-surface) | case20_enum_member_value_changed |
| `embedded-type-size-increased` | case07_struct_layout | variant (language) | case14_cpp_class_size |
| `inline-function-outlined` | case16_inline_to_non_inline | duplicate | case47_inline_to_outlined |
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
