### Changed

- **The release fan-out reserves memory for the state resident beside its
  workers.** `workflows/release_jobs.py` clamped the auto-sized pool with
  `int(available / budget)`, committing 100% of what the memory probe
  reported to worker working sets. The fan-out is a `ThreadPoolExecutor`, so
  three things live in that same address space and were charged nothing:
  each completed member's retained result, the shared header-depth context
  (AST/template indexes, acquisition and metadata reuse), and the staleness
  of a `MemAvailable` sampled once at pool-sizing time, before any of it
  exists. Measured on a real 16 GB host: `--depth headers` probed 13.17 GiB
  available and admitted 3 workers at the 4.0 GiB budget — a 12.0 GiB
  commitment, 91% of available, with the parent's share still to come out of
  the remaining 1.17 GiB. That is the shape that OOM-kills rather than
  clamps, which is the one failure this cap exists to prevent.

  Workers are now admitted against *committable* memory: a utilization
  fraction of the probe, less a flat reserve, both tunable
  (`ABICHECK_RELEASE_MEM_UTILIZATION`, `ABICHECK_RELEASE_MEM_RESERVE_GIB`).
  On the same host that admits 2 workers for an 8.0 GiB commitment (61%).

  Conservative in one direction only, and pinned as invariants rather than
  as arithmetic: it can never admit *more* than the rule it replaces, always
  admits at least one worker (a run that compares nothing is never a clean
  pass, ADR-065), stays `None` — clamp skipped — when RAM cannot be probed,
  is monotonic in available memory, and still scales up, so it is not a
  blanket single-worker policy (a 32 GiB host still fans out to six). Binary
  depth is unchanged on any host that was not already memory-clamped, so an
  ordinary `compare OLD_DIR NEW_DIR` is sized exactly as before. The tests
  derive the old rule independently rather than importing it, and were
  verified non-vacuous against three mutants: the reserve removed, the
  reserve inflated into a blanket clamp, and the one-worker floor dropped.

  This bounds the *concurrent* half of a bundle's peak only. The retention
  half is bounded on the default path (a compact `BundleSignatureEvidence`
  per member) but not under `need_full_snapshots`, which JUnit and
  `--bundle-facts-out` both set and which holds every member's full old and
  new snapshot at once; see `docs/contribute/known-gaps.md` for why that is
  a separate, larger change and what it means for the 16 GB runner receipt.
