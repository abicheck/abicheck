# One Semantic Pipeline — unifying application, fact, identity, and outcome models

**ADR:** [ADR-063](../adr/063-one-semantic-pipeline.md) · Proposed; nothing in
this plan implemented yet. **Effort:** XL, multi-quarter, phased — do not
attempt as one PR. **Depends on / sequences with:** ADR-055, ADR-061,
ADR-062, ADR-042, ADR-046/048, ADR-049, ADR-050 (each partially implemented
already; see "Sequencing against in-flight ADRs" below).

## Problem

AGENTS.md's own "Known gaps" section documents, over dozens of numbered
findings, one recurring root cause: the same concept (an input, a config
value, a fact's availability, an entity's identity, a semantic result) is
represented more than once in the codebase, and the representations drift
out of agreement. ADR-063 states the target architecture — and, in its own
"Governing invariant" section, the one rule every phase here exists to
enforce: **one concept, one representation, everywhere it is used, never
two.** This document is the phased, file-level plan to get there without a
rewrite and without ever leaving two live implementations of the same
concept standing side by side for longer than one phase. That is not a
style preference either document treats as negotiable: a phase whose own
PR leaves the representation it was meant to replace still reachable by
any caller is an incomplete phase, not a phase with follow-up work, no
matter how much of the new representation it built.

Three constraints shape every phase below, taken directly from this
repository's own conventions (AGENTS.md, ADR-061's migration discipline)
and from the governing invariant above:

1. **Vertical slice, not flag day.** Each phase ships one consolidation,
   behavior-preserving, independently mergeable, independently revertible.
2. **Delete after consolidating — same PR or the very next one, never
   "eventually."** A phase is not done when the new path works; it is done
   when the old path it replaces is removed and nothing in the repository
   can still reach it. A phase that only adds is half a phase, and "half a
   phase, to be finished later" is exactly the accumulation pattern this
   plan exists to stop — it does not get counted as progress toward
   consolidation until the deletion half lands.
3. **Verify at the size of the change.** A phase touching the compare/scan
   hot path re-runs the FP-rate gate, the tier-accuracy gate, and the
   mutation-score gate for any module it touches; a phase touching
   persisted schema adds a v(N) migration test and a round-trip test at
   production scale (per this repo's "third-party-boundary tests" and
   "toolchain pins" conventions).

## Sequencing against in-flight ADRs

This plan does not start from zero. Four backing ADRs are already partially
implemented, and their current state determines what each phase below can
assume:

| Backing ADR | Current state | What this plan's phases assume |
|---|---|---|
| ADR-055 (typed request/result) | D1 implemented for `compare` only | Phase 1 extends the existing `CompareRequest`/`service_compare_pipeline.py` shape to `dump`/`scan`, it does not invent a new shape |
| ADR-061 (responsibility packages) | Phases 0-1 implemented; Phase 5 (`model` package) begun | Phase 0/2/4/7 of this plan land inside the `model`/`compare`/`policy` packages ADR-061 already created; this plan does not create new top-level packages beyond what ADR-061 names |
| ADR-062 (storage v2) | Phase 0 primitives (`abicheck/storage/`: `FactStatus`/`FactAvailability`, occurrence-preserving identity, canonical encoding, version axes) implemented and **inert** — nothing wired to a producer/reader | Phase 0/5 of this plan is the *generalization* of these primitives into the domain layer; Phase 8 of this plan is the *wiring* ADR-062 Phase 1 still needs, done jointly rather than twice |
| ADR-042 (compatibility/gate separation) | Implemented for JSON/SARIF/`compare-release`; `junit_report.py` still computes inline | Phase 7 of this plan closes exactly this one remaining gap, not a redesign |
| AGENTS.md "PR C" (dump/scan typed convergence) | `resolve_dump_request`/`execute_dump_request` split landed; real `dump`/`scan` execution still on the legacy path, blocked on two named items (castxml availability for parity testing, `--compile-db-filter` typed surface — now closed) | Phase 1 of this plan is exactly "finish PR C," not a new design |

## Phases

### Phase 0 — `Fact[T]` in the domain layer (builds on ADR-062 Phase 0)

**Goal.** A detector cannot observe a field's value without first observing
its availability. `None`/`[]`/a boolean flag stop being overloaded to mean
both "confirmed absent" and "not collected."

**Design.** `abicheck.storage.availability_status.FactStatus` is the leaf
vocabulary this phase reuses — but ADR-061's dependency direction is
`storage -> model`, so `Fact[T]` (living in `model/`) may not import
*from* `storage`. This phase therefore **relocates** `FactStatus`/
`Confidence`/the status-order tuples from `abicheck/storage/
availability_status.py` into `abicheck/model/availability.py` (a leaf
module, no dependency on anything but the standard library, matching that
module's own existing "none of it needs to know what a stored record or a
ledger looks like" framing), and `abicheck/storage/availability_status.py`
becomes a re-export shim for one release so existing `storage.*` imports
keep working. `FactAvailability` (the ledger record) stays in `storage/`,
since *it* legitimately depends on `model`, not the other way around.

Add `abicheck/model/fact.py`: a generic `Fact[T]` wrapping the relocated
`FactStatus` plus an optional `T` payload. `FactStatus` has exactly six
members (`PRESENT`, `PARTIAL`, `NOT_COLLECTED`, `UNSUPPORTED`, `FAILED`,
`NOT_APPLICABLE` — see that module's own docstring) and deliberately has
**no seventh "confirmed absent" member**: per `PRESENT`'s own documented
meaning ("the producer ran, covered the requested scope, and established
the facts — *including establishing that a collection is legitimately
empty*"), a confirmed absence is `PRESENT` carrying an empty/`None`
payload, not a distinct status. `Fact[T]`'s constructors are therefore
`Fact.present(value)` (value may legitimately be `None`/`[]` — that *is*
confirmed absence), `Fact.not_collected()`, `Fact.unsupported()`,
`Fact.failed(reason)`, `Fact.not_applicable()`, and `Fact.partial(value)`.
There is no `Fact.absent_confirmed()` — a draft of this plan proposed one
and it was corrected during review for contradicting the vocabulary it
claims to reuse unchanged; a caller wanting to assert absence calls
`Fact.present(None)` (or `Fact.present(())`/`Fact.present([])` for a
collection) explicitly, so the payload contract's only rule is that this
is the *one* legitimate way to spell "present, empty" — never a bare
sentinel construction readers could mistake for "not collected."
`Fact.value_or(default)` and `Fact.is_present` are the only two ways to
read one without a full `match`. `Fact.__bool__` is explicitly **defined**
to raise `TypeError("Fact[T] has no truth value — read .is_present or
.value_or(...)")` — plain absence of `__bool__` leaves ordinary Python
object truthiness in effect (every `Fact[T]` instance would be truthy
regardless of status), so the no-implicit-truthiness invariant needs the
raise, not silence.

**Scope for this phase (deliberately narrow).** Convert exactly the three
fields AGENTS.md's "Known gaps" names as actively causing fabricated
findings from absent evidence: `RecordType.vtable`/`vptr_offset_bits`
(the `type_vtable_changed` guard), `RecordType.bases` (the accepted-gap
`type_base_changed` entry — converting its *representation* first makes a
future evidence-based guard additive instead of another reinterpretation
of `None`), and `Param.is_va_list` (the reliability-flag entry). Every
other model field stays as-is in this phase — a blanket conversion is
Phase 5's job, after D7's registry exists to drive it mechanically.

**Where the `Fact[...]` value actually comes from (both directions —
fresh extraction and loading a legacy persisted snapshot).** This phase is
incomplete without both halves; a detector switched to read `Fact[...]`
with nothing populating it correctly would either suppress every existing
finding (if unpopulated defaults to `not_collected()`) or silently
recreate the exact ambiguity this phase exists to remove (if derived
naively from the existing raw value with no producer-aware distinction).
Concretely, this repository already has the mechanism this phase
generalizes, in the form of `AbiSnapshot`'s per-field, per-producer
reliability flags (`clang_vtable_facts_reliable`, `clang_va_list_facts_
reliable`, and their siblings for other fields not converted in this
phase) — each already encodes, in careful hand-written prose, exactly the
producer/schema-version distinction `Fact[...]` generalizes into a typed
value instead of a side boolean:
- **Fresh extraction** (`dumper_castxml.py`, `dumper_clang.py`/
  `dumper_clang_vtable.py`, `dwarf_snapshot.py`): each producer now
  constructs the field's value directly as a `Fact[...]` at parse time —
  `Fact.present(vtable_list)` when it actually reconstructed a vtable,
  `Fact.unsupported()` for a producer that has never populated this fact
  at all (castxml for `is_va_list`, per that field's own existing
  docstring), `Fact.not_collected()` when the run's evidence depth never
  reached that extractor. No snapshot-level reliability flag is needed for
  a freshly-built snapshot, since the per-field `Fact[...]` states it
  directly — this is the generalization's actual payoff, not an
  afterthought.
- **Loading a legacy, pre-`Fact[...]` persisted snapshot**
  (`serialization.py`): the existing reliability flag is read *once*, at
  load time, to reconstruct the correct `Fact[...]` value for that
  snapshot's schema version and producer — `clang_vtable_facts_reliable ==
  True` backfills `Fact.present(raw_vtable)`; `== False` backfills
  `Fact.not_collected()` (never `Fact.present([])` — the old field's
  "blanket empty" value on an unreliable snapshot is not a confirmed
  absence, exactly the "real but WRONG data" distinction that flag's own
  docstring already draws). The reliability flags themselves become
  write-only after this phase (kept only so pre-Fact[...] snapshots still
  load correctly) and are deleted in Phase 9 once no pre-conversion
  snapshot needs to be read anymore — not before.

**Files.** `abicheck/model/availability.py` (new — the relocated
`FactStatus`/`Confidence`/order-tuple vocabulary); `abicheck/storage/
availability_status.py` (trimmed to a re-export shim); `abicheck/model/
fact.py` (new — `Fact[T]`); `abicheck/model/snapshot.py`'s `RecordType`/
`Param` dataclasses (new `Fact[...]`-typed fields alongside the existing
ones, old field deprecated-but-present for one release to keep
`asdict`-based external consumers working — removed in Phase 5's
registry-driven sweep, not here); `dumper_castxml.py`/`dumper_clang.py`/
`dumper_clang_vtable.py`/`dwarf_snapshot.py` (each producer constructs the
`Fact[...]` value directly, per the design above); `serialization.py`
(the legacy-schema backfill path, reading the existing reliability flags
exactly once on load); `diff_layout.py`/`diff_types.py`'s vtable/base-list
detectors (read `Fact[...]`, not raw `None`).

**Tests.** Port the existing `tests/test_vtable_evidence_guard.py`
Hypothesis properties to assert over `Fact[...]` states directly, not only
derived booleans; add a property asserting no detector in `diff_types.py`/
`diff_layout.py` pattern-matches a `Fact[...]`-typed field without handling
every `FactStatus` variant (a static AST check, mirroring
`check_ai_readiness.py`'s own style, is preferable to a runtime check here
since the failure mode is a missing `case`, not a bad value). Add a direct
`serialization.py` round-trip test per converted field pinning the
backfill rule itself: a pre-conversion fixture snapshot with the
reliability flag `True` loads as `Fact.present(...)`, one with the flag
`False` loads as `Fact.not_collected()` — **not** `Fact.present([])`/
`Fact.present(False)` — since that exact confusion (a placeholder value
read as a confirmed fact) is the bug this phase exists to make
unrepresentable; a freshly-extracted snapshot round-trips through every
backend's real parser and never consults the legacy flag at all.

**Acceptance criteria.** The three converted fields cannot be read by any
detector without explicit availability handling (enforced by a new
`check_ai_readiness.py` check: a `Fact[...]`-typed field accessed via
direct attribute rather than `.value_or`/pattern-match outside
`model/fact.py` itself is an ERROR). Full test suite green; FP-rate/
tier-accuracy gates unchanged (this phase changes representation, not
detector logic).

---

### Phase 1 — finish the `dump`/`scan` typed-API convergence (closes AGENTS.md "PR C")

**Goal.** `dump`, `scan`, and `compare`'s implicit-dump operand execute
through the same `resolve_dump_request`/`execute_dump_request` pair; no
entry point hand-rolls its own L2 seed, ADR-039 collector call, or AST
cache key.

**Design.** This is not new design — AGENTS.md's "PR C" note already
names the two blockers precisely and one is closed:

1. *(Closed, carried over from main)* `InputSpec.compile_db_filter` exists
   and is threaded through `resolve_dump_request`/`resolve_compare_request`.
2. *(Open — the actual work of this phase)* The default header backend
   (castxml) must be available in CI/dev environments capable of running
   this migration's parity tests; every measurement backing "PR C" so far
   is clang-only. Either (a) obtain a working castxml build for the
   parity-test lane (this plan's own investigation found conda-forge
   0.7.0 segfaulting inside `clang::ParseAST` in this environment — file
   that as its own upstream-castxml investigation, tracked separately, not
   blocking this phase's clang-only half), or (b) explicitly scope this
   phase's first landing to the clang backend and track the castxml
   parity gap as a named residual the same way AGENTS.md already does for
   every other castxml-unavailable finding in this file.

Once unblocked: route `perform_elf_dump`/`handle_non_elf_dump` through
`execute_dump_request`, and `scan_engine._build_new_snapshot` through the
already-landed `_resolve_side_snapshot_impl` call (this step is smaller
than it looks — the candidate-resolver convergence already landed per
AGENTS.md's own record; what remains is the `dump` CLI's real execution
path). Fold the legacy `-p`/`--compile-db` auto-match into the L3→L2 fold
as the *sole* source of compile-database-derived context when the fold
applies (already decided and landed per AGENTS.md's "legacy-match
overlap" entry) rather than re-deciding it here.

**Files.** `abicheck/cli_dump_helpers.py` (`perform_elf_dump`/
`handle_non_elf_dump` → call `execute_dump_request` instead of `dumper.
dump()` directly, keeping every existing post-processing hook —
ADR-039 collector, G31 header-graph attach, clang-layout-tool attach — as
hooks the executor calls, not logic removed); `abicheck/
service_dump_pipeline.py` (the executor gains the hook points);
`abicheck/cli.py`'s `dump_cmd` (already builds a real `DumpRequest` per
AGENTS.md's record — this phase is where it starts being what actually
runs, not only what `--dry-run` renders).

**Tests.** `tests/test_dump_cli_typed_api_parity.py`'s existing
`_BUILD_SHAPES`/`xfail`-gated known-divergent-shape mechanism becomes the
acceptance gate: every shape currently marked `xfail` for a *named,
diagnosed* divergence must flip to passing, with no new shape added to the
divergent list. A shape that cannot be closed this phase is demoted to a
tracked AGENTS.md "Known gaps" entry with the same rigor the existing ones
carry (a real repro, a named mechanism, not a guess).

**Acceptance criteria.** `dump`'s CLI path and `execute_dump_request`
produce bit-for-bit identical snapshots (modulo timestamps/provenance) for
every build shape in the parity corpus; `cli_dump_helpers.render_dump_dry_
run()` is deleted and `--dry-run` renders from the real
`ResolvedDumpRequest` (closing the last item of AGENTS.md's own blocker
list for this exact migration). PR 3C (removing `dump --build-query`/
`--build-compile-db`, currently blocked on this per the plan's own
ordering rule) unblocks as a follow-on, not part of this phase.

---

### Phase 2 — `EntityId`/`ScopePath` as the one identity primitive

**Goal.** Every place that currently computes identity from a string
(dict key, `name`, `qualified_name`, a synthetic ctor/dtor key) instead
reads one `EntityId`, computed once, downstream of backend extraction.

**Design.** `abicheck/model/identity.py`: `ScopePath` (an immutable tuple
of typed segments — `Namespace(name)`, `Record(name, access)`,
`InlineNamespace(name, version_tag)`, `Anonymous(kind, ordinal)`,
`LocalToFunction(owner)`), `EntityId` (a `ScopePath` plus a kind
discriminator — record/enum/typedef/function/variable/constant), and
`OccurrenceId` (an `EntityId` plus a disambiguator for the
already-documented "two declarations, one identity" case ADR-062 Phase 0
already solves at the storage layer — reused here, not reinvented).
Generalizes ADR-046/048's source-graph identity (already real,
`USR`-based) by making `EntityId` the *single* identity both the flat
snapshot and the source graph reference, rather than two graphs with their
own identity schemes that happen to usually agree.

This phase explicitly targets, and closes, the specific collision bugs
AGENTS.md's "Known gaps" records as already-found-and-patched-locally:
opaque-type suppression keyed by bare `RecordType.name`
(`diff_filtering._find_opaque_types`), the `dumper_clang.py` tag-vs-
ordinary-namespace typedef collision, and `type_reachability.py`'s
multi-round namespace-suffix/bare-alias collision history (eleven-plus
numbered findings in that one entry). Each of those local patches is
replaced by one `ScopePath`-based identity computation instead of being
kept as a parallel, narrower fix.

**Files.** `abicheck/model/identity.py` (new, leaf — no dependency on
`checker_types`/`diff_*`, per ADR-063 D10); `diff_filtering.py`'s
`_find_opaque_types`/`_find_by_value_types`/`_root_type_name` (consume
`EntityId` instead of bare `t.name`); `dumper_clang.py`/
`dumper_castxml.py`'s `parse_types()` (produce `ScopePath`-derived
identity, replacing the ad hoc `"::".join([*entry.scope, name])`);
`type_reachability.py` (its multiple ambiguity-tracking helpers —
`_spelling_index`, `_typedef_spelling_targets`, `_namespace_suffix_
spellings` — collapse into one `ScopePath`-based resolver, deleting the
bespoke string-suffix machinery once the new resolver's test coverage
matches or exceeds the existing eleven-plus regression cases).

**Tests.** Every existing regression test named in the "Known gaps"
collision-history entries above is kept (they pin real, previously-found
counterexamples) and re-pointed at the new `EntityId` resolver rather than
deleted — a primitive-level property suite
(`tests/test_entity_identity.py`, per AGENTS.md's "Primitive-level
property tests" convention) states the contract directly: two distinct
declarations in different namespaces never collide regardless of bare-name
overlap; a using-declaration's `EntityId` always resolves to its target's,
never a sibling; namespace-suffix stripping is symmetric and never merges
two records whose full `ScopePath`s differ.

**Acceptance criteria.** `diff_filtering.py`/`type_reachability.py`'s
string-based ambiguity-tracking helpers are deleted, not kept alongside
the new resolver. FP-rate gate shows no regression (a net-new suppressed
finding from the identity change is a Phase 2 bug, not acceptable drift).

---

### Phase 3 — public surface as a graph query over one evidence graph (D5)

**Goal.** `compute_public_surface()` answers "is this declaration public"
by traversing one authoritative evidence graph, not by independently
reconstructing include/reference/export relationships from the flat
snapshot a second time.

**Design.** This phase is deliberately split across two packages, because
"is this declaration public" is a **relevance decision** — AGENTS.md's own
task-routing table assigns exactly that class of question ("decide
relevance, suppression, classification, severity, or gating") to
`policy/`, not to `compare/` ("match old/new entities or identify a raw
change"). Putting the decision itself in `compare/` would make that
package own policy behavior, and ADR-061's fixed import direction
(`policy -> model, compare`; nothing imports the reverse) means `compare/`
can never import a relevance decision back out of `policy/` later without
creating the cycle ADR-061 already forbids. So:

- **The graph substrate** — `abicheck/compare/surface_graph.py` (new):
  typed nodes (`Header`, `TranslationUnit`, `Declaration`, `Type`,
  `Symbol`, `Target`) and typed edges (`Includes`, `Declares`,
  `References`, `Instantiates`, `Exports`, `OwnedByTarget`), built once per
  snapshot side from facts the existing extraction layer already produces
  (the header origin/scoping data `dumper_scoping.py` reads, the
  export-table data `export_surface.py` already computes for
  `contract=exports`, the declaration/reference data `type_reachability.
  py`/`surface.py` each independently reconstruct today). This is a
  reconciliation of raw per-format facts into one structural shape —
  identifying *what references what*, not deciding what is public — which
  fits `compare/`'s charter and, critically, `policy -> compare` is an
  already-allowed import edge, so `policy/` can consume this graph
  directly. Nodes reference entities by the `EntityId` Phase 2 already
  established — this phase is ordered after Phase 2 for exactly that
  reason, the same dependency Phase 5 (`SemanticIR`) has on Phase 2,
  stated explicitly here so the plan's phase order is never read as
  arbitrary.
- **The relevance query** — `abicheck/policy/public_surface.py` (new):
  `PublicSurfaceQuery.resolve(graph, explicit_roots) -> frozenset[EntityId]`,
  a traversal from explicit public roots through `Includes`/`Declares`
  edges (closing the reachable-header surface) and `References`/
  `Instantiates` edges (closing the reachable-type surface), with
  `Exports` edges answering the `contract=exports` domain from the *same*
  graph instead of `export_surface.py`'s separate walk. This is where
  `compute_public_surface()`'s actual decision logic — which declarations
  count as part of the public contract — lives after migration, consuming
  the `compare/`-built graph rather than rebuilding it.

`type_reachability.py`'s `directly_referenced_stdlib_types()` — itself a
relevance decision (it un-filters a record for suppression purposes) —
becomes a second, narrower query in `policy/public_surface.py` over the
same graph (a one-hop `References` filter) rather than its own independent
scan with its own ambiguity-tracking machinery — the machinery this phase
removes is exactly what Phase 2 already started removing for the identity
half of the same problem; this phase removes the *reachability* half.

**Files.** `abicheck/compare/surface_graph.py` (new — graph builder only,
no relevance logic); `abicheck/policy/public_surface.py` (new —
`PublicSurfaceQuery`, migrated from `surface.py`'s existing traversal
logic); `surface.py` (`compute_public_surface()` becomes a thin wrapper
calling `PublicSurfaceQuery.resolve`); `dumper_scoping.py`/
`export_surface.py`/`type_reachability.py` (each becomes a graph
*builder* contributing nodes/edges in `compare/`, or a relevance *query* in
`policy/`, not an independent reachability algorithm); `abicheck/
workflows/consumer_graph.py` (ADR-057's consumer graph) and ADR-053's
TU→link-unit→DSO attribution are explicitly **not** migrated in this
phase — each is noted here as a candidate for a later, separate phase once
this phase's graph has a real consumer to validate the generalization
against, per this plan's own "don't attempt a change with no real caller"
discipline (see AGENTS.md's "shape first, wiring later" gap and ADR-063
D7's capability-lifecycle states).

**Tests.** Every existing `surface.py`/`type_reachability.py` regression
test (including the namespace-collision property suite Phase 2 already
restated for identity) is kept and re-targeted at
`PublicSurfaceQuery.resolve`'s output — this phase's acceptance bar is
that none of those tests need a *behavior* change, only a different call
path, and any test that does need a behavior change is a sign this phase
introduced a real regression, not a refactor.

**Acceptance criteria.** `surface.py`'s own traversal implementation and
`export_surface.py`'s independent closure walk are deleted, not kept
alongside the graph query (the actual removal happens in Phase 9's
checklist, but this phase's own PR is incomplete if it leaves both
implementations live past one release). FP-rate gate shows no regression.

---

### Phase 4 — `AnalysisPlan`: pre-flight resolution, not mid-run discovery

**Goal.** An unsatisfiable request (an evidence requirement no resolved
collector/backend combination can produce) is rejected before extraction,
with a named reason, not discovered as a silent no-op mid-run.

**Design.** `abicheck/workflows/plan.py`: `AnalysisPlan` as a frozen
dataclass (operation, per-side `SidePlan`, requested depth, required
facts, resolved toolchain/compile context, resolved policy, surface
contract) built by a new `AnalysisPlanner.resolve(request) ->
AnalysisPlan | PlanningError`. `PlanningError` carries one entry per failed
requirement (`requested`, `why_unsupported`), modeled directly on the
`--build-target` + pre-captured `aquery` gap and the `-H` + unsupported-
collect-mode gap AGENTS.md already documents as *silent* failures — this
phase's acceptance test is exactly "these two scenarios now raise
`PlanningError` instead of silently dropping the request."

**Files.** `abicheck/workflows/plan.py` (new); `service_compare_pipeline.
resolve_compare_request`/`service_dump_pipeline.resolve_dump_request`
(construct `AnalysisPlan` as part of resolution, reusing — not
duplicating — ADR-049's `compatibility_evaluation_resolver.resolve_field`
for the policy half); `buildsource/adapters/bazel.py` (the `--build-
target` scoping gap gets its first real pre-flight check site here, per
its own AGENTS.md entry's recommended option 2 — reject, don't silently
scope-miss).

**Tests.** Two direct regression tests reproducing the exact named gaps
from AGENTS.md (`--build-target` with pre-captured `--build-info`; `-H`
with an incompatible collect mode) — each asserting `PlanningError`, not
a warning or silent continuation.

**Acceptance criteria.** Both named silent-failure gaps in AGENTS.md close
as a side effect of this phase, not as separate fixes — if either needs a
bespoke patch instead of falling out of the planner, the planner's design
is incomplete and should not be landed yet.

---

### Phase 5 — the fact/capability registry (generalizes `change_registry.py`)

**Goal.** A new fact requires declaring the model field plus one registry
entry, not nine touched files spread across serialization, diff,
suppression, and hand-maintained docs.

**Design.** `abicheck/model/fact_registry.py`: `FactDefinition` (id, value
type, producing backends, persisted/identity-relevant/comparable/
suppressible/reportable flags, lifecycle state per ADR-063 D7). A codegen/
validation script (`scripts/gen_fact_capability_matrix.py`, mirroring
`scripts/gen_cli_reference.py`'s existing pattern) emits the backend
capability-matrix doc and a serialization-completeness check from this
registry; `scripts/check_ai_readiness.py` gains a `fact-registry-
completeness` check mirroring its existing `changekind-partition`/
`changekind-detector` checks, one level up.

**Scope.** This phase converts the *remaining* model fields Phase 0 left
alone into `Fact[T]` + a registry entry, mechanically, field by field —
each conversion is its own small commit (not one repository-wide diff),
so a regression is attributable to one field's conversion.

**Files.** `abicheck/model/fact_registry.py` (new); every `model/
*_facts.py` module (each field gains a registry entry as it's converted);
`scripts/check_ai_readiness.py` (new check); `scripts/gen_fact_capability_
matrix.py` (new, generates what is today a hand-maintained capability doc).

**Tests.** `tests/test_fact_registry_completeness.py`: every `Fact[T]`-
typed model field has exactly one registry entry; every registry entry's
declared producing backends match at least one real parser. Re-run the
full FP-rate/mutation-score gates once after this phase's field-by-field
conversion is complete (not per-field — the mechanical conversions don't
individually risk detector-logic drift, but the cumulative change to every
`Fact[T]`-typed field's representation is worth one full re-verification).

**Acceptance criteria.** PR #734's exact touch list (model, ELF dumper,
serialization, `Change`, diff, suppression, capability matrix, docs,
fixtures — nine files) shrinks, for a comparably-scoped new fact added
after this phase, to: the model dataclass field itself + one registry
entry + parser + detector + test. **The registry does not generate the
model field** — `FactDefinition` describes and validates an existing
`Fact[...]`-typed field on a `model/*_facts.py` dataclass; it is not a
schema from which that field is code-generated, so adding the field by
hand is still required and is explicitly counted in this acceptance
criterion rather than silently omitted from it, per this corrected draft
(a reviewer caught an earlier version of this criterion listing only four
items). Designing and validating real generation of the model field
itself from the registry — which would shrink the list further, to
registry entry + parser + detector + test — is out of scope for this
phase; it would need its own dataclass-field-codegen design (interacting
with `from __future__ import annotations`, `dataclasses.field(kw_only=
True)` placement, and the "new field appended last" convention every
public dataclass in this repo already follows) and is not attempted here.
Demonstrate the stated (five-item) reduction directly — the phase's own PR
adds one new, real fact end-to-end as a worked example and states the
old-vs-new touch-list diff in its description.

---

### Phase 6 — canonical `SemanticIR` between backends and the checker

**Goal.** Type-spelling, scope, template-argument, anonymous/lambda, and
CV-qualification canonicalization happens once, not once per backend.

**Design.** `abicheck/extract/semantic_normalizer.py`: one
`normalize(raw: RawCastXmlFacts | RawClangFacts | RawDwarfFacts | ...) ->
SemanticIR`. Each backend's existing parser (`dumper_castxml.py`,
`dumper_clang.py`, `dwarf_snapshot.py`, `pdb_metadata.py`) is narrowed to
produce only its own `RawXFacts` — today's `parse_types()`/`parse_
typedefs()`-style functions stop doing their own ad hoc namespace-joining,
anonymous-marker handling, and closure-identity stripping, and instead
emit the backend's literal output for the normalizer to canonicalize via
the `EntityId`/`ScopePath` primitives Phase 2 already built.

**Why this phase is ordered after Phase 2, not before.** Every
cross-backend disagreement AGENTS.md records in this area (the lambda-
closure-identity entries, the MSVC-vs-Itanium mangling-scheme entries, the
`Outer::Inner` partial-qualification entry) is a canonicalization
disagreement *about identity specifically* — Phase 2's `EntityId`/
`ScopePath` is the primitive this normalizer is built on, not a parallel
concern.

**Files.** `abicheck/extract/semantic_normalizer.py` (new);
`dumper_castxml.py`/`dumper_clang.py`/`dwarf_snapshot.py` (narrowed to
raw-fact production, each losing its own copy of anonymous-marker/
closure-identity/namespace-join logic as that logic moves to the shared
normalizer); `name_classification.py` (its `_ANONYMOUS_TYPE_MARKERS` and
sibling helpers become the normalizer's, used once).

**Tests.** Every existing per-backend regression test that currently
proves "backend X handles construct Y" is kept and re-targeted at the
normalizer's output for that backend's raw facts — this is a large,
mechanical re-pointing, not new test design, and is the natural place to
retire now-redundant backend-local duplicates of the same assertion (e.g.
two nearly-identical closure-identity tests, one per backend, collapsing
into one normalizer test parameterized over both backends' raw fixtures).

**Acceptance criteria.** **Not** "an identical `SemanticIR` regardless of
source backend" — backends genuinely differ in what evidence they can
produce (DWARF may see only emitted template instantiations where a
header AST sees uninstantiated declarations too; a given backend may be
structurally unable to produce a given fact at all, which is exactly
`Fact.unsupported()`'s job from Phase 0), and requiring bit-identical
output across backends could only be satisfied by discarding real
backend-specific evidence or fabricating a fact a backend never actually
observed — the opposite of what `Fact[T]` exists to prevent. The real bar
is narrower and is what this phase actually fixes: for the subset of
facts two backends **both** produce for a shared fixture, canonical
identity and spelling (`EntityId`/`ScopePath`, template-argument/
anonymous-marker/CV-qualification rendering) must agree exactly — a single
shared test fixture (one closure-parameterized template, one
partially-qualified nested type, one using-re-exported constant) asserts
that agreement on the intersection, and separately asserts each backend's
expected `FactStatus` for the facts only one of them can produce (e.g.
`dumper_castxml.py` genuinely reporting `Fact.unsupported()` for a fact
only the clang backend extracts) — stated as one parameterized test with
two assertions per fixture, not one assertion claiming full equality.

---

### Phase 7 — `RunOutcome` and the last inline exit-code computation

**Goal.** `junit_report.py` stops computing an exit code inline; every
front end encodes `RunOutcome`'s independent axes exactly once, at the
boundary.

**Design.** `abicheck/policy/outcome.py`: `RunOutcome` (compatibility,
assurance, gate, operational, lifecycle — each already real today as
`Verdict`/`AnalysisAssurance`/the ADR-042 gate decision/various ad hoc
operational-status values/ADR-053's target lifecycle, just not yet one
object). **`RunOutcome` is report-level, not per-finding — it does not
replace `junit_report.py`'s per-test-case classification, and this phase
does not attempt to make it.** `junit_report.py`'s `_is_failure` decides,
per `Change`, whether that individual finding fails its JUnit test case,
after contract evaluation, scoped-finding filtering, policy overrides, and
severity mapping have already run on it — exactly the per-change
granularity ADR-042 already records `_is_failure` as needing, and an
aggregate whole-report gate/compatibility value cannot answer "does
*this* change fail" for a report where only some category blocks. The fix
this phase makes is narrower than "read `RunOutcome` instead of `changes`":
each `Change` gains the already-resolved `compatibility_decision`/gate
contribution as a carried field (mirroring how `contract_context.py`
already persists a per-finding decision, per ADR-049 D1) during
resolution, and `junit_report.py`'s `_is_failure` reads *that* per-finding
field instead of re-deriving severity inline from raw `Change` data —
`RunOutcome` is what the report's own top-level `compatibility_decision`
summary still renders from, unchanged. "Stops computing inline" means
`_is_failure` stops re-running severity/policy logic itself, not that it
starts asking the aggregate report outcome a per-test-case question it
cannot answer.

**Files.** `abicheck/policy/outcome.py` (new); `junit_report.py` (the one
remaining inline exit-code computation ADR-042 already named as
unfinished); `html_report.py`'s CI Gate card (already `RunOutcome`-shaped
per ADR-042 — confirm it reads the new object directly rather than a
precursor shape, closing ADR-036 Increment 3 as a side effect if it
hasn't landed separately by then).

**Tests.** A parity test asserting `junit_report.py`'s exit-relevant
output (failure count, failure classification) is unchanged for the
existing `tests/test_junit_report.py` fixtures before and after the
rewrite — this is a refactor, not a behavior change, and needs to prove
that explicitly given JUnit output is consumed by external CI systems.

**Acceptance criteria.** Zero remaining inline exit-code/severity
computation outside the one designated encoder per front end — enforced
by a new `check_ai_readiness.py` check (`no-inline-gate-computation`,
WARN) flagging a severity/exit-code literal compared against `Change`
data outside `policy/outcome.py` and the per-front-end encoders.

---

### Phase 8 — wire storage v2's writer/reader to the domain layer (closes ADR-062 Phase 1, jointly with D8)

**Goal.** ADR-062 Phase 0's primitives stop being inert. A real
`ProjectSnapshot` can be written and read, using `Fact[T]`/`EntityId` from
Phases 0/2 as its domain representation rather than a second identity/
availability scheme invented at the storage layer.

**Design.** This phase is ADR-062 Phase 1 (the v1-v25 import adapter, the
directory-backed `ObjectStore`, folding baseline sets/`BundleFacts` into
sections) **executed with this plan's D8 constraint already in force**:
every DTO is a distinct, versioned class from the domain `SemanticIR`/
`Fact[T]`/`EntityId` objects, with an explicit `to_dto()`/`from_dto()`
(never `asdict`/a 500-line mirror deserializer) and a migration adapter per
DTO version. Doing this jointly with ADR-062 Phase 1 (rather than landing
Phase 1 first, unconstrained, and retrofitting D8 after) avoids writing a
throwaway first version of the writer/reader.

**Files.** `abicheck/storage/package.py` (already has the object model —
`PackageManifest`/`VariantRef`/`ArtifactRef`/`ObjectRef`/`ObjectStore`;
this phase adds the directory-backed implementation and the writer);
`abicheck/storage/dto.py` (new — the `SnapshotDTO`/`ProjectSnapshotDTO`
classes D8 requires); `serialization.py` (the legacy `asdict`-adjacent
`snapshot_from_dict` path is the one this phase's D8 constraint exists to
prevent from growing a `ProjectSnapshot`-shaped sibling).

**Tests.** Per ADR-062's own validation-corpus plan, plus a D8-specific
test: renaming an internal domain field (a synthetic identity key, a
reordered dataclass field) must not change any persisted DTO's bytes —
stated as a property test generating domain-object mutations outside the
DTO's own declared field set and asserting the serialized bytes are
unchanged.

**Acceptance criteria.** Matches ADR-062 Phase 1's own acceptance
criteria (see that ADR and the `storage-format-v2.md` plan) plus: zero
direct `asdict`/mirror-deserializer call sites for any `ProjectSnapshot`-
related type, enforced by the same AI-readiness-style check this plan's
earlier phases already establish as the pattern.

---

### Phase 9 — delete the superseded representations

**Goal.** Every phase above is only complete once its "before" state is
removed, not left as a second path. This phase is the accounting pass,
not new design.

**Checklist (one row per phase, each a real PR removing code):**

- Phase 0: the raw `None`/`bool` reliability-flag fields Phase 0 added
  `Fact[...]` siblings for are removed once every consumer reads the
  `Fact[...]` field.
- Phase 1: `cli_dump_helpers.render_dump_dry_run()`'s independent
  resolution logic; the legacy `-p`/`--compile-db` auto-match's standalone
  code path once the fold fully subsumes it (already partly done per
  AGENTS.md's "legacy-match overlap" record — this is closing the
  remainder).
- Phase 2: `diff_filtering.py`/`type_reachability.py`'s bespoke string-
  suffix ambiguity trackers.
- Phase 3: `surface.py`'s pre-graph traversal implementation and
  `export_surface.py`'s independent closure walk, once
  `PublicSurfaceQuery.resolve` is the only path either one calls.
- Phase 5: any hand-maintained capability-matrix doc section the
  generator now produces.
- Phase 6: each backend parser's own copy of anonymous-marker/closure-
  identity/namespace-join logic.
- Phase 7: `junit_report.py`'s pre-rewrite inline computation (deleted,
  not `# deprecated` and kept).
- Phase 8: any remaining legacy baseline-set/`BundleFacts`-only code path
  once the `ProjectSnapshot` import adapter covers it — per ADR-062's own
  phasing, not accelerated here.

**Acceptance criteria.** For each row: a `git grep` for the removed
pattern/function name returns nothing outside test fixtures/changelog
history. This checklist is re-run, and re-verified, at the end of the
*last* phase landed in a given release cycle — not deferred to "eventually."

## What this plan deliberately does not attempt

- **No new root CLI command or public API surface.** Every new type in
  this plan (`Fact[T]`, `EntityId`, `AnalysisPlan`, `RunOutcome`) is
  internal until a specific phase's own PR explicitly promotes it, per
  ADR-063's own "Explicitly not done by this ADR" section.
- **No schema version bump beyond what ADR-062 already plans.** Phase 8
  follows ADR-062's own phase boundaries; this plan does not add an
  independent schema migration.
- **No attempt to resolve every AGENTS.md "Known gaps" entry.** Several
  entries there are accepted, permanent limitations (e.g. the reverted
  linkage-blind-removal attempts, the `type_base_changed` evidence gap
  with no independent signal) that this plan's primitives make *easier to
  close later* (Phase 0's `Fact[T]` on `RecordType.bases`, specifically)
  but does not itself close — closing them needs the evidence this plan
  doesn't add (consumer-side evidence, a captured base-layout fact), which
  is out of scope here and remains a tracked gap.
- **No toolchain-identity-probe implementation.** AGENTS.md names this gap
  independently (castxml/clang invoked without validating the resolved
  compiler matches the real build); Phase 6's `SemanticNormalizer` makes a
  future probe's result easier to thread through uniformly, but does not
  implement the probe itself.

## Effort and risk summary

| Phase | Effort | Primary risk |
|---|---|---|
| 0 | M | Converting the wrong three fields first (pick fields with an *active* fabricated-finding incident, not merely "many `None` checks") |
| 1 | L | castxml unavailability blocking the parity-test half; mitigated by explicit clang-only first landing |
| 2 | L | Identity collision regressions are exactly the bug class this phase targets — the property-test suite is the real acceptance bar, not code review alone |
| 3 | L | Migrating `surface.py`/`export_surface.py`'s traversal into a shared graph without changing what counts as public — the kept-test-behavior acceptance bar exists specifically to catch a silent surface-scoping regression |
| 4 | M | Planner rejecting a request current behavior silently accepted — must ship with a migration note in `CHANGELOG.md`/docs, not only a changelog fragment, since it is a user-visible behavior change (a previously-silent no-op becomes an error) |
| 5 | L (mechanical, field-by-field) | Scope creep — cap each commit to one field |
| 6 | XL | Largest blast radius in this plan (every backend parser); sequence last among the "hard" phases, after 0/2/3 give it primitives to build on |
| 7 | S | JUnit output is consumed by external CI systems, and `_is_failure` stays per-finding rather than becoming an aggregate-outcome lookup — parity testing, not redesign |
| 8 | XL | Shared with ADR-062's own Phase 1 risk profile; do not duplicate that ADR's own risk analysis here, defer to it |
| 9 | S per row, continuous | The easiest phase to skip under time pressure — explicitly called out as required, not optional, per ADR-063's decision drivers |
