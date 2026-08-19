### Changed

- **CLI cleanup phase two, PR B (slice 3)**: `scan --against --pack` no
  longer rejects a `kind: gate` pack. `gate.exit_code_scheme`/
  `gate.severity.<category>` assignments now apply to the baseline
  comparison's exit code the same way `--severity-preset`/
  `--exit-code-scheme` given directly (or via `.abicheck.yml`) already do --
  `scan`'s exit code has honored the resolved severity/exit-code-scheme
  configuration since the fix that closed the "scan never consults
  severity" gap, and a gate pack is simply one more source for that same
  gate, mirroring the release fan-out's identical slice 2 fold. Matching
  ADR-049 D8's precedence rule, a selected pack cannot override a
  severity/exit-code-scheme value that was actually stated -- by an
  explicit `--severity-preset`/`--exit-code-scheme`, or by `.abicheck.yml`.
  `scan --dry-run`'s previewed exit-code scheme now also carries the same
  "a selected --pack may adjust it" caveat `compare --dry-run` already
  shows when a gate pack is selected, instead of asserting the pre-pack
  scheme as final. No change to `scan --against` without `--pack`, or to
  `compare`'s own `--pack` behavior.
