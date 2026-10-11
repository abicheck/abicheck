---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# ADR-071's release assurance fold: what it deliberately does not do

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): POLICY_NOTE.** The entry records deliberate ADR-071 scope exclusions (per-library override, per-member block, fold convergence). Items 4-5 (no exit block on the stored BundleFacts JSON; depth not projected onto the stored/live driver) are residual defects described as open.


ADR-071 gave `assurance.require_complete` real semantics for a
directory/package (release) `compare` and for a stored `BundleFacts` operand,
and retired the four guards that existed only to stay ahead of the missing
semantics. Three adjacent things were in scope to consider and deliberately
left out, each for a stated reason rather than for effort:

1. **No per-library `assurance.require_complete` override.** The setting stays
   project-wide (ADR-071 D4), like `gate.fail_on_removed_library` and the
   `release.*` keys. A per-member override is a real config-surface question
   (where does it live — a `release.libraries.<key>` block? a selector? — and
   how does it interact with the D7 precedence resolver), not a fold detail,
   and nothing in the reported gap asked for one.
2. **No per-member `analysis_assurance` *block* in the release report.** Each
   `libraries[]` entry carries its own `analysis_assurance_status` and the
   fold names the short members and their notes, but the full per-pair block
   (target/TU/export accounting, the five context statuses, layout-unverified
   detectors) is not embedded per member. Doing so would multiply a
   substantial sub-object across every member of every release document — a
   report-schema sizing decision of its own. A member that needs the full
   block is reachable today by comparing that library individually, and the
   fold's notes name which member to compare.
3. **The three orthogonal `0`/`1` release axes still fold in three places.**
   ADR-049's contract-coverage floor, ADR-065 D6's incomplete-scope floor and
   ADR-071's assurance floor now have the same shape — resolve a decision per
   run, `max` it across members, carry it to the exit and the report — and
   each has its own resolver, its own `*_terms` projection and its own
   parameter threaded through `render_release_summary`/
   `_finalize_release_output`/`_exit_compare_release`. That is three near-
   identical threadings, and the `no_growth` baseline bumps ADR-071 needed in
   five files are the visible cost of adding the third. The convergence target
   is the duplication-and-convergence plan's own P0
   `EffectiveGate`/`EffectiveEvaluationConfig` work — one object carrying every
   orthogonal axis's decision, threaded once — not a fourth copy of the
   pattern. **Do not add a fourth `0`/`1` release axis by copying this one;**
   land that convergence first.

4. **The stored-`BundleFacts` operand's JSON carries no `exit` block.** It
   folds the axis, exits on it, and — since a Codex P1 on this PR — publishes
   the canonical top-level `analysis_assurance_exit_contribution` and the fold
   section in its own report and in every `--output-dir` file, so the gate is
   no longer lost to `aggregate` or a deferred gate. What it still lacks is an
   `exit` block of its own, unlike the live release document: a consumer
   reading `exit.code` from this driver's JSON finds nothing, and has to read
   the verdict and the individual axis keys instead.

   An earlier revision of this entry used that missing `exit` block to excuse
   omitting the fold entirely, on the grounds that the section had "nowhere to
   hang". That was wrong about the half that mattered: the gate-bearing key is
   a plain top-level scalar and needs no `exit` block at all, which is exactly
   why the omission was a real gate bypass rather than a cosmetic gap. Giving
   that driver a real `exit` block remains its own change, and the right one —
   it would close several adjacent asymmetries at once.

5. **`--depth binary` is not projected onto a stored-`BundleFacts` OLD side,
   so that operand is stricter than a live one.** `compare_bundle_facts.
   dispatch` threads `depth=` into the **stored/stored** driver
   (`workflows.bundle_stored_pair_compare.compare_stored_bundle_facts_pair`)
   but not into the **stored/live** one
   (`bundle_side_input.compare_release_against_bundle_facts`, which has no
   depth parameter at all). At `--depth binary` it clears NEW's headers while
   the stored OLD snapshot keeps whatever L2/L5 facts its capture recorded, so
   the comparison is asymmetric in a way live-vs-live at the same depth is not.

   Measured on a three-library fixture, same pair both ways: live-vs-live at
   `--depth binary` reports `analysis_assurance: partial` with one note
   (`scope_resolved is False`), while stored-vs-live reports `partial` with
   three (`header context asymmetric: the new side carries no header/API-level
   evidence`, `graph completeness unknown`, `contract_coverage is 'partial'`).

   ADR-071 did not cause the asymmetry — it pre-dates this axis and affects
   *findings* too, not only assurance — but it is what makes the asymmetry
   gate, so the divergence is worth stating plainly: under
   `assurance.require_complete` a stored-OLD release at `--depth binary` can be
   floored where the live equivalent is not. The `partial` is **not** a false
   finding: the evidence really was asymmetric, and `AGENTS.md`'s "weaker
   evidence narrows conclusions" says to report that rather than hide it. What
   is wrong is only that the user asked for binary depth and the stored side
   did not honour it.

   The fix is to give that driver a real `depth` and project both sides to it
   before comparing — which moves findings, not just assurance, and so belongs
   with the stored/live driver's own evidence handling rather than bolted onto
   this axis. Until then, compare a stored OLD side at its captured depth (omit
   `--depth`), or use a live OLD tree when you need `--depth binary` to be
   symmetric. Found by Codex review on PR #1237.

The companion test gap is recorded in `tests/regressions/manifest.py` under
bug class `gate.per_member_axis_fold`: the order-independence and monotonicity
properties are stated and generated for the assurance axis only. The coverage
and scope axes have example-level tests but no property suite of their own, so
a regression in *their* fold would not be caught by it. Generalizing one
property harness over all three axes is the real remaining work there, and it
pairs naturally with the convergence above.
