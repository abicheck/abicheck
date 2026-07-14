# CLAUDE.md — `examples/`

The catalog has multiple owner families: ordinary single-library pairs,
multi-library bundles, G20 audit/cross-source fixtures, L3/L4/L5 fixtures, BTF,
Python API, reconcile, snapshot-pair, KABI, and other specialized cases. The
authoritative count is the number of entries in `ground_truth.json` — never
trust a hard-coded count over that file.

Before reporting catalog status, read
`../docs/development/examples-validation-runbook.md`. `validate_examples.py`
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
loads each fixture and asserts the case's `expected_crosscheck_kinds` /
`expected_providers` (from `ground_truth.json`) via `run_crosschecks` — **no
compiler / castxml**, so the corpus runs in the default fast lane. The
`ground_truth.json` v4 fields (`mode: audit`, `expected_crosscheck_kinds`,
`expected_providers`, `fixtures`) carry these expectations; `min_evidence` is
derived from the cross-check kinds, not hand-set.

Other build-emitted fixture types these cases may carry (ingested compiler-free
via the `merge` path): `abicheck_inputs/` (Flow-2 build-dropped facts pack),
`compile_commands.json` (L3 build context), `install_manifest.txt` (installed
public-header set), and `.abicheck.yml` (risk/cross-check config).

## Ground truth

The authoritative expected verdicts live in `ground_truth.json` at the
top of this directory. **If a per-case README disagrees with
`ground_truth.json`, `ground_truth.json` wins.**

`ground_truth.json` aligns with the 5-tier classification in
`abicheck/checker_policy.py`:
`BREAKING_KINDS` → `API_BREAK_KINDS` → `RISK_KINDS` → `QUALITY_KINDS`
→ `ADDITION_KINDS`.

## What NOT to do

- Don't modify a case's source or expected verdict without understanding
  what failure mode it encodes — these are calibration fixtures.
- Don't add a new case without:
  1. A per-case `README.md`.
  2. An entry in `ground_truth.json`.
  3. Regenerating `docs/examples/` via `scripts/gen_examples_docs.py`.
- Don't rely on `examples/<case>/README.md` alone — always cross-check
  against `ground_truth.json`.

## Adding a new case

1. Pick the next free `caseNN` number.
2. Write `v1/`, `v2/`, `app.c|cpp`, and a README.
3. Add the expected verdict to `ground_truth.json`.
4. Run `python scripts/gen_examples_docs.py` and commit the regenerated
   `docs/examples/caseNN_*.md` **and** the refreshed `README.md` catalog
   (its headline/distribution/case-index regions are generated from
   `ground_truth.json`; don't hand-edit them).
5. Validate with `pytest tests/test_abi_examples.py -k caseNN -m integration`.
