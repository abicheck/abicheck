### Fixed

- **`bundle_side_input.compare_release_against_bundle_facts` silently
  dropped a caller's `PolicyFile` reclassify/override rules.** The G38
  Phase 13 driver forwarded only a bare *policy* name (e.g. `"strict_abi"`)
  to `service.compare_snapshots` for each matched library's per-library
  diff — a caller's own policy document (kind overrides,
  `ReclassifyRule` selectors such as demoting `func_visibility_changed`
  on `binding: weak`) never reached that per-library comparison
  regardless of whether the caller declared any, since `compare_snapshots`
  was never given a `policy_file=` at all. `compare_release_against_
  bundle_facts` (and the `StoredBundleFactsInput`/`resolve_bundle_side`
  chain it shares) now accept and forward `policy_file` alongside
  `policy`, matching how the native `compare`/`scan` CLIs already pass
  the two together. `policy_file` also now reaches bundle-level
  (`BUNDLE_*`-kind) scoring: `BundleDiffResult.policy_file`,
  `bundle.compare_bundle`, `bundle_analysis.analyze_bundle`,
  `bundle_facts.compare_bundle_from_facts`, and
  `bundle_side_input.compare_bundle_sides` all accept and forward it, so
  `BundleDiffResult.bundle_verdict` is no longer scored under the bare
  `policy` name alone when a policy file overrides a `BUNDLE_*` kind.
- **The G40 bundle-facts archive format's per-blob JSON container-node
  budget (`DEFAULT_MAX_JSON_OBJECT_NODES`, 1,000,000) had no override,**
  so a real per-library facts blob for a large, template-instantiation-
  heavy library (e.g. a SYCL/DPC++ library) could need well over the
  default budget to decode and be rejected outright as if it were a
  container-count amplification attack — with no way for a caller to
  say "this is a known-large, trusted payload." `bundle_facts.
  read_bundle_facts_archive`/`maybe_read_bundle_facts_archive` and
  `serialization.load_bundle_facts` all now accept a
  `max_json_object_nodes` override (plumbed further through
  `bundle_side_input.StoredBundleFactsInput`/
  `compare_release_against_bundle_facts`). Separately, the same budget
  is now enforced identically on `load_bundle_facts`'s plain
  (non-archive) `.json`/`.json.zst` path, which previously applied no
  container-node budget at all — the same bytes were checked per blob
  when read via `format="archive"` but not when read as plain JSON.
- **`bundle_models.DEFAULT_SYSTEM_PROVIDERS` was missing several common
  runtime libraries a library built against oneTBB/oneMKL/the Intel
  compiler runtime routinely links against** (oneTBB's malloc/proxy
  libraries, oneMKL's core/runtime/threading-layer libraries, the Intel
  compiler/OpenMP runtime, and the oneAPI Level Zero loader), so a bundle
  comparison over such a release needed an explicit
  `--bundle-system-providers` workaround for every one of these sonames.
  Broadened the built-in allow-list to cover them by default (as
  version-generic entries, matching any real runtime major).
