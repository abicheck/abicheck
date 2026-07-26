### Fixed

- **`dump --header <dir>` and `compare --header <dir>` now agree on scope
  comparability.** A snapshot produced by `abicheck dump --header <dir>`
  (with no explicit `--public-header-dir`) previously left the extraction
  contract's `public_header_dirs` scope field empty, while `compare
  --header <dir>` always populates it for the live side it dumps — so
  comparing a `dump`-produced baseline against a live `compare`-side
  candidate of the *identical* header set spuriously raised
  `ScopeMismatchError` (found during the G30 pilot validation). `dump`'s CLI now folds a
  bare `-H`/`--header` directory argument into the extraction contract's
  scope fingerprint the same way `compare` already does, via a new
  `dumper.dump(scope_header_dirs=...)` parameter that feeds only the
  comparability contract — declaration-provenance tagging (ADR-015) stays
  exactly as opt-in as before, driven only by the separate
  `--public-header`/`--public-header-dir` flags.
