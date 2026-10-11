---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Scalar and release `compare` still fold the exit code in two places (2026-10-07)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-11 at `46c127111`): OPEN, narrowed.** #1532 moved the release compatibility-axis fold into `workflows/release_exit_fold.py`, which folds each member's scalar-resolver contribution. Two precedence resolvers still exist: `policy/exit_decision.py::resolve_compare_exit_decision` and `policy/exit_decision_precedence.py::resolve_release_exit_decision`, the latter still owning the release-only axes listed below.


`tests/test_compare_cardinality_invariance.py` pins, for cardinality 1..4,
that a release member's findings, `disposition_audit` and verdict equal the
scalar `compare` of the same pair, and that the release exit code is the
worst member's. Both paths now share the per-member primitive
(`workflows/member_compare.compare_member` -> `run_compare_request`), but the
exit fold is still two implementations: the scalar one in
`policy/exit_decision.py::resolve_compare_exit_decision` (driven from
`cli_compare_fold`/`cli_helpers_compare`) and the release one in
`policy/release_exit_decision.py` -> `exit_decision_precedence.resolve_release_exit_decision`.

Axes only the release fold has, which a single fold must keep and which have
no scalar input to exercise them:

- exit 8 for a proven-removed required library, whose precedence depends on
  the scheme (severity mode: above the verdict, coverage and operational
  axes; legacy mode: below any nonzero verdict);
- a member's operational `ERROR` floored to 4 (the scalar path aborts instead,
  with its own codes: 16 not comparable, 5 budget, 7 evidence contract);
- the release-global verdict (bundle and probe-matrix findings) folded with
  `max` into the legacy code;
- ADR-065's incomplete-scope and no-comparison-completed contributions;
- the lockstep-SONAME suppression pass, which needs a BREAKING sibling and is
  inert at N=1.

Also still scalar-only: `cli_compare_helpers.run_compare` resolves its own
inputs (strict suppressions, packs via `resolve_and_apply`, probe matrix,
`--build-info`, force-public allowlist, `stated_contract_mode`) and calls
`compare_snapshots` directly rather than `run_compare_request`. The evidence
fold between resolution and classification (build-source diff, abi3 audit) is
already shared: `workflows/pair_evidence.fold_pair_evidence`. Unifying the
fold means generalizing the release fold so N=1 reduces to the scalar one;
any exit-code difference that exposes at N=1 must be decided before it
changes (ADR-064). Owner: ADR-063/065, lane A stage A2(b).
