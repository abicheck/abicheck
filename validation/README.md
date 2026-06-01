# validation/ — real-world validation runs

Evidence-based validation of abicheck against real upstream C/C++ shared
libraries (not synthetic fixtures), used to drive planning and improvement.

- `REPORT.md` — latest validation report (start here)
- `DESIGN_ANALYSIS.md` — code-level root cause + architectural fix per false
  positive. FP-1/FP-2 are fixed in `abicheck/model.py` + `abicheck/diff_types.py`;
  FP-3/FP-4 are guarded by strict-xfail regression tests in
  `tests/test_real_world_false_positives.py`.
- `data/manifest.json` — the curated version-pair matrix (exact upstream files)
- `data/results.json` — raw per-`.so` comparison results
- `data/false_positive_evidence.json` — false-positive exemplars
- `suppress_internal.yaml` — internal-namespace suppression used in the report
- `scripts/run_matrix.py` — reproducible harness

Binaries are intentionally not committed; reproduce them from `data/manifest.json`
(conda-forge, `https://conda.anaconda.org/conda-forge/linux-64/<file>`).
