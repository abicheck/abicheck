### Added

- **`DumpRequest` — a typed request for `dump`, and the MCP parity it
  unlocks** (G33 Phase 5) — `abicheck.service.run_dump_request` takes one
  `DumpRequest` (an `InputSpec` plus `depth`/`dwarf_only`/`debug_format`/
  `frontend`/`follow_dependencies`/`frontend_context`) and applies the four
  steps that previously lived only inside the `dump` CLI command: collect-mode
  inference, inline L3-L5 build/source embedding, the dependency walk, and the
  depth floor (an explicit `depth` that was not reached raises instead of
  returning a weaker snapshot). It resolves through the same per-input
  primitives `compare` does, and validates through the same helpers
  `CompareRequest` does — so `dump` and `compare` now reject an identical
  mistake with identical text.
- **`abi_dump` reaches the whole `dump` evidence surface** — the MCP tool
  gained `depth`, `sources`, `build_info`, `dump_manifest`,
  `public_header_dirs`, `include_dependencies`, `dwarf_only`, `debug_format`
  and the `ast_frontend`/`gcc_path`/`gcc_prefix`/`gcc_options`/`sysroot`/
  `nostdinc`/`frontend_context` compile-context family, having previously
  accepted a five-argument subset of what `abicheck dump` accepts.
- **`abi_scan` gained the `--against` config surface and the same compile
  context** — `build_info`, `policy`, `policy_file`, `suppression_file`,
  `contract_evaluation`, `contract_mode`, and the compile-context family, so a
  scan's baseline comparison is configurable exactly as `abi_compare` is
  (ADR-049 Phase 5 §6.4).
- **`abi_compare` gained `contract_mode`** — ADR-049 Phase 6's `--contract`
  domain selector (`public`/`exports`/`all`), which `CompareRequest` already
  carried but no MCP caller could set.

### Fixed

- **An `android` AST frontend no longer fails the whole extraction** — `android`
  is source-ABI only, with no header-AST path, so both pipelines already fall
  back to `auto` for the bare header backend. But an explicit
  `CompileContext.frontend` takes *precedence* over that argument inside
  `run_dump`, and the header-backend resolver rejects anything outside
  `castxml`/`clang`/`hybrid`/`auto` — so a run that named `android` died with
  "Unknown AST frontend 'android'" before any build/source evidence was
  embedded. The resolved compile context now drops a non-header-AST frontend,
  fixing the new `abi_dump(ast_frontend="android", ...)` path and the
  pre-existing typed `CompareRequest` one alike.
- **`build_info` is held to `ABICHECK_MCP_MAX_FILE_SIZE`** — it accepts a
  `compile_commands.json` (or a Bazel jsonproto), not only a directory, and the
  build-source loader parses it, so an oversized build-info artifact bypassed
  the limit every other file-shaped MCP input is held to. The guard now lives
  in the one helper every evidence path goes through.
- **`abi_scan`'s compile-context arguments are validated** — `ast_frontend` and
  `frontend_context` were copied into `ScanRequest` unvalidated (it has no
  `validate()` of its own), so a typo or an uppercased `"DEVICE"` survived into
  the spawned scan worker to be ignored or resurface as a generic failure. Both
  tools now reject the same mistake with the same text `abi_dump` produces.
- **A nonexistent `sources`/`build_info`/`compile_db` path is now a usage
  error on the MCP tools** — these infer an evidence-collection depth from
  being set at all, and only an *explicit* depth arms the depth floor, so a
  typo silently collected nothing and still reported success. The `dump`/`scan`
  CLIs reject the same input via `click.Path(exists=True)`.

### Changed

- **`abi_dump` routes through the Tier-2 chokepoint** — it builds one
  `DumpRequest` instead of calling `mcp_server._resolve_input` directly, the
  same move ADR-055 D4 made for `abi_compare`. Its MCP-local guards (path
  containment, file size, and the linker-script pin that keeps the size check
  authoritative) are unchanged, carried as request fields.
