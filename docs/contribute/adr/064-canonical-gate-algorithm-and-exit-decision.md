# ADR-064: One Canonical Gate Algorithm and Exit-Decision Precedence

**Date:** 2026-08-30
**Status:** Accepted — partially implemented. `ExitDecision`'s three-axis
core (compatibility gate, contract coverage, analysis assurance) shipped
additively as PR G1 (#789, `abicheck/policy/exit_decision.py`) before this
ADR was written. Of this ADR's own two-stage plan (see "Staged landing,
additive first" below), **stage 1a** landed complete:
`resolve_scan_exit_decision`/`resolve_release_exit_decision`
(`abicheck/policy/exit_decision_precedence.py`) are pure functions
reproducing the remaining axes' full precedence (evidence-contract error,
budget overflow, not-comparable, the mode-dependent
removed-required-library rank, and a release's independent operational-error
axis), unit-tested against the real code they model. **Stage 1b landed
partially:** `ExitDecision.to_dict` now serializes all five ADR-064 fields
(report schema 2.47/1.22), `scan`'s `NOT_COMPARABLE` outcome persists a real
`diff.exit` block, and the release fan-out's JSON summary gains an `exit`
block reproducing `_exit_compare_release`'s own precedence — verified,
never assumed, to always agree numerically with that (deliberately
untouched) function's real, independently-tested output (see "Stage 1b,
further split" below for exactly what landed and why the numbers can never
diverge). **Still not implemented:** persisting a decision for `scan`'s
`_BudgetOverflow`/`_EvidenceContractError` abort points, which raise
*before* any report exists today and therefore need a genuine new design
decision this stage deliberately did not make; the release fan-out's
`GateOptions` unification; and **stage 2**, the `--exit-code-scheme`
removal itself. See
[cli-cleanup-phase-two.md](../plans/cli-cleanup-phase-two.md)'s "PR 4 — one
gate algorithm" section, which this ADR formalizes rather than restates.
**Decision maker:** Nikolay Petrov

## Context

`--exit-code-scheme auto|legacy|severity` (`compare`, `scan --against`) is
not a spelling choice between equivalent renderings of the same result — it
selects between two different gate *algorithms* that can disagree on the
same comparison: a compatibility-based one, deriving `0/2/4` straight from
the `NO_CHANGE`/`COMPATIBLE`/`RISK` vs. `API_BREAK` vs. `BREAKING` verdict,
and a severity-based one, deriving `0/1/2/4` from which severity category
(addition/quality, potential-breaking, ABI-breaking) actually carries an
error-level finding under the configured policy (see
[cli-cleanup-phase-two.md](../plans/cli-cleanup-phase-two.md)'s "PR 4" table
for the full side-by-side). A compatible addition can block CI under one
severity policy can demote a real ABI break to `0`. `auto` already picks
severity-based whenever a severity policy is actually configured and falls
back to compatibility-based otherwise — the manual `legacy`/`severity`
spellings exist only to *override* that inference, and CLI cleanup phase
two's broader review found no first-party caller, doc, or Action recipe
that ever needs to. Deleting the selector without a documented successor
algorithm would silently change users' CI outcomes on the next release,
which is why the plan gates the removal behind its own ADR rather than
folding it into a routine mechanical cleanup PR.

Two more forces make this larger than a two-value enum:

1. **The axes multiplied since the flag was designed.** Contract coverage
   (ADR-049 Phase 7) and analysis assurance (P0.4) each add their own
   orthogonal `1`, folded with `max()`. `scan` adds an evidence-contract-error
   floor (`1`) and a budget-overflow floor (`5`), and neither precedes the
   gate at one fixed point — the evidence-contract-error check
   (`scan_engine.py`'s `_check_scan_evidence_contract`) always precedes the
   baseline compare (and therefore its severity computation) entirely, but
   `--budget` is deadline-guarded at *two* separate points: candidate-
   snapshot collection, which precedes the evidence-contract check too
   (correcting a fresh review finding against an earlier draft of this
   section, which had claimed budget overflow always precedes the gate the
   same way evidence-contract error does), and the baseline compare's own
   deadline plus the final, unconditional post-comparison check — both of
   which run *after* severity computation, discarding whatever it decided
   rather than preceding it. See "Budget exceeded is not one precedence
   slot" under Decision below for the exact line references. A release
   comparison adds a removed-required-library code (`8`) whose precedence
   relative to the gate is **mode-dependent**, not a fixed rank
   (`docs/reference/exit-codes.md`, `abicheck compare` (multi-library)
   section). `NOT_COMPARABLE` (`16` for native `compare`, `6` for
   `scan --against`, `9` for `compat check`) dominates the release's
   gate/removed-library pair in both modes, but does **not** dominate
   `scan`'s own budget overflow.
2. **A flat `max()` over "the number" cannot explain a tie.** A caller
   reading a bare exit `1` cannot tell whether it came from an error-level
   addition, an incomplete contract-coverage domain, or an incomplete
   analysis-assurance requirement without re-deriving the answer from
   several separately-read report fields.

PR G1 already landed the additive, lower-risk half of the fix: a canonical
`ExitDecision`/`resolve_exit_decision` (`abicheck/policy/exit_decision.py`)
that wraps *today's* three-axis fold (compatibility gate or scoped gate,
contract coverage, analysis assurance, plus `scan`'s crosscheck-promotion
axis) into one explainable object, bit-for-bit preserving every existing
call site's returned code. Its own module docstring is explicit that the
three remaining axes — `not_comparable`, `scan`'s budget/evidence-contract
floors, and the release's mode-dependent removed-library rank — are "real,
further work for PR G2, not attempted here — extending this module before
that design is settled would risk exactly the kind of partially-verified,
cross-cutting change this codebase's own conventions warn against." This
ADR is that settled design.

## Decision

**Remove the manual algorithm selector. Keep both gate algorithms. Make
today's `auto` inference the only behaviour**, expressed as one canonical
precedence order every command's `ExitDecision` resolution reproduces, with
the axes that don't apply to a given command simply absent rather than
special-cased:

```text
usage/config error              (outside the report entirely — 64 everywhere)
scan budget exceeded            (scan only, exit 5 — ONLY the candidate-
  (candidate-collection stage)   snapshot-collection deadline, scan_engine.py
                                 :1180-1221; this specific stage runs BEFORE
                                 the evidence-contract check below, so an
                                 overflow here preempts it — see "Budget
                                 exceeded is not one precedence slot" below)
scan evidence-contract error    (scan only, exit 1 — ADR-037 D5)
scan budget exceeded            (scan only, exit 5 — the baseline-compare
  (later stages)                 deadline or the final, unconditional check;
                                 both run only once the evidence-contract
                                 check above has already passed, and this
                                 axis dominates not-comparable below when
                                 both would apply in the same run)
not comparable                  (dominates the removed-library/gate pair
                                 below, but never dominates either budget
                                 slot above — ADR-050 D2)
removed required library      ─┐ mode-dependent rank, not a fixed slot — see
ABI / API / policy gate        ─┘ "Removed-required-library is mode-dependent"
coverage & assurance floors     (max-folded on top; never lowers the above)
clean
```

**Budget exceeded is not one precedence slot — it is two, and a resolver
that treats it as one gets the wrong answer for a real, reachable case**
(Codex review, fresh evidence, against the real line order in
`scan_engine.py`). `run_scan_core`'s deadline-guarded candidate-snapshot
collection (`scan_engine.py:1180-1221`) raises `_BudgetOverflow` — and,
critically, this runs *before* `_check_scan_evidence_contract`
(`scan_engine.py:1229`) is even called. A pinned deep scan that both lacks
the source evidence its depth requires *and* overruns the budget while
still building the candidate snapshot never reaches the evidence-contract
check at all — the real outcome is exit `5`, not exit `1`. Only the *later*
budget checks (the baseline compare's own deadline scope, and the final,
unconditional `_check_scan_budget` call after the comparison completes)
run after the evidence-contract check has already had its chance to fire,
and it is only against *those* that evidence-contract error legitimately
wins. (`_EvidenceContractError` also has a second, earlier raise site —
`scan_engine.py:852`'s abi3 precondition check inside `_run_abi3_audit`,
which still runs after the candidate-collection budget guard — mapping to
the same `evidence_contract_error=True` input as the `:1229` site,
consistent with this precedence; named here so the two raise sites are not
mistaken for one.) `abicheck/policy/exit_decision_precedence.py`'s
`resolve_scan_exit_decision` models this as two separate boolean inputs
(`budget_overflow_before_evidence_check` and `budget_overflow`) rather
than one, precisely so a future caller cannot collapse them back into a
single, incorrectly-ordered axis.

**`auto`'s existing inference rule is the policy, restated, not changed:**
a severity preset, an explicit `--severity-*` flag, a `.abicheck.yml`
`severity:` block, or a `kind: gate` pack in effect selects the
severity-based gate; otherwise the compatibility-verdict-based gate applies.
The user configures *policy* (do they have a severity map or not); they no
longer choose an *implementation* of how policy is scored.

### Removed-required-library is mode-dependent, not a fixed precedence slot

Today's contract (`docs/reference/exit-codes.md`'s release table,
`tests/test_compare_release.py::test_removed_and_breaking_exits_4_not_8`)
already encodes a real behavioural switch that `ExitDecision`'s resolver
must reproduce exactly, not collapse into one row:

- **Legacy scheme** (the *resolved* scheme is compatibility-based for this
  run — either no severity map is in effect, or, until stage 2 removes it,
  `--exit-code-scheme legacy` was explicitly forced despite one): an
  ABI/API break or an operational `ERROR` wins; removed-library (`8`) is
  checked only when neither applies.
- **Severity-aware scheme** (the *resolved* scheme is severity-based —
  a severity map is in effect and nothing forced the other way):
  removed-library (`8`) takes precedence over the aggregated `0/1/2/4`.

An earlier draft of the plan this ADR formalizes gave removed-library a
fixed rank; a review round against `scan_engine.py`/`cli_compare_release*.py`
corrected it. Encoding the *wrong* fixed rank here would silently flip CI
outcomes for every release comparison that removes a library while also
carrying a lower-severity break — exactly the class of change this ADR
exists to make an explicit, reviewed decision about rather than a side
effect of a refactor.

### Numbers are not unified across commands — only the precedence is

`ExitDecision` unifies *which reason wins*, never the numeric code a
command emits for that reason. Every command keeps its own, already-documented
exit-code scheme: `NOT_COMPARABLE` is `16` for native `compare`, `6` for
`scan --against`, `9` for `compat check` — three different numbers for the
identical reason today, and this ADR does not renumber any of them. A
resolver that emitted one global number per reason would silently break
every script and CI Action that recognises `scan`'s `6`, while this ADR is
scoped to removing the *algorithm selector*, not to a command-numbering
migration. Concretely: `resolve_exit_decision`/its PR G2 extension answers
"which axis determined this outcome" as an `ExitReason`; each command's own,
already-existing code table maps that reason to its own number.
`docs/reference/exit-codes.md` becomes a rendering of this resolver plus
each command's mapping, not a second, independently-hand-kept table.

### `GateOptions` — the release fan-out's own prerequisite rewrite

The directory/package release fan-out still threads six raw
preset/category/scheme strings through four functions
(`_resolve_release_severity_config`, `_compute_release_severity_exit_code`,
`_fold_release_global_severity`, and the per-library JSON write) instead of
building one typed object the way `compare`/`scan` already share via
`ResolvedCompareConfig` (CLI cleanup phase two's PR B, finalized
2026-08-28). Folding that rewrite into PR G2 — rather than attempting it as
a standalone PR B follow-up — was a deliberate scope decision recorded in
the plan doc: it touches the identical exit-code-computation logic this
ADR's `ExitDecision` unification is already rewriting, and building it
ahead of this ADR risked colliding with a design that did not yet exist.

### Staged landing, additive first

Consistent with PR G1's own precedent and this codebase's "fix the cause,
generalize the test, land additively where possible" convention, PR G2
lands in two stages rather than one atomic change:

1. **Additive**, itself two independently-landable sub-steps — no flag is
   removed in either:
   1. **1a — pure resolvers.** Extend `ExitDecision`/`resolve_exit_decision`
      to compute the remaining axes (evidence-contract error, budget
      overflow, not-comparable, removed-required-library's mode-dependent
      rank, and a release's independent operational-error axis) as pure,
      independently unit-tested logic — verified against the real code
      they reproduce, but not yet called from it.
   2. **1b — wiring.** Call those resolvers from `scan_engine.py`/
      `cli_compare_release_helpers.py` and persist the result into
      `scan`'s and the release fan-out's own report `exit` block for
      explanatory purposes — every existing call site's *actually
      returned* exit code stays bit-for-bit unchanged, exactly as PR G1
      did for the first three axes. **Landed partially:**
      `ExitDecision.to_dict` now serializes all five ADR-064 fields
      (report schema 2.47/1.22, both `compare` and `scan`); `scan`'s
      `NOT_COMPARABLE` outcome (`ProfileMismatchError`/`ScopeMismatchError`)
      persists a real `diff.exit` block via `resolve_scan_exit_decision`,
      since that outcome already builds and emits a report today; and the
      release fan-out's JSON summary gains an unconditional `exit` block
      (`resolve_release_exit_decision_for_report`,
      `abicheck/policy/exit_decision_precedence.py`) reproducing
      `_exit_compare_release`'s own precedence, including the legacy-scheme
      aggregation gap this section used to describe as open (a
      `_compute_release_legacy_exit_code` helper, the "worst verdict among
      non-`ERROR`/non-`not_comparable` libraries" this paragraph called
      for) — but landed as a **separate, report-only** resolver rather than
      a rewrite of `_exit_compare_release` itself, since that function's
      exact signature and numeric outputs are pinned directly by
      `tests/test_exit_code_integrity.py`, which CI gates depend on;
      rewriting it in place to delegate to the new resolver risked exactly
      the kind of silent exit-code regression this ADR exists to prevent
      for a function with that much test weight resting on its current
      shape. The two are proven, not merely assumed, to always agree
      numerically (`tests/test_exit_code_integrity.py`'s
      `TestReleaseExitDecisionForReportAgreesWithRealExit` — every
      legacy-scheme code the new resolver can produce caps at the same `4`
      the real function's own operational-`"ERROR"` floor does, so the two
      cannot diverge on `code`, only on which `reasons`/contributions a
      report reader sees). **Still not landed:** `scan`'s
      `_BudgetOverflow`/`_EvidenceContractError` abort points
      (`scan_engine.py`) raise *before* any report is ever constructed
      today (unlike `NOT_COMPARABLE`, which already builds one) — a real
      design decision (should the CLI construct a minimal report at those
      abort points at all, and if so, what does it contain?) that this
      stage deliberately declined to make as a side effect of wiring
      already-designed resolvers. Also still open: the release fan-out's
      `GateOptions` unification and a full cross-front-end parity pass
      (typed API, Action).
2. **Atomic.** Once the report block agrees with today's real behaviour for
   every axis and every mode (verified by the axis-separated tests this ADR
   requires below), remove `--exit-code-scheme` from `compare` and `scan`,
   correct `action.yml`'s prose (there is no `exit-code-scheme` Action
   input to remove — only its `verdict` output description names the flag
   today), remove or replace `.abicheck.yml`'s `exit_code_scheme` key, and
   change `pack_application.py` to read a resolved `gate.exit_code_scheme`
   pack field as *policy* (does this pack imply a severity map or not)
   rather than as an *algorithm selector*. Update CLI, typed Python API,
   Action, and `aggregate` parity tests together in this stage, per the
   plan's own "Merge criteria for every removal PR" checklist.

Splitting the stages is what lets a bisect over a red CI job land
unambiguously on the atomic stage rather than on the (behaviourally inert)
additive one, and lets the additive stage's tests double as the removal
stage's regression baseline instead of being written under time pressure
alongside the flag deletion itself.

## Consequences

- `ExitReason` gains members for the three new axes (naming to match
  `abicheck/scan_engine.py`'s existing verdict strings —
  `EVIDENCE_CONTRACT_ERROR`, a budget-overflow reason, `NOT_COMPARABLE`), a
  `removed_required_library` reason whose precedence the resolver computes
  according to the mode-dependent rule above, not a static ordering table,
  and an `operational_error` reason for a release's own independent,
  tie-foldable axis (a library's dump/extract/compare failure, distinct
  from a real compatibility-gate finding even when both happen to tie).
- `docs/reference/exit-codes.md` is updated, once the atomic stage lands, to
  state precedence via a link to this ADR's table instead of the prose
  spread across the `compare` (multi-library), `scan`, and `scan --against`
  sections today.
- No `ChangeKind`, schema-version, or report-field removal ships with the
  additive stage; the atomic stage bumps whichever report schema versions
  gain or lose the `exit_code_scheme`-related fields, per the plan's
  "Machine contracts" merge criterion.
- `--exit-code-scheme legacy`/`severity` callers (CLI, `.abicheck.yml`,
  packs) lose the ability to force an algorithm that disagrees with their
  own configured policy. Per this plan's stated non-goals, no deprecation
  alias or transition window ships — the old spelling errors with
  `No such option`, exit `64`, matching every other removal in this
  cleanup.
- The release fan-out gains the `exit`/reasons block parity with `compare`
  via **stage 1b** (`resolve_release_exit_decision_for_report`,
  `abicheck/policy/exit_decision_precedence.py`, per "Staged landing"
  above) — not the atomic stage; that block is additive and needs no
  algorithm-selector decision to exist. Only the
  release fan-out's *internal* severity/exit-code representation changing
  shape (raw strings → `GateOptions`-shaped object) is atomic-stage work,
  since it is the same rewrite that removes `--exit-code-scheme`. Neither
  step changes the release's externally observable exit codes.

## Cross-references

- [cli-cleanup-phase-two.md](../plans/cli-cleanup-phase-two.md) — "PR 4 —
  one gate algorithm (`--exit-code-scheme` removal)" is this ADR's source
  material; the plan's "Ordering" table tracks PR G1 (done, #789) and PR G2
  (this ADR — stage 1a landed, stage 1b/stage 2 not yet implemented) as a
  pair.
- [ADR-049](049-contract-relevance-and-compatibility-configuration.md) —
  contract-coverage's own orthogonal exit contribution, folded on top of
  this ADR's precedence, never lowering it.
- [ADR-050](050-comparability-contract-and-multi-tu-manifest.md) — D2, the
  `NOT_COMPARABLE` contract this ADR's precedence table defers to.
- [ADR-037](037-cli-interface-contract.md) — D5, `scan`'s evidence-contract
  check, the source of the evidence-contract-error axis.
- `abicheck/policy/exit_decision.py` — PR G1's already-implemented
  three-axis core this ADR extends.
- `docs/reference/exit-codes.md` — the per-command number tables this ADR's
  precedence resolver must reproduce exactly, not renumber.
