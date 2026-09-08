### Changed

- **`validation/scripts/run_oneapi_scan.py`'s binary-tier oneAPI driver now
  invokes `abicheck compare BASE NEW --depth binary`** instead of
  `abicheck scan NEW --against BASE --depth binary` (plan
  `docs/contribute/plans/one-comparison-product.md` Phase 4 commit 4): a
  plain two-sided binary-depth comparison with no crosscheck/audit-only
  capability involved, so `compare` reproduces it exactly. Its JSON-parsing
  now reads `compare`'s report shape (`evidence_tiers`/`changes`) instead of
  `scan`'s (`coverage`/`diff`), verified against a real
  `compare --depth binary --format json` run.

