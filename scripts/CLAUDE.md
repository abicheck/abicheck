# CLAUDE.md — `scripts/`

Maintenance and demo scripts. Not packaged; not part of the public API.
Each must run with Python 3.10+ and the package installed in dev mode
(`pip install -e ".[dev]"`).

## Inventory

| Script | Purpose | Triggered by |
|--------|---------|--------------|
| `check_ai_readiness.py` | AI-readiness gate (file size, CLAUDE.md coverage, test ratio, ChangeKind invariants, mypy baseline drift, import cycles, test-assertion density). | CI (`ai-readiness`) and `pre-commit`. Exits 1 on errors. |
| `check_fp_rate.py` | False-positive/false-negative gate for public-surface scoping (ADR-024 §7). Labelled `(old, new)` corpus; baselines FP=0/FN=0. Cases are tagged by scoping *axis* (`CASE_CATEGORY`): `--json` carries a `by_category` breakdown and `--markdown` renders a per-axis table for CI step-summary / release-over-release trend reading. | CI (`ai-readiness`). Mirrored in `tests/test_fp_rate_gate.py`. |
| `check_tier_accuracy.py` | Per-evidence-tier accuracy gate — *what each level buys*. One labelled logical change per case, projected down to what each tier observes (L0 symbols → L1 debug → L2 headers → L3 build) and run through `compare`; verdicts collapse to a 3-band ordinal (non-breaking/risk/breaking). Measures per-tier **over-call (FP)** vs **under-call (FN)** and which transition removes each. Gates: top-tier correctness + under-call monotonicity (more evidence never hides a break — ADR-028 D3). `--markdown`/`--json` emit the per-tier matrix. | CI (`ai-readiness`, + step-summary artifact). Mirrored in `tests/test_tier_accuracy_gate.py`. |
| `check_mutation_score.py` | Mutation-score baseline-drift gate. Counts surviving `mutmut` mutants in the detector core and compares to `SURVIVOR_BASELINE`. Parser unit-tested in `tests/test_mutation_score_gate.py`. | CI (`mutation.yml`: weekly / `mutation` label / dispatch). |
| `gen_examples_docs.py` | Regenerates `docs/examples/caseNN_*.md` from `examples/case*/README.md`, and the generated regions (headline, verdict distribution, case index) of `examples/README.md` from `ground_truth.json`. `--check` gates both. Run after adding a new example case. | manual |
| `gen_detector_spec.py` | Regenerates the formal detector specification matrix (`docs/reference/detector-spec.{md,json}`) by fusing per-`ChangeKind` category (`checker_policy`), default verdict/severity/doc-slug (`policy_for`), and min evidence tier (`evidence_tiers`). `--check` gates sync; mirrored in `tests/test_detector_spec.py`. Run after adding a `ChangeKind`. | manual |
| `gen_stable_abi_data.py` | Regenerates the vendored CPython Stable-ABI membership set (`abicheck/stable_abi_data.py`) from a `Misc/stable_abi.toml` (local path or `--url`). Extracts every `[function.*]`/`[data.*]` entry's `added` floor, incl. `abi_only` `_Py*` symbols. Refresh when a new CPython minor ships so `--abi3 3.NN` extensions aren't flagged for symbols that entered the Stable ABI in that release. | manual |
| `gen_g20_fixtures.py` | Single source of truth for the committed G20 snapshot fixtures (`examples/case143–151`, ADR-035 / plan `g20-source-scan-example-catalog`). Writes each hand-built `AbiSnapshot` to `snapshot.abi.json` (plus `thin.abi.json` for the provider-matrix case); `--check` fails if committed snapshots drift. Validated in the fast lane by `tests/test_g20_catalog.py` (no compiler/castxml). Run after changing a fixture's design. | manual |
| `benchmark_comparison.py` | Benchmarks abicheck vs ABICC / libabigail across the `examples/` catalog. `--evidence-tiers` instead runs abicheck at each evidence source (L0 binary / L1 +debug / L2 +headers / L3 +build) and reports which cases each source discovers. | manual |
| `evidence_tiers.py` | Single source of truth for the five-source / L0–L4 evidence model: `EVIDENCE_TIER_BY_KIND` + `KINDLESS_CASE_TIER` compute each case's `min_evidence` (stored in `examples/ground_truth.json`). Pure-stdlib, importable. Consumed by `benchmark_comparison.py --evidence-tiers` and gated by `tests/test_evidence_tiers.py`. | imported |
| `evidence_benchmark.py` | ADR-033 Phase 7 performance & false-positive report: times the compiler-free inline collection path per collect mode and prints the FP-rate gate's D9 delta metrics. `--json` for machine output. Reporting tool, not a gate. | manual / CI report |
| `benchmark_scaling.py` | Synthetic scaling benchmark for the pipeline — sweeps sizes and times `compare()`, suppression audit, severity, serialization, and the HTML/SARIF/JUnit reporters (plus PE/Mach-O/typedef/union/vtable/opaque-filter diff arms, fuzzy-rename accept path, and version-node migration fan-out), reporting a scaling exponent, peak heap (`tracemalloc`), and process peak RSS (`resource.getrusage` — catches native allocs). Pure-Python (no compiler/castxml). Gating in CI; `--max-seconds` / `--max-exponent` (per-scenario opt-out via `gate_exponent`) / `--max-memory-mb` / `--max-rss-mb` gate absolute budgets, `--baseline`/`--regress-tolerance` gate PR-vs-base drift. See `docs/development/performance.md`. | CI (`performance.yml`: weekly / `performance` label / dispatch / PR detector-core); manual |
| `demo_libz.py` | End-to-end demo on libz, used by the `e2e` CI job. | CI (`e2e` job) |
| `summarize_test_durations.py` | Renders the per-test durations captured by the `tests/conftest.py` `ABICHECK_DURATIONS_JSON` hook into a Markdown table (GitHub run summary in CI, stdout locally). Reporting only — not a gate. Unit-tested in `tests/test_summarize_test_durations.py`. | CI (`unit-tests`, Linux/3.13) |
| `extract_bundle_manifest.py` | Extracts a manifest from multi-library bundles (cases 90–93). | manual |

## Conventions

- **Pure stdlib** for anything that may run before `pip install` (e.g.
  `check_ai_readiness.py` — it's the first CI step).
- **`from __future__ import annotations`** at the top of every script.
- **No global side effects** at import time — gate behavior on
  `if __name__ == "__main__":`.
- **Exit codes**: 0 on success, 1 on any check/operational failure.
  Demo scripts may print to stdout but should not write outside the repo
  tree without an explicit flag.

## Adding a new script

1. Place it here; give it an executable shebang (`#!/usr/bin/env python3`).
2. Add a row to the inventory table above.
3. If it runs in CI, wire it into `.github/workflows/ci.yml` and (where
   sensible) `.pre-commit-config.yaml`.
4. Document its arguments via `argparse` so `--help` is enough for an
   agent to use it.
