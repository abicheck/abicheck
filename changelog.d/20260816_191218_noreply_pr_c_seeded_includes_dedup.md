### Fixed

- **`service_input_resolution.resolve_side_snapshot` (the shared per-input
  primitive `compare`'s implicit-dump operand and `dump`'s typed API,
  `run_dump_request`, both use) no longer collects L3 build evidence
  twice per side.** The L2 include-dir seed and the P0.3 L3→L2
  compile-context fold were two independent
  `buildsource.inline.collect_inline_pack()` calls, diverging from
  `buildsource.l2_seed.seed_includes_and_fold_compile_context()` — the
  one combined primitive the three CLI-side resolvers (`dump`'s ELF and
  PE/Mach-O paths, `scan`'s candidate resolution) already use for exactly
  this reason (a caller genuinely needing the zero-config inferred
  build-system query could self-deadlock on the same build-dir lock — see
  `AGENTS.md`'s "Known gaps"). This path never actually hit that timeout
  (Tier-2 API callers never allow the inferred query), but it was real,
  avoidable duplicated work and a real divergence from the shared
  primitive the other three call sites had already converged on. Fixed by
  merging `_seeded_includes`/`_seeded_compile_context` into one
  `_seeded_includes_and_compile_context()` that calls the same combined
  primitive. Part of CLI cleanup phase two's PR 3A (typed `dump`/`scan`
  convergence) — see the plan doc and `AGENTS.md`'s "Known gaps" for what
  of that PR remains open and why.
