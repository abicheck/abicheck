# G22 — CLI consolidation & interface-contract enforcement

**Type:** Refactor / UX plan. Implements [ADR-037](../adr/037-cli-interface-contract.md).
Tracked by `usecase-registry.yaml` entry `UC-WF-cli-contract` (gap G22).
**Effort:** L (touches every `cli_*.py`, `service.py`, `mcp_server.py`,
`cli_options.py`, plus a new CI gate) · **Risk:** medium — behaviour-preserving
for the common path, but folds commands and moves settings to config.
**Builds on:** G21 (depth dial, one-shot `deep-compare`), ADR-035 (typed
requests, `.abicheck.yml`), ADR-036 (report view-model).

## Problem

The CLI exposes the internal pipeline (~394 options / 31 commands). Five
verdict-emitting commands differ only by operand yet re-declare option families
inline; `compare-release` bypasses `service.run_compare` and classifies with a
different `scope_public` default; the evidence dial has three vocabularies;
exit-code meaning is inferred from flag presence. See ADR-037 §Context for the
full audit.

## Goal & acceptance criteria

1. Every verdict-emitting front-end command routes through a `service.run_*`
   verb. **AC:** new `cli-contract` CI check (ADR-037 D10.1) passes; no
   `cli*.py` *calls* `checker.compare`/`diff_*` orchestration directly (type
   imports for annotations/rendering remain legal).
2. Shared option families exist once as decorators. **AC:** D10.2 coverage check
   passes; `INTENTIONAL_SUBSET` allowlist holds every deliberate exception with
   a reason.
3. One depth vocabulary. **AC:** `--collect-mode`/`--mode`/`--source-method`
   resolve as deprecated aliases of `--depth`; `--help` shows one dial; `graph`
   is no longer a user-facing depth (D6).
4. `compare-release` and `deep-compare` are deprecated aliases; `compare`
   accepts dir/package inputs and `--max`. **AC:** existing
   `compare-release`/`deep-compare` tests pass against the alias; new tests
   cover `compare <dir> <dir>`.
5. `--header-backend` → `--ast-frontend` (header AST + source-ABI), old name
   aliased. **AC:** alias test; `--ast-frontend android` without `--sources`
   errors.
6. Per-category severity, FP-tuning, suppression hygiene, and precise S-axis
   move to `.abicheck.yml`; CLI keeps the coarse overrides only. **AC:**
   config round-trips; CLI flag override beats config; per-command flag count
   under budget (D10.5).
7. One explicit exit-code scheme (`--exit-code-scheme`, default `auto`). **AC:**
   passing `--severity-*` no longer silently changes the scheme.
8. Validation is fail-fast and lives in Tier 2. **AC:** identical error text
   from CLI and MCP for the same bad request.
9. MCP params and CLI flags share one name map. **AC:** D10.3 completeness test.

Non-goals: changing detection/classification logic; the ABICC `compat` dialect
(it intentionally mirrors abi-compliance-checker spelling — it routes through
Tier 2 but keeps its own flags).

## Files & surfaces

What each module gains/loses. "T2" = Tier 2 (service), "FE" = front-end.

| Module | Change | Phase |
|--------|--------|-------|
| `abicheck/api_types.py` *(new)* | `InputSpec`, `CompareRequest` (+`validate()`), `OutputSpec`, re-export `AnalysisDepth` | 1 |
| `abicheck/service.py` (T2) | `run_compare(req: CompareRequest)`; old-kwargs shim; `resolve_input` already exists | 1 |
| `abicheck/cli_options.py` | the 7 decorators (D3); `INTENTIONAL_SUBSET`, `DEPRECATED_FLAGS`, `MCP_CLI_NAME_MAP` tables | 2,3,6,7 |
| `abicheck/cli.py` (FE) | `compare` recomposed onto decorators; input-type dispatch; `--exit-code-scheme` | 2,4,5 |
| `abicheck/cli_compare_release.py` (FE) | `_run_compare_pair` → `service.run_compare`; command → deprecated alias | 1,4 |
| `abicheck/cli_max.py` (FE) | `deep-compare` → deprecated alias of `compare --max` | 4 |
| `abicheck/cli_appcompat.py` (FE) | route through `service.run_compare`; adopt shared decorators | 1,2 |
| `abicheck/cli_scan.py` (FE) | `--mode`/`--source-method` → aliases of `--depth` | 3 |
| `abicheck/mcp_server.py` (FE) | params aligned to `MCP_CLI_NAME_MAP`; build `CompareRequest` | 6 |
| `abicheck/dumper.py` / dump cmd | `--header-backend` → `--ast-frontend`; `@evidence_options` | 6,3 |
| `abicheck/buildsource/inline.py` (`load_build_config`/`BuildConfig`) | new `.abicheck.yml` blocks: `severity`, `scope`, `suppression`, `source`, `exit_code_scheme`, `version` — wired into `policy_file.py`/`severity.py`/`suppression.py` loaders | 5,7 |
| `scripts/check_ai_readiness.py` | `cli-contract` check (D10.1–5) | 1,2,5 |
| `tests/test_cli_contract.py` *(new)* | the gate's unit-test mirror + option-count snapshot | 1,2,5 |

## Phases

Each phase = one PR. Sub-structure: **Work** · **Tests** · **Risk & rollback**
· **Done-when**.

### Phase 1 — Typed requests + single chokepoint (D1, D2)

**Status: landed.** `abicheck/api_types.py` ships `InputSpec`/`CompareRequest`
(`validate()`)/`OutputSpec`; `service.run_compare` is a kwargs shim over
`run_compare_request(CompareRequest)`, and the new Tier-2 `service.compare_snapshots`
wraps `checker.compare` so every front-end (`compare`, `compare-release`,
`scan`, `appcompat`) routes through Tier 2 — no `cli*.py` calls `checker.compare`
directly. Enforced by the `cli-contract` AI-readiness check (D10.1) and mirrored
in `tests/test_cli_contract.py`; `tests/test_api_types.py` covers the request
struct. The remaining phases (2–7) are still open.

**Work.** Add `api_types.py` (`InputSpec`, `CompareRequest`+`validate()`,
`OutputSpec`); struct fields via `field(default_factory=...)` (a frozen
dataclass still shares one import-time default otherwise). Refactor
`service.run_compare` to take a `CompareRequest`; keep a thin `**kwargs` shim
that builds the request internally so existing callers compile during the phase.
Re-point `cli_compare_release._run_compare_pair` and `cli_appcompat` at
`service.run_compare` (this alone kills the `scope_public` default drift). Add
the `cli-contract` D10.1 check (no Tier-1 *call sites* in `cli*.py`; type
imports stay legal).

**Tests.** `tests/test_cli_contract.py::test_no_tier_skip`; a parity test
asserting `compare` and `compare-release` now yield the *same* `DiffResult` for
one pair (the drift regression); `CompareRequest` round-trip + frozen/default
unit tests.

**Risk & rollback.** Low — internal refactor, no user-visible flag change. The
kwargs shim means a partial landing still runs; rollback = revert the FE
re-pointing, keep the dataclass.

**Done-when.** Drift parity test green; `cli-contract` D10.1 active; no behaviour
change observable from the CLI.

### Phase 2 — Decorator-ize shared families (D3)

**Status: landed.** `cli_options.py` now defines six shared families as
decorators — `two_sided_input_options`, `policy_options`, `severity_options`,
`scope_options`, `debug_resolution_options`, and the `output_options` factory
(per-command `--format` choices vary legitimately) — plus the contract tables
(`FAMILY_FLAGS`, `FAMILY_DECORATOR`, `REQUIRED_FAMILIES`,
`VERDICT_EMITTING_COMMANDS`, `INTENTIONAL_SUBSET`, `DEFERRED_MULTI_DEFAULT`).
`compare`, `compare-release`, `appcompat`, and `deep-compare` are recomposed onto
them with their inline duplicates deleted (behaviour-preserving; help text is now
uniform, and the divergences are closed — `deep-compare`'s `-H` no longer needs
the file to exist at parse, matching its siblings). `deep-compare` keeps only the
coarse `--severity-preset` and is the sole `INTENTIONAL_SUBSET` entry. The
`cli-contract` gate gained D10.2 (decorator coverage) and D10.4
(one-default-per-flag), mirrored in `tests/test_cli_contract.py` along with the
per-command option-set snapshot and the gate↔`cli_options` table-mirror test.

The seventh decorator, **`@evidence_options` (`--depth`/`--collect-mode`/
`--sources`/`--build-info`), is deferred to Phase 3** where the depth vocabulary
is unified: the existing `build_source_compare_options`/`build_source_dump_options`
helpers stay as-is for now, and their `--collect-mode` double-default (the very
case D10.4 exists to catch) is parked on the `DEFERRED_MULTI_DEFAULT` allowlist
with a pointer to Phase 3 rather than hidden. `dump`/`scan` are not in the
verdict-emitting set, so their recompose rides along with the depth work in
Phase 3.

**Original plan.** Define the 7 decorators in `cli_options.py` (ADR-037 D3 table).
Recompose `compare`, `compare-release`, `appcompat`, `deep-compare`, `dump`,
`scan` onto them, deleting inline duplicates. Seed `INTENTIONAL_SUBSET` with any
deliberate omission (each with a reason string). Add D10.2 (decorator coverage)
+ D10.4 (one-default-per-flag) checks.

**Tests.** `test_decorator_coverage` (every verdict-emitting command carries the
required decorators or is allowlisted); `test_one_default_per_flag`; a snapshot
of each command's resolved option set so an accidental drop is caught in review.

**Risk & rollback.** Low/medium — mechanical, but Click decorator order affects
`--help` grouping; pin order in the decorator definitions. This phase fixes the
§Context divergences and is independently shippable even if 3–7 slip.

**Done-when.** Divergence matrix (ADR §Context #2) is empty; `appcompat` now has
`--strict-suppressions`, `compare-release` has `--ast-frontend`/`--demangle`,
debug-resolution is uniform — all *via the shared decorator*, not copies.

### Phase 3 — Depth vocabulary + L5-internal (D5, D6)

**Status: landed.** One user-facing dial `--depth {symbols,headers,build,source,full}`
now spans `compare`/`deep-compare`/`dump`/`scan` via a shared `DepthParam`
(`cli_params.py`): it adds `symbols` (L0/L1, suppresses the L2 AST), drops `graph`
as a user rung, and resolves the deprecated `--depth graph` → `source` with a
stderr note. `compare` gained `--depth`/`--max` (folded into the collect mode the
same way `dump`/`deep-compare` do). `--collect-mode` is now a hidden deprecated
alias that warns when used. The L5 graph stays internal — built automatically at
`--depth source` — and `EvidenceDepth.GRAPH` survives only for scan's `pr-deep`
preset (determinism). A new `.abicheck.yml` `sources.graph: summary|full` knob
(default `summary`, `BuildConfig.graph_detail`) caps/deepens the graph replay
scope (`effective_graph_scope`). The deprecated spellings are catalogued in
`cli_options.DEPRECATED_FLAGS` (the window-enforcing resolver lands in Phase 7).
Covered by `tests/test_depth_vocabulary.py` (alias resolution, monotone ladder,
graph-at-source, symbols-only, config knob). `--source-method`/`--mode` on `scan`
are unchanged — their move to config is Phase 5 (D4), not D5.

**Work.** `--depth {symbols,headers,build,source,full}` + `--max` on
`@evidence_options` (incl. per-side `--old/new-sources`, `--old/new-build-info`).
Map deprecated `--collect-mode`/`--mode`/standalone `--source-method` **and the
G21 `--depth graph` value** into `DEPRECATED_FLAGS` (`graph` → `source`). Make
the L5 graph an internal consequence of `--depth source`; delete the `graph-*`
rungs; add the `source.graph: summary|full` config knob (default `summary`).

**Tests.** `test_depth_alias_resolution` (every old spelling → the right
`AnalysisDepth`, incl. `graph`→`source`); `test_depth_monotone` (each rung is a
superset of the one below); `test_graph_built_at_source_depth` (no user mode
needed).

**Risk & rollback.** Medium — user-visible vocabulary change, mitigated by
aliases + stderr deprecation notes. Rollback = keep aliases as the primary
spelling.

**Done-when.** `--help` shows one dial; all three legacy vocabularies resolve as
aliases; `graph` no longer appears as a user-facing depth.

### Phase 4 — Command consolidation (D7)

**Status: landed.** `compare` now classifies each operand
(`classify_compare_operand` in `cli_resolve.py`) as file / directory / package /
app — snapshots ride the `file` path — and dispatches: a directory or package operand fans out to the
per-library release comparison (`cli._dispatch_release_compare` → the existing
`compare-release` engine through the single Tier-2 `service.run_compare`
chokepoint, so a library gets the identical verdict from `compare` and
`compare-release`); an application/PIE operand (`_looks_like_application`, a
positive ET_EXEC / PIE-with-PT_INTERP-and-non-`.so`-name test — never a guess) is
rejected with a hint at `appcompat`. The set-input fan-out flags (`-j/--jobs`,
`--dso-only`, `--output-dir`) ride a shared `set_input_options` decorator and
no-op-with-warning on single files. `compare-release` and `deep-compare` keep
working as thin deprecated aliases that emit a stderr note (suppressed for
machine formats and when `compare-release` runs as the fan-out backend). Covered
by `tests/test_compare_dispatch.py` (classifier, file/dir dispatch, app
rejection, `compare <dir> <dir>` == `compare-release <dir> <dir>` JSON parity,
`--output-dir` fan-out, alias smoke).

**Work.** `compare` input-type dispatch: file / snapshot / directory / package /
(app → actionable hint to `appcompat`). Disambiguate `ET_DYN` PIE executables
from `.so` (ELF type alone is insufficient — fall back to `DT_SONAME` presence
and require an explicit operand kind when still ambiguous, never guess). Move
set-only flags (`-j/--jobs`, `--dso-only`, `--output-dir`, bundle opts) under
the dispatch, no-op-with-warning on single-file inputs. Preserve the
two-level output for set inputs (summary on stdout/`-o`, per-library reports
under `--output-dir`). Turn `compare-release` and `deep-compare` into thin
deprecated aliases. `appcompat`/`plugin-check` stay distinct verbs.

**Tests.** `test_compare_dispatch_*` (file, snapshot, dir, package, ambiguous
PIE → error); `test_release_fanout` (summary + per-lib reports match the old
`compare-release` output); alias smoke tests for the two folded commands.

**Risk & rollback.** Medium/high — the dispatch is the most behaviour-bearing
change. Rollback = keep `compare-release`/`deep-compare` as real commands (the
aliases already point at the same code, so reverting is cosmetic).

**Done-when.** `compare <dir> <dir>` reproduces a known `compare-release` run
byte-for-byte on the summary; ambiguous-binary inputs error with guidance.

### Phase 5 — CLI↔config rebalance (D4)

**Status: landed.** `BuildConfig` (`buildsource/inline.py`) gained the
project-contract blocks `severity:` (preset + per-category), `scope:`
(`public`/`collapse_versioned_symbols`/`public_symbols`), `suppression:`
(`strict`/`require_justification`), `source:` (`method`, the precise S-axis),
plus the top-level `exit_code_scheme:` and `version:` — all validated, with a
`to_dict()` that round-trips through `from_dict`. `compare` auto-discovers the
nearest `.abicheck.yml` (`discover_project_config`, overridable with `--config`)
and merges CLI flags over it through one pure resolver
(`resolve_compare_config` → `ResolvedCompareConfig`) with precedence **CLI >
config > built-in default**. The demoted families (per-category severity, scope
FP-tuning, suppression hygiene) stay on the CLI as **hidden** overrides — still
functional for a one-off run, off the visible surface. The exit-code scheme is
now explicit (`--exit-code-scheme {auto,legacy,severity}`, D12): `auto` resolves
to severity when a severity setting is in effect (CLI *or* config) else legacy,
so passing `--severity-*` no longer silently flips an explicitly-pinned scheme.
The D10.5 budget is a `COMPARE_FLAG_BUDGET` constant + `count_visible_options`
(WARN nudge). Covered by `tests/test_config_rebalance.py` (per-key precedence,
dataclass/YAML round-trip, flag budget + hidden/visible split, explicit
exit-scheme, config-driven exit scheme and severity).

**Work.** Extend `.abicheck.yml` — the loader is `buildsource/inline.py`
(`load_build_config`/`BuildConfig`; `risk_rules`/`crosschecks` already live in
`risk.py`/`crosscheck.py`). Add `severity:` (per-category), `scope:` (FP-tuning,
public-surface list), `suppression:` (strict/justification policy), `source:`
(precise S-axis, graph detail), `exit_code_scheme:`, and wire each into the
existing `policy_file.py`/`severity.py`/`suppression.py` loaders. Demote the
corresponding flags to config; CLI keeps coarse
overrides (`--severity-preset`, `--show-filtered`, `--depth`,
`--exit-code-scheme`). Precedence: **CLI > config > built-in default** — one
resolver, tested. Add `--exit-code-scheme` (D12) and the D10.5 flag-count budget
(WARN).

**Tests.** `test_config_precedence` (CLI beats config beats default, per key);
`test_config_roundtrip` (load→dump→load stable); `test_flag_budget` (compare ≤
budget); `test_exit_scheme_explicit` (`--severity-*` no longer flips the scheme).

**Risk & rollback.** High — biggest UX shift; a project without config must keep
working on built-in defaults. Land *after* 1–4 so the structure is stable.
Rollback = re-expose the demoted flags (they still map to the same request
fields).

**Done-when.** A project runs `abicheck compare old new` with everything else in
`.abicheck.yml`; flag-count budget passes; precedence test green.

### Phase 6 — `--ast-frontend`, MCP name-map, validation, docs (D8, D9, D10.3)

**Status: partially landed.** `--header-backend` → **`--ast-frontend`** (D8) on
`compare`/`deep-compare`/`dump`, with `--old/new-ast-frontend` per-side and the
`--header-backend` spellings kept as working aliases on the same Click params;
the env knob is now `ABICHECK_AST_FRONTEND` (legacy `ABICHECK_HEADER_BACKEND`
honored as a fallback in `dumper._resolve_header_backend`). Each command body
prints a one-line stderr deprecation note when a legacy spelling is used
(`cli_options.note_deprecated_ast_frontend`, advisory until 1.0); the renamed
flags are catalogued in `DEPRECATED_FLAGS`. The single **`MCP_CLI_NAME_MAP`**
(D10.3) reconciles every `abi_compare` MCP param with its `compare` flag and is
enforced by a new `cli-contract` sub-check (`_check_mcp_cli_name_map`) plus the
live `tests/test_cli_contract.py::test_mcp_cli_name_map_complete`. `CompareRequest`
gained a `frontend` field and `validate()` now rejects an out-of-enum
`--ast-frontend` (with the allowed set) and an `android` frontend without source
inputs (D9), threaded through `service.run_compare`. Covered by
`tests/test_api_types.py` (frontend enum + android-needs-sources) and the new
`tests/test_cli_contract.py` D8/D10.3 cases.

**Deferred within Phase 6:** unifying the L4 source-ABI extractor selection
under the same `--ast-frontend` dial (the ADR's "spans header AST + L4 replay"
note) — the inline-collection chain (`prepare_embedded_build_source` →
`diff_embedded_build_source` → `collect_inline_pack`) still hard-defaults its
extractor, and `collect`'s explicit `--source-abi-extractor` is unchanged. The
full mutual-exclusion / depth-feasibility matrix in `validate()` and the
mechanical doc regen (`cli-flags.md`, the `.abicheck.yml` schema page) also
remain. These are the open items before Phase 6 can be marked landed.

**Work.** Rename `--header-backend` → `--ast-frontend` (+ `ABICHECK_AST_FRONTEND`
env, + old aliases); wire it to the L4 extractor selection too. Introduce the
single `MCP_CLI_NAME_MAP` and align `mcp_server.py` params to it (D10.3 check).
Flesh out `CompareRequest.validate()`: mutually-exclusive flags, enum values,
depth feasibility, `--ast-frontend android` without `--sources` (D9). Regenerate
`docs/user-guide/cli-flags.md` from `--help`, update
`docs/reference/exit-codes.md`, add the `.abicheck.yml` schema reference page.

**Tests.** `test_ast_frontend_alias`; `test_mcp_cli_name_map_complete` (no param
or flag missing from the map); `test_validate_*` (each rule, asserting identical
CLI/MCP error text per goal AC 8).

**Risk & rollback.** Low/medium — additive plus a rename behind an alias. Doc
regen is mechanical (mkdocs `--strict` is the guard).

**Done-when.** MCP↔CLI name-map test green; validation errors identical across
front-ends; docs build `--strict`.

### Phase 7 — Backward-compat scaffolding (future-enabled)

**Status: landed.** `cli_options` now exposes the single deprecation-window
resolver over the `DEPRECATED_FLAGS` registry: `resolve_deprecated_flag(spelling)`
→ `(replacement, reason)`, `deprecated_flags_in_argv(argv)` (scans for every
deprecated spelling — both flag-level renames and value-level deprecations like
`--depth=graph`, matched as `--depth graph` too), and `note_deprecated_flags(argv)`
(the combined one-line note the 1.0 switch-on will route all front-ends through;
the live per-flag sites stay until then). `.abicheck.yml` is forward-compatible:
`BuildConfig` carries `version:` and `from_dict` **warns** (never errors) on any
unknown top-level or in-block key (`_KNOWN_TOP_KEYS`/`_KNOWN_BLOCK_KEYS`; sibling
`risk_rules`/`crosschecks` are recognized so they don't trip it). The
deprecation-window test stays **advisory** (a pytest table-test, not an
AI-readiness ERROR gate) until 1.0 per ADR-037 §Backward compatibility. Covered
by `tests/test_cli_contract.py` (`test_every_deprecated_flag_resolves`,
`test_deprecated_flags_in_argv`, `test_note_deprecated_flags_combines`) and
`tests/test_config_rebalance.py::TestConfigForwardCompat`.

**Work.** Build the `DEPRECATED_FLAGS` resolver + stderr deprecation notes;
test that every alias in the table still resolves. Add `version:` to
`.abicheck.yml` (unknown keys warn, not error). Keep the deprecation-window test
**advisory** (not ERROR) until 1.0 per ADR-037 §Backward compatibility.

**Tests.** `test_deprecated_flags_resolve` (table-driven); `test_config_version_forward_compat`
(unknown key warns, load succeeds).

**Risk & rollback.** Low — pure scaffolding, no enforcement yet.

**Done-when.** Every deprecated spelling from phases 3–6 resolves with a note;
the 1.0 switch-on is a one-line severity change, documented.

## Sequencing & PR map

- **PR-1 (Phase 1)** and **PR-2 (Phase 2)** are highest-value / lowest-risk and
  land first: they kill the classification drift and the copy-paste without
  user-visible change. Either can merge independently.
- **PR-3 (Phase 3)** and **PR-4 (Phase 4)** are user-visible; both ship behind
  deprecation aliases so no invocation breaks hard in one release. PR-4 depends
  on PR-1 (needs the single chokepoint to fold cleanly).
- **PR-5 (Phase 5)** is the biggest UX shift (flags → config); it depends on
  PR-2 (decorators) + PR-3 (depth) being in so the demoted surface is stable.
- **PR-6** and **PR-7** are additive and can trail; PR-6 depends on PR-1 (the
  request type) and PR-2 (decorators).

Dependency sketch: `1 → {2, 4}`, `{2,3} → 5`, `{1,2} → 6`, `{3..6} → 7`.

## Measurement (proves the headline claims)

The "~62 → ~20 flags" and "no divergence" claims are testable, not aspirational:

- A snapshot test records each command's option count; CI diffs it so a
  regression (a flag sneaking back inline) is visible in review.
- The D10.5 budget gives `compare` a hard ceiling once Phase 5 lands.
- `test_no_tier_skip` + the drift parity test (Phase 1) make "one classifier"
  a runtime guarantee, not a doc promise.

## Definition of done (when implementation lands)

Phases 1–5 have landed (typed `CompareRequest` + single `service` chokepoint;
the shared option-family decorators; the unified `--depth` vocabulary; `compare`
input-type dispatch folding `compare-release`/`deep-compare`; and the
`.abicheck.yml` config rebalance with explicit `--exit-code-scheme`), along with
the `cli-contract` gate and its test mirror. G22 stays **in progress** and
ADR-037 stays `Proposed` until the remaining phases (6–7) merge.
The gap is considered closed only once registry `UC-WF-cli-contract` can flip to
`complete` with evidence pointing at `api_types.py`, the `cli-contract` gate,
`tests/test_cli_contract.py`, and the alias/round-trip tests — at which point
ADR-037 moves to Accepted — implemented.
