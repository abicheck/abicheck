# Comparison Performance

This page documents the runtime cost of comparing **real, large** shared
libraries, the bottlenecks that were found and fixed, and the tooling that
guards against regressions in CI.

## TL;DR

- **Dump scales fine.** Snapshotting `libonedal_core.so` (~10,550 exported
  functions) takes ~5 s.
- **Compare used to blow up.** On the same library, `compare` did **not finish
  within 60 s**. The cost was entirely in the *post-processing* detectors, not
  the core symbol diff. A profiling sweep found **six** super-linear paths —
  several quadratic, one effectively cubic — all now fixed (see
  [What was fixed](#what-was-fixed)).
- A synthetic scaling harness (`scripts/benchmark_scaling.py`) reproduces each
  path **without** a real binary, compiler, or castxml, and a `slow` regression
  test guards the realistic hot path.

## What was fixed

Every fix preserves detector behaviour (the full unit suite, the FP-rate gate,
and the metamorphic/oracle detector tests all stay green); they only change how
the work is organised.

| # | Path | Was | Fix | Result |
|---|------|-----|-----|--------|
| 1 | Public-surface scoping (`surface.classify_change_surface`) | Recomputed four old∪new set unions **per finding** → O(findings × surface). Made *every* comparison quadratic. | Compute the unions once per pass (`surface_unions`) and reuse. | `add_remove` 4000: **9.1 s → 0.32 s** (linear) |
| 2 | Namespace detection (`diff_namespaces`) | `demangle_batch` called **one symbol at a time** → one `c++filt` subprocess per symbol. | Batch-demangle each snapshot once (`_batch_demangle_public`) and thread the map through. | `elf_namespace` 4000: **5.2 s → 0.33 s** (linear) |
| 3 | Variable / symbol diffing | Quadratic via the same per-finding surface unions (#1). | Fixed by #1. | `var_churn` 4000: **2.1 s → 0.06 s** (linear) |
| 4 | Batch-rename heuristic (`diff_symbols._find_rename_pairs`) | O(removed × added) suffix scan. | Reversed-name index + binary search (`endswith` → reversed prefix lookup). | folded into `add_remove` win |
| 5 | Type-spelling fallback (`diff_type_spellings`) | Rebuilt a `set(...)` inside a comprehension → O(n²). | Hoist the set once. | folded into `add_remove` win |
| 6 | Affected-symbol enrichment / ancestor closure (`diff_filtering`) | Transitive ancestor function **lists** accumulated duplicates, then re-sorted per change → effectively cubic on nested type graphs. | Use sets (dedup on union); sort once. | `nested_types` n=200: **>60 s → 0.16 s** |
| 7 | ELF-only rename matching (`binary_fingerprint`, `diff_symbols._plausible_rename`) | O(removed × added) name-similarity scan; the name predicate re-demangled both names per pair. | Scan only the size-tolerance window via the existing size index; cache the per-name parse; cap the heuristic pass for mass-rename inputs. | `rename_churn` n=1000: **13.2 s → 2.1 s**, larger inputs bounded |
| 8 | Affected-symbol enrichment type↔function/field mapping (`diff_filtering._build_type_to_funcs`, `_build_type_embed_index`) | `any(tname in ft ...)` nested inside the type loop and the function/field loop → O(types × functions × refs); quadratic when many distinct types churn (a header refactor or versioned upgrade). The original perf sweep only sampled these scenarios at n=500, so the exponent was never computed and the table mislabelled them "linear". | One Aho-Corasick `_SubstringMatcher` over the affected type names, built once and shared; each ref/field is matched in O(len) with **identical** substring semantics. | `typedef_churn` n=4000: **6.3 s → 0.73 s**; `union_churn` **9.1 s → 1.10 s**; `vtable_churn` **7.8 s → 1.03 s**; `enum_churn` **≈1.8 → 1.0**; `opaque_filter` **≈1.7 → 1.2** (all now linear) |
| 9 | Opaque-handle pointer-only / factory check (`diff_filtering._is_pointer_only_type`, `_has_public_pointer_factory` via `_filter_opaque_size_changes`) | Each opaque candidate rescanned every public function/variable with a word-boundary regex → O(candidates × functions), a regex per pair (`type_churn` n=4000: ~3.2 M searches). | One indexed pass per snapshot (`_opaque_usage_index`): an Aho-Corasick prefilter narrows each type string to the candidates present, then the **same** regex oracle decides — so the verdict is unchanged (verified by a fuzz test vs the per-candidate functions). | `type_churn` n=4000: **1.13 s → 0.38 s** (≈1.6 → linear) |

With fixes #8 and #9 the compare pipeline has **no remaining quadratic path** at
the tracked sizes — every scenario is linear (tail exponent ≈1.0–1.3) except the
inherently deep `nested_types` chain. The opaque-handle pointer-only check
(`_is_pointer_only_type`) used to be O(candidates × functions) with a
word-boundary regex per pair (`type_churn` n=4000: ~3.2 M regex searches, ≈1.6);
fix #9 replaced the per-candidate rescan with one indexed pass.

## How to reproduce

No real binary, compiler, or castxml required — the harness synthesises
`AbiSnapshot` pairs that exercise each path:

```bash
# Sweep all scenarios and print a table with a scaling exponent per scenario.
python scripts/benchmark_scaling.py

# Focus one path and emit machine-readable JSON.
python scripts/benchmark_scaling.py --scenario type_churn \
    --sizes 1000 2000 4000 --json-out reports/perf/scaling.json
```

Scenarios (`add_remove` is the linear control; the rest target a specific
path). The first group exercises `compare()` (the original focus); the second
group, added later, extends coverage beyond `compare()` to the suppression and
reporting stages — see [Coverage beyond `compare()`](#coverage-beyond-compare):

| Scenario | Measures | Stresses |
|----------|----------|----------|
| `add_remove` | `compare()` | Core symbol diff + surface scoping (control) |
| `type_churn` | `compare()` | Affected-symbol enrichment, opaque filtering (structs) |
| `enum_churn` | `compare()` | Enum diffing (`diff_types._diff_enums`) |
| `typedef_churn` | `compare()` | Typedef base-change diffing (`_diff_typedefs`) |
| `union_churn` | `compare()` | Union member diffing |
| `wide_struct` | `compare()` | Per-field diffing within large records |
| `vtable_churn` | `compare()` | Vtable / virtual-layout diffing |
| `elf_namespace` | `compare()` | Namespace detection + demangling (stripped lib) |
| `pe_churn` | `compare()` | PE/COFF export diffing (`diff_platform` PE arm) |
| `macho_churn` | `compare()` | Mach-O export diffing (`diff_platform` Mach-O arm) |
| `var_churn` | `compare()` | Public-surface classification |
| `rename_churn` | `compare()` | ELF-only fingerprint rename matching |
| `versioned_rename_churn` | `compare()` (collapse on) | Versioned-symbol-scheme detection **and** collapse over `2×n` churn (ICU/OpenSSL `u_*_NN`) |
| `nested_types` | `compare()` | Transitive type-ancestor closure |
| `opaque_filter` | `compare()` | Opaque-handle size filter (the known O(candidates × functions) residual) |
| `suppression_audit` | `SuppressionList.audit()` | Rule-vs-finding matching (O(rules × findings)) |
| `severity` | `categorize_changes()` | Severity categorization of findings |
| `serialize` | `snapshot_to_json` → `from_dict` | Snapshot serialize/load round-trip (dump-pipeline proxy) |
| `report_html` | `generate_html_report()` | HTML document assembly |
| `report_sarif` | `to_sarif_str()` | SARIF JSON assembly |
| `report_junit` | `to_junit_xml()` | JUnit XML assembly |

### Peak memory

Every measurement also records the **peak tracked heap** (`peak_mb`, via
`tracemalloc`) of the timed call. The inputs are built *outside* the traced
window, so the figure attributes only the call's own allocations. The memory
pass also runs **cold**: process-wide caches warmed by the timing loop (e.g. the
`functools.lru_cache` demanglers) are cleared first, so input-scaled cache
growth is counted rather than hidden behind a warm cache. A flat per-item time
alongside a rising `peak_mb` flags an intermediate O(n²) *space* blow-up that a
wall-clock-only gate would miss. Disable with `--no-memory` (timing only); gate
with `--max-memory-mb <budget>`.

### Coverage beyond `compare()`

The original sweep (PR #331) only covered `compare()` post-processing. A
follow-up gap analysis extended it to the two other stages that build the
largest data structures from the finding set:

- **Suppression audit** (`suppression.py`, `SuppressionList.audit`) tests every
  rule against every change — O(rules × findings). The `suppression_audit`
  scenario holds the rule count fixed (a project's ruleset is roughly fixed
  while its library grows) and scales findings, so it stays **linear in
  findings**; a regression that makes per-finding matching itself super-linear
  (e.g. recompiling a pattern per change) shows up as a rising exponent.
- **Reporting** — `to_markdown`/`to_json` were already guarded by `slow` tests;
  `report_html` and `report_sarif` extend that to the HTML and SARIF renderers,
  which assemble the largest output documents. Both are linear.

## Measured scaling (after fixes)

Most scenarios are linear at the sizes a real library reaches (per-change cost
roughly flat); `type_churn` and `enum_churn` are mildly super-linear (~1.7) but
bounded and tracked:

Figures are indicative local timings (absolute seconds vary with runner speed —
the **tail exponent** is the portable signal). The first group times
`compare()`; the second group, added in PR #336, times the suppression and
reporting stages (see [Coverage beyond `compare()`](#coverage-beyond-compare)).

| Scenario | time @ size | tail exponent |
|----------|------------:|--------------:|
| `add_remove`   | 0.32 s @ n=4000 | ~0.9 (linear) |
| `var_churn`    | 0.06 s @ n=4000 | ~1.0 (linear) |
| `elf_namespace`| 0.33 s @ n=4000 | ~1.1 (linear) |
| `pe_churn` / `macho_churn` | <0.05 s @ n=500 | ~1.0 (linear) |
| `wide_struct` | 0.1–0.2 s @ n=500 | ~1.0 (linear) |
| `typedef_churn` / `union_churn` / `vtable_churn` | 0.7–1.1 s @ n=4000 | ~1.0 (linear, after fix #8) |
| `enum_churn`   | 1.0 s @ n=4000 | ~1.0 (linear, after fix #8 — was ≈1.8) |
| `type_churn`   | 0.38 s @ n=4000 | ~1.0 (linear, after fix #9 — was ≈1.6) |
| `opaque_filter`| 0.45 s @ n=1000 | ~1.2 (linear at tracked sizes after fix #8) |
| `rename_churn` | 2.1 s @ n=1000, capped above | bounded |
| `versioned_rename_churn` | 0.87 s @ n=8000 (16 k changes) | ~1.1–1.2 (mild) |
| `nested_types` | 0.70 s @ n=400 | inherent for deep chains |
| `suppression_audit` | 0.09 s @ n=2000 (fixed 40-rule set) | ~1.0 (linear in findings) |
| `severity` | <0.01 s @ n=1000 | ~1.0 (linear) |
| `serialize` | 0.12 s @ n=1000 | ~1.0 (linear) |
| `report_html` / `report_sarif` / `report_junit` | ≤0.04 s @ n≤2000 | ~1.0 (linear) |

## CI integration

[`.github/workflows/performance.yml`](https://github.com/napetrov/abicheck/blob/main/.github/workflows/performance.yml)
runs the scaling benchmark and the `slow` performance tests. It is deliberately
**flexible and non-gating**:

- Triggers: weekly schedule, manual `workflow_dispatch` (with size / budget
  inputs), and **automatically on any PR that changes the detector core**
  (`abicheck/diff_*.py`, `checker.py`, `post_processing.py`, `demangle.py`,
  `binary_fingerprint.py`, `surface.py`, the benchmark script, or the perf
  test). Adding the **`performance`** label
  re-triggers the lane; for a PR that does not touch the detector core, run it
  on demand with `workflow_dispatch`.
- `continue-on-error: true` — it never blocks a merge.
- Publishes the scaling table to the job summary and uploads the JSON.

**Turning gating on:** now that the catastrophic paths are fixed, a budget can be
enforced by adding `--max-seconds <budget>` and/or `--max-exponent <slope>` to
the benchmark step and removing `continue-on-error`. The thresholds are CLI
flags so the budget lives in the workflow, not the script. The harness exits
non-zero when a comparison exceeds the budget or the tail (largest-two-size)
scaling exponent exceeds the allowed slope.

`slow` regression guards also live in
[`tests/test_performance.py`](https://github.com/napetrov/abicheck/blob/main/tests/test_performance.py)
— `TestTypeChurnScaling` (compare back to genuine O(n²)),
`TestSuppressionAuditScaling` (audit stays linear in findings), and the
HTML/SARIF cases in `TestReporterScaling`. They run in the existing slow lane
with generous thresholds, so a catastrophic regression fails fast without
flaking on normal drift.

## Coverage gap analysis & remaining gaps

A second pass (continuation of PR #331) audited the whole pipeline for scaling
risk and extended the harness to the highest-value uncovered paths plus per-call
peak-memory tracking and PR-vs-base drift detection. Current status:

| Path | Status | Notes |
|------|--------|-------|
| `compare()` post-processing | ✅ covered | Original PR #331 scenarios. |
| Suppression audit | ✅ covered | `suppression_audit` scenario + `slow` test. O(rules × findings); linear in findings for a fixed ruleset. |
| HTML / SARIF / JUnit reporting | ✅ covered | `report_html` / `report_sarif` / `report_junit` scenarios + `slow` tests; all linear. (`to_markdown`/`to_json` already guarded.) |
| Enum / typedef / union / wide-struct / vtable diffing | ✅ covered | `enum_churn`, `typedef_churn`, `union_churn`, `wide_struct`, `vtable_churn`. Sweeping `typedef`/`union`/`vtable`/`enum` across sizes (the original table only sampled n=500, so no exponent was ever computed) exposed a genuine **≈O(n²)** in the affected-symbol enrichment — see fix #8; all four are linear after it, and `opaque_filter` dropped from ≈1.7 to ≈1.2 as a side effect (its cost was the enrichment, not `_filter_opaque_size_changes`). |
| PE/COFF & Mach-O diff arms | ✅ covered | `pe_churn` / `macho_churn` build `pe=`/`macho=` snapshots so `diff_platform`'s PE/Mach-O detectors run. |
| Opaque-handle pointer-only check | ✅ covered | Was the O(candidates × functions) residual (`_is_pointer_only_type`, regex per pair, surfaced by `type_churn` ≈1.6); fix #9 linearized it via `_opaque_usage_index` (one indexed pass). Both `type_churn` and `opaque_filter` are now linear. |
| **Versioned-symbol-scheme collapse (ICU/OpenSSL)** | ✅ covered | `versioned_rename_churn` reproduces the field-eval P08 ICU 75→78 shape (16 k removed/added churn findings + the scheme-collapse pass). Profiling it surfaced a per-finding name re-tokenization in the namespace detectors (`diff_namespaces._segments`), now fast-pathed for plain names. ~1.1–1.2 tail exponent; the residual is the post-processing detector fan-out, not the scheme recogniser. |
| Severity categorization | ✅ covered | `severity` scenario over `categorize_changes`; linear. |
| Peak memory (all scenarios) | ✅ covered | `tracemalloc` `peak_mb` column + `--max-memory-mb` gate (cold-cache pass). |
| **Historical / PR-vs-base regression** | ✅ covered | `--baseline`/`--regress-tolerance` + the `regression` workflow job measure the base branch and PR head on the same runner and flag scenarios that got slower by more than the tolerance — catching *gradual* drift the per-run exponent misses. See [Baseline regression](#baseline-regression). |
| **Dump / snapshot creation (DWARF/PE/PDB)** | ⚠️ partial | The synthetic harness can't run the real parsers. The ELF **symbol-table** parse is now guarded by `tests/test_perf_dump_scaling.py` (`integration`, gcc-only) and the `serialize` scenario proxies the snapshot-pipeline cost, but **DWARF/PE/PDB parsing proper is still unbenchmarked** — that needs a committed large real binary or a synthetic DWARF/ELF generator. |
| Appcompat HTML / stack analysis / appcompat filtering | ⚠️ not benchmarked | `stack_checker` runs one `compare()` per dependency (inherent). Appcompat filtering uses set-membership lookups (`appcompat.py` — O(1) per change, **likely already fine**) and `appcompat_html.py` is linear by inspection; neither is timed. |
| Bundle / multi-library & environment-matrix compare | ⚠️ not benchmarked | O(libraries) compares; per-library cost is covered, cross-library orchestration is not. |

### Recommended next steps (in priority order)

1. **Wire a budget gate** now that every `compare()` scenario is linear (fixes
   #8/#9) — e.g. `--max-exponent 1.4` across the board (only the inherently deep
   `nested_types` chain stays exempt) and a `--max-memory-mb` ceiling. Likewise,
   drop `continue-on-error` from the `regression` job once its tolerance has
   proven stable across a few PRs.
2. **Extend the dump/parse guard to DWARF/PE/PDB** — the ELF symbol-table parse
   is now covered (`tests/test_perf_dump_scaling.py`), but the DWARF/castxml and
   PE/PDB parsers need a committed large real binary or a synthetic byte-stream
   generator behind the `integration` marker.
3. ~~**Optimise the super-linear residuals**~~ — done: fix #8 linearized the
   enrichment (typedef/union/vtable/enum/opaque), fix #9 the opaque pointer-only
   check (`type_churn`). No quadratic `compare()` path remains at tracked sizes.

## Baseline regression

The per-run scaling exponent catches *catastrophic* blow-ups but not a gradual
20–30 % slowdown. To catch drift, the harness can compare against a baseline:

```bash
# On the base branch / a prior commit, capture a baseline:
python scripts/benchmark_scaling.py --json-out base.json

# On the PR head, measure and compare (fails if any shared scenario is >50% slower):
python scripts/benchmark_scaling.py --baseline base.json --regress-tolerance 0.5
```

Only scenarios present on **both** sides are compared (a scenario new in the PR
has no baseline and is skipped), and baseline times below a 50 ms noise floor are
ignored. The [`regression`](https://github.com/napetrov/abicheck/blob/main/.github/workflows/performance.yml)
workflow job automates this on PRs: it installs the base branch and the PR head
into separate venvs on the same runner, runs both, and prints the regressions to
the job summary. It is `continue-on-error` (informational) until the tolerance is
proven stable.
