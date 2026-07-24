# Environment Variables

`abicheck` is configured primarily through CLI flags and the
[`.abicheck.yml` config file](config-file.md). A small set of environment
variables tune behaviour that is awkward to express as a per-run flag —
parallelism, memory budgets, the build-injection wrapper, the MCP server, and
debug-info resolution.

Conventions used below:

- **Default** is the value used when the variable is unset (or empty, where
  noted).
- Unless stated otherwise, an unparsable value falls back to the default rather
  than raising.
- "Module" is the code that reads the variable (the source of truth for its
  behaviour).

> **Note:** `ABICHECK_BUILD_DIR` and `ABICHECK_INPUTS_VERSION` appear in the
> code but are **module constants, not environment variables** — abicheck never
> reads them from the environment. They are not listed here.

---

## AST / header parsing (L2)

These affect header parsing (the L2 backend), i.e. any command that dumps or
compares from C/C++ headers (`dump`, `compare` with `--sources`/headers,
`scan`).

| Variable | Values | Default | Effect | Module |
|----------|--------|---------|--------|--------|
| `ABICHECK_AST_FRONTEND` | `castxml`, `clang`, `hybrid` (any other value is ignored) | unset → resolves to `castxml` | Pins the AST frontend when the request is `auto` (no explicit `--ast-frontend`). An explicit `--ast-frontend castxml`/`clang`/`hybrid` on the CLI is honoured verbatim and takes precedence over this variable. Does **not** suppress the automatic castxml→clang fallback on a castxml toolchain-version / direct-include error, which only happens when the frontend was auto-selected (no flag *and* no `castxml`/`clang`/`hybrid` pin here). `hybrid` (G28 Phase 3) runs both castxml and clang and merges them — needs both tools installed, never auto-selected by `auto` itself. | `dumper.py` (`_resolve_header_backend`); flag in `cli_options.py` |
| `ABICHECK_AUTO_SYSTEM_INCLUDES` | truthy / falsey (`0`, `false`, `no`, `off` disable) | `1` (enabled) | When enabled, abicheck probes the host compiler for its system include search paths and feeds them to the castxml/clang frontend. Set to a falsey value to suppress the probe (e.g. a hermetic build that supplies its own `-isystem`/`--sysroot`). | `dumper_sysinc.py` (`_auto_system_includes_enabled`) |
| `ABICHECK_CLANG_LAYOUT_TOOL` | path to a compiled binary | unset → enrichment skipped | Opt-in path to the G28 Phase 4 companion tool (`tools/clang-layout-tool/`, built separately with LibTooling — never a default/hard dependency). When set and the snapshot's L2 backend is `clang` or `hybrid`, enriches its `RecordType`s with real field offsets/vtable-pointer placement clang's own `-ast-dump=json` never computes. Falls back to a bare `abicheck-clang-layout-tool` on `PATH` if unset. Any failure (missing binary, compile error, timeout) silently skips enrichment — never a hard error. | `clang_layout_tool.py` (`find_layout_tool_bin`) |
| `ABICHECK_ALLOW_UNSUPPORTED_CASTXML` | truthy (`1`, `true`, `yes`, `on`) | unset → gate enforced | The CastXML version gate (`castxml_policy.py`) rejects an authoritative L2 CastXML scan whose resolved `castxml --version` falls outside the supported range (`>=0.6.11,<0.8.0`, bundled/linked Clang `>=18`) — notably the legacy PyPI `castxml` distribution — **before** any header is parsed. This is an explicit, exploratory-mode-only opt-in to proceed *with castxml anyway*; the resulting snapshot's `ast_toolchain_supported` is `false` with `ast_toolchain_unsupported_reasons` recording why, so it is not silently indistinguishable from a normal supported scan. This is not the only way past the gate: when the frontend was auto-selected (no explicit `--ast-frontend`/`ABICHECK_AST_FRONTEND` pin) and `ABICHECK_ALLOW_AST_FALLBACK=1` is also set, the gate failure instead triggers a graceful fallback to the clang backend rather than a hard error — see the `ABICHECK_AST_FRONTEND` row above and `--allow-ast-frontend-fallback` in [CLI usage](../user-guide/cli-usage.md). With neither opt-in set, the gate fails closed. | `dumper_toolchain.py` (`_allow_unsupported_castxml_enabled`), `dumper.py` (`_castxml_dump`) |

See the `--ast-frontend` flag in [CLI usage](../user-guide/cli-usage.md).

---

## L4 source-replay parallelism & memory

These affect the L4 source-ABI replay (`dump --sources`, `compare`/`scan` at a
source depth). See [Producing source facts](../user-guide/producing-source-facts.md)
and [Build & source data](../concepts/build-source-data.md).

| Variable | Values | Default | Effect | Module |
|----------|--------|---------|--------|--------|
| `ABICHECK_L4_JOBS` | positive integer (`1` forces serial) | unset → auto: `min(n_units, cpu, 8)` | Worker count for the parallel L4 extract phase. An explicit value is clamped to the oversubscription ceiling **and** to the available-memory cap; the auto default is also memory-capped. An unparsable value falls back to `1`. Clamps are logged, never silent. | `buildsource/source_replay.py` (`_l4_jobs`) |
| `ABICHECK_L4_EXECUTOR` | `thread`, `process` | `thread` | Selects the executor for the L4 extract phase. `process` uses a `ProcessPoolExecutor` to parallelize the GIL-bound clang-AST post-processing (opt-in; validate the win before relying on it). Any unrecognized value falls back to `thread`. | `buildsource/source_replay.py` (`_l4_use_process_pool`) |
| `ABICHECK_L4_JOB_MEM_GIB` | float GiB (floored at `0.25`) | `3.0` | Per-worker RAM budget used to compute the memory cap on L4 (and L5 call-graph) workers, so a template-heavy TU's multi-GiB clang JSON AST cannot OOM-kill the replay. Available memory is `min(/proc/meminfo MemAvailable, cgroup v2/v1 headroom)` (Linux only). An unparsable value falls back to the default. | `buildsource/source_replay.py` (`_l4_job_mem_budget_gib`) |
| `ABICHECK_L4_CACHE_DIR` | directory path | unset → no persistent cache dir | Persists the per-TU L4 source-ABI cache across runs (the CI-friendly knob — point it at a restored cache directory). Used only when an explicit `--source-abi-cache-dir` is not given; the flag wins when both are present. | `buildsource/inline.py` |

---

## Other scan-phase parallelism

| Variable | Values | Default | Effect | Module |
|----------|--------|---------|--------|--------|
| `ABICHECK_PATTERN_SCAN_JOBS` | `auto`, `0`, `1`, or a positive integer | unset / `auto` → `min(cpu, 8)` above a 256-file floor, else serial | Worker count for the lexical (compiler-free) ABI-risk pattern pre-scan. `0`/`1` force serial (CI/test determinism, constrained sandboxes); `N` caps at `N` (still serial below the file floor). Always serial inside a daemonic process. | `buildsource/pattern_scan.py` (`_resolve_scan_jobs`) |
| `ABICHECK_CALL_GRAPH_JOBS` | positive integer | unset → `min(n_units, cpu, 8)` | Overrides the CPU-derived worker count for the best-effort L5 clang call-graph pass. Capped by `min(n_units, N, max(8, 2×cpu))` and by the shared L4 memory cap (`ABICHECK_L4_JOB_MEM_GIB`). An unparsable value falls back to `1`. | `buildsource/call_graph.py` (`_call_graph_jobs`) |

---

## Build-injection wrapper (`abicheck-cc`, Flow 2)

Read by the `abicheck-cc` compile wrapper, which stays argv-transparent and is
therefore configured entirely by environment. See
[Producing source facts](../user-guide/producing-source-facts.md).

| Variable | Values | Default | Effect | Module |
|----------|--------|---------|--------|--------|
| `ABICHECK_INPUTS_DIR` | directory path | `abicheck_inputs` | Output directory for the emitted `abicheck_inputs/` facts pack. | `cc_wrapper.py` |
| `ABICHECK_CC_EXTRACTOR` | `auto`, `clang`, `castxml` | `auto` | Source-ABI extractor the wrapper uses to capture per-TU facts. | `cc_wrapper.py` |
| `ABICHECK_CC_HEADERS` | `os.pathsep`-joined header roots | `""` (empty) | Public-header roots used to classify which decls belong to the public surface (ADR-015). | `cc_wrapper.py` |
| `ABICHECK_CC_LIBRARY` | string | `""` (empty) | Library name stamped into the pack manifest / target id. | `cc_wrapper.py` |
| `ABICHECK_CC_VERSION` | string | `""` (empty) | Version stamped into the pack manifest. | `cc_wrapper.py` |
| `ABICHECK_CC_DISABLE` | any non-empty value | unset (extraction on) | When set, the wrapper is a pure pass-through: it runs the real compile and skips all fact extraction. | `cc_wrapper.py` |

> Fact extraction is best-effort and never fails the build: a missing front-end
> or a parse error degrades to a warning on stderr and preserves the compiler's
> exit code.

---

## MCP server

Read at import time by the MCP server (`abicheck mcp`). An invalid integer here
raises a clear error (these two are the exception to the "fall back to default"
rule).

| Variable | Values | Default | Effect | Module |
|----------|--------|---------|--------|--------|
| `ABICHECK_MCP_TIMEOUT` | integer (seconds) | `120` | Maximum seconds for a single MCP tool invocation (`abi_dump` / `abi_compare`). | `mcp_server.py` (`MCP_TIMEOUT`) |
| `ABICHECK_MCP_MAX_FILE_SIZE` | integer (bytes) | `524288000` (500 MB) | Maximum accepted input file size for MCP tools. | `mcp_server.py` (`MCP_MAX_FILE_SIZE`) |

See [MCP integration](../user-guide/mcp-integration.md).

---

## Debug-info resolution

| Variable | Values | Default | Effect | Module |
|----------|--------|---------|--------|--------|
| `DEBUGINFOD_URLS` | space-separated server URLs | `""` (no servers) | The standard debuginfod server list. abicheck consults it **only** when network resolution is enabled with `--debuginfod` and `--debuginfod-url` is not given. `--debuginfod-url` overrides `DEBUGINFOD_URLS`. Only `http`/`https` URLs are used. | `debug_resolver.py` (`DebuginfodResolver._default_urls`) |

---

## Other environment variables abicheck honours

These are standard / third-party variables read for caching, Windows symbol
resolution, reproducibility, and CI annotations. abicheck does not define them.

| Variable | Read for | Module |
|----------|----------|--------|
| `XDG_CACHE_HOME` | Base directory for the snapshot cache, header-AST cache, and debuginfod cache (falls back to `~/.cache`). | `snapshot_cache.py`, `dumper_cache.py`, `debug_resolver.py` |
| `LOCALAPPDATA` | Windows cache base directory for the header-AST cache. | `dumper_cache.py` |
| `_NT_SYMBOL_PATH` | Windows symbol search path used when resolving PDB / debug info. | `debug_resolver.py` |
| `SOURCE_DATE_EPOCH` | Reproducible snapshot `created_at` provenance timestamp. | `cli.py` |
| `GITHUB_ACTIONS` | When `== "true"`, enables GitHub Actions-specific output (job summary / annotations). | `annotations.py`, `cli_compare_release.py` |
| `GITHUB_STEP_SUMMARY` | Path abicheck writes a Markdown job summary to, when present. | `annotations.py`, `cli.py` |
