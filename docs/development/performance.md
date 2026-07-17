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
| `rename_churn` | `compare()` | ELF-only fingerprint rename matching — the *reject* path (disjoint names, no match emitted) |
| `fuzzy_rename_churn` | `compare()` | ELF-only fingerprint rename matching — the *accept* path (every symbol genuinely renamed → one `func_likely_renamed` per pair). The ICU/LLVM cost driver (P11: rename detection, not symbol count) |
| `version_node_churn` | `compare()` | Version-node migration fan-out — every export moves `LIB_1.0 → LIB_2.0` → `n` `symbol_moved_version_node` findings (the LLVM 17→18 36,991-finding shape) |
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

Alongside it, each point records the **process peak RSS** (`rss_mb`, via
`resource.getrusage`). Unlike `peak_mb`, which only sees Python-heap
allocations, RSS also counts **native** memory — pyelftools parse buffers and
`c++filt` subprocess pages — which is what dominates real libraries (the field
eval observed ~330 MiB RSS at LLVM scale, invisible to `tracemalloc`).
`ru_maxrss` is a *process* high-water mark, so it is monotonic across sizes and
the **largest/last** value is the true peak (it overstates a single call's own
footprint, since inputs are built in-process); gate the peak with
`--max-rss-mb <budget>`. RSS is unavailable on Windows (`resource` is
Unix-only), where the column is simply absent.

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
| `fuzzy_rename_churn` | 0.39 s @ n=4000 | ~1.0 (linear) |
| `version_node_churn` | 0.86 s @ n=10000 (10 k moves) | ~1.0 (linear) |
| `versioned_rename_churn` | 0.87 s @ n=8000 (16 k changes) | ~1.1–1.2 (mild) |
| `nested_types` | 0.70 s @ n=400 | inherent for deep chains |
| `suppression_audit` | 0.09 s @ n=2000 (fixed 40-rule set) | ~1.0 (linear in findings) |
| `severity` | <0.01 s @ n=1000 | ~1.0 (linear) |
| `serialize` | 0.12 s @ n=1000 | ~1.0 (linear) |
| `report_html` / `report_sarif` / `report_junit` | ≤0.04 s @ n≤2000 | ~1.0 (linear) |

## CI integration

[`.github/workflows/performance.yml`](https://github.com/abicheck/abicheck/blob/main/.github/workflows/performance.yml)
runs the scaling benchmark and the `slow` performance tests. Now that every
`compare()` scenario is linear, the lane is **gating**:

- Triggers: weekly schedule, manual `workflow_dispatch` (with size / budget
  inputs), and **automatically on any PR that changes the detector core**
  (`abicheck/diff_*.py`, `checker.py`, `post_processing.py`, `demangle.py`,
  `binary_fingerprint.py`, `surface.py`, the benchmark script, or the perf
  test). Adding the **`performance`** label
  re-triggers the lane; for a PR that does not touch the detector core, run it
  on demand with `workflow_dispatch`.
- **Armed budgets:** the scaling step runs with `--max-exponent 1.4` (the tail,
  largest-two-size slope) and `--max-rss-mb 2048`; the `regression` job blocks on
  a >50 % PR-vs-base slowdown (`--regress-tolerance 0.5`). `continue-on-error` is
  dropped on both, so a catastrophic regression fails the lane. The thresholds
  are CLI flags so the budget lives in the workflow, not the script — **loosen a
  threshold rather than re-adding `continue-on-error`** if normal drift ever
  flakes a lane.
- The `--max-exponent` gate is **per-scenario opt-out**: `nested_types` is an
  inherently super-linear embedding chain, so it carries `gate_exponent=False`
  and is exempted (its tail slope is still printed for visibility, just not
  gated). Every other scenario is gated.
- Publishes the scaling table to the job summary and uploads the JSON.

`slow` regression guards also live in
[`tests/test_performance.py`](https://github.com/abicheck/abicheck/blob/main/tests/test_performance.py)
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
| **Fuzzy rename matching (accept path)** | ✅ covered | `fuzzy_rename_churn` — every symbol genuinely renamed → one `func_likely_renamed` per pair, the cost driver P11-refined identified (ICU 2134 renames = 94.5 s; rename detection, *not* symbol count, dominates). The pre-existing `rename_churn` only exercised the *reject* path (disjoint names, zero matches). Linear at ICU scale (≤8 k). |
| **Version-node migration fan-out (LLVM bump)** | ✅ covered | `version_node_churn` — every export moves `LIB_1.0 → LIB_2.0`, reproducing the LLVM 17→18 36,991-`symbol_moved_version_node` shape and the post-processing fan-out over it. Linear to 50 k. |
| Peak memory (all scenarios) | ✅ covered | `tracemalloc` `peak_mb` column + `--max-memory-mb` gate (cold-cache pass), **plus** process `rss_mb` (`resource.getrusage`) + `--max-rss-mb` gate — RSS catches native (pyelftools / `c++filt`) allocations `tracemalloc` cannot see (the ~330 MiB LLVM-scale figure). |
| **Historical / PR-vs-base regression** | ✅ covered (now gating) | `--baseline`/`--regress-tolerance` + the `regression` workflow job measure the base branch and PR head on the same runner and flag scenarios that got slower by more than the tolerance — catching *gradual* drift the per-run exponent misses. `continue-on-error` is dropped, so it now blocks. See [Baseline regression](#baseline-regression). |
| **Dump / snapshot creation (DWARF/PE/PDB)** | ⚠️ partial | The synthetic harness can't run the real parsers. The ELF **symbol-table** parse **and** the **DWARF** debug-info parse (`-g` build) are now guarded by `tests/test_perf_dump_scaling.py` (`integration`, gcc-only) — DWARF being the dominant real-library dump cost (ICU 18.6 MB snapshot, openblas 23 MB / 9.5 s). The `serialize` scenario proxies the rest of the pipeline. **PE/COFF + PDB parsing remains unbenchmarked** — those need a committed binary or a synthetic byte-stream generator (no Linux-only toolchain produces them). |
| Appcompat HTML / stack analysis / appcompat filtering | ⚠️ not benchmarked | `stack_checker` runs one `compare()` per dependency (inherent). Appcompat filtering uses set-membership lookups (`appcompat.py` — O(1) per change, **likely already fine**) and `appcompat_html.py` is linear by inspection; neither is timed. |
| Bundle / multi-library & environment-matrix compare | ⚠️ not benchmarked | O(libraries) compares; per-library cost is covered, cross-library orchestration is not. |

### Recommended next steps (in priority order)

1. ~~**Wire a budget gate**~~ — done: the lane now runs `--max-exponent 1.4`
   (`nested_types` exempt via `gate_exponent=False`) and `--max-rss-mb 2048`, and
   `continue-on-error` is dropped on both the scaling and `regression` jobs. The
   `--regress-tolerance 0.5` PR-vs-base check also blocks now; loosen a threshold
   rather than re-adding `continue-on-error` if runner variance flakes a lane.
2. **Extend the dump/parse guard to PE/PDB** — the ELF symbol-table *and* DWARF
   parses are now covered (`tests/test_perf_dump_scaling.py`, `integration`,
   gcc + `-g`); the PE/COFF and PDB parsers still need a committed binary or a
   synthetic byte-stream generator behind the `integration` marker (no Linux-only
   toolchain emits them).
3. **Benchmark the cross-library orchestration** — bundle / environment-matrix
   compares are O(libraries) over an already-covered per-library cost, but the
   orchestration layer (and appcompat/stack fan-out) is still untimed.
4. ~~**Optimise the super-linear residuals**~~ — done: fix #8 linearized the
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
ignored. The [`regression`](https://github.com/abicheck/abicheck/blob/main/.github/workflows/performance.yml)
workflow job automates this on PRs: it installs the base branch and the PR head
into separate venvs on the same runner, runs both, and prints the regressions to
the job summary. It now **gates** (a >50 % slowdown fails the job) — loosen
`--regress-tolerance` rather than re-adding `continue-on-error` if runner variance
proves noisy.

### Scan level cost model: one cliff at L4

A real `scan`-level sweep on two UXL libraries (oneTBB v2021.12→.13, C++;
UMF v0.10→v0.11, C; raw data in `validation/data/uxl_scan_results_2026-06.json`)
shows the cost has **one cliff, at the L4 AST-replay boundary**, and the cheap
tier below it is dominated by the binary dump + always-on pattern scan, *not* by
the source layer:

| Level | Reaches | oneTBB (C++, 40 TUs) | UMF (C, 50 TUs) |
|-------|---------|---------------------:|----------------:|
| `s0` diff classifier | — (L0/L1 + pattern) | ~29 s | ~17 s |
| `s1` compile-DB | +L3 | ~29 s | ~17 s |
| `s3` lexical | (pattern only) | ~29 s | ~17 s |
| `s4` symbol/graph index | +L3 +L5 | ~29 s | ~17 s |
| `s5` targeted AST | +L4 (changed TUs) +L5 | **~222 s** | ~22 s |
| `s6` full AST | +L4 (all TUs) | ~215 s | ~21 s |

**Rules of thumb:**

- **The cliff height is a C++ phenomenon.** L4 cost = clang per-TU AST replay; it
  scales with C++ template/STL instantiation depth, not `.so` or TU count. Heavy
  C++ (oneTBB) jumps **~7×** (29→222 s); plain C (UMF) barely moves (**~1.3×**,
  17→21 s). Budget L4 by *how templated* the source is.
- **The cheap tier (s0–s4) is one price.** All four cost the same — the floor is
  the DWARF dump + lexical scan of the tree. Pick by *coverage you need*, not
  cost: `s0`≈`s3` (L0/L1 + pattern only), `s1` adds L3, `s4` adds the L5
  reachability graph **without** paying for L4. `s4` is the structure sweet spot.
- **`s5` is only cheaper than `s6` with a diff seed.** Without `--since`/
  `--changed-path` the changed-TU set is empty and `s5` replays every TU — same
  cost as `s6`. With a one-file seed, oneTBB `s5` dropped from 222 s to **11.5 s
  (~19×)** for the identical verdict. This scoping applies **only** to the
  `source-changed` collect mode — i.e. `s5` and `--mode pr`. The other AST modes
  replay **full** scope regardless of any seed: `--mode pr-deep` resolves to
  `graph-full`, and `--mode baseline`/`s6` to full
  (`source_replay.CI_MODE_TO_SCOPE`: `source-changed`→`changed`, `graph-full`→`full`),
  so pinning those in CI will not produce the scoped speedup.
- **`audit` costs the same as the baseline modes** — the wall-clock is L4/L5
  *collection* of the new side, not the baseline diff.

The verdict was identical across all levels on both libraries: the authoritative
L0/L1 binary diff sets the gate; L3–L5 add coverage/localization, not a different
pass/fail. **For a CI gate, the cheap tier suffices; spend on L4 only when you
want source-body semantics or PR localization for humans.**

### Scan-level scalability sweep

The UXL run above fixed the corpus (two real libs) and varied the level. The
complementary question — how each level scales as a project's *complexity*
grows — is swept by [`eval/scan_level_scaling.py`](https://github.com/abicheck/abicheck/blob/main/eval/scan_level_scaling.py),
a self-contained harness (no network/repo) that synthesises STL/template-heavy
C++ trees of increasing TU count, builds them with the host compiler, and runs
`scan` at each level against a slightly-changed baseline — recording wall time
and **peak child RSS** (`os.wait4`) per (size, level). Raw findings:
[`validation/scan-level-scalability-2026-06.md`](https://github.com/abicheck/abicheck/blob/main/validation/scan-level-scalability-2026-06.md).

Two results:

- **The cheap tier is flat in TU count.** `binary`/`headers`/`build`/`graph`
  (s4) cost the same at 4, 8, and 16 TUs (tail exponent ≈0) — they are priced on
  the binary dump + L2 header AST + L3 compile-DB parse, none of which grow with
  the number of `.cpp` files. `full` (s6) is **linear** in TU count (every TU is
  replayed). Both as expected.
- **Seedless `--depth source` (s5) used to hide a full-tree cost — now fixed.**
  It cost ~2× the wall time and ~2.5× the RSS of the *seeded* run for the
  **identical** L4 coverage (both report `L4=1/1`), and the gap *widened* with TU
  count. The seed scopes both the L4 replay **and** the L5 clang call-graph pass
  to the changed TU; without a seed the L4 replay fell back to headers-only (one
  TU) but the **call-graph pass ran over the whole compile DB** — a second
  `clang -ast-dump=json` over every TU. The unseeded call-graph pass now scopes to
  the **same** compile units the L4 replay used (headers-only), so it is
  consistent with the L4 surface and no longer scales with the tree
  (~2.4× faster on a synthetic n=8 tree, identical verdict). Seeded runs and
  `--depth full` are unchanged.

That whole-DB call-graph pass shells out to the same multi-GiB
`clang -ast-dump=json` as the L4 replay, but its worker count
(`call_graph._call_graph_jobs`) was **CPU-bound only** — it lacked the
RAM-aware, cgroup-aware clamp the L4 replay grew (`_l4_jobs` → `_l4_mem_cap`)
after the UXL oneTBB/oneDNN OOM. On a constrained host the L4 pass was protected
but the unseeded call-graph pass was not. `_call_graph_jobs` now shares the L4
memory cap (`_call_graph_mem_cap` → `_l4_mem_cap`, same `ABICHECK_L4_JOB_MEM_GIB`
budget); `ABICHECK_CALL_GRAPH_JOBS` still overrides the CPU count but memory wins
over an over-eager override, exactly like `_l4_jobs`.

### L2 header-scan deadline enforcement (pathological headers)

A real-world field report (Intel SVS) found the cheap tier's flatness above has
an exception: a *pathological* header (deep `#include`/template complexity) can
make the L2 clang/castxml AST dump itself run far longer than its on-disk size
suggests — the report's own `scan --dry-run` estimate read 0.51 s for a header
set whose actual parse ran over 15,000 s and 3+ GiB RSS before an *external*
`SIGKILL`, because `--budget` was checked only once, after the whole scan had
already finished, and the clang/castxml `subprocess.run(timeout=120)` call had
no process-group isolation (a timeout only killed the direct child, orphaning
any compiler-driver grandchild).

The fix (`abicheck/deadline.py`) threads a shrinking `--budget` deadline down
to the L2 subprocess boundary (checked before each clang/castxml invocation,
not only at the end) and runs that subprocess in its own process group so a
timeout kills the whole tree. This is a **bounding** fix, not a speedup — a
genuinely pathological header still costs whatever clang/castxml need, up to
whatever `--budget` is given; it now fails cleanly at that boundary instead of
running unbounded.

Regression/perf-tracking coverage, deliberately without needing the SVS corpus
itself (see "Extract minimal synthetic fixtures" guidance):

- `tests/test_deadline.py` — fast, synthetic (`sh`/`sleep`), proves the
  process-group kill and mid-stage budget check mechanisms directly.
- `tests/test_header_scan_deadline_integration.py` — real clang, self-skips if
  absent. `test_pathological_header_aborts_within_bounded_time_under_tiny_budget`
  reproduces the SVS shape with a *genuinely* expensive (not simulated) 4-line
  header: a recursive template chain whose clang `-ast-dump=json` output grows
  steeply super-linearly with recursion depth (calibrated locally: depth 100 →
  ~40 MB/0.2 s, depth 200 → ~280 MB/0.6 s, depth 300 → ~900 MB/1.5 s — kept at
  depth 150 in the test to stay CI-safe), and asserts a tiny budget bounds it.
  The `slow`-marked companion `test_pathological_header_natural_cost_is_tracked`
  records that header's *unbudgeted* natural cost so a future regression (lost
  disk cache, a clang upgrade changing dump behaviour) shows up in the existing
  per-test duration trend (`tests/conftest.py`'s `ABICHECK_DURATIONS_JSON` hook
  → `scripts/summarize_test_durations.py` → the CI run summary) — the same
  mechanism this page already relies on for the `compare()`-scaling story,
  rather than a new bespoke benchmark harness. `scripts/benchmark_scaling.py`
  is deliberately **not** the home for this: it is pure-Python by design ("no
  compiler/castxml" — see its module docstring) and this concern is inherently
  compiler-driven.

**Known remaining gap (not yet fixed):** the L2 path
(`dumper._clang_header_dump`) still captures the whole AST-dump subprocess
output into a Python `str` (`capture_output=True`), unlike the L4 per-TU replay
(`source_extractors/clang.py`'s `_run_ast_to_file`), which spills clang's JSON
AST to a temp file specifically to avoid buffering a multi-GiB payload in
memory. The calibration above shows a *tiny* header can legitimately produce
hundreds of MB to multiple GB of AST-dump output — for a sufficiently
pathological header, the process could plausibly OOM before the deadline timer
even fires. `--budget` bounds *wall time*; it does not yet bound *memory* on
this path. A follow-up should switch the L2 aggregate parse to the same
file-spilling pattern the L4 extractor already uses.

## L4 source-replay (dump-side) performance

The scaling harness above is pure-Python and times the *compare* pipeline. The
**dump-side L4 source ABI replay** (clang per-TU AST extraction) is a separate
cost, timed by [`eval/scaling.py`](https://github.com/abicheck/abicheck/blob/main/eval/scaling.py)
on real source trees (it needs clang + a built tree, so it is manual, not in CI).

Knobs and the reasoning behind them (`abicheck/buildsource/source_replay.py`):

- **`ABICHECK_L4_JOBS`** — worker count for the per-TU extract pool. Auto =
  `min(TUs, cpu_count, 8)`. An explicit override is **clamped** to
  `max(8, 2×cpu_count)` (logged when it fires) so a stray `=64` can't
  oversubscribe a host into thrash (`eval/SCALING.md` already saw jobs=8 on 4
  CPUs *regress*). Set `=1` to force serial (determinism).
- **Memory cap (auto + override).** A single template-heavy C++ TU's
  `clang -ast-dump=json` output — and its in-Python parse — can reach several
  GiB, so the worker count is **also capped by available RAM**
  (`min(…, available / ABICHECK_L4_JOB_MEM_GIB)`, default `3.0` GiB/worker,
  Linux only). "Available" is the *smaller* of host `MemAvailable`
  (`/proc/meminfo`) and the **cgroup** memory headroom (v2 `memory.max` −
  `memory.current`, or v1 `memory.limit_in_bytes` − `memory.usage_in_bytes`), so
  a container/pod confined to a small cgroup on a large host sizes its workers to
  what it is actually allowed to use rather than to host RAM. On a low-memory
  host this stops N concurrent giant ASTs from
  exhausting one process and getting the whole replay **OOM-killed** (the kernel
  SIGKILLs it → `exit -9`, all L4 work lost — observed on the UXL oneTBB/oneDNN
  `s5`/`s6` full-target replays on a 15 GiB host). The clamp is logged. For a
  template-heavy tree on a constrained host, prefer a **seeded/scoped** scan
  (`--since`/`--changed-path` → a handful of TUs) over a full-target `s5`/`s6`;
  it sidesteps both the time *and* the memory cliff. `ABICHECK_L4_JOB_MEM_GIB`
  tunes the per-worker budget (lower = more workers).
- **`ABICHECK_L4_EXECUTOR`** (`thread` default / `process`) — after clang
  returns, the extractor parses clang's large JSON AST dump and builds
  structural fingerprints: pure-Python, **GIL-bound** work. A thread pool
  parallelizes only the clang *subprocess wait*, so that post-processing
  serializes on the GIL — part of the ~60–83 % "serial fraction" in
  `eval/SCALING.md`. `process` runs the extract phase in a `ProcessPoolExecutor`,
  parallelizing the AST work too (at the cost of pickling each `SourceAbiTu` and
  per-process spawn). It is opt-in pending a measured win — compare the curves
  with `python eval/scaling.py --jobs 1,2,4 --executor process` vs `thread`. The
  driver falls back to serial if a process pool can't start (sandbox, spawn
  import error), so it never aborts L4.
- **Concurrent AST memory: clang's output is spilled to a temp file, not captured.**
  A template-heavy TU's `clang -ast-dump=json` output can be multiple GiB.
  Capturing it (`capture_output=True`) holds the whole AST *string* in the heap from
  the moment clang finishes — and because the C `json` parse holds the GIL, the
  default **thread** pool serializes parsing, so all *N* workers sit holding their
  giant AST strings (≈ N × text) while queued behind the GIL. Spilling clang's
  stdout to a temp file keeps those payloads **on disk** until each worker's turn to
  parse, so the heap holds roughly one payload at a time instead of N. `json.load`
  still reads the file back to parse, so a *single* TU's parse peak is unchanged
  (≈ serialized text + tree) — this is a concurrency win, not a per-TU one — and it
  also drops the `text=True` decode copy (bytes parse) and frees the tree before the
  macro pass. The per-TU tree itself (~2–5× the AST text) is irreducible without a
  streaming JSON parser (a dependency the project avoids); for a template-heavy tree
  on a constrained host, a **seeded/scoped** scan is still the structural win.
- **`ABICHECK_L4_CACHE_DIR`** — persists the per-TU cache (`SourceAbiCache`,
  content-addressed + per-included-file dependency-hash invalidation) across
  `dump --sources` runs. Previously the inline path passed **no** cache, so every
  dump re-extracted every TU; wiring this dir makes a cold run (`eval` E4: zstd
  48.6 s) reuse the warm cache (3.4 s). Point it at a CI cache directory restored
  via `actions/cache` to start every CI run warm. The cache validation phase is
  serial, so the dependency digest is **memoized per replay pass** — a public
  header included by N TUs is hashed once, not N times.

### Why not precompiled headers (PCH) / modules?

A natural idea to cut the repeated per-TU header parse is a PCH over the public
headers. It does **not** apply here: `clang -Xclang -ast-dump=json` does *not*
re-emit declarations that came from a PCH, so loading one would silently drop the
very header surface L4 exists to capture — a correctness bug, not a speedup. The
right levers for repeated-parse cost are therefore the per-TU **cache** and the
replay **scope** (`changed`/`target`), both already in place.
