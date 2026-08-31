### Added

- **`compare --old-bundle-facts` compares a live release directory against a
  previously captured `--bundle-facts-out` document, from the CLI.**
  `bundle_side_input.compare_release_against_bundle_facts()` was fully
  implemented and parity-tested but, per its own docstring, was deliberately
  never exposed on any CLI command — `cli_compare_release.py`/
  `cli_compare_helpers.py`, the files that would host its dispatch, both sit
  at the AI-readiness 2000-line hard cap. `compare OLD_FACTS NEW_DIR
  --old-bundle-facts` reads `OLD_FACTS` as a stored `BundleFacts` document
  instead of a live directory, renders a `mode: "bundle_facts"` JSON/markdown
  report, and exits via the same legacy verdict-based scheme as
  `compare-release`. A new `--max-json-object-nodes` option overrides the
  JSON container-node budget (`bundle_facts.DEFAULT_MAX_JSON_OBJECT_NODES`,
  1,000,000) for this path — previously the only way to raise that budget for
  a real, large, template-heavy per-library facts blob (e.g. a SYCL/DPC++
  library) was to patch the constant in a private fork; it's now a supported
  CLI flag. `compare_release_against_bundle_facts()` itself also gained a
  `suppress` parameter, forwarded to each per-library `service.
  compare_snapshots()` call — previously this driver had no way to honor a
  caller's suppression list at all, unlike every other comparison entry point
  in this codebase. `--old-bundle-facts` now also rejects `--dry-run` and
  `--contract` explicitly (exit 64) instead of silently ignoring them, merges
  `.abicheck.yml`'s `compile.include_dirs` into the NEW side's header search
  the same way every other `compare` dispatch path does, and reports a
  malformed `OLD_FACTS` document as a clean CLI error instead of a raw
  traceback. NEW_INPUT now accepts a package archive (wheel/deb/rpm/tar), not
  just a directory, extracted with the same primitive the live release
  fan-out uses — `--devel-pkg new=...` is honored the same way (its header
  root/include roots feed the NEW side's header search) and `--write
  FORMAT=PATH` now renders and writes the promised second artifact instead of
  being silently accepted and ignored. `--debug-info`,
  `--severity-preset`/`--pack`/`--exit-code-scheme`, and
  `--no-scope-public-headers` are rejected explicitly (exit 64) rather than
  silently ignored, since none of them have a channel into
  `compare_release_against_bundle_facts()`. `--depth binary` now clears the
  NEW side's headers the same way `run_compare` does, and `--depth
  build`/`--depth source` are rejected explicitly (no channel for L3-L5
  evidence in this mode). `--no-bundle-analysis` is rejected explicitly
  rather than silently ignored. `--output-dir` now writes one
  `{library}.json` report per matched library, mirroring the live release
  fan-out's own layout, with the library name sanitized to a basename (it
  originates in the OLD_FACTS document, not a path this process resolved
  itself). A package-extraction failure (a malformed archive with a
  recognized extension) no longer leaks its temporary extraction directory.
  `--sources`/`--build-info`/`--dump-manifest` and the single-pair-only flag
  family (`--used-by`, `--required-symbol`, `--use-cases`, `--env-matrix`,
  `--reconcile-build-context`, `--diagnostic-comparison`,
  `--audit-suppressions`, `--require-complete-analysis`) are now rejected
  explicitly, reusing the same guard the live release fan-out applies to a
  directory/package operand. An explicit `--config` whose `severity:`/
  `scope:`/`suppression:`/`exit_code_scheme:` blocks would otherwise be
  silently unapplied (only `compile:` reaches this mode) is now rejected
  too. A comparison where nothing in NEW_INPUT matches any library in
  OLD_FACTS's stored facts is now a clean error instead of a `NO_CHANGE`
  verdict for a comparison that never actually ran.
