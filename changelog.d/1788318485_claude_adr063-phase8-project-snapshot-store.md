### Added

- **A directory-backed `ProjectSnapshot` store, a v1-v25 import adapter, and
  a per-section DTO layer land as internal infrastructure** (ADR-062 Phase 1
  A1.1-A1.3, jointly ADR-063 Phase 8). `abicheck.storage.dto.SectionDTO` is
  a distinct, versioned, explicitly-encoded envelope per section (never
  `dataclasses.asdict()`, enforced mechanically by a new
  `scripts/check_ai_readiness.py` gate), built on the existing `SemanticIR`
  encoder; `abicheck.storage.import_v1.import_legacy_snapshot` reshapes an
  already-serialized legacy snapshot document into a one-artifact,
  one-variant package; `abicheck.project_snapshot_store.DirectoryObjectStore`
  plus its manifest/ref writer and reader implement ADR-062 D6's on-disk
  layout over ADR-059's compressed, atomic storage envelope. **Not yet
  reachable from any CLI command, config key, or Action input** -- every
  existing `dump`/`compare`/`scan` snapshot, baseline set, and `BundleFacts`
  document is unchanged. See `docs/reference/project-snapshot-format.md` and
  `docs/contribute/plans/storage-format-v2.md` for the full account of what
  is and is not implemented.
