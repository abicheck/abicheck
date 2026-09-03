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
  `ERROR` bucket a bad flag or crash gets. (Two earlier iterations of this
  fix were still forgeable: signaling over a stderr marker line let a
  crafted `INPUT_NEW_LIBRARY` path — echoed into an unrelated error message,
  even via an embedded newline — spoof the classification regardless of
  anchoring, so the signal moved to a marker file; that file's path was
  then found to leak as an inherited environment variable to every
  subprocess `abicheck` itself spawns during evidence collection, letting a
  PR-controlled build script forge the marker directly, so the variable is
  now popped out of this process's own environment at import time, before
  any subprocess of its own can inherit it.)
