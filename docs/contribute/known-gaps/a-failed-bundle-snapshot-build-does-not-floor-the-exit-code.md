---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A failed bundle snapshot build is reported but does not floor the exit code

Index: [Known gaps](../known-gaps.md)

**Status (2026-11-13): OPEN.**

## What happens

A directory/package `compare` runs bundle (cross-library, ADR-023) analysis
after the per-library fan-out. When the bundle snapshot cannot be built
(`bundle.build_bundle_snapshot_mixed` raises: an unreadable stored
sub-package, a parser bug on one member), `workflows/release_bundle.py`'s
`run_bundle_analysis` now records the failure in
`BundleDiffResult.analysis_errors` (`bundle snapshot could not be built:
...`). The JSON report carries it as `bundle_analysis_errors` and the
Markdown report as a "Bundle Analysis Warnings" section, the same record a
raising `compare_bundle` already left.

Before 2026-11-13 the function returned `None`, so the bundle axis vanished
from the report with only a stderr warning.

The exit code is still unaffected. A release whose per-library comparisons
are clean exits `0` although no cross-library check ran. The same holds for
the older `analysis_errors` producers (a raising `compare_bundle`, a raising
signature-evidence check).

## Why it is not closed here

Every exit axis that could carry it is owned by an ADR that excludes it:

- ADR-071 §3 defines analysis assurance as a fold of *per-pair* member
  assurance and states a release has no evidence of its own to assess, so
  feeding a release-level bundle failure into `ANALYSIS_ASSURANCE` would
  contradict it.
- The contract-coverage and scope-completeness axes answer other questions
  (consumer contracts; which members were compared).
- Raising `0 -> 1` on a new release-level axis changes an exit code, which
  AGENTS.md "Authority" reserves for an ADR and its migration.

## Closing it

Write an ADR (or an ADR-064/071 amendment) that gives the release ladder a
bundle-analysis-completeness input. It should fold with `max` and raise only
`0 -> 1`, like the coverage axes. Opt-in (`assurance.require_complete`) or
default is the ADR's decision. Then wire `collect_bundle_result`'s
`analysis_errors` into `resolve_release_exit_decision_for_report` and
convert the canary into a regression test.
