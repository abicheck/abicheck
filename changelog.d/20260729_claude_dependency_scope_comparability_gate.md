<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`dump`/`compare` dependency-scoping asymmetry could silently mix a
  filtered and an unfiltered snapshot** (urgent follow-up to `dumper_scoping.py`
  / PR #649): `dump`'s default output excludes toolchain/system-header
  declarations, but `compare`'s own live-binary dumping (`service.run_dump`)
  did not apply that filter — so the recommended `dump old.so -o baseline.json`
  followed by `compare baseline.json new.so` could compare a
  dependency-filtered snapshot against an unfiltered one with neither
  `scope_fingerprint` nor `profile_fingerprint` (neither observes this axis)
  catching the mismatch, producing an ordinary verdict instead of failing
  loudly. `AbiSnapshot` now carries a `dependency_scope` field (schema v18,
  `"filtered"`/`"full"`/`None`) recording which mode a snapshot was produced
  under: `dump`'s own serialization step tags it explicitly, and
  `service.run_dump`'s live-binary path is tagged to match. (CodeRabbit
  review: at the time this fix landed, `run_dump`'s live-binary path never
  filtered and was always tagged `"full"` — a separate, later follow-up in
  this same PR, `20260729_claude_compare_default_dependency_filtering.md`,
  made it filter by default too, matching `dump`, so that description below
  is now historical rather than current behavior.)
  `comparability.check_contracts_comparable` (wired into
  every `compare`/`scan --against`/MCP `abi_compare` entry point via
  `checker.compare`) now raises `ScopeMismatchError` when both sides carry an
  explicit, differing value — this catches the originally-reported danger
  once both sides come from a current abicheck build. Deliberately does
  **not** treat a missing/pre-v18 value as `"full"`: `dumper_scoping.py`'s
  filtering already shipped as `dump`'s default before this field existed,
  so an ordinary pre-v18 baseline is usually already-filtered content that
  simply predates the tag — assuming `"full"` for it would have spuriously
  broken the single most common workflow (compare a cached baseline against
  a fresh dump). Only fires when at least one side actually has
  header-derived declarations; a binary/DWARF-only compare is unaffected.
  Also bumps
  `snapshot_cache._SNAPSHOT_CACHE_VERSION` (whole-snapshot disk cache) to
  invalidate any pre-existing cache entry written before `run_dump` started
  tagging its result — a warm cache hit returns the cached snapshot directly
  without ever calling `run_dump`, so a stale, untagged entry would
  otherwise keep bypassing the new gate indefinitely.
