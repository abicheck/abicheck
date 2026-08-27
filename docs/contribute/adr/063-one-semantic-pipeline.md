# ADR-063: One Semantic Pipeline — Unifying Application, Fact, Identity, and Outcome Models

**Date:** 2026-08-27
**Status:** Proposed — roadmap ADR, not implemented. Several of its decisions
are already partially satisfied by work this ADR consolidates rather than
replaces (see "Relationship to existing ADRs" below); none of its new
primitives exist yet.
**Decision maker:** abicheck maintainers
**Relates to / builds on:** [ADR-036](036-report-view-model.md),
[ADR-037](037-cli-interface-contract.md),
[ADR-042](042-compatibility-and-gate-decision-separation.md),
[ADR-045](045-identity-based-old-new-entity-matching.md),
[ADR-046](046-source-graph-identity-v2-and-evidence-merge.md),
[ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md),
[ADR-049](049-contract-relevance-and-compatibility-configuration.md),
[ADR-050](050-comparability-contract-and-multi-tu-manifest.md),
[ADR-054](054-cli-project-integration-surface-consolidation.md),
[ADR-055](055-typed-request-result-completeness-and-schema-registry.md),
[ADR-057](057-consumer-graph-and-impact-join.md),
[ADR-061](061-responsibility-package-architecture.md),
[ADR-062](062-project-snapshot-storage-v2.md)
**Implementation plan:** [One Semantic Pipeline](../plans/one-semantic-pipeline.md)

## Context

A second pass over abicheck's merged-PR history — not just `fix:` commits,
but feature/refactor/perf/hardening PRs and, in particular, chains of the
shape *feature → follow-up → corrective fix → generalized fix* — shows a
recurring root cause behind the codebase's most expensive defects. It is
**not** localized algorithm bugs. It is **disagreement between multiple
representations of the same concept** that are each updated independently,
by different PRs, at different times:

| Concept | Representations that can disagree today |
|---|---|
| Input resolution | `cli.py`/`cli_dump_helpers.py`, `service.py`, `scan_engine.py`, the composite Action, bundle/project paths — each independently resolving headers, includes, compile context |
| Configuration | Click options, `.abicheck.yml`, Action inputs, `CompareRequest`/`DumpRequest` kwargs, environment variables |
| Identity | dict key, `name`, `qualified_name`, `mangled`, castxml synthetic ctor/dtor key, `finding_identity`'s resolved identity, source-graph node id |
| Availability | `None`, `[]`, a boolean reliability flag, a coverage row, a provenance map entry |
| Public surface | header provenance, exported-symbol reachability, path-prefix rules, source-graph reachability |
| Semantic result | findings (`Change`), `Verdict`, severity-scheme exit code, `AnalysisAssurance`, the raw process exit code |
| Report | `DiffResult`, JSON construction, Markdown, SARIF, JUnit, the PR comment |
| Fact support | a model field, a parser, a serializer, a detector, a provenance entry, a capability-matrix row |
| Persistence | per-library snapshot, baseline set, `BundleFacts`, `BuildSourcePack` |
| Compiler context | CLI flags, a compile database, a captured evidence pack, L4 replay argv, the resolved `CompileContext` |

AGENTS.md's own "Known gaps" section is the best evidence for this claim,
not an assertion made here: dozens of numbered findings there are the same
shape — a fact is folded into one of these representations but not a
sibling, or two independently-maintained implementations of the same
decision quietly diverge. Three examples, chosen because each is already a
closed, documented incident:

- **ELF binding** (PR #734): adding one symbol-binding fact required
  coordinated edits to the model, the ELF dumper, serialization, `Change`,
  the diff layer, suppression, the backend capability matrix, docs, and
  fixtures — for one field.
- **L3→L2 compile-context fold** (the "Known gaps" entry by that name):
  eighteen-plus numbered follow-on findings, each a *different* place the
  same resolved compile context needed to be threaded but wasn't —
  `perform_elf_dump`, `handle_non_elf_dump`, `scan_engine._build_new_
  snapshot`, the header-graph second pass, three independent AST cache
  keys, and the legacy `-p`/`--compile-db` auto-match overlapping the new
  fold instead of composing with it.
- **`scan` severity folding** (PR #700): teaching `scan --against` to
  respect a configured severity scheme required widening the exit-code
  space, which broke a downstream consumer's assumption that exit code `1`
  meant only "coverage issue" — because the exit code was being used
  *inside the system* as semantic data, not only as an external contract.

None of these are bugs in the PR that shipped the fix. They are evidence
that **the integration surface for one new fact, one new config value, or
one new policy decision is too large**, because the same concept exists in
more than one place and nothing makes those places agree by construction.

abicheck's current `main` already contains the correct building blocks for
fixing this the right way: typed `CompareRequest`/`CompareResult`
(ADR-055), a shared resolve/classify compare pipeline
(`service_compare_pipeline.py`), `AnalysisAssurance` as an axis independent
of `Verdict` (introduced alongside ADR-049/055-era work), the
`compatibility_decision`/gate separation (ADR-042), canonical entity
identity and graph reconciliation (ADR-046/048), the responsibility-package
migration (ADR-061), and the storage-v2 foundation — `FactStatus`/
`FactAvailability`, occurrence-preserving identity, canonical encoding,
separated version axes (ADR-062, Phase 0 already implemented in
`abicheck/storage/`).

**This ADR does not propose a rewrite.** It proposes finishing the
consolidation these ADRs already started, generalizing three of their
primitives (typed request/result, fact availability, canonical identity)
from the specific subsystem each first applied to (the compare path, the
storage layer, source-graph matching) to the whole analysis model, and then
**removing the legacy parallel paths** each of those ADRs left in place
rather than letting a fourth, fifth, or sixth representation accumulate
next to them.

## Decision drivers

- A large share of abicheck's worst-documented incidents are cross-layer
  disagreement, not single-function logic errors — fixing the class means
  changing where disagreement is *possible*, not adding another guard.
- Several ADRs (042, 046, 048, 049, 055, 061, 062) already introduce the
  right primitive for one subsystem. The fix is to generalize and finish
  wiring those, not to invent a seventh parallel design.
- Migration must be incremental and behavior-preserving per phase — ADR-061
  and ADR-062 already establish this discipline (vertical slices, explicit
  "nothing consumes this yet" states) and this ADR keeps it.
- Every phase that consolidates a representation must end by **deleting**
  the representation(s) it replaces. A consolidation that leaves the old
  path "for compatibility" creates a third implementation, which is the
  exact failure mode this ADR exists to stop (AGENTS.md's own closing
  principle: "After each migration phase, delete the old. This is
  critical.").
- Generalized regression tests (AGENTS.md's "bug-class regression testing"
  convention, and `tests/regressions/manifest.py`) become smaller and
  stronger once an invariant is enforced by the type/module structure
  instead of by a test that has to re-check it at N call sites.
- Nothing here should weaken the AI-readiness gates, the architecture gate
  (`scripts/check_architecture.py`, ADR-061), or the FP-rate/tier-accuracy/
  mutation-score gates — each new primitive below must ship with the
  verification those gates already require for a behavior change of this
  size.

## Decision

Adopt ten decisions (D1–D10), each generalizing an existing, narrower
primitive into a repository-wide one, executed as the sequence in the
[implementation plan](../plans/one-semantic-pipeline.md). Collectively they
are referred to as **One Semantic Pipeline**.

### D1 — One application pipeline; adapters never call the engine directly

All entry points (`cli.py`/`cli_*.py`, the Python API, the composite Action,
`cli_project.py`, bundle/release fan-out) construct a typed request and
hand it to one application-layer pipeline — `resolve → plan → execute →
classify → outcome` — instead of each independently orchestrating
`dumper.dump`/`checker.compare`/policy loading/evidence collection.

This generalizes ADR-055's D1 (CLI and the typed API already share one
input-resolution path for `compare`) and ADR-061's `workflows` ring to
*every* operation, not only `compare`. Concretely: `dump`'s ELF/PE/Mach-O
execution paths (`perform_elf_dump`/`handle_non_elf_dump`) and `scan`'s
candidate resolution (`scan_engine._build_new_snapshot`) converge on the
same `resolve_dump_request`/`execute_dump_request` split already added to
`service_dump_pipeline.py` (see AGENTS.md's "PR C" note for the concrete,
already-identified blockers to finishing this for `dump`/`scan`), and the
Action/`cli_project.py`/bundle fan-out stop doing their own policy
interpretation or compare setup. The `cli-contract`/`engine-cli-boundary`
AI-readiness gates are widened to check this directly rather than only the
currently-allowlisted legacy exceptions.

**No new root entry point is introduced.** ADR-043/054's CLI surface is
unchanged; this decision is about what happens *behind* each existing
entry point.

### D2 — `Fact[T]`: one representation of "do we know this, and how"

Generalize ADR-062 Phase 0's `FactStatus`/`FactAvailability` (currently
storage-scoped, in `abicheck/storage/`) into the in-memory domain
representation used by the comparison/detector layer, not only the
on-disk one. A fact-bearing model field becomes a tagged value distinguishing:

- "we never attempted to collect this" (`NotCollected`) from
- "this backend cannot produce this fact" (`Unsupported`) from
- "collection ran and failed" (`Failed[Reason]`) from
- "the fact does not apply to this kind of entity" (`NotApplicable`) from
- a genuine present value, **including a confirmed absence** (`Present[T]`,
  `T` legitimately `None`/empty; optionally `Partial[T]`) —
  `FactStatus.PRESENT`'s own existing definition already covers "the
  producer ran... including establishing that a collection is legitimately
  empty," so confirmed absence is not a seventh status; it is `Present`
  carrying an empty payload, and `Fact[T]` does not introduce a status
  `FactStatus` itself does not have.

This replaces the current overloaded use of `None`/`False`/`[]` to mean
several of the above simultaneously — documented repeatedly in AGENTS.md's
"Known gaps" (the `type_vtable_changed`/`type_base_changed` evidence-gap
entries, the `Param.is_va_list` reliability-flag entry, the per-finding
evidence-provider gap) as a root cause of fabricated or suppressed
findings. A detector cannot write `if old.default != new.default` without
first unwrapping availability — the invalid comparison becomes a type
error, not an untested branch (`Fact.__bool__` is defined to raise rather
than merely left undefined, since an object with no `__bool__` is still
truthy in Python). `abicheck/storage/availability.py`'s existing
`FactAvailability`/`FactStatus` vocabulary is reused verbatim as the wire
encoding `Fact[T]` serializes to — but because ADR-061 fixes the
dependency direction as `storage -> model`, the vocabulary itself
(`FactStatus`/`Confidence`, not the storage-specific `FactAvailability`
ledger record) relocates to `abicheck/model/` as part of this
generalization, with `storage/` re-exporting it rather than `model/`
importing from `storage/`; this decision does not introduce a second
vocabulary, and the implementation plan states the exact relocation.

### D3 — `EntityId`/`OccurrenceId` as the one identity primitive

Generalize ADR-062's occurrence-preserving identity model and ADR-046/048's
canonical-entity/source-graph identity into the identity primitive used
everywhere an entity needs to be referenced: diff matching (`diff_helpers.
TypeMap`, `finding_identity.py`), suppression/policy selectors, the source
graph (`graph_facts.py`), and persisted snapshots. A structural `ScopePath`
(a sequence of typed segments — namespace, record, inline-namespace,
anonymous/local markers) replaces string-concatenated qualified names
wherever identity, not display, is being computed, which closes the family
of bare-name/qualified-name collision bugs AGENTS.md's "Known gaps"
documents repeatedly for opaque-type suppression, typedef dedup, and
`type_reachability.py`'s stdlib-reference detection — each currently
patched locally, each sharing the identical root cause.

Backend-internal implementation keys (castxml's synthetic ctor/dtor key
being the canonical example — see the `#761`/`#582` incident in AGENTS.md)
are explicitly **not** persisted identity; `EntityId` is produced once,
downstream of backend-specific extraction, not re-derived from a
backend-specific string at comparison time.

### D4 — `AnalysisPlan` resolved before any extraction runs

Before a single collector or backend is invoked, an immutable `AnalysisPlan`
states: the requested operation, per-side evidence requirements, the
resolved toolchain/compile context, the resolved policy, and the public-
surface contract — each as a `requested`/`resolved`/`unsupported`/
`ambiguous` tuple per requirement. A request that cannot be satisfied
(AGENTS.md's recorded `--build-target` + pre-captured `aquery` silent
no-op, or a `-H` flag accepted by a collect mode that cannot use it) is
rejected by the planner, before execution, instead of discovered mid-run or
not at all.

This generalizes ADR-050's comparability contract (a `profile_fingerprint`/
`scope_fingerprint` computed and checked, today, mostly *after* both sides
are extracted) into a pre-flight step, and gives ADR-049's D7 precedence
resolver (`compatibility_evaluation_resolver.py`) one canonical point to
run before, rather than interleaved with, extraction.

### D5 — Public surface as a query over one evidence graph

Generalize the public-surface computation (`surface.py`,
`dumper_scoping.py`, `type_reachability.py`, ADR-024/044) into a query over
one authoritative graph with typed nodes (`Header`, `TranslationUnit`,
`Declaration`, `Type`, `Symbol`, `Target`) and typed edges (`Includes`,
`Declares`, `References`, `Instantiates`, `Exports`, `OwnedByTarget`, ...).
`compute_public_surface()` becomes a traversal from explicit public roots
through this graph rather than a second, independently-maintained
reconstruction of the same relationships from the flat snapshot — closing
the class of bugs AGENTS.md documents under the namespace-collision and
partial-qualification findings in `type_reachability.py`. The same
substrate is the natural target for later generalizing ADR-053's
TU→link-unit→DSO attribution and ADR-057's consumer graph onto one graph
instead of a third representation, but migrating either is explicitly
**not** part of this decision's first implementation phase — see the
implementation plan's Phase 3, which builds the graph for
`compute_public_surface()`/`export_surface.py` only and records the
ADR-053/057 migration as a later, separately-justified phase once this
phase's graph has a real second consumer to validate the generalization
against.

### D6 — `RunOutcome` as independent axes; no `exit_code` inside the domain

Generalize ADR-042's `CompatibilityDecision`/gate-decision split and the
existing `AnalysisAssurance` axis into one `RunOutcome` with independent,
non-encoding fields:

```text
RunOutcome(
    compatibility: CompatibilityVerdict,
    assurance: AnalysisAssurance,
    gate: PolicyGateDecision,
    operational: OperationalStatus,
    lifecycle: TargetLifecycle,
)
```

No domain or workflow code computes or branches on an integer exit code.
Exactly one function per front end (the CLI's `_exit_with_severity_or_
verdict`, the Action's own encoder) maps `RunOutcome` to that front end's
exit-code scheme. This directly targets the PR #700 failure mode (a
downstream consumer decoding semantic meaning from an exit-code integer)
and finishes what ADR-042 started: `mcp_server.py`'s removal already
deleted one of the two remaining inline exit-code computations AGENTS.md
flagged; `junit_report.py` is the other and is folded into this decision's
scope.

`RunOutcome` is a **report-level** aggregate; it is not a substitute for
the per-finding `compatibility_decision` ADR-049 D1 already persists on
each `Change`, and this decision does not ask it to be one —
`junit_report.py`'s per-test-case pass/fail still reads a per-finding
decision, the same one the report's own `changes` array already carries,
not a question answered by the whole-run aggregate. See the implementation
plan's Phase 6 for exactly what that rewrite does and does not change.

### D7 — A declarative fact/capability registry

Every persisted, detected, or reported fact is declared once —
value type, producing backends, persistence, identity-relevance,
comparability, suppressibility — in a registry (`abicheck/model/
fact_registry.py` or equivalent), generalizing `change_registry.py`'s
existing `ChangeKindMeta` pattern (already required for every `ChangeKind`,
per AGENTS.md's "Adding a new ChangeKind" steps) from change *kinds* to
*facts*. Serialization, the backend capability matrix
(`docs/reference/*capability*`), coverage computation, and the AI-readiness
`changekind-*` checks are **generated or validated from this registry**
instead of hand-maintained in parallel, closing the "add a field, touch
nine files" cost the ELF-binding incident (PR #734) is the canonical
example of. Detector logic stays hand-written — this only removes the
*mechanical* plumbing, mirroring the distinction AGENTS.md's own decision
already draws between "the registry entry" and "implement detection in the
appropriate diff module."

A fact also carries an explicit lifecycle state (`MODELLED → PRODUCED →
NORMALIZED → PERSISTED → CONSUMED → REPORTED → PUBLIC`). A capability is
documented or exposed as a CLI option only once it reaches `PUBLIC` —
closing the repeated "shape shipped, wiring followed later" pattern
AGENTS.md records for the snapshot cache (PR #580, zero call sites at
merge) and the L3→L2 fold (`compare`'s implicit-dump path wired first,
`dump`'s CLI path following later, `scan`'s candidate resolution later
still).

### D8 — Finish storage v2 as domain/wire separation, nothing else

ADR-062 already states the correct target for persistence: content-
addressed sections, explicit fact availability, occurrence-preserving
identity, separated version axes, semantic-vs-operational payload
separation. This ADR adds one explicit rule to that target: **no phase of
storage v2 serializes a runtime/domain object directly** (no `asdict(
AbiSnapshot)`, no single-function mirror deserializer the size of
`snapshot_from_dict`, per the `#696` incident AGENTS.md records at ~530
lines). Each layer is `Domain SemanticIR → DTO vN → canonical wire
encoding`, and back, with a migration adapter per DTO version — so an
internal refactor (a synthetic key rename, a reordered field) is never, by
construction, a persisted-schema change. This decision does not change
ADR-062's phasing; it is an added constraint on Phase 1/2's implementation.

### D9 — A canonical semantic IR between raw backend output and the checker

CastXML, direct-clang, DWARF, PDB, BTF/CTF, and ELF-symbol extraction each
produce `RawXFacts`; a single `SemanticNormalizer` (not duplicated per
backend) turns each into one `SemanticIR` that the comparison layer
consumes. Canonicalization of type spelling, scope, template arguments,
anonymous/lambda naming, CV-qualification, and identity happens exactly
once, downstream of the backend, rather than once per backend — closing
the repeated-fix pattern AGENTS.md documents across more than a dozen
numbered findings in the `type_reachability.py`/`dumper_clang.py`/
`dumper_castxml.py` entries, each an instance of one backend normalizing a
construct the other backend handles differently (or not at all). A backend
adapter's contract narrows to "what did the tool actually say" — it no
longer decides how abicheck identifies a C++ entity.

### D10 — Selector/identity/availability as dependency-free leaf packages

`selectors/` (glob/regex/match grammar shared by suppression,
reclassification, and policy filtering), the `Fact[T]`/identity primitives
from D2/D3, and the fact registry from D7 are leaf packages with zero
dependency on `checker_types`, `reporter`, or any CLI module — generalizing
the fix direction PR #733 already took for one local import cycle
(`reclassify.py`'s `importlib.import_module` workaround to avoid importing
`suppression.py`) into a standing rule enforced by `scripts/
check_architecture.py`'s import-direction gate (ADR-061), not solved ad hoc
per occurrence.

## Relationship to existing ADRs

This ADR does not supersede any of the ADRs it builds on. It states how
their already-accepted decisions generalize and finish converging:

- **ADR-055** (typed request/result): D1 extends its CLI/API convergence
  from `compare` to every operation; D7's registry extends its
  schema-version-registry idea from wire formats to facts generally.
- **ADR-061** (responsibility packages): D1/D9/D10 are instances of the
  `extract/model/compare/policy/workflows/storage/report/frontends` ring
  structure it already defines; this ADR does not add new packages beyond
  what ADR-061 already names, and the `model` package is D2/D3/D7's home.
- **ADR-062** (storage v2): D8 is an added constraint on its existing
  phasing, and D2/D3 generalize its Phase 0 primitives (`FactAvailability`,
  occurrence-preserving identity) from storage-only to domain-wide. Nothing
  here changes ADR-062's own phase boundaries.
- **ADR-042** (compatibility/gate separation) and the `AnalysisAssurance`
  axis: D6 is their completion, not a new design — it closes the two
  remaining inline exit-code computations ADR-042 already flagged as
  unfinished.
- **ADR-046/048** (canonical entity identity, source-graph identity v2):
  D3 generalizes their identity model from source-graph matching to every
  identity-bearing subsystem (diff matching, suppression selectors,
  persistence).
- **ADR-049** (contract relevance/compatibility configuration): D4's
  `AnalysisPlan` gives its D7 precedence resolver one pre-flight call site;
  D6's `RunOutcome` is where its `compatibility_decision` axis already
  lives and stays.
- **ADR-050** (comparability contract): D4 promotes its fingerprint checks
  from a post-extraction gate to a pre-extraction planning input where
  possible; the fingerprint mechanism itself is unchanged.
- **ADR-036** (report view-model): D6's `RunOutcome` is exactly the
  upstream object ADR-036's `ReportModel` should render — this ADR extends
  ADR-036's "renderer does not compute, only displays" rule to cover the
  gate/assurance/lifecycle axes ADR-036 predates.

## Consequences

**Benefits**

- A large, recurring class of defect (cross-representation disagreement)
  becomes structurally harder to introduce, not merely better-tested.
- New facts, new config fields, and new policy decisions have one
  integration point instead of N, shrinking both the review surface and
  the AGENTS.md "Known gaps" entries this exact pattern keeps producing.
- Generalized regression tests shrink and strengthen: a test proves one
  shared primitive's contract once, rather than re-proving "this config
  value reached every consumer" at each consumer.
- Performance improves as a side effect of correctness (D9's single
  normalization pass, a run-scoped semantic index replacing repeated
  re-derivation — see the CastXML XML-root-scanned-nine-times finding in
  AGENTS.md for a concrete case this already reproduces).

**Costs / risks**

- This is a multi-quarter, many-PR effort touching nearly every package.
  Each phase must be independently shippable and behavior-preserving, or
  the project accumulates exactly the kind of half-migrated parallel path
  (ADR-061's own "Phase 5 begun... the rest remains incremental" note is
  the honest precedent) this ADR is meant to prevent.
- Several of the backing ADRs (049, 050, 055, 061, 062) are themselves
  partially implemented. Sequencing matters — D1 depends on finishing
  specific, already-identified blockers in `service_dump_pipeline.py`
  (see AGENTS.md's "PR C" entry) before it can close; D8 depends on
  ADR-062 Phase 1 landing an actual writer/reader, not only the Phase 0
  primitives that exist today.
- A registry-generated capability matrix (D7) can mask a real gap as
  "modelled" if lifecycle-state discipline isn't enforced in code review,
  not only in the registry's own schema — this is the same vigilance
  AGENTS.md's "change the kind of tests that get written" section already
  asks for `ChangeKind` registration.
- D6 removing inline exit-code computation from `junit_report.py` is a
  behavior-risk-bearing change to a stable output format and needs its own
  parity tests before and after, per the repository's "toolchain/wire
  format changes need a round-trip test at production scale" convention.

**Explicitly not done by this ADR**

- No new CLI root command, no new Python public API class is promised by
  this decision alone — `RunOutcome`, `AnalysisPlan`, `Fact[T]`, `EntityId`
  are internal domain types unless and until a specific phase's plan
  explicitly promotes one to public API (and updates ADR-055's typed
  surface accordingly).
- No existing persisted schema (snapshot v25, `BundleFacts` v1, baseline
  set manifests) changes as a result of accepting this ADR. Schema
  migrations are ADR-062's own concern and happen on ADR-062's phasing.
- No existing exit code, JSON field, or CLI flag changes meaning as a
  result of accepting this ADR alone; D6/D7's changes are internal until a
  phase's own plan states an external contract change and the usual
  docs/test/changelog-fragment discipline applies.

See the [implementation plan](../plans/one-semantic-pipeline.md) for
phasing, sequencing against the blockers named above, file-level targets,
and acceptance criteria per phase.
