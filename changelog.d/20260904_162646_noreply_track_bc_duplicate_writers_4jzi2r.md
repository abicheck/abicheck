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
  enforced. `export_bundle_facts` also now rejects a stored, non-string
  `variant_fingerprint` and a stored template instantiation naming the same
  parameter more than once, instead of silently coercing/collapsing either
  — content that reaches it without going through `import_bundle_facts`'s
  own validation (a hand-assembled or corrupted package) is untrusted the
  same way any other stored section content is. `export_bundle_facts` also
  now rejects an artifact whose own `sections` carries a kind the
  package-wide `StorageVersions.section_schema_versions` does not
  advertise (unversioned content could otherwise reach a comparison), and
  the `on_document` hook charges an artifact's recovered library name
  against the aggregate decoded-byte budget too, not only its snapshot
  document. The same unadvertised-section check now also covers the
  variant's own `bundle_composition` section, checked before it is ever
  decoded, so a hand-edited package cannot drop that section from
  `section_schema_versions` while keeping it in `VariantRef.sections` to
  smuggle an unversioned variant fingerprint or instantiation manifest
  into a comparison. `export_bundle_facts` also now rejects a stored
  manifest `provides` entry that is not itself a mapping (e.g. a JSON list
  of `[key, value]` pairs), instead of silently accepting it via a plain
  `dict(entry)` conversion, and a stored `manifest` that is not itself a
  mapping with a list-valued `provides` key (previously an unhandled
  `TypeError`/`KeyError` instead of the documented `ValueError`). It also
  now checks the composition `ObjectRef.kind` against
  `BUNDLE_COMPOSITION_SECTION_KIND` before fetching it, not only via the
  fetched content's own internal kind afterward. `bundle_facts_serialization
  .bundle_facts_to_dict` gained a matching optional `on_snapshot` hook, used
  by `write_bundle_facts_package` to charge each library's encoded size as
  it is converted, rather than only after every member of a possibly-large
  `BundleFacts` has already been converted and retained in one combined
  document -- including the library name itself (a `per_library_snapshots`
  dict key), not only its snapshot document, mirroring the read side's
  identical charge. `bundle_facts_store.py`'s own alias node-count estimator
  (`_alias_element_count`) now tolerates a malformed per-library
  `filesystem_aliases` value (e.g. a stray integer instead of a list),
  contributing `0` instead of raising an unhandled `TypeError` before
  `bundle_facts_from_dict`'s own validator gets a chance to report the
  documented `ValueError`. `storage.import_bundle_facts.export_bundle_facts`
  likewise now validates a stored template entry's `instantiations` value is
  a list before iterating it, and looks up each `variant.artifact_ids` entry
  against a `dict` built once rather than a fresh linear scan per artifact
  (an earlier version of this reconciliation made reconstruction quadratic
  in artifact count).
