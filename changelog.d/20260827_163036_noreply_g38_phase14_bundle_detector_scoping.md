<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`compare-release`'s bundle analysis no longer misses an internal,
  headerless cross-DSO break just because `--scope-public-headers` (on by
  default) correctly excluded it from that library's own public API
  report** — `bundle_intra_dep_signature_changed`, `bundle_intra_type_
  changed`, and `bundle_provider_changed` previously scanned only each
  library's already public-surface-scoped `DiffResult.changes`, so a
  signature/type/provider change on a symbol with no public header (but
  still imported or referenced by a sibling library in the bundle) was
  invisible to the bundle report even though it breaks the shipped
  bundle at load/call time. These three detectors now also scan each
  library's `out_of_surface_changes` — the already-recorded ledger of
  what scoping demoted, never silently dropped — so the bundle's own
  internal linkage contract is evaluated independently of what counts as
  "public" for the standalone per-library report. Each detector's own
  reachability rule (an import-resolution check, a name-embedding
  symbol-table match, or no check at all) is unchanged.
