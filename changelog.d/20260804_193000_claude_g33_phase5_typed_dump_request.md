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

### Changed

- **`abi_dump` routes through the Tier-2 chokepoint** — it builds one
  `DumpRequest` instead of calling `mcp_server._resolve_input` directly, the
  same move ADR-055 D4 made for `abi_compare`. Its MCP-local guards (path
  containment, file size, and the linker-script pin that keeps the size check
  authoritative) are unchanged, carried as request fields.
