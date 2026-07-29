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
  previously could false-flag private-header churn as a source break;
  `InputSpec.dump_manifest` and `InputSpec.headers` on the same side are now
  rejected as mutually exclusive (mirroring the CLI's own
  `--dump-manifest`/`-H` `UsageError`) — a manifest replaces the primary AST's
  header list entirely, so forwarding both previously mixed two different
  declared surfaces into one snapshot's public-header provenance and
  pair-wide dialect detection; and a per-side
  `InputSpec.compile.frontend_context` is now validated the same way the
  request-level `frontend_context` already is, and normalized to lowercase
  before use, since an accepted case-insensitive spelling (e.g. `"DEVICE"`)
  previously bypassed validation entirely and then compared unequal to the
  lowercase literal every real consumer (the DPC++/SYCL AST-context selector)
  checks against; the `android` frontend's feasibility check now also accepts
  either side's `InputSpec.build_info` (not just `sources`/`has_sources`),
  since `embed_build_source` auto-detects a prebuilt evidence pack in either
  input the same way.
  `CompileContext` itself moved to a new leaf module
  (`abicheck.compile_context`, re-exported from `service_scan` for
  back-compat) so `api_types.py` can type against it without joining the
  CLI/service import-cycle-allowlisted cluster. Does not yet match every
  capability of the CLI's own, separately-maintained
  `cli_resolve._resolve_compare_snapshots` (project-config `source.method`
  inference, the set-input evidence-flag rejection guard, per-side
  AST-frontend override) — migrating the CLI onto this path, or extending it
  further to match, is deliberately left as follow-up work.

### Fixed

- **`scan --against`'s dependency-scope baseline peek no longer fully
  parses a large JSON snapshot** just to read one top-level tag: since
  `AbiSnapshot.dependency_scope` is one of the last fields serialized, a
  cheap tail-byte regex scan resolves it directly for a real
  `dump`-produced snapshot, falling back to the full `json.load` only when
  the tail scan can't confidently resolve the tag. Previously an explicitly
  unfiltered (`"full"`) baseline — precisely the mode most likely to carry
  the largest transitive dependency surface — paid the full parse cost
  before the real comparison parsed it again.
- **A corrupt/hand-edited `dependency_scope` value now fails snapshot
  loading** instead of silently downgrading to `None`. The comparability
  gate deliberately treats a `None` side as "an old, untagged snapshot with
  no recoverable mode" and skips the filtered-vs-full mismatch check for
  it; silently mapping an invalid value (e.g. a `"filterd"` typo) to `None`
  the same way let a corrupt current-schema snapshot exploit that same
  leniency and bypass a real mismatch. Only a genuinely absent key or an
  explicit `null` still load as `None`.
- **`run_compare_request` now enforces an explicitly requested
  `CompareRequest.depth`** the same way `dump`'s own
  `check_requested_depth_satisfied` hard-fails: previously a `depth="source"`
  request whose raw source tree failed to actually produce L4 evidence (no
  usable compile database, extractor, or linkable declarations) still diffed
  whatever weaker evidence `embed_build_source` produced, silently returning
  an artifact-only verdict with no signal that the requested depth was never
  reached. Each side's resolved snapshot is now checked against the
  requested depth after resolution, raising `ValidationError` naming the
  side and the depth actually reached if it falls short.
- **`run_compare_request` no longer leaks CLI-framework behavior through the
  Tier-2 API** when `InputSpec.sources`/`build_info` is set: a malformed
  evidence pack previously raised `click.ClickException` straight out of
  `embed_build_source`'s pack loader, bypassing this method's documented
  `ValidationError`/`SnapshotError` contract — now caught and translated to
  `SnapshotError`. Separately, `embed_build_source` and
  `prepare_embedded_build_source`/`attach_evidence_metrics` (the D7
  coverage table, the D6 timing/metrics summary, and a "no
  compile_commands.json found" warning) previously wrote CLI-formatted
  tables to stderr unconditionally whenever any evidence was involved,
  with no way for a non-CLI caller (embedded application, MCP server) to
  suppress them; all three now accept a `quiet` keyword (default `False`,
  preserving the CLI's and `scan`'s existing behavior unchanged) that
  `run_compare_request` passes as `True`.
