# ADR-082: Closure Respelling Disposition and Identical Export Observations

**Date:** 2026-10-12
**Status:** Proposed — drafted for maintainer decision. This ADR changes no behaviour by itself. The exported-symbol half of the respelling bug is fixed separately (`compare/closure_respelling.py`); the two decisions below wait for acceptance.

Governed by: [ADR-009](009-verdict-system-and-exit-codes.md) (verdict and exit codes), [ADR-042](042-compatibility-and-gate-decision-separation.md) (`effective_verdict` modulation), [ADR-067](067-change-intent-acknowledgment-and-disposition-audit.md) (record before disposing), [ADR-068](068-one-comparison-product-and-scan-retirement.md) §D3 (cross-source resolution states).

## Context

A non-constructor function parameterized over a local lambda closure spells the closure with a per-header ordinal (`raii_guard<(lambda:lib.h#1)>`). An unrelated closure inserted earlier in the same header renumbers it to `#2`. Two kinds of residual finding remain after the exported-symbol fix:

1. **Unexported or inline declarations.** `make_guard`, `arena_fn<int>::make` and synthetic ctor keys have no exported symbol. Nothing in the binary proves the respelling harmless, so the filter keeps them. Each produces a gating `func_return_changed` (and synthetic ctor `func_added`). Today these are BREAKING, so they gate the exit code.
2. **`exported_not_public` on statically linked standard-library symbols.** A library using `std::call_once` exports `std::once_flag::_Prepare_execution` thunks from libstdc++. The cross-source check (`buildsource/cross_source_checks.py`) reports them as `exported_not_public`. The finding appears on identical sources too. `workflows/cross_source_evolution.py` resolves an identity flagged on both sides as `PERSISTENT` and emits it, which ADR-068 §D3 defines as an intended state.

## Decision (proposed)

### D1. A closure respelling on a symbol with no binary export evidence is source-level

A signature finding whose only difference is a closure discriminator (the same rule `compare/closure_respelling.py` applies to exported symbols) and whose symbol is not `PRESENT(True)` in the binary export fact on both sides is recorded and modulated to `API_BREAK` (source-level), not `BREAKING`:

- `effective_verdict = API_BREAK`, `modulation_rule = "closure_respelling_unexported"`, reason naming the missing export evidence. The finding stays in `changes` (annotate, never remove; ADR-042, ADR-067).
- A closure respelling whose export fact is absent, partial, or never collected keeps its current verdict. Fail closed: only a confirmed unexported symbol is demoted.

Consequence: a run whose only break is such a respelling exits `2` (source break) instead of `4`. Severity presets that map the API-break category to `error` still gate.

### D2. A cross-source observation identical on both sides is not emitted as a change

`exported_not_public` findings that are `PERSISTENT` (flagged identically on both sides) are not emitted as compare changes. Their count is recorded in the disposition ledger (ADR-067) under a named rule, so the observation is not silently lost. A finding that is `INTRODUCED`, `RESOLVED` or `NOT_EVALUATED` is unchanged.

Consequence: ADR-068 §D3's `PERSISTENT` state is no longer emitted for this one check. That is a narrowing of a documented state, so ADR-068 needs its own amendment naming it. The alternative considered and not chosen is ADR-078's informational category: it stops the finding from gating but still emits it on every identical run.

## Consequences

- D1 changes a verdict and therefore an exit code for a documented set of runs. ADR-009 names the exit-code matrix, so the change is recorded there, and `docs/reference/exit-codes.md` is updated in the same PR as the code.
- D2 removes rows from the compare report for identical builds. Reports that cite the `PERSISTENT` state need the ledger count as their replacement.
- Neither decision affects an exported symbol, whose respelling is already handled by the evidence-based filter.

## Not decided here

- Whether D2's ledger rule should be a new rule id or reuse the existing suppression-ledger vocabulary.
- The mangled-only surface gap in `surface.py`. It is a separate detector and is not covered by either decision.

## Implementation, after acceptance

1. Add the modulation rule and its reason text to `policy/` (not `checker.py`).
2. Route the `exported_not_public` persistent case through the disposition ledger; add the ADR-068 amendment.
3. Update the exit-code reference and the ADR index; one changelog fragment per `abicheck/` change.
4. Tests: the inline insertion case reports `API_BREAK` with the modulation rule; the exported and negative cases are unchanged; a `PERSISTENT` identical build reports zero `exported_not_public` changes and a non-zero ledger count.
