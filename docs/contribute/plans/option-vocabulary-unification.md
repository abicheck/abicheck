# Option vocabulary unification: implementation plan

**Status:** Proposed (2026-10-30), not started. Implements
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

## Phase 0 — Inventory and the open semantic questions (no behavior change)

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
  R12 `--abi3` → `--abi3-floor`; R16 `deps compare --root [old=|new=]`.
  Old spellings hidden, warn on stderr.
- **Config:** R3 `acknowledgment:` → `acknowledgments:`; R13 `source:` into
  `sources:` (if Phase 0 allows). The loader accepts the old key with a
  warning; `config-keys-reference.md` regenerated.
- **Action:** R4 `frontend`, R5 `compiler`/`compile-options`, R6
  `require-complete`, R7 `public-header-dirs`, R1 `old-label`/`new-label`.
  Old inputs emit `::warning::` and forward. `github-action-inputs.md`
  regenerated.
- **Python:** R1 `old_label`/`new_label`, R3 `acknowledgments`, R8
  `follow_deps`, R9 `debuginfod`, R7 per Phase 0. Old keyword arguments
  raise `DeprecationWarning`; `python-api-reference.md` regenerated.

Each PR: registry exceptions removed, changelog fragment (Changed /
Deprecated), `docs/start/upgrading-*.md` migration table updated, and
`scripts/check_docs_contract.py`'s `_RETIRED_SURFACES` gains the old
spellings once deleted in Phase 6. Size: M each.

## Phase 5 — Surface budget (D4)

- R14: delete the 11 retired Action inputs and their error stubs.
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

Delete every deprecated spelling from Phases 3–4, add them to
`_RETIRED_SURFACES`, empty the registry's `exceptions:` list, and mark
ADR-091 implemented in `adr/index.md` and `adr-surface-registry.yaml`.

## Phase 7 — Generate the references from the registry (optional)

Make `gen_cli_reference.py`, `gen_action_reference.py`,
`gen_config_reference.py` emit a "same option on other surfaces" column from
the registry, so one table answers "how do I say this in the Action / in
Python".

## Risks

- **Downstream workflows** using renamed Action inputs: mitigated by the
  one-release alias and `::warning::`; pinned tags are unaffected.
- **`--set` as a second spelling:** the registry rejects `--set` for core
  concepts, so each tier keeps one spelling.
- **Hidden behavior differences found in Phase 0** (e.g. `source:` vs
  `sources:`): any rename that would change a result moves out of this plan
  into its own ADR.
