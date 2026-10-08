### Changed

- **Internal: the L0 hard-removal fold runs on every compare route.** `fold_l0_hard_removals` (the case97 guard that restores an exported function a header-scoped diff cannot see) moved from `abicheck.cli_helpers_compare` to `abicheck.l0_export_delta` and now runs inside the shared evidence fold (`workflows.pair_evidence.fold_pair_evidence`), so the typed API and every directory/package member apply it exactly as the native `compare` CLI does. No output changes for stored snapshots that carry their ELF table; the comparison already reported those removals on every route.
