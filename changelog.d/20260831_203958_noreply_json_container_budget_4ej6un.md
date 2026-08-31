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
  traceback.
