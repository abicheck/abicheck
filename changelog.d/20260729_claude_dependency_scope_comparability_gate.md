<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`dump`/`compare` dependency-scoping asymmetry could silently mix a
  filtered and an unfiltered snapshot** (urgent follow-up to `dumper_scoping.py`
  / PR #649): `dump`'s default output excludes toolchain/system-header
  declarations, but `compare`'s own live-binary dumping never applies that
  filter — so the recommended `dump old.so -o baseline.json` followed by
  `compare baseline.json new.so` could compare a dependency-filtered snapshot
  against an unfiltered one with neither `scope_fingerprint` nor
  `profile_fingerprint` (neither observes this axis) catching the mismatch,
  producing an ordinary verdict instead of failing loudly. `AbiSnapshot` now
  carries a `dependency_scope` field (schema v18, `"filtered"`/`"full"`/`None`)
  recording which mode a snapshot was produced under;
  `comparability.check_contracts_comparable` (wired into every
  `compare`/`scan --against`/MCP `abi_compare` entry point via
  `checker.compare`) now raises `ScopeMismatchError` when the two sides'
  effective dependency scope differs — a missing/pre-v18 value is treated as
  `"full"` (the only behavior that existed before `dumper_scoping.py`), so an
  old baseline compared against a freshly filtered dump is caught too. Only
  fires when at least one side actually has header-derived declarations; a
  binary/DWARF-only compare is unaffected. Making `compare`'s own live-binary
  dumping apply the same default filtering (so the recommended workflow
  succeeds instead of merely failing safely) is deliberately out of scope for
  this fix — a larger, separately-tracked change spanning `dump`/`compare`/
  `scan`/the Python API/MCP/the GitHub Action.
