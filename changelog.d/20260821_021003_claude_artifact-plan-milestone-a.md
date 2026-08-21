### Changed

- **Internal: `perform_elf_dump`'s (`abicheck dump` ELF path) temp-build-dir
  cleanup now goes through a new, reusable `ResolvedArtifactPlan` primitive**
  (`abicheck/artifact_plan.py`) instead of a hand-rolled
  `list[Callable[[], None]]` accumulator drained by a manual `try`/`finally`
  — Phase 1 (Milestone A) of the duplication-and-convergence plan's
  "Finish artifact-resolution convergence" work. Purely internal:
  cleanup thunks and timing are unchanged, no CLI-visible or Python-API
  behavior changes.
