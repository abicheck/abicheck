### Added

- **ADR-068 D3 / plan P2 (`one-comparison-product.md`)**: a new
  `FindingEvolution` state (`introduced`/`resolved`/`persistent`/
  `not_evaluated`) on the canonical finding model, carried by
  `Change.finding_evolution`, and the first cross-source hygiene check
  (`unversioned_exported_symbol`) migrated onto `compare()`'s own pipeline
  via the new opt-in `compare(..., cross_source_checks=True)` parameter
  (`workflows.cross_source_evolution.compute_cross_source_evolution`). The
  check now runs independently against OLD and NEW rather than only against
  a single candidate binary (as it does today via `scan`), and the two
  one-sided results are folded into one evolution-stated finding set.
  `not_evaluated` is mandatory, not a convenience: a hygiene problem present
  on both real releases never reads as `introduced` merely because one side
  (e.g. a stripped/ELF-only baseline) lacked the evidence to confirm it --
  reporting a pre-existing problem as new would be a manufactured finding,
  which `vision.md` forbids outright. Authority is unchanged (ADR-028 D3 /
  ADR-035 D1): these findings keep their ordinary `RISK` default verdict
  regardless of evolution state. `compare --format json`'s report gains an
  additive `finding_evolution` field on the affected `change` entries plus a
  top-level `cross_source_evolution` summary object (schema 3.4), present
  only when the caller opts in -- off by default, so every existing caller's
  output is unchanged. The other ten cross-source checks `scan` owns remain
  unmigrated; this lands the vertical slice (model + one real producer + one
  real report projection) the plan's prerequisite P2 calls for.
