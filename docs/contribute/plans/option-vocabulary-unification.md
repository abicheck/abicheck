# Option vocabulary unification: implementation plan

**Status:** Phase 0 done (2026-10-30); Phase 2 CLI and typed-API half for `compare` done, the rest of Phase 2 open; Phases 1 and 3–7 not started. Implements
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

**Result (2026-10-30, partial).** The CLI and typed-API half for `compare`
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
The Action half (`set:` input, `action/run.sh` `compile:` overlay) is not started.

## Phase 3 — Behavior unifications (D2)

| R | Change | Files |
|---|---|---|
| R2 | Action `policy`/`policy-file` → one `policy`; Python `policy: str \| Path` | `action.yml`, `action/run.sh`, `workflows/compare/execute.py` |
| R10 | Python `contract` with `{public,exports,all,auto}` | `workflows/compare/session_request.py`, `execute.py` |
| R11 | Action `output` list of `FMT=PATH` | `action.yml`, `action/run.sh`, `action/validate-inputs.sh` |
| R15 | Action `fail-on-*` forwarded as `--set severity.*`, shell gate code removed | `action/run.sh` (~`5268`, `5542`, `5593`) |

Each lands with its old spelling removed (D7 amendment: no alias). Size: M.

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
`--label` or `--root`. Still open: the Python keyword and config key halves of
Phase 4, and the Action tombstones, each land with their own renames.

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
