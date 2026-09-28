### Added

- **`ABICHECK_MAX_THREADS` caps abicheck's total thread count** — every thread pool (release members, comparison sides, include probes, per-TU header parses, L4/L5 build-source passes) now borrows its workers from one process-wide budget. Pools nest, so until now nothing bounded their product. A pool created while the budget is spent runs its work in the calling thread instead of blocking, so a tight cap is slower but never deadlocks and never changes a result. Unset, the behaviour is unchanged.

### Changed

- **The include-probe pool follows `ABICHECK_INCLUDE_MAP_JOBS`** — the shared `clang -M` probe pool now has as many threads as that setting allows probes to run, instead of always `max(4, cpu)`.

### Documentation

- **New guide: faster release comparisons on free-threaded Python** (`docs/use/free-threading.md`) — measured gains (a 12-library release: ~160 s on CPython 3.13, 51 s on 3.15t), how to run abicheck on `python3.15t` locally and in the GitHub Action, and every thread/memory control in one table. The Action's `python-version` input now accepts a release-candidate interpreter such as `3.15t` (`allow-prereleases`), which changes nothing for released versions.
