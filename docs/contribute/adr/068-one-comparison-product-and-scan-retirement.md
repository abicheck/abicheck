# ADR-068: One Comparison Product — Retiring `scan` and Consolidating the CLI Conceptual Model

**Date:** 2026-09-06
**Status:** Proposed — not implemented. Supersedes
[ADR-056](056-multi-artifact-library-set-scan.md) outright and amends
[ADR-037](037-cli-interface-contract.md) D5/D7/D12,
[ADR-040](040-compare-surface-reduction.md) Lever 3,
[ADR-043](043-cli-pre-1.0-surface-reset.md) D1/D5/D9/D10, and
[ADR-047](047-github-actions-integration-model.md) §8's S5 routing. Aligns
with, and does not reopen, [ADR-063](063-one-semantic-pipeline.md),
[ADR-064](064-canonical-gate-algorithm-and-exit-decision.md),
[ADR-065](065-comparison-scope-selection-and-completeness.md),
[ADR-066](066-longitudinal-history-and-versioning-policy.md) and
[ADR-067](067-change-intent-acknowledgment-and-disposition-audit.md).
Implementation is sequenced in
[`plans/one-comparison-product.md`](../plans/one-comparison-product.md),
which also carries the capability-by-capability retirement map, the flag
inventory, and the acceptance-test matrix.
**Decision maker:** maintainer (product decision recorded in
[`vision.md`](../vision.md)); technical sign-off pending review.

---

## Context

`vision.md` states the product as one thing: *make the evolution of a
supported surface visible, intentional, and traceable*, with **one model for
one component or many**. The CLI does not match that statement today.

Measured against `main` at `309c8a82` (Click introspection, not help text —
hidden options included):

| Command | Positional operands | Accepted options | Hidden |
|---|---|---|---|
| `compare` | `OLD_INPUT NEW_INPUT` | 78 | 4 |
| `scan` | `ARTIFACT` | 45 | 0 |
| `dump` | `SO_PATH` | 39 | 0 |
| `deps tree` / `deps compare` | `BINARY` | 7 / 8 | 0 |
| `aggregate` | `REPORTS_DIR` | 6 | 0 |
| `project` (4 subcommands) | varies | 3–8 each | 0 |
| `compat` | — | frozen legacy | — |

Three structural problems, in descending order of cost:

**1. `scan` is a second analysis product, not a mode of the first.** Its own
docstring calls it "a thin front-end over the existing `dump`/`compare`
engine", but it is not thin: it owns ~10,000 lines
(`cli_scan*.py`, `scan_engine.py`, `service_scan.py`, `workflows/scan_*.py`,
`frontends/cli/scan_*.py`, `pr_comment_scan*.py`), a **separate typed API**
(`ScanRequest`/`ScanResult`), a **separate JSON schema**
(`SCAN_SCHEMA_VERSION`, currently 1.23, with `compare`'s own result nested
under `diff`), a separate Action mode with ~40 branches in `action/run.sh`,
and 40 test modules. Most damagingly it owns **capabilities `compare` cannot
reach at all**: the eleven cross-source checks
(`buildsource/crosscheck.py` — `private_header_leak`, `public_not_exported`,
`exported_not_public`, `rtti_for_internal_type`, `odr_type_variant`,
`unversioned_exported_symbol`, `compile_context_conflict`,
`header_build_context_mismatch`, `source_surface_dso_mismatch`,
`public_to_internal_dependency`, `identity_collision_detected`), the lexical
pattern pre-scan (`buildsource/pattern_scan.py`), the preprocessor scan
(`buildsource/preprocessor_scan.py`), changed-path localization
(`--since`/`--changed-path`), and the `abi3` limited-API audit. Verified by
call site, not by import: the only production callers of `scan_files`,
`run_preprocessor_scan` and `run_crosschecks` anywhere under `abicheck/` are
three lines in `scan_engine.py`. (`workflows/extraction.py` *imports* the
first two, but only re-exports them — it never calls either, which is
exactly the kind of near-miss that makes an import-graph reading alone
untrustworthy here.) A user who runs `abicheck compare` on a pull request
**does not get these checks**, and nothing in the product tells them so.

That is the real defect. It is not "the CLI has too many verbs": it is that
the same evidence produces different findings depending on which command
the user happened to pick, and the command that owns the *comparison* — the
one the vision names as the product — owns the *smaller* set of checks.

**2. Cardinality has grown special surfaces.** `scan --artifact-set`
(ADR-056) is an audit-only, old-side-less, N-library mode with its own
manifest, its own coverage accounting and its own per-member exit
aggregation, existing in parallel with `compare`'s directory/package
fan-out, `bundle.py`'s ADR-023 bundle analysis, `BundleFacts`, and now
ADR-065's acquisition/selection model. Four answers to "several libraries at
once", of which only one is on the command the vision says owns comparison.

**3. Presentation is entangled with analysis.** `--explain-patterns`
*implies* `--pattern-verdicts`, so asking for an explanation changes the
verdict and can change the exit code. `--surface-metrics`,
`--show-filtered`, `--audit-suppressions` and `--pattern-verdicts` are
opt-in switches for analysis or accounting that is either free or already
required by the vision's own "record before disposing" rule. `--profile`
(ADR-040 Lever 3) bundles three unrelated axes — evidence depth, report
rendering, and CI gate policy — behind one word.

Alongside these, ~30 `compare`/`dump` options are stable *project*
properties (toolchain identity, debug-resolution settings, bundle topology,
declared contracts, deployment constraints) that a user retypes every run,
and a handful are internal storage/resource details
(`--max-json-object-nodes`, `--keep-extracted`, `--bundle-facts-out`).

### What this ADR must not break

Three current behaviors are load-bearing and are preserved verbatim, because
each was bought with real defect history:

- **ADR-065's scope semantics.** *Unselected*, *expected but not produced*,
  *failed*, and *deliberately retired* stay four states. A partial local
  build never reads as a removal, and a run that completed zero comparisons
  never reads as a pass.
- **ADR-049's contract-relevance prerequisites.** `--scope-public-headers`
  is not deleted in favor of `--contract public` until
  [`public-contract-default.md`](../plans/public-contract-default.md)'s two
  open relevance defects and two uncovered measurement lanes are closed. A
  possible false negative is never traded for a shorter CLI.
- **ADR-067/vision's disposition audit.** Detected and allowed remain two
  totals in every projection.

## Decision

### D1. The root command surface is six verbs, in three tiers

| Tier | Commands |
|---|---|
| **Primary** — the product | `compare`, `dump`, `deps` |
| **Advanced integration** — plumbing for CI and multi-target projects | `aggregate`, `project` |
| **Frozen legacy** | `compat` |

`scan` is **retired**, not reshaped. `tests/test_cli_root_surface.py`'s
pinned set becomes those six.

This supersedes ADR-043 D1 ("exactly five verbs", since grown to seven) and
D5 (`scan` reshape). ADR-054's admission bar for *new* root commands is
unchanged and still governs; this ADR only removes one.

### D2. One comparison product; baseline availability and cardinality are scope, not command

`compare` is the only command that analyzes evolution. `dump` captures
evidence into a reusable snapshot and never produces a compatibility
verdict. Everything else is scope:

| Scope | Spelling |
|---|---|
| One artifact vs. one artifact | `compare OLD NEW` |
| Snapshot vs. live artifact | `compare baseline.abi.json.zst build/libfoo.so` |
| Package/release directory vs. the same | `compare OLD_DIR NEW_DIR` (+ `--select`, ADR-065 S1) |
| Selected build variant | `--old-variant`/`--new-variant` over a stored `ProjectSnapshot` |
| **No prior surface exists** | `compare --no-baseline NEW` |

`--no-baseline` is the replacement for `scan`'s audit-only mode and for
ADR-047 §8's S5. It is an **explicit declaration**, never inferred from
argument count: `compare NEW` without it is a usage error (exit `64`), and
`compare --no-baseline OLD NEW` is a usage error too. This is the direct
answer to the "don't introduce a vague `check` whose semantics depend on
arity" non-goal.

An audit run is a comparison whose OLD side has the ADR-065 acquisition
state **`declared_absent`**. It therefore:

- reports every candidate-side finding the available evidence supports;
- reports the evolution axis as `not_evaluated`, not as "everything added";
- never emits an addition, removal, or compatibility verdict — `run_outcome`
  carries no compatibility contribution, exactly as `scan` without
  `--against` does today;
- keeps the assurance, coverage, disposition and completeness axes it would
  have in a two-sided run.

`scan --artifact-set` disappears with `scan`. An N-library audit is
`compare --no-baseline DIR`; an N-library comparison is `compare OLD NEW`
over directories or packages. Both resolve members through ADR-065's one
acquisition/selection model — ADR-056's parallel model is superseded, and
its one genuinely new capability (a declared member-identity/provider
manifest checked against a single set) becomes a `.abicheck.yml` contract
read by the same path, not a mode.

### D3. Candidate-side checks become comparison stages, reported as evolution

Every check `scan` owns runs inside `compare`'s pipeline, over each side
independently, and is reported through the canonical `ReportDocument` with
an explicit evolution state:

| State | Meaning |
|---|---|
| `introduced` | Absent on OLD (with OLD evidence sufficient to say so), present on NEW |
| `resolved` | Present on OLD, absent on NEW |
| `persistent` | Present on both |
| `not_evaluated` | The side's evidence could not answer the question |

The `not_evaluated` state is mandatory, not a convenience. A private-header
leak that exists in both releases must not be reported as *introduced* merely
because the baseline was a stripped snapshot with no headers. This is the
vision's "weaker evidence narrows conclusions" rule applied to one-sided
checks, and it is the single largest correctness risk in the migration — so
it is an acceptance test (F-7/F-8 in the plan), not a note.

Authority is unchanged (ADR-028 D3 / ADR-035 D1): these findings stay
`RISK`/`API_BREAK` and never become `BREAKING` on their own. Migrating them
into `compare` does not promote them.

Checks that are meaningful only on the candidate (the `abi3` limited-API
audit, absolute surface metrics) appear as candidate-side enrichment **in
the same result document**, marked as such. They do not create a second
result.

### D4. Presentation never changes analysis

The invariant, stated so it can be tested: *for a fixed set of operands,
evidence inputs, and policy/contract configuration, the canonical result —
facts, compatibility verdict, assurance, disposition ledger, gate decision,
and exit code — is byte-identical regardless of `--format`, `--write`,
`--view`, demangling, or any filter.*

Consequences:

- `--explain-patterns` no longer implies `--pattern-verdicts`. Pattern
  modulation becomes automatic where its evidence exists (it is already
  evidence-gated), and explanation becomes pure rendering over the recorded
  modulation. A user cannot change a verdict by asking why.
- `--surface-metrics`, `--show-filtered`, `--show-suppressed` and
  `--audit-suppressions` stop being switches. The underlying accounting is
  computed always (ADR-067 S1 already made the disposition ledger
  unconditional); what remains is a *rendering* choice.
- `--report-mode`, `--show-only`, `--explain-patterns` and `--demangle`
  merge into one repeatable projection concept (`--view`), because they are
  four spellings of "which parts of the canonical result do I want rendered,
  and how".
- `--write` becomes **repeatable** (`--write json=result.json --write
  markdown=summary.md`), so one analysis yields every artifact a job needs.
  A complete machine-readable result must always be obtainable without a
  second run; `--max-findings`-style truncation knobs are therefore removed
  rather than migrated.

### D5. Per-invocation operands on the CLI; stable properties in the project contract

A CLI option must be a **decision or an operand that legitimately varies per
run**. Stable properties of a project — toolchain identity and cross-compile
setup, debug-resolution policy, bundle topology, declared contracts,
deployment constraints, resource budgets — move to `.abicheck.yml`.

Three guards on that migration, each targeting a way it could go wrong:

1. **Not one-for-one.** Options are re-expressed in the config's own
   vocabulary, merged where they are the same concept. `--compiler-prefix`
   does not become `compile.compiler_prefix`; it disappears into
   `compile.compiler`.
2. **No escape hatch.** There is no `--set internal.path=value`.
3. **Experiments stay cheap.** Real per-run *evidence* inputs — `-H`,
   `-I`, `--sources`, `--build-info`, `--depth`, `--used-by`,
   `--required-symbol`, `--debug-info`, `--devel-pkg` — stay on the CLI. A
   user must never have to author a project config to compare two files.

Hidden-but-accepted options count as public surface and are inventoried and
removed, never merely hidden. `--profile` (ADR-040 Lever 3) is removed: it
bundles evidence depth, report rendering and CI gate policy, which D4 and
this decision separate.

### D6. `deps` stays, with its own question stated and its engine converged

`deps` answers a **runtime/deployment** question that `compare` does not,
and the distinction is the operand, not the cardinality:

- `deps tree BINARY` — *which transitive shared libraries will the dynamic
  loader actually resolve for this binary in this environment, and are its
  required symbols bindable?* Operand: one binary plus a search
  configuration.
- `deps compare BINARY --old-root R1 --new-root R2` — *will this consumer
  still load and work, from an ABI/loadability standpoint, when its complete
  deployment environment changes from R1 to R2?* Operands: a consumer, and
  two **environments**.

`compare OLD NEW`'s operands are two *surfaces* of the same component.
Neither reduces to the other, so `deps` is not merged into `compare`, and
reducing the root command count is explicitly not a reason to try.

Convergence requirements (architecture, not surface):

- per-library ABI comparison inside `deps compare` must go through the
  canonical engine — it already does, via `service.run_dump`/`compare` in
  `stack_checker._run_abi_diff`; that must not regress;
- `StackVerdict`'s independent taxonomy and `cli_stack.py`'s hand-rolled
  `0/1/4/5` exits are re-expressed as `RunOutcome`/`ExitDecision` axes
  (ADR-064), so loadability becomes an operational axis rather than a
  parallel verdict vocabulary;
- its report becomes a `ReportDocument` projection (ADR-061/ADR-036);
- no compatibility taxonomy, policy engine, or suppression mechanism may
  evolve inside `deps`.

A later unification is recorded as **future direction only**, conditional on
a clean operand model (most plausibly: an environment as a first-class
comparison scope). This ADR does not invent one.

### D7. `compat` is frozen

`compat` is a frozen legacy ABICC adapter. No new product features, no
cleanup for aesthetics, no renames. Changes only for correctness, security,
or to keep it working after canonical internal engine changes; its internals
may delegate to newer engines as long as externally visible behavior is
stable. Its option count is excluded from every target and metric in this
work.

### D8. Hard removal, no deprecation window

Consistent with ADR-043 and PR #770, and because the project is pre-1.0:
removed spellings are deleted outright and exit `64` with `No such option`.
No hidden aliases, no shims, no silent ignoring of explicitly supplied
input. The one compatibility obligation is to the **GitHub Action**, whose
inputs are versioned separately: `mode: scan` is migrated to
`mode: compare` (+ `baseline-channel: none` where there is no baseline)
inside the Action, so a user's workflow YAML keeps working while the CLI
underneath changes — and the Action's own `scan` mode is then removed in its
next major, per ADR-047's own input-lifecycle rules.

### D9. Deletion follows callers, never precedes them

No `scan`-specific module, schema, or test is deleted until its capability
has a proven home in `compare` and every caller (Action, docs, typed API,
examples) has moved. The plan's phase order is normative, and the parity
suite in its Phase 3 is the gate: `scan`'s removal PR may not land while any
scenario in the acceptance matrix shows `compare` producing fewer or weaker
findings than `scan` did on the same inputs.

## Consequences

**Good.** One analysis command, one result model, one schema, one Action
mode family. The eleven cross-source checks, the pattern and preprocessor
scans, and changed-path localization become available to every `compare`
user — a capability *gain* for the majority of users, who never ran `scan`.
The largest single body of duplicate CLI/service/report/Action code in the
repository is deleted. The conceptual model becomes explainable without
L0–L5 names, `BundleFacts`, or `ScanRequest`.

**Costly.** This is a large, multi-PR migration touching the CLI, the typed
API, the Action, every report projection, and ~40 test modules. Two schema
majors are likely: one when the one-sided checks enter `compare`'s
`ReportDocument`, one when `SCAN_SCHEMA_VERSION` is retired. Users and CI
pipelines invoking `abicheck scan` directly must change their command line;
the Action absorbs this for Action users, the CLI does not for CLI users.

**Risk, and its mitigation.** The dominant risk is silent capability loss
during migration — a check that ran under `scan` and now runs nowhere. D9's
ordering and the acceptance matrix exist for exactly this; the parity suite
runs both commands over the same fixtures and diffs the finding sets while
both still exist. The second risk is the `not_evaluated` correctness
question in D3: reporting a pre-existing hygiene problem as newly introduced
would be a manufactured finding, which the vision forbids outright.

## Amendment (2026-09-09): baseline cross-source authority divergence

Phase 4 landed the migration this ADR's D3 requires (every cross-source
check reaches `compare`'s automatic `cross_source_checks` stage, ADR-068
D4/D5 — no opt-out), but a parity audit found the Action's scan-to-compare
translation (`action/run.sh`) had to be disabled entirely
(`9f2166e5c`) because of a divergence D3 does not leave any room for: on a
**baseline** comparison, `scan --against`'s own baseline path
(`cli_scan_baseline._strip_automatic_cross_source_findings`) removes every
cross-source finding from `diff.findings` before computing its verdict,
while `compare` leaves the same finding in `changes` as real, gating
evidence. Directly verified: self-compared `case143_audit_accidental_export`
(`exported_not_public`) reports `NO_CHANGE` under `scan --against` and
`COMPATIBLE_WITH_RISK` under `compare`; under `--severity-preset strict`,
`scan --against` still exits 0 while `compare` exits 2.

**This is `scan`'s stripping that was wrong, not `compare`'s gating.** D3
already states the answer this amendment makes explicit: "Authority is
unchanged (ADR-028 D3 / ADR-035 D1): these findings stay `RISK`/`API_BREAK`
and never become `BREAKING` on their own. Migrating them into `compare` does
not promote them." A cross-source finding was never advisory-only under
ADR-028/ADR-035's own authority rule — `scan`'s
`_strip_automatic_cross_source_findings` (added when `scan`'s dedicated
`crosscheck` report block and `--crosscheck KEY=error` promotion were its
*only* mechanism for these checks, predating their migration onto
`compare`'s pipeline) was a `scan`-specific accommodation for a
single-version-only signal that no longer describes what these checks are:
migrated, dual-sided, `RISK`/`API_BREAK`-bucketed findings like any other
`compare` finding. Option (a) — giving `compare` an opt-out to match
`scan`'s stripping — is rejected outright: D4/D5 ("no opt-out for a real
front end") forbids exactly that, and D4's own invariant ("the canonical
result ... is byte-identical regardless of `--format`, `--write`, `--view`
...") would be violated by a second, parallel gating pipeline for the same
finding kind.

**Decision:** `scan --against`'s baseline path stops stripping cross-source
findings to advisory-only. A baseline `scan` now reports and gates a
cross-source finding exactly as `compare` does — same verdict, same
severity, same exit-code contribution. **This is a documented breaking
change to the Action's `mode: scan` contract and to the `scan --against` CLI
command**: a baseline scan that previously reported `NO_CHANGE`/exit 0 for a
library with an accidental export, an unversioned exported symbol, or any
other cross-source-only issue now reports the finding as a real,
policy-gated result, which can raise its verdict and (under a severity
preset that treats `RISK`/`API_BREAK` as error-level) its exit code. No
ADR-068 decision above changes as a result — this closes an implementation
gap D3 already ruled on, it does not reopen D3 itself.

`action/run.sh`'s `_SCAN_NEEDS_LEGACY_CLI` predicate's severity-divergence
condition (the "unconditionally true" catch-all `9f2166e5c` added) is closed
by this fix and removed. Its other, narrower conditions — `--budget`,
`--risk-rules`, `--crosscheck`, `--build-target` (no `compare` flag
equivalent), risk-driven `auto` depth selection (no `compare` equivalent),
an explicit `--depth build`/`--depth source` (`compare`'s single-pair path
has no equivalent evidence-contract floor — a pinned build/source depth with
no evidence to satisfy it is a real, reported error under `scan`, not a
silent downgrade, and `compare` has nothing that reproduces that contract),
`--header`/`--include` vs. baseline-side duplicates, a `.json`-extension or
compressed (`.gz`/`.zst`)/content-detected JSON snapshot baseline file,
`--output-file`, an effective (dedicated- or `extra-args`-supplied)
non-JSON/text `--format`, a `-o`/`--output` output path via `extra-args`,
a `--write`/scan-only `extra-args` flag (`--abi3`,
`--frontend-context`, `--allow-ast-frontend-fallback`, and the rest of the
compile-context-option family included), and a default (or explicit
`--no-pattern-verdicts`) baseline scan reaching `compare`'s own pattern-
verdict modulation, which has been unconditional since D4 with no flag left
to turn it off (`scan --against` still defaults it off; only an explicit
bare `--pattern-verdicts` in `extra-args` — matching `compare`'s forced-on
behavior exactly — is safe to route) —
are **independent, still-open capability gaps**, not instances of this
divergence; `_SCAN_NEEDS_LEGACY_CLI` keeps routing those cases to the legacy
CLI until each grows its own `compare` equivalent (tracked in
`docs/contribute/known-gaps.md`). This amendment closes exactly the D3
authority gap it investigated; it does not claim the Action's `scan`
translation is now unconditional.

## Alternatives considered

**Keep `scan`, make `compare` call into it.** Rejected: it preserves two
result models and two schemas, which is the actual cost, and leaves the
product with two names for one question.

**Rename `scan` to `audit`.** Rejected: the problem is not the name. A
one-sided audit and a two-sided comparison share evidence collection, policy,
disposition, assurance and reporting; splitting them by command duplicates
all five.

**Fold `scan`'s checks into `dump`.** Rejected explicitly by the product
decision: `dump` is evidence capture. A `dump` that emits findings becomes
the one-sided analysis command under a different name, and snapshots stop
being pure reusable evidence.

**Delete the one-sided capability entirely.** Rejected: ADR-047 §8's S5 (a
project with no baseline yet) is a real, distinct, validated user scenario,
and the cross-source checks are among the findings users act on most.

**Merge `deps` into `compare`.** Rejected for now (D6): the operands differ
in kind, and forcing them together to reduce a command count is the sort of
argument-count-dependent semantics this work is meant to eliminate.

## Relationship to existing ADRs

| ADR | Effect |
|---|---|
| [037](037-cli-interface-contract.md) | Amended: D5's depth dial keeps its vocabulary but now has one owner (`compare`/`dump`); D7's consolidation bar extends to removing a verb; D12's declared exit scheme is unchanged (ADR-064 already made it automatic) |
| [040](040-compare-surface-reduction.md) | Amended: Lever 1 (side-aware flags) and Lever 2 (config demotion) are extended and continued; **Lever 3 (run profiles) is reversed** — `--profile` is removed |
| [043](043-cli-pre-1.0-surface-reset.md) | Amended: D1's verb set becomes six; D5 (`scan` reshape) is superseded by retirement; D9's shared `--dry-run` model and D10's typed-API vocabulary follow |
| [047](047-github-actions-integration-model.md) | Amended: §8's S5 routes to `compare --no-baseline`; the Action's `scan` mode is absorbed then removed on its own input lifecycle |
| [054](054-cli-project-integration-surface-consolidation.md) | Unchanged; its admission bar still governs new root commands |
| [055](055-typed-request-result-completeness-and-schema-registry.md) | Amended: `ScanRequest`/`ScanResult` leave the registry; `CompareRequest`/`CompareResult` absorb their fields |
| [056](056-multi-artifact-library-set-scan.md) | **Superseded** by this ADR |
| [063](063-one-semantic-pipeline.md) | Aligned: this removes one of the two pipelines Phase 1 was converging |
| [064](064-canonical-gate-algorithm-and-exit-decision.md) | Aligned: `scan`'s budget/evidence-contract/not-comparable axes move onto `compare`'s `ExitDecision` unchanged |
| [065](065-comparison-scope-selection-and-completeness.md) | Aligned and depended on: `declared_absent` joins its acquisition vocabulary; `--artifact-set` members become its selection model |
| [008](008-full-stack-dependency-validation.md) | Amended: D6 states `deps`' user question and its convergence requirements |
| [049](049-contract-relevance-and-compatibility-configuration.md) | Unchanged and protected: contract-mechanism consolidation waits on its own correctness prerequisites |

## References

- [`vision.md`](../vision.md) — product direction
- [`plans/one-comparison-product.md`](../plans/one-comparison-product.md) —
  retirement map, flag inventory, migration sequence, acceptance tests
- [`plans/vision-api-abi-evolution.md`](../plans/vision-api-abi-evolution.md)
  — the result-semantics workstreams this is subordinate to
- `plans/index.md` — cli-cleanup-phase-two, the interface-hygiene
  predecessor, is retired there
