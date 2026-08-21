### Changed

- **Internal: unified the last of the three known artifact-resolution
  cleanup-accumulator sites.** `service_input_resolution.
  _resolve_side_snapshot_impl` (shared by `compare`'s implicit-dump operand
  and `dump`'s typed pipeline) now uses the same `ResolvedArtifactPlan`
  session `perform_elf_dump`/`handle_non_elf_dump` already use — no
  observable behavior change (duplication-and-convergence plan, Phase 1
  Milestone A completion).
