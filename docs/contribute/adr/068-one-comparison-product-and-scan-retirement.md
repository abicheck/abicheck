# ADR-068: One Comparison Product — Retiring `scan` and Consolidating the CLI Conceptual Model

**Date:** 2026-09-06
**Status:** Accepted — Phase 6 (`scan` retirement) has landed: the `scan`
root command is deleted outright (`abicheck scan` exits 64, naming
`compare`/`compare --no-baseline` as the replacement), its scan-only
modules and tests are deleted, and `tests/parity/` is now a compare-only
regression corpus. Of the three flag demotions Phase 6 unblocked,
`compare --require-complete-analysis` has since landed
(`assurance.require_complete` in `.abicheck.yml`, rulings.py
deferred-option followup), and `dump --build-target` is now implemented too:
`frontends/cli/options/rulings.py`'s deferred ruling recorded its only real
blocker as `--build-target` still being a live `scan` option (`plans/
one-comparison-product.md`'s "One deferral's blocker re-attributed" note);
with `scan` deleted, that blocker is gone and the flag is removed outright
(old spelling exits 64, no alias; `.abicheck.yml`'s `build.targets` is the
only front-end-reachable source now). `compare --env-matrix` remains open,
the one unimplemented followup — see
`docs/contribute/plans/one-comparison-product.md`'s Phase 6 section for the
exact scope and what stayed a documented gap. Supersedes
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
(`buildsource/cross_source_checks.py` — `private_header_leak`, `public_not_exported`,
`exported_not_public`, `rtti_for_internal_type`, `odr_type_variant`,
`unversioned_exported_symbol`, `compile_context_conflict`,
`header_build_context_mismatch`, `source_surface_dso_mismatch`,
`public_to_internal_dependency`, `identity_collision_detected`), the lexical
pattern pre-scan (`buildsource/pattern_facts.py`), the preprocessor scan
(`buildsource/preprocessor_facts.py`), changed-path localization
(`--since`/`--changed-path`), and the `abi3` limited-API audit. Verified by
call site, not by import: the only production callers of `find_pattern_facts`,
`collect_preprocessor_facts` and `run_crosschecks` anywhere under `abicheck/` are
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

## Amendment (2026-09-09, Phase 4 commit 2): closing `_SCAN_NEEDS_LEGACY_CLI` — per-condition rulings

The maintainer's explicit re-scoping for this commit: **the Action does not
keep a compatible interface with `scan`'s own CLI surface.** A `scan`-only
capability with no `compare` equivalent is retired along with the legacy
routing branch, not silently kept alive by leaving `_SCAN_NEEDS_LEGACY_CLI`
in place for it. Every condition that predicate checked is ruled below —
(a) **already covered** by `compare` today (verified against a real built
pair, not `--help` text), (b) **dropped**, a documented breaking change to
`mode: scan`'s own contract, or (c) **genuinely required**, and implemented
on `compare` in this commit. The bar for (c), restated from the task that
produced this amendment: a capability qualifies only if dropping it would
trade a possible *false negative* for a shorter CLI — not convenience.

| Condition | Ruling | Reasoning |
|---|---|---|
| `--depth build`/`--depth source` with no reachable evidence (exit 7) | **(c) — implemented** | The one condition the amendment above already flagged as a real, undocumented gap distinct from every other row: `compare --depth build` on a pair with no `--sources`/`--build-info` silently fell back to symbols-only evidence and reported `NO_CHANGE`/exit 0 — verified live (`compare --depth build old.so new.so` with a header but no build evidence). `workflows.artifact.execute.enforce_requested_depth` already implements this exact floor as a hard `ValidationError`/exit 64, but only for the typed-API composition (`resolve_compare_request`) — the native CLI's own resolution (`cli_resolve._resolve_compare_snapshots`) never called it. `policy/depth_evidence_contract.py` (new) recomputes the identical floor (`evidence_depth.depth_rank`/`gated_source_label`, the same primitives) and records it as ADR-064's exit-7 axis (`DiffResult.evidence_contract_error`) instead of raising, mirroring `workflows.abi3_audit.record_abi3_evidence_contract_error`'s existing pattern for the `--abi3` case — wired into both the native CLI (`cli_compare_helpers.run_compare`) and the typed pipeline (`service_compare_pipeline.classify_compare_pair`, defense-in-depth alongside the pre-existing hard fail). `compare --depth build old.so new.so` (no evidence) now exits `7`, verified live. **Live extraction only, not a pre-built snapshot's own embedded evidence:** the floor fires only for a side `compare` itself resolves from a live binary (ELF/PE/Mach-O) this run's own extraction under-collected — a side that is already a serialized JSON snapshot was never (re-)extracted by this run at all, so there is nothing to blame it for; `record_depth_evidence_contract_error`'s `old_is_live`/`new_is_live` parameters carve this out explicitly rather than reproducing `enforce_requested_depth`'s own narrower, pre-existing behavior (which does not make this distinction — a known, separately-documented limitation, `docs/contribute/known-gaps.md`'s "`--depth` is a floor for live extraction, not a ceiling for a pre-built snapshot" entry) newly onto the native CLI path, which had never enforced any floor at all before this commit. Caught by `tests/parity/test_source_depth_parity.py`'s real case192 fixture (a stored-snapshot pair where the NEW side genuinely has no embedded build evidence) during verification — without the carve-out, a real BREAKING finding that `scan --against` correctly reports at exit 4 would have regressed to exit 7 on `compare`, which would itself have been exactly the kind of manufactured-by-migration divergence this whole commit exists to avoid. |
| `--budget` (§3 #19) | **(c) — implemented** | The other condition the task explicitly named as a real (c) candidate: an unbounded run is a real CI risk (opaque runner kill instead of a clear, actionable exit code). `compare` gained a `--budget` option (`frontends/cli/commands/compare.py`) parsed the same way `scan`'s always was (`15m`/`900s`/`1h`, bare number = seconds) and enforced via `deadline.deadline_scope` — the same ambient, contextvar-based mechanism `scan_engine.py` already used, which every deadline-aware subprocess call already downstream of it (castxml/clang extraction, the preprocessor scan, source replay) already consults without further plumbing. Entered around both the resolve and the classify phase, with the *remaining* budget (not the full duration again) passed to the second entry, so a slow resolution cannot silently grant classification a second full budget. Overflow sets `DiffResult.budget_overflow` (exit 5, ADR-064's existing axis) via the native CLI's own catch of `deadline.DeadlineExceeded`; the typed API (`CompareRequest.budget_s`, `service_compare_pipeline.run_compare_request`) raises the same exception rather than fabricating a partial result, since a typed caller can already catch it. Verified live: `compare --budget 0s -H foo.h old.so new.so` exits `5`. **Known, accepted narrowing versus `scan`'s own budget guard:** `scan_engine.py`'s per-stage checks additionally bounded fine-grained sub-steps within a single collection pass; this implementation bounds the two coarser phases (resolve, classify) `compare`'s own architecture actually separates. Still a real, enforced ceiling — recorded in `docs/contribute/known-gaps.md` as an accepted granularity difference, not a capability gap. |
| `--risk-rules` (§3 #14) and risk-driven `auto` depth selection (§3 #13) | **(b) — dropped** | Omitting `--depth` on `compare` already deterministically defaults to `headers` — never narrower than what a *non*-risk-driven `scan` run would have used, so dropping the risk-scoring auto-escalation is not a new false negative relative to `compare`'s own existing, already-shipped default; it is the removal of a convenience that used to *sometimes* escalate depth further than the default, never a removal of a floor. A CI job that wants source-level assurance on every run must now pin `--depth source` explicitly rather than rely on risk scoring inferring it — a documented breaking change to `mode: scan`'s implicit depth selection when `depth` is left unset, not a silent capability loss (the Action now requires an explicit `depth` input, or accepts the same fixed `headers` default `compare` always used). |
| `--crosscheck KEY=error` promotion syntax (§3 #23) | **(b) — dropped, superseded** | All eleven cross-source checks already reach `compare` as ordinary `ChangeKind`s (§3 #3, landed), so the underlying capability — controlling a specific check's severity — already exists via `--policy`/`.abicheck.yml`'s `policy.overrides` (a `ChangeKind`-keyed mechanism every one of these checks already has a registry entry for). The `KEY=LEVEL` *syntax* itself does not survive; `policy.overrides.<CHANGE_KIND>: error` is the replacement spelling. A documented breaking change to `mode: scan`'s `crosscheck` input's promotion syntax — the dedicated `crosscheck` report block itself was already scan-only surface with no compare equivalent, and disappears with the mode. |
| `--build-target` (no `compare` flag equivalent) | **(b) — dropped** | `scan --build-target` is `dump --build-target`'s own CLI equivalent (`BuildEvidence.target_scope`), an advanced knob narrowing which build target's evidence collection uses when a compile database names several — not a correctness floor: omitting it does not silently narrow what `compare` reports below its own ordinary (all-targets) default, it only foregoes a precision narrowing a minority of multi-target libraries need. `.abicheck.yml`'s `build.targets` (§4.2's own CONFIG classification) was, at the time of this ruling, the eventual config-level replacement, but that key was not implemented for either `dump` or `compare` yet (verified then: no `build.targets`/`build_target` reference anywhere outside `scan`'s own modules) — implementing it was blocked on `dump --build-target` itself remaining a live flag until this same Phase 6 `scan` removal landed (`frontends/cli/options/rulings.py`'s deferred ruling for `--build-target` named exactly that blocker, not an undecided architectural question). Documented breaking change: `build-target` under `mode: scan` now errors clearly rather than routing to a nonexistent flag or a silent no-op. **Resolved by the 2026-09-11 amendment below:** with `scan` (and `scan --build-target` with it) now deleted, that blocker is gone — `dump --build-target` has since been removed outright too (old spelling exits 64, no alias), and `.abicheck.yml`'s `build.targets` is now the only front-end-reachable source, on both `dump` and `compare`. |
| `--artifact-set`/`new-library-set` (§3 #16/#17) | **(b) — dropped, pending prerequisite** | ADR-065 S3's package component inventories (plan Prerequisite P5) — the model `compare --no-baseline DIR`'s own N-library audit selection would need to genuinely preserve `--artifact-set`'s per-member manifest/coverage accounting — is explicitly "Not started" in the plan, owned by a different workstream. Routing `new-library-set` onto `compare --no-baseline` today would silently narrow its member-selection/coverage guarantees rather than reproduce them, which is exactly the false-negative risk the (c) bar exists to catch — so this is not implemented in this commit. `mode: scan` with `new-library-set` set now errors clearly (naming ADR-065 S3 as the blocking prerequisite) instead of silently falling back to a legacy CLI branch that itself was one asymmetric input combination away from disappearing anyway. Tracked as a real, open gap in `docs/contribute/known-gaps.md`, not silently dropped. |
| Per-side header/include root: additive (`scan`) vs. overriding (`compare`) | **(a) — already covered, fixed in the Action** | This was never a `compare` capability gap — it is purely how `action/run.sh` assembles `-H`/`-I` flags before invoking either CLI. The Action itself now unions a shared `header`/`include` root into each side-specific list before building `CMD`, reproducing `scan`'s documented additive semantics without needing any `compare` change — `compare`'s own per-side override behavior when given a bare (non-side-prefixed) root plus a side-specific one elsewhere is unaffected and pre-existing. |
| `.json`/`.json.gz`/`.json.zst`/content-sniffed snapshot baseline, `dependency_scope` tag matching | **(a) — already covered** | `service.run_dump`'s `include_dependencies` parameter already lets `compare`'s own live-binary dumping filter consistently with a `dump --include-system-declarations` baseline (module map, "Snapshot" section) — the exact tag-matching `scan`'s own `_scan_candidate_include_dependencies` provided. No `compare`-side gap; the Action's own JSON-snapshot-sniffing routing check is simply no longer needed once every `mode: scan` request routes to `compare` unconditionally. |
| `--output-file`, a bare `-o`/`--output`/`--write` via `extra-args` | **(a) — already covered** | `compare` has had `-o`/`--output` and a **repeatable** `--write FORMAT=PATH` (D4) since before this commit. Nothing to translate. |
| Non-JSON `--format` / bare `format: json` (the scan-vs-compare schema-shape divergence) | **(a) — already covered, with a documented breaking change to the JSON shape itself** | The routing concern here was never about `--format` support (`compare` already accepts every format `scan` did, and more) — it was that `scan`'s JSON carries `scan_schema_version`/nested `diff.findings`, `compare`'s carries `report_schema_version`/root `changes`, and a workflow step parsing the raw file would silently receive the other shape. That divergence is the *entire point* of this migration (D3/D4): `mode: scan`'s JSON output now uses `compare`'s own schema, unconditionally. **Documented breaking change**, not a bug: any downstream step in a workflow YAML that parses `mode: scan`'s JSON output by its old `scan_schema_version`/`diff.*` shape must update to the canonical `ReportDocument` shape `compare` (and `pr-comment`) already auto-detect and render correctly for either input. |
| Compile-context flags via `extra-args` (`--lang`, `--ast-frontend`, `--compiler`, `--compiler-prefix`, `--compiler-option`, `--sysroot`, `--nostdinc`, `--frontend-context`, `--allow-ast-frontend-fallback`, `--allow-unsupported-castxml`) | **(a) — already covered, pre-existing product decision** | These flags were removed from `compare`/`dump`'s own CLI *before* this commit (ADR-037 D8.1, CLI cleanup Phase 7b) in favor of `.abicheck.yml`'s `compile.*` namespace, which is already implemented and wired (`cli_compare_helpers.py`, `service_dump_native.py`). `scan` simply hadn't migrated yet. A `mode: scan` workflow passing one of these via `extra-args` must move it to `.abicheck.yml`'s `compile.*` block — the identical requirement every `compare`/`dump` user already has, not a new one this commit introduces. |
| Default (or `--no-pattern-verdicts`) baseline scan reaching `compare`'s unconditional pattern-verdict modulation | **(a) — already covered, moot** | `compare` has had no `--pattern-verdicts`/`--explain-patterns`-implies-modulation flag since D4 landed (verified: absent from `compare --help-all`) — modulation is unconditional, evidence-gated, with no off switch anywhere. Once every `mode: scan` request routes to `compare`, this is simply the run's real, current behavior, not a divergence to route around. |
| `--max-findings`, `--show-suppressed`, `--manifest`, `--public-header-dir` (dedicated Action input unaffected), `--against` (already routed structurally) | **(a)/(b) — already covered or already-ruled DELETE** (§3 rows #20, #26, #17, #22, #1) | No new ruling needed; the table in §3 already classifies each and none required Action-level translation logic beyond what direct field passthrough already does. |

**Consequence for `action/run.sh`, once implemented (not this commit — see
below):** `_SCAN_NEEDS_LEGACY_CLI` and its call sites are deleted outright.
Every `mode: scan` request — baseline and audit-only alike — is assembled as
a `compare`/`compare --no-baseline` invocation. `new-library-set` and
`build-target` inputs are rejected with a clear `::error::`; `new-library-set`
names its still-blocking prerequisite (ADR-065 S3), while `build-target` —
per the 2026-09-11 amendment below, once `dump --build-target` itself was
retired — is simply retired on every mode, naming `.abicheck.yml`'s
`build.targets` as the replacement rather than a prerequisite still to land;
`risk-rules`/`crosscheck` inputs are rejected the same way, naming their
`.abicheck.yml` replacement. This is what D8 calls hard removal with no
deprecation window, applied to the Action's own input surface for the first
time — previously only the CLI's flag surface was held to it.

**Current state (CodeRabbit review, this same commit):** the ruling table
above is complete, but `_SCAN_NEEDS_LEGACY_CLI` and its ~17 `MODE == "scan"`
branches are **not yet deleted** — `action/run.sh` is unchanged by this
commit. Deletion is blocked on `compare --no-baseline` gaining
`--sources`/`--build-info`/`--depth`/`--dry-run` support (a real, separate
gap this investigation surfaced, outside this commit's file ownership; see
`docs/contribute/known-gaps.md`'s matching entry for the exact remaining
steps), since audit-only `mode: scan` still needs the legacy CLI until that
closes. Only the two (c) items (`--budget`, the depth evidence-contract
floor) are implemented in this commit, on `compare` itself.

## Amendment (2026-09-10): the audit-gate exit axis

`docs/contribute/known-gaps.md`'s "no way to gate a CI job on an audit
finding" entry (added by the 2026-09-09 amendment above) named a real,
user-facing regression the D2/D3 migration introduces on its own: legacy
`scan`'s audit mode derives a *verdict* from its own candidate-side
findings and exits `2` when one is `API_BREAK`-classified — verified live,
`scan` on `case148_xcheck_header_build_mismatch`'s and
`case149_xcheck_odr_variant`'s committed snapshots exits `2`, while
`case143_audit_accidental_export`'s `RISK`-classified finding exits `0`.
`compare --no-baseline` exits `0` for all three, correctly, per D2 — an
audit reports no compatibility verdict, and `2` is that family's own
source-break code. The consequence is that a `scan`-based gating CI job has
no `compare --no-baseline` equivalent it can migrate to. This amendment
closes that gap without reopening D2: no `--no-baseline` run may ever emit
`2` or `4`.

### Decision: a new orthogonal axis, exit code `3`, opt-in via `--severity-preset`

**The axis.** `policy/audit_gate_exit.py`'s
`audit_gate_exit_contribution` reproduces legacy `scan`'s own partition —
`BREAKING_KINDS | API_BREAK_KINDS` gates, `RISK_KINDS`/`COMPATIBLE_KINDS`
does not — over the audit's already-computed `findings[]`, and contributes
exactly one new code, `3`, folded with `max` exactly like every other
orthogonal axis this codebase already has (`contract_coverage_exit.py`,
`depth_evidence_contract.py`, `analysis_assurance.py`): it can raise a
clean `0`, and it can never lower, or be mistaken for, a `2`/`4` — D2's
invariant, unchanged.

**Why `3`.** Surveyed against every code `compare`/`scan --against`
currently emit (`docs/reference/exit-codes.md`, `severity.py`'s
`_CATEGORY_EXIT_CODES`, `contract_coverage_exit.py`,
`depth_evidence_contract.py`/`exit_decision_precedence.py`,
ADR-065's completeness axis): `0` (clean), `1` (severity-aware error /
contract-coverage / analysis-assurance / incomplete-scope), `2`
(compatibility source-break — reserved, per D2, never emitted by an
audit), `4` (compatibility ABI-break — reserved, same reason), `5`
(budget overflow), `6` (not-comparable, legacy scheme), `7` (evidence
contract), `8` (removed required library), `64` (usage error). `3` is the
one small integer in that family no axis currently uses.

**Opt-in, not on by default — and reusing `--severity-preset` rather than a
new flag.** Two designs were weighed:

- *(a) default-on.* Every existing `compare --no-baseline` invocation's
  exit code would change the moment this axis has anything to say, with no
  action from the caller. This repository's own contract ("don't change a
  public interface without an ADR and migration") forbids exactly this
  unless the ADR explicitly accepts the cost, and there is no offsetting
  benefit: a CI job that was never told to gate on audit findings should
  not silently start failing.
- *(b) opt-in.* The axis contributes `0` unless the invocation explicitly
  asked for it, so every pre-existing invocation is bit-for-bit unchanged.
  The cost is the mirror image of (a): a `scan`-to-`compare --no-baseline`
  migration that forgets to opt in gets a *silently non-gating* job where
  `scan` used to gate — exactly the "record before disposing" risk
  `vision.md` warns about, except on the *exit code* rather than a
  finding. This is mitigated, not eliminated, by making the opt-in the
  same flag a `scan`-based gating job already had reason to reach for:

Adopted: **(b), opt-in, activated by passing `--severity-preset`** (any
value except `info-only`) to `compare --no-baseline`. Before this
amendment, `--severity-preset` under `--no-baseline` was a hard usage
error (exit `64`) naming this exact gap
(`no_baseline_rulings._UNSUPPORTED_OPTIONS["severity_preset"]`); it is now
legal there, and passing it is the declaration. This was chosen over a
bespoke `--audit-gate` boolean for two reasons: it reuses a flag every
`scan`-migrating CI author already reaches for to express "I want this run
to gate" on a two-sided `compare`, rather than teaching a second spelling
of the same intent; and it gives the migration guidance a single, concrete
sentence — *"a `scan`-based gating job must add `--severity-preset
default` (or `strict`) to its `compare --no-baseline` invocation to keep
gating on a hygiene finding; a non-gating job needs no change."*
`info-only` (`SeverityLevel.INFO` on every category, the same "don't gate
anything" request it is on a two-sided `compare`) is the one preset value
that does **not** enable the axis — `audit_gate_enabled_for_severity_preset`
is the single function this decision is read from, so the CLI and any
future typed-API caller cannot drift on what "opted in" means.

**Why the axis does not route through `severity.py`'s category model,
even though `--severity-preset` activates it.** `severity.py`'s own
`IssueCategory`/`SeverityConfig` split findings into four buckets, and by
that module's own docstring `potential_breaking` is **`API_BREAK_KINDS ∪
RISK_KINDS`** — deliberately merged, because a severity preset answers "how
strict should review-worthy findings be", not "does this specific finding
require recompilation". Routing this axis's own gating rule through that
bucket (option (a) named in the module map's own task description) would
have gated `case143`'s `RISK`-classified finding identically to
`case148`/`case149`'s `API_BREAK`-classified ones the moment
`potential_breaking` reached `error` — reintroducing, under a different
name, the exact over-gating regression the reproduction requirement this
amendment was written against forbids. So `--severity-preset`'s *value* is
read only as the activation signal (`info-only` or not); the axis's own
gating rule reads `BREAKING_KINDS`/`API_BREAK_KINDS` membership directly,
unchanged from what legacy `scan` computed. This reuses the registry-
derived classification sets `checker_policy.py` already exports, per this
file's own "Adding a new ChangeKind" procedure — it does not invent a
second taxonomy.

**What is, and is not, wired.** The axis is folded into
`report/no_baseline.py`'s `no_baseline_exit_code`/
`compute_no_baseline_document` (every format — `json`, `markdown`, `sarif`,
`junit`, `oneline` — reads the same resolved `exit_axes`/`exit_code`), and
the native `compare --no-baseline` CLI
(`frontends/cli/commands/compare_no_baseline.py`) derives the opt-in from
the resolved `--severity-preset` value. The typed Python API does not carry
`--no-baseline` at all yet (`CompareRequest`/`CompareResult` have no
`declared_absent`/audit-mode field — verified, no reference anywhere under
`service*.py`/`checker_types.py`), so there is no `CompareResult`-shaped
consumer to extend today; this stays true to "read, don't re-derive" by
having exactly one axis owner (`policy/audit_gate_exit.py`) ready for that
front end the day it exists, rather than duplicating the rule into a second
module now.

**Update (2026-09-10, same day): the composite Action's own translation has
landed.** At the time this amendment was first written, the composite
GitHub Action did not invoke `compare --no-baseline` for `mode: scan`'s
audit-only path at all — audit-only requests stayed on the legacy `scan`
CLI unconditionally, pending the separate `compare --no-baseline`
parity gap named above (`--sources`/`--build-info`/`--depth`/cross-toolchain
flags and `--dry-run`). That gap closed the same day, and
`action/run.sh` was updated to match: every `mode: scan` request (audit-only
or baseline) now assembles `compare`/`compare --no-baseline` through one
shared command-assembly branch, with no legacy-CLI fallback branch left at
all. `--severity-preset default` is injected on the audit-only shape
whenever the caller stated no preset of its own (neither the dedicated
`severity-preset` input nor an explicit `extra-args --severity-preset ...`),
which is what keeps `mode: scan`'s own documented default-gating behavior
intact across the migration — a caller who already asked for a preset
(`info-only` included) keeps exactly the preset it asked for. Exit `3` is
published as a new `AUDIT_GATE` Action verdict output, alongside
`COVERAGE_INCOMPLETE`/`SEVERITY_ERROR`, and fails the step unconditionally
(no `fail-on-*` input governs it, matching the coverage/assurance axes'
own treatment). See `tests/test_action_run_sh_audit_gate.py` for the
end-to-end coverage (real `run.sh`, real `abicheck`, the G20 corpus
fixtures this amendment's own verification already used) and
`action.yml`'s `verdict` output description for the user-facing contract.

**Verification.** `tests/parity/test_no_baseline_audit_corpus_parity.py`
extends the G20 corpus with `test_audit_gate_axis_matches_legacy_scan_
gating` (every one of the eleven fixtures, with `--severity-preset
default`: `case148`/`case149` exit `3`, every other fixture — `case143`
included — exits `0`), `test_audit_gate_axis_requires_opt_in` (no flag,
every fixture stays `0`), `test_audit_gate_axis_disabled_by_info_only_
preset`, and `test_scan_baseline_exit_codes_documented_for_the_gated_
fixtures` (pins the legacy `scan` exit codes this whole amendment is
measured against, on the same committed snapshots). Unit tests
(`tests/test_audit_gate_exit.py`) and a property test
(`tests/test_audit_gate_exit_properties.py`, generated combinations of this
axis alongside the other `max`-folded axes) cover the module directly.

**Status.** Implemented, axis and Action translation both. `docs/contribute/known-gaps.md`'s matching entry
is updated to record both as closed.

### Correction (2026-09-10, same day): the gating rule reads the effective verdict, not the raw kind

A Codex security review on the PR implementing this amendment (P1) found a
real gap in the paragraph above titled "Why the axis does not route through
`severity.py`'s category model": reading `BREAKING_KINDS`/`API_BREAK_KINDS`
membership off a finding's *raw* `change.kind` is correct only on the
un-policied G20 corpus. The moment a run supplies `--policy` (a document
this repository's `SECURITY.md` treats as trusted, unlike the analyzed
binary itself), an `overrides:`/`reclassify:` rule can promote a normally
`RISK`-classified finding (e.g. `case143`'s `exported_not_public`) to
`Verdict.BREAKING` — every other consumer of a finding's classification in
this codebase (the JSON/Markdown/SARIF/JUnit renderers, the two-sided gate)
reads that *effective*, policy-resolved verdict via `policy.severity.
effective_verdict_for_change`, but this axis's first revision did not, so
the promotion was silently invisible to it: an untrusted candidate artifact
could still exit `0` under an explicitly-selected policy that had named its
finding breaking.

Fixed the same day, before merge, by changing `audit_gate_exit_contribution`
to read each finding's already-resolved `.verdict` (a
`report.finding.ReportFinding`, produced by `build_report_findings` /
`effective_verdict_for_change`) and compare it against `Verdict.BREAKING`/
`Verdict.API_BREAK` directly, instead of importing the raw `BREAKING_KINDS`/
`API_BREAK_KINDS` sets. This is "read, don't re-derive" applied to this
axis, matching how every other renderer already reads classification — it
does not change the decision stated above (the axis still does not route
through `severity.py`'s coarser `IssueCategory`/`potential_breaking`
bucket, which stays merged with `RISK_KINDS` for the reason already given),
it corrects which representation of "does this finding's classification
gate" the axis reads. `policy/audit_gate_exit.py` stays a leaf module with
no new upward dependency: it reads a duck-typed `.verdict` attribute rather
than importing `report.finding.ReportFinding` itself, preserving the
`policy -> report` dependency direction this repository's architecture
gate enforces. Regression coverage: `tests/test_audit_gate_exit.py::
TestAuditGateExitContribution::test_a_policy_promoted_verdict_gates` (unit
level) and `tests/parity/test_no_baseline_audit_corpus_parity.py::
test_audit_gate_axis_honors_a_policy_promoted_verdict` (live, an
`overrides:` policy document promoting `case143`'s finding, verified to
flip its exit code from `0` to `3` with no change to which finding is
reported).

## Amendment (2026-09-11): `dump --build-target` removed, closing the last flag-demotion deferral Phase 6 unblocked

Phase 6 (`scan` retirement) named three flag demotions it unblocked but did
not itself implement: `compare --env-matrix`, `compare
--require-complete-analysis`, and `dump --build-target` — the ruling table
above's own `--build-target` row (2026-09-09) recorded `dump --build-target`
as still blocked specifically on `scan --build-target` remaining a live
option, not as an open architectural question. With `scan` (and
`scan --build-target` with it) deleted, that blocker is gone, and this
amendment closes it: `dump --build-target` is removed outright (old spelling
exits 64, no alias), and `.abicheck.yml`'s `build.targets` — the CONFIG
classification the ruling table already named as the eventual replacement —
is now the only front-end-reachable source of build-target scoping, on both
`dump` and `compare`. `InputSpec.build_targets` stays as a genuine typed-API
field for a programmatic caller: unlike `--build-query`/`--build-compile-db`
(removed from `InputSpec` for being entangled with build-system execution
trust), `build_targets` is ordinary per-side evidence-scoping data, the same
shape as `compile_db_filter`/`public_header_dirs`. The existing
CLI-overrides-config fallback (`buildsource/embed.py`, `buildsource/
l2_seed.py`, `workflows/plan.py`'s `_discovered_config_build_targets`)
already read `.abicheck.yml`'s `build.targets` whenever `build_targets` was
empty, so removing the CLI's only non-empty source makes config the sole
front-end-reachable route with no further plumbing changes needed.
`action.yml`/`action/run.sh`/`action/validate-inputs.sh`'s `build-target`
input is retired on every mode now (previously scan-only), failing loudly
before the toolchain install and naming `.abicheck.yml`'s `build.targets` as
the replacement — the "Consequence for `action/run.sh`" paragraph above is
updated accordingly. `compare --env-matrix` and `compare
--require-complete-analysis` remain the two still-open, unimplemented
followups; see `docs/contribute/plans/one-comparison-product.md`'s Phase 6
section for their exact scope. This amendment closes the `--build-target`
deferral the ruling table already ruled on; it does not reopen that ruling
or the (b) classification itself.

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

## Amendment (2026-09-11): the Action input lifecycle — `mode: scan` retired outright

The CLI's own `scan` command was already deleted (Phase 6, above) — `abicheck
scan` has exited 64 for a while, naming `compare`/`compare --no-baseline` as
the replacement. Until now, `mode: scan` survived as a composite-Action
*input value* only: `action/run.sh` translated it internally to `compare`/
`compare --no-baseline`, matching the CLI's own retirement one layer up but
never actually removing the Action-level spelling. This amendment closes
that gap, per D8's own hard-removal rule (no deprecation window): setting
`mode: scan` on the Action now fails the step outright, at the earliest
possible point (`action/validate-inputs.sh`, before Python setup or any
toolchain install), naming the exact replacement for the caller's own shape.
The Action's three scan-only inputs (`against`, `estimate`, `audit`) are
retired the same way — each becomes a hard `::error::`, not a silently
inert no-op, since GitHub Actions drops an undeclared input with only a
warning and this input family used to change what actually ran.

### The two `mode: scan` shapes, and what replaces each

`mode: scan` never collapsed to one spelling — a baseline scan and an
audit-only scan were two different requests, and they get two different
replacements:

**(a) `mode: scan` with `against`/`abi-baseline` set (a baseline scan).**
Replacement: `mode: compare` with the identical baseline value passed as
`old-library` (or `abi-baseline`, unchanged) and the same `new-library`.
This was already exactly what the Action's own internal translation did
for this shape (`INPUT_AGAINST` → `INPUT_OLD_LIBRARY`) — the only change is
that the caller now states `compare` directly instead of `scan` being
translated for them.

**(b) `mode: scan` with no baseline (or `audit: true`, which forced this
shape regardless of an `against`/`abi-baseline` also being configured).**
Replacement: `mode: compare` with **both** `old-library` and `abi-baseline`
omitted — omission is the trigger, not a dedicated flag. This routes to a
first-class `compare --no-baseline` invocation against `new-library` alone,
reporting no old/new compatibility verdict at all (D2). `compare` did not
previously expose this shape as a *native* Action request at all — before
this amendment, `old-library` was a required Action operand for `mode:
compare` in every practical sense (the CLI's own `--no-baseline` flag had
no Action-level way to reach it for a native `compare` request), and the
shape was reachable only through `mode: scan`'s own internal translation.
This amendment makes it native: `action/validate-inputs.sh` and
`action/run.sh` now both treat "old-library and abi-baseline both absent"
as a first-class, documented compare shape, not an implicit fallback. See
"What changed to make (b) native" below for the mechanics.

### The audit-gate trap, restated as a migration requirement

Legacy `mode: scan` with no baseline gated a CI job on a `BREAKING`/
`API_BREAK`-classified candidate-side finding **by default, unconditionally,
no flag needed**. `compare --no-baseline`'s own audit-gate axis
(ADR-068's 2026-09-10 amendment, above) reproduces the identical gating
partition at exit code `3` — but it is **opt-in**, activated only by
`--severity-preset` (any value except `info-only`). A caller migrating an
audit-only `mode: scan` job to `mode: compare` (shape (b) above) who does
not also set `severity-preset` gets a job that always exits 0/passes,
silently converting what used to gate CI into a step that never fails
regardless of what the audit finds. This is exactly the risk `vision.md`'s
"record before disposing" principle warns about, on the exit code rather
than a finding.

**This is a required migration step, not an optional hardening.** Every
audit-only `mode: scan` job that relied on the default gating (i.e., did not
already pass `severity-preset: info-only` to opt out) must add
`severity-preset: default` (or `strict`) when migrating to `mode: compare`'s
audit-only shape, or the job silently stops gating. A job that already
passed `severity-preset: info-only` — an explicit "don't gate" request —
needs no change; the same value keeps the same meaning.

Verified live against the G20 corpus fixtures this ADR's 2026-09-10
amendment already established as the reproduction set
(`catalog/cases/case148_xcheck_header_build_mismatch`,
`case149_xcheck_odr_variant`, `case143_audit_accidental_export`):

```console
$ abicheck compare --no-baseline catalog/cases/case148_xcheck_header_build_mismatch/snapshot.abi.json \
    --format json --severity-preset default
exit=3
$ abicheck compare --no-baseline catalog/cases/case149_xcheck_odr_variant/snapshot.abi.json \
    --format json --severity-preset default
exit=3
$ abicheck compare --no-baseline catalog/cases/case143_audit_accidental_export/snapshot.abi.json \
    --format json --severity-preset default
exit=0
```

case148/case149 carry `API_BREAK`-classified findings and gate (exit 3,
matching legacy `scan`'s own exit 2 on the identical fixtures); case143
carries only a `RISK`-classified finding and does not gate (exit 0),
matching legacy `scan`'s own exit 0 there too — the replacement wiring
reproduces the exact gate/no-gate partition legacy `scan` had, provided the
caller adds `--severity-preset`.

### What changed to make (b) native

Before this amendment, `action/run.sh`'s `compare` branch unconditionally
required `old-library` (`${INPUT_OLD_LIBRARY:?old-library is required for
compare mode}`) — the audit-only shape was reachable only via `mode: scan`'s
own separate command-assembly branch. That branch is now deleted outright,
and the compare branch itself gained the audit-only case: `old-library` and
`abi-baseline` both empty routes to `compare --no-baseline new-library`
instead of failing the bash parameter expansion. Everything scan's own
audit-only translation used to do for this shape now lives in the one
`compare` branch, keyed on this same presence check (`_NO_BASELINE` in
`action/run.sh`):

- `-H`/`-I`: no OLD side exists in this shape (old-header/old-include would
  be a CLI usage error — "the OLD side is declared absent"), so they are
  never forwarded; `public-header-dir` folds into a bare `-H` root the same
  way `dump` derives provenance-and-extraction scope from `-H` (legacy
  `scan`'s own `--public-header-dir` CLI flag is gone, so there is no
  separate flag to fold from any more).
- `since`/`changed-path`/`budget`: rejected upfront (before any dependency
  install) as usage errors for this shape, matching `compare --no-baseline`'s
  own CLI-level rejection (D2) — unchanged from the previous `mode: scan`
  behavior for this shape.
- `require-complete-analysis`: **now forwarded**, unlike legacy `scan`'s own
  audit mode (which never accepted the flag at all, so this Action
  documented it as a no-op for that shape). `compare --no-baseline` genuinely
  accepts the flag and gives it real teeth (live-verified: an incomplete
  analysis-assurance candidate-side finding fails the step under it). This
  is a real, accepted capability gain from unifying onto `compare`'s own CLI
  surface rather than preserving a narrower historical accident.
- `budget`: the two-sided (baseline) shape gains the dedicated `budget`
  Action input's forwarding too — previously `compare`'s own branch had no
  `--budget` forwarding at all (the input was documented scan-only); now any
  `mode: compare` baseline request can set it.
- The `-H`/`-I` shared-root-plus-override union behavior legacy `scan`'s own
  CLI documented as additive (`_add_unioned_sided_flag` in the pre-amendment
  `action/run.sh`) is **not** carried forward for a two-sided compare: a
  two-sided `compare`'s own native per-side resolution (which OVERRIDES a
  bare shared root with a side-specific one, not unions them) now applies
  uniformly, since there is no longer a `scan`-flavored CLI to reproduce.
  This is a deliberate, accepted behavior change of this migration — it
  restores `compare`'s own always-documented semantics rather than
  preserving `scan`'s divergent one for a route that no longer exists.
- One `run.sh`-internal bug was caught and fixed while implementing this:
  `_compile_context_sources_pairwise()` (decides whether a `--sources`
  tree's own `compile:`/`source:`/`debug:` blocks promote pair-wide or
  single-sided) keyed only on `$MODE == "compare"` plus whether `old-library`
  looked like a stored snapshot — it had no way to tell "old-library
  genuinely omitted" apart from "old-library happens to test as a live
  binary" (an empty string is not a stored-snapshot magic-byte match
  either), so it would have misclassified the new audit-only shape as
  pairwise. Fixed to also require `old-library` be non-empty before
  classifying pairwise.

### Consequences for this repo's own callers

- `.github/workflows/test-action.yml`'s three `mode: scan` acceptance lanes
  (`test-scan-baseline-breaking`, `test-scan-audit`, `test-scan-estimate`)
  are migrated to the shape (a)/(b) replacements above, asserting the
  identical exit code and verdict as before — these lanes are this
  migration's own acceptance evidence, not just coverage that happened to
  need updating.
- `tests/test_action_run_sh_scan_no_baseline_capability_gap.py` and
  `tests/test_action_run_sh_scan_routing_edge_cases.py` (both named for a
  scan-vs-legacy-CLI routing predicate that no longer exists — there is
  only one CLI, `compare`, and no routing decision left to make) are
  retired: the former is renamed to
  `tests/test_action_run_sh_compare_no_baseline_capability_gap.py` and its
  real invariant (since/changed-path/budget rejected upfront for the
  audit-only shape; require-complete-analysis forwarded, not withheld) kept
  and restated for `mode: compare`; the latter's entire subject (does input
  X affect which of two CLIs a scan request routes to) has no meaning once
  there is only one CLI, and its coverage of `compare`'s own real behavior
  (header/include unions, depth values, JSON-snapshot detection, extra-args
  passthrough) was already independently covered by this directory's other
  `compare`-focused test modules, so the file is deleted rather than kept
  as a hollow renamed shell. `tests/test_action_validate_inputs_no_baseline_
  capability_gap.py` is renamed to `tests/test_action_validate_inputs_
  compare_no_baseline_capability_gap.py` on the same basis as its `run_sh`
  sibling. A number of other, pre-existing test modules that happened to
  exercise `mode: scan` as one parametrized case alongside `compare`
  (`test_action_analysis_assurance_verdict.py`,
  `test_action_compile_context_old_library_liveness.py`,
  `test_action_compile_context_parity.py`, `test_action_coverage_verdict.py`,
  `test_action_run_contract.py`) are updated in place: a `scan`-parametrized
  case is either dropped (a `compare`-only invariant now) or ported directly
  to the `compare` shape it was implicitly already testing, with no loss of
  the underlying invariant.
- `docs/use/github-action.md` gains a migration section covering both
  shapes, including the `severity-preset` requirement above.
  `docs/integration/scenarios/source-replay.md` and `single-build-audit.md`
  (built around `mode: scan` examples) are rewritten for the `mode: compare`
  replacement wiring. `docs/use/github-action-source-scans.md` — described
  elsewhere as the canonical `mode: scan` Action reference — is retired per
  `docs/AGENTS.md`'s ownership rules, folded into `github-action.md`'s own
  migration section and `use/dump-compare-flags.md` rather than kept as a
  page about a retired mode.
- `scripts/retired_surfaces.py` gains a `mode: scan` (Action input value)
  entry, so no future documentation page can reintroduce the retired
  spelling without the `check_docs_contract.py` sweep catching it.

### Status

Implemented: `action.yml`, `action/validate-inputs.sh`, `action/run.sh`,
this repository's own callers, and the docs above. Verified live against
the G20 corpus (audit-gate reproduction, above) and via
`tests/test_action_run_sh_*.py`/`tests/test_action_validate_inputs*.py`
(the full directory, not just the files this amendment specifically
touched — every test in both directories passes against the new
behavior).

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
