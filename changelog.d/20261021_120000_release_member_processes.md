### Changed

- **Parallel release members run in forked processes on GIL builds** — a
  directory/package `compare` with more than one member worker now compares
  each member in its own forked child process on Linux under the GIL (when no
  other thread runs), instead of threads that the GIL serialized: measured,
  three members ran 2.88x faster in three processes than serially, where two
  threads had inflated CPU time 1.93x for no wall-time gain. Reports are
  byte-identical to the thread path, a failed or killed member is still
  recorded as that member's failure, the `ABICHECK_MEMBER_JOBS` cap and the
  `ABICHECK_RELEASE_JOB_MEM_GIB` memory admission still bound how many run at
  once, and each member's own two sides can again resolve in concurrent side
  processes. Members no longer share request-scoped caches, so peak memory
  per member is its own working set. `ABICHECK_MEMBER_PROCESSES=0` restores
  the thread fan-out; free-threaded interpreters, non-Linux platforms and
  `-j1` are unchanged. Owner: `workflows/release_member_processes.py`.
