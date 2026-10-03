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

### Fixed

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
