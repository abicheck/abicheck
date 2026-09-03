### Fixed

- **The GitHub Action can now tell a `scan --depth`/`--abi3` evidence-contract
  abort apart from a generic CLI error on the Action's default `format: text`
  step.** `abicheck scan`'s `_EvidenceContractError` abort (a pinned
  `--depth`/`--source-method` with no source evidence, or `--abi3` targeting
  a binary that isn't a recognisable CPython extension module) now signals
  the Action over a private marker file (`action/run.sh` creates and names
  it itself, never from any PR-controlled input), closing the one remaining
  gap
  [ADR-064](docs/contribute/adr/064-canonical-gate-algorithm-and-exit-decision.md)
  named as still open ("the `--format text` gap"). `action/run.sh`'s
  `_evidence_contract_gated()` now checks that marker file when there is no
  JSON report to read, so a `format: text` step (the Action's documented
  default) publishes `EVIDENCE_CONTRACT_ERROR` instead of the generic
  `ERROR` bucket a bad flag or crash gets. (An earlier iteration of this fix
  signaled over a stderr marker line; two review rounds found that channel
  forgeable — a crafted `INPUT_NEW_LIBRARY` path echoed into an unrelated
  error message could spoof it, even with a whole-line match, since a legal
  Unix filename may itself contain embedded newlines — so the signal was
  moved off stderr entirely.)
