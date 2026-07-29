<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`scan --artifact-set`: hard-linked provider aliases no longer produce
  false `bundle_unresolved_intra_dependency` findings.**
  `discover_artifact_set()` dedupes candidate paths on filesystem identity
  (`st_dev`/`st_ino`) and keeps only one representative path per inode — a
  provider library with multiple hard-linked names (e.g. `libfoo.so.1` and
  `libfoo.so.1.0.0`) previously lost every alias basename except the
  representative's own, so a consumer whose `DT_NEEDED` named the discarded
  alias read as unreachable and the audit emitted a false unresolved-
  dependency finding. `_compute_resolution_graph()` now also indexes every
  hard-linked sibling of a provider's representative path (scanned from its
  own directory), so any loader-visible alias resolves correctly.
