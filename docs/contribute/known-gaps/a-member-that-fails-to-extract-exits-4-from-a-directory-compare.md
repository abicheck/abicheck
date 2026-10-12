---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A member that fails to extract exits `4` from a directory `compare` but `1` from a single-pair one

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** The entry records a maintainer ruling 'keep both' (exit 1 scalar vs 4 release for an extraction failure); both are documented in docs/reference/exit-codes.md, and changing either needs an ADR-064 amendment.


**Recorded, not fixed (lane A, A2(b), 2026-10-09; maintainer ruling: keep both).**
A single-pair `compare` whose operand cannot be dumped (for example a
truncated ELF) aborts with a CLI error and exits `1`. The same pair as the only
member of a directory/package release produces a member `ERROR` entry and the
release exits `4` with `exit.reasons: ["operational_error"]`. Both behaviours are
documented in `docs/reference/exit-codes.md`. Reproduce: put the same corrupt
`libm0.so` in `old/` and `new/`, then run `compare old/libm0.so new/libm0.so`
(exit `1`) and `compare old new` (exit `4`).

This is the one axis where a release of one member and a scalar `compare` of that
member disagree. The release fold takes each member's compatibility
contribution from the scalar resolver (`resolve_compare_exit_decision`, stamped
by `workflows.member_compare.member_exit_decision`), and
`tests/test_compare_cardinality_invariance.py` pins the agreement on every axis
where a comparison completed. A member that never compared has no scalar
decision to fold, so it falls to the release's own operational-error axis.
Making the two agree means changing a public exit code in one direction: either
a release exits `1` for a failed member, or a scalar dump failure exits `4` and
reads as an ABI break. Either change needs an ADR-064 amendment.
