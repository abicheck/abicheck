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
**Effort:** L (seven independent PRs) · **Risk:** mixed — PR 1 is
presentation-only, PR 1b is gated on new persisted-report schema work, PR 4
changes what a CI job's exit code means.

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
| `--exit-code-scheme` (compare, scan) | Remove, but reworked | Own ADR + semantics PR: keep two orthogonal axes, drop the *manual algorithm selector* |
| `compare --stat`, `compare --recommend` | Remove | `--format review` replaces `--stat`; recommendation becomes an unconditional renderer output |
| `scan --artifact-set` | **Keep** | Refine the value syntax only (repeatable option / manifest); do not overload positional `DIRECTORY` |
| `--annotate`, `--annotate-additions` | Remove from CLI (PR 1b, not PR 1) | Options on `compare` alone, shared by both operand shapes (no CLI-level split is possible) — blocked on a release-report persistence prerequisite; see PR 1b |
| `dump --build-query`, `dump --build-compile-db` | Remove from CLI | Move to explicitly-trusted `.abicheck.yml`, with a real trust + dry-run contract |
| `aggregate --on-missing-required`, `--on-unexpected-target` | Remove from CLI | Move the policy into the manifest / run-plan schema alongside the expected target set |

Everything above is a **breaking** change to the native CLI. Consistent with
the #770 cleanup and ADR-037's stance, none of it gets a deprecation alias:
an old spelling must fail as `No such option` with exit `64`.

## PR 0 — restore a green CI baseline first

**Status: verified, not separately actioned** — the "Verified state" below
was confirmed current as of PR #779; the standing Windows lane failure is
tracked as pre-existing and unrelated to this initiative's own changes, per
that PR's own thread. The branch-protection/required-checks work (items 3–4
below) has not been done as part of this initiative.

This is a prerequisite, not a nicety: a breaking interface change cannot be
evaluated against a red baseline.

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
   a red CI does not block merge. Add required checks (branch protection or
   Ruleset) covering the Linux/macOS/Windows unit lanes, `lint-and-types`, and
   `ai-readiness`.
4. Add exact-merge-SHA verification on `push: main`, so a merge that did not
   run the checks it claimed is detectable after the fact.

**Done when** a full `ci.yml` run on `main` is green on Linux, macOS and
Windows, and a red required check demonstrably blocks a merge.

## PR 1 — presentation

**Status: implemented** (PR #779) — `--stat`/`--recommend` are gone,
`--profile quick` carries the one-line summary (including a fix for the
`--used-by`/`--required-symbol` scoped-gate case, found by review after the
first push), and the release recommendation is unconditional in
`json`/`markdown`/`review` output.

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

**Tests.** A saved report fixture (both a single-library and a release-style
report) must produce, through the Action's renderer, byte-identical
annotations to what the CLI emitted for the same report before the move.
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

**Tests:** manifest-supplied policy; run-plan-supplied policy; default policy;
missing required target; unexpected analyzed report; unreadable unexpected
report; `effective_policy` preserved in aggregate JSON; incompatible manifest
schema rejected; the removed flags exit `64`.

## PR 3 — build execution moves into trusted config

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
machine-readable dry-run. Do the semantics first if the two compete.

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

```text
PR 0   green CI + required checks     (prerequisite)
PR 1   presentation                   (low risk)
PR 1b  annotations → Action           (medium risk, needs the persistence prerequisite first)
PR 2   aggregate policy schema        (schema, MAJOR version bump)
PR 3   build execution → trusted config (trust)
PR 4   gate semantics + ADR           (behavioural, highest risk)
PR 5   artifact-set syntax refinement (optional, after set-mode semantics)
```
