### Documentation

- **Added a worked toolchain-matrix reference example** for the
  `profiles.<id>.compile` → `run-plan generate --toolchain-bindings`
  wiring (task #9 of the P1 toolchain-profile audit, following up on the
  `run-plan` compile-overlay projection). A committed `.abicheck.yml` +
  `toolchain-bindings.yml` pair (`tests/fixtures/run_plan/toolchain_matrix/`)
  declares two contract profiles pinning two different real toolchains
  (GCC via a resolved `binding`, Clang with a full `standard`/`stdlib`/
  `abi_macros`/`args` overlay on top of its own binding) for the same
  target, with a README walking through the exact CLI invocation and
  expected `compile_gcc_path`/`compile_gcc_options` output. Regression-
  tested (`tests/test_run_plan.py::TestToolchainMatrixFixtureExample`) so
  the README and fixtures can't silently drift apart; linked from
  `docs/reference/run-plan-schema.md`. Lives under `tests/fixtures/`
  rather than `examples/case*/` — it demonstrates config/toolchain
  resolution, not an ABI comparison, so it doesn't fit the compiled
  `v1`/`v2` ground-truth catalog's verdict/expected-kinds schema.
