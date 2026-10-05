### Changed

- Comparison: each declaration's qualified name is now derived once per
  comparison (a per-snapshot table) and the reconciliation alias index is
  shared across reconciliation slots, halving the qualified-name derivations
  per declaration (12.8 → 6.3 on the cost-budget workloads). No finding or
  verdict changes.
