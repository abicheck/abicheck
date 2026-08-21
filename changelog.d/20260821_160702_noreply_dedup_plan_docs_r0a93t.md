### Added

- **`ResolvedArtifactPlan` (`abicheck/artifact_plan.py`) now optionally
  carries the resolved-fact fields the dedup-and-convergence plan's target
  artifact-resolution architecture names** — normalized binary format,
  language, requested/effective header-AST backend, requested depth,
  effective collect mode, and public-header scope — alongside the cleanup
  session it already owned. All new fields are optional keyword-only
  arguments defaulting to `None`/`()`, so existing bare `ResolvedArtifactPlan()`
  construction is unaffected.
  `service_dump_pipeline.resolve_dump_request()` now attaches one,
  populated from the same facts it already returns on the new
  `ResolvedDumpRequest.artifact_plan` field (additive, defaulted — no
  existing caller breaks). Nothing reads this yet; it establishes the
  shape a future `dump --dry-run` migration can build from. The L3→L2
  compile-context fold's own resource lifetime deliberately stays inside
  execution, not resolution — moving it would break `dump --dry-run`'s
  already-documented "never raise except on a usage error" guarantee, so
  the plan doc's Phase 1 item 1 is updated with that finding rather than
  the fold being moved.
