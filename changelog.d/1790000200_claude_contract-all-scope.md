### Fixed

- **`compare --contract all` (and `exports`) no longer hides internal
  breaks.** The legacy public-header filter kept running at its default value
  ahead of the contract evaluator, so a change to an internal type that
  `--no-scope-public-headers` reports as an ABI break (exit 4) came out clean
  (exit 0) under `--contract all`, the domain documented as its exact
  replacement. An explicit `all` or `exports` domain now turns header-origin
  demotion off, in the CLI and the typed Python API alike. `--contract
  public` and runs without `--contract` are unchanged.
