### Added

- **`ABICHECK_MAX_THREADS` caps abicheck's total thread count** — every thread pool (release members, comparison sides, include probes, per-TU header parses, L4/L5 build-source passes) now borrows its workers from one process-wide budget. Pools nest, so until now nothing bounded their product. A pool created while the budget is spent runs its work in the calling thread instead of blocking, so a tight cap is slower but never deadlocks and never changes a result. Unset, the behaviour is unchanged.

### Changed

- **The include-probe pool follows `ABICHECK_INCLUDE_MAP_JOBS`** — the shared `clang -M` probe pool now has as many threads as that setting allows probes to run, instead of always `max(4, cpu)`.
