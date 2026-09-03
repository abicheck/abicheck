<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`storage.GraphSection` now rejects a non-mapping `surface_graph`
  constructed directly** instead of silently coercing it via `dict(...)` —
  a caller bypassing `from_document`'s own validation (e.g.
  `GraphSection(surface_graph=[])`) previously got a fabricated empty (or,
  for a pair sequence, fabricated non-empty) graph rather than a rejection.
