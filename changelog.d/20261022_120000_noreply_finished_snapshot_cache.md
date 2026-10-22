### Performance

- **A warm snapshot-cache hit no longer re-runs dependency scoping and
  ownership classification** — `resolve_input` used to reload the raw cached
  snapshot of a binary operand and re-finish it (ADR-075 ownership, the
  achieved `--exclude-header` stamp and the dependency re-scope it triggers)
  on every run; on a 28-member MKL release comparison that was most of the
  warm prologue. The finished snapshot is now cached too, in a `finished/`
  sub-directory of the snapshot cache with its own LRU budget, keyed by the
  raw entry's key plus every finishing input (exclude patterns and the ones
  that matched, `-H`/public-header roots as resolved on disk, the ownership
  request, the project ownership rules, the working directory). A hit returns
  the same serialized snapshot an uncached run computes.
