# ADR-091: One Option Vocabulary Across CLI, Config, Action and Typed API

**Date:** 2026-10-30
**Status:** Proposed — Phases 0, 1 and 2 done. Phase 4 done for the CLI, the Action, the reusable workflows, and the typed request and `run_compare` shim. The Tier-1 `checker.compare()` keywords are an accepted exception (R10). Phases 3 and 5 are in progress; Phase 6 is collapsed into each rename (D7 amendment). The R19–R21 findings are classified as accepted or handled by Phase 5 in their rows (2026-10-31). This ADR proposes renames and behavior unifications on four public surfaces; each lands through the migration in D7. Open questions Q1–Q4 are resolved below. Implementation plan: [`plans/option-vocabulary-unification.md`](../plans/option-vocabulary-unification.md).

Governed by: [ADR-037](037-cli-interface-contract.md) (CLI interface
contract), [ADR-040](040-compare-surface-reduction.md) (compare surface
reduction, config-backed flags), [ADR-054](054-cli-project-integration-surface-consolidation.md)
(command admission), [ADR-068](068-one-comparison-product-and-scan-retirement.md)
(one comparison product), [ADR-070](070-action-layer-does-not-encode-cli-semantics.md)
(the Action does not encode CLI semantics), [ADR-088](088-one-compare-exit-gate.md)
(one compare exit gate).

## Context

abicheck exposes one product through four surfaces: the CLI, `.abicheck.yml`,
the composite GitHub Action (`action.yml`) and the typed Python API
(`run_compare` / `CompareRequest` / `DumpRequest`). Each journey stage in
[`cjm.md`](../cjm.md) is reachable, but the same concept is spelled and
behaves differently per surface, so a user moving from `first_check` (CLI) to
`adopt_ci` (Action) to `tune` (config) relearns the vocabulary at each step.
A 2026-10-30 audit of the live surfaces (click tree, `action.yml`,
`workflows/compare/execute.py`, `config-keys-reference.md`) found:

| Concept | CLI | Config | Action | Python |
|---|---|---|---|---|
| Policy | `--policy NAME\|PATH` | `policy.overrides` | `policy` **+** `policy-file` | `policy` **+** `policy_file_path` |
| Acknowledgments | `--acknowledgments` | `acknowledgment:` (singular) | `acknowledgments` | `acknowledgments_path` |
| Version label | `compare --version [old=\|new=]L`; `dump --version L`; root `--version` = tool version | — | `old-version`/`new-version` | `old_version`/`new_version` |
| Header frontend | — | `compile.frontend` | `ast-frontend` | `frontend` |
| Compiler | — | `compile.compiler`, `compile.options` | `gcc-path`, `gcc-prefix`, `gcc-options` | `compile_context` |
| Dependencies | `--follow-deps`, `--search-path` | — | `follow-deps` | `follow_dependencies`, `dependency_search_paths` |
| System declarations | `--include-system-declarations` | — | — | `InputSpec.include_dependencies` (a different word for the same switch) |
| Debuginfod | `dump` only | `debug.debuginfod` | — | `enable_debuginfod` |
| Contract | `--contract {public,exports,all,auto}` | `contract:` | — | `contract_evaluation` + `contract_mode` (no `auto`) |
| Completeness floor | — | `assurance.require_complete` | `analysis-assurance-complete` | `require_complete` (ADR-088) |
| Public surface | default on | `scope.public`, `scope.public_header_dirs`, `scope.public_symbols` | `public-header-dir` | `scope_to_public_surface`, `public_header_dirs`, `public_surface_allowlist`, `force_public_symbols` |
| Python floor | `--abi3` | `python.abi3_floor` | — | — |
| Output | `-o FMT=PATH` (repeatable) | — | `format` + `output-file` (one) | — |
| Per-side value | `old=`/`new=` value qualifier; `deps compare --old-root/--new-root` | — | `old-*`/`new-*` inputs, and `debug-info1/2`, `devel-pkg1/2` | `InputSpec` per side |
| Gate | exit codes + `--severity-preset` | `severity.*`, `gate.*` | `fail-on-breaking`, `fail-on-api-break`, `severity-preset` | `severity_preset` + overrides |

Three things make this more than cosmetic:

1. **Divergent behavior, not only names.** One CLI key takes a policy name or
   a file; the Action and Python split it in two and the Action makes one
   outrank the other. `--version` means three things, and ADR-070 already
   recorded a real defect from it (`extra-args: --version --dry-run`). The
   Action has a boolean `fail-on-*` gate model beside the CLI's severity model.
2. **The Action carries semantics.** `action/run.sh` (6944 lines) synthesizes
   a `compile:` config overlay from `ast-frontend`/`gcc-*`/`sysroot`/`nostdinc`
   because the CLI has no spelling for those keys. (`action.yml` also
   declares 10 retired inputs and 2 ignored ones. Those are not drift: a
   retired input stays declared on purpose as a fail-closed tombstone, because
   GitHub silently drops an undeclared input and the run would then report a
   narrower analysis as a clean pass. `tests/test_action_retired_inputs_contract.py`
   owns that rule.)
3. **No size budget.** `compare --help` shows 22 options and `--help-all` 45;
   `run_compare` takes about 43 flat keyword arguments; `action.yml` declares
   about 85 inputs. Nothing stops each from growing independently.

The good precedent already exists: `depth` and `severity-preset` have one
name, one value set and one default on all four surfaces.

## Decision

### D1 — The config path is the canonical name of every option

Every user-settable option is one **concept** with one canonical name: its
dotted `.abicheck.yml` path (`scope.exclude_headers`, `compile.frontend`). A
concept with no config home today gets one (e.g. `deps.follow`,
`output`). The other three spellings are derived from it mechanically:

| Surface | Rule | Example (`scope.exclude_headers`) |
|---|---|---|
| Config | the path itself | `scope: {exclude_headers: [...]}` |
| CLI | `--` + kebab-case leaf; singular for a repeatable list | `--exclude-header` |
| Action | kebab-case leaf; plural for a list | `exclude-headers` |
| Python | `CompareRequest`/`DumpRequest` field at the same dotted path, or the snake leaf on the convenience shim | `request.scope.exclude_headers`, `run_compare(exclude_headers=...)` |

Where the leaf alone is ambiguous across blocks, the CLI/Action name keeps the
block prefix (`debug.format` → `--debug-format`). The derived name is recorded,
not re-derived by readers: see D6.

### D2 — One concept, one behavior

For each concept the four surfaces share **type, cardinality, value set,
default and precedence**. Specifically:

- A key that accepts a built-in name *or* a file (policy) is one key
  everywhere.
- A repeatable CLI option is a list in config, a list input in the Action,
  and a sequence in Python (already true for `suppress` in Python; not for
  Action `output-file`).
- A value set is identical (`contract` gains `auto` in Python).
- Precedence is one rule, owned by the CLI per ADR-070 D2: explicit surface
  value > `.abicheck.yml` > built-in default. The Action never resolves it.

### D3 — One per-side convention

A per-side value is the canonical concept with a side qualifier, never a
separate concept: CLI `old=`/`new=` value prefix (as `-H`, `--version`,
`--variant` already do), Action `old-`/`new-` input prefix mapped one-to-one
onto that qualifier, Python `InputSpec` on `CompareRequest.old`/`.new`. Flags
that encode the side in the option name (`deps compare --old-root`/`--new-root`)
migrate to the qualifier form (`--root old=… --root new=…`).

### D4 — Two tiers, with a budget

Each concept is either:

- **core** — a visible CLI flag, an Action input and a convenience-shim
  argument. Budget: at most **15** core options on `compare --help` and at
  most **25** analysis inputs in `action.yml` (infrastructure inputs such as
  `python-version`, `install-deps`, `baseline-*` are counted separately).
- **config** — reachable only through `.abicheck.yml`, the typed request,
  and one generic override spelling on CLI and Action:
  `--set PATH=VALUE` (repeatable) and the Action input `set:` (one
  `PATH=VALUE` per line), validated against the same config schema. This
  replaces the Action's `compile:` overlay synthesis and removes the
  pressure to add a flag per key.

Promoting a concept from config to core needs the ADR-054 admission
argument. `--help-all` keeps listing everything.

**Amendment (2026-10-31, maintainer decision): the budget is soft.** The
15 and 25 figures are soft targets, not hard limits; usability wins. A
concept stays a flag or an Action input, and is not moved to the config tier,
if any of these holds: (a) it is per-side (`old=`/`new=`); (b) it varies per
run or per CI matrix cell; (c) a first run without `.abicheck.yml` needs it;
(d) it is a commonly used analysis setting. Only project-stable, rarely-set
options move to the config tier, reached through `.abicheck.yml` or
`--set`/`set:`. `--set` is the flexible escape hatch, not the primary
interface. The same rule applies to the CLI and the Action. The recorded
no-growth counts in `architecture/option-vocabulary.yaml` are the budget that
the test enforces: `compare` 17 visible core options (`--used-by-manifest`,
`--required-symbol` and `--view` moved to `consumers.manifests`,
`consumers.required_symbols` and `output.view`), and the root Action 42
analysis inputs (dso-only, include-private-dso, fail-on-removed-library,
public-header-dirs, frontend, compile-options and nostdinc moved to `set:`).
Remaining candidates, not yet moved and not part of this record: the
`--debug-info` per-side transports, the `deps.*` keys that have no config
home yet, and `--sources`, which needs a config discovery rule first.

### D5 — The specific changes

| # | Concept | Change |
|---|---|---|
| R1 | version label | CLI `--version` (on `compare`, `dump`, `project history`) → `--label`; Action `old-version`/`new-version` → `old-label`/`new-label`; Python `InputSpec.version` stays as the snapshot field but the shim arguments become `old_label`/`new_label`. Root `--version` keeps its meaning (tool version). |
| R2 | policy | Action `policy` + `policy-file` → one `policy`; Python `policy: str \| Path`, `policy_file_path` removed. |
| R3 | acknowledgments | config key `acknowledgment:` (one whole-value key, `Kind.WHOLE`) → `acknowledgments:`, touching its parser and writer (`parse_acknowledgment_config`, `acknowledgment_block`); Python `acknowledgments_path` → `acknowledgments`. |
| R4 | header frontend | Action `ast-frontend` → `frontend` (config tier; `--set compile.frontend=…` on CLI). |
| R5 | compiler | Action `gcc-path`/`gcc-prefix`/`gcc-options` → `compiler`/`compile-options` (config tier). `gcc-prefix` is only a fallback spelling of the compiler (`compiler = gcc_path or gcc_prefix` in `action/run.sh`), so it folds into `compiler` (Q3). |
| R6 | completeness floor | Action `analysis-assurance-complete` → `require-complete`. Note the near-collision with the retired tombstone `require-complete-analysis`, which keeps failing the run; the two must stay distinct names and the tombstone's error message should point at `require-complete`. |
| R7 | public surface | Action `public-header-dir` → `public-header-dirs`; Python `force_public_symbols` → `public_symbols`, the same widening overlay as `scope.public_symbols`. `public_surface_allowlist` is a different concept (a manifest-committed surface that *narrows* scoping) and stays, typed-API only (Q4). |
| R8 | dependencies, system declarations | `--follow-deps` ↔ config `deps.follow` ↔ Python `follow_deps` (`CompareRequest`/`DumpRequest.follow_dependencies`); `--search-path` ↔ `deps.search_paths`. Separately, `InputSpec.include_dependencies` and the `include_dependencies` shim arguments are `--include-system-declarations` under another name, and become `include_system_declarations`. |
| R9 | debuginfod | Python `enable_debuginfod` → `debug.debuginfod` / shim `debuginfod`. |
| R10 | contract | Python `contract_evaluation` + `contract_mode` → one `contract` with the CLI's value set including `auto`. **Amended 2026-10-31:** ACCEPTED exception: the Tier-1 `checker.compare()` core keeps `contract_evaluation` and `contract_mode` as the documented raw building block (`docs/use/python-api.md`), not the typed request surface. It is not inventoried, so the registry cannot carry it as an exception row. |
| R11 | output | Action `format` + `output-file` → `output` (list of `FMT=PATH`, same grammar as `-o`); a bare `FMT` keeps today's default path. **Amended 2026-10-31: kept as-is.** `format` and `output-file` stay as two inputs. Nearly every Action workflow uses them, and folding them into `output: FMT=PATH` breaks all of them for the small gain of multiple outputs. This is an accepted deviation from the CLI's `-o FMT=PATH` grammar (D2). A multi-output form can be added later as an additive input if users ask. `tests/test_action_report_output_paths.py` pins today's destinations per format. The two registry exceptions (`action:format`, `action:output-file`) are marked `accepted` (#1866). ACCEPTED as-is: `format`, `output-file`. |
| R12 | python floor | CLI `--abi3` → `--abi3-floor`. |
| R13 | source evidence | config `source.method` → `sources.method`. Phase 0 found no key collision: `source:` holds only `method`, `sources:` holds `public_headers`, `exclude`, `graph`. |
| R14 | Action retired inputs | Retired inputs **stay declared** as fail-closed tombstones, per `tests/test_action_retired_inputs_contract.py`; they are excluded from D4's input budget and form the registry's `retired` tier. Removing one remains that contract's product decision (no supported workflow can still set it) and is out of this ADR's scope. The two *ignored* inputs (`jobs`, `allow-build-query`) silently do nothing today; they become tombstones that fail the run, so that no input is accepted and ignored. |
| R15 | gate | Action `fail-on-breaking`/`fail-on-api-break` stay as the two Action-only inputs. **Amended 2026-10-31:** they are a pure mapping of the CLI's own exit code (4 → `fail-on-breaking`, 2 → `fail-on-api-break`; every other code passes through), not `--set severity.*` forwarding. `severity.potential_breaking` is `API_BREAK_KINDS ∪ RISK_KINDS`, so it has no exact equivalent for `fail-on-api-break`; an exact category would need an ADR-088 change, deferred. The shell does not re-read the report to decide the gate (Q2). |
| R16 | deps roots | `deps compare --old-root`/`--new-root` → `--root [old=\|new=]PATH`; `deps tree --sysroot` → `--root PATH`, the same concept. |
| R17 | numeric sides | Action `debug-info1`/`debug-info2` → `old-debug-info`/`new-debug-info`; `devel-pkg1`/`devel-pkg2` already forward as `--header old=`/`new=`, so they fold into `old-header`/`new-header`. |
| R18 | stray spellings | `actions/check-target` `requested-depth` → `depth`, `contract-file` → `contract`; `run_dump(header_backend)` → `frontend`; `run_dump(public_headers)` is reviewed against `headers` in Phase 4. |
| R19 | Python names (Phase 1 finding) | Request fields and `run_compare`/`run_dump` shim parameters whose spelling breaks D1 and that no row above renames: flattened full paths (`pack_*`, `severity_preset`, `debug_format`) and sided or suffixed shim names (`old_pdb_path`, `budget_s`, `compile_context`). Each takes the D1 spelling: the flattened path on the request, the snake leaf on the shim. Phase 4 (Python). **Amended 2026-10-31 (maintainer rule: balance over minimalism; ACCEPTED exceptions are not renames).** ACCEPTED as-is, kept on purpose: the flattened request fields and nested-path spellings of the typed API (`pack_paths`, `pack_policy_overrides`, `pack_internal_namespaces`, `severity_abi_breaking`, `severity_addition`, `severity_potential_breaking`, `severity_preset`, `severity_quality_issues`, `debug_format`, `scope_public`, `scope_to_public_surface`, `strict_suppressions`, `project_policy_overrides`, `performance_profile`, `require_complete_analysis`, `allow_build_query`, `has_sources`, `build_targets`, `build_config`, `env_matrix`, `dependency_search_paths`, `lang_explicit`, `public_include_search_dirs`, `includes`, `pdb`, `pdb_path`, `debug_roots`); the sided shim names of the D3 convention (`old`, `new`, `old_input`, `new_input`, `old_includes`, `new_includes`, `old_pdb_path`, `new_pdb_path`, `old_debug_roots`, `new_debug_roots`, `old_probe_matrix`, `new_probe_matrix`); and the shim names `path`, `input`, `binary_fmt`, `compile_context`, and the unit-suffixed `budget_s`. No R19 item remains a planned rename. The registry records each as an `accepted: true` exception against this row. |
| R20 | Action names (Phase 1 finding) | Action inputs that break D1's kebab-leaf rule and no row above renames: `include` → `include-dirs`, `build-config`/`build-system`, `changed-path` → `changed-paths`, `used-by-manifest`, `search-path` → `search-paths`, `snapshot-compression`, and `operands`' `old-library`/`new-library` (D3 qualifiers on `library`). Phase 4 (Action). **Amended 2026-10-31 (maintainer rule: balance over minimalism; ACCEPTED exceptions are not renames).** ACCEPTED as-is, kept on purpose because the familiar spelling is the usable one: `include`, `old-include`, `new-include` (the `-I/--include` convention), `old-library`, `new-library`, `changed-path`, `search-path`, `compile-db`, `build-config`, `build-system`, `build-generator`, `bundle-members`, `snapshot-compression` and `severity-preset` (the precedent this ADR cites). `used-by-manifest` is HANDLED BY PHASE 5 (D4 balance amendment), not renamed here: the Phase 5 PR moves it to `consumers.manifests`. The registry records each accepted name as an `accepted: true` exception against this row. |
| R21 | CLI names (Phase 1 finding) | Visible CLI options that break D1 and no row above renames: `compare --include` → `--include-dir`; `--used-by-manifest`, a second file-valued spelling beside `--used-by` that Phase 0 grouped under `used_by`, which Phase 4 either folds into `--used-by` or gives its own concept; `--instantiation-manifest` and the `--bundle-facts-*` family, which Phase 0 grouped under `bundle`. Phase 4 (CLI). **Amended 2026-10-31 (maintainer rule: balance over minimalism; ACCEPTED exceptions are not renames).** ACCEPTED as-is: `--include` on `compare` and `dump` (the familiar `-I/--include` convention), `--instantiation-manifest`, `--bundle-facts-out`, `--bundle-facts-library-manifest`, `--performance-profile` and `--severity-preset` (the precedent this ADR cites). HANDLED BY PHASE 5 (D4 balance amendment), not renamed here: `--used-by-manifest`, a second file-valued spelling beside `--used-by`, moves to the config tier as `consumers.manifests` (with `--required-symbol` as `consumers.required_symbols`). If Phase 5 lands first, its exception is dropped. |
| R22 | surface budget (Phase 1 finding, D4) | `compare --help` shows 20 core options (D4 budget 15) and the root `action.yml` has 53 analysis inputs (budget 25). The recorded counts may only fall; Phase 5 moves options to the config tier until both are within budget. **Amended 2026-10-31 (Phase 5):** handled by the D4 soft-budget amendment: the budgets are soft targets, and the recorded no-growth counts after Phase 5 are 17 (`compare --help`) and 42 (root `action.yml`). |
| R23 | generic override (Phase 2, D4) | The one generic per-run override, `compare --set PATH=VALUE` and the typed `config_overrides` / `run_compare(config_overrides)`. It is not one path: it carries any config key, so D1's derivation from a concept name (`config.override`) does not apply and the spelling is the one D4 names. Phase 2 (CLI and Python). |

### D6 — One registry and a parity gate

`architecture/option-vocabulary.yaml` lists every concept: canonical path,
tier, type, cardinality, value set, default, sidedness, and the derived name on
each surface (or `none` with a reason). `tests/test_option_vocabulary.py`
checks it against the live surfaces — click introspection, `action.yml`
inputs, the config schema and `CompareRequest`/`DumpRequest` fields — and
fails on an unregistered option, a name that breaks D1 without a recorded
exception, a default or value-set mismatch, or a core count above D4's budget.
Existing deviations sit in a no-growth exceptions list, removed as the plan's
phases land (the `architecture/debt.yaml` pattern).

### D7 — Migration

Pre-1.0 policy (ADR-061's amendment): no permanent facades. Each rename ships
with the old spelling accepted for **one minor release**, printing a
deprecation warning naming the new one (CLI stderr, Action `::warning::`,
Python `DeprecationWarning`), plus a changelog fragment. The next minor
release retires the old spelling, which differs by surface:

- CLI and Python: the old spelling is deleted; it then fails as an unknown
  option (exit 64) or an unexpected keyword argument, which is already loud.
- Config: the old key becomes unknown and is reported by the strict schema.
- Action: the old input becomes a fail-closed **tombstone**, not a deletion,
  because GitHub drops an undeclared input silently (R14,
  `tests/test_action_retired_inputs_contract.py`).

Behavior unifications (D2) that change a result rather than a name need their
own ADR; R11 and R15 are checked for this with a differential test before they
land (plan Phase 3).

**Amendment (2026-10-30, maintainer decision): no transition window.** The
one-release alias window above is withdrawn. A renamed CLI flag, Python
argument or field, or config key is renamed outright in the same change that
lands it, and its old spelling is deleted: the CLI fails as an unknown option
(exit 64), Python raises an unexpected-keyword `TypeError`, and a config file
fails the strict schema's unknown-key error. The single exception is the
GitHub Action. An old Action input becomes a RETIRED tombstone that fails the
run before analysis and names the replacement, because GitHub silently drops an
undeclared input and a narrower analysis would otherwise pass as clean
(`tests/test_action_retired_inputs_contract.py` owns that rule). Each removed
spelling is recorded in the retired-surface registry (`scripts/retired_surfaces.py`)
and in a row of the newest `docs/start/upgrading-*.md` table. The plan's
Phase 6 (alias removal) therefore collapses into each rename's own phase.

## Consequences

- One vocabulary to learn and document: `config-keys-reference.md`,
  `cli-reference.md`, `github-action-inputs.md` and the Python reference can be
  generated from the registry instead of maintained apart.
- The Action shrinks toward a forwarder; ADR-070 D1/D2 become easier because
  `--set` gives the CLI a spelling for every key the Action used to synthesize.
- Users pay a one-release migration on renamed spellings; workflows pinned to
  an old Action tag are unaffected.
- `--set` is a second way to reach a core option; D6 rejects `--set` for a core
  concept so there is still exactly one spelling per tier.

## Out of scope

Exit codes, verdicts and report schemas (owned by ADR-009/064/088 and the
report schemas); the ABICC translation layer; `aggregate` and `project *`
option names beyond `--version` → `--label` and `-o`.

## Resolved questions

Accepted with the recommendations on 2026-10-30; Q3 and Q4 were answered from
code in Phase 0.

- **Q1.** `--version` becomes `--label` (R1): the collision already produced a
  defect.
- **Q2.** `fail-on-*` stays in the Action as sugar over `severity.*` (R15).
- **Q3.** `gcc-prefix` folds into `compiler` (R5).
- **Q4.** `scope.public_symbols` and `force_public_symbols` are one concept,
  and `public_surface_allowlist` is another (R7).

## Phase 0 result

`architecture/option-vocabulary-inventory.json` (generated by
`scripts/gen_option_vocabulary.py`) holds 101 CLI options, 61 config keys,
274 Action inputs (92 root, 59 `check-target`, the rest in transport and
reporting sub-actions) and 153 typed-API fields and parameters.
`architecture/option-vocabulary.yaml` groups every analysis row into 104
concepts: 29 core, 65 config, 5 Action-only, 3 typed-API-only, 1 internal
group and 1 retired group. 21 concepts carry a rename id. The 29 core
concepts are over D4's budget of 15, which is Phase 5's work.
`tests/test_option_vocabulary.py` fails when a live option is not claimed by
exactly one concept, so no new option can join a mapped surface unregistered.
Mapped today: the CLI, config, typed API, root `action.yml` and
`actions/check-target`. The other sub-actions are listed `out_of_scope` with an
expiry (`actions/aggregate` and `actions/collect-facts` until Phase 1; baseline
transport, report publication and run verification permanently).

## Alternatives considered

- **Document the mapping, rename nothing.** Cheap, but leaves the divergent
  behaviors (R1, R2, R11, R15) and has no mechanism to stop new drift.
- **Make the CLI name canonical instead of the config path.** CLI names are
  constrained by short flags and singular/plural conventions, and many concepts
  are config-only by design (ADR-040); the config path is the only name every
  concept can have.
- **A flag per config key.** Removes the Action overlay too, but doubles
  `--help` and defeats ADR-040; `--set` reaches the same keys with one option.
