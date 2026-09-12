### Changed

- **`compare --no-baseline` no longer audits a build by comparing it against
  itself.** `checker.compare()` now takes an optional baseline (`old=None`),
  so baseline presence is an input to the one comparison pipeline rather
  than a second product: facts are collected, the applicable checks run,
  policy is applied and a result is built exactly as for a two-sided run,
  and only the stages that need two sides (the detector registry, the SONAME
  policy, build-context reconciliation, the NumPy C-API envelope delta, the
  surface-metric and pattern-verdict modulations, and every OLD->NEW
  post-processing detector) are skipped. Nothing stands in for the missing
  baseline. The visible consequence: every cross-source hygiene finding an
  audit reports now carries the evolution state `not_evaluated` instead of
  `persistent`. Confidence in the observation is unchanged -- the check ran
  against the candidate's own evidence -- but no history was observable, and
  the self-comparison's `persistent` ("present on both sides") was a claim
  about a baseline the run was explicitly told does not exist.
- **`compare --no-baseline --contract` now files no OLD-side provider
  record at all**, instead of filing the candidate's own evidence twice.
  Phase 3's convention is that "not consulted" is an absent entry, never a
  failed one, and a declared-absent baseline was never consulted and cannot
  be short of evidence -- so the coverage ledger answers the audit's real
  question ("does this build carry the evidence the selected domain
  requires?") from the candidate alone. `--contract public` against a
  headerless candidate still exits `1`, and the OLD side no longer reads as
  fully covered.

### Removed

- **`abicheck.policy.no_baseline_findings.NO_BASELINE_EVOLUTION_STATES` and
  `d3_violations`.** The permitted-evolution-state allowlist existed only to
  police the self-diff's own artefact (`persistent`); with the baseline
  genuinely absent the engine can produce no other state than
  `not_evaluated`, so ADR-068 D3 is now a structural property of the run
  instead of a second, mode-specific rule set to keep in sync.

### Fixed

- **`assurance.require_complete: true` is no longer a usage error for a
  directory/package (release) `compare`.** The per-library fan-out folds
  each member's own analysis-assurance floor with `max()` -- the identical
  `0`/`1` contribution a single-pair `compare` of that member computes --
  so a release of one library and that library compared on its own reach
  the same exit code, and library count no longer changes what the setting
  means. The validation-time rejection of `checks[].analysis.assurance:
  complete` for a `kind: bundle` check, which existed only because that CLI
  rejection did, is gone with it. A stored-BundleFacts OLD side still
  rejects the setting explicitly (it carries no per-member result to fold),
  rather than accepting and ignoring it.
- **`--write` is repeatable for a directory/package `compare`.** Every
  requested artifact is rendered from the same already-computed result --
  one analysis, several artifacts, no second per-library pass -- instead of
  a second `--write` being a usage error.
- **The release JSON/JUnit document is no longer implicitly truncated.**
  Each library's `findings` array carried the same 10-entry presentation
  projection the Markdown summary renders, so `--format json` was a lossy
  document nobody asked to truncate and (as its own Markdown truncation
  note said at the time) there was no complete source to fall back to
  unless the run also passed `--output-dir`. The cap is now applied at
  Markdown render time, so the machine document is complete by default; an
  explicit `--max-findings-per-library` (or the env var) still truncates,
  still sets `findings_truncated`/`findings_truncated_kinds`, and -- under
  `--output-dir` -- now names the member's complete artifact in a new
  per-library `complete_report` field.
