# Full example validation runbook

Use this workflow when a change can affect example detection, ground truth,
fixtures, or validation harnesses. It is the source of truth for complete
catalog accounting.

## Choose the correct runner

| Question | Runner |
|---|---|
| Is a single-library `v1`/`v2` case classified correctly? | `tests/validate_examples.py` |
| Does a case demonstrate a runtime effect? | `validation/scripts/run_example_runtime_smoke.py` |
| Are multi-library release bundles correct? | `validation/scripts/run_bundle_examples.py` |
| Are audit, BTF, L3/L4/L5, Python API, reconcile, snapshot-pair, and KABI fixtures valid? | The dedicated pytest files below |
| Is every ground-truth case accounted for? | `validation/scripts/collect_full_example_matrix.py` |
| How accurate are evidence depths or external tools? | Benchmark/depth runners; measurement only |

`validate_examples.py` alone is not the full catalog. A scan of directories
that produce `libv1.so`/`libv2.so` also omits bundle, audit, fixture, Python,
BTF, KABI, and other dedicated-owner cases.

## Prerequisites and trust boundary

Run on Linux x86_64 with the repository development environment and the same
tool dependencies as `Examples Validation` CI: gcc/g++, clang/clang++, CMake,
Ninja, CastXML, and binutils.

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
mkdir -p results
export PYTHONPATH=.
export ABICHECK_TRUSTED_SOURCE_SMOKE_RUN=1
```

The source-smoke `run` mode executes fixture commands. Enable it only for a
trusted checkout containing reviewed repository-owned fixtures. Its default is
intentionally disabled. Without this opt-in, source-smoke-owned cases can be
`SKIP` in both compiler lanes and the collector reports them `UNRESOLVED`.

## Reproduce the full matrix

### 1. Validate dedicated owners

```bash
python -m pytest \
  tests/test_g20_catalog.py \
  tests/test_l3l4l5_examples.py \
  tests/test_python_api_examples.py \
  tests/test_workflow_kernel_accel.py::test_committed_btf_example_matches_ground_truth \
  tests/test_diff_reconcile.py::test_case164_fixtures_reconcile \
  'tests/test_environment_drift.py::TestCase170Example' \
  tests/test_kabi_examples.py \
  -q
```

Do not pass a collector `--proof-*` flag unless its proof command passed in the
same checkout.

### 2. Produce lane artifacts

```bash
python tests/validate_examples.py --toolchain gcc --json > results/validate-examples-gcc.json
python tests/validate_examples.py --toolchain clang --json > results/validate-examples-clang.json
python validation/scripts/run_example_runtime_smoke.py --json > results/example-runtime-smoke.json
python validation/scripts/run_bundle_examples.py --json > results/bundle-examples.json
```

### 3. Aggregate one row per case

```bash
python validation/scripts/collect_full_example_matrix.py \
  --gcc results/validate-examples-gcc.json \
  --clang results/validate-examples-clang.json \
  --runtime results/example-runtime-smoke.json \
  --bundle results/bundle-examples.json \
  --proof-g20 --proof-l3l4l5 --proof-btf --proof-python-api \
  --proof-reconcile --proof-snapshot-pair --proof-kabi \
  --out results/full-example-matrix.json
```

The collector exits non-zero for `UNRESOLVED` or `FAILED`. Never use
`--allow-unresolved` for a release or correctness gate.

### 4. Verify the gate

```bash
python - <<'PY'
import json
from pathlib import Path

d = json.loads(Path("results/full-example-matrix.json").read_text())
total = d["ground_truth_cases"]
assert len(d["results"]) == total
assert d["summary"] == {"COVERED": total}
assert not d["unresolved_cases"]
assert not d["failed_cases"]
print(f"{total}/{total} COVERED")
PY
```

The exact count comes from `examples/ground_truth.json`; automation must not
hard-code a historic count. When this runbook was added, the proven result was
`181/181 COVERED`.

## Interpret results

- `FAILED`: a lane ran and contradicted ground truth, or a proof failed.
- `UNRESOLVED`: no owner lane proved the case; inspect `lanes`, `proof_lane`,
  and `note` in that row.
- A compiler-lane `SKIP` is acceptable only when another designated owner
  proves that case and the final row is `COVERED`.
- Runtime statuses describe behavior; they do not replace verdict proof.

## Agent checklist

1. Read `examples/ground_truth.json`; it defines scope and ownership.
2. Use the smallest owner runner while iterating.
3. Before claiming full-catalog success, obtain every artifact above.
4. Record commit SHA, tool versions, commands, exit codes, and artifact paths.
5. Claim success only when collector JSON has one row per case and every row is
   `COVERED`, with no unresolved or failed cases.
6. Never substitute pair counts, scan-depth totals, benchmark accuracy, or one
   green compiler lane for the full matrix.
7. Keep generated binaries and ad-hoc results out of commits.

CI implementation: `.github/workflows/examples-validation.yml`. Keep this
runbook synchronized with its full-matrix job.
