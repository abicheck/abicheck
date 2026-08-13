<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`action/run.sh`'s `_coverage_gated()` no longer defeats
  `contract.unresolved: warn`** — a real bug found in review, distinct from
  (and pre-dating) the release-mode work in this fragment: whenever a
  readable JSON report answered `contract_coverage_exit_contribution: 0`,
  the function still fell through to grepping stderr for "Contract coverage
  incomplete" — a substring the diagnostic notice contains even in the
  `warn`-accepted case (worded "Accepted by contract.unresolved: warn..."
  rather than "Contributes N..."). Any run with both a readable JSON (true
  for most markdown-format `compare`/`scan` invocations) *and* that stderr
  notice therefore failed the step regardless of `warn`, defeating the
  documented acceptance mechanism entirely. The JSON answer is now
  authoritative whenever the report is readable; the stderr fallback is
  reached only when it genuinely is not (an unreadable/malformed/absent
  report).
- **Sticky PR comment no longer folds analysis-quality findings into "Needs
  review"** — `layer_coverage_asymmetric`, `evidence_required_missing`,
  `source_fact_coverage_incomplete`, and `dwarf_info_missing` are
  comparison-quality signals, not compatibility findings, and previously
  drove the same generic `⚠️ Review recommended` headline as a genuine source
  API change. They now render in their own **🛑 Analysis incomplete** section
  with a distinct headline (`🛑 Source analysis incomplete` /
  `⚠️ Analysis coverage reduced`) and are excluded from the Breaking/Needs
  review/Safe counts, so a clean, implementation-only PR with a coverage gap
  no longer reads as "this PR made a risky API change." Whether that headline
  is blocking or advisory mirrors `action/run.sh`'s actual `compare`-mode
  gate exactly: a `breaking`-severity finding blocks only under the new
  `gate_breaking` flag (`fail-on-breaking`, default `True`); an
  `api_break`/`risk`-severity finding only under `gate_api_break`
  (`fail-on-api-break`; `potential_breaking: error` alone is not sufficient,
  since compare mode — unlike `scan` mode — has no independent unconditional
  severity gate for its exit-code-2/4 tiers); and a `compatible`-severity
  finding (e.g. `dwarf_info_missing`) whose resolved `addition`/
  `quality_issues` severity-config category is `error` blocks
  unconditionally — that's compare's own exit-code-1 `SEVERITY_ERROR` tier,
  which has no `fail-on-*` gate at all.
- **Release-mode (`compare` on directory/package operands) reports still
  fold ordinary evidence-coverage findings into their per-library counts**
  rather than a separate incomplete count — documented, not fixed, in this
  pass: the release JSON's per-library `findings` list is capped (10 total,
  possibly truncated) and carries no `severity` field, so any bucketing
  built on it would be unavoidably best-effort/sample-based, unlike the
  exhaustive `compare`/`appcompat` path. A correct fix needs an
  authoritative per-category evidence-finding count added to
  `cli_compare_release.py`'s release JSON schema — a separate, scoped
  change, not a `pr_comment.py`-only fix.
- **Release-mode sticky comment now surfaces the orthogonal contract-
  coverage ledger** (ADR-049 Phase 7), a materially different, already-
  available signal from the per-library findings-list gap above: a release
  whose only problem was incomplete `--contract-evaluation` coverage
  previously had `incomplete == []` and zero changes, so the default
  `--on=changes` policy silently skipped (or deleted) the sticky comment
  even though the release's own exit code had already failed via the
  ledger's unconditional `max()` fold, and `--on=always` rendered
  "No ABI changes" beside a release that had already failed. The release
  JSON's `contract_coverage_exit_contribution` (release-level and
  per-library) now produces a blocking analysis-incomplete finding naming
  the affected librar(y/ies), same as the single-pair `compare` report's
  `contract_coverage_failures` ledger already did. **The release-mode
  finding was also unreachable in the rendered comment body**: the release
  path's own `_body_sections` early return skipped `model.incomplete`
  entirely, so only the headline/count surfaced it, never the actual
  "Incomplete for: ..." detail naming the affected library — now appended
  after the per-library results table, same as compare mode's own section.
- **Release-mode contract-coverage now stays visible under
  `contract.unresolved: warn`** — a real second gap in the fix above, found
  in review: `contract.unresolved: warn` deliberately zeroes the exit-code
  contribution while the underlying failures stay real (an acceptance of
  incomplete assurance, not a way to hide it — see `contract_coverage_
  exit.py`'s own module docstring), but the first release-mode fix gated
  *finding creation itself* on that same now-zeroed contribution, making a
  warn-accepted coverage gap invisible all over again. `cli_compare_
  release.py` now stamps a second, never-zeroed
  `contract_coverage_failure_count` (release-level and per-library,
  alongside the existing `contract_coverage_exit_contribution`) that finding
  creation keys on instead, with blocking-ness still following the
  contribution alone — an advisory (warn-accepted) release finding now
  renders `⚠️ Analysis coverage reduced` rather than not rendering at all.
  `compare` on a directory/package operand also announces this to stderr
  for a warn-accepted gap now, worded as accepted rather than blocking,
  mirroring single-pair `compare`'s own `coverage_failure_diagnostic`.
- **`compare` on a directory/package operand now announces incomplete
  contract coverage to stderr for every non-JSON output format**, not just
  `--format json`'s own `contract_coverage_exit_contribution` field — the
  release fan-out already folded the contribution into its real exit code
  unconditionally, but (unlike single-pair `compare`'s `announce_coverage_
  floor`) never said why anywhere else. This closes a real gap in `action/
  run.sh`'s own `_coverage_gated()` gate: outside a `pull_request` event (or
  with PR comments disabled), the Action's secondary-JSON/PR-comment rerun
  never runs for a release-style operand, so `_coverage_gated()`'s stderr
  fallback was the only way it could ever detect the axis — and that
  fallback had nothing to grep for release runs until now.
- **Sticky PR comment "Needs review" headline names the actual reason**
  instead of a generic `⚠️ Review recommended` whenever every finding in the
  bucket agrees on one severity: `⚠️ Source API changed; binary ABI
  unchanged` for a source-only change, `⚠️ Compatibility risk — review
  recommended` for a risk finding.

### Changed

- **Sticky PR comment renamed its generic "✅ Safe" section to "ℹ️
  Informational findings"** and its all-compatible headline to "No
  compatibility impact detected" — "Safe" read as an absolute guarantee it
  wasn't.
- **Sticky PR comment now demangles C++ symbols** for breaking/review/
  additions findings, showing the readable signature
  (`Calculator::multiply(int, int) const`) as the primary Symbol value
  instead of the raw mangled linker name, with the mangled form kept as
  `linker: ...` evidence in full-detail rows.
- **Sticky PR comment now renders an `Impact:` line** (from the report's own
  `impact` field, already emitted by `reporter.py`) under any
  breaking/review/incomplete finding that carries one — labelled "Impact",
  not "Fix", since not every `impact=` entry is an actionable remediation
  step.
- **Sticky PR comment normalizes an absolute CI-checkout location** (e.g.
  `/home/runner/work/<repo>/<repo>/include/foo.h:10`) down to its
  repo-relative form (`include/foo.h:10`) when it matches the
  doubled-checkout-directory convention shared by GitHub Actions and
  similar CI systems.
