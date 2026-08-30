# ADR-064: One Canonical Gate Algorithm and Exit-Decision Precedence

**Date:** 2026-08-30
**Status:** Accepted — partially implemented. `ExitDecision`'s three-axis
core (compatibility gate, contract coverage, analysis assurance) shipped
additively as PR G1 (#789, `abicheck/policy/exit_decision.py`) before this
ADR was written. This ADR's own additive stage has also landed:
`resolve_scan_exit_decision`/`resolve_release_exit_decision` are pure
functions reproducing the remaining axes' full precedence (evidence-contract
error, budget overflow, not-comparable, the mode-dependent
removed-required-library rank), unit-tested against the real code they
model. Not yet implemented: wiring those two resolvers into any command's
persisted report or actually-returned exit code (a real report-schema
version bump), the release fan-out's `GateOptions` unification, and the
`--exit-code-scheme` removal itself (PR G2's atomic stage). See
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
   orthogonal `1`, folded with `max()`. `scan` adds a budget-overflow floor
   (`5`) and an evidence-contract-error floor (`1`) that precede the gate
   entirely (`docs/reference/exit-codes.md`'s own text: both "are returned
   before the baseline comparison — and therefore before any severity
   computation — ever runs"). A release comparison adds a
   removed-required-library code (`8`) whose precedence relative to the
   gate is **mode-dependent**, not a fixed rank (`docs/reference/exit-codes.md`,
   `abicheck compare` (multi-library) section). `NOT_COMPARABLE` (`16` for
   native `compare`, `6` for `scan --against`, `9` for `compat check`)
   dominates the release's gate/removed-library pair in both modes, but does
   **not** dominate `scan`'s own budget overflow.
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
usage/config error            (outside the report entirely — 64 everywhere)
scan evidence-contract error  (scan only, exit 1 — ADR-037 D5)
scan budget exceeded          (scan only, exit 5 — dominates not-comparable
                                below when both would apply in the same run)
not comparable                (dominates the removed-library/gate pair below,
                                but never dominates budget above — ADR-050 D2)
removed required library    ─┐ mode-dependent rank, not a fixed slot — see
ABI / API / policy gate      ─┘ "Removed-required-library is mode-dependent"
coverage & assurance floors   (max-folded on top; never lowers the above)
clean
```

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

- **Legacy scheme** (no severity map in effect): an ABI/API break or an
  operational `ERROR` wins; removed-library (`8`) is checked only when
  neither applies.
- **Severity-aware scheme** (a severity map is in effect): removed-library
  (`8`) takes precedence over the aggregated `0/1/2/4`.

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

1. **Additive.** Extend `ExitDecision`/`resolve_exit_decision` to compute
   the three remaining axes (evidence-contract error, budget overflow,
   not-comparable, and removed-required-library's mode-dependent rank) as
   pure, independently unit-tested logic, and wire it into `scan`'s and the
   release fan-out's report `exit` block for explanatory purposes — every
   existing call site's *actually returned* exit code stays bit-for-bit
   unchanged, exactly as PR G1 did for the first three axes. No flag is
   removed in this stage.
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
  `EVIDENCE_CONTRACT_ERROR`, a budget-overflow reason, `NOT_COMPARABLE`) and
  a `removed_required_library` reason whose precedence the resolver
  computes according to the mode-dependent rule above, not a static
  ordering table.
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
- The release fan-out's internal severity/exit-code representation changes
  shape (raw strings → `GateOptions`-shaped object) as part of the atomic
  stage; its externally observable exit codes and report fields do not
  change, other than gaining the `exit`/reasons block parity `compare`'s
  release path currently lacks.

## Cross-references

- [cli-cleanup-phase-two.md](../plans/cli-cleanup-phase-two.md) — "PR 4 —
  one gate algorithm (`--exit-code-scheme` removal)" is this ADR's source
  material; the plan's "Ordering" table tracks PR G1 (done, #789) and PR G2
  (this ADR, not yet implemented) as a pair.
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
