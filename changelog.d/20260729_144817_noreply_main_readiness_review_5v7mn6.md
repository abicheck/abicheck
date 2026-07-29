### Fixed

- **`project validate --toolchain-bindings` no longer passes a resolved
  binding that can't actually run, and now checks the declared `target`.**
  A stale binding merely *named* like a real compiler (e.g. `gcc`) whose
  `--version` probe fails outright (wrong format, not executable, timed
  out) previously fell through to a basename-only family guess and reported
  successful validation. `abicheck.buildsource.toolchain_probe` now treats
  that failure as a probe error. Separately, a declared
  `profiles.<id>.compile`/`consumer_compile` `target` was never checked
  against the resolved executable at all; it's now compared against the
  probed target triple's leading architecture component (with a small
  alias table for `amd64`/`arm64`-style spellings), catching e.g. a
  `target: aarch64-linux-gnu` profile silently bound to an x86_64
  compiler.

