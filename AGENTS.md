# AGENTS.md — Canonical instructions for coding agents working on abicheck

This is the **canonical, vendor-neutral** repository contract (CLAUDE.md
"M1-1"). Every tool-specific instruction surface is a thin adapter that
points back here instead of maintaining its own copy:

| File | Role |
|------|------|
| `AGENTS.md` (this file) | Canonical instructions — the source of truth |
| `CLAUDE.md` | Claude Code bootstrap — imports this file via `@AGENTS.md` |
| `.github/copilot-instructions.md` | GitHub Copilot adapter — points here |

A Cursor adapter (`.cursor/rules/abicheck.mdc`) previously existed here too;
it was removed as part of this repository's repo-structure cleanup, along
with the other tool-specific directories this repo had accumulated
(`.agents/`, `.gemini/`, extra `.claude/` content) — see this file's own git
history for that change. There is currently no Cursor-specific adapter in
this repository; `AGENTS.md` remains the canonical, vendor-neutral source a
Cursor user (or any other agent without its own adapter) should be pointed
at directly.

If you're editing repository-wide instructions, edit **this file**. Don't
hand-duplicate a command or invariant into an adapter — adapters exist so
each tool's convention is satisfied without a second copy to drift.
Sub-directory `CLAUDE.md` files (`abicheck/CLAUDE.md`, `tests/CLAUDE.md`,
etc.) are scoped, per-area context, not adapters to this file — they stay as
they are.

## What is abicheck?

ABI compatibility checker for C/C++ shared libraries. Pure Python (3.10+).
Detects 397 ABI/API change types across ELF, PE/COFF, and Mach-O binaries,
categorized into `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, and `RISK_KINDS` (see `ChangeKind`).
Drop-in replacement for abi-compliance-checker (ABICC).

**Two different Python version numbers matter here, don't conflate them:**
`pyproject.toml`'s `requires-python = ">=3.10"` is the *minimum supported*
version (what a user's environment needs to run abicheck) — CI tests 3.12,
3.13, and 3.14 across platforms to keep that floor honest. **3.13** is the
*canonical development/CI* version — `repo_facts.json`'s `canonical_python`,
the single Linux lane the 95% coverage floor runs on (see "Line-coverage
floor" below), and what the `ai-readiness` CI job (including its
`repo_facts.json` mypy-baseline recheck) pins to. The separate
`lint-and-types` job that gates `mypy abicheck/` cleanliness on every PR
runs on 3.14, matching the other non-canonical lanes. When in doubt about
which Python to develop against locally, use 3.13.

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
implementation modules, never legacy `cli`/`service` facades. Preserve only
documented public paths through delegation-only facades. The executable
contract and temporary no-growth inventory live in `architecture/`; run
`python scripts/check_architecture.py` for the focused gate. See
[ADR-061](docs/contribute/adr/061-responsibility-package-architecture.md).

## Quick reference

```bash
# Install in dev mode (do this first if pytest/ruff/mypy are missing)
pip install -e ".[dev]"

# Run fast unit tests (THE go-to command — ~43s, ~5400 tests)
pytest tests/ -m "not integration and not libabigail and not abicc and not slow and not golden" -q

# Lint (must pass, CI enforces)
ruff check abicheck/ tests/

# Type check (CI runs this — see "Known mypy issues" below)
mypy abicheck/

# Format check
ruff format --check abicheck/ tests/
```

### M0-3: `scripts/verify.py` — the one verification contract

The four commands above are the everyday inner loop, but they are **not**
the definition of "ready for PR" — the canonical CI unit lane runs golden
tests and enforces a 95% coverage floor that the fast command above
deliberately skips. `scripts/verify.py` is the single executable
orchestrator every consumer (pixi, pre-commit, CI, this file) calls through,
so the local and CI definitions of done cannot silently diverge again:

```bash
python scripts/verify.py --profile fast   # the four commands above, bundled
python scripts/verify.py --profile pr     # exact CI-equivalent PR gate (incl. golden + coverage floor + ai-readiness)
python scripts/verify.py --profile full   # + external-tool/parity/performance lanes, skipped where the environment lacks the tool

python scripts/verify.py --profile pr --list          # show the steps a profile runs, without running them
python scripts/verify.py --profile pr --only lint,typecheck   # run a subset
python scripts/verify.py --profile pr --json receipt.json     # machine-readable pass/fail/skip receipt
```

**Before opening a PR, run `--profile pr` (or `pixi run check`, which calls
the identical command) — not just the fast command above.**
`tests/test_verify_profiles.py` asserts that `pixi run check`,
`.pre-commit-config.yaml`, and `.github/workflows/ci.yml` all route through
`scripts/verify.py`'s step catalog rather than keeping independent copies of
these commands; if you change a check, change it in `scripts/verify.py` and
let that test tell you what else needs updating.

The `bugfix-test-contract` step is the one gate CI can run more of than a
local shell can: its declared half reads the pull request's body. A local run
without `BUGFIX_CONTRACT_BODY_FILE` set still performs the structural half and
then exits **2**, which `verify.py` records as a skip — so the `pr` profile
marks the run incomplete rather than letting it claim parity with a CI job
that can still fail on the body afterwards. A real structural finding is
still exit 1, so it can never be laundered into "partial". Point that variable at a file holding the PR
description to run the whole gate locally.

**`pip install -e ".[dev]"` alone is not full `pr`-profile parity.** The
`docs-build` step needs `mkdocs` (`pip install -e ".[dev,docs]"`) and the
`distribution-build` step needs `build`/`twine` (`pip install -e ".[dev,dist]"`)
— neither is in bare `[dev]`, matching the CI `lint-and-types`/`fair-metadata`
jobs' separate installs. Run `pip install -e ".[dev,docs,dist]"` for full
parity. `verify.py` never silently claims success when *its own* step is
skipped for a missing tool: a `pr`-profile run with any step-level skip
prints an explicit `WARNING: this pr-profile run is INCOMPLETE` line and
sets `"complete": false` in the `--json` receipt — don't treat a
skip-containing run as equivalent to a clean CI pass.

[pixi](https://pixi.sh) is also supported (`pixi install && pixi run test`,
`pixi run check`) and additionally manages the `castxml`/compiler/`libabigail`/
`abi-compliance-checker` system tools for the `integration`/`libabigail`/`abicc`
marker lanes below — see `[tool.pixi.*]` in `pyproject.toml` and
`CONTRIBUTING.md`. Unlike bare `pip install -e ".[dev]"`, pixi's `default`
environment includes the `docs` and `dist` features too, so `pixi run check`
is complete out of the box. Prefer `pip install -e ".[dev]"` above when pixi
isn't available in your environment (add `,docs,dist` for full parity).

## Test markers — know which tests you can run

| Marker | What it needs | When to use |
|--------|--------------|-------------|
| *(default)* | Python only | Always run these — fast, no external deps |
| `integration` | castxml + gcc/g++ | Only if modifying DWARF/ELF parsing |
| `libabigail` | abidiff + gcc/g++ | Only for parity testing |
| `abicc` | abi-compliance-checker + gcc/g++ | Only for parity testing |
| `msvc` | MSVC `cl.exe` (Windows) | Only for the MSVC+PDB end-to-end lane |
| `slow` | varies | Hypothesis/perf benchmarks, skip in normal dev |
| `golden` | golden files | Snapshot tests, skip unless changing output format |

**Default fast command excludes all external-tool markers.** Use it.

## Architecture — module map

Entry points:
- `abicheck/cli.py` — Click CLI (large file, at the 2000-line hard cap; be careful with edits)
- `abicheck/compat/cli.py` — ABICC-compatible CLI wrapper
- `abicheck/__main__.py` — `python -m abicheck` entry

Agent/script integration is via the CLI's structured JSON/SARIF output or
the typed Python API (`abicheck/service.py`, see "Python API" below) —
there is no separate protocol server. An earlier MCP (Model Context
Protocol) server shipped and was later removed; see
`docs/contribute/adr/021-mcp-security-model.md` (retired) for the historical
design.

Core pipeline (in order of data flow):
0. **Model** — `abicheck/model/` owns every shape the stages below agree on
   (ADR-061's innermost ring; see `abicheck/model/AGENTS.md`). A
   `*_metadata.py` module *parses*; the dataclass it parses into lives in the
   matching `model/*_facts.py` and is re-exported by the parser, so
   `from abicheck.elf_metadata import ElfMetadata` still resolves. **A new
   fact's field goes in `model/`, next to the format it belongs to — not in
   the parser.**
   `model/semantic_ir.py` is ADR-063 Phase 6's canonical, backend-independent
   IR (`SemanticIR.occurrences`, keyed by `OccurrenceId` so an ODR-duplicate
   pair is never collapsed; `CanonicalEntity` holds only the non-identity
   payload). Persisted as `AbiSnapshot.semantic_ir` (schema v38,
   `storage/semantic_ir_codec.py`) and reconciled across the two header-AST
   backends by `extract/semantic_ir_merge.py`. **Second slice landed:**
   `extract/semantic_normalizer.py`'s `normalize_header_ast` projects each
   header-AST backend's already-parsed `RecordType`/`EnumType`/typedef
   output (both `dumper_castxml.py` and `dumper_clang.py` already carry a
   real `entity_id` on each, per Phase 2's option (a)) into a real
   `SemanticIR`, wired through `dumper_manifest.resolve_header_ast_result`
   so both the legacy single-TU ELF dump and a real manifest dump populate
   it — a real `dump()`/`compare()` now carries a non-empty `semantic_ir`,
   including through `--ast-frontend hybrid`'s reconciliation. Functions,
   variables, and constants are not normalized yet (a function's/variable's
   canonical *signature* spelling is exactly the still-open cross-backend
   canonicalization problem, not a mechanical projection — see that
   module's own docstring), and the PE/Mach-O header-AST assembly sites in
   `dumper.py` are not yet wired (that file sits at its `architecture/
   debt.yaml` no-growth baseline — see the ELF site's own comment there).
   BTF/CTF/PDB remain fully unmigrated: those backends do not populate
   `entity_id` at all yet.
1. **Parsing** — extract metadata from binaries
   - `elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py` — platform-specific
   - `dwarf_metadata.py`, `dwarf_advanced.py`, `dwarf_unified.py` — DWARF debug info
   - `pdb_parser.py`, `pdb_metadata.py`, `pdb_utils.py` — Windows PDB
   - `btf_metadata.py`, `ctf_metadata.py` — Linux kernel debug formats
   - `sycl_metadata.py` — SYCL plugin interface
2. **Snapshot** — `dumper.py` creates `AbiSnapshot` (model in `model/snapshot.py`)
   - `dumper_castxml.py` — castxml XML → model parser (default L2 header backend)
   - `dumper_clang.py` — `clang -ast-dump=json` → model parser (alternative L2
     backend for clang-only hosts; `--ast-frontend clang` /
     `ABICHECK_AST_FRONTEND=clang`). Both parsers expose the same `parse_*`
     surface behind `dumper._header_ast_parser`.
   - `dwarf_snapshot.py` — DWARF-specific snapshot logic
   - `snapshot_cache.py` — caching layer
   - `dumper_scoping.py` — dependency exclusion, on by default (`dump`/
     `compare --include-system-declarations` opts out, both sharing one
     `cli_options.include_dependencies_option` decorator): drops
     declarations whose own defining header is a toolchain/system header
     (`/usr/include`, MSVC `VC/Tools`, the Xcode/macOS SDK, ...) so a full
     header-AST dump's transitive dependency surface (e.g. SYCL/libstdc++
     declarations pulled in by `#include`) doesn't dominate snapshot size
     for a library with a large dependency stack. A header-*origin* filter,
     not an ABI-visibility one — the library's own private/internal
     declarations are always kept, same as its public ones. `AbiSnapshot.
     dependency_scope` (schema v18) records which mode a snapshot was
     produced under; `comparability.check_contracts_comparable` refuses
     (`ScopeMismatchError`) to compare two sides with a differing explicit
     value, and `service.run_dump`'s `include_dependencies` parameter
     (default `True`, folded into the whole-snapshot disk cache key) is
     what lets `compare`'s own live-binary dumping filter consistently with
     a `dump` baseline instead of always producing the unfiltered surface
3. **Diffing** — compare two snapshots
   - `diff_symbols.py` — function/variable/parameter changes
   - `diff_types.py` — struct/enum/union/typedef changes
   - `diff_platform.py` — ELF/PE/Mach-O specific changes
   - `diff_elf_layout.py` — binary-only (no-DWARF/L0) vtable & RTTI layout diff from `_ZTV`/`_ZTI` symbol sizes
   - `diff_filtering.py` — deduplication and redundancy removal
   - `diff_versioning.py` — symbol version checks
   - `diff_sycl.py` — SYCL-specific diffs
   - `finding_identity.py` — ADR-049 Phase 2: tiered canonical/normalized/
     reduced identity resolution for flat (L0-L2) findings
     (`resolve_function_identity`/`resolve_variable_identity`/
     `resolve_change_identity`), generalizing the mangled-primary +
     name-based extern-C fallback already hand-rolled in
     `diff_symbols._diff_functions`. Mirrors the "most specific available
     identity, ambiguity-safe fallback" principle ADR-045 established for
     flat type matching (`diff_helpers.TypeMap`) and ADR-048 established for
     L5 source-graph nodes (`buildsource/entity_identity.py`). Fully wired:
     `diff_filtering.py`'s cross-detector dedup key uses
     `resolve_change_identity()`, and `diff_symbols.py`'s own old/new
     function and variable matching joins through `SymbolIdentityIndex` —
     the flat-symbol counterpart of `TypeMap`, a `Mapping` over the same
     keys `_public_functions`/`_public_variables` return plus one
     ambiguity-checked alias tier (`unique_alias_match` answers `None` for
     "no candidate" and "several candidates" alike). Unlike `TypeMap`,
     `__getitem__` never resolves an alias, and variables enable no alias
     tier at all: two differing mangled names are two different exports, so
     a display-name join would hide a real removal (the extern-C fallback is
     the one case where one entity is legitimately spelled two ways)
4. **Detection** — classify changes
   - `detectors.py` — individual detection rules
   - `detector_registry.py` — registry pattern for detectors
   - `checker.py` — main comparison orchestrator
   - `checker_types.py` — `DiffResult`, result types
   - `checker_policy.py` — verdict classification (ChangeKind enum lives here)
5. **Policy & Suppression**
   - `policy_file.py` — YAML policy profiles. An unknown `ChangeKind` slug in
     an `overrides:` block is a hard load error (`PolicyError`), not a
     warning-and-skip (ADR-049 D8)
   - `suppression.py` — suppression rules (YAML + ABICC formats)
   - `severity.py` — severity configuration
   - `contract_relevance_types.py` — ADR-049 Phase 0 (accepted): reserved
     contract-mode/relevance vocabulary, reason-code registry, and
     snapshot/decision schema versions. Leaf module; not yet wired into
     detection or reports (see `docs/contribute/plans/public-contract-default.md`)
   - `compatibility_evaluation_config.py` — ADR-049 Phase 1 slice 1: the
     `CompatibilityEvaluationConfig` typed object (contract/evidence/surface/
     assurance/policy/gate/suppressions + field-level `ValueProvenance`).
     Shape only — no service/API front end constructs one from real
     CLI/config/recipe input yet
   - `compatibility_evaluation_resolver.py` — ADR-049 Phase 1 slice 2: the
     field-level precedence resolver (`resolve_field`) implementing D7's
     `explicit_cli/api_request > legacy_alias > run_recipe > run_profile >
     project_config > built_in_default` tier order over already-collected
     `FieldCandidate`s, the conflicting-values/legacy-alias-disagreement
     usage-error rules, and `detect_pack_conflicts` (D8: two selected packs
     assigning different values to the same field *or* `ChangeKind` are a
     usage error unless an explicit override resolves it — one generic
     field-keyed function covers both policy-pack `ChangeKind` overrides and
     contract/gate-pack field assignments). Pure resolution logic
   - `compatibility_evaluation_wiring.py` — ADR-049 Phase 1's per-field
     front-end wirings: `resolve_legacy_contract_mode` (`contract.mode` from
     the real `--scope-public-headers`/`--no-` flag),
     `resolve_internal_namespaces` (from a real `--policy` document),
     `resolve_selected_packs`/`resolve_policy_pack_overrides`/
     `resolve_pack_field_assignments` (real pack manifests → the three
     `*.packs` fields, `policy.overrides`, and — through an explicit
     per-kind route table — a contract/gate pack's own typed target fields;
     an assignment outside its namespace, `contract.mode` included, is a
     hard `PackManifestError`)
   - `compatibility_evaluation_frontend.py` — ADR-049 Phase 1's whole-object
     resolver: assembles one `CompatibilityEvaluationConfig` (all seven
     namespaces + a per-field provenance receipt) from a front end's real
     inputs — `compare`'s own CLI kwargs plus the set of parameters actually
     typed (`--policy`/`--scope-public-headers` carry non-`None` click
     defaults), a typed `CompareRequest`, and the project's `.abicheck.yml`.
     `cross_front_end_differences()` is this phase's gate as an executable
     check: equivalent CLI and API input must resolve equally, modulo only
     which front end stated a value. Resolution only — it changes no verdict,
     finding, or exit code (Phase 7 owns the default flip). ADR-049 Phase 5
     wired the native `compare` CLI to it: `cli_compare_receipt.py` resolves
     one object per invocation from the raw CLI values + which parameters
     Click reports as typed + the discovered `.abicheck.yml` + a selected
     `--profile` (`RunProfileInputs`, D7's `run_profile` tier), and installs
     it via `contract_context.with_resolved_config` so the persisted
     `evaluation_context` carries real per-field D7 provenance instead of
     `checker.compare`'s honest `API_REQUEST` under-claim. The gate is the
     one split: values from `resolve_compare_config` (what actually scored
     the run), provenance from this resolver, with
     `tests/test_cli_compare_config_receipt.py` asserting the two agree.
     Reference: `docs/reference/compatibility-evaluation-config.md`
   - `pack_application.py` — ADR-049 D8's *application* layer: what turns a
     selected `--pack` manifest into something the engine runs, rather than
     a line in the receipt (a first `--pack` was reverted before merge for
     being exactly that). Deliberately not a second resolver — it reads back
     off the already-resolved `CompatibilityEvaluationConfig` only the
     fields whose `ValueProvenance.source_kind` is `pack_manifest`, so a
     value D7 precedence ruled out is unreachable from here, and folds them
     into the two objects the run is scored from: a `PolicyFile`
     (`policy.overrides`, `surface.internal_namespaces`) and the resolved
     compare config (`gate.exit_code_scheme`, `gate.severity.*`). Ordering
     matters: the config is resolved from the *explicitly given*
     `--policy` document and only then folded, since folding first would present
     a pack's override to the resolver as an explicit one.
     `UNAPPLIED_PACK_FIELDS` is the enforcement half — a routable field with
     no engine consumer (`contract.overlays`, `assurance.require_evidence`)
     is a usage error rather than a silently inert assignment, and it is the
     complement of what is applied, so a newly-routable field is applied or
     listed, never neither. `contract.unresolved` left that list in Phase 7,
     when the coverage exit gave it a consumer. Three more routes are
     rejected for adjacent reasons: a field whose consumer only runs under
     contract evaluation when no `--contract` was given
     (`CONTRACT_EVALUATION_ONLY_FIELDS`), a value the runtime does not act on
     (`INERT_PACK_VALUES`), and a manifest whose `assignments` mapping is
     empty — each is a pack recorded as active configuration that changes
     nothing, which is the single failure all of these guard. `compare`
     takes all three kinds; `scan --against` now takes all three too (CLI
     cleanup phase two, "PR B" slice 3) — a `kind: gate` pack's
     `gate.exit_code_scheme`/`gate.severity.*` fold onto the real
     `ResolvedCompareConfig` `resolve_compare_config` already produces
     (`pack_application.apply_to_compare_config`, the identical function
     single-pair `compare` uses, called from `cli_scan._resolve_scan_
     evaluation_config`), since `scan`'s exit code has honored the resolved
     severity/exit-code-scheme config since the fix that closed the "scan
     never consults severity" gap below. The directory/package
     release fan-out (`cli_compare_release.py`) takes all three kinds too,
     since CLI cleanup phase two's "PR B" slice 2 — it has no `GateOptions`-
     shaped object of its own, so a `kind: gate` pack's `gate.exit_code_
     scheme`/`gate.severity.*` are folded into the fan-out's own raw
     severity/exit-code-scheme strings by `cli_compare_release_helpers.
     apply_release_gate_pack`, called once before every downstream consumer
     of those strings reads them (see that plan section for what's still
     open — the full `GateOptions` unification, reassigned to PR G2's own
     prerequisite work rather than attempted reactively inside PR B; PR B's
     other stated goal, the effective-config digest, has already landed for
     the native compare/release JSON path, the `--stat` JSON summary, and
     `scan --against` JSON -- non-JSON renderers (Markdown, review, SARIF,
     JUnit, HTML) and `compat check` don't carry it, see that plan
     section's own PR B note for the exact scope). Two review findings
     worth not rediscovering: the gate application must *read* the resolved
     `gate.exit_code_scheme` rather than re-derive one (re-deriving let a
     severity-only gate pack override an explicit `--exit-code-scheme
     legacy`), and manifest validity is checked ahead of `compare`'s
     `--dry-run` emit — but pack-vs-pack conflict detection is not, since
     D8 exempts a field another layer states and those layers aren't
     resolved that early
   - `contract_evaluation.py` — ADR-049's contract-relevance evaluator: one
     `ContractEvaluationDecision` (relevance + stable reason code +
     assurance) per already-emitted finding. Computed only when `compare`
     is given `--contract`, which both activates the evaluation and selects
     the evidence domain it judges against: `public|exports|all` name one
     (ADR-049 Phase 6), while `auto` activates without naming one and lets
     D7's lower tiers decide. Under `auto` the domain follows
     `--scope-public-headers`/
     `--no-scope-public-headers`, and an explicit value outranks that legacy
     alias via `compatibility_evaluation_wiring.resolve_legacy_contract_mode`
     (D7 precedence). **No longer advisory** — see `contract_pipeline.py`
     below: since ADR-049 Phase 7 the decision runs *before* compatibility
     policy and determines whether policy scores the finding at all, so
     selecting a domain can change a verdict, a finding set, and an exit code
   - `contract_scoped_promotion.py` — ADR-049 §4.3 item 1's evidence tier and
     everything it implies. The evaluator above answers relevance from
     *snapshot* evidence; this module answers the one question that evidence
     cannot — a run given `--used-by` or `--required-symbol` has been *told*
     what the contract is, and §4.3 ranks a caller's explicit consumer or
     entrypoint above anything two snapshots can show. So it runs after
     `compare()` returned, over the collections a scoping pass built, and
     only ever promotes (to `IN_CONTRACT`, one reason code). Since Phase 7
     that promotion is not cosmetic, which is why each function carries the
     consequences with it: the finding's own `compatibility_decision`, the
     verdict it may raise (`recompute_verdict_after_promotion`, monotonic —
     it can raise a verdict, never lower one, since the set `compare()`
     scored is not recoverable from the `DiffResult`), the gate contribution
     a missing-contract label makes, and the receipt row recording all three.
     Depends on `contract_evaluation` one way; nothing there imports back
   - `contract_pipeline.py` — ADR-049 D9's normative order, made executable:
     relevance is classified *before* compatibility policy, and policy then
     scores only the `EVALUATED` findings (`IN_CONTRACT`/`NOT_APPLICABLE`).
     Split into `build_contract_stage()` (the expensive half — mode
     resolution, both sides' public and export surfaces, the provider-evidence
     ledger; once per comparison) and `ContractEvaluationStage.classify()`
     (idempotent per finding, called at each point `compare()` computes or
     recomputes a verdict, since `--surface-metrics`/`--pattern-verdicts`
     append findings after the first pass). `record_compatibility_decisions()`
     and `build_context()` close the run: D1's per-finding
     `compatibility_decision` (JSON `null` for a `NOT_EVALUATED` finding —
     "policy did not run", not a sixth verdict) and Phase 4's persisted
     context over every finding the stage saw, ledgers included. Decides no
     exit code itself; `contract_gating.py` is the leaf predicate
     `checker._compute_verdict_for` and `severity.compute_exit_code`/
     `compute_gate_decision` share so the verdict and the gate cannot exclude
     different sets. An **unstamped** finding is evaluated, which is what
     keeps every run without `--contract` bit-for-bit unchanged
   - `contract_evidence_collect.py` — ADR-049 Phase 3's *observed provider
     ledger* (plan §4.1) and the raw type graph Phase 4 persists. Produces
     one `EvidenceSearchRecord` per (provider, side) — `public_header`,
     `export_table`, plus the `post_manifest`/`forced_public_symbols`
     overlays when a run configures them — each with its own status,
     completeness, identity coverage, requested-vs-searched scope and
     content digest, so a provider failure stays scoped to its own domain.
     Also owns the `decl:`/`record:`/`enum:`/`typedef:`/`alias:` node
     encoding of `TypeGraphSnapshot` and the closure walk over it, and maps
     a decision's reason code to the records it rests on
     (`evidence_refs_for_reason` → `Change.contract_evidence_refs`). "Not
     consulted" is deliberately encoded as an absent entry, never as a
     failed one
   - `contract_coverage_ledger.py` — ADR-049 Phase 5's *unsuppressible*
     sibling ledger (plan §6.1/§6.2, Definition-of-done item 6). Derives
     `contract_coverage_failures` from the observed provider records **for
     the selected `--contract` domain** — the same record is a failure under
     one domain and advisory under another (§7), so it is answered per mode
     rather than recorded at collection time, which would also go stale under
     `reevaluate_from_evidence`. Unsuppressibility is structural: a
     `CoverageFailure` is not a `Change` (no `ChangeKind`, no symbol, never
     in `DiffResult.changes`), so `checker._filter_suppressed_changes` — the
     one place suppression is applied — cannot see one;
     `suppression_reaches_coverage_failures()` is the executable *proof* of
     that, not its enforcement. `coverage_exit_contribution()` states §6.1's
     `0`/`1`. Emitted by `reporter.py` under `--contract`
     (report schema 2.26), `[]` rather than omitted when a domain closed
   - `contract_coverage_exit.py` — ADR-049 Phase 7: the step that turns the
     ledger's `0`/`1` into a real exit code. Deliberately the *only* place
     that fold happens, and it is `max` — §7's orthogonality means a
     coverage failure raises a clean `0` to `1` and can never lower a gate's
     `2`/`4` (that would demote a real ABI break to "warnings only"), and it
     never rewrites a finding's compatibility decision or gate contribution.
     `compare` folds it inside `cli._exit_with_severity_or_verdict` rather
     than at each call site, so a command cannot pick up a compatibility
     exit and forget the orthogonal one; `cli_scan_baseline.py` folds the
     same function, since a ledger gating one command and not the other is
     exactly §6.4's cross-command divergence. `contract.unresolved=warn`
     (D9) zeroes the floor and changes nothing else — the failures stay
     listed and unsuppressible, because accepting incomplete assurance is
     not hiding it. `reporter.py` emits *this* function's answer as
     `contract_coverage_exit_contribution`, so the number a user reads is
     the one that gated them. `0` whenever no contract context exists: a run
     without `--contract` has no selected domain to be short of
     evidence for, which is what keeps every pre-existing invocation's exit
     code unchanged
   - `contract_context.py` / `contract_context_io.py` / `contract_replay.py`
     — ADR-049 Phase 4's assembly, JSON round-trip, and the two procedures
     D6 names. `checker.compare(..., contract_evaluation=True)` returns a
     `PersistedContractContext` on `DiffResult.contract_context`, which
     `reporter.py` emits as the report's `contract_context` block (all three
     JSON paths). `replay_original_decisions()` reproduces a recorded
     decision from the receipt *alone* — this build's provider defaults
     cannot alter it — while `reevaluate_from_evidence()` re-decides
     findings from the same persisted, policy-independent observations under
     a *different* contract mode, with no re-collection and no live
     re-probe. Both fail closed on a version counter newer than this build
     (`load_replayable_context`); a mixed-version context is the ordinary
     re-evaluation case, not an error. The replay evaluator is deliberately
     narrower than the live one and may only ever *weaken* a decision —
     `compare_decisions()` checks that direction rather than equality. Note
     the blocks are persisted with the *comparison*, not inside
     `AbiSnapshot`: the evidence is two-sided by construction, is derived
     from content the snapshot already carries, and a snapshot field would
     mean a `SCHEMA_VERSION` bump inside the ADR-050 comparability contract
     (reasoning recorded in the plan's Phase 4 section)
   - `export_surface.py` — ADR-049 `contract=exports`'s evidence provider
     (`compute_export_surface`): roots are the declarations present in the
     binary's *observed* export table (ELF `.dynsym` / PE export directory /
     Mach-O export trie), closure is the raw record/enum/typedef graph walk
     — reusing `surface.py`'s own closure walk, so only the seeds differ.
     Deliberately not `surface.py`'s domain: no header-origin demotion
     applies, and an uncaptured (or empty) export table leaves the surface
     `resolvable=False` rather than claiming "exports nothing". Its
     `exclusion_is_provable` gate is what any `PROVEN_OUT_OF_CONTRACT`
     decision rests on, and it fails closed on four independent kinds of
     incomplete evidence: no observed table, no resolved root, an untyped
     root, an unaccounted export, or an unresolved *type edge* (a signature/
     field/base spelling naming nothing the snapshot carries — resolved
     through `type_reachability.py`'s namespace-suffix and stdlib-stripping
     machinery, so a bare `string` for `std::string` still resolves)
6. **Reporting** — output results
   - `reporter.py` — JSON/Markdown/text output
   - `html_report.py` — HTML reports
   - `sarif.py` — SARIF 2.1.0 output
   - `junit_report.py` — JUnit XML output
   - `report_summary.py`, `report_classifications.py` — report helpers
   - `report/` — ADR-061 Phase 2's canonical `ReportDocument` and the pure
     projection every format now goes through (`render_json.py` covers
     SARIF too; also `render_xml.py`, `render_text.py`, `render_markdown.py`,
     `render_html.py`). Markdown/HTML split two ways: a `compute_*` half in
     `reporter_markdown.py`/`html_report.py` returning frozen structs of
     plain values, a `render_*` half here that formats and decides nothing —
     **add a report section to that pair, never to a renderer alone**
     (`abicheck/report/AGENTS.md`)
7. **Application compatibility** — `appcompat.py`, `appcompat_html.py`
8. **Utilities**
   - `binary_utils.py` — binary file helpers
   - `binary_fingerprint.py` — rename detection via fingerprinting
   - `demangle.py` — C++ name demangling
   - `classify.py` — symbol classification
   - `annotations.py` — annotation handling
   - `errors.py` — exception types
   - `serialization.py` — snapshot serialization (`load_snapshot`/
     `save_snapshot`/`write_snapshot` — the public compatibility surface)
   - `snapshot_io.py` — ADR-059's canonical snapshot *storage envelope* I/O:
     plain/gzip/zstd detection (magic bytes), atomic + deterministic
     compressed writes, decompression-bomb limits. A dependency-free leaf
     module `serialization.py`/`snapshot_cache.py`/CLI code build on it
   - `package.py` — package/archive handling
   - `debian_symbols.py` — Debian symbols file adapter
   - `environment_matrix.py` — multi-env comparison
   - `binder.py` — symbol binding logic
   - `resolver.py` — symbol resolution
   - `type_metadata.py`, `dwarf_utils.py` — shared type helpers
   - `change_registry.py` — change kind registry
   - `service.py` — service layer (Python API)
   - `service_compare_pipeline.py` — ADR-055 D1: `run_compare_request`'s two
     phases (`resolve_compare_request` / `classify_compare_pair`), split so
     the native `compare` CLI can run its Click-dependent ADR-049
     `resolve_and_apply` step between them and still share one resolution
     with the typed API instead of keeping a second copy (historically also
     with the now-removed MCP server — see ADR-021).
     `resolve_sides_sequentially` owns the one rule about resolving both
     sides concurrently (a `dump_manifest` on either side, or
     `ABICHECK_PARALLEL_EXTRACTION=0`, forces sequential — a manifest dump
     sizes its per-TU pool off a live `MemAvailable` reading, so two at once
     jointly overcommit)
   - `service_input_resolution.py` — G33 Phase 5: the per-*input* primitives
     `compare` and `dump` share (`resolve_side_snapshot`,
     `embed_side_build_source`, `enforce_requested_depth`,
     `reject_hybrid_source_frontend`). All of it was
     `service_compare_pipeline`'s private helpers, lifted out of the pair and
     re-expressed for one input so a change to how an input resolves lands on
     both commands at once. The pair-shaped decisions deliberately stayed
     behind — the pair-wide C++20 dialect override exists because two sides
     must agree on a standard, and the concurrency rule is about two
     extractions at once; neither means anything for a lone dump
   - `service_dump_pipeline.py` — G33 Phase 5: `run_dump_request`, `dump`'s
     counterpart to `resolve_compare_request`. `resolve_input` was already the
     one way to turn a path into a snapshot, but the four steps a real `dump`
     does *around* it (collect-mode inference, inline L3-L5 embedding, the
     dependency walk, the depth floor) lived only in `cli.py`'s `dump_cmd` —
     which is why the now-removed MCP `abi_dump` tool historically sat at a
     five-argument subset of what `abicheck dump` accepts. Deliberately
     excludes the CLI's provenance/
     presentation layer (git/build-id stamping,
     `fold_dump_provenance_into_json`). Since CLI cleanup phase two's PR 3A
     the native `dump` CLI *does* build a real `DumpRequest`
     (`cli_dump_request.py`) and `--dry-run` renders from
     `resolve_dump_request`'s `ResolvedDumpRequest`. The real **ELF** run now
     executes through `execute_dump_request` too (PR C, landed) — the same
     shared pipeline `compare`'s implicit-dump operand and `scan`'s
     candidate resolution already use, with the legacy `-p`/`--compile-db`
     auto-match threaded through as an explicit pass-through rather than a
     typed-API field (`execute_dump_request`'s own docstring). PE/Mach-O
     now routes through the identical shared executor too (ADR-063 Phase
     1) — `handle_non_elf_dump` stays defined, unchanged, only for its own
     direct unit tests, never called from the CLI's real dispatch;
     verified via mock-based CLI/unit tests only, since no PE/Mach-O
     toolchain was available to verify a migration against a real binary.
     See the "PR C" entry under "Known gaps" for the full ELF account,
     including the L4 source-extractor default change that migration
     carries
   - `cli_dump_request.py` — CLI cleanup phase two, PR 3A: `dump_cmd`'s ~30
     Click parameters as one `DumpRequest`, plus the Tier-2-to-Click error
     translation the boundary owes. Fed the CLI's *already-resolved* compile
     context/frontend/language decision rather than re-deriving them, so the
     resolved object records the run instead of forming a second opinion
     about it
   - `stack_checker.py`, `stack_report.py`, `stack_html.py` — stack analysis
9. **Build-source evidence (optional L3–L5 layers)** — `buildsource/` package
   (collect/merge/source-ABI replay/source graph; ADR-028…033). See
   `abicheck/buildsource/CLAUDE.md` for its module map.

10. **Published Agent Skills (ADR-058)** — `skills-src/` is the one
   hand-authored source (one `SKILL.md` in Layer A — the portfolio was
   reset to a single internal candidate, `check-abi-compatibility`
   (renamed from `review-native-library-change`), see `skills-src/
   CLAUDE.md`'s portfolio-status table and ADR-058's 2026-08-20
   amendments — plus one `shared/` tree of Layer-B domain fragments);
   `scripts/gen_agent_skills.py` publishes it into three self-contained
   trees (`.agents/skills/`, `.claude/skills/`, `.gemini/skills/`) — build
   output, not committed (2026-08-21 ADR-058 amendment): CI regenerates them
   itself via `gen_agent_skills.py --check`/`gen_agent_skills.py`, and
   `scripts/install_dev_skill.py` writes them locally on demand for
   exercising an installed skill. Never hand-edit the generated trees. See
   `skills-src/CLAUDE.md`.

Beyond the core package: `.github/AGENTS.md` (CI/workflow architecture),
`action/AGENTS.md` (the composite GitHub Action's shell-script layer), and
`contrib/abicheck-clang-plugin/AGENTS.md` (the optional Clang facts plugin)
cover the surrounding first-party trees this file doesn't detail.

## Key types

- `AbiSnapshot` (`model/snapshot.py`) — serializable snapshot of a library's ABI surface
- `DiffResult` (`checker_types.py`) — single detected change with kind, severity, details
- `ChangeKind` (`checker_policy.py`) — enum of 397 change types; categorized into `BREAKING_KINDS`, `API_BREAK_KINDS`, `RISK_KINDS`, and `COMPATIBLE_KINDS` (further split into `ADDITION_KINDS` and `QUALITY_KINDS`)
- `Verdict` (`checker.py`) — overall comparison result (compatible/source_break/breaking)
- `LibraryMetadata` (`checker.py`) — parsed library info

## Adding a new ChangeKind

1. Add a `("NAME", "value", "optional doc comment or None")` triple to
   whichever of `abicheck/model/change_catalog/kind_names_{1,2,3}.py` is
   shortest at the time (ADR-061 D9's model-vs-policy split moved
   `ChangeKind` itself out of `checker_policy.py`; see `kinds.py`'s own
   docstring for why it's split three ways instead of one class body). The
   third element is required — `kinds.py` unpacks each entry as
   `name, value, _comment`, so a bare two-element `("NAME", "value")` pair
   raises `ValueError` on import; pass `None` there if the kind needs no
   comment. Then run
   `python scripts/gen_changekind_stub.py` to regenerate the matching mypy
   stub, `kinds.pyi` — required, since mypy type-checks against that stub
   file instead of the real runtime module (`gen_changekind_stub.py --check`
   catches a forgotten regeneration). `checker_policy.py` still re-exports
   `ChangeKind`/`HasKind` unchanged, so every existing `from .checker_policy
   import ChangeKind` call site is unaffected.
2. Add ONE `ChangeKindMeta` entry (kind string, `default_verdict`, required
   `impact`, optional `description_template`) to the taxonomy module under
   `abicheck/model/change_catalog/` that matches which detector actually
   produces the kind (ADR-061 D9 — see each module's own docstring for its
   scope and the categorization methodology):
   - `symbols.py` — function/variable/parameter/constant/Python-API facts
     (`diff_symbols.py` and siblings)
   - `types.py` — struct/class/union/enum/typedef/layout/vtable facts
     (`diff_types.py` and siblings)
   - `platform.py` — ELF/PE/Mach-O container facts, DWARF presence, symbol-
     table representation, hardening flags, toolchain-mode ABI traits,
     symbol versioning, kABI, SYCL (`diff_platform.py` and siblings)
   - `build.py` — L3 build-evidence facts, bundle/release coherence, wheel/
     NumPy packaging facts (`buildsource/build_diff.py` and siblings)
   - `source.py` — L4/L5 source-ABI-replay and semantic-source-graph facts,
     public/private surface reconciliation, declaration identity
     (`buildsource/source_diff.py` and siblings)

   `abicheck/change_registry.py` is now a pure assembly point (imports each
   taxonomy's entry list, constructs the single production `REGISTRY`) — it
   holds no `ChangeKindMeta` entries itself; don't add one there. `impact`
   must be non-empty — `ChangeKindRegistry` rejects an entry with no
   `impact` text at construction time (the production `REGISTRY` is built
   at import time, so this fires then in practice); `description_template`
   stays genuinely optional. **Do NOT hand-edit `BREAKING_KINDS`/
   `API_BREAK_KINDS`/`COMPATIBLE_KINDS`/`RISK_KINDS` in `checker_policy.py`
   directly** — those are `frozenset`s *derived* from the registry at import
   time (`_kinds_for(...)`); the registry entry's `default_verdict` is what
   actually places a kind into one of them, and the import-time completeness
   assertion checks the derived sets, not a set you'd edit by hand.
3. Implement detection in the appropriate diff module, registered via
   `@registry.detector("...")` (`detector_registry.py`) the way the
   neighboring detectors in that file are.
4. Add unit test.
5. **Classify the kind for canonical identity** in
   `tests/canonical_identity_contract.py` — put it in exactly one of
   `TYPE_BEARING` (its `old_value`/`new_value` hold C/C++ type spellings, so
   they must be canonicalized, which also means adding it to
   `finding_identity._TYPE_BEARING_DISCRIMINATOR_KINDS`),
   `VALUE_INSENSITIVE` (identity does not vary with value spelling because the
   kind resolves through an `_EQUIVALENT_CHANGE_CATEGORIES` entry), or
   `UNVERIFIED` (the call site has not been read yet — an explicit backlog
   entry, not a verdict). `tests/test_canonical_finding_id_completeness.py`
   fails until the kind is in a bucket. This step exists because PR #753
   shipped `canonical_finding_id` with three type-slot kinds silently omitted
   from that set and PR #759 had to add them hours later: a *missing* entry
   produced no failure anywhere, so 12 targeted tests and a 26k-test suite all
   passed against the gap. The judgement stays manual (an automatic
   classification was proposed and rejected — see the set's own comment); only
   the exhaustiveness is mechanical.

## Conventions

- **Commits**: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`)
- **Branches**: `feat/<name>` or `fix/<name>`
- **Python**: 3.10+ syntax, type annotations, `from __future__ import annotations`
- **No line length limit** (ruff E501 ignored)
- **Tests**: use `assert` freely; parametrize when possible
- **Changelog**: if your change touches `abicheck/**/*.py`, add a fragment
  with `scriv create` — writes `changelog.d/<name>.md`; uncomment one
  `### <Category>` section and describe the change (see
  `changelog.d/README.md`). Do **not** hand-edit `CHANGELOG.md`'s
  `## [Unreleased]` section — CI (`changelog-check.yml`) rejects a PR that
  touches `abicheck/**/*.py` without a fragment, and every PR editing that
  shared section directly was the reason it kept conflicting.

## Decision-making principles

- **Time estimates are not a factor in technical decisions.** Don't scope,
  simplify, defer, or pick an implementation approach because it's
  "faster" or "quicker to ship" — there is no deadline or velocity
  criterion here. Judge an approach purely on correctness, generality, and
  fit with the codebase's existing architecture. If a thorough fix is the
  right one, do the thorough fix; don't downgrade to a smaller patch on
  time-cost grounds.
- **Fix the cause, not the instance.** When you find a bug or a reported
  problem, don't stop at a patch for the one call site or input that
  triggered it. Trace it to its root cause, and implement a generalized
  fix — one that closes the whole class of failure, not just the observed
  case — plus generalized tests that state the underlying primitive's or
  detector's contract as invariants (property-style tests, per this
  file's own "Primitive-level property tests" guidance below), not only a
  regression test pinned to the original repro — a fixed-example test only
  forecloses the one input it names. If a genuinely general fix isn't
  feasible in one pass, say so explicitly and record the gap (see "Known
  gaps" below) rather than quietly shipping a narrow patch as if it were
  the complete fix.
- **A bug fix's regression test targets the bug *class*, not the one
  reported input.** This sharpens the previous bullet into a concrete,
  checkable requirement for the bug-fix test contract
  (`.github/PULL_REQUEST_TEMPLATE.md`'s "Bug class" / "General invariant"
  rows, enforced by `scripts/check_bugfix_test_contract.py`). A repository
  audit of this codebase's own fix history found a repeated pattern behind
  its worst escapes: the shipped test proved the *reported* input was now
  handled correctly, the class was described only in prose (a PR body, or
  a "Known gaps" entry below), and the next defect was a sibling case the
  same mechanism still got wrong — #699→#721 (a compression window-size
  formula tested against itself, at a toy scale that never reached the
  bug), #753→#759 (a missing registry entry that failed nothing, anywhere),
  #705→#758 (a workflow-injection defense that asserted file *text* instead
  of executing the attack). None of these needed a cleverer reviewer; they
  needed the invariant to be executable and adversarially generated, not a
  fixed-input assertion plus a paragraph explaining the class. Concretely,
  "General invariant" in the PR contract is not answered by prose alone —
  the named regression test must exercise the invariant with inputs beyond
  the one reported (generated/property-based, an exhaustive small-domain
  enumeration, or at minimum several independently-chosen sibling cases),
  against a stated oracle that is not the same formula/helper the
  implementation itself uses. See
  [`docs/contribute/plans/bug-class-regression-testing.md`](docs/contribute/plans/bug-class-regression-testing.md)
  for the full analysis, the named bug classes it identifies, and the
  phased plan closing the specific generalized-test gaps that analysis
  found still open — check there before writing a narrow reproducer for a
  mechanism a class already covers. `tests/regressions/manifest.py` (that
  plan's Phase 1) is the queryable registry: check `BUG_CLASSES`/`get()`
  there first for a matching `BugClass.id` before restating an invariant
  from scratch, and add an entry there — not just prose — when a fix
  closes a genuinely new class.

## Known mypy issues

CI runs `mypy abicheck/` as a required gate. The baseline is currently **0 errors** — the previously-documented 26 errors were all `unused-ignore` / `no-any-return` / `misc` warnings on third-party calls (pyelftools, click). They are suppressed in `pyproject.toml` via per-module `disable_error_code` overrides, which keeps the file portable across mypy releases without churning the underlying `# type: ignore` comments.

**Your responsibility**: run `mypy abicheck/` after your changes and ensure it stays clean. If a new third-party suppression is needed, extend the existing `disable_error_code` override for that module rather than scattering ad-hoc `# type: ignore` comments. If you legitimately reduce a real error to zero, leave `MYPY_ERROR_BASELINE = 0` in `scripts/check_ai_readiness.py` — it now warns on drift in either direction.

## AI-readiness gate

`scripts/check_ai_readiness.py` runs in CI as a fast structural gate. It checks:

| Check | Severity | What it enforces |
|-------|----------|------------------|
| `file-size` | ERROR > 2000 lines, WARN > 1500 | Every first-party Python tree (`abicheck/`, `scripts/`, `tests/`, `eval/`, `validation/`, `action/`, the clang plugin's `tests/` — `FIRST_PARTY_PY_ROOTS`) stays legible. `LARGE_FILE_ALLOWLIST` downgrades a specific pre-existing violator to WARN with a reviewed reason — it is not a way to silently exempt a new file |
| `claude-md-coverage` | ERROR | `CLAUDE.md` exists in each original major sub-tree (`REQUIRED_CLAUDE_MD_DIRS`, which now also covers `skills-src/`) |
| `agent-instructions-coverage` | ERROR | `AGENTS.md` or `CLAUDE.md` exists in `.github/`, `action/`, `contrib/abicheck-clang-plugin/` (`REQUIRED_AGENT_INSTRUCTION_DIRS`) |
| `script-inventory` | WARN | Every `scripts/*.py` is named in `scripts/CLAUDE.md`'s inventory table — an unlisted script is invisible to that discovery path |
| `generated-file-ownership` | ERROR | A known-generated file (`GENERATED_FILE_MARKERS`, plus every `docs/reference/examples/case*.md`, plus every `*.md` under the three generated agent-skill trees — `.agents/skills/`, `.claude/skills/`, `.gemini/skills/` — scoped to the skill directories `scripts/gen_agent_skills.py` actually owns, so a hand-authored skill sharing an output root is not flagged) still carries its "this is generated, don't hand-edit" marker comment |
| `test-ratio` | WARN | At least 20% test-to-source file ratio; test files are discovered recursively under `tests/` (not just top-level) |
| `future-annotations` | WARN | `from __future__ import annotations` per this file's convention |
| `changekind-partition` | ERROR | Every `ChangeKind` is in exactly one of `BREAKING_KINDS` / `API_BREAK_KINDS` / `COMPATIBLE_KINDS` / `RISK_KINDS` |
| `changekind-detector` | WARN | Every `ChangeKind` is produced somewhere (not orphaned) |
| `changekind-docs` | WARN | Every `ChangeKind` is mentioned in `docs/` |
| `doc-count-sync` | ERROR on drift, WARN if anchor moved | Headline counts in docs (ChangeKind count, example-catalog size) match their source of truth (`len(ChangeKind)`, `ground_truth.json`) — this file (`AGENTS.md`) is included in the generic sweep, same as `README.md`/`CLAUDE.md` |
| `cli-contract` | ERROR | No *unallowlisted* front-end `cli*.py`/`appcompat.py`/`compat/cli.py` module calls a Tier-1 core entry point (`checker.compare`, `dumper.dump`, `service.resolve_input`) directly — it must route through the Tier-2 service (`service.run_compare`/`compare_snapshots`, `service.run_dump`/`service_dump_pipeline.run_dump_request`, `service_input_resolution.resolve_side_snapshot`); ADR-037 D10.1, extended to the latter two per Phase 0 item 2 of `docs/contribute/plans/duplication-and-convergence-assessment.md`. A small set of reviewed, line-pinned legacy exceptions remain permitted via `CLI_CONTRACT_ALLOWLIST` in `scripts/check_ai_readiness.py` — the gate rejects only a *new*, unlisted direct call |
| `engine-cli-boundary` | ERROR | No engine-layer module (`scan_engine.py`, `service*.py`, `artifact_*.py`, `buildsource/**/*.py`, `workflows/artifact/**/*.py`) imports `click` or a `cli_*` sibling — the CLI is a frontend adapter over the engine, not the reverse. `ENGINE_CLI_BOUNDARY_ALLOWLIST` records today's pre-existing inversions (`scan_engine.py`'s own `click.ClickException`/lazy `cli_scan_baseline`/`cli_scan_helpers` imports, three `service*.py` modules' lazy `cli_*` imports, `buildsource/evidence_policy.py`'s `click`) the same allowlist-and-shrink way `IMPORT_CYCLE_ALLOWLIST` does — a new site outside the allowlist fails outright; closing a listed one is Phase 1 of `docs/contribute/plans/duplication-and-convergence-assessment.md` |
| `fact-detector-misuse` | ERROR | ADR-063 Phase 0 (`docs/contribute/plans/one-semantic-pipeline.md`): no direct `==`/`!=` comparison of a `Fact[T]`-typed value (a `<attr>_fact` field access, or a `Fact(...)`/`Fact.<classmethod>(...)` constructor call) anywhere under `abicheck/` — a detector must unwrap via `.status` first, never compare two `Fact[...]`s (or a `Fact[...]` against a bare value) directly, since `Fact[T]` deliberately doesn't override `__eq__` and a direct comparison silently falls back to structural dataclass equality over `status`/`value`/`diagnostics` together. Real, repo-wide AST scan (`scripts/fact_detector_misuse.py` + `fact_detector_misuse_aliases.py`/`fact_detector_misuse_scope.py`), resolving same-function local aliases, annotated parameters, constructor-classmethod aliases, and closure-scope shadowing — not a naive textual match. No baseline: any match is an unconditional error |
| `fact-field-readers` | ERROR | ADR-063 Phase 0 (`docs/contribute/plans/one-semantic-pipeline.md`): no function outside `EXEMPT_FUNCTIONS` reads a `Fact[T]`-bridged legacy field (`RecordType.bases`/`virtual_bases`/`vtable`/`vptr_offset_bits`, `Param.is_va_list`) directly — via a plain attribute access, a `getattr(obj, "name", ...)` call (including a resolved `getattr`/`builtins` alias, excluding one locally shadowed by a parameter), an `operator.attrgetter(...)` call or a bound/unbound `__getattribute__` call (each through a resolved alias too), an `ast.AugAssign` target (`rec.bases += x`, an implicit read before the write), or a `case RecordType(bases=[]):` structural-pattern match (keyword or positional) — without first consulting its `Fact[...]` sibling's `.status`, which would collapse "confirmed empty/false" and "no evidence" onto the same value. Real, repo-wide AST scan, not a `diff_*.py` glob. `KNOWN_UNMIGRATED_READERS` records every currently-known reader site the same allowlist-and-shrink way `IMPORT_CYCLE_ALLOWLIST` does, keyed by enclosing function, attribute, the read's own outermost containing expression, its own exact source text, and a per-site occurrence rank — a new, unlisted site fails outright |
| `import-cycle-growth` | ERROR | No *unapproved* strongly-connected-component growth within `abicheck/` — not literally "no import cycles": a large, deliberately-baselined CLI-registration SCC already exists and is allowed (`IMPORT_CYCLE_ALLOWLIST`). The invariant is that no *new* module joins it and no *new* separate SCC forms; extending the allowlist to unblock a fresh cycle needs an ADR or explicit architectural sign-off, not a routine edit (CLAUDE.md "M1-3") |
| `mypy-baseline` | ERROR if drifted up | mypy error count ≤ documented baseline |
| `examples-ground-truth` | ERROR | Every `examples/case*/` has a `README.md` and an entry in `ground_truth.json` |
| `examples-readme-sync` | ERROR | `examples/README.md` headline count, verdict distribution, and case-index rows match `ground_truth.json` (catches missing/stale catalog rows) |
| `mkdocs-nav-coverage` | WARN | Every `docs/**/*.md` is in `mkdocs.yml` nav or linked from another doc |
| `adr-index-nav-sync` | ERROR | Every `docs/contribute/adr/*.md` is linked from `adr/index.md`, and the ADR index page itself (not each individual ADR — relaxed, since that overloaded top-level nav with 50+ flat entries for no reader benefit) is listed in `mkdocs.yml`'s nav, so every ADR stays reachable from published navigation (this is what originally caught ADR-041 going missing from nav despite being accepted). Also requires every ADR to carry a Status metadata line/heading, and an ADR whose status leads with "Superseded" to link to its replacement |
| `adr-status-sync` | ERROR on contradiction / bad receipt, WARN on staleness | An ADR's own `**Status:**` line and its row in `adr/index.md` may not *contradict* each other — one claiming nothing is implemented while the other claims something is (how ADR-056's row went stale), or disagreeing on the decision word. Paraphrase is explicitly allowed: the index cell is an abridgement, and a stricter prototype flagged 15 of 56 ADRs, nearly all false positives. Separately validates the optional `**Verified:** <ref>@<sha> on <YYYY-MM-DD>` receipt (see `adr/index.md`'s convention section): exactly one per ADR, well-formed, a real non-future date, and naming a commit reachable from the default branch — a receipt anchored to the branch that adds it vanishes on merge and then fails this required job on `main` permanently. It then WARNs when commits after that sha touched a first-party file the Status paragraph names, which is the only mechanism here that catches *document-vs-code* drift (ADR-049's status claimed its evaluator was unwired for five merged PRs after it wasn't). **A file is watched only when the Status names it by full repo-relative path** (any `FIRST_PARTY_PY_ROOTS` tree, not just `abicheck/`); a bare `x.py` is accepted only when it resolves to `abicheck/x.py`, and family shorthand (`_resolver.py`) is deliberately not guessed at — see `adr/index.md` for why. Lives in `scripts/adr_status_sync.py`, a sibling leaf module, since `check_ai_readiness.py` is already past the 2000-line hard cap |
| `banned-imports` | ERROR | No `print(...)` outside CLI/reporter modules; no `subprocess(..., shell=True)` |
| `license-header` | WARN | Every `abicheck/**/*.py` carries the Apache-2.0 header / SPDX identifier |
| `test-assertion-density` | WARN | Every `test_*` function asserts something (directly or via a same-file helper) — flags zero-assertion smoke tests so coverage isn't "filled" without verification |

Run locally: `python scripts/check_ai_readiness.py`. Errors fail; warnings print and pass.

## Test-quality gates (beyond line coverage)

Line coverage measures *reach*, not whether a test actually checks the result.
Several mechanisms guard test quality so coverage can't be "filled" without verifying behaviour:

- **FP-rate gate** — `scripts/check_fp_rate.py` (mirrored in `tests/test_fp_rate_gate.py`).
  A labelled corpus of `(old, new)` snapshot pairs run under public-surface scoping:
  internal-noise cases must stay non-breaking (no false positives), real-break cases
  must stay breaking (no false negatives). Both baselines are 0; grow the corpus only
  with cases the correct implementation already passes. Cases carry a scoping *axis*
  tag (`CASE_CATEGORY`); `--markdown`/`--json` emit a per-axis FP/FN breakdown for trend
  tracking.
- **Per-tier accuracy gate** — `scripts/check_tier_accuracy.py` (mirrored in
  `tests/test_tier_accuracy_gate.py`). Complements the FP-rate gate by measuring *what
  each evidence level buys*: one labelled change per case is projected down to what each
  tier observes (L0 symbols → L1 debug → L2 headers → L3 build) and run through `compare`;
  verdicts collapse to a 3-band ordinal (non-breaking/risk/breaking). It records, per
  tier, over-calls (false positives) vs under-calls (false negatives) — encoding the
  principle that **adding a layer reduces both** (L1 sees layout but over-calls internal
  churn; L2 scoping removes it; L0/L1 under-call breaks only headers/build see). Gates on
  top-tier correctness + under-call monotonicity (more evidence never hides a break an
  earlier tier caught — authority rule). CI posts the matrix to the step summary. User
  docs: `docs/learn/evidence-and-detectability.md` § "What each layer buys".
- **Mutation testing** — `scripts/mutation_results.py` (parser/attribution) +
  `scripts/check_mutation_score.py` (gate) + `.github/workflows/mutation.yml`.
  `mutmut` mutates the detector core; `[tool.mutmut].only_mutate` is the list, and it
  now covers identity, suppression and serialization alongside `diff_*`/`checker_policy`.
  A *surviving* mutant is a covered-but-unverified line. **Three lanes, because one
  cannot serve both purposes:**
  - **PR** — auto-runs on a diff touching a mutated module *or* that module's own tests
    (path-filtered; the `mutation` label still forces a run the filter misses), gating
    `--diff-scoped`: any survivor in a function this branch changed fails. Absolute —
    there is no baseline to be under. Lines the branch *removed* are resolved against
    the merge base, since that is the only revision they exist in.
  - **Weekly** — per-module drift against the committed `mutation-baseline.json`, so one
    module's regression cannot be paid for by an unrelated module's improvement.
  - **Dispatch** (`write_baseline: true`) — records that file. Deliberately manual:
    accepting the current survivor set is a review decision, not something a cron does
    silently.

  `SURVIVOR_BASELINE` (one global total) remains as a fallback and is the weaker gate —
  it cannot express the per-module invariant. `--require-baseline` is what stops a run
  that gated nothing from exiting 0, and an unresolved run (timeout/suspicious/no-tests)
  is a *failed measurement*, not zero survivors. **`mutation-baseline.json` is not
  recorded yet**; until a dispatch run writes it, the drift lanes fail closed rather
  than passing vacuously. Per-lane trigger detail lives in `.github/AGENTS.md`'s
  workflow table rather than being copied here.
- **Metamorphic property tests** — `tests/test_detector_properties.py` (`slow`).
  Hypothesis-generated snapshot pairs checked against invariants that hold for *any*
  input (idempotence, determinism, direction-symmetry of touched symbols, emitted-kind
  partition, additive monotonicity) — generalization guards, not example-shaped tests.
- **Primitive-level property tests** — a narrower sibling of the metamorphic suite
  above, for a *reusable, general-purpose helper* rather than a whole detector.
  `test_diff_namespaces.py::TestPairedStableIndicesProperties` tests
  `_paired_stable_indices` (the evidence-gated connected-components merge behind
  `EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT`'s versioned-inline-namespace alias
  handling) directly, not only through its highest-level caller. It exists because
  fixing that one double-report bug took six independent review rounds against the
  same ~150-line function, and five of the six findings were bugs in the *generic
  merge primitive itself* (order-dependence, side-membership asymmetry, an empty
  string silently accepted as identity, parameter-signature text leaking into the
  grouping key, a merged key's string representation coincidentally colliding with
  an unrelated singleton's own key) — none of which any hand-written example test
  caught, because every one of those tests was written to confirm the fix just made,
  which by construction only encodes the bug the fix's author already thought of. A
  hand-written test only forecloses the *specific* input it names; only property
  tests stating the primitive's actual contract — "no merge without shared identity
  evidence," "the result never depends on input order," "a real alias merges
  regardless of which side holds which spelling" — search the input space the way an
  adversarial reviewer does. When adding a new reusable merge/dedupe/grouping
  primitive anywhere in this codebase, give it this same treatment: a small,
  standalone property-test class stating its contract as invariants, decoupled from
  any one caller's domain logic, before or alongside the domain-level example tests.
  Two of the two-round-falsified *identity sources* the same incident produced
  (constants' value-equality, types' structural-fingerprint-then-`source_location`)
  are the companion lesson: once a proposed identity heuristic has been individually
  falsified by a concrete counterexample twice, the correct response is to stop
  proposing a third and accept the double-report as a documented limitation (see
  `_type_index_items`'s and `_diff_constants`'s docstrings) — the same
  "attempted twice, reverted twice" discipline the linkage-blind-removal and
  `type_base_changed` entries above already establish, not a heuristic that keeps
  finding one more counterexample.
- **Silent-skip guard** — `tests/conftest.py`. A marker lane can export
  `ABICHECK_MIN_EXECUTED=<n>`; the session fails unless at least `<n>` tests actually ran,
  so a missing external tool can't turn a lane green with zero work done. Wired into the
  `abicc`, `libabigail`, and `integration` CI lanes.
- **Third-party-boundary tests must exercise the real public API at realistic scale, not
  just internal arithmetic.** Lesson from a real incident (ADR-059 §12: `snapshot_io.py`'s
  zstd `max_window_size` was silently computed in the wrong unit for months): one test
  asserted a value's own formula was self-consistent (a tautology against the bug's own
  wrong formula), and a second used a toy-shaped fixture (small, highly-compressible input
  at a large nominal parameter) whose *actual* required behavior collapsed to something
  trivial — both passed identically before and after the regression. When a module's job is
  "honor an external library's/format's contract," **every** supported algorithm needs at
  least one test that goes through the module's *actual public entry point*, at a *content
  scale realistic enough to trigger the condition being defended against* where one is known
  — never only a hand-constructed shortcut into the dependency's lower-level API. This
  applies per algorithm even when only one of them has a known incident to defend against: a
  principle that silently excludes the algorithm nobody has broken yet isn't a principle, and
  a review round caught exactly that gap here (gzip had none). See
  `tests/test_snapshot_compression.py`'s `test_zstd_round_trip_at_production_scale_and_level`
  (real `AbiSnapshot` → real `write_snapshot_bytes`/`read_snapshot_bytes` chokepoints → scaled
  past the threshold where the KiB/bytes regression actually reproduces) and its gzip sibling
  `test_gzip_round_trip_at_production_scale` (same chokepoints/scale, no known incident to
  reproduce, added purely to keep this bullet true for every supported algorithm) for the
  pattern to follow for the next storage/serialization boundary.

## Line-coverage floor

The `pr` profile's `unit-pr` step (`scripts/verify.py`) enforces a **95%**
line+branch coverage floor (`--cov-fail-under=95`) — the `fast` profile does
not, since it's the everyday inner loop and deliberately skips coverage
instrumentation. This floor applies **only on the canonical Linux/Python-3.13
unit-test lane** in `.github/workflows/ci.yml` — that's where the full unit
suite runs under coverage.
The other Linux Pythons (3.12/3.14) run the same suite *without* coverage (they would
only re-check the identical floor, and coverage instrumentation adds ~60% wall time).
macOS/Windows skip the Linux-only ELF/DWARF parsing tests, which structurally lowers
their coverage (~93% on macOS), so those lanes run the same tests without the
fail-under gate (macOS still emits a coverage report). Coverage uses the
`sys.monitoring` backend (`COVERAGE_CORE=sysmon`, Python 3.12+) to keep the
instrumentation cheap. If the macOS lane ever fails on coverage, the fix is to keep the
gate Linux-scoped — **do not lower the global 95% floor** to make another platform pass.

## Files that are large — edit carefully

**Don't trust hard-coded line counts — they drift.** The AI-readiness gate is the
source of truth: it WARNs on any file >1500 lines and ERRORs >2000 (hard cap, no
allowlist). To see today's large files, run:

```bash
python scripts/check_ai_readiness.py 2>&1 | grep "exceeds soft limit"
```

That sentence is load-bearing: this paragraph previously named the WARN set as
"`cli.py`, `dumper.py`, and `buildsource/crosscheck.py`" long after it had
stopped being true (`cli.py` is now a 131-line registration facade), which is
exactly the drift the "don't trust hard-coded line counts" warning above is
about. As a shape rather than a list: the WARN set is **large — roughly 100
files, about a third of them under `abicheck/`** — and a meaningful number sit
within a few lines of the 2000-line hard cap, so a routine addition to one can
turn an ERROR on. Run the command; don't reason from any count written here.

`architecture/debt.yaml` is the sharper gate for these files and the one you
will actually trip: every one of them carries a `no_growth` baseline
(`python scripts/check_architecture.py`), so growing one is a reviewed
debt-baseline change, not an ordinary edit. ADR-061's definition of done wants
that file empty or holding only accepted exceptions — **the way to shrink an
entry is to move responsibility out to a properly-owned module, never to
trim the file to fit** (`report/render_html.py` is a worked example: it took
~200 lines of formatting out of `html_report.py` by giving them an owner).

When editing any large file, read the specific section you need rather than the
whole file. Several big commands have already been split into sibling
`cli_<name>.py` / `diff_*` modules (see the module map above); prefer extending a
split-out module over growing the parent toward the cap.

### Adding a new top-level command

**First, ask whether it should be a *root* command at all (ADR-043/ADR-054).**
The public root surface is exactly `dump`, `compare`, `scan`, `deps`, `compat`,
`aggregate`, `project` — and `tests/test_cli_root_surface.py` pins that set as
an executable contract, so a new root registration fails CI until the test is
updated too. Before adding one, a new root command must clear **every** one of
these (ADR-054's admission bar — the same review that consolidated four
G30/ADR-047 root groups, one added per artifact, back into the single
`project` group below):

1. It answers a stable, user-facing question — not "here is JSON artifact X,
   let me expose the function that reads/writes it."
2. Its operand is a domain object a user already thinks in terms of (a
   binary, a set of reports, a project config) — not an internal pipeline
   transport format (a manifest, a projection of another artifact).
3. It is useful outside one specific CI Action's wire format. A command whose
   whole job is "shape this JSON exactly how `actions/foo`'s one input
   expects it" is a library function that Action/workflow calls directly
   (`python3 -c "from abicheck.x import y; ..."`), not CLI surface — see
   `abicheck/buildsource/baseline_publish.py`'s `derive_baseline_libraries()`
   for the pattern (used by `publish-baseline.yml`/`update-main-baseline.yml`,
   never a CLI command).
4. It doesn't already fit naturally as an option or subcommand of an existing
   durable operation (`--dry-run`, a new `<verb> <noun>` subcommand under an
   existing group) — a second, parallel "preflight" vocabulary next to an
   already-established one (`dump --dry-run`) is exactly the drift ADR-050's
   `plan --dump-manifest` command caused before ADR-054 folded it back into
   `dump --dump-manifest --dry-run`.
5. It has a real, validated usage scenario beyond the PR that introduced it —
   not just "this pipeline stage produces an artifact, so it should be
   inspectable."
6. Landing it means updating `tests/test_cli_root_surface.py`, this file,
   `README.md`, and `docs/reference/cli-reference.md`
   (`python scripts/gen_cli_reference.py`) in the *same* PR — a root surface
   change with only the test updated (or only the code) is how ADR-043's
   "nothing else is registered" invariant drifted from the actual command set
   before (the CLI carried ten root commands while `README.md` still said
   six).

**If it's advanced multi-target/CI-integration surface that fails the "user
already thinks in terms of this operand" bar (#2) on its own** — validating a
project's `.abicheck.yml`, a `build-output.json`, or generating a run-plan —
it almost certainly belongs as a new subcommand of the existing `project`
group (`abicheck/cli_project.py`), not a new root command. `project` exists
precisely to hold this class of operation: `project validate`,
`project validate-build`, `project plan` are all "read one project-integration
artifact, report on it" operations that share one advanced/opt-in namespace
instead of each claiming root. Add `@project_group.command("your-verb")`
there and extend `tests/test_cli_root_surface.py`'s existing assertions
(which don't need to change, since `project`'s subcommand set isn't pinned
the way the root set is) plus a `TestProjectYourVerbCli`-shaped test class.

Once a root command genuinely clears the bar above, pick the right home:

- **Small command (one function, no significant helpers)** — add to `cli.py` directly with `@main.command(...)`.
- **Larger command or command group** — add as a sibling `abicheck/cli_<name>.py` module:
  1. Top of module: `from .cli import main` (and any shared `_helpers`).
  2. Decorate with `@main.command("foo")` or `@main.group("foo")` as usual.
  3. At the bottom of `cli.py`, add `cli_<name>` to the side-effect `from . import (...)` block — that runs after `main` and helpers are defined, registering the new command.
  4. If the new module uses `@click` decorators, add `abicheck.cli_<name>` to the `disallow_untyped_decorators = false` override in `pyproject.toml` (alongside the existing entries).
  5. If `scripts/check_ai_readiness.py` flags a cycle, this is `IMPORT_CYCLE_ALLOWLIST`'s known CLI-registration cluster — see "What NOT to do" below before extending it.
  6. **Shared utility flags go through a decorator, not an inline copy.** `-v/--verbose` is `@verbose_option`, `--format`/`-o/--output` are `output_options(...)`, language is `lang_option(...)` (all in `cli_options.py`). Every visible option must carry `help=` text and a shared concept must use one canonical primary spelling — both are enforced by `tests/test_cli_contract.py` (`test_no_option_has_empty_help`, `test_shared_concept_canonical_spelling`).
  7. **Moving helpers out of a module that re-exports them?** If you relocate a helper that an existing module re-exports "for API stability / tests" (e.g. the `cli_buildsource` block), preserve the old import path with a lazy module-level `__getattr__` shim that resolves via `importlib.import_module` — a static `from .new_module import …` re-export would re-introduce the import cycle the split was meant to avoid (see the shim at the tail of `cli_buildsource.py`).

## Exit codes

- `compare` command (legacy, with no severity setting in effect): 0 = compatible, 2 = source break, 4 = ABI break
- `compare` command (severity-aware, with `--severity-preset` or a config `severity:` block): 0 = no error-level findings, 1 = error in addition/quality only, 2 = error in potential_breaking, 4 = error in abi_breaking
- `scan --against`: 0 = compatible, 2 = API break, 4 = ABI break, 5 = budget overflow, 6 = NOT_COMPARABLE (legacy scheme). Like `compare`, it also accepts `--severity-preset`/`--exit-code-scheme` (and `.abicheck.yml`'s `severity:`/`exit_code_scheme`); under the resolved `severity` scheme the 0/2/4 portion is computed by `severity.compute_exit_code` instead of the raw verdict, same as `compare`'s severity-aware row above. `--pack` gate-severity folding now reaches `scan` too (CLI cleanup phase two, "PR B" slice 3) — a `kind: gate` pack's assignments apply the same way an explicit `--severity-preset`/`--exit-code-scheme` does, and cannot override one that was actually given (CLI or `.abicheck.yml`).
- **Orthogonal contract-coverage axis (ADR-049 Phase 7), on `compare` and
  `scan --against` alike:** under `--contract`, the selected
  domain whose required evidence is incomplete contributes
  **1**, folded with `max` (`contract_coverage_exit.py`). It raises a clean
  `0` to `1` and never lowers a `2`/`4`, and it never rewrites a finding's
  compatibility decision or gate contribution. Without
  `--contract` the contribution is always `0`, so every
  pre-existing invocation is unchanged. Every consumer that publishes an
  exit status folds it and explains it — the two CLIs and the composite
  Action (`verdict: COVERAGE_INCOMPLETE`). A directory/package
  `compare` (the per-library release fan-out) applies the same flag to
  each library and `max`s every library's own contribution into the
  release's exit code, stated in the release JSON summary under the same
  `contract_coverage_exit_contribution` field. `--fail-on-removed-library`'s
  exit `8` is checked ahead of this coverage-only fallback when both could
  apply, so a removed library's own signal is never masked by an unrelated
  coverage gap
- `compat` command: 0 = compatible, 1 = BREAKING, 2 = API_BREAK (source-level), 3-11 = errors (see `compat/cli.py:_classify_compat_error_exit_code`)
- `64` = usage error (bad flags/inputs; `cli._EXIT_USAGE_ERROR`) — applies across commands
- Full per-command matrix: `docs/reference/exit-codes.md`

## Known gaps — acknowledged remaining work

See [`docs/contribute/known-gaps.md`](docs/contribute/known-gaps.md) for the
full, detailed history of every investigated-but-unfixed gap, reverted fix
attempt, and the reasoning behind each — this is genuinely load-bearing
institutional memory (not a changelog), read it before re-attempting a fix
in an area it already covers. It was moved out of this file (2026-08-28,
verbatim, nothing trimmed) purely to keep this file's own per-session
context cost down; `docs/contribute/known-gaps.md` itself always points
back here as the primary contract.

## Don't

- Don't hand-edit `CHANGELOG.md`'s `## [Unreleased]` section directly — add a `changelog.d/` fragment instead (see Conventions above); CI enforces this
- Don't modify `examples/` test cases without understanding the ground truth they encode
- Don't add dependencies without strong justification (this is a lightweight tool)
- Don't skip test markers — if a test needs `castxml`, mark it `@pytest.mark.integration`
- Don't "fix" the mypy errors listed above by adding `# type: ignore` broadly
- Don't modify binary test fixtures without regenerating expected outputs
- Don't change public API signatures without checking for breaking changes
- Don't add platform-specific code without considering cross-platform compatibility
- Don't extend `IMPORT_CYCLE_ALLOWLIST` in `scripts/check_ai_readiness.py` to make a new cycle pass, and never as a routine step to unblock CI. The existing large CLI/service entry documents an accepted, by-design registration pattern (Click sibling commands registering back on `cli.main`) — a *new* member outside that documented pattern is very likely a real dependency-direction problem, not another instance of it. Prefer a function-local import or moving the shared logic to a leaf module both sides can depend on. If the coupling really is intentional, extending the allowlist needs an ADR (or explicit maintainer sign-off) recorded in the PR, the same bar as any other architectural exception — not a comment justifying it inline and moving on.
- Don't hand-duplicate a command, invariant, or count from this file into an adapter (`CLAUDE.md`, `.github/copilot-instructions.md`) — point the adapter back here instead (see the table at the top of this file).
- Don't let time/effort estimates ("this would take too long", "quicker to just patch") drive a technical implementation decision — see "Decision-making principles" above.
- Don't ship a narrow, site-specific patch for a bug without first tracing it to its root cause and considering a generalized fix and generalized test — see "Decision-making principles" above.
