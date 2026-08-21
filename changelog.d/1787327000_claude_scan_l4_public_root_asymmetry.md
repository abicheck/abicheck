### Fixed

- **`scan --against` a `dump`-produced baseline could report a spurious
  `source_decl_binary_symbol_mismatch`/`source_to_binary_mapping_changed`
  RISK finding on an otherwise-unchanged library, when the project's only
  `-H` input was a single header *file* with no accompanying directory and
  no `--public-header-dir`.** `dump`'s write-time embed and `compare`'s
  implicit-dump operand both derive their L4 source-ABI-replay public-header
  roots via `split_public_header_inputs` (every `-H` file/directory is a
  root, no directory required) — but `scan`'s candidate resolution derives
  the *same* parameter from `cli_scan_baseline._public_provenance_set`,
  which deliberately returns an empty root set for a lone `-H` file (a
  single header cannot establish a public *directory* boundary, and that
  restriction is a real, separately-tested contract for L2/crosscheck-origin
  classification — `test_lone_file_does_not_activate`). A `dump` baseline
  for such a project therefore correctly linked its L4 declarations to the
  binary's exported symbols, while `scan`'s own candidate for the identical,
  unchanged sources silently degraded to zero matches — an asymmetry the
  comparison read as a real symbol/mapping change.

  Fixed by giving L4 replay its own, wider root set
  (`service_input_resolution.embed_side_build_source`'s new
  `l4_public_headers`/`l4_public_header_dirs` parameters, defaulted to the
  existing `public_headers`/`public_header_dirs` for every pre-existing
  caller) rather than changing `_public_provenance_set`'s own deliberate,
  pinned default. `scan_engine._build_new_snapshot` now unions its
  `-H`-derived (`split_public_header_inputs`) roots with its own narrower,
  provenance-derived set specifically for the L4 embed call — L2/crosscheck
  origin classification (and every other scan default) is unaffected.

  Regression coverage: `tests/test_scan_l2_cleanup_ordering.py::
  test_scan_candidate_widens_l4_roots_with_a_lone_header_file` (confirmed to
  fail against the pre-fix code) and the pre-existing
  `tests/test_dump_scan_l3_comparability.py::
  test_scan_against_real_dump_baseline_is_comparable_on_unchanged_source`
  end-to-end integration test, which this fix restores to green.
