# ADR-070: Release Analysis-Assurance Fold — One Axis, Any Cardinality

**Date:** 2026-09-12
**Status:** Accepted — implemented. Defines what `assurance.require_complete`
means for a directory/package (release) `compare`, which until now raised a
usage error (exit 64) for exactly that combination because "the per-library
fan-out has no single `analysis_assurance` result to gate on". It does not:
it has *N* of them, one per compared member, and this ADR decides how they
fold. Extends [064](064-canonical-gate-algorithm-and-exit-decision.md)'s
`ExitDecision` axis set by *wiring* its already-declared
`ANALYSIS_ASSURANCE` axis on the release path (no new axis, no new exit
number); follows the per-member fold shape
[065](065-comparison-scope-selection-and-completeness.md) established for
its orthogonal completeness axis. Owners:
`abicheck/policy/release_assurance.py`,
`abicheck/report/release_assurance.py`,
`abicheck/policy/exit_decision_precedence.py`,
`abicheck/policy/release_exit_decision.py`,
`abicheck/workflows/release_assurance_members.py`,
`abicheck/cli_compare_release_pairwise.py`,
`abicheck/frontends/cli/release_exit.py`,
`abicheck/frontends/cli/release_summary.py`.

## Context

`assurance.require_complete: true` asks one question: *was the evidence this
comparison rests on complete enough to trust the answer?* On a single pair
it is answered by `analysis_assurance.AnalysisAssurance.status` and folded
into the exit code as a `0`/`1` floor
(`analysis_assurance_exit_contribution`, `fold_analysis_assurance_exit`) —
orthogonal to the compatibility verdict, raising a clean `0` to `1` and
never lowering a real `2`/`4`.

On a directory/package operand the setting was rejected outright:

```
assurance.require_complete is not supported for directory/package (release)
comparisons yet (P0.4): the per-library fan-out has no single
analysis_assurance result to gate on.
```

That rejection chain propagated outward — `compare_bundle_facts_rejections`
mirrored it for a stored-`BundleFacts` operand,
`buildsource/project_targets.py` rejected `analysis.assurance: complete` on
a `kind: bundle` check at run-plan-generation time so the Action would never
forward it into the usage error, and `actions/check-target`'s
`analysis-assurance-complete` input `_fail`ed for a bundle. Four guards, all
correct given the semantics, all blocking the same missing semantics.

The premise in the error text is the part that was wrong. The fan-out does
not lack an assurance result; it has one per member, because each member is
compared through the same Tier-2 `service.run_compare` chokepoint a scalar
`compare` uses and each produces its own `DiffResult.analysis_assurance`.
The open question was never *where to get* the fact — it was *how N facts
become one gate*.

## Decision

**D1 — One axis, `max`-folded over members. Not a new orthogonal axis.**

The release-level assurance contribution is

```
max(analysis_assurance_exit_contribution(member) for member in members)
```

folded into the *existing* `ExitReason.ANALYSIS_ASSURANCE` axis and the
*existing* `ExitDecision.analysis_assurance_contribution` field. No new
exit number, no new reason, no second assurance field.

Three reasons, in order of force:

1. **`AGENTS.md`'s "One model, any cardinality".** `max` over a singleton is
   the identity, so a one-member package exits and reports exactly as the
   scalar path does for the same pair. A separate release-only axis with its
   own number could not satisfy that — the same input would produce a
   different exit code and a different report field purely because the
   operand was spelled as a directory.
2. **A second axis would make the first one lie.** `ExitDecision`'s
   `analysis_assurance_contribution` is already serialized on every report
   (ADR-064 stage 1b, report schema 2.47/1.22) and `ExitReason`'s own
   docstring exists to stop an axis from being decided somewhere the
   `reasons` list cannot explain. A consumer reading
   `analysis_assurance_contribution` on a release report that was floored by
   a *different* assurance field would read `0` for a run the assurance axis
   actually gated. That is precisely the trap that field was introduced to
   close.
3. **There is no release-level evidence to assess.** Assurance is a property
   of a comparison's evidence — both sides' header/DWARF/L3/L5 context for
   *one pair*. A release has no evidence of its own beyond its members'; a
   "release assurance" that is not a fold of member assurance would have to
   invent a fact.

**D2 — `max`, not `any`-as-all-or-nothing, and never `min`.** A member-level
incomplete analysis is not maskable by a complete sibling. This is the same
monotonic direction ADR-049 Phase 7's coverage floor and ADR-065 D6's scope
floor take, and the same `max()` the release fan-out already applies to
aggregate `contract_coverage_exit_contribution` across libraries. Adding a
member can only raise the release's contribution, never lower it.

**D3 — Distinct from ADR-065's completeness axis; both apply.** They answer
different questions and a release can be either without the other:

| | question | evidence |
|---|---|---|
| ADR-065 scope | was every *selected member* compared at all? | inventory/acquisition |
| This ADR | for the comparisons that *ran*, was the evidence complete? | per-pair `AnalysisAssurance` |

Every member compared, one member's DWARF missing ⇒ scope `complete`,
assurance `partial`. One member never supplied, the rest fully analysed ⇒
scope `incomplete`, assurance `complete` over what ran. Both are `0`/`1`,
both fold with `max` in every non-dominant branch of
`resolve_release_exit_decision`, and both are named independently in
`reasons` on a tie. Reusing ADR-065's axis for this would have collapsed two
independent facts into one number.

**D4 — No new policy setting.** `assurance.require_complete` is already the
opt-in, and it is already a project-wide `.abicheck.yml` key rather than a
per-library one — so it applies uniformly to every member, the same way
`gate.fail_on_removed_library` and `release.*` already do. Default `false`
⇒ every member contributes `0` ⇒ **every pre-existing release invocation's
exit code, report bytes, and stderr are unchanged**. That is what makes this
purely additive despite touching the exit fold.

**D5 — The aggregate status is the worst member status, and it agrees with
the contribution by construction.** The release's reported
`analysis_assurance_status` is `complete` iff *every* compared member's is
`complete`; otherwise it is the worst member status under the stated order

```
complete < not_requested < partial < failed < not_comparable
```

`not_requested` ranks below `complete` because the exit fold already treats
any status other than `complete` as a shortfall — a release whose aggregate
status read `complete` while its contribution was `1` would be a report that
contradicts its own exit code. `policy/release_assurance.py` owns both
answers together (one `ReleaseAssuranceDecision`) for the same reason
ADR-065's `resolve_scope_decision` does: so the status a reader sees and the
number that gated them cannot be derived twice.

**D6 — Report.** Under `assurance.require_complete` only (mirroring how the
release report gains its coverage fields only under `--contract`), the
release JSON summary gains `analysis_assurance` — the aggregate status, the
`0`/`1` contribution, the count of incomplete members, and the per-member
`{library, status, notes}` rows for the members that fell short — and each
per-library entry gains `analysis_assurance_status` /
`analysis_assurance_exit_contribution`. Naming the members is deliberate:
"2 of 9 members incomplete" is not actionable, `libfoo.so (partial: no DWARF
on the new side)` is. A one-line stderr notice mirrors
`incomplete_scope_diagnostic`'s wording family so a Markdown/JUnit consumer
with no JSON still learns why the run was floored. `0` fields are emitted,
not omitted, once the setting is on.

**D7 — Exit code.** `1`, folded with `max` — identical to the scalar path.
It raises a clean `0` to `1`, never lowers a real `2`/`4`, is preserved but
never decides under a dominant `16` (`not_comparable`), `8` (proven removed
required library), or `7` (evidence contract), and it never rewrites a
finding's compatibility decision or gate contribution. A release
`compare`'s exit-code matrix therefore gains no new number.

**D8 — The stored-`BundleFacts` operand takes the same fold.** Its driver
(`bundle_side_input.compare_release_against_bundle_facts`) returns a
`BundleDiffResult` whose `per_library` is a list of real `DiffResult`s, each
carrying its own `AnalysisAssurance` — the same member facts, reached a
different way. It folds through the identical function, so the two operand
shapes cannot diverge on what `require_complete` *means*.

What they can still differ on is the *evidence* each one collects, and this
axis now makes one such pre-existing difference visible as an exit code. The
stored-OLD/live-NEW branch does not thread `--depth` into its driver (the
stored/stored branch does), so `--depth binary` clears NEW's headers while the
stored OLD snapshot keeps whatever L2/L5 facts its capture recorded. The result
is an `asymmetric` header/graph context that a live-vs-live comparison at the
same depth does not have — measured, not inferred: on the same three-library
fixture, live-vs-live at `--depth binary` reports `partial` only for
`scope_resolved is False`, while stored-vs-live reports `partial` for
`header context asymmetric`, `graph completeness unknown` and
`contract_coverage is 'partial'` as well. That `partial` is a *true* statement
about that run's evidence — the asymmetry is real — so this axis is not
manufacturing a finding; it is stricter on the stored path for a reason the
user did not ask for. Projecting a stored snapshot down to a requested depth is
its own change to that driver's evidence handling (it would move findings, not
just assurance), so it is **not** attempted here and is recorded in
[known-gaps.md](../known-gaps.md) with that reproduction. Read D8 as a claim
about the fold, not about evidence parity between operand shapes.

**D9 — Every document a run publishes carries the axis, and the notice is
formatted where the real code is known.** Three places build a
release `ExitDecision` — the process exit (`release_exit.py`), the primary
report (`cli_compare_release_helpers._format_release_json`), and the
`--output-dir` sidecar (`release_summary._write_release_summary_file`) — and
all three fold it. The sidecar is the one worth naming: it resolves its own
`exit` block through the same resolver, so threading the axis into only the
first two would have published a `summary.json` whose
`exit.code`/`analysis_assurance_contribution` disagreed with the process's
real status — the same self-contradicting report D5 exists to prevent, one
level out. Correspondingly, `_exit_compare_release` takes the whole resolved
decision rather than a bare contribution and formats the stderr notice
itself: that wording turns on the compatibility axis's own exit code
("floored to 1" versus "below the compatibility axis's own exit 4, which
stands"), and the decision it resolves is the only place that number is
known. A caller passing its own guess gets it wrong — `compare
--bundle-facts` has no severity code to guess from and would have announced a
floor beside a real ABI break — and the base is every *other* axis, not the
compatibility one alone: under a dominant `16`/`8`/`7` the compatibility
contribution can be `0` while the exit was decided elsewhere, so
`ExitDecision.exit_without_analysis_assurance()` owns that number and reads
every `*_contribution` field off the dataclass, so an axis added later is
included without a second list to keep in sync.

"Publishes" means every document, not only the exit: the canonical **top-level**
`analysis_assurance_exit_contribution` (report schema 2.40, the sibling of
`contract_coverage_exit_contribution`) on the release document and on
`summary.json`, because that key — not the `exit` block — is what
`workflows.aggregate.gate._analysis_assurance_exit` and the Action's
`gate_mode: deferred` path read; each per-library `{library}.json` under
`--output-dir`, written with the setting threaded through so it cannot report
`exit.code: 0` for a member that floored the run; and the
`effective_config_fields`/digest receipt, which must name
`gate.require_complete_analysis` or a gated run is indistinguishable from an
ungated one. Each of these was a real loss found in review, and all four share
one shape — an orthogonal axis reaching some consumers and not others — which
is why they are stated here as one rule rather than four fixes.

`run_outcome.gate` deliberately stays `none` for a run this axis floors, and
that is **not** an omission: `gate` is a *compatibility* category
(`policy_gate_decision_for_exit_code`), an orthogonal floor is by definition not
one, and a scalar `compare` floored by the same axis reports `gate: none` with
`compatibility: COMPATIBLE` too. Making the release path differ here would
break D1's cardinality agreement rather than close a gap.

## Consequences

The four guards are removed, in dependency order (semantics first, guards
last): `cli_compare_options._reject_set_input_flags`,
`compare_bundle_facts_rejections.reject_unsupported_options`,
`project_targets`'s `kind: bundle` run-plan rejection, and
`actions/check-target`'s `_fail`. A single-bundle L2 analysis with
`assurance.require_complete: true` now runs end to end.

What this ADR does **not** do: it adds no per-library
`assurance.require_complete` override (the setting stays project-wide, D4),
and it does not give the release report a per-member `analysis_assurance`
*block* — only the status, contribution, and notes. A full per-member
assurance block is a report-schema question of its own size; the members
that need one are reachable today by comparing that library individually.
