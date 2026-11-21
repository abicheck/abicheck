---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: historical
generated: false
---

# A failed bundle snapshot build is reported but does not floor the exit code

Index: [Known gaps — closed](../archive/known-gaps-closed.md)

**Status (2026-11-21): CLOSED** by the [ADR-071 2026-11-21 amendment](../adr/071-release-analysis-assurance-fold.md#amendment-2026-11-21-a-failed-bundle-analysis-lowers-release-assurance). A directory/package `compare` whose bundle (cross-library) analysis failed -- non-empty `BundleDiffResult.analysis_errors` from a raising bundle snapshot build, a raising `compare_bundle`, or a raising signature-evidence check -- now has lowered analysis assurance: the release exit resolver (`policy/release_exit_decision.resolve_release_exit_decision_for_report`) folds `1` into the `ANALYSIS_ASSURANCE` axis with `max`, unconditionally, so a clean release exits `1` and a higher code is never lowered. The JSON `exit` block, the `--output-dir` summary, the Markdown "Bundle Analysis Warnings" section and a stderr notice all state it. Regression tests: `tests/test_fail_closed_error_paths.py::test_failed_bundle_axis_floors_the_release_exit_code` (public CLI, all three producers x clean/breaking release) and `::test_bundle_failure_floor_is_max_folded`.

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
