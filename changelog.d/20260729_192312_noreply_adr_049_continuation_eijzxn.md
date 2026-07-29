### Added

- **`compare --contract-evaluation`** — exposes ADR-049 Phase 3's shadow
  contract evaluator on the native `compare` CLI command (previously reachable
  only via the Python service API and the MCP `abi_compare` tool). Stamps each
  finding in the report with `contract_relevance`, `contract_reason_code`, and
  `contract_assurance` fields, advisory only (never changes `verdict` or
  `exit_code`). Not supported for directory/package (release) comparisons yet.
