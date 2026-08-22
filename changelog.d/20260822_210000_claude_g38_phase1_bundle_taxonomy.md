### Documentation

- **Documented why `bundle_*` findings aren't suppressed by public-surface
  policy scoping, and that they currently have no per-finding suppression
  mechanism at all.** [Multi-binary (bundle) ABI analysis](docs/use/multi-binary.md)
  now explains that a `bundle_*` kind answers "does the shipped bundle still
  work end-to-end" — classified as `BREAKING`/etc. through the same
  registry as every other `ChangeKind`, but scoped differently — so
  `--scope-public-headers` and a public-surface-scoped `--policy` profile
  do not suppress a `bundle_*` finding on an internal, non-public symbol.
  This was already the intended scoping behavior, just previously
  undocumented. Also documents, as a known limitation, that `compare_bundle()`
  is never given a suppression ruleset, so `--suppress` rules have no effect
  on bundle findings today; `--no-bundle-analysis` and
  `--bundle-system-providers` remain the only available levers
  (G38 Phase 1, an amendment to ADR-023).
