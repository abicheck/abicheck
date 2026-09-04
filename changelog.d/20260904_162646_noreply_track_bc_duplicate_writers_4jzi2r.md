### Changed

- **`bundle_facts_store.py` no longer has its own multi-artifact package
  layout** — `write_bundle_facts_package`/`read_bundle_facts_package` are
  now a thin wrapper over `bundle_facts_serialization.bundle_facts_to_dict`/
  `bundle_facts_from_dict` plus `storage.import_bundle_facts`, reconciling
  the two independently-landed ADR-063 Track B/C "8B" writers onto one
  physical layout. `PackageManifest.project_sections`/`ArtifactRef
  .native_identity`-for-filename/aliases are retired for this path; a
  `BundleFacts` package now stores its instantiation manifest and
  filesystem facts on `VariantRef.sections`, the same layout a persisted
  `BundleFacts` document already used. `storage.import_bundle_facts
  .export_bundle_facts` gained an optional `on_document` hook so a caller
  can charge its own aggregate decoded-size budget incrementally, as each
  artifact/section is reconstructed, rather than only after every member of
  a possibly-untrusted package has already been retained in memory —
  `bundle_facts_store.py`'s reader uses it to reapply the same read-side
  byte and alias-element-count budgets its previous implementation
  enforced.
