# Performance findings registry

Optimization candidates found by the performance instruments
(`scripts/perf_report.py`, `scripts/audit_repeated_calls.py --by-cost`, the
call-count and per-declaration gates; see [performance.md](performance.md))
and what was decided about each. Read this before re-investigating a hot spot
the weekly report surfaces: a candidate marked *accepted* has a reason, and a
*fixed* one has a gate that should catch it coming back.

## Open

| Candidate | Evidence | Notes |
|---|---|---|
| `qualified_declaration_name` / `demangle_one_batched` ~13 calls per declaration on `add_remove` | `per_decl_x10:*` budgets; `perf_report.py` "Calls per declaration" | Several detectors each re-derive the qualified name of the same function. Cheap per call (memoized demangle) but the leading costed repeat. A per-snapshot name table under `detection_memo` is the likely fix. |
| `_reconciled_function_surfaces` called ~12× with identical arguments per `compare()` | `--by-cost` | `reconciled_public_functions` caches internally; the outer wrapper still repeats its surface lookup. |
| Surface graph built twice under `--pattern-verdicts` + `--surface-metrics` | `audit_repeated_calls.py --mode patterns_and_metrics` | Each stage builds its own graph; sharing one needs an owner for its lifetime across both stages (architectural decision, not a memo). |
| Duplicate type names collapse first-wins (`Ctx (×8)` in the real C++ corpus) | `tests/_cpp_corpus.py` dump log | Correctness, not speed: findings on the colliding record are labelled with the bare name. Tracked in [known-gaps.md](known-gaps.md) territory; noted here because the corpus is what exposes it. |

## Fixed (guarded)

| Fix | Guard |
|---|---|
| `pattern_verdicts` type-name and pimpl-pointee scans were types × findings; now indexed (`_TypeNameIndex`, `_PimplPointeeIndex`) | `tests/test_compare_call_complexity.py`, `tests/test_pattern_verdicts_indices.py` |
| `_attribute_stdlib_embedding` re-scanned a record's fields once per field (O(width²)); now once per record | `wide_record_churn` workload in the call-count gate |
| Namespace-shape detectors demangled the public surface per detector; now once per snapshot via `detection_memo` | `repeats:*` budgets |
| `mask_operator_symbols`, `template_angle_depth`, `_strip_param_signature`, `_cpo_function_stem` recomputed per call; memoized | `repeats:*` budgets, H5 coverage rows |
| Legacy surface-fact fallback allocated a new `Fact` per read; now shared constants | `legacy_signature_churn` workload |
| Proof-path de-dup in `internal_leak.py` and `_compute_occurrences` used list membership | `perf-antipatterns` baseline |
