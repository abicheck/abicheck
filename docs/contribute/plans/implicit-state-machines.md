---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# Implicit state machines

Status: Phases 1–4 done; Phase 5 partly done (see its table).

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

## Phase 3 — one worse-of fold per value family (done)

| Item | Result |
|---|---|
| Verdict order | `policy/release_outcome.py` already owned it. `policy/outcome.worst_real_verdict` ranked by `Verdict`'s *declaration order*; `workflows/aggregate/contracts._VERDICT_RANK` restated the order; `workflows/aggregate/report_gate._LEGACY_SEVERITY` copied `severity.legacy_exit_code`. All three now derive from the owners. |
| Exit-code folds | `release_exit_fold`, `aggregate/scope_axis`, `severity.compute_exit_code`, `exit_decision_precedence`: `max()` over integers, already one ordering. No change. |
| Assurance rank | `release_assurance._rank` already ranks an unknown status worst. But `release_assurance_members.member_assurance_fields` turned a present block's empty/`None` status into `complete` (`... or "complete"`), so one such member passed `require_complete`; it now reads as `unknown`. A non-empty unknown string still passes through verbatim (ranked worst) — validating it is in Phase 5. |
| Assurance if-chain | Done in Phase 2 (`_status_ladder.py`). |
| Frozen-namespace floor | Seven copies in `reclassify.py`/`reclassify_outcome.py` → `reclassify._frozen_floor`. |

## Phase 4 — the disposition lifecycle (done)

| Item | Result |
|---|---|
| Transition rules | `policy/disposition_transitions.py`: `scope_out` (consumer scope narrows a record) and `exclude_consumer_only` (D-S1, full-library gate) as pure functions; the ledger and `disposition_close` call them instead of restating the moves. Tested exhaustively over every disposition and flag against a table in the test. |
| Scope applied once | `DispositionLedger.apply_scope` records the scope. Repeating the same scope is a no-op (`check_appcompat` and `--used-by` can close one result twice); a *different* second scope raises, since it would intersect consumers' scopes instead of taking their union — previously a silent pass with a real break counted out. |
| Promote-then-narrow | `refresh_promoted` after `apply_scope` now narrows what it promotes against the recorded scope, so the two passes commute. Latent before (the one call site promotes first). |
| Pipeline step order | `DispositionPipeline` rejects more than one step that establishes the `ctx.kept` alias when it is built. |
| Asymmetry checked | `exclude_consumer_only` leaves a `non_gating` record unmarked while `scope_out` marks it: not reachable as a bug — a consumer-only record is outside the severity gate's input (so `with_gate` excludes it) and without a severity config is re-answered by its own unchanged kind. |

No change to the event set, so no ADR-067 amendment.

## Phase 5 — scanners, lifecycle flags, in-place mutation

| Item | Status |
|---|---|
| `extract/headers/concept_scan.py`: `#elif 0` after `#if 0` was read as live code | **done** (extractor generation 58; generation 57 frozen) |
| `header_conditionals.py`: braces inside string/char literals opened/closed scopes | **done** (literals blanked before brace tracking) |
| `deadline.py`: an unbalanced registration-window exit drove the depth negative, so a deferred SIGTERM was never replayed | **done** (raises at the unbalanced exit) |
| `process_resources.BudgetedExecutor`: accepted work after shutdown when it ran inline (the thread pool already rejected it) | **done** |
| `workflows/release_assurance_members.py`: an unknown member assurance status was shown verbatim | **done** (reported as `unknown` with the original value in its notes; ranked worst as before) |
| `demangle.py` availability latches | **dismissed**: the flags never gate the `import cxxfilt` attempt, so a backend is retried every call; "missing" and "broken" come from distinct exception kinds of the same import |
| One shared C/C++ lexer for `pattern_facts_lexer.py` and `macro_graph.py:566` | open: `macro_graph` needs per-line "starts inside a block comment" state the shared lexer does not expose; raw strings are still unmodelled there |
| Bracket-depth counters: one policy for an unbalanced closer | **done**: four scanners could go negative and now clamp (`type_graph_names` x2, `namespace_spelling`, castxml `out_of_line_inline`); the other sweep sites start on an opener, are already guarded (`and depth`, `elif depth == 0`), or stop at `depth <= 0` |
| `extract/headers/clang/locations.py:79`: check document order | **dismissed**: the one stateful caller (`header_graph_ast_stream.project_header_graph_ast_file`) walks top-level declarations once, forward; replay builds a fresh `LocationMaterializer` per element from its recorded state; and clang legitimately moves a line backwards within one file (implicit members after fields), so a backwards-line guard would fire on valid input |
| `depth_projection.py:465/665` in-place writes | **dismissed**: both write to the fresh copy `project_snapshot_to_depth` makes (`copy.deepcopy`/`copy.copy`), pinned by `test_depth_projection_ownership.py` |
| `workflows/consumer_impact.enrich_covered_changes` writes a consumer's proof path onto `DiffResult.changes` objects | **open, confirmed bug**: the path leaks into the library-wide report and into later `--used-by` consumers (the first app's walk wins; suppression reads `reachability_state`). Copying the `Change` fixes the leak but the copy must stay tied to the original's ledger record: `close_consumer_scope` records `also_detected` by identity without a `dedupe_key`, so a bare copy would be counted as a new detection |
| `checker_types.py:51`: a result read before its verdict is computed reads `NO_CHANGE` | open (public dataclass default; needs its own change) |

## Dismissed

- `workflows/bundle_facts_stored_compare.py:181` "any value other than
  `warn` becomes `fail`": `assurance.unconfirmed` is validated against
  `fail | warn` when `.abicheck.yml` is loaded (`buildsource/build_config.py`).

- `abicheck/sycl_context.py` `_BracketState` "depth can go negative": every
  document starts with `{`/`[` (checked before the scan) and the scan returns
  the moment depth reaches 0, so a negative depth is unreachable.
- `abicheck/model/name_heuristics.py` `LazyFactInput` "no lock": a per-call
  local object, never shared between threads.
