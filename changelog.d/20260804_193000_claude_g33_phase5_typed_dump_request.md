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
  pre-existing typed `CompareRequest` one alike. The downgrade is narrowed to
  frontends that are *known* but header-less, and a per-input
  `compile.frontend` is now validated, so a typo still raises rather than
  silently running the default backend.
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
- **The typed path seeds the build's L2 include dirs, as the CLI does** — with
  headers plus `sources`/`build_info` but no explicit include dirs, the
  public-header parse could not see the include dirs the build already knows,
  so a `DumpRequest`/`CompareRequest` parsed less than the equivalent CLI
  invocation. A Tier-2 call never *executes* a build system to discover them,
  unlike the CLI: passive discovery of an existing compile database only.
- **`--follow-deps` under a sysroot searches the target, not the host** — the
  typed path passed no sysroot to the dependency resolver, so a cross/sysrooted
  extraction searched the host defaults and reported the target's dependencies
  unresolved. It now comes from the input's own compile context, as the CLI's
  `--sysroot` already did.
- **`abi_scan` rejects `ast_frontend="android"`** rather than silently running
  an ordinary auto/castxml pass: it is source-ABI-replay only, and a scan has
  no request-level frontend to carry it into replay. `abi_dump` still accepts
  it, because `DumpRequest.frontend` does.
- **A one-build `abi_scan` audit rejects comparison-only arguments up front** —
  `policy`/`policy_file`/`suppression_file`/`contract_evaluation` without
  `against` were already rejected by the engine, but only inside the spawned
  worker, so the caller got a sanitized unexpected error after paying for a
  process spawn instead of the usage error the CLI gives.
- **The MCP `abi_compare` receipt records the selected `contract_mode`** — the
  persisted `contract_context` named the built-in default domain even when the
  caller selected `exports`/`all`, which since ADR-049 Phase 7 is the domain
  that decides the verdict and the coverage gate a replay consumer reads it for.
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
