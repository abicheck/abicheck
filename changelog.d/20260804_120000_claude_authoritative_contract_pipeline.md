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
  below it can no longer disagree. `scan --against` reports gain a matching
  `not_evaluated` count and findings bucket (scan schema 1.8), so an excluded
  fact stays itemized there too.

### Fixed

- **Every renderer now agrees with the verdict it prints.** Under
  `compare --contract-evaluation`, SARIF annotated a `NOT_EVALUATED` finding
  `level: error`, JUnit reported it as a `<failure>`, and the review digest
  listed it under "Top impacted symbols" — all beside a `NO_CHANGE` verdict
  and a clean exit. SARIF now emits `level: note` with the relevance and
  reason that explain it, JUnit gives it a passing `<testcase>`, and the
  digest's impacted list is over what actually gated. Conserved in all three:
  downgraded, not dropped.
- **`scan --against --contract-evaluation` findings carry ADR-049 D1's
  canonical decision pair** (`compatibility_evaluation_status`,
  `compatibility_decision`, `null` for an unscored row), so a scan row and
  the `compare` finding for the same fact can be compared field by field
  (§6.4). Not `gate_contribution` — that is a property of a severity gate
  `scan --against` does not run.
