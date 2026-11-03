### Fixed

- **`abicheck dump` keeps a section the extractor recorded as failed.** A PE dump whose PDB was found but failed to parse stored its `debug` section as present and empty in the written `.abi.json` (and on stdout), because the dump wrote a flat payload; it now writes the snapshot itself, so the file records `debug` as `failed` and a later `compare` reports the debug-reading analyses not comparable.
- **No writer stores a flat snapshot mapping that silently lost ADR-062 D14/D15 state.** `serialization.snapshot_to_dict` now raises `SnapshotError` for a snapshot whose flat document cannot hold what it carries (a failed or blocked section, an unknown section, section or record extensions, a document-level must-understand list), instead of returning a document every writer (`to_sectioned_document`, `import_legacy_snapshot`, `import_bundle_facts`, `import_baseline_set`) would store as complete. Write such a snapshot with `save_snapshot`/`write_snapshot`. A probe-matrix file stores such a snapshot as its sectioned document.

### Changed

- **`write_legacy_snapshot_package` takes only an `AbiSnapshot`.** A flat mapping is refused with `TypeError`; decode a stored document with `snapshot_from_dict` first.
