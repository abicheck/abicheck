<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Pack manifests reject non-finite float assignments (`NaN`/`inf`/`-inf`)**
  instead of silently accepting them: IEEE 754 defines `nan != nan`, so two
  contract/gate manifests both assigning a YAML `.nan` to the same field
  previously made `detect_pack_conflicts()` treat the two, semantically
  identical assignments as a genuine disagreement and raise a spurious
  `PackConflictError`. `_canonicalize_scalar()` in
  `compatibility_evaluation_packs.py` now raises `PackManifestError` at
  load time for any non-finite float, matching this module's existing
  "reject rather than silently produce an ambiguous value" handling of
  `None` and nested mappings.
