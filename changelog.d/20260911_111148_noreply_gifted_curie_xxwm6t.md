### Fixed

- **A `check-target` `gate-mode: advisory` `compare --no-baseline` audit's
  persisted document no longer contradicts itself, or reports a stale
  blocking exit code** — `exit_axes.contract_coverage` is now neutralized
  alongside `audit_gate`/`analysis_assurance` (it mirrored the same value
  the existing generic neutralization loop already zeroes at the
  document's dedicated `contract_coverage_exit_contribution` key, but left
  its own `exit_axes` copy stale), and the document's own top-level
  `exit_code` is now recomputed from the fully-neutralized axes instead of
  staying at its original, pre-neutralization value. A genuine
  never-completed axis (e.g. `evidence_contract`) is still never
  neutralized, so the recomputed `exit_code` still reflects a real
  operational failure.

