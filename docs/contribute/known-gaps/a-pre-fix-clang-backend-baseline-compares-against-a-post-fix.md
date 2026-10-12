---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A pre-fix clang-backend baseline compares against a post-fix candidate as a wave of `FUNC_BECAME_INLINE` — no reliability flag guards `Function.is_inline`

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-12): PARTLY FIXED — see [ADR-079](../adr/079-inline-fact-reliability-and-producer-generation.md).** Header-derived snapshots below SCHEMA_VERSION 53 (the fix's own version, clang and castxml both) are stale for the new `inline` family. The detector declines the pair and the assurance status records it as `inline_facts_reliable`. The persisted marker needs no schema bump. Remaining: a baseline re-saved by a build from before this change already carries the current version and no marker, so it reads as reliable until re-dumped. `Function.is_inline` is still a plain bool (a reviewed deferral in the case (a) baseline). Not closed until that is resolved.

The implicit-inline fix
(`extract/headers/clang/inline_semantics.py`) changed what the clang
header backend *records* for a `constexpr`/in-class/`= default`
declaration, from `False` to the correct `True`. A snapshot dumped with
the clang backend before that fix therefore carries a wrong value, and
`diff_symbols._check_inline_transitions` compares `is_inline` raw on both
sides with no producer or generation gate — so a stored baseline from
before the fix, compared against a freshly-dumped candidate, reports
`FUNC_BECAME_INLINE` (RISK) for every such declaration. The findings are
spurious: nothing about the library changed.

**Bounded, with a conservative load-time remedy.** Both clang and castxml
producers are in scope: the same fix commit changed castxml's out-of-line inline
recovery. A snapshot loaded below v53 can contain stale `is_inline` facts, so the
loader demotes them and the existing detector declines the comparison instead of
reporting a finding. The findings are RISK-class, never breaking, so no gate flips
from pass to fail. This load-time fix needs no schema bump. Only persisting the
marker across load-and-save needs `SCHEMA_VERSION` 59, a migration note and
older-reader handling, and that is blocked on ADR-079 acceptance. It also lands in
`policy/analysis_assurance_degraded_facts.py`'s `consulted_when` map,
whose every-flag-has-a-key invariant is enforced by a `KeyError` rather
than a check, and — if `is_inline` is converted to a `Fact[bool]` at the
same time, which is the `param_kind` precedent — in the storage backfill
rules too. Recorded here rather than attempted under the same review, per
this file's own convention.
