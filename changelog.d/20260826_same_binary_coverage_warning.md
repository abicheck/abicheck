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
  copies `not_evaluated`. The metadata stamp is best-effort (a caller
  passing a path with no real file backing it degrades to a no-op
  rather than raising).
- **Second follow-up (Codex review): the warning was still invisible in
  `scan`'s own default text/console output.** `_baseline_summary` copied
  `coverage_warnings` into the JSON summary, but `cli_scan_helpers.
  render_baseline_lines` -- the text renderer, which is the *default*
  format printed with no `--format`/`-o` at all -- never read that key.
  It now prints each warning right under the counts line.
- **Third follow-up (Codex review): a GNU ld linker script vs. its own
  resolved target DSO still didn't warn.** `resolve_input()` follows a
  linker script to the shared library it points at before dumping, but
  the metadata stamp above hashed the original operand paths -- a script
  and its target necessarily differ in content, so a comparison of the
  same underlying binary via one side named by a linker script (or two
  differently-written scripts targeting the same DSO) went unrecognized.
  The metadata stamp now resolves each operand through the same GNU ld
  linker-script following before hashing.
- **Fourth follow-up (Codex review, two findings): a multi-hop linker-script
  chain, and the typed `CompareRequest`/Python API path, still didn't
  warn.** The single-hop resolution the third follow-up added missed a
  linker script pointing at *another* linker script (e.g. a dev symlink ->
  a soname script -> the real versioned file) -- `resolve_input()` follows
  the whole chain recursively via its own self-call, so hashing needed the
  identical multi-hop behavior. New shared `binary_utils.
  resolve_linker_script_chain()` loops until no further hop resolves
  (bounded, to guard against a pathological cycle), used by both
  `cli_scan_baseline._run_baseline_compare` and, separately,
  `service_compare_pipeline.classify_compare_pair` -- the shared
  typed-API/Python-API compare path.
- **Fifth follow-up (Codex review): the native `compare` CLI itself had
  the identical multi-hop gap.** `_finalize_compare_result` hashes the
  operand paths `cli_compare_helpers.py` already resolved via
  `cli._normalize_binary_input` -- which, like `resolve_linker_script`
  itself, only ever follows one hop. It now also resolves through
  `resolve_linker_script_chain()` immediately before hashing, matching
  the scan and typed-API paths.
