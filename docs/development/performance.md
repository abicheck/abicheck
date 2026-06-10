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
former bottleneck):

| Scenario | Stresses |
|----------|----------|
| `add_remove` | Core symbol diff + surface scoping (control) |
| `type_churn` | Affected-symbol enrichment, opaque filtering |
| `elf_namespace` | Namespace detection + demangling (stripped lib) |
| `var_churn` | Public-surface classification |
| `rename_churn` | ELF-only fingerprint rename matching |
| `nested_types` | Transitive type-ancestor closure |

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

A `slow` regression guard also lives in
[`tests/test_performance.py`](https://github.com/napetrov/abicheck/blob/main/tests/test_performance.py)
(`TestTypeChurnScaling`): it runs in the existing slow lane with generous
thresholds, so a regression back to genuine O(n²) fails fast without flaking on
normal drift.
