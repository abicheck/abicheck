### Removed

- `compare`'s exit decision, report `exit` block, typed-API `exit_decision`,
  scoped `--used-by`/`--required-symbol` gate and effective-config digest now
  resolve from one `EffectiveGate`. The internal resolvers no longer take an
  exit-code scheme beside a severity map, so the two can no longer be passed
  in disagreement; `add_effective_config_digest`/
  `effective_config_fields_from_raw` lost their `exit_code_scheme` override.
  No behavior change.
- Dead-code plan Stage F: the retired `scan` `--mode` preset resolver
  (`ScanMode`, `resolve_level`, `resolve_source_method`, `mode_preset`) and
  five helpers whose last caller went with #1477 or Stage E. "Explicit
  `--depth` to collect mode" now has one owner,
  `model.evidence_depth_levels.collect_mode_for_depth`, instead of three
  copies in `dump`, `compare`'s typed pipeline and the planner. No behavior
  change.
- Dead-code plan Stage G: removed the stale helpers that a plan or ADR still
  named after their path was replaced: the `fold_coverage_exit`/
  `fold_analysis_assurance_exit` wrappers (`policy.exit_decision.
  resolve_exit_decision` is the one fold), the Click-translating
  `_build_match_map`/`_resolve_release_package_side` wrappers (and
  `frontends/cli/release_variant_operand.py`), `atomic_copy`,
  `compute_section_summary` and its `BinarySummary` types, the castxml-only
  provenance predicates, `parse_user_depth`, `Suppression.selector_matches`,
  `ExportSet.destinations`, `scan_bazel_scoping_failure` and
  `_l5_payload_empty`. No behavior change.
- Removed `semantic_ir_legacy_adapter.legacy_record_ir`, an uncalled wrapper;
  `compare/record_layout.py` reads through `legacy_record_occurrences`.
- Dead-code plan library-API pass: removed the `TypeMetadataSource` protocol
  and its accessors (BTF/CTF reach the checker through
  `to_dwarf_metadata()`), and `EntityResolver.v1_id_for`;
  `buildsource.inputs_emit.write_inputs_pack` and
  `project_snapshot_store.read_project_manifest`, which only tests called,
  moved to `tests/`.
- Removed `EvidenceView.available_depths`, an unread restatement of the
  `--depth` ladder `evidence_depth.DEPTH_RANK` owns.

### Added

- `compare old.whl new.whl` checks each library against the NEW wheel's own
  claims when no `deployment.runtime_floors` is declared (G26/G27): the
  platform tag's glibc/musl/macOS floor and architecture, and the `numpy`
  requirement from `METADATA`. A binary needing a newer glibc than its
  `manylinux` tag allows now reports `platform_baseline_floor_raised`, and a
  NumPy C-API target above the declared `numpy` floor reports
  `numpy_metadata_understates_required_version` (with
  `numpy_abi_major_incompatible` across the 1.x/2.x boundary) — two kinds that
  had no emitter. `deployment.runtime_floors.NUMPY_REQUIREMENT` declares the
  requirement by hand.

### Fixed

- A directory/package `compare` no longer treats a non-binary file whose
  name merely contains `.so.` (`libfoo.so.c`, `libfoo.so.bak`) as a library.
  Such a file used to become a release member that failed to load, which
  failed the whole comparison and could displace the real `libfoo.so` that
  shared its match key. `libfoo.so` and `libfoo.so.<digits>` are still
  accepted by name (linker-script stubs); any other suffix is decided by the
  file's content.
- A directory/package `compare` no longer treats separate debug info as a
  library: anything inside a macOS `*.dSYM` bundle (whose DWARF companion is
  a real Mach-O with the library's own name) and any `*.debug` file. On macOS
  a `gcc -g` build directory used to yield two `libx.so` candidates and fail
  with an ambiguous match.

### Changed

- ADR-067 D6's additions-review floor is now an `ExitDecision` axis
  (`exit.additions_review_contribution`, reason `additions_review`, report
  schema 5.13) instead of a fold applied after the decision, so the report's
  `exit.code` and the process exit cannot disagree once the axis fires.
  `acknowledgment_gate.fold_additions_review_exit` is removed. No exit code
  changes today: no front end supplies acknowledgment records yet.
