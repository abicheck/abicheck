### Changed

- Began ADR-061 Phase 3 (artifact-workflow convergence) by moving the shared
  `ResolvedArtifactPlan` cleanup-thunk session type from the flat
  `abicheck/artifact_plan.py` into `abicheck.workflows.artifact.contracts`,
  the `contracts.py` half of the target `Request -> ResolvedPlan -> Result`
  split for dump/scan artifact resolution. The module had zero first-party
  imports, so the move is purely mechanical: its four call sites
  (`service_dump_pipeline.py`, `service_input_resolution.py`,
  `cli_dump_helpers.py`, `cli_dump_non_elf.py`) now import
  `ResolvedArtifactPlan` from `abicheck.workflows.artifact` and are otherwise
  unchanged. The larger migration of those call sites' own owning modules
  into `workflows/` stays deferred — `service_dump_pipeline.py` and
  `service_input_resolution.py` still reach into `cli_dump_helpers.py`
  (a `frontends`-destined module), which a same-pass move would turn into a
  `workflows -> frontends` inversion.
