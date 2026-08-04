### Changed

- **Contract relevance is now an authoritative pipeline stage rather than a
  shadow annotation (ADR-049 D9).** Under `compare --contract-evaluation`,
  each finding's contract relevance is classified *before* compatibility
  policy runs, and policy then scores only the `EVALUATED` findings —
  `IN_CONTRACT` and `NOT_APPLICABLE`. A `PROVEN_OUT_OF_CONTRACT`,
  `UNKNOWN_UNPROVEN` or `UNKNOWN_UNRESOLVED` finding is `NOT_EVALUATED`: it
  contributes nothing to the verdict or the change gate, so a report can no
  longer state that a finding is outside the promised contract beside a
  process that exited `4` because of it. Uncertainty does not become an ABI
  break either — an unresolved finding is answered on the orthogonal
  contract-coverage axis, which contributes its own exit `1`. Nothing is
  hidden: excluded findings stay in `changes` and in every audit ledger, keep
  their `ChangeKind`, and are disclosed with the relevance and reason that
  explain why they did not gate. Runs that do not pass
  `--contract-evaluation` (the default) are unaffected.

### Added

- **ADR-049 D1's canonical per-finding shape in reports (schema 2.27).**
  Every finding that carries `contract_relevance` now also carries
  `compatibility_evaluation_status` (`EVALUATED`/`NOT_EVALUATED`),
  `compatibility_decision` (its own `Verdict`, or JSON `null` when policy did
  not run — `null` is not a sixth verdict), and `gate_contribution` (what the
  finding actually contributed to the exit code under whichever gate scheme
  the run resolved). Markdown reports gain a "Not Evaluated (Contract)"
  section and a headline count, so the compatibility summary and the findings
  below it can no longer disagree.
