# Agent guide: module map

> Moved verbatim out of the root `AGENTS.md` (progressive disclosure: the root file is loaded into every agent session, this one only when its pointer fires). The root `AGENTS.md` remains the primary contract.

## Architecture — module map

Entry points:
- `abicheck/cli.py` — Click CLI **root only**: the root group, its
  `--version`/SIGTERM wiring, and the side-effect registration imports.
  ~120 lines, no product logic — ADR-061 Phase 4 moved shared runtime
  helpers and process-exit decisions to `abicheck/frontends/cli/`. Command
  callbacks live in their owning modules (`abicheck/frontends/cli/`, plus
  sibling `abicheck/cli_aggregate.py`, `cli_project.py`, `cli_stack.py`).
  Add a command to its owner, not here. It carried a
  lazy `__getattr__` alias table (`frontends/cli/moved.py`) keeping ~80
  private helpers importable from `abicheck.cli` after they moved; every
  caller now imports from the owner, so the table, the resolver and the
  assignment guard that protected it are deleted. **Import from the owner,
  and patch the owner** — `abicheck.cli` re-exports nothing. (This entry read
  "large file, at the 2000-line hard cap" long after that stopped being
  true — exactly the drift the "don't trust hard-coded line counts" warning
  below is about.)
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
   backends by `extract/semantic_ir_merge.py`. **Third slice landed:**
   `extract/semantic_normalizer.py`'s `normalize_header_ast` projects each
   header-AST backend's already-parsed `RecordType`/`EnumType`/typedef/
   `Function`/`Variable` output (all already carry a real `entity_id`, per
   Phase 2's option (a)) into a real `SemanticIR` — functions/variables
   reuse the same cross-backend spelling primitives `entity_id_for_function`/
   `resolve_function_identity` already apply
   (`model.signature_normalization.
   canonicalize_function_signature_param_type` per parameter,
   `name_classification.canonicalize_type_name` for the return/variable
   type), with `is_const`/`is_volatile` carried via `CanonicalEntity.
   cv_qualification`. Wired through `dumper_manifest.
   resolve_header_ast_result` (legacy single-TU ELF dump and a real
   manifest dump) and, via the new `extract/header_ast_fields.
   parse_header_ast_fields` choke point, `dumper.py`'s `_dump_pe`/
   `_dump_macho` too — every header-AST platform now populates
   `semantic_ir`, including through `--ast-frontend hybrid`'s
   reconciliation (`workflows/dump/hybrid_merge.py`'s own Mach-O mangled-name and
   ctor/dtor synthetic-key identity rewrites are propagated into
   `semantic_ir` via `_rewrite_semantic_ir_entity_ids`, so a hybrid merge
   never leaves one representation keyed under a retired identity another
   already moved past). **Fourth slice landed:** constants too — both
   backends already attach a real `entity_id` to every public constant
   (`parse_constant_entity_ids()`, Phase 2), so the identity half was
   already done; the normalizer projects `parse_constants()`'s raw value
   text verbatim as `canonical_spelling`, deliberately uncanonicalized
   (mirrors `diff_symbols._diff_constants`'s own long-standing raw-string
   `!=` comparison — there is no *observed* cross-backend value-spelling
   disagreement the way there is for a function/variable's type spelling,
   so inventing a canonicalizer here would be a heuristic in search of a
   bug, not a fix for one) — **except** clang's own compound-initializer
   value, which is a build-stable structural fingerprint
   (`dumper_clang_expr._expr_fingerprint`'s `"expr:" + sha256(...)[:16]`),
   not a spelling of the source text at all; that one case is `Fact.
   unsupported()` rather than a raw-value `Fact.present(...)`, matched by
   the exact fingerprint shape (not merely its `"expr:"` prefix, which
   would also misfire on a real castxml `expr::`-namespaced qualified
   name). A castxml-only value that resolves to its own opaque
   `FunctionType` tag (a direct function-pointer parameter/variable/return
   type castxml's resolver has no dedicated rendering for) gets the same
   `Fact.unsupported()` treatment. A clang-only lone boolean literal
   (`dumper_clang_expr._initializer_value` stringifies a captured Python
   `bool` via plain `str(...)`, spelling `"True"`/`"False"` — never the
   real `true`/`false` source text) gets the same treatment too, gated on
   `producer == "clang"`: `"True"`/`"False"` are otherwise legal C++
   identifier spellings a castxml `init` text could genuinely carry
   verbatim, so the exception applies only to clang's own artifact, not to
   every occurrence of those two strings. Both PDB and BTF/CTF now have
   their own Phase 6 slice landed, types only, each with function/variable
   identity explicitly out of scope. **PDB's own slice:**
   `extract/pdb_scope.py` parses CodeView's flat, already-`"::"`-qualified
   struct/class/union/enum names back into typed `ScopePath` segments (the
   reverse of DWARF's/the header-AST backends' own tree-walk construction
   — CodeView carries no parent-scope tree to walk), disambiguating an
   enclosing segment as a `Record` only when the accumulated prefix up to
   it is itself a name PDB separately recorded as a struct/class/union,
   defaulting to `Namespace` otherwise — an unverified heuristic (no MSVC
   toolchain in this environment to check it against). A named declaration
   nested inside a CodeView-synthesized anonymous scope (e.g.
   `N::<unnamed-tag>::Inner`) still reaches the model with its layout
   facts, but its `entity_id` is left unset (`extract/pdb_scope.py` builds
   no `Anonymous` scope segment at all). See ADR-063 Phase 6's PDB slice
   for the full account, including its documented, accepted limitations.
   **BTF/CTF's own slice:** `extract/debug_layout_semantic_ir.py` bridges
   the shared `DwarfMetadata` shape both formats reduce to
   (`BtfMetadata.to_dwarf_metadata`/`CtfMetadata.to_dwarf_metadata`) into
   transient, `entity_id`-bearing `RecordType`/`EnumType` objects — no
   scope heuristic needed at all (both are pure-C formats with no
   namespace/class nesting, so every `ScopePath` is unconditionally empty,
   unlike PDB's own unverified namespace-vs-record heuristic). Wired into
   the ELF headerless fallback path; deliberately leaves
   `AbiSnapshot.types`/`.enums` untouched for a BTF/CTF-sourced snapshot
   (only `semantic_ir` gains occurrences) — widening what other
   `.types`-consuming detectors see is a separate, larger design question
   this slice does not attempt. Function/variable/typedef identity remains
   unimplemented for both formats (neither's own richer parse carries that
   evidence across its own `to_dwarf_metadata()` conversion at all).
   `model/surface_facts.py` owns the three independent facts `Visibility`
   used to conflate — (a) a declaration exists in the headers this run
   parsed, (b) it belongs to the promised public contract for the run's
   scope/contract selection, (c) the artifact's export table carries a
   symbol for it — each a real `Fact[bool]` on `Function`/`Variable`
   (`declared_in_headers_fact`/`in_public_contract_fact`/
   `binary_exported_fact`, schema v46) so "unknown" stays representable and
   the `fact-detector-misuse` gate applies. **Never read those fields
   directly and never add a fourth merged boolean**: that module's
   accessors are the supported surface, and each names the question it
   answers (`in_public_surface`, `is_abi_visible`,
   `in_source_declaration_index`, `declaration_confirmed_absent`,
   `is_export_confirmed_absent`, `surface_fact_summary`). A pre-v46
   snapshot, or a hand-built `Function`, falls back to a `PARTIAL` reading
   derived from the legacy `visibility` value. Producers answer only what
   they observed (`extract/surface_fact_producers.py`); the scope-aware
   half of (b) is added by `provenance.tag_provenance`, and it only ever
   adds a positive. Reasoning: the `Visibility.PUBLIC` entry in
   `docs/contribute/known-gaps.md`.
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
   - `workflows/dump/dependency_scope.py` (with `dependency_retention.py`) — dependency exclusion, on by default (`dump`/
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
     (folded into the whole-snapshot disk cache key) is
     what lets `compare`'s own live-binary dumping filter consistently with
     a `dump` baseline instead of always producing the unfiltered surface.
     **That parameter, `InputSpec.include_dependencies`, and the CLI flag all
     default to `False`** (exclude toolchain/system declarations) — one
     default across every front end. `run_dump`/`InputSpec` defaulted to
     `True` until the defaults-alignment pass, which meant a typed-API caller
     omitting the field got the *unfiltered* surface while the identical CLI
     invocation got the filtered one (measured: 10 vs 5,597 functions on a
     one-header C++ library), and — since the two are not comparable — a CLI
     baseline could not be compared against a typed-API candidate at all
     (`scope_mismatch`, no verdict). Note there are **three** places that
     spell this default: the `InputSpec` field, `wrap_run_dump_with_
     dependency_scope`'s wrapper keyword, and the synthetic `__signature__`
     it builds for introspection; a test asserts the last two agree
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
     warning-and-skip (ADR-049 D8). Also parses the `versioning:` namespace
     (ADR-066 D4/S2) into `PolicyFile.versioning`/`versioning_stated`
   - `policy/versioning_policy.py` — ADR-066 D4/D5/S2: the typed
     `VersioningPolicy` model (`scheme`/`promise`/`support_window`/
     `deprecation_window`/`enforcement`; built-in default equals today's
     strict-SemVer, advisory-only behavior) resolved through the ADR-049 D7
     resolver (`compatibility_evaluation_wiring.resolve_versioning_policy`,
     `CompatibilityEvaluationConfig.versioning`); `evaluate_release_acceptance`
     (an orthogonal *acceptance* fact `abicheck/semver.py`'s
     `recommend_release(..., versioning_policy=...)` attaches as
     `ReleaseRecommendation.policy_acceptance` — never changes `bump`/
     `soname`/`state`); `evaluate_deprecation_compliance` (a
     history-report-only fact over `workflows/history.py`'s
     `LongitudinalHistoryResult.deprecation_compliance`, never read by any
     pairwise `compare()` verdict)
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
     the legacy alias — `.abicheck.yml`'s `scope.public` or the typed API's
     `scope_public`; the CLI flag pair was deleted in one-comparison-product
     Phase 9b),
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
     typed (`--policy` carries a non-`None` click default), a typed `CompareRequest`, and the project's `.abicheck.yml`.
     This phase's gate is an executable check in the test suite
     (`tests/_cross_front_end.py`'s `cross_front_end_differences()`):
     equivalent CLI and API input must resolve equally, modulo only which
     front end stated a value. Resolution only — it changes no verdict,
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
     compare config (`gate.severity.*` — `gate.exit_code_scheme` was a
     pack-assignable field here too until CLI cleanup phase two PR G2
     deleted it along with `--exit-code-scheme`; a pack asserting it is
     now rejected at load time, ADR-064). Ordering
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
     takes all three kinds (`scan --against`, which also took all three,
     was deleted by ADR-068 Phase 6). The directory/package
     release fan-out (`cli_compare_release.py`) takes all three kinds too,
     since CLI cleanup phase two's "PR B" slice 2 — a `kind: gate` pack's
     `gate.severity.*` folds into the fan-out's own
     resolved `GateOptions` (`policy.release_gate_options.
     resolve_release_gate_options`, which ADR-064 landed 2026-09-02 —
     closing the "no `GateOptions`-shaped object of its own" gap this note
     used to describe) via `apply_release_gate_pack`, called once before
     every downstream consumer reads the result. The fold *rule* both sides
     apply is shared, not mirrored, since track T6 (2026-09-05):
     `policy/gate_pack_fold.py`'s `fold_gate_pack_severity` owns it and both
     `apply_release_gate_pack` and `pack_application.
     apply_to_compare_config` call it — a leaf inward of both, which is what
     lets them share without `policy` importing the flat-root
     `pack_application` (`release_gate_options.py`'s `_GatePackApplication`
     `Protocol` exists for that same rule). The same module owns the one
     `gate_exit_code_scheme` derivation every object that publishes a scheme
     now uses, which is why `GateOptions.exit_code_scheme` and
     `ResolvedCompareConfig.exit_code_scheme` are derived properties rather
     than settable fields. What remains open (see that plan section for the
     exact scope): the two callers still fold onto *different shapes* — four
     raw optional strings before any `SeverityConfig` exists vs. an
     already-resolved one — since the release fan-out still has no
     `ResolvedCompareConfig`-shaped object of its own; deferred to the
     duplication-and-convergence-assessment plan's own P0
     `EffectiveGate`/`EffectiveEvaluationConfig` target, not ADR-064's own
     `GateOptions` rewrite (already landed) or its PR G2 (a different,
     unrelated deferred item — ADR-063 Track 4's 7B ledger entry has the
     full account). PR B's
     other stated goal, the effective-config digest, has already landed for
     the native compare/release JSON path, and the `--stat` JSON summary -- non-JSON renderers (Markdown, review, SARIF,
     JUnit, HTML) don't carry it, see that plan
     section's own PR B note for the exact scope). One review finding
     worth not rediscovering (historical — `--exit-code-scheme` itself is
     gone, but the underlying "read, don't re-derive" principle still
     governs the surviving `gate.severity.*` fold): the gate application
     must *read* the resolved gate config rather than re-derive one
     (re-deriving used to let a severity-only gate pack silently override
     an explicit `--exit-code-scheme legacy`, back when that flag
     existed). Manifest validity is checked ahead of `compare`'s
     `--dry-run` emit — but pack-vs-pack conflict detection is not, since
     D8 exempts a field another layer states and those layers aren't
     resolved that early
   - `contract_evaluation.py` — ADR-049's contract-relevance evaluator: one
     `ContractEvaluationDecision` (relevance + stable reason code +
     assurance) per already-emitted finding. Computed on every comparison
     since ADR-049 Phase 7's default flip; `--contract public|exports|all`
     names the domain (Phase 6), while `auto` or no flag lets D7's lower
     tiers decide and, when none states one, the evidence-adaptive default
     (`policy/contract_default_mode.py`) picks `public` if header evidence
     closes on every side, else `exports`, else `all` -- an unstated
     `public` also consults observed exports for a finding the headers make
     no commitment about, so an undeclared export's removal still gates. Under `auto` the domain follows the legacy
     alias, `.abicheck.yml`'s `scope.public` (the CLI flag pair was deleted in
     one-comparison-product Phase 9b), and an
     explicit value outranks that legacy alias via `compatibility_evaluation_wiring.resolve_legacy_contract_mode`
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
     exit code itself; `model/contract_finding_relevance.py` is the leaf predicate
     `checker._compute_verdict_for` and `severity.compute_exit_code`/
     `compute_gate_decision` share so the verdict and the gate cannot exclude
     different sets. An **unstamped** finding is evaluated (a direct
     `checker.compare(..., contract_evaluation=False)` call stamps none)
   - `contract_evidence_collect.py` — ADR-049 Phase 3's *observed provider
     ledger* (plan §4.1) and the raw type graph Phase 4 persists. Produces
     one `EvidenceSearchRecord` per (provider, side) — `public_header`,
     `export_table`, plus the `post_manifest`/`forced_public_symbols`
     overlays when a run configures them — each with its own status,
     completeness, identity coverage, requested-vs-searched scope and
     content digest, so a provider failure stays scoped to its own domain.
     Also owns the node encoding of `TypeGraphSnapshot` (canonical nodes
     are the Phase 1 `model/graph_entity_identity.py` ids since
     `contract_evidence` schema 2, with `name:`/`alias:` spelling tiers; a
     schema-1 `decl:`/`record:` graph is still read as written, never
     remapped) and the closure walk over it, and maps
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
     exit and forget the orthogonal one; the directory/package release
     fan-out `max`s the same function across members, since a ledger gating
     one path and not the other is exactly §6.4's cross-command divergence. `contract.unresolved=warn`
     (D9) zeroes the floor and changes nothing else — the failures stay
     listed and unsuppressible, because accepting incomplete assurance is
     not hiding it. `reporter.py` emits *this* function's answer as
     `contract_coverage_exit_contribution`, so the number a user reads is
     the one that gated them. `0` whenever no contract context exists (evaluation
     explicitly disabled); since Phase 7 every CLI run has one, and the
     evidence-adaptive default picks a domain its evidence closes
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
5b. **Release product model (one contract, many providers)** — **every**
   comparison whose candidate is a set of artifacts judges **one** public
   contract backed by **several** binary providers, not the Cartesian
   product of the two.
   `model/release_surface.py` owns the acquisition identity
   (`SurfaceAcquisitionIdentity`: every input that can change the header
   AST, folded into one key) and the acquired surface itself
   (`ReleasePublicSurface` — the declarations that owe an export, the
   symbols the headers declare, and whether the acquisition resolved at
   all; an `unresolved_surface` is never an empty one).
   `workflows/release_surface_acquisition.py` acquires it **once per
   acquisition key** through a counted, thread-safe ledger, so an ordinary
   directory comparison performs one acquisition per side however many
   members it has — and two members whose request genuinely differs key
   apart instead of being forced onto one snapshot.
   `compare/bundle_export_index.py` indexes `exported symbol -> exporting
   member(s)` using the *same* `model.export_index.default_versioned_names`
   projection the single-artifact check uses.
   `policy/release_contract_reconciliation.py` reconciles the contract
   against the union of those exports and folds OLD/NEW into one
   evolution-stated finding set. `workflows/release_public_surface.py`
   orchestrates the stage; `report/release_public_surface.py` owns the
   report section and the shared-finding fold.
   **Three drivers, one model.** `workflows/release_public_surface.py`'s
   `reconcile_member_sets` is the driver-agnostic core and
   `member_pass_scope` the one place the ">1 member" rule lives, so a new
   multi-member driver wires to those rather than restating either: the
   live directory/package fan-out (`cli_compare_release*.py`), a stored
   `BundleFacts` baseline against a live release
   (`stored_old_live_new_reconciliation`), and two stored documents
   (`workflows/bundle_stored_pair_compare.py`). Only the live path
   *acquires* a surface; a stored side uses the contract its capture
   recorded (`BundleFacts.public_surface`, bundle-facts schema 4), falling
   back to one derived from its member snapshots for a pre-v4 document. A
   side with no header evidence records **no contract** and never borrows
   the other side's — doing so asserts that NEW still promises everything
   OLD did, which turns every deliberately retired declaration into a
   missing export.

   **What each side retains** is a separate, per-consumer question owned by
   `workflows/release_snapshot_retention.py`: a release fan-out holds every
   matched member's result until its folds run, so whatever a member entry
   keeps is multiplied by the member count. JUnit and `--bundle-facts-out`
   read the **OLD** side only, so `SnapshotRetention` keeps the full
   `AbiSnapshot` there and the compact `BundleSignatureEvidence` on NEW —
   never one shared "some output needs snapshots" switch, which is what
   pinned a NEW graph nobody opened. Adding a consumer means naming it
   there; `tests/test_release_snapshot_retention.py` re-derives the
   inventory from the real call sites so the claim cannot go stale.

   **Two rules not to relearn:** only `public_not_exported` moves off the
   member pass (`workflows/crosscheck_ownership.py`'s run-scoped
   ownership) — `exported_not_public` stays per member because the
   exporting member *is* its attribution; and an unread member makes the
   reconciliation *incomplete* (obligations recorded as unresolved, coverage
   warning) rather than turning an absent symbol into a missing export.
   Reference: `docs/learn/products-not-libraries.md` § "One public surface,
   many providers".

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
     `save_snapshot`/`write_snapshot` — the public compatibility surface).
     A thin, delegation-only facade (ADR-061 gap E, closure package 6): the
     real codec lives in `storage/snapshot_codec.py` and its siblings
     (`storage/snapshot_schema_versions.py`, `storage/snapshot_encode.py`,
     `storage/snapshot_decode_declarations.py`,
     `storage/snapshot_reliability_flags.py`, split purely to keep each
     file under the ADR-061 new-file line ceiling). This facade itself
     stays permanently unclassified (`public_root_surfaces`) — it is the
     one legal route through which `workflows.snapshot_load.
     backfill_python_ext_from_evidence` and `policy.
     analysis_assurance_degraded_facts.degraded_reliability_facts` run
     between the storage codec's `decode_snapshot`/`finalize_snapshot`,
     neither of which a `storage`-classified module (`may_import: [model]`
     only) may call
   - `workflows/memory_trace.py` — optional, attributable memory instrumentation
     (`ABICHECK_MEMORY_TRACE=<path>`): parent RSS, process-tree RSS *and*
     PSS, cgroup `memory.current`/`peak`, and structural retention counts,
     each recorded as its **own** field per phase, with an unavailable probe
     recorded as `null` rather than `0`. Python allocation totals are a
     separate opt-in (`ABICHECK_MEMORY_TRACE_TRACEMALLOC`) because
     tracemalloc perturbs the time and RSS it would otherwise sit beside.
     A near-leaf (only `process_resources.py`, for the cgroup walk, and
     `storage/ast_size_observer.py`, whose AST-intake hook it installs so
     the `extract`/`storage` parse sites can report `ast.intake:start`/
     `:done` without importing `workflows`); one boolean test per call site
     when off, which is the default. Every sample also carries
     `parent_rss_peak_bytes` (`VmHWM`), so a transient peak between two
     samples is still visible. The four figures are four *different*
     numbers — conflating them is how a memory investigation reaches the
     wrong owner — and `docs/contribute/memory.md` is the reader's page.
     `scripts/bench_release_memory.py` is the benchmark harness that
     consumes it, deliberately runnable at a pre-instrumentation revision
     so a before/after pair is one harness over one fixture
   - `storage/json_stream.py` — fragment-at-a-time `json.dumps(obj,
     indent=2)` with `LazyItems`, a member map whose values are produced
     while being encoded and dropped immediately after. What
     `--bundle-facts-out`'s JSON path writes through
     (`snapshot_io.write_snapshot_text_stream`, which streams an
     *uncompressed* write through the same atomic writer — generalised to
     take chunks, not duplicated — and joins-and-delegates a compressed
     one). Byte-identical to the eager spelling, tested differentially
     against `json.dumps` itself; a subtree below
     `_DELEGATE_NODE_LIMIT` is handed to the C encoder whole, because the
     memory saving comes from the *member* boundary and descending small
     objects in Python is pure time
   - `storage/derived_ast.py` — the rule letting the *final* consumer of a
     clang AST be handed its **location** instead of its contents, when a
     cheap derived form is all it wants (`derived_ast_scope` opens the
     offer, `offer_derived_ast_source` makes it once per place an AST
     becomes available, `DerivedAstArtifact.used` is the discriminator —
     never a type check on the opaque marker). A callback, so `storage`
     never imports the layers that own those derived types. Both a warm
     cache entry and a freshly written document answer to it, which is what
     makes a *cold* header-graph attach cheap and not only a warm one
   - `snapshot_io.py` — ADR-059's canonical snapshot *storage envelope* I/O:
     plain/gzip/zstd detection (magic bytes), atomic + deterministic
     compressed writes, decompression-bomb limits. A dependency-free leaf
     module `serialization.py`/`snapshot_cache.py`/CLI code build on it
   - `extract/cache_header_scan.py` — the include-tree walk every
     header-parse cache key folds in (`dumper_ast_config._cache_key`,
     `snapshot_cache`). Its **ordering is part of the cache key**:
     component-wise, matching `sorted(directory.rglob("*"))`, since consumers
     hash the paths in the order returned. `header_utils.py` still owns the
     suffix vocabulary (`CACHE_HEADER_SUFFIXES`) it filters on; the traversal
     lives here because reading the filesystem is `extract`'s job
   - `package.py` — package/archive handling
   - `debian_symbols.py` — Debian symbols file adapter
   - `environment_matrix.py` — multi-env comparison
   - `binder.py` — symbol binding logic
   - `resolver.py` — symbol resolution
   - `type_metadata.py`, `dwarf_utils.py` — shared type helpers
   - `change_registry.py` — change kind registry
   - `service.py` — service layer (Python API). One typed request/result pair:
     `CompareRequest` -> `CompareResult` and `DumpRequest`. ADR-068 Phase 4's
     typed-API slice retired `ScanRequest`/`ScanResult` (and `run_scan`/
     `run_scan_set`) — `service_scan.py` was then deleted with `scan`
     itself (ADR-068 Phase 6); the surviving dry-run cost model is
     `dry_run_estimate.estimate_scan`
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
     shared pipeline `compare`'s implicit-dump operand already uses, with the legacy `-p`/`--compile-db`
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

11. **Report-only publication (ADR-073)** — `actions/report` publishes an
   already-produced canonical JSON report to a pull request; `actions/
   verify-source-run` selects and unpacks the producer run a trusted
   `workflow_run` publisher reports on. Neither analyses anything, which is
   what makes them safe in a privileged job, and everything either one
   *decides* lives in `abicheck/frontends/action/` (`report_publication.py`,
   `run_selection.py`, `cli.py`) so it is testable with no credentials. See
   `abicheck/frontends/AGENTS.md`'s own `action/` section and
   `docs/use/fork-pr-reporting.md`.

Beyond the core package: `.github/AGENTS.md` (CI/workflow architecture),
`action/AGENTS.md` (the composite GitHub Action's shell-script layer), and
`contrib/abicheck-clang-plugin/AGENTS.md` (the optional Clang facts plugin)
cover the surrounding first-party trees this file doesn't detail.


## Adding a new ChangeKind — full rationale

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

