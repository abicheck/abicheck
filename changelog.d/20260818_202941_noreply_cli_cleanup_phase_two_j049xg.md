### Added

- **`resolve_dump_request`/`execute_dump_request`** — `service_dump_pipeline`
  gains a resolve/execute split (`ResolvedDumpRequest`/`DumpResult`) around
  `run_dump_request`, which is now a thin adapter over the two and keeps
  returning a bare `AbiSnapshot` unchanged. First step of CLI cleanup phase
  two's typed `dump`/`scan` convergence (PR C / PR 3A) — `dump --dry-run`
  does not yet build from `ResolvedDumpRequest`.
