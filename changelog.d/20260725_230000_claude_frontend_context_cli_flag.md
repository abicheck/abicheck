<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--frontend-context host|device` (ADR-050 D3/D5, G32 Phase B)**: shared
  `dump`/`compare`/`scan` flag declaring which AST context the L2 header
  frontend should target — the CLI counterpart to a `--dump-manifest`
  document's own `frontend_context` base-profile field. Only `host` (the
  existing behavior) is honored this phase; `device` (a future SYCL/DPC++
  offload-target selector, Phase D) is accepted by the flag's own
  `click.Choice` but rejected at resolution time with a clear error rather
  than silently treated as `host`. Rejected on a directory/package
  (release) `compare`, since the per-library fan-out doesn't thread a
  per-pair compile context.
