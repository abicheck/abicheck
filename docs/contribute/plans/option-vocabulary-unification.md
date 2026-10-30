# Option vocabulary unification: implementation plan

**Status:** Phase 0 done (2026-10-30); Phase 1 done; Phase 2 CLI and typed-API half done for `compare`, `dump` and `project capture-variants` (the compile-path keys on `dump`, and the Action `set:` input, are open); Phases 3–7 not started. Implements
[ADR-091](../adr/091-one-option-vocabulary.md). Each phase is one PR, ordered
so that the parity gate lands before any rename and every rename lands with
its alias.

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

## Phase 2 — `--set` and the Action `set:` input (D4)

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

## Phase 3 — Behavior unifications (D2)

| R | Change | Files |
|---|---|---|
| R2 | Action `policy`/`policy-file` → one `policy`; Python `policy: str \| Path` | `action.yml`, `action/run.sh`, `workflows/compare/execute.py` |
| R10 | Python `contract` with `{public,exports,all,auto}` | `workflows/compare/session_request.py`, `execute.py` |
| R11 | Action `output` list of `FMT=PATH` | `action.yml`, `action/run.sh`, `action/validate-inputs.sh` |
| R15 | Action `fail-on-*` forwarded as `--set severity.*`, shell gate code removed | `action/run.sh` (~`5268`, `5542`, `5593`) |

Each keeps the old spelling as a deprecated alias (D7). Size: M.

## Phase 4 — Renames with aliases (D5)

One PR per surface keeps review small:

- **CLI:** R1 `--version` → `--label` (`compare`, `dump`, `project history`);
  R12 `--abi3` → `--abi3-floor`; R16 `deps compare --root [old=|new=]` and
  `deps tree --root`.
  Old spellings hidden, warn on stderr.
- **Config:** R3 `acknowledgment:` → `acknowledgments:`; R13 `source.method` →
  `sources.method`. The loader accepts the old key with a
  warning; `config-keys-reference.md` regenerated.
- **Action:** R4 `frontend`, R5 `compiler`/`compile-options`, R6
  `require-complete`, R7 `public-header-dirs`, R1 `old-label`/`new-label`,
  R17 `old-debug-info`/`new-debug-info` (and `devel-pkg*` into
  `old-header`/`new-header`), R18 `check-target` `depth`/`contract`.
  Old inputs emit `::warning::` and forward. `github-action-inputs.md`
  regenerated.
- **Python:** R1 `old_label`/`new_label`, R3 `acknowledgments`, R8
  `follow_deps` and `include_system_declarations`, R9 `debuginfod`, R7
  `public_symbols`, R18 `run_dump(frontend=)`. Old keyword arguments
  raise `DeprecationWarning`; `python-api-reference.md` regenerated.

Each PR: registry exceptions removed, changelog fragment (Changed /
Deprecated), `docs/start/upgrading-*.md` migration table updated, and
`scripts/check_docs_contract.py`'s `_RETIRED_SURFACES` gains the old
spellings once deleted in Phase 6. Size: M each.

**Result (CLI part, ADR-091 D5 R1/R12/R16).** Done: `compare`, `dump` and
`project history` take `--label` (dest unchanged, so the command bodies and
rulings keep their parameter names); `compare --abi3-floor`; `deps compare
--root [old=|new=]PATH` (the two old flags became two hidden aliases that
translate per side and may be combined with each other); `deps tree --root`.
The old spellings are hidden click options, each printing click's own
`DeprecationWarning` naming its replacement, and passing an old and a new
spelling for the same option is a usage error (exit 64). The mechanism is
`abicheck/frontends/cli/options/renamed.py`; the aliases are listed under
`deprecated_aliases:` in `architecture/option-vocabulary.yaml`, and the parity
gate checks that each is hidden and replaced by a visible claimed option. The
in-repo users (Action shell and metadata, rulings, help panels, docs, examples,
skills, tests) moved to the new spellings; the Action's own inputs are
unchanged until its part of Phase 4. R21 (`--include`, `--used-by-manifest`,
the `--bundle-facts-*` family) is not in this change; R19 is Python-only. Migration: `docs/start/upgrading-to-0.7.md`. Removal of the aliases is
Phase 6.

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

## Phase 6 — Remove aliases (next minor release)

Retire every deprecated spelling from Phases 3–4 per ADR-091 D7: delete
CLI/Python/config spellings, and turn each old Action input into a RETIRED
tombstone (it joins the registry's `retired` tier and the tombstone contract
test). Add them to `_RETIRED_SURFACES`, empty the registry's `exceptions:` list, and mark
ADR-091 implemented in `adr/index.md` and `adr-surface-registry.yaml`.

## Phase 7 — Generate the references from the registry (optional)

Make `gen_cli_reference.py`, `gen_action_reference.py`,
`gen_config_reference.py` emit a "same option on other surfaces" column from
the registry, so one table answers "how do I say this in the Action / in
Python".

## Risks

- **Downstream workflows** using renamed Action inputs: mitigated by the
  one-release alias and `::warning::`, then a tombstone that fails loudly
  rather than a silent drop; pinned tags are unaffected.
- **R1 is CLI-breaking** for `compare/dump/project history --version`: the
  alias window, the `docs/start/upgrading-*.md` row and the exit-64 failure
  after removal are the migration; `abicheck --version` is untouched.
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
