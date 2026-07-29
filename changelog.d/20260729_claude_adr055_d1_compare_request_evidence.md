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
  evidence per side, which is then diffed (`prepare_embedded_build_source`)
  and folded into the comparison's findings the same way the CLI compare path
  already does — the embedded evidence is not just stored inertly; `depth ==
  "binary"` clears headers before resolving; each side's `compile` override is
  merged with the existing pair-wide C++20 dialect override (the pair-wide
  standard is kept even when a side sets an unrelated override, e.g. only a
  sysroot) and the request-level `frontend_context` default; `dump_manifest`
  and `public_header_dirs` are forwarded/unioned into the existing resolution
  path, including the dependency-scope filter (a declared-public file or
  directory is no longer misclassified as a toolchain dependency); `depth ==
  "binary"` also clears `dump_manifest`, not just `headers`; the inline
  evidence collection's `extractor` matches whichever L2 frontend actually ran
  for that side, using a normalized-case `--ast-frontend` value so a
  case-insensitive `frontend="CASTXML"` doesn't silently fall back to a
  different extractor; `frontend_context` is validated (case-insensitively,
  `host`/`device`) and normalized before use; a side whose
  `compile.frontend_context` differs from the class default always wins over
  the request-level default (an unrelated per-side override, e.g. only a
  `sysroot`, still picks up the request-level default for this one field,
  since `CompileContext.frontend_context` has no way to represent
  "explicitly set to the default" — see
  `service_compare_evidence._compile_context`'s docstring); the `android`
  frontend's source-evidence check now also accepts either side's own
  `InputSpec.sources`, not just the legacy `has_sources` flag — but
  `InputSpec.sources`/`build_info` are rejected together with `frontend=
  "android"`, since `run_compare_request`'s inline evidence collection has no
  real Android source extractor and would otherwise silently substitute
  Clang; only the legacy `has_sources=True` (pre-captured dump, no inline
  path) combination is supported for `android` today. `CompileContext` itself
  moved to a new leaf module
  (`abicheck.compile_context`, re-exported from `service_scan` for
  back-compat) so `api_types.py` can type against it without joining the
  CLI/service import-cycle-allowlisted cluster. Does not yet match every
  capability of the CLI's own, separately-maintained
  `cli_resolve._resolve_compare_snapshots` (project-config `source.method`
  inference, the set-input evidence-flag rejection guard, per-side
  AST-frontend override) — migrating the CLI onto this path, or extending it
  further to match, is deliberately left as follow-up work.
