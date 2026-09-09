### Fixed

- **`compare --view` is no longer silently dropped on a directory/package
  operand** — `--view show=...`, `--view demangle`/`--no-demangle`, and
  `--view patterns` used to be accepted but ignored by the directory/package
  release fan-out, silently rendering the ordinary, full, unfiltered release
  report instead. `show=...` now filters the release's own per-library
  findings (JSON and Markdown alike, including the `findings_truncated`
  flag's own filtered count); `demangle`/`no-demangle` now controls the
  release Markdown's symbol spelling the same way it does for a single-pair
  `compare` (JSON/JUnit stay raw/mangled either way, matching that same
  single-pair behaviour) — release Markdown now demangles by default,
  matching the single-pair default; `patterns` now echoes each library's own
  pattern-verdict modulation ledger to stderr. `--view leaf`/`--view
  root-cause` are rejected with a clear usage error (exit `64`) instead of
  being silently ignored — both restructure a single comparison's own
  root-cause graph, and the release summary is a genuinely different,
  already-aggregated document across every library with no single such graph
  to restructure (the same structural reason `--format sarif/html/review`
  already reject a directory/package operand).
