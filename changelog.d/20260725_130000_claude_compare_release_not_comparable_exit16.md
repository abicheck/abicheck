<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`abicheck compare`'s directory/package (release) fan-out now surfaces the
  ADR-050 D2 comparability gate per library instead of folding a genuine
  mismatch into the generic `ERROR`/exit-4 bucket a real crash uses.**
  `_compare_one_library` gains a dedicated
  `except (ProfileMismatchError, ScopeMismatchError)` branch, ordered before
  its existing `except Exception`, returning a `"not_comparable"` verdict
  (with a `reason`, and — when `--output-dir` is set — a schema-conformant
  `verdict: null` per-library report file). `_RELEASE_VERDICT_ORDER` gains a
  new top rank for it, so one not_comparable library dominates the
  release-level "worst verdict wins" rollup over every other outcome,
  including a genuine `ERROR` and `--fail-on-removed-library`'s exit `8`.
  Exits **`16`** — identical to native `compare`'s own not_comparable code —
  in both the legacy and severity-aware release schemes.
  `--diagnostic-comparison` itself is still rejected for directory/package
  inputs (the release fan-out doesn't wire that escape hatch), matching the
  behavior already shipped for the native `compare` command's directory
  dispatch.

This closes out all seven ADR-050 D2 entry points (native `compare`,
`compare-release` fan-out, `compat check`, `scan --against`,
`deps compare`, the `abi_compare` MCP tool, and `service.py`'s
`CompareRequest`/`run_compare`).
