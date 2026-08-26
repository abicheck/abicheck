### Changed

- **`dump --dry-run` and the real `dump` run now consume the same resolved
  plan** (ADR-061 Phase 3 acceptance). `dump_cmd` resolves one
  `ResolvedDumpRequest` above the `--dry-run` branch, and both branches read
  their headers, language, header backend, collect mode, depth and
  public-header split off it instead of off a parallel set of locals that
  merely agreed. A preview computed by a second resolver looks authoritative
  while being connected to nothing, so nothing failed when the two drifted.

### Fixed

- **A bare `dump` with no binary and no inputs now reports the same error with
  and without `--dry-run`.** Previously the real run and the preview rejected
  the identical invalid input with two different messages. The surviving
  message keeps the flag-naming guidance the real run's had ("pass a binary
  (SO_PATH), or `--sources`/`--build-info` for a source-only snapshot").
