<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Performance

- **`scan --depth build`'s S2 preprocessor pre-scan is now deduped and
  parallelized.** `capture_macros`/`capture_header_includes`
  (`buildsource/preprocessor_scan.py`) used to shell out to `clang -E`
  once per compile unit, serially — on a real-world build with thousands
  of translation units sharing a handful of distinct compile contexts,
  this dominated `scan` wall time (a reported 4-minute-to-20-minute jump
  at `--depth build`, ~920s in this tier alone). Compile units are now
  probed **once per distinct (language, cwd, flags) signature**, with the
  result fanned out to every unit sharing it, and probes run **in
  parallel** (`ABICHECK_PREPROCESSOR_SCAN_JOBS`, mirroring the existing
  `ABICHECK_L4_JOBS` convention). `ABICHECK_PREPROCESSOR_SCAN_MAX_PROBES`
  bounds worst-case cost on a build with an unusually large number of
  distinct compile contexts (truncation is reported in the scan's
  diagnostics, never silent), and `ABICHECK_PREPROCESSOR_SCAN=0` skips
  the whole S2 tier (reported as an honest `not_collected` coverage row,
  same as a missing compile DB or missing `clang`).

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
