<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Performance

- **`scan --depth build`'s S2 preprocessor pre-scan now runs its
  `clang -E`/`clang -M` probes in parallel, with new cap and disable
  knobs.** `capture_macros`/`capture_header_includes`
  (`buildsource/preprocessor_scan.py`) used to shell out to `clang -E`
  once per compile unit, serially, with no cap — on a real-world build
  with thousands of translation units, this dominated `scan` wall time
  (a reported 4-minute-to-20-minute jump at `--depth build`, ~920s in
  this tier alone). Probes now run **in parallel**
  (`ABICHECK_PREPROCESSOR_SCAN_JOBS`, mirroring the existing
  `ABICHECK_L4_JOBS` convention), still one per compile unit / public
  header — **not** deduplicated by compile flags, since two units
  sharing identical flags can legitimately resolve a curated ABI macro
  differently via their own `#include` chain, and collapsing that away
  would hide exactly the divergence this tier exists to detect.
  `ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES` bounds worst-case cost on a
  build with an unusually large number of compile units/headers
  (truncation is reported in the scan's diagnostics and downgrades the
  coverage row to `partial`, never silent), and
  `ABICHECK_PREPROCESSOR_SCAN=0` skips the whole S2 tier (reported as an
  honest `not_collected` coverage row, same as a missing compile DB or
  missing `clang`).

<!--
### Added

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Changed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Deprecated

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Removed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Fixed

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Security

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
