### Added

- **`compare --contract-evaluation`** — exposes ADR-049 Phase 3's shadow
  contract evaluator on the native `compare` CLI command (previously reachable
  only via the Python service API and the MCP `abi_compare` tool). Stamps each
  finding in the report with `contract_relevance`, `contract_reason_code`, and
  `contract_assurance` fields, advisory only (never changes `verdict` or
  `exit_code`). Not supported for directory/package (release) comparisons yet.
- **`compare --audit-suppressions`** — wires the existing
  `SuppressionList.audit()`/`SuppressionAudit` (`suppression.py`) into
  `compare`: audits the `--suppress` rule file against this run's findings
  (stale rules, matches on BREAKING changes, expired/near-expiry rules).
  Requires `--suppress`. Adds a `suppression_audit` key in `--format json`
  and a `## Suppression Audit` section in markdown/text/review. Advisory
  only; never changes `verdict` or `exit_code`. Not supported for
  directory/package (release) comparisons yet.
