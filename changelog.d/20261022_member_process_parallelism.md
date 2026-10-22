### Changed

- **Release members forked into processes are no longer capped at 2 at a time.** The cap of 2 (`GIL_MEMBER_PARALLELISM`) is a GIL limit, and it now applies only to the thread fan-out. The process fan-out defaults to the CPU count, capped at 6 (`PROCESS_MEMBER_PARALLELISM`), and memory admission still bounds it. On a 28-member `--depth headers` release with a warm cache, wall time fell from 868 s to 346 s, CPU rose by 1.8 %, and the report was identical. `ABICHECK_MEMBER_JOBS` still overrides the default.

### Fixed

- **`ABICHECK_MEMORY_TRACE` samples now carry `thread_id`, the OS thread id.** Pool workers reuse thread names on new OS threads, and each new thread's CPU clock starts again from 0. Pairing samples by `thread` name therefore produced made-up stage CPU, for example 5 029 s CPU against 3.9 s wall for `release.member.attribute:done`. Pair samples by `thread_id` instead.
