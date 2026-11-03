### Fixed

- **A stored section recorded as not collected no longer reads as an empty
  one** (ADR-062 D15) — a `not_collected` entry in a sectioned snapshot's
  `section_availability` table for `binary`, `build`, `debug`,
  `declarations`, `layout` or `types` now blocks exactly the analyses that
  read that section (`not_evaluated`, a `not comparable:` coverage gap
  naming it, `analysis_assurance.status` `partial`), as a `failed` one
  already did. Before, its fields were filled with defaults and the
  comparison reported every entity it held on the other side as removed or
  added. A section absent from a document with no entry is `unrecorded` and
  is read as before. The compare report (schema 7.1,
  `analysis_assurance` 1.3) lists every section that is not present, per
  side, in the new optional `analysis_assurance.section_availability`
  array. `storage.wire_extensions.record_section_failure` moved to
  `storage.section_states`, beside the general
  `record_section_availability`. The wire-contract field inventory is now
  generated from the section encoders' declared fields
  (`tests/_wire_inventory.py`), and every key a written document carries must
  be declared there.
