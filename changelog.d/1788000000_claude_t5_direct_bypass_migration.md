### Changed

- **`appcompat.check_appcompat()` and `stack_checker._run_abi_diff()` now
  route through the Tier-2 `service` module** (`service.run_dump`/
  `service.compare_snapshots`) instead of calling `dumper.dump()`/
  `checker.compare()` directly (ADR-037 D10.1, T5 direct-bypass
  migration). No user-visible behavior change for either caller, except
  that `check_appcompat`'s snapshots now record `dependency_scope="full"`
  the same way every other Tier-2 dump caller's snapshots do, instead of
  leaving it unset.
