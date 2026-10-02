# Defect-family harnesses: generalizing the September 2026 fix history

**Status:** In progress. Landed: the family layer of the registry
(`tests/regressions/families.py`, enforced by
`tests/test_regressions_families.py`) and harnesses H1, H2, H3 and H5
(`tests/test_family_f{1,2,3,5}_*.py`). Second round: H4, H6 and H7 landed too. Not landed: the merge-quiescence
gate (a repository setting, not code). The implementation-side follow-up — making these families
unrepresentable rather than only detected — is sequenced in
[Design hardening from defect families](design-hardening-from-defect-families.md). Extends
[bug-class regression testing](bug-class-regression-testing.md) (its Phases
0–9 and `tests/regressions/manifest*.py` stay as they are).

## Problem

**Evidence base.** Between 2026-08-30 and 2026-09-30, 456 PRs merged. 166 of
them are fixes, reverts or regressions, and about 140 of those PR bodies were
read for this analysis. No GitHub issues were filed in the window, so every
defect report is recorded in a PR body.

**Where the bugs were found:**

| Source | Finds | Examples |
|---|---|---|
| Real-library validation runs | Almost every high-impact false positive | #1411 (oneCCL/oneDNN), #1283, #1314, #1321, #1330, #1406 (MKL), #1268, #1308, #1324, #1350 (SVS), #1165, #1176, #1361, #1383 (oneDAL), #985, #1204, #1228 (oneTBB), #1280 (six-member bundle) |
| Codex/CodeRabbit review after merge | The follow-up chains | — |
| Windows/macOS CI | A steady stream | — |
| Mutation lane | One bug | #1396 |

**The pattern that repeats.** The registry follows each fix faithfully, but it
does not stop the next bug in the same family:

- **The registry keeps growing.** `tests/regressions/` now holds **129**
  `BugClass` entries, and about 107 of them carry an open `known_gaps` entry.
  Most new fixes add a new, narrowly named class. Many of those classes are
  instances of the same few mechanisms. For example, all of the following say
  "an unread or unknown input was read as a definite value":
  - `evidence.unread_producer_read_as_confirmed_absence`
  - `evidence.container_presence_read_as_evidence_content`
  - `evidence.silent_degradation_to_clean_verdict`
  - `status.container_existence_taken_for_completed_work`
  - `report.unobserved_population_counted_as_observed`
  - `report.unestablished_result_reads_as_success`
- **Sibling chains.** The same mechanism is fixed again and again, one site at
  a time:
  - A1→A5: #1384 → #1385 → #1387 → #1388/#1398 → #1389
  - Fact collapse: #1033 → #1039 → #1046 → #1049 → #1075 → #1091 → #1213
  - Raw comparison across a producer-capability gap: `is_restrict`, then
    `is_va_list`, then #1200
  - Mach-O identity: #1140 → #1156 → #1167, with 24 recurrences across
    review rounds
  - Declaration kind forwarded unfiltered: #1001 → #1371
  - Checkout-path leak: #1343 → #1355
- **Merged one commit short.** About 25 chains in the month follow the same
  shape: a PR merged while review findings were still arriving, and a
  follow-up PR carried the rest. Several of those follow-ups fixed a
  regression the merge itself shipped (#1165→#1168, #1288→#1290,
  #945→#947, #957→#958, #1235→#1249/#1254).

**The conclusion is not "write more tests per fix".** It is that a test
anchored to one call site is structurally unable to find the next site. What
does find it:

1. A harness that **enumerates every site mechanically** (every producer,
   every entry point, every front end).
2. That harness applies a **family-level oracle** to each site.
3. A new site joins the harness automatically, or fails a completeness check
   until it is added.

## The seven defect families

| Family | Invariant | Representative PRs | Existing classes it absorbs |
|---|---|---|---|
| **F1 Unknown ≠ value** | Removing, failing or truncating any evidence input never makes the result *cleaner*, never adds a BREAKING finding, and never raises assurance. | #1384–#1398, #1033–#1075, #1091, #1200, #1209, #1268, #1277, #1324, #1248 | `evidence.unread_producer_*`, `silent_degradation_*`, `container_presence_*`, `status.container_existence_*`, `report.unobserved_*`, `report.unestablished_*`, Fact collapse, producer-capability raw comparison |
| **F2 Route parity** | The same semantic request gives the same normalized report whichever route it takes: CLI, typed API, Action, dry run, stored vs live operand, scalar vs one-member release vs N-member release, and every snapshot entry point (dump, compat, appcompat, header-only). | #1391, #1393, #1321, #1258/#1264, #1172, #1233, #1089/#977/#1013, #1236, #1176, #1326 | `cardinality.*`, `config.front_end_default_divergence`, `config.propagation_completeness`, `config.option_dropped_at_a_dispatch_branch`, `evidence.entry_point_skips_extraction_record`, `evidence.stored_snapshot_rederivation`, `report.finding_entry_builder_parity`, `report.scalar_release_projection_drift`, `cli_surface.capability_guard_diverged_*` |
| **F3 Identity is semantic** | Identity keys are invariant under the environment: checkout relocation, symlinks, path separators, hash seed, member order, platform decoration (Mach-O `_`, x86 stdcall/fastcall, C1/C2/D0 variants). Semantically distinct entities stay distinct. | #1343/#1355, #1383, #1330, #1359, #1367, #1370, #1369, #1380, #1392, #1140–#1167, #1148–#1155, #1204 | `identity.*`, `matching.dedup_key_soundness`, `evidence.backfill_bare_name_match`, `classification.one_spelling_of_a_path_*`, `comparability.incidental_ordering_*` |
| **F4 Structure over spelling** | No finding's severity rests only on a name, suffix or directory heuristic. Every heuristic has a named structural fact that confirms or vetoes it. | #1411, #1231, #1316, #1308, #1344, #1218 | `classification.name_shape_*`, `evidence.spelling_used_as_a_semantic_model`, `classification.declaration_existence_as_export_obligation` |
| **F5 Optimization ≡ reference** | Every cache, memo, fast path, narrowing and parallel fan-out produces output byte-identical to the unoptimized, serial run. | #1336, #1361, #1340, #1357, #1331, #1306, #1245, #1371 | `perf.*` (about 20 classes), `concurrency.*`, `cache.*` |
| **F6 Compatible pair ⇒ no break** | On a real, known-compatible release pair the tool reports no BREAKING or API_BREAK finding. On a known-incompatible pair it reports the documented break. | Every VAL row above | New corpus gate |
| **F7 Test/harness integrity** | Every test proves that the path it claims actually ran. Oracles are independent of the implementation. Fixtures cannot fabricate states the type system forbids. | #1243, #1287, #1318, #1296–#1299, #1396, #1395 | `guard.*`, `tests.*`, `test_harness.*`, `test_infra.*`, `test_double.*` |

## Design: one harness per family, with mechanical site enumeration

Each harness has three parts.

- **Site inventory.** It is derived from the code, never hand-listed. The
  sources are:
  - the detector registry and the `@registry.detector` producers
  - the `surface_fact_producers` and `Fact` fields
  - Click introspection of `compare`/`dump`/`deps`
  - the `CompareRequest`/`DumpRequest` dataclass fields
  - the Action option table
  - the functions that call `AbiSnapshot(...)`
  - the functions decorated as caches

  An inventory entry the harness cannot exercise must be listed in an
  explicit `UNCOVERED` table with a reason. That table only ever shrinks.
  This is the same allowlist-and-shrink discipline as
  `IMPORT_CYCLE_ALLOWLIST`.
- **Transformation generator.** It is family-specific (see below).
- **Family oracle.** It is a monotonicity or equivalence relation. It is never
  the expected output restated.

### H1: evidence-ablation harness (F1)

For each fixture in a corpus of about 30, covering ELF/PE/Mach-O and
L0–L5 fixture pairs, and for each evidence producer in the inventory, apply
each ablation to OLD, NEW and both sides. The ablations are:

- missing
- raises
- returns empty
- truncated or short decode
- producer marked FAILED

The oracles, checked on every combination:

- `verdict_rank(ablated) >= verdict_rank(full)` in the "less certain" order.
  An ablation may lower confidence, but it never turns an established break
  into COMPATIBLE through silence, and it never invents one.
- The set of BREAKING findings under ablation is a subset of the full set.
  No exception: an evidence gap is reported as lower confidence or a coverage
  note, never as a new BREAKING finding. (`Change` has no `evidence_gap`
  marker, and H1 does not introduce one.)
- `assurance(ablated) <= assurance(full)`, and the report states the gap: a
  coverage failure, a `FAILED` fact or a `degraded` marker.

The completeness check is that every new `Fact` field or producer must appear
in the inventory. That is what would have prevented the A1–A5 chain and the
Fact-collapse series.

### H2: route-parity matrix (F2)

This harness uses one semantic request and every route that can express it:

- `compare` CLI
- `run_compare_request`
- `--dry-run` plan vs execution
- stored snapshot vs live binary
- scalar vs directory with one member vs directory with N members (the
  member is projected out)
- the Action's argv builder, run as the real script
- every snapshot entry point (dump, compat, appcompat, header-only)

The oracle is that the reports are equal after the documented, route-specific
fields have been dropped. The list of those droppable fields is the only
place where divergence may be declared.

The inventory comes from Click params ∪ request fields ∪ Action inputs. A
parameter that is not routed through the matrix fails the completeness test
until it is added, or declared route-specific with a reason. Two properties
fall out of this directly: a scalar field that a release member drops
(#1391) is a failing cell, and so is a front-end default that differs
between routes (#1258).

### H3: environment-metamorphic identity suite (F3)

This generalizes Phase 4 of the existing plan from "checkout relocation" to
a single transform catalogue:

- relocate the checkout or symlink the root
- change `PYTHONHASHSEED`
- reverse the member order and the header order
- change the path separator
- apply a platform decoration round trip, where each decoration scheme is an
  explicit codec with a property test for `decode(encode(x)) == x` and
  for `x != y` implying `encode(x) != encode(y)`

The oracle is snapshot identity equality plus a `NO_CHANGE` verdict. The
negative controls come from the known-gaps counterexamples: pairs that must
stay distinct.

### H4: heuristic registry (F4)

Every name- or spelling-based classifier is registered with three pieces:

- its structural confirmation fact
- its false-positive corpus
- its false-negative corpus

An AST gate fails when a detector decides a verdict from a regex or suffix
match without going through a registered heuristic. #1411's enum-sentinel,
`_tag` and experimental-namespace rules would each have needed this
registration, and so would its FP corpus from oneCCL/oneDNN.

### H5: optimizations-off differential (F5)

Every cache, memo, streaming path and fast path gets a single, centrally
honoured kill switch: `ABICHECK_REFERENCE_MODE=1` disables all of them and
forces serial execution. A scheduled lane runs the example catalog and the
F6 corpus twice, once in reference mode and once in default mode (and once
more with `ABICHECK_MAX_THREADS=8`), and diffs the canonical JSON.

The inventory is every `functools.cache`/`lru_cache` use, every module-level
dict cache and every `ThreadPool` site. It is enforced by an AST scan: a
cache that does not honour reference mode fails the scan.

Per AGENTS.md's "differential test must prove both configurations ran" rule,
each run records how often every switch was engaged.

**Status (design-hardening plan, Phase 4):** the switch is now production
code. Every cache goes through `abicheck/model/execution_cache.py`, which
honours `ABICHECK_REFERENCE_MODE=1` and counts each bypass in a registry the
harness reads; the earlier test-side per-site bypass is gone. The inventory
scan finds wrapper sites (`memoized`, `MemoryCache`, `ScopedCache`, ...),
and `tests/test_module_cache_gate.py` rejects a cache that bypasses the
wrapper. The scheduled lane is `.github/workflows/reference-mode.yml`.

### H6: real-library compatibility corpus as a CI lane (F6)

Validation on real libraries found the most expensive bugs, but it runs by
hand and its lessons land as small fixture repros. The proposal:

- Promote `skills-src/evaluation/validation/` into a scheduled lane (weekly,
  plus a PR label).
- Use curated pairs with ground truth: compatible patch releases of oneTBB,
  oneDNN, oneCCL, MKL, oneDAL, SVS and pvxs, taken from conda-forge.
- Set the budget to **zero BREAKING on known-compatible pairs**. Store a
  per-pair baseline of non-breaking finding counts so that drift is visible
  (for example, 500 `exported_not_public` findings appearing on one run).
- The run's canonical JSON and memory trace are artifacts, so a failure is
  reproducible without re-running the validation.

This is the only family whose oracle is external truth rather than
self-consistency. That is exactly why it caught #1283 (the ground truth was
one added symbol, and the tool reported breaks).

### H7: test-integrity gates (F7)

These gates reuse the existing mutation lane, pointed at the harnesses
themselves:

- Each harness must kill a documented set of known-bad mutants, one per
  historical bug in its family. The mutation is reapplied as a patch in CI
  (`tests/regressions/mutants/<family>/*.patch`).
- A harness that stops killing its seed mutants is broken, whatever its pass
  rate.

Historical bugs make the best mutants because they are proven to be
realistic.

## Registry change: classes attach to a family

- Add `family: Literal["F1", …, "F7", "other"]` to `BugClass`.
- A new class in F1–F5 must name the harness cell (inventory entry plus
  transform) that now covers it, *and* ship its historical bug as an H7
  mutant.
- Only an `other` class may rely solely on its own seed test. The PR must say
  why no family fits.
- `tests/test_regressions_manifest.py` reports the per-family count. A
  growing `other` bucket is the signal that a new family is needed.

## Process change: merge only on a quiesced review

About 25 of the month's follow-up chains are "merged before the last review
round's findings were pushed". Proposed gate:

- Merging requires the review bots' latest run to be on the **head SHA**,
  with no unresolved red findings.
- A PR labelled `touches:paths|shell|platform` must pass the Windows and
  macOS smoke lanes before merge, not after. This covers the recurring
  Windows/MSYS, UTF-8 and `mktemp` failures, and #1197 was the fifth
  recurrence.

## Phasing (by expected catch value)

1. **H1, evidence ablation.** F1 is the largest family and the one with the
   longest sibling chains.
2. **H2, route parity.** Second largest family. Several cells already exist
   as ad-hoc tests that can be folded in.
3. **H6, the real-library lane.** It has the highest-severity catches and the
   infrastructure is mostly present.
4. **H5, reference mode.** The perf work is ongoing, so the class keeps
   growing.
5. H3 (extends the existing plan's Phase 4), H4, H7, and the registry
   `family` field.
6. The merge-quiescence gate. This is a policy change, independent of the
   harness work.

## Acceptance

For each harness, replaying its family's historical fixes from this month in
reverse (by reverting the fix) must make the harness fail **without** any
test named after that fix. That is the definition of "generalized".

## Implementation record (first round)

- **Registry families.** Every registered `BugClass` belongs to exactly one
  family. The integrity test rejects a class with no family and a family entry
  whose class no longer exists.
  - The rule that a new F1/F2/F3/F5 class must list its family harness in
    `seed_tests` holds for every class registered after this change. Classes
    that already existed are listed in `families_legacy.py`, and that list may
    only shrink.
  - The `OTHER` bucket has a budget. Growing past it needs review.
  - Each harness must carry at least two seeded-mutant tests.
- **H1, evidence ablation.** The site inventory is built by introspecting the
  model: 59 `Fact[...]` fields and 18 snapshot evidence containers.
  - Each seeded historical mutant is caught: the pre-#1033 Fact collapse and
    the pre-#1384 reading of an unknown export as absent.
  - **Real bug found:** when a header-origin type's `source_header_fact` is
    unknown on both sides, the public-surface closure seed silently drops a
    real break to NO_CHANGE. Recorded as a strict xfail.
- **H2, route parity.** Covers CLI, typed API, a one-member release, and
  stored vs live operands.
  - It checks all 47 `compare` Click parameters and all 37 `CompareRequest`
    fields against a routing table. Every parameter and field needs an entry,
    and the table may not name one that no longer exists.
  - **Real divergences found:**
    - The `pattern_verdicts` default differs between front ends.
    - `*_evidence_depth` is set only by the CLI.
    - `suppression_audit` is set only by the CLI.
    - The `effective_config_digest` tier depends on the route.
- **H3, identity transforms.** The transform catalogue covers path and
  hash-seed variations, plus codecs for Mach-O, PE and Itanium special-member
  names.
  - Of the identity functions found by scanning the tree, 23 are covered and
    22 are in `UNCOVERED` with a reason.
  - **Real bugs found:**
    - A checkout path containing a space leaks into canonical identity (the
      anonymous-type location regex uses `\S+`).
    - PE vectorcall decoding strips a leading `_`, so two distinct names map
      to one identity.
- **H5, optimization equals reference.** The site inventory covers 67
  cache and pool sites.
  - The cells compare memo-bypass vs default, 1 thread vs 8, a cold vs warm
    disk cache on separate roots, and a warm vs fresh run. Each cell asserts
    that the optimization actually engaged.
  - Three historical mutants are caught. No current bug was found.

## Implementation record (second round)

- **H4, heuristic registry** (`tests/test_family_f4_heuristics.py`). An AST
  scan finds 205 name- or spelling-based decision sites. 12 have covered
  cells (enum sentinel, `_tag`, experimental promotion, internal
  namespaces). The other 193 are listed with a category, and each category
  has a ceiling that may only shrink. 4 seeded mutants are caught.
  - **Real bug:** the enum-sentinel rule is name-only, so an `E_MAX` member
    that is neither last nor largest is demoted to
    `enum_last_member_value_changed`.
  - **Real bug:** a `detail::`/`impl::` record reached from an exported
    signature is dropped out of contract, while `priv::` with the same
    structure is BREAKING.
- **H6, real-library corpus** (`tests/test_family_f6_corpus.py`,
  `.github/workflows/real-library-corpus.yml`,
  `skills-src/evaluation/validation/scripts/run_compat_corpus.py`).
  - 11 conda-forge pairs, each with ground truth cited from
    `data/manifest.json`, and a recorded baseline.
  - The gate is offline-tested, including 3 mutants.
  - **First real run:** 5 of 9 known-compatible pairs report BREAKING:
    oneTBB 2 pairs, protobuf, zstd, libxml2. These are open for triage.
- **H7, mutant replay** (`tests/test_family_f7_mutant_replay.py`,
  `tests/regressions/mutants/`). 13 historical bugs are stored as source
  patches, and 10 are killed by their harness. The 3 survivors are strict
  xfails that expose two H5 gaps:
  - the disk-cache cell never varies exactly one key input;
  - `ABICHECK_MAX_THREADS=1` still takes the pooled release path, so the
    sequential path is never compared.
