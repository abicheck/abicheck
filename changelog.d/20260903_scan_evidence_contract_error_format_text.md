### Fixed

- **The GitHub Action can now tell a `scan --depth`/`--abi3` evidence-contract
  abort apart from a generic CLI error, regardless of `--format`.**
  `abicheck scan`'s `_EvidenceContractError` abort (a pinned
  `--depth`/`--source-method` with no source evidence, or `--abi3` targeting
  a binary that isn't a recognisable CPython extension module) now exits
  with its own dedicated process exit code (`7`), closing the one remaining
  gap
  [ADR-064](docs/contribute/adr/064-canonical-gate-algorithm-and-exit-decision.md)
  named as still open ("the `--format text` gap"). `action/run.sh` dispatches
  on that exit code directly, so a `format: text` step (the Action's
  documented default) publishes `EVIDENCE_CONTRACT_ERROR` instead of the
  generic `ERROR` bucket a bad flag or crash gets — with no JSON report, no
  stderr text, and no environment variable involved in the classification at
  all. (Three earlier iterations of this fix were each shown forgeable in
  turn by a PR-controlled build script running as part of this scan's own
  evidence collection: a stderr marker line — even whole-line-matched — could
  be spoofed via a crafted path containing an embedded newline; a
  marker-file path passed as an environment variable could be read back out
  of that same environment, and even after being removed from `os.environ`,
  recovered from `/proc/<pid>/environ`, which reflects a process's *initial*
  environment regardless of later mutation. A process's own exit code,
  reported to its parent by the OS kernel, has none of those gaps.)
