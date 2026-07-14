# CLAUDE.md — `validation/`

Evidence-based **real-world validation** of abicheck against real upstream
C/C++ shared libraries (not synthetic fixtures), used to drive planning and
release gating. Distinct from `tests/`: `tests/` is the deterministic pytest
suite (gates every PR); this tree gathers slow, network/tool-heavy empirical
evidence and is exercised by `.github/workflows/examples-validation.yml`.

## Module map

| Path | Role |
|------|------|
| `data/manifest.json` | Curated version-pair matrix (exact upstream conda-forge files) — the input source of truth. |
| `scripts/run_matrix.py` | Reproducible harness: runs `abicheck compare` over the matrix, emits `data/results.json` (`run_matrix.v2`) + `data/results.meta.json`. |
| `scripts/run_tracker_parity.py`, `scripts/fetch_tracker_oracle.py` | Score abicheck against the ABICC abi-laboratory.pro parity oracle (harvest expected verdicts, then compare). |
| `scripts/run_component_suites.py` | pytest harness for source-family component remeasurement. |
| `scripts/run_example_runtime_smoke.py` | Runtime smoke over example cases. |
| `scripts/summarize_remeasurement.py` | Combines example / component-suite / real-world artifacts into the release-gate summary. |
| `scripts/conda_harness.py`, `scripts/validate.py` | Fetch/extract + unified end-to-end validation loop. |
| `scripts/fp_depth_demo.py` | Pure-Python (no toolchain/network) runnable demonstration of *which evidence depth clears a false positive*: the build-context/preprocessor-divergence FP that `binary`/`headers` raise and `build` clears, plus the honest negative that no pure source-only clear exists. Backs `false-positive-depth-analysis-2026-07.md`. |
| `data/*.json` | Raw per-`.so` results, run metadata, FP exemplars, dated UXL/oneDAL scans. |
| `suppress_internal.yaml` | Internal-namespace suppression used in the reports. |
| `*.md` (`REPORT.md`, `DESIGN_ANALYSIS.md`, `realworld-*.md`, `uxl-*.md`) | Curated findings + root-cause analyses; hand-edited narrative. |

## Full example matrix

For synthetic example-catalog completeness, follow
`docs/development/examples-validation-runbook.md`. The matrix aggregates gcc
and clang validator JSON, runtime smoke, bundle JSON, and dedicated owners. Do
not call a compiler lane, pair-only scan, benchmark, or depth matrix “full
catalog.” Success requires one `COVERED` row per current ground-truth entry and
no `UNRESOLVED`/`FAILED` rows.

Enable `ABICHECK_TRUSTED_SOURCE_SMOKE_RUN=1` only when intentionally executing
reviewed repository-owned source-smoke commands from a trusted checkout.

## Scoring semantics (important)

For `run_matrix.v2`, a non-zero `abicheck compare` exit code is **not** a failure
by itself — expected `BREAKING`/`API_BREAK` outcomes legitimately exit non-zero.
Release-gate summaries score the *normalized expected vs actual verdict*;
`ABICHECK_STRICTER`, `ABICHECK_WEAKER`, and missing-verdict run errors are the
blocking failures.

## Conventions

- Binaries are intentionally **not committed** — reproduce from
  `data/manifest.json` (`https://conda.anaconda.org/conda-forge/linux-64/<file>`).
- Needs network + a C/C++ toolchain; treat as a slow lane, not a unit test.
- `from __future__ import annotations`; prefer stdlib.
