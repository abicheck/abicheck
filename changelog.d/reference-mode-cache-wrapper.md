### Added

- **`ABICHECK_REFERENCE_MODE=1` turns every optimization off.** Every cache
  now goes through one wrapper (`abicheck/model/execution_cache.py`): set the
  variable and each in-memory, request-scoped and on-disk cache (whole-snapshot,
  header-AST, build-evidence, source-ABI) computes instead of reading or
  storing, and every thread pool runs its work inline, so a directory/package
  `compare` takes its sequential member path. Results never change -- the
  switch exists for differential testing (the H5 harness, now also run weekly
  by `.github/workflows/reference-mode.yml`) and for ruling a cache out while
  chasing a bug. See `docs/reference/environment.md`.

### Changed

- **One cache wrapper replaces the per-site mechanisms.** `functools`
  memos, module-level memo dicts, `ContextVar` memos and object-attached
  memos are migrated onto the wrapper, which keys an entry on the request's
  named inputs, counts hits/misses/bypasses per cache, and re-validates
  I/O-dependent entries: the clang/compiler probes now re-probe when the
  executable at that path changes. `abicheck.extract.digest_memo.DigestMemo`
  is removed; its owner is `abicheck.model.execution_cache.MemoryCache`. A new
  repository gate (`tests/test_module_cache_gate.py`) rejects a `functools`
  memo or new module-level mutable state outside a reasoned allowlist.
