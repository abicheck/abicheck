### Changed

- **Internal: ADR-061 gap B — `architecture/modules.yaml`'s
  `public_root_surfaces` entries now separate "supported import path" from
  "owning layer".** `abicheck.api_types`, `abicheck.checker_policy`,
  `abicheck.contract_coverage_ledger`, and `abicheck.qualified_name_segments`
  are now thin, delegation-only facades over real implementations moved to
  `workflows.request_inputs`/`workflows.contracts`, `policy.classification`/
  `policy.evidence_status`, `policy.coverage_ledger`, and
  `compare.qualified_name_normalization`/`storage.closure_identity`
  respectively; `abicheck.errors` and `abicheck.dumper_contract` already had
  a real owner (`model`/`extract`) and were dropped from the exemption list
  as stale bookkeeping. No behavior or public import path changed — every
  existing `from abicheck.<name> import ...` call site keeps working
  unchanged, verified by new facade-delegation tests. `checker_policy`,
  `contract_gating`, and `reclassify` stay deliberately unclassified (a
  confirmed, not merely asserted, "no single layer" leaf both `model`'s
  legacy `DiffResult` and several `policy`/`workflows`/`report`/`compare`
  modules depend on); `abicheck.schemas` and `abicheck.serialization`
  likewise stay unclassified, each with a recorded reason in
  `docs/contribute/adr/061-responsibility-package-architecture.md`'s gap B
  closure note.
