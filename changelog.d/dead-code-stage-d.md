### Removed

- **`ChangeKind.FRAME_REGISTER_CHANGED` (`frame_register_changed`) and the
  CFI pass behind it (409 → 408 kinds).** The `.eh_frame`/`.debug_frame` pass
  that fed it, and the callee-saved-register fallback for
  `calling_convention_changed`, never ran on a real comparison: the unified
  DWARF parse did not call it, and it asked pyelftools for methods that do not
  exist, so it returned nothing when it was called. Enabling a corrected pass
  over the 169 built catalog cases added one finding, a false
  `frame_register_changed` that turned `case15_noexcept_change` from
  `COMPATIBLE_WITH_RISK` into `BREAKING`, and did not detect the GCC `ms_abi`
  change it was meant to (case64 stays a known gap). A policy file naming the
  kind now fails to load like any unknown kind. `AdvancedDwarfMetadata` loses
  the always-empty `frame_registers`/`callee_saved_regs` fields; snapshots that
  carry them still load.
- **`abicheck.dwarf_advanced.parse_advanced_dwarf` and the
  `abicheck.dwarf_unified.parse_advanced_dwarf` shim.** Use
  `abicheck.dwarf_unified.parse_dwarf`, which returns both halves from one
  ELF open.

- **More Python helpers no production path called.**
  `abicheck.serialization.load_snapshot_document` (read the file with
  `abicheck.snapshot_io.read_snapshot_text` and `json.loads`),
  `pe_metadata.is_pe`/`macho_metadata.is_macho` (use
  `abicheck.binary_utils.detect_binary_format`),
  `policy.severity.classify_change_object` (use
  `classify_effective_change`), `model.surface_facts.is_unknown`,
  `AbiSnapshot.func_by_mangled` (use `function_map.get`), and about forty
  other unreferenced accessors and wrappers; see
  `docs/contribute/plans/dead-code-and-single-owner.md`, Stage D.
- **Parameters left on the shared input resolver by `scan`'s removal.**
  `workflows.artifact.execute._resolve_side_snapshot_impl`,
  `resolve_side_snapshot` and `embed_side_build_source` lose the keyword
  parameters only `scan` passed (`build_config_locally_trusted`,
  `baseline_reuse_hint`, `l4_public_headers`, ...; 17 in all), and
  `service_input_resolution.BaselineReuseContext`/
  `resolve_baseline_compile_context` go with them. `compare` and `dump`
  behave as before; see the plan's Stage E.
- **Renderer parameters no caller passed.** `to_markdown`,
  `to_review_digest`, `to_sarif`/`to_sarif_str`, `to_junit_xml` and
  `generate_html_report` lose `report_document`, the pre-envelope form of
  sharing one report document: pass `envelope=` (a
  `report.build.build_report_envelope` result), as every `compare` render
  does. `reporter.to_json`, `sarif.to_sarif_str` and
  `stack_report.stack_to_json` lose `indent` (always 2);
  `junit_report.to_junit_xml_multi` loses `report_mode` (the release
  fan-out it serves rejects `--view root-cause` first);
  `html_report.write_html_report` loses `demangle`.
- **Further parameters no production call passed.** Each function now runs
  what every caller already got. The ones a Python caller may notice:
  `workflows.aggregate.execute.aggregate`/`aggregate_reports_dir` lose
  `on_missing_required`/`on_unexpected_target` (state the policy in the
  manifest's `gate` block, as `abicheck aggregate` does; the aggregate
  report's `effective_policy.source` is no longer `"explicit"`, and the
  schema still accepts that value from an older report);
  `resolver.resolve_dependencies` and `stack_checker.check_single_env` lose
  `max_file_size`; `policy.severity.compute_gate_decision` now requires a
  `SeverityConfig` (the verdict-only scheme has no category to blame, and
  every caller handled that case first); `buildsource.source_replay.
  run_source_replay` loses `target_id`/`library`/`forced_public`, so its
  `target` scope is every unit attached to a build target;
  `buildsource.header_graph.build_header_only_graph` takes only
  `ast_projection=` (project a parsed tree with
  `project_header_graph_ast` first); `sycl_metadata.parse_sycl_metadata`
  loses `extra_plugin_paths` (use `SYCL_PI_PLUGINS_DIR`); the pattern
  pre-scan's input resolvers lose their `changed_paths` narrowing, whose
  only caller was `scan`. The rest are internal; the plan's Stage E
  (`docs/contribute/plans/dead-code-and-single-owner.md`) lists them.

### Fixed

- **Coverage and scope notices no longer advise the setting a run already
  has.** The contract-coverage notice ended "or set contract.unresolved=warn
  to accept incomplete coverage" even when it opened "Accepted by
  contract.unresolved=warn", and the incomplete-scope notice told a run
  under `scope.on_incomplete: block` to set `block`. Each now offers only a
  change that would change something.

- **The preprocessor pre-scan runs the compiler `.abicheck.yml` configures.**
  `compile.compiler` (and a compiler prefix) selected the compiler for L4
  source replay, but the pre-scan `compare` runs over the same compile units
  always ran a bare `clang++`: with `icpx` or a cross prefix it probed with
  the host compiler, or reported `clang++` missing on a host that has only
  the configured one. It now uses the same selection as L4, except that a
  CL-mode driver (`clang-cl`) falls back to `clang++`, since the pre-scan
  passes GNU-mode flags.

- **`inline_body_references_renamed_member` follows the policy's
  `internal_namespaces`.** The pimpl inline-accessor detector used its own
  three-name list (`detail`, `impl`, `internal`) instead of the run's
  convention: a project declaring `internal_namespaces: [priv]` never got the
  finding, and neither did `__detail::`/`_impl::` types with no
  configuration, although every other internal-namespace check treats them
  as internal.

- **`project history --policy` takes a policy document, as `compare --policy`
  does.** A document path was passed on as a profile name and read as
  `strict_abi`, so its `overrides:` never reached the pairwise comparisons
  and its `versioning:` block never reached the deprecation-window check:
  `deprecation_compliance` was always empty from the CLI. The document now
  drives both; a profile name works as before.

- **`compat check` labels source-only HTML reports as source reports.** With
  `-old-style`, the report written for `-source` and the one written to
  `-src-report-path` were titled "Binary compatibility report" and carried
  `kind:binary` in the metadata comment ABICC tooling reads. They now say
  `Source` and `kind:source`, as abi-compliance-checker's do.

- **`compare --no-baseline` now applies the `.abicheck.yml` settings
  two-sided `compare` applies.** `compile.lang`, the `debug:` block
  (`format`, `dwarf_only`, `debuginfod`, `debuginfod_url`, `pdb_path`),
  `source.method` and `scope.public_symbols` have no CLI flag any more, and
  the audit resolved the config but never read them: a `compile.lang: c`
  project's headers were audited as C++, detached debug info configured
  under `debug:` was never looked up, and `scope.public_symbols` forced
  nothing public. Both `compare` shapes now read these through one function.
  Still not applied on the audit: `scope.show_redundant`.

- **PDB forward references resolve to the same definition the layout comes
  from.** When a PDB carried two definitions of one struct name (an ODR
  violation), `pdb_metadata` took the first as the struct's layout while the
  type database linked forward references to the last, so a member typed
  through a forward reference reported the other definition's size. Struct
  and enum names also shared one map, so a struct and an enum with the same
  name could cross-link, and an enum forward reference never followed to
  its definition's underlying type. Forward references now link per kind to
  the first complete definition, and every name and size lookup goes
  through that link.
- **`compare --dry-run` on a directory pair fails where the real run would.**
  The release preview repeated input discovery and answered an empty plan
  for a directory with no supported input; it now uses the same discovery as
  the comparison and reports the same error.
- **A release-level suppression now relabels every copy of a finding.** The
  lockstep-SONAME suppression a directory/package `compare` applies after
  each member's disposition ledger closed matched findings by object, so a
  finding the ledger had recorded through a second producer (an alias of the
  same observation) kept its earlier disposition in the audit while the
  report hid it. `DispositionLedger.with_suppressed` now resolves aliases the
  way every other ledger lookup does.
