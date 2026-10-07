### Fixed

- On a free-threaded interpreter, the default number of release members run at
  once (`ABICHECK_MEMBER_JOBS` unset) is now the CPU count capped at 4 instead
  of the full CPU count. On a 28-member release on a 224-core host, 96 members
  were no faster than 4 but used 6.5x the CPU and ~4x the memory.

### Changed

- `function_signature_index` reuses one `SemanticIRIndex` per IR inside a
  comparison instead of rebuilding it (and re-ranking every IR entity) on each
  of its ~20 calls per `compare()`.
