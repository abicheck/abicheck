<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`compare --bundle-facts-out`'s stranded-library resolver now clears
  headers at `--depth binary` through the same shared evidence-resolution
  machinery every matched pair uses**, instead of a hand-rolled
  special-case that could drift from it. A stranded (removed-in-new)
  library's `BundleFacts` entry is resolved through the same
  `DumpRequest -> resolve_dump_request -> execute_dump_request` pipeline
  `dump`/`scan` already converge on (ADR-063 D1), gaining the same
  `AnalysisPlan` pre-flight check while keeping its existing
  degrade-to-ELF-only fallback on failure.
