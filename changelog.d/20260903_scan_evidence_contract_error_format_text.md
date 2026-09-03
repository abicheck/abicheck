### Fixed

- **The GitHub Action can now tell a `scan --depth`/`--abi3` evidence-contract
  abort apart from a generic CLI error on the Action's default `format: text`
  step.** `abicheck scan`'s `_EvidenceContractError` abort (a pinned
  `--depth`/`--source-method` with no source evidence, or `--abi3` targeting
  a binary that isn't a recognisable CPython extension module) now always
  prints a stable stderr marker line ahead of its existing `Error: ...`
  message, independent of `--format` — closing the one remaining gap
  [ADR-064](docs/contribute/adr/064-canonical-gate-algorithm-and-exit-decision.md)
  named as still open ("the `--format text` gap"). `action/run.sh`'s
  `_evidence_contract_gated()` now falls back to matching that marker in
  stderr when there is no JSON report to read, so a `format: text` step (the
  Action's documented default) publishes `EVIDENCE_CONTRACT_ERROR` instead of
  the generic `ERROR` bucket a bad flag or crash gets.
