<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`--depth` now caps evidence for `compare`/`scan --against`, not only
  floors it.** `enforce_requested_depth` has long failed a run when the
  resolved evidence fell short of an explicit `--depth`, but never stripped
  richer evidence a pre-built JSON snapshot (or a directory/package
  operand) carried beyond what was requested — `compare old.json new.json
  --depth binary` could still emit real header-derived findings and publish
  `BREAKING`. `classify_compare_pair` now projects each side down to the
  requested rung (`abicheck.policy.depth_projection.
  project_snapshot_to_depth`) before classifying, so `--depth binary`
  behaves the same whether the input was freshly extracted or loaded from
  disk. `dump`'s own `--depth` is unaffected (it stays floor-only, so a
  dumped artifact keeps whatever richer evidence extraction produced for a
  later, deeper comparison).
