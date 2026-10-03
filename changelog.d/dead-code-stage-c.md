### Removed

- **Python API that no command, Action or workflow reached.**
  `abicheck.product_baseline` (`pack_product_baseline`,
  `unpack_product_baseline`, `compare_product_directories`) is removed:
  compare a whole product with directory or package `compare`, and store its
  baseline with `--bundle-facts-out`. Also removed:
  `bundle_side_input.compare_bundle_sides`/`resolve_bundle_side` and their
  `LiveBundleInput`/`StoredBundleFactsInput` types (the CLI drives each
  operand shape directly), `abicheck.bundle_multibuild` (stored packages pair
  variants through `compare/variant_pairing.py`),
  `impact.use_cases.build_use_case_graph`/`join_use_case_graph`
  (`compare --use-cases` answers over the library graph), and
  `storage.identity.group_by_entity`.
- **`ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED`
  (`bundle_variant_coverage_regressed`).** Its only producer was the removed
  `bundle_multibuild` module, so no run could emit it. A policy file naming
  it as an override now fails to load like any unknown kind.

### Changed

- **`localize_symbol` (`graph explain`) names the static-archive members that
  define a symbol** as `defined_in_archive_members` (`[{archive, member}]`),
  from the archive pass's `OBJECT_DEFINES_SYMBOL` edges. An empty list means
  no archive evidence, not "defined nowhere". `defining_members` moved to
  `abicheck.model.source_graph_query`.
- **The reusable `update-main-baseline.yml` workflow computes its
  accepted-main cache key with `accepted_main_cache_key`** instead of a bash
  copy of it. `baseline-generation` is parsed by the new
  `abicheck.buildsource.baseline_publish.parse_baseline_generation` with
  `actions/baseline`'s rule (ASCII digits only), so `03` now keys as `-g3`,
  matching the generation the manifest records, and an invalid value fails
  the step before any cache is restored.
- **Test-only code moved out of the package:** `abicheck.source_smoke` is now
  `tests/source_smoke.py`; `sycl_context.decode_frontend_contexts`,
  `select_frontend_context` and `decode_and_select_frontend_context` are now
  `tests/_sycl_context_oracle.py`; and
  `compatibility_evaluation_frontend.cross_front_end_differences`/
  `unstatable_selectors` are now `tests/_cross_front_end.py`.
