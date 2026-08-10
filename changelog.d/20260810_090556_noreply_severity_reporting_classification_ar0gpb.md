### Added

- **`scan --against` now honors the severity gate** — `--severity-preset`,
  the per-category `--severity-*` overrides, and `--exit-code-scheme`
  (plus `.abicheck.yml`'s `severity:`/`exit_code_scheme` keys) now feed
  `scan --against`'s exit code exactly as they already do for `compare`:
  under the resolved `severity` scheme, the exit code is the worst
  error-level category among the baseline comparison's findings rather
  than a fixed mapping from the overall verdict, so e.g.
  `--severity-preset info-only` can leave a `BREAKING` verdict at exit `0`.
  Previously `scan --against` always used the legacy verdict→`{0,2,4}`
  mapping and silently ignored any severity setting. `--pack` gate-severity
  folding is not yet extended to `scan`; pass severity settings directly.
  See `docs/reference/exit-codes.md`'s new "`scan --against` and severity"
  section.

