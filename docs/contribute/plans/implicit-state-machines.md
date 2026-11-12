---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Implicit state machines

Status: Phases 1 and 2 done. Phases 3–5 not started.

## Why

A 2026-11 sweep of `abicheck/` and `scripts/` found about 55 places where
behavior depends on a mutable mode/status variable whose states and
transitions exist only as string literals, flag combinations, counters or
the order of an if-chain. Two were live bugs. Most of the rest are correct
today, but nothing stops the next change from adding an invalid transition,
a misspelled state or an out-of-order call.

## Rules for this work

- A state that crosses a function boundary is a `StrEnum` (or an existing
  enum), not a bare string. Serialized spellings stay byte-identical — the
  enum's value is the old literal — so no report or snapshot schema changes.
- An ordering contract stated in a docstring becomes a checked one: the
  object records its phase and rejects a call out of order with a
  `RuntimeError` naming the expected phase.
- "Worse-of" folds use one rank table per value family, and an unknown value
  raises; it never ranks as best or worst by accident.
- Each fix ships with a test over the state space (every state, every
  transition or a generated input set), against an oracle that is not the
  code's own helper.
- A finding that turns out not to be a reachable defect is recorded under
  [Dismissed](#dismissed) with the reason, not silently dropped.

## Phase 1 — confirmed defects

| Item | Location | Fix | Status |
|---|---|---|---|
| 1.1 | `abicheck/pdb_parser.py` `TypeDatabase.parse_all` | `_parsed` set before the work; a failure left a half-filled database marked parsed. Set it after success, reset tables on failure. | done |
| 1.2 | `scripts/check_bugfix_test_contract.py` `added_lines_by_path` | Context lines did not advance the new-side line counter. Latent today (the gate diffs with `--unified=0`). | done |
| 1.3 | `abicheck/buildsource/pattern_facts_lexer.py` | Unknown lexer state fell into the char-literal branch. Explicit branch plus an assertion. | done |

## Phase 2 — typed status vocabularies (done)

| Item | Result |
|---|---|
| Extractor status `ok \| partial \| failed \| skipped` | `buildsource.model.ExtractorStatus`; `ExtractorRecord`, `CollectionResult` and `NormalizationResult` reject any other value. A stored record with an unknown value loads as `failed` with a diagnostic, never `ok`. The new check caught the Bazel `--build-info` path writing `present`; fixed to `ok`, and stored `present` loads as `ok`. |
| Assurance sub-statuses | `Literal` vocabularies in `policy/assurance_status_vocabulary.py`, typed on the producers and on `AnalysisAssurance`. mypy found one undocumented value (`schema_staleness_status = "not_evaluated"` when there is no old snapshot), now part of the vocabulary. |
| Assurance overall status | `analysis_assurance/_status_ladder.py`: one table per sub-status; a value missing from a table is a `KeyError` and a test failure, not `complete`. Proven equal to the old if-chain over the whole input space. |
| Inline-graph completeness tie | `call_graph_state` is never `none`, so the re-check was always true; replaced with a plain `else`. No behavior change. |
| Deprecation status | `DeprecationStatus` `Literal`. |

## Phase 3 — one worse-of fold per value family

The verdict and exit-code ladders built with `max()` in eight or more
places get one owner each in `policy/`, with a rank table that raises on an
unknown value:

- verdict rank: `workflows/aggregate/fold.py`, `policy/outcome.py:671`,
  `policy/release_exit_decision.py:266`;
- exit-code fold: `workflows/release_exit_fold.py`,
  `workflows/aggregate/scope_axis.py`, `policy/severity.py:578`,
  `policy/exit_decision_precedence.py:509`;
- assurance rank: `policy/release_assurance.py:136`.
- The assurance if-chain in `_compute.py:242–276` becomes a table of
  (condition, status) rows checked in rank order, with a test that every
  sub-status field is listed in some row (a new field can no longer default
  to `complete`).
- Frozen-namespace promotion in `policy/reclassify.py` (five call sites)
  moves to one helper.

## Phase 4 — the disposition lifecycle

`policy/disposition_ledger.py` plus `disposition_close.py`,
`disposition_gate.py`, `disposition_acknowledgment.py`:

- one transition table `(from disposition, event) -> to disposition` that
  every rewrite goes through;
- a ledger phase (`recording -> promoted -> scoped -> closed`) checked by
  each method, so `apply_scope` before `refresh_promoted`, or twice, raises;
- an exhaustive test over every (disposition, event) pair against a table
  written in the test;
- `finding_disposition/pipeline.py:246`: validate the step order up front,
  not only the kept-alias contract.

Owner ADR: [ADR-067](../adr/067-change-intent-acknowledgment-and-disposition-audit.md).
Needs an ADR amendment if the event set changes.

## Phase 5 — scanners, lifecycle flags, in-place mutation

- One shared C/C++ comment/literal lexer for
  `buildsource/pattern_facts_lexer.py` and `buildsource/macro_graph.py:566`
  (the second lacks raw strings).
- `header_conditionals.py:482`: count braces outside string/char literals;
  clear `pending` at a new declaration, not only at `;`.
- `extract/headers/concept_scan.py:85`: re-evaluate `#elif` after `#else`
  inside an `#if 0` group.
- Bracket-depth counters: one policy for unbalanced closers (clamp, and
  record it) across the ~20 scanners listed in the sweep.
- `deadline.py`: assert the registration-window depth never goes negative.
- `demangle.py:59–93`: one backend-availability enum instead of three
  independent latches; set under a lock.
- `process_resources.py:550`: reject `submit()` after the grant is released.
- `extract/headers/clang/locations.py:79`: check document order.
- `consumer_impact.py:494`, `depth_projection.py:465/665`: return new values
  instead of mutating shared `Change`/snapshot objects.
- `checker_types.py:51`: a result read before its verdict is computed is an
  error, not `NO_CHANGE`.

## Dismissed

- `workflows/bundle_facts_stored_compare.py:181` "any value other than
  `warn` becomes `fail`": `assurance.unconfirmed` is validated against
  `fail | warn` when `.abicheck.yml` is loaded (`buildsource/build_config.py`).
- `workflows/release_assurance_members.py:85` "coerces any value":
  `AnalysisAssurance.status` is already the typed `AssuranceStatus` `Literal`.

- `abicheck/sycl_context.py` `_BracketState` "depth can go negative": every
  document starts with `{`/`[` (checked before the scan) and the scan returns
  the moment depth reaches 0, so a negative depth is unreachable.
- `abicheck/model/name_heuristics.py` `LazyFactInput` "no lock": a per-call
  local object, never shared between threads.
