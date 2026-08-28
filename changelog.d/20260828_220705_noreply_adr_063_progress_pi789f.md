### Changed

- **`dump --dry-run` now renders from the same resolved `DumpRequest` the
  real run consumes.** `cli_dump_helpers.render_dump_dry_run()` used to be
  handed fifteen independently-threaded primitives (`so_path`, `headers`,
  `sources`, `build_info`, `depth`, `collect_mode`, `header_backend`,
  `dump_manifest`, ...) that `dump_cmd` re-derived by hand; it now takes the
  real `ResolvedDumpRequest` object `resolve_dump_request_for_cli` already
  builds and reads those fields off it. No CLI-visible behavior change —
  verified against the real `g++`/clang toolchain
  (`tests/test_dump_cli_typed_api_parity.py`, 16/16 green; that file is
  itself clang-only) plus the wider integration suite's real castxml
  coverage. ADR-063 Phase 1 ("finish the `dump`/`scan` typed-API
  convergence"); see
  `docs/contribute/known-gaps.md`'s "PR C" entry for the still-open routing
  half this doesn't close.

