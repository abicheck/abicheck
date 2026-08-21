### Changed

- **Internal: unified `dump`'s PE/Mach-O and ELF resource-cleanup handling.**
  `handle_non_elf_dump`'s hand-rolled pending-cleanup list is now a
  `ResolvedArtifactPlan` session, the same primitive `perform_elf_dump`
  already uses — no observable behavior change, just one fewer duplicated
  cleanup-accumulator pattern (duplication-and-convergence plan, Phase 1).
