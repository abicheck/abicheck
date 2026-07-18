# ADR-043: Pre-1.0 CLI Surface Reset — Root Command Collapse, Depth Ladder Narrowing, and Dry-Run Unification

**Date:** 2026-07-16
**Status:** Accepted — implemented.
**Decision maker:** (pending)

**Amendments:**

- **2026-07-18 (D13):** The root surface grows from five verbs to **six** with
  the addition of `aggregate`, a report-level multi-target CI fan-in gate. This
  amends D1's "exactly five verbs" — see [D13](#d13-add-aggregate-a-report-level-multi-target-fan-in-gate)
  for why a sixth verb clears the ADR-037 D7 "different question / different
  operand shape" bar (it consumes *reports*, not binaries) rather than folding
  into `compare`.

---

## Context

ADR-037 (D7) set the bar for a new top-level command — "a different question,
or a fundamentally different operand shape" — and applied it once:
`compare-release`/`deep-compare` folded into `compare`, but `appcompat` and
`plugin-check` were kept as separate verbs because they answer a
*consumer-scoped* question ("is this application/plugin-host still
satisfied?") rather than the library-surface question `compare` answers.

In practice that line turned out to be in the wrong place. `appcompat` and
`plugin-check` do not ask a different question at all — they ask the *same*
compatibility question as `compare`, then **scope** the same diff to a
narrower relevance set (an app's actual imports, or a fixed set of required
entry points). They duplicated `compare`'s dumping/diffing pipeline instead of
reusing an already-computed diff, and both had drifted from `compare`'s option
families exactly as ADR-037 §Context predicted duplication would.

Beyond that specific case, by 2026-07 the CLI carried a second layer of
accumulated surface ADR-037 did not reach:

- A `baseline` registry command group (ADR-022, "partially implemented") that
  never grew past a filesystem backend and largely duplicated what a plain
  JSON snapshot plus `scan --against`/a stored file already provides.
- `collect`/`merge`/`recommend-collect-mode` — a three-command build-evidence
  pipeline that `dump --sources`/`--build-info` and `compare`'s own automatic
  pack ingestion had already made unnecessary for the common case; they
  survived as separate verbs mostly by inertia.
- `debian-symbols`, `doctor`, `config` (a `.abicheck.yml` scaffolding
  generator), `init`, `surface-report`, `suggest-suppressions`, `probe` — a
  long tail of narrow, low-traffic commands, several of which (`doctor`,
  `config`, `init`) existed only to compensate for a *permissive* config
  loader that silently accepted typos.
- `scan`'s own surface had re-grown a second evidence vocabulary
  (`--mode`/`--source-method`) alongside `--depth`, plus a separate
  `--audit`/`--estimate` pair of boolean toggles that were really just
  "no `--against` given" and "show me the resolved plan, don't run it" in
  disguise.
- The public `--depth` ladder still exposed `full` as a fifth rung even
  though, per ADR-037 D6, `full` and `source` never differed in *what*
  evidence they collect — only in replay **scope** (the whole target vs. a
  narrower changed-path seed). Keeping it as a separate rung invited exactly
  the "five-value enum, two of which mean almost the same thing" confusion
  ADR-037 D5 tried to avoid for the L/S vocabulary.
- A latent scope-resolution bug: an explicit `scan --depth source` with no
  `--since`/`--changed-path` seed could resolve to the "changed" replay scope
  with an empty change set — i.e. **zero translation units**, silently. The
  bare default (no `--depth` at all) happened to fall back to a broader scope
  and did not hit this; only the explicit, deeper request did, which is
  backwards from what a user asking for more evidence should get.

We are still pre-1.0. ADR-037 built the deprecation-alias *mechanism* but
explicitly deferred switching it on until 1.0 ("old flags still work and
nothing errors on their use yet"). This ADR is the last surface cut before
that switch — it is deliberately **not** using the alias mechanism: every
removed command and flag is deleted outright, with no deprecated shim. Doing
the breaking cleanup now, in one pass, is cheaper than deprecating twice.

---

## Decision

### D1. Root command surface is exactly five verbs

> **Amended 2026-07-18 (D13):** now **six** verbs — `aggregate` was added as a
> report-level fan-in gate. The "nothing else is registered / no hidden alias /
> deleted commands are a usage error" invariants below are unchanged; only the
> count and the explicit verb list grow by one.

`dump`, `compare`, `scan`, `deps`, `compat`. Nothing else is registered on the
root `Click` group; nothing removed below leaves a hidden alias. Every deleted
command produces the ordinary Click "No such command" usage error (exit 64) —
indistinguishable from a typo. `deps` keeps its two subcommands (`tree`,
`compare`); `pr-comment` moves **off** the public tree entirely (see D3).

### D2. `appcompat`/`plugin-check` fold into `compare` as scoping flags

This reverses ADR-037 D7's "appcompat stays a separate command" call. The
"different question" framing does not hold once the operation is factored
correctly: **the comparison is identical** (same `compare_snapshots` diff);
only the *relevance filter* over an already-computed diff differs.

- `compare --used-by APP` (repeatable) replaces `appcompat`. `appcompat.py`
  is refactored so the app-requirement parsing / symbol-availability /
  verdict logic (`scope_diff_to_app`) operates on a diff the caller already
  computed, instead of re-dumping and re-comparing — `check_appcompat` becomes
  a thin standalone wrapper around the same function for any remaining
  library callers.
- `compare --required-symbol SYM` (repeatable) / `--required-symbols FILE`
  (one symbol per line, `#`-comments/blanks ignored) replaces `plugin-check`.
  Same refactor shape: `scope_diff_to_required_symbols` scopes an
  already-computed diff to an explicit entrypoint contract;
  `check_plugin_host_contract` becomes the thin standalone wrapper.
- The two scoping modes are mutually exclusive on one `compare` invocation
  (`click.UsageError` if both given).
- A required-symbol contract defaults `--policy` to `plugin_abi` unless the
  user passed `--policy` explicitly — matching the deleted `plugin-check`
  command's default, now expressed as a policy default rather than a
  parallel command.
- The scoped verdict's exit code (`BREAKING` → 4, `API_BREAK` → 2, else 0)
  **overrides** the unscoped diff's exit code when scoping is requested — the
  point of scoping is that the narrower relevance set is what the caller
  actually wants gated on.
- Both the CLI and the MCP `abi_compare` tool (`used_by`/`required_symbols`
  params) route through the same `appcompat.py` functions — one
  implementation, two front-ends, per the ADR-037 D1 tier discipline.

### D3. `pr-comment` moves off the public command tree

It was never a user-facing verb — it exists purely so `action/run.sh` can post
a sticky PR comment. Keeping it registered on `main` implied it belonged on
the public five-command surface. It is now a standalone
`@click.command` invoked only as `python -m abicheck.cli_pr_comment`, used
internally by the Action; it does not appear in `abicheck --help`.

### D4. Delete the registry/build-collection/long-tail commands outright

No replacement command, no alias — each is either subsumed by existing
capability or judged not to earn a place on the five-command surface:

| Deleted | Where its capability lives now |
|---|---|
| `baseline` (registry group) | A plain JSON snapshot file, compared via `scan --against OLD` or `compare` |
| `collect` | `dump --sources`/`--build-info` (inline collection); library functions survive for programmatic use |
| `merge` | `compare`'s automatic embedded-pack ingestion, plus an out-of-band `--old/new-build-info` pack (auto-detects an `abicheck_inputs/` Flow-2 pack too) |
| `recommend-collect-mode` | Folded into `scan`'s automatic depth inference (D6) |
| `debian-symbols` | No CLI replacement; `abicheck/debian_symbols.py` remains a library adapter |
| `doctor`, `config`, `init` | No longer needed once config loading is strict (D8) — there is nothing to "doctor" a permissive loader into tolerating, and no scaffolding step a strict loader can't just error clearly against |
| `surface-report` | `compute_surface_metrics()`/`recognise_idioms()`/`detect_antipatterns()`/`run_crosschecks()` (`abicheck/appcompat.py`-adjacent modules) remain directly callable |
| `suggest-suppressions`, `probe` | No replacement; judged below the five-command bar with no measured usage to preserve |

Per ADR-037 D11 ("don't unnecessarily remove stable library/service
functionality merely because its CLI command disappeared"), the underlying
Python functions for `collect`/`merge`/`surface-report`/etc. are **not**
deleted — only their Click command registrations are. A few narrow
orchestration helpers that existed solely to wire a deleted command's flags
together (e.g. `collect`'s `--source-abi android` dispatch glue) did not
survive the cut; the lower-level primitives they called remain.

### D5. `scan` reshape — positional artifact, `--against`, no separate mode/audit/estimate flags

- Repeated `--binary` → a single positional `ARTIFACT` argument. `scan` always
  targets exactly one artifact; the repeated-flag shape never matched that.
- `--baseline` → `--against OLD`. `--baseline-header`/`--baseline-include` are
  removed; `-H/--header` and `-I/--include` become **side-aware**
  (`old=PATH`/`new=PATH` prefix scopes to one side, a bare value applies to
  both — the same convention `compare --header old=/new=` already uses,
  ADR-040).
- `--mode`/`--source-method` are removed entirely, not hidden. Absence of
  `--against` is *already* a one-build audit; presence is *already*
  audit+compare — there is no longer a separate flag encoding what presence of
  `--against` already tells you. Evidence depth is inferred automatically
  (escalating with the changed-path risk score once a `--since`/
  `--changed-path` seed exists), or pinned with `--depth`.
- `--audit` is removed — redundant with omitting `--against` (previous
  bullet).
- `--estimate` is removed — subsumed into `--dry-run` (D7): the per-layer
  TU-count/cost projection it used to print is now one section of the shared
  dry-run report.

### D6. Public `--depth` ladder narrows to four rungs; `full` is gone

`binary | headers | build | source` — full stop. `--max`, `--source-method`,
`--mode`, and the `symbols`/`graph` depth spellings are rejected outright by
the CLI's `DepthParam` (a plain "not one of ..." `click.BadParameter`, no
translation). `full` collapsed into `source` per the ADR-037 D6 rationale
extended one step further: since `full`/`source` differ only in replay scope
(D7), scope is exactly the axis the calling *command* should resolve, not a
second depth value the user has to pick.

The internal `EvidenceDepth.FULL`/`GRAPH` enum members still exist (the
engine still needs them for `pr-deep`-style presets used by the internal
Python service API / `ScanRequest`), but they are excluded from
`USER_DEPTHS` and never reachable from the CLI's `--depth` flag. Internal
source-method vocabulary (`s0`..`s6`) must not leak into the public CLI,
`--help` text, reports, the config schema, MCP tool parameters, or GitHub
Action inputs — the boundary line ADR-037 D5 drew for "evidence" vocabulary,
now enforced at the CLI-parameter layer specifically (`cli_params.DepthParam`)
rather than relying on documentation discipline.

### D7. Command-aware source replay scope fixes the zero-TU defect

Introduces `SourceScope {CHANGED, TARGET, ALL}` and threads it through
`level_to_collect_mode(method, depth, *, source_scope=...)`:

- `dump` and `compare` always resolve `--depth source` at **TARGET** scope —
  the whole current library target on each side. Neither command has a
  natural "changed" concept (no diff seed), so there is only one sane
  interpretation.
- `scan` uses **CHANGED** scope only when a valid `--since`/`--changed-path`
  seed produced a real (possibly empty) diff; otherwise **TARGET**. This is
  the fix for the defect in Context: an explicit `--depth source` with no
  seed now analyses the whole target (matching what a user asking for the
  deepest evidence level expects) instead of silently resolving to zero
  translation units. A seeded-but-empty diff (a genuine no-op PR) still
  correctly scores as "nothing changed" and stays cheap.
- The same fix applies below the CLI: `service_scan.run_scan` (and its cost
  estimator) is the shared engine behind `service.run_scan`/the MCP `abi_scan`
  tool, and had the identical latent defect — it resolved the S5 collect mode
  without pinning replay scope at all, so an unseeded programmatic/MCP scan
  request also silently defaulted to "source-changed" (0 TUs). Fixed the same
  way, with the estimator's L3/L4/L5 pricing taught about the resulting
  `"source-target"` collect mode (previously it only recognized
  `"source-changed"`/`"graph-full"`, so an unseeded estimate silently reported
  zero source-layer rows instead of pricing the broader scope).

### D8. Config loading becomes strict

`.abicheck.yml` loading (`buildsource/inline.py`'s `BuildConfig.from_dict`)
now raises on an unknown top-level key, an unknown block subkey, a
non-mapping block value, a wrong scalar/list subkey type, or a bad enum value
— collecting every finding into one error rather than warning and guessing.
CLI call sites surface this as `click.UsageError` (exit 64), not
`ClickException`, so a bad config reliably fails loud rather than silently
running with a typo'd key ignored. This removes the motivation for `doctor`/
`config`/`init` (D4): there is no permissive-loader failure mode left for
`doctor` to diagnose, and a strict loader's own error message *is* the
scaffolding guidance.

### D9. One shared `--dry-run` model across `dump`/`compare`/`scan`/`deps tree`/`deps compare`

A single `abicheck/dry_run.py` (`DryRunResult`, `emit_dry_run`,
`reject_dry_run_with_output`, `tool_status`) replaces command-by-command
hand-built dry-run strings (and folds `scan`'s deleted `--estimate` flag, D5).
Contract, identical across all five commands:

- Cheap, read-only resolution only: classify inputs, resolve config/CLI
  precedence, check tool availability on `PATH`, count candidate translation
  units when doing so is itself cheap. **Never** invokes a compiler/frontend,
  never runs a build-system query, never touches the network, never writes a
  file (a cache, an output file, anything).
- Rejects `-o/--output` outright (`click.UsageError`, exit 64) — a dry run by
  definition produces no output to write.
- Exit code is only ever `0` (ok), `1` (blocked — the requested analysis
  cannot be satisfied operationally, e.g. an explicit depth with no usable
  evidence for it), or `64` (usage error). **Never** a compatibility verdict
  code (`2`/`4`) — a dry run makes no comparison, so it cannot have an opinion
  on compatibility.
- Deterministic: the same invocation renders byte-identical output twice.

### D10. MCP and service-API vocabulary follow the same cuts

`abicheck/mcp_server.py`'s tools are updated to the same contract as their
CLI counterparts, per the D6 leak-boundary rule:

- `abi_scan`'s `baseline` param is renamed `against`; `abi_estimate`/
  `abi_scan` drop their `mode`/`source_method` params entirely (depth is
  inferred, or pinned via `depth` restricted to the four D6 rungs — validated
  with the same rejection message shape the CLI gives).
- `abi_compare` gains `used_by`/`required_symbols` params mirroring D2,
  including the same mutual-exclusivity check and scoped-verdict exit-code
  override.
- `MCP_CLI_NAME_MAP` (ADR-037 D10.3) gets rows for the two new params so the
  CLI↔MCP parity gate keeps catching drift.
- The **internal** `ScanRequest`/`parse_user_depth` Python service API (used
  by mode-preset-driven programmatic callers, e.g. resolving `pr-deep` to the
  internal `GRAPH` depth) intentionally stays permissive — it still accepts
  the historical `symbols` alias and the internal `full`/`graph` values. D6's
  strictness is a CLI-*parameter*-layer rule (`cli_params.DepthParam`), not a
  blanket rule on every Python entry point; the MCP tool surface is where the
  leak-boundary is actually enforced for external callers.

### D11. `deps compare` renames `--baseline`/`--candidate` to `--old-root`/`--new-root`

Purely a naming-consistency fix — `--baseline`/`--candidate` collided in
spelling (though not in meaning) with `scan`'s own now-removed `--baseline`
flag (D5) and with the deleted `baseline` registry command (D4). The
internal `check_stack(..., baseline_root=, candidate_root=, ...)` API keeps
its original parameter names; only the CLI-facing flag spelling changes.

### D12. A CI gate makes CLI-surface drift visible on every PR

A new `.github/workflows/cli-interface-check.yml` workflow diffs the CLI
surface (`scripts/dump_cli_surface.py` introspects every registered
command/subcommand's options and arguments into JSON;
`scripts/diff_cli_surface.py` diffs two such dumps) between a PR's base and
head commit, using **separate, non-editable-install virtualenvs** per
checkout (via `git worktree`) so neither side's import machinery can shadow
the other's. When the surface differs, the workflow labels the PR and posts
(or updates) a sticky comment summarizing exactly what changed — command
additions/removals, option additions/removals/type changes — so a reviewer
cannot miss that a PR touches the user-facing interaction surface. The label
is removed automatically if a later push in the same PR reverts the surface
back to parity with base.

### D13. Add `aggregate` — a report-level multi-target fan-in gate

*(Amendment, 2026-07-18.)* The five-verb surface of D1 covers *analysing one
artifact pair*. It does not
cover the multi-platform CI shape the GitHub Action recipes document: a build
matrix fans out, each leg runs its own `compare`/`scan` and uploads a per-target
JSON report, and a downstream job must fold those reports into one gate. Before
this amendment that fan-in was a hand-written shell heredoc in the docs — and
that heredoc had a latent, dangerous bug: its `for path in glob('*.json')` loop
silently dropped any target whose build failed before uploading a report, so a
matrix that never analysed Windows still passed green as "all platforms
compatible". An unavailable target is *unknown*, not an empty (compatible) ABI;
a shell loop over whatever files happen to be present cannot express that.

`aggregate reports/` is added as the **sixth** root verb to own that fan-in:

- **It clears the ADR-037 D7 bar rather than folding into `compare`.** D2 folded
  `appcompat`/`plugin-check` into `compare` because they ask `compare`'s exact
  question over a scoped diff. `aggregate` does *not*: its operand is a
  *directory of already-produced reports*, not a binary/snapshot pair, and its
  question is "did every target the matrix was supposed to build actually report,
  and do their combined gate decisions pass?" That is a different question over a
  fundamentally different operand shape — the D7 test for a genuinely new verb.
  It analyses nothing itself; it reconciles.
- **Three axes stay orthogonal, per ADR-042.** `aggregate` keeps **compatibility**
  (worst verdict, for reporting), **gate** (each report's own recorded
  `severity` decision, *combined* — never recomputed from the verdict, so a
  policy-blocked `COMPATIBLE` still fails and a demoted `BREAKING` can pass), and
  **coverage** (did every required target report?) as separate conclusions. A
  required-coverage gap is a *coverage* failure at exit `1`; it is never
  promoted to an ABI-break exit `4`. Reports with no `severity` block (produced
  without a `--severity-*` policy) fall back to the legacy verdict→exit mapping.
- **The expected-target set is first-class and explicit.** One of `--manifest`
  (a committed `{"targets": [...]}` file, the recommended single source of truth
  fed to both the matrix and the gate), `--expect`/`--optional`, or an explicit
  `--discovered-only` opt-out is **required** — a bare `aggregate reports/` is a
  usage error, because with no declared target set the gate cannot tell a
  missing required target from an intentionally absent one. Duplicate target ids
  and malformed manifests are hard usage errors (exit 64), never silent drops.
- **Exit scheme:** `0` pass / `1` coverage gap or an addition/quality-only gate
  block / `2` source-API break / `4` ABI break / `64` usage. The `--format json`
  output is versioned (`aggregate_schema_version`) with the three axes kept
  under separate `gate`/`coverage`/`compatibility` keys.

`aggregate` is registered via the same sibling-module pattern as the other
split-out commands (`abicheck/cli_aggregate.py`, imported for side-effect at the
tail of `cli.py`); the core reconciliation logic is a front-end-independent
`abicheck/aggregate.py`. The D12 CLI-surface gate and the D1 root-surface
behaviour tests (`tests/test_cli_root_surface.py`,
`tests/test_cli_surface_diff.py`) are updated to expect the sixth verb, so the
surface change is recorded rather than silent.

---

## Non-goals

Explicitly out of scope for this reset (recorded so a future PR does not
"helpfully" redo work this ADR deliberately avoided):

- **Renaming `dump`.** It is not part of the appcompat/plugin-check fold and
  was not judged to need one.
- **Recreating a baseline registry**, in any form, as a replacement for the
  deleted `baseline` command group. A stored JSON snapshot plus `--against`
  is the intended shape going forward.
- **New generic `consumer`/`evidence`/`adapter`/`report`/`doctor`/`config`
  command groups.** The whole point of D1/D4 is a flatter five-command
  surface; introducing a new umbrella group to "organize" the deleted
  commands would silently re-grow the surface this ADR cuts.
- **Preserving any CLI compatibility alias** for a deleted command or flag.
  Unlike ADR-037's softer "deprecation window, switched on at 1.0" mechanism,
  this reset is a hard, immediate cut — we are still pre-1.0 and judged one
  clean breaking pass cheaper than a deprecate-then-remove cycle for this
  much surface at once.
- **Merging `deps` into `compare`.** `deps tree`/`deps compare` answer a
  structurally different question (dependency-stack loadability across
  sysroots, not a single library's ABI surface) and keep their own subcommand
  group.

---

## Known limitations (carried forward, not fixed by this ADR)

- `.abicheck.yml`'s `source.method` config key (the `s0`-`s6` vocabulary,
  ADR-037 D4) was **not** removed from the config schema, despite D6's
  general "must not leak into config schema" principle. It is a pre-existing
  power-user escape hatch (ADR-037 D4's own carve-out) and removing it was
  judged out of scope for this pass; flagged here for a future ADR to
  reconsider explicitly rather than silently left as an inconsistency.
- Package/directory `compare` fan-out does not automatically ingest Debian
  `.symbols` package metadata now that the standalone `debian-symbols`
  command is gone (D4) — the library adapter (`abicheck/debian_symbols.py`)
  is unchanged and callable directly, but nothing in `compare`'s automatic
  dispatch reaches for it yet.
- The L5 source graph's diff/localization functions
  (`diff_source_graph`/`localize_symbol`, ADR-031) remain library-only calls;
  this reset did not add automatic graph-diff surfacing into `compare`'s or
  `scan`'s own report output. They were reachable only via the now-deleted
  `graph compare`/`graph explain` commands before this ADR and are reachable
  only via direct library calls after it — a lateral move, not a regression,
  but noted since a future ADR may want to resurface them through `scan`'s
  report instead of a dedicated command.
- Neither `compare`'s nor `dump`'s JSON/markdown report currently carries the
  resolved `--depth`/source-scope as report metadata (unlike `scan`, whose
  per-layer coverage rows already state the resolved level). A consumer
  parsing a `compare`/`dump` report cannot currently recover "what depth was
  this run at" from the report itself, only from the invocation. Left as a
  follow-up rather than folded into this reset, since it touches the
  reporter's output schema rather than the CLI surface this ADR is scoped to.

---

## Consequences

**Positive**

- The public command surface is small enough to hold in your head: five
  verbs, no "which of these three similar commands do I want" decision.
- `compare --used-by`/`--required-symbol` reuse one diff instead of
  re-dumping/re-comparing per app/contract — scoping N apps against the same
  old/new pair is now O(1) comparisons + O(N) cheap filters, not O(N)
  comparisons.
- The zero-TU scan defect (D7) is fixed at its root (the shared
  `level_to_collect_mode` helper) rather than patched per call site, and the
  fix reaches the MCP/service-API path that the CLI-only version of this fix
  would have missed.
- A strict config loader (D8) turns a silent typo into an immediate, precise
  error — the failure mode `doctor` used to exist to diagnose no longer
  occurs.
- The dry-run contract (D9) is enforceable and testable once, not five times
  with five slightly different shapes.
- The CLI-interface-check gate (D12) makes "this PR changes what users type"
  an unmissable, automatically-labeled fact instead of something a reviewer
  has to notice by reading a diff of `cli.py`.

**Negative / cost**

- Every invocation of a deleted command breaks with no migration shim —
  intentional (see Non-goals) but a real cost for any existing script/CI
  workflow calling `appcompat`, `plugin-check`, `baseline ...`, `collect`,
  `merge`, or the other deleted commands. The GitHub Action's own `run.sh`
  needed the equivalent dispatch-mode removal to avoid becoming an
  in-repo example of calling a command that no longer exists.
- `compare`'s help text and option count grow slightly (the `--used-by`/
  `--required-symbol(s)` family) to absorb what two separate commands used to
  carry, trading "five commands" for "one command with a few more scoping
  flags" — judged the right side of the ADR-037 D7 bar once the underlying
  operation was recognized as identical.
- The internal-vs-public depth/source-method split (D6/D10) is a rule that
  has to be remembered by anyone touching `scan_levels.py` or
  `cli_params.py` — `parse_user_depth` (permissive, internal) and
  `DepthParam.convert` (strict, CLI-only) look similar enough to conflate by
  mistake. Documented at both call sites and here.

---

## Relationship to existing ADRs

- **ADR-037** is the direct predecessor. D2 reverses ADR-037 D7's "appcompat
  stays separate" call once the shared-diff refactor showed the operations
  are identical modulo scoping; D6 extends ADR-037 D6's "graph is derived,
  not a rung" reasoning one step further to collapse `full` into `source`;
  D9 is the dry-run/estimate unification ADR-037 D2's "options are data"
  discipline implied but did not itself deliver.
- **ADR-022** (Baseline Registry) is effectively superseded by D4 — the
  registry command group is deleted with no replacement command, in favor of
  plain snapshot files and `scan --against`.
- **ADR-035** (D3/D7/D10) supplied the `ScanMode`/`SourceMethod`/
  `EvidenceDepth` model and `service.run_scan`/`ScanRequest` that D6/D7/D10
  build on; this ADR narrows what of that model is *user-facing* without
  changing the underlying engine.
- **ADR-005** (plugin bidirectional contract, if present) and the deleted
  `plugin-check` command's host-contract semantics are preserved verbatim
  under D2's `scope_diff_to_required_symbols` — no behavior change, only a
  relocation.
- **ADR-042** (compatibility-verdict / CI-gate separation) governs D13's
  fan-in: `aggregate` combines each report's *recorded* gate decision and never
  re-derives a gate from the compatibility verdict, extending ADR-042's
  single-report rule to the multi-report aggregate.

## References

- `scripts/dump_cli_surface.py`, `scripts/diff_cli_surface.py`,
  `.github/workflows/cli-interface-check.yml` — the D12 CI gate.
- `abicheck/dry_run.py` — the D9 shared model.
- `abicheck/buildsource/scan_levels.py` (`SourceScope`,
  `level_to_collect_mode`) — the D6/D7 depth/scope model.
- `abicheck/appcompat.py` (`scope_diff_to_app`,
  `scope_diff_to_required_symbols`) — the D2 shared-diff scoping core.
- `tests/test_cli_root_surface.py`, `tests/test_dry_run_contract.py`,
  `tests/test_depth_vocabulary.py` — behavior tests for D1/D6/D9.
- `abicheck/aggregate.py`, `abicheck/cli_aggregate.py`,
  `tests/test_aggregate.py` — the D13 fan-in gate core, CLI, and tests.
