### Fixed

- **Comparing two byte-identical binaries now surfaces a coverage
  warning instead of silently reading as a clean, confident `NO_CHANGE`
  report.** A `compare`/`scan` run against literally the same file
  content (a stale build artifact, a symlink resolving both `--old` and
  `--new` to the same path, a packaging step that copied the wrong
  binary) necessarily reports no ABI differences -- correctly, for the
  bytes actually given -- but that report was indistinguishable from a
  genuine "these two releases have no ABI-visible differences" result,
  silently under-reporting the fact that the comparison couldn't have
  caught anything either way. `confidence.note_if_same_binary_compared`
  now appends a `coverage_warnings` entry ("old and new binaries are
  byte-identical (sha256 ...); this comparison cannot detect a change
  even if one was intended -- verify the correct build artifacts were
  provided") whenever both sides' `LibraryMetadata.sha256` match --
  surfaced in every existing report format (JSON/SARIF/text/HTML/
  Markdown) that already renders `coverage_warnings`, with no new field
  or schema change needed.
- **Follow-up (Codex review): `scan --against` didn't actually surface
  this warning yet.** `cli_scan_baseline._run_baseline_compare` builds
  its own summary via `compare_snapshots` directly, bypassing both the
  metadata stamping `compare`'s own result-finalization does and the
  summary field that would carry the warning. It now stamps
  `old_metadata`/`new_metadata` from the baseline/candidate paths, calls
  `note_if_same_binary_compared`, and `_baseline_summary` copies
  `coverage_warnings` into the JSON summary the same way it already
  copies `not_evaluated`.
