### Added

- **`abicheck aggregate --format json`'s per-target entries now carry
  `completed_without_compatibility_verdict` (schema 1.11, present only
  when true)** — a completed `compare --no-baseline` audit target reads
  `state: "analyzed"` with `compatibility_verdict: null`, which previously
  conflated with a real compatibility comparison (before this shape
  existed, `state: "analyzed"` implied a non-null verdict). The new field
  lets a consumer distinguish the two without inferring it from the null
  verdict alone.

