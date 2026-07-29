### Fixed

- **A genuinely invalid Clang cross-compilation `target:` was still
  silently accepted on Clang versions/vendors that phrase the rejection
  diagnostic differently.** The prior fix for a bogus `target:` matched
  the literal substring `"unknown target triple"` in stderr; real Clang 17
  (`"version 'target' in target triple '...' is invalid"`) and Apple/macOS
  clang (confirmed by a real CI failure on `macos-latest`) both reject an
  invalid target with different wording, so the probe returned an
  inconclusive `None` instead of a proven mismatch on either. `_clang_accepts_target`
  now classifies purely on exit status — the invocation is a deliberately
  minimal, well-formed empty translation unit, so any nonzero exit is
  treated as a rejected target rather than requiring a specific diagnostic
  phrase.
- **An unrecognized declared OS or ABI-environment marker in `target:` no
  longer silently passes against any real probed value.** The
  architecture/OS/environment comparison only flagged a mismatch when
  *both* sides normalized to a recognized marker — so a declared target
  this module's marker tables don't recognize (e.g. `x86_64-pc-solaris2.11`,
  or the bare-metal `arm-none-eabi`) normalized to `None` and skipped the
  comparison entirely, passing unconditionally against a real, incompatible
  probed toolchain (`x86_64-linux-gnu`, `arm-linux-gnueabi`). A mismatch is
  now flagged whenever either side is non-`None` and they disagree; only
  "both unrecognized" remains a genuine skip.
