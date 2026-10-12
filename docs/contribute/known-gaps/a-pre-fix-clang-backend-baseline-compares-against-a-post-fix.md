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

**Status (re-verified 2026-10-12): OPEN — design in [ADR-079](../adr/079-inline-fact-reliability-and-producer-generation.md), no code change.** `_check_inline_transitions` declines through the existing `Fact` path when `is_inline` is absent, so nothing is missing there. What is missing is a staleness signal for a *present*, wrong `is_inline` fact. The fix commit `5ba6a5c84` landed at `SCHEMA_VERSION` 52, the same version as the pre-fix snapshots, so the boundary is v52/v53: v<53 is treated as possibly pre-fix. The fix also touched castxml out-of-line inline recovery, so both producers are in scope. A persisted marker needs a `SCHEMA_VERSION` 59 bump, which is blocked on ADR-079's acceptance.

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

**Bounded, with a one-step remedy.** castxml is the default header
backend and always recorded these as inline, so only a baseline captured
under the opt-in `--ast-frontend clang` / `ABICHECK_AST_FRONTEND=clang` is
affected, and re-dumping that baseline clears it permanently. The findings
are RISK-class, never breaking, so no gate flips from pass to fail.

**The proper fix is the established one, and it is a schema change.** This
codebase already has the mechanism for exactly this situation — a fact
whose stored value is wrong in snapshots from an earlier generation:
`clang_restrict_facts_reliable`, `clang_va_list_facts_reliable`,
`param_kind_facts_reliable` and their siblings
(`model/snapshot_reliability.py`), each set `False` at load time for a
snapshot below a `_MIN_SCHEMA_VERSION_FOR_*` threshold from the affected
producer, and read by the consuming detector, which then declines rather
than fabricating a finding. Adding `clang_inline_facts_reliable` the same
way is the complete fix. It is **not** folded into the fix PR because it
cannot work without bumping `SCHEMA_VERSION` (a pre-fix snapshot is v52,
and so is a post-fix one — there is otherwise no way to tell them apart),
and this file's own contract is that a schema change gets its own ADR and
migration rather than riding along with a behavior fix. It also lands in
`policy/analysis_assurance_degraded_facts.py`'s `consulted_when` map,
whose every-flag-has-a-key invariant is enforced by a `KeyError` rather
than a check, and — if `is_inline` is converted to a `Fact[bool]` at the
same time, which is the `param_kind` precedent — in the storage backfill
rules too. Recorded here rather than attempted under the same review, per
this file's own convention.
