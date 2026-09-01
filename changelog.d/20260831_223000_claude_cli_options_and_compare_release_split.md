### Changed

- **`abicheck/cli_options.py` split, closing another G38 Phase 15 file-split
  prerequisite**: the release-fanout/build-source/header-graph/evidence
  option-group cluster (`release_options`, `debug_resolution_options`,
  `adr027_compare_options`, `app_usage_scope_options`,
  `build_source_dump_options`, `header_graph_options`,
  `warn_deprecated_header_graph_flags`, `evidence_options`, and its
  `build_source_compare_options` alias) moved into a new sibling module,
  `abicheck/frontends/cli/options/release.py` — the same package this
  module's `contract.py`/`profiles.py`/`secondary_output.py` splits already
  live in, following the identical "deliberately a leaf, restate the `F`
  TypeVar rather than importing it back" shape. `cli_options.py` re-exports
  every name for back-compat (`cli_options.py`: 1977 -> 1504 lines).
- **`abicheck/cli_compare_release.py` split into three, closing the last
  G38 Phase 15 file-split prerequisite**: `architecture/debt.yaml` pins this
  file (and its pre-existing sibling `cli_compare_release_helpers.py`) at
  their exact adoption-time line count, not merely the AI-readiness
  2000-line hard cap, after an earlier attempt at this same split grew both
  frozen files. The per-pair/per-library comparison engine
  (`_run_compare_pair`/`_compare_one_library`/`_compare_release_libraries`/
  `_compare_release_parallel`/`_compare_release_sequential`/
  `_suppress_lockstep_soname_findings`) moved to a new sibling,
  `cli_compare_release_pairwise.py`; matrix-result collection, output
  finalization, gating, and input-discovery
  (`_collect_matrix_result`/`_finalize_release_output`/
  `_write_release_summary_file`/`_validate_suppression_early`/
  `_release_gating_buckets`/`_release_finding_dicts`/
  `_strip_diff_results_and_adjust_verdict`/`_prepare_compare_release_inputs`/
  `_discover_files`) moved to another, `cli_compare_release_matrix.py` — two
  new files instead of one so each stays under the AI-readiness 800-line cap
  a brand-new file is held to. Both import the subset of
  `cli_compare_release_helpers.py` they need directly, one-directionally,
  rather than routing through `cli_compare_release.py`, so neither pinned
  file grows. `cli_compare_release.py` re-exports every moved name for
  back-compat (`cli_compare_release.py`: 1995 -> 820 lines); several tests
  that monkeypatched a moved private helper by its old module path were
  updated to patch the function's new home instead, since the helper's own
  callers now resolve it through that module's namespace.
