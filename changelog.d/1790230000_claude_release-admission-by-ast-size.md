### Changed

- The directory/package `compare` fan-out now admits members against memory
  using a cost learned from each member's measured header-AST size
  (`floor + 2.0 x AST bytes`), rather than only the fixed 4.0 GiB
  header-depth guess. The first wave is sized exactly as before. After a
  member finishes, later members are charged the largest measured cost, so
  small libraries run more workers at once and very large ones run fewer.
  `ABICHECK_RELEASE_JOB_MEM_GIB` still fixes the budget and turns learning
  off.
