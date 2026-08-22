### Documentation

- **Documented why `bundle_*` findings aren't suppressed by public-surface
  policy scoping.** [Multi-binary (bundle) ABI analysis](../use/multi-binary.md)
  now explains that a `bundle_*` kind answers "does the shipped bundle still
  work end-to-end," a deliberately different question from
  `BREAKING_KINDS`/`API_BREAK_KINDS`, so `--scope-public-headers` and a
  public-surface-scoped `--policy` profile do not suppress a `bundle_*`
  finding on an internal, non-public symbol — this was already the intended
  behavior, just previously undocumented (G38 Phase 1, an amendment to
  ADR-023).
