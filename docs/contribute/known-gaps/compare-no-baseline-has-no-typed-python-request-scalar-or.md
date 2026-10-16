---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# `compare --no-baseline` has no typed Python request, scalar or directory (2026-10-03)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** grep for 'NoBaseline.*Request' in abicheck/ finds nothing, so there is still no typed no-baseline request.


Neither shape of the single-build audit -- `compare --no-baseline FILE` nor
the N-library `compare --no-baseline DIR` (one-comparison-product F-23) --
has a typed request in `abicheck.service` the way two-sided `compare` has
`CompareRequest`/`run_compare_request`. Both are reachable from Python only
through the framework-free workflow functions the CLI itself calls:
`workflows.no_baseline_compare.audit_no_baseline_candidate` (one candidate,
given a `NoBaselineAuditInputs`), and `workflows.no_baseline_set.
resolve_no_baseline_set_plan` + `run_no_baseline_set` (a set), rendered by
`report.no_baseline.render_no_baseline` / `report.audit_set.
render_no_baseline_set`. What is missing is the front-end half: resolving
`.abicheck.yml` (`scope.public`, `scope.on_incomplete`, `release.*`,
`deployment:`, `assurance.require_complete`, the suppression acceptance
checks) and the policy documents into those inputs, which today only
`frontends/cli/commands/no_baseline_invocation.py` does -- so a Python
caller has to restate it, and could resolve a config differently from the
CLI. Deferred deliberately when F-23 landed: the request type should be one
decision covering both cardinalities (ADR-061's "one model, any
cardinality"), not a directory-only addition.
