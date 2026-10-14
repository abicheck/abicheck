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

**Status (2026-10-12): PARTIAL, narrowed further.** The evidence-contract precedence (ADR-064's exit 7) is now decided in one place for both paths: `policy/exit_decision_precedence.py::resolve_evidence_over_ordinary`, called by `resolve_scan_exit_decision` (native `compare` through `resolve_compare_exit_decision_with_abort_axes`) and by the release resolver's evidence branch. The shared non-dominant fold was already one function, `policy/exit_decision.py::resolve_exit_decision`, and both paths call it. `tests/test_exit_precedence_matrix.py` pins the exit code and reasons of both resolvers over their full per-axis matrices (scalar: verdict x evidence x budget; release: not-comparable x scheme x verdict/severity x removed x operational x coverage x evidence), plus cardinality-one agreement on the axes both can express. The two resolvers remain separate because the release-only axes below have no scalar input. No exit code changed; the one visible difference is that a scalar `compare` whose `DiffResult` carries `evidence_contract_error` now keeps its ordinary contributions in the `exit` block (unreachable from the CLI today). #1532 moved the release compatibility-axis fold into `workflows/release_exit_fold.py`, which folds each member's scalar-resolver contribution.

**Stays separate, release-only.** `resolve_release_exit_decision` keeps its own ladder for these, and this is deliberate, not drift:
- `not_comparable` (16) dominates everything, and the scalar path aborts with `sys.exit` before any resolver runs.
- A proven removed required library (8) ranks mode-dependently: above the fold under severity, below a nonzero verdict/operational fold under legacy, and above evidence (7) only when active.
- The release-global verdict folded with `max`, the lockstep-SONAME suppression pass, and the operational `ERROR` floor.
- `budget_overflow` (5) is scalar-only (no release budget axis) and stays in `resolve_scan_exit_decision`.

**Still open.** Unifying the two ladders means generalizing the release fold so N=1 reduces to the scalar one. Any exit-code difference that exposes at N=1 must be decided before it changes (ADR-064). `cli_compare_helpers.run_compare` also still resolves its own inputs and calls `compare_snapshots` directly rather than `run_compare_request`.


`tests/test_compare_cardinality_invariance.py` pins, for cardinality 1..4,
that a release member's findings, `disposition_audit` and verdict equal the
scalar `compare` of the same pair, and that the release exit code is the
worst member's. Both paths now share the per-member primitive
(`workflows/member_compare.compare_member` -> `run_compare_request`), but the
exit fold is still two implementations: the scalar one in
`policy/exit_decision.py::resolve_compare_exit_decision` (driven from
`cli_compare_fold`) and the release one in
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
