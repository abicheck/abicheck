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
presentation-only, PR 1b **landed** (annotations moved to the Action — see
its own section below), PR 4 changes what a CI job's exit code means.

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
>
> **Update (2026-08-27, fresh re-review, `main` at `327df7b`, after
> [#883](https://github.com/abicheck/abicheck/pull/883)).** Re-checked every
> claim in this plan against current `main` rather than trusting the prior
> checkpoints' status cells. Confirmed unchanged: PR 1 (presentation) and PR 2
> (aggregate policy) are done — **and so is PR E/1b (annotations moved to the
> Action), missed by this checkpoint's own summary and caught in a later docs
> sync pass verifying against `abicheck/cli.py`/`action.yml` directly: no
> `annotate` reference remains in the CLI, and `action.yml` carries real
> `annotate`/`annotate-additions` inputs. See PR 1b's own section, whose
> "blocked on a persistence prerequisite" subtitle was itself stale until
> that pass corrected it.** Pack parity now covers `compare`, the
> release fan-out, *and* `scan --against` for both policy/contract
> assignments and `gate.*` fields (PR B's scope is wider than the 2026-08-16
> update above states — see that section for the current breakdown); the
> PR 3B build-context-completeness gaps (forced pre-includes, matched-unit
> include scoping) are closed. #883 itself fixed a real bundle-facts gap:
> `PolicyFile` now reaches both per-library *and* bundle-level verdicts (it
> previously reached only per-library), the JSON resource budget is now
> applied uniformly to archive and plain-JSON bundle-facts input, and
> `DEFAULT_SYSTEM_PROVIDERS` grew several more vendor runtimes (oneTBB,
> oneMKL, Intel runtime, Level Zero) — the latter is explicitly a tactical
> fix, not a real topology model for bundle-level system-provider
> classification. **Correction (2026-08-28): this was never actually PR B's
> own scope** — PR B's two stated goals (pack parity, effective-config
> digest) are about *configuration* reaching every relevant CLI command
> path (typed-API pack parity is separate, outstanding work — see PR B's
> own "finalized" note below), not about bundle *verdict topology*; the
> growing-allow-list gap is recorded in the
> root `AGENTS.md`'s "Known gaps" section, pointing at `g42-check-identity-
> environments-and-provider-resolution.md`'s own "Environment-aware
> system-provider resolution" design rather than sketching a competing fix
> (Codex review, PR #910, fresh evidence: an earlier revision of this note
> also pointed at a since-removed AGENTS.md entry for a *second*, "sibling"
> policy-override gap that #883 -- named two sentences above -- had already
> fixed; that stale entry is gone, this note no longer claims it) — see PR
> B's own "finalized" note below for why the remaining topology gap doesn't
> block PR B closing.
> **PR 0B/P0 is still the single outstanding item with no code-side gap
> left** — see its status note below for the ready-to-apply Ruleset
> artifact this pass added. **PR C's real ELF run is now migrated** onto
> `execute_dump_request` — see its own section's closing status note
> ("Slice landed: the real ELF run is migrated") for the account; the
> section's own long investigation history above that note remains as the
> record of why this was not attempted sooner. PR C's PE/Mach-O half
> (`handle_non_elf_dump`) is unmigrated — no PE/Mach-O toolchain was
> available where this was done — and PR F/3C's removal itself still needs
> that half closed too, per 3C's own "all three resolvers" ordering rule
> (its own section, unchanged by this update).
>
> **Update (2026-08-16, later the same day).** PR G1 (canonical `ExitDecision`
> + report block) landed as [#789](https://github.com/abicheck/abicheck/pull/789)
> — out of the reviewed "Ordering" sequence below, which lists it after PR B/C/D,
> but that is harmless: G1 is purely additive (no CLI change, no flag removed),
> so it has no prerequisite on the others. PR B ("effective configuration
> parity") gained its first slice: `--pack`'s `policy.overrides`/`surface.
> internal_namespaces` contributions now apply uniformly to `compare`, `scan
> --against`, *and* the directory/package release fan-out (previously the
> release fan-out rejected `--pack` outright). See PR B's own section below for
> what this slice closed and what is still open (`gate.*` pack fields on
> release/scan, and the effective-config digest in every report).

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
| `--annotate`, `--annotate-additions` | **Removed** (PR 1b/E — done) | Options on `compare` alone, shared by both operand shapes (no CLI-level split was possible); the release-report persistence prerequisite landed and the flags are deleted — `compare --annotate` now exits `64` with `No such option`; see PR 1b |
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
| **PR 0B** | Required checks / Ruleset + exact-merge-SHA verification (items 3–4) | **Code side implemented; the Ruleset toggle itself is still an outstanding manual step** — see status note below |

**PR 0B is the one to do first, and it is the review's PR A.** The branch API
still reports `protected: true` with
`required_status_checks.enforcement_level: off` and empty `contexts`/`checks`.
Unless an equivalent GitHub Ruleset supplies them, a red CI does not block a
merge — so PR 0A's green lanes are green by convention, not by enforcement.
Separately, the exact merge SHA of #782 did not run a full CI sweep on
`push: main` in the returned workflow set (AgentReady completed; Examples
Validation was still running; no separate full `ci.yml` run over that exact
SHA), which is precisely what item 4 exists to make detectable.

> **Status note.** Every code-side prerequisite for item 3 is implemented:
> `.github/AGENTS.md`'s new "Required-status-check configuration" section
> states the classification rule (superseding the hand-copied-list attempts
> below, kept here as the historical record of why a rule beat a list) and
> the concrete required-check name list it produces; `ci.yml` gained the two
> neutral-aggregate gate jobs (`docs-pr-required`/`test-action-required`),
> and `test-action.yml` gained the `test-action-summary` aggregate they
> wrap, all structurally guarded by
> `tests/test_required_checks_governance.py`. Item
> 4's `verify-merge-checks.yml` (push-to-`main` exact-merge-SHA
> verification) is implemented and tested the same way. **What remains is
> exactly one manual action**: an account with repository-admin access must
> actually configure the GitHub Ruleset (or classic branch protection) to
> require the check names `.github/AGENTS.md`'s section lists — no tool
> available to an automated PR reaches that surface (confirmed: neither the
> GitHub MCP server's tool set nor any CLI available in this environment
> exposes branch-protection/Ruleset administration). Once that toggle is
> flipped, PR 0B is fully closed.
>
> **2026-08-27: the admin action now has a one-command runbook, not just a
> classification rule to re-derive by hand.**
> `.github/branch-protection-ruleset.json` is the exact Rulesets API payload
> for the 14-name required-check list, and
> `.github/branch-protection-ruleset.md` is the apply/verify runbook (the
> `gh api` command, how to update rather than duplicate an existing ruleset,
> and — the part that actually closes this item — a negative-test procedure
> confirming a deliberately red required check blocks a merge, not just that
> the rule was configured). `tests/test_required_checks_governance.py`'s
> `TestBranchRulesetArtifact` keeps the JSON's context list mechanically in
> sync with `AGENTS.md`'s prose and `verify-merge-checks.yml`'s
> `REQUIRED_CHECKS` array, so this is a third checked copy of the list, not
> a fourth hand-copied one. This closes every part of PR 0B/P0 that can be
> closed from inside a PR; the ruleset still has to actually be applied and
> the negative test actually run by an admin before this item is done.

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

## PR 1b — annotations move to the Action (done)

**Status: fully landed** (all three steps in "What this means for
sequencing" below, plus the `scan --against` half of PR E) — verified
directly against `abicheck/cli.py` (no `annotate` reference remains) and
`action.yml` (`annotate`/`annotate-additions` are now real, documented
inputs). This heading's original "(blocked on a persistence prerequisite)"
qualifier is stale; kept as a section title change rather than rewritten
prose below, since the body already records each landed slice with its own
dated status note.

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

   **Status: all three steps landed.** Step 1's report persistence and
   step 2's release `--write` support merged first (see the "Landed since
   the paragraph above was written" note further up this section); step
   3 — deleting the flags, adding the `action.yml` inputs, implementing
   `action/run.sh`'s renderer, and updating first-party workflow/recipe/
   doc snippets — landed in the same PR. `compare --annotate` now exits
   `64` with `No such option`, matching the sentence a few paragraphs
   below this one.

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

**Not a proposed common envelope — (Codex review) `compatibility.verdict`/
`findings` below were an illustrative shorthand, and neither exists in
`compare_report.schema.json` (which is top-level `verdict`/`changes`) nor in
scan's or release's own, differently-shaped report structures.** Introducing
those spellings for real would be a major-version schema migration this PR
does not undertake, and would leave two sources for the same verdict/finding
set free to drift. What actually generalizes across all three operand shapes
is the *concept* of three fields — `exit`, `contract_coverage_exit_
contribution`, `analysis_assurance_exit_contribution` — not yet a uniform
*location* for them today. `compare` puts all three at the report's top
level, **implemented and merged as #789 (`e43abfd`) — "PR G1" is done.**
**`scan --against` now does too (first half of PR E, landed).**
`ScanOutcome.to_dict()` (`scan_engine.py`) nests its whole `diff_summary`
under a `"diff"` key, and `_baseline_summary()`/`_run_baseline_compare()`
(`cli_scan_baseline.py`) already wrote `analysis_assurance_exit_contribution`
and the contract-coverage block *into that nested summary*, not the outer
`ScanOutcome` dict — so the location question above was answered by
precedent rather than reopened: `exit` was added at `report["diff"].exit`
(schema 1.18), matching where its own constituent fields already live,
**not** moved to the top level to match `compare`'s `report.exit` — the two
commands keep their own report shapes; only the *concept* is shared, exactly
as this section's own "not a proposed common envelope" note already argues
for `verdict`/`findings`. `_run_baseline_compare` calls
`exit_decision.resolve_compare_exit_decision` directly (the same function
`add_contract_context` calls for `compare`), so the two commands cannot read
two different numbers for the same kind of comparison. One scan-only wrinkle
this block does not, and structurally cannot, resolve on its own: a
maintainer-promoted `--crosscheck KEY=error` finding (`scan_engine.
_crosscheck_severity_exit`) can raise the *process* exit code strictly after
`_run_baseline_compare` already returned its `exit` block — the same
after-the-fact timing problem `_promote_published_gate` already solved for
the persisted `severity` block. Fixed the same way: `_promote_published_
gate` now also raises `diff.exit.code` and re-stamps `diff.exit.reasons` to
a new `ExitReason.PROMOTED_CROSSCHECK` (a scan-only reason;
`resolve_compare_exit_decision` itself never emits it) whenever a promotion
actually fires — never lowering, never firing when the existing code already
dominates. Budget overflow and `NOT_COMPARABLE` remain unmodeled by this
block, matching `exit_decision.py`'s own explicit scope (no `DiffResult`
exists for `NOT_COMPARABLE`; budget overflow aborts before a report is
built) — see that module's own docstring for the reasoning.

**The persistence prerequisite's single-library half is now also landed**
(schema 2.43): every `compare --format json` report carries a top-level
`annotations` array (`reporter_contract_blocks.add_annotations` /
`annotations.annotation_report_entries`) — one already-classified,
already-formatted entry per finding a full annotation pass over the
comparison found, always the superset (as if `--annotate-additions` had
also been given) regardless of what this run's own flags were. It reuses
`annotations.collect_annotations`/`_format_annotation` exactly (no second,
independently-maintained rendering path), so the persisted array and
`--annotate`'s stderr output can never disagree. `annotations.py` itself
had to stop importing `reporter.py` to land this without growing an import
cycle (`reporter -> reporter_contract_blocks -> annotations -> reporter`,
since `reporter_contract_blocks.add_annotations` now imports
`annotations.py`) — `emit_github_step_summary` moved to a new leaf module,
`annotations_step_summary.py`, since it was the one function in
`annotations.py` that reached back into `reporter` (for `to_markdown`).

**The persistence prerequisite's release-operand half is now also landed**
(the capped `findings` list stays, unchanged, as the small human-readable
summary it always was; this adds alongside it). Every `libraries[]` entry
in a directory/package `compare --format json` release report now carries
its own `annotations` array — the identical shape and identical
`annotation_report_entries` function the single-library slice above uses,
computed straight from that library's own already-produced `DiffResult`
(`entry["_diff_result"]`, stashed by the primary per-library pass, after
SONAME-lockstep-suppression has already run on it) rather than a second,
independent re-run of that library's comparison.
`_compare_release_libraries`'s own `--annotate` stderr rendering now reads
from the same primary-pass results too
(`_release_annotations_from_primary_pass`), so `_collect_release_extras` is
called only for JUnit's `collect_diff_results` (which genuinely needs the
old `AbiSnapshot` alongside the `DiffResult`, something the primary pass
never stashes) — `--annotate` on a release operand no longer re-runs any
library's comparison a second time. A Codex review round on the
single-library slice above also caught a genuine design gap this slice
closes for both operand shapes at once: `annotation_report_entries`'s
persisted `"notice"`-level entries conflated two different things — a
`--contract` finding compatibility policy never evaluated (shown by plain
`--annotate` alone) and an addition/quality-issue/`info`-severity finding
(shown only when `--annotate-additions` opts in) — so a renderer that
dropped every `"notice"` unless `annotate-additions` was on would have
silently hidden the former. Each entry now also carries `always_visible`
(schema 2.44): always `true` for `error`/`warning`, and for `notice` only
`true` for the always-shown contract-audit kind.

**`action/run.sh`'s `--write`-equivalent path is now also landed.** Rather
than a new bash-side code path, the actual gap was in the CLI itself:
`compare --write FORMAT=PATH` used to be rejected outright for a
directory/package operand (`_reject_set_input_flags`'s `secondary_fmt`
check). `compare_release_cmd` now carries its own
`secondary_output_options(["json", "markdown", "junit"])` (the same set
`--format` itself accepts for a release operand) and renders the
secondary format from the exact same already-computed
`library_results`/`diff_pairs`/`bundle_result`/`matrix_result` its primary
format uses — no second per-library comparison pass, mirroring how
single-pair `compare`'s own `--write` reuses its one `DiffResult`.
`_dispatch_release_compare` (`cli.py`) now explicitly rejects a
release-incompatible secondary format (`sarif`/`html`/`review`) with a
usage error, since `compare`'s own `--write` still accepts all six formats
at parse time (before dispatch ever reaches the release engine's own,
narrower `secondary_output_options` declaration) and `_format_release_
summary`'s fallback branch would otherwise have silently rendered markdown
into the requested path instead of erroring. `action/run.sh`'s `--write
json=$PR_JSON` injection for the sticky PR comment no longer skips
directory/package operands (the `_is_release_style_operand` guard on that
one injection was removed; the helper itself stays, used by every other
release-only flag check).

**Landed since the paragraph above was written**: the Action's own
renderer now reads the persisted `annotations` report field directly
(`action/run.sh`'s `_emit_annotations`, driven by the new `annotate`/
`annotate-additions` `action.yml` inputs) instead of inferring anything
from stderr or re-running a comparison, and works identically for a
single pair and a release fan-out. The CLI's own `--annotate`/
`--annotate-additions` flags (and every internal parameter/code path that
only existed to render them — `_maybe_emit_annotations`,
`release_annotations_from_primary_pass`, `_collect_release_extras`) have
since been deleted entirely; `abicheck compare`/`compare-release` no
longer accept them at all. A single-library compare report, unmodified by
this section, already reads:

```json
{
  "verdict": "COMPATIBLE",
  "contract_coverage_exit_contribution": 0,
  "analysis_assurance_exit_contribution": 1,
  "exit": {"code": 1, "reasons": ["analysis_assurance"]},
  "annotations": [],
  "changes": []
}
```

With that in place the Action does not parse stderr, does not re-run any
comparison, does not guess why the exit was `1`, renders annotations from the
persisted findings and the Job Summary from the same object, and behaves
identically for a single pair and a release fan-out — **including
`scan --against`, whose own `exit` block landed as PR E's first half (see
above: `report["diff"].exit`, schema 1.18)**, while still reading
`verdict`/`changes` (or that operand's own equivalent) unchanged. The `exit`
block is the
same object PR 4/G formalizes as `ExitDecision`. Build it once and consume it
once — which is why PR G is split, and the split was load-bearing for this
section rather than cosmetic: **G1** built the decision object and emitted its
report block (no CLI behaviour change, no flag removed) and landed *before* PR
E; **G2** makes today's `auto` the only gate algorithm and deletes
`--exit-code-scheme` (still open — see PR 4's own section). Without that split
PR E would have had to either depend on unlanded work or invent the second,
Action-shaped spelling this section prohibits — moot now that G1 landed
first, exactly as planned: PR E shipped both its annotations half and the
`scan --against` `exit` block, not merely the former.

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

**Follow-up: implemented.** The policy moved to the right *lifecycle stage*
(plan time, versioned, alongside the expected target set) in the slice above,
and now to durable *project configuration* too: `.abicheck.yml`'s optional
`aggregate: gate:` block (parsed by `buildsource/project_targets.py`'s new
`AggregateGateSpec`, validated against the same `OnMissingRequired`/
`OnUnexpectedTarget` enum vocabulary the manifest's own `gate` block is) —

```yaml
# .abicheck.yml
aggregate:
  gate:
    missing_required: fail
    unexpected_target: include
```

— is sourced by `project plan`, which stamps it onto the generated
`run-plan.json`'s own `gate` block exactly as before. The two `project plan`
`--gate-missing-required`/`--gate-unexpected-target` flags are removed (no
CLI alias, same "no deprecation window" stance as the rest of this cleanup),
leaving that command's CLI to project config, build outputs, head/project
identity, and output. `check-project.yml`'s own former `on-missing-required`/
`on-unexpected-target` workflow-call inputs (the pass-through this reusable
workflow used to convert into the now-removed CLI flags) are removed too —
set the policy in the project's `.abicheck.yml` directly. This was a small,
independent slice — it did **not** block any other PR here, and was
deliberately not folded into PR B/PR G's configuration convergence, whose
subject is the compare/scan gate, not the aggregate target-expectation gate.

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
  the way `compare`'s implicit-dump operand already does.
  **`ResolvedDumpRequest` and `DumpResult` are two distinct objects, not one
  renamed in transit (Codex review, fresh evidence — an earlier draft of
  this paragraph said `--dry-run` renders `DumpResult`, contradicting the
  investigation below the same paragraph now leads into).**
  `ResolvedDumpRequest` states the requested depth, effective collect mode,
  resolved header backend, and detected binary format — everything
  resolvable without invoking castxml/clang or writing anything — and is
  what `--dry-run` would render. **Not a hard parity guarantee for the
  backend field specifically (Codex review, fresh evidence — an earlier
  draft of this sentence claimed the printed and executed backends "cannot
  disagree", which the landed code's own class documentation on
  `effective_header_backend` explicitly disclaims):** `execute_dump_request()`
  deliberately forwards the *unresolved* `header_backend` (e.g. still the
  literal `"auto"`) to preserve `dumper`'s own runtime `auto`-specific
  routing (non-host `frontend_context`, the CastXML-to-Clang fallback) —
  see that field's own comment for why pre-resolving it before execution is
  a real regression, not a pin. `effective_header_backend` is therefore a
  best-effort projection of what resolution currently favors, not what
  execution is bound to use, and can still diverge if the environment
  changes between resolve and execute or if the unpinned-fallback path
  fires — the same class of imprecision the field's own docstring already
  accepts. `requested_depth`/`collect_mode`/`fmt` carry no such caveat: none
  of them are re-resolved at execution time the way the backend is.

  `DumpResult` states the executed outcome: the
  real snapshot, the *achieved* effective depth (only knowable from the
  completed snapshot), and — **landed 2026-08-19, updating this paragraph in
  place per Codex review rather than leaving a contradictory later status
  note** — the P0.3 L3→L2 fold's own `effective_includes`/
  `effective_compile_context`, now genuinely surfaced as fields (previously
  computed internally and discarded; see the "Slice landed (2026-08-19...)"
  note below this bullet for the exact shape and its own lifetime caveat).
  **Corrected ownership (CodeRabbit review, fresh evidence — an earlier
  draft of this sentence called the omitted fields "CLI-presentation-layer
  concerns `execute_dump_request()` doesn't touch", which overstated the
  gap):** the resolved compile context and the dependency scope genuinely
  *are* computed inside `execute_dump_request()`'s own call chain —
  `resolve_side_snapshot`/`_resolve_side_snapshot_impl` performs the P0.3
  L3→L2 compile-context fold internally (see `service_input_resolution.py`),
  and `populate_side_dependency_info` runs directly in
  `execute_dump_request()` when `follow_dependencies` is set. The compile
  context is now surfaced (this landed slice); the dependency scope is not
  yet a `DumpResult` field — still open, distinct from the compile-context
  gap this paragraph originally described. Only the ADR-039 build-context
  collector's own diagnostics are a genuine CLI-layer concern here — those
  post-processing passes live in `perform_elf_dump` alone, which
  `execute_dump_request` does not call (blocker 2 above).
  Execution consumes a `ResolvedDumpRequest` and produces a `DumpResult`;
  `--dry-run` never reaches that step. **The storage result is not part of
  the `DumpResult` this slice's `execute_dump_request()` produces (Codex
  review, fresh evidence — an earlier draft of this paragraph still listed
  it, contradicting the landed code and its own pinned test).**
  `service_dump_pipeline.py`'s own module docstring already scopes writing
  as CLI presentation/provenance layer, out of bounds for this module —
  `execute_dump_request()` genuinely never writes anything, so it has no
  storage result to report. A storage field is a real, separate addition
  for whichever future slice folds `cli.py`'s `fold_dump_provenance_into_json`
  write step into this pipeline (not attempted here) — until then, treat
  `DumpResult` as snapshot + achieved depth + effective includes/compile
  context, no storage result. See the follow-up investigation below this
  bullet for the concrete function split
  (`resolve_dump_request`/`execute_dump_request`) and why `run_dump_request`
  itself keeps returning a bare `AbiSnapshot`. The root `AGENTS.md` "Known
  gaps" entry on `service_dump_pipeline.py` is the same migration seen from
  the code side; that entry does not yet mention the scan side explicitly,
  so this plan is the tracking place for that half until it does.

  > **Investigated in depth; one real, scoped slice landed, the full
  > convergence NOT attempted (2026-08-16).** `service_input_resolution.
  > _seeded_includes`/`_seeded_compile_context` — the per-input primitive
  > `compare`'s implicit-dump operand and `dump`'s typed API
  > (`run_dump_request`) already share — ran the L2 include-dir seed and the
  > P0.3 L3→L2 compile-context fold as two independent `collect_inline_pack()`
  > calls, diverging from `seed_includes_and_fold_compile_context()`, the one
  > combined primitive the three CLI-side resolvers (`perform_elf_dump`,
  > `handle_non_elf_dump`, `scan_engine._build_new_snapshot`) already
  > converged on for exactly this reason (self-deadlock risk under an inferred
  > build query — see the L3→L2-fold "Known gaps" entry's own fifth finding).
  > Fixed: merged into one `_seeded_includes_and_compile_context()`, using the
  > same shared primitive the other three call sites use — closing one real
  > piece of drift, verified by the existing test suite plus `mypy`/`ruff`.
  > **The rest of PR 3A — routing `perform_elf_dump`/`handle_non_elf_dump`
  > and `scan_engine._build_new_snapshot` themselves through
  > `run_dump_request`, and making `dump --dry-run` render a real
  > `ResolvedDumpRequest` (Codex review, fresh evidence — an earlier
  > draft of this note named `DumpResult` here, which the correction two
  > sections above this one already retracted: `--dry-run` renders the
  > resolve-only object, never the executed one) — was not attempted**, for
  > three concrete reasons found by reading the code, not assumed: (1) `dump
  > --dry-run` (`render_dump_dry_run`) is today a hand-written *second*
  > implementation, not a dry pass of the same resolver — `run_dump_request`
  > has no "resolve without executing" mode to render from yet; (2)
  > `perform_elf_dump` runs three post-processing passes after the primary
  > snapshot (ADR-039 build-context collector, the G31 header-graph second
  > pass, the optional clang-layout-tool attach), each with its own
  > hard-won correctness fixes already recorded in `AGENTS.md`'s L3→L2-fold
  > entry (findings 9/10/17/18), and `run_dump_request` has no equivalent
  > hook; (3) `scan_engine._build_new_snapshot`'s side-aware `-H
  > old=PATH`/`-I old=PATH` baseline-reuse decision (findings 12/13/15 on
  > the same entry) is inherently about *two* snapshots' relationship,
  > which the single-input `DumpRequest` shape cannot express without a new
  > pair-aware primitive — exactly the class of decision `service_
  > compare_pipeline.py`'s own docstring already says was deliberately kept
  > out of the per-input layer. Each of the three is a real, separate,
  > multi-file design on its own, not a follow-up edit to this slice; see
  > the root `AGENTS.md` "Known gaps" entry (search "PR C (typed
  > `dump`/`scan` convergence") for the full accounting, including why
  > forcing any of the three under continued session pressure risks
  > reopening one of the already-fixed findings this same area has needed
  > many prior review rounds to reach.
  >
  > **Follow-up investigation (2026-08-18), no code change.** Re-read
  > `resolve_side_snapshot`/`_seeded_includes_and_compile_context`
  > (`service_input_resolution.py`) against `scan_engine._build_new_snapshot`
  > and `run_dump_request`'s existing callers directly, to check whether any
  > of the three blockers above could be narrowed safely in one more pass.
  > Two additional, concrete obstacles confirmed, neither previously spelled
  > out at this level of detail:
  >
  > 1. **`_build_new_snapshot` cannot call `resolve_side_snapshot` as-is,
  >    independent of the pair-aware baseline decision (finding 3 above) —
  >    and this gap is scoped narrower than an earlier draft of this note
  >    claimed (Codex review, fresh evidence).** `resolve_side_snapshot`
  >    takes a typed `InputSpec`/`SideEvidence` pair (themselves built by
  >    `service_compare_evidence` resolution) and returns a bare
  >    `AbiSnapshot` — the caller never sees the seeded `includes` or the
  >    folded `compile_context` `resolve_side_snapshot` computed internally.
  >    `_build_new_snapshot` returns exactly those two values
  >    (`effective_includes`/`effective_compile_context`) for its caller,
  >    `run_scan_core`, to forward on — but checked directly, `run_scan_core`
  >    only ever reads `eff_includes`/`eff_compile_context` inside its
  >    `if baseline is not None and scan_mode is not ScanMode.AUDIT:` block,
  >    feeding `_run_baseline_compare`'s side-aware reuse check
  >    (`scan_engine.py` ~1253–1329). A baseline-free scan never consumes
  >    either value — an earlier draft of this note claimed otherwise, which
  >    was wrong. So the return-shape gap is real, but it belongs entirely to
  >    the pair-aware baseline path this section's finding 3 already names,
  >    not to `resolve_side_snapshot`'s general contract — closing it does
  >    not by itself require widening what every caller (`compare`'s
  >    implicit-dump path included) gets back; a scan-specific extension
  >    point (or keeping `_build_new_snapshot`'s own resolution separate for
  >    this one reason) is the narrower, correctly-scoped fix.
  > 2. **`run_dump_request`'s return type does not need to change to move
  >    forward — add additive siblings instead of breaking the existing
  >    entry point, and keep the resolve-only and execute steps genuinely
  >    separate (Codex review, two rounds).** An earlier draft of this note
  >    framed "wrap the return in `DumpResult`" as requiring an explicit,
  >    coordinated breaking-API decision before any implementation. That
  >    framing itself was avoidable: `run_dump_request` is a documented,
  >    tested Tier-2 entry point today ([Python API guide](../../use/python-api.md),
  >    [Python API reference](../../reference/python-api-reference.md),
  >    `tests/test_typed_dump_request.py`,
  >    `tests/test_header_compile_context.py`,
  >    `tests/test_clang_header_backend_integration.py`) returning a bare
  >    `AbiSnapshot`, and nothing about giving `dump --dry-run` a real
  >    resolved object to render requires changing what that function
  >    returns. A first fix proposed one new sibling returning `DumpResult`
  >    (snapshot + storage result) and reused it for both the adapter and
  >    the dry-run render — but that conflates two things this section's own
  >    original design already keeps apart ("Click parsing → `DumpRequest` →
  >    `ResolvedDumpRequest` → dry-run-or-execution → `DumpResult`"): a
  >    `DumpResult` carrying a real storage result has, by construction,
  >    already executed, so it cannot also be what a read-only `--dry-run`
  >    renders without either violating the dry-run contract (never
  >    executes) or leaving the resolve-only path with no snapshot to
  >    report. The correct shape keeps that split: a resolve-only sibling
  >    (e.g. `resolve_dump_request`) returning `ResolvedDumpRequest` — only
  >    what resolution can determine without a snapshot (requested depth,
  >    effective *collect mode*, resolved header backend), no snapshot, no
  >    I/O beyond what resolution already does. **The landed field set is
  >    narrower than this sketch's own "resolved compile context, backend,
  >    the build-query decision" (Codex review, fresh evidence — this
  >    sketch predates the actual implementation and overclaimed its scope):
  >    `ResolvedDumpRequest` carries `header_backend`/`effective_header_
  >    backend` (a reporting-only projection; see its own comment for why
  >    it's never fed back into execution) but no resolved compile context
  >    and no build-query decision — `resolve_dump_request()` itself
  >    genuinely doesn't touch either (the L3→L2 fold and any build-query
  >    execution only happen later, inside `resolve_side_snapshot`, which
  >    only `execute_dump_request()` calls), so this omission is correctly
  >    scoped to this resolve-only step. **Not a CLI-presentation-layer
  >    claim (CodeRabbit review, fresh evidence — corrected alongside the
  >    identical overstatement in `DumpResult`'s own field list two
  >    sections above this one): both values are computed inside this same
  >    dump execution pipeline by the time `execute_dump_request()`
  >    finishes, just not surfaced as a field on either typed object yet.**
  >    — and
  >    a separate executor (e.g. `execute_dump_request`,
  >    taking a `ResolvedDumpRequest`) that produces `DumpResult` with the
  >    real snapshot and the *achieved* effective depth (a storage field is
  >    a separate, later addition — see the "storage result" correction
  >    above this bullet).
  >    **`effective depth` (as opposed to effective collect mode) belongs on
  >    `DumpResult`, not `ResolvedDumpRequest` (Codex review, fresh
  >    evidence)**: today's `fold_dump_provenance_into_dict` derives it from
  >    the completed snapshot via `_gated_source_label(snap.build_source,
  >    snap)` — there is no snapshot yet at resolve time, so a resolve-only
  >    object claiming to report it would have to guess, and a guess that
  >    disagrees with the real run defeats the entire point of rendering
  >    `--dry-run` from a real resolved object. `--dry-run` therefore reports
  >    requested depth (a known input) and collect mode (a resolvable
  >    decision), never a predicted achieved depth.
  >    `run_dump_request` stays as-is — either unchanged, or reimplemented as
  >    a thin adapter over the executor (`execute_dump_request(resolve_dump_
  >    request(request)).snapshot`), never over the resolve-only step alone
  >    — so no existing caller, doc page, or test needs to move. This two-
  >    function shape is the plan's required route now, not merely one
  >    option among several.
  >
  > Net: the three blockers this section already named are confirmed, not
  > merely asserted, and both are narrower than the first pass through this
  > note stated. (1) needs a small, scoped extension to the scan baseline
  > path specifically (not a `resolve_side_snapshot`-wide widening). (2)
  > needs two new, additive functions (a resolve-only step and a separate
  > executor), not a breaking change to `run_dump_request` — removing the
  > coordinated-break blocker
  > entirely for that half. Still not attempted here: each is a real,
  > separate, reviewable design (what the scan-specific extension point
  > looks like; the new function's exact signature and where the CLI's
  > `--dry-run` path starts calling it), and this file's own "known gaps
  > over risky reactive patches" convention says do that as its own
  > dedicated pass, not as a rushed follow-on to an already-large
  > investigation.
  >
  > **Slice landed (2026-08-19, this session): `DumpResult`/
  > `_resolve_side_snapshot_impl` split, and two concrete new blockers found
  > while attempting the next slice.** `service_input_resolution.py` gained
  > `SideResolution` (snapshot + the fold's own effective `includes`/
  > `CompileContext`, both previously discarded after use) and
  > `_resolve_side_snapshot_impl` (the real implementation;
  > `resolve_side_snapshot` is now a one-line wrapper, unchanged for every
  > existing caller). `service_dump_pipeline.DumpResult` surfaces the same
  > two fields, populated by `execute_dump_request`. Both additive, fully
  > tested, zero behavior change for existing callers — a genuinely safe
  > slice, per this note's own "Net" paragraph above.
  >
  > Attempting the *next* slice — routing `perform_elf_dump`'s primary parse
  > through this shared primitive instead of its own direct
  > `seed_includes_and_fold_compile_context()` + `dumper.dump()` call — found
  > this is **not** the small step it looked like: `perform_elf_dump` and
  > `service._dump_elf` (which `_resolve_side_snapshot_impl` reaches via
  > `service.resolve_input`) are two **independently evolved** ELF-resolution
  > pipelines, not one function called from two places. Concretely:
  > `perform_elf_dump` receives an *already-resolved* `debug_info_path`
  > (computed by its caller, `dump_cmd`, via `_resolve_debug_artifact`, from
  > `--debug-root`/`--debuginfod`), while `service._dump_elf` has no
  > `debug_info_path` parameter at all — it independently *re-derives* the
  > identical fact from raw `debug_roots`/`enable_debuginfod`/`debuginfod_url`
  > via its own call to `debug_resolver.resolve_debug_info`. Routing through
  > `resolve_input` would mean either dropping `perform_elf_dump`'s
  > already-resolved artifact on the floor, or reconciling two independently
  > written debug-artifact-resolution implementations to confirm they agree
  > — itself a real, separate investigation, not a follow-up edit.
  > `perform_elf_dump`'s `extra_hash_dirs`/`scope_header_dirs` are *not* an
  > equivalent problem (`_dump_elf` derives its own, deliberately-aligned
  > versions of both internally — confirmed by its own comments referencing
  > this exact alignment), so only the debug-artifact-resolution divergence
  > blocks this specific slice.
  >
  > A parallel check of the *other* half — `scan_engine._build_new_snapshot`,
  > which already calls `service.resolve_input` directly (not `dumper.dump()`,
  > so it doesn't share `perform_elf_dump`'s ELF-pipeline-divergence problem)
  > — found a second, independent blocker: `_build_new_snapshot` passes
  > `symbols_only`/`debug_presence_only` to `resolve_input` (needed for a
  > binary-depth/debug-presence-only scan), but `_resolve_side_snapshot_impl`
  > never threads either through (always `False`/`False`) — a real, if
  > narrower, additive gap to close first. Its `embed_build_source` call also
  > constructs `public_headers` differently from `embed_side_build_source`'s
  > (`_expand_public_headers` over the *combined* headers+dirs list, vs. the
  > shared wrapper's separate, unexpanded treatment of each) — a genuine
  > behavioral difference, not just a parameter-naming one, that needs
  > reconciling (which construction is actually correct for which caller)
  > before scan can safely route through the shared wrapper's embed step.
  >
  > **Neither of these two newly-found blockers was attempted this session**
  > — each needs its own focused investigation (confirm the debug-artifact
  > resolutions are equivalent before merging them; decide which
  > public-header construction is correct and unify), and rushing either
  > under continued session time pressure is exactly what this file's "known
  > gaps over risky reactive patches" convention exists to prevent, especially
  > given this exact code area's extensive prior history of exactly this
  > shape of subtle divergence (see `AGENTS.md`'s L3→L2-fold "Known gaps"
  > entry, 18+ numbered findings). **PR 3A's full convergence is therefore
  > still open after this session — PR 3C (PR F) remains blocked** on it, per
  > this section's own explicit ordering requirement. What this session adds
  > is a materially more precise map of exactly what blocks it, plus one more
  > safely-landed, real piece of the eventual solution.
  >
  > **Slice landed (2026-08-18): the `resolve_dump_request`/
  > `execute_dump_request` split from finding 2 above, coded.**
  > `service_dump_pipeline.py` now has `ResolvedDumpRequest`/`DumpResult`
  > and the two functions this note specified — `run_dump_request` is a
  > literal composition of both, unchanged in signature and return type.
  > This closes the "`run_dump_request`'s return type does not need to
  > change" question for real (not just as a design conclusion), but is
  > **not** the same as wiring `dump --dry-run` to `resolve_dump_request()`
  > — `render_dump_dry_run()` is still its own hand-written implementation.
  > That migration, blocker (1)'s scan-baseline narrowing, and blockers 2/3
  > (post-processing hooks, pair-aware scan decision) all remain open. See
  > the root `AGENTS.md`'s PR C entry for the verification detail.
  >
  > **Re-investigated (2026-08-19): the dry-run migration (blocker 1) is
  > confirmed larger than "wire the renderer to a new function call" —
  > `dump_cmd` has no `DumpRequest` to resolve in the first place.** Read
  > `cli.py`'s `dump_cmd` in full rather than assumed: it never constructs a
  > `DumpRequest` anywhere, on either the `--dry-run` or the real-run branch
  > (confirmed by grep — no `DumpRequest(` call site exists in `cli.py` or
  > `cli_dump_helpers.py`). Its actual resolution path is two CLI-only
  > helpers, `resolve_dump_collect_context`/`resolve_dump_compile_context`
  > (`cli_dump_helpers.py`), which compute `collect_mode`/`header_backend`/
  > `includes`/`gcc_option_tokens` directly from raw Click parameters —
  > entirely independent of `service_input_resolution`/
  > `service_dump_pipeline.resolve_dump_request`, and **not only for the
  > dry-run report**: those same two locals feed the real (non-dry-run)
  > `dump()` call a few hundred lines later in the same function. So
  > migrating `render_dump_dry_run` to build from a real
  > `ResolvedDumpRequest` is not an isolated renderer change — it requires
  > first constructing a `DumpRequest` from `dump_cmd`'s ~30 CLI parameters
  > (matching Click's parsing exactly, including the `_resolved_compile_
  > context`/`_resolved_collect_mode`/`_resolved_include_labels`/
  > `_resolved_lang_explicit` private hooks `compare`'s own `ctx.invoke`
  > already threads through this same command for its implicit-dump
  > operand), and doing so for **both** branches — dry-run and the real
  > run — so the two cannot silently diverge the moment one of them
  > migrates and the other doesn't. That is exactly the scope blocker (2)
  > already named ("`perform_elf_dump` runs three post-processing passes...
  > `run_dump_request` has no equivalent hook") from the opposite end: the
  > real run cannot move to `execute_dump_request()` without those hooks,
  > and the dry-run preview cannot honestly move to `resolve_dump_request()`
  > alone while the real run it is meant to preview keeps using a
  > completely different resolver — a preview built from one resolver
  > describing an execution built from another is worse than today's "two
  > independent implementations, kept in sync by hand," since it *looks*
  > authoritative without being connected to what actually runs. Not
  > attempted here, for the same reason blockers 2/3 were not: this is a
  > real, cross-cutting redesign of `dump_cmd`'s ~250-line resolution
  > section (already documented as needing careful, dedicated review —
  > `cli_dump_helpers.py` sits at its 2000-line AI-readiness hard cap, so
  > any new shared surface has to be added to a sibling module, not inline),
  > not a follow-up to the already-landed `resolve_dump_request`/
  > `execute_dump_request` split. That split remains real, additive
  > progress in its own right (§2 above) — it is the primitive a future
  > `dump_cmd` migration would build on, just not yet consumed by one.
  >
  > **Slice landed (2026-08-20): both of the "two newly-found blockers"
  > above closed, plus the debug-artifact-resolution question confirmed.**
  > (1) The debug-artifact divergence: read both resolutions in full rather
  > than assumed equivalent. `perform_elf_dump`'s only caller is `dump_cmd`
  > (confirmed by grep — no second call site exists), and `dump_cmd` never
  > sets `symbols_only`/`debug_presence_only` at all (`dump` has no such
  > flags — only `scan`/`compare` do), so `_dump_elf`'s extra `not
  > symbols_only and not debug_presence_only` gate is vacuously true for
  > every input `perform_elf_dump` can actually receive. The remaining
  > differences (`debug_roots=debug_roots` vs. `list(debug_roots) or
  > None`; `click.echo` vs. `notify`/`_logger`; `if artifact:` vs. `if
  > artifact is not None`) are all behaviorally inert — `BuildIdTreeResolver`/
  > `PathMirrorResolver` etc. already do `list(debug_roots or [])`
  > internally, a `DebugArtifact` dataclass instance is always truthy, and
  > the messaging difference is cosmetic. **Confirmed equivalent for the
  > real call shape — no code change needed here**, closing this question
  > without the "reconciling two independently written implementations"
  > investigation this note flagged as still open.
  >
  > (2) `symbols_only`/`debug_presence_only` now thread through
  > `resolve_side_snapshot`/`_resolve_side_snapshot_impl` into
  > `service.resolve_input`, both defaulting `False` (matching
  > `resolve_input`'s own defaults) so every pre-existing caller is
  > unaffected — a strict superset of the prior behavior, the same shape
  > as this primitive's existing `changed_paths`/`allow_build_query`
  > pass-throughs. `scan_engine._build_new_snapshot` does not consume this
  > yet (it still calls `service.resolve_input` directly, not through this
  > shared primitive — see below), so this closes the *primitive's*
  > capability gap, not yet the caller-side migration. Regression test:
  > `tests/test_header_compile_context.py::
  > test_resolve_side_snapshot_forwards_symbols_only_and_debug_presence_only`
  > (confirmed to fail against the pre-fix code with `TypeError: unexpected
  > keyword argument 'symbols_only'`).
  >
  > (3) The `public_headers`/`public_header_dirs` construction divergence:
  > **investigated, a fix attempted and merged, then reverted the same day
  > after a real regression was caught by review — recorded in full since
  > the reasoning that looked right the first time was genuinely
  > incomplete, not merely under-tested.** The first pass read one consumer
  > of `embed_build_source`'s `public_header_roots`
  > (`source_extractors._argv.split_public_roots`/`_ClassifyContext.
  > classify()`, `source_extractors/clang.py`) and confirmed a directory
  > root already classifies every file under it via segment/prefix
  > matching — so `_build_new_snapshot`'s `_expand_public_headers`-based
  > expansion looked purely redundant against *that* consumer, and the fix
  > switched the call to the simpler, unexpanded raw pass-through
  > `embed_side_build_source` already uses. That reasoning missed a
  > **second, differently-shaped consumer of the identical
  > `public_header_roots` list**: `clang_public_roots.
  > _equivalent_public_roots_for_unit`, the install-tree-vs-build-tree
  > "mirror detection" heuristic L4 replay uses when a public root names a
  > physically different tree from the build's own include dir (a common
  > shape for release/package validation, per that module's own
  > docstring). Its promotion rule is asymmetric by root shape: a *file*
  > root promotes an equivalent build-tree header on a single sampled
  > match; a *directory* root needs `>= _PUBLIC_ROOT_WHOLE_DIR_MIN_MATCHES`
  > (2) sampled matches before promoting the whole directory. So a build
  > include directory that happens to mirror only ONE header out of a
  > larger public root (a small per-module local include directory against
  > a larger installed SDK tree) loses that mirror-promotion entirely once
  > the directory stops being pre-expanded into individual files — proven
  > by direct reproduction against `_equivalent_public_roots_for_unit`
  > itself (three installed headers, one mirrored in the build tree: the
  > expanded-file-roots call promotes the mirrored header; the single
  > directory-root call promotes nothing). `embed_side_build_source`'s own
  > raw pass-through (already shipped, used by `compare`/`dump`) carries
  > the identical weakness — not fixed here, since unifying either
  > direction changes real classification behavior for a real consumer,
  > and deciding which needs its own scoped design (harden
  > `_equivalent_public_roots_for_unit`'s directory threshold, or thread
  > real expansion into the shared primitive instead), not a same-PR
  > revert-and-redo. **Reverted `_build_new_snapshot`'s call back to the
  > expanded shape** — the one proven not to regress this heuristic — and
  > pinned two regression tests: the reverted call's own shape
  > (`tests/test_scan_l2_cleanup_ordering.py::
  > test_scan_candidate_expands_public_header_dirs_before_embed`) and, more
  > importantly, the underlying asymmetry itself, directly against
  > `_equivalent_public_roots_for_unit`
  > (`tests/test_clang_public_roots_coverage.py::
  > test_equivalent_public_roots_promotes_on_single_match_only_for_file_roots`)
  > so a future "simplify this like the other primitive" pass doesn't
  > silently reintroduce the same regression by generalizing from only the
  > first consumer again.
  >
  > **What this slice does not close**: `_build_new_snapshot` still calls
  > `service.resolve_input`/`embed_build_source` directly rather than
  > routing through `_resolve_side_snapshot_impl`/`SideResolution` — the
  > two additive fixes above make that future migration *safe*, they don't
  > perform it. Investigating the migration itself surfaced one more
  > wrinkle beyond what this section had already named: `_build_new_
  > snapshot`'s own `allow_build_query` parameter gates only its
  > `embed_build_source` call (whether the *inferred/auto-discovered* build
  > query may run) and is never threaded into its `seed_includes_and_
  > fold_compile_context` call at all (that call always passes `build_
  > query=None, build_compile_db=None` — `scan` has no `--build-query`/
  > `--build-compile-db` CLI flags to begin with, unlike `dump`) — whereas
  > `_resolve_side_snapshot_impl`'s `_gated_build_query_inputs` gates both
  > the seed and the embed step from one shared decision. Reconciling this
  > correctly needs confirming what `_build_new_snapshot`'s `allow_build_
  > query` is actually meant to authorize today (the auto-discovery-vs-
  > seed distinction, not just the embed step) before the two functions'
  > gating can be safely unified — a real, separate investigation, not a
  > follow-up to this slice's two closed items. Blockers 4 (post-processing
  > hooks), 5 (`dump_cmd` building a real `DumpRequest`), and 6 (a
  > pair-aware scan-baseline primitive) remain fully open, unchanged from
  > the notes above.
  >
  > **Blocker 4 narrowed by re-reading `service.py`'s own ELF `run_dump`
  > tail against `perform_elf_dump`'s three named post-processing passes,
  > one at a time, rather than assuming all three are equally missing
  > (2026-08-20, no code change).** Two of the three already run on the
  > shared `resolve_side_snapshot`/`resolve_input` path — `service.
  > resolve_input`'s native-binary dispatch calls `run_dump`, and
  > `run_dump`'s ELF branch already calls `_attach_header_graph` (the G31
  > header-graph second pass) and `attach_clang_layout` (the G28 clang-
  > layout-tool attach) unconditionally, the identical two functions
  > `perform_elf_dump` calls by name. So `_resolve_side_snapshot_impl`
  > (and therefore `compare`'s implicit-dump path and `dump`'s typed
  > `run_dump_request`) already gets both — blocker 4's "no equivalent
  > hook" framing was accurate for the ADR-039 pass alone, not for all
  > three. **The ADR-039 build-context collector (`_attach_build_context`,
  > `cli_dump_helpers.py`) is confirmed, by grep, to have exactly one call
  > site in the whole codebase — `perform_elf_dump` itself.** Not `handle_
  > non_elf_dump` (PE/Mach-O `dump`), not `service.py`'s `run_dump`/`_dump_
  > elf`/`_dump_pe`/`_dump_macho`, not `scan_engine._build_new_snapshot`.
  > It is ELF-`dump`-CLI-only today, and this is a materially different
  > kind of gap than "the shared primitive is missing a hook to call an
  > existing, portable step": `_attach_build_context` is driven entirely by
  > CLI-only inputs (`-p`/`--compile-db`, `--compile-db-filter`,
  > `effective_compile_db` — a raw, unfiltered compile-database path plus a
  > source-glob filter, resolved by `cli.py`'s own flag parsing) that have
  > no representation anywhere in `CompileContext`, `InputSpec`, or
  > `DumpRequest` — there is no typed-API field to route through a hook
  > even once one exists. Closing this needs *new* typed-API surface (an
  > `InputSpec`/`DumpRequest` field carrying a compile-DB path + filter,
  > threaded through `_resolve_side_snapshot_impl` to a new call to
  > `_attach_build_context` or an equivalent), not merely wiring an
  > existing portable step into an existing hook — a real, separate,
  > additive feature addition on its own, out of proportion to a
  > same-session follow-up given `_attach_build_context`'s own documented
  > "must not be unioned snapshot-wide" invariant (AGENTS.md's L3→L2-fold
  > entry, ninth finding) that any new call site would have to re-verify
  > against. Not attempted here. Blockers 5 and 6 are unchanged.
  >
  > **Blocker 4 closed for the shared pipeline (2026-08-20).** The "new
  > typed-API surface" this note called for is now real: a call to the
  > ADR-039 collector inside `_resolve_side_snapshot_impl`, gated exactly the
  > way `perform_elf_dump` gates its own call (a compile-DB path resolvable
  > from `side.build_info`, real headers, `snap.from_headers`).
  > `InputSpec` deliberately does **not** gain a `compile_db_filter` field
  > mirroring `--compile-db-filter` — an earlier revision of this change
  > added one, and Codex review found it had no successful execution path
  > where it narrowed anything (this shared pipeline's own L2 header-AST
  > context always resolves from the whole, unfiltered compile database
  > regardless of collect mode, so the field could only ever be combined
  > with a resolvable database by raising, never by actually narrowing the
  > collector's scan); removed rather than half-implemented — see the field's
  > replacement comment in `api_types.py` for why threading the filter
  > through is a separate, larger feature.
  > `attach_build_context`/`user_define_flags`/`compile_db_from_build_info`
  > moved from `cli_dump_helpers.py` (CLI-presentation layer, not importable
  > from a service module) to `header_conditionals.py` — already a
  > dependency-free leaf module and the collector's own home — with
  > `cli_dump_helpers.py` keeping the original private names as thin
  > re-exports, so `perform_elf_dump` and every existing test are unchanged.
  > The "must not be unioned snapshot-wide" invariant is preserved the same
  > way `perform_elf_dump` preserves it: only `side.compile` (this side's
  > pre-fold, caller-supplied `CompileContext`) feeds the collector's
  > `extra_flags`, never `compile_ctx` (the P0.3-folded result also computed
  > in this function). `perform_elf_dump` itself is **not** migrated to call
  > through this shared path — it keeps its own direct call — so this closes
  > the *capability* gap (compare's implicit-dump operand and `dump`'s typed
  > `run_dump_request` API can now reach the collector too), not blocker 5
  > (making `dump_cmd` build a real `DumpRequest` in the first place, which
  > is what would let the real ELF `dump` CLI path route through here).
  > Verified with direct tests on `_resolve_side_snapshot_impl`
  > (`tests/test_typed_dump_request.py::
  > TestSharedPipelineReachesADR039BuildContextCollector`) — collector fires
  > and populates `build_context_defines`/`conditional_fields`; folded/
  > derived tokens never reach `extra_flags`; the collector is skipped (not
  > raised) with no `build_info` at all — each confirmed to fail against the
  > pre-fix code (no `attach_build_context` attribute on the module). Full
  > fast unit suite green, `mypy abicheck/` clean, `ruff check` clean.
  >
  > **Three more Codex-review rounds on this same change (2026-08-20),
  > each confirmed and fixed, none requiring a design change.** (1) A PE/
  > Mach-O typed dump/compare with headers + a compile-database `build_info`
  > silently attached ELF-only ADR-039 evidence — gated the whole block on
  > `fmt == "elf"` too. (2) A followed GNU ld linker script (`fmt` reads
  > `None` pre-resolution, `resolve_input`'s own `follow_linker_scripts`
  > default follows it to a real ELF target) silently skipped the collector
  > — regated on `snap.elf is not None`, the model's own post-resolution
  > signal, instead of the stale pre-resolution `fmt`. (3) `snap.elf is not
  > None` alone proved insufficient on its own: a *loaded* JSON snapshot
  > (`resolve_input`'s `fmt == "json"` branch, `load_snapshot(path)`
  > verbatim, no live parse) round-trips its original `elf`/`from_headers`
  > fields exactly as saved, so the identical signal fires for a side that
  > never touched the header parser at all — running the collector there
  > would scan the *current* filesystem's headers/compile-db and overwrite
  > the loaded snapshot's own recorded build-context evidence with
  > unrelated data. Fixed by also requiring
  > `service.sniff_text_format(side.path) != "json"` (the exact predicate
  > `resolve_input` itself uses to take that branch, so the two can't
  > disagree). Each of the three confirmed to fail against the pre-fix code
  > via a dedicated regression test in the same test class.
  >
  > **A separate, unrelated Codex finding on `cli_dump_helpers.py`'s own
  > re-export of the moved helpers, also fixed the same round.**
  > `compile_db_from_build_info`/`compile_db_filter_scope_error` (re-exported
  > only for `cli.py`/test back-compat, never called internally by this
  > module) now go through the lazy module-level `__getattr__` shim this
  > repo's own moved-helper convention requires, mirroring
  > `cli_buildsource.py`'s identical shim.  `_attach_build_context`/
  > `_user_define_flags` stay a normal static import, since `perform_elf_dump`
  > calls them directly — that edge to `header_conditionals.py` (a verified
  > true leaf module) is structurally required either way, so it isn't the
  > pattern the convention targets.
  >
  > **The `scan_engine._build_new_snapshot` `allow_build_query` gating
  > "unreconciled" framing (this section's own opening paragraph, and PR
  > 3A's summary in "Ordering") is investigated and found to be a
  > non-issue, not left open.** Tracing `build_config`'s origin
  > (`run_scan_core` → `cli_scan_helpers.resolve_effective_allow_query`)
  > shows its docstring already states the trust rule: *"Trusted = an
  > explicit --config path (build_config is not None here; an
  > auto-discovered source-tree config is resolved later in
  > embed_build_source and never reaches this gate)"* — by the time
  > `build_config` reaches `_build_new_snapshot`, its mere presence already
  > encodes the trust decision, matching `_resolve_l2_seed_pack_args`'s own
  > internal rule (`build_config_trusted_for_query=(build_config is not
  > None or build_query is not None)`). Passing it ungated to the seed call
  > is therefore correct, not drift; applying `_gated_build_query_inputs`-
  > style re-gating on top would be a **regression** — `effective_allow_
  > query` can be `False` for reasons unrelated to trust (e.g.
  > `collect_mode == "off"`), which would wrongly suppress an
  > already-vetted `--config`'s `build.query` from reaching the seed.
  > `build_query`/`build_compile_db` being hardcoded `None`/`None` in the
  > same call is confirmed fully dead code today (`scan` has no
  > `--build-query`/`--build-compile-db` CLI flags, `run_scan_core` has no
  > such parameters, and `_build_new_snapshot`'s one call site never passes
  > non-default values) — adding live parameters/gating for a capability
  > nothing can reach yet would be speculative, untested plumbing, not a
  > fix for an observed defect. Not implemented; recorded as investigated
  > and closed rather than left as an open reconciliation item. A future
  > `scan --build-query` flag, if ever added, needs its own trust-gate
  > design at that point (mirroring `dump`'s), not a preemptive parameter
  > added now.
  >
  > **Slice landed (2026-08-21): `scan`'s candidate resolver now reaches the
  > ADR-039 build-context collector, through the one shared gate all three
  > resolvers use — plus three newly-verified blockers on `dump_cmd`'s own
  > `DumpRequest` migration (blocker 5) that none of the notes above had
  > named.**
  >
  > *What landed.* PR #809 gave the typed pipeline the ADR-039 collector and
  > noted it did not migrate `perform_elf_dump`. Re-reading the three
  > resolvers side by side to plan that migration surfaced something the
  > note above had not: `scan_engine._build_new_snapshot` never ran the
  > collector **at all**. So `scan --against` a `dump`-produced baseline
  > compared a candidate carrying no `build_context_defines`/
  > `conditional_fields` against a baseline carrying both — and the ADR-039
  > reconciler, whose whole job is clearing a context-free header-parse
  > false positive (a `#ifdef`-guarded record field the context-free parse
  > pruned), could do that on the baseline side and not on the candidate's.
  > A real dump-vs-scan asymmetry, produced by exactly the mechanism this
  > section exists to close: three resolvers each hand-writing the same
  > gate.
  >
  > Fixed at the gate rather than by writing a fourth copy of it.
  > `header_conditionals.attach_build_context_for_parsed_headers` now owns
  > the whole decision — compile-DB resolution (from an already-resolved
  > path *or* from `build_info`), the best-effort header expansion a
  > directory `-H` entry needs, the `snap.from_headers` check, and the
  > caller-supplied `live_elf_parse` answer — and `perform_elf_dump`, the
  > typed `_resolve_side_snapshot_impl`, and `_build_new_snapshot` all call
  > it. `perform_elf_dump` gains one gate in the process (`from_headers`),
  > which is the same one its own sibling `parsed_with_build_context` stamp
  > ten lines above already applies, with the same recorded reason: a
  > `--dwarf-only` run explicitly ignores `-H`, so a database matching the
  > *requested* headers is not evidence about a snapshot that never parsed
  > them. Tests: `tests/test_scan_adr039_build_context.py` (7 cases —
  > collection, directory expansion, the "pre-fold flags only" invariant,
  > and the four skip gates; the three positive ones confirmed to fail
  > against the pre-fix `scan_engine.py`).
  >
  > A latent ordering bug in `_resolve_side_snapshot_impl` was fixed
  > alongside it: it drained the L2 seed's cleanups only *after*
  > `embed_side_build_source`, so an inferred build query's exclusive lock
  > was still held when the embed step ran its own inferred query — the
  > in-process self-contention (up to a 600s timeout) that
  > `_build_new_snapshot` already avoids, and that the fifth finding on the
  > root `AGENTS.md`'s L3→L2-fold entry records for the CLI resolvers.
  > Unreachable today (`_seeded_includes_and_compile_context` pins the
  > seed's own `collect_mode="off"`, so no current caller can run an
  > inferred query there and the cleanup list is always empty), which is
  > precisely why it needed fixing now rather than a bug report later: it
  > springs on whichever PR relaxes that pin, which is what migrating the
  > CLI resolvers means. Pinned by
  > `tests/test_typed_dump_request.py::TestSeedCleanupsDrainBeforeTheEmbedStep`
  > (confirmed to fail pre-fix).
  >
  > *Blocker 5 (`dump_cmd` builds a real `DumpRequest`) — three concrete
  > obstacles, each verified against the code or empirically, none of them
  > named in the notes above.* The re-investigation note from 2026-08-19
  > correctly established that `dump_cmd` has no `DumpRequest` to resolve
  > and that both branches must migrate together. What it did not establish
  > is what specifically breaks when you try:
  >
  > 1. **`InputSpec.path` is a required field, and `dump`'s source-only
  >    branch has no path.** `dump --sources ./tree -o out.json` (SO_PATH
  >    omitted) is a supported, tested invocation, and `dump_cmd` dispatches
  >    it to `cli_buildsource.dump_source_only` — but the `--dry-run` branch
  >    runs *before* that dispatch, so "both branches build from the same
  >    `DumpRequest`" cannot hold for this shape at all until `InputSpec`
  >    can express "no binary". Making `path` optional is a public typed-API
  >    model change reaching every `InputSpec` consumer
  >    (`resolve_input`/`compare`/`scan`/the MCP-era callers), not a step
  >    inside `dump_cmd`.
  > 2. **The CLI and typed collect-mode resolvers genuinely disagree, and
  >    not only in a vacuous case.** `cli_dump_helpers.resolve_dump_collect_
  >    context` resolves an omitted `--depth` to `resolve_dump_depth(None,
  >    "source-target")`; `service_compare_evidence.collect_mode_for`
  >    resolves the same omission by *inferring from the inputs*. Measured
  >    directly rather than reasoned about: for every explicit `--depth` the
  >    two agree exactly (`binary`/`headers` → `off`, `build` → `build`,
  >    `source` → `source-target`), and with nothing supplied the difference
  >    (`source-target` vs `off`) is unobservable, since neither the embed
  >    step nor the L2 seed does anything without `sources`/`build_info`.
  >    But **`--build-info` with no `--depth` resolves to `source-target`
  >    on the CLI and `build` through the typed path** — so a `dump
  >    --build-info <pack> -H api.h` that attempts L4 source replay today
  >    would silently stop at L3 the moment `dump_cmd` starts taking its
  >    collect mode from `resolve_dump_request`. That is a real regression
  >    for a real shape, and choosing between the two defaults is a product
  >    decision (is `--build-info` alone a request for source evidence?),
  >    not a mechanical reconciliation — it has to be made, and shipped,
  >    before the migration can use the typed resolver's answer.
  > 3. **The ELF `dump` CLI embeds L3–L5 at *write* time; the typed executor
  >    embeds at *resolve* time.** `perform_elf_dump` finishes by calling
  >    `write_snapshot_output` → `cli_buildsource._write_snapshot_output`,
  >    and it is *there* that `embed_build_source` runs — together with the
  >    G21.7 missing-evidence-layer warning, the Flow-2 `--inputs` pack
  >    fold, the depth gate and the provenance fold.
  >    `execute_dump_request` embeds much earlier, inside
  >    `_resolve_side_snapshot_impl`, before the dependency walk and the
  >    depth floor. Routing `dump_cmd`'s real run through
  >    `execute_dump_request` therefore does not merely reorder the embed —
  >    it **embeds twice**, re-running L4 source replay, unless
  >    `_write_snapshot_output` is simultaneously taught to skip an embed
  >    that already happened. That is a restructuring of the write path, a
  >    fourth pipeline this section had not counted.
  >
  > Together these say something sharper than "blocker 5 is open": a
  > migration that moved only the `--dry-run` branch would be *actively
  > misleading* for the `--build-info` shape in (2), reporting a collect
  > mode the real run does not use — the exact "a preview built from one
  > resolver describing an execution built from another" failure the
  > 2026-08-19 note warns against, now with a concrete instance rather than
  > a general worry.
  >
  > *One more verified divergence, adjacent to this section's goal and
  > recorded so the next pass does not have to rediscover it.* `scan`'s
  > `embed_build_source` call passes no `extractor` at all, taking that
  > function's `"auto"` default — and `buildsource.inline._make_source_
  > extractor` treats anything that is not literally `"castxml"` as clang.
  > Every other resolver passes `service_compare_evidence.effective_
  > frontend(...)`, which resolves `"auto"` through `dumper._resolve_header_
  > backend` to **castxml**. So `dump --depth source` and `scan --depth
  > source` over the same project, both at their defaults, replay L4 through
  > *different extractors*, and a `scan --against` a `dump` baseline
  > compares source-ABI facts produced by two different tools —
  > `effective_frontend`'s own docstring says it exists to stop exactly this.
  > **Not fixed here**: making `scan` match would newly require castxml for
  > a `scan --depth source` that works with clang today (`_resolve_header_
  > backend` resolves `"auto"` to castxml unconditionally — there is no
  > availability fallback), so it is a real behavior change for real users,
  > and it cannot be verified in an environment without castxml. It needs
  > its own slice with a castxml-capable lane, not a same-pass patch.

  > **Slice landed (2026-08-21, later the same day): blocker 5's three
  > sub-issues closed, `dump_cmd` now builds one real `DumpRequest`, and
  > blocker 6 has its narrow extension point. The real ELF/PE/Mach-O run is
  > deliberately NOT migrated — see "what this does not close" below.**
  >
  > *Blocker 5, sub-issue (a) — `InputSpec.path`.* Widened to `Path | None`,
  > a pure widening (it accepts everything `Path` did), so `dump`'s
  > source-only branch is expressible. Which requests may leave it `None` is
  > enforced per request type in `validation_errors()` — never for
  > `CompareRequest`, and for `DumpRequest` only alongside real
  > `sources`/`build_info`/`dump_manifest` — rather than defended at each of
  > the seven call sites that dereference it; `api_types.required_path` is
  > the single place the narrowing is spelled. `resolve_dump_request()`
  > resolves the pathless shape (which is what `--dry-run` needs);
  > `execute_dump_request()` raises a specific `ValidationError` for it,
  > since producing a binary-less snapshot is still
  > `cli_buildsource.dump_source_only`'s own pipeline. **The
  > `dump_manifest` half was found by the existing tests, not by design**:
  > a first revision named only `sources`/`build_info` and broke
  > `dump --dump-manifest m.yaml --dry-run` (no SO_PATH), which
  > `tests/test_cli_dump_manifest.py` already covered — precisely the "the
  > model cannot say what the CLI accepts" gap this widening exists to
  > close, caught in the right direction.
  >
  > *Sub-issue (b) — the collect-mode disagreement.* Resolved in favour of
  > the CLI's `source-target` default, as this section's own measurement
  > called for. The typed `dump` path now goes through a new
  > `service_compare_evidence.dump_collect_mode_for`, a mirror of
  > `cli_dump_helpers.resolve_dump_depth`'s rule; `collect_mode_for` is
  > **unchanged** and still serves `compare`, whose own front end genuinely
  > infers omitted depth from its inputs. Pinned by
  > `tests/test_dump_collect_mode_parity.py`, which enumerates the whole
  > `(depth, sources-given, build_info-given)` grid against the *real* CLI
  > resolver rather than a restatement of its rule. Deliberately a separate
  > module from `tests/test_dump_cli_typed_api_parity.py` despite asking the
  > same class of question: every test there is `integration`-marked and
  > skips without castxml, so the pin would not have gated the lane it
  > matters in.
  >
  > *Sub-issue (c) — the double embed.* `cli_buildsource.
  > build_source_already_satisfies` is the check-before-embed guard, stated
  > through the same `_missing_requested_evidence_layers` the neighbouring
  > G21.7 fail-loud warning already trusts, so the guard and the warning
  > cannot disagree about what "satisfied" means. Its `pack is None -> []`
  > case is explicitly *not* satisfaction, which is what keeps it a no-op
  > for today's CLI. Tests: `tests/test_dump_embed_idempotence.py` —
  > the predicate's answers, the guard's skip/no-skip/still-embed
  > directions, and an `integration`-marked end-to-end count proving one
  > real `dump --depth source` performs exactly one embed.
  >
  > *The `DumpRequest` itself.* `abicheck/cli_dump_request.py` (its own
  > sibling module: `cli.py` is 1800+ lines and `cli_dump_helpers.py` is at
  > the 2000-line hard cap) builds one request from `dump_cmd`'s parameters,
  > and the `--dry-run` branch now renders from a real `ResolvedDumpRequest`
  > (`resolved.headers`/`collect_mode`/`header_backend`) instead of
  > `dump_cmd`'s own locals. **The half-migration hazard this section names
  > — "a preview built from one resolver describing an execution built from
  > another is worse than two hand-synced implementations" — is answered in
  > two ways, not waved at.** First, the request is fed the CLI's
  > *already-resolved* values (the resolved `CompileContext`, the resolved
  > frontend, the resolved explicit-language decision) rather than
  > re-deriving them, so it records the run instead of forming a parallel
  > opinion about it. Second, the fields `resolve_dump_request` *does*
  > derive independently are pinned equal to the CLI's own by
  > `tests/test_dump_request_from_cli.py::
  > TestResolvedRequestAgreesWithTheCliLocals`, across every `--depth` and
  > both frontends — so a divergence fails a test rather than surfacing as a
  > dry-run report describing a run that never happened. Sub-issue (b) was a
  > prerequisite for exactly this: without it the preview would have
  > reported a collect mode the real run does not use.
  >
  > `resolve_dump_request_for_cli` translates the Tier-2 `ValidationError`
  > into a Click `UsageError` at the boundary. One user-visible consequence,
  > stated rather than discovered later: `DumpRequest.validate()`
  > front-runs a check `dumper.dump()` already performs at *runtime* — a
  > `--dump-manifest` combined with `-I`/`--include`, two conflicting
  > declared surfaces — so that combination now reports a usage error in the
  > dry run too instead of dry-running clean and failing during extraction.
  > That stays inside the dry-run contract ("never raises on anything but a
  > usage error") because it is precisely a usage error.
  >
  > *Blocker 6.* `service_input_resolution.BaselineReuseContext` /
  > `resolve_baseline_compile_context` is the pair-shaped rule, extracted
  > from the four-clause boolean that was inline in
  > `scan_engine.run_scan_core` and that the root `AGENTS.md`'s
  > L3->L2-fold entry records three review rounds correcting (findings 12,
  > 13, 15). `run_scan_core` calls it today;
  > `_resolve_side_snapshot_impl` accepts the same object as an optional
  > `baseline_reuse_hint` and reports the identical answer on
  > `SideResolution.baseline_compile_context` — so the migration that
  > finally routes `_build_new_snapshot` through the shared resolver
  > inherits the rule rather than reimplementing it. Deliberately an
  > **opt-in hint**, per this section's own decision not to widen
  > `resolve_side_snapshot`'s general single-input contract: a caller that
  > passes none is bit-for-bit unaffected. Tested as a primitive
  > (`tests/test_baseline_reuse_context.py`), per AGENTS.md's
  > "Primitive-level property tests" guidance — which is exactly what a rule
  > with that correction history calls for — including the invariant that
  > the resolver never disagrees with its own predicate, and a pin that
  > include *order* matters (search order is first-match-wins, so a
  > "compare as sets" simplification has to argue with a test).
  >
  > **What this does not close, stated plainly.** The real ELF/PE/Mach-O
  > `dump` run still executes through `perform_elf_dump`/
  > `handle_non_elf_dump`, not through `execute_dump_request`. Sub-issue
  > (c) removes the double-embed hazard from that migration's path, and the
  > `DumpRequest` is the object it would build from, but the migration
  > itself needs the ADR-039 collector's CLI-only inputs
  > (`--compile-db-filter`, the raw `effective_compile_db`) to have typed-API
  > representation, and the write-time provenance/`--inputs`/depth-gate
  > sequence in `cli_buildsource._write_snapshot_output` to be reordered
  > around a resolve-time embed. Likewise `scan_engine._build_new_snapshot`
  > still calls `service.resolve_input`/`embed_build_source` directly; the
  > hint parameter makes that migration safe, it does not perform it. Two
  > divergences remain open and unchanged: the `public_headers` expansion
  > shape (deliberately kept expanded — see the 2026-08-20 note above for
  > the regression a "simplification" caused) and the L4 extractor default
  > (`scan` takes `embed_build_source`'s `"auto"` -> clang while every other
  > resolver passes `effective_frontend`, which resolves `"auto"` to
  > castxml). **castxml was re-checked for in this session's environment and
  > is still absent**, so the extractor divergence stays a documented gap
  > rather than a guessed fix, exactly as the 2026-08-21 note above
  > requires.

  > **Investigated (2026-08-21, later session): both real-run migrations
  > attempted and stopped, with the reason measured rather than reasoned
  > about for the first time. One guard landed; no production code changed.**
  >
  > *The `dump` real-run migration.* The note above lists two prerequisites
  > (typed representation for the ADR-039 collector's CLI-only inputs; the
  > write-time embed reordered). Both are real. What the note did not carry
  > is what the migration would actually *change about the snapshot*, which
  > is the bar it has to clear. Measured field-by-field, against a real
  > `g++` build and a real clang L2 parse, comparing the written `dump` CLI
  > snapshot with `execute_dump_request`'s over the same three build shapes
  > this section's own parity module already uses:
  >
  > * Everything outside the extraction contract agrees, apart from the
  >   CLI's own presentation/provenance layer (`created_at`,
  >   `dump_provenance`, `version`) and the build-source pack's timestamped
  >   content hash — all of which stay in the CLI by design.
  > * `contract.profile_fields` agrees exactly for a plain build. For a
  >   build with a `-D`, the CLI records `macro_ops` as
  >   `[["D","FOO=1"],["D","FOO=1"]]` where every other path records one
  >   entry. For a build with an extra `-I<dep>`, the CLI records
  >   `include_sequence` as `[]` where every other path records one slot.
  > * `scope_fingerprint` agrees in every case; `profile_fingerprint`
  >   therefore differs in exactly the two cases above.
  >
  > Both trace to one mechanism, and it is the one this plan's own
  > `AGENTS.md` counterpart already names as an open design decision: the
  > `dump` CLI runs the legacy `-p`/`--compile-db` auto-match
  > (`cli_helpers_compare._resolve_build_context_flags`, merged into
  > `effective_gcc_options`) *in addition to* the P0.3 L3→L2 fold whenever
  > both are fed by the same `--build-info` compile database. The duplicate
  > `-D` is that overlap recorded twice; the empty `include_sequence` is the
  > legacy match putting `-I<dep>` into explicit context *before* the L2
  > seed runs, so `seed_l2_includes` correctly declines to seed a directory
  > explicit context already supplies and the dir reaches the parse through
  > `gcc_option_tokens` — which contributes no `declared_includes` slot,
  > the sole source `include_sequence` tokenizes.
  >
  > So the migration does not merely need the two prerequisites above; it
  > *forces* that design decision, because routing the real run through
  > `execute_dump_request` drops the legacy match. Dropping it is arguably
  > right (the fold is strictly richer: per-header matching, ambiguity
  > checking, include paths, forced includes) — but it makes
  > `dump --compile-db-filter` inert, since the shared fold has no filter
  > concept, and `InputSpec` deliberately carries no `compile_db_filter`
  > field: one was added in PR #809 and removed the same review round for
  > having no successful execution path. Making a documented flag silently
  > inert is a worse outcome than the gap. **The correct ordering is
  > therefore: thread `--compile-db-filter` into the shared fold
  > (`buildsource/l2_seed.py` / `header_compile_context.py`, exactly what
  > `InputSpec`'s own comment already specifies), decide and ship the
  > legacy-match removal, and only then migrate the real run.** Three
  > slices, not one.
  >
  > **The first of the three landed in this session, and it is a user-facing
  > bug fix on its own terms, not only migration plumbing.**
  > `resolve_header_compile_context` fails closed when one public header is
  > compiled under two ABI-relevant contexts, and its message names
  > `--compile-db-filter` as a way to narrow the input — but the filter
  > reached only the legacy match, so the fold still saw every unit and a
  > user who followed that advice got the identical error back, with no
  > remedy short of hand-writing a pre-filtered `compile_commands.json`.
  > Reproduced end to end (`dump --depth headers -H api.h --build-info
  > db.json --compile-db-filter a.cpp`, two TUs disagreeing on an
  > ABI-relevant `-D`) and fixed: `resolve_header_compile_context` takes a
  > `source_filter`, `l2_seed` forwards it, and `perform_elf_dump` threads
  > the CLI's own flag through. The matching rules are consolidated into one
  > `build_context.source_matches_filter`, so the fold, the legacy match and
  > the ADR-039 collector cannot select different translation units for one
  > filter; a filter matching nothing keeps every unit, the conservative
  > fallback the other two layers already applied. Tests:
  > `tests/test_compile_db_filter_scope.py` — the primitive's contract as
  > invariants, the three layers agreeing, the resolver, and a real
  > `g++`+clang `dump` asserting the guarded field is parsed in or out
  > according to *which* TU the filter names (the end-to-end cases confirmed
  > to fail pre-fix). Still open in this slice (at the time it landed): the
  > typed half — `InputSpec.compile_db_filter` plus the CLI's own
  > L2-filtered/L3-unfiltered refusal
  > (`header_conditionals.compile_db_filter_scope_error`) mirrored into
  > `resolve_dump_request`, the only place that knows the resolved collect
  > mode. That is one clearly-specified step; see the field's replacement
  > comment in `api_types.py`.
  >
  > **That typed half has since landed (2026-08-21, later session).**
  > `InputSpec.compile_db_filter` exists; `_seeded_includes_and_compile_
  > context` and `attach_build_context_for_parsed_headers` both forward it as
  > `source_filter`; `resolve_dump_request` mirrors the CLI's scope-error
  > refusal from `evidence.collect_mode`/`evidence.headers` and raises
  > `ValidationError` (translated to `click.UsageError` at the CLI boundary,
  > unchanged); `dump_cmd` forwards its own `--compile-db-filter` into the
  > `DumpRequest` `--dry-run` resolves. Verified against the identical real
  > `g++`+clang project through the typed `DumpRequest`/`resolve_dump_
  > request`/`execute_dump_request` path directly (not the CLI):
  > `tests/test_compile_db_filter_scope.py`'s
  > `TestTypedApiHonorsTheFilterInTheFold`. The CLI's own behavior was
  > re-verified unchanged. This closes item (1) of the "what still blocks the
  > `dump` real-run migration" list below — it does **not** migrate the real
  > run itself, and does not claim to; see the root `AGENTS.md`'s PR C entry
  > for the full account, including why item (2) (castxml) still blocks the
  > migration on its own.
  >
  > One environmental fact that independently rules out doing it in that
  > session: **the default header backend is castxml, and no working
  > castxml was obtainable there.** A conda-forge 0.7.0 build was assembled
  > by hand and segfaults inside `clang::ParseAST` on any input, so every
  > measurement above is clang-backend only. Migrating the real `dump` run
  > while able to exercise only the non-default backend is not a verified
  > change.
  >
  > *The `scan` real-run migration.* `_build_new_snapshot` still calls
  > `service.resolve_input`/`embed_build_source` directly. Re-read against
  > `_resolve_side_snapshot_impl` line by line, the gap is four items, not
  > the two this section had named — and three of them are behaviour
  > changes, not missing plumbing:
  >
  > 1. **L4 extractor default** — unchanged from the note above, and
  >    castxml was re-checked for and is still effectively unavailable, so
  >    this stays documented rather than guessed at.
  > 2. **`public_headers` expansion shape** — `_build_new_snapshot` expands,
  >    `embed_side_build_source` passes through raw; the 2026-08-20 note
  >    above records the regression a "simplification" to the raw shape
  >    caused, so routing scan through the shared wrapper reintroduces it.
  > 3. **Seed collect mode** — `_seeded_includes_and_compile_context` pins
  >    `collect_mode="off"` (a Tier-2 primitive must never execute a build
  >    system as a side effect), while `_build_new_snapshot` passes scan's
  >    real collect mode, so scan *can* run the zero-config inferred build
  >    query in its seed today. Routing through the shared primitive
  >    silently removes that, losing build-derived include seeding for a
  >    source tree with no compile database.
  > 4. **`defer_cleanup`** — `embed_side_build_source` has no such
  >    parameter, and scan hands its embed cleanups to the caller. Purely
  >    additive, and the only one of the four that is.
  >
  > Each of 1–3 could be expressed as an opt-in parameter on the shared
  > primitive, the way `symbols_only`/`allow_build_query`/`changed_paths`
  > already are — that is a legitimate design, and it is what a future slice
  > should do. It was not done here because reproducing roughly a dozen
  > parameter behaviours exactly, on the hot path of every `scan`, with the
  > integration lane only partly executable, is precisely the rewrite shape
  > this area's own review history keeps punishing.
  >
  > *What landed instead.* `tests/test_dump_cli_typed_api_parity.py::
  > test_dump_cli_and_typed_api_agree_on_extraction_contract` — the raw
  > extraction-contract counterpart of that module's existing
  > `ast_compile_args` parity test, which deliberately compares through
  > `split_compile_args`' semantics-preserving normalization and therefore
  > cannot see either divergence above. `profile_fingerprint` hashes the
  > recorded fields as-recorded, so a difference normalization hides is
  > still a comparability failure. The two known-divergent (shape, field)
  > pairs are encoded the same conditional-xfail way
  > `_SCAN_KNOWN_DIVERGENT_SHAPES` already is: the exact diagnosed
  > signature reproduces, or the test fails outright — so "the gap closed"
  > fails as loudly as "a new field diverged", and the mapping cannot go
  > stale silently. Verified in both directions (a deliberately wrong
  > mapping fails).

  > **Slice landed (2026-08-21, later session): the measured divergence above
  > is closed, `scan`'s candidate resolver is migrated, and two further real
  > bugs were found by the measurement itself. The `dump` real run is still
  > NOT migrated — see "what still blocks it" at the end.**
  >
  > *The legacy-match overlap (the note immediately above this one).* The
  > decision that note left open — which of the two mechanisms owns a matched
  > compile database — is made: **when the P0.3 fold resolves a compile context
  > for the headers being parsed, it is the sole source of
  > compile-database-derived context, and the legacy `-p`/`--compile-db`
  > auto-match's own derived flags are unfolded rather than stacked on top of
  > it.** When the fold does not apply (no `--build-info`, or a header no
  > compile unit matches) the legacy match still runs and still applies, so
  > only the overlap is dropped. `--compile-db-filter` does **not** go inert
  > with it — the concern that made this note rank the removal second: the
  > filter reaches the shared fold too, since the previous slice threaded
  > `source_filter` through `seed_includes_and_fold_compile_context`/
  > `resolve_header_compile_context`, so the ordering that note called for was
  > already satisfied by the time this landed.
  >
  > Mechanically it is one conditional, and where it goes matters: the legacy
  > flags are already merged into `effective_gcc_options` by `dump_cmd` before
  > `perform_elf_dump` is called, so `perform_elf_dump` now takes them
  > separately (`legacy_build_context_flags`) and passes the caller's *own*
  > `--gcc-options` string to the fold as its explicit context. Presenting the
  > legacy result to the fold as though it were an explicit user choice is
  > exactly what recorded the same `-D` twice and pushed a derived `-I` through
  > `gcc_option_tokens` instead of `declared_includes`.
  >
  > Measured after: `macro_ops`, `include_sequence`, `scope_fingerprint` and
  > `profile_fingerprint` agree across all three build shapes, and — the
  > user-visible half — `scan --against` a real `dump` baseline for the
  > `extra-include-dir` shape goes from **exit 6, `NOT_COMPARABLE ... differing
  > fields: include_sequence`** to exit 0 for an unchanged library.
  > `_CONTRACT_KNOWN_DIVERGENT_FIELDS` and `_SCAN_KNOWN_DIVERGENT_SHAPES` are
  > both empty; the conditional-xfail mechanism stays for the next divergence.
  >
  > *`scan`'s candidate resolver is migrated.* `scan_engine._build_new_snapshot`
  > builds an `InputSpec`/`SideEvidence` and calls
  > `service_input_resolution._resolve_side_snapshot_impl`, returning its
  > `SideResolution`; `run_scan_core` hands the `BaselineReuseContext` in at
  > resolve time and *reads* `SideResolution.baseline_compile_context` instead
  > of recomputing one from the values it got back. The L2 seed, the
  > `parsed_with_build_context` stamp, the ADR-039 collector gate, the
  > drain-before-embed ordering and the pair-aware baseline rule are now
  > inherited from one implementation rather than hand-written twice.
  >
  > The four divergences this section enumerated are preserved as **opt-in
  > parameters on the shared primitive**, which is what this note said a future
  > slice should do: `seed_collect_mode` (scan's real collect mode, so the
  > zero-config inferred query still seeds includes for a compile-DB-less
  > tree — every other caller keeps the Tier-2 pin), `seed_lang_explicit`
  > (scan's `lang == "c"` seed guard, which does not apply to the parse),
  > `defer_cleanup`, `source_extractor` (`"auto"` → clang), and
  > `expand_public_header_roots` + `source_frontend_compile`. The L4 extractor
  > default therefore stays exactly as documented — closing it would newly
  > require castxml for a `scan --depth source` that works with clang today,
  > and castxml is still unavailable here.
  >
  > `expand_public_header_inputs` moved to `service_scan.py` so the engine-layer
  > resolver can reach it without an engine-imports-CLI edge;
  > `cli_scan_baseline._expand_public_headers` is a thin delegate. The migration
  > *removed* an `ENGINE_CLI_BOUNDARY_ALLOWLIST` entry (`scan_engine.py`'s
  > `cli_buildsource` import) and dropped `scan_engine.py` back below the
  > 1500-line soft limit.
  >
  > Equivalence was measured rather than argued: the candidate snapshot, the
  > effective includes, the effective compile context and the deferred-cleanup
  > count, for three real build shapes × three collect modes, are identical
  > before and after — apart from wall-clock timestamps and the build-source
  > pack's own content hash. `test_scan_engine_calls_the_shared_resolver` was a
  > source-text match on `run_scan_core`; it is replaced by two behavioural pins
  > through a real `scan --against` (the hint carries the *resolved* old-side
  > scopes; a sentinel answer from the resolution is what reaches the baseline
  > parse, which nothing but forwarding can produce).
  >
  > *A second real bug, found by the item-1 verification bar rather than by a
  > report.* Extending the parity measurement from the extraction contract to
  > the *whole* snapshot showed the two paths disagreeing on the L3–L5 payload,
  > and not cosmetically: the `dump` CLI recorded `0/2 symbols matched`,
  > `reachable_declarations=0`, `fact_family_states: empty-confirmed` where the
  > typed path recorded `1/2` matched and a real
  > `source_decl_to_binary_symbol` mapping. Cause:
  > `_write_snapshot_output`'s own `embed_build_source` call passed **no**
  > `public_headers`/`public_header_dirs`, so L4 replay ran with an empty
  > `public_header_roots` set — every declaration classifies private and nothing
  > links. The layer is present and the coverage row honestly reports "partial",
  > so nothing fails; every L4-derived source-ABI finding is simply inert for a
  > `dump`-produced baseline. Fixed on both the ELF and PE/Mach-O paths. With
  > that, the `dump` CLI's written snapshot and `execute_dump_request`'s agree
  > on every field except wall-clock timings and the CLI's own provenance layer
  > (`git_commit`, `version`).
  >
  > **What still blocks routing `dump_cmd`'s real run through
  > `execute_dump_request` — narrowed to two items, both real** (as of the
  > notes below, item 1 itself later split into two: castxml plus the
  > untested Flow-2 `--inputs` fold — see "Item 2 (castxml) is unchanged,
  > but is not the *sole* remaining blocker" further down for the current,
  > correct count). Blocker 4 is
  > closed on measurement, not just on reading: `service.run_dump`'s ELF branch
  > already runs every post-processing pass `perform_elf_dump` does (SYCL,
  > `python_ext`, `python_api`, `numpy_capi`, the G31 header graph, the G28
  > clang-layout attach), the ADR-039 collector now runs inside
  > `_resolve_side_snapshot_impl`, and the whole-snapshot comparison above shows
  > no difference in any field those produce. What remained:
  >
  > 1. **`--compile-db-filter` would go inert.** `InputSpec` deliberately
  >    carries no `compile_db_filter` (see its own replacement comment), so the
  >    shared path cannot narrow the fold or the ADR-039 collector the way the
  >    native `dump` CLI does. Making a documented flag silently do nothing is
  >    worse than the gap. The step is clearly specified — add the field, thread
  >    it into `_seeded_includes_and_compile_context` (whose primitive already
  >    takes a `source_filter`) and into
  >    `attach_build_context_for_parsed_headers`, and mirror the CLI's own
  >    L2-filtered/L3-unfiltered refusal into `resolve_dump_request`, the only
  >    place that knows the resolved collect mode — but it is its own slice.
  > 2. **The default backend is still unexercisable here.** `--ast-frontend`
  >    defaults to castxml and no working castxml is available in this
  >    environment (re-checked), so every measurement above is clang-backend
  >    only. Migrating the real `dump` run while able to exercise only the
  >    non-default backend is not a verified change, and this section has said
  >    so since the previous session.
  >
  > **Item 1 closed (2026-08-21, later session).** `InputSpec.compile_db_filter`
  > exists now, threaded into `_seeded_includes_and_compile_context` (as
  > `source_filter`) and into `attach_build_context_for_parsed_headers`, and
  > `resolve_dump_request` mirrors the CLI's own scope-error refusal —
  > `compile_db_filter_scope_error`, extracted into
  > `service_compare_evidence.reject_compile_db_filter_scope_mismatch` so
  > `CompareRequest.old`/`.new` (which reach the identical fold through
  > `resolve_compare_request`) share one guard rather than risking a second,
  > independently-drifting copy. `dump_cmd` forwards its own
  > `--compile-db-filter` into the request it builds, so `--dry-run` now
  > records the same filter the real run would apply. Landing this drew eight
  > separate Codex-caught corrections, six of them to the scope-mismatch guard
  > itself (`header_conditionals.compile_db_for_filter_scope_check`/
  > `compile_db_filter_scope_error`) — a `--sources`-only tree with an
  > auto-discoverable compile database, a nested
  > `<dir>/build/compile_commands.json`, a pack or Bazel `aquery`/`cquery`
  > `--build-info`, a `--sources` pack with no `--build-info`, a false positive
  > when an explicit `--build-info` resolves to nothing, and a Flow-2
  > `abicheck_inputs/` pack named by `--build-info` — plus two adjacent to it:
  > `InputSpec.of()`, the public loose-value factory (not the guard's own
  > logic), not accepting the new `compile_db_filter` keyword, and the guard
  > being wired into `resolve_dump_request` only rather than also
  > `CompareRequest`'s identical exposure. Each was traced to a real, reachable
  > combination rather than a hypothetical, and each is pinned by its own
  > regression test. **A ninth, related finding was investigated and
  > deliberately left as a documented gap rather than "fixed": the keyword-only
  > `build_compile_db` parameter `execute_dump_request`/
  > `_resolve_side_snapshot_impl` already accept has the identical unguarded
  > shape, but it is not a field of `DumpRequest`/`InputSpec` at all and has no
  > real caller anywhere in the codebase today — it exists purely as
  > scaffolding for the not-yet-landed real-run migration below, so there is no
  > reachable path to validate a guard against, and closing it belongs with
  > that migration rather than shipping unverifiable validation code now.** See
  > the root `AGENTS.md`'s `service_dump_pipeline.py`/PR C "Known gaps" entry
  > for the full, numbered account (search "Item (1) closed") — not reproduced
  > here in full, since it is the identical narrative this plan already defers
  > to for every other slice in this subsection.
  >
  > **Item 2 (castxml) is unchanged, but is not the *sole* remaining blocker
  > — an earlier revision of this note said so, which a later investigation
  > (below, "Investigated further (2026-08-27)") corrected.** Routing
  > `dump_cmd`'s real run through `execute_dump_request` needs castxml
  > (still unavailable in every environment this work has been done in) for
  > the migration itself, **and** it needs the `_write_snapshot_output`
  > Flow-2 `--inputs` pack fold verified against a resolve-time-embedded
  > snapshot — untested as of the 2026-08-27 investigation below, which
  > verified the rest of the sequence but explicitly left this one
  > component open. See that note for the precise, current split rather
  > than trusting this earlier one.
  >
  > Separately, a CI-caught regression from an
  > unrelated fix in the same session — the write-time-embed fix that gave
  > `dump`'s L4 replay real `public_headers`/`public_header_dirs` (the "second
  > real bug" two paragraphs up) — asymmetrically widened `scan`'s own
  > lone-header-file L4 root set relative to `dump`'s, producing a spurious
  > `source_decl_binary_symbol_mismatch` on an unchanged library; fixed with an
  > opt-in `l4_public_headers`/`l4_public_header_dirs` override on
  > `embed_side_build_source`, scoped to `scan`'s candidate resolution alone
  > (`compare`/`dump`'s typed pipeline are unaffected). Documented in full in
  > the same root `AGENTS.md` entry.
  >
  > PR 3C therefore stays blocked on item 2 (castxml) and the untested Flow-2
  > `--inputs` fold noted above, per this section's own ordering rule.

  Two #782 follow-ups that change the *parsed public surface*, not just
  performance, so they belong before the model is called finished: (1)
  compile-unit matching — the L2 include-dir seed is still gathered from
  *every* `CompileUnit` rather than the matched one(s), so an unrelated TU's
  colliding generated header can shadow the matched TU's; (2) forced includes
  — `-include`, `-imacros`, `/FI`, `/FU` are absent from
  `ABI_RELEVANT_FLAG_PREFIXES`, so a matched unit's macro-controlling
  forced-include header never reaches the derived L2 context even though the
  run reports a match and stamps `parsed_with_build_context`. Both were
  recorded as known gaps in the root `AGENTS.md`, which now carries the full
  closure notes.

  > **Landed (2026-08-16).** (1) `HeaderCompileContextResolution` gained
  > `matched_units` (`matched_unit_count` stays as a derived property, so the
  > two cannot drift), and `l2_seed.seed_includes_and_fold_compile_context`
  > now resolves the compile context *before* seeding and restricts
  > `_existing_include_dirs` to that set — falling back to every unit only
  > when nothing matched, which is the case the seed was built for (a public
  > header the compile DB does not cover) and where there is no narrower set
  > to prefer. The entry's own "two independent call sites" worry
  > (`_existing_include_dirs`'s caller *and*
  > `service_input_resolution._seeded_includes`) was obsolete rather than
  > addressed: PR C merged those two into one, so there was one site left to
  > restrict.
  >
  > (2) **The forced-include fix the `AGENTS.md` entry proposed — a
  > spaced-value branch in `extract_abi_relevant_flags` — was investigated
  > and found to be actively wrong, and this is the part worth not
  > rediscovering.** `source_extractors._argv.replay_extra_flags` already
  > handles forced includes for L4 by a *different* route: it carries
  > `abi_relevant_flags` through **and**, separately, re-scans raw `argv` for
  > the same tokens without consulting the first pass's `seen` set. Capturing
  > a forced include into that list would therefore have made every L4 replay
  > command carry `-include config.h` twice — a silent double inclusion that
  > a header without include guards turns into a hard redefinition error.
  > Closed at the layer that actually had the gap instead:
  > `header_utils.forced_include_operands` is the one shared recognizer (the
  > replay matchers moved into that leaf — the one already owning this
  > codebase's include-flag vocabulary, which both consumers already sit
  > above — so L2 and L4 recognize the same spellings from one
  > implementation) and
  > `header_compile_context._forced_include_flags` renders it into the L2
  > command straight from `cu.argv`, leaving L4 bit-for-bit unchanged. Forced
  > includes also now participate in the ambiguity signature (two units
  > forcing *different* macro-controlling headers fail closed rather than
  > silently applying whichever grouped first) and in the AST cache key
  > (`header_utils.cache_relevant_operand_dirs`, now shared by all three
  > header-parse cache keys). `-include-pch` and `/FU` are deliberately not
  > rendered; the ADR-029 D9 *drift-detection* half — a changed forced-include
  > header does not raise `ABI_RELEVANT_BUILD_FLAG_CHANGED` — stays open,
  > since closing it needs a structured `CompileUnit` field, a
  > `BUILD_EVIDENCE_VERSION` bump and `build_diff` wiring, and specifically
  > *not* another attempt to route it through `ABI_RELEVANT_FLAG_PREFIXES`.
  > Tests: `tests/test_build_context_completeness.py` (20 cases; 9 verified to
  > fail against the pre-fix code, and
  > `TestReplayStillEmitsForcedIncludesExactlyOnce` kept as the executable
  > record of the rejected fix).
- **PR 3C — the removal itself** (everything the rest of this section
  describes), landing only after **all three** resolvers converge — 3A (both
  `dump` and `scan`) and 3B. 3C must not land on 3A-covers-`dump`-only; a
  `.abicheck.yml` build input used for a `dump` baseline and a
  `scan --against` candidate needs one interpreter, not two, before the flags
  that configure it are removed.

  > **Status (2026-08-21): still blocked — but on a materially shorter list
  > than this note carried earlier the same day.** Blocker 5's three
  > sub-issues and blocker 6 are now closed (see the second 2026-08-21 note
  > in the 3A sub-section above): `InputSpec.path` expresses the source-only
  > branch, the two collect-mode resolvers agree and are pinned by a
  > grid test, the write-time embed is idempotent, `dump_cmd` builds one
  > real `DumpRequest` that `--dry-run` renders from, and the pair-aware
  > baseline-reuse rule lives in one shared primitive with an opt-in hook on
  > the shared resolver.
  >
  > **Update: item 1's `dump`-side half is now closed for ELF.** See PR C's
  > own section above ("Slice landed: the real ELF run is migrated") — the
  > real ELF `dump` run now executes through `execute_dump_request`, the
  > identical shared pipeline `scan`'s candidate resolution already routes
  > through (that half of item 1 closed earlier, in the 3A sub-section
  > above). **3C is therefore down to exactly one blocker on item 1**:
  > `handle_non_elf_dump` (PE/Mach-O) still executes independently — no
  > PE/Mach-O toolchain was available where the ELF migration was verified,
  > and this section's own "measure, don't reason" discipline means that
  > half is not attempted without one. Item 2 (the `scan`-vs-`dump`/
  > `compare` L4 extractor default divergence) is unchanged, deliberately —
  > closing item 1 did not require closing item 2, and item 2 stays its own
  > separate, deferred decision. 3C's removal itself remains blocked until
  > PE/Mach-O closes item 1 in full, per this entry's own "all three
  > resolvers" rule.
  >
  > **What still blocks the removal**, restated precisely because "3A is not
  > done" is now too coarse to act on (numbered list below predates the
  > update immediately above; item 1's `dump`-side/ELF half is superseded by
  > it, not by anything in this list):
  >
  > 1. **Neither real run routes through the shared pipeline yet.** The ELF/
  >    PE/Mach-O `dump` executes through `perform_elf_dump`/
  >    `handle_non_elf_dump`, and `scan`'s candidate through
  >    `service.resolve_input`/`embed_build_source` directly. This is the
  >    condition 3C's own ordering rule actually names — "a `.abicheck.yml`
  >    build input used for a `dump` baseline and a `scan --against`
  >    candidate needs one interpreter, not two" — and two interpreters is
  >    still what exists. The prerequisites for closing it are now concrete
  >    rather than open-ended: typed-API representation for the ADR-039
  >    collector's CLI-only inputs (`--compile-db-filter`, the raw
  >    `effective_compile_db`), and reordering
  >    `_write_snapshot_output`'s provenance/`--inputs`/depth-gate sequence
  >    around a resolve-time embed.
  > 2. **The L4 extractor default still diverges, in more than one pairing.**
  >    `scan`'s candidate resolution hardcodes `source_extractor="auto"`
  >    (`embed_build_source`, ignoring whatever `--ast-frontend` scan itself
  >    received), which resolves to clang; `compare`'s implicit-dump operand
  >    and the typed `execute_dump_request` pipeline reach
  >    `effective_frontend`, which resolves an unflagged `"auto"` to castxml
  >    by default -- **not unconditionally: an `ABICHECK_AST_FRONTEND`
  >    override changes this (`clang` resolves clang instead; `hybrid`
  >    resolves `"hybrid"`, which rejects a raw-source `depth="source"`
  >    request outright rather than resolving to any extractor at all --
  >    see the dated note's own environment-resolution matrix below for the
  >    full per-value account, Codex review, fresh evidence).** The native
  >    `dump` CLI is neither of those — it
  >    resolves its own `header_backend` from the explicit `--ast-frontend`
  >    flag or a `compile.frontend` config value only, never through
  >    `effective_frontend`, and defaults an unflagged invocation to clang
  >    too (Codex review, fresh evidence, correcting this item's own prior
  >    grouping of `dump` with `compare` here — see the dated note two
  >    sections below for the full investigation).** Removing the flags
  >    does not cause any of this, but it is a live "two interpreters of one
  >    config" instance. **No longer "unverifiable without castxml"** — see
  >    the 2026-08-27 note below this
  >    block, which obtained a real one and reproduced the divergence
  >    directly; the fix itself is still not attempted, deliberately.
  > 3. **Prerequisite 3's own remaining `-H`-directory gap**, below, is
  >    unchanged. **Update (2026-08-27): closed** — see the dated note
  >    below this list's own section for the fix and its tests. This item
  >    no longer blocks anything; items 1 and 2 above still do.
  >
  > Deliberately *not* forced through on the strength of blocker 5/6 being
  > closed: the flags' removal moves their inputs into config, and doing that
  > while two resolvers still interpret that config independently is the
  > exact failure this whole section was split into three slices to avoid.
  >
  > **Re-confirmed (2026-08-21, later session), with item 1 now measured
  > rather than asserted.** Both real-run migrations were attempted and
  > stopped; see the "Investigated (2026-08-21, later session)" note in the
  > 3A sub-section for the field-by-field evidence. The short version, as it
  > bears on this removal: the two interpreters do not merely *risk*
  > disagreeing about a `.abicheck.yml` build input — they demonstrably
  > already disagree about a `--build-info` one, recording a different
  > `contract.profile_fields.macro_ops` and `include_sequence` (and
  > therefore a different `profile_fingerprint`) for the same library from
  > the same evidence. Removing the flags in that state would move
  > `build.query`/`build.compile_db` into config while leaving two
  > interpreters that are *known* to produce non-comparable snapshots,
  > which is strictly worse than the ordering rule already forbids. Item 1
  > additionally now has a stated sub-ordering: thread `--compile-db-filter`
  > into the shared fold first, then decide and ship the legacy-match
  > removal, then migrate. The first of those three landed in that session
  > (see the same 3A note) and was a user-facing bug fix in its own right;
  > the second and third are untouched.
  >
  > **Update (2026-08-21, later still): the first two of item 1's three
  > sub-ordering steps are now both done — the legacy-match removal decision
  > was shipped, and `--compile-db-filter` gained typed-API representation
  > (eight review-caught corrections, plus one related finding deliberately
  > left as a documented gap; see the 3A sub-section's "Item 1 closed" note
  > above for the precise split, and the root `AGENTS.md` for the full
  > numbered account).** Item 1 of this status block therefore narrows from
  > "typed-API representation... and reordering the write-time embed" to two
  > remaining pieces, both still open and neither optional: reordering
  > `_write_snapshot_output`'s provenance/`--inputs`/depth-gate sequence
  > around a resolve-time embed, and the still-unstarted third step —
  > actually migrating `dump_cmd`'s real ELF/PE/Mach-O execution onto
  > `execute_dump_request`. Of those two, only the migration step is blocked
  > on an external dependency: castxml, unavailable in every environment
  > this work had been done in as of this note (item 2 of the 3A
  > sub-section's own "What still blocks routing `dump_cmd`'s real run"
  > note) — an implementer resuming this item should not read that as
  > license to skip the reordering, which has no such external blocker and
  > could be done independently. **Update (2026-08-27): the tooling half of
  > this specific blocker is closed — see the 2026-08-27 item-2 note below,
  > which obtained and verified a local, policy-compliant castxml. That
  > note's own castxml install was used only to reproduce the already-known
  > L4 extractor divergence (a narrower, already-scoped test run); "the
  > migration itself" — this bullet's own byte-identical-output
  > verification — was not attempted in that same session and remains open
  > for exactly that reason: not for lack of castxml any more, but for lack
  > of the work itself.** Items 2 (the L4 extractor default divergence) and
  > 3 (the `-H` directory gap, below) are unchanged in substance as of this
  > note, item 2's "unverifiable" framing corrected per its own note — item
  > 3 is later closed (2026-08-27); see that dated note below for the fix.
  >
  > **Investigated further (2026-08-27): the reordering is real work to
  > *verify*, and its depth-gate/provenance/dependency-scope half is now
  > verified — real evidence, not further reasoning, that
  > `_write_snapshot_output`'s current sequence already handles a
  > resolve-time-embedded snapshot correctly there, with no code change
  > needed for that half.** (The Flow-2 `--inputs` half is separately
  > addressed below and stays open.) Traced through both functions' actual
  > bodies rather than reasoned about
  > abstractly: `execute_dump_request`'s own `enforce_requested_depth`
  > (`workflows/artifact/execute.py`) and `_write_snapshot_output`'s
  > `check_requested_depth_satisfied` (`cli_dump_helpers.py`) are not two
  > independent implementations that could disagree — the latter's
  > `_DEPTH_RANK`/`_gated_source_label` are a documented `= DEPTH_RANK`
  > constant and a documented compatibility alias for
  > `evidence_depth.gated_source_label`, the exact same shared primitives
  > the former uses — so calling both in sequence (once inside
  > `execute_dump_request`, once inside `_write_snapshot_output`, if a
  > migrated `perform_elf_dump` called both) is redundant, not risky.
  > Likewise the provenance fold (`fold_dump_provenance_into_dict`) and the
  > dependency-scope resolution (`resolve_dependency_scope`) both read
  > `snap.build_source`/`snap` state directly, with no dependency on *when*
  > that state was populated. The one real question this reading could not
  > settle from the code alone — whether `build_source_already_satisfies`
  > (PR 3A blocker 5 sub-issue 3) genuinely prevents a second embed against
  > a *real*, non-stubbed pack the typed pipeline itself produced, and
  > whether the rest of the sequence still completes correctly around it —
  > is now answered end to end:
  > `tests/test_dump_write_after_resolve_time_embed.py` builds a real
  > library, runs it through the actual `resolve_dump_request`/
  > `execute_dump_request` split (`--ast-frontend clang` — the header parse
  > itself never invokes castxml; a policy-non-compliant castxml stub
  > (`pip install castxml`, 0.4.5, below the 0.6.11 minimum
  > `castxml_policy.py` enforces for an authoritative scan) had to be
  > installed in this session purely to satisfy `tests/conftest.py`'s
  > `integration`-marker gate, which checks only `shutil.which("castxml")`
  > and cannot tell that this specific test never calls it — a CI lane that
  > actually runs the `integration` marker has a real, policy-compliant
  > castxml installed for its other tests, so this is a local-session
  > workaround, not a statement that the test needs none), and hands the
  > result straight to
  > `_write_snapshot_output` with an explicit `--depth source` — asserting
  > no second embed occurs, the depth gate does not raise, the provenance
  > fold correctly reports `effective_depth == "source"`/`degraded is
  > False`, the written JSON's own `build_source.manifest.coverage` rows
  > report L3/L4 `"present"`, and — since `BuildSourcePack` serializes its
  > manifest independently of `build_evidence`/`source_abi`/`source_graph`,
  > so a regression that drops the real per-layer payload while leaving
  > those coverage labels stale would pass a labels-only check (Codex
  > review, fresh evidence) — one representative real fact out of each
  > layer's own serialized payload: the real compile unit's `standard`
  > (L3), the real `source_decl_to_binary_symbol` mapping entry for the
  > compiled symbol (L4), and a non-empty `source_graph.nodes` list (L5).
  > The dependency-scope resolution step is checked too, and separately
  > from every fact above (Codex review, fresh evidence — an earlier
  > revision asserted only provenance/`build_source` facts, none of which
  > `resolve_dependency_scope` touches, so it would have passed unchanged
  > even with that call removed entirely): `execute_dump_request`'s own
  > result carries `dependency_scope == "full"` (it never calls this
  > step), so asserting the *written* JSON's `dependency_scope ==
  > "filtered"` is real evidence the step ran on the already-embedded
  > snapshot, not a value merely carried through.
  > A second case pins the depth gate's own negative direction (a
  > `depth="binary"` resolve-time result — no header parse at all, so
  > `build_source` stays genuinely `None` — still raises
  > `DumpDepthNotSatisfiedError` for an explicit `--depth source`), so the
  > redundant check is proven to still be a real gate, not merely inert.
  >
  > One genuine, previously-undocumented discovery along the way, recorded
  > in the test's own docstring so it is not rediscovered: a *headers-only*
  > resolve-time result (no `--sources`/`--build-info` at all) already
  > populates `snap.build_source` with a real, if L3/L4-empty, pack —
  > the header-graph attach pass runs and records L5 coverage regardless of
  > whether any build evidence was given. "No build/source evidence" for
  > this class of test therefore needs the header parse itself to never
  > run (`depth="binary"`, no `-H`), not merely L3/L4 to be empty.
  >
  > **What this narrows, precisely — the reordering item is narrowed, not
  > closed.** The depth-gate/provenance/dependency-scope half of the
  > sequence is verified safe for a resolve-time embed, so no
  > `_write_snapshot_output` code change is needed for *that* half. **Still
  > open, and deliberately not folded into "closed" above (Codex review
  > caught an earlier revision of this note doing exactly that): whether
  > `_write_snapshot_output`'s Flow-2 `--inputs` pack fold** — the third
  > name in "provenance/`--inputs`/depth-gate sequence" — **behaves
  > identically when layered on top of a resolve-time embed.** Untested
  > here; constructing a real Flow-2 pack fixture was not attempted. This
  > prerequisite therefore stays open until that combined path has its own
  > test, alongside the still-unstarted third step: whether the *whole*
  > migrated pipeline (a real `perform_elf_dump` calling
  > `execute_dump_request` end to end, ADR-039 collector included) produces
  > output byte-identical to today's write-time-embed path under the
  > *default* castxml backend — "the migration itself." **No longer blocked
  > on castxml being unavailable** (see the 2026-08-27 item-2 note below,
  > which obtained one), but this exact byte-identical-output verification
  > was not attempted with it — the castxml install that session was used
  > only to reproduce the L4 extractor divergence, a different, narrower,
  > already-scoped test run. This step stays open until someone actually
  > runs it.
  >
  > **Update (2026-08-28): the Flow-2 half is closed.** A real Flow-2 pack
  > fixture (`write_inputs_pack`) was built and run through
  > `_write_snapshot_output(snap, ..., inputs_pack=...)` with `snap` produced
  > by the real `resolve_dump_request`/`execute_dump_request` split, not a
  > hand-stubbed pack. The fold does the identical combination
  > `_combine_packs`'s own documented per-layer priority already predicts,
  > regardless of how `snap.build_source` was populated: it is not a
  > per-fact merge — `bi_pack` (the resolve-time-embedded snapshot passed as
  > `embed_inputs_pack`'s first argument) wins L3 (`build_evidence`) first,
  > while `src_pack` (the ingested Flow-2 pack, second argument) wins
  > L4/L5 (`source_abi`/`source_graph`) first when it supplies real facts —
  > wholesale per layer, not unioned. Verified both directions with a
  > fixture where the Flow-2 pack supplies a declaration (`helper`, declared
  > in no header the resolve-time embed ever parsed) the resolve-time L4
  > surface has no way to see: the written snapshot links `helper` (the
  > Flow-2 replacement took effect) while the resolve-time embed's own L3
  > compile-unit facts — the one layer Flow-2 did not supply here — survive
  > the combination untouched, and the resolve-time embed's own L4 fact for
  > `sum()` does *not* survive (pinning the wholesale-replacement,
  > not-a-merge semantics itself, not just the positive case). **Correction
  > (Codex review, PR #917): L5 is not independently preserved in this
  > scenario** — `ingest_inputs_pack` builds `source_abi`/`source_graph`
  > together from the same `tus` list whenever any TU is supplied, so a
  > Flow-2 pack that replaces L4 always supplies a real, non-empty L5 graph
  > too, and `_combine_packs` prefers `src_pack` for L5 exactly as it does
  > for L4 — the resolve-time embed's own graph (17 nodes, including a
  > `sum()` declaration node) is replaced wholesale by Flow-2's own graph
  > (8 nodes, containing only `helper`), confirmed directly by asserting the
  > `sum()` node is absent and the `helper` node present in the final graph
  > rather than only that the graph is non-empty (an earlier revision of
  > this test asserted only non-emptiness, which passed regardless of which
  > pack's graph won). This is not new behavior introduced by a
  > resolve-time embed — `_combine_packs`'s per-layer priority is
  > unconditional on how either pack was produced — but it was genuinely
  > unverified for this specific base-pack shape until now. Test:
  > `tests/test_dump_write_after_resolve_time_embed.py::
  > test_write_snapshot_output_folds_a_flow2_inputs_pack_onto_a_resolve_time_embedded_snapshot`.
  > This closes the Flow-2 half of the reordering prerequisite in full; the
  > still-unstarted third step above (the whole migrated pipeline's
  > byte-identical-output verification, blocked only on castxml before this
  > update, no longer blocked on it either) remains the sole open item in
  > this prerequisite.
  >
  > **Re-audited (2026-08-28, later session), no code change, no drift
  > found.** Re-checked this section's live claims directly against current
  > `main` rather than trusting the prior notes' status cells: `dump_cmd`'s
  > real ELF/PE/Mach-O execution still goes through `perform_elf_dump`/
  > `handle_non_elf_dump`, not `execute_dump_request` (the migration itself
  > is still unstarted); `scan_engine.py` still hardcodes
  > `source_extractor="auto"`; `--build-query`/`--build-compile-db`/
  > `--exit-code-scheme` are all still live CLI options. Nothing in this
  > section had gone stale.
  >
  > **Correction (Codex review, same PR): the castxml-tooling paragraph this
  > note originally carried here sent an implementer the wrong way and is
  > replaced rather than kept for history.** It reported `apt`'s castxml
  > (0.6.3, below the 0.6.11 floor) and a hand-assembled conda-forge 0.7.0
  > build with an unresolved `libclang-cpp.so.20.1` dependency — both real
  > observations, but neither needed: this repository already ships a
  > checksum-pinned installer, `action/install-castxml.sh` (the same one
  > `.github/actions/setup-castxml` and the CI `integration` lane use), with
  > a `castxml-ubuntu-24.04-x86_64` asset. Running it in this session's own
  > Ubuntu 24.04/x86_64 container installed CastXML `0.6.20260105-g9864b1e`
  > (bundled Clang 21.1.8) in under a minute, no dependency assembly
  > required, and `castxml_policy.evaluate_castxml_version()` confirms
  > `supported=True` against its real `--version` output. **Any future note
  > in this section needing a policy-compliant castxml should run
  > `action/install-castxml.sh` first, rather than reassembling one from
  > `apt`/`pip`/conda-forge by hand** — `apt`'s own package genuinely fails
  > the version floor, and `pip`'s only wheel is far below it too, but
  > conda-forge itself is not unusable: the 2026-08-27 note below this one
  > records a working conda-forge 0.7.0 install (`supported=True`), and the
  > CI `integration` lane's own Windows leg installs castxml from
  > conda-forge. What actually failed in *this* session was one manual
  > dependency-assembly attempt (a missing `libclang-cpp.so.20.1` payload
  > from the obvious conda-forge counterpart package) — not conda-forge as a
  > source. Either route can work; the pinned installer is just faster and
  > doesn't require re-solving that dependency split by hand.
  >
  > With that installer's castxml on `PATH`, `tests/
  > test_dump_write_after_resolve_time_embed.py`'s three cases — previously
  > only exercised under `--ast-frontend clang` per that module's own notes
  > above, since no working castxml was available in the sessions that wrote
  > it — now run and pass under the `integration` marker with a real,
  > policy-compliant castxml present (not re-verified against the *default*
  > castxml backend specifically, since that suite still pins
  > `--ast-frontend clang` explicitly rather than exercising an unflagged
  > `dump`/`execute_dump_request` call; that distinction is unchanged by
  > this note). `tests/test_dump_scan_l3_comparability.py` was re-run too, as
  > a reconfirmation rather than a new finding: 4 passed, 2 xfailed — the
  > same `_SCAN_KNOWN_DIVERGENT_FRONTENDS` signature the fact owner already
  > documents, not a new or different divergence. Neither run moves the
  > still-open items: the whole migrated pipeline's byte-identical-output
  > verification under the default castxml backend, and item 2's L4
  > extractor default divergence fix, are both untouched — this note only
  > removes a wrong tooling recipe and confirms the right one works here,
  > it does not attempt the migration itself.
  >
  > **Item 2 (the L4 extractor default divergence) is now locally
  > reconfirmed under real castxml — but this is a reproduction of an
  > already-established fact, not its first verification, and deliberately
  > still not fixed (2026-08-27, corrected same day — Codex review caught an
  > earlier revision of this note overclaiming both halves of that
  > sentence).** The divergence was already the *fact owner*'s own recorded
  > finding, not new: `tests/test_dump_scan_l3_comparability.py`'s own
  > docstring (added in an earlier commit, `2f1accaa`) already states that
  > running its `scan`-comparison tests under castxml surfaced this exact
  > divergence and pinned its signature as
  > `_SCAN_KNOWN_DIVERGENT_FRONTENDS = frozenset({"castxml"})` — and
  > `.github/workflows/ci.yml`'s `integration` lane already installs a real,
  > policy-compliant castxml (`./.github/actions/setup-castxml`) and already
  > runs this exact module there. So this was neither "unverifiable" nor
  > "first-time" in any absolute sense; what this session's environment
  > specifically lacked, and what genuinely changed, was a local castxml
  > install to reproduce that CI-side finding without waiting on a CI run.
  > A genuine, policy-compliant castxml (0.7.0, conda-forge, within
  > `castxml_policy.py`'s `>=0.6.11,<0.8.0` range, bundled Clang 20) was
  > obtained and installed in *this* environment — `.conda` files are zip
  > archives around zstd-compressed tarballs, not tarballs themselves, and
  > the extracted binary needs its `share/castxml/` resource tree alongside
  > it at a real install prefix, not just the bare executable on `PATH` —
  > and verified via `castxml_policy.evaluate_castxml_version()` returning
  > `supported=True`. With it, the two already-pinned xfail cases
  > (`test_scan_against_real_dump_baseline_is_comparable_on_unchanged_source`
  > and `test_scan_against_real_dump_baseline_matches_reported_cli_invocation`)
  > were reproduced locally, matching exactly the divergence their own
  > long-standing module docstring already predicted: `scan`'s
  > candidate resolution *hardcodes* `source_extractor="auto"`
  > (`scan_engine.py`, `_build_new_snapshot`) regardless of whatever
  > `--ast-frontend` value scan itself received, which `_make_source_
  > extractor` (`buildsource/inline.py`) resolves to clang -- while these
  > two tests give `dump` an *explicit* `--ast-frontend castxml`, which the
  > native `dump` CLI honors directly (`resolve_dump_compile_context()`,
  > never through `effective_frontend`). **The two land on the same
  > castxml-vs-clang outcome these tests observe, but not through the
  > mechanism this paragraph originally described (Codex review, fresh
  > evidence, correcting this exact paragraph's own prior "dump/compare
  > reach `effective_frontend(...)`" claim) — `dump`'s own CLI path never
  > calls `effective_frontend` at all; only `compare`'s implicit-dump
  > operand and the typed `execute_dump_request` pipeline do, and only for
  > an *unflagged* `"auto"` request, which is not the shape these two tests
  > exercise.** The rest of the `integration`-marked
  > suite for this area — `test_dump_cli_typed_api_parity.py`,
  > `test_dump_scan_l3_comparability.py`'s non-xfailed cases,
  > `test_dump_write_after_resolve_time_embed.py`,
  > `test_dump_embed_idempotence.py` — is 23 passed / 9 deselected / 2 xfailed
  > under real castxml, i.e. this is the *only* known divergence this suite
  > can currently see.
  >
  > Deliberately **not** changed as part of confirming this: flipping
  > `scan_engine.py`'s `source_extractor="auto"` to
  > `effective_frontend(compile_context, header_backend)` (or an equivalent
  > shared resolver — `abicheck/buildsource/source_extractors/` already has
  > one, `resolve_source_extractor` in its `resolver.py` (re-exported by the
  > package's `__init__.py`), independent of `_make_source_extractor`, worth
  > checking for reuse before hand-rolling a call site) would make
  > `scan --depth source` require castxml at its defaults for every user who
  > doesn't already have one installed — that line's own existing comment
  > already names this precisely: "a real behaviour change for real users...
  > unverifiable without a castxml-capable lane," calling for "its own
  > dedicated verification against real castxml/clang divergence in
  > production usage, not a side effect of hardening this module's test
  > coverage." Having a local castxml install closes the *unverifiable*
  > half of that sentence for this environment (CI's own `integration` lane
  > already had it, per above), not the *dedicated verification in
  > production usage* half — the two xfail tests exercise one project shape
  > each, not the breadth "production usage" implies, and a default-changing
  > fix here is exactly the class of decision this file's own established
  > "known gaps over risky reactive patches" convention (see this plan's own
  > 2026-08-19 note above, and `AGENTS.md`'s "Known gaps" entries throughout
  > this same code area) says belongs in its own dedicated, deliberately
  > verified pass rather than a rushed follow-on to an investigation whose
  > actual subject was the plan doc, not this call site — not a claim that
  > any repository rule requires a maintainer's explicit sign-off before it
  > can land (Codex review — an earlier revision of this note overstated
  > that). Left as item 2, unchanged in substance, with its "unverifiable"
  > framing corrected to "verified and reproduced, fix not yet attempted"
  > so a future session does not have to redo the castxml acquisition to
  > pick this up.

  > **Item 2 attempted, and reverted: the flag flip is unsafe, and the real
  > blocking bug is now precisely characterized (2026-08-27).** With a real,
  > policy-compliant castxml now available, the obvious fix was tried:
  > `scan_engine.py`'s `source_extractor="auto"` (always clang) changed to
  > `source_extractor=None`, which `embed_side_build_source` already
  > documents as the untaken path — it lets the L4 replay frontend resolve
  > via `service_compare_evidence.effective_frontend`, the same primitive
  > `dump`/`compare` already use, matching L4 to the L2 header-AST parse
  > `scan` already runs through the identical `header_backend="auto"`
  > resolution. Verified against the two `_SCAN_KNOWN_DIVERGENT_FRONTENDS`
  > xfail tests: the *originally diagnosed* divergence signature
  > (`COMPATIBLE_WITH_RISK` naming `source_fact_coverage_incomplete`) no
  > longer reproduced — but a *different*, real failure did:
  > `source_binary_provenance_mismatch` on both sides ("6/7 exportable
  > public declarations... do not map to any exported binary symbol").
  >
  > Traced to ground rather than left as a surprising number. A minimal
  > `struct Widget { int x, y; int sum() const {...}; }` + a free function
  > `compute(const Widget&)`, compiled and dumped directly (`dump --depth
  > source --ast-frontend castxml`, no `scan` involved) reproduces the
  > identical shape: `exported_symbols: 2, matched_symbols: 1`. Inspecting
  > the raw `source_decl_to_binary_symbol` mapping shows why —
  > `_CastxmlParser.parse_functions()` (`dumper_castxml.py`), reused
  > verbatim by the castxml L4 extractor's `_parse_root`
  > (`buildsource/source_extractors/castxml.py`), returns castxml's
  > compiler-synthesized IMPLICIT special members for `Widget` (three
  > constructor overloads, two assignment operators, the destructor —
  > `Widget`'s own real, user-written code declares none of these) as
  > ordinary `Function` entries, all with a real, public-header-declared
  > `origin`. `buildsource/source_extractors/base.py`'s `entity_from_function`
  > — the shared, tool-independent model→`SourceEntity` mapping castxml's
  > L4 path (and only that path; clang's own L4 extractor builds
  > `SourceEntity` directly and never goes through this function) routes
  > through — computes `api_relevant` from `fn.origin`/`fn.access` alone; it
  > never consults `fn.visibility`, `is_synthetic_ctor_key`/
  > `is_synthetic_dtor_key`, or castxml's own raw `artificial="1"` XML
  > attribute. So every one of these six phantom, never-written declarations
  > lands in the L4 `reachable_source_surface` as real public API — and,
  > since a *trivial* implicit special member is essentially never emitted
  > as its own out-of-line symbol, five of the six show up as
  > `decls_without_symbol`, which is exactly what drags the match ratio low
  > enough to trip `source_binary_provenance_mismatch`'s "this source
  > doesn't look like it corresponds to this binary" heuristic — a false
  > positive of that heuristic, not evidence of an actual checkout/binary
  > mismatch. (The seventh entry, `compute()` itself, is a *separate*,
  > already-expected, non-bug absence on both castxml and clang alike — it
  > is declared and defined only in the `.cpp` TU, never in the public
  > header, so both backends correctly exclude it from the public source
  > surface regardless of this bug.)
  >
  > This is why the flag flip was reverted rather than shipped: it is
  > directionally correct (matching L4 to L2's own frontend, exactly as
  > `embed_side_build_source`'s own docstring already prescribed) but
  > surfaces a genuine, previously-invisible castxml L4 extractor bug —
  > invisible before only because `scan`'s L4 replay always used clang
  > regardless of `--ast-frontend`, so castxml's L4 declaration surface was
  > never actually exercised end to end through `scan --against` until this
  > attempt. `dump --depth source --ast-frontend castxml` on any class
  > relying on the compiler's own implicit special members already carries
  > this same defect today, independent of `scan` or this migration — it
  > just has no comparable "provenance mismatch" self-check to trip over it
  > the way the `scan`-side comparison did.
  >
  > **Deliberately not fixed in the same pass, and here is exactly why.**
  > The obvious-looking fix — exclude `fn.visibility == Visibility.HIDDEN`
  > from `api_relevant` in `entity_from_function` — was checked directly
  > against the raw XML and rejected: `castxml.py`'s `_parse_root`
  > constructs `_CastxmlParser` with **empty** `exported_dynamic`/
  > `exported_static` sets (L4 has no ELF symbol table to check against at
  > parse time), so `_visibility()`'s plain ELF-lookup fallback returns
  > `HIDDEN` unconditionally for *every* ordinary function in this context
  > too — confirmed directly: `sum()`, the one real, legitimate public
  > method in the fixture, resolves to `visibility=HIDDEN` under this same
  > empty-ELF-set L4 parse (only the ctor/dtor-specific fallback in
  > `_ctor_or_dtor_visibility` treats `artificial` specially; ordinary
  > methods have no such fallback at L4 parse time at all). Filtering on
  > `visibility` would therefore exclude every real declaration from L4
  > along with the phantom ones, not just the phantom ones. The two
  > synthetic-mangled-name markers already in this codebase
  > (`is_synthetic_ctor_key`/`is_synthetic_dtor_key`) are closer but still
  > incomplete: castxml emits a real-looking Itanium mangled name for a
  > compiler-synthesized `operator=` (confirmed: `_ZN6WidgetaSERKS_`/
  > `_ZN6WidgetaSEOS_` in the fixture above), so neither marker catches it —
  > two of the six phantom entries would still leak through. The only
  > reliable, general signal is castxml's own raw `artificial="1"` XML
  > attribute, which `_parse_function_element` currently reads *only* for
  > the `Constructor`/`Destructor` branch and never records on the
  > `Function` object itself for any other declaration kind (`operator=`
  > included) — closing this for real needs a new field on the shared,
  > public `Function` model (a real, if narrow, public-API/schema-version
  > change per `model.py`'s own documented contract), populated
  > consistently by both header backends, and re-verified against every
  > existing consumer of `Function.visibility`'s current HIDDEN/PUBLIC
  > split before trusting a change to it — a genuine, separate,
  > cross-cutting fix, not a follow-up edit to `base.py` alone. Filed here,
  > per this file's own "known gaps over risky reactive patches"
  > discipline, rather than attempted under continued investigation
  > pressure. Item 2 therefore stays open, with its blocker now precisely
  > characterized instead of merely "unverifiable" or "needs its own
  > dedicated pass" in the abstract.

  > **A new, previously-undocumented divergence found while investigating
  > item 2's migration risk directly (2026-08-28): `dump`'s own CLI L4
  > extractor default disagrees with `compare`'s implicit-dump operand and
  > the typed `execute_dump_request` pipeline — a real bug, unrelated to
  > whether the migration happens, and it changes the migration's risk
  > profile rather than only its verification burden.** Traced end to end,
  > not guessed: `perform_elf_dump` (`cli_dump_helpers.py`) receives
  > `header_backend` from the CLI's own resolved value and forwards it
  > **unresolved** — `extractor=header_backend`, still the literal string
  > `"auto"` when `--ast-frontend` is not given — all the way down through
  > `_write_snapshot_output` → `embed_build_source` → `collect_inline_pack`
  > to `buildsource.inline._make_source_extractor`, which (per its own,
  > already-documented behavior) treats anything but the literal string
  > `"castxml"` as clang -- **unconditionally**, with no `ABICHECK_AST_
  > FRONTEND` consultation of its own. So a plain `dump --depth source`
  > with no explicit `--ast-frontend` resolves its L4 source-ABI replay to
  > **clang**. Confirmed with a debug spy on the real CLI invocation
  > (`header_backend` captured as the literal `"auto"` at the
  > `perform_elf_dump` call site).
  >
  > **Third qualification (Codex review, fresh evidence, two rounds --
  > the first draft of this qualification was itself wrong and is
  > corrected here rather than left standing).** A first attempt at this
  > qualification claimed the divergence additionally requires
  > `ABICHECK_AST_FRONTEND` to be unset, alongside no config-selected
  > frontend. That is false for the env var half: `ABICHECK_AST_FRONTEND`
  > is consulted by `dumper._resolve_header_backend` (the L2 header-AST
  > backend picker) and by `compare`/`execute_dump_request`'s own
  > `effective_frontend`, but **never** by the native `dump` CLI's L4
  > extractor selection — `resolve_dump_compile_context()`
  > (`cli_dump_helpers.py`/`cli_options.resolve_compile_context`) only
  > ever resolves `header_backend` from the explicit CLI flag or the
  > discovered `.abicheck.yml`'s `compile.frontend`, never from the
  > environment. So `ABICHECK_AST_FRONTEND=castxml` does **not** prevent
  > this reproduction: the `dump` CLI's `header_backend` still resolves to
  > the un-given default `"auto"` (env-var-blind) and `_make_source_
  > extractor` still treats that as clang, while `compare`'s/`execute_
  > dump_request`'s `effective_frontend` honors the env var and resolves
  > castxml — the two sides still disagree. `ABICHECK_AST_FRONTEND=clang`
  > closes this specific gap by coincidence: `_make_source_extractor` was
  > already going to pick clang by default, and the env var pushes
  > `effective_frontend` to the identical choice. **`ABICHECK_AST_
  > FRONTEND=hybrid` does NOT close it for the raw-source `--depth source`
  > reproduction -- it turns the discrepancy into native success vs.
  > typed/compare rejection, not agreement (Codex review, fresh evidence,
  > correcting this exact paragraph's own prior claim that hybrid closes
  > the gap "too, for the same coincidental reason").** Both
  > `resolve_dump_request()` and `resolve_compare_request()` call
  > `workflows.artifact.resolve.reject_hybrid_source_frontend()` before
  > embedding, which resolves the environment-selected frontend via the
  > *same* `effective_frontend()` this paragraph already relies on -- so
  > with `ABICHECK_AST_FRONTEND=hybrid` set, that guard sees
  > `effective_frontend(...) == "hybrid"` for a raw (non-pack) `sources`
  > tree under `depth="source"` and raises `ValidationError` outright,
  > before `_make_source_extractor("hybrid", ...)` is ever reached. The
  > native `dump` CLI carries the identical guard
  > (`_dump_will_attempt_hybrid_l4_extraction`,
  > `frontends/cli/commands/dump.py`) but only fires it when the CLI's own
  > *resolved* `header_backend` is literally `"hybrid"` -- which, per this
  > note's own earlier finding, only happens from an explicit
  > `--ast-frontend hybrid` flag or a `compile.frontend: hybrid` config,
  > never from the (env-var-blind) `"auto"` default. So with only
  > `ABICHECK_AST_FRONTEND=hybrid` set and no explicit flag/config, the
  > native `dump` CLI's guard never fires, `header_backend` stays `"auto"`,
  > and `_make_source_extractor("auto", ...)` proceeds to clang and
  > succeeds -- while the typed/compare path raises `ValidationError` for
  > the identical input. `ABICHECK_AST_FRONTEND=clang` is therefore the
  > *only* `ABICHECK_AST_FRONTEND` value that closes this gap by genuine
  > agreement (both sides succeed and resolve clang); `hybrid` closes
  > nothing and `castxml` leaves the original clang-vs-castxml disagreement
  > standing.
  >
  > The config half of the original qualification does hold, but only for
  > the **CLI-vs-CLI** pairing, not the CLI-vs-typed-API one: a project
  > `.abicheck.yml` pinning `compile.frontend` (to either `castxml` or
  > `clang`) is discovered by `resolve_dump_compile_context()` and reaches
  > `header_backend` as that explicit string, and `compare`'s own CLI path
  > discovers and honors the identical config value — so the native `dump`
  > and `compare` CLIs agree whenever a config pins the frontend, closing
  > the divergence for that pairing. It does **not**, by itself, close the
  > divergence against a *directly constructed* typed `DumpRequest`/
  > `execute_dump_request` call: `resolve_dump_request()` never discovers
  > `.abicheck.yml` at all — it derives its evidence solely from
  > `DumpRequest.input.compile` and the request-level `frontend` field the
  > caller supplied, so a config's `compile.frontend` has no effect on a
  > typed request unless the caller separately replicates it.
  >
  > **Qualification (Codex review, fresh evidence): this does not mean
  > every config pin leaves a bare typed request diverging equally in both
  > directions.** `resolve_dump_request()`
  > (`service_dump_pipeline.py`) resolves `header_backend` from the
  > request-level `DumpRequest.frontend` field *before* calling
  > `effective_frontend()` — `header_backend = frontend_lower if
  > frontend_lower in HEADER_AST_FRONTENDS else "auto"`, then
  > `effective_frontend(evidence.compile, header_backend)` — so a typed
  > caller can align with a `compile.frontend: clang` config by setting
  > only the request-level `DumpRequest(frontend="clang", ...)`, with no
  > need to touch `input.compile.frontend` at all: `effective_frontend`
  > honors an already-non-`"auto"` `header_backend` over its own castxml
  > default. A `compile.frontend: castxml` config needs no typed-side
  > action to align either, for a simpler reason — `effective_frontend`'s
  > bare default (an un-set `DumpRequest.frontend`, still `"auto"`) is
  > already castxml, so that direction was never divergent from a config
  > pin in the first place -- **when no `ABICHECK_AST_FRONTEND` override
  > is set (Codex review, fresh evidence, correcting this exact claim).**
  > `dumper._resolve_header_backend`'s own precedence honors an *explicit*
  > `castxml`/`clang`/`hybrid` verbatim, consulting `ABICHECK_AST_FRONTEND`
  > only for the `"auto"` case -- so the native CLI's config-forwarded
  > explicit `"castxml"` string is env-var-blind, but a bare typed
  > request's un-set `"auto"` default is not: with `ABICHECK_AST_
  > FRONTEND=clang` set, the CLI stays castxml (explicit, ignores the env
  > var) while the typed request resolves clang (auto, honors it) -- the
  > two diverge again, contrary to the "needs no typed-side action" claim
  > this paragraph made without that qualification. The claim above holds
  > precisely for a typed request that leaves *both* `DumpRequest.frontend`
  > and `input.compile.frontend` at their defaults (`"auto"`/unset) *and*
  > no `ABICHECK_AST_FRONTEND` override selects a frontend — that is the
  > shape that survives a config-pinned frontend unclosed, not every typed
  > request unconditionally.
  >
  > `compare`'s implicit-dump operand and `execute_dump_request` both reach
  > L4 replay through the *same* shared primitive,
  > `workflows.artifact.execute.embed_side_build_source`, whose
  > `source_extractor: str | None = None` parameter's own docstring already
  > states the contract: `None` keeps that function's own
  > `service_compare_evidence.effective_frontend` resolution -- the
  > identical native-`dump`-CLI-vs-typed/`compare` pairing, `ABICHECK_AST_
  > FRONTEND`-vs-config precedence, and per-value outcome (unset/`castxml`
  > disagree; `clang` converges on clang by coincidence; `hybrid` does
  > *not* converge -- it turns the divergence into native success vs.
  > typed/compare `ValidationError` rejection) the "Third qualification"
  > note above already establishes in full **(consolidated here rather
  > than restated a second time, per this file's own single-owner
  > documentation contract; Codex review, fresh evidence)** -- see that
  > note for the complete per-value matrix and reasoning.
  >
  > Confirmed directly, with the environment variable unset: a
  > `dump`-CLI-written baseline's
  > `build_source.source_abi.coverage.fact_set.producer` reads
  > `"abicheck-cc-clang-extractor"`; the identical input run through
  > `resolve_dump_request`/`execute_dump_request` reads `"castxml-source"`.
  > Every other field of the two runs' *flat* (L0-L2) snapshot payload was
  > confirmed byte-identical (modulo `created_at`/`dependency_scope`, both
  > already-documented, expected differences) — this is specifically an L4
  > extractor-selection divergence, not a wider parity gap.
  >
  > This is a **third** instance of the "L4 extractor default" divergence
  > this plan already tracks for `scan` (item 2, two notes above) — but on
  > the opposite side of the pairing this whole area cares about most:
  > `scan`'s own candidate resolution *also* defaults to clang (documented),
  > so a `scan --against` a `dump`-CLI-written baseline happens to have both
  > sides agree by accident (verified: `scan --against` a plain `dump`
  > baseline for the fixture above reports `NO_CHANGE`, not
  > `NOT_COMPARABLE`) — but `dump`'s own CLI baseline compared against
  > `compare`'s implicit-dump resolution of the identical live binary pairs
  > a clang-derived old side against a castxml-derived new side. **This is
  > not reproducible through the native `compare` CLI's own `--old/new-
  > sources`/`--build-info` flags at all (Codex review, fresh evidence,
  > correcting this exact paragraph's own prior `compare oldbaseline.json
  > new.so --sources new=<tree>` reproduction command) -- a raw, non-pack
  > `sources`/`build_info` value on either side makes `_needs_inline_embed()`
  > true, which routes through `_embed_inline_source_sides()` /
  > `_embed_inline_source_side()`'s own nested `ctx.invoke(dump_cmd, ...)` --
  > the exact same native `dump` CLI path the baseline itself took, unflagged
  > `"auto"` and all, so a `--sources new=<tree>` new side resolves clang,
  > not castxml, pairing clang with clang instead of demonstrating the
  > divergence. **`sources` and `build_info` are not equivalent here, though
  > (Codex review, fresh evidence): a `--build-info`-only new side (no
  > `--sources`) still enters this same nested `dump_cmd` invocation, but
  > `_run_inline_source_abi(sources, ...)` (`buildsource/inline.py`) returns
  > `(None, [])` immediately when `sources is None` -- before
  > `_make_source_extractor()` is ever called -- since a raw `--build-info`
  > supplies only L3 compile-unit evidence, never an L4 source tree to
  > extract from. So `--build-info`-only resolves no extractor at all
  > (clang or castxml), and only a raw `--sources` value actually reaches
  > and exercises this resolution.** "`compare`'s implicit-dump operand" instead names the
  > *typed* pipeline `resolve_compare_request`/`resolve_side_snapshot`
  > share with `execute_dump_request` (`header_conditionals.py`'s own
  > module-header comment) -- reached only when `compare` resolves a side
  > with no CLI-level raw `--sources`/`--build-info` flag at all (a direct
  > typed `CompareRequest` call). **A project-level `.abicheck.yml`'s
  > `sources:`/`build:` blocks are NOT an alternative route to this here
  > (Codex review, fresh evidence, correcting this exact paragraph's own
  > prior claim to the contrary): `_resolve_side_snapshot_impl()`
  > (`workflows/artifact/execute.py`) only embeds L3-L5 evidence when `if
  > side.sources or side.build_info:` -- a per-side `InputSpec` field the
  > typed pipeline never populates from a discovered config file (the typed
  > pipeline doesn't discover `.abicheck.yml` at all, established earlier in
  > this same note); those config keys are collection settings the *CLI*
  > resolvers consult, not an `InputSpec` source.** A bare `compare
  > oldbaseline.json new.so` with no
  > `sources`/`build_info` input anywhere (CLI flag or a typed `InputSpec`
  > field) has nothing
  > for `embed_build_source` to call `collect_inline_pack()` against, so it
  > produces no new-side L4 evidence to diverge on at all -- the actual
  > reproduction needs a typed `CompareRequest(old=..., new=InputSpec(path=
  > new_so, sources=<the same tree the baseline was dumped from>, ...),
  > depth="source")` call -- `depth` lives on `CompareRequest` itself, not
  > `InputSpec` (Codex review, fresh evidence, correcting this exact
  > paragraph's own prior misplacement, which would raise `TypeError:
  > InputSpec.__init__() got an unexpected keyword argument 'depth'` before
  > ever reaching the resolver) -- with a real, raw (non-pack) `sources` value
  > on the `InputSpec` itself, so `resolve_side_snapshot` reaches
  > `effective_frontend`
  > directly, pairing a clang-derived old side against a castxml-derived new
  > side as originally intended -- and the `profile_fingerprint`/
  > `scope_fingerprint`
  > comparability gate does not consult the extractor choice, so this does
  > **not** surface as `NOT_COMPARABLE`. **Correction (Codex review, fresh
  > evidence): the extractor choice is not *unrecorded* -- it already reads
  > straight off `build_source.source_abi.coverage.fact_set.producer`, as
  > this same paragraph's own confirmation above demonstrates
  > (`"abicheck-cc-clang-extractor"` vs. `"castxml-source"`).** The actual
  > gap is narrower than "nowhere a consumer can see": that already-recorded
  > identity is simply never folded into the comparability fingerprints, so
  > the existing `NOT_COMPARABLE` gate -- which exists precisely to catch a
  > disagreement between two sides' extraction facts -- has no way to act on
  > a signal it already has sitting right next to it.
  >
  > **Second correction (Codex review, fresh evidence): folding `producer`
  > into `profile_fingerprint`/`scope_fingerprint` is NOT the right fix
  > direction, and this note should not have named that gate as "where the
  > fix belongs."** `buildsource.source_diff.diff_source_abi()` already
  > passes both sides' `fact_set`s through `fact_set.check_fact_
  > compatibility()`, which explicitly handles a `producer`/`producer_
  > version`/`compiler_version` mismatch as one of its own named
  > invalidating conditions (see that function's own docstring) -- it
  > selectively suppresses only the specific evidence categories a producer
  > mismatch actually invalidates (structured-content, opaque-hash,
  > source-edge comparisons) while retaining compiler-neutral structured
  > facts, and emits `SOURCE_FACT_COVERAGE_INCOMPLETE` to record why. This
  > is a deliberate, already-built, category-specific degradation
  > mechanism -- folding `producer` into the coarse, all-or-nothing
  > `profile_fingerprint`/`scope_fingerprint` gate would hard-fail the
  > *entire* comparison as `NOT_COMPARABLE` the moment two sides picked
  > different (but individually valid) default extractors, discarding every
  > compiler-neutral fact `check_fact_compatibility` would have correctly
  > kept comparable. Whatever the real fix for this divergence turns out to
  > be (see the "Deliberately not fixed here" note below), it needs to
  > reconcile with this existing mechanism first, not bypass it with a
  > blunter one. It silently produces
  > two different sets of L4-derived facts for the
  > "same" comparison, real only when the two extractors actually disagree
  > about a declaration (item 3's own castxml phantom-implicit-member bug
  > is exactly such a disagreement: clang does not have it, so a clang-L4
  > `dump` baseline compared against a castxml-L4 live resolution of a class
  > with implicit special members is a live vector for a spurious
  > `source_binary_provenance_mismatch`-shaped finding, though this was not
  > separately reproduced with a repro that isolates it from item 3's own
  > repro).
  >
  > **Deliberately not fixed here, in either direction, and here is why
  > both "obvious" fixes are each their own real behavior change:** (a)
  > making `dump`'s CLI resolve `header_backend` through
  > `effective_frontend` before forwarding it as `extractor=` would align it
  > with `compare`/the typed pipeline — but at the time this was written it
  > would also have meant every plain `dump --depth source` newly
  > inheriting item 3's then-still-open castxml phantom-implicit-member
  > bug, which `dump`'s current (accidental) clang default did not have.
  > **Correction (Codex review, fresh evidence): item 3's fix had NOT
  > landed on this tree at the time this note was written, and an earlier
  > revision overstated it as already fixed.** Item 3's own fix
  > (`Function.is_compiler_generated`, `entity_from_function` gating
  > `api_relevant` on it, the ctor/dtor owner-index rescue and its several
  > follow-on findings) is real and verified, and has since landed in this
  > same tree (see this plan's own item-3 status note below, "The castxml
  > L4 extractor bug is fixed") — so the specific regression risk this
  > paragraph names (a naive migration of `dump`'s CLI onto
  > `effective_frontend`/`execute_dump_request` newly inheriting the castxml
  > phantom-implicit-member bug on every plain `dump --depth source`) is
  > now closed. The CLI-vs-typed-pipeline extractor divergence documented
  > in this note is independently still open regardless of item 3's landing
  > status, and still needs its own dedicated, verified pass rather than a
  > byproduct of this one. (b)
  > making `compare`/the typed pipeline default to clang to match `dump`'s
  > CLI would contradict `dumper._resolve_header_backend`'s established,
  > intentional castxml-first default for **L2** header-AST parsing, which
  > the typed dump/compare pipeline deliberately couples its own L4 choice
  > to via `effective_frontend` (this plan's item 2 elsewhere). **Correction
  > (Codex review, fresh evidence): this is not a repository-wide "castxml
  > is the canonical L2/L4 default" architecture, and the original wording
  > overstated it.** A separate, dedicated L4-only resolver exists
  > (`buildsource.source_extractors.resolver.resolve_source_extractor`,
  > `AUTO_PREFERENCE = (CLANG, CASTXML)` — clang preferred first, on
  > capability grounds, "most capable first") with the *opposite* default
  > direction. **Second correction (Codex review, fresh evidence): this
  > resolver does have a real production caller, contrary to the "no
  > production caller anywhere" claim this note originally made.**
  > `select_source_backend()` calls `resolve_source_extractor()`, and
  > `cc_wrapper.emit_facts_for_command()` — reached from the published
  > `abicheck-cc` console entry (`pyproject.toml`'s `[project.scripts]`,
  > ADR-035 D5/G19.4's Flow-2 compiler wrapper) — calls
  > `select_source_backend()`. So the capability-ordered, clang-first
  > `AUTO_PREFERENCE` policy is already user-facing, through a different
  > front end (`abicheck-cc`'s per-TU fact capture at compile time) than
  > the `dump`/`compare`/`scan` pipeline this note is about. This still
  > does not contradict `dump`'s CLI default: the two resolvers serve
  > genuinely different callers (a compiler-wrapper's own per-compile
  > extractor choice, vs. this pipeline's post-hoc choice of which
  > extractor a whole comparison routes through), so `AUTO_PREFERENCE`
  > being real and shipped doesn't itself argue for or against either of
  > the two "obvious fixes" this note already rejects — only the "no
  > production caller" framing was wrong, not the conclusion drawn from
  > it. What actually constrains direction (b) is narrower and real: the *typed*
  > `dump`/`compare` pipeline's specific, deliberate choice to couple L4 to
  > L2 through one shared resolution (`effective_frontend`), for reasons
  > that have nothing to do with this one CLI code path — not a
  > codebase-wide L4 convention. Either direction is a real, user-facing
  > behavior change needing its own dedicated, verified pass — not a
  > byproduct of characterizing item 2's migration risk. What this finding
  > *does* change is item 2's own risk calculus: a naive migration of
  > `dump`'s CLI onto `execute_dump_request` would, as a side effect,
  > silently flip this default from clang to castxml -- so "byte-identical
  > to today's output" is not actually the right acceptance bar for L4
  > facts specifically; today's output is itself one side of a pre-existing,
  > real disagreement, not a stable target to match. (Item 3's fix was not
  > merged on this tree at the time this note was originally written, but
  > has since landed in this same tree -- see this plan's own item-3 status
  > note below, "The castxml L4 extractor bug is fixed" -- closing this
  > specific regression risk. It is still a real, user-facing default
  > change that needs to be a deliberate decision, not a side effect
  > discovered mid-migration -- Codex/CodeRabbit review: an earlier
  > revision of this wording said "now that item 3 is fixed", contradicting
  > this same section's own earlier correction at the time it was written.)
  > A correct migration plan needs
  > to decide this divergence's resolution *before* attempting
  > byte-identical verification, not discover it via a failing diff
  > mid-migration.
  > **The castxml L4 extractor bug is fixed (2026-08-28).** A new field,
  > `Function.is_compiler_generated` (schema v27), records castxml's own
  > `artificial="1"` XML attribute — read for ANY function-like element,
  > not just `Constructor`/`Destructor` where it was already read for
  > `_ctor_or_dtor_visibility` — closing the exact gap this entry
  > identified as the only reliable, general signal (the two pre-existing
  > synthetic-mangled-name markers, `is_synthetic_ctor_key`/
  > `is_synthetic_dtor_key`, could not catch a synthesized `operator=`,
  > which castxml gives a real-looking Itanium mangled name). The
  > direct-clang L2 backend stamps `is_compiler_generated=False`
  > unconditionally, not per-node: its own `_walk` already skips
  > `_categorize` entirely whenever a node is `isImplicit`, so a node
  > reaching `parse_functions()`'s output is structurally guaranteed to
  > have been written by the user — confirmed by reading `dumper_clang.py`
  > directly, not assumed. Every existing consumer of `Function.visibility`
  > was checked and needed no change — this fix is additive (a new field)
  > rather than a change to `visibility`'s own HIDDEN/PUBLIC split, so
  > nothing that already reads `visibility` observes different behavior.
  >
  > **Correction (Codex review, same day): the first cut of this fix
  > unconditionally excluded every confirmed compiler-generated declaration
  > from `api_relevant` — wrong, because an ODR-used implicit special member
  > CAN have a real exported symbol** (e.g. a public function returning a
  > type by value calls its implicit copy/move constructor, which the
  > compiler still emits as a real weak export). `entity_from_function` is a
  > per-declaration, export-table-blind mapping stage — it has no way to
  > know whether a given implicit member is ODR-used, so excluding
  > unconditionally there would silently drop that genuine symbol's only
  > source declaration. Fixed by moving the decision downstream to
  > `link_source_abi`'s `_route_declaration`, which already has the
  > exported-symbol table: `entity_from_function` now stays additive (stamps
  > `SourceEntity.ownership["compiler_generated"] = "true"`, `api_relevant`
  > unchanged), and `_route_declaration` gives such an entity one export-
  > match attempt before recording it at all — matched, it is linked like
  > any ordinary declaration; unmatched, it is dropped outright (not counted
  > reachable-but-unmatched), closing the identical false-positive
  > `source_binary_provenance_mismatch` this entry's own repro produced,
  > without losing a genuinely-exported implicit member in the process.
  >
  > **Two further findings on this same correction, both real, both fixed
  > (Codex review, same PR).** (1) The export-match attempt only ever
  > compares `entity.mangled_name` directly against the export set —
  > correct for `operator=` (castxml always gives it a real Itanium mangled
  > name) but not for a constructor/destructor, whose real mangled name
  > castxml frequently omits, leaving a *synthetic* internal key
  > (`dumper_castxml.SYNTHETIC_CTOR_KEY_PREFIX`-prefixed, or `~`-prefixed
  > for a destructor) that can never equal a real export by direct
  > comparison — so an ODR-used implicit constructor/destructor with a real
  > weak export (`_ZN...C1.../_ZN...C2...`) was still silently dropped, the
  > identical class of loss the correction above was written to close, just
  > for a different declaration kind. Fixed with a new, narrowly-scoped
  > module, `buildsource/ctor_export_match.py`: `itanium_scope_components`
  > already parses a ctor/dtor mangled symbol's owning scope correctly
  > (confirmed empirically: `_ZN6WidgetC1ERKS_` → `["Widget", "{ctor}"]`),
  > so a class-level index (owner scope → "ctor"/"dtor"/"both") built once
  > per link gives a synthetic key one rescue check: does its owning class
  > have *any* matching ctor/dtor export at all. Deliberately conservative
  > in one direction, documented rather than attempted: a templated owner
  > (castxml's own spelling embeds `"<...>"`) is never matched, since
  > `itanium_scope_components`'s mangled-argument spelling (`"BoxIiE"` for
  > `Box<int>`) does not textually agree with castxml's spelled form, and
  > this codebase's own history (this file's "linkage-blind-removal"/type-
  > identity entries) shows that reconciling two independently-spelled
  > identities via a partial match is exactly the class of bug that has
  > taken multiple review rounds to find and revert elsewhere — a templated
  > class's synthetic-keyed ctor/dtor still falls back to the original
  > "no export visibility, drop it" behavior. (2) `_route_declaration`
  > dropped a `compiler_generated` candidate outright whenever `exported`
  > was empty — correct for "checked a real export table, found nothing",
  > wrong for "the export table isn't known yet", which is exactly the
  > Flow-2/parallel-baseline `merge` flow's own documented shape
  > (`relink_surface_exports`'s own docstring: "the parallel-baseline
  > `merge` flow links the source surface with no binary present"). That
  > flow's later `relink_surface_exports()` pass only re-matches entities
  > already in `reachable_declarations` — it never adds one back that the
  > first link already dropped — so every compiler-generated candidate in
  > that flow was permanently lost before the real export table was ever
  > consulted. Fixed by never dropping when `exported` is itself empty
  > (unresolved, not confirmed absent).
  >
  > A third, independent finding surfaced while testing the ctor/dtor
  > rescue against a real fuzzed/adversarial export string (an existing
  > regression test, `test_ctor_dtor_fold_tolerates_malformed_huge_length_
  > fields`, which this new code path made reachable for the first time):
  > `diff_cxx_rules._read_length_prefixed_name` computed a mangled symbol's
  > declared length via a bare `int(s[i:j])`, which raises on a fuzzed
  > symbol with thousands of digits (Python's integer-conversion digit
  > limit) — a pre-existing latent bug in a shared, widely-used parser,
  > newly reachable because this is the first caller to feed it strings
  > read from a binary's own untrusted export table. `buildsource/
  > source_link.py`'s own, unrelated ctor/dtor folder already carries the
  > identical guard (`_consume_source_name`, digit-by-digit accumulation
  > capped at the input length) for the same reason; `_read_length_
  > prefixed_name` now does too.
  >
  > Verified against real castxml 0.7.0 and real clang 20, not only against
  > hand-built fixtures: `tests/test_castxml_l4_phantom_members.py::
  > test_castxml_l4_extract_excludes_implicit_special_members_from_reachable_surface`
  > reproduces this entry's own exact repro end to end through the real
  > `CastxmlSourceExtractor.extract` → `link_source_abi` pipeline (not a
  > synthetic fixture) — confirmed to fail against the pre-fix code with
  > the identical `7` exportable-declarations / `1` matched shape this
  > entry's own investigation found, and to pass after it with a clean
  > `1/1`. Further coverage: `tests/test_castxml_compiler_generated.py`
  > (the castxml parser level, hand-built XML mirroring real castxml
  > output element-for-element), `tests/
  > test_dumper_clang_compiler_generated.py` (the direct-clang parser
  > level, plus a direct pin that an `isImplicit` node never reaches
  > `parse_functions()`'s output at all), and `tests/
  > test_serialization_function_compiler_generated.py` (schema-v27
  > round-trip, including a pre-v27 snapshot dict loading the field as
  > `None`).
  >
  > **Deliberately not done in this same pass, per this entry's own
  > established caution and the plan's item 2 section above**: flipping
  > `scan_engine.py`'s `source_extractor="auto"` to follow
  > `effective_frontend` (closing the scan-vs-dump/compare L4 extractor
  > default divergence item 2 documents) — that remains a separate,
  > deliberately deferred decision needing its own dedicated verification
  > in production usage, not a byproduct of fixing the bug that blocked it.
  > This fix removes that decision's main objection (castxml's L4 surface
  > was unsafe to trust), but does not itself change any default.

  > **Slice landed: the real ELF run is migrated.** With item 3's fix
  > landed (above) and the reordering prerequisite's Flow-2 half closed
  > (above, "Update (2026-08-28): the Flow-2 half is closed"), the two
  > remaining named blockers to "the migration itself" — a policy-compliant
  > castxml, and byte-identical-output verification — are both cleared:
  > `dump_cmd`'s real ELF branch (`frontends/cli/commands/dump.py`) now
  > calls `execute_dump_request` directly, retiring `perform_elf_dump` from
  > this call site (the function itself is unchanged and still defined, in
  > case another caller depends on it — only `dump_cmd` no longer imports
  > it). Built on exactly the pieces this section already closed, not a
  > fresh redesign: the same `DumpRequest` `--dry-run` already resolves
  > (`_dump_request`), re-pointed at the post-linker-script-following
  > `so_path` (`resolve_dump_request`'s own `detect_binary_format` call runs
  > before any such following, so feeding it the pre-follow path risked a
  > wrong `fmt` for a symlink-to-linker-script input — a fresh
  > `ResolvedDumpRequest` is built for execution alone, leaving `--dry-run`'s
  > own, already-tested resolution against the pre-follow path unchanged);
  > `requested_depth` nulled out on that execution-only copy, so
  > `execute_dump_request`'s own `enforce_requested_depth` — a differently
  > worded `ValidationError` than `check_requested_depth_satisfied`'s
  > `DumpDepthNotSatisfiedError` for the identical condition — never fires,
  > keeping `_write_snapshot_output`'s own call (unchanged) the sole
  > enforcement point and its pinned message intact
  > (`tests/test_depth_vocabulary.py`); and the legacy `-p`/`--compile-db`
  > auto-match threaded through via `legacy_compile_db_tokens`/
  > `legacy_compile_db_matched`, the explicit pass-through ADR-063 Phase 1
  > already built into `execute_dump_request` for exactly this purpose
  > (its own docstring's precedence rule), rather than a new `InputSpec`
  > field — the pass-through already reproduces `perform_elf_dump`'s own
  > `_fold_explicit_gcc_options` unfold-when-the-fold-applies logic, so a
  > second, dataclass-shaped copy of the same fact would be a second place
  > for it to drift, not a cleaner one. `_write_snapshot_output`'s own
  > write-time embed fallback (`build_source_already_satisfies`), depth
  > gate, Flow-2 `--inputs` fold, and dependency-scope resolution are all
  > unchanged, called exactly as before with the now-execution-produced
  > snapshot — the embed itself is the only step this migration actually
  > moves to resolution time, not the whole write sequence PR 3A's earlier
  > "reordering" framing worried about; see the root `AGENTS.md`'s own PR C
  > entry (search "The real ELF `dump` run is migrated") for the precise
  > account of why that split is safe, including the one structural nuance
  > that is measured rather than proven in general (the shared pipeline's
  > own pre-existing double-dependency-scoping, confirmed idempotent for
  > every shape this migration's own parity suite exercises).
  >
  > One real, user-visible default change falls out of the migration:
  > `dump`'s L4 source-extractor default flips from an accidental **clang**
  > (`perform_elf_dump` forwarded the bare, unresolved `header_backend`
  > straight to the write-time embed, which treats anything but the literal
  > string `"castxml"` as clang) to **castxml** (the shared pipeline's
  > `effective_frontend`, matching `compare`'s implicit-dump operand, the
  > typed `DumpRequest` API, and `dump`'s own L2 header-AST default) — safe
  > now specifically because item 3's castxml L4 phantom-implicit-member fix
  > landed first; `--ast-frontend clang` recovers the previous default.
  > **`scan`'s own item 2 divergence (`scan_engine._build_new_snapshot`'s
  > `source_extractor="auto"` override) is unchanged by this** — it stays
  > its own separate, deliberately deferred decision, exactly as the
  > paragraph immediately above already states.
  >
  > Verified with real `g++`/clang/castxml (this session's environment has a
  > policy-compliant castxml on `PATH`, unlike the sessions that wrote most
  > of this section's history): the full fast unit suite; the
  > `integration`-marked suite for this area end to end —
  > `test_dump_cli_typed_api_parity.py` (16 cases; `_CONTRACT_KNOWN_
  > DIVERGENT_FIELDS` stays empty, i.e. the migrated CLI path and the typed
  > pipeline now agree on the extraction contract with zero remaining
  > divergence, for every parametrized real-build shape this module
  > exercises — the byte-identical-output bar this section's own history
  > named as the last open item), `test_dump_scan_l3_comparability.py` (2
  > xfails, the same pre-existing `_SCAN_KNOWN_DIVERGENT_FRONTENDS`
  > signature item 2 already documents, unchanged), `test_dump_write_after_
  > resolve_time_embed.py`, `test_dump_embed_idempotence.py` (updated to
  > count both the write-time embed call site and the new resolve-time one,
  > since a single-site count can no longer distinguish "moved" from
  > "doubled"), `test_compile_db_filter_scope.py`, `test_dry_run_contract.py`,
  > `test_dry_run_build_query_contract.py`, `test_l2_seed_flow2_packs.py`,
  > `test_scan_adr039_build_context.py`, `test_castxml_l4_phantom_members.py`,
  > `test_dump_depth_provenance.py`, `test_depth_vocabulary.py` — and
  > `test_dump_request_from_cli.py`'s own `TestExecutionConsumesTheResolvedPlan`
  > (updated to spy on `execute_dump_request` instead of the now-retired
  > `perform_elf_dump` call site). `mypy`/`ruff` clean on every touched
  > module.
  >
  > **What this does not close.** `handle_non_elf_dump` (PE/Mach-O) is
  > untouched — no PE/Mach-O toolchain was available in this environment to
  > verify a migration against, and this section's own established
  > discipline is to measure, not reason, before moving a real-run call
  > site. PR 3C's removal itself stays blocked on that half per its own
  > "all three resolvers" ordering rule below, unchanged by this slice. Item
  > 2 (the `scan`-vs-`dump`/`compare` L4 extractor default divergence) is
  > unchanged, deliberately.

  > **Update: two real regressions in the migration above, found by review
  > and fixed before this PR merged.** Both were plain dropped kwargs —
  > `execute_dump_request`/`_resolve_side_snapshot_impl` already had the
  > parameters `scan`'s own candidate resolution needs for the identical
  > purpose, but the migrated ELF call site never passed either:
  > (1) `seed_collect_mode` was never forwarded, so the L2 include/compile
  > seed silently ran with the Tier-2 API's own "never execute a build
  > system as a side effect" pin (`collect_mode="off"`) instead of the CLI's
  > actually-resolved collect mode — unlike `perform_elf_dump`, which always
  > forwarded its own resolved `collect_mode`, a `--sources` tree with no
  > compile database lost its zero-config inferred build query entirely.
  > (2) `source_frontend_from_folded_context` was never passed, so L4 source
  > replay kept selecting its compiler from the pre-fold context instead of
  > the L3 build-context fold's own match — unlike `perform_elf_dump`, which
  > always reassigned `gcc_path`/`gcc_prefix` from the folded context once
  > it applied, a project whose real compiler (clang-cl, a prefixed
  > cross-compiler, one named only in a compile database) is discovered
  > only through that fold could replay source with the wrong driver.
  > Fixed by threading both through `execute_dump_request`'s own new
  > `seed_collect_mode`/`source_frontend_from_folded_context` parameters
  > (mirroring the existing pass-through pattern for `legacy_compile_db_*`)
  > down to `_resolve_side_snapshot_impl`, and having `dump_execute.
  > execute_dump_cli_run` pass `seed_collect_mode=resolved.collect_mode`/
  > `source_frontend_from_folded_context=True` unconditionally — exactly
  > the values `scan`'s own candidate resolution already passes, for the
  > identical reasons. `tests/test_dump_request_from_cli.py`'s existing
  > `TestExecutionConsumesTheResolvedPlan` spy now also asserts both kwargs
  > reach `execute_dump_request` (verified to fail against the pre-fix code
  > with a `KeyError`, confirming the test catches the exact regression).

  > **A third regression, same review round.** `execute_dump_cli_run`'s call
  > forwarded `dump_cmd`'s own `allow_build_query` local -- the deprecated,
  > always-`False` `--allow-build-query` no-op flag (`cli_options.py`: "Kept
  > as a no-op for backward compatibility") -- straight into
  > `_gated_build_query_inputs`, a Tier-2 gate written for a programmatic API
  > caller who must opt in. That silently nulled an explicit `--config`/
  > `--build-query` for the execution step alone, contradicting both flags'
  > own documented CLI contract (`--build-query`: "runs automatically as
  > trusted operator input"; `--config`: "build.query runs only from an
  > explicit --config") and regressing `perform_elf_dump`, which forwarded
  > both unchanged with no such gate. `dump`'s CLI is itself the trust
  > boundary an explicit `--config`/`--build-query` already crossed by being
  > typed there at all -- unlike `scan`'s config-file-sourced `build.query`,
  > which needs its own `resolve_effective_allow_query` "level-implies-query"
  > decision (ADR-037 D4) precisely because it is not operator-typed. Fixed
  > by passing `allow_build_query=True` unconditionally at this one call
  > site instead of the CLI local. The same spy test now also asserts
  > `seen["allow_build_query"] is True` (verified to fail against the
  > pre-fix code the same way).

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

> **Prerequisite status (2026-08-19).** 1 and 2 were already implemented —
> predating this plan (ADR-032 D5): `cli_buildsource.py`'s
> `cfg_trusted_for_query = build_config is not None or build_query is not
> None` is the exact gate, and `buildsource.inline._resolve_compile_db`
> already skips an auto-discovered `.abicheck.yml`'s `build.query` with a
> recorded diagnostic rather than running it. 4 was already covered too —
> `tests/test_dry_run_contract.py::
> test_depth_source_with_prebuilt_pack_build_info_does_not_block` pins the
> "gate on achieved depth, not compile-DB presence" behavior this item
> describes. 5 was already covered by existing docs (`docs/reference/
> config-file.md`, `docs/start/real-world-example.md`,
> `docs/learn/build-source-data.md`). **3 was the real gap** — `dump
> --dry-run` had no visibility into the trust decision at all
> (`render_dump_dry_run` never received `build_query`/`build_compile_db`).
> Closed additively: a new leaf module,
> `cli_dump_dry_run_build_query.add_build_query_dry_run_section()` (split
> out rather than added inline, since `cli_dump_helpers.py` sits at its
> 2000-line AI-readiness hard cap), mirrors — read-only, never executing —
> the exact trust decision and `argv`/`cwd` construction
> `cli_buildsource.embed_build_source`/`buildsource.inline._run_build_query`
> make at real-run time, and reports the resulting compile-DB path when
> `build.compile_db` is configured. Wired into `dump_cmd`'s existing
> `--dry-run` branch in `cli.py`, after `render_dump_dry_run` builds the rest
> of the report. `dry_run.py`'s shared `SECTION_ORDER` gained one new title
> ("Build query (trust)") so the section renders in a stable position for
> every command using `DryRunResult` (only `dump` populates it today; an
> unpopulated section renders nothing). Tests:
> `tests/test_dry_run_contract.py::TestDumpDryRunBuildQueryTrust` (untrusted
> auto-discovered config, trusted `--config`, trusted CLI `--build-query`
> overriding config, no query configured, determinism, and 26 review-caught
> reachability/precedence/pack-normalization/exit-code-class/config-discovery
> edge cases besides — each asserting the query is never actually executed).
> **Prerequisites 1, 2, 4, and 5 are fully satisfied; 3 is now satisfied for
> every input shape except one.** `cli_dump_dry_run_build_query.py`
> originally documented two deliberately unclosed cases where the report
> could still claim "will run" when the real input would not: a Flow-2
> `abicheck_inputs/` pack as the sole `--sources`/`--build-info` input, and a
> `-H` directory containing no supported header.
>
> **The Flow-2 pack gap is closed (2026-08-20), and it turned out to be two
> gaps, not one.** Investigating this module's own docstring ("Flow-2 packs
> fold into `raw_build_info`/`raw_sources` the identical way a
> `BuildSourcePack` does in `embed_build_source`") to add the missing
> recognizer here surfaced a real, independent gap in the L2-seed
> **production** path itself: `buildsource.l2_seed._l2_seed_pack_inputs` (the
> pack-precedence resolver `seed_includes_and_fold_compile_context` uses)
> only ever recognized a classic `BuildSourcePack` (`is_pack_dir`), never a
> Flow-2 pack — so a Flow-2 pack given alongside `-H` headers was silently
> treated by the *real* L2 seed as a literal, un-normalized source tree: its
> own compile-unit include dirs never reached L2 seeding, and a trusted,
> explicit `build.query`/`--config` could genuinely be re-executed against
> the pack directory itself. This was the load-bearing finding: mirroring
> `embed_build_source`'s recognition in the dry-run preview *alone*, without
> also fixing `_l2_seed_pack_inputs`, would have made the preview *wrong* for
> every L2-seed-reachable branch (reporting "will NOT run" for an input the
> unfixed L2 seed's own resolution would still have genuinely reached
> `cfg.query` through) — a worse regression than the pre-existing gap. Fixed
> both, root cause first: `_l2_seed_pack_inputs` now also recognizes a Flow-2
> pack (folding its `BuildEvidence` via the lighter
> `load_inputs_manifest`/`_load_build_evidence` pair, not the full
> `ingest_inputs_pack`, which this L3-only seed doesn't need), and only then
> did `cli_dump_dry_run_build_query.py` gain the identical, now-correct
> recognition (`_is_pack_dir_any`/`_pack_dir_build_evidence`) across all
> seven of its own pack-precedence checks. See `abicheck/buildsource/
> l2_seed.py`'s own docstrings and `changelog.d/
> 1787253700_claude_l2_seed_flow2_pack_recognition.md` for the full account.
> Tests: `tests/test_l2_seed_flow2_packs.py` (the production resolver,
> including an end-to-end `derive_l2_include_dirs` case) and `tests/
> test_dry_run_build_query_flow2_packs.py` (the dry-run preview, mirroring
> the existing classic-pack CLI tests one-for-one).
>
> **The `-H` directory gap is closed (2026-08-27).** The "design decision"
> this note originally called for turned out not to be one: `_expand_header_
> inputs` (`cli_resolve.py`) — the exact function the real run already calls
> downstream to expand a `-H` directory into its header files, raising
> `click.ClickException` for a missing path, an empty header directory, or a
> path that is neither file nor directory — is itself already a pure
> directory walk (`iter_directory_headers`'s `rglob`) with no compiler/build
> invocation, squarely inside `--dry-run`'s existing "no I/O beyond
> stat()/PATH lookups" contract; a `rglob` over an already-resolved local
> directory is filesystem-stat-family I/O, not the kind of "real work" that
> contract exists to keep out. `dump_cmd` (`frontends/cli/commands/dump.py`)
> now calls it once, unconditionally, immediately after `headers` gets its
> final post-`resolve_dump_collect_context` value — before either the
> `--dry-run` or the real-run branch — mirroring the exact pattern this same
> file already uses for its other two unconditional pre-branch usage-error
> checks (the hybrid+depth rejection, the binary-depth-with-no-SO_PATH
> rejection). The result is discarded; the call exists purely for its
> validation side effect, and the real run's own downstream expansion call
> is untouched (a second, cheap, idempotent directory walk, not worth
> threading a resolved value through every intermediate call site to avoid).
> Verified end to end: `dump --dry-run -H <empty-dir>` and `dump -H
> <empty-dir>` (no `--dry-run`) now produce the byte-identical error message
> and exit code (1), where the dry-run path previously exited 0. Tests:
> `tests/test_dry_run_contract.py::TestDumpDryRun::
> test_dry_run_rejects_empty_header_directory_like_the_real_run` (both
> invocations, one assertion each) and `::
> test_dry_run_accepts_a_header_directory_with_real_headers` (the positive
> control, confirming a real header directory dry-runs cleanly as before).
>
> This closes PR 3C prerequisite 3 in full. **PR 3C's removal itself
> remains blocked**, unchanged by this — the ordering blocker is PR 3A's
> full convergence (both `dump` and `scan` resolvers), which is a separate,
> larger item covered in that section's own status notes above and not
> touched by this fix.

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

> **Status (2026-08-31): the ADR landed; stage 1a is complete and stage 1b
> partially so.**
> [ADR-064](../adr/064-canonical-gate-algorithm-and-exit-decision.md) is the
> settled design this section's own analysis called for — precedence order,
> the mode-dependent removed-required-library rank, the "numbers stay
> per-command" constraint, the `GateOptions` reassignment, and a two-stage
> landing plan whose first stage is itself split into 1a (pure resolvers)
> and 1b (wiring). Stage 1a landed complete:
> `abicheck/policy/exit_decision_precedence.py` gained
> `resolve_scan_exit_decision` (evidence-contract-error / budget-overflow /
> not-comparable, with the exact "budget discards an already-decided
> not-comparable result" ordering) and `resolve_release_exit_decision`
> (the release fan-out's mode-dependent removed-required-library rank,
> including the real severity-mode asymmetry where removed-library wins
> outright even over a coverage contribution, and an independent
> operational-error axis that can tie with a real compatibility-gate
> finding from a different library rather than replacing its reason) —
> both pure functions, verified against `scan_engine.py`/
> `cli_compare_release_helpers.py` and unit-tested (`tests/
> test_exit_decision.py`'s `TestResolveScanExitDecision`/
> `TestResolveReleaseExitDecision`).
>
> Stage 1b landed partially: `ExitDecision.to_dict` now serializes all five
> ADR-064 fields (report schema 2.47/1.22); `scan`'s `NOT_COMPARABLE`
> outcome persists a real `diff.exit` block, since that outcome already
> builds and emits a report today; and the release fan-out's JSON summary
> gains an unconditional `exit` block
> (`resolve_release_exit_decision_for_report`, a new, report-only resolver
> in `exit_decision_precedence.py`) reproducing `_exit_compare_release`'s
> own precedence, including the legacy-scheme "worst verdict among
> non-`ERROR`/non-`not_comparable` libraries" aggregation this section used
> to flag as missing — landed as a separate resolver rather than a rewrite
> of `_exit_compare_release` itself (that function's exact signature/output
> is pinned by `tests/test_exit_code_integrity.py`, which CI gates depend
> on), proven to always agree with it numerically
> (`TestReleaseExitDecisionForReportAgreesWithRealExit`) rather than merely
> assumed to. **Still open:** persisting a decision for `scan`'s
> `_BudgetOverflow`/`_EvidenceContractError` abort points, which raise
> *before* any report is ever built today (a real design decision this
> slice deliberately did not make), the release fan-out's `GateOptions`
> typed-object rewrite, a full cross-front-end parity pass (typed API,
> Action), and stage 2 (the atomic removal — deleting `--exit-code-scheme`
> and updating CLI/API/Action/`aggregate` parity together).

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
accepted by a single-pair `compare`; the release fan-out and `scan` used to
reject or only partially honour parts of it (see the slices below for what
closed). A pack can assign `gate.exit_code_scheme` and `gate.severity.*`. Removing
`--exit-code-scheme` while pack resolution still differs per front end leaves
no answerable question about how a legacy pack migrates: the answer would be
"depends which command reads it." Land first: packs resolved **once** into one
immutable effective configuration (one `CompatibilityEvaluationConfig`, one
`GateOptions`), the same object used by `compare`, the release fan-out, `scan`
and the Action, with the same effective-config digest recorded in every
report.

**Scope correction (2026-08-28): the `GateOptions` half of "land first"
above was reassigned to PR G2, not landed as part of PR B — see PR B's own
"finalized" note below for the complete, canonical status (what landed,
the typed-API pack-parity exception, and why `GateOptions` was
reassigned). Consolidated here to one pointer rather than three
independently-drifting restatements (Codex review, PR #910, `docs/AGENTS.md`'s
"one fact, one canonical page" rule).**

> **Slice 1 landed (2026-08-16):** the directory/package release fan-out no
> longer rejects `--pack` outright. `CompareRequest` gained `pack_policy_
> overrides`/`pack_internal_namespaces` (additive, `None` by default — see the
> field's own docstring in `api_types.py`), folded into the loaded `PolicyFile`
> at the one Tier-2 chokepoint every `CompareRequest` consumer already passes
> through (`service_compare_pipeline.classify_compare_pair`, via `pack_
> application.policy_file_with_packs` — the exact function the single-pair CLI
> path already used). `cli_compare_helpers.run_compare` resolves the release
> operand's `--pack` once (`cli_compare_receipt.resolve_release_pack_
> application`/`_from_ctx`), before dispatch, and forwards the resulting
> `PackApplication` through `compare_release_cmd` → `_compare_release_
> libraries`/`_collect_release_extras`/`_collect_matrix_result` → `_run_
> compare_pair` → `service.run_compare` (now `service_compare_pipeline.
> run_compare`, moved there in the same change to stay under the AI-readiness
> file-size cap) uniformly, so every library in the release — and its
> build-configuration-matrix findings — sees the identical pack-resolved
> policy. **What is still open, deliberately not attempted in this slice:** a
> `kind: gate` pack (`gate.exit_code_scheme`/`gate.severity.*`) is still
> rejected for the release fan-out (`gate_supported=False`, mirroring `cli_
> scan.py`'s existing stance) — release's severity/exit-code-scheme resolution
> (`_resolve_release_severity_config`, raw CLI-or-config strings re-derived at
> several call sites) has no `GateOptions`-shaped object to fold a pack's
> `gate.*` fields into yet, and forcing one through the existing raw-string
> shape risked exactly the "changed behavior nobody reviewed" this cleanup
> exists to avoid. `scan`'s own `gate.*` rejection is unchanged. Neither the
> "one effective configuration shared by the Action" half nor the
> "effective-config digest recorded in every report" half of PR B's stated
> goal is attempted in this slice — both remain open, and both need the
> `GateOptions` unification above as a prerequisite, same as the gate-pack gap.
> A review round on the PR that landed this slice caught two further gaps,
> both closed the same way as the gate-pack one — reject rather than accept
> and silently score nothing: `contract.unresolved` is now also rejected for
> a release comparison unconditionally (with or without `--contract`), since
> its consumer reads a per-comparison `PersistedContractContext` the release
> fan-out never builds per library. The review's second finding — bundle-
> level (cross-library) findings never respecting *any* policy override, pack
> or not — turned out to be a pre-existing gap unrelated to `--pack`, not a
> gap this slice introduced; recorded in the root `AGENTS.md`'s "Known gaps"
> instead of fixed here, since it's a real feature addition to the bundle-
> analysis subsystem, not a pack-specific follow-up.
> Tests: `tests/test_pack_application.py`'s `TestOnlyAppliedFieldsAreAccepted`
> (`test_policy_pack_is_applied_to_a_release_comparison`,
> `test_gate_pack_is_still_rejected_on_a_release_comparison`,
> `test_contract_unresolved_pack_still_rejected_on_a_release_comparison`).
>
> **Slice 2 landed (2026-08-16):** the directory/package release fan-out no
> longer rejects a `kind: gate` pack either. Rather than building the full
> `GateOptions` object this section's own goal names — a shared, unified
> configuration object for `compare`/release/`scan`/the Action, still not
> attempted — the fix is the same additive discipline slice 1 used for
> `policy.overrides`/`surface.internal_namespaces`, applied to the release
> fan-out's own raw exit-code-scheme/severity strings instead of a resolved
> object: `cli_compare_receipt.resolve_release_pack_application` now calls
> `pack_application.check_resolved_config_applies_packs` with
> `gate_supported` at its default (`True`) instead of forcing it `False`, so
> the already-resolved `PackApplication`'s `exit_code_scheme`/
> `severity_levels` fields (`pack_application.pack_application()` always
> populates them, gate-supported or not — nothing new needed there) reach
> the release fan-out instead of being rejected before they're read. A new
> `cli_compare_release_helpers.apply_release_gate_pack()` mirrors
> `pack_application.apply_to_compare_config`'s logic — a pack-supplied
> severity level overrides the matching raw `--severity-<category>`-shaped
> string (only ever reached when nothing more explicit already stated it,
> since `pack_application()` itself already excludes a field an explicit
> source shadowed), and a pack-supplied `gate.exit_code_scheme` overrides
> the raw scheme string the same way, falling back to the resolver's own
> already-decided `resolved_exit_code_scheme` when only a severity level
> moved and no scheme was directly assigned (the identical "a severity
> level *is* severity being configured" rule `apply_to_compare_config`'s
> own docstring states, and the identical "read the resolved value, never
> re-derive one" rule that avoids silently overriding an explicit
> `--exit-code-scheme legacy`). `cli_compare_release.compare_release_cmd`
> calls this fold exactly once, early — the same point it already
> reassigns `severity_preset`/`release_exit_code_scheme` for the
> `.abicheck.yml`-only `exit_code_scheme: severity` case — so every
> downstream consumer of those six raw strings (`_resolve_release_severity_
> config`, the per-library JSON write inside `_compare_release_libraries`,
> `_compute_release_severity_exit_code`, `_fold_release_global_severity`)
> agrees with the pack, the same "one fold point, not several independently
> re-deriving ones" discipline slice 1 established. **What was still open
> after this slice** (unchanged from slice 1's own list, restated here since
> the gate-pack gap it named is now closed): the full `GateOptions` object
> shared by `compare`/release/`scan`/the Action; the effective-config digest
> recorded in every report; and `scan --against`'s own `kind: gate`
> rejection. The first two remain open (below); the third closed in slice 3.
> Tests: `tests/test_pack_application.py`'s `TestOnlyAppliedFieldsAreAccepted`
> (`test_gate_pack_is_applied_to_a_release_comparison`, replacing the
> now-stale rejection test of the same name minus "is_applied", and
> `test_gate_pack_severity_moves_a_release_onto_the_severity_scheme`).
>
> **Slice 3 landed (2026-08-19): `scan --against` also accepts a `kind:
> gate` pack.** Unlike the release fan-out (slice 2, above), `scan` already
> has a real `ResolvedCompareConfig` object to fold a pack's contribution
> into — `resolve_compare_config`, the exact function single-pair `compare`
> uses, already runs inside `scan_cmd` to resolve `--severity-preset`/
> `--exit-code-scheme` (direct CLI flags and `.abicheck.yml`) into the
> `sev_config`/`resolved_exit_scheme` `run_scan_core` gates on — the "scan
> never consults severity" gap this closed in an earlier PR. So this slice
> reuses `pack_application.apply_to_compare_config` **directly**, unchanged,
> rather than a raw-string mirror of it the way the release fan-out's
> `apply_release_gate_pack` needed: `_resolve_scan_evaluation_config` now
> takes the already-resolved `resolved_cfg` as a parameter, folds the
> selected packs' `PackApplication` into it with `apply_to_compare_config`
> (gated on `gate_supported=True`, no longer `False`), and returns the
> folded object as a third return value; `scan_cmd` re-derives `sev_config`/
> `resolved_exit_scheme` from it before `run_scan_core` runs, and
> `dry_run_scheme_label(resolved_cfg, pack_paths)` (the real `pack_paths`,
> not a hardcoded `()`) gives `scan --dry-run` the same honest "a selected
> --pack may adjust it" caveat `compare --dry-run` already gives, rather
> than resolving the pack early against different pins than the real
> (later) resolution uses — the same reason `compare --dry-run` never
> resolves one early either (`dry_run_scheme_label`'s own docstring).
> **Still open after this slice, unchanged**: the full `GateOptions` object
> shared by `compare`/release/`scan`/the Action (this slice folds directly
> onto `ResolvedCompareConfig`, same as single-pair `compare` already did —
> it does not create a new shared object), and the effective-config digest
> recorded in every report. Tests: `tests/test_pack_application.py`'s
> `TestOnlyAppliedFieldsAreAccepted::test_a_gate_pack_is_applied_to_scan`,
> replacing the now-stale `test_a_gate_pack_is_rejected_by_scan_which_has_
> no_gate`.
>
> **Slice 4 landed: the effective-config digest, closing PR B's second
> still-open goal in full.** `effective_config_digest.py` is a new leaf
> module computing a `sha256:...` fingerprint (plus the named field dict it
> was hashed from, for attribution -- the same `profile_fields`/
> `scope_fields` precedent `comparability.py` already established) over the
> resolved gate/policy/surface/contract configuration a comparison actually
> ran under. Two tiers, both real and both honest about their own scope
> (there is still no single object holding *every* configuration axis for
> *every* run -- see the module's own docstring): a rich tier reading the
> full `CompatibilityEvaluationConfig` off `DiffResult.contract_context`
> when the run passed `--contract`/`--pack` (real pack identities included),
> and a baseline tier built from the policy/gate fields every comparison
> resolves regardless (`DiffResult.policy`/`policy_file`, the resolved
> `SeverityConfig`/`exit_code_scheme` pair). One function,
> `effective_config_fields`, picks the tier; `compare` (via
> `reporter_contract_blocks.add_contract_context`), `reporter.py`'s
> `--stat` JSON summary builder, and `scan --against`
> (`cli_scan_baseline._run_baseline_compare`,
> reusing the exact `sev_config`/`exit_scheme` pair its own `exit` block was
> just resolved from) all call it through one shared helper,
> `reporter_contract_blocks.add_effective_config_digest` -- so across
> those call sites this digest reaches, it's now literally one
> function, not several approximations of one shape. `report_schema_version`
> 2.45 / `scan_schema_version` 1.19 (additive keys: `effective_config_digest`/
> `effective_config_fields`). **Scope correction (Codex review, fresh
> evidence, three rounds — the first draft of this correction undercounted
> the call sites, the second overcounted them, this is the corrected
> version): "in every report" overstates this -- the digest reaches only
> the native JSON report path, and even there it's not one uniform digest
> everywhere.** `add_effective_config_digest` is called from exactly
> three places: `reporter_contract_blocks.py`'s own `add_contract_context`
> (a per-library/single-comparison `compare` JSON report, gated on
> `include_exit_decision`), `reporter.py`'s separate `--stat` JSON summary
> builder, and `cli_scan_baseline.py`'s `scan --against` JSON path. **The
> directory/package release fan-out does NOT go through this function at
> all** -- its own release-level *summary* JSON (both the primary release
> report, `cli_compare_release_helpers._format_release_json`, and the
> `--output-dir` sibling, `cli_compare_release._write_release_summary_
> file`) instead calls a separate, narrower helper,
> `_release_summary_effective_config_block`, which computes its digest
> from `SeverityConfig` alone -- no `CompatibilityEvaluationConfig`/
> `PolicyFile`/suppression object exists at release-summary scope at all
> (documented as a known, accepted gap in that helper's own docstring).
> So two releases differing only by a policy pack or internal-namespace
> override can share an identical release-summary digest even though
> their *per-library* sidecar reports (which DO reach
> `add_effective_config_digest` via `add_contract_context`) correctly
> differ -- a real, narrower parity gap this note previously elided by
> claiming the release fan-out reuses the identical call. The Markdown,
> review, SARIF, JUnit, and HTML renderers (`html_report.py`, `sarif.py`,
> `junit_report.py`, and `reporter.py`'s own non-JSON renderers) never call
> it, so a user selecting one of those output formats gets no
> effective-configuration fingerprint at all. `compat check
> --report-format json` also intentionally omits it: `compat/cli.py`
> reuses `reporter.to_json` with `include_exit_decision=False` (its real
> process exit follows the ABICC-style 0/1/2 scheme, not the native
> gate/severity axes this digest describes), which
> `TestCompatReportOmitsTheDigest` in `tests/test_effective_config_
> digest.py` pins directly. Tests: `tests/test_effective_config_digest.py`
> (both tiers directly, plus cross-report parity: two reports resolving
> the identical configuration produce the identical digest regardless of
> findings/library name, a policy override changes it, and the compat
> exception above).
>
> **What remains open, deliberately not attempted in this slice, and why:**
> the *first* still-open goal from the note above -- one shared
> `GateOptions`-*typed* object the release fan-out's own severity/exit-
> code-scheme resolution is built from, replacing its six-raw-string
> threading through `_resolve_release_severity_config`/
> `_compute_release_severity_exit_code`/`_fold_release_global_severity`/the
> per-library JSON write -- is still open. Investigated in this same pass:
> `apply_to_compare_config` (`pack_application.py`) already operates
> structurally on any object exposing `severity`/`severity_active`/
> `exit_code_scheme` (typed `Any`, not a nominal `GateOptions` class), which
> is why `compare` and `scan` already share one real resolved object
> (`ResolvedCompareConfig`) through it with zero duplication -- the
> unification these two front ends needed was already done before this PR.
> The release fan-out is different in kind, not just missing a cast: its
> `severity_config: SeverityConfig | None` (`None` meaning "legacy scheme,
> nothing to score") has no `ResolvedCompareConfig`-shaped counterpart to
> fold onto, by design (see `apply_release_gate_pack`'s own docstring,
> unchanged by this slice) -- and its `SeverityConfig` is deliberately
> *lossy* relative to the raw preset/category strings each of those three
> downstream functions independently re-resolves from (a `SeverityConfig`
> has no `preset` field to decompose back to). Building a real `GateOptions`
> object *and* migrating those four call sites off raw-string re-derivation
> is a genuine rewrite of exit-code-computation logic in the single most
> reviewed area of this whole plan -- the `--exit-code-scheme`/severity
> section a few pages down lists five independently-Codex-caught bugs in
> this exact function family, and PR G2 (the ADR-gated gate-algorithm
> unification) has not landed yet either, so redesigning the release
> fan-out's internal representation now risks colliding with that PR's own
> design rather than simplifying ahead of it. Per this repo's own "known
> gaps over risky reactive patches" convention, left for PR G2 (or its own
> dedicated follow-up) to fold in as part of that rewrite, rather than
> attempted reactively here. The digest slice above does not depend on it:
> it reads already-resolved values from whichever shape each front end
> currently produces them in, so it is correct today and stays correct
> once the release fan-out's internals eventually change shape.
>
> **Follow-up fix, same day (Codex review on #801, fresh evidence, real
> reproduction): the initial slice 3 had a real D8-precedence bug, not just
> an incomplete receipt.** `_resolve_scan_evaluation_config` built the
> `PackApplication` from `resolve_scan_config`'s own ADR-049 receipt object
> — but `SCAN_CONFIG_PARAMS` never included `severity_preset`/
> `exit_code_scheme`, and that receipt's own `_without_gate_settings` helper
> additionally blanked the six project-config-sourced gate fields to avoid a
> stale two-resolver disagreement. So neither an explicit
> `--severity-preset`/`--exit-code-scheme` nor a `.abicheck.yml` `severity:`/
> `exit_code_scheme:` block was ever visible to this resolver's D7
> precedence — every selected gate pack looked unopposed regardless of what
> was actually stated, and `apply_to_compare_config` (correctly assuming its
> caller's `PackApplication` already respected D7 precedence, the same
> assumption that holds for `compare`) silently let the pack win. Reproduced
> concretely: a removed export scanned with `--severity-preset strict` and a
> `gate.severity.abi_breaking: warning` pack exited 0, not 4. Fixed at the
> root: `severity_preset`/`exit_code_scheme` joined `SCAN_CONFIG_PARAMS`
> (closing the explicit-CLI tier), and `_without_gate_settings` was removed
> entirely rather than merely extended (closing the project-config tier
> too) — verified safe specifically for `scan`, which has no `--profile`
> option unlike `compare`, so `ProjectCompatibilityInputs.from_build_config`
> and `resolve_compare_config` read the identical six fields off the
> identical `project_cfg` object with the identical `explicit CLI > project
> config > built-in default` precedence and cannot disagree the way the
> blanking was originally written to guard against. `service_scan.
> run_scan`'s hand-built params dict (the API front end, which has no
> `severity_preset`/`exit_code_scheme` CLI flags to be explicit about) now
> passes both as `None`, matching every other field `ScanRequest` does not
> carry. Tests: `tests/test_pack_application.py`'s
> `test_a_gate_pack_cannot_override_an_explicit_scan_severity_preset` and
> `test_a_gate_pack_cannot_override_a_project_config_severity_preset` (both
> confirmed to fail with the pre-fix exit 0, verified via negative control);
> `tests/test_cli_scan_receipt_unit.py`'s `TestGateConfigReceipt` rewritten
> from `TestGateBlanking` to assert the new, accurate provenance instead of
> the old blanked-to-default one.

> **PR B finalized (2026-08-28).** Both of PR B's own stated goals are fully
> landed for every CLI command path: pack parity across `compare`, the
> release fan-out, and `scan --against`, both `policy.overrides`/`surface.
> internal_namespaces` *and* `gate.*` fields — slices 1-3 — plus the
> effective-config digest reaching every native JSON report path in some
> form (slice 4; **narrower than "every report", and narrower still than
> "one uniform digest"**, Codex review, fresh evidence — see slice 4's own
> section above for the exact scope: only the JSON report path calls
> `add_effective_config_digest` at all — the Markdown/review/SARIF/
> JUnit/HTML renderers don't, and `compat check --report-format json`
> deliberately omits the field too — and even within JSON, the
> directory/package release-summary report does not go through
> `add_effective_config_digest`: its own `_release_summary_effective_
> config_block` hashes `SeverityConfig` alone, so two releases differing
> only by a policy pack or internal-namespace override can share an
> identical release-summary digest even though their per-library sidecar
> reports correctly differ — a real, open parity gap this status note does
> not claim closed, kept open alongside the `GateOptions` item below).
> **Narrower again once the typed
> Python API is included**: `--pack`
> is a CLI selector today (ADR-049 D8), and neither `ScanRequest` nor
> `CompareRequest` carries a pack field a typed caller can set — `ScanRequest`
> has none at all (`service_scan._resolve_api_evaluation_config` explicitly
> resolves `pack_paths=()`, documented at that call site as "a `ScanRequest`
> has no pack field"), and `CompareRequest` accepts only pre-resolved
> `pack_policy_overrides`/`pack_internal_namespaces` (the `policy.overrides`/
> `surface.internal_namespaces` halves), with no `gate.*` equivalent — so a
> typed-API caller cannot reproduce a CLI `gate`-pack's severity/exit-code
> configuration today (Codex review, PR #910, fresh evidence). Landing that
> is its own, separate slice — a `pack_paths`/gate-pack field on both typed
> requests, threaded through `PackApplication` the way the CLI receipts
> already are — not implied by anything closed here. The one
> remaining loose end named above — a typed `GateOptions` object the
> release fan-out's own severity/exit-code-scheme resolution is built from,
> replacing its six-raw-string threading — is deliberately **not** PR B's
> to close. It was investigated in slice 4's own pass and found to need a
> genuine rewrite of the release fan-out's internal representation, in the
> single most reviewed area of this plan, immediately ahead of PR G2's own
> not-yet-designed rewrite of the identical logic (the ADR-gated one-gate-
> algorithm unification `--exit-code-scheme` removal needs) — building it
> now risks colliding with a design that doesn't exist yet, rather than
> simplifying ahead of it. Reassigned explicitly to PR G2 as one of *its*
> prerequisites (see that section's own "(1) Pack parity" and "(2) One
> canonical `ExitDecision`" pair below — the `GateOptions` unification
> belongs with (2), since it's the same rewrite), not tracked as
> outstanding PR B work. See the "Ordering" table at the bottom, which now
> marks PR B **(DONE)** on this basis.

This is also PR 1b/E's prerequisite, which is why it sits early in the
reviewed ordering rather than inside PR 4.

**(2) One canonical `ExitDecision` — the review's PR G proper.** This
prerequisite also inherits PR B's one deliberately-deferred item (see PR B's
"finalized" note above): the release fan-out's own severity/exit-code-scheme
resolution still threads six raw strings through four functions instead of
one typed `GateOptions` object the way `compare`/`scan` already share via
`ResolvedCompareConfig`. That rewrite belongs here, not as separate PR B
follow-up, since it touches the identical exit-code-computation logic this
`ExitDecision` unification is already rewriting. Since #780
this is no longer two axes but a set of them, and a flat `max()` is not the
whole rule: scan budget overflow (`5`) is scan-only, and so is a pinned depth
whose evidence can't be collected (`_EvidenceContractError`, exit `1`); usage
error (`64`) is not a result at all and never appears in a report; and exit
`1` has several distinct causes that a bare number cannot tell apart. Encode
it once:

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
than distributed across CLI callbacks — **precedence order, not a renumbering,
and derived from what the current code actually does, not from what "should"
dominate (two rows below were wrong in an earlier draft for exactly that
reason — Codex review, fresh evidence against `scan_engine.py`)**:

```text
usage/config error            (outside the report entirely — 64 everywhere)
scan evidence-contract error  (scan only, exit 1 — see below)
scan budget exceeded          (scan only, exit 5 — see below; dominates
                                not-comparable when both would apply)
not comparable                (dominates the gate/coverage/assurance axes
                                below, but not budget — see below)
removed required library    ─┐ mode-dependent (see below) — NOT a fixed rank
ABI / API / policy gate      ─┘
coverage & assurance floors   (max-folded, never lowering the above)
clean
```

**Scan evidence-contract error and the budget-vs-comparability ordering, both
found by review, fresh evidence against `scan_engine.py`:**

- `run_scan_core` raises `_EvidenceContractError` during evidence collection
  — before a candidate/baseline comparison is even attempted — whenever a
  pinned (non-`auto`) `--depth`/`--source-method` has no source evidence to
  satisfy it (ADR-037 D5). `cli_scan.py` maps it to a `click.ClickException`
  (exit `1`); `service_scan.py` maps it to `ScanResult(verdict=
  "EVIDENCE_CONTRACT_ERROR", exit_code=1)`. Distinct from usage error `64`
  (a bad flag combination) and from the gate's own `1` (a severity-scheme
  error-level finding) — it's "the evidence contract this run pinned itself
  to could not be met," always scan-only, and an earlier draft of this table
  omitted it entirely.
- An earlier draft also had not-comparable dominating budget overflow — the
  real code does the opposite. `run_scan_core`'s baseline-compare block can
  set `exit_code = 6` (`NOT_COMPARABLE`) on a `ProfileMismatchError`/
  `ScopeMismatchError`, but `_check_scan_budget(budget, budget_s, elapsed)`
  still runs unconditionally afterward and, if the elapsed time is over
  budget, *raises* `_BudgetOverflow` regardless of what was already decided
  — discarding the not-comparable result before `ScanOutcome` is even
  constructed, so the caller maps the exception to exit `5`. So today, when
  both conditions hold in the same run, budget wins. `ExitDecision`'s
  resolver must reproduce that order, not the "more fundamental axis should
  dominate" ordering that reads more natural on paper.

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

**Status: the repeatable-option syntax slice is implemented (2026-08-28).**
`--artifact-set` is now `multiple=True` (`cli_options.py`); a single
directory value is unchanged, and multiple explicit paths are given as one
`--artifact-set` per member (`--artifact-set a.so --artifact-set b.so`), not
a comma-separated string. The comma-separated form is gone with no alias,
same "hard cleanup" stance as every other removal in this plan — passing
the old `a.so,b.so` spelling as a single value is now read as one literal
(nonexistent) path and errors `--artifact-set member not found`, not
silently parsed. `_resolve_artifact_set_paths` (`cli_scan.py`),
`reject_incoherent_scan_operands`/`reject_incoherent_scan_secondary_output`
(`cli_scan_helpers.py`), and `_run_artifact_set`/`scan_cmd`'s own type all
moved from `str | None` to `tuple[str, ...]` together — "supplied" is now
exactly `bool(artifact_set)`, which closes the CodeRabbit-caught
truthiness/`is not None` mismatch class the comma-string form needed a
special-cased comment to avoid (a tuple has no falsy-but-present state the
way an empty string did). The composite Action's own `new-library-set`
input (`action.yml`) deliberately keeps its comma-separated contract
unchanged — that is a separate, already-decoupled front end, and front-end
parity here means *staying working*, not re-breaking to match the CLI's new
syntax — so `action/run.sh` now splits a comma-separated `new-library-set`
value into one `--artifact-set` occurrence per member (a bare directory,
having no comma, passes through as the single unsplit value it always was).
Docs regenerated (`gen_cli_reference.py`); tests updated across
`test_scan_artifact_set.py`, `test_bazel_root_targets_scan.py`, and
`test_action_run_sh_artifact_set.py` (the latter gained two new cases
pinning the Action-side comma-split and blank-member-skipping behavior
directly against the real `action/run.sh` text, not a paraphrase of it).

**A second slice (2026-08-29) shipped the dry-run/cost-estimation item.**
`scan --artifact-set --dry-run` is a real preview now, not a hard rejection
(`reject_incoherent_scan_operands`'s own `--dry-run` check was removed;
`render_artifact_set_dry_run`, `abicheck/frontends/cli/
artifact_set_dry_run.py`, builds the report). The cost projection is
genuinely per-member-scaled: one single-binary `ScanRequest` is built per
discovered member from the real request's shared fields, each run through
`service.estimate_scan()` independently, and the per-layer TU/time results
summed across members — rather than reusing the shared estimator's
single-request shape, which only scales its `L0_binary` row by
`len(binaries)` (see `docs/contribute/plans/g35-multi-artifact-scan.md`'s
own estimator bullet for that general, still-open gap for other
`estimate_scan()` callers). Lives in a new `frontends/cli/` leaf module
rather than `cli_scan.py` (`no_growth`-debt-tracked, at its line-count
baseline) or `cli_scan_helpers.py` (which cannot import `.service` without
closing an import cycle back through `service -> service_scan ->
scan_engine -> cli_scan_helpers`). Tests in
`tests/test_scan_artifact_set_coverage.py`.

**Still open, per the sequencing note below:** `--artifact-set-manifest`
(no real domain contract proposed for it yet) and the remaining set-mode
*semantics* items (expected provider DSO, a symbol moved between sibling
libraries, duplicated providers, L4 symbol reconciliation) — the first
slice touched only the value syntax and the second only the dry-run/cost
item; the review itself called the syntax change "the only part of this
section worth doing on its own."

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
# before this slice (removed, no alias — now errors, see the status note above)
abicheck scan --artifact-set a.so,b.so,c.so

# landed: repeatable option
abicheck scan --artifact-set a.so --artifact-set b.so --artifact-set c.so

# unchanged
abicheck scan --artifact-set directory/

# still just a proposal, not implemented — see "Still open" in the status note above
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
duplicated providers, and L4 symbol reconciliation (cost estimation and the
dry-run/cost preview shipped in the second slice above -- text-only, like
every other `abicheck` dry-run, ADR-054). Do the remaining semantics first
if they compete with anything else. The
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
PR B  effective configuration parity  — packs resolved once into one
      (DONE)                           CompatibilityEvaluationConfig, pack
                                       parity across every CLI command
                                       path (typed-API parity is separate,
                                       outstanding work), and an
                                       effective-config digest in every
                                       native JSON report (see PR B's own
                                       section for the non-JSON/compat
                                       exception); the release fan-out's own
                                       typed GateOptions object is
                                       deliberately reassigned to PR G2,
                                       see PR B's own section for why
PR C  typed dump+scan convergence     = PR 3A — DumpRequest →
      (ELF done, PE/Mach-O open)       ResolvedDumpRequest → DumpResult, one
                                       resolver for dump CLI/Python/Action
                                       *and* scan_engine's candidate
                                       resolution, JSON dry-run rendered
                                       from that object. The real ELF `dump`
                                       run now executes through
                                       execute_dump_request (scan's
                                       candidate resolution already did);
                                       handle_non_elf_dump (PE/Mach-O) is
                                       unmigrated -- no PE/Mach-O toolchain
                                       was available to verify against
PR D  build-context completeness      = PR 3B — matched compile-unit
      (DONE)                           selection, forced includes, provenance
                                       tests
PR G1 canonical exit decision, part 1 — ExitDecision + reasons + the report
                                       block, precedence pinned by ADR and
                                       tests; NO CLI change, no flag removed.
                                       Lands before PR E, which consumes it
PR E  Action machine-report           = PR 1b — uncapped persisted release
      (DONE)                           findings, no comparison re-run, no
                                       stderr inference; reads G1's block
      └─ DELETE --annotate, --annotate-additions — DONE, verified against
         current abicheck/cli.py and action.yml
PR F  trusted build config            = PR 3C — build.query executes only
      (blocked on PE/Mach-O, below)     from an explicit --config (a data
                                       path like build.compile_db carries no
                                       such restriction), trust receipt in
                                       --dry-run, fail closed. Blocked on
                                       PR C's remaining half: handle_non_elf_
                                       dump (PE/Mach-O) still resolves
                                       independently of the shared pipeline
      └─ then DELETE dump --build-query, dump --build-compile-db
PR G2 canonical exit decision, part 2 = PR 4 — one automatic gate algorithm,
      (ADR-064 accepted; stage 1a done,   schema / report / Action parity
       stage 1b partially wired)
      └─ then DELETE --exit-code-scheme
PR H  artifact-set semantics          = PR 5 — provider ownership, moved and
      (syntax slice DONE)               duplicated symbols, cost and dry-run;
                                       syntax refinement (DONE) was the one
                                       piece independent of the semantics work
```

Independent of the chain, unblocked at any time: PR 1 (**done**), PR 2
(**done**, including its `.abicheck.yml` gate-policy sourcing follow-up —
see PR 2's own section: `aggregate: gate:` shipped, and `project plan`'s
`--gate-missing-required`/`--gate-unexpected-target` flags were removed),
PR E / 1b (**done** — annotations moved to the Action; see PR 1b's own
section, whose "blocked on a persistence prerequisite" subtitle was stale
until this pass corrected it).

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
