<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`resolve_compare_request` now attaches a real `ResolvedExecutionContext`** —
  the One Semantic Pipeline PR 1 primitive (`workflows/resolved_execution_context.py`)
  landed additively with no production caller; `compare`'s shared resolution
  seam (`service_compare_pipeline.ResolvedComparePair.resolved_execution_context`)
  now builds one from the same `AnalysisPlan` it already resolves, closing
  sub-phase 4B's "no live caller wired yet" gap for the `compare` path. Purely
  additive and behavior-preserving — no existing consumer reads the new field,
  and no CLI/API output changes for any existing invocation.
