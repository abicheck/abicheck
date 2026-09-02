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
diverge). **Update (2026-08-31):** the programmatic `ScanResult` API's own
`_BudgetOverflow`/`_EvidenceContractError` catches
(`service_scan.run_scan`/`_run_scan_one_member`) now also persist a real
`ExitDecision` into `ScanResult.report["exit"]`
(`abicheck.workflows.scan_abort_result.scan_abort_result_fields` — a
`workflows`-classified module, not `policy`, since shaping `ScanResult`'s
own fields is report-shape work `abicheck/policy/AGENTS.md` reserves for a
different layer than the gate decision itself, which still resolves through
`abicheck.policy.exit_decision_precedence.resolve_scan_exit_decision`
unchanged; Codex review, PR #967),
closing that half of the gap this section used to describe as fully open —
these two abort exceptions previously left `report` at its default empty
dict, unlike `NOT_COMPARABLE`, which already built one. Prior contributions
across a *late* `_BudgetOverflow` (the post-compare deadline check, which
can fire after a real gate/coverage/assurance decision already exists) are
preserved too (in both the baseline-compare and audit-only branches), via
`_BudgetOverflow.prior_decision`/`abicheck.workflows.scan_abort_result.
attach_prior_on_budget_overflow`/`audit_prior_decision`, rather than
discarded in favor of a budget-only decision. **Landed (2026-08-31): the
native `scan` CLI's own equivalent.** `cli_scan.py`'s two abort catches now
call the new `_emit_scan_abort_report` helper — but only for `--format
json` (or a `--write json=...` secondary output); before this, such an
invocation that hit either abort produced empty stdout/no secondary file,
so a consumer trying to parse it was already broken, and adding real
content on that path changes no exit code and adds no output where any
consumer could have depended on emptiness of a *working* JSON path. The
payload is a minimal `ScanOutcome.to_dict()`-*compatible* envelope
(top-level `verdict`/`exit_code`, the exit decision under `diff.exit`) —
deliberately **not** `scan_abort_result_fields(...)["report"]`'s own shape,
which is the *typed API's* `ScanResult.report` nesting, a different
envelope; `workflows/aggregate/gate.py`'s `GateInfo.from_scan_report`
requires a top-level `exit_code` and would raise `_MalformedGate` without
one (Codex review, fresh evidence) — an earlier revision of this fix used
that wrong shape before the gap was caught. `--format text` is deliberately
unchanged: `bo.message`/`ce.message` already read as the human-facing
explanation, and there is no `ScanOutcome` to feed `_render_text` at this
point (most of its fields were never computed) — inventing prose for that
gap remains a separate, open question this update does not attempt. **Update
(2026-09-01):** the first slice of the "full cross-front-end parity pass"
this section names as still open has landed — the composite Action's own
`scan` verdict mapping (`action/run.sh`) previously folded an
`_EvidenceContractError` abort into the generic `ERROR` bucket a CLI usage
error gets, since both produce the identical `Error: ...` stderr shape; it
now recognizes the native CLI's own `verdict: "EVIDENCE_CONTRACT_ERROR"`
JSON envelope and publishes a matching, distinguishable verdict (see
"Staged landing, additive first" below, item 1's own end-of-list "Update"
for the full account). **Update (2026-09-02):** the effective-format-override
gap that same parity pass's review rounds found — `extra-args: --format
json` overriding a `format: text`/`markdown` step's nominal format, which
`action/run.sh`'s JSON-detection sites (`_STDOUT_JSON_FILE`,
`_json_report_src`'s `OUTPUT_FILE` branch) previously missed — is fixed; see
"Staged landing, additive first" below, item 1's own end-of-list note. Still
open: the release fan-out's `GateOptions` unification; the rest of the
cross-front-end parity pass (typed API; the `--format text` gap named
above; a real `--artifact-set` member-level evidence-contract signal); and
**stage 2**, the
`--exit-code-scheme` removal itself. See
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
      report reader sees). **Landed (2026-08-31), typed-API half:** the
      programmatic `ScanResult` API's own `_BudgetOverflow`/
      `_EvidenceContractError` catches (`service_scan.run_scan`/
      `_run_scan_one_member`) now persist a real `ExitDecision` into
      `ScanResult.report["exit"]`
      (`abicheck.workflows.scan_abort_result.scan_abort_result_fields`,
      `tests/test_scan_abort_result.py`) — `ScanResult` already existed as a
      real return value at these two abort points (it is what `run_scan`'s
      docstring calls "the single object the CLI and library callers
      consume"), so giving its already-present, always-empty `report` field
      real content needed no new design decision, only the same wiring
      `NOT_COMPARABLE` already got. The shaping logic (the verdict/exit_code
      pairing and the `{"exit": ...}` wrapping) lives in this new
      `workflows` module rather than the `policy` package that resolves the
      underlying `ExitDecision` — `abicheck/policy/AGENTS.md` reserves "how
      is it reported" for a different layer, and an earlier revision had put
      it in `exit_decision_precedence.py` itself before a review round
      caught the boundary violation (PR #967). `SCAN_SCHEMA_VERSION` bumped
      to `1.23` for the newly nonempty `report.exit` shape. **Landed
      (2026-08-31), prior-decision follow-up:** carrying a `prior_decision`
      across `scan_engine.py`'s own *later* `_BudgetOverflow` raise site
      (the post-compare deadline check, which runs after a real
      gate/coverage/assurance decision already exists) — `_BudgetOverflow`
      now carries a `prior_decision: dict[str, object] | None` attribute,
      set by `abicheck.workflows.scan_abort_result.
      attach_prior_on_budget_overflow` (a context manager wrapping that one
      call site, catching via `hasattr` duck typing rather than importing
      the private exception class into the unclassified `scan_engine.py`);
      `service_scan.py`'s two catch sites forward `exc.prior_decision`
      through to `scan_abort_result_fields`, which reconstructs it via
      `ExitDecision.from_dict` before handing it to `resolve_scan_exit_
      decision`'s own `prior_decision` parameter (`tests/
      test_scan_abort_result.py::TestAttachPriorOnBudgetOverflow`). **Landed
      (2026-08-31), native-CLI half:** `cli_scan.py`'s `scan_cmd` calls
      `run_scan_core` directly (not through `service_scan.run_scan`), and
      used to only write a stderr message plus `sys.exit`/`ClickException`
      at these two abort points — no `ScanOutcome`/report was ever
      constructed on this path, unlike `NOT_COMPARABLE`, which the CLI's own
      code path already built one for. The open design question was whether
      a machine-readable `--format json` scan invocation should get a
      minimal JSON report on this abort path too, instead of empty stdout,
      and from what partial state (most of `ScanOutcome`'s fields are never
      computed at the earliest, candidate-collection-stage budget overflow).
      Resolved narrowly rather than by constructing a partial `ScanOutcome`:
      a new `_emit_scan_abort_report` helper prints a minimal
      `ScanOutcome.to_dict()`-*compatible* envelope (top-level
      `verdict`/`exit_code`/`scan_schema_version`, the exit decision nested
      under `diff.exit`, matching where `NOT_COMPARABLE`/a baseline compare
      already publish theirs) — but only when `fmt == "json"`; a `--format
      json` invocation on this path previously produced empty stdout, which
      was already unusable to any consumer parsing it as JSON, so this adds
      content only where none existed and changes neither exit code
      (`tests/test_cli_scan_abort_report.py`). `--format text` is
      unchanged: `bo.message`/`ce.message` already read as the human-facing
      explanation, and inventing prose to fill `ScanOutcome`'s missing
      fields for a text rendering remains a separate, unaddressed question.
      **Landed (2026-08-31), four follow-up fixes found by review on the
      slices above:** (1) the *audit* path
      (`run_scan_core`'s no-baseline branch) had the same late-budget-
      overflow gap the baseline-compare branch's own fix closed —
      `_audit_exit_code` never built a `diff_summary`, so a late overflow in
      audit mode had nothing to preserve either. `_audit_exit_code` now
      returns a third element, `abicheck.workflows.scan_abort_result.
      audit_prior_decision`'s ``{"exit": ...}`` shape built from the same
      compatibility/crosscheck contributions it already computes, fed to
      `attach_prior_on_budget_overflow` via `diff_summary or audit_prior` —
      without changing audit mode's own (non-aborting) report, which still
      carries `diff: null` (`cli_scan_helpers.py`'s text renderer keys off
      exactly that presence/absence, so populating it unconditionally would
      have been a real regression, not merely a schema-version bump).
      (2) `cli_scan._emit_scan_abort_report` only wrote to the *primary*
      `--format`/`--output`; the documented `--format text --write
      json=...` combination (the GitHub Action's own text-primary/JSON-
      secondary pattern) silently produced no secondary artifact on abort.
      It now also writes to `secondary_output` whenever `secondary_fmt ==
      "json"`, independent of the primary format (`tests/
      test_scan_abort_result.py::TestAuditPriorDecision`, `tests/
      test_cli_scan_abort_report.py`'s secondary-output tests). (3) The
      first cut of `_emit_scan_abort_report` reused
      `scan_abort_result_fields(...)["report"]` directly — the *typed API's*
      `ScanResult.report` nesting, `{scan_schema_version, exit}` with no
      top-level `verdict`/`exit_code` — which is a different envelope from
      the CLI's own `ScanOutcome.to_dict()` contract. A saved `--format
      json` abort report fed to `workflows/aggregate/gate.py`'s
      `GateInfo.from_scan_report` (which requires a top-level `exit_code`)
      would have raised `_MalformedGate` rather than reading the budget/
      evidence decision it carries (Codex review, fresh evidence). Fixed by
      building the envelope-compatible payload described above instead
      (`TestAbortPayloadIsAggregateCompatible` in `tests/
      test_cli_scan_abort_report.py`, exercising `GateInfo.from_scan_report`
      and `workflows/aggregate/load.parse_report_verdict` directly against a
      real abort payload). (4) That fix alone was still not enough for the
      real `aggregate` pipeline: `workflows/aggregate/load._load_report_file`
      only calls `GateInfo.from_scan_report` *after*
      `parse_report_verdict` succeeds, and neither `"BUDGET_OVERFLOW"` nor
      `"EVIDENCE_CONTRACT_ERROR"` is a `Verdict` enum member, so the abort
      still read as an unavailable/verdictless report a warn/optional/
      discovered-target policy could silently tolerate, exactly the
      "unmodeled" gap the review caught by exercising `_load_report_file`
      itself rather than its two callees in isolation. Fixed the same way
      `_load_report_file` already handles a compare-release operational
      `"ERROR"` verdict and a native `not_comparable` result: two new
      sentinels (`_SCAN_BUDGET_OVERFLOW_VERDICT`/
      `_SCAN_EVIDENCE_CONTRACT_ERROR_VERDICT` in `workflows/aggregate/
      contracts.py`) force a blocking `GateInfo` before the generic
      verdict-parsing branch, the same "real failure, never silently
      tolerated" treatment `_OPERATIONAL_ERROR_VERDICT` already gets —
      unlike `_BOOTSTRAP_VERDICT`/`_NEW_TARGET_VERDICT`, which are
      legitimately-tolerated fall-throughs. **A same-day follow-up caught
      this forced gate's own `exit_code`:** the first cut hardcoded scan's
      raw private code (5 for budget overflow) straight into the forced
      `GateInfo`, bypassing `GateInfo.from_scan_report`'s own normalization
      (every scan exit outside `{0, 2, 4}` folds to `1`,
      `COVERAGE_INCOMPLETE_EXIT`) — the aggregate's own published contract
      has no exit 5, so this leaked scan's numbering into
      `AggregateResult.exit_code` (Codex review, fresh evidence: a legacy
      scan payload with the same verdict already correctly returned 1,
      while the new sentinel branch returned 5 for the identical failure).
      Fixed by using `COVERAGE_INCOMPLETE_EXIT` for both abort verdicts'
      gate `exit_code` (still `blocking_categories=("budget_overflow",)`/
      `("evidence_contract_error",)`), matching `GateInfo.from_scan_report`'s
      own rule exactly. Verified against the real end-to-end path this
      time, not just the two readers: `tests/test_aggregate_migration_
      coverage.py` exercises `_load_report_file` directly, and `tests/
      test_cli_scan_abort_report.py::TestAbortPayloadThroughRealAggregate`
      runs a real `scan --format json` abort through the real
      `aggregate_reports_dir`. **(5) A further review round caught the
      forced gate itself inventing a compatibility verdict:** setting
      `compatibility_verdict=Verdict.BREAKING` for the forced abort gate
      (mirroring `_OPERATIONAL_ERROR_VERDICT`) made `AggregateResult.
      to_dict()` report `compatibility.verdict: "BREAKING"`, a complete
      `analyzed_targets` count, and an affected profile for a scan that
      never actually compared anything (Codex review, fresh evidence).
      Fixed by keeping the target `compatibility_verdict=None` (unavailable)
      for a scan abort specifically, while its forced gate still counts
      toward `AggregateResult.exit_code()`/`blocking_targets` regardless of
      required/optional declaration via a new `AggregateResult.
      _forced_gate_targets` fold — the unavailable-but-gated shape
      `operational_error`/`not_comparable` don't need, since those keep the
      synthetic `BREAKING` verdict this fix removes only for scan aborts.
      **(6) A sixth round caught the sticky PR comment reading the same
      abort envelope as a clean, zero-findings comparison:**
      `pr_comment_scan.from_scan` only special-cased `NOT_COMPARABLE`'s
      `{"reason": ...}` shape, so the abort envelope's empty `findings`/
      `additions`/`quality` buckets rendered "No ABI changes" — under
      `--on=changes` this could delete a prior sticky failure comment
      (Codex review, fresh evidence). Fixed via a new
      `pr_comment_scan_abort.scan_abort_incomplete_reason` helper (split
      into its own leaf module for the same no-growth-budget reason as the
      scan-engine helpers above), giving the abort the identical single
      blocking "analysis incomplete" `Finding` treatment `NOT_COMPARABLE`
      already gets. **(7) A seventh round caught the aggregate gate
      downgrading a real prior break:** a *late* `_BudgetOverflow`
      preserves the ordinary compatibility/coverage/assurance/crosscheck
      contributions already computed before it fired in the report's
      `diff.exit` (`attach_prior_on_budget_overflow`, (4) above) -- but
      `_load_report_file`'s forced-blocking branch never read them,
      unconditionally floor-setting the gate to `COVERAGE_INCOMPLETE_EXIT`
      (1) even when `compatibility_contribution` was `4` (a real ABI break
      already found) (Codex review, fresh evidence). Fixed via a new
      `_scan_abort_prior_exit` helper that reads the largest valid
      preserved contribution from `diff.exit` and folds it with `max()`
      against the coverage floor; an early abort (every contribution
      genuinely `0`) still floors at `1` unchanged, and a malformed/
      out-of-scheme contribution is ignored rather than trusted. **(8) An
      eighth round caught the preserved contract-coverage/analysis-
      assurance contributions folded into the gate but dropped from their
      own orthogonal reports:** `AggregateResult.contract_coverage_exit`/
      `.analysis_assurance_exit` (and their `..._targets` lists) read
      `_LoadedReport.contract_coverage_exit`/`.analysis_assurance_exit`
      directly, never the gate -- and those two fields only ever read the
      differently-named, older `contract_coverage_exit_contribution`/
      `analysis_assurance_exit_contribution` fields a scan-abort payload
      never carries, so a late abort that preserved a real `1` on either
      axis silently reported `0` with an empty target list on both
      (Codex review, fresh evidence). Fixed via a new `_scan_abort_exit_
      axis` helper that reads each axis separately from `diff.exit` and
      folds it into the corresponding `_LoadedReport` field. **(9) A ninth
      round caught the same two helpers missing `scan --artifact-set`'s own
      abort shape entirely:** `ScanSetResult.to_dict()` has no `diff` key at
      all -- its own top-level `verdict` can equally read `"BUDGET_
      OVERFLOW"` (`_aggregate_scan_set_verdict`: any member overflowing
      makes the whole set report one), but each member's own preserved
      decision nests instead at `per_artifact[i].report.exit`
      (`ScanArtifactResult.to_dict()` wrapping the typed API's own
      `ScanResult.report` envelope, not `ScanOutcome`'s `diff.exit`) (Codex
      review, fresh evidence). Reading only the single-binary shape silently
      downgraded a real member break to the generic abort floor and omitted
      the coverage/assurance axes for a set-level abort. Fixed by
      generalizing `_scan_abort_exit_block` into `_scan_abort_exit_blocks`,
      which returns every exit-decision block a report may carry (the
      single `diff.exit`, plus every `per_artifact[i].report.exit`); both
      consumers now fold `max()` across all of them instead of reading one.
      **(10)/(11) A tenth and eleventh round, on the same commit, caught two
      further shapes `_scan_abort_exit_blocks` still missed:** (10) the
      typed API's own `ScanResult.to_dict()` dumped directly (no native CLI
      involved) has no `diff` key at all -- its preserved decision nests at
      the document *root*'s own `report.exit`, a third shape distinct from
      both `diff.exit` and an artifact-set member's `per_artifact[i].
      report.exit`; (11) a `scan --artifact-set` set-level abort firing
      *after* every member already finished normally (the shared budget
      expiring during the post-member bundle audit, `run_scan_set`'s own
      `per_artifact=per_artifact` branch there) preserves real, completed
      member results in `per_artifact` -- but a completed member never
      aborted, so its own `ScanResult.report` is empty with no nested
      `exit` block at all; its real result lives only in its own bare
      top-level `exit_code` (Codex review, fresh evidence for both). Fixed
      by extending `_scan_abort_exit_blocks` to also read root `report.
      exit`, and to synthesize a minimal `{"compatibility_contribution":
      exit_code}` block from a member with no nested decision -- both fold
      through the same `max()` machinery as a real block, rather than a
      separate code path. This closes every envelope shape this codebase's
      own report producers can actually emit (native CLI single-binary,
      typed-API single-result, artifact-set member abort, artifact-set
      member completed-without-abort); a further exotic shape would need
      its own review round to surface, same as these five did. **(12) A
      twelfth round caught a different kind of gap in the same file, not
      another envelope shape:** `_aggregate_scan_set_verdict` (ADR-056 D3,
      `service_scan.py`) deliberately keeps a stronger real `API_BREAK`/
      `BREAKING` verdict at a set's own root even when another member
      aborted with `EVIDENCE_CONTRACT_ERROR` alongside it -- a real break
      must never be hidden behind an evidence-completeness verdict -- but
      that left the root `verdict` string with no way to say which member
      aborted, so the loader's `blocking_categories` silently dropped
      `evidence_contract_error` for that target despite the member never
      completing a comparison (the real severity, exit 2/4, was already
      correct through `GateInfo.from_scan_report`'s mapped-code branch;
      only the category label was missing) (Codex review, fresh evidence).
      Fixed by a new `_member_abort_categories` helper that reads each
      `per_artifact` member's own bare `verdict` field directly and folds
      any abort category it names into the gate, independent of which
      verdict won at the root. Unlike (1)-(11), this was not another
      envelope shape `_scan_abort_exit_blocks` needed to recognize --
      it was the set-level verdict-blending logic itself dropping a
      category label it never carried into any `exit` block to begin
      with, so it needed its own read of `per_artifact[*].verdict` rather
      than another fold over `_scan_abort_exit_blocks`'s output. **(13) A
      thirteenth round, immediately after, caught the fix above only
      reaching one of `_load_report_file`'s two abort-handling branches:**
      `_aggregate_scan_set_verdict`'s own step 1 makes any member's
      `BUDGET_OVERFLOW` dominate the set-level `verdict` unconditionally,
      even when a *different* member aborted with `EVIDENCE_CONTRACT_ERROR`
      for an unrelated reason -- but the root-abort branch (the one keyed
      on `raw_scan_verdict` matching a synthetic abort string directly)
      hardcoded only the single category matching that string and
      returned before `_member_abort_categories` was ever consulted, so a
      sibling member's `evidence_contract_error` category was still
      silently dropped in exactly the case (12)'s fix didn't reach (Codex
      review, fresh evidence). Fixed by unioning
      `_member_abort_categories` into that branch's `blocking_categories`
      too, the same way (12) already does for the normal-verdict branch.
      Still open: the release fan-out's `GateOptions` unification and a
      full cross-front-end parity pass (typed API, Action).

      **Update (2026-09-01): first slice of the Action-side parity pass.**
      The composite Action's own `scan` verdict mapping
      (`action/run.sh`) had exactly the gap this precedence work exists to
      close: `cli_scan.py` raises `_EvidenceContractError` as a
      `click.ClickException` (stderr `Error: <message>`, exit 1) — the
      identical shape a bad flag or a crash produces — so `run.sh`'s
      `_is_cli_error` check (`grep -qE '(^Usage:|^Error:|...)'`) matched it
      unconditionally and folded a well-formed, evidence-incomplete scan
      into the same generic `ERROR` bucket a syntax typo gets, even though
      the native CLI's `--format json` path already writes a real,
      distinguishable `verdict: "EVIDENCE_CONTRACT_ERROR"` envelope for
      this exact abort (`_emit_scan_abort_report`/
      `scan_abort_result_fields`, landed earlier in this same stage). Fixed
      by a new `_evidence_contract_gated()` helper (mirrors
      `_coverage_gated`/`_assurance_gated`'s own JSON-first pattern,
      reading the report's top-level `verdict` field, which
      `_json_report_src`'s existing freshness/fingerprint checks already
      keep from false-positiving on a stale prior report) consulted ahead
      of `_is_cli_error` in the exit-1 dispatch, giving the abort its own
      `EVIDENCE_CONTRACT_ERROR` verdict with a job-summary line and
      `action.yml` output documentation, mirroring `NOT_COMPARABLE`/
      `BUDGET_OVERFLOW`'s existing treatment — including the same
      unconditional step-failure block those two verdicts needed of their
      own (splitting a new verdict out of the generic `ERROR` bucket means
      it no longer matches that bucket's own `FINAL_EXIT=1`, so it needs an
      explicit twin or the step would silently start passing). **Deliberately
      not** given `BUDGET_OVERFLOW`'s own `_maybe_post_pr_comment` skip — an
      initial version of this fix copied that skip by analogy and a Codex
      review round (fresh evidence) caught that the analogy doesn't hold:
      unlike `BUDGET_OVERFLOW` (which genuinely has no report to reuse,
      since `run_scan_core`'s deadline-guarded candidate-snapshot collection
      raises before `_emit_scan_report` ever runs), reaching
      `EVIDENCE_CONTRACT_ERROR` already proves `_evidence_contract_gated`
      found a populated, readable JSON report — that is the only way it can
      have returned true — and `pr_comment_scan_abort.
      scan_abort_incomplete_reason` (above) already renders that exact
      envelope as a blocking "analysis incomplete" finding, its own
      docstring naming the GitHub Action as one of the paths meant to reach
      it. The skip would have left a previous sticky BREAKING/API_BREAK
      comment stale and misleading instead of updating it, and — for the
      rarer case where no JSON report exists yet at this point — a re-run to
      obtain one is cheap for this specific abort, since
      `_EvidenceContractError`'s own precondition check fires before any
      source evidence collection begins, unlike a real budget-limited scan.
      Whenever no JSON report exists at all for this wrapper to read, this
      classification is unavailable and the run still reads as `ERROR`,
      same as before this fix — the one acknowledged gap noted above this
      update. **Three successive review rounds each found the previous
      restatement of exactly *when* a JSON report exists incomplete**
      (`pr-comment: false` with no other JSON source; an `--artifact-set`
      scan, which suppresses the auto-injected sidecar unconditionally; and
      the run's own `extra-args` already supplying a non-JSON `--write`,
      which also suppresses the injection so as not to clobber the
      caller's own flag) — `action.yml`'s `verdict`/`exit-code` output
      descriptions stopped trying to enumerate the condition in prose after
      the third finding and instead point at `action/run.sh`'s own
      JSON-sidecar-injection logic as the one authoritative source, rather than
      a fourth prose restatement this file's own history shows keeps
      finding one more uncovered combination. **A stricter instance of the same
      gap (Codex review, fresh evidence):** `--artifact-set`
      (the Action's `new-library-set` input) skips the JSON secondary
      write *unconditionally* — `action/run.sh`'s own injection guard
      requires `-z "$SCAN_ARTIFACT_SET"` regardless of `pr-comment` — even
      though `cli_scan._run_artifact_set`'s text renderer
      (`_render_artifact_set_text`) always prints a stable, parseable
      `Artifact-set scan verdict: EVIDENCE_CONTRACT_ERROR (exit N)` line
      (unlike the single-binary abort, which prints nothing distinguishing
      in text form at all — `ScanSetResult` already exists as a real
      object here, so there is genuinely something to render). Left
      unread rather than parsed: teaching the wrapper to recognize a
      second, mode-specific text sentinel — after this same review round
      already found the first `--evidence_contract_gated` addition needed
      its own hostile-input test — was judged not worth the additional
      parsing surface and its own adversarial-input analysis for one
      narrower mode, when `--format json` already produces this verdict
      at the set's top level in the common case (`ScanSetResult.to_dict()`'s
      own top-level `verdict` field is exactly the one `_report_query`'s
      `compat_verdict` query already reads). **A second review round found
      that claim itself incomplete (Codex review, fresh evidence):**
      `ScanSetResult`'s own aggregation
      (`service_scan._aggregate_scan_set_verdict`, pre-existing, untouched
      by this PR) reports `EVIDENCE_CONTRACT_ERROR` at the set's top level
      only when it is the *worst* outcome across the set — a sibling
      library's real `API_BREAK`/`BREAKING` keeps that verdict at the top
      level instead (the aborted member's contribution still floors the
      overall exit code at 1, per that function's own docstring, but the
      wrapper's fail-on-api-break/fail-on-breaking inputs alone decide the
      step's outcome in that case, same as for an ordinary break, with the
      sibling abort visible only in the JSON report's own `per_artifact`
      list). So even under `--format json`, "unconditionally fails the
      step" holds for `--artifact-set` only when the evidence-contract
      abort is the set's own worst outcome. Documented precisely in
      `action.yml`'s own `verdict`/`exit-code` output descriptions instead
      of parsing `per_artifact` here too, for the same reason as the first
      gap: a second layer of JSON-array parsing logic earns its own
      hostile-input analysis and test, and this PR would rather record an
      accurate limitation than ship that under-tested.
      `tests/test_action_run_sh_scan_evidence_contract_error.py` covers the
      exit-1 dispatch (including the "both signals present" case, since
      real stderr always satisfies `_is_cli_error` too) and the
      step-failure block. **A fourth review round (Codex, fresh evidence)
      caught a message-accuracy gap, not a detection gap:**
      `_EvidenceContractError` has two independent raise sites in
      `scan_engine.py` -- the pinned-depth/missing-evidence check this
      wrapper's messages were written around, and `_run_abi3_audit`'s own
      abi3-precondition check (`--abi3` targeting a binary that isn't a
      recognisable CPython extension module, unrelated to any depth pin)
      -- and the JSON envelope this wrapper reads carries only the verdict
      string, not which raise site fired. The `::error::` annotation, job
      summary, and final-exit message all named the depth/evidence cause
      unconditionally, misdiagnosing the abi3 case. Fixed by making all
      three generic (naming the axis -- "this scan's evidence contract
      could not be satisfied" -- and pointing at the command's own error
      message for the specific cause) rather than picking one cause to
      describe; `action.yml`'s own verdict description updated the same
      way. This one needed no adversarial-input analysis, unlike the
      JSON-sidecar-condition gaps above -- it is a wording correction, not
      a new signal to parse. **A fifth review round (Codex, fresh
      evidence) caught the identical narrow framing surviving in the
      adjacent `exit-code` output description and the changelog
      fragment** -- both still named only the pinned-depth cause for the
      scan exit-1 axis, and a leftover `action/run.sh` comment (the exit-1
      dispatch's own "four possible sources" note) did too. Fixed the same
      way: named the axis generically, listed both raise sites where a
      concrete example was still useful, and swept the rest of this PR's
      own diff for the same narrow phrasing rather than waiting for a
      sixth round to find the next copy. **A sixth review round (Codex,
      fresh evidence) found a fourth uncovered escape hatch in the same
      JSON-detection family, distinct from the three the `verdict`
      description already names:** `_STDOUT_JSON_FILE` (the primary-output
      stdout capture immediately above `_json_report_src`) is gated on
      `"${FORMAT:-}" == "json"` -- this Action's own `format` input, read
      *before* the CLI runs -- not on what the invocation actually
      produced. `extra-args: --format json` under `format: text` makes the
      real CLI invocation emit JSON on stdout (the later flag wins, same
      as any CLI argv), but the wrapper's own capture never notices,
      since it never re-derives the effective format from the actual
      command it built. Unlike the fifth round, this is a real detection
      gap, not wording -- but it is the same *shape* of gap as the
      `--write`-collision findings (three and five), so it gets the same
      treatment for the same reason: teaching this wrapper to track an
      effective-format override needs its own parsing of `extra-args` and
      its own hostile-input test, the bar the first `_evidence_contract_
      gated` addition was already held to, and this PR would rather record
      an accurate limitation than add that under-tested. Folded into the
      `verdict` description's existing generic pointer at
      `action/run.sh`'s own JSON-detection logic, now naming
      `_STDOUT_JSON_FILE` alongside `_json_report_src` and updating the
      combination count from three to four. **Fixed (2026-09-02):** the
      effective-format-override gap itself. A new `_effective_format` helper
      scans `INPUT_EXTRA_ARGS` the same word-splitting way
      `_extra_args_has_write_flag`/`_extra_args_write_json_path` already
      scan it for their own flag, keeping the last `--format`/`--format=`
      occurrence (Click's own last-wins precedence) and falling back to the
      nominal `$FORMAT` when extra-args carries none; computed once, into
      `$_EFFECTIVE_FORMAT`, right after extra-args are appended to `CMD`.
      Both sites this section names now gate on that value instead of the
      bare `$FORMAT`: the stdout-JSON capture (`_STDOUT_JSON_FILE`) and
      `_json_report_src`'s `OUTPUT_FILE` branch, each falling back to
      `${FORMAT:-}` when `$_EFFECTIVE_FORMAT` is unset so the several
      isolated-snippet tests that extract `_json_report_src` without
      running the real command-assembly section keep behaving exactly as
      before (`tests/test_action_run_sh_helpers.py::TestEffectiveFormat`,
      `tests/test_action_run_sh_pr_json.py`). **A same-PR review round
      (Codex, fresh evidence) found a third site of the identical class**:
      `_text_report_content` -- the text-report counterpart
      `_severity_gate_exit`/`_severity_gate_categories` both read through --
      gated on the bare `$FORMAT` too, so a `format: json` step whose own
      `extra-args` overrode to `--format text` (with `output-file` set)
      wrote real text to `$OUTPUT_FILE` that this function still refused to
      read, silently losing the severity-gate line and publishing the
      generic `ERROR` instead of `SEVERITY_ERROR`. Fixed the same way --
      gated on `${_EFFECTIVE_FORMAT:-${FORMAT:-}}` (`tests/
      test_action_run_sh_helpers.py::TestTextReportContentEffectiveFormat`).
      **A fourth review round (Codex, fresh evidence) found the general-
      purpose `$_EFFECTIVE_FORMAT` computation itself ran too late for two
      of its own consumers**: it was computed once, after `extra-args` is
      appended to `CMD` -- but compare and scan mode's own `PR_JSON`
      sidecar-injection decisions (`--write json=$PR_JSON`, added when the
      primary format isn't already JSON) run *earlier*, inside each mode's
      own block, and still checked the bare `$FORMAT`. A `format: json`
      step whose own `extra-args` overrode to a non-json format skipped the
      injection (nominally "already JSON, no secondary needed") while the
      real run produced no JSON at all -- the mirror image of the gap
      `_STDOUT_JSON_FILE` had, one step earlier in the pipeline. Fixed by
      computing `$_EFFECTIVE_FORMAT` a second time, right after each mode's
      own `$FORMAT` is set (idempotent with the later, general-purpose
      computation, which still covers every mode with no injection decision
      of its own), and gating both injection sites on it
      (`tests/test_action_run_sh_compare_pr_json_write.py::
      TestCompareDoesNotInjectALosingWrite::
      test_extra_args_overriding_json_away_still_injects_a_write`,
      `tests/test_action_run_sh_scan_pr_json_write.py`, a new module mirroring
      the compare-mode one). **A fifth review round (Codex, fresh evidence)
      found the fix had stopped one layer short: report *detection* was
      covered, report *rendering* was not.** The step-summary "Format" row
      and its "Full report" markdown-vs-code-fence decision (whether the raw
      output embeds as rendered Markdown or inside a ` ``` ` fence) still
      read the nominal `$FORMAT`, so a `format: json` step overridden to
      `--format markdown` mislabeled the summary row and embedded real
      Markdown output inside a code fence, and the reverse override embedded
      raw JSON as if it were Markdown. Fixed by gating both on
      `${_EFFECTIVE_FORMAT:-${FORMAT:-markdown}}` too (`tests/
      test_action_run_sh_summary.py::
      TestStepSummaryFullReportFencingUsesEffectiveFormat`). **Also
      identified, and deliberately deferred rather than fixed in this PR**
      (recorded in `docs/contribute/known-gaps.md`): (1) CodeRabbit review,
      fresh evidence — none of `_effective_format`'s three siblings
      (`_extra_args_has_write_flag`, `_extra_args_write_json_path`) or the
      real `CMD` assembly (`CMD+=($INPUT_EXTRA_ARGS)`) disable pathname
      expansion when splitting `INPUT_EXTRA_ARGS`, so a crafted `extra-args:
      '*'` in a workspace containing a flag-shaped filename could inject an
      unintended argument — real, but `_effective_format` deliberately
      matches its siblings' and the real command's own (equally unsafe)
      splitting on purpose, so hardening only the newest of the four sites
      would introduce a detection/execution divergence rather than close
      one; the fix needs all four sites (plus a hostile-glob test corpus)
      changed together. (2) Codex review, fresh evidence — extra-args
      supplying its own `-o`/`--output` (a different flag than `--format`,
      with no existing "effective value" helper the way `--write` has
      `_extra_args_write_json_path`) can point the real primary report at a
      path this script's `$OUTPUT_FILE` tracking never learns about,
      leaving `_json_report_src` with nothing to find; closing this
      properly needs a new `_effective_output_file` helper with the same
      freshness/fingerprint discipline `_json_report_src` already applies
      to `$OUTPUT_FILE`, not a narrow patch to one call site. Still open:
      the release fan-out's `GateOptions` unification, the typed-API half of
      this parity pass, the `--format text` gap named above, and a real
      `--artifact-set` member-level evidence-contract signal for the Action
      to consume.

      **A CI-infrastructure fix, not a review finding:** the new test
      file's own malicious-fixture test (and its siblings) passed their
      generated bash script via `subprocess.run([bash, "-c", script])`,
      which failed on `windows-latest` CI with a bash parse error
      (`unexpected EOF while looking for matching \`)'`) -- Windows
      reconstructs that argv via `list2cmdline` (MSVCRT quoting rules) and
      Git Bash's own MSYS runtime then re-parses the resulting command
      line with its own, not-quite-identical rules, corrupting this file's
      large, quote-heavy scripts. An earlier revision patched only the
      interpolated paths with `Path.as_posix()`; that addressed a
      narrower instance of the same class and left this one, confirmed
      still failing with the identical error text. Fixed by porting the
      same fix two sibling test modules already use for this exact class
      of gap (`test_action_run_sh_helpers.py`'s `_run_harness`,
      `test_action_run_sh_py_safe_path.py`'s `_run_bash_script`): write
      the script to a real file and run `bash <path>`, which needs no
      argv reconstruction at all.

      **A seventh review round (Codex, fresh evidence) found a real test-
      coverage gap, not a wording or detection gap:** every exit-1
      dispatch test in the new test file stubs `_evidence_contract_gated`
      out entirely, and the one test executing the real
      `_report_query`/`_evidence_contract_gated` pipeline only supplied a
      near-miss verdict (expecting `GATED=0`) -- none of the five tests
      would have failed if that pipeline were broken to always return
      false, silently restoring the exact pre-fix misclassification for
      every genuine `EVIDENCE_CONTRACT_ERROR` report. Fixed by adding a
      positive-path test supplying the exact sentinel string through the
      same real, extracted pipeline (factoring the report-writing/script-
      assembly the hostile-value test already did into a shared helper so
      both tests exercise one pipeline, not two copies), verified to
      actually catch the regression the same way every malicious-fixture
      test in this PR has been.

      **An eighth review round (Codex, fresh evidence) found a real
      correctness/cost bug in pre-existing, untouched-by-this-PR code
      this PR's own comment had overclaimed about:** `_can_reuse_primary_
      json` (the sticky-PR-comment JSON acquisition decision) requires
      `$FORMAT == "json"` before it will reuse an already-produced report
      -- so a `format: text`/`markdown` run whose own extra-args supplied
      `--write json=PATH` (exactly the faithful, unfiltered report
      `_json_report_src`'s `_extra_write_json_path` branch already
      trusts, and exactly what let `_evidence_contract_gated` classify
      the verdict correctly in the first place) is rejected anyway,
      forcing `_maybe_post_pr_comment` into a full rerun despite the JSON
      already sitting on disk. For the abi3 `_EvidenceContractError` raise
      site specifically, that rerun happens *after* real candidate-
      snapshot extraction -- not the cheap, precondition-only rerun this
      PR's own `EVIDENCE_CONTRACT_ERROR`-doesn't-get-`BUDGET_OVERFLOW`'s-
      skip comment (added in the very first slice above) claimed for
      every raise site. Unlike the documentation-precision rounds above,
      this is fixed in code rather than recorded as a gap: dropped the
      blanket `$FORMAT == "json"` requirement and let `_can_reuse_primary_
      json` rely purely on `_json_report_src` (whose own per-branch
      gating already restricts a non-`format:-json` trust to exactly the
      `_extra_write_json_path` case) -- a minimal, general fix to the
      shared acquisition helper every mode's PR comment goes through, not
      a special case for this one verdict. Corrected the now-inaccurate
      "rerun is cheap" framing in the earlier comment to match: reaching
      `EVIDENCE_CONTRACT_ERROR` already proves a report exists, so with
      this fix the reuse-or-rerun fallthrough never actually reruns for
      this verdict at all. New test:
      `test_reuses_extra_args_write_json_sidecar_under_a_non_json_format`
      in `tests/test_action_run_sh_pr_json.py`, verified to catch the
      regression the same way.

      **A ninth review round (Codex, fresh evidence) found the mirror
      image of the sixth round's finding, falsifying the one guarantee
      the `verdict` description still asserted:** "This Action's own
      `format: json` input always qualifies" was itself wrong -- `extra-
      args: --format text` under `format: json` overrides the effective
      invocation to text output the same way `--format json` under
      `format: text` (the sixth round's finding) overrides it to JSON;
      neither direction is detected specially, so `format: json` alone
      guarantees nothing about what `_json_report_src`/`_STDOUT_JSON_FILE`
      actually find. This is the fifth successive round to find one more
      uncovered combination in a *claim* this description made about
      when a JSON report exists, even after the fifth round (see above)
      already tried retreating from enumeration to a single narrower
      claim -- proof that the narrower claim was still an enumeration,
      just of one case instead of several. Fixed by removing the "always
      qualifies" guarantee entirely rather than adding a sixth caveat:
      the description now states plainly that the *effective* invocation
      decides, not any one input considered alone, and points at
      `action/run.sh`'s own logic with no shortcut claim standing in for
      it. No code change and no new test -- this is prose accuracy only,
      the same class the fourth round already established needs neither.
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
  (this ADR — stage 1a landed, stage 1b partially landed per this ADR's own
  status header above, stage 2 not yet implemented) as a pair.
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
