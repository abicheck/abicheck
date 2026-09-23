### Changed

- At header depth, the directory/package `compare` fan-out now admits
  members through a memory gate that also learns each member's cost from
  its measured header-AST size (`floor + 2.0 x AST bytes`). The per-depth
  4.0 GiB budget remains the minimum charge, so the first wave is sized
  exactly as before. Once a member measures above that budget, later members
  are charged the larger figure and fewer run at once, which avoids
  overcommit on releases with very large headers.
  `ABICHECK_RELEASE_JOB_MEM_GIB`, an explicit job count, or any depth other
  than headers keeps the previous fixed sizing.
