# Examples/catalog split — taxonomy first, no directory move yet

**Effort:** XL (six phases) · **Status:** Phase 1 implemented; Phase 2 complete (every `rule`-entity case carries a `rule_slug`, seven confirmed duplicate/variant pairs found and recorded, and every one of the 30 `scenario`-entity cases now carries `related_rules`); Phase 3 complete (every path resolver in the codebase now routes through `example_catalog.case_dir`); Phase 6 implemented; Phase 5 in progress; Phase 4 not started. See the table below for per-phase detail.

## Problem

`examples/` is simultaneously the user-facing "how do I use abicheck"
catalog and the calibration corpus of 197 `caseNN_*` fixtures the FP-rate,
tier-accuracy, mutation, and full-catalog-coverage gates all run against
(`examples/CLAUDE.md`). Those are different audiences with different
quality contracts, and even within the 197-case corpus, "case" hides at
least four different entities:

- a **rule** — an atomic, ecosystem-neutral compatibility proposition
  ("removing an exported function breaks existing binary consumers");
- a **variant** — the same rule demonstrated under a different condition
  (language, evidence tier, public-surface reachability);
- a **scenario** — several rules composed into a realistic problem: an
  ecosystem case study (oneTBB/SYCL/oneMKL/Linux-kernel), a multi-library
  project topology (the bundle cases), or a capability/evidence
  demonstration (the G20 audit/cross-source cases);
- a **fixture** — implementation-only proof data backing a rule or
  scenario, not itself a user-facing example.

Treating all 197 as interchangeable "examples" both weakens the user
learning path (a C++ oneTBB case study reads the same as an atomic C rule)
and inflates coverage claims (three demonstrations of the same rule under
three languages count as three ABI concepts, not one rule with three
variants). Concretely: `case01_symbol_removal` and `case12_function_removed`
are the same rule (a plain exported-function removal) with no real
distinguishing variant; `case08_enum_value_change` and
`case20_enum_member_value_changed` are the same rule, but case20's
public-header-not-reachable-from-an-exported-signature condition is a
genuine variant (it exercises public-surface scoping) worth keeping
separate.

## Non-goals for this change

This change has grown beyond its original Phase 1+2-only scope (see the
per-phase status table below: Phase 1 and Phase 2 are complete, Phase 6 is
implemented, and Phases 3 and 5 have real, in-progress work), but the
following remain true regardless of how far any individual phase has
progressed:

- No `examples/case*/` directory is moved, renamed, or deleted (Phase 4,
  not started).
- No case is removed from `ground_truth.json["verdicts"]` or from any gate
  that counts cases — every change so far is either new metadata
  (`taxonomy`), a new report (`catalog-coverage.md`), a new path resolver
  with unchanged behavior (`example_catalog.py`), or a new, independent
  `examples/workflows/` tree that doesn't touch the `caseNN_*` calibration
  catalog at all.
- `examples/case*/` itself is not yet physically split into a curated
  user-facing tree and a separate `catalog/` tree — that's still Phase 4,
  and `examples/workflows/`'s curated examples (Phase 5) are additive
  alongside it, not a replacement for it.

## What Phase 1 implements

`scripts/gen_catalog_taxonomy.py` generates a `taxonomy` object in
`examples/ground_truth.json` — a sibling of the existing `verdicts` object,
never merged into it, so every existing consumer of `verdicts` is
unaffected. Each of the 197 entries carries:

| Field | Meaning |
|---|---|
| `entity` | `rule` or `scenario` |
| `scenario_kind` | (scenarios only) `case-study`, `project-topology`, `capability`, or `audit` |
| `ecosystem` | `generic`, `onetbb`, `sycl`, `onemkl`, or `linux-kernel` |
| `topics` | derived from `expected_kinds` via the existing `abicheck/model/change_catalog/{symbols,types,platform,build,source}.py` split (AGENTS.md's own "Adding a new ChangeKind" categorization) — reused rather than re-invented; `controls` for a `NO_CHANGE` case with no kinds to derive from; `audit` added for the four G20 audit rules |
| `languages` | derived from which source-file extensions the case's own fixtures ship |
| `scope` | `single-library` or `multi-library` (the five bundle cases) |
| `artifact_shape` | `compiled-pair`, `snapshot-pair`, `snapshot-audit`, `stub-pair`, `btf-pair`, `kabi-pair`, `fixture-pair`, or `bundle` — derived from ground_truth.json's own `fixtures:`/`mode:` fields when a case declares them (authoritative over any file-scan heuristic; `snapshot-audit` is a single-release G20 scan, `snapshot-pair` an old/new comparison), else from what the case directory actually ships |
| `validation_owner` | which runner family exercises the case (mirrors `examples/CLAUDE.md`'s "owner families" list) |
| `related_rules` | rule slugs a scenario composes — populated for every scenario case (see "What Phase 2 implements" below for how each was derived) |
| `rule_slug` / `variant_of` | every `rule`-entity case's canonical family name and, for a confirmed duplicate, the case it's a variant of — see "What Phase 2 implements" below |

Entity/scenario_kind/ecosystem classification for the scenario families
(bundles, G20 audit, oneTBB/SYCL/oneMKL/Linux-kernel case studies) is by
explicit case-number lookup table in the generator, not a heuristic — every
other case defaults to `entity: rule`, `ecosystem: generic`.

Run `python scripts/gen_catalog_taxonomy.py` to regenerate; `--check` gates
drift the same way every other `gen_*.py` generator in this repo does.

## What Phase 2 implements

Non-destructive consolidation via `rule_slug`/`variant_of` rather than
deleting any case (these are calibration fixtures — `examples/CLAUDE.md`'s
"don't modify a case's source or expected verdict without understanding
what failure mode it encodes"), in two parts:

**Every `rule`-entity case (167 of them) now carries a `rule_slug`.** A case
with no known duplicate gets one mechanically derived from its own case name
(`gen_catalog_taxonomy._default_rule_slug`, e.g. `case02_param_type_change`
→ `param-type-change`) — so "does this rule have a canonical name" never
depends on whether a sibling duplicate happens to have been found yet.

**Seven pairs were confirmed as genuine duplicates**, found by clustering
every `rule`-entity case on its exact `expected_kinds` set (13 clusters, 37
candidate cases) and reading each cluster's actual README content — not
just the shared `ChangeKind` — to separate a true duplicate from a case that
merely shares a `ChangeKind` while demonstrating a different mechanism or
reaching a different verdict:

| Rule | Canonical | Variant |
|---|---|---|
| `exported-function-removed` | case01_symbol_removal | case12_function_removed |
| `enum-member-value-changed` | case08_enum_value_change | case20_enum_member_value_changed (public-surface scoping) |
| `embedded-type-size-increased` | case07_struct_layout | case14_cpp_class_size (C++ variant) |
| `inline-function-outlined` | case16_inline_to_non_inline | case47_inline_to_outlined (confirmed by diffing the two READMEs byte-for-byte, not just matching `expected_kinds`) |
| `executable-stack-flag-changed` | case49_executable_stack | case136_executable_stack_removed (identical transition and library source, despite the README framing it as a separate "fix direction" case) |
| `symbol-version-node-removed` | case65_symbol_version_removed | case139_symbol_version_node_removed (adds the "symbol name persists, folded into a different node" nuance) |
| `public-api-gains-internal-dependency` | case160_public_api_internal_dep_added | case190_public_inline_function_references_internal_constant (narrows to the inline-function case) |

Each variant case's README gained a short "Related rule" cross-reference to
its canonical case (e.g. `examples/case12_function_removed/README.md`,
`examples/case47_inline_to_outlined/README.md`).

**Clusters reviewed and deliberately *not* merged**, because they share a
`ChangeKind` but demonstrate a different mechanism or reach a different
verdict — each keeps its own default rule_slug: `case03`/`16`/`47`/`62`/`185`
(`func_added` from four unrelated causes — plain addition, un-inlining twice
over, vtable-slot reuse, opaque-struct-plus-accessor); `case07`/`14`/`17`/
`18`/`36`/`40`/`44`/`48` (`type_size_changed` from eight unrelated causes —
case07/case14 *are* the C/C++ duplicate pair above, the other six are
distinct mechanisms: template instantiation, transitive dependency leak,
anonymous union, compound multi-axis stress case, cyclic self-reference,
embedding propagation); `case09`/`38` (a compound four-changes-at-once
stress case is not a duplicate of the single-mechanism one); `case46`/`102`
(pointer-chain vs. frozen-namespace-runtime-entry — unrelated mechanisms);
`case65`/`139`/`183` (case183's private/internal-node naming convention
downgrades its verdict to `COMPATIBLE_WITH_RISK`, so it stays separate from
the case65/139 pair even though all three share the `ChangeKind`);
`case74`/`75`/`76`/`77` (the "leaked internal types" pattern family — four
distinct embedding mechanisms: base-class inheritance, embed-by-value,
pimpl+vtable, templated base — deliberately kept as four rules, not
collapsed into one, the same restraint AGENTS.md's own case01/12 vs.
case08/20 discussion calls for); `case43`/`77` (plain base-class field
addition vs. template-instantiation layout shift); `case97`/`182`
(macro-gated conditional export vs. undocumented accidental export);
`case137`/`52` (RUNPATH newly added vs. RUNPATH build-path leak — different
transition, different lesson). See `scripts/gen_catalog_taxonomy.py`'s
`RULE_FAMILIES` docstring for the same list with the read behind each call.

**`related_rules` is now populated for every scenario entity** — the
original design discussion named `case108`, `case112`, `case126`, and
`case94` explicitly; this pass reviewed each of the remaining 26 scenario
cases' own README ("Verdict and consumer impact" + `expected_kinds`) and
recorded the generic rule(s) it composes, reusing an existing rule case's
`rule_slug` wherever the scenario's underlying mechanism is the same one a
single-library rule case already demonstrates (e.g. `case90_bundle_intra_dep_removed`
→ `exported-function-removed`, `case92_bundle_provider_changed` →
`symbol-source-owner-changed`, five of the seven header-graph/call-graph
capability cases (`case191`, `case194`-`case197`) → `public-api-gains-internal-dependency`,
the internal dependency each of them introduces), and otherwise a conceptual
slug in the same ecosystem-neutral style the four original entries already
used for a mechanism no rule case demonstrates alone yet (e.g.
`case175_kabi_crc_changed` → `symbol-type-signature-hash-changed`,
`case148_xcheck_header_build_mismatch` → `header-build-context-mismatch`,
`case192_call_graph_break_survives_suppression` →
`internal-symbol-required-by-public-api`, distinct from case160's "public
entry newly gains a dependency" rule since case192's call edge already
exists in v1). Three of the five also name the specific graph-reconciliation
outcome their own README's Category calls out as its actual point (`case194`
→ `internal-declaration-renamed-reconciled`, `case196` →
`internal-declaration-moved-reconciled`, `case197` →
`internal-declaration-identity-reconciled`) — `case195` is the deliberate
"reconciliation correctly declines to fire" counter-example, so it carries
no reconciliation-outcome slug of its own.
`related_rules` is deliberately not validated against the live `rule_slug`
set for this reason — see `tests/test_catalog_taxonomy.py`'s
`test_related_rules_are_non_empty_strings` and `gen_catalog_taxonomy.py`'s
`RELATED_RULES` table for the full case-by-case list and the read behind
each entry.

## Known gaps / remaining phases

Not attempted in this change (from the original six-phase migration). Each
row is tracked independently — update its Status cell in the same PR that
makes progress on it, rather than leaving this table to drift the way
`related_rules` did before this pass:

| Phase | Status | Depends on | Description |
|---|---|---|---|
| 3 | **Complete** — `scripts/example_catalog.py` (`case_dir`/`all_case_ids`/`iter_case_dirs`/`load_ground_truth`) is now the one resolver every non-seam consumer routes through. A first pass (migrating `gen_catalog_taxonomy.py`, `gen_examples_docs.py`, `benchmark_comparison.py`, `check_ai_readiness.py`'s catalog root, the four `validation/scripts/*_examples.py` runners, and five `tests/test_*_examples.py` files) was marked complete prematurely — a Codex review on the PR found a repo-wide search still turned up real per-case joins in `tests/test_castxml_free_examples.py`, `tests/test_python_api_examples.py`, `tests/test_reachability_examples.py`, `tests/_scan_fixtures.py`, and `tests/test_workflow_kernel_accel.py`, and a follow-up manual sweep (grepping every `EXAMPLES`/`_EXAMPLES`/`REPO_DIR`-style constant joined against `"examples"` or a case name, not just the ones a first grep for `EXAMPLES_DIR =` happened to catch) found several more: `tests/check_stripped_fp.py`, `tests/test_skill_eval_scenarios.py`, `tests/test_bundle.py` (case84, 3 call sites), `tests/test_environment_drift.py` (case170, 3 call sites), `tests/test_workflow_kernel_accel.py` (case121, 3 call sites), `tests/test_g20_catalog.py`, `tests/test_kabi_examples.py`, `tests/test_appcompat_examples.py`, `tests/test_l3l4l5_examples.py`, `tests/test_diff_reconcile.py` (case164), `scripts/gen_l3l4l5_examples.py`, `scripts/gen_g20_fixtures.py`, `scripts/gen_reachability_examples.py`, and `scripts/gen_skill_eval_pack.py` (two Category-A fixture-resolution call sites) — plus, for consistency, every remaining `GROUND_TRUTH`-path-only duplicate (`tests/test_evidence_tiers.py`, `tests/test_platform_coverage_honesty.py`, `tests/test_generate_benchmark_report.py`, `tests/example_shards.py`, `tests/test_scan_accuracy.py`, `tests/test_catalog_taxonomy.py`, `tests/test_skill_eval_pack.py`, `scripts/gen_detector_spec.py`, `scripts/check_examples_validation_status_sync.py`, `validation/scripts/run_full_catalog.py`, `validation/scripts/run_example_owner_proofs.py`). Every hand-rolled `EXAMPLES_DIR / case_name` (or `_EXAMPLES` / literal `REPO_DIR / "examples" / case`) per-case join across the whole codebase is now `example_catalog.case_dir(case_id)`, and every duplicated `ground_truth.json` path constant now reads `example_catalog.GROUND_TRUTH_PATH` / `example_catalog.load_ground_truth()`. A bare directory *scan* (`for d in EXAMPLES_DIR.iterdir(): ...`, a `cmake -S`/glob over the whole tree, a generator's `examples_dir` root parameter) is left pointed at `EXAMPLES_DIR`/`example_catalog.EXAMPLES_DIR` itself, since that's not a per-case join Phase 4 needs to redirect. Three deliberate exceptions remain, by design rather than oversight — each is a name a test's `monkeypatch.setattr` overrides to redirect resolution at a throwaway root, so routing the call site itself through `example_catalog.case_dir()` (which cannot see the monkeypatch) would silently stop honoring the override: `tests/validate_examples.py`'s `EXAMPLES_DIR`/`GROUND_TRUTH` (seam: `tests/test_validate_examples_unit.py`), `check_ai_readiness.py`'s `EXAMPLES` (seam: `tests/test_ai_readiness.py`), and `scripts/gen_skill_eval_pack.py`'s `EXAMPLES` (seam: `tests/test_skill_eval_pack.py::test_scenario_digest_covers_the_fixture_not_just_the_record`) — the last one was caught by this pass's own review discipline: an initial `example_catalog.case_dir()` edit there silently broke that seam (the digest test's `before != with_fixture` assertion started comparing a hash against itself), caught by running the file's real test suite before considering the migration done, not just a syntax/lint check. `scripts/check_docs_contract.py`'s `EXAMPLES` and `scripts/gen_examples_docs.py`'s `DOCS_EXAMPLES_DIR` are the two remaining `EXAMPLES`-named constants that were deliberately *not* touched at all — both are directory-root uses (a `case*/README.md` glob; the *docs output* tree, not the source catalog), not per-case joins. Verified via the pre-existing test suite for every touched file (`tests/test_review_comment_regressions.py`'s dynamic-load coverage of the validation runners; every migrated file's own real suite, including real castxml/clang-backed executions; `tests/test_skill_eval_pack.py`/`test_ai_readiness.py`/`test_docs_contract_retired_surfaces.py`/`test_validate_examples_unit.py` re-run in full to confirm every remaining monkeypatch seam still works: 290 passed), a full-repo `pytest.ini` fast-lane run, and `python scripts/check_ai_readiness.py`: 0 errors, same warning count as before.

A second Codex review round on the same PR (after the above landed) found the "complete" claim still slightly premature and caught two distinct remaining gaps, both real: (1) five more per-case joins the manual sweep's own pattern still missed — four `_EXAMPLES / self.CASE / "suppress*.yaml"` sites in `tests/test_reachability_examples.py` (only `_snapshots()`'s join had been fixed, not the three suppression-file loads in `TestCase192CallGraphBreakSurvivesSuppression` plus one in `TestCase193OrdinaryExportedFnCallNotReachable`) and one `(EXAMPLES / name).is_dir()` well-formedness check in `tests/test_castxml_free_examples.py` (only the main parametrized test's join had been fixed, not `test_subset_entries_are_well_formed`'s) — both now routed through `example_catalog.case_dir()`, and each file's now-unused `EXAMPLES`/`_EXAMPLES` constant removed; (2) a real architectural gap in two places that had been *correctly* identified as "directory-root, not a per-case join" but were then shown to still be a problem for Phase 4 specifically: `tests/test_example_autodiscovery.py`'s case *discovery* (`_collect_cases()`) walked `EXAMPLES_DIR.iterdir()` directly rather than through the resolver, so after Phase 4 it would silently discover zero cases instead of failing loudly — fixed by driving discovery from `example_catalog.iter_case_dirs()` (itself derived from `ground_truth.json["verdicts"]`, not a directory walk, so it needs no Phase-4-aware change of its own) rather than a raw scan; and `scripts/fixture_sync.py` (the write/`--check` driver `gen_g20_fixtures.py`/`gen_reachability_examples.py` share) did its own `examples_dir / case_name` join internally from a flat root parameter, which cannot express "different cases live under different `catalog/` subtrees" the way Phase 4 needs — fixed by changing `sync_fixtures()`'s signature from `examples_dir: Path` to `case_dir: Callable[[str], Path]`, with both production callers now passing `example_catalog.case_dir` directly and `tests/test_fixture_sync.py`'s own test-injection seam (`case_dir=lambda name: tmp_path / "examples" / name`) preserved exactly the way the three monkeypatch seams above are. `check_docs_contract.py`'s directory glob was re-examined against this same "will Phase 4 break it" question and confirmed to *stay* untouched — its own test (`tests/test_docs_contract_retired_surfaces.py::test_retired_surfaces_scans_example_case_readmes`) monkeypatches `EXAMPLES` to a synthetic `case999_demo` directory that has no `ground_truth.json` entry at all, so routing this one through `example_catalog.iter_case_dirs()` (ground-truth-driven) would silently stop scanning exactly the kind of not-yet-registered case README this generator-source sweep exists to catch; this is the correct exemption, not a remaining gap, and Phase 4 itself will need to teach this one specific sweep how to walk the post-split tree directly (out of Phase 3's scope). Verified the same way: syntax/ruff/ai-readiness clean, the affected files' own suites re-run directly (`tests/test_fixture_sync.py`, `tests/test_g20_catalog.py`, `tests/test_reachability_examples.py`, `tests/test_castxml_free_examples.py`, `tests/test_example_autodiscovery.py`), `gen_g20_fixtures.py --check`/`gen_reachability_examples.py --check` run directly (the latter's pre-existing, environment-specific fixture drift confirmed present unchanged on the unmodified base commit via `git checkout <base> -- scripts/gen_reachability_examples.py scripts/fixture_sync.py`, so not a regression), and a second full fast-suite run: identical 37549 passed / 36 skipped / 4 xfailed / the same 2 pre-existing unrelated failures.

A third Codex review round found the "three deliberate exceptions" framing itself understated the problem: `scripts/gen_skill_eval_pack.py` and `tests/validate_examples.py`'s flat-root constants (`EXAMPLES`/`EXAMPLES_DIR`) are correctly test-injectable *today*, but a flat-root join is still fundamentally incompatible with Phase 4's multi-subtree layout regardless of how test-safe it is -- and `validation/scripts/run_full_catalog.py` transitively inherits `validate_examples.py`'s exception by calling its `_resolve_case_sources` directly, so the exception's blast radius includes a second production script, not just tests. Both are now fixed by generalizing the exact pattern `fixture_sync.sync_fixtures` already established: replace the flat-root constant with a `case_dir: Callable[[str], Path] = example_catalog.case_dir` keyword-only parameter on the one function that does the actual per-case join (`gen_skill_eval_pack.py`'s `_fixture_paths`/`_scenario_digest`; `validate_examples.py`'s `_resolve_case_sources`), with every production call site relying on the default and every test injecting its own resolver (`case_dir=lambda name: tmp_path / name`) instead of monkeypatching the module constant. `gen_skill_eval_pack.py`'s now-dead `EXAMPLES` constant was removed outright; `validate_examples.py`'s `EXAMPLES_DIR` was kept (unused in production code now, but still a valid monkeypatch target for four `tests/test_validate_examples_unit.py::TestMainCategoryFilter` tests that predate this fix and monkeypatch it defensively without actually depending on it) to avoid touching those tests' own scope. `validate_examples.py` sits at its `architecture/debt.yaml` no-growth ceiling with zero headroom (`legacy_oversized_test_module`, baseline 1701 lines) -- the fix's net line delta was driven to exactly zero (signature/docstring compaction, no behavior change) rather than requesting a baseline bump, the same discipline as the `test_environment_drift.py` fix above.

`scripts/check_ai_readiness.py`'s `EXAMPLES` exception is different in kind from the two above, not just remaining scope: `check_examples_ground_truth`'s whole job is a *bidirectional* audit (every on-disk case directory has a `ground_truth.json` entry, and vice versa) that necessarily discovers cases via `EXAMPLES.iterdir()` -- a raw, ground-truth-*independent* directory scan, by design, since driving discovery from `ground_truth.json` itself (the way `example_catalog.iter_case_dirs()` or `test_example_autodiscovery.py`'s fixed `_collect_cases()` do) would make every discovered case trivially already have an entry, silently defeating the half of the check that catches an orphaned directory. No amount of resolver injection helps here: the join this check does (`case_dir = EXAMPLES / case_name`, immediately after discovering `case_name` from that same scan) is a redundant reconstruction of a `Path` the scan already produced, not an independent per-case resolution — the real Phase-4 dependency is the *discovery* step itself, which will genuinely need Phase-4-aware logic (walking N taxonomy subtrees instead of one flat root) regardless of how the join underneath it is spelled. This is accepted as out of Phase 3's scope, for the same reason `check_docs_contract.py`'s glob was: a directory-audit's discovery mechanism is Phase 4's job to update, not Phase 3's. Verified: `check_architecture.py` 0 errors (confirms `validate_examples.py`'s line budget), `check_ai_readiness.py` 0 errors/137 warnings (unchanged), `ruff check` clean, `tests/validate_examples.py` run for real (`PYTHONPATH=. python tests/validate_examples.py case01_symbol_removal --json`, real PASS), `tests/test_validate_examples_unit.py` (58 passed, including the four now-vestigial-monkeypatch tests), `tests/test_skill_eval_pack.py` (73 passed), `tests/test_review_comment_regressions.py` (51 passed, covers `run_full_catalog.py`'s dynamic-load path), and a third full fast-suite run confirming the identical result.

A fourth Codex review round found two more instances of `check_ai_readiness.py`'s exact structural exception, not a new class: `tests/test_case_analysis_validation.py::TestDirectorySync::test_every_directory_has_ground_truth_entry` and `tests/test_examples_docs.py::test_ground_truth_matches_example_dirs` both run the identical *bidirectional* audit (every on-disk case directory has a `ground_truth.json` entry, **and** every entry has a directory) via a `EXAMPLES_DIR.iterdir()` scan whose whole purpose is independence from `ground_truth.json` — the exact reasoning already given for `check_ai_readiness.py`'s `check_examples_ground_truth` and `check_docs_contract.py`'s `case*/README.md` glob applies verbatim: routing discovery through `example_catalog.iter_case_dirs()`/`all_case_ids()` (both `ground_truth.json`-derived) would make every discovered case trivially already have an entry, permanently blinding the check to the one failure mode it exists to catch. **This is now a closed, named class — "bidirectional directory-sync audits" — with exactly four members, all deliberately unmigrated for the identical structural reason, not four independent oversights**: `scripts/check_ai_readiness.py::check_examples_ground_truth`, `scripts/check_docs_contract.py`'s retired-surfaces sweep, `tests/test_case_analysis_validation.py::TestDirectorySync`, and `tests/test_examples_docs.py::test_ground_truth_matches_example_dirs`. Each is Phase 4's discovery-logic problem (teach the scan to walk N taxonomy subtrees instead of one flat root) — no Phase 3 resolver-injection pattern reaches it, because the scan's independence from `ground_truth.json` *is* the point, not an implementation gap.

The same review also caught one genuine, fixable oversight of the same repo-wide-sweep kind as the earlier rounds: `tests/test_benchmark_smoke.py::test_pinned_suite_matches_historical_74_cases` scanned `EXAMPLES_DIR.iterdir()` to build its case-name list, but (unlike the four audits above) never needed independence from `ground_truth.json` at all — it only wants the canonical set of known case names to filter by regex, which is exactly what `example_catalog.all_case_ids()` already provides. Fixed by reusing the `example_catalog` instance `benchmark_comparison.py` (the module this test dynamically loads) already imports at module load, rather than importing a second copy. Verified: `ruff check` clean, the fixed test passes, `check_ai_readiness.py` 0 errors/137 warnings unchanged. | — | Make every path resolver in the codebase (`benchmark_comparison.py`, `gen_examples_docs.py`, `check_ai_readiness.py`, the various validators) go through a declarative `catalog.resolve(case_id)` rather than a hard-coded `EXAMPLES_DIR / case_name`, so Phase 4 doesn't require touching every consumer at once. |
| 4 | Not started | Phase 3 | The physical directory split (`catalog/rules/`, `catalog/patterns/`, `catalog/case-studies/`, `catalog/capabilities/`), with redirects for existing doc URLs. |
| 5 | In progress — `examples/workflows/compare-release/` added (1 of 8): a real, verified `gcc` + `abicheck compare` walkthrough, independent of the `caseNN_*` calibration catalog (see `examples/CLAUDE.md`'s `workflows/` section). Remaining: audit a release, multi-library project, evidence depth, build/source evidence, Python API, suppressions, GitHub Actions. | — | Rebuild `examples/` as a small, curated, task-oriented set (compare one library, audit a release, multi-library project, evidence depth, build/source evidence, Python API, suppressions, GitHub Actions). Independent of Phase 4 — the curated set and the calibration catalog are different trees regardless of which one physically moves first. |
| 6 | Implemented — `scripts/gen_catalog_coverage_report.py` generates `docs/contribute/catalog-coverage.md`, reporting rule/variant/scenario/ecosystem/workflow coverage independently. Workflow coverage is derived from `examples/workflows/`'s real subdirectory count (1 so far, tracking Phase 5's own progress) rather than a static placeholder, so the two phases' status can't go stale against each other. Report-only: no existing gate's case count changed. | — | Split benchmark/coverage reporting into separate rule, variant, scenario, ecosystem, and workflow dimensions instead of one flat case count, per this plan's "stop reporting all cases as semantically equal" motivation above. Depends on Phase 2's `related_rules`/`rule_slug` data (done) but not on Phases 3-5. |

Each phase is its own PR against this plan, not a single follow-up commit —
Phase 4 in particular touches every consumer that currently assumes
`examples/caseNN_*` and needs its own validated, reviewable diff.

## Files & surfaces

- `scripts/gen_catalog_taxonomy.py` (new) — the taxonomy generator.
- `examples/ground_truth.json` — new `taxonomy` top-level key.
- `examples/case12_function_removed/README.md`,
  `examples/case20_enum_member_value_changed/README.md`,
  `examples/case14_cpp_class_size/README.md`,
  `examples/case47_inline_to_outlined/README.md`,
  `examples/case136_executable_stack_removed/README.md`,
  `examples/case139_symbol_version_node_removed/README.md`,
  `examples/case190_public_inline_function_references_internal_constant/README.md`
  — added "Related rule" cross-references.
- `examples/CLAUDE.md` — documents the new taxonomy block and the
  rule/variant relationship.
- `scripts/CLAUDE.md` — inventory row for the new script.
- `scripts/frozen_competitor_results.json` — `ground_truth_sha256` stamp
  updated to match `ground_truth.json`'s new byte content (the added
  `taxonomy` key changes the whole-file digest
  `benchmark_comparison._ground_truth_digest()` pins the frozen
  abidiff/ABICC competitor cache to). Verified safe before updating: this
  branch's `verdicts` object is byte-for-byte identical to the base
  branch's (`git diff <base> -- examples/ground_truth.json` touches only
  the new `taxonomy` key), so the frozen competitor results this stamp
  guards remain valid — nothing about the case fixtures or expected
  verdicts they were computed against has changed. This leaves the file's
  own `frozen_at`/`git_commit` fields pointing at the original 2026-07-18
  run rather than this PR's commits — deliberate, not an oversight: those
  fields record *when the competitor tools actually ran*, and rewriting
  them to today's date/commit on a taxonomy-only touch would falsely claim
  a fresh run happened. `_merge_frozen_into_results()` only trusts
  `ground_truth_sha256`, precisely so a metadata-only touch like this one
  doesn't require re-running abidiff/ABICC — narrowing that digest to
  cover only `verdicts` (so a `taxonomy`-only change never needs this
  stamp bumped at all) would be a real improvement, but it's a shared
  computation with `tests/validate_examples.py`'s own independent
  whole-file digest and `tests/test_example_shards.py`'s cross-shard
  agreement check, so it's a separate, wider change than this PR's scope.

## Tests

`tests/test_catalog_taxonomy.py` mirrors `gen_platform_matrix.py`/
`test_platform_matrix.py`'s pattern: it re-derives the taxonomy from
`gen_catalog_taxonomy.build_taxonomy()` and asserts it matches the
committed block, so drift fails the ordinary fast pytest lane rather than
only a `--check` someone has to remember to run. It also pins the
classification invariants directly — `scenario_kind` is set only for
`entity == "scenario"`, every `variant_of` names a real case whose own
`rule_slug` matches and which is not itself a variant, and `related_rules`
entries are non-empty strings. `scripts/gen_catalog_taxonomy.py --check` is
the equivalent manual/CI drift gate; run either after any
`ground_truth.json`/case change.

## Out of scope

Everything under "Known gaps" above.
