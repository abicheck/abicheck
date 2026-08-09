### Added

- **`DumpRequest` — a typed request for `dump`** (G33 Phase 5) —
  `abicheck.service.run_dump_request` takes one
  `DumpRequest` (an `InputSpec` plus `depth`/`dwarf_only`/`debug_format`/
  `frontend`/`follow_dependencies`/`frontend_context`) and applies the four
  steps that previously lived only inside the `dump` CLI command: collect-mode
  inference, inline L3-L5 build/source embedding, the dependency walk, and the
  depth floor (an explicit `depth` that was not reached raises instead of
  returning a weaker snapshot). It resolves through the same per-input
  primitives `compare` does, and validates through the same helpers
  `CompareRequest` does — so `dump` and `compare` now reject an identical
  mistake with identical text.

### Fixed

- **An `android` AST frontend no longer fails the whole extraction** — `android`
  is source-ABI only, with no header-AST path, so both pipelines already fall
  back to `auto` for the bare header backend. But an explicit
  `CompileContext.frontend` takes *precedence* over that argument inside
  `run_dump`, and the header-backend resolver rejects anything outside
  `castxml`/`clang`/`hybrid`/`auto` — so a run that named `android` died with
  "Unknown AST frontend 'android'" before any build/source evidence was
  embedded. The resolved compile context now drops a non-header-AST frontend,
  fixing the typed `DumpRequest`/`CompareRequest` path. The downgrade is narrowed to
  frontends that are *known* but header-less, and a per-input
  `compile.frontend` is now validated, so a typo still raises rather than
  silently running the default backend.
- **The typed path seeds the build's L2 include dirs, as the CLI does** — with
  headers plus `sources`/`build_info` but no explicit include dirs, the
  public-header parse could not see the include dirs the build already knows,
  so a `DumpRequest`/`CompareRequest` parsed less than the equivalent CLI
  invocation. A Tier-2 call never *executes* a build system to discover them,
  unlike the CLI: passive discovery of an existing compile database only.
- **L4 source-ABI replay invokes the compiler the request selected** — the
  typed path left `embed_build_source`'s `clang_bin` at its bare `"clang"`
  default, where the `dump` CLI and `scan_engine` both override it from
  `--gcc-path`/`--gcc-prefix`. On a hermetic or cross-toolchain host where
  only the requested compiler works, that made an omitted `depth` silently
  return a weaker snapshot and an explicit `depth="source"` fail, even though
  the caller supplied the right compiler.
- **`--follow-deps` under a sysroot searches the target, not the host** — the
  typed path passed no sysroot to the dependency resolver, so a cross/sysrooted
  extraction searched the host defaults and reported the target's dependencies
  unresolved. It now comes from the input's own compile context, as the CLI's
  `--sysroot` already did.
- **A one-build `scan` audit rejects comparison-only arguments up front** —
  `policy`/`policy_file`/`suppression_file`/`contract_evaluation` without
  `against` were already rejected by the engine, but only inside the spawned
  worker, so a `ScanRequest` caller got a sanitized unexpected error after
  paying for a process spawn instead of the usage error the CLI gives.
- **`dump_manifest` alongside `public_header_dirs` or `includes` now fails
  fast, as it already did for `headers`** — `dumper.dump()` itself rejects
  `dump_manifest` alongside any of `headers`/`extra_includes`/
  `public_header_dirs` (its names for `InputSpec.headers`/`includes`/
  `public_header_dirs`), but the Tier-2 pre-flight `validate()` only checked
  `headers`. A `DumpRequest`/`CompareRequest` combining a manifest with the
  other two passed `validate()` and failed late, deep inside extraction, as a
  generic `SnapshotError` instead of the same usage error `headers` already
  gets.

