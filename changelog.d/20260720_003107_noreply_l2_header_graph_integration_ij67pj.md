### Changed

- **L2 header-only semantic graph is now default-on** — the L2 header-only
  semantic graph (and its include-file extension) is now always built for
  `dump`/`compare` whenever `--depth headers` or deeper evidence is
  available, for a single-library run. No flag is needed any more.

### Deprecated

- **`--header-graph`/`--header-graph-includes` are now hidden no-ops** — the
  two flags on `compare` and `dump` no longer change behavior; passing them
  is harmless and prints a one-line deprecation note to stderr. They no
  longer appear in `--help`, and are kept only for a transition window
  before removal.
