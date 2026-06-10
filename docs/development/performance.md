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

The one remaining super-linear path is the **opaque-handle size filter**
(`diff_filtering._filter_opaque_size_changes`), O(candidates × functions). It is
left as-is: it only triggers on the narrow "compatible struct growth of a
pointer-only handle" pattern, so the candidate count is small for real
libraries, and `type_churn` (which forces *every* struct into that pattern) is
already down from 8.8 s to 1.4 s at 4000 functions as a side effect of the other
fixes.

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
| `elf_namespace` | `compare()` | Namespace detection + demangling (stripped lib) |
| `var_churn` | `compare()` | Public-surface classification |
| `rename_churn` | `compare()` | ELF-only fingerprint rename matching |
| `nested_types` | `compare()` | Transitive type-ancestor closure |
| `suppression_audit` | `SuppressionList.audit()` | Rule-vs-finding matching (O(rules × findings)) |
| `report_html` | `generate_html_report()` | HTML document assembly |
| `report_sarif` | `to_sarif_str()` | SARIF JSON assembly |

### Peak memory

Every measurement also records the **peak tracked heap** (`peak_mb`, via
`tracemalloc`) of the timed call. The inputs are built *outside* the traced
window, so the figure attributes only the call's own allocations. A flat
per-item time alongside a rising `peak_mb` flags an intermediate O(n²) *space*
blow-up that a wall-clock-only gate would miss. Disable with `--no-memory`
(timing only); gate with `--max-memory-mb <budget>`.

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

All scenarios are linear or bounded at the sizes a real library reaches
(per-change cost roughly flat):

| Scenario | 4000 functions (or cap) | tail exponent |
|----------|------------------------:|--------------:|
| `add_remove`   | 0.32 s | ~0.9 (linear) |
| `var_churn`    | 0.06 s | ~1.0 (linear) |
| `elf_namespace`| 0.33 s | ~1.1 (linear) |
| `type_churn`   | 1.39 s | ~1.7 (opaque filter residual) |
| `rename_churn` | 2.1 s @ n=1000, capped above | bounded |
| `nested_types` | 0.70 s @ n=400 | inherent for deep chains |

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

A second pass (continuation of PR #331) audited the whole pipeline for
scaling risk and extended the harness to the highest-value uncovered paths
(`enum_churn`, `suppression_audit`, `report_html`, `report_sarif`) plus
per-call peak-memory tracking. Findings and what is still **not** guarded:

| Path | Status | Notes |
|------|--------|-------|
| `compare()` post-processing | ✅ covered | Original PR #331 scenarios. |
| Suppression audit | ✅ covered | New `suppression_audit` scenario + `slow` test. O(rules × findings); linear in findings for a fixed ruleset. |
| HTML / SARIF reporting | ✅ covered | New `report_html` / `report_sarif` scenarios + `slow` tests; both linear. (`to_markdown`/`to_json` already guarded.) |
| Enum diffing | ✅ covered | New `enum_churn` scenario. **Mildly super-linear** at the sizes swept (tail exponent ≈1.7) — comparable to `type_churn`'s opaque-filter residual; tracked, not yet optimised. |
| Peak memory (all scenarios) | ✅ covered | `tracemalloc` `peak_mb` column + `--max-memory-mb` gate. |
| **Dump / snapshot creation** | ⚠️ **not benchmarked** | The synthetic harness builds `AbiSnapshot` directly, bypassing all parsing. `dump` is only anecdotally "~5 s for libonedal" and DWARF/PE/PDB parsing has **no scaling guard**. Closing this needs either a committed large real binary or a synthetic DWARF/ELF generator (heavier than the pure-Python harness — out of scope here). |
| **PE/COFF & Mach-O diff paths** | ⚠️ partial | Only ELF-style inputs are swept; `diff_platform.py`'s PE/Mach-O arms are untimed. |
| Typedef / union / large-single-struct churn | ⚠️ partial | `type_churn` covers structs-by-pointer; `enum_churn` covers enums. Unions, typedef storms, and 100+-field single structs are not isolated. |
| JUnit reporting, appcompat HTML | ⚠️ not benchmarked | `junit_report.py` / `appcompat_html.py` renderers untimed (linear by inspection). |
| Stack analysis / appcompat filtering | ⚠️ not benchmarked | `stack_checker` runs one `compare()` per dependency (inherent); appcompat finding-vs-required-symbol filtering untimed. |
| **Historical / PR-vs-base regression** | ⚠️ **gap** | The benchmark is *standalone* — it only catches catastrophic super-linear blow-ups via the per-run exponent, not a gradual 20–30 % regression. There is no stored baseline nor a PR-head-vs-base comparison. A future step could persist `scaling.json` as a baseline artifact and diff per-`us/change` against it with a tolerance. |

### Recommended next steps (in priority order)

1. **Wire a budget gate on the linear scenarios** now that they're stable —
   e.g. `--max-exponent 1.4` on `add_remove`/`var_churn`/`report_html` and a
   `--max-memory-mb` ceiling — while leaving the known super-linear scenarios
   (`type_churn`, `enum_churn`, `nested_types`) non-gating.
2. **Add a dump/parse scaling guard** — the largest untracked surface. Either
   commit one large stripped real `.so` behind the `integration` marker, or
   add a synthetic DWARF/ELF byte-stream generator.
3. **Persist a baseline** for PR-vs-base regression detection (gradual drift),
   not just absolute-budget gating.
