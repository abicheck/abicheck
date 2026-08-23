### Fixed

- **`--bundle-facts-out` (G38 Phase 2) no longer joins
  `cli_compare_release_helpers.py` into the CLI-registration import cycle
  (CI, `import-cycle-growth`).** The stranded-library snapshot fix routed
  `write_bundle_facts_out()` through `cli_resolve._resolve_input`, the
  approved Tier-2 CLI wrapper — correct per the `cli-contract` check, but
  `cli_resolve` (and every other Tier-2 entry point) already sits inside
  `scripts/check_ai_readiness.py`'s large, baselined CLI-registration
  import cycle, so importing it from `cli_compare_release_helpers.py` — a
  module deliberately kept a leaf (see its own module docstring) — pulled
  that leaf into the cycle for the first time, which the
  `import-cycle-growth` gate correctly rejected as new SCC membership.
  `write_bundle_facts_out()` now takes a `resolve_stranded_library`
  callable instead of performing the resolve itself; `cli_compare_release.py`
  (already a member of that cycle) builds the callable and owns the actual
  `cli_resolve`/`elf_metadata` imports and the resolve-or-degrade logic.
