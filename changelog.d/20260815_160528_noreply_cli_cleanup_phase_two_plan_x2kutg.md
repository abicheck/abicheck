<!-- Follow-up to PR 1's --profile quick scoped-gate fix, found by Codex review. -->

### Fixed

- **`compare --profile quick`'s scoped one-liner no longer shows an
  unexplained finding count.** The previous fix kept the one-liner's counts
  from `--format json`'s own scoped fold, which deliberately keeps
  full-library counts alongside a `changes` array for context. The one-line
  format has no room for that context, so a `COMPATIBLE` scoped verdict
  could still print `1 breaking` for a finding the app never used. Counts
  are now recomputed from only the findings that actually decide the scoped
  verdict/exit code.
