<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

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
  gate exactly (only `fail-on-api-break`/`fail-on-breaking`-gated severities
  block; a `potential_breaking: error` severity-config alone does not, since
  compare mode — unlike `scan` mode — has no independent unconditional
  severity gate for its exit-code-2/4 tiers).
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
- **Sticky PR comment now renders a `Fix:` remediation line** (from the
  report's own `impact` field, already emitted by `reporter.py`) under any
  breaking/review/incomplete finding that carries one.
- **Sticky PR comment normalizes an absolute CI-checkout location** (e.g.
  `/home/runner/work/<repo>/<repo>/include/foo.h:10`) down to its
  repo-relative form (`include/foo.h:10`) when it matches the
  doubled-checkout-directory convention shared by GitHub Actions and
  similar CI systems.
