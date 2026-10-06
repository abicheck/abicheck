# AGENTS.md — Canonical instructions for coding agents working on abicheck

This is the **canonical, vendor-neutral** repository contract. Tool-specific
surfaces are thin adapters pointing here: `CLAUDE.md` (imports this file via
`@AGENTS.md`) and `.github/copilot-instructions.md`. Edit repository-wide
commands and invariants **here**, never in an adapter. Sub-directory
`AGENTS.md`/`CLAUDE.md` files (`abicheck/<layer>/AGENTS.md`, `tests/CLAUDE.md`,
`scripts/CLAUDE.md`, …) are scoped, per-area context that loads when you work
in that tree.

This file is loaded into **every** agent session, so it holds only what every
task needs. Reference material lives behind the pointers in
[Where to read next](#where-to-read-next) — follow a pointer when its trigger
matches your task. When editing this file or any agent-facing document, apply
`.claude/skills/writing-for-agents/` (keep the always-loaded root lean; push
branch-specific reference behind a trigger-worded pointer).

## What is abicheck?

abicheck helps library and package maintainers understand and validate
API/ABI evolution: it compares two versions of a native library (ELF, PE/COFF,
Mach-O, plus optional debug info, headers, build data and sources — evidence
levels L0–L5), classifies every change into one of 408 `ChangeKind`s
(`BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, `RISK_KINDS`), and
gates CI on the result while reporting what the evidence could not establish.
Pure Python, 3.11+. Product direction: [`vision.md`](vision.md); this file owns
development procedure.

**Python versions:** `requires-python = ">=3.11"` is the supported minimum;
**3.13** is the canonical development/CI version (full suite, 95% coverage
floor, `ai-readiness` job). Develop against 3.13. Other versions run a smoke
lane (`.github/workflows/python-compat.yml`).

## Product decisions and change routing

Compact consequences of `vision.md` for anyone adding behavior. Each is a
stable product rule; where the current code does not yet satisfy one, the
linked owner records the gap — do not read a rule here as a claim that
every existing path already honors it.

**Authority.** `vision.md` governs product *direction*. Accepted ADRs
(`docs/contribute/adr/`) govern technical choices. Schemas, CLI `help=`
text, and code describe the *current* public contract. Plans
(`docs/contribute/plans/`) record implementation status. This file and its
scoped siblings govern development procedure. A direction stated in the
vision is **not** permission to change an existing default, exit code,
schema, or public interface — that still needs its ADR and migration.

Before adding behavior, establish, in this order: the **user task**
(PR review, local check, release, audit), the **comparison scope** (one
artifact, a package, a selected variant, a declared matrix), the
**compatibility contract** it is judged against, the **evidence available**
on each side, and the **platform**. A change that skips one of these is
usually the one that later manufactures a finding.

- **One model, any cardinality.** Single and multi-component analysis share
  the same semantic owners (ADR-061/063). Scalar inputs stay simple: a bare
  binary comparison never needs package metadata, a variant selector, or a
  consumer artifact. A one-member package and the scalar path must yield
  the same applicable findings.
- **Record before disposing.** Observed changes are recorded before policy
  (suppression, reclassification, scope exclusion, acknowledgment,
  deduplication, display filtering) acts on them, and every disposition
  keeps its rule and reason. Additions, accepted breaks, exclusions, and
  unknowns never silently vanish — "100 removals detected, 100 suppressed
  by rule X" stays visible on a passing run. Owner: [ADR-067](docs/contribute/adr/067-change-intent-acknowledgment-and-disposition-audit.md).
- **Optional inputs stay optional.** DWARF, build data, sources, consumer
  artifacts, and a complete build matrix add assurance; none may become an
  accidental prerequisite of the standard binary-plus-headers path.
- **Absent is not removed.** A selected local variant is not a complete
  release. *Unselected*, *expected but not produced*, *failed*, and
  *deliberately retired* are four different states with four different
  outcomes; unmatched never implies deleted without inventory evidence,
  and a run that completed zero comparisons never reads as a clean pass.
  Owner: [ADR-065](docs/contribute/adr/065-comparison-scope-selection-and-completeness.md).
- **Weaker evidence narrows conclusions.** A failed extractor is an error
  or an explicit `FAILED` fact, never an empty surface. Missing evidence
  lowers assurance and is reported; it never fabricates a break and never
  upgrades to a clean compatibility claim (ADR-028/049/050/063/064).
- **Policy decides acceptance, not facts.** Versioning model, enforcement
  strictness, acknowledgment, and CI gate settings change whether a release
  is *accepted*; they never change what was *observed* or its technical
  compatibility. Owner: [ADR-066](docs/contribute/adr/066-longitudinal-history-and-versioning-policy.md).
- **Finish the workflow.** Reuse the existing owner (request/plan/execute/
  result, `ReportDocument`, `ExitDecision`, `SelectorSet`, storage v2) and
  wire the consumer before claiming a feature; a parser-only or DTO-only
  slice is not a shipped capability. Delete the superseded path per this
  file's architecture rules rather than leaving two.
- **Validate the user-facing result.** Prove a change through the public
  workflow (CLI, typed API, Action) and the rendered report, across live and
  stored operands, not only through an internal detector or a mocked test.

Workstream status for all of the above:
[`docs/contribute/plans/vision-api-abi-evolution.md`](docs/contribute/plans/vision-api-abi-evolution.md).

## Task routing and dependency direction

ADR-061 makes these responsibility owners authoritative for new code. During
the incremental migration, route new behavior to the target owner rather than
extending a flat root prefix family.

| Change | Owner |
|---|---|
| Read a binary, debug, header, build, or source fact | `extract/` |
| Add an ABI entity/value shared across stages | `model/` |
| Match old/new entities or identify a raw change | `compare/` |
| Decide relevance, suppression, classification, severity, or gating | `policy/` |
| Coordinate dump, compare, scan, release, aggregate, project, or dependency behavior | `workflows/` |
| Serialize snapshots/baselines, own their schemas/migrations, or manage caches | `storage/` |
| Add a report field, report schema, or output format | `report/` |
| Add a CLI flag, Python adapter, or ABICC translation | `frontends/` |

Imports point inward: `storage -> model`; `extract -> model, storage`;
`compare -> model`; `policy -> model, compare`; `workflows -> model, storage,
extract, compare, policy`; `report -> model, compare, policy, workflows`; and
`frontends -> model, workflows, report`. New internal code imports canonical
implementation modules, never legacy `cli`/`service` facades. A
delegation-only facade preserving a historical import path is **not** a
durable outcome pre-1.0: twelve of them were deleted outright rather than
kept (ADR-061's 2026-09-13 amendment), so retire the old path and name its
owner in a changelog fragment instead of adding a re-export. The exception
is a facade that exists for a real dependency-direction constraint rather
than compatibility -- `checker_policy`/`contract_gating`/`reclassify`,
which `model`-owned `checker_types.py` imports because `model` cannot
depend on `policy`. The executable
contract and temporary no-growth inventory live in `architecture/`; run
`python scripts/check_architecture.py` for the focused gate. See
[ADR-061](docs/contribute/adr/061-responsibility-package-architecture.md).

## Task routing and dependency direction

ADR-061 makes these responsibility owners authoritative for new code. During
the incremental migration, route new behavior to the target owner rather than
extending a flat root prefix family.

| Change | Owner |
|---|---|
| Read a binary, debug, header, build, or source fact | `extract/` |
| Add an ABI entity/value shared across stages | `model/` |
| Match old/new entities or identify a raw change | `compare/` |
| Decide relevance, suppression, classification, severity, or gating | `policy/` |
| Coordinate dump, compare, scan, release, aggregate, project, or dependency behavior | `workflows/` |
| Serialize snapshots/baselines, own their schemas/migrations, or manage caches | `storage/` |
| Add a report field, report schema, or output format | `report/` |
| Add a CLI flag, Python adapter, or ABICC translation | `frontends/` |

Imports point inward: `storage -> model`; `extract -> model, storage`;
`compare -> model`; `policy -> model, compare`; `workflows -> model, storage,
extract, compare, policy`; `report -> model, compare, policy, workflows`; and
`frontends -> model, workflows, report`. New internal code imports canonical
implementation modules, never legacy `cli`/`service` facades. A
delegation-only facade preserving a historical import path is **not** a
durable outcome pre-1.0: twelve of them were deleted outright rather than
kept (ADR-061's 2026-09-13 amendment), so retire the old path and name its
owner in a changelog fragment instead of adding a re-export. The exception
is a facade that exists for a real dependency-direction constraint rather
than compatibility -- `checker_policy`/`contract_gating`/`reclassify`,
which `model`-owned `checker_types.py` imports because `model` cannot
depend on `policy`. The executable
contract and temporary no-growth inventory live in `architecture/`; run
`python scripts/check_architecture.py` for the focused gate. See
[ADR-061](docs/contribute/adr/061-responsibility-package-architecture.md).

## Quick reference

```bash
# Install (do this first if pytest/ruff/mypy are missing)
pip install -e ".[dev]"            # add ,docs,dist for full pr-profile parity

# Fast unit tests — the everyday loop. ~50,000 tests, ~12 min at 4-way
# parallelism: give it a real timeout, or run only the test files for the
# modules you touched first (`pytest tests/test_<module>*.py -q`).
pytest tests/ -m "not integration and not libabigail and not abicc and not slow and not golden" -q

ruff check abicheck/ tests/        # lint (CI-enforced)
ruff format --check abicheck/ tests/
mypy abicheck/                     # baseline is 0 errors — keep it 0
python scripts/check_architecture.py   # ADR-061 import-direction + no-growth gate
python scripts/check_ai_readiness.py   # structural repo gates
```

### Definition of done: `scripts/verify.py`

`scripts/verify.py` is the single verification orchestrator that pixi,
pre-commit, CI and this file all route through
(`tests/test_verify_profiles.py` enforces that). **Before opening a PR, run
`python scripts/verify.py --profile pr`** (same as `pixi run check`) — it adds
golden tests, the 95% coverage floor and ai-readiness on top of the fast loop.

```bash
python scripts/verify.py --profile fast            # the commands above
python scripts/verify.py --profile pr              # exact CI-equivalent PR gate
python scripts/verify.py --profile pr --list       # show steps without running
python scripts/verify.py --profile pr --only lint,typecheck
python scripts/verify.py --profile pr --json receipt.json
```

A `pr` run that skipped a step prints `WARNING: this pr-profile run is
INCOMPLETE` — treat that as not equivalent to CI. The `bugfix-test-contract`
step exits 2 (skip) locally unless `BUGFIX_CONTRACT_BODY_FILE` points at the
PR description. To change a check, change it in `verify.py` and let
`tests/test_verify_profiles.py` say what else must follow.

## Test markers

| Marker | Needs | Run when |
|--------|-------|----------|
| *(default)* | Python only | Always |
| `integration` | castxml + gcc/g++ | Touching DWARF/ELF/header parsing |
| `libabigail` / `abicc` | abidiff / abi-compliance-checker + gcc | Parity work |
| `msvc` | MSVC `cl.exe` | MSVC+PDB lane |
| `slow` | varies | Hypothesis/perf benchmarks |
| `golden` | golden files | Changing output format (CI runs them in `pr`) |
| `repo_scan` | Python only | Whole-tree AST/text gates; CI runs them once in `ai-readiness` |

Mark a test needing an external tool with its marker.

## Where to read next

Follow the pointer whose trigger matches your task:

| Read | When you are… |
|------|---------------|
| [`docs/contribute/agent-guide/module-map.md`](docs/contribute/agent-guide/module-map.md) | locating the module that owns a behavior, or touching the pipeline (model → extract → compare → policy → report), contract evaluation, release/multi-member comparison, snapshots/storage, or the service API |
| `abicheck/<layer>/AGENTS.md` | editing code inside that layer (`model`, `extract`, `compare`, `policy`, `workflows`, `storage`, `report`, `frontends`) |
| [`docs/contribute/agent-guide/quality-gates.md`](docs/contribute/agent-guide/quality-gates.md) | an `ai-readiness`/coverage/mutation/FP-rate gate failed, or you are writing tests for a bug fix, a matrix, a differential comparison or a reusable primitive |
| [`docs/contribute/agent-guide/performance.md`](docs/contribute/agent-guide/performance.md) | touching a hot path, a call-count/cost-budget/`perf-antipatterns` gate failed, or a user reports "slow" |
| [`docs/contribute/agent-guide/cli-and-exit-codes.md`](docs/contribute/agent-guide/cli-and-exit-codes.md) | adding a CLI command or flag, changing an exit code, or editing a file over 1500 lines |
| [`docs/contribute/agent-guide/decision-principles.md`](docs/contribute/agent-guide/decision-principles.md) | filling the PR template's "Bug class / General invariant" rows |
| [`docs/contribute/known-gaps.md`](docs/contribute/known-gaps.md) | about to fix something in an area with a known gap or a reverted fix — read it **before** re-attempting |
| [`tests/regressions/manifest.py`](tests/regressions/manifest.py) | writing a regression test — reuse a matching `BugClass` first |
| [`docs/contribute/adr/index.md`](docs/contribute/adr/index.md) | a change touches a default, schema, exit code or public interface |

**Repository skills** (`.claude/skills/`): `diagnosing-bugs` (load for a bug report, failing behavior or perf regression), `writing-for-agents` (load before editing any `AGENTS.md`/`CLAUDE.md`/skill), and the user-invoked `/handoff` and `/retro`.

## Key types

- `AbiSnapshot` (`model/snapshot.py`) — serializable snapshot of a library's ABI surface
- `DiffResult` (`checker_types.py`) — comparison result: changes, verdict, context
- `ChangeKind` (`model/change_catalog/kinds.py`, re-exported by `checker_policy.py`) — the 408 change types
- `Verdict` (`checker.py`) — overall result (compatible/source_break/breaking)

## Adding a new ChangeKind

1. Add a `("NAME", "value", None-or-comment)` triple to the shortest of
   `abicheck/model/change_catalog/kind_names_{1,2,3}.py` (the third element is
   required). Run `python scripts/gen_changekind_stub.py` to regenerate
   `kinds.pyi` (mypy reads the stub).
2. Add one `ChangeKindMeta` entry (`default_verdict`, non-empty `impact`,
   optional `description_template`) to the taxonomy module matching the
   producing detector: `symbols.py` (functions/variables/params/constants),
   `types.py` (records/enums/typedefs/layout/vtables), `platform.py`
   (ELF/PE/Mach-O, DWARF presence, versioning, SYCL), `build.py` (L3, bundle,
   wheel), `source.py` (L4/L5, surface reconciliation). The `*_KINDS` sets in
   `checker_policy.py` are derived from `default_verdict` — edit the registry
   entry, never the sets; `change_registry.py` holds no entries.
3. Implement detection in the matching diff module with
   `@registry.detector("...")`, like its neighbors.
4. Add a unit test.
5. Classify the kind in `tests/canonical_identity_contract.py` — exactly one
   of `TYPE_BEARING` (also add it to
   `finding_identity._TYPE_BEARING_DISCRIMINATOR_KINDS`), `VALUE_INSENSITIVE`,
   or `UNVERIFIED`. `tests/test_canonical_finding_id_completeness.py` fails
   until you do.

## Conventions

- **Commits**: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`).
  Sign when signing is configured; if signing fails for an environmental
  reason, retry once, then commit unsigned with
  `git -c commit.gpgsign=false commit …` (never change global git config) and
  say so in your summary.
- **Branches**: `feat/<name>` or `fix/<name>`.
- **Python**: 3.11+ syntax, type annotations, `from __future__ import annotations`; no line-length limit.
- **Tests**: plain `assert`; parametrize when possible.
- **Changelog**: a change to `abicheck/**/*.py` needs a fragment — `scriv
  create`, then uncomment one `### <Category>` section in
  `changelog.d/<name>.md`. `CHANGELOG.md`'s `## [Unreleased]` is generated
  from fragments; leave it untouched (CI `changelog-check.yml`).
- **mypy**: keep `mypy abicheck/` at 0 errors. A needed third-party
  suppression goes into that module's `disable_error_code` override in
  `pyproject.toml`, not a scattered `# type: ignore`.
- **Large files**: check `python scripts/check_ai_readiness.py 2>&1 | grep
  "exceeds soft limit"` (WARN > 1500 lines, ERROR > 2000) and
  `architecture/debt.yaml`'s `no_growth` baselines before growing a big file;
  shrink by moving a responsibility to a properly-owned module.

## Decision-making principles

- **Correctness decides, not effort.** Judge an approach on correctness,
  generality and fit with the architecture; there is no deadline criterion.
- **Fix the cause, not the instance.** Trace a bug to its root cause and close
  the whole class. If a general fix is not feasible in one pass, say so and
  record the gap in `docs/contribute/known-gaps.md`.
- **Test the bug class, not the reported input.** The regression test
  exercises the invariant with inputs beyond the reported one
  (property-based, exhaustive small domain, or several independent sibling
  cases) against an oracle that is not the implementation's own helper. This
  is what the PR template's "General invariant" row and
  `scripts/check_bugfix_test_contract.py` check.

## Exit codes (summary)

`compare`: 0 compatible · 2 source break · 4 ABI break (severity-aware mode:
0/1/2/4 by highest error-level category) · 8 proven removed library
(directory/package) · 64 usage error. Contract-coverage and scope-completeness
axes fold in with `max` and can only raise 0 → 1. Full matrix:
`docs/reference/exit-codes.md`; mechanics in
[`agent-guide/cli-and-exit-codes.md`](docs/contribute/agent-guide/cli-and-exit-codes.md).

## Rules

- Add changelog fragments via `scriv create`.
- Change `examples/` cases only together with the ground truth they encode (`catalog/ground_truth.json`).
- Justify every new dependency; abicheck stays lightweight.
- Regenerate expected outputs whenever you change a binary test fixture.
- Check public API signature changes for breakage; keep platform code cross-platform.
- Resolve a new import cycle with a function-local import or a shared leaf
  module. Extending `IMPORT_CYCLE_ALLOWLIST` needs an ADR or explicit
  maintainer sign-off recorded in the PR.
- Add a new root CLI command only after clearing ADR-054's admission bar
  (see `agent-guide/cli-and-exit-codes.md`); most new surface belongs under
  `project`.
- Point adapters (`CLAUDE.md`, `.github/copilot-instructions.md`) back here
  instead of copying commands, invariants or counts into them.
