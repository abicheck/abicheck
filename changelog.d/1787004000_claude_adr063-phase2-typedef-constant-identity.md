### Added

- **Typedef and constant findings now carry a resolved `entity_id`**
  (ADR-063 Phase 2's closing slice). `AbiSnapshot.typedefs_qualified` and
  `AbiSnapshot.constants` are plain name-to-string maps with no parsed
  declaration object to hang an identity on, so the typed scope both
  header-AST backends already walk was previously discarded when those maps
  were built. Two additive sidecars -- `AbiSnapshot.typedef_entity_ids` and
  `AbiSnapshot.constant_entity_ids`, keyed exactly like the maps they
  annotate -- now carry it, populated by `castxml` and direct-`clang` alike
  and persisted through the snapshot format (`schema_version` 31). The
  `TYPEDEF_REMOVED`, `TYPEDEF_BASE_CHANGED`, `TYPEDEF_VERSION_SENTINEL`,
  `CONSTANT_ADDED`, `CONSTANT_CHANGED` and `CONSTANT_REMOVED` detectors read
  them, so those findings now key on the same identity primitive every other
  detector family already uses. Both sidecars are empty for a DWARF-only
  snapshot and for one written by an older abicheck, exactly as
  `typedefs_qualified` already is; no verdict, finding or exit code changes.
