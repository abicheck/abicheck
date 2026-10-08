### Changed

- **The castxml+clang hybrid merge moved to the `workflows/dump/` package.** `abicheck/dumper_hybrid.py` is deleted. Its code moved, unchanged, into `abicheck.workflows.dump.hybrid_merge` (`merge_snapshots`), `abicheck.workflows.dump.hybrid_identity` (ctor/dtor identity reconciliation, Mach-O normalization, function-fact backfill) and `abicheck.workflows.dump.hybrid` (`run_hybrid_dump`). `PROFILE_FIELD_KEYS` is now defined in `comparability_fields`; `abicheck.comparability.PROFILE_FIELD_KEYS` still resolves. Hybrid dump output is unchanged.
