### Changed

- **Release members forked into processes are no longer capped at 2 at a time.** The cap of 2 (`GIL_MEMBER_PARALLELISM`) is a GIL limit, and it now applies only to the thread fan-out. The process fan-out defaults to the CPU count, capped at 16 (`PROCESS_MEMBER_PARALLELISM`). The pool never exceeds the number of members, and memory admission still bounds it. On a 28-member `--depth headers` release with a warm cache, a pool of 6 instead of 2 cut wall time from 868 s to 346 s for +1.8 % CPU, with an identical report. `ABICHECK_MEMBER_JOBS` still overrides the default.

### Fixed

- **Forked release members and compare sides no longer redo each other's header AST work.** Threads shared one clang run per header set and one projection stream per AST through the in-process acquisition table, and forked children did not. A cross-process lock per AST cache key, and per projection sidecar, now makes the first process produce the entry while siblings wait and read it from disk. Before, a cold `dump.binary` cost 2.4x per dump in processes, and projections were re-streamed from a 250 MB AST. Uses `flock`; a no-op where that is unavailable.
- **A cold compare side looks the snapshot cache up once, not twice.** The parent's cache-only probe records its miss, and the forked side child skips the identical lookup (it was 112 lookups for 56 sides).
- **`ABICHECK_MEMORY_TRACE` samples now carry `thread_id`, the OS thread id.** Pool workers reuse thread names on new OS threads, and each new thread's CPU clock starts again from 0. Pairing samples by `thread` name therefore produced made-up stage CPU, for example 5 029 s CPU against 3.9 s wall for `release.member.attribute:done`. Pair samples by `thread_id` instead.
