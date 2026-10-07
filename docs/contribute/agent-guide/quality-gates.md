# Agent guide: quality gates

> Moved verbatim out of the root `AGENTS.md` (progressive disclosure: the root file is loaded into every agent session, this one only when its pointer fires). The root `AGENTS.md` remains the primary contract.

## AI-readiness gate

`scripts/check_ai_readiness.py` runs in CI as a fast structural gate (every gate script's full description: [`scripts-inventory.md`](scripts-inventory.md)). It checks:

| Check | Severity | What it enforces |
|-------|----------|------------------|
| `file-size` | ERROR > 2000 lines, WARN > 1500 | Every first-party Python tree (`abicheck/`, `scripts/`, `tests/`, `skills-src/evaluation/field/`, `skills-src/evaluation/validation/`, `action/`, the clang plugin's `tests/` — `FIRST_PARTY_PY_ROOTS`) stays legible. `LARGE_FILE_ALLOWLIST` downgrades a specific pre-existing violator to WARN with a reviewed reason — it is not a way to silently exempt a new file |
| `claude-md-coverage` | ERROR | `CLAUDE.md` exists in each original major sub-tree (`REQUIRED_CLAUDE_MD_DIRS`, which now also covers `skills-src/`) |
| `agent-instructions-coverage` | ERROR | `AGENTS.md` or `CLAUDE.md` exists in `.github/`, `action/`, `contrib/abicheck-clang-plugin/` (`REQUIRED_AGENT_INSTRUCTION_DIRS`) |
| `script-inventory` | WARN | Every `scripts/*.py` is named in `scripts/CLAUDE.md`'s inventory table — an unlisted script is invisible to that discovery path |
| `generated-file-ownership` | ERROR | A known-generated file (`GENERATED_FILE_MARKERS`, plus every `docs/reference/examples/case*.md`, plus every `*.md` under the three generated agent-skill trees — `.agents/skills/`, `.claude/skills/`, `.gemini/skills/` — scoped to the skill directories `scripts/gen_agent_skills.py` actually owns, so a hand-authored skill sharing an output root is not flagged) still carries its "this is generated, don't hand-edit" marker comment |
| `test-ratio` | WARN | At least 20% test-to-source file ratio; test files are discovered recursively under `tests/` (not just top-level) |
| `future-annotations` | WARN | `from __future__ import annotations` per this file's convention |
| `changekind-partition` | ERROR | Every `ChangeKind` is in exactly one of `BREAKING_KINDS` / `API_BREAK_KINDS` / `COMPATIBLE_KINDS` / `RISK_KINDS` |
| `abi-taxonomy-coverage` | ERROR | Every leaf mechanism in `docs/contribute/abi-api-failure-taxonomy.md` (88 today) carries exactly one Phase 3 coverage status in `docs/_meta/abi-taxonomy-coverage.json` — the taxonomy counterpart of `changekind-partition`, so a leaf added without being classified can't silently drop out of the coverage denominator. Also validates that every learn page/topic/catalog case/`ChangeKind` the mapping names exists, that a non-`COVERED` status states a reason, that a `KNOWN_UNDETECTABLE`/`NOT_IMPLEMENTED` leaf's cross-reference really appears in `docs/learn/limitations.md`/`docs/contribute/known-gaps.md`, and that the recorded status agrees with its own Phase 2 columns under the plan's first-match-wins order. Rules live in `scripts/abi_taxonomy_coverage.py`, which `scripts/gen_abi_taxonomy_coverage.py` imports too, so the gate and the generated report (`docs/contribute/abi-taxonomy-coverage.md`) can't disagree |
| `changekind-detector` | WARN | Every `ChangeKind` is produced somewhere (not orphaned) |
| `changekind-docs` | WARN | Every `ChangeKind` is mentioned in `docs/` |
| `doc-count-sync` | ERROR on drift, WARN if anchor moved | Headline counts in docs (ChangeKind count, example-catalog size) match their source of truth (`len(ChangeKind)`, `ground_truth.json`) — this file (`AGENTS.md`) is included in the generic sweep, same as `README.md`/`CLAUDE.md` |
| `cli-contract` | ERROR | No *unallowlisted* front-end `cli*.py`/`appcompat.py` module calls a Tier-1 core entry point (`checker.compare`, `dumper.dump`, `service.resolve_input`) directly — it must route through the Tier-2 service (`service.run_compare`/`compare_snapshots`, `service.run_dump`/`service_dump_pipeline.run_dump_request`, `service_input_resolution.resolve_side_snapshot`); ADR-037 D10.1, extended to the latter two per Phase 0 item 2 of `docs/contribute/plans/duplication-and-convergence-assessment.md`. `CLI_CONTRACT_ALLOWLIST` in `scripts/check_ai_readiness.py` held the reviewed, line-pinned legacy exceptions; the last ones left with the ABICC `compat` front end, so it is empty and any direct call fails |
| `engine-cli-boundary` | ERROR | No engine-layer module (`service*.py`, `artifact_*.py`, `buildsource/**/*.py`, `workflows/artifact/**/*.py`) imports `click` or a `cli_*` sibling — the CLI is a frontend adapter over the engine, not the reverse. `ENGINE_CLI_BOUNDARY_ALLOWLIST` (`scripts/engine_cli_boundary.py`) is now empty — every pre-existing inversion was closed (Phase 1 of `docs/contribute/plans/duplication-and-convergence-assessment.md`, the rest deleted with `scan`) — so any engine-layer `click`/`cli_*` import fails outright |
| `fact-detector-misuse` | ERROR | ADR-063 Phase 0 (`docs/contribute/plans/one-semantic-pipeline.md`): no direct `==`/`!=` comparison of a `Fact[T]`-typed value (a `<attr>_fact` field access, or a `Fact(...)`/`Fact.<classmethod>(...)` constructor call) anywhere under `abicheck/` — a detector must unwrap via `.status` first, never compare two `Fact[...]`s (or a `Fact[...]` against a bare value) directly, since `Fact[T]` deliberately doesn't override `__eq__` and a direct comparison silently falls back to structural dataclass equality over `status`/`value`/`diagnostics` together. Real, repo-wide AST scan (`scripts/fact_detector_misuse.py` + `fact_detector_misuse_aliases.py`/`fact_detector_misuse_scope.py`), resolving same-function local aliases, annotated parameters, constructor-classmethod aliases, and closure-scope shadowing — not a naive textual match. No baseline: any match is an unconditional error |
| `fact-field-readers` | ERROR | ADR-063 Phase 0 (`docs/contribute/plans/one-semantic-pipeline.md`): no function outside `EXEMPT_FUNCTIONS` reads a `Fact[T]`-bridged legacy field (`RecordType.bases`/`virtual_bases`/`vtable`/`vptr_offset_bits`, `Param.is_va_list`) directly — via a plain attribute access, a `getattr(obj, "name", ...)` call (including a resolved `getattr`/`builtins` alias, excluding one locally shadowed by a parameter), an `operator.attrgetter(...)` call or a bound/unbound `__getattribute__` call (each through a resolved alias too), an `ast.AugAssign` target (`rec.bases += x`, an implicit read before the write), or a `case RecordType(bases=[]):` structural-pattern match (keyword or positional) — without first consulting its `Fact[...]` sibling's `.status`, which would collapse "confirmed empty/false" and "no evidence" onto the same value. Real, repo-wide AST scan, not a `diff_*.py` glob. `KNOWN_UNMIGRATED_READERS` records every currently-known reader site the same allowlist-and-shrink way `IMPORT_CYCLE_ALLOWLIST` does, keyed by enclosing function, attribute, the read's own outermost containing expression, its own exact source text, and a per-site occurrence rank — a new, unlisted site fails outright |
| `import-cycle-growth` | ERROR | No *unapproved* strongly-connected-component growth within `abicheck/` — not literally "no import cycles": a large, deliberately-baselined CLI-registration SCC already exists and is allowed (`IMPORT_CYCLE_ALLOWLIST`). The invariant is that no *new* module joins it and no *new* separate SCC forms; extending the allowlist to unblock a fresh cycle needs an ADR or explicit architectural sign-off, not a routine edit (CLAUDE.md "M1-3") |
| `mypy-baseline` | ERROR if drifted up | mypy error count ≤ documented baseline |
| `mypy-override-targets` | ERROR | Every first-party (`abicheck.*`) `[[tool.mypy.overrides]]` target still resolves — an exact target naming neither a module nor a package, or a wildcard matching nothing, fails. mypy silently ignores an override whose target does not exist, so a deleted module otherwise leaves its override *and the comment explaining it* behind with no signal anywhere (eleven such entries had accumulated when this gate was added). Module resolution and wildcard matching follow mypy's own rules — stub-only (`.pyi`) modules count, a PEP 420 namespace directory containing a module is a module, and a `*` component matches *zero* or more components (ported from `mypy.options.Options.compile_glob`, not approximated with `fnmatch`). Third-party targets are out of scope: whether `yaml` or `click` resolves depends on the environment mypy runs in, not the tree. Lives in `scripts/mypy_override_targets.py`, a sibling leaf module, since `check_ai_readiness.py` is already past the 2000-line hard cap |
| `examples-ground-truth` | ERROR | Every `examples/case*/` has a `README.md` and an entry in `ground_truth.json` |
| `examples-readme-sync` | ERROR | `examples/README.md` headline count, verdict distribution, and case-index rows match `ground_truth.json` (catches missing/stale catalog rows) |
| `mkdocs-nav-coverage` | WARN | Every `docs/**/*.md` is in `mkdocs.yml` nav or linked from another doc |
| `adr-index-nav-sync` | ERROR | Every `docs/contribute/adr/*.md` is linked from `adr/index.md`, and the ADR index page itself (not each individual ADR — relaxed, since that overloaded top-level nav with 50+ flat entries for no reader benefit) is listed in `mkdocs.yml`'s nav, so every ADR stays reachable from published navigation (this is what originally caught ADR-041 going missing from nav despite being accepted). Also requires every ADR to carry a Status metadata line/heading, and an ADR whose status leads with "Superseded" to link to its replacement |
| `adr-status-sync` | ERROR on contradiction / bad receipt, WARN on staleness | An ADR's own `**Status:**` line and its row in `adr/index.md` may not *contradict* each other — one claiming nothing is implemented while the other claims something is (how ADR-056's row went stale), or disagreeing on the decision word. Paraphrase is explicitly allowed: the index cell is an abridgement, and a stricter prototype flagged 15 of 56 ADRs, nearly all false positives. Separately validates the optional `**Verified:** <ref>@<sha> on <YYYY-MM-DD>` receipt (see `adr/index.md`'s convention section): exactly one per ADR, well-formed, a real non-future date, and naming a commit reachable from the default branch — a receipt anchored to the branch that adds it vanishes on merge and then fails this required job on `main` permanently. It then WARNs when commits after that sha touched a first-party file the Status paragraph names, which is the only mechanism here that catches *document-vs-code* drift (ADR-049's status claimed its evaluator was unwired for five merged PRs after it wasn't). **A file is watched only when the Status names it by full repo-relative path** (any `FIRST_PARTY_PY_ROOTS` tree, not just `abicheck/`); a bare `x.py` is accepted only when it resolves to `abicheck/x.py`, and family shorthand (`_resolver.py`) is deliberately not guessed at — see `adr/index.md` for why. Lives in `scripts/adr_status_sync.py`, a sibling leaf module, since `check_ai_readiness.py` is already past the 2000-line hard cap |
| `perf-antipatterns` | ERROR on growth, WARN on a stale baseline | No *new* performance anti-pattern inside a loop or comprehension under `abicheck/`: membership/`index`/`count` on a function-local list, `re.compile`, `json.loads`/`deepcopy`, self-copying accumulation (`acc = acc + [...]`, `[*acc, x]`, `{**acc, ...}`), a `subprocess` call, re-sorting or copying a loop-invariant collection, `str +=` concatenation, quadratic self-dedup (`[x for i, x in enumerate(s) if s.index(x) == i]`, `x not in s[:i]`; `s` a name or attribute chain such as `self.s`), `pickle.loads`/`model_copy(deep=True)` per item, or blocking network I/O / `time.sleep` per iteration (error paths inside `raise` are exempt). Every site in the tree has been triaged: fixed, or exempted where it stands by a `# perf-ok: <reason>` comment (trailing the statement, or on its own line above it; no reason, no exemption) -- for shapes that are the work itself, such as one `json.loads` per JSON-lines record or `deepcopy` inside `__deepcopy__`. A string a loop body rebinds unconditionally each iteration, and a module-scope comprehension (runs once, at import), are not flagged. `scripts/perf_antipatterns_baseline.json` (per-(file, function, rule) counts) is empty and stays the ratchet for untriaged debt: a count above it is an error, one below a warning to shrink it. Lives in `scripts/perf_antipatterns.py` |
| `banned-imports` | ERROR | No `print(...)` outside CLI/reporter modules; no `subprocess(..., shell=True)` |
| `project-snapshot-dto-no-asdict` | ERROR | No `dataclasses.asdict()`/`asdict()` call in a `ProjectSnapshot` DTO file (`abicheck/storage/dto.py`, `abicheck/storage/import_v1.py`, `abicheck/project_snapshot_store.py`, `abicheck/storage/semantic_ir_codec.py`) — ADR-063 Phase 8's D8 constraint, made mechanical |
| `test-change-symbol-typed` | ERROR | No `Change(..., symbol=None)` or `make_change(symbol=None)` anywhere under `tests/`. `mypy` runs over `abicheck/` only, so a fixture could construct a `Change` in a state its own annotation (`symbol: str`) forbids and nothing would say so — which is how a `known-gaps.md` entry came to record a production defect that did not exist, from a `None` a test had fabricated. Typechecking the (unannotated) suite is not an available alternative; this is the narrow structural stand-in |
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
  that gated nothing from exiting 0, and an unresolved run (suspicious/no-tests/segfault)
  is a *failed measurement*, not zero survivors. A `timeout` counts as detected (as in
  PIT/Stryker): every timeout the first complete run produced was a mutation that made
  the code loop forever, and timeouts are still reported separately. **`mutation-baseline.json` is not
  recorded yet**; until it is committed, the PR lane gates only changed functions and
  warns `Mutation drift not checked` (failing every detector-test PR in two minutes
  measured nothing), and the weekly run records the baseline instead of drifting
  against it. **Run cost is scoped to what a gate reads:** with no baseline, a PR
  run executes only the changed functions' mutants (`scripts/mutation_scope.py`), and a
  full-population run is split across 12 shard jobs by *function* (`mutation_scope.
  shard_assignment`; module-level shards could not fit `diff_platform.py` under the job
  ceiling), and a weekly run with no committed
  baseline records one (artifact `mutation-baseline`) instead of failing. Per-lane trigger detail lives in
  `.github/AGENTS.md`'s workflow table rather than being copied here.
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
- **A differential test must prove both of its configurations actually ran.** A
  test whose claim is "configuration A and configuration B agree" (pruning
  off vs. on, one backend vs. another, cache cold vs. warm) asserts nothing
  if B was served A's cached result — it then compares A with itself and
  passes no matter what B would have done. This is not hypothetical: both
  double-`dump()` tests in `tests/test_clang_header_backend_integration.py`
  derived their "fresh" AST-cache root from the same `tmp_path`, so the
  second run hit the first's on-disk cache, the streaming pruner never
  parsed anything, and the equivalence and method-count assertions were
  vacuously true. A *sibling* test proving the mechanism can engage on the
  same repro does not repair this — it says nothing about whether this
  comparison engaged it. So: give each configuration a genuinely distinct
  cache root (and clear any in-process memo alongside it — a disk-cache miss
  alone does not force a reparse), and assert **within the same test** that
  the path under comparison executed, by observing the mechanism rather than
  its output (`_PruneSpy` there wraps the real loader and records its call
  count and reported prune count). The same rule applies before sharing an
  expensive fixture between two configurations: sharing immutable *inputs* is
  fine, sharing the *output* whose equivalence is the claim is the bug above.
- **An autouse fixture's cost is charged to every test, so its allocation
  must be O(1).** `tests/conftest.py`'s `_isolate_snapshot_cache` is autouse;
  it originally allocated a pytest *numbered* directory, which enumerates
  every existing sibling to choose the next number — so a worker that had run
  N tests paid a scan of N entries to start test N+1, roughly quadratic over
  a session, charged even to tests that only compare two enum values. Measured
  two ways: the allocator alone costs 5.8ms vs. 0.07ms per call against a
  directory holding 8000 siblings, and end to end a 1107-test subset runs in
  1.7-2.2s instead of 2.3-2.9s (0/8000/20000 pre-existing siblings) — the
  allocator's own margin is larger than the end-to-end one, so read the
  end-to-end figure as the claim. It now uses `tempfile.mkdtemp` inside
  pytest's own base temp dir: the same guarantee (a distinct, empty,
  test-owned directory, under pytest's retention policy) from an atomic
  random name. The isolation itself is **not** the thing to economize on —
  `tests/test_conftest_cache_isolation.py` states that contract as
  invariants specifically so the next round of "make this faster" cannot
  reach for a shared session-wide cache directory, whose cross-test cache
  hits would surface as unrelated mystery failures.
- **A matrix test needs an oracle, not just a type check.** A parametrized
  sweep asserting only that the result is *one of* the valid enum members
  pins nothing: `TestExhaustiveMatrix` in
  `tests/test_policy_override_matrix.py` emitted 1,608 such cases that an
  implementation returning `COMPATIBLE` for every input — and one returning
  `BREAKING` for every input — both passed in full (verified by
  substitution). Nor is a test that asserts `A & B ⊆ A` about policy; it is
  set algebra, true for any two sets including two wrong ones (four such
  tests were removed). Write the expectation as an *independent second
  derivation* of the documented behavior (`_expected_verdict` there derives
  from the intrinsic `*_KINDS` partitions and the two named downgrade sets,
  deliberately **not** from `policy_kind_sets`, which is what the function
  under test folds over), batch the sweep so a failure names every
  disagreeing case at once, and add a vacuity guard on the oracle itself —
  an oracle accidentally reduced to a constant makes the whole matrix pass
  while asserting nothing, which is the original failure in a new place.
- **Don't re-run the whole repository to test argument dispatch.** A check
  that scans every first-party file has exactly one owner — for the
  readiness gate, the dedicated `ai-readiness` CI job, which runs
  `verify.py --profile pr --only ai-readiness` with no skips. A unit test
  that drove nearly that whole registry over the live tree in order to
  assert `main()` returns 0 cost ~5m in a measured run and added no signal
  the owning job did not already have. `tests/test_ai_readiness_main_dispatch.py`
  keeps `main()`'s own contract (selection, `--only`/`--skip` composition,
  error-vs-warning exit codes, JSON agreeing with the human report) against
  the *real* `main()` over a small instrumented registry, and each check's
  own live-tree test stays in `tests/test_ai_readiness.py`, where it belongs.

  Two sibling live-tree assertions were examined and deliberately **kept**
  in the unit lane: `test_fact_detector_misuse.py`'s
  `test_no_violation_in_real_repo` and `test_fact_field_readers.py`'s
  `test_no_unlisted_violation_in_real_repo`. Unlike the `main()` case they
  assert something substantive (the tree is clean; the baseline holds no
  stale entry), so relocating them would stop a contributor learning
  locally that they introduced a violation. Their cost was attacked at its
  cause instead — see the next bullet. Note also what measurement ruled
  out: sharing one parsed-AST inventory across the eleven test modules that
  each walk `abicheck/**/*.py` sounds like the win and is not. Reading and
  parsing all 787 files costs ~1.1s in total against ~43s for those two
  tests — the scan logic dominates by roughly 40x, so a shared parse would
  have bought ~2s of 43s.
- **When a live-tree gate is slow, profile it before relocating the test
  that runs it.** `fact-detector-misuse`'s scan took 25.5s over 787 files,
  and profiling said why: `fact_equality_misuse_sites` computes
  `_def_containing_qualnames`, `_locally_bound_constructor_shadow_names`
  and `_lexical_function_parents`, then calls `_fact_aliases`, which
  computes all three again — four, two and two full `ast.walk`s per file,
  and the bulk of the scan's 13.7M `walk` calls. `_memoize_per_tree` in
  `scripts/fact_detector_misuse_scope.py` caches each on the tree node's
  own `__dict__` (not a module-level `id(tree)` dict, which would leak for
  the life of the process and could serve a stale entry once an address is
  reused), taking the scan to 19.0s with **byte-identical** output across
  all 787 files. Prefer that to moving or deleting the test: the unit lane
  and the `ai-readiness` job both get faster, no coverage moves, and no
  lane-policy decision is needed. Two conditions make such a cache safe and
  both were checked rather than assumed — every decorated helper is pure,
  and no caller mutates a returned mapping. One caution, recorded because
  it caught a real gap in the first version of the accompanying test:
  `tests/test_fact_detector_misuse_memoization.py` initially had 77 tests
  that a `key = ()` mutation — dropping the second argument from the cache
  key — passed in full, because every real caller happens to pass the one
  memoized spans object per tree. An untested key is not an unnecessary
  key: `test_cache_key_includes_the_second_argument` now calls the helper
  twice on one tree with two genuinely different span arguments, and a
  cache-that-caches-nothing mutation is caught separately, since output
  equivalence alone cannot distinguish a correct cache from an absent one.

- **A duplicate test body is a review queue, not a deletion list.** An audit of
  this suite reported 61 same-module clone groups; re-screening with
  `scripts/find_duplicate_tests.py` (body + parameters + decorators) found 29,
  and the count is not the point — the *kind* of clone is. Clones across two
  differently-named classes are usually intentional: each class states a
  distinct claim and the body coincides, and `test_signature_normalization.py`
  has two such pairs that each already name their counterpart in a comment, so
  removing them would trade a stated regression guard for no measurable time.
  The category worth reading is same class or module scope with **different
  names**, because a test whose name promises input X while its body tests
  input Y is worse than a duplicate — it makes a gap look filled. All nine such
  groups were worked through; four were one assertion under two names, and the
  other five each hid something:
  `_strip_param_signature`'s "pointer parameter is not mistaken for a wrapper"
  asserted an input the function's own docstring says never reaches that
  branch (now a function-pointer parameter, which no other case in the file
  supplied); a `baseline_generation` test never had a manifest that declared
  one; `test_sc_offline_snapshot` could not be removed at all, because
  `tests/test_scenarios.py` separately asserts one `test_sc_*` per automated
  catalog scenario, so it was strengthened to assert the offline property
  instead of inheriting it from the helper; an `l3l4l5` clone claimed the
  header/build pass-name alias while exercising no alias; and a
  `classify_perf_paths` clone named a *workflow-wiring* claim no CLI argument
  can express, which turned out to be genuinely untested — `performance.yml`
  sourcing the PR's whole current label set rather than the delivered event's
  is now asserted, and both regressions it guards against were confirmed by
  mutating the workflow.
  Two cautions from that pass. **Mutate before claiming a gap:** one group
  looked like a missing one-sided-evidence case in the ELF alignment detector,
  and a mutation test showed `test_declared_alignment_known_one_side_only_
  falls_back` already covered it — the speculative test was withdrawn and the
  clone folded instead. And when asserting against workflow *text*, strip
  comments first: the first version of the `performance.yml` assertion failed
  because the file documents the rejected `github.event.label.name` spelling in
  a comment, so a raw substring search reported the very thing it was checking
  for absent.

## Line-coverage floor

The `pr` profile's `unit-pr` step (`scripts/verify.py`) enforces a **95%**
line+branch coverage floor (`--cov-fail-under=95`) — the `fast` profile does
not, since it's the everyday inner loop and deliberately skips coverage
instrumentation. This floor applies **only on the canonical Linux/Python-3.13
unit-test lane** in `.github/workflows/ci.yml` — that's where the full unit
suite runs under coverage. In CI that lane runs as three concurrent
`pytest --shard=K/3` jobs (`tests/pytest_shards.py`: whole files, balanced by
test count) that only collect coverage data; `unit-tests-coverage` combines
them and enforces the floor once. Locally, `verify.py`'s `unit-pr` step runs
the same selection unsharded, in one process.
Other Python versions do not run the full suite at all; `python-compat.yml`'s
smoke lane covers them (a second full-suite leg would only re-check the same tests).
macOS/Windows skip the Linux-only ELF/DWARF parsing tests, which structurally lowers
their coverage (~93% on macOS), so those lanes run the same tests without the
fail-under gate — and, since a CI audit found the reports had no reader, without
coverage instrumentation at all. The macOS lane wrote a `coverage.xml` that the
Codecov upload (gated to Linux/3.13) never took and no gate consulted; the `slow`
lane wrote a `coverage-slow.xml` that nothing anywhere read. Both were dropped and
every test kept: instrumentation costs ~60% wall time, so collecting a measurement
with no consumer is the one kind of coverage work that is pure loss. **If you add
coverage collection to a lane, give it a consumer in the same change** — an upload,
an artifact, or a gate.

`COVERAGE_CORE=sysmon` is requested on the unit-test job and in `scripts/verify.py`'s
`unit-pr` step, but it is **inert**: `branch = true` applies to every lane and
sys.monitoring cannot measure branches before Python 3.14, so coverage.py warns and
falls back to CTracer. It is left in place because it becomes live unchanged if the
coverage lane moves to 3.14; `tests/test_coverage_core_effectiveness.py` fails if that
disagreement stops being stated where the setting is. Moving the canonical lane to
3.14 to make it live is a separate decision — it also moves `repo_facts.json`'s
`canonical_python` and the `ai-readiness` job's pin.

If the macOS lane ever fails on coverage, the fix is to keep the
gate Linux-scoped — **do not lower the global 95% floor** to make another platform pass.


## `scripts/verify.py` — full contract (M0-3)

The `fast` profile is the everyday inner loop, but it is **not**
the definition of "ready for PR" — the canonical CI unit lane runs golden
tests and enforces a 95% coverage floor that the `fast` profile
deliberately skips. `scripts/verify.py` is the single executable
orchestrator every consumer (pixi, pre-commit, CI, this file) calls through,
so the local and CI definitions of done cannot silently diverge again:

```bash
python scripts/verify.py --profile fast   # everyday inner loop (lint, format, mypy, fast unit tests)
python scripts/verify.py --profile pr     # exact CI-equivalent PR gate (incl. golden + coverage floor + ai-readiness)
python scripts/verify.py --profile full   # + external-tool/parity/performance lanes, skipped where the environment lacks the tool

python scripts/verify.py --profile pr --list          # show the steps a profile runs, without running them
python scripts/verify.py --profile pr --only lint,typecheck   # run a subset
python scripts/verify.py --profile pr --json receipt.json     # machine-readable pass/fail/skip receipt
```

**Before opening a PR, run `--profile pr` (or `pixi run check`, which calls
the identical command) — not just the `fast` profile.**
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

The `pr` profile also runs `complexity-bench` (`scripts/complexity_bench.py
--strict`): an empirical time-exponent ceiling per hot path, run in CI's
`ai-readiness` job. A failure there, or in the unit lane's whole-package
repeat-call ratchet (`tests/test_compare_repeat_audit.py`), is triaged as
described in [`performance.md`](performance.md).

[pixi](https://pixi.sh) is also supported (`pixi install && pixi run test`,
`pixi run check`) and additionally manages the `castxml`/compiler/`libabigail`/
`abi-compliance-checker` system tools for the `integration`/`libabigail`/`abicc`
marker lanes below — see `[tool.pixi.*]` in `pyproject.toml` and
`CONTRIBUTING.md`. Unlike bare `pip install -e ".[dev]"`, pixi's `default`
environment includes the `docs` and `dist` features too, so `pixi run check`
is complete out of the box. Prefer `pip install -e ".[dev]"` above when pixi
isn't available in your environment (add `,docs,dist` for full parity).


## Known mypy issues

CI runs `mypy abicheck/` as a required gate. The baseline is currently **0 errors** — the previously-documented 26 errors were all `unused-ignore` / `no-any-return` / `misc` warnings on third-party calls (pyelftools, click). They are suppressed in `pyproject.toml` via per-module `disable_error_code` overrides, which keeps the file portable across mypy releases without churning the underlying `# type: ignore` comments.

**Your responsibility**: run `mypy abicheck/` after your changes and ensure it stays clean. If a new third-party suppression is needed, extend the existing `disable_error_code` override for that module rather than scattering ad-hoc `# type: ignore` comments. If you legitimately reduce a real error to zero, leave `MYPY_ERROR_BASELINE = 0` in `scripts/check_ai_readiness.py` — it now warns on drift in either direction.


## ADR surface traceability (`adr-surfaces`)

[ADR-076](../adr/076-adr-use-case-surface-traceability.md):
`scripts/check_adr_surfaces.py` (a `verify.py` `pr` step, CI `ai-readiness`
job) keeps `docs/contribute/adr/adr-surface-registry.yaml` honest. Update it
in the same PR when you:

- **add an ADR** — add its entry (`surfaced`/`partial`/`gap` with `missing`,
  or `internal` with `reason`), and the `use_cases` it serves;
- **add, rename or remove a CLI flag, Action input, API symbol or report
  field an ADR cites** — the gate resolves every surface against the code and
  fails where a cited one no longer exists;
- **change a use case** — each UC carries `user_task:` (`pr_review`,
  `local_check`, `release`, `audit`) and `adrs:`, which must mirror the
  registry;
- **add a scenario** — declare the `surfaces:` its `flow` command actually
  exercises (checked against that command line) and, for a use case reachable
  from cli+api+action, a shared `family:`.

The gate ratchets against `docs/contribute/adr/adr_surface_baseline.json`: a
disposition downgrade, a lost surface or use case, a newly untraced ADR
surface, or a newly uncovered multi-channel use case fails. If the change is
deliberate, run `python scripts/check_adr_surfaces.py --write-baseline` and
commit the rewritten baseline in the same PR so review sees it. The generated
coverage report is `docs/contribute/generated/adr-surface-coverage.md`
(`--write-report`).
