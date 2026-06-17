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
| `abicheck/policy_file.py` | new `.abicheck.yml` blocks: `severity`, `scope`, `suppression`, `source`, `exit_code_scheme`, `version` | 5,7 |
| `scripts/check_ai_readiness.py` | `cli-contract` check (D10.1–5) | 1,2,5 |
| `tests/test_cli_contract.py` *(new)* | the gate's unit-test mirror + option-count snapshot | 1,2,5 |

## Phases

Each phase = one PR. Sub-structure: **Work** · **Tests** · **Risk & rollback**
· **Done-when**.

### Phase 1 — Typed requests + single chokepoint (D1, D2)

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

**Work.** Define the 7 decorators in `cli_options.py` (ADR-037 D3 table).
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

**Work.** Extend `.abicheck.yml` (in `policy_file.py`): `severity:`
(per-category), `scope:` (FP-tuning, public-surface list), `suppression:`
(strict/justification policy), `source:` (precise S-axis, graph detail),
`exit_code_scheme:`. Demote the corresponding flags to config; CLI keeps coarse
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

This is a docs-only PR; G22 stays `planned` and ADR-037 stays `Proposed` until
the work below merges. The gap is considered closed only once registry
`UC-WF-cli-contract` can flip to `complete` with evidence pointing at
`api_types.py`, the `cli-contract` gate, `tests/test_cli_contract.py`, and the
alias/round-trip tests — at which point ADR-037 moves to Accepted — implemented.
