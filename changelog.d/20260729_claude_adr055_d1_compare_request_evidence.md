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
  "binary"` also clears `dump_manifest` and the header-derived public-header
  provenance/fingerprint set, not just `headers`, since a headerless dump
  still fingerprints those for `compute_extraction_contract`'s
  `scope_fingerprint` and would otherwise spuriously `ScopeMismatchError` on
  differing header lists; the side's public-header roots (plus a
  `dump_manifest`'s own `public_header_paths`/`public_header_dirs`, since a
  manifest-driven request has no `headers` for them to derive from) are
  forwarded into the inline evidence collection too, so source replay's own
  public-header set isn't silently left empty; the
  inline evidence collection's `extractor` matches whichever L2 frontend
  actually ran for that side — resolved to a concrete backend (e.g. the
  default `"auto"` resolves to `castxml`, matching L2's own default), using a
  normalized-case `--ast-frontend` value so a case-insensitive
  `frontend="CASTXML"` doesn't silently fall back to a different extractor;
  `frontend_context` is validated (case-insensitively,
  `host`/`device`) and normalized before use; a side whose
  `compile.frontend_context` differs from the class default always wins over
  the request-level default (an unrelated per-side override, e.g. only a
  `sysroot`, still picks up the request-level default for this one field,
  since `CompileContext.frontend_context` has no way to represent
  "explicitly set to the default" — see
  `service_compare_evidence._compile_context`'s docstring); the `android`
  frontend's source-evidence check now also accepts either side's own
  `InputSpec.sources`, not just the legacy `has_sources` flag; `frontend=
  "android"` combined with a *raw* source tree in `InputSpec.sources` is
  rejected at runtime (`run_compare_request`'s inline evidence collection has
  no real Android source extractor and would otherwise silently substitute
  Clang), but a prebuilt evidence pack (`BuildSourcePack`/build-emitted
  `abicheck_inputs/`, auto-detected the same way `embed_build_source` already
  does) is allowed, since it's loaded as pre-captured facts with no extractor
  ever running — `build_info` alone is likewise unaffected, since it never
  feeds L4 extraction; a `depth="source"`
  request that would attempt L4 replay with the `hybrid` AST frontend is
  similarly rejected (mirroring `dump`'s own `--depth source`/`--ast-frontend
  hybrid` `UsageError` — L4 has no dual-backend hybrid extractor); a
  `dump_manifest`'s own `translation_units[].forced_includes` feed the
  pair-wide C++20 dialect heuristic alongside `headers`, since a
  manifest-driven side has no `headers` for it to see otherwise; and a
  `dump_manifest`'s `project_owned` per-TU include directories (private
  sibling/support roots used only for dependency-scope filtering) are no
  longer forwarded into L4 source replay's public-header set, which
  previously could false-flag private-header churn as a source break.
  `CompileContext` itself moved to a new leaf module
  (`abicheck.compile_context`, re-exported from `service_scan` for
  back-compat) so `api_types.py` can type against it without joining the
  CLI/service import-cycle-allowlisted cluster. Does not yet match every
  capability of the CLI's own, separately-maintained
  `cli_resolve._resolve_compare_snapshots` (project-config `source.method`
  inference, the set-input evidence-flag rejection guard, per-side
  AST-frontend override) — migrating the CLI onto this path, or extending it
  further to match, is deliberately left as follow-up work.
