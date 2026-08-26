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
- **Sixth follow-up (Codex review, two findings): a `frontends`-layer
  module reaching this fix's own new functions violated ADR-061's
  documented dependency direction.** `abicheck/frontends/AGENTS.md`
  states a migrated `frontends` module may import `workflows` but must
  never import back through the `service`/`cli` compatibility facades --
  `frontends/cli/runtime.py`'s `_finalize_compare_result` (the CLI's
  counterpart to `cli_scan_baseline`'s) had been routing both
  `resolve_linker_script_chain`/`note_if_same_binary_compared` through
  `service.py` to sidestep an unrelated `frontends -> extract` layering
  error, which solved that error but created exactly the facade-import
  violation this convention exists to prevent. Fixed by re-exporting both
  functions from `abicheck.workflows.extraction` (an existing, real
  `workflows`-package module, not a facade) instead -- `binary_utils.py`
  was already `extract`-layer classified; `confidence.py` (home of
  `note_if_same_binary_compared`) was not classified into any ADR-061
  layer at all, so `architecture/modules.yaml` now classifies it under
  `compare` (its own docstring already states it "depends only on the
  snapshot model and the policy enums", matching that layer's shape).
  Both `frontends/cli/runtime.py` and `cli_scan_baseline.py` now import
  both functions from `workflows.extraction`; `service.py`'s own
  now-unnecessary re-exports were removed.
- **Seventh follow-up (Codex review): `scan --against`'s new
  `coverage_warnings` key shipped without the required
  `SCAN_SCHEMA_VERSION` bump.** Every additive key in the baseline
  summary this codebase has previously shipped bumped
  `abicheck.schemas.SCAN_SCHEMA_VERSION` with a documented history entry
  (`tests/test_cli_scan_baseline.py::TestBaselineSummaryKeysArePinned`
  exists specifically to force this after a prior PR shipped two keys
  without it) -- this PR's own new `coverage_warnings` key missed the
  same checklist. Bumped to `1.21`, documented in `schemas/__init__.py`'s
  own version-history comment and in `docs/use/output-formats.md`, and
  added to `TestBaselineSummaryKeysArePinned`'s `_KNOWN_KEYS`/fixture
  (confirmed the pinning test fails without the fixture change, since the
  pre-existing fixture never populated `coverage_warnings` and so never
  actually exercised the new key).
- **Eighth follow-up (Codex review): the warning was still invisible in
  `compare --format review`, and silently dropped for the deep-compare
  `--old/new-sources`/raw `--build-info` path.** `to_review_digest` never
  read `coverage_warnings` -- now renders each entry as a `> ⚠️` banner,
  right after the manual-review-required banner. Separately,
  `_embed_inline_source_sides` rewrites `old_input`/`new_input` to a
  temporary embedded-snapshot `.abi.json` path before
  `_finalize_compare_result` hashes them for the same-binary check, so
  `_collect_metadata` (which returns `None` for a JSON path) silently
  dropped the warning for exactly these deeper comparisons even when both
  real binaries are byte-identical. Fixed by hashing the pair already
  captured before that rewrite for `--used-by`/`--required-symbol`
  scoping (`used_by_old_input`/`used_by_new_input`) instead.
- **Ninth follow-up (Codex review, two findings): `workflows.extraction`
  was the wrong re-export surface for a post-comparison coverage warning,
  and `--report-mode leaf`/`root-cause` markdown never showed it either.**
  `note_if_same_binary_compared` moved to `workflows.gate` --
  `extraction.py`'s own docstring scopes it to operations performed on an
  input, not deciding part of a completed comparison's process response,
  which is exactly what `gate.py` already owns for the exit-code axes.
  Separately, `_append_confidence_section` (the JSON/full-markdown
  coverage-warning banner) only runs in full mode; `--report-mode
  leaf`/`root-cause` share a different preamble (`_view_preamble`), which
  now carries the same banner.
- **Tenth follow-up (Codex review): the warning was invisible in
  `--format junit` too.** A same-binary comparison rendered as an empty
  passing `<testsuite>` with no indication the artifacts were duplicates.
  `to_junit_xml`/`to_junit_xml_multi` now append a
  `abicheck.coverage_warnings.<library>` suite with one passing
  `<testcase>`/`<system-out>` per matching entry -- scoped to the
  same-binary marker specifically (a new shared
  `confidence.SAME_BINARY_WARNING_MARKER`), not every `coverage_warnings`
  entry, since an ordinary comparison already carries a dozen routine
  detector-disabled notices there that would otherwise flood every JUnit
  document with boilerplate testcases. New leaf module
  `junit_coverage_warnings.py` (kept out of `junit_report.py` to respect
  its ADR-061 debt-no-growth baseline).
