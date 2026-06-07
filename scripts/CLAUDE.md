# CLAUDE.md — `scripts/`

Maintenance and demo scripts. Not packaged; not part of the public API.
Each must run with Python 3.10+ and the package installed in dev mode
(`pip install -e ".[dev]"`).

## Inventory

| Script | Purpose | Triggered by |
|--------|---------|--------------|
| `check_ai_readiness.py` | AI-readiness gate (file size, CLAUDE.md coverage, test ratio, ChangeKind invariants, mypy baseline drift, import cycles, test-assertion density). | CI (`ai-readiness`) and `pre-commit`. Exits 1 on errors. |
| `check_fp_rate.py` | False-positive/false-negative gate for public-surface scoping (ADR-024 §7). Labelled `(old, new)` corpus; baselines FP=0/FN=0. | CI (`ai-readiness`). Mirrored in `tests/test_fp_rate_gate.py`. |
| `check_mutation_score.py` | Mutation-score baseline-drift gate. Counts surviving `mutmut` mutants in the detector core and compares to `SURVIVOR_BASELINE`. Parser unit-tested in `tests/test_mutation_score_gate.py`. | CI (`mutation.yml`: weekly / `mutation` label / dispatch). |
| `gen_examples_docs.py` | Regenerates `docs/examples/caseNN_*.md` from `examples/case*/README.md`, and the generated regions (headline, verdict distribution, case index) of `examples/README.md` from `ground_truth.json`. `--check` gates both. Run after adding a new example case. | manual |
| `benchmark_comparison.py` | Benchmarks abicheck vs ABICC / libabigail across the `examples/` catalog. | manual |
| `demo_libz.py` | End-to-end demo on libz, used by the `e2e` CI job. | CI (`e2e` job) |
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
