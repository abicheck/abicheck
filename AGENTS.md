# AGENTS.md — Canonical instructions for coding agents working on abicheck

This is the **canonical, vendor-neutral** repository contract (CLAUDE.md
"M1-1"). Every tool-specific instruction surface is a thin adapter that
points back here instead of maintaining its own copy:

| File | Role |
|------|------|
| `AGENTS.md` (this file) | Canonical instructions — the source of truth |
| `CLAUDE.md` | Claude Code bootstrap — imports this file via `@AGENTS.md` |
| `.github/copilot-instructions.md` | GitHub Copilot adapter — points here |
| `.cursor/rules/abicheck.mdc` | Cursor adapter — points here |

If you're editing repository-wide instructions, edit **this file**. Don't
hand-duplicate a command or invariant into an adapter — adapters exist so
each tool's convention is satisfied without a second copy to drift.
Sub-directory `CLAUDE.md` files (`abicheck/CLAUDE.md`, `tests/CLAUDE.md`,
etc.) are scoped, per-area context, not adapters to this file — they stay as
they are.

## What is abicheck?

ABI compatibility checker for C/C++ shared libraries. Pure Python (3.10+).
Detects 395 ABI/API change types across ELF, PE/COFF, and Mach-O binaries,
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
1. **Parsing** — extract metadata from binaries
   - `elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py` — platform-specific
   - `dwarf_metadata.py`, `dwarf_advanced.py`, `dwarf_unified.py` — DWARF debug info
   - `pdb_parser.py`, `pdb_metadata.py`, `pdb_utils.py` — Windows PDB
   - `btf_metadata.py`, `ctf_metadata.py` — Linux kernel debug formats
   - `sycl_metadata.py` — SYCL plugin interface
2. **Snapshot** — `dumper.py` creates `AbiSnapshot` (model in `model.py`)
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
     takes all three kinds; `scan --against` takes policy/contract and
     rejects a gate pack, having no gate of its own. Two review findings
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
     presentation layer (`--dry-run` rendering, git/build-id stamping,
     `fold_dump_provenance_into_json`); the native `dump` CLI does not build a
     `DumpRequest` yet — see G33's Phase 5 note for what that migration needs
     first
   - `stack_checker.py`, `stack_report.py`, `stack_html.py` — stack analysis
9. **Build-source evidence (optional L3–L5 layers)** — `buildsource/` package
   (collect/merge/source-ABI replay/source graph; ADR-028…033). See
   `abicheck/buildsource/CLAUDE.md` for its module map.

10. **Published Agent Skills (ADR-058)** — `skills-src/` is the one
   hand-authored source (four `SKILL.md` files in Layer A, one `shared/`
   tree of Layer-B domain fragments); `scripts/gen_agent_skills.py`
   publishes it into three committed, self-contained trees
   (`.agents/skills/`, `.claude/skills/`, `.gemini/skills/`). Never
   hand-edit the generated trees. See `skills-src/CLAUDE.md`.

Beyond the core package: `.github/AGENTS.md` (CI/workflow architecture),
`action/AGENTS.md` (the composite GitHub Action's shell-script layer), and
`contrib/abicheck-clang-plugin/AGENTS.md` (the optional Clang facts plugin)
cover the surrounding first-party trees this file doesn't detail.

## Key types

- `AbiSnapshot` (`model.py`) — serializable snapshot of a library's ABI surface
- `DiffResult` (`checker_types.py`) — single detected change with kind, severity, details
- `ChangeKind` (`checker_policy.py`) — enum of 395 change types; categorized into `BREAKING_KINDS`, `API_BREAK_KINDS`, `RISK_KINDS`, and `COMPATIBLE_KINDS` (further split into `ADDITION_KINDS` and `QUALITY_KINDS`)
- `Verdict` (`checker.py`) — overall comparison result (compatible/source_break/breaking)
- `LibraryMetadata` (`checker.py`) — parsed library info

## Adding a new ChangeKind

1. Add to `ChangeKind` enum in `checker_policy.py`.
2. Add ONE `ChangeKindMeta` entry (kind string, `default_verdict`, optional
   `impact`/`description_template`) to `abicheck/change_registry.py` or one
   of its sibling `change_registry_<topic>.py` files (`_castxml`,
   `_buildsource`, `_composition`, `_coverage`, `_numpy`, `_suppression` —
   split out only to stay under the file-size cap; declaring an entry in any
   of them is equivalent). **Do NOT hand-edit `BREAKING_KINDS`/
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
| `cli-contract` | ERROR | No front-end `cli*.py` module calls Tier-1 `checker.compare` directly — it must route through the Tier-2 service (`service.run_compare`/`compare_snapshots`); ADR-037 D10.1 |
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

As of this writing the WARN set (>1500 lines) is `cli.py`, `dumper.py`, and
`buildsource/crosscheck.py` — the main CLI, binary-metadata extraction, and the
cross-check engine. Treat that command output (not this sentence) as current.

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
- `scan --against`: 0 = compatible, 2 = API break, 4 = ABI break, 5 = budget overflow, 6 = NOT_COMPARABLE (legacy scheme). Like `compare`, it also accepts `--severity-preset`/`--exit-code-scheme` (and `.abicheck.yml`'s `severity:`/`exit_code_scheme`); under the resolved `severity` scheme the 0/2/4 portion is computed by `severity.compute_exit_code` instead of the raw verdict, same as `compare`'s severity-aware row above. `--pack` gate-severity folding is not yet extended to `scan` — pass severity settings directly.
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

- **`--build-target` silently does nothing when combined with a pre-captured
  Bazel `--build-info` (an `aquery`/`cquery` jsonproto), on both `dump` and
  `scan` — investigated, not fixed (Codex review, fresh evidence, P0.2
  follow-up).** `BazelAdapter.collect()`'s `self.targets` scoping is applied
  in exactly two places: gating whether a *live* `bazel query` subprocess
  runs at all (`_resolve`/`_run_bazel`, only reachable when `workspace` is
  given and no pre-captured `aquery=`/`cquery=` path was supplied), and
  populating the `TargetScope` report (`requested`/`resolved`/
  `transitive_count`) on the returned `BuildEvidence`. Neither path filters
  `ev.compile_units`/`ev.targets` themselves once a pre-captured file is
  parsed — `_collect_aquery`/`_collect_cquery` walk the *entire* captured
  action/target graph unconditionally. `buildsource/inline.py`'s
  `_maybe_collect_bazel_build_info` (the function `collect_inline_pack`
  routes a `--build-info` recognized as `bazel_aquery`/`bazel_cquery` through)
  doesn't even accept a `targets` parameter, so `--build-target` isn't merely
  unenforced here — it's never threaded to this call site at all, and no
  diagnostic or `TargetScope` records that a scope was requested. Confirmed
  by reading the code (no live Bazel repro run in this pass): `scan
  --build-info saved-aquery.json --build-target //:lib` (or the identical
  `dump` invocation) collects every TU in the captured workspace, with no
  error, warning, or `TargetScope` entry showing the mismatch between what
  was requested and what was actually scoped — unrelated targets can pollute
  L3 evidence and any detector built on it. **Not fixed here, and the two
  candidate fixes are not equally easy:** (1) *Actually filter* the captured
  graph to the transitive closure of the requested roots. Feasible in
  principle for `cquery` data, whose `Target.dependencies` already carries a
  real label-to-label dependency edge list (`attrs.get("deps", ...)` in
  `_collect_cquery`) a BFS could walk — but a `cquery`-only capture produces
  no `compile_units` at all (only `_collect_aquery` does), so this alone
  doesn't scope the TUs that actually matter. `aquery` data has no equivalent
  target-level `deps` list — only a flat action list, each tagged with its own
  `targetId` (`CompileUnit.target_id`) — so a correct closure would have to be
  reconstructed from the action graph's own input-artifact edges
  (`_AqueryGraph`'s depset walk), a materially different and more involved
  algorithm than the `cquery` case, not a shared implementation. (2) *Reject*
  the combination with a clear usage error instead — architecturally cleaner,
  but `collect_inline_pack`/`_maybe_collect_bazel_build_info` sit in a shared
  Tier-2 module used by both CLI and typed-API callers (`embed_build_source`,
  in turn called from `cli_dump_helpers.py`, `scan_engine.py`, and
  `service_input_resolution.py`), and raising there can only be a plain
  `ValueError`/`AbicheckError` (per this codebase's Tier-1/Tier-2 separation —
  Click-specific exceptions belong to the CLI layer only). Neither `dump_cmd`
  nor `scan_cmd` currently wraps its `embed_build_source`/`run_scan_core` call
  in a catch for that error class (confirmed by reading both), so today such
  an error would propagate as an unhandled Python exception with a raw
  traceback instead of a clean `click.UsageError`/exit 64 — fixing that
  requires adding (and testing) a catch-and-reraise at every one of those call
  sites, not a single choke point. Either option is a real, multi-call-site
  feature needing its own dedicated design and test coverage, not a
  same-session reactive patch under continued review pressure — per this
  file's own "known gaps over risky reactive patches" convention. Until then,
  the safe workaround is to only combine `--build-target` with a *live* query
  (`--sources <workspace>` and no `--build-info`, letting `BazelAdapter` run
  `bazel query deps(...)` itself, which does scope correctly) or to
  pre-capture the `aquery`/`cquery` jsonproto already scoped to the desired
  targets before passing it as `--build-info`.

- **The native ELF `abicheck dump` path never applies L3 build context to its own
  L2 header parse, and this is now confirmed by an external end-to-end
  reproduction, not only by the pre-existing module-map note about the
  `DumpRequest` migration (2026-08-15, `napetrov/abicheck-bazel-lab` audit
  against `5b52989`).** The "Module map"'s `service_dump_pipeline.py` entry
  above already named the cause — "the native `dump` CLI does not build a
  `DumpRequest` yet" — but that note described a *migration gap*, not a
  concrete, reproduced *symptom*. Traced here to the exact call graph: P0.3's
  L3→L2 fold (`service_input_resolution._seeded_compile_context`, wired via
  `buildsource/l2_seed.derive_l2_compile_context` and
  `buildsource/header_compile_context.resolve_header_compile_context`) is
  real and already correct — `resolve_side_snapshot()`, the function that
  calls it, is shared by *both* `service_compare_pipeline.
  resolve_compare_request` (the path `compare`'s own implicit-dump operand
  takes) *and* `service_dump_pipeline.run_dump_request`. But
  `cli.py`'s `dump_cmd` (the ELF path) never calls `run_dump_request` at
  all — it calls `cli_dump_helpers.perform_elf_dump()` directly, a separate,
  older code path whose `CompileContext` is built only from explicit
  `--ast-frontend`/`--compiler*`/`--sysroot`/`--nostdinc` flags (confirmed by
  grep: `cli_dump_helpers.py` contains no reference to
  `resolve_side_snapshot`, `_seeded_compile_context`, or
  `derive_l2_compile_context` anywhere). The external repro: a fresh
  `abicheck dump lib.so --sources . --build-info compile_commands.json
  --depth source` snapshot's own `parsed_with_build_context` reads `false`
  and `language_standard` reads `""` even though real L3 evidence was
  supplied and is embedded in the snapshot — the evidence is collected and
  stored, but never routed to the L2 header-AST invocation, because that
  invocation runs through a path P0.3's fold was never wired into.
  Consequence: a `dump`-produced baseline and a `scan --against`
  live-binary comparison of the *same* project, given the *same* L3
  evidence, resolve to genuinely different `CompileContext`s
  (`profile_fingerprint` mismatch on `include_sequence`/
  `language_standard`), so `scan` correctly (per ADR-050 D2) refuses the
  comparison as `NOT_COMPARABLE` — not because the evidence was
  insufficient, but because the two commands extracted under
  non-comparable recipes for reasons neither command's own diagnostics
  name. **Not fixed here**: migrating `dump_cmd`/`perform_elf_dump`
  (`cli_dump_helpers.py` is already at 1914 of its 2000-line hard cap —
  real headroom for an inline fix is tight) to route through
  `run_dump_request` the way `compare`'s implicit-dump path already does
  is a genuine, cross-cutting architecture change — the "what that
  migration needs first" the module-map note already flags — not a
  same-pass reactive patch. The PE/Mach-O `dump` paths (`service.py`'s
  `_dump_pe`/`_dump_macho` mirrors) were not independently checked for the
  identical gap in this pass.

  **Closed, additively, without the `run_dump_request` migration this entry
  originally called for.** Full migration would have meant rewriting
  `perform_elf_dump`'s already-large, carefully-ordered pipeline (header
  parse, ADR-039 build-context harvest, G14/G23/G26 Python/NumPy attach,
  the header-graph and clang-layout-tool second passes) around a
  fundamentally different call shape — real, but out of proportion to the
  actual gap, which is narrowly "the L3→L2 fold never runs on this path,"
  not "this path's whole architecture is wrong." The fix is additive
  instead: `buildsource/l2_seed.fold_l3_compile_context()` — a new, shared
  wrapper around the already-correct `derive_l2_compile_context()` +
  `_merge_l3_compile_context()` pair (the latter *moved* into `l2_seed.py`
  from `service_input_resolution.py`; see that function's own docstring for
  why — leaving it in place would have closed an
  `l2_seed -> service_input_resolution -> cli_dump_helpers -> l2_seed`
  import cycle the AI-readiness gate correctly rejects once
  `cli_dump_helpers` needed it directly) — called from both
  `perform_elf_dump` (ELF) and `handle_non_elf_dump` (PE/Mach-O, which
  shared the identical gap: confirmed by reading `service.run_dump`'s own
  `compile` parameter never receiving anything beyond the CLI/config-
  resolved context either). Both call sites now fold the real L3
  `CompileUnit`-derived context (`-std=`, ABI-relevant `-D`/`-U`, target,
  sysroot) into the *same* explicit context CLI flags/`.abicheck.yml`
  already built, exactly mirroring what `_seeded_compile_context` already
  does for `compare`'s implicit-dump path via `resolve_side_snapshot` — so
  `dump` and `compare` now fold under one shared primitive, even though
  `dump`'s own CLI pipeline still doesn't route through `run_dump_request`.
  `perform_elf_dump`'s two independent second-pass clang re-parses (the
  header-graph attach and the clang-layout-tool attach) also now receive
  this same fully-merged context via `effective_compile_context =
  l3_effective_ctx`, closing a narrower, previously-undocumented sibling
  gap where those passes' own `gcc_options`-string-only re-derivation never
  looked at `gcc_option_tokens`/`sysroot`/`nostdinc`/deferred include roots
  at all — so a real disagreement on any of those between the primary
  parse and the second passes could previously have gone unnoticed even
  without any L3 evidence in play. `AbiSnapshot.parsed_with_build_context`
  is now stamped from either this fold or the older `-p`/`--compile-db`
  mechanism (OR'd, matching `resolve_side_snapshot`'s own rule), on both
  the ELF and PE/Mach-O paths.

  **A third, independent instance of the same gap, found only by testing
  the actual end-to-end repro rather than trusting this entry's original
  framing: `scan`'s own candidate resolution never applied the fold
  either.** This entry's first draft claimed `_seeded_compile_context`
  "already does" this for `scan` — false. `scan_engine._build_new_snapshot`
  calls `service.resolve_input` directly, not `resolve_side_snapshot`, so
  `compare`'s/`dump`'s fold never ran on `scan`'s candidate side. A
  same-project `scan --against` a freshly-fixed `dump` baseline still
  returned `NOT_COMPARABLE` on `language_standard` even after the two
  `dump`-path fixes above — confirmed by actually running the repro end to
  end (a real `g++`-compiled library + `compile_commands.json`), not by
  re-reading the code. Fixed the identical way: `_build_new_snapshot` now
  calls `fold_l3_compile_context` immediately before `resolve_input`, and
  stamps `parsed_with_build_context` the same way. The same real repro now
  produces `NO_CHANGE` end to end (`dump` baseline → `scan --against`),
  which is the actual acceptance criterion this whole entry exists for.

  **A fourth finding, from a Codex review of this same change, on the
  *shared* `_merge_l3_compile_context` primitive itself — pre-existing in
  the `compare`/`scan`-implicit-dump fold this PR reused, not introduced by
  it, but real and now fixed alongside it.** "Derived leads, explicit
  wins" (last-flag-wins) is the right rule for a macro/std/sysroot switch,
  but `header_compile_context._context_flags` also renders a matched
  `CompileUnit`'s own `include_paths`/`system_include_paths` as `-I`/
  `-isystem` tokens, and an include search path is *first*-match-wins —
  so putting a derived `-I` ahead of an explicit one (the pre-existing
  order) silently let the build's own header win over a caller's explicit
  override for a colliding basename. Fixed with a new
  `_split_include_tokens()` helper that carves derived's own include-search
  entries (`-I`/`-isystem`/`-iquote`/`-idirafter`/MSVC `/I`/`/imsvc`/
  `/external:I`, spaced-pair-aware) out of the leading last-flag-wins group
  and appends them *after* explicit instead — every other derived token
  keeps its original leading, overridable position.

  Verified end-to-end against a real `compile_commands.json` fixture
  through the actual `perform_elf_dump`/`handle_non_elf_dump`/
  `_build_new_snapshot` entry points (not a hand-built `CompileContext`) —
  see `tests/test_cli_dump_helpers_coverage.py::
  test_perform_elf_dump_folds_l3_compile_context_into_header_parse`,
  `tests/test_non_elf_dump_l2_seed.py::
  test_non_elf_dump_folds_l3_compile_context_into_header_parse`,
  `tests/test_scan_l2_cleanup_ordering.py::
  test_scan_candidate_folds_l3_compile_context_into_header_parse`, and
  `tests/test_header_compile_context.py`'s
  `test_merge_l3_compile_context_explicit_include_search_wins_first_match`/
  `test_merge_l3_compile_context_attached_include_form_stays_paired` —
  confirming the derived `-std=`/`-D` flags reach the primary header-AST
  parse, `parsed_with_build_context` is stamped, and an explicit `-I`
  outranks a derived one. Full test suite (28k+ tests) green; no
  import-cycle or file-size regression (`cli_dump_helpers.py` stayed under
  its 2000-line hard cap by moving the shared fold logic into `l2_seed.py`
  rather than duplicating it inline for both call sites).

  **Two more findings on the same change, both fixed, one left as a
  narrower residual gap.** (1) A derived `-I`/`-isystem` reaches the header
  parse only as an opaque `gcc_option_tokens` string, which the AST cache
  key's own directory-mtime hashing (`extra_includes`/`extra_hash_dirs`,
  `dumper_ast_config.py`) never inspected — editing a header under a
  derived include dir could reuse a stale cached AST. `fold_l3_compile_
  context()` now also returns the derived include directories
  (`_include_operand_dirs()`), threaded into `perform_elf_dump`'s existing
  `extra_hash_dirs`. **Not closed for `handle_non_elf_dump` (PE/Mach-O) or
  `scan_engine._build_new_snapshot`**: neither `dump_native_binary`→
  `service.run_dump` nor `service.resolve_input` exposes a public
  `extra_hash_dirs` hook the way `perform_elf_dump`'s own direct `dump()`
  call does — and `service.py`'s own internal cache-key computation for
  those paths already has the identical, broader, pre-existing gap for
  *any* `CompileContext.gcc_option_tokens` include entry (not just an
  L3-derived one), so a scoped fix here would still leave the general case
  open. Threading a real `extra_hash_dirs` channel through `resolve_input`'s
  public signature is a genuine, separate change affecting every caller of
  that function, not a follow-up to this one. (2) `scan`'s own fold call
  hard-coded `lang_explicit=False`; since `scan --lang c` is never the
  Click default (only `"c++"` is), it is always a genuine explicit
  request, and the hard-coded `False` let a matched C++ compile unit's own
  `-std=c++20` reach a parse `scan --lang c` was explicitly forcing into C
  mode. Fixed by treating `lang == "c"` as explicit, mirroring `perform_
  elf_dump`'s identical squash-guard rule elsewhere in this same area.

  **A fifth finding, on the fold's own call shape rather than its logic: the
  include-dir seed and the L3→L2 fold each independently collected L3
  evidence, and a caller genuinely needing the inferred build query (no
  existing compile database) could self-deadlock.** All three call sites
  (`perform_elf_dump`, `handle_non_elf_dump`, `scan_engine._build_new_
  snapshot`) called `seed_l2_includes()` and then, immediately after,
  `fold_l3_compile_context()` — each independently calling
  `buildsource.inline.collect_inline_pack()`. Harmless when at most one call
  can trigger the zero-config *inferred* build-system query (cmake/make/
  bazel, gated by `allow_inferred_build_query`/`collect_mode`), but a real
  inferred query is a `flock`-protected, deterministic per-source-tree temp
  build dir (`build_query._claim_inferred_build_dir`) held until its own
  *cleanup* runs — deliberately deferred until after the header parse
  consumes the seeded dirs, i.e. long after the seed call returns. The
  second call's own inferred-query attempt then contends on the identical
  lock the first call is still holding, and — being the *same process*
  reopening the same lock file, not a different one — `flock`'s
  per-open-file-description semantics mean this is genuine self-contention,
  not a race with some other process: it blocks for up to
  `INFERRED_QUERY_TIMEOUT_S` (600s) before falling back to a throwaway
  sibling dir (Codex review). Fixed by collapsing the two into one
  collection: `buildsource.l2_seed.seed_includes_and_fold_compile_context()`
  runs `collect_inline_pack()` exactly once and derives both the include-dir
  seed (mirroring `seed_l2_includes`'s own gating and directory derivation)
  and the compile-context fold (mirroring `derive_l2_compile_context`'s
  resolution + merge) from that single `BuildEvidence`, so only one inferred
  query — if any — ever runs per call. All three call sites now call this
  one combined function instead of the two separate ones; `seed_l2_includes`
  itself is unchanged and still used independently elsewhere (well-defined
  on its own — the bug was specifically two independent *collections* of
  the same evidence in one logical operation, not either function
  individually). Verified against the same real `compile_commands.json`
  fixtures the fourth finding's own regression tests use, now exercised
  through the combined entry point. `fold_l3_compile_context` itself —
  the standalone wrapper this combined function was built to replace — is
  **not** still used independently: once all three call sites moved to
  the combined function, nothing called it anymore, so it was removed
  entirely as dead code rather than left orphaned alongside its
  replacement (found while writing this entry's own closure notes; no
  call site or test referenced it directly by the time this was checked).

  **A seventh finding, from writing direct unit tests for the combined
  function itself (not through any of the three call sites), on the
  combined function's own body — a real regression relative to both
  siblings it was assembled from.** `derive_l2_include_dirs`'s and
  `derive_l2_compile_context`'s own identical comments both state "pack
  resolution stays inside this protected section... a corrupt/unreadable
  pack must degrade best-effort, not raise" — but
  `seed_includes_and_fold_compile_context`'s own `_resolve_l2_seed_pack_args`
  call was placed *before* the `try:` block, not inside it, so a corrupt
  pack (a `manifest.json` present but unparseable, `is_pack_dir`'s own
  documented "still a pack" case) raised a bare `JSONDecodeError` straight
  out of the function instead of degrading to the pre-existing "nothing to
  apply" no-op every other caller relies on. Caught immediately by writing
  `test_seed_and_fold_corrupt_pack_degrades_to_empty` (mirroring the two
  siblings' own `test_derive_l2_*_corrupt_*_pack_degrades_to_empty`
  tests) — it failed with the raw `JSONDecodeError` before the fix, not
  the intended empty-degrade result. Fixed by moving the
  `_resolve_l2_seed_pack_args` call inside the `try:`, matching both
  siblings exactly. The four new direct tests (no-inputs no-op, no-match
  returns none, ambiguous raises and drains pending cleanups, corrupt pack
  degrades) live in `tests/test_non_elf_dump_l2_seed.py` (not the more
  natural `test_header_compile_context.py`, which is near its own
  1500-line soft/2000-line hard cap) as a
  "seed_includes_and_fold_compile_context branch coverage" section.

  **A sixth finding, on `_split_include_tokens` itself, documented as a
  residual gap rather than fixed here (Codex review).** The split
  distinguishes include-vs-non-include tokens and preserves relative order
  within each group, but not GCC/Clang's distinct include-search *buckets*
  (`-iquote` > `-I` > `-isystem` > `-idirafter`, each a separate search
  class the compiler consults in that fixed order regardless of argv
  position). An explicit `-isystem` therefore still searches ahead of a
  derived `-iquote`/`-I` after the split, even though a real compiler
  would consult the quote/regular buckets first regardless of flag order.
  A correct fix needs the merge to track bucket membership, not just
  include-vs-non-include — a real, if narrow, redesign of the function's
  output shape, not a follow-up to the explicit-vs-derived ordering fix
  (the fourth finding above) this function exists for. See the function's
  own docstring for the same note.

  **An eighth and ninth finding, both from a fresh Codex review round on the
  P1/P2-labeled commit that added the combined function above, both real
  and both fixed.** (8, P1) `scan_engine._build_new_snapshot`'s own L3->L2
  fold only updated its *local* `compile_context` variable — `run_scan_core`
  still held the caller's original, un-folded `compile_context` and
  forwarded *that* to `_run_baseline_compare`, so a `scan --against` a
  native library could fold real L3 context into the *candidate*'s header
  parse while the *baseline*'s own native-library header parse never
  received it — silently recreating the exact `NOT_COMPARABLE`/false-ABI-
  difference risk this whole P0.3 fold exists to close, just moved from the
  dump-vs-scan pairing to the candidate-vs-baseline pairing within one
  `scan --against` invocation. Fixed by widening `_build_new_snapshot`'s
  return from `(snapshot, effective_includes)` to `(snapshot,
  effective_includes, effective_compile_context)` — mirroring the existing
  `effective_includes` precedent for exactly the same reason — and having
  `run_scan_core` forward that third value to `_run_baseline_compare`
  instead of its own original. Regression test:
  `tests/test_cli_scan.py::test_baseline_compare_receives_l3_folded_compile_context`
  (mocks both `_build_new_snapshot` and `_run_baseline_compare` at the
  `cli_scan.py`/`scan_engine.py` module boundary, confirmed to fail against
  the pre-fix code with the un-folded context forwarded instead of the
  sentinel folded one). (9, P2) `perform_elf_dump`'s ADR-039 build-context
  collector (`_attach_build_context`/`_user_define_flags`) has its own
  long-standing, explicit rule — stated in both functions' docstrings —
  that the auto-derived, per-header-matched build context must never be
  unioned snapshot-wide, since doing so would mark one TU's `-D` active for
  every scanned header. The L3->L2 fold's `l3_context_applied`
  reassignment folds that same derived context into the *identically-named*
  `gcc_option_tokens` local variable used for the primary header parse,
  which silently defeated that rule once `_user_define_flags` was called
  with the (by-then-merged) `gcc_option_tokens` rather than the caller's
  original tokens. Fixed by capturing `_user_gcc_option_tokens =
  gcc_option_tokens` before the fold's reassignment and passing that
  captured value to `_user_define_flags` instead. Regression test:
  `tests/test_non_elf_dump_l2_seed.py::
  test_perform_elf_dump_keeps_l3_derived_flags_out_of_build_context_collector`
  (confirmed to fail against the pre-fix code, asserting the L3-derived
  `-DL3ONLY=1` reaches `_attach_build_context`'s `extra_flags` when it must
  not). `handle_non_elf_dump` (PE/Mach-O) was checked and does not call
  `_attach_build_context` at all — the ADR-039 collector is ELF-only — so
  finding 9 has no PE/Mach-O counterpart.

  **A tenth finding, from the same Codex review round (P1), on
  `service._attach_header_graph`'s own second, independent
  `_clang_header_dump` pass — real and fixed.** That pass has its own AST
  cache key, computed from its own `deferred_dirs`, which only covered
  `resolve_inferred_header_roots`'s own deferred roots — never any
  include-search directory riding in `compile.gcc_option_tokens` itself
  (an explicit `--gcc-options`/`--compiler-option -I`, or — since the
  P0.3 fold — a compile-DB-derived one). A directory the primary snapshot
  pass already hashes into its own cache key was therefore invisible to
  this second pass's key, so editing a header under it could silently
  reuse a stale cached graph even though the primary snapshot re-parsed
  correctly and picked up the change. Fixed by extracting
  `l2_seed._include_operand_dirs`'s logic into a new, shared
  `header_utils.include_operand_dirs()` (a leaf module both `l2_seed.py`
  and `service.py` already import from) and folding its result into
  `_attach_header_graph`'s own `deferred_dirs` — closing the gap
  generically for *any* include-search token the merged context carries,
  not just an L3-derived one, since `_attach_header_graph` has no way to
  distinguish the two once they're both flattened into
  `gcc_option_tokens`. `l2_seed._include_operand_dirs` itself is now a
  thin alias forwarding to the shared function, so existing callers/tests
  needed no changes. Regression tests:
  `tests/test_service_unit.py::TestAttachHeaderGraphHashesIncludeSearchTokens`
  (both confirmed to fail against the pre-fix code — the positive case
  asserting a `gcc_option_tokens`-carried `-I` dir reaches
  `_clang_header_dump`'s own `extra_hash_dirs`, the negative case pinning
  the no-tokens baseline stays `()`).

  **Several smaller findings from the same CodeRabbit review round, all
  fixed alongside the above.** (1) The changelog fragment's early entries
  still named the now-removed `fold_l3_compile_context()` wrapper as the
  shipped fix — corrected to the combined function's real name (this
  file's own narrative-history style is left as-is, since later
  paragraphs already record the removal). (2) `seed_includes_and_fold_
  compile_context`'s own guard (`(sources is None and build_info is
  None) or (not want_seed and not headers)`) had a redundant second
  clause — `not want_seed and not headers` always reduces to `not
  headers`, since `want_seed = bool(headers) and ...` is already `False`
  whenever `headers` is empty — leaving a genuinely unreachable `if not
  headers:` guard a few lines further down; both simplified/removed. (3)
  The same function's `pending_cleanups.extend(cleanups)` ran before
  `_merge_l3_compile_context`/`_include_operand_dirs`, so either raising
  would have the `except Exception` branch's `_run_cleanups(cleanups)`
  remove the same already-handed-off thunks a second time — not fatal
  (`_run_cleanups` logs rather than raises on an already-closed handle),
  but a real, avoidable double-removal; moved the extend to after every
  remaining fallible step succeeds. (4) A genuinely weakened test
  assertion in `test_scan_l2_cleanup_ordering.py`
  (`test_scan_l2_seed_cleanup_runs_before_embed`): `assert seed_kwargs.
  get("pending_cleanups") is not None` also passes for the outer scan
  list itself (`defer_cleanup=[]` is never `None` either), so it would
  not have caught a regression that reused it — fixed to assert identity
  against the outer list directly. (5) Two cosmetic-only cleanups: a
  redundant local `import json` shadowing the same module-level import,
  and an unparenthesized chained `and`/`or` (ruff `RUF021`, not in this
  repo's enabled rule set but still real and fixed) in `perform_elf_dump`'s
  `parsed_with_build_context` stamp condition. (6) The three fake
  `seed_includes_and_fold_compile_context` implementations in
  `test_scan_l2_cleanup_ordering.py` each hand-built an identical
  `CompileContext` from the same eight kwargs — extracted into one shared
  `_CC_FIELDS`/`_explicit_ctx_from_kwargs()` helper, mirroring the
  identical pattern `test_cli_dump_helpers_coverage.py` already used.

  **An eleventh finding, from a further Codex review round (P1), on
  `_existing_include_dirs`'s own selection scope — real, but a
  pre-existing, cross-cutting design shared with `compare`'s implicit-dump
  path, documented as a known gap rather than fixed here.** When no
  explicit `-I` is given, `_existing_include_dirs` gathers directories
  from *every* `CompileUnit` in the build evidence, not only the unit(s)
  `resolve_header_compile_context` actually matched to the headers being
  parsed. Those directories reach the AST command as `extra_includes`,
  emitted ahead of the matched context's own include tokens — so in a
  multi-TU build, an unrelated TU's own colliding generated header (e.g.
  a stray `config.h`) can shadow the header the matched TU would have
  resolved, while the snapshot may still get stamped
  `parsed_with_build_context=True` from the (separate) successful fold,
  reading as more authoritative than the seed actually was. Confirmed
  **not** new to this PR: `service_input_resolution._seeded_includes`/
  `_seeded_compile_context` — the pre-existing pair `resolve_side_snapshot`
  already uses for `compare`'s implicit-dump operand, and the reference
  implementation this PR's own fold was modeled after — combines the
  identical broad-seed-plus-matched-fold shape already, unchanged by this
  PR. A correct fix needs `HeaderCompileContextResolution` to expose which
  `CompileUnit`s actually matched (today it exposes only a
  `matched_unit_count`), then both `_existing_include_dirs`'s caller here
  *and* `_seeded_includes` in `service_input_resolution.py` to restrict
  the seed to that set — a genuine, cross-cutting widening of a
  well-tested module's return shape and two independent call sites, not a
  scoped fix reactive to one review comment on one PR. Documented in
  `_existing_include_dirs`'s own docstring alongside this entry.

  **A twelfth finding, from a further Codex review round (P1), on
  `run_scan_core`'s own forwarding of the folded context to the baseline
  parse — real and fixed.** The eighth finding above fixed `run_scan_core`
  forwarding its *original* `compile_context` to `_run_baseline_compare`
  unconditionally; that fix itself was too broad in the other direction.
  The fold is derived by matching the *candidate*'s own headers against
  the *new* build's compile units, so its `-D`/`-U`/`-std`/include flags
  describe the new side specifically — but `_run_baseline_compare`'s own
  `_resolve_baseline_header_scope` parses a side-aware `-H old=PATH`
  baseline through its own, different old headers, whose macros/standard/
  generated-header paths may genuinely differ from the new build's.
  Forwarding the new side's folded context there unconditionally risked a
  bad parse or a false ABI diff on exactly the old-side-headers path the
  eighth finding's own fix never distinguished from the common case (no
  `baseline_headers`, baseline reuses the candidate's headers). No
  old-side build evidence exists to derive a matching fold for that case
  (there is no `--build-info-old`/`--sources-old` flag), so the correct
  fallback is the caller's plain, unfolded `compile_context` — not a
  second, unfounded fold attempt. Fixed by conditioning the forwarded
  value on whether `baseline_headers` was given:
  `eff_compile_context if not baseline_headers else compile_context`.
  Regression test:
  `tests/test_cli_scan.py::test_baseline_compare_with_side_aware_headers_keeps_unfolded_context`
  (confirmed to fail against the pre-fix code — the folded sentinel
  reached `_run_baseline_compare` even with `-H old=...` given).

  **A thirteenth finding, from a further Codex review round (P1), on the
  twelfth finding's own fix — a real regression in the fix itself, caught
  before merge.** `not baseline_headers` was the wrong signal: `cli_scan.py`
  builds `baseline_header = header_both + header_old`, so a bare, *shared*
  `-H api.h` (no `old=` scoping at all — the ordinary, most common
  `scan --against` usage) already makes `baseline_headers` truthy and
  identical in *content* to `headers`, since both draw from the same
  `header_both` list. The twelfth finding's own fix therefore treated
  every `scan --against` invocation with any headers at all as
  "old-side-scoped" and fell back to the unfolded `compile_context` —
  silently reintroducing this whole PR's own `NOT_COMPARABLE`/false-ABI-
  diff bug for the common case, one commit after fixing the narrower
  `-H old=PATH` case. Fixed by checking content equality instead of mere
  truthiness: `not baseline_headers or list(baseline_headers) ==
  list(headers)` — the fold is used whenever the old side's resolved
  headers are the same as the candidate's (shared, or genuinely absent),
  and only the caller's plain, unfolded context is used when they
  actually diverge (a real `old=` override). Regression test:
  `tests/test_cli_scan.py::test_baseline_compare_with_shared_bare_header_still_gets_folded_context`
  (confirmed to fail against the pre-fix — twelfth-finding-only — code,
  which forwarded `None` rather than the folded sentinel for a bare
  shared `-H` with no `old=` at all).

  **A fourteenth finding, from a further Codex review round (P1), on
  `ABI_RELEVANT_FLAG_PREFIXES` itself — real, pre-existing (confirmed
  present at this PR's own base commit, before any of its changes), and
  documented as a known gap rather than fixed here.** GNU/clang
  `-include`/`-imacros` and MSVC `/FI`/`/FU` (forced pre-include) are
  absent from this list, so `extract_abi_relevant_flags()` never captures
  them into `CompileUnit.abi_relevant_flags` — `buildsource.adapters.
  base.SOURCE_OPERAND_FLAGS` already recognizes these as value-taking, but
  for an unrelated purpose (not mistaking the operand for the TU source
  file), and that recognition never feeds this list. A matched compile
  unit's own forced-include header is therefore silently absent from
  `header_compile_context`'s derived L2 `CompileContext`, so the P0.3
  fold (reached from `dump`/`scan`/`compare`'s implicit-dump path alike,
  not just the three call sites this PR added) can report a real match
  and stamp `parsed_with_build_context` while still parsing without a
  macro-controlling forced-include header the real build always applies.
  Confirmed genuinely pre-existing, not introduced by this PR:
  `ABI_RELEVANT_FLAG_PREFIXES` and its consumers (`extract_
  abi_relevant_flags`, `header_compile_context.py`) already existed at
  commit `674a506` (this PR's own base, before any of its changes) with
  the identical omission — this PR only added two more call sites
  (`dump` ELF/PE-Mach-O, `scan`) to the same, already-existing
  `resolve_header_compile_context` machinery `compare`'s implicit-dump
  path already used. Not fixed here because a correct fix is a genuine,
  non-trivial extraction-logic change: unlike every other entry in
  `ABI_RELEVANT_FLAG_PREFIXES` (a bare prefix match that appends the
  single matched token), `-include`/`-imacros`/`/FI` always carry a
  required following value that must travel with the flag — appending
  just the bare prefix (the pattern this whole list otherwise uses)
  would silently drop the header filename that is the entire point. A
  correct fix needs a new spaced-value-flag branch in
  `extract_abi_relevant_flags` (mirroring, but distinct from, its
  existing `-D`/`/D` split-form handling), verified end-to-end through
  `header_compile_context._context_flags()`'s reconstruction and the
  real header-parse invocation's own handling of `-include`/`/FI` —
  a cross-cutting change to a shared, well-tested primitive several
  other pre-existing paths already depend on, not a scoped fix reactive
  to one review comment on this PR. Documented in `ABI_RELEVANT_FLAG_
  PREFIXES`'s own docstring alongside this entry.

  **A fifteenth finding, from a further Codex review round (P1), on the
  thirteenth finding's own header-equality fix — a real gap in the fix
  itself, caught before merge (fresh evidence).** Comparing only the
  resolved *header* lists was not sufficient: `cli_scan.py` builds
  `baseline_include = include_both + include_old` completely
  independently of `baseline_header = header_both + header_old`, so a
  shared, bare `-H api.h` (no `old=` scoping — passing the thirteenth
  finding's own equality check) combined with side-specific `-I
  old=old-build -I new=new-build` shares one header list across both
  sides while still routing each through a genuinely different include
  tree. The header-only check let the new side's folded `-D`/`-std`/
  sysroot/include context reach a baseline parsed through a different
  include scope — parsing the old binary under the new build's own
  configuration risks a bad parse or a false ABI diff on the old side,
  the exact failure mode this whole fix exists to prevent. Fixed by also
  requiring the old side's resolved include scope to match the
  candidate's own effective includes (`not baseline_includes or
  list(baseline_includes) == list(eff_includes)`, ANDed with the
  existing header-equality check) before reusing the fold — a genuine
  `-I old=PATH`/`-I new=PATH` override on either side now falls back to
  the caller's plain, unfolded context, same as a genuine `-H old=PATH`
  override already did. Regression test:
  `tests/test_cli_scan.py::test_baseline_compare_with_side_aware_includes_keeps_unfolded_context`
  (confirmed to fail against the pre-fix — header-equality-only — code,
  which forwarded the folded sentinel even though the old side's
  resolved include scope diverged from the candidate's).

  **A sixteenth finding, from a CodeRabbit review round (Major), on
  `header_utils._INCLUDE_FLAG_PREFIXES`'s own matching itself — real,
  and confirmed pre-existing for most of its consumers (present at this
  PR's own base commit `dc09aec`, before any of its changes), documented
  as a known gap rather than fixed here.** Every match against this
  tuple — in `_has_include_build_context`, `_build_context_include_dirs`,
  `_flag_tokens`, `_msvc_deferred_flag`, `include_operand_dirs`, and
  `buildsource.l2_seed._split_include_tokens` alike — is exactly
  case-sensitive `str.startswith`. That is correct for the GNU/clang
  spellings (a real compiler only ever accepts the documented lowercase
  forms), but wrong for the two clang-cl-only entries: verified against
  real documentation for both drivers — native `cl.exe`'s own options are
  case-sensitive (and it doesn't recognize `/imsvc` at all, a clang-cl-only
  spelling), while clang-cl's own option parsing is documented
  case-insensitive, so `/IMsvc`/`/EXTERNAL:I` are legal, real spellings
  this tuple's exact-case matching silently fails to recognize as
  include-search flags at all. Concretely: a real clang-cl build record
  using `/IMsvc` would have that directory not suppress the L2 include-dir
  seed, not resolve through the existing-dir dedup, and — this PR's own
  two new consumers specifically — not be hashed into the AST cache key's
  `extra_hash_dirs` (`include_operand_dirs`) and not be carved out of the
  leading last-flag-wins token group by `_split_include_tokens`, so an
  explicit `/IMsvc` override could still lose to a derived `-I` for a
  colliding header. Confirmed pre-existing for `_has_include_build_context`/
  `_build_context_include_dirs`/`_flag_tokens`/`_msvc_deferred_flag` — all
  four already existed at commit `dc09aec` (this PR's own base) with the
  identical case-sensitive matching; only `include_operand_dirs` (moved
  from `l2_seed._include_operand_dirs`) is new to this PR. Not fixed here:
  a correct fix needs case-insensitive matching applied *consistently* to
  every one of those six consumers, not just the two this PR added — fixing
  only the new ones while leaving the pre-existing four case-sensitive
  would be strictly worse than today's uniform gap, making the
  seed-suppression logic and the cache-hashing/ordering logic silently
  *disagree* about whether a given `/IMsvc` token is an include-search
  flag at all. It also needs a genuine new tie-break case-folding
  introduces that a case-sensitive scan never had: `/imsvc` case-
  insensitively also matches the shorter `/I` prefix, and picking the
  wrong one changes which include bucket the directory is treated as (see
  `_msvc_deferred_flag`'s own bucket-priority docstring) — a real, if
  narrow, cross-cutting change to a shared, well-tested primitive several
  pre-existing callers depend on, not a scoped fix reactive to one review
  comment on this PR. Documented in `_INCLUDE_FLAG_PREFIXES`'s own
  docstring alongside this entry.

  **A seventeenth finding, from a further Codex review round (P1), on the
  counterpart cache key the tenth finding's `_attach_header_graph` fix was
  supposed to stay aligned with — real, and fixed.** `service._dump_elf`'s
  own `deferred_dirs` computation (its PRIMARY header parse's cache key,
  not the header-graph attach) hashed only `resolve_inferred_header_roots`'s
  deferred roots, never any include-search directory riding in the compile
  context's own `gcc_option_tokens` — exactly the gap the tenth finding
  closed on `_attach_header_graph`'s side, left open on the primary parse
  whose cache key that fix was meant to match. Editing a header under such
  a directory could let the primary parse reuse a stale cached AST while
  `_attach_header_graph`'s own independent second parse correctly
  reparsed, producing a snapshot whose declarations and embedded graph
  describe different source states — the exact divergence the tenth
  finding's fix was supposed to prevent, just from the other side. Fixed
  by folding `include_operand_dirs(cc.gcc_option_tokens)` into `_dump_elf`'s
  `deferred_dirs` too, the identical fold `_attach_header_graph` already
  applies. Regression test:
  `tests/test_service_unit.py::TestDumpElf::test_gcc_option_tokens_include_dir_is_hashed`
  (confirmed to fail against the pre-fix code, which never hashed the
  directory into `extra_hash_dirs`).

  **An eighteenth finding, from a further Codex review round (P2), on a
  real ordering bug in `perform_elf_dump`'s own composition — distinct
  from `_merge_l3_compile_context`'s already-fixed explicit-vs-derived
  split — and fixed.** `resolve_inferred_header_roots`'s own `deferred`
  tokens (the inferred `-H` header root, emitted in a search bucket that
  defers below any *existing* build context) were folded into the
  "explicit" side of the L3 fold purely to suppress the fold's own
  internal broad include-dir seed — but `_merge_l3_compile_context` has no
  way to tell a synthetic deferred marker apart from a genuine user token,
  so it ranked `deferred` ahead of the L3-derived include tokens too. A
  colliding generated header under the L3-derived directory could then be
  shadowed by the inferred root — the exact inversion "deferred" exists to
  prevent. Fixed by excluding `deferred` from the tokens passed into the
  fold and appending it back at its correct lowest-priority position
  (after the L3-derived includes) once the fold result is known. Verified
  this doesn't break the suppression it was providing: `deferred` is
  non-empty only when the *original* tokens (unmodified, still passed to
  the fold) already showed build-context evidence, which the fold's own
  suppression check reads directly — so nothing is lost by leaving
  `deferred` itself out. Regression test:
  `tests/test_non_elf_dump_l2_seed.py::test_perform_elf_dump_keeps_deferred_inferred_root_below_l3_derived_includes`
  (confirmed to fail against the pre-fix code, both via an internal
  assertion catching `deferred` leaking into the fold's own explicit
  tokens and via the final ordering check).

  **A nineteenth finding, from real Bazel/castxml CI evidence (not a
  hand-built fixture) on `napetrov/abicheck-bazel-lab`'s diagnostic PR #14,
  after repinning it to `abicheck/abicheck@84cf3d4` (PR #788) — a narrower
  residual of this same topic survives the eighth/`_build_new_snapshot`
  fold fix above, investigated but not fixed.** The lab's
  `validate-two-fresh-mains.yml` workflow builds BASE and HEAD with real
  Bazel, captures target-scoped cquery/aquery evidence for both, `dump`s
  each fresh (`fresh-base.abi.json`/`fresh-head.abi.json`, same code,
  identical `--sources`/`--build-info`), then separately `scan`s HEAD
  `--against` the fresh BASE dump. `compare fresh-base fresh-head` (pure
  `dump` vs `dump`) reads `COMPATIBLE`/`NO_CHANGE` with a complete
  `analysis_assurance` — confirming `language_standard` parity holds, i.e.
  the eighth-finding fix above genuinely works. But `scan HEAD --against
  fresh-base.abi.json` (the same HEAD code, same `-H`/`--sources`/
  `--build-info` inputs, `dump`'s baseline) still reads `NOT_COMPARABLE`:
  `diff.reason` names exactly one differing field, `include_sequence` (not
  `language_standard`, which no longer reproduces) — a narrower,
  previously-undetected sibling of the field this whole topic exists to
  close, confirmed via the run's own uploaded `two-fresh-mains-validation`
  artifact (run
  https://github.com/napetrov/abicheck-bazel-lab/actions/runs/31950549361,
  job 95173233150, commit `a7f5a7f`). The most concrete, verified-by-
  reading (not yet verified against a live repro — no `bazel`/`castxml` in
  this pass's environment) structural asymmetry: `scan_engine.
  _build_new_snapshot` passes its raw, **unexpanded** `headers` list
  (`-H new=math.h -H new=include` — the directory entry still present as a
  single item) into both `seed_includes_and_fold_compile_context` and
  `service.resolve_input`, while `cli_dump_helpers.perform_elf_dump` first
  calls `expand_header_inputs()` — which recursively expands a directory
  via `header_utils.iter_directory_headers` into individual files, in a
  deterministic order, with order-preserving dedup — and passes that
  already-**expanded** `resolved_headers` list to the identical two calls
  instead. `seed_includes_and_fold_compile_context`'s own use of `headers`
  is confirmed harmless (`seed_l2_includes` only ever tests it for
  truthiness, never iterates it), so the divergence — if this asymmetry is
  indeed the cause, which is not yet confirmed — would have to originate
  deeper, in whatever directory-expansion `service.resolve_input`/
  `dumper.dump()`'s own ELF path performs internally on an unexpanded
  directory entry, which may not traverse/order/dedup identically to
  `expand_header_inputs`'s explicit, upfront pass. **Not fixed here**: the
  hypothesis above is code-reading-only, this pass's environment has no
  `bazel`/`castxml` to build a live repro and verify it, and even if
  confirmed, the fix (either making `scan_engine._build_new_snapshot` call
  `expand_header_inputs()` upfront the way `perform_elf_dump` already does,
  or tracing the deeper internal expansion path to bring it in line) needs
  the same real-repro verification discipline every other finding in this
  topic already required, not a guess shipped without it. Consequence for
  `napetrov/abicheck-bazel-lab`: PR #14's `fresh-to-fresh` control job
  should be treated as still red on this one residual field, and the lab's
  own checked-in `abi/math.abicheck.json` should **not** be regenerated
  against the new core pin yet — doing so now would just encode a baseline
  that a fresh `scan --against` still can't cleanly compare to, the same
  problem this diagnostic exists to catch, not fix.

- **`dump --lang c++` is silently discarded on the primary clang header-AST
  pass for a language-ambiguous header, diverging from `_attach_header_graph`'s
  own pass on the identical headers — investigated, not fixed (G31 Phase C
  castxml-installation pass, fresh evidence, reproduced end-to-end).**
  `cli_dump_helpers.py`'s ELF `dump` path (and `service.py`'s two mirrors,
  `_dump_elf`/PE-Mach-O's `_header_graph_lang`) all normalize the caller's
  `lang` to `lang if lang == "c" else None` before calling `dumper.dump()` —
  deliberately, per their own comment: "every format's own main pass
  normalizes `lang` to only ever force a language explicitly requested,
  letting auto-detection run otherwise (including for the default 'c++')",
  so `_attach_header_graph`'s own `_clang_header_dump` call computes the
  *identical* cache key and hits the in-process AST memo instead of paying a
  second clang invocation. That assumption — "an explicit `--lang c++` and
  auto-detection converge on the same result anyway, so unifying their cache
  keys is free" — is false whenever the header itself is language-ambiguous:
  `_resolve_force_cpp`'s auto-detection (`_detect_cpp_headers`/
  `_detect_cpp20_headers`/an explicit `-std=c++NN`) requires the header to
  contain *some* C++-only syntax, but a plain POD struct with no such syntax
  (`struct Widget { int x; int y; };` — ordinary, real C++ code, e.g. a
  value/DTO type) compiles as valid C too and auto-detects as C. Reproduced
  end-to-end: `abicheck dump lib.so -H widget.h --ast-frontend clang --lang
  c++` silently parses `widget.h` in **C mode** for the primary snapshot
  (confirmed via the raw clang AST: a plain `RecordDecl`, not
  `CXXRecordDecl`, no `definitionData` at all) — with no error, warning, or
  any user-visible sign the explicit `--lang c++` was overridden — while the
  *same* `dump`'s internal `_attach_header_graph` pass, reached through a
  different `lang` derivation one call removed
  (`abicheck/service.py:1281`/`abicheck/service.py:608`'s ELF branch shares
  the identical `lang if lang == "c" else None` squash, so it too loses the
  signal — the divergence traced here is specifically the ELF `_dump_elf`
  helper's own second, ADR-050/G31-era call site,
  `abicheck/cli_dump_helpers.py:1717`, versus `_clang_header_dump`'s
  documented "an explicit `--lang c++`/`cpp` always wins" contract at the
  function it's calling into — the squash happens one layer *above* that
  contract, defeating it before it ever runs). Concrete, silent correctness
  cost: `RecordType.is_standard_layout`/`is_trivially_copyable` (and any
  other clang-only, C++-semantic-only fact — `_clang_record_type_traits`
  itself documents "a plain C `RecordDecl`... genuinely absent", the correct
  behavior *for C mode*, just not the mode the user asked for) silently read
  `None` instead of a real value for a header that would parse identically
  either way *except* for these C++-only facts, with the user's own explicit
  override having no effect. **Not fixed here**: the squashing is not an
  oversight — it is a deliberate, twice-Codex-reviewed cache/memo-consistency
  design (see `abicheck/service.py:608`'s own comment) that this finding does
  not invalidate for the *common* case (a header with real C++ syntax
  auto-detects as C++ regardless, so squashing costs nothing there) — only
  for the specific, real, silently-wrong case: an explicit `--lang c++` on a
  syntactically-ambiguous header. A correct fix needs the squash itself to
  stop conflating "auto-detect, and it happens to reach the same verdict as
  the default" with "explicitly override, must not be silently downgraded to
  a guess" — e.g. resolving `force_cpp` once, upstream of all three call
  sites and their `_attach_header_graph` mirrors, and threading the
  *resolved* boolean (or an unsquashed `lang` plus a corrected memo key that
  hashes the resolved language rather than the raw flag) through consistently
  — a change to a shared cache-key contract three call sites and two
  Codex-reviewed comments currently rely on, not a one-line fix at any single
  site. No test in the repository currently exercises this path: every
  existing `is_standard_layout`/`is_trivially_copyable` regression test
  (`tests/test_dumper_clang.py::test_parse_types_populates_standard_layout_and_trivially_copyable`,
  `tests/test_diff_layout.py`) hand-builds the parsed AST or `RecordType`
  directly, bypassing `dumper.dump()`'s CLI-level `--lang` resolution
  entirely — closing this gap should add an end-to-end regression case in
  the shape of `tests/test_clang_header_backend_integration.py`'s existing
  siblings, verified against a real compiled library with an
  intentionally-C-compatible C++ struct, once the fix itself is designed.

  **Closed for the `abicheck dump` ELF CLI path — scoped narrower than the
  "resolve `force_cpp` once, upstream of all three call sites" design sketch
  above (G31 continuation).** Rather than a shared, force_cpp-boolean
  cache-key contract spanning `cli_dump_helpers.py`, `service.py`'s
  `_dump_elf`, and `service.py`'s PE/Mach-O `_header_graph_lang`, the actual
  fix is narrower: only `cli_dump_helpers.py`'s ELF `dump` CLI path had a
  real *internal* divergence between its own primary pass (squashed) and its
  own `_attach_header_graph` call (raw, unsquashed) — `service.py`'s
  `_dump_elf` and its `_header_graph_lang` computation already squash
  *consistently* with each other (both feed the identical normalized value),
  so `compare`'s implicit-dump path and the Python `service.run_dump` API
  were never the site of this specific divergence; PE/Mach-O's own primary
  pass (`_try_header_scoped_dump`) never squashed at all. What was missing
  everywhere is the one bit no string-normalization scheme can recover on
  its own: whether `--lang` was genuinely given on the command line, since
  Click's own default for `--lang` (`LANG_DEFAULT`, `cli_options.py`) is the
  identical string `"c++"` a real `--lang c++` produces. `dump_cmd` now
  resolves this once via Click's own parameter-source tracking
  (`click.get_current_context().get_parameter_source("lang") ==
  click.core.ParameterSource.COMMANDLINE`) and threads a new
  `lang_explicit: bool` keyword parameter through `perform_elf_dump`, which
  derives one `_effective_lang` (the real `lang` when explicit or `"c"`,
  else `None`) and passes that identical value to *both* the primary
  `dump()` call and the `_attach_header_graph` call, instead of the primary
  pass's own one-off squash and the graph pass's raw pass-through. This also
  fixes a second, previously-undocumented half of the same divergence in the
  *other* direction: on a plain default invocation (no `--lang`, still
  `lang="c++"`), the header-graph pass previously force-parsed C++
  unconditionally (since a non-empty `lang` was always treated as explicit
  by `_resolve_force_cpp`), while the primary pass correctly auto-detected —
  so even a default, no-flags `dump --ast-frontend clang` could already
  silently disagree with itself between its own primary snapshot and its
  own embedded header-graph. Verified end-to-end against a real compiled
  library with an intentionally-C-compatible POD struct (`struct Widget {
  int x; int y; };`, exactly this entry's own repro shape) through the real
  `abicheck dump` CLI, not a hand-built AST or `RecordType` — see
  `tests/test_clang_header_backend_integration.py::
  test_cli_dump_explicit_lang_cpp_forces_cpp_mode_on_ambiguous_header`.
  **Closed for `service.run_dump`/`DumpRequest`/`CompareRequest` in a later
  pass, once a real conda-forge castxml build (0.7.0, within the
  `>=0.6.11,<0.8.0` policy range — `castxml_policy.py`) was available in this
  environment to verify against, alongside clang 18.** Rather than the
  `lang: str | None = None` tri-state default this entry originally
  sketched — a public-API *shape* change with a wide, hard-to-verify blast
  radius across every `resolve_input`/`run_dump` caller (`compare`, `scan`,
  `appcompat`, `l0_export_delta`, ...), most of which still legitimately pass
  a concrete, Click-defaulted `lang` string that must keep auto-detecting —
  the actual fix is the same **additive** `lang_explicit: bool = False`
  parameter the CLI fix above already established, generalized one layer
  up: `service.resolve_input`/`run_dump`/`_dump_elf`/`_dump_pe`/
  `_dump_macho` and `service_header_scoped._try_header_scoped_dump` all gain
  it (default `False`, a no-op — every existing caller's behavior is
  bit-for-bit unchanged), `DumpRequest.lang_explicit`/
  `CompareRequest.lang_explicit` carry it on the typed API, and
  `service_dump_pipeline.run_dump_request`/`service_compare_pipeline.
  resolve_compare_request` thread it through `resolve_side_snapshot` (their
  one shared per-side resolution function) to `service.resolve_input`. The
  `dump`/`compare` CLIs resolve it the identical way `dump_cmd` already did
  — `compare_cmd` mirrors the established `_frontend_explicit`/
  `_nostdinc_explicit` `ctx.get_parameter_source(...)==COMMANDLINE` pattern
  already used one function over in `cli_compare_helpers._embed_inline_
  source_side` for `--ast-frontend`/`--nostdinc`, extended to `--lang` and
  threaded through `cli_resolve._resolve_compare_snapshots` into
  `CompareRequest`. The whole-snapshot disk cache key
  (`service_dump_cache._dump_cache_extra_key`/`cached_run_dump`) folds
  `lang_explicit` in too — the identical `lang` string now legitimately
  resolves to two different parsed ASTs depending on it, so a cache entry
  for one must never serve the other. `scan`/`appcompat`/the
  release/set-input fan-out/`l0_export_delta` are **not** touched by this
  pass — they still pass their own already-resolved, Click-defaulted `lang`
  string with `lang_explicit` defaulted `False`, so they keep their
  pre-existing (unfixed, but also not regressed) behavior; wiring each is
  the identical mechanical pattern applied here, left for its own follow-up
  rather than expanding this pass's verified surface further. Verified
  end-to-end against the same real, intentionally-C-compatible POD struct
  through `service.resolve_input`, `DumpRequest`/`run_dump_request`,
  `CompareRequest`/`resolve_compare_request`, and the real `compare` CLI
  (Click parameter-source spy) — see
  `tests/test_clang_header_backend_integration.py::
  test_dump_request_and_compare_request_lang_explicit_forces_cpp_mode` and
  `tests/test_service_dump_cache.py`'s
  `test_differs_by_lang_explicit`/`test_lang_explicit_reaches_run_dump_and_keys_separately`.
- **Opaque-type suppression is keyed by bare `RecordType.name`, not a
  qualified identity — pre-existing on both header backends, newly reachable
  on direct-clang by PR #719's opaque-handle-type fix (Codex review,
  investigated, not fixed).** `diff_filtering._find_opaque_types()` (and its
  siblings `_find_by_value_types()`/`_downgrade_opaque_type_changes()`) index
  a snapshot's opaque/impl-private types by `t.name` alone, and
  `_root_type_name()` derives a `Change`'s matching key from `Change.symbol`
  the same way `diff_types.py` stamps it — also the bare name for a
  top-level type change (`name = t_old.name`), never `qualified_name`. Two
  distinct records sharing a bare name in different namespaces (a complete,
  genuinely public `api::Foo` and an unrelated forward-only `impl::Foo`)
  therefore collide in the same `opaque: set[str]`: if `impl::Foo` is opaque,
  `_downgrade_opaque_type_changes()` silently suppresses a real
  `TYPE_SIZE_CHANGED`/field-change finding on the unrelated, complete,
  public `api::Foo` too, since both match the bare key `"Foo"`. Confirmed
  by reading the code (no live repro run); this predates PR #719 and
  already applied identically to castxml's own `is_opaque=True` types — the
  PR's clang-backend opaque-stub fix (previously clang silently dropped
  every forward-decl-only type instead of emitting a stub) makes this
  reachable from a new source, not a new bug class. **Not fixed here**: a
  correct fix needs qualified identity threaded consistently through
  `_find_opaque_types`/`_find_by_value_types`/`_downgrade_opaque_type_changes`
  and `_root_type_name`'s several call sites in `diff_filtering.py` (at
  least five, by grep), none of which currently have test coverage for the
  cross-namespace-collision case to validate a change against — a
  systematic, cross-cutting rework, not a scoped fix reactive to one review
  comment. Filed here rather than attempted under this PR's time budget,
  per this file's own "known gaps over risky reactive patches" convention.
- **`dumper_clang.py`'s `parse_types()` conflates a C/C++ tag-namespace
  identity with an ordinary-namespace typedef identity that happens to
  share the same spelling — pre-existing, not introduced by PR #719's own
  changes, but investigated and confirmed reachable in this pass (Codex
  review, investigated, not fixed).** For a legal (if unusual) header like
  `struct Foo; typedef struct { int x; } Foo;` — an unrelated forward-only
  tag `Foo` and a SEPARATE anonymous struct given the ordinary-namespace
  name `Foo` via typedef, which C's two-namespace rule keeps genuinely
  distinct — `parse_types()`'s `identity = "::".join([*entry.scope, name])`
  computation uses the same bare `name` for both (the tag's own `name`,
  and the anonymous record's `anon_names`-derived typedef fallback name),
  so the two collide into one `identity` key. Reproduced directly against
  `_ClangAstParser`: only ONE `RecordType` named `Foo` is emitted (the
  typedef-backed definition), and the unrelated opaque tag `struct Foo;`
  is silently absent from the snapshot entirely — the exact regression
  PR #719's own opaque-handle-type fix was written to prevent, just
  reached through a different, adjacent mechanism (a spelling collision
  across namespaces rather than declaration-order/redecl-set instability).
  **Not fixed here**: closing it correctly needs the tag-namespace vs.
  ordinary-namespace distinction threaded through the `identity` key
  itself (and every downstream consumer that currently assumes `identity`
  uniquely names one type — `_build_record`, the opaque/deprecated/kind
  merge maps this same function already builds), which is a real, if
  narrow, data-model change to a function this same PR already revised
  three times this session (the opaque-stub fix, the kind-canonicalization
  fix, and that fix's own regression fix) — each of which independently
  needed careful re-verification against `dumper_clang.py`'s exact
  2000-line hard cap. A fourth, differently-shaped change to the same
  function under continued review pressure is exactly the risk profile
  this file's own "known gaps over risky reactive patches" convention
  exists to avoid; a correct fix needs its own dedicated pass with fresh
  test coverage for the namespace-collision case specifically, not a
  same-session extension.
- **The castxml L4 source-ABI extractor does not fold the resolved
  EMULATED compiler's identity into either `fact_set.compiler_version` or
  the D8 TU cache key — attempted once for the persisted half, and
  REVERTED after a follow-up review caught it as a real regression, not a
  fix (Codex review, PR #719, three follow-up rounds).** castxml shells
  out to the emulated compiler (`cc_bin`, resolved per compile unit by
  `pick_compiler_binary` — the real build's own recorded `argv[0]`, absent
  an explicit `--gcc-path` override) purely to discover its built-in
  defines/include paths (`docs/learn/architecture.md`), so a header
  conditional on `__GNUC__`/`_MSC_VER` can extract differently once that
  compiler is upgraded at the same path, even though castxml itself and
  its own `--version` probe stay identical — a real gap. The second
  follow-up round's fix folded a STAT signature (`dev`/`ino`/`mtime_ns`/
  `size`) of the resolved `cc_bin` into `fact_set.compiler_version`
  (`_stamp_fact_set_and_coverage()` already has the per-TU `compile_unit`
  in scope). The third follow-up round found this made things WORSE, not
  better: those stat fields are filesystem-local, so (1) two TUs in ONE
  surface resolved to DIFFERENT but same-toolchain drivers (`gcc` for a
  `.c` TU, `g++` for a `.cpp` TU — an entirely ordinary mixed-language
  build) get different suffixes purely from being different files on
  disk, tripping `rollup_fact_set()`'s exact-equality check into
  `fact_set_inconsistent` for a perfectly healthy, unchanged surface; and
  (2) an identical build run on two different machines/paths (baseline
  collected in one CI run, compared against another — an entirely
  ordinary workflow) never shares device/inode at all, making every such
  cross-machine comparison spuriously inconsistent and silently
  suppressing every structured/opaque/source-edge finding. Between
  "silently under-detect genuine toolchain drift" (the pre-existing gap)
  and "spuriously suppress every finding on completely ordinary mixed-
  language or cross-machine comparisons" (the attempted fix), the second
  is strictly worse for typical usage, so the stat-based fold was
  reverted rather than patched further under review pressure. **Not fixed
  here, either half**: a correct fix needs a portable SEMANTIC identity —
  a real `cc_bin --version` probe, normalized (mirroring
  `_castxml_tool_version`'s existing shape, but for an arbitrary
  gcc/clang/MSVC driver rather than castxml specifically) — not
  filesystem stat fields, for the persisted half; the D8 cache-key half
  additionally needs a wider, per-instance-hook-signature change to
  `source_replay.py`'s shared cache-key infra (also used by
  `ClangSourceExtractor`, whose analogous `--gcc-path` case resolves once
  per extractor construction, not per TU, so its existing zero-arg hook
  shape doesn't transfer directly), plus a decision on probing cost (a
  version probe per distinct resolved `cc_bin` across a build with mixed
  toolchains, cached the same way `_castxml_tool_version`'s `lru_cache`
  already is). Left for a dedicated follow-up with its own MSVC-vs-
  GCC-vs-Clang version-probe design, not a same-PR reactive patch.
- **A compatible-but-ambiguous opaque redeclaration set (`class H; struct
  H;`) that later gains a same-key COMPLETE definition still produces a
  false `SOURCE_LEVEL_KIND_CHANGED` — investigated, not fixed (Codex
  review, PR #719, fourth follow-up round on this same area).** The
  earlier "keep the definition's own kind" fix (this same file, above)
  deliberately never applies `dumper_clang.py`'s canonicalized opaque
  `override_kind` to a record that survives as a COMPLETE definition — the
  definition's own real, unmodified `_record_kind()` always wins, which is
  correct in isolation (a real kind change on the definition itself must
  never be hidden). But this means an identity whose opaque forward decls
  were genuinely ambiguous (`class H;` AND `struct H;`, both present,
  canonicalized to the fixed `"struct"` spelling per the kind-stability fix
  above) reports "struct" in an old snapshot with no definition, while a
  new snapshot adding a same-key `class H {...};` definition reports the
  definition's real "class" — a spurious kind-change finding even though
  "class" was always one of the two already-compatible, already-declared
  keys. Reproduced directly against `_ClangAstParser`. **Not fixable at
  the extraction layer**: an opaque snapshot has no way to know in advance
  which of the ambiguous, compatible keys a LATER definition will use, so
  no fixed canonicalization choice can match every possible future
  definition. The generic comparison (`diff_types_abicc_parity.
  _diff_type_kind_changes()`, shared across all producers) does a plain
  `t_old.kind != t_new.kind` check with no notion of "this kind was
  extracted from a genuinely ambiguous, unresolved forward-decl set" —
  and correctly so, since blanket-suppressing every class↔struct
  transition would hide the genuine, intentional ones this detector
  exists to catch. Closing this properly needs new PROVENANCE carried on
  `RecordType` itself (e.g. a `kind_ambiguous`-shaped field, schema-
  versioned, populated by both header backends, read by the diff layer to
  skip exactly this one shape of transition) — a cross-cutting model
  change touching `model.py`, both backends, serialization, and the
  detector, not a scoped fix reactive to one review comment, and
  `dumper_clang.py`'s `parse_types()` has already been revised four times
  in this same PR session for adjacent findings in this exact area. Filed
  here per this file's own "known gaps over risky reactive patches"
  convention rather than attempted under continued review pressure.
- **Linkage-blind removal — attempted twice, reverted twice. The evidence
  keeps proving something adjacent to the invariant.** A symbol vanishing from
  the export table is reported as `func_removed` (and, on the same symbol,
  `func_deleted_elf_fallback`) regardless of its *linkage*, so a weak
  vague-linkage export reads as a hard break. The demotion's invariant is
  *"every consumer already emitted its own copy"*, and both attempts
  established something else:

  1. **`Function.is_inline`** proves the `inline` *specifier*, not that a
     definition exists. Verified against real clang: `inline int f();` yields
     `inline=True, has_body=False`, and both AST parsers assign the field
     straight from the specifier attribute with no body check.
  2. **COMDAT-group membership** proves the *library* used vague linkage, not
     that its *consumers* did. `extern template` is the counterexample, and it
     is ordinary code: a public header carrying `extern template struct
     Box<int>;` tells every consumer TU **not** to instantiate, while the
     library's own explicit instantiation still emits a weak COMDAT
     definition. Verified against g++ — the library object has
     `_ZNK3BoxIiE3getEv` inside a COMDAT group while the consumer object has
     it as `NOTYPE GLOBAL UND` with an empty COMDAT set. Dropping that export
     breaks the consumer, and the predicate demoted it (Codex review).

  The shared shape is worth naming, because it is what a third attempt will
  hit too: a fact about the **library's own build** was read as a fact about
  **its consumers**. Nothing in two library snapshots, and nothing in one
  library's object files, distinguishes "the consumer emitted a copy" from
  "the consumer holds an undefined reference". Only consumer-side evidence, or
  a header fact recording `extern template` (which castxml does not expose —
  checked through 0.7.0), can separate them.

  **Kept, because it is sound and answers a real question:**
  `buildsource/comdat_groups.py` — an ELF `SHT_GROUP`/`GRP_COMDAT` parser
  (byte-order correct, ELF-only, degrading to diagnostics on unreadable
  objects) and its `BuildEvidence.comdat` collection. It answers "did *this
  build* emit this symbol vaguely", which is genuinely useful and was already
  reserved vocabulary in `graph_facts.py` (`comdat_group`) for a future
  linker-artifact extractor. It is simply not sufficient for the demotion.

  **Separately open, and independent of the above:** the L3 collection path
  does not deliver usable object paths. `CompileUnit.output` is a label
  normalized *for persistence*, not a path — home paths redacted to `~/...`
  (ADR-032 D7), Ninja/Make/Bazel outputs relative to `CompileUnit.directory` —
  and `CompileDbAdapter` never assigns it at all, discarding the compile
  database's `output` field and `-o` alike. **Half-closed on the reading
  side:** `build_evidence._resolved_object` now expands `~` and joins a
  relative label onto the unit's own `directory`, and skips a label naming no
  file that exists, so an adapter that *does* record an output is readable
  from outside the build directory. That also makes the scan conservative
  where it used to be destructive: a build whose objects resolve to nothing
  leaves `comdat` untouched rather than replacing a scan loaded from an
  existing pack with an empty, unresolvable one, and a fresh scan that
  established nothing never displaces one that did. **Still open:** the
  producing side — `CompileDbAdapter` recording an output at all, and every
  adapter keeping the raw resolved path alongside the redacted label, so a
  pack collected on one machine can be scanned on another. Until then the
  scan is opt-in (`ABICHECK_COLLECT_COMDAT=1` at `inline.py`'s call site):
  parsing every object's symbol table is real I/O, and no detector consumes
  the result.

- **`Function.elf_binding`/`Variable.elf_binding` (and the pre-existing
  `elf_visibility` it mirrors) collapse mixed bindings across symbol-versioned
  aliases sharing one bare name — investigated, not fixed (Codex review,
  fresh evidence).** `ElfMetadata.symbol_map` is a `name -> ElfSymbol` dict
  built from `elf.symbols`, last-entry-wins; `elf_metadata.py`'s own parser
  deliberately never embeds a version suffix in `ElfSymbol.name` (pyelftools
  doesn't decode `@@`/`@` into the name either — see the `_pyelftools_exported_symbols`
  comment), so two legally distinct versioned definitions of one exported
  name — e.g. a `GLOBAL` `foo@GLIBC_2.2` and a `WEAK` `foo@@GLIBC_2.14` —
  collapse to a single dict entry, and `_populate_elf_visibility` (which both
  fields share) reads only that survivor. A `Function`/`Variable` whose
  `mangled` matches such a bare name therefore reports whichever binding
  happened to parse last, not "the" binding — and if that happens to be
  `WEAK` while an older, still-live version was `GLOBAL`, a `binding: weak`
  suppression rule (`suppression.py`) could match a removal that, from the
  `GLOBAL` version's perspective, is a real break. Not fixed here: a correct
  fix needs `symbol_map` (or a sibling index) to preserve every versioned
  entry per bare name rather than collapsing to one — a change with several
  other consumers beyond `elf_visibility`/`elf_binding` (`dumper_elf_symbols.py`'s
  own callers, `diff_versioning.py`'s existing version-aware machinery, which
  already correlates symbols against `versions_defined`/`versions_required`
  through a different path) that deserves its own scoped design rather than a
  drive-by change to a cached property several modules depend on today. Per
  this file's own "known gaps over risky reactive patches" convention.

- **Default dependency scoping (PR #649) vs. contextual reachability
  (`type_reachability.py`) — the direct-reference conflict is fixed; the
  comparability-contract gap is not.** A status-review follow-up flagged
  that `dump`'s default header-origin scoping (`dumper_scoping.py`) and the
  same-pass contextual-reachability work were pulling in opposite
  directions: reachability says a dependency type directly named in a
  public signature (`std::string` taken by a public function, or a
  platform type like `struct tm`) is genuinely part of the library's ABI
  contract, while scoping unconditionally dropped every declaration whose
  own header was a toolchain/system header, regardless of whether anything
  referenced it directly. Fixed: `scope_snapshot_excluding_dependencies`
  now retains a dependency-header type/enum that is directly named by a
  kept declaration's own return/parameter/variable type or by a kept
  type's own field/base (`_directly_referenced_dependency_names`), while
  still dropping what's only reachable transitively through that type's
  own internals (`std::string::_Alloc_hider` and the like stay excluded).
  **Still open, deliberately not attempted in the same change:** the
  chosen dependency-scoping mode (scoped vs. `--include-system-declarations`) is
  not part of the `ExtractionContract` `scope_fingerprint`
  (`comparability.py`'s `SCOPE_FIELD_KEYS`), so two snapshots extracted
  under different scoping modes can still compare as "comparable" even
  though they don't share the same fact universe — and `cli.py`'s inline
  (non-persisted) `compare old.so new.so` path still hardcodes
  `include_dependencies=True` regardless of what a persisted baseline JSON
  on the other side of the same comparison was scoped with. Closing that
  gap needs its own scoped design (a new `SCOPE_FIELD_KEYS` entry plus a
  `comparability.py`-level compatibility rule, verified against
  `test_comparability_gate.py`'s existing superset-growth assertions), not
  a drive-by extension of the direct-reference fix above. Until then, the
  safe authoritative flow for a compiler/stdlib-sensitive comparison is
  either `--include-system-declarations` on both `dump` invocations, or comparing
  two default-scoped persisted snapshots against each other rather than
  mixing a persisted baseline JSON with a live-binary operand.

- **Depth contract, CLI vs. API/MCP — closed for real by G33 Phase 5, having
  first been closed as stale.** Finding 1 below is now out of date in a way
  worth keeping visible rather than deleting: it closed this gap on the
  grounds that no service/MCP surface *promised* a depth-qualified snapshot,
  so there was nothing to extend the gate to. That was true when written and
  is no longer: `DumpRequest.depth` and the MCP `abi_dump`'s `depth` argument
  are exactly such a promise, and `service_dump_pipeline.run_dump_request`
  enforces it — the same floor, raising the Tier-2 `ValidationError` where the
  CLI path raises `DumpDepthNotSatisfiedError`. Note which direction the fix
  went: the gap was closed by giving the typed surface the *capability* and
  the gate together, not by extending a gate to a surface that had no
  capability to gate. Finding 2 is unchanged and still correct. The original
  text follows.

  This entry previously said PR
  #601 (which adds a hard-fail `DumpDepthNotSatisfiedError` when an explicit
  `dump --depth` isn't actually reached, in `cli.py`/`cli_dump_helpers.py`)
  was still open, and that `abicheck/service.py`'s `ScanRequest`/
  `run_scan_subprocess` and `abicheck/mcp_server.py`'s MCP tools needed the
  same check extended to them once it merged. PR #601 merged 2026-07-19.
  Re-checking what "extend the same check" would actually mean turned up two
  separate findings, both closing this gap rather than giving it new code:
  1. `check_requested_depth_satisfied` (the strict gate PR #601 added) is
     called from exactly one place, `cli._write_snapshot_output` — reached
     only by the `dump` command and one `cli_buildsource.py` snapshot-writing
     helper. Neither `service.py`'s `run_dump`/`resolve_input` nor the
     `abi_dump` MCP tool accept a `depth`/`sources`/`build-info` parameter at
     all (confirmed by reading both) — there is no service.py/MCP surface
     that promises a depth-qualified persisted snapshot for this gate to
     extend to.
  2. The only place a caller *can* pass an explicit `depth=` through
     `service.py`/MCP is `ScanRequest`/`abi_scan`/`abi_estimate` — and
     `service_scan.run_scan`, the CLI `scan` command
     (`cli_scan.py`), and the MCP `abi_scan` tool all call the exact same
     `scan_engine.run_scan_core`, so they already share one evidence-contract
     implementation (`_check_scan_evidence_contract`'s pinned-depth
     `_EvidenceContractError`, ADR-037 D5) — there was never a CLI-vs-API/MCP
     disparity on the `scan` side to close, before or after PR #601.
  `_validate_public_depth`'s docstring in `mcp_server.py` carried the same
  stale "PR #601 open, tracked as remaining work" wording and was corrected
  alongside this entry.

- **Action pinning is deliberately partial, not a full sweep.** Third-party
  GitHub Actions in `.github/workflows/agentready.yml`, `ci.yml` (the
  `id-token: write` jobs), `pages.yml`, `publish.yml`, `security.yml`, and
  `schedule-check-project-failure-path.yml` (its one `dispatch` job) are
  pinned to a full commit SHA (with a `# <tag>` comment) rather than a
  mutable tag/branch — those carry `security-events:write`,
  `pull-requests:write`, `contents:write`, `actions:write`, or
  `id-token:write` (OIDC/PyPI Trusted Publishing), so a re-pointed tag there
  is a real supply-chain risk.
  The root `action.yml` (the composite Action third-party repos consume
  directly) is pinned the same way, for the same reason: its final step
  conditionally runs `github/codeql-action/upload-sarif` under whatever
  `security-events: write` permission the *consuming* workflow grants it, so
  it carries the same blast radius as the elevated-permission workflows
  above even though this repo's own CI doesn't invoke it with that scope.
  Other workflows (`test-action.yml`, `eval-suite.yml`, `performance.yml`,
  `realworld-validation.yml`, `dependency-review.yml`, and any future ones)
  still use tags — deliberately deferred, since they only run with
  `contents: read` and don't touch secrets/publishing/security-event write
  access, so the blast radius of a compromised tag there is far smaller.
  Extend the same pinning to a workflow only when it gains elevated
  permissions, not preemptively.
- **CODEOWNERS risk tiers currently all resolve to one person.** The file is
  structured by risk tier (CRITICAL/HIGH/STANDARD) so a second maintainer
  can be slotted into CRITICAL/HIGH without restructuring, but there is
  only one maintainer today — don't read the tiering as "these are reviewed
  by different people," it isn't, yet.
- **Toolchain-profile compiler-family rendering — audited, `args` trust
  boundary hardened; the `-stdlib=`/`--target=` "fix" itself was wrong and
  has been reverted.** An external audit found `run_plan.py`'s
  `_compose_gcc_options()` composing `-stdlib=`/`--target=` unconditionally
  for any `profiles.<id>.compile` overlay, even when
  `compile.compiler_family: gcc` — both are Clang-driver-only spellings a
  real GCC binary rejects (confirmed against GCC 14.2), so an early pass
  dropped both whenever `compiler_family` resolved to a GCC family name. A
  later review round found that fix backwards: the composed string this
  function returns is **never actually fed to a literal GCC binary
  anywhere in this pipeline** — `--ast-frontend` only has
  `auto`/`castxml`/`clang`/`hybrid` (no `gcc`); castxml's own frontend is
  always its internal bundled Clang (`--castxml-cc-<id>` selects an
  *emulation* mode, not a literal execution path); and the direct-clang
  backend's `_resolve_clang_bin` (`dumper_clang.py`) explicitly rejects a
  `gcc-path` that isn't clang-family and falls back to host
  `clang`/`clang++`. Since the real consumer is always Clang, dropping
  `--target=` actively broke cross-compilation-target correctness for the
  direct-clang backend — it was the *only* signal available there to steer
  parsing away from the host architecture (no "probe the real compiler"
  auto-discovery step exists on that path the way castxml has one), so a
  GCC-family profile with an explicit `target:` would silently have its
  headers parsed for the runner's architecture instead. Reverted:
  `_compose_gcc_options()` emits `-stdlib=`/`--target=` unconditionally
  again, same as before the original audit, with both the change and the
  reasoning for reverting it recorded in the function's own docstring so a
  future reader doesn't rediscover and re-"fix" the same false positive.
  The same original audit flagged a real trust-boundary gap in
  `profiles.<id>.compile.args`, which is unaffected by this revert and
  stays fixed: the existing whitespace-
  smuggling check (`_safe_profile_atom`) rejected one YAML scalar expanding
  into multiple argv tokens, but not a single, whitespace-free dangerous
  atom. `_DANGEROUS_ARG_PREFIXES` (`project_targets.py`) now blocks four
  families of these: direct code-loading flags (`-Xclang`, `-load`,
  `-fplugin=`, `-fpass-plugin=`), file/argv re-expansion (`@response-file`,
  Clang's `--config`/`--config=`), driver command-line substitution
  (`-specs=`/`--specs=`, `-wrapper`), and — added across two follow-up
  review rounds on the same PR, since each is the same underlying
  "opaque subprocess-forwarding" mechanism as the others — GCC's
  `-Wa,`/`-Wp,`/`-Wl,` (comma-joined payload passed straight to the
  assembler/preprocessor/linker; `-Wp,-fplugin=./evil.so` reaches cc1 the
  same as a bare `-fplugin=`, `-Wl,-plugin=./evil.dso` loads an LTO linker
  plugin) and Clang's `-Xpreprocessor`/`-Xassembler`/`-Xlinker`
  (separate-argument equivalent of `-Xclang`). A third review round found a
  deeper issue than another missing flag spelling: every `compile.*` atom
  (not just `args`) now also rejects quote (`'`/`"`) and backslash (`\`)
  characters, since `_compose_gcc_options` space-joins every field into one
  string that `dumper.py`'s `--gcc-options` handling later re-splits with
  `shlex.split()` — an atom like `"'-fplugin=./evil.so'"` starts with a
  quote, not `-fplugin=`, so the prefix denylist alone accepted it, but
  POSIX shlex quote-removal reconstitutes the exact blocked flag on
  re-split (confirmed with an actual `shlex.split()` round-trip). Two more
  review rounds each found a flag real for the mechanism it names but
  empirically NOT exploitable through abicheck's actual pipeline —
  verified rather than taken on faith, and blocked anyway since doing so
  is free: `--castxml-cc-` (a second occurrence naively looks like it
  could replace the trusted `--castxml-cc-<id> <path>` pair
  `dumper_ast_config.py` composes ahead of `args`, but real castxml
  0.6.3 hard-rejects any repeated `--castxml-cc-*` occurrence at
  argv-parse time instead of silently substituting the compiler); and
  `-B<dir>`/`-B <dir>` (GCC's compiler-component search path override
  really does let a planted `cc1`/`cc1plus` run instead of the real one,
  confirmed against real GCC — but every consumer of this composed
  string is Clang, not GCC, and Clang re-execs itself via `-cc1` rather
  than spawning a separate, `-B`-discoverable one; confirmed neither
  castxml's internal bundled Clang nor the direct `--ast-frontend clang`
  backend ran a planted `cc1` with `-B` set). A fifth review round found a
  flag family that IS actually exploitable through this pipeline, unlike
  the two immediately above: clang-cl's (Clang's MSVC-compatible driver
  mode — reachable via a `compile.binding` whose path stem contains
  "clang", e.g. `clang-cl`/`clang-cl.exe`, which
  `dumper_clang._is_clang_family_binary` recognizes as clang-family)
  `/clang:<arg>` escape hatch forwards an argument straight to the
  underlying clang driver, bypassing clang-cl's MSVC-shaped option parsing
  entirely — empirically confirmed exploitable: `clang
  --driver-mode=cl "/clang:-fplugin=./evil.so" -c t.h` really does load and
  run the planted plugin. `/link <options>` (clang-cl's documented
  "forward options to the linker") is blocked alongside it on the same
  LTO-linker-plugin grounds as the already-blocked `-Wl,`, without a
  from-scratch empirical repro of that specific sub-case. A sixth review
  round found a different shape of finding again: `-cc1`/`-cc1as`, Clang's
  internal frontend mode, only activates when `-cc1`/`-cc1as` is literally
  the *first* argument after the program name (confirmed empirically:
  `-cc1` anywhere else is rejected as "unknown argument", including right
  after a leading `-I`) — but `dumper.py`'s `_build_clang_header_command`
  builds argv as `[cc_bin, *-I dirs, --sysroot, -nostdinc, *gcc_options
  tokens, ...]`, so a scan with no `extra_includes`/`sysroot`/`nostdinc`
  lets a leading `-cc1` in `compile.args` genuinely land in that
  first-argument slot. Once in cc1 mode, `-load`/`-fpass-plugin=` were
  already blocked, but cc1 mode exposes an entirely different, much larger
  argument namespace this denylist was never designed to enumerate — Codex
  found `-fcas-plugin-path` (a cc1-only flag not present in every Clang
  build) doing the identical thing. Rejected the mode switch itself rather
  than chasing individual cc1-only flags, the same reasoning as `--config`.
  This denylist is necessarily reactive to the delivery *mechanism*, not exhaustive over
  every dangerous flag a mechanism could carry — a real fix for the
  whack-a-mole shape of this (an allowlist of known-safe ABI flags instead
  of a denylist of known-dangerous ones) was suggested during review but
  deliberately not done here: `args` is documented as a general escape
  hatch for ABI-relevant flags this codebase cannot enumerate a priori
  (GCC/Clang/MSVC each have their own vocabulary), and a strict allowlist
  would need that vocabulary built out first — its own scoped project, not
  a reactive expansion of this fix. (A fourth review round briefly caught a
  correctness gap in a since-reverted sentinel the family-aware
  `_compose_gcc_options()` fix needed — moot now that the fix itself is
  reverted, see above; not detailed here since it no longer applies to any
  code that ships.) Still **not** implemented, and out of
  scope for that fix (each needs its own
  scoped design, not a drive-by extension of the same narrow correction):
  a real toolchain-identity probe that validates a resolved `binding`'s
  actual compiler family/version/target against the profile's declared
  constraints (`compiler_version` is still parsed but never checked against
  anything); a profile-specific AST frontend (there is still only one
  global `--ast-frontend`); and a genuine family-specific argv resolver —
  in particular MSVC `/std:`/`/D` spellings, which this fix does not
  attempt (no `compiler_family: msvc` caller/test exists yet to validate
  against, and a wrong guess here is worse than the pre-existing gap).
- **`ruff format` has never gated anything, and the tree has never been
  formatted — pins fixed, the reformat itself deliberately not attempted.**
  Two separate problems were tangled here. (1) `ruff` was pinned in two places
  that disagreed: `.pre-commit-config.yaml` at `rev: v0.9.0`, and
  `pyproject.toml`'s `[dev]` at `ruff>=0.3` — a floor, so CI and
  `pip install -e ".[dev]"` resolved whatever was newest on the day they ran.
  That is not cosmetic: 0.9.0 reports an `F811` in
  `abicheck/cli_buildsource_helpers.py` that current ruff does not, so a
  contributor's `pre-commit` run and CI reached different *lint* verdicts on
  unmodified code. Fixed by pinning both to the same exact version, forward
  rather than back (pinning to 0.9.0 would red the lint lane on existing
  code), with `tests/test_toolchain_pins.py` asserting the two stay in
  lockstep. (2) Separately — and *not* caused by the version skew — the tree is
  not `ruff format`-clean under **any** version: 488 files under 0.9.0, 486
  under 0.16.3, ~56.5k changed lines, and the diffs are near-identical across
  versions (checked), i.e. the formatter has simply never been applied
  repo-wide. It went unnoticed because the `fmt-check` step, though present in
  `scripts/verify.py`'s `fast`/`pr`/`full` profiles, **is not run by any CI
  job**: `ci.yml`'s `lint-and-types` invokes
  `--profile pr --only lint,typecheck,docs-build`, no workflow runs the full
  `pr` profile, and `pre-commit` is not run in CI at all. So the one consumer
  that would catch it is `pixi run check` (which *does* run the whole `pr`
  profile) — i.e. a pixi contributor's local gate is currently stricter than
  CI. That much was **already known and deliberately tracked**, in
  `tests/test_verify_profiles.py`'s `_PR_STEPS_NOT_IN_A_CI_ONLY_LIST`
  (`"fmt-check": "NOT RUN IN CI — pre-existing gap, tracked here"`); what this
  entry adds is the *measurement* of what enabling it would cost, and the
  finding that the gap is not version skew. **Not fixed here**: making `fmt-check` real requires the ~56.5k-line
  mechanical reformat first, which would drown this change's review and
  conflict with every in-flight branch, so it belongs in its own PR that does
  nothing else. Until then, treat a green CI run as saying nothing about
  formatting.

- **Deferred entirely, not attempted this pass** (heavier structural
  changes, each needing its own scoped design rather than a drive-by
  addition):
  - *Devcontainer image* — a maintained `.devcontainer/` needs a decision on
    which system tools (castxml, libabigail, abi-compliance-checker,
    compilers) ship baked-in vs. installed on first use, and upkeep as those
    pins drift; `pixi` (see CONTRIBUTING.md) already solves the "one command
    gets you a working dev environment" problem this would target, without
    the image-maintenance burden.
  - *Trend-reporting database* — persisting `scripts/check_tier_accuracy.py`
    /`check_fp_rate.py`/mutation-score history across runs (rather than each
    CI run only gating against a static baseline) needs a storage decision
    (artifact-based vs. external DB) and a retention/access policy before
    it's worth building.
  - *Full behavioral baseline* — `agent-evals/` (this pass, M1-5) is a real
    but minimal harness with one task; a "full behavioral baseline" implies
    a broad task suite plus a scoring/leaderboard story, which should grow
    from real usage of the one-task harness rather than being speculatively
    built out now.
- **Findings emitted from absent evidence — `type_vtable_changed` fixed;
  `type_base_changed` carries the identical shape and is not.** A list-valued
  `RecordType` field cannot express "not captured", so an empty-vs-non-empty
  difference conflates a real change with one side's debug info simply not
  covering the declaring TU. Confirmed for `vtable`: identical headers, no
  DWARF vtable, and zero `_ZTV` symbols on either side still produced a
  `BREAKING` `type_vtable_changed`, because `_diff_type_vtable` guarded on
  `t_old.vtable == t_new.vtable` and nothing else. Fixed by requiring an
  independent layout signal (a size change — the vptr a genuinely polymorphic
  class gains — or a virtual-base change) before an empty↔non-empty
  transition is reported; both-sides-captured differences are untouched, and
  an unknown size on either side keeps the finding, since the suppression
  needs positive evidence that layout held still rather than being a fallback
  for missing information. This is the discipline `diff_vtable_layout.py`
  (tri-state `None`, "degrading to B1's L0 view rather than fabricating a
  break") and `diff_elf_layout.py` (compare only a `_ZTV` present on *both*
  sides) already state in their own docstrings; the type-level detector had
  neither. **Two things remain open.** (1) `_diff_type_bases`
  (`set(t_old.bases) != set(t_new.bases)`, and its virtual-base half) has the
  same unguarded shape and **stays that way — an attempted guard was written
  and reverted before merge, and the reason is worth not rediscovering.**
  Every layout-based premise for it is false: an *empty* base is invisible by
  the empty-base optimization, and — the one that killed the attempt — a
  *storage-contributing* base can be added without moving the derived class's
  size at all when the class is over-aligned (verified against g++:
  `struct alignas(8) D {}` and `struct alignas(8) D : B {}` with
  `struct B { int y; }` are both 8 bytes, as are the `alignas(16)` pair at
  16). So "size held still" proves nothing about a base list, in either
  direction. Unlike the vtable case there is no independent evidence stream
  to fall back on — `snapshot.functions` answers "did this class's virtuals
  change" but nothing answers "did this class's bases change" except
  `RecordType.bases` itself. Guarding it therefore needs evidence the model
  does not currently carry (per-finding provenance, or a captured base-layout
  fact such as `base_offsets` corroboration), not a cleverer reading of
  `size_bits`. Until then a fabricated `type_base_changed` from a capture gap
  is the accepted cost, because the alternative — suppressing a real
  hierarchy change, which is sometimes the *only* breaking finding a
  same-size base addition produces — is strictly worse. (2) The vtable guard is **narrower than
  a first reading suggests, and its own docstring used to overclaim it.** The
  class's virtual functions and its `RecordType.vtable` are two projections of
  the same DWARF evidence (both trace to `DW_TAG_subprogram`), not independent
  streams — so a translation unit whose coverage vanishes can take both, the
  two sides' signature sets then differ, and the guard declines to suppress.
  The false positive survives in that shape. That is the failure direction to
  have (it leaves a pre-existing false positive standing rather than hiding a
  real break), but it means the guard covers the *reported* case — identical
  headers, no DWARF vtable, no `_ZTV` anywhere, virtuals absent from both
  sides — and not every capture gap. Closing the rest needs artifact or
  provenance evidence (`_ZTV` presence, per-finding providers) the type-level
  detector does not receive today. (3) One accepted
  false negative in the
  fix itself: a class already polymorphic *through a base*, declaring no
  virtuals of its own, that gains one — its vtable grows while its object
  size does not, making it indistinguishable from capture noise without a
  real polymorphism walk over both sides' base chains
  (`diff_vtable_layout._is_polymorphic`) plus the per-finding provenance the
  entry below describes. (4) A *pure* virtual reaches that same size backstop
  for a different reason: it has no out-of-line definition, so
  `dwarf_snapshot` drops its declaration-only DIE from `snapshot.functions`
  while still counting it as a vtable child of the class, leaving both
  owned-signature sets empty — and with `alignas` absorbing the new vptr the
  size does not move either. Reproduced against g++
  (`struct alignas(8) A { virtual void f() = 0; }` compiled alongside a
  concrete derived class, which is what makes GCC emit A's complete DIE
  rather than a `DW_AT_declaration` stub): old vtable `[]`, new vtable
  `['_ZN1A1fEv']`, both 8 bytes, `_ZN1A1fEv` absent from the function map on
  both sides. **An attempt to close this one was written, merged into the
  branch, and then reverted — the reason is the most useful thing in this
  entry.** The fix consulted `RecordType.vptr_offset_bits` as "the layout
  descriptor's own witness, the only signal here that is not another
  projection of the same subprogram DIEs." That claim was false, and
  checkably so: **both** producers assign the field as `0 if vtable else
  None` (`dwarf_snapshot.py`, `dumper_castxml.py`), so on every real DWARF
  or CastXML snapshot `(old.vptr is None) != (new.vptr is None)` is
  *identical to* the empty↔non-empty vtable transition being guarded. It was
  therefore true by construction for every input that reached it, which did
  not merely weaken the guard — it made the guard **inert**, restoring the
  original capture-gap false positive in full (Codex review). Only the
  optional `ABICHECK_CLANG_LAYOUT_TOOL` path computes a real vptr, and
  nothing in the model distinguishes that value from the derived one, so the
  field cannot be trusted here at all. Reverted; case (4) joins case (3) as
  an accepted false negative. **The tests are the second lesson**: the
  guard's unit tests built `RecordType`s with `vptr_offset_bits` left `None`
  on both sides — a record no backend can emit — so the entire suite,
  including the pre-existing test for the guard's whole purpose, passed
  against a guard that suppressed nothing on real input. The helper now
  derives the field the way the producers do, one test pins that derivation
  in the producers' own source (so the reasoning fails loudly if a producer
  ever computes a real vptr), and five of these tests fail against the
  reverted revision. Worth recording for both (3) and (4) that the break is
  **not** hidden: `diff_layout._check_vptr_introduced` fires independently
  on the same `None → 0` transition and the verdict stays `BREAKING`
  (verified end to end, and now pinned by its own test) — which is also why
  the FP-rate corpus, a verdict-level gate, could not catch either the
  original false positive or the inert-guard regression, and why the
  regression coverage is a direct unit test on the predicate
  (`tests/test_vtable_evidence_guard.py`) instead. Leaning on a sibling
  detector remains uncomfortable, but the alternative on offer was a witness
  that only appeared independent. Guarded by four FP-corpus cases under the
  `evidence-absence` axis (one FP guard, three FN sentinels), the unit tests
  above, and three Hypothesis properties in
  `tests/test_detector_properties.py`.

  **A named follow-on, found by a user rather than by any gate in this
  repo, and worth being explicit about why: two detectors reading the
  identical evidence gap and reaching two different severities is its own
  bug class, distinct from either detector being individually wrong.**
  `LAYOUT_UNVERIFIABLE` (`diff_layout.py`) answers "was there real layout
  evidence for this type" from the exact same asymmetric-evidence condition
  `_vtable_transition_is_evidenced` above declines to suppress on. Both
  answers are individually correct and individually documented (this entry
  is the vtable side's own justification) — but a type carrying both at
  once reads as a hard BREAKING verdict sitting next to a finding that
  already says the evidence for that type couldn't be verified either way,
  reproducible with zero real ABI change (scanning a binary against a dump
  of itself; reported against real oneDAL binaries, three types, two
  libraries). None of this repo's existing quality gates were positioned to
  catch it: the FP-rate/tier-accuracy gates are verdict-level (the overall
  verdict was already correctly `BREAKING`, dominated by the vtable
  finding, so nothing there looked wrong), and every existing test —
  including the Hypothesis properties this entry already describes — is
  scoped to one detector's own correctness in isolation, never to whether
  two detectors' outputs are mutually *legible* when they land on the same
  subject.

  **The fix went through four designs before landing, and the last pivot is
  the one worth reading closely — it generalizes past this one pair of
  detectors.** (1) Demoting `TYPE_VTABLE_CHANGED` was tried first and
  rejected immediately: a real last-virtual-method removal with a new-side
  capture gap is indistinguishable from this same evidence gap, so
  softening its severity risks a false negative on a genuine break. (2)
  Folding the now-redundant `LAYOUT_UNVERIFIABLE` finding into
  `redundant_changes` unconditionally was tried next, and review found four
  real defects in it in turn: correlating on bare `Change.symbol` (two
  distinct same-named records in different namespaces can share it, so
  fold on `qualified_name`); folding on mere co-occurrence rather than a
  marker recording *why* the stronger finding was kept (an
  independently-evidenced `TYPE_VTABLE_CHANGED` — a real reorder, a real
  size delta, a real virtual-base change — must never trigger the fold);
  reusing `Change.modulation_reason`/`modulation_rule` for internal
  signaling (those are a public, report-facing audit trail for an actual
  verdict override — tagging an unmodulated finding with them is a false
  audit entry a downstream consumer would read as real); and running the
  fold *before* `FilterNonPublicSurface`/`ApplySuppression` had settled
  both findings, which both hid the redundant finding from a suppression
  rule targeting it directly and let it outlive its covering finding's
  later exclusion. (3) Fixing all four and gating the fold on a
  policy-resolved-verdict subsumption check (`checker.
  _vtable_gap_finding_is_verdict_subsumed`) closed the `PolicyFile`-override
  case, but review found the fix was still solving the wrong layer: **the
  severity-scheme axis (`severity.SeverityConfig` — `abi_breaking`/
  `potential_breaking`/etc., each independently `error`/`warning`/`info`)
  is chosen by the CLI/reporter caller entirely *after* `compare()`
  returns, so no compare()-time decision to remove a finding from
  `changes` can ever be correct for it.** Concretely: a caller configuring
  `abi_breaking=info` / `potential_breaking=error` wants
  `LAYOUT_UNVERIFIABLE`'s error-level contribution to reach
  `severity.compute_exit_code`, but that function reads `result.changes`
  directly — if the fold already removed the finding because the
  *policy* axis (a completely orthogonal gate, resolved before
  `compare()` returns) ranked the covering `TYPE_VTABLE_CHANGED` as more
  severe, the severity axis never gets a chance to see it, and this
  reproduces with **zero policy override involved** (Codex review, fresh
  evidence). The same review round also found the fold's pipeline
  ordering relative to `DemoteUnreachableInternalChurn` (which runs later,
  to demote a confirmed-unreachable internal-namespace type) could orphan
  an already-made fold decision when the covering finding was itself
  demoted afterward. (4) **The only structurally-safe fix was to stop
  removing information from `changes` for this reason at all.**
  `post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged`
  leaves both findings exactly where they always were and only sets the
  already-existing, generic `Change.correlated_change_kind` field (reused
  from its original ADR-041 purpose, not a new one) on the redundant
  `LAYOUT_UNVERIFIABLE` finding, pointing at `"type_vtable_changed"`.
  `Change.vtable_covers_unverifiable_layout_gap` (set in
  `diff_types._diff_type_vtable`) still records which `TYPE_VTABLE_CHANGED`
  findings rest purely on the ambiguous evidence gap versus real,
  independent evidence — that detection logic is exactly what survived all
  four designs unchanged; only the *action* taken on it changed. This
  design is immune by construction to every one of the defects above and
  to any future one shaped like them, because nothing is ever hidden from
  a consumer that reads `changes` — there is no "was this the right time
  to remove it" question left to get wrong. **The general principle this
  leaves behind: never remove a finding from a shared, multiply-consumed
  list (`DiffResult.changes`) to resolve what is fundamentally a
  *presentation* problem (two findings reading as contradictory) when that
  list has independent downstream consumers — a legacy verdict, a
  policy-file override, a severity-scheme exit code, a future pipeline
  step — that this fix cannot fully enumerate and whose configuration is
  chosen after the removal decision was made. Annotate; never remove.**
  Regression coverage: `tests/test_vtable_severity.py`'s
  `TestLayoutUnverifiableCorrelatedWithVtableChanged` (hand-picked example
  cases, including one exercising the severity-axis gap directly via
  `severity.compute_exit_code`) and a generalized Hypothesis property,
  `test_layout_unverifiable_always_correlated_when_vtable_change_shares_its_gap`
  in `tests/test_detector_properties.py`, checking the annotation holds
  over arbitrarily generated classes and evidence-descriptor shapes.

  **A whole-class structural fix was attempted mid-way through the fold
  design (2) above, for the field-ordering half of it, and reverted — the
  in-repo audit that justified it was the wrong audit.** (This sub-entry
  predates the pivot to design (4) and is kept because the lesson is
  independent of which design won.) The field-ordering bug (found twice
  now: first on `AbiSnapshot` in PR #582, again on the new `Change` field
  this fix originally added mid-list) was first "fixed" by making `Change`
  keyword-only from `old_value` onward via the `dataclasses.KW_ONLY`
  sentinel, on the strength of an AST-parse of every `Change(...)` call
  site in this repo confirming every positional call anywhere passes
  exactly the three required leading fields and never more. That audit
  answers the wrong question for a type this codebase itself documents as
  public API (`checker_types.py`'s own module docstring; CLAUDE.md:
  "changing their public surface is a breaking change to the Python API —
  coordinate it"): it proves this repo's own call sites are safe, and says
  nothing about an external consumer who previously called `Change(kind,
  symbol, description, old_value, new_value, ...)` positionally — for whom
  the whole-class sentinel is exactly the same breaking shift the fix
  exists to prevent, just moved to a boundary this repo cannot see or test
  (Codex review, fresh evidence). Reverted in favor of the same per-field
  `field(kw_only=True)` PR #582 already used for `AbiSnapshot`, applied
  only to the one new field (`vtable_covers_unverifiable_layout_gap`) and
  appended at the true end of the dataclass (not mid-list) so every
  pre-existing field's position — and therefore every pre-existing
  positional caller's behavior, in or out of this repo — is completely
  unchanged. The lesson generalizes beyond this one field: for a type
  documented as public API, "no in-repo caller breaks" is not the bar:
  prefer the narrowest change that cannot break a caller this repo cannot
  see, even when a broader structural fix looks more complete. The
  `KW_ONLY` sentinel is still the right tool for a genuinely *internal*
  dataclass with no external-construction contract (nothing here argues
  against it in general) — the mistake was applying it to one that has
  such a contract without checking for one first.

- **Evidence-provider model — investigated, found not to reproduce as
  described; no fix applied.** A status-review follow-up asked whether
  `evidence_status_for_result`'s report-level downgrade (kind-level
  `ARTIFACT_PROVEN` → `UNATTRIBUTED` only when `DiffResult.evidence_tiers`
  is header-only for the *whole* comparison) can let an individual
  header-derived `BREAKING_KINDS` finding read as artifact-proven merely
  because *some other, unrelated* part of the same report had binary
  evidence. Traced this for the highest-stakes family it could apply to —
  layout findings (`TYPE_SIZE_CHANGED`/`TYPE_ALIGNMENT_CHANGED`,
  `diff_types.py`) — and it does not hold up: (1) the direct-clang L2
  backend's `RecordType.size_bits`/`alignment_bits` are populated **only**
  when `dumper_layout_backfill.backfill_dwarf_layout()` actually
  corroborates them against real DWARF (`model.py`'s own
  `dwarf_layout_coherence` docstring) — with no DWARF to backfill against,
  those fields stay `None` and `_append_type_size_and_alignment_changes`'s
  own `is not None` guard means no finding is even emitted, so an
  "unconfirmed clang-derived layout finding" cannot occur; (2) the castxml
  backend computes struct layout itself, via its own bundled real compiler
  targeting the resolved ABI — `model.py` already documents this as
  deliberately treated as sufficient L2 evidence ("trivially self-consistent
  by construction", not needing DWARF corroboration), a prior, intentional
  design decision this pass would have to *overturn*, not merely patch.
  The one place this class of risk is genuinely live is exactly the
  already-tracked toolchain-identity-probe gap above (castxml/clang invoked
  with compiler/ABI flags that don't match the real build) — not a separate
  evidence-status bug. A **real** per-finding provider model (recording,
  per `Change`, which of L0–L5 actually produced/corroborated it) would
  need new provenance plumbing through all ~45 `Change(...)` construction
  sites across `diff_*.py`/`buildsource/*.py`, each individually verified
  against the FP-rate/mutation-score gates — a multi-day project on its
  own, not attempted here.
- **Type reachability (direct vs. transitive stdlib references) — computed
  and wired into `diff_types.py`'s RecordType-based detectors; enum/typedef
  paths remain unwired.** `abicheck/type_reachability.py`
  (`directly_referenced_stdlib_types()`) computes, from a snapshot alone,
  which `std::`/`__gnu_cxx::`/etc. record types are directly referenced by
  a non-stdlib function's signature or a non-stdlib type's own field — as
  opposed to only reachable via deep template-instantiation internals
  (`std::string::_Alloc_hider`, `std::_Rb_tree_node_base`) that
  `is_non_abi_surface_type`'s existing whole-name-prefix filter already
  correctly excludes as toolchain-artifact churn either way. A Codex review
  round found and fixed a real correctness gap in the computational claim:
  candidate identification originally matched only `RecordType.name`, but
  castxml/direct-clang populate the bare leaf there and the
  namespace-qualified spelling separately in `qualified_name` (`model.py`,
  `dumper_clang.py`) — so `name` alone never carries a `std::` prefix for
  those two backends and the helper silently found nothing on any real
  castxml/clang-produced snapshot. Fixed by identifying candidates via
  `qualified_name or name`. That fix alone was still insufficient, confirmed
  by dumping a real compiled `std::vector<int>` parameter end to end:
  `Function.return_type`/`Param.type` spell the outer type **bare**
  (`"vector<int, std::allocator<int> >"`) even when the matching
  `RecordType`'s identity is fully qualified
  (`"std::vector<int, std::allocator<int> >"`), across *all three* backends
  (DWARF bakes the qualified form straight into `name` with no separate
  field; castxml/clang keep `name` bare and `qualified_name` separate) — so
  a pure full-identity substring match still couldn't connect the two.
  Fixed by also generating a namespace-prefix-stripped spelling per
  candidate and matching against either form. **Since resolved** (a later
  pass, user-requested): a signature spelled with a typedef alias
  (`std::string`, `std::wstring`, ...) names the alias, not the real
  underlying class (`std::basic_string<char, ...>`) that owns the
  `RecordType` entry — no current model field maps one back to the other
  directly, but `snapshot.typedefs` does carry the alias → target mapping.
  Verified empirically against a real DWARF-dumped `std::string`
  parameter: `snapshot.typedefs["std::string"]` resolves to the bare
  `"basic_string<char, std::char_traits<char>, std::allocator<char> >"`,
  while the owning `RecordType.name` is the fully-qualified
  `"std::__cxx11::basic_string<char, std::char_traits<char>,
  std::allocator<char> >"` — libstdc++ wraps its own post-C++11 dual-ABI
  types in an inline namespace (`__cxx11::`) the exact same way libc++
  wraps its whole standard library (`__1::`/`__ndk1::`, already handled),
  so that inline-namespace-stripping list gained a third entry. The
  typedef *key* itself needed the identical bare-vs-qualified treatment
  already applied to `RecordType` identities (the DWARF backend spells the
  signature with the bare form `"string"`, never the qualified typedef key
  `"std::string"`), so `_typedef_spelling_targets()` builds a
  `spelling -> target` index covering both the literal key and its
  namespace-stripped bare form (dropped instead of recorded when
  ambiguous, same false-negative-over-false-positive principle as the
  `RecordType` spelling index), and `_scan()` now follows a matched
  typedef alias to its target the same way `surface.py`'s own
  reachability closure does. What this does *not* cover: a stdlib alias
  the producing backend never emitted into `snapshot.typedefs` at all (no
  empirical case of this found across the three backends so far, but
  nothing guarantees one couldn't exist) — that residual case degrades
  silently back to "not directly referenced," the same conservative
  false-negative default this whole module already uses throughout.

  **Two more real gaps found and fixed in the same pass** (Codex review,
  fresh evidence): (1) the non-stdlib bare-alias fallback derived a
  record's unqualified spelling via `identity.rsplit("::", 1)`, which
  splits inside a *template argument's own* qualified name rather than at
  the outer namespace boundary — for `"api::Wrapper<dep::Tag>"`, the
  lexically last `"::"` belongs to the template argument `dep::Tag`, not
  the outer namespace path, so the old code derived the corrupted bare
  form `"Tag>"` instead of `"Wrapper<dep::Tag>"`, and a real dumper
  backend's bare signature spelling for that wrapper then never matched
  anything. Fixed with a new `_bare_type_name()` that tracks `<`/`>`
  nesting depth and only treats a `"::"` at depth zero as a namespace
  separator. (2) stdlib and non-stdlib spellings were matched via one
  *combined* compiled pattern in a single non-overlapping `finditer()`
  pass — when a non-stdlib record's own identity embeds a stdlib type's
  spelling verbatim (e.g. a template instantiation `"Wrapper<std::string>"`
  registered as its own record identity), and a public signature names
  that wrapper's full identity exactly, the combined pattern's
  longest-first alternation matches the whole wrapper span first,
  consuming it — since regex matches never overlap, the nested
  `"std::string"` substring inside that same span was never independently
  found, even though it is directly present in the public signature text.
  Fixed by splitting `_spelling_index()` into two independent indices
  (stdlib vs. non-stdlib/record) with two independently compiled patterns
  scanned separately over each declaration, so one pattern's match can
  never mask the other's.

  **A third real gap found in the same pass** (Codex review, fresh
  evidence): both the stdlib-stripping collision guard (in
  `_spelling_index`) and the typedef-key stripping collision guard (in
  `_typedef_spelling_targets`) checked a stripped spelling only against
  *full* non-stdlib record identities, not against the bare
  (namespace-unqualified) alias a real backend actually spells that record
  with. A non-stdlib record like `api::vector<int>` is spelled bare as
  `"vector<int>"` — the same bare spelling `std::vector<int>` reduces to
  after namespace-stripping — so a signature naming the unrelated user
  type by its bare spelling incorrectly marked the real `std::vector<int>`
  as directly referenced too; the identical gap existed one level up for
  `api::string`/`"std::string"`'s typedef key. Fixed with a new
  `_non_stdlib_signature_spellings()` helper (full identity plus bare
  alias — deliberately keeping an ambiguous bare alias that
  `_spelling_index`'s own `record_index` drops, since it's still a real
  spelling *some* non-stdlib record can be named by) shared by both
  collision guards.

  **A fourth finding pointed one level deeper, into shared infrastructure
  this module calls rather than into `type_reachability.py` itself**
  (Codex review, fresh evidence): `diff_cxx_rules.owner_class_of()` — the
  helper this module's owner-class seeding reuses, also used by
  `diff_symbols.py`'s owner-based move detection, `diff_cxx_rules.py`'s
  own member-move heuristics, and `surface.py`'s reachability closure —
  mis-parses a public conversion operator's owner when the operator's own
  target type is namespace-qualified. Confirmed against a real compiled
  and demangled symbol: `struct Foo { operator ns::Bar() const; };`
  demangles to `"Foo::operator ns::Bar() const"`, and abicheck's own
  `Function.name` (after its existing signature-stripping step) is exactly
  `"Foo::operator ns::Bar"`. The old naive `rsplit("::", 1)` split at the
  *lexically last* `"::"` — which belongs to the operator's own qualified
  target (`ns::Bar`), not the owner/member boundary — producing the
  corrupted owner `"Foo::operator ns"` instead of `"Foo"`, so a public
  conversion operator to a qualified type would never seed its owner
  class, potentially hiding a genuine layout break in one of the owner's
  fields. Fixed in `owner_class_of()` itself (not duplicated locally) by
  locating the literal `"::operator "` marker — present only for a
  conversion-to-named-type operator, never for a symbol operator like
  `operator+`/`operator[]`, which has no target type to separate from the
  keyword with a space — and splitting there when present, falling back to
  the previous behavior otherwise. Fixing the shared helper directly
  (rather than working around it only in `type_reachability.py`) also
  corrects the same latent mis-parse for its other three callers, since
  none of them could have been relying on the old behavior's output for
  this input shape without already being wrong.

  **A fifth finding, on the same owner-seeding feature, investigated and
  deliberately not implemented this pass:** a public method whose dumper
  backend recorded only a bare member name (CastXML's convention — "the
  bare `bar` rather than `C::bar`", per `owner_class_of()`'s own
  docstring) on a class-template specialization falls through to
  `owner_class_of()`'s mangled-name fallback
  (`itanium_scope_components`), which — confirmed empirically
  (`itanium_scope_components("_ZN3FooIiE3barEv")` returns
  `["FooIiE", "bar"]`) — deliberately keeps the **raw, undemangled**
  Itanium template-argument encoding (`"FooIiE"`) rather than the spelled
  form (`"Foo<int>"`) a real `RecordType` identity actually uses; that
  design choice is itself intentional and documented in
  `itanium_scope_components`'s own docstring ("the raw template-argument
  encoding is kept so distinct specializations stay distinct"), since its
  other callers use it for grouping/distinguishing specializations, not
  for matching against demangled model spellings. `type_reachability.py`'s
  owner-seeding then feeds this raw string into `_scan()`, which correctly
  finds no match (a silent false negative — the same
  false-negative-over-false-positive default this whole module already
  uses throughout, not a new failure mode). A real fix has two paths, both
  rejected as out of scope for a drive-by extension here: (1) making
  `owner_class_of()` itself resolve raw template encodings to spelled
  form would mean invoking the real demangler (`demangle.py`'s
  `demangle()`, which shells out to `c++filt`/`cxxfilt` on a cache miss)
  from a hot path every one of its four callers shares, directly
  contradicting `itanium_scope_components`'s own stated design rationale
  ("avoids any dependency on an external demangler ... so this works
  identically on Linux, macOS, and Windows and never shells out"); (2) a
  narrower, local-only translation in `type_reachability.py` (demangle
  just `fn.mangled` when `owner_class_of()` took the mangled-fallback
  path, then re-derive the owner from the *demangled* qualified name)
  would need a genuinely new depth-aware "class::member" boundary splitter
  for demangled text — not a reuse of `_bare_type_name` (which strips a
  *leading* namespace qualifier, the opposite half of this problem) — and
  would have to correctly compose with the already-fragile
  `"::operator "` marker special-case from the fourth finding above (a
  demangled conversion operator on a qualified template specialization
  could combine both edge cases at once), which is exactly the kind of
  compounding-edge-case complexity this file's own docstring already
  flags as needing "its own scoped follow-up," not a reactive patch.

  **Two more real gaps found and fixed in the same pass** (Codex review,
  fresh evidence): (1) A real backend does not always spell a nested type
  as either the fully-qualified identity or the fully-bare leaf —
  confirmed empirically via `clang -ast-dump` on `namespace api { struct
  Outer { struct Inner {}; }; Outer::Inner g(); }`: direct-clang prints
  the return type as exactly `"Outer::Inner"`, dropping the enclosing
  namespace (`api::`) while keeping the class-nesting qualifier
  (`Outer::`). Neither the full-identity match nor the single
  fully-bare-leaf match (`_bare_type_name`) covered this partial
  qualification. Generalized `_bare_type_name` into
  `_namespace_suffix_spellings()`, returning every suffix obtainable by
  dropping some prefix of the scope chain at each depth-zero `"::"`
  boundary, and updated all three call sites to register every suffix
  (same ambiguity-drop collision guard extended to each). (2)
  CastXML/direct-clang record a function or namespace-scope variable's
  own display name bare (e.g. `"touch"`, never
  `"__gnu_cxx::Node::touch"` or `"std::touch"`), so the existing
  `name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)` guard cannot catch a
  retained, seemingly-public declaration that is actually part of the
  standard library itself — verified with two real Itanium
  mangled-symbol repros (a namespace-scope stdlib variable and a stdlib
  free function) that both incorrectly marked `std::string` as directly
  referenced before the fix. Fixed by also checking the declaration's
  recovered qualified name (`diff_cxx_rules.itanium_qualified_name`, from
  `mangled`) against the stdlib prefixes for both functions and
  variables — which subsumes the narrower owner-only check from the
  fourth finding above (a stdlib-prefixed owner always makes the full
  qualified name stdlib-prefixed too, but not vice versa: a stdlib
  namespace's own direct free function/variable is a single mangled
  scope component, so `owner_class_of` returns a bare `"std"` with no
  trailing `"::"`, never matching the `"std::"` prefix string), so the
  now-redundant owner-only guard was removed.

  **A sixth finding found a different shape of gap again: an owner-seeding
  correctness bug, not a missing-spelling one.** `owner_class_of()`
  derives its result by chopping the trailing `"::"`-component off *any*
  already-qualified declaration name or mangled-symbol scope chain, with
  no way to tell — from the string alone — whether what remains is really
  an enclosing *class* or just an enclosing *namespace* (Codex review,
  fresh evidence, confirmed with a minimal repro): a public namespace
  function `api::run()` makes `owner_class_of` return the bare namespace
  fragment `"api"`, which the general suffix-matching mechanism
  (`_namespace_suffix_spellings`, added for the first finding above) could
  then coincidentally match against an unrelated internal record's own
  bare-suffix spelling (e.g. `other::api`), wrongly walking that record's
  fields and unfiltering its layout churn. Fixed by seeding an owner only
  on an *exact* match against a non-stdlib record's full identity —
  bypassing `_spelling_index`'s `record_index`/suffix mechanism entirely
  for this specific seed, rather than routing it through `_scan()`. This
  is safe rather than a regression risk: unlike a genuine signature type
  spelling (which a backend can legitimately partially-qualify, per the
  first finding), `owner_class_of`'s result is always either the complete,
  exact scope chain of a real class (both its already-qualified-name path
  and its mangled-decomposition fallback reconstruct the *full* chain,
  never a partially-elided one — DWARF always bakes the complete
  namespace/class path into a qualified name, and Itanium mangling always
  encodes the complete nested-name unambiguously) or, when the function
  isn't actually a method, namespace noise — so restricting to exact
  matching loses no real case while closing the false-positive collision.
  While verifying this fix through the full `compare()` pipeline (not just
  the unit level), the same class of bug was found to independently exist
  in `surface.py`'s `compute_public_surface()` — its own, separate
  `owner_class_of`-based seeding (`_seed_public_roots`) feeds the raw
  owner through `_type_identifiers()` into `seed_types`, and
  `_walk_type_closure()`'s `record_by_name` lookup is *itself* keyed by
  bare-tail aliases (an intentional, correct mechanism for genuine type
  references — "a short alias reached inside its own namespace resolves
  to the namespaced record"), so the identical `"api"` vs. `other::api`
  collision reproduces there too, confirmed with the same minimal repro
  (`compute_public_surface` marks `"api"` — and therefore `other::api` —
  public). **Deliberately not fixed in this pass**: `surface.py` is a
  different, foundational module (the public-surface-scoping gate every
  other detector in the codebase depends on) that this PR never otherwise
  touches, and unlike the narrow `type_reachability.py` seeding path, its
  `record_by_name` bare-tail lookup is a *shared* mechanism relied on by
  every other seed type too — restricting it for the owner case
  specifically needs its own careful, independently-verified design (which
  seed paths may legitimately need the ambiguous-tail lookup and which
  must not), not a same-PR drive-by extension of an unrelated finding.

  **Two more ambiguity-tracking gaps found in the same collision guards**
  (Codex review, fresh evidence, both confirmed with minimal repros): (1)
  when two non-stdlib records had identities `"Inner"` and `"api::Inner"`,
  `_spelling_index`'s derived-suffix collection only counted contributors
  to the *derived* suffix `"Inner"` (from `"api::Inner"`) — the unrelated
  global `"Inner"` identity never contributes to that same tracking
  structure (it's already a full identity, not a derived suffix), so the
  ambiguity count saw only one contributor and merged `"api::Inner"`
  straight into the pre-existing full-identity entry for the global
  `"Inner"`. Fixed by also treating a derived suffix that collides with a
  *different* record's own full identity as ambiguous. (2)
  `_typedef_spelling_targets` gave an *exact* pre-existing typedef key
  automatic priority over a derived suffix from a different key, rather
  than tracking both through the same ambiguity-counting structure: when
  `snapshot.typedefs` held both a global `"Alias" -> "std::…"` and a
  qualified `"api::Alias" -> "Foo"`, a declaration inside `api` can
  legitimately spell the latter as bare `"Alias"` too — silently
  preferring the pre-existing exact key could resolve it to the wrong
  one. Fixed by unifying exact keys and derived suffixes into one
  target-set-per-spelling structure, resolving a spelling only when every
  contributing source agrees on exactly one target.

  **A follow-up review round on the same fix found the removal above was
  necessary but not sufficient.** Refusing to *merge* `"api::Inner"`'s
  candidates into the pre-existing `record_index["Inner"]` entry still
  left that entry pointing at the unrelated global `"Inner"` record
  (Codex review, fresh evidence, confirmed with a minimal repro):
  direct-clang's own "drop the enclosing namespace" convention (the same
  mechanism `_namespace_suffix_spellings` models for the `Outer::Inner`
  finding above) means a signature declared *inside* namespace `api` can
  spell `api::Inner` bare as `"Inner"` too — not just a partially-qualified
  form. A public `api::f()` returning (bare-spelled) `api::Inner` would
  then have its `std::` field misattributed to the *unrelated* global
  `Inner`'s own field instead of correctly failing to resolve. Fixed by
  removing the colliding spelling from `record_index` entirely
  (`record_index.pop(bare, None)`) rather than merely refusing to add the
  other record's candidates to it — since the bare spelling is genuinely
  ambiguous between both records, leaving it resolved to either one
  (including the "already there by default" one) is the wrong outcome,
  not just an incomplete fix.

  **A separate, deeper finding on typedef keys, investigated and
  deliberately not implemented this pass:** direct-clang's own
  `parse_typedefs()` (`dumper_clang.py`) stores a typedef's bare
  `node["name"]` as the `snapshot.typedefs` key — never the scope-joined
  qualified form `_qualified()` uses for every other decl kind — so a
  namespaced alias loses its namespace at the point the snapshot is
  produced, not merely at the point this module reads it. Confirmed
  empirically via a real `clang -ast-dump` on `namespace api { struct Foo
  {}; using Alias = Foo; } api::Alias make();`: the `TypeAliasDecl`'s own
  name is bare `Alias`, while the function's return type is printed fully
  qualified `"api::Alias"` (a typedef reference is always spelled
  qualified by clang's printer, unlike a plain class reference) — meaning
  `snapshot.typedefs` ends up with `{"Alias": "Foo"}` while the real
  signature spells `"api::Alias"`, the exact inverse of the
  qualified-key/bare-signature shape `_typedef_spelling_targets` was built
  to handle. Since suffix-stripping only ever produces a *shorter*
  candidate from a key, it can never reconstruct a *longer*, more-qualified
  spelling from an already-bare key — there is no string-level fix
  possible in this module for this direction, only two heavier ones, both
  out of scope for a drive-by extension here: (1) fixing
  `dumper_clang.py`'s `parse_typedefs()` to store the qualified key
  instead — a genuine, separate producer-side bug, but one whose blast
  radius reaches every other consumer of `snapshot.typedefs` (typedef
  diffing, `surface.py`'s own typedef-following in `_walk_type_closure`),
  each needing its own re-verification against the FP-rate/mutation-score
  gates before trusting a changed key shape; (2) a local reverse-namespace
  guesser in this module (re-attaching every namespace prefix seen among
  the snapshot's own record identities to a bare typedef key and hoping
  one matches) — pure speculation with no way to verify which, if any,
  namespace a given bare key actually belongs to, and a real risk of
  fabricating new false-positive matches rather than closing a
  false-negative gap. Left as a silent false negative — the same
  conservative default this module already uses throughout.

  **A seventh finding pointed at a platform-specific mangled-name quirk,
  silently disabling the mangled-scope-recovery guard on every Mach-O
  snapshot.** Confirmed via `dumper_clang.py`'s own `_visibility()`
  docstring: clang's `mangledName` carries an extra platform leading
  underscore on macOS (`"__ZN3lib3addEii"`, not the plain Itanium
  `"_ZN3lib3addEii"`), and empirically: `itanium_scope_components(
  "__ZSt5touchv")` returned `None` before this fix, since
  `_itanium_strip_prefix()` only recognized the bare `"_Z"` prefix
  (Codex review, fresh evidence). Since every declaration's stdlib-scope
  check in this module (and `owner_class_of()`'s mangled fallback) relies
  on this recovery, a bare-named stdlib declaration on macOS bypassed the
  guard *entirely* — not just in the one edge case a synthetic unit test
  would reach, but for every symbol on that platform. Fixed in the shared
  `diff_cxx_rules.py` parser (benefiting all four of its callers, not
  just this module) by stripping the extra leading underscore before the
  Itanium-prefix check, mirroring `dumper_clang.py`'s own
  `_symbol_candidates()` de-prefixing approach for the identical quirk.

  **An eighth finding pointed at a different mangling scheme entirely, not
  a variant of the same Itanium quirk.** A `clang-cl` (or any
  `--target=*-windows-msvc`) direct-clang snapshot records a method's bare
  AST name — the same unqualified-leaf convention CastXML uses — while
  `mangledName` is mangled in the proprietary Microsoft C++ ABI scheme, not
  Itanium (Codex review, fresh evidence). `owner_class_of()`'s mangled-name
  fallback only ever recognized the Itanium `_Z`/`__Z` prefix, so this
  owner seed stayed `None` on every MSVC-mangled bare-named method,
  regardless of the Mach-O fix above (a different, unrelated prefix
  convention, not fixed by it). Confirmed empirically by compiling real
  headers with `clang --target=x86_64-pc-windows-msvc -fms-compatibility
  -Xclang -ast-dump=json`: `Foo::run()` mangles to `?run@Foo@@QEAAXXZ`
  (scope components written *innermost first*, `@`-separated, terminated
  by the first `@@` — the reverse order and terminator convention Itanium
  uses, confirmed against nested-namespace, single-letter-class-name, and
  global-free-function cases too). Fixed with a new, genuinely separate
  `msvc_scope_components()`/`msvc_qualified_name()` pair in
  `diff_cxx_rules.py` (not a branch inside the Itanium parser, since the
  two schemes share no structure beyond both being length/separator-based),
  tried as a second fallback in `owner_class_of()` after Itanium — the two
  prefixes (`_Z`/`__Z` vs. `?`) are mutually exclusive, so trying both in
  sequence is unambiguous and free on the common Itanium path. Deliberately
  conservative, mirroring `itanium_scope_components`'s own "model the
  simple cases, return `None` for the rest" contract, confirmed unmodelled
  against the same real compiler output: special member functions and
  operators (`??0` ctor, `??1`/`??_D` dtor, `??4` `operator=`, ...) mangle
  with a *second* `?` immediately after the first, so the leaf/scope split
  does not apply and is rejected outright; template classes/functions
  (`?$Name@Args@`) embed the template-argument encoding inside the same
  `@`-delimited region as the scope chain, and an argument token is
  indistinguishable from a scope token by simple splitting, so any
  component starting with `?` (the template marker `?$` or the anonymous-
  namespace marker `?A`) is rejected; a bare-digit component is a
  name-backreference into MSVC's per-symbol substitution table, not a
  literal identifier (no real C++ identifier is all-digits, so this is an
  unambiguous, lossless signal to bail — verified this does *not*
  misfire on a genuine single-letter class name like `struct A`, which
  mangles as a component that is a letter, never a bare digit). Also wired
  the same new fallback into `type_reachability.py`'s two direct
  `itanium_qualified_name()` call sites (the free-function/variable
  stdlib-namespace guards, not just the owner-seeding path the review
  comment named) — same root cause, same one-line fix, verified against a
  `std::`-namespaced MSVC-mangled free function that would otherwise have
  bypassed the guard identically to the Mach-O case above.

  **A ninth finding pointed at an asymmetry in the typedef-spelling
  ambiguity guard, not a mangling gap.** `_typedef_spelling_targets()`
  registers every *derived* candidate spelling (a stdlib-stripped or
  namespace-suffix form of a typedef key) only after checking it against
  `_non_stdlib_signature_spellings()` — but the typedef's own *exact* key
  was registered unconditionally, with no equivalent guard (Codex review,
  fresh evidence). The already-documented direct-clang typedef-scope-loss
  gap above (`parse_typedefs()` storing only the bare `node["name"]`) means
  an exact key like `"Alias"` can itself collide with an unrelated
  non-stdlib record's own bare signature spelling — e.g. a global `struct
  Alias {};` sharing the same name as a namespaced `namespace api { using
  Alias = std::string; }` whose `api::` the producer already dropped.
  Confirmed empirically: `directly_referenced_stdlib_types()` incorrectly
  returned `{"std::string"}` for a public function taking the unrelated
  `Alias` record by value, purely because of the same-named, unrelated
  typedef. Fixed by applying the identical `non_stdlib_spellings` guard to
  the exact-key registration, matching how a colliding derived candidate is
  already skipped — the spelling belongs to the real record, so the
  typedef contributes nothing for it, rather than competing through the
  ambiguity-resolution machinery.

  **A tenth finding closed the conversion-operator half of the owner-
  seeding gap the earlier `"::operator "`-marker fix only partly covered.**
  That earlier fix handled a *display-name* conversion operator whose own
  qualification embeds `"::"` (e.g. DWARF's `"Foo::operator ns::Bar"`), but
  a direct-clang snapshot stores a conversion operator's AST name bare —
  `"operator Bar"`, no owning-class prefix at all, confirmed via a real
  `clang -ast-dump` — so `owner_class_of()`'s display-name branch never
  applies (there is no `"::"` to find), and it falls through to the
  mangled-name fallback (Codex review, fresh evidence). That fallback had
  no coverage for conversion operators either:
  `itanium_scope_components()`'s underlying component parser deliberately
  excludes the Itanium `cv` (conversion-to-*T*) code from
  `_ITANIUM_OPERATORS` — correctly, for that set's own purpose of grouping
  operator *overloads* by a fixed 2-char code, since every conversion
  operator carries a different target type and is never an overload of
  another one — but treating `cv` as entirely unparseable meant hitting it
  aborted the *whole* scope-recovery attempt, discarding the class name
  already parsed before it. Confirmed empirically: `_ZNK3FoocvN2ns3BarEEv`
  (`Foo::operator ns::Bar() const`) made `itanium_scope_components()`
  return `None` outright, and `owner_class_of()` therefore returned `None`
  instead of `"Foo"`. Fixed by recognizing `cv` as a distinct, opaque leaf
  component (`"{op:cv}"`) in `_parse_operator_component()` — separately
  from `_ITANIUM_OPERATORS`, since the overload-grouping semantics
  correctly stay excluded — and forcing `_step_next_component()`'s `done`
  flag to `True` immediately upon seeing it, regardless of nesting: the
  conversion operator's own leaf is always the last component, and the
  target-type encoding immediately following `cv` (e.g. `N2ns3BarE` for
  `ns::Bar`) is a full, arbitrary Itanium `<type>` production — a much
  larger grammar than this structural parser attempts elsewhere — but
  recovering the *scope prefix* never needs that type parsed at all, only
  a signal to stop before attempting it. Regression tests added: direct
  parser-level cases in `TestItaniumScopeParser`/`TestMsvcScopeParser`'s
  sibling `diff_cxx_rules` test file, plus an end-to-end
  `directly_referenced_stdlib_types` test confirming a `Foo`-owning
  conversion operator's embedded `std::string` field is no longer
  filtered.

  **An eleventh finding pointed at a masking mechanism the earlier
  cross-index split didn't fully close.** Splitting `_spelling_index()`
  into independent `stdlib_index`/`record_index` patterns (an earlier
  fix) solved masking *between* the two indices — a non-stdlib wrapper's
  identity embedding a stdlib type's spelling verbatim. It did not solve
  the identical masking *within* either index (Codex review, fresh
  evidence): `.finditer()` only returns non-overlapping matches, so when
  one candidate's registered spelling is itself a substring of another
  candidate's spelling *in the same index* (e.g. `"std::string"` inside
  `"std::vector<std::string>"`, both stdlib; or a non-stdlib `"Inner"`
  inside `"Wrapper<Inner>"`), the longest-first alternation matches the
  outer candidate first, consumes the whole span, and the search
  continues from the end of that match — so the inner one, though
  directly present in the text, is never independently reported.
  Confirmed empirically for both the stdlib and non-stdlib cases (and, on
  further investigation while fixing this, the identical mechanism in
  `typedef_pattern`'s typedef-key matching too — a third, independently
  confirmed instance of the same root cause). Fixed with a single new
  helper, `_finditer_allow_nested()`, used at all three call sites: for
  every match found, it recurses into `text[m.start()+1 : m.end()]` — a
  strictly narrower window, so recursion terminates — to catch a shorter
  candidate embedded anywhere inside it, at any nesting depth, not just
  one level. Kept as one shared helper rather than three inline copies
  since all three loops have the exact same masking mechanism. Verified
  against the existing large-corpus performance regression guard
  (`test_many_unreferenced_stdlib_candidates_scan_efficiently`) to confirm
  this doesn't reintroduce the quadratic candidate-by-candidate cost the
  single-pattern rewrite was originally built to eliminate — the extra
  recursive search only runs when a match is actually found (rare in the
  common case), bounded by nesting depth, not candidate count.

  **A twelfth finding closed a narrower gap in the conversion-operator
  owner fix itself (tenth finding, above).** The `"::operator "`-marker
  fix only detects a conversion operator when an *owner* precedes the
  marker; a bare-recorded conversion operator (no owning-class prefix at
  all, per the tenth finding) can still carry a *qualified target* with
  its own `"::"` — e.g. `"operator ns::Bar"`, no `"Foo::"` prefix — and
  for that shape neither the marker (there's no owner text before
  `"operator"`) nor the previous unqualified-bare check applied, so the
  naive `rsplit` fallback still ran and returned junk like `"operator
  ns"` (CodeRabbit review). Confirmed empirically: constructing exactly
  this input shape reproduced the bad `"operator ns"` result before the
  fix. Fixed by checking for the `"operator "` prefix the same way the
  already-fixed unqualified case is detected, falling through to
  mangled-name recovery for both shapes uniformly.

  **A thirteenth finding pointed at a robustness gap in the eleventh
  finding's own fix, not a new correctness bug.** `_finditer_allow_nested()`
  (the nested-match helper from the eleventh finding) recursed one Python
  call per nesting level to search each match's own span for a further
  embedded candidate (Codex review, fresh evidence). For a genuinely deep
  chain of registered spellings each nested one inside the next —
  plausible for template-metaprogramming-heavy C++ under a compiler's
  configured template-instantiation depth (GCC/Clang both default well
  into the hundreds, and it is routinely raised higher for real
  metaprogramming-heavy code) — that per-level recursion follows the C++
  template depth 1:1. Confirmed empirically: 1,000 successively nested
  registered candidate spellings raised `RecursionError` under Python's
  default 1,000-frame recursion limit, aborting the whole comparison
  rather than degrading gracefully. Fixed by converting the recursive
  search into an explicit stack — each entry is still a strictly narrower
  window than the match that produced it, so the search still always
  terminates, just without consuming Python's call stack to do it, so no
  amount of nesting depth can overflow it.

  **A fourteenth finding was a genuine regression the tenth finding's own
  fix introduced, caught before merge.** Recognizing the Itanium `cv` code
  as an opaque leaf component (tenth finding) used a single fixed
  placeholder label (`"{op:cv}"`) regardless of the conversion's actual
  target type. `diff_types._overload_group_key()` chains
  `itanium_qualified_name()` — which now runs this label onto the scope
  prefix — to decide whether two declarations are genuine overloads of one
  another for `_diff_overload_additions()`'s KDE-policy check (Codex
  review, fresh evidence). A fixed placeholder made *every* conversion
  operator on a class produce the same qualified name regardless of
  target — e.g. both `operator int()` and `operator double()` on the same
  class reduced to `"Foo::{op:cv}"` — collapsing two conversion operators
  that are never overloads of each other (each is a distinct, unambiguous
  conversion function; there is no shared `&Foo::operator T` that becomes
  ambiguous) into one group. Confirmed empirically:
  `_diff_overload_additions()` fired a false `OVERLOAD_ADDED` for adding
  `operator double()` alongside an existing `operator int()` before this
  fix. Fixed by embedding the raw, un-decoded remainder of the mangled
  string after `cv` into the label itself, instead of a fixed placeholder
  — Itanium mangling is deterministic, so the same target always
  reproduces the identical remainder (keeping genuine re-declarations in
  the same group) while distinct targets always mangle differently
  (keeping them in distinct groups), without this parser needing to
  actually decode the arbitrary Itanium `<type>` grammar the remainder
  encodes. Owner recovery (`owner_class_of()`, which only ever consumes
  `comps[:-1]`, dropping the leaf entirely) is unaffected either way.

  **A fifteenth finding pointed at a data-model assumption this module's
  own new code introduced without verifying, contradicted by an existing
  sibling.** `directly_referenced_stdlib_types()` built `non_stdlib_records`
  as a plain `dict[str, RecordType]` keyed by identity — when
  `snapshot.types` contains multiple entries sharing the same identity
  (e.g. a complete definition alongside an ODR-duplicate or incomplete
  declaration), a later entry silently overwrote an earlier one, so a
  public signature reaching that identity walked only the survivor (Codex
  review, fresh evidence). `surface.py`'s own `record_by_name` index —
  the established reference this module has mirrored throughout every
  finding above — already anticipates exactly this by keying on a *list*
  of records per identity (`dict[str, list[RecordType]]`) and walking
  every one (`for rec_node in rec_nodes: ...`), not a single winner; this
  module's new dict introduced a real regression relative to that already-
  correct sibling pattern, not a hypothetical edge case. Confirmed
  empirically both orderings (the complete definition first, and the
  complete definition last): whichever entry didn't survive the dict
  overwrite, if it carried a `std::` field the survivor lacked, that field
  was silently missed. Fixed by changing `non_stdlib_records` to
  `dict[str, list[RecordType]]` (appending instead of overwriting) and
  walking every record for a reached identity in the worklist loop,
  checking each one's own `origin` independently (a private-origin
  duplicate still excludes only itself, not a public-origin sibling
  sharing the same identity) — exactly mirroring `surface.py`'s own
  per-record walk.

  **Wiring (this pass):** `diff_types.py`'s single choke-point gate,
  `_is_abi_surface_type()`, now accepts a `directly_referenced` set (built
  once per detector via `_directly_referenced(old, new)`) and un-filters a
  std:: record that set names, instead of blanket-filtering every std::
  record regardless of direct use. Because every RecordType-based
  struct/union/field/kind/reserved detector in that file already shares this
  one gate function, wiring it there once covers all of them uniformly —
  not 9 independent, individually-drifting call sites. While wiring this in,
  the FP-rate corpus's own new cases (`stdlib-direct-reference` category)
  surfaced a second, *pre-existing* correctness gap in the gate's std::
  check itself (independent of `directly_referenced`): it filtered using
  `_is_non_abi_surface_type(t.name, ...)`, i.e. bare `t.name` only, the exact
  same bare-vs-qualified split as the `type_reachability.py` fix above — so
  a real castxml/clang-produced std:: record (bare `name`, qualified
  `qualified_name`) was **never actually filtered as std:: at all**,
  independent of whether anything referenced it. Fixed in the same gate by
  keying the std:: prefix check on `qualified_name or name` (the anonymous-
  type-marker half of the check still uses bare `name`, unaffected).
  `diff_platform.py`/`diff_symbols.py`/`diff_vtable_layout.py`/
  `diff_stdlib_impl.py`/`diff_layout.py`/`diff_filtering.py`/
  `diff_type_spellings.py`, plus `diff_types.py`'s own enum/typedef paths
  (which call `is_non_abi_surface_type`/`is_abi_surface_type_name` directly
  on enum/typedef names, not through `_is_abi_surface_type`), remain
  unwired and carry the identical bare-name gap — each needs its own
  individually-verified follow-up (FP-rate/mutation-score gates), not a
  drive-by extension of this pass's RecordType-scoped fix.
- **L4 SYCL replay via a resolved `--gcc-path icpx`/`dpcpp` override — flag
  vocabulary fixed, the two-pass JSON-document crash fixed, real host+device
  dual-context replay still not implemented.** Fixing L4 clang_bin
  resolution to honor `--gcc-path` (an earlier PR) meant L4 could for the
  first time actually invoke a SYCL-capable compiler (`icpx`/`dpcpp`)
  instead of always a bare `clang`, which surfaced a narrower, real gap:
  `-fsycl`/`-fsycl-*` wasn't in `adapters.base.ABI_RELEVANT_FLAG_PREFIXES`,
  so it never reached the reconstructed L4 replay command even when the
  real build recorded it (Codex review) — fixed, since the existing
  `abi_relevant_flags` carry-through (`replay_extra_flags`) already handles
  this class of flag correctly for every other case (`-std=`,
  `-fvisibility`, …), so this was a one-line vocabulary gap, not a design
  gap. That fix's own consequence #1 — "whether replaying without an
  explicit `-fsycl-host-only` pin causes `icpx` to attempt a device pass
  this pipeline can't consume" — was later **confirmed against a real
  toolchain**: a real `-fsycl` bazel compile unit (oneDAL, 2.8GB AST) hit
  exactly this, `json.load()` failing with `Extra data: line 26076735
  column 2` at the byte offset where the device pass's document begins,
  i.e. consequence #2 also confirmed (legacy `dpcpp`/`icpx` both emit
  multi-document host+device AST output for a plain `-fsycl`, the identical
  shape `sycl_context.py` already handles for the *L2* header-AST backend).
  **Now fixed** by mirroring the L2 backend's own fix
  (`dumper_ast_config._build_clang_header_command`/
  `dumper_clang._needs_sycl_host_only`) rather than adopting
  `sycl_context.py`'s full host/device dual-context decoder: L4 replay
  parses ONE compile unit at a time and has no `--frontend-context device`
  concept to select against (unlike L2's header-AST dump, which can be
  asked for either context), so there is nothing for a second document to
  serve here — `_clang_context_args` (shared by both the AST pass and the
  macro pass) now appends `-fsycl-host-only` whenever
  `dumper_clang._needs_sycl_host_only` says the resolved compiler is an
  Intel oneAPI driver with SYCL effectively enabled and no single pass
  already pinned, collapsing the compile back to the one host-side pass
  that actually links into the scanned `.so` — the device pass's SPIR-V
  kernel code never does. Reuses `_needs_sycl_host_only` directly (not a
  reimplementation) so the last-flag-wins `-fsycl`/`-fno-sycl` scan and the
  legacy `dpcpp`/`dpcpp-cl` SYCL-implied-by-default handling stay a single
  source of truth with the L2 fix. The remaining "Extra data" `json.load()`
  path now also emits an actionable hint (mirroring
  `dumper_clang_errors._parse_clang_ast_result`'s) for any *other*,
  not-yet-special-cased offload flag that still produces a multi-document
  stream, rather than a bare byte offset. **Still not implemented, and
  deliberately out of scope**: a genuine host+device dual-context L4
  replay (an L4 counterpart to `--frontend-context device`) — nothing in
  L4's `SourceAbiTu`/`CompileUnit` model has a notion of "this TU's device
  pass," so adding one is a real, separate design (schema + linker +
  cross-check changes), not a follow-up to this crash fix.
- **`depfile_args_from_argv()`'s `trusted_root` parameter — the self-jail
  vulnerability is closed, real production wiring not implemented.** Closing
  the vulnerability (a compile unit's own `directory` field, attacker-
  controlled for a unit sourced from an untrusted build pack, was used as
  both the resolution base *and* the trust jail for expanding an unexpanded
  `@response-file`) required the three production call sites
  (`ClangIncludeExtractor.extract_from_build`,
  `ClangPreprocessorExtractor.capture_macros`, `preprocessor_scan._depfile_context`)
  to fall back to the existing safe "drop the token" behavior, since none of
  them currently supply an independently-trusted `trusted_root` (Codex
  review). **Not implemented**: threading a genuinely-trusted root into
  those three call sites so response-file expansion works again for this
  secondary L5/S2-scoping path. This is real, non-trivial plumbing, not a
  one-line fix: `BuildEvidence.build_root`/`source_root` exist as fields but
  no adapter (`compile_db.py`, `cmake_file_api.py`, `ninja.py`, `bazel.py`,
  `make.py`) actually populates them today, so there is no already-flowing
  trusted value to read off the model — the real anchor would have to be
  threaded as a new parameter from `inline.collect_inline_pack()`'s own
  `sources`/`build_info` CLI arguments (or, for the separate Flow-2
  `abicheck_inputs/` ingest path in `inputs_pack.py`, the pack's own `root:
  Path` already used for `_safe_pack_path` containment) through several
  call layers in `inline.py` (already WARN-flagged oversized) and
  `preprocessor_scan.py`. The functional impact of the current gap is
  narrower than it first appears: `build_context.py`'s own `@file`
  expansion (correctly jailed to the compile database's own directory since
  the first response-file fix in this PR) already expands a
  `compile_commands.json`-sourced `CompileUnit.argv` *before* it reaches
  these three call sites, so they rarely see a raw, unexpanded `@file`
  token for that primary path in practice — the gap mainly affects the
  Flow-2 untrusted-pack path this fix was specifically about securing in
  the first place. Confirmed via the full local suite (20935 passed) that
  disabling expansion at these three call sites introduces no test
  regressions.
- **`Param.is_va_list` (G31 Phase C continued) inherits the toolchain-
  identity-probe gap above, rather than being a new problem.** Its
  extraction predicate (`dumper_clang_qualifiers._clang_param_is_va_list`)
  is deliberately scoped to the one ABI verified here — x86-64 System V —
  and a snapshot from an unrecognized target already degrades to a
  conservative `False` (see the function's own docstring). What's still
  open (Codex review, fresh evidence): the snapshot-level reliability flag
  (`AbiSnapshot.clang_va_list_facts_reliable`) records only "did the fixed
  extractor run", not "against which resolved target" — so two
  genuinely-different-target clang snapshots (x86-64 vs. AArch64, say) can
  both read as reliable, and a real cross-architecture comparison (which
  this tool's comparability layer permits in general) could read the
  x86-64 side's real detections against the AArch64 side's uniform `False`
  as a spurious `PARAM_BECAME_VA_LIST`/`PARAM_LOST_VA_LIST`. No header-AST
  fact has resolved-target validation today, not just this one — closing
  it here alone would be an inconsistent one-off fix for a structural gap;
  it belongs with the toolchain-identity probe above once that lands.
- **A using-declaration re-exporting a namespace-scope constant produces
  two `AbiSnapshot.constants` entries for one declaration on the castxml
  backend — reported against real oneDAL headers. A per-snapshot,
  name-shape-based dedup was implemented, reviewed, found unsound in two
  independent ways, and reverted rather than shipped broken (Codex
  review, three rounds).** `cpu.hpp` opens a plain (non-inline)
  `namespace v1 { constexpr ... cpu_feature_map = ...; }` and later does
  `using v1::cpu_feature_map;` inside the enclosing `detail` namespace —
  an ordinary C++ re-export pattern, not versioned-inline-namespace ABI
  tagging (`inline namespace v1 {}`, which is the shape
  `diff_namespaces.py`'s `_paired_stable_indices`/
  `EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT` machinery already merges
  aliases for — that machinery is function/type-scoped, cross-*snapshot*
  (old vs. new), and keys on `experimental`/`preview`/`v0` segments
  specifically, so it neither targets nor would catch this same-snapshot
  pattern even if it applied to constants). castxml's `<Variable>` XML
  records the using-declaration as a second full element rather than a
  reference to the original, so `dumper_castxml.py`'s
  `_iter_public_constants()` (the shared source for `parse_constants()`/
  `parse_constant_headers()`) emits both `detail::v1::cpu_feature_map`
  and `detail::cpu_feature_map` as independent entries in
  `AbiSnapshot.constants` with the same value — distinct qualified-name
  *keys*, not a key collision, so this is invisible to `model.py`'s
  existing first-wins duplicate-name dedup (`function_map`/
  `variable_map`/`type_by_name` all dedup by identity — mangled name or
  bare type name — under which a using-shadow naturally collapses onto
  its target; `AbiSnapshot.constants` has no mangled-name equivalent).
  Reported at real scale, not a one-off: 98 alias groups across 267
  constants in the reporting library's own dump. The same castxml
  behavior duplicates other declaration kinds too, with two different
  outcomes: 1,477 of 6,009 mangled function names were duplicated the
  same way, but that half is **not a *double-reporting* bug** —
  `model.py`'s mangled-name-keyed `function_map`/`variable_map` already
  collapse a using-shadow function or variable onto its target for free
  for the ABI/mangled-symbol question (a using-declaration doesn't change
  what a symbol mangles to), so this is not a repeat of the constants
  over-reporting problem above. **It is a separate, undocumented
  false-negative gap on the source/API side, narrower in scope than the
  `std::`-specific `STD_REEXPORT_REMOVED` detector below (Codex review,
  fresh evidence):** because the diff is keyed on the unchanged mangled
  symbol, a release that removes only the `using` re-export of a
  library's *own* function or variable — not a `std::` name, which is
  the one case `STD_REEXPORT_REMOVED` already covers — while keeping the
  real declaration and its export produces no finding at all, even
  though a consumer that named the alias-qualified spelling no longer
  compiles. This is the identical shape of gap the clang-side note below
  documents for constants (an unchanged underlying identity hides a real,
  source-visible alias removal), just reached from the opposite side
  (castxml capturing the alias correctly here, the *diff* layer being the
  one blind to it) — not attempted in this pass, and would need
  `detect_std_reexport_removed`'s general shape (matching declared
  qualified names, not `std::`-specific) extended to a library's own
  namespaces, which is its own scoped detector design, not a drive-by
  extension of this entry. A duplicated bare *type* name (`range`
  alongside `v1::range`) is a **separate, still-open** gap from the
  type-dedup entry already documented above
  (opaque-type suppression keyed by bare `RecordType.name` colliding
  *across namespaces* on an accidentally-shared bare name) —
  `range`/`v1::range` are two distinct qualified spellings of what may be
  the same using-re-exported record, so today's exact-bare-name
  first-wins dedup does not merge them either, and would need its own,
  differently-shaped fix (threaded through `RecordType` identity, not
  `AbiSnapshot.constants`), not attempted here.

  **Confirmed independently that the duplicate-entry shape is castxml-only
  — but the direct-clang L2 backend is not clean either, it just fails in
  the opposite direction.** Verified directly against real Clang 18
  (`-Xclang -ast-dump=json`) on a minimal repro of the same shape: a
  using-declaration lowers to a `UsingDecl` node carrying the qualified
  target name (`v1::cpu_feature_map`) but no `init`/value of its own,
  immediately followed by an **implicit**, **unnamed** `UsingShadowDecl`
  (`"isImplicit": true`, no `name` key at all — confirmed by inspecting
  the emitted JSON directly, not inferred). **That `UsingShadowDecl` node
  does carry real target identity** — a `target` object with the
  underlying `VarDecl`'s own `id`, `kind`, `name`, and `type` (confirmed
  by inspecting the real JSON: `{"id": "0x...", "kind": "UsingShadowDecl",
  "isImplicit": true, "target": {"id": "0x...", "kind": "VarDecl", "name":
  "cpu_feature_map", "type": {"qualType": "const int"}}}`) — so this is
  identity Clang exposes and `dumper_clang.py` simply never reads, not
  identity Clang lacks (Codex review, fresh evidence; an earlier revision
  of this entry claimed the latter, which was wrong). `_categorize()` has
  no branch matching either `UsingDecl` or `UsingShadowDecl` — its
  `VarDecl` branch requires both `kind == "VarDecl"` and a non-empty
  `name`, neither of which either using-related node satisfies — so both
  are silently dropped, `target` included, and the re-exported spelling
  never enters `AbiSnapshot.constants` at all on that backend.
  This is *complementary* missing coverage, not a clean bill of health:
  a release that removes only the `using v1::cpu_feature_map;` re-export
  while keeping the real `v1::cpu_feature_map` definition breaks every
  consumer of the re-exported spelling, but both the old and new
  Clang-derived snapshots contain only the one underlying key —
  `_diff_constants()` has nothing to compare and emits no
  `CONSTANT_REMOVED`, a silent false negative. Castxml's two qualified
  keys, over-reporting as they are for the case this entry is about, would
  at least detect that removal correctly. So the two backends fail in
  opposite directions on the same underlying gap (no alias-identity
  evidence for constants) — over-reporting on castxml, under-reporting on
  clang. Not verified against a live castxml run (no castxml binary
  available in this environment to reproduce the XML shape directly) —
  the castxml-side mechanism above is taken from the original report, not
  independently re-derived from castxml's own output. **Update (G31 Phase
  C, later pass): now independently re-derived, against a real
  conda-forge castxml 0.7.0 build, and the "opposite directions" framing
  in this paragraph does not hold — see the "Option (b) closed" note at
  the end of this entry, below, for the reproduction and its
  implications.**

  **Attempted fix, reverted (three review rounds):** a
  `qualified_name_segments.dedup_versioned_namespace_alias_items()`
  helper collapsed one spelling onto the other within one already-
  collected `_iter_public_constants()` item list, gated on BOTH (1) the
  two qualified names differing by exactly one versioned-inline-namespace
  *segment* (`version_strip_segments` — the same, already-trusted
  structural check `diff_namespaces.py` already relies on for its own
  alias merging) AND (2) the two values being byte-identical. Deliberately
  narrower than the plain value-equality merge this same module's own
  docstring already rejected after three earlier review rounds for a
  *different*, cross-snapshot reason (that heuristic had no name evidence
  at all and could merge unrelated same-valued declarations that never
  even coexist together) — this attempt was same-snapshot only and added
  a real structural name-shape gate. It was not enough. Two more rounds of
  review found it unsound from two independent directions, and a third
  round (after the second was "fixed") found the second fix was *also*
  wrong, at which point it was reverted rather than iterated a fourth
  time:

  1. **Round 1 (P1):** the first revision kept the shorter, version-
     stripped alias and dropped the version-qualified original —
     reasoned as "the spelling a consumer of the `using` re-export
     actually writes." This actively fabricated findings: a release that
     adds or removes only the re-export (the real declaration unchanged)
     went from one snapshot keeping `detail::v1::x` (no alias present,
     nothing to strip) to the other collapsing down to `detail::x`
     instead — two different surviving keys for one unchanged
     declaration, which `_diff_constants` read as a spurious
     `CONSTANT_REMOVED` + `CONSTANT_ADDED` pair. In other words: this
     revision manufactured exactly the kind of double-reporting it was
     written to eliminate, just shaped as fabricated add/remove instead
     of a duplicated `CONSTANT_CHANGED` — arguably worse, since a
     fabricated `CONSTANT_REMOVED` reads as BREAKING.
  2. **Round 2 (P1 again, on the "fix" for round 1):** reversing the
     direction — always keep the version-*qualified* spelling, always
     drop the alias — looked sound (invariant to whether either side
     happens to carry the re-export) and was reviewed as fixing round 1.
     A further round then produced a concrete counterexample proving the
     fixed direction was *also* wrong in general: `namespace detail {
     constexpr int x = 42; namespace v1 { using detail::x; } }` is
     legal C++ where a using-declaration imports a name **into** a
     versioned-looking namespace rather than out of one — here
     `detail::x` (the shorter spelling) is the real declaration and
     `detail::v1::x` (the longer, version-qualified spelling) is the
     alias, the exact reverse of what round 1's fix assumed universally.
     "Always keep the longer/qualified spelling" reproduces round 1's
     failure mode for this reversed input shape instead. **This is the
     structural finding that ended the attempt**: qualified-name *shape*
     alone (segment count, or which segment looks version-tagged) cannot
     determine which of two spellings is the real declaration and which
     is the using-introduced alias — a using-declaration can legally go
     in either direction relative to a versioned-looking namespace
     segment, and `_iter_public_constants()`'s current
     `(qualified_name, value, declaring_header)` output carries no
     signal (declaration order, an `artificial`-style marker, a
     using-shadow/target back-reference) that distinguishes the two
     directions. No fixed, name-shape-based rule can be sound for both.
  3. **Round 3 (P2, on the value-equality gate itself, independent of
     direction):** even a correctly-directed rule would still merge two
     genuinely independent declarations that happen to form a
     version-alias-shaped name pair AND happen to share a value at
     extraction time (e.g. `namespace detail { namespace v1 { constexpr
     int x = 42; } constexpr int x = 42; }` — two unrelated `x`s, no
     `using` anywhere) — the same class of coincidence-driven risk this
     module's docstring already flags for the cross-snapshot merge it
     rejects, now shown to reach the narrower same-snapshot case too.

  Given round 2 proves no per-snapshot, name-shape-based direction rule
  can be sound, and a genuinely correct fix needs real using-shadow/
  target identity evidence rather than name shape — continuing to patch
  the heuristic's direction a third time was exactly the "one more
  counterexample" pattern this file's own linkage-blind-removal and
  type-identity entries above already warn against repeating. Reverted in
  full (code, tests, changelog fragment) rather than shipped with a
  known-unsound direction, per this file's own "known gaps over risky
  reactive patches" convention — and per the same "attempted twice,
  reverted twice" discipline already established here: a false BREAKING
  finding fabricated by an unsound heuristic is a worse failure mode than
  the pre-existing double-reporting it would have fixed, since the
  original bug is merely noisy while a wrong-direction fabricated
  `CONSTANT_REMOVED` blocks a release for nothing.

  **What "real identity evidence" actually means differs by backend, and
  the two must not be conflated (Codex review, fresh evidence corrected
  an earlier draft of this entry that did conflate them).** On
  direct-clang, the identity evidence demonstrably EXISTS today and is
  simply unused: `UsingShadowDecl.target` (see above) names the exact
  underlying `VarDecl` by AST id, so a sound clang-side fix is a real,
  scoped option — capture the shadow, resolve `target.id` back to the
  already-visited `VarDecl`, and re-register the alias under the
  using-declaration's own enclosing-scope name, carrying its target's
  identity forward rather than guessing from name shape. That is new
  extraction work (`_categorize()` doesn't currently visit
  `UsingShadowDecl` at all) and was not attempted in this pass, but it is
  a materially different, better-founded starting point than the
  name-shape heuristic this entry's earlier rounds tried and reverted. On
  castxml, whether an equivalent target back-reference exists in its
  `<Variable>` XML is genuinely **unknown** — not confirmed absent, not
  confirmed present — since no castxml binary was available in this
  environment to inspect real output for this construct; the reported
  castxml duplication mechanism throughout this entry is taken from the
  original report, not independently re-derived. A correct, full fix
  needs one of: (a) the clang-side `target.id` threading described above,
  landed and verified against the FP-rate/mutation-score gates before
  trusting it; (b) inspecting real castxml XML for this construct to
  determine whether it exposes anything equivalent, which this
  environment cannot do; or (c) a genuinely different architecture that
  resolves the ambiguity at diff time with both sides' full data available
  simultaneously (mirroring how `diff_namespaces.py`'s
  `_paired_stable_indices` jointly builds paired OLD/NEW indices for
  functions/types, gated on real identity) rather than destructively
  dropping information from one side's snapshot in isolation — a
  materially larger, cross-cutting change on its own, not a follow-up
  patch to the same per-snapshot helper.

  **Option (b) closed (G31 Phase C, real conda-forge castxml build):** a
  real, policy-conformant castxml (0.7.0, `conda-forge`, within the
  `>=0.6.11,<0.8.0` range `castxml_policy.py` enforces, bundled Clang
  20.1.8) is now available and was used to reproduce this construct
  directly through the exact invocation shape `_build_castxml_command`
  emits (`--castxml-cc-gnu (g++ -x c)`/`--castxml-cc-gnu g++`, matching
  real header-dump usage) — **the originally-hypothesized castxml
  duplication mechanism does not reproduce.** For `namespace detail {
  namespace v1 { constexpr int cpu_feature_map = 42; } using
  v1::cpu_feature_map; }`, real castxml emits exactly **one** `<Variable>`
  element (`context="_13"`, i.e. `v1` — never `detail`), and that single
  element's id is listed in *both* `detail`'s and `v1`'s `<Namespace
  members="...">` attribute — a shared reference, not two elements. There
  is no `<Using...>`-shaped XML tag in castxml's schema at all (checked
  against castxml's own `--help`), and forcing the value to be ODR-used
  (`&cpu_feature_map` from an inline function) does not change this.
  Since `dumper_castxml.py`'s `_variable_els`/`_iter_public_constants()`
  iterate the flat, once-per-XML-element id map (`_build_id_map`), not
  either namespace's `members` list, they see this construct exactly once
  too — `_qualified_name()` resolves it to `detail::v1::cpu_feature_map`
  only; the alias spelling `detail::cpu_feature_map` never enters
  `AbiSnapshot.constants` on this castxml version, at all. The identical
  single-element behavior was independently confirmed for the sibling
  type-dedup case this entry cross-references (`struct range` in `v1`,
  `using v1::range;` in `detail`): one `<Struct>` element, shared between
  both namespaces' `members` lists, never a duplicate. **What this means
  for the entry above:** the "castxml over-reports (duplicate keys),
  clang under-reports (missing alias)" asymmetry this entry documented
  was written from an unverified report and does not hold for the
  currently-supported castxml version range — for this exact construct,
  both backends now demonstrably fail the *same* way (silent false
  negative: the alias spelling is absent from the snapshot entirely,
  matching the clang-side finding already confirmed above). This does not
  retroactively invalidate the original report (98 alias groups across
  267 constants, real oneDAL headers) — that scan may have used a
  different castxml build, or the real duplication may come from a
  different source construct entirely (e.g. two independent, textually
  separate definitions sharing a value, rather than a language-level
  using-declaration/using-directive) that this pass did not have the
  original headers to reproduce against. It does mean: (1) the three
  reverted name-shape-heuristic attempts documented above were correctly
  reverted regardless of this finding (their unsoundness was proven by
  counterexample, independent of whether castxml duplicates), so nothing
  here reopens that question; (2) a future fix attempt should design for
  the *false-negative* alias-identity gap confirmed symmetric on both
  backends (option (a)'s `UsingShadowDecl.target` clang-side threading is
  now the best-founded starting point, since it is real, already-present
  AST identity, not a heuristic), rather than a castxml-side dedup this
  evidence no longer motivates; (3) closing this for real still needs the
  original large-scale report's exact source construct identified before
  trusting any fix against it — not attempted here, since the oneDAL
  headers that produced the original 98/267 count are not available in
  this environment either.
- **`AbiSnapshot.typedefs` is a flat `dict[str, str]` keyed by bare
  (unqualified) name on both header backends — a member/nested typedef
  silently collides with, and can be overwritten by, any other typedef
  anywhere in the snapshot sharing the same bare spelling. Confirmed by
  reading both producers directly, not yet fixed (G31 Phase C CastXML
  fact-completeness audit).** `dumper_castxml.py`'s `parse_typedefs()` and
  `dumper_clang.py`'s `parse_typedefs()` both do the identical
  `typedefs[name] = underlying`, where `name` is the typedef's own local
  `name` attribute/node field — never the scope-joined qualified spelling
  `_qualified_name()`/`_qualified()` uses for every other declaration kind
  in the same two modules. Verified directly against real CastXML XML
  output (`--castxml-output=1`, clang-emulated) for a minimal repro: a
  member typedef nested inside a struct (`struct WithUsing { using
  value_type = int; ... };`) is emitted as an ordinary top-level
  `<Typedef name="value_type" ... context="_12" .../>` element, structurally
  indistinguishable from a namespace-scope typedef except for its
  `context` attribute — which `parse_typedefs()` never reads. Two structs
  each declaring their own `value_type` member alias (an extremely common
  C++ pattern — STL-container-shaped types conventionally expose
  `value_type`/`size_type`/`reference`/... as member typedefs) collide on
  the identical bare key `"value_type"` in the resulting dict, and
  whichever element is encountered last in document order silently wins —
  the other's aliasing information is dropped from the snapshot entirely,
  with no warning, error, or any user-visible sign of the loss. The same
  collision shape reproduces on the direct-clang backend: `_typedefs` is
  populated by the same flat walk used for every other decl kind, but
  `parse_typedefs()` discards the entry's own recovered `scope` and keys
  only on `node["name"]`. **Not fixed here**: this is a real, if narrow,
  public-model change — `AbiSnapshot.typedefs`'s key shape is read by
  `type_reachability.py`'s `_typedef_spelling_targets()` (see the
  "Type reachability" entry above, which already works around this same
  bare-key ambiguity from the *consumer* side via its own
  ambiguity-counting helper, `_typedef_spelling_targets`, rather than
  assuming a bare key uniquely names one typedef), by `diff_types.py`'s
  typedef diffing, and by `surface.py`'s typedef-following in
  `_walk_type_closure` — none of which currently have test coverage for
  the cross-class member-typedef-collision case to validate a change
  against. A correct fix needs a qualified (or at minimum
  collision-detecting) key threaded through both producers and every
  consumer simultaneously, each independently re-verified against the
  FP-rate/mutation-score gates before trusting it — the same systematic,
  cross-cutting shape (and the same "known gaps over risky reactive
  patches" reasoning) as the already-documented bare-`RecordType.name`
  opaque-type-suppression collision above, for a different model field.
  Filed here per this file's own convention rather than attempted under
  this pass's time budget.

  **Closed, additively, in a later pass (G31 Phase C continued).** Rather
  than replacing `AbiSnapshot.typedefs`'s key shape — which would have
  meant re-verifying every one of its existing consumers
  (`type_reachability.py`, `diff_types.py`, `surface.py`) against a changed
  contract, plus every external Python-API caller reading that field
  directly — the actual fix is purely additive: a new field,
  `AbiSnapshot.typedefs_qualified` (schema v25), a fully-qualified-name-keyed
  twin populated by both `dumper_castxml.py`'s and `dumper_clang.py`'s
  `parse_typedefs_qualified()` (using the identical `_qualified_name()`/
  `_qualified()` scope-joining every other declaration kind in those modules
  already uses) alongside the existing, deliberately-unchanged
  `parse_typedefs()`. Since a qualified name is unique per declaration, this
  twin cannot suffer the bare-name collision at all — both `Foo::value_type`
  and `Bar::value_type` survive as distinct entries where only one bare
  `value_type` could before. Threaded through the ELF manifest per-TU merge
  path too (`TuFragment`/`MergedTuFragments`/`ElfHeaderAstResult` in
  `tu_fragment.py`/`tu_merge.py`/`dumper_manifest.py`), closing the identical,
  separately-documented "Known, accepted limitation" comment `tu_merge.py`
  carried for the multi-TU case specifically. Only one consumer was wired to
  actually use the new field: `type_reachability.py`'s
  `directly_referenced_stdlib_types()` (via a new `_merged_typedefs()` helper
  that folds `typedefs_qualified` into the flat dict already passed to
  `_typedef_spelling_targets()` and siblings) — closing the real false
  negative where a public signature spelled with the qualified alias the
  bare dict had already lost could silently miss a reachable `std::` field.
  `diff_types.py`'s typedef diffing and `surface.py`'s typedef-following in
  `_walk_type_closure` are **not** wired to the new field in this pass — each
  is its own scoped follow-up, not a drive-by extension, since each has its
  own call shape and (per this file's own established discipline) needs its
  own test coverage before trusting a change to it. No reliability flag was
  needed for the new field (unlike the `*_facts_reliable` flags v19–v23 use
  for a real-but-wrong scalar default): an empty `typedefs_qualified` is
  exactly the same value a genuinely-typedef-free snapshot would carry, so a
  pre-v25 snapshot degrades cleanly to "no extra qualified data available"
  rather than being misread as a real fact — see `serialization.py`'s own
  v25 history-comment entry for the full reasoning. Verified via new unit
  tests on both header backends directly (`_CastxmlParser.
  parse_typedefs_qualified`/`_ClangAstParser.parse_typedefs_qualified`) and
  an end-to-end `type_reachability` regression proving the bare dict's lossy
  collision no longer hides a real `std::` field.

- **`run-plan.json`'s `gate` block is unprotected against an already-shipped,
  version-skewed reader — investigated end-to-end, confirmed structurally
  infeasible to close within the document's own shape, not fixed (Codex
  review, PR #779).** `aggregate_manifest.py`'s `gate` block gained real
  protection against an old `aggregate` binary misapplying a new manifest's
  policy: `_check_manifest_version` already existed *before* this PR with
  real MAJOR-rejection logic (`major > supported` raises), so bumping
  `aggregate_manifest_version` from `1.0` to `2.0` gives an old, already-
  installed reader a real, working rejection point — it already knows how to
  say "too new for me." `run-plan.json`'s new `RUN_PLAN_SCHEMA_GATE`
  (`abicheck.run-plan/v2`) discriminator was modeled on the identical
  pattern but does **not** give the same protection, and cannot: traced the
  full old (pre-PR779, commit `90e1813`, the last shipped state) `aggregate
  --run-plan` pipeline directly. (1) Old `RunPlan.from_dict()`/
  `RunPlanCheck.from_dict()` use exclusively total, defensive conversions
  (`str()`, `bool()`, `isinstance`-guarded comprehensions, `.get()` with a
  default for everything) — by design, matching this package's own
  documented forward-compat convention ("every dataclass carries
  `to_dict()`/`from_dict()` with defensive `.get()` parsing so a newer/
  hand-edited pack never aborts a load" — `abicheck/buildsource/CLAUDE.md`).
  Nothing in either function can raise regardless of input shape, so an
  unrecognized `schema` string and an unknown `gate` key are both silently
  ignored, not rejected. (2) Old `aggregate --run-plan` doesn't validate the
  parsed plan directly at all — it projects it via `to_aggregate_manifest
  (plan)`, which stamps `"aggregate_manifest_version": AGGREGATE_MANIFEST_
  VERSION` using **the old binary's own hardcoded constant, imported fresh
  at call time** — never read from or influenced by the run-plan.json's own
  `schema` field. The one place a version-rejection check *does* fire
  (`ExpectedTargets.from_manifest_data`) is therefore always checking the
  old reader's own version against itself, and can never observe a
  "too new" value regardless of what the source run-plan.json declares.
  **Consequence:** a genuinely version-skewed setup — an already-installed
  old `abicheck aggregate --run-plan` reading a `run-plan.json` a newer
  `project plan --gate-missing-required`/`--gate-unexpected-target` wrote —
  silently drops the requested gate policy and falls back to the old
  binary's hard-coded `fail`/`include` defaults, exactly the "silently wrong
  gate decision" class of bug the manifest-side `2.0` bump exists to
  prevent, just unreachable to prevent here. **Not fixable within the
  document's own shape**: every value in both dataclasses is consumed via a
  total, defaulting conversion, so no crafted field value can force old,
  already-shipped code to raise — this is a property of code that has
  already been released and cannot be retroactively patched, not a
  correctness gap in this PR's own new code. Closing it for real would need
  a mechanism outside the JSON document itself (e.g. a CI-level convention
  that `project plan` and `aggregate --run-plan` are always the same
  abicheck version/pinned together, enforced or documented at the tooling
  level) — out of scope for a schema-shape fix and not attempted here.

- Don't hand-edit `CHANGELOG.md`'s `## [Unreleased]` section directly — add a `changelog.d/` fragment instead (see Conventions above); CI enforces this
- Don't modify `examples/` test cases without understanding the ground truth they encode
- Don't add dependencies without strong justification (this is a lightweight tool)
- Don't skip test markers — if a test needs `castxml`, mark it `@pytest.mark.integration`
- Don't "fix" the mypy errors listed above by adding `# type: ignore` broadly
- Don't modify binary test fixtures without regenerating expected outputs
- Don't change public API signatures without checking for breaking changes
- Don't add platform-specific code without considering cross-platform compatibility
- Don't extend `IMPORT_CYCLE_ALLOWLIST` in `scripts/check_ai_readiness.py` to make a new cycle pass, and never as a routine step to unblock CI. The existing large CLI/service entry documents an accepted, by-design registration pattern (Click sibling commands registering back on `cli.main`) — a *new* member outside that documented pattern is very likely a real dependency-direction problem, not another instance of it. Prefer a function-local import or moving the shared logic to a leaf module both sides can depend on. If the coupling really is intentional, extending the allowlist needs an ADR (or explicit maintainer sign-off) recorded in the PR, the same bar as any other architectural exception — not a comment justifying it inline and moving on.
- Don't hand-duplicate a command, invariant, or count from this file into an adapter (`CLAUDE.md`, `.github/copilot-instructions.md`, `.cursor/rules/`) — point the adapter back here instead (see the table at the top of this file).
- Don't let time/effort estimates ("this would take too long", "quicker to just patch") drive a technical implementation decision — see "Decision-making principles" above.
- Don't ship a narrow, site-specific patch for a bug without first tracing it to its root cause and considering a generalized fix and generalized test — see "Decision-making principles" above.
