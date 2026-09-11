### Changed

- **`abicheck.serialization`'s snapshot codec moved to `abicheck.storage`.**
  ADR-061 gap E's last open item: the ~1500-line `snapshot_to_dict`/
  `snapshot_from_dict` implementation, the on-disk schema-version history,
  and the reliability-flag computations now live in
  `abicheck.storage.snapshot_codec` and four siblings
  (`storage.snapshot_schema_versions`, `storage.snapshot_encode`,
  `storage.snapshot_decode_declarations`,
  `storage.snapshot_reliability_flags`), each classified `storage` per
  ADR-061's responsibility-package architecture. `abicheck.serialization`
  itself is now a thin, delegation-only facade — every documented name
  (`load_snapshot`/`save_snapshot`/`write_snapshot`/`snapshot_to_dict`/
  `snapshot_from_dict`/`snapshot_to_json`/`snapshot_content_digest`/
  `SCHEMA_VERSION`/the `bundle_facts_*` helpers) keeps its historical
  signature and import path, so no caller of the documented Python API is
  affected. No behavior change.
