---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A directory `compare` feeds the union `-H` set to every member again

Index: [Known gaps](../known-gaps.md)

**Status (2026-10-12): OPEN, reopened.** Step 2 of the 2026-10-11 landing (each member parses only the headers its exports claim) was reverted. Every member of a directory/package `compare` parses the release's whole `-H` union again, on both sides.

## What was tried

#1554 (`882482c47`) added `workflows/release_member_headers.py`. A header claimed a member when one of the member's exported names appeared in it as an identifier, and each side was assigned on its own. On a real oneDAL release (2026.0.0 to 2026.1.0, four members, castxml, Python 3.12) three of four members stopped being compared:

- `libonedal_thread.so.4` failed in castxml (exit 1). Its header subset was not self-contained: an aggregate header pulled in `algorithm_base_mode.h` without the umbrella header that declares `AlgorithmContainerImpl`.
- `libonedal.so.4` and `libonedal_parameters.so.4` were refused as NOT_COMPARABLE (`scope_fingerprint` mismatch). Their assignments followed each side's exports and header set, and the two trees differ, so each member got different headers on old and new.

The release exit moved from 4 (2271 breaking findings) to 16. The regression is pinned by `test_a_header_naming_one_members_export_on_one_side_keeps_both_comparable` in `tests/test_release_member_headers_integration.py`, which fails on `882482c47` with the same scope mismatch.

## Why it is not a tuning problem

- Export tokens cannot prove self-containment. An umbrella header that supplies a prelude is an includer of the aggregate, not something the aggregate includes, so an include-closure over the claimed set does not find it.
- Per-side assignment is not symmetric. Two sides with different header sets produce different declared surfaces for the same member, and the scope fingerprint then refuses the comparison.
- A subset that happens to parse can lose prelude macros and yield a wrong surface with no error. That is worse than the castxml failure.

## What a retry needs

- Include-graph evidence, both includers and includes, so that a narrowed operand is provably self-contained.
- One assignment shared by both sides, so that a member's declared surface is comparable between old and new.
- A castxml failure on a narrowed operand recorded as an explicit FAILED fact, never a silent fall-back to a partial surface.

Until then the union is the proven-safe operand. The cost is that header work grows with members x headers, and a type in one member's header is parsed by its siblings. The verdict half is still handled: a type that a member's own exports prove unreachable is recorded out of its contract (see the [archive](../archive/known-gaps-closed.md)).
