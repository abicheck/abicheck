### Fixed

- `overload_ambiguity_introduced` is now reported for snapshots that record a
  constructor under both of its Itanium symbols (`C1`/`C2`), as the native gcc
  header backend does. The second symbol of the kept overload no longer reads
  as a rival that withdraws the ambiguity witness.
