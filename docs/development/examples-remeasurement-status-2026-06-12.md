# Synthetic Example Remeasurement Status - 2026-06-12

**Task:** `examples-full-run`
**Scope:** First full synthetic-example pass after pulling latest `main`.

## Environment

- Repo: `/home/openclaw/.openclaw/workspace-abicheck/abicheck-pr-data-source-main`
- Commit: `79fdf0f3d416595d3f3d87b2704bcbc5564be97f`
- Command prefix needed locally: `PYTHONPATH=.`
- Local run artifacts (gitignored, reproducible):
  - `validation/data/runs/examples-full-main-2026-06-12.json`
  - `validation/data/runs/example-runtime-smoke-debug-2026-06-12.json`
  - `validation/data/runs/examples-release-headers-2026-06-12.json`
  - `validation/data/runs/examples-stripped-headers-2026-06-12.json`
  - `validation/data/runs/examples-build-source-2026-06-12.json`

## Command

```bash
PYTHONPATH=. python tests/validate_examples.py --json
```

The command exits `1` because the suite still contains unexpected `FAIL` and
`ERROR` records. The JSON artifact uses `validate_examples.v2`.

## Result

- Ground-truth cases: `134`
- Selected cases: `134`
- Artifact variants: `debug-headers`
- PASS: `109`
- XFAIL: `5`
- SKIP: `7`
- FAIL: `10`
- ERROR: `3`

## Runtime Smoke

Command:

```bash
PYTHONPATH=. python validation/scripts/run_example_runtime_smoke.py --json
```

This build/run layer does not call `abicheck dump` or `abicheck compare`. It
builds each CMake-backed consumer app against `libv1`, confirms the baseline app
starts with `libv1`, then substitutes `libv2` under the old library name and
records whether the old app sees a loader failure, non-zero exit, signal, stderr
change, or stdout change.

Result:

- DEMONSTRATED: `69`
- NO_RUNTIME_SIGNAL: `41`
- SKIP: `13`
- BUILD_ERROR: `3`
- BASELINE_ERROR: `8`

Runtime smoke is intentionally descriptive, not the policy oracle. A
`NO_RUNTIME_SIGNAL` result can still be a valid `BREAKING` or `API_BREAK` case
when the issue is source-only, policy-only, or requires abicheck evidence layers
rather than old-binary execution.

Runtime smoke error buckets to triage:

- BUILD_ERROR: `case104_glibcxx_dual_abi_flip`,
  `case115_bit_int_width_changed`, `case116_atomic_qualifier_changed`
- BASELINE_ERROR: `case06_visibility`,
  `case109_flow_graph_policy_renames`,
  `case110_concurrent_unordered_map_api_drift`,
  `case111_enumerable_thread_specific_lambda_ambiguity`,
  `case112_lp64_ilp64`, `case42_type_alignment_changed`,
  `case50_soname_inconsistent`, `case78_task_arena_attach_tag`

These are now explicit demo-quality follow-ups: either the runtime smoke harness
needs case-specific expectations, or the case README/build should make the
baseline behavior clearer.

## abicheck Mode Matrix

Commands:

```bash
PYTHONPATH=. python tests/validate_examples.py --json
PYTHONPATH=. python tests/validate_examples.py --artifact-variant release-headers --json
PYTHONPATH=. python tests/validate_examples.py --artifact-variant stripped-headers --json
PYTHONPATH=. python tests/validate_examples.py --artifact-variant build-source --json
```

| Mode | Meaning | PASS | XFAIL | SKIP | FAIL | ERROR |
|---|---|---:|---:|---:|---:|---:|
| `debug-headers` | debug binary + headers | 109 | 5 | 7 | 10 | 3 |
| `release-headers` | stock/release binary + headers | 109 | 5 | 7 | 10 | 3 |
| `stripped-headers` | stripped binary + headers | 103 | 7 | 7 | 14 | 3 |
| `build-source` | binary + headers + build/source evidence | 96 | 5 | 7 | 13 | 13 |

Variant-specific observations:

- `release-headers` currently matches `debug-headers`.
- `stripped-headers` loses additional signal in `case103`,
  `case117`, `case129`, and `case62`.
- `build-source` exposes a larger CastXML/build-context failure bucket,
  mostly from compile-command flags passed through to CastXML, and currently
  fails the new build-flag examples `case130`-`case133` as `NO_CHANGE`.

## New Examples

The new examples added on latest `main` are clean and demonstrate the intended
classification.

| Case | Status | Expected | Got | Evidence |
|---|---:|---|---|---|
| `case129_struct_return_convention` | PASS | `BREAKING` | `BREAKING` | `L0,L1` |
| `case130_exceptions_mode_flip` | PASS | `COMPATIBLE_WITH_RISK` | `COMPATIBLE_WITH_RISK` | `L0,L1,L3` |
| `case131_rtti_mode_flip` | PASS | `COMPATIBLE_WITH_RISK` | `COMPATIBLE_WITH_RISK` | `L0,L1,L3` |
| `case132_threadsafe_statics_flip` | PASS | `COMPATIBLE_WITH_RISK` | `COMPATIBLE_WITH_RISK` | `L0,L1,L3` |
| `case133_tls_model_flip` | PASS | `COMPATIBLE_WITH_RISK` | `COMPATIBLE_WITH_RISK` | `L0,L1,L3` |

## Unexpected FAIL

These are still false-negative or under-classified examples in the default
`debug-headers` run. They should be treated as the current demonstration set for
missing detection.

| Case | Expected | Got |
|---|---|---|
| `case01_symbol_removal` | `BREAKING` | `NO_CHANGE` |
| `case02_param_type_change` | `BREAKING` | `NO_CHANGE` |
| `case03_compat_addition` | `COMPATIBLE` | `NO_CHANGE` |
| `case102_frozen_runtime_signature_changed` | `BREAKING` | `NO_CHANGE` |
| `case10_return_type` | `BREAKING` | `NO_CHANGE` |
| `case12_function_removed` | `BREAKING` | `NO_CHANGE` |
| `case33_pointer_level` | `BREAKING` | `NO_CHANGE` |
| `case46_pointer_chain_type_change` | `BREAKING` | `NO_CHANGE` |
| `case59_func_became_inline` | `BREAKING` | `NO_CHANGE` |
| `case66_language_linkage_changed` | `BREAKING` | `NO_CHANGE` |

## Unexpected ERROR

These fail before verdict classification because CastXML cannot dump the v1
header in the current default path.

| Case | Expected | Failure |
|---|---|---|
| `case126_sycl_device_impl_ptr` | `BREAKING` | `dump v1 failed` |
| `case80_pimpl_shared_to_unique` | `BREAKING` | `dump v1 failed` |
| `case89_inline_accessor_renamed_pimpl_member` | `BREAKING` | `dump v1 failed` |

## Expected Non-Pass Buckets

XFAIL remains `5` and is already classified in `examples/ground_truth.json`:

- `case105_concept_tightening`
- `case111_enumerable_thread_specific_lambda_ambiguity`
- `case64_calling_convention_changed`
- `case78_task_arena_attach_tag`
- `case97_api_depends_on_consumer_env`

SKIP remains `7` and is intentional:

- `case115_bit_int_width_changed`: local compiler lacks `_BitInt`
- `case121_kernel_btf_struct_field_added`: committed BTF blobs are validated by
  kernel workflow tests
- `case84_bundle_soname_skew`
- `case90_bundle_intra_dep_removed`
- `case91_bundle_intra_signature_drift`
- `case92_bundle_provider_changed`
- `case93_bundle_manifest_drift`

The bundle cases are exercised by `tests/test_bundle.py`.

## Readiness Call

Classification is clear for this first run:

- The five new cases on latest `main` are correctly classified.
- PASS/XFAIL/SKIP buckets are separated cleanly in the JSON artifact.
- The default run still demonstrates `10` unexpected classification failures and
  `3` CastXML dump errors. These are the next triage targets before calling the
  synthetic corpus fully green.

Next useful work:

1. Fix or document the runtime smoke `BUILD_ERROR`/`BASELINE_ERROR` cases.
2. Investigate the `stripped-headers` regressions where debug/release pass or
   XFAIL but stripped loses the expected signal.
3. Fix `build-source` compile-command/CastXML handling, then remeasure the
   `case130`-`case133` build-flag examples in that mode.
