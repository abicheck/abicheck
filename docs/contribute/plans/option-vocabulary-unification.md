# Option vocabulary unification: implementation plan

**Status:** Phases 0, 1, 2 done (Phase 0 2026-10-30; Phase 1 is #1850; Phase 2: the CLI `--set` half for `compare`, `dump` and `project capture-variants` (#1851, #1857), the Action `set:` input (#1854), and the compile-context keys replacing the Action's `compile:` overlay (#1861); the typed API still refuses `compile.*`, see the Result below). Phase 4 done for the CLI (#1852), the Action (#1860), the typed request and `run_compare` shim, and the reusable workflows (this change). Phase 4 is done for the typed API. The Tier-1 `checker.compare()` keywords (`contract_evaluation`, `contract_mode`) are an accepted exception (ADR-091 R10). Open review item, not a blocker: `run_dump(public_headers)`. Phase 3 (R11, R15) and Phase 5 (budget) in progress. Phase 6 is collapsed into each rename (D7 amendment). Phase 7 not started. Implements
[ADR-091](../adr/091-one-option-vocabulary.md). Each phase is one PR, ordered
so that the parity gate lands before any rename. Every rename lands with its
migration row and no alias: the 2026-10-30 policy (ADR-091 D7 amendment)
removes the old spelling in the same change (Action inputs become tombstones).

**Problem.** The same option is spelled and behaves differently on the CLI,
in `.abicheck.yml`, in `action.yml` and in the typed Python API (ADR-091's
Context table), and nothing stops new drift. Users meet a new vocabulary at
each [CJM](../cjm.md) stage: CLI at `first_check`, Action at `adopt_ci`,
config at `tune` and `trust`.

**Done means:** every user-settable option is a row in
`architecture/option-vocabulary.yaml`; `tests/test_option_vocabulary.py`
passes with an empty exceptions list; `compare --help` lists at most 15
options and `action.yml` at most 25 analysis inputs; the four reference
pages are generated from or checked against the registry.

## Phase 0 — Inventory and the open semantic questions (done)

- Write `scripts/gen_option_vocabulary.py --inventory`: dump every option from
  click introspection (all commands), `action.yml` inputs, the config schema
  (`gen_config_reference.py`'s source), and `CompareRequest`/`DumpRequest`/
  `InputSpec`/`run_compare` fields, as one JSON table.
- Group the rows into concepts by hand into
  `architecture/option-vocabulary.yaml` (schema below). Every current
  deviation from ADR-091 D1–D3 is recorded in its `exceptions:` list with the
  rename id (R1…R16) that will remove it.
- Answer from code, and record in the ADR:
  - Q4: are `public_surface_allowlist`, `force_public_symbols` and
    `scope.public_symbols` one concept? (`workflows/compare/execute.py`,
    `policy/` scope code.)
  - R13: do config `source:` and `sources:` overlap or conflict?
  - R8: are `DumpRequest.follow_dependencies` and
    `InputSpec.include_dependencies` the same behavior?
  - Q3: what `gcc-prefix` does in `action/run.sh` and whether `compile.*`
    can express it.

Registry entry schema:

```yaml
- concept: scope.exclude_headers   # canonical config path (D1)
  tier: core                       # core | config (D4)
  type: path
  cardinality: list                # scalar | list
  values: null                     # closed value set, or null
  default: []
  sided: true                      # takes old=/new= (D3)
  cli: {compare: --exclude-header, dump: --exclude-header}
  action: exclude-headers          # or none: <reason>
  python: {request: scope.exclude_headers, shim: exclude_headers}
exceptions: []                     # no-growth; each names an R-id
```

Owner: `architecture/`, `scripts/`. Size: S.

**Result (2026-10-30).** Landed as `scripts/gen_option_vocabulary.py`,
`architecture/option-vocabulary-inventory.json`,
`architecture/option-vocabulary.yaml` and `tests/test_option_vocabulary.py`
(inventory fresh; every row claimed exactly once; ids exist; tiers valid).
The registry carries `concept`, `tier`, `renames` and the member ids per
surface. The richer per-concept fields of the schema above (type,
cardinality, default, sidedness, derived names) are Phase 1's job, because
Phase 1 is what checks them. Answers, recorded in ADR-091:

- Q4: `scope.public_symbols` = `force_public_symbols` (a widening overlay);
  `public_surface_allowlist` is a separate, narrowing, typed-API-only concept
  (`committed_surface` in the registry).
- R13: `source:` holds only `method`; no collision with `sources:`.
- R8: `follow_dependencies` is `--follow-deps` on both requests;
  `InputSpec.include_dependencies` is actually `--include-system-declarations`,
  so the rename targets that name instead.
- Q3: `gcc-prefix` is only a fallback for the compiler path, so it folds into
  `compiler`.
- New findings, added to ADR-091 as R17/R18: the Action's numeric sides
  (`debug-info1/2`, `devel-pkg1/2`), `check-target` `requested-depth` and
  `contract-file`, `run_dump(header_backend)`, and `deps tree --sysroot`.
- Sub-actions other than `check-target` are `out_of_scope` patterns, each
  with an expiry: `actions/aggregate` and `actions/collect-facts` carry
  `until: phase-1` (they mirror CLI commands and must be mapped then); baseline
  transport, report publication and run verification are `until: permanent`.
  Phase 0's coverage is therefore the CLI, config, typed API, root Action and
  `check-target`, not every sub-action yet.
- Retired tombstones and ignored inputs sit only in the `retired` tier; the
  test checks both directions and reuses the tombstone contract's own
  "RETIRED in description" rule.

## Phase 1 — Parity gate

- `tests/test_option_vocabulary.py` (marker `repo_scan`): every live option
  maps to a registry row; derived names follow D1 unless excepted; type,
  cardinality, value set and default agree across surfaces; core counts are
  within D4's budget (the budget check starts as an exception carrying
  today's 22 / ~85 counts, no-growth).
- Wire it into `scripts/check_architecture.py` (or `verify.py`'s
  `ai-readiness` step) and add it to `tests/test_verify_profiles.py`'s
  expectations.
- From here, any new option must add a registry row in the same PR.

Owner: `tests/`, `scripts/verify.py`. Size: S.

**Result (2026-10-30).** The gate is `tests/test_option_vocabulary.py`. It
checks the D1 name rule, the D2 closed-value-set rule, the D4 budget (no-growth
against recorded counts) and the exceptions both ways (each names a real row,
a rule and an ADR-091 R-id; a deviation that no longer exists fails as stale).
The derivation is written out in the test, not imported from the generator. The
inventory now carries `type`, `choices`, `default`, `required`, annotations and
`help_common` (the rows plain `--help` shows). Every `until: phase-1` entry is
mapped: `actions/aggregate` and `actions/collect-facts` are claimed by existing
concepts where they mirror a CLI option (`sources`, `compile.compiler`, `output`,
`action.runtime`, `project`) and by three new tier-`action` concepts otherwise.
`aggregate` and `project` opt out of the name rule, as ADR-091's "Out of scope"
section requires; the test enforces that only those two may. A full
block-prefixed spelling (`debug-format`) counts as D1 only when another concept
shares the leaf, as ADR-091 D1 says. The registry records 155 name deviations
(59 Python, 22 Action, 8 CLI, 66 under R1–R18) and
two budget counts: `compare --help` shows 20 core options (ADR-091's 22 counts
the two help flags, which the inventory skips) and the root `action.yml` has 53
analysis inputs. The D1 name rule and the D4 budget can't be satisfied yet, so
the gate shows the gap and fails on any growth. Four rename ids (R19–R22) are
new in ADR-091 D5 for deviations no existing row covers.

Gaps left for later phases: the value-set rule only sees closed sets on both
surfaces, so `contract_mode`'s missing `auto` (R10) is typed `str` in Python and
stays invisible until Phase 4 types it. Defaults and types are extracted but not
compared yet; the plan's default and type checks need their own phase. The gate
rides the existing `repo-scan-tests` step (`-m repo_scan`, `pr` profile and the
CI `ai-readiness` job); the unit lanes exclude that marker, so Phase 0's tests
move with it. No `verify.py` change was needed.

## Phase 2 — `--set` and the Action `set:` input (D4) — done (typed-API compile keys excepted, see Result)

- `compare`, `dump`, `deps *`, `project *`: add repeatable
  `--set PATH=VALUE`, parsed through the same config loader and schema as
  `.abicheck.yml`, applied after the file and before explicit flags
  (ADR-091 D2 precedence). Reject `--set` on a core concept (registry
  lookup) with exit 64 naming the flag.
- Typed API: `CompareRequest`/`DumpRequest` accept a `config_overrides`
  mapping with the same semantics; the shim takes `set=`.
- Action: `set:` input, one `PATH=VALUE` per line, forwarded verbatim as
  `--set` arguments.
- Replace the `compile:` overlay synthesis in `action/run.sh` (the
  `_compile_context_helper_py` block around `run.sh:1393-1560`) with
  `--set compile.*` forwarding. This is ADR-070 D2 progress; record it in
  `plans/action-cli-surface-drift.md`.

Owner: `frontends/`, `workflows/compare/configuration.py`, `action/`.
Size: M. Changelog fragment: Added.

**Result (2026-10-30, partial; dump and capture-variants added).** The CLI and
typed-API half is in for three commands. `compare --set PATH=VALUE` and
`CompareRequest.config_overrides` (typed; `run_compare(config_overrides=)`) were
the first slice. This slice adds `dump --set` (CLI), `DumpRequest.config_overrides`
(typed; `with_dump_config_overrides`, run first in `resolve_dump_request`),
`run_dump(config_overrides=)` and `project capture-variants --set`. All of them
share one validator (`abicheck/buildsource/build_config_overrides.py`, the file's
own `KEY_TABLE`), and each command passes its applied-key set to the one `--set`
parser (`frontends/cli/options/config_set.py`, `config_set_option_for`).
Precedence is explicit flag > override > file > default. Core keys are refused with
exit 64 naming their flag (`CORE_CONFIG_FLAGS`; this includes `scope.exclude_headers`
and `debug.pdb_path`, so no `dump` channel can reach them). Every key a command does
not apply is refused with exit 64, not dropped. The registry claims the four new
spellings under `config.override`; `tests/test_config_overrides_dump_project.py`
checks precedence, refusals and CLI-vs-typed equivalence at the resolved config.

- **dump** applies `compile.lang`, `build.compile_db_filter`, `debug.format`,
  `debug.dwarf_only`, `debug.debuginfod`, `debug.debuginfod_url`, and the CLI-only
  `compile.ast_frontend_fallback`/`compile.allow_unsupported_castxml` toggles
  (the typed API refuses the toggles: they are process-wide env settings). Every
  dump config resolver reads through one loader, `_dump_build_config`. With
  overrides, a named file is read strictly, since a stated value must not be lost
  to the lenient fallback (`DumpConfigError`, exit 64).
- **Not yet applied on dump:** every other `compile.*` key (`compile.std`,
  `compile.include_dirs`, `compile.frontend`, ...), `build.query`, `build.*`,
  `sources.*`. `compile.*` is read by `workflows/compile_config.merge_compile_config`,
  and `--sources`/`--build-info` re-read the file in `buildsource/embed.py` and
  `l2_seed_args.py`; those loaders do not take an override yet. Until they do,
  `--set` on these keys exits 64. This is the largest remaining gap in Phase 2.
- **capture-variants** applies `bundle_variants` (the whole block). Its only config
  read is that block; each variant's dump runs with no project config.
- **project plan takes no `--set`.** The plan's premise was that it reads the config
  through about a dozen loaders. It does not: `project plan` reads only
  `ProjectTargetsConfig` (`targets:`/`profiles:`/`checks:`/`aggregate:`), which no
  `KEY_TABLE` key reaches, so `--set` would refuse every key. The dozen loaders are
  dump's and compare's. Adding a flag that always refuses would be noise, so the
  flag is not added; `tests/test_config_overrides_dump_project.py` pins that
  `project plan` has no `--set`.
- **No `--set`:** `project validate`, `project history`, `deps tree`, `deps compare`
  and `aggregate` read no `KEY_TABLE` key a `--set` can set, and the test pins that
  as well.
- **Typed `run_dump(config_overrides=)`** fills the `run_dump` keyword parameters
  for `compile.lang` and the `debug.*` keys, through the dependency-scope wrapper
  before the live extraction; the cache sits above that wrapper, so it keys on the
  resolved values. It refuses `build.compile_db_filter`, which has no `run_dump`
  parameter.
- **Still open in this phase:** the Action `set:` input and the `action/run.sh`
  `compile:` overlay replacement (not started), and the compile-path loaders above.

**Result (2026-10-30, Action `set:` input, #1854).** The CLI and typed-API half for `compare`
is in: `compare --set PATH=VALUE` (repeatable) and
`CompareRequest.config_overrides` (typed; `run_compare(config_overrides=)`)
share one validator (`abicheck/buildsource/build_config_overrides.py`, the
file's own `KEY_TABLE`) and one loader (`load_build_config`,
`load_build_config_with_digest`, `load_and_resolve_compare_config`). Precedence
is explicit flag > override > file > default; core keys are refused with exit 64
naming the flag (`CORE_CONFIG_FLAGS`, checked against the registry by
`tests/test_config_overrides.py`). The typed override fills only the request fields the caller
left at their default. The stored BundleFacts route refuses `--set` with exit 64.
Still open in this phase: `--set` on `dump` (CLI and `DumpRequest.config_overrides`),
`project plan` and `project capture-variants`, which read the file through about a dozen
loaders that do not yet take an override; `project validate`, `project history`,
`deps tree`, `deps compare` and `aggregate` read no `KEY_TABLE` key and take no `--set`.
The Action `set:` input is in (root `action.yml` and `actions/check-target`):
each non-blank, non-comment line forwards verbatim as `--set` on `mode: compare`
only, and the CLI validates it. An effective `set:` on any other mode fails the
step in `validate-inputs.sh`, because dump has no `--set` yet and an override
must not drop silently. Both spellings are claimed under the generic-override
concept with R23 exceptions. **The `compile:` overlay is not replaced.**
`compare --set` still refuses every `compile.*` key (`compare does not apply
compile.compiler yet`: the typed and CLI-only channels in
`workflows/compare_configuration.py` carry no compile context), so forwarding
them would fail every cross-compiler run. Until the CLI applies them, the
Action still synthesizes the overlay in `add_compile_context_flags` for every
mode (compare and dump), and `actions/check-target` keeps its assurance-overlay
step. The `set:` input is applied after those overlays, so an explicit `set:`
line wins over the overlay's keys once the CLI accepts them.

Update (#1857): `dump` and `project capture-variants` now take `--set` (see the result above). The Action still refuses an effective `set:` outside `mode: compare`; that refusal is unchanged here.

**Result (2026-10-30, compile keys and the Action overlay: Phase 2 done).** The
CLI applies the compile-context keys: `compile.frontend`, `compile.frontend_context`,
`compile.compiler`, `compile.options`, `compile.sysroot`, `compile.nostdinc`,
`compile.std` and `compile.lang`, through `COMPILE_CONTEXT_OVERRIDE_PATHS`
(`workflows/compile_config.py`). `compare --set` (single pair, `--dry-run`, and the
directory/package fan-out), `dump --set` and `merge_compile_config` /
`resolve_compile_context` take `config_overrides` and load the file with the
overrides laid over it (`load_build_config(cfg, config_overrides=...)`), so a key
set by `--set` and the same key in `compile:` resolve to the same
`CompileContext` (`tests/test_compile_context_set_equivalence.py`, per key and
for a user file block). Precedence is unchanged: flag > `--set` > file > default.
A `compile.compiler` set by `--set` is trusted even when the file was
auto-discovered; a discovered file's own `compile.compiler` is still ignored.
`compile.include_dirs` and `compile.defines` stay on their core flags.
The composite Action's `add_compile_context_flags` (`action/run.sh`) forwards
`ast-frontend`, `gcc-path`/`gcc-prefix`, `gcc-options`, `sysroot`, `nostdinc` and
`lang` as `--set compile.*`, after which an explicit `set:` line is appended so it
still wins. The synthesized `compile:` config, its Python helper, the temp-file
merge into `--config`, and the compile-only sources-root helpers are deleted;
`set:` is accepted on `mode: dump`
(`tests/test_action_compile_set_forwarding.py`, argv per input; the retired
overlay-internal test modules are removed). **Still open:** the typed API
(`CompareRequest.config_overrides`, `DumpRequest.config_overrides`) refuses
`compile.*`, because a typed request does not fold `.abicheck.yml`'s `compile:`
block today, so there is no compile resolver for the override to reach. Checked
in code: the typed compare path (`workflows/compare/execute.py`, `resolve.py`)
calls no compile resolver; its `CompileContext` values are supplied by the
caller. **Explicit open item:** applying `compile.*` overrides on the typed API
needs a new fold of `.abicheck.yml`'s `compile:` block into typed requests first,
then the same per-key equivalence test extended to the typed API. That is a
separate change, not part of this phase's CLI and Action work. Phase 2's remaining
gap is that typed-API fold, not the Action. One behavior to verify: a
`--sources` tree's own `compile:` block used to be excluded by the Action on a
pairwise compare with an old side; the CLI's own discovery now decides that case.

## Phase 3 — Behavior unifications (D2)

| R | Change | Files |
|---|---|---|
| R2 | Action `policy`/`policy-file` → one `policy`; Python `policy: str \| Path` | `action.yml`, `action/run.sh`, `workflows/compare/execute.py` |
| R10 | Python `contract` with `{public,exports,all,auto}` | `workflows/compare/session_request.py`, `execute.py` |
| R11 | Action `output` list of `FMT=PATH` | `action.yml`, `action/run.sh`, `action/validate-inputs.sh` |
| R15 | Action `fail-on-*` forwarded as `--set severity.*`, shell gate code removed | `action/run.sh` (~`5268`, `5542`, `5593`) |

Each lands with its old spelling removed (D7 amendment: no alias). Size: M.

**Result (2026-10-30, Python part of R2 and R10).** The typed `policy` takes a
built-in profile name or a policy document and resolves them exactly as the
CLI's `--policy NAME|PATH` (`abicheck/workflows/policy_operand.py`,
`resolve_policy_operand`, the same rule as `PolicyFileParam`). `policy_file_path`
is gone from `CompareRequest` and `run_compare`. `contract` replaces
`contract_evaluation`/`contract_mode` with the CLI's value set, `auto` included
(folded to `None`, as `resolve_contract_domain` does). Contract relevance is
always evaluated, so the opt-out is removed; the release layer refuses
`contract_evaluation=False` with a `ValidationError`. The equivalence and rename
tests are `tests/test_adr091_python_renames.py`. The Action half of R2
(`policy` + `policy-file`) is still open.

## Phase 4 — Renames (D5; no aliases, see the D7 amendment)

One PR per surface keeps review small:

- **CLI:** R1 `--version` → `--label` (`compare`, `dump`, `project history`);
  R12 `--abi3` → `--abi3-floor`; R16 `deps compare --root [old=|new=]` and
  `deps tree --root`.
  Old spellings removed outright (done, see the CLI result below).
- **Config:** R3 `acknowledgment:` → `acknowledgments:`; R13 `source.method` →
  `sources.method`. The old keys are removed outright: the strict schema
  rejects them as unknown keys. `config-keys-reference.md` regenerated.
- **Action:** R4 `frontend`, R5 `compiler`/`compile-options`, R6
  `require-complete`, R7 `public-header-dirs`, R1 `old-label`/`new-label`,
  R17 `old-debug-info`/`new-debug-info` (and `devel-pkg*` into
  `old-header`/`new-header`), R18 `check-target` `depth`/`contract`.
  Each old input becomes a RETIRED tombstone that fails the run before
  analysis and names its replacement (not a forwarder, not a warning).
  `github-action-inputs.md` regenerated.
- **Python:** R1 `old_label`/`new_label`, R3 `acknowledgments`, R8
  `follow_deps` and `include_system_declarations`, R9 `debuginfod`, R7
  `public_symbols`, R18 `run_dump(frontend=)`. Old keyword arguments are
  removed outright (an unexpected-keyword `TypeError`);
  `python-api-reference.md` regenerated.

**Result (2026-10-30, Python part).** Renamed outright, with no alias: R1
`run_compare(old_label, new_label)` (`InputSpec.version` and `run_dump(version)`
stay, per the ADR text); R3 `acknowledgments`; R7 `public_symbols`; R8
`follow_deps` (`CompareRequest`/`DumpRequest`) and `include_system_declarations`
(`InputSpec`, `run_compare`, `run_dump`); R9 `debuginfod`; R10 `contract`; R18
`run_dump(frontend=)`. Each old spelling is in `scripts/retired_surfaces_0_7.py`
and the typed-API table of `docs/start/upgrading-to-0.7.md`. The gate's exceptions
for these ids are removed. R19 (the flattened and sided Python names) is
resolved as ACCEPTED on 2026-10-31 under the maintainer rule: the registry
records each as an `accepted: true` exception against the R19 row, and no
Python name in it remains a planned rename. Phase 4 is done for the typed API. `checker.compare()`'s `contract_evaluation` and
`contract_mode` stay as the documented Tier-1 raw building block (docs/use/python-api.md), not the typed request surface, and are an accepted exception (ADR-091 R10). Open review item, not a blocker: `run_dump(public_headers)` (R18 review: `run_dump` already has
a `headers` parameter, so `headers` would collide), `ReleaseCompareRequest` and
`ReleaseMemberCompareRequest` (they keep their own spellings and translate at the
boundary), and the `resolve_input`/`compare_snapshots`/`load_suppression_and_policy`
keyword spellings (public functions outside the typed-request surface). The
follow_deps name is recorded as a named R8 exception: the leaf `follow` alone is
ambiguous across the deps block.

Each PR: registry exceptions removed, changelog fragment (Changed /
Removed), `docs/start/upgrading-*.md` migration table updated, and the
retired-surface registry (`scripts/retired_surfaces.py`) gains the old
spellings in the same PR. Size: M each.

**Result (CLI part, ADR-091 D5 R1/R12/R16).** Done: `compare`, `dump` and
`project history` take `--label` (dest unchanged, so the command bodies and
rulings keep their parameter names); `compare --abi3-floor`; `deps compare
--root [old=|new=]PATH`; `deps tree --root`. The retired spellings were
removed in the same change (see Phase 6): `--version`, `--abi3`, `--old-root`,
`--new-root` and `--sysroot` now fail as unknown options (exit 64). R21 (`--include`,
`--used-by-manifest`, the `--bundle-facts-*` family) is not in this change; R19
is Python-only. Migration: `docs/start/upgrading-to-0.7.md`.

**Result (Action part, ADR-091 D5 R1/R2/R4–R7/R17/R18).** Done under the
2026-10-30 no-transition policy, so there is no one-release alias for the
Action: each new input replaces the old one in the same change, and the old
name becomes a RETIRED tombstone. Root `action.yml`: `policy` takes a built-in
name or a file (`policy-file` folds into it); `frontend` (was `ast-frontend`);
`compiler` (`gcc-path`, with `gcc-prefix` removed as a fallback spelling);
`compile-options` (`gcc-options`); `require-complete` (`analysis-assurance-complete`;
the `require-complete-analysis` tombstone now names it); `public-header-dirs`;
`old-debug-info`/`new-debug-info`; `old-header`/`new-header` (absorb
`devel-pkg1`/`devel-pkg2`); `old-label`/`new-label` (defaults `old`/`new` kept, so
the forwarded `--label` argv is unchanged). `actions/check-target`: `depth`
(`requested-depth`), `contract` (`contract-file`), and the same compile and
policy names; its `consumer-*` inputs are unchanged. The old names are listed in
`tests/test_action_retired_inputs_contract.py`'s frozen tombstone set and in the
run step's `_NON_RUN_SH_INPUTS`, since only `validate-inputs.sh` consumes them,
and in the registry's `retired` tier. The registry drops the Action exceptions
for these renames; one stays, `compile-options` (R5 names it, but its bare leaf
`options` is not ambiguous, so the block prefix is recorded as a deviation).
The Action budget fell from 53 to 49 analysis inputs and is recorded at 49. The
reusable workflows keep their own `workflow_call` input names, which this
change does not rename; they forward to the new Action names. The in-repo
workflows, the Action test suites and the docs use the new names;
`docs/start/upgrading-to-0.7.md` has the rename table. Python (R2, R3, R7–R10,
R18 `run_dump`) were the rest of Phase 4 at the time of that PR. The Python
typed-request part has since landed (see the Python note above); what remains is
listed in the Phase 4 Python paragraph.

**Result (reusable workflows, ADR-091 R4–R7/R17/R18 for `workflow_call`).**
`check-single.yml` and `check-project.yml` rename their inputs to the Action's
vocabulary: `depth`, `frontend`, `compiler`, `compile-options`, `contract`,
`policy`. `actions/check-target` renames its consumer inputs to
`consumer-frontend`, `consumer-compiler` and `consumer-compile-options`. The old
names stay declared as empty tombstones. This is a fail-closed choice, not a
confirmed GitHub behavior: the reusable-workflow docs do not say what an
undeclared `with:` key does, so each workflow's first step (`plan` in
check-project, `check` in check-single) rejects a non-empty tombstone and names
the replacement before any analysis runs. `tests/test_reusable_workflow_retired_inputs.py`
executes that step. The `workflow:` inventory (`scripts/gen_option_vocabulary.py`)
claims each input under its concept; artifact-staging names and the project-plan
config location are `out_of_scope`.

## Phase 5 — Surface budget (D4)

- R14: retired inputs stay declared as tombstones (`tests/test_action_retired_inputs_contract.py`); they do not count toward the budget. Convert the two ignored inputs (`jobs`, `allow-build-query`) into tombstones that fail the run, and add them to that test's `_TOMBSTONES_THAT_MUST_STAY_DECLARED`.
- Move `compare` options to the config tier until `--help` shows ≤ 15 (the
  six groups of the ADR: operands, evidence, scope, policy, gate, output).
  Candidates: `--instantiation-manifest`, `--bundle-facts-*`,
  `--diagnostic-comparison`, `--require-same-generation`,
  `--performance-profile`, `--include-system-declarations`, `--budget`,
  `--use-cases`. Each needs a config home first and stays reachable via
  `--set` and `--help-all`.
- Same review for Action inputs down to ≤ 25 analysis inputs.
- Drop the budget exception from the registry.

Size: M. Changelog: Changed.

## Phase 6 — Remove aliases (collapsed into each rename, 2026-10-30)

The 2026-10-30 policy (ADR-091 D7 amendment) withdraws the one-release
window, so there is no later release that deletes the aliases. Each rename
deletes its old spelling in the same change: CLI and Python outright (exit 64
as an unknown option, or an unexpected-keyword `TypeError`), config as an
unknown key under the strict schema, and each Action input as a RETIRED
tombstone that fails the run and names its replacement (it joins the
registry's `retired` tier and `tests/test_action_retired_inputs_contract.py`).
Each removed spelling is added to `scripts/retired_surfaces.py` and to the
newest `docs/start/upgrading-*.md` table.

**Result (2026-10-30, CLI part).** `abicheck/frontends/cli/options/renamed.py`
is deleted, with every CLI alias registration (`label.py`, `cli_options.py`'s
`--abi3`, `cli_stack.py`'s `--sysroot`/`--old-root`/`--new-root`). The
`deprecated_aliases:` section of `architecture/option-vocabulary.yaml` and the
gate test that checked it are removed; the gate's inventory is regenerated
without the hidden rows. `scripts/retired_surfaces.py` names the four retired
CLI spellings, and `docs/start/upgrading-to-0.7.md` now says "removed" in
place of "deprecated". In-repo callers (tests, benchmark and corpus scripts,
skills-src validation harnesses, the Action shell's extra-args checks and
messages, the test-action workflow's flag-shaped extra-args case) moved to
`--label` or `--root`. The Action tombstones (#1860) and the reusable-workflow tombstones
(this change) have landed. Still open: the Tier-1 `checker.compare()` keywords
of Phase 4.

## Phase 7 — Generate the references from the registry (optional)

Make `gen_cli_reference.py`, `gen_action_reference.py`,
`gen_config_reference.py` emit a "same option on other surfaces" column from
the registry, so one table answers "how do I say this in the Action / in
Python".

## Risks

- **Downstream workflows** using renamed Action inputs: there is no alias
  window (D7 amendment); the old input becomes a tombstone that fails the run
  and names its replacement, rather than a silent drop. Pinned tags are
  unaffected.
- **R1 is CLI-breaking** for `compare/dump/project history --version`: the
  `docs/start/upgrading-*.md` row and the exit-64 failure are the migration
  (no alias window); `abicheck --version` is untouched.
- **R11 changes report paths:** a bare `format: sarif` must keep writing the
  file it writes today. Phase 3 pins the current default path per format with
  a test before folding `output-file` into `output`.
- **R15 changes gating mechanics:** Phase 3 adds a differential test over the
  `fail-on-breaking` × `fail-on-api-break` × verdict matrix proving the
  forwarded `--set severity.*` yields the same step outcome and exit code as
  today's shell gate before the shell code is removed.
- **`--set` as a second spelling:** the registry rejects `--set` for core
  concepts, so each tier keeps one spelling.
- **Hidden behavior differences found in Phase 0** (e.g. `source:` vs
  `sources:`): any rename that would change a result moves out of this plan
  into its own ADR.
