### Changed

- **Internal: the whole-snapshot dump cache has one owner** (ADR-061) — `service_dump_cache.py` and `snapshot_cache.py` are merged into `abicheck/workflows/dump/cache/` (`api.py` exposes `cached_run_dump`; `key.py`, `salt.py` and `store.py` are private). Every dump input is now keyed in exactly one place, so a new input can no longer be keyed on one side and missed on the other. Cache keys and the on-disk entry format are unchanged, and the golden key test pins that. The `ABICHECK_SNAPSHOT_CACHE_WRITE` and `XDG_CACHE_HOME` settings behave as before.
