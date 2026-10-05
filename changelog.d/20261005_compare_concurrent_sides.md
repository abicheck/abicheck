### Changed

- `compare` of two live binaries now resolves both sides concurrently, each
  in its own forked child process (Linux, `balanced` profile): a cold compare
  of two 60-module C++ libraries went from 62-65 s to 41-43 s (clang
  frontend 90 s -> 49 s) at ~1.5x peak memory. The `compare` CLI previously
  resolved the sides one after the other. Stored snapshots, directories and
  packages, multi-threaded callers, `ABICHECK_PARALLEL_EXTRACTION=0` and the
  `low-memory` profile keep the previous behaviour; the two sides' progress
  notes may now interleave on stderr.
