### Fixed

- **A `RESOLVED` cross-source finding no longer fails a passing `compare`/
  `scan --against` run.** `compare()`'s automatic cross-source-checks stage
  (ADR-068 D3) stamps a finding present on OLD but absent on NEW as
  `RESOLVED` — the problem was fixed. That finding used to reach the
  verdict/exit-code gate exactly like a currently-present one, so *fixing*
  a pre-existing issue like `private_header_leak` could still report
  `API_BREAK`/exit 2, contradicting the migration plan's own acceptance
  criterion ("resolved, and visible on a passing run"). A `RESOLVED`
  finding now stays fully visible in the report and every disposition
  ledger, but no longer contributes to the verdict, the severity-preset
  exit code, or its own per-finding `gate_contribution` field.
