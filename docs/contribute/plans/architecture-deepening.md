# Architecture deepening: from shallow modules to fewer, deeper owners

Status: Phases 1 and 2 landed 2026-10-07 (see each phase's "Landed"
note); Phases 3-5 proposed. No new ADR: each phase finishes
work [ADR-061](../adr/061-responsibility-package-architecture.md) already
decided (D8 facades, gap B dependency direction, the `service_*`/`cli_*`/
`diff_*` target owners). Effort: L overall, delivered as independent
vertical slices.

## Problem

An architecture review (a deletion test applied to each suspect module)
found five places where a concept costs more to understand than it
should: interfaces nearly as wide as their implementation, pass-through
modules, and one invariant spread across several files. Each claim below
was re-checked against the tree on 2026-10-07; one claim from the initial
review did not survive and is recorded under
[Rejected findings](#rejected-findings) so it is not re-raised.

Vocabulary: a **deep** module has a lot of behaviour behind a small
interface; a **shallow** one does not. The **deletion test** asks
whether deleting a module removes complexity (pass-through) or spreads
it across its callers (earning its keep).

## Findings, analysis and phases

Ordered by recommended execution order.

### Phase 1: `DiffResult` stops reaching `policy` through root facades. Strong

**Evidence.**
- Ten root modules define no function or class. The following are
  re-export shims over `policy/`: `checker_policy.py` (130 lines),
  `contract_gating.py` (45), `reclassify.py` (53), `severity.py` (96),
  `exit_decision.py` (56), `contract_coverage_exit.py` (50) and
  `service_input_resolution.py` (60).
- `checker_types.py` (981 lines, the `DiffResult` home) imports
  `checker_policy` at module level and reaches `reclassify` and
  `contract_gating` through function-local imports (lines 891, 915 and
  927).
- `AGENTS.md` names `checker_policy`, `contract_gating` and `reclassify`
  as the one sanctioned exception to "delete delegation-only facades".
  They exist only because `model`-owned `checker_types` cannot import
  `policy`.

**Analysis.** The facades pass the deletion test, so they are
pass-through. The real friction is that `DiffResult` is both a data
record and a policy evaluator: its verdict and relevance methods call
policy. While that holds, the facades cannot go, and every reader of
`DiffResult` has to learn the facade indirection. The other shims
(`severity`, `exit_decision`, `contract_coverage_exit`,
`service_input_resolution`) are ordinary D8 leftovers with no
dependency-direction excuse. In the tree, internal importers of
`checker_policy` are about 14 `abicheck/` files, mostly for `ChangeKind`
and `Verdict`, both of which have a real owner today.

**Deepening.**
1. *Repoint and delete the ordinary shims.* Point every internal and
   test caller of `severity`, `exit_decision`, `contract_coverage_exit`
   and `service_input_resolution` at its owner, then delete the shim with
   a changelog fragment naming the owner. This is the same procedure as
   ADR-061's 2026-09-13 amendment.
2. *Split `DiffResult` data from `DiffResult` evaluation.* Move the
   methods that call `reclassify`/`contract_gating`/`policy_kind_sets`
   into a `policy/diff_evaluation.py` owner. That owner takes a
   `DiffResult` and returns the effective verdict and relevance.
   `DiffResult` keeps only fields and pure accessors. Callers that used
   the method switch to the policy function. This is the seam: policy
   decides, the model records, which is AGENTS.md's "record before
   disposing".
3. *Delete the three sanctioned facades.* Once `checker_types` no longer
   needs them, remove the AGENTS.md exception text.

**Benefits.** Locality: verdict and relevance logic lives only in
`policy/`. Leverage: about 10 modules are removed, and one dependency
inversion with them. Testing: policy evaluation becomes a pure function
over a `DiffResult` fixture, with no model import cycle.

**Risks.**
- `DiffResult` methods are public Python API. Check for documented use
  in `docs/use` and `docs/reference`. If a method is documented, keep a
  thin method that does a function-local import of the policy owner (the
  shape the file already uses), not a module.
- `checker_types` is imported by about 190 modules. Step 2 must keep
  every field and must not change serialization.

**Landed (2026-10-07).** Step 1 as written. Steps 2-3 landed in a
different shape once the code was traced:

- The vocabulary `Change` is built from (`Confidence`, `EvidenceTier`,
  `ReachabilityState`, `FindingEvolution`, `CrossSourceEvolution`,
  `EvidenceStatus`) was a dependency-free leaf misfiled under `policy`. It
  moved to `model/evidence_status.py`. The default-verdict kind sets moved to
  `change_registry`. With both moved, the compare-layer detectors that used
  `checker_policy` need no `policy` import at all.
- `Change` and its pure-data siblings moved to `model/change.py`.
  `checker_types.py` keeps only `DiffResult`.
- `contract_gating`'s predicates only read a stamped field, so they moved to
  `model/contract_finding_relevance.py`, and the facade was deleted.
- `DiffResult` stays in `model`. Moving it to `policy` would have made every
  frontend that annotates a result break the direction rule. Its single real
  policy call (effective verdict and kind sets) is now a call-time import
  recorded as two `dependency_direction_exceptions` in
  `architecture/debt.yaml`. The `reclassify` facade was deleted. Closing
  that exception means taking the verdict buckets off `DiffResult`, which is
  a public Python API decision.
- `checker_policy` stays only because `docs/use/python-api.md` documents it.
  No `abicheck` module imports it.

**Tests.** A parity test over the `examples/` catalog: for every case,
the effective verdict from the new policy function equals the old
method's verdict (the oracle is the pre-change method, captured before
removal). Run `scripts/check_architecture.py` to prove the facade
inventory shrinks.

### Phase 2: dump orchestration gets one owner with a stated scope invariant. Strong

**Evidence.**
- Dump orchestration spans `service_dump_native.py` (809 lines; its
  `debt.yaml` target is `workflows`), `service_dump_pipeline.py` (613),
  `service_dump_cache.py` (794) and `service_dump_native_pe.py` (286).
- The hybrid header backend recurses into `run_dump(...,
  include_dependencies=True)` (`service_dump_native.py:295`) and merges
  the two snapshots. The dependency-scope wrapper lives in a fifth
  module, `workflows/run_dump_scope.py`.

**Analysis.** "Which dependency scope does this snapshot represent?" is
one invariant answered in three places: the caller's keyword, the
recursive sub-dump and the scope wrapper. A real bug of this shape has
already shipped (the snapshot claimed full dependency scope while its
declarations were filtered). This is the textbook case of
extract-for-testability without locality: each piece is tested, and the
bug lives in how the pieces are called.

**Deepening.** Create `workflows/dump/` with one interface: plan a dump,
run sub-dumps, merge, and stamp scope. The scope invariant ("the
recorded scope equals the scope the declarations were filtered to") is
asserted once, at merge. ELF, PE and Mach-O tails, and the cache, sit
behind it as adapters. `service.run_dump` becomes a call into the
workflow.

**Benefits.** The scope invariant has one home and one test. The four
`service_dump_*` debt entries retire instead of growing.

**Coordination.**
- [ADR-062](../adr/062-project-snapshot-storage-v2.md): the cache moves
  under storage v2 rules.
- [ADR-063](../adr/063-one-semantic-pipeline.md) Phase 5: the pre-flight
  `AnalysisPlan` may absorb "plan a dump"; do not build a second
  planner.

**Tests.** A property test over generated (backend × include_dependencies
× header set) combinations that checks the invariant against an
independent recount of declaration origins. Golden snapshots must not
change.

**Landed (2026-10-07), narrower than proposed.** The trace found a live bug of
exactly this class. `dumper.dump`'s hybrid path turned off the parse-time
dependency skip for both legs. The CLI's own hybrid path in
`service_dump_native.py` was a hand-copied recursion that did not, so under
`compare`'s default scoped run the clang leg skipped declarations the
castxml leg kept, and each leg was stamped `dependency_scope="full"` over a
filtered surface. The fix:

- `workflows.run_dump_scope.extraction_scope` is the one place that maps a
  dump's dependency scope onto both parse-time mechanisms (the streaming
  pruner and the dependency skip).
- The CLI hybrid path delegates to `dumper_hybrid.run_hybrid_dump`.
- `tests/test_dump_extraction_scope.py` checks every nesting of outer scope
  (none, scoped, full) around inner scope (scoped, full), plus both legs of a
  real `service.run_dump` hybrid call, against a table derived from the
  request. Bug class: `extraction.recorded_scope_matches_parse_skip`.

Still proposed: consolidating the four `service_dump_*` modules under one
`workflows/dump` owner (the ELF/PE/Mach-O tails and the cache as adapters).

### Phase 3: CLI compare helpers keep only adapter work. Strong (re-scoped)

**Evidence.** `cli_compare_helpers.py` is 2,198 lines, over the
2,000-line hard limit and listed in `debt.yaml`.
`cli_compare_release_helpers.py` is 1,841 lines; its `debt.yaml`
rationale shows repeated baseline raises for threading keyword
parameters through a four-layer dispatch chain (`_run_compare_pair` →
`_compare_one_library` → `_compare_release_libraries` →
`_CompareReleaseCommonArgs`).

**Analysis.** The initial review claimed that `model` and `policy` import
these helpers. That is **false**: every match is in a comment or
docstring (see [Rejected findings](#rejected-findings)). The real
friction is different. The release fan-out's per-member compare request
is a positional tuple re-threaded by hand, so every new compare option
costs one edit at each layer and a debt bump. That makes the interface
as wide as the implementation.

**Deepening.** Replace `_CompareReleaseCommonArgs` and the per-layer
keyword lists with the existing typed `CompareRequest` (ADR-055),
derived once per release and copied per member. Move the member fan-out
loop to `workflows/` (release fan-out already has `workflows/release_*`
owners). The CLI keeps option parsing and rendering only.

**Benefits.** A new compare option is added once, on `CompareRequest`,
instead of four times. The fan-out becomes testable without Click.

**Tests.** A parity test: for each `CompareRequest` field, a directory
operand and the equivalent scalar pair produce the same member result.
This checks the "one model, any cardinality" rule directly and catches
the next dropped option, a class that has recurred at least five times
in `debt.yaml`.

### Phase 4: narrow `service.py`. Worth exploring

**Evidence.** `service.py` is 289 lines, with 8 `noqa: E402` re-export
blocks. The ADR-061 facade budget is 120–150 lines. It re-exports
underscore names (`_dump_elf`, `_run_dump_uncached` and
`_attach_header_graph`) so that tests can patch them.

**Analysis.** The private re-exports exist as test seams, so the module's
real interface includes internals. Phase 2 removes most of the reason:
once dump has an owner, tests patch adapters at that owner's seam.

**Deepening.** After Phase 2, `service.py` exposes only the documented
`run_dump`/`run_compare`/render entry points. Tests that patched
`abicheck.service._*` patch the owning module instead.

**Do it after Phase 2. On its own this is churn.**

### Phase 5: detector families. Worth exploring, needs a spike

**Evidence.** There are 47 root `diff_*.py` files (from 64 lines up to
1,978 lines in `diff_platform.py`) and 52 modules under `compare/`.
`diff_helpers.py` (756 lines) is a shared catch-all.

**Analysis.** Where a new detector belongs cannot be decided from the
filesystem. However, ADR-061 already targets `compare/` for these
modules and warns that mechanical splits preserve coupling. The friction
is real but diffuse, and a file move alone would not deepen anything.

**Spike first.** Measure the coupling: which `diff_helpers` functions
each family uses, and which `compare/*` micro-modules have exactly one
caller. Propose families only where a family shares an interface (for
example layout: record lookup plus offset reasoning), and fold
single-caller micro-modules into that caller. The spike's output is a
family map. Moves follow as one vertical slice per family.

## Rejected findings

- **"`model`/`policy`/`workflows` import `cli_compare_helpers`."** False
  on 2026-10-07. Every reference is in prose (comments or docstrings);
  `scripts/check_architecture.py` already gates the direction.
- **"Fold `change_registry.py` into `model/change_catalog/__init__.py`."**
  Not pursued. It is a 92-line assembly point AGENTS.md documents in the
  ChangeKind recipe; moving it changes contributor instructions for no
  gain in depth.

## Out of scope

- Any change to exit codes, schemas or CLI flags. Every phase is
  behaviour-preserving.
- `stable_abi_data.py` (1,006 lines with no functions) is a data table,
  not a shallow module.

## Acceptance (whole plan)

- No new `debt.yaml` baseline raises in touched files. Each phase lowers
  or retires entries.
- `scripts/check_architecture.py`'s facade and no-growth inventory
  strictly shrinks per phase.
- `python scripts/verify.py --profile pr` is green per slice.
