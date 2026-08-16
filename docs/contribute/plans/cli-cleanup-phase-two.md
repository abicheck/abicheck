---
doc_type: contributor
level: advanced
lifecycle: active
---

# CLI cleanup, phase two — reviewed plan

**Type:** Refactor / interface-contract plan. Continues the hard cleanup landed
by [#770](https://github.com/abicheck/abicheck/pull/770)
(`refactor(cli)!: hard cleanup — remove superseded and duplicate flags`), which
physically deleted the superseded aliases and no-op flags with **no deprecation
window**. Related: [ADR-037](../adr/037-cli-interface-contract.md) (CLI
interface contract), [ADR-043](../adr/043-cli-pre-1.0-surface-reset.md) /
[ADR-054](../adr/054-cli-project-integration-surface-consolidation.md)
(root-surface admission),
[ADR-047](../adr/047-github-actions-integration-model.md) (Action model),
[ADR-056](../adr/056-multi-artifact-library-set-scan.md) (`scan --artifact-set`).
**Effort:** L (seven independent PRs, plus three convergence prerequisites
added by the post-#780/#782 review) · **Risk:** mixed — PR 1 is
presentation-only, PR 1b is gated on new persisted-report schema work, PR 4
changes what a CI job's exit code means.

> **Review checkpoint (2026-08-16, `main` at `410caf5`, after
> [#779](https://github.com/abicheck/abicheck/pull/779),
> [#780](https://github.com/abicheck/abicheck/pull/780) and
> [#782](https://github.com/abicheck/abicheck/pull/782)).** PR 1 and PR 2 are
> implemented and reviewed as correct. The rest of this plan's ordering is
> **superseded** by the reviewed sequence in "Ordering" at the bottom: the next
> step is **not** another hard-delete PR. Three shared contracts — one typed
> `dump` resolution path, one effective pack/gate configuration, one canonical
> exit decision — have to converge first, or deleting the flags merely freezes
> today's divergence between parallel paths into the removed-flag baseline. The
> per-PR sections below carry the prerequisites each one gained.

## Problem

Phase one removed the *duplicate spellings*. What is left is a different class
of surface: options that are real, single-spelling, and still wrong to keep on
the native CLI, because they encode something that belongs somewhere else —
in a renderer default, in a GitHub Action, in a versioned manifest, in trusted
project config, or in the gate's own semantics.

A first draft of phase two proposed removing all of them in one hard-cleanup
PR shaped like #770. This plan is the reviewed version of that draft. The
review's two substantive corrections are that `--exit-code-scheme` is **not** a
cosmetic flag (it selects between two different gate algorithms, so deleting it
silently changes existing CI outcomes) and that `scan --artifact-set` is **not**
redundant surface (it is a deliberate safety boundary from ADR-056 D2, and
replacing it with implicit positional-directory dispatch is a regression, not a
simplification).

## Scope at a glance

| Candidate | Verdict | Action |
|---|---|---|
| `--exit-code-scheme` (compare, scan) | Remove, but reworked | Own ADR + semantics PR: keep the orthogonal axes, drop the *manual algorithm selector* — after pack parity and one canonical `ExitDecision` |
| `--require-complete-analysis` (compare, scan; new in #780) | **Keep** | Real, distinct axis — but move its semantics into the resolved gate/assurance policy instead of leaving it a CLI-only boolean |
| `compare --stat`, `compare --recommend` | Remove | `--format review` replaces `--stat`; recommendation becomes an unconditional renderer output |
| `scan --artifact-set` | **Keep** | Refine the value syntax only (repeatable option / manifest); do not overload positional `DIRECTORY` |
| `--annotate`, `--annotate-additions` | Remove from CLI (PR 1b, not PR 1) | Options on `compare` alone, shared by both operand shapes (no CLI-level split is possible) — blocked on a release-report persistence prerequisite; see PR 1b |
| `dump --build-query`, `dump --build-compile-db` | Remove from CLI | Move to `.abicheck.yml`; only `build.query` (an executable command) needs the explicit-`--config` trust gate — `build.compile_db` is a data path and carries the ordinary dry-run contract only |
| `aggregate --on-missing-required`, `--on-unexpected-target` | Remove from CLI | Move the policy into the manifest / run-plan schema alongside the expected target set |

Everything above is a **breaking** change to the native CLI. Consistent with
the #770 cleanup and ADR-037's stance, none of it gets a deprecation alias:
an old spelling must fail as `No such option` with exit `64`.

## PR 0 — restore a green CI baseline first

This is a prerequisite, not a nicety: a breaking interface change cannot be
evaluated against a red baseline. **The review splits it in two**, because the
code half and the governance half now have genuinely different statuses:

| | Scope | Status |
|---|---|---|
| **PR 0A** | The Windows test failures themselves (items 1–2 below) | **Closed at the test level** — the Windows unit *and* integration lanes passed on PR #782; the specific MSYS/Git-Bash failures described under "Verified state" are no longer the current blocker |
| **PR 0B** | Required checks / Ruleset + exact-merge-SHA verification (items 3–4) | **Still open, still P0** — this is what actually makes a red CI block a merge |

**PR 0B is the one to do first, and it is the review's PR A.** The branch API
still reports `protected: true` with
`required_status_checks.enforcement_level: off` and empty `contexts`/`checks`.
Unless an equivalent GitHub Ruleset supplies them, a red CI does not block a
merge — so PR 0A's green lanes are green by convention, not by enforcement.
Separately, the exact merge SHA of #782 did not run a full CI sweep on
`push: main` in the returned workflow set (AgentReady completed; Examples
Validation was still running; no separate full `ci.yml` run over that exact
SHA), which is precisely what item 4 exists to make detectable.

**The required-check list must match `.github/AGENTS.md`'s own required-vs-
informational classification, not the set of workflow names visible in a run
(Codex review caught an earlier draft that didn't).** `Examples Validation`
and `agentready.yml` are both explicitly `No (informational)` there, and
`test-action.yml` is conditional — it runs only when the path set in
`.github/workflows/test-action.yml`'s own `on:` block changes (wider than
`.github/AGENTS.md`'s own "when `action/**`/`action.yml` changes" gloss for
it — that table's cell is a summary, not the filter itself), path-filtered
like several other workflows in that same table. A
required check that a path-filtered workflow never starts is not "green by
default" on an unrelated PR — GitHub blocks merge on a required check that
never reports at all, the same as on a failing one, so requiring any of these
three as written would strand every PR that doesn't touch their paths. Cover:

**This section states a rule, not a list — after three review rounds each
catching a different mistake in a hand-copied snapshot, hand-copying a
fourth one is the wrong fix.** Round 1 conflated "sounds conditional" with
"is trigger-filtered" (put `CLI Interface Check` in the wrong bucket, missed
`Changelog Fragment Check`/`Bug-fix test contract` entirely). Round 2 found
the fixed list still missed `docs-pr.yml` — required per `.github/AGENTS.md`
whenever `docs/**`/`mkdocs.yml` changes (again that table's summary gloss,
not `.github/workflows/docs-pr.yml`'s own, wider `on:` block), *and*
genuinely trigger-path-filtered on its own path set, the identical shape as
`test-action.yml`. Round 3 found the fallback
plan itself wrong: **no native GitHub mechanism — not classic branch
protection, not a Ruleset's required-status-checks rule — conditions
"required" on which files a given PR touched.** A Ruleset can require a
check unconditionally for matching refs; it cannot express "required only
when `action/**` changed." So there is no first-class way to make a
trigger-path-filtered workflow's own check required without also stranding
every PR that doesn't touch its paths — the neutral-aggregate wrapper below
is not a fallback for when the ideal mechanism is unavailable, it is the
*only* correct approach for this class of workflow.

**The rule PR 0B's implementer applies, once, against `.github/AGENTS.md`'s
table at implementation time (not against a hand-copied snapshot here,
which is what has now drifted wrong three times in a row):**

1. For every workflow that table marks required (unconditionally or "when
   X changes"), read its own `on: pull_request:` block.
2. **No `paths:` filter** (the workflow always runs; a `skip-*` label or
   internal diff check decides applicability) → require its check directly.
   Example: `changelog-check.yml` has no `paths:` filter — required
   directly, its own `skip-changelog` label is the escape hatch, not a
   missing check run.
3. **Has a `paths:` filter** (the workflow may not run at all on an
   unrelated PR) → never require its check directly. Add — or extend — one
   always-triggered neutral-aggregate job that itself reads the same path
   set that workflow's own condition uses, reports success when the paths
   don't apply and otherwise mirrors that workflow's real outcome, and
   require *that* aggregate job instead. Example: both `test-action.yml`
   and `docs-pr.yml` are in this bucket.
4. Anything the table marks **not required** (`Examples Validation`,
   `AgentReady`, `docs-review-triggers.yml`, ...) stays out of the required
   set entirely; PR 0B does not change that classification.

This rule is self-checking in a way a copied list is not: step 1 always
starts from the table's current contents, and steps 2/3 are a mechanical
read of each workflow's own trigger, not a memory of what a prior draft of
this document said.

Prefer **one stable aggregate required check per always-required workflow**
(or a Ruleset-compatible job) over requiring every matrix leg individually —
a matrix-leg-level required list goes stale on every matrix edit and is the
usual reason required checks get turned back off.

The "Verified state" below is kept as the historical record of what PR 0A
fixed; it describes `main` as of PR #779, not today.

**Verified state** (`ci.yml`, run `31882605683`, head `13be967` on `main`):
19 of 20 jobs pass; the single failure is
`unit-tests (windows-latest, 3.13, false)` at the step
*"Run tests (Windows — parallel, no coverage)"*, with **6 failed, 27628 passed**.
All six failures are in the Action shell-script tests, and all six are
MSYS/Git-Bash path-translation assumptions rather than product defects:

- `tests/test_action_run_sh_py_safe_path.py` — `TestPySafeDirFailsClosedWhenMktempFails`,
  `TestPyBinHasAbicheckFallback`, `TestPySafeDirCleanedUpOnEarlyExit`,
  `TestPyBinResolvedAsAbsolute` (a `/tmp/tmp.…` path read back as a
  `WindowsPath`; `/c/hostedtoolcache/.../python3` not importing `abicheck`;
  `/c/Users/...` not `is_absolute()`).
- `tests/test_action_run_sh_severity_summary.py` — `TestReportPathAnchoring`
  (`C:\Users\…` expected, `/c/Users/…` produced).

The same job has been failing on every recent `main` commit, so this is a
standing red lane, not #770's regression.

**Work**

1. Fix the six tests (or the script's path handling if the script is genuinely
   wrong under Git-Bash) — classify each as *test-environment assumption* or
   *product bug on Windows*, and say which in the PR body.
2. Do **not** paper over it with a blanket `continue-on-error` or a
   Windows-wide skip; a marker-level skip needs a named reason and must keep
   `ABICHECK_MIN_EXECUTED` honest (`tests/conftest.py`'s silent-skip guard).
3. Reconcile branch protection with reality. The repository reports
   `protected: true` with `required_status_checks.enforcement_level: off` and
   empty `contexts`/`checks`; unless an equivalent GitHub Ruleset supplies them,
   a red CI does not block merge. Add required checks per the classification
   rule stated below (Codex review caught an earlier draft of this item
   restating a second, stale required-check list — `.github/AGENTS.md`'s
   `ci.yml` row names only the canonical Linux/3.13 `unit-tests` lane,
   `ai-readiness`, `fair-metadata`, `lint-and-types`, and `packaging` as the
   core required jobs; macOS and Windows are separate matrix legs the table
   does not require, and this item must not diverge from that classification).
4. Add exact-merge-SHA verification on `push: main`, so a merge that did not
   run the checks it claimed is detectable after the fact.

**Done when** a full `ci.yml` run on `main` is green on Linux, macOS and
Windows (**0A — done**), and a red required check demonstrably blocks a merge
(**0B — outstanding**).

## PR 1 — presentation

**Status: implemented** (PR #779) — `--stat`/`--recommend` are gone,
`--profile quick` carries the one-line summary (including a fix for the
`--used-by`/`--required-symbol` scoped-gate case, found by review after the
first push), and the release recommendation is unconditional in
`json`/`markdown`/`review` output.

**Closed — do not reopen.** The review's only remaining housekeeping is
negative, not new work: the Tier-2 Python compatibility shims
(`render_output(stat=..., show_recommendation=...)`) must not leak back into
the CLI as flags, and whether those kwargs are themselves stable Python API or
removable is a separate pre-1.0 decision, not part of this initiative. Do not
add `summary`/`release` values to `--report-mode` merely to give the removed
flags a home (see "`--report-mode` stays as-is" below).

Removes `compare --stat` and `compare --recommend` only. `--annotate` /
`--annotate-additions` are **not** in this PR — see PR 1b below for why they
cannot be split off by operand type the way this plan's first two drafts
assumed.

### `--stat`

`--stat` has **two** distinct existing uses, and they migrate to two different
replacements — collapsing both onto `--format review` (an earlier draft of
this plan did exactly that) would silently turn a machine-readable consumer's
JSON into human text:

- **Human summary use** (`--stat` alone, or with a human `--format`): a
  compact one-line summary. `--format review` already exists for compact human
  output. This use migrates to `--format review` — **except** the built-in
  `quick` profile (`cli_profiles.py`'s `COMPARE_PROFILES["quick"]`, which sets
  `"stat": True`), which needs its own explicit decision rather than a silent
  substitution: `reporter_markdown.to_review_digest` is not a one-line
  replacement for `--stat`'s one-line output — it's a multi-section Markdown
  digest (verdict heading, a counts table, the release recommendation, a
  manual-review banner, top impacted symbols). Reusing it for `quick` silently
  breaks the profile's own documented and tested contract ("`quick`
  (symbols-only, one-line summary)" in `--profile`'s help text). PR 1 must
  make one of two explicit choices for `quick`, not inherit `--format review`
  by default: (a) keep a real one-line renderer for this case (a small
  formatter, not `to_review_digest`) so `quick`'s contract is unchanged, or
  (b) deliberately redesign `quick` onto the multi-line digest, updating its
  `--profile` help text and its profile-contract tests in the same PR so the
  new behavior is documented rather than a silent regression.
- **Machine `--stat --format json` use**: not the same output shape as plain
  `--format json` — it is a documented, real combination
  (`docs/use/output-formats.md`'s own `--stat` section shows it:
  `{"library": ..., "verdict": ..., "summary": {...}}`, no `changes` array).
  `action/run.sh` depends on exactly this distinction when stripping flags
  before its internal PR-comment re-run: it explicitly drops `--stat` because
  "it suppresses the `changes` array in JSON, which the comment parser needs."
  This use does **not** migrate to `--format review` (a human/Markdown-shaped
  format, not JSON) — it migrates to plain `--format json`, reading the
  same nested `summary` block that `--stat --format json` already only
  contains, now alongside (not instead of) the full `changes` array. Any
  consumer that genuinely wants the `--stat`-shaped subset without `changes`
  extracts `summary` from the full JSON client-side; the CLI does not need a
  third flag to produce a strict subset of its own full output.
  **One real gap in that equivalence:** `reporter.to_json` short-circuits on
  `stat=True` *before* checking `report_mode`, so `--stat --format json
  --report-mode leaf` today silently ignores `--report-mode leaf` entirely and
  returns the same stat summary either way — including
  `binary_compatibility_pct`/`affected_pct`, which `to_stat_json` always
  includes. Plain `--format json --report-mode leaf`'s summary
  (`_to_json_leaf`) genuinely does **not** carry those two keys — leaf mode's
  summary dict is `breaking`/`source_breaks`/`risk_changes`/
  `compatible_additions`/`total_changes` only. So "reads the same nested
  summary" is false specifically for a caller combining `--stat` with
  `--report-mode leaf`. Document this explicitly rather than let it surface as
  a migration surprise: that combination's migration is `--format json`
  **without** `--report-mode leaf` (i.e. `--report-mode full`, the default) if
  the two percentage fields are required; a caller that doesn't read those two
  keys is unaffected either way.

- Remove `--stat` and every path its `stat=` flag threads through — it is not
  one chokepoint: `cli.py`'s `_render_output` → `service.render_output`;
  `cli_compare_helpers._render_compare_report` and both of its call sites
  (primary output, and the always-unfiltered secondary output, which already
  passes `stat=False`); `_announce_exit_scheme` and
  `_exit_with_severity_or_verdict`; `contract_coverage_exit.announce_coverage_floor`'s
  own `stat=` parameter; and `cli.py`'s structured-format branch
  (`if stat or fmt not in {"markdown", "html", "review"}`), which is where the
  suppression of human-readable extras is actually decided. Removing the flag
  without also collapsing that branch is how a structured or secondary-output
  path gets left half-wired.
- `action/run.sh`'s own `--stat`-stripping branch (its PR-comment re-run flag
  filter) is deleted along with the flag, not left as dead code matching a
  spelling that no longer exists.
- Any human-output profile or docs snippet using bare `--stat`, other than
  `quick`, moves to `--format review`; `quick` gets the explicit (a)/(b)
  decision above instead of an implicit substitution. Any `--stat --format
  json` snippet moves to `--format json` and reads `.summary`, dropping
  `--report-mode leaf` first if it needs the two percentage fields.
- `--show-only` interactions documented against `--stat` are re-stated
  per-migration-path: against `--format review` for the human case (with
  `quick` covered separately), and as "no effect on `.summary`, same as
  today" for the JSON case.

### `--recommend`

`release_recommendation` is *always* in the JSON report; `--recommend` only
decides whether the human renderers print it. That is a renderer default, not a
user-facing capability. Change the defaults first, then delete the flag:

- `review`: always shows the recommendation.
- `markdown`: always shows it (own section).
- `html`: **unchanged** — `generate_html_report()` never computed a
  recommendation section, `--recommend` never reached it, and this PR did not
  add one. Implemented this way deliberately (Codex review): adding an HTML
  recommendation section is a real, separate feature, not part of removing a
  flag that never touched HTML in the first place.
- `json`: unchanged — `reporter.py` already writes `release_recommendation`
  unconditionally on all three of its JSON paths, independent of `--recommend`.
- `sarif`/`junit`: unchanged, and deliberately *without* the recommendation —
  neither renderer emits the field today, and neither format has a natural slot
  for a release verdict. Adding it is not part of this PR.

### `--report-mode` stays as-is

`--report-mode full|leaf|impact|root-cause` answers *how findings are grouped*.
The recommendation answers *whether a release-action summary is printed*. These
are orthogonal, so do **not** fold `--stat`/`--recommend` into new report modes
(`summary`, `release`), and do not let `--report-mode` become the dumping
ground for renderer switches. A genuine `release` mode may be added later if it
describes a whole coherent report shape — but not merely to justify deleting a
boolean.

**Tests.** `compare --stat` (with or without `--format json`) exits `64` with
`No such option`; `--format review` output contains the recommendation;
`--format json --report-mode full` (the default) output contains `.summary`
with the same shape `--stat --format json` used to return on its own,
alongside `changes`; `--format json --report-mode leaf` output's `.summary`
is asserted to **not** carry `binary_compatibility_pct`/`affected_pct` (the
documented, intentional gap above — not a regression to "fix" by adding them
back); `compare --profile quick` output matches whichever of the (a)/(b)
choices above was made, with a contract test pinning that exact shape (not
just "it exits 0"); the `action/run.sh` PR-comment re-run path is exercised
end to end (its own `--stat`-stripping branch is gone, not merely
unreachable).

**Risk:** low — no analysis, verdict, or exit code changes.

## PR 1b — annotations move to the Action (blocked on a persistence prerequisite)

`--annotate`/`--annotate-additions` only do anything when `GITHUB_ACTIONS=true`;
they emit `::error`/`::warning`/`::notice` workflow commands and are inert in an
ordinary shell. That is transport, not comparison semantics — but this PR
cannot be scoped as cleanly as PR 1, for a reason two earlier drafts of this
plan got wrong in two different ways, so both are recorded here.

**First wrong idea: split the flag removal by operand type.** `--annotate` /
`--annotate-additions` are **not** two independent flags, one per operand
shape — they are options on the single registered `compare` command
(`cli.py`). A directory/package operand is dispatched to the unregistered
`compare_release_cmd` fan-out engine *after* `compare`'s own options are
parsed (`cli.py`'s `_dispatch_release_compare`, called from the same
`compare` invocation, `cli.py:1202-1207`), reusing the identical
`annotate`/`annotate_additions` values. So "remove `--annotate` from `compare`
for single-library operands only, keep it for release operands" is not
achievable with the current CLI surface: removing the flag from `compare`
removes the *only* way a release operand can request annotations too. Do not
attempt that split; either flag stays on `compare` for both operand shapes, or
it goes for both at once.

**Second wrong idea (from the same section, one draft earlier): the Action
can render from "the report it already produces," full stop.** True for a
single-library operand's JSON/SARIF report. False for a directory/package
(release-style) operand's summary report:
`cli_compare_release._strip_diff_results_and_adjust_verdict` discards each
library's `DiffResult` after projecting only a capped
(`_MAX_RELEASE_FINDINGS_PER_LIBRARY = 10`) `findings` list
(`bucket`/`kind`/`symbol`/`description`/`source_location`) — no
severity/contract-evaluation classification, truncated past the cap. Today's
`--annotate` on a release operand doesn't even read that projection; it goes
through `_collect_release_extras`, which **re-runs every library's comparison
independently** to recover a full `DiffResult` for
`annotations.collect_annotations`. Separately, `action/run.sh`'s
`_is_release_style_operand` skips the internal `--write` JSON sidecar entirely
for a directory/package operand (the release engine rejects `--write`), so for
that shape the Action does not even have a persisted machine report to read
from.

**What this means for sequencing:** since the flag cannot be split by
operand and the report cannot yet serve both operand shapes, `--annotate` /
`--annotate-additions` stay on `compare`, unchanged, through PR 1 (and every
PR up to this one). PR 1b is real work in its own right, gated on a
persistence prerequisite completing first:

1. The primary release pass persists a real, uncapped, un-stripped per-finding
   machine report — schema work: what today collapses to
   `findings`/`findings_truncated` needs the same detail
   `collect_annotations` reads from a live `DiffResult`, including the
   severity/contract classification the capped projection currently drops.
2. `action/run.sh` gains a `--write`-equivalent path for a release operand, so
   the Action has something to read for that shape too.
3. Only once both of those land: remove `--annotate`/`--annotate-additions`
   from `compare` (covering both operand shapes at once, since they share one
   flag), define `annotate`/`annotate-additions` inputs in `action.yml`
   (neither exists today — a workflow currently passes these through
   `extra-args`), implement the Action's renderer over the now-uniformly-
   persisted report for both operand shapes, update every first-party
   workflow/recipe/doc snippet using `extra-args` for this, and state the
   `extra-args` → `annotate` input migration for external callers.

**New invariant (post-#780), and it is the reason PR 1b became PR E in the
reviewed ordering:**

> The Action never infers a gate's identity or its reason from stderr when the
> versioned report already carries it.

#780 made this checkable rather than aspirational: reports now persist
`analysis_assurance`, `analysis_assurance_exit_contribution` and
`contract_coverage_exit_contribution`, so the *exact* number each orthogonal
axis folded into the exit code is machine-readable. `action/run.sh`'s
`_assurance_gated` is already JSON-first (a dedicated
`require-complete-analysis` boolean input, then
`analysis_assurance.status` from the report) with the stderr grep kept only as
the no-readable-JSON fallback — so this is technical debt, not a live bug. But
the debt is real in two directions and both are PR 1b/E's job:

- the Action still re-derives "did this axis gate" from *input + status*
  instead of reading the persisted contribution, which is the field that
  actually decided the exit code;
- for a release/package operand there is still no persisted report to read at
  all (see the second wrong idea above), so the stderr fallback is not a
  fallback there — it is the only path.

So the prerequisite below is not just "uncap the findings": the persisted
report must be the single source for *both* the annotations and the gate
explanation, for both operand shapes.

The shape the Action should be able to read, uniformly, for a single-library
compare, a release/package compare, and `scan --against`:

```json
{
  "compatibility": {"verdict": "COMPATIBLE"},
  "gate": {"exit_code": 0, "blocking": false},
  "contract_coverage_exit_contribution": 0,
  "analysis_assurance_exit_contribution": 1,
  "exit": {"code": 1, "reasons": ["analysis_assurance"]},
  "findings": []
}
```

With that in place the Action does not parse stderr, does not re-run any
comparison, does not guess why the exit was `1`, renders annotations from the
persisted findings and the Job Summary from the same object, and behaves
identically for a single pair and a release fan-out. The `exit` block is the
same object PR 4/G formalizes as `ExitDecision`. Build it once and consume it
once — which is why PR G is split, and the split is load-bearing for this
section rather than cosmetic: **G1** builds the decision object and emits its
report block (no CLI behaviour change, no flag removed) and lands *before* PR
E; **G2** makes today's `auto` the only gate algorithm and deletes
`--exit-code-scheme`. Without that split PR E would have to either depend on
unlanded work or invent the second, Action-shaped spelling this section
prohibits. If G1 slips, PR E ships its annotations half and leaves the
gate-explanation half — not a private `exit` block of its own.

**Tests.** A saved report fixture (both a single-library and a release-style
report) must produce, through the Action's renderer, byte-identical
annotations to what the CLI emitted for the same report before the move. A
report carrying a non-zero `analysis_assurance_exit_contribution` /
`contract_coverage_exit_contribution` must produce the correct labelled
verdict with `STDERR_CONTENT` empty — i.e. the stderr path is provably not
load-bearing when a report exists.
`compare --annotate` exits `64` with `No such option` only once step 3 lands —
not before.

**Risk:** medium — gated on new persisted-report schema work, not a pure
renderer move, and it changes what a release-operand Action run can observe.

## PR 2 — aggregate policy into the manifest schema

**Status: implemented** (PR #779) — `aggregate_manifest_version` bumped to
`2.0`, the manifest's `gate` block, `project plan --gate-missing-required`/
`--gate-unexpected-target`, and `effective_policy` in the JSON output are all
in place; `--on-missing-required`/`--on-unexpected-target` exit `64`. The
`.abicheck.yml`-driven (rather than per-invocation-flag-driven) sourcing of
`project plan`'s gate flags is not part of this slice — see that command's
own `--gate-missing-required`/`--gate-unexpected-target` for the mechanism a
future `.abicheck.yml` key would feed the same way.

`aggregate` currently takes the expected target set from `--run-plan` /
`--manifest` / `--discovered-only`, but takes the *policy for violating that
expectation* from two separate CLI flags: `--on-missing-required fail|warn`
(default `fail`) and `--on-unexpected-target include|warn|fail|ignore`
(default `include`).

Expectation and the consequence of breaking it belong in one versioned
contract:

```json
{
  "aggregate_manifest_version": "2.0",
  "targets": [
    {"id": "linux-gcc", "required": true},
    {"id": "windows-msvc", "required": true}
  ],
  "gate": {
    "missing_required": "fail",
    "unexpected_target": "include"
  }
}
```

**This must be a MAJOR bump (`2.0`), not an additive `1.1`.**
`aggregate.py`'s `_check_manifest_version` only rejects a MAJOR component
*newer* than `AGGREGATE_MANIFEST_VERSION` (currently `"1.0"`) — it accepts any
`1.x`, on the stated assumption that a MINOR bump is additive-only within a
MAJOR. `ExpectedTargets.from_manifest_data` bears that assumption out today:
it reads `targets`/`head_sha` and silently ignores any other key. Put `gate`
in at `1.1` and an old `1.0`-vintage `aggregate` binary reading the new
manifest passes the version check (`1 <= 1`), never reads `gate` at all, and
silently falls back to the hardcoded defaults (`missing_required: fail`,
`unexpected_target: include`) — which can be exactly the wrong policy the
manifest asked for, misapplied with no error. Since a *silently wrong gate
decision* is worse than a *loud rejection*, the new field must ship at a
MAJOR an old reader is guaranteed to reject: `major > supported` raises
`AggregateError` instead of falling through. Publish `gate` at
`aggregate_manifest_version: "2.0"` and bump `AGGREGATE_MANIFEST_VERSION`
accordingly, so an old reader fails loud rather than misapplying policy.

`project plan` emits the same fields into `run-plan.json` from its own
`--gate-missing-required`/`--gate-unexpected-target` flags (implemented: CLI
flags only — `.abicheck.yml`-driven sourcing of the same fields is future
work, not part of this slice; see the "Status: implemented" note above).

- Defaults are unchanged (`missing_required: fail`, `unexpected_target: include`)
  when the manifest omits `gate`.
- `--discovered-only` stays, as the explicit escape hatch with no expected set;
  under it neither policy is applicable and supplying them is a usage error.
- The aggregate JSON records what actually applied:

  ```json
  {"effective_policy": {"missing_required": "fail",
                        "unexpected_target": "include",
                        "source": "run-plan"}}
  ```

Because this is a schema change: bump the manifest schema version and keep
reading the older version. Implemented: validation is Python-level
(`aggregate_manifest.py`'s own parser rejects a malformed/version-mismatched
`gate` block with a clear `AggregateError`), not a separate published JSON
Schema file — the manifest's `{targets, gate}` input shape is small and has
no external-tooling consumers analogous to the aggregate *report*'s
(`aggregate_report.schema.json`, which is packaged/published and does gain a
`gate`-adjacent `effective_policy` entry). A machine-readable manifest input
schema remains future work if an external producer needs one, not part of
this slice.

**Follow-up, explicitly still open (review):** the policy moved to the right
*lifecycle stage* (plan time, versioned, alongside the expected target set) but
not yet to durable *project configuration*. Today it is still restated per
invocation, via `project plan --gate-missing-required` /
`--gate-unexpected-target`. A stable gate policy should not have to be re-typed
on every `project plan` run:

```yaml
# .abicheck.yml
aggregate:
  gate:
    missing_required: fail
    unexpected_target: include
```

Once that key exists and `project plan` sources it, the two `project plan`
gate flags should be removed too, leaving that command's CLI to project config,
build outputs, head/project identity, and output. This is a small, independent
slice — it does **not** block any other PR here, and it is deliberately not
folded into PR B/PR G's configuration convergence, whose subject is the
compare/scan gate, not the aggregate target-expectation gate.

**Tests:** manifest-supplied policy; run-plan-supplied policy; default policy;
missing required target; unexpected analyzed report; unreadable unexpected
report; `effective_policy` preserved in aggregate JSON; incompatible manifest
schema rejected; the removed flags exit `64`.

## PR 3 — build execution moves into trusted config

**Split into three slices by the post-#782 review.** PR #782 fixed a real
defect — a `dump` baseline and a `scan --against` candidate given the *same*
`compile_commands.json` could resolve to non-comparable `CompileContext`s,
because `dump` stored L3 evidence without applying it to its own L2 header
parse — and it fixed it *additively*, by calling one shared folding primitive
from three existing paths. That closed the symptom and simultaneously proved
the structural problem: there are still three resolution paths.

```text
compare's implicit dump  → resolve_side_snapshot (typed, shared)
native `dump` CLI        → cli_dump_helpers.perform_elf_dump (its own pipeline)
scan candidate           → scan_engine._build_new_snapshot (a third)
```

Deleting `--build-query`/`--build-compile-db` before those converge would move
the inputs into config while leaving three places that interpret them, so:
config could apply differently in the CLI and the typed API, `--dry-run` could
print a context the real run does not use, the L3→L2 fold could diverge again,
and every future context field would need hand-threading through three
pipelines a fourth time.

- **PR 3A — typed `dump` *and `scan`* convergence (the review's PR C) — both
  remaining resolvers, not just one.** An earlier draft of this section scoped
  3A to `dump_cmd`/`perform_elf_dump` alone, which would still leave
  `scan_engine._build_new_snapshot` as a second, independent interpreter of
  the same `.abicheck.yml` build input once 3C removes the CLI flags — the
  identical structural divergence this whole section exists to close, just
  moved from a `dump`-vs-`compare` mismatch to a `dump`-vs-`scan` one
  (Codex review). Both must converge in this slice: one canonical path,
  `Click parsing → DumpRequest → ResolvedDumpRequest → dry-run or execution →
  DumpResult`, with `dump_cmd`/`perform_elf_dump` **and**
  `scan_engine._build_new_snapshot` both routing through
  `service_dump_pipeline.run_dump_request` (or the per-input primitives it
  shares with `resolve_side_snapshot` — see `service_input_resolution.py`),
  the way `compare`'s implicit-dump operand already does. `DumpResult` states
  the snapshot, requested vs. effective depth, the resolved compile context,
  the build-query decision, the source scope, the dependency scope,
  diagnostics, and the storage result — and `--dry-run` renders *that
  object*, so the printed plan and the executed run cannot disagree. The root
  `AGENTS.md` "Known gaps" entry on `service_dump_pipeline.py` is the same
  migration seen from the code side; that entry does not yet mention the scan
  side explicitly, so this plan is the tracking place for that half until it
  does.
- **PR 3B — build-context completeness (the review's PR D).** Two #782
  follow-ups that change the *parsed public surface*, not just performance, so
  they belong before the model is called finished: (1) compile-unit matching —
  the L2 include-dir seed is still gathered from *every* `CompileUnit` rather
  than the matched one(s), so an unrelated TU's colliding generated header can
  shadow the matched TU's; (2) forced includes — `-include`, `-imacros`, `/FI`,
  `/FU` are absent from `ABI_RELEVANT_FLAG_PREFIXES`, so a matched unit's
  macro-controlling forced-include header never reaches the derived L2 context
  even though the run reports a match and stamps `parsed_with_build_context`.
  Both are already recorded as known gaps in the root `AGENTS.md`.
- **PR 3C — the removal itself** (everything the rest of this section
  describes), landing only after **all three** resolvers converge — 3A (both
  `dump` and `scan`) and 3B. 3C must not land on 3A-covers-`dump`-only; a
  `.abicheck.yml` build input used for a `dump` baseline and a
  `scan --against` candidate needs one interpreter, not two, before the flags
  that configure it are removed.

`dump --build-query` and `dump --build-compile-db` describe how the *project*
is built, not what this snapshot is. They are already documented as CLI
equivalents of the `.abicheck.yml` `build.query` / `build.compile_db` fields,
and `--build-query` is a **trusted executable command**, not a data path.

Kept on the CLI (genuine per-run inputs): `--build-info`, `--build-target`,
`--compile-db-filter`.

Config form — **the existing string syntax**, so the removed flags have a
replacement that works the day they are removed:

```yaml
build:
  query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
  compile_db: build/compile_commands.json
```

`build.query` is a string today: `BuildConfig.from_dict` reads it through
`_str()` (a YAML list is silently coerced to `""`, i.e. dropped rather than
rejected), and `inline.py` splits it with `shlex.split` at the point of use. An
argv-list form would be *nicer* — it removes a quoting layer from a value that
is executed — but it is a config-contract change, not a docs example. If PR 3
wants it, it must be its own scoped slice: accept both forms (string kept
working, list added), regenerate the schema and config reference, add migration
tests for both, and keep the trust gating identical for both. Do **not** switch
the parser to lists only — every existing trusted string config would break.

**Prerequisites, all required before the flags are removed:**

1. Only an explicitly-passed `--config` may authorize executing `build.query`.
2. An auto-discovered `.abicheck.yml` never executes a query.
3. `dump --dry-run` prints the exact argv, the cwd, the resulting compile-DB
   path, and why the query will or will not run.
4. A source-depth request produces an actionable error only when the
   requested rung is genuinely unreached — **not** merely on "no compile DB
   present." A raw compile-database `--build-info` never carries L4 facts by
   itself, but a *pack-shaped* `--build-info` (e.g. from a previous
   `collect`, or the abicheck-cc wrapper) can carry its own `source_abi` and
   satisfy `--depth source` with no compile DB at all — already a supported,
   tested path (`tests/test_dry_run_contract.py::
   test_depth_source_with_prebuilt_pack_build_info_does_not_block`), and the
   suggested remedy in the error message is itself `--build-info`, so the
   check must not reject the thing it's telling the user to do. Gate the
   error on the existing achieved-depth check (whether the resulting
   snapshot's evidence actually reaches the requested rung), not on compile-DB
   presence: *"source depth requested, compile DB missing; configure
   `build.query` or provide `--build-info`"* only when neither a compile DB
   nor another source-evidence provider (a pack's own `source_abi`) satisfies
   the requested depth.
5. Docs carry a minimal throwaway-config example, so the removed flags have a
   one-paste replacement.

**Risk:** medium — this is a trust boundary, and it is the one item here where
a mistake is a security regression rather than a UX one.

## New since the plan was written — `--require-complete-analysis`

#780 added `--require-complete-analysis` to `compare` and `scan --against`. It
does not touch the compatibility verdict; it contributes exit `1` when
`analysis_assurance.status != complete`, folded with `max` exactly like
`--contract`'s coverage axis. **Keep it** — it answers a real and distinct CI
question ("was this comparison complete enough to trust at all?"), and it is
not a duplicate spelling of anything.

What it must **not** become is a permanent CLI-only axis. Today it is not part
of `CompareRequest`/`ScanRequest`, the release/package fan-out rejects it
outright, `project`'s run plan cannot express it as durable policy, and gate
packs cannot set it. That is one boolean per assurance dimension, decided at
the CLI, and it does not scale: the next assurance dimension would arrive as a
second such boolean.

So fold its *semantics* into the typed gate/assurance policy PR B/PR G build —
**one persisted key, not two**: an earlier draft of this section showed
`assurance.required_status` and `gate.require_complete_analysis` as
interchangeable spellings, which recreates exactly the duplicate-configuration
surface this cleanup exists to remove and leaves schema authors to guess which
one is canonical (Codex review). Pick `assurance.required_status` — it is the
extensible form (a status enum, not a boolean, so a future partial-assurance
tier is a new value, not a new key) — and treat everything else as an alias
into it, never a second key:

```yaml
assurance:
  required_status: complete
```

**The unset/disabled case must be pinned explicitly, not left implicit
(Codex review).** `AssuranceStatus` (`analysis_assurance.py`) is already a
five-value *observed*-status enum — `complete`, `partial`, `failed`,
`not_comparable`, `not_requested` — and `not_requested` there names something
`analysis_assurance.status` genuinely comes back as. `assurance.required_status`
is a different axis: a configured *threshold*, not an observation, so it must
not reuse `not_requested` (or any other observed-status spelling) to mean "no
gate configured" — that would make one string do double duty as both a real
status value and an off switch, and risks gating every default invocation the
moment the field is merely present with an unexpected value. The field is
**optional and absent by default**: unset (`null`/key omitted) means exactly
what today's ungated behavior means — `analysis_assurance_exit_contribution`
returns `0` regardless of the observed status, matching
`--require-complete-analysis` never having been passed. Only an explicit
`required_status: complete` (today's one real tier; a future looser or
stricter tier is a new value here, not a new key) activates the gate. Pin
this — the optional field, its `null` default, and its serialization — in the
config schema and in tests before PR B ships it, not as an implementation
detail discovered while wiring the resolver.

`--require-complete-analysis` stays as the CLI spelling (the advanced one-run
override), but resolves into `assurance.required_status: complete`, not into a
parallel `gate.require_complete_analysis` field — the boolean is a resolver
*input alias*, not a second place the value can live. Ordinary precedence
`explicit CLI > run plan > project config > built-in default` resolves to one
field (`resolved_gate.assurance_required_status`) that the **comparison-
producing** front ends read directly: `compare`, `scan --against`,
the release compare, and `check-target`.

**`aggregate` does not join that list, and must not (Codex review).**
`aggregate.py` already has this invariant for the sibling contract-coverage
axis, stated in `_contract_coverage_exit`'s own docstring: "read, not
recomputed... a per-target run that accepted incomplete coverage must not
have the aggregate re-impose it." `_analysis_assurance_exit` follows the same
rule for this axis, folding each report's own persisted
`analysis_assurance_exit_contribution`. That has to stay true here too — a
matrix leg and the trailing `aggregate` invocation can resolve differing
project/run-plan settings, so if `aggregate` recomputed
`resolved_gate.assurance_required_status` from its own config instead of
folding what each report already decided, two failure modes open at once: a
target report that legitimately exited `1` could aggregate green (its
contribution silently overridden by a looser aggregate-time policy), or a
report that explicitly accepted partial assurance could be newly failed
downstream by a stricter one. If a future need for `aggregate` to *verify*
consistent assurance policy across targets emerges, that is a check against
PR B's per-report effective-configuration digest — not PR 2's
`effective_policy` (that field is `aggregate`'s own `missing_required`/
`unexpected_target` target-expectation policy, per
`aggregate_report.schema.json`, and carries no assurance information to
compare) — and never a value the aggregate derives from its own gate policy
and substitutes for what each report already recorded.

**Do not add a further standalone boolean for any new assurance dimension**
on the comparison-producing side — extend the `assurance.required_status`
value set, or add a sibling key under the same `assurance` namespace, never a
second top-level spelling of the same fact.

## PR 4 — one gate algorithm (`--exit-code-scheme` removal)

**This is the item the original draft got wrong, and it gets its own ADR.**

`--exit-code-scheme auto|legacy|severity` is not a spelling choice; it selects
between two gate algorithms with genuinely different outcomes:

| | compatibility-based | severity-based |
|---|---|---|
| clean | `NO_CHANGE`/`COMPATIBLE`/`RISK` → 0 | no error-level findings → 0 |
| | — | addition/quality error → 1 |
| source break | `API_BREAK` → 2 | potential-breaking error → 2 |
| ABI break | `BREAKING` → 4 | ABI-breaking error → 4 |

A compatible addition can block CI under one and not the other; a policy can
demote a real ABI break to `0` under the severity scheme. Contract coverage and
analysis assurance add their own orthogonal `1` on top
(`contract_coverage_exit.py`, folded with `max`). Deleting the selector without
defining the successor algorithm silently changes users' CI outcomes.

**Decision to encode in the ADR:** remove the *manual algorithm selector*, keep
both axes, and make today's `auto` the only behaviour:

```text
no gate/severity policy configured  → compatibility verdict decides 0/2/4
severity preset, severity config,
or gate pack in effect              → resolved GateDecision decides 0/1/2/4
```

The user configures *policy*; they no longer choose an implementation.

The report keeps stating both halves explicitly, so the number is explainable:

```json
{"verdict": "COMPATIBLE",
 "gate": {"exit_code": 1, "blocking": true,
          "reason": "addition configured as error"}}
```

**Two prerequisites the post-#780 review adds, both before the flag is
removed.**

**(1) Pack parity — the review's PR B, and the harder blocker.** `--pack` is
accepted by a single-pair `compare`, rejected by the release fan-out, and only
partially honoured by `scan` (`cli_scan.py` rejects a gate pack outright,
having no gate of its own — see the root `AGENTS.md` on `pack_application.py`).
A pack can assign `gate.exit_code_scheme` and `gate.severity.*`. Removing
`--exit-code-scheme` while pack resolution still differs per front end leaves
no answerable question about how a legacy pack migrates: the answer would be
"depends which command reads it." Land first: packs resolved **once** into one
immutable effective configuration (one `CompatibilityEvaluationConfig`, one
`GateOptions`), the same object used by `compare`, the release fan-out, `scan`
and the Action, with the same effective-config digest recorded in every
report. This is also PR 1b/E's prerequisite, which is why it sits early in the
reviewed ordering rather than inside PR 4.

**(2) One canonical `ExitDecision` — the review's PR G proper.** Since #780
this is no longer two axes but a set of them, and a flat `max()` is not the
whole rule: a non-comparable pair must dominate every other axis; scan budget
overflow (`5`) is scan-only; usage error (`64`) is not a result at all and
never appears in a report; and exit `1` has several distinct causes that a
bare number cannot tell apart. Encode it once:

```python
@dataclass(frozen=True)
class ExitDecision:
    code: int
    reasons: tuple[ExitReason, ...]
    compatibility_contribution: int
    policy_gate_contribution: int
    contract_coverage_contribution: int
    analysis_assurance_contribution: int
    target_coverage_contribution: int
```

with an explicit precedence resolver, pinned by the ADR and by tests rather
than distributed across CLI callbacks — **precedence order, not a renumbering**:

```text
usage/config error          (outside the report entirely — 64 everywhere)
not comparable               (dominates every result axis below)
budget exceeded              (scan only)
removed required library  ─┐ mode-dependent (see below) — NOT a fixed rank
ABI / API / policy gate    ─┘
coverage & assurance floors (max-folded, never lowering the above)
clean
```

**Removed-required-library's rank is not fixed, and an earlier draft of this
table got that wrong** (Codex review): today's contract
(`docs/reference/exit-codes.md`'s release table,
`tests/test_compare_release.py::test_removed_and_breaking_exits_4_not_8`) is
mode-dependent, not a constant precedence slot —

- **legacy scheme** (no severity map in effect): an ABI/API break or an
  operational `ERROR` wins; removed-library is checked only when neither
  applies.
- **severity-aware scheme** (a severity map is in effect): removed-library
  takes precedence over the aggregated `0/1/2/4`.

`ExitDecision`'s resolver must reproduce this switch, not collapse it into one
row — the existing legacy-mode test (`4`, not `8`, for a release with both a
breaking pair and a removed library) is exactly the behavior PR G is
forbidden from changing, since it is a consolidation of the *algorithm
selector*, not a migration of release exit semantics.

**`ExitDecision` unifies the precedence, not the numbers, and this is a hard
constraint on PR G, not a detail** (Codex review of this plan caught an earlier
draft that wrote a single global number per row). Every command keeps its own
exit-code scheme, and `docs/reference/exit-codes.md` already says so
explicitly: a non-comparable pair is `16` for native `compare`, **`6`** for
`scan --against`, and `9` for `compat check`. A resolver that emitted one
number for that row would silently renumber `scan`, breaking every script and
Action consumer that recognises `6` — while PR G is framed as removing the
*algorithm selector*, not as a command-numbering migration. So the resolver
answers "which reason wins", and each command's own code table maps the winning
reason to its own number; renumbering, if ever wanted, is a separate, explicitly
designed and documented breaking change with its own migration note.

`reasons` is what makes a shared `1` explainable, and it is the same block PR E
has the Action read instead of inferring from stderr — see the split of PR G
into **G1** (the decision object and its report block, no CLI change, landing
*before* PR E) and **G2** (one gate algorithm, `--exit-code-scheme` deleted) in
"Ordering". `docs/reference/exit-codes.md` becomes a rendering of this resolver
plus each command's own mapping, not a parallel hand-kept table.

**Atomic change set:** remove the flag from `compare` and `scan`; migrate the
Action side — there is **no** `exit-code-scheme` input in `action.yml` to
remove, so the real work is auditing `extra-args` usage in first-party
workflows, recipes and docs, and correcting `action.yml`'s own prose (its
`verdict` output description names `--exit-code-scheme` today), with a
policy-oriented input introduced only if a caller genuinely needs one; remove
or replace `.abicheck.yml`'s `exit_code_scheme`;
make packs state gate *policy* rather than select an algorithm
(`pack_application.py` currently reads a resolved `gate.exit_code_scheme` —
that read becomes a policy read); record the effective gate mode in
report provenance; update CLI, typed Python API, Action and `aggregate` parity
tests together.

**Tests must separate the axes** — a single `exit_code == expected` assertion is
not enough. Assert independently: compatibility verdict, gate decision, process
exit, contract-coverage contribution, analysis assurance.

**Risk:** high (behavioural, user-visible in CI). Sequence it **after** the
mechanical PRs so a bisect over a red CI job lands on this PR unambiguously.

## PR 5 — `scan --artifact-set` refinement (not removal)

The draft proposed dispatching on the operand type:

```text
scan file.so   → single-artifact scan
scan directory → artifact-set scan
```

**Rejected.** ADR-056 deliberately refused exactly this. `scan ARTIFACT` has
always meant one artifact; a directory can hold dozens of DSOs; scope and cost
change by an order of magnitude; set mode is audit-only and incompatible with
`--against`. Making that switch implicit hides a scope expansion that today is
visible in the command line and in CI logs.

What is actually worth changing is the *value syntax*. The comma-separated form
(`_resolve_artifact_set_paths` in `cli_scan.py`) is the weak part:

```bash
# today
abicheck scan --artifact-set a.so,b.so,c.so

# proposed: repeatable option
abicheck scan --artifact-set a.so --artifact-set b.so --artifact-set c.so

# unchanged
abicheck scan --artifact-set directory/

# optional, for bundles needing stable IDs / expected-provider ownership
abicheck scan --artifact-set-manifest set.json
```

Invariants that stay, extended to cover the optional manifest form too: exactly
one of positional `ARTIFACT`, `--artifact-set`, or `--artifact-set-manifest`
(three mutually exclusive operands if the manifest form ships, not two); each
of the two set-mode operands is incompatible with `--against`, the same
audit-only rejection applying to the manifest form as to `--artifact-set`
itself. If `--artifact-set-manifest` is not implemented in this PR, state that
explicitly rather than leaving the invariant silently ambiguous about a form
the surrounding text already shows as a usage example.

**Sequencing note:** the syntax cleanup is lower value than finishing set-mode
*semantics* — expected provider DSO, a symbol moved between sibling libraries,
duplicated providers, L4 symbol reconciliation, cost estimation, and a
machine-readable dry-run. Do the semantics first if the two compete. The
review reaffirms this and sharpens it: the *only* part of this section that is
worth doing on its own is replacing the comma-separated value with a repeatable
option. `--artifact-set-manifest` is worth adding only when it carries a real
domain contract — member identity, expected-provider ownership, external
providers, cohort — never merely as nicer syntax. `--artifact-set` itself is an
explicit safety boundary, not surplus surface; it stays.

## Merge criteria for every removal PR here

**CLI**

- The old spelling errors with `No such option`, exit `64`. No hidden alias.
- `--help` / `--help-all` regenerated.
- The root command tree is unchanged (`tests/test_cli_root_surface.py`).

**Front-end parity** — updated in the same PR: CLI, typed Python API (where
applicable), the composite Action, reusable workflows, Agent Skills
(`skills-src/` + regenerated trees), generated CLI/Action references.

**Machine contracts** — when a manifest or report changes: schema version bump,
packaged *and* documented schema copies, JSON Schema validation, an explicit
backward-reading decision, effective values in provenance.

**Semantics** — assert compatibility verdict, gate decision, process exit,
contract coverage and analysis assurance *separately*.

**CI** — Linux, Windows, macOS, Action tests, the `cli-contract` gate and the
docs/schema gates all green.

## Out of scope / explicit non-goals

- No deprecation aliases or transition window (same stance as #770).
- No new root commands (ADR-043/ADR-054 admission bar).
- `--report-mode` does not become a general renderer-switch namespace.
- `scan DIRECTORY` does not silently become an N-artifact analysis.
- The gate/verdict separation is not collapsed.
- The ABICC `compat` dialect stays frozen.
- No further mechanical option-count reduction after this phase. The remaining
  advanced options are load-bearing; subsequent work should go to `--pack`
  parity, multi-DSO ownership, and contract-evaluation precision instead.

## Ordering

The original ordering (kept below for reference) sequenced by risk. The
post-#780/#782 review re-sequences by *contract convergence*: every remaining
removal depends on a shared contract that does not exist yet, so the
convergence work comes first and each deletion becomes the last step of the
slice that made it safe.

**Current, authoritative sequence:**

```text
PR A  repository governance          = PR 0B — required checks / Ruleset,
                                       exact-merge-SHA verification
PR B  effective configuration parity — packs resolved once into one
                                       CompatibilityEvaluationConfig +
                                       GateOptions, shared by compare /
                                       release / scan / Action, digest in
                                       every report
PR C  typed dump+scan convergence     = PR 3A — DumpRequest →
                                       ResolvedDumpRequest → DumpResult, one
                                       resolver for dump CLI/Python/Action
                                       *and* scan_engine's candidate
                                       resolution, JSON dry-run rendered
                                       from that object
PR D  build-context completeness      = PR 3B — matched compile-unit
                                       selection, forced includes, provenance
                                       tests
PR G1 canonical exit decision, part 1 — ExitDecision + reasons + the report
                                       block, precedence pinned by ADR and
                                       tests; NO CLI change, no flag removed.
                                       Lands before PR E, which consumes it
PR E  Action machine-report           = PR 1b — uncapped persisted release
                                       findings, no comparison re-run, no
                                       stderr inference; reads G1's block
      └─ then DELETE --annotate, --annotate-additions
PR F  trusted build config            = PR 3C — build.query executes only
                                       from an explicit --config (a data
                                       path like build.compile_db carries no
                                       such restriction), trust receipt in
                                       --dry-run, fail closed
      └─ then DELETE dump --build-query, dump --build-compile-db
PR G2 canonical exit decision, part 2 = PR 4 — one automatic gate algorithm,
                                       schema / report / Action parity
      └─ then DELETE --exit-code-scheme
PR H  artifact-set semantics          = PR 5 — provider ownership, moved and
                                       duplicated symbols, cost and dry-run;
                                       syntax refinement last
```

Independent of the chain, unblocked at any time: PR 1 (**done**), PR 2
(**done**, minus its `.abicheck.yml` gate-policy sourcing follow-up).

**Superseded original ordering:**

```text
PR 0   green CI + required checks     (prerequisite)
PR 1   presentation                   (low risk)
PR 1b  annotations → Action           (medium risk, needs the persistence prerequisite first)
PR 2   aggregate policy schema        (schema, MAJOR version bump)
PR 3   build execution → trusted config (trust)
PR 4   gate semantics + ADR           (behavioural, highest risk)
PR 5   artifact-set syntax refinement (optional, after set-mode semantics)
```

The governing principle behind the change: **stop the mechanical CLI cleanup
until the typed resolution, configuration, gate and report paths are unified.**
The remaining flags are not the risk; several parallel implementations of the
same contract are. Deleting a flag that three code paths interpret differently
does not remove the divergence — it removes the only place a user could see it.
