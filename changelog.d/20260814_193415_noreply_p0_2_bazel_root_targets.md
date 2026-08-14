<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **Bazel root-target scoping (P0.2)** — `dump --build-target TARGET`
  (repeatable) and `.abicheck.yml`'s `build.targets:` list scope Bazel L3
  evidence collection to the declared root target(s) and their transitive
  dependencies instead of a workspace-wide `deps(//...)` query, so a
  multi-package Bazel workspace with fixture/test targets alongside the
  real library no longer pollutes L3 evidence with unrelated compile
  units. Threaded through the typed API (`InputSpec.build_targets`, so
  both `DumpRequest` and `CompareRequest` carry it) and through
  `compare`'s implicit raw-`--old/new-sources` dump path. The `L3_build`
  evidence-coverage report row gains `requested_roots`/`resolved_roots`/
  `transitive_targets`/`compile_units`/`link_units` (report schema 2.37),
  populated only for a scoped run. A misspelled/nonexistent root target now
  records the requested label with an empty `resolved` set instead of
  omitting `requested_roots` entirely, and `BazelAdapter`'s plural
  `targets=[...]` constructor argument reliably triggers the live
  cquery/aquery path on its own, without also requiring the legacy
  singular `target=`.
