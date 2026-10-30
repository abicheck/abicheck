# ADR-091: One Option Vocabulary Across CLI, Config, Action and Typed API

**Date:** 2026-10-30
**Status:** Proposed — not implemented. No code has changed. This ADR proposes
renames and behavior unifications on four public surfaces; each lands through
the migration in D7. Implementation plan:
[`plans/option-vocabulary-unification.md`](../plans/option-vocabulary-unification.md).

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
| Dependencies | `--follow-deps`, `--search-path` | — | `follow-deps` | `DumpRequest.follow_dependencies`, `InputSpec.include_dependencies`, `dependency_search_paths` |
| Debuginfod | `dump` only | `debug.debuginfod` | — | `enable_debuginfod` |
| Contract | `--contract {public,exports,all,auto}` | `contract:` | — | `contract_evaluation` + `contract_mode` (no `auto`) |
| Completeness floor | — | `assurance.require_complete` | `analysis-assurance-complete` | `require_complete` (ADR-088) |
| Public surface | default on | `scope.public`, `scope.public_header_dirs`, `scope.public_symbols` | `public-header-dir` | `scope_to_public_surface`, `public_header_dirs`, `public_surface_allowlist`, `force_public_symbols` |
| Python floor | `--abi3` | `python.abi3_floor` | — | — |
| Output | `-o FMT=PATH` (repeatable) | — | `format` + `output-file` (one) | — |
| Per-side value | `old=`/`new=` value qualifier; `deps compare --old-root/--new-root` | — | `old-*`/`new-*` inputs | `InputSpec` per side |
| Gate | exit codes + `--severity-preset` | `severity.*`, `gate.*` | `fail-on-breaking`, `fail-on-api-break`, `severity-preset` | `severity_preset` + overrides |

Three things make this more than cosmetic:

1. **Divergent behavior, not only names.** One CLI key takes a policy name or
   a file; the Action and Python split it in two and the Action makes one
   outrank the other. `--version` means three things, and ADR-070 already
   recorded a real defect from it (`extra-args: --version --dry-run`). The
   Action has a boolean `fail-on-*` gate model beside the CLI's severity model.
2. **The Action carries semantics.** `action/run.sh` (6944 lines) synthesizes
   a `compile:` config overlay from `ast-frontend`/`gcc-*`/`sysroot`/`nostdinc`
   because the CLI has no spelling for those keys, and `action.yml` still
   declares 11 retired inputs (`against`, `audit`, `build-target`,
   `bundle-system-providers`, `crosscheck`, `estimate`, `jobs`,
   `new-library-set`, `require-complete-analysis`, `risk-rules`, plus their
   error stubs).
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

### D5 — The specific changes

| # | Concept | Change |
|---|---|---|
| R1 | version label | CLI `--version` (on `compare`, `dump`, `project history`) → `--label`; Action `old-version`/`new-version` → `old-label`/`new-label`; Python `InputSpec.version` stays as the snapshot field but the shim arguments become `old_label`/`new_label`. Root `--version` keeps its meaning (tool version). |
| R2 | policy | Action `policy` + `policy-file` → one `policy`; Python `policy: str \| Path`, `policy_file_path` removed. |
| R3 | acknowledgments | config block `acknowledgment:` → `acknowledgments:`; Python `acknowledgments_path` → `acknowledgments`. |
| R4 | header frontend | Action `ast-frontend` → `frontend` (config tier; `--set compile.frontend=…` on CLI). |
| R5 | compiler | Action `gcc-path`/`gcc-options` → `compiler`/`compile-options` (config tier); `gcc-prefix` gets a `compile.*` home or is retired (Q3). |
| R6 | completeness floor | Action `analysis-assurance-complete` → `require-complete`. |
| R7 | public surface | Action `public-header-dir` → `public-header-dirs`; Python `public_surface_allowlist`/`force_public_symbols`/`scope.public_symbols` collapse to one concept after Phase 0 confirms they are the same (Q4). |
| R8 | dependencies | `--follow-deps` ↔ config `deps.follow` ↔ Python `follow_deps` (both `DumpRequest.follow_dependencies` and `InputSpec.include_dependencies` renamed); `--search-path` ↔ `deps.search_paths`. |
| R9 | debuginfod | Python `enable_debuginfod` → `debug.debuginfod` / shim `debuginfod`. |
| R10 | contract | Python `contract_evaluation` + `contract_mode` → one `contract` with the CLI's value set including `auto`. |
| R11 | output | Action `format` + `output-file` → `output` (list of `FMT=PATH`, same grammar as `-o`); a bare `FMT` keeps today's default path. |
| R12 | python floor | CLI `--abi3` → `--abi3-floor`. |
| R13 | source evidence | config `source:` and `sources:` merge into one `sources:` block if Phase 0 confirms no conflicting key. |
| R14 | Action retired inputs | the 11 retired inputs are deleted; GitHub reports an unknown input as a warning, and the release notes name each replacement. |
| R15 | gate | Action `fail-on-breaking`/`fail-on-api-break` stay as the two Action-only conveniences but are defined as sugar over `severity.*` overrides (ADR-088) and forwarded as `--set severity.…`, not evaluated in shell (Q2). |
| R16 | deps compare roots | `--old-root`/`--new-root` → `--root [old=\|new=]PATH`. |

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
release deletes the old spelling. Behavior unifications (D2) that change a
result rather than a name — none are known today — need their own ADR.

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

## Open questions

- **Q1.** `--label` vs keeping `--version` with a warning when it collides.
  Proposed: `--label`, because the collision already produced a defect.
- **Q2.** Should `fail-on-*` be removed from the Action outright in favor of
  `severity-preset`/`set:`? Proposed: keep as sugar (R15); revisit with usage.
- **Q3.** `gcc-prefix`: add `compile.compiler_prefix` or retire.
- **Q4.** Are `public_surface_allowlist`, `force_public_symbols` and
  `scope.public_symbols` one concept? Phase 0 answers from code.

## Alternatives considered

- **Document the mapping, rename nothing.** Cheap, but leaves the divergent
  behaviors (R1, R2, R11, R15) and has no mechanism to stop new drift.
- **Make the CLI name canonical instead of the config path.** CLI names are
  constrained by short flags and singular/plural conventions, and many concepts
  are config-only by design (ADR-040); the config path is the only name every
  concept can have.
- **A flag per config key.** Removes the Action overlay too, but doubles
  `--help` and defeats ADR-040; `--set` reaches the same keys with one option.
