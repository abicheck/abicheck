<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ADR-049 Phase 0: reserved contract-relevance vocabulary** (no behavior
  change): `abicheck/contract_relevance_types.py` reserves the
  `ContractMode` (`public`/`exports`/`all`), `ContractRelevance`
  (`IN_CONTRACT`/`PROVEN_OUT_OF_CONTRACT`/`UNKNOWN_UNPROVEN`/
  `UNKNOWN_UNRESOLVED`/`NOT_APPLICABLE`), and related enums from
  [ADR-049](docs/contribute/adr/049-contract-relevance-and-compatibility-configuration.md),
  plus a stable per-finding `contract_reason` code registry, the legacy
  `--[no-]scope-public-headers` alias table, and independent
  `contract_evidence`/`evaluation_context` schema-version counters. This is
  a leaf module only — nothing in detection, policy, the CLI, or reports
  produces or consumes these types yet; see
  `docs/contribute/plans/public-contract-default.md` for the phased rollout.
