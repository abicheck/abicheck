### Fixed

- **An explicit `--build-target` scoped L3/L4/L5 evidence collection but left
  the earlier L2 include-dir/compile-context seed unscoped, on `dump`,
  `scan`, and the typed Python API alike.** `embed_build_source` (the L3/L4/L5
  embed step) has always honored `build_targets`, but `dump`/`scan` both run
  a separate, earlier `collect_inline_pack()` call first —
  `l2_seed.seed_includes_and_fold_compile_context` (P0.3's L3→L2 fold) and,
  for the typed API, `derive_l2_compile_context`/`seed_l2_includes` via
  `service_input_resolution._seeded_compile_context`/`_seeded_includes` —
  to seed L2 include dirs and fold the build's compile context. None of
  these threaded `build_targets` through at all, on any caller. In a
  multi-target Bazel workspace this let an unrelated target's include dirs
  or conflicting dialect flags leak into the header parse despite
  target-scoped L3 evidence, potentially producing a snapshot parsed under
  the wrong compile context. Fixed by threading `build_targets` through
  `_l2_seed_config`/`_resolve_l2_seed_pack_args` and every function built on
  them (`derive_l2_include_dirs`, `derive_l2_compile_context`,
  `seed_l2_includes`, `seed_includes_and_fold_compile_context`), and wiring
  it from every real caller that already has the value: both `dump` call
  sites, `scan`'s new `--build-target` support, and the typed API's
  `InputSpec.build_targets`.
