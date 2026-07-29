### Added

- **`profiles.<id>.consumer_compile` (G34 Phase 0)** — an optional
  `.abicheck.yml` profile overlay, additive alongside the existing
  `compile:` block, declaring a separate client/consumer toolchain axis
  distinct from the producer/artifact toolchain the library binary was
  actually built with (e.g. a `.so` built with GCC 14 but contractually
  supporting a Clang 20 client under a different standard/standard-library).
  A profile with no `consumer_compile:` behaves exactly as today. Reaches
  `abicheck project plan`'s generated `run-plan.json` as its own
  `consumer_compile_gcc_path`/`consumer_compile_gcc_options` fields,
  resolved identically to (and independently of) `compile:`'s own pair.
  Schema/projection only in this change — see
  `docs/contribute/plans/g34-producer-consumer-compiler-profile-separation.md`
  for the remaining extraction/merge integration.
