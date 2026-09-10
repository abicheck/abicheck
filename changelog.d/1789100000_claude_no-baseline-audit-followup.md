### Fixed

- **The audit honors `.abicheck.yml`'s suppression *acceptance* rules.**
  `suppression.require_justification` and `suppression.strict` decide whether
  a suppression document may be used at all, and the audit resolved the
  project config without reading either — so a reasonless or long-expired
  rule that ordinary `compare` rejects was accepted, suppressed the finding,
  and exited `0`. Both settings now reach the audit's real run *and* its
  `--dry-run` validation load, so a preview cannot approve a run that then
  cannot start.
- **A saved ABICC Perl dump is exempt from the `--depth` floor, like any
  other pre-built ABI description.** `--depth build`/`--depth source` is a
  floor for live extraction, not a ceiling for a description someone already
  wrote down, but the carve-out recognized only `.abi.json` and
  `ProjectSnapshot` packages — so `--depth source` exited `7` on a saved
  ABICC dump and `0` on the equivalent `.abi.json`. A `Module.symvers`
  manifest and a bare BTF/CTF blob stay on the raw-evidence side, where the
  floor still applies. Applies to two-sided `compare` too, through the same
  shared predicate.
- **A suppressed audit finding keeps its full ADR-067 rule provenance.** The
  audit published `suppression_rule`, the rule's short *display label* —
  `label` or `reason`, never both — so a waiver stating both lost the reason
  it was written for and the file it lives in, which is exactly what a
  reviewer needs to decide whether it still applies. Every projection now
  carries the run's own disposition-ledger record (rule id, source file,
  reason, label, expiry) as `suppression_provenance`, joined by object
  identity through the same `rule_for` lookup the two-sided report uses.
  Every projection carries it: the Markdown suppression table gains
  reason/source/expiry columns, the SARIF suppression's `justification`
  states the reason with the full record beside it in `properties`, and the
  JUnit `<skipped>` message and body do the same. `suppression_rule` is
  unchanged for existing consumers.
- **A gated audit's JUnit output names the axis that fired.** The suite
  carried only the total exit code and the contract-coverage contribution,
  and the failure text listed every axis that *might* have gated — so a
  consumer stopped by the evidence contract could not tell which one it was.
  Each orthogonal axis is now published as its own `exit_axis.<name>`
  property with its own contribution, the failure text names only the axes
  that actually contributed, and the contract-coverage ledger's provider
  details are included. JSON, Markdown, oneline and SARIF already did this.
