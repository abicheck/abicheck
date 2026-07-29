<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`CompareRequest`/`InputSpec` build/source-evidence completeness** (ADR-055
  D1): the Tier-2 Python API's typed request structs previously had no way to
  express `compare`'s `--depth`/`--sources`/`--build-info`/`--dump-manifest`/
  per-side cross-toolchain `CompileContext`/`--public-header-dir` feature set
  at all — a Python caller wanting that had to fall back to loose keyword
  arguments on lower-level functions instead of the documented
  `CompareRequest` chokepoint. `InputSpec` gains `sources`, `build_info`,
  `dump_manifest`, `compile`, and `public_header_dirs`; `CompareRequest` gains
  `depth` and `frontend_context`. `service.run_compare_request` now resolves
  these directly: `depth`/`sources`/`build_info` infer a collect mode (mirroring
  the CLI's own `--depth`-omitted inference) and embed inline build/source
  evidence per side; `depth == "binary"` clears headers before resolving;
  each side's `compile` override is merged with the existing pair-wide C++20
  dialect override and the request-level `frontend_context` default;
  `dump_manifest` and `public_header_dirs` are forwarded/unioned into the
  existing resolution path. `CompileContext` itself moved to a new leaf
  module (`abicheck.compile_context`, re-exported from `service_scan` for
  back-compat) so `api_types.py` can type against it without joining the
  CLI/service import-cycle-allowlisted cluster. Does not yet match every
  capability of the CLI's own, separately-maintained
  `cli_resolve._resolve_compare_snapshots` (project-config `source.method`
  inference, the set-input evidence-flag rejection guard, per-side
  AST-frontend override) — migrating the CLI onto this path, or extending it
  further to match, is deliberately left as follow-up work.
