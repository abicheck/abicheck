# Agent guide: decision principles (full rationale)

> Moved verbatim out of the root `AGENTS.md` (progressive disclosure: the root file is loaded into every agent session, this one only when its pointer fires). The root `AGENTS.md` remains the primary contract.

## Decision-making principles

- **Time estimates are not a factor in technical decisions.** Don't scope,
  simplify, defer, or pick an implementation approach because it's
  "faster" or "quicker to ship" — there is no deadline or velocity
  criterion here. Judge an approach purely on correctness, generality, and
  fit with the codebase's existing architecture. If a thorough fix is the
  right one, do the thorough fix; don't downgrade to a smaller patch on
  time-cost grounds.
- **Fix the cause, not the instance.** When you find a bug or a reported
  problem, don't stop at a patch for the one call site or input that
  triggered it. Trace it to its root cause, and implement a generalized
  fix — one that closes the whole class of failure, not just the observed
  case — plus generalized tests that state the underlying primitive's or
  detector's contract as invariants (property-style tests, per this
  file's own "Primitive-level property tests" guidance below), not only a
  regression test pinned to the original repro — a fixed-example test only
  forecloses the one input it names. If a genuinely general fix isn't
  feasible in one pass, say so explicitly and record the gap (see "Known
  gaps" below) rather than quietly shipping a narrow patch as if it were
  the complete fix.
- **A bug fix's regression test targets the bug *class*, not the one
  reported input.** This sharpens the previous bullet into a concrete,
  checkable requirement for the bug-fix test contract
  (`.github/PULL_REQUEST_TEMPLATE.md`'s "Bug class" / "General invariant"
  rows, enforced by `scripts/check_bugfix_test_contract.py`). A repository
  audit of this codebase's own fix history found a repeated pattern behind
  its worst escapes: the shipped test proved the *reported* input was now
  handled correctly, the class was described only in prose (a PR body, or
  a "Known gaps" entry below), and the next defect was a sibling case the
  same mechanism still got wrong — #699→#721 (a compression window-size
  formula tested against itself, at a toy scale that never reached the
  bug), #753→#759 (a missing registry entry that failed nothing, anywhere),
  #705→#758 (a workflow-injection defense that asserted file *text* instead
  of executing the attack). None of these needed a cleverer reviewer; they
  needed the invariant to be executable and adversarially generated, not a
  fixed-input assertion plus a paragraph explaining the class. Concretely,
  "General invariant" in the PR contract is not answered by prose alone —
  the named regression test must exercise the invariant with inputs beyond
  the one reported (generated/property-based, an exhaustive small-domain
  enumeration, or at minimum several independently-chosen sibling cases),
  against a stated oracle that is not the same formula/helper the
  implementation itself uses. See
  [`docs/contribute/plans/bug-class-regression-testing.md`](../plans/bug-class-regression-testing.md)
  for the full analysis, the named bug classes it identifies, and the
  phased plan closing the specific generalized-test gaps that analysis
  found still open — check there before writing a narrow reproducer for a
  mechanism a class already covers. `tests/regressions/manifest.py` (that
  plan's Phase 1) is the queryable registry: check `BUG_CLASSES`/`get()`
  there first for a matching `BugClass.id` before restating an invariant
  from scratch, and add an entry there — not just prose — when a fix
  closes a genuinely new class.

