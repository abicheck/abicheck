### Added

- **`compare --dry-run` now previews evidence-collection cost** — a new
  "Cost preview" section projects the same per-layer (L0-L5) TU count and
  wall-clock estimate `scan --dry-run` already shows, reusing
  `service_scan.estimate_scan` directly and summing both operands' own
  projection (one-comparison-product.md #35, Phase 2f). Advisory only: it
  does not change `--budget` runtime enforcement, which remains `scan`-only.
