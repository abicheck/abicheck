### Changed

- **`validation/scripts/run_oneapi_scan.py`'s binary-tier oneAPI driver was
  investigated for migration to `abicheck compare BASE NEW --depth binary`**
  (plan `docs/contribute/plans/one-comparison-product.md` Phase 4 commit 4),
  but reverted (Codex review, fresh evidence): `compare`'s automatic
  cross-source-checks stage has no opt-out and no advisory-only stripping
  for a baseline comparison, the same gap that keeps the GitHub Action's
  own `mode: scan` on the legacy `scan` CLI. Reproduced directly —
  `compare --depth binary` on an identical snapshot pair reported
  `COMPATIBLE_WITH_RISK` where `scan --against --depth binary` reported
  `NO_CHANGE`, not depth-gated. The driver stays on
  `abicheck scan NEW --against BASE --depth binary`, unchanged.
