# Comparison Performance

This page evaluates the runtime cost of comparing **real, large** shared
libraries and documents the tooling that tracks it in CI.

## TL;DR

- **Dump scales fine.** Snapshotting `libonedal_core.so` (~10,550 exported
  functions) takes ~5 s.
- **Compare does not.** On the same library, `compare` did **not finish within
  60 s**. The cost is in the *post-processing* detectors, not the core symbol
  diff.
- Two bottlenecks dominate, both super-linear in the number of functions:
  1. A **`c++filt` subprocess storm** in namespace detection — one subprocess
     spawn per unique mangled symbol instead of one batched call.
  2. An **O(functions × types) substring scan** in opaque/pointer-only type
     filtering.
- Because of this, **performance gating is intentionally off**. CI *measures*
  and reports; it does not block. See [CI integration](#ci-integration) for how
  to enable a budget once the bottlenecks are fixed.

## How to reproduce

No real binary, compiler, or castxml required — the scaling harness synthesises
`AbiSnapshot` pairs that exercise the expensive paths:

```bash
# Sweep the default sizes across all scenarios and print a table.
python scripts/benchmark_scaling.py

# Focus the realistic hot path and emit machine-readable JSON.
python scripts/benchmark_scaling.py --scenario type_churn \
    --sizes 1000 2000 4000 8000 --json-out reports/perf/scaling.json
```

The harness defines three workloads:

| Scenario | What it stresses | Represents |
|----------|------------------|------------|
| `add_remove` | Core symbol diff only (control group) | Functions added/removed, no type churn |
| `type_churn` | Affected-symbol enrichment, opaque/pointer-only filtering | Header-aware compare where types changed |
| `elf_namespace` | Namespace detectors + demangling | Stripped, ELF-only real library |

## Measured scaling

`type_churn`, single-threaded, Python 3.11 (representative numbers — absolute
times vary by machine):

| functions | type changes | seconds |
|----------:|-------------:|--------:|
| 500       | 100          | ~1.0 |
| 1000      | 100          | ~1.2 |
| 2000      | 200          | ~3.5 |
| 4000      | 400          | ~8.7 |

Per-change cost roughly **doubles** as the library grows 4×, i.e. growth is
super-linear (empirical log-log exponent ≈ 1.3–1.4 over this range, trending
higher as the affected-type set grows). Extrapolating to ~10,550 functions with
the much larger affected-type set of a real numeric library comfortably exceeds
a 60 s budget — matching the observed `libonedal_core.so` timeout.

## Where the time goes

`cProfile` of `compare` on the 4000-function `type_churn` workload
(~15 s total, dominated by `post_processing.run`):

### 1. `c++filt` subprocess storm (largest cost)

```
cumtime  function
  9.28s  diff_namespaces.detect_experimental_namespace_changes
  9.05s   └─ _index_funcs_by_stable_key
  8.88s      └─ _qualified_function_name        (24,000 calls)
  8.84s         └─ demangle.demangle_batch      (64,001 calls)
  7.54s            └─ _batch_phase3_cppfilt     (4,000 subprocess spawns!)
```

`abicheck/diff_namespaces.py:130` (`_qualified_function_name`) calls
`demangle_batch([mangled])` **one symbol at a time**. `demangle_batch` is
explicitly designed to demangle a *whole batch* in a single `c++filt`
subprocess and memoise the result, but feeding it single-element lists defeats
that: each unique symbol triggers its own subprocess spawn. On a stripped
library where every function name must be demangled, this is thousands of
process spawns.

**Fix direction:** in `_index_funcs_by_stable_key`
(`abicheck/diff_namespaces.py:166`), collect every `f.mangled` up front and call
`demangle_batch(all_mangled)` **once**, then look results up from the returned
dict. This turns N spawns into 1 and is the single highest-leverage change.

### 2. O(functions × types) substring scan

```
cumtime  function
 10.25s  diff_filtering._filter_opaque_size_changes
  9.00s   └─ _is_pointer_only_type              (per candidate opaque type)
  8.94s      └─ _public_function_uses_type_by_value  (full function scan each)
```

`abicheck/diff_filtering.py:535` (`_public_function_uses_type_by_value`) scans
**all** public functions for **each** candidate type, doing substring matching
on every return type and parameter. The same shape recurs in
`_build_type_to_funcs` (`diff_filtering.py:192`) and
`_build_type_embed_index` (`diff_filtering.py:208`): each iterates
`functions × affected_types` with `in` substring tests rather than an inverted
index.

**Fix direction:** build a single **inverted index** once per compare —
`type_name → [functions that reference it]` — by scanning each function's type
references one time, then have the opaque/enrichment/embed passes look up that
index instead of re-scanning all functions. This collapses the repeated
O(functions × types) passes to roughly O(functions + types).

## Recommendations (ordered by leverage)

1. **Batch demangling in namespace detection.** Single highest-leverage fix;
   removes thousands of subprocess spawns. Low risk — pure call-site change.
2. **Build one inverted type→functions index** and reuse it across
   `_enrich_affected_symbols`, the opaque-size filter, and the embed index.
3. **Short-circuit detectors that don't apply.** Skip namespace detection when
   no `experimental`-style namespaces are present; skip opaque filtering when
   there are no size/opaque changes.
4. **Only then consider a performance gate** in CI (a `--max-seconds` budget on
   `type_churn`), once a stable post-fix baseline exists.

These are tracked as a performance bottleneck to be addressed upstream before
gating is enabled.

## CI integration

[`.github/workflows/performance.yml`](https://github.com/napetrov/abicheck/blob/main/.github/workflows/performance.yml)
runs the scaling benchmark and the `slow` performance tests. It is deliberately
**flexible and non-gating**:

- Triggers: weekly schedule, manual `workflow_dispatch` (with size / budget
  inputs), and **automatically on any PR that changes the detector core**
  (`abicheck/diff_*.py`, `checker.py`, `post_processing.py`, `demangle.py`, the
  benchmark script, or the perf test). Adding the **`performance`** label
  re-triggers the lane on such a PR; for a PR that does not touch the detector
  core, run it on demand with `workflow_dispatch`.
- `continue-on-error: true` — it never blocks a merge.
- Publishes the scaling table to the job summary and uploads the JSON.

**Turning gating on later:** the thresholds are CLI flags, not hard-coded, so a
budget lives in the workflow rather than the script. Add
`--max-seconds <budget>` and/or `--max-exponent <slope>` to the benchmark step
and remove `continue-on-error`. The harness exits non-zero when a comparison
exceeds the budget or the tail (largest-two-size) scaling exponent exceeds the
allowed slope.

A `slow` regression guard also lives in
[`tests/test_performance.py`](https://github.com/napetrov/abicheck/blob/main/tests/test_performance.py)
(`TestTypeChurnScaling`): it runs in the existing slow lane with generous
thresholds, so a regression to genuine O(n²) fails fast without flaking on
normal drift.
