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
  exit code, or its own per-finding `gate_contribution` field. This
  exclusion applies at every verdict-computing chokepoint: `scan
  --against`'s own `--crosscheck KEY=off` post-removal verdict recompute,
  and `compare`'s own opt-in `--surface-metrics`/`--pattern-verdicts`
  verdict recomputations — each previously could resurrect an
  already-`RESOLVED` cross-source finding into a failing verdict when an
  unrelated check was disabled, or an unrelated public-surface/pattern
  finding also fired, on the same run.
- **`mode: scan` Action requests carrying a `compare`-only flag through
  `extra-args` (`--surface-metrics`, `--used-by`, `--diagnostic-comparison`,
  and about three dozen others `scan --help-all` doesn't accept) now stay
  on the legacy `scan` CLI instead of silently routing to `compare`.**
  Previously such a request either reproduced a `compare`-only usage error
  a `mode: scan` caller should never see, or — for the consumer-scoping and
  surface-metrics flags specifically — silently *succeeded* against a
  different finding set/gate scope than the caller's `scan`-shaped workflow
  was written against.
