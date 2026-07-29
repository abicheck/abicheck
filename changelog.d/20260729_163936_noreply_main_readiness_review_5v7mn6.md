### Fixed

- **A Clang/Intel-icx `target:` probe that couldn't even run is now a
  validation error instead of a silent pass.** `_clang_accepts_target`
  returns `None` (not a proven mismatch) when the controlled validation
  compile itself fails to complete — e.g. a wrapper binary that answers
  `--version` fine but hangs or errors on a real invocation. That
  inconclusive result was previously treated the same as "nothing to
  check," approving a declared cross-compilation target that was never
  actually verified. Now reported as an error, matching this module's
  existing "can't verify → error, not a silent pass" principle for every
  other unprobeable case.
