### Added

- **The first real multi-artifact `ProjectSnapshot` package writer**
  (ADR-062 A1.4/A1.5, ADR-063 Track B "8B"). `abicheck/bundle_facts_store.py`'s
  `write_bundle_facts_package`/`read_bundle_facts_package` build a real,
  multi-`ArtifactRef` `PackageManifest` from a `BundleFacts` (one artifact
  per library under a shared `VariantRef`), reusing `storage/import_v1.py`'s
  per-artifact section split so byte-identical section content across
  libraries is stored once. `PackageManifest.project_sections` (new,
  `abicheck/storage/package.py`) is D7's cross-artifact evidence slot --
  today, a `BundleFacts.manifest` instantiation manifest -- and
  `abicheck/project_snapshot_store.py`'s `write_project_manifest`/
  `read_project_manifest` now publish and read it back through the real,
  filesystem-backed D6 layout. Previously, `storage/package.py`'s
  manifest/refs/`ObjectStore` object model had no producer that ever
  assembled more than one artifact into a package.
