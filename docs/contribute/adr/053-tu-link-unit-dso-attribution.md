# ADR-053: TU → Link-Unit → DSO Source-Evidence Attribution

**Date:** 2026-07-25
**Status:** Accepted — implemented (scope below), tracked as G30 P2's first slice.
**Verified:** main@2e43d53 on 2026-08-04
**Decision maker:** pending

## Context

ADR-047 §9 ("Source evidence: safe model now vs. full model later") adopted a
**safe model** for `build-output.json`'s `evidence.projection` field: an
`abicheck_inputs/` evidence pack may only satisfy a target's
`effective_depth: source` claim when the pack is explicitly `"declared"` —
produced by one wrapper/plugin invocation scoped to a single link unit, or a
compile-DB already filtered to one target's translation units (TUs). A
build-wide pack shared across several DSOs may only feed build-wide source
audits and per-target *header*-depth scans; it may never satisfy an
unqualified per-target *source*-depth claim, because nothing today proves
which of its TUs actually belong to which output binary.

§9 explicitly deferred the alternative — `evidence.projection: "inferred"`,
automatically and safely projecting a build-wide pack onto the correct subset
of targets via **TU → object/link-unit → output-DSO attribution** — to P2,
"scoped as its own follow-up ADR when undertaken, not retrofitted into" the
original ADR. The companion G30 plan doc (`plans/g30-github-actions-
integration-model.md`, "P2 — Deeper architecture") lists it as: "needs
linker-invocation capture, extending `abicheck/buildsource/build_query.py`'s
existing partial zero-config compile-DB inference." This ADR is that follow-up.

**What already exists.** `abicheck/buildsource/build_evidence.py`'s
normalized `BuildEvidence` schema (ADR-029 D2) already has the *shape* this
needs: `Target.source_files`/`.outputs`/`.dependencies`/`.kind`,
`CompileUnit.source`/`.output`/`.target_id`, and `LinkUnit.inputs`/`.output`/
`.target_id`. But the adapters that populate it are inconsistent about
actually *filling in* the attribution-relevant fields:

- `adapters/bazel.py` already emits real `LinkUnit`s from `aquery`'s action
  graph, with `target_id` resolved from the Bazel label graph — Bazel's half
  of this problem is already solved.
- `adapters/cmake_file_api.py` emits `Target.source_files` (CMake's codemodel
  already tells you, per target, exactly which of its own sources compile
  into it — the target graph itself is the attribution signal, no linker
  capture needed) but never emits `CompileUnit`s or `LinkUnit`s at all — those
  come from a *separate* adapter, `adapters/compile_db.py`, parsing
  `compile_commands.json`, which has no target concept and never sets
  `CompileUnit.target_id`. The two adapters' outputs are merged
  (`BuildEvidence.merge`) but never *cross-referenced* — a CMake+compile-DB
  build ends up with a full target graph and a full compile-unit list that
  simply never talk to each other.
- `adapters/make.py` scrapes a `make -n`/`--trace` dry-run transcript for
  compile lines only; every non-compile line (including link/archive
  invocations) is silently dropped. Make has no target graph to lean on at
  all — this is the one build system where attribution genuinely requires
  parsing real linker-invocation argv, matching the ADR-047 §9 text's
  "linker-invocation capture" framing most literally.
- `inputs_pack.ingest_inputs_pack()` (the Flow-2 `abicheck_inputs/` consumer)
  always folds *every* TU in a pack into one linked surface; a pack's
  `target_id` is only set when every TU already agrees on one (via existing,
  producer-asserted tags) — there is no mechanism to *derive* a per-target
  subset from a build-wide pack today, so even if a validator could prove an
  "inferred" projection safe, nothing would actually act on that proof.

## Decision

### D1. One pure attribution module, two independent signal channels

New module `abicheck/buildsource/link_attribution.py`:

```python
def attribute_sources_to_targets(evidence: BuildEvidence) -> dict[str, frozenset[str]]:
    ...  # normalized source path -> the set of target_ids it feeds
```

Combines two independent channels, each individually sufficient where it
applies, unioned per source path (a source legitimately shared between two
DSOs must attribute to *both*, not arbitrarily pick one):

- **Target-graph channel** (CMake, Ninja, any adapter with a real target
  graph): walk `Target.source_files` directly, then transitively fold
  `OBJECT_LIBRARY`/`STATIC_LIBRARY` targets' sources into every
  `SHARED_LIBRARY`/`EXECUTABLE` target that (transitively, via
  `Target.dependencies`) depends on them. No linker-command parsing needed —
  the target graph already *is* the attribution.
- **Link-unit-graph channel** (Bazel, Make): walk each `LinkUnit.output`
  backward through `.inputs`, matching object-file paths against
  `CompileUnit.output`, resolving nested static-library `LinkUnit`s
  transitively (an `.a` that is itself the input to another link unit) until
  reaching a `shared_library`/`executable` terminal link unit.

A source **absent** from the returned mapping is "unknown" (no signal from
either channel; it is never present with an empty set) — callers must treat
this as unresolved, never as "belongs to nothing" or "safe to drop silently."

### D2. Make gets real link-invocation capture

`adapters/make.py`'s dry-run scraper gains `_link_unit()`, mirroring the
existing `_compile_unit()` heuristic-scraper pattern (same reduced-confidence
posture, same diagnostics-not-exceptions philosophy): recognizes `ar`
archive-creation lines (`ar rcs libfoo.a a.o b.o ...` → `static_library`) and
compiler-frontend link lines (no `-c`/`/c`, an `-o <output>` naming a
`.so`/`.dylib`/`.dll`/no-extension output, at least one `.o`/`.a` input
argument) → `shared_library`/`executable` per `-shared`/output extension.
Never runs Make itself, identical to the compile-line scraper's own
constraint (ADR-028 D6, ADR-029 D7).

### D3. `ingest_inputs_pack` gains an opt-in, attribution-filtered mode

`inputs_pack.ingest_inputs_pack()` gains two new optional parameters,
`attribution: Mapping[str, frozenset[str]] | None` and
`expected_target_id: str | None`. When both are given, the TU list is
filtered *before* linking to only TUs whose `.source` attributes (via
`attribution`) to a set containing `expected_target_id` — a TU attributing to
an unrelated target is excluded; a TU attributing to no target (unknown) is
also excluded (fail-safe: an unproven TU must never silently ride along).
Omitting either parameter preserves today's unfiltered behavior exactly —
purely additive, no change to any existing caller.

### D4. `build-output.json`'s validator accepts `"inferred"`, for real

`BuildOutputEvidence` gains an optional `attribution_path` field (a
build-output-root-relative path to a JSON-serialized `BuildEvidence`, i.e.
`BuildEvidence.to_dict()`'s own shape — no new schema, reuses ADR-029 D2's
existing model). `build_output.py`'s validator drops the previous
unconditional "`inferred` is always a hard failure" rule (`_evidence_
projection_issues`) and replaces it, for `projection: "inferred"` targets
specifically, with a real check: `attribution_path` must resolve and parse as
a `BuildEvidence`; `attribute_sources_to_targets()` run over it must produce
a **non-empty** subset of the referenced pack's own TUs attributing to
`f"target://{target.id}"` — an inferred claim that resolves to zero matching
TUs is exactly the "silently degrades to nothing, tells no one" failure mode
this ADR exists to prevent, so it is a hard validation error, not a warning.
Unlike `"declared"`, two targets referencing the *same* physical
`evidence.path` pack is not itself an error for `"inferred"` targets — that
is the entire point: one build-wide pack, safely and automatically split.

### D5. Explicitly deferred, not built in this pass

Consistent with G30 P1.1's own precedent ("this PR defines the contract and
validates a hand-authored example... no producer tooling yet"), this ADR
delivers the attribution algorithm, the Make capture extension, the ingest
filtering mechanism, and the build-output.json validator — a complete,
independently testable, and independently usable core. It does **not** wire
`attribution`/`expected_target_id` through the `compare`/`dump` CLI's actual
`--build-info`/inputs-pack consumption path (`cli_buildsource_merge.py`,
`action/run.sh`, `actions/check-target`'s `evidence-pack-path` input) to
automatically produce and pass an `attribution_path` end-to-end in a real CI
run. That plumbing is real, additive, mechanical work once a project actually
needs it — deferred rather than retrofitted here, the same boundary §9 itself
drew between "the safe model's contract" and "the pipeline that populates
it." Tracked as the next slice, not re-opened as a new unscoped gap.

## Consequences

### Positive

- Closes the last standing item in ADR-047 §9/D8 with a real, tested
  mechanism rather than leaving it as a permanent "P2, not built" bullet.
- CMake and Make projects — the two most common real-world C/C++ build
  systems this tool targets (per `docs/contribute/plans/g30-...`'s own
  "minimal generic pilots" list) — gain genuine TU-to-target attribution;
  previously only Bazel had any.
- The attribution module is pure and side-effect-free, consistent with every
  other `buildsource/` module's testing posture (`CLAUDE.md` "Conventions").
- Fail-safe by construction: an unknown/ambiguous source is excluded, never
  silently included — the same posture `evidence.projection: "declared"`'s
  existing shared-pack rejection already established.

### Negative / risks

- The Make link-scraper is heuristic, like its compile-line sibling — a
  dry-run transcript is not an authoritative target graph, so Make-derived
  `"inferred"` claims carry the same "reduced confidence" caveat Make's
  compile units already carry.
- `attribution_path` is not yet produced by any real build integration —
  until D5's follow-up lands, a caller wanting to use `"inferred"` today must
  hand-author or script the `BuildEvidence` JSON themselves (the same
  "defines the contract, no producer yet" position G30 P1.1 shipped in for
  `build-output.json` itself, which a later PR (P1.2/P1.3) filled in).
- A source shared between two DSOs (legitimately compiled once, linked
  twice) now attributes to both — correct, but means "inferred" does not by
  itself prove *exclusive* ownership the way a hand-declared pack does;
  downstream consumers relying on exclusivity must still use `"declared"`.
- **Found during pilot validation (a real CMake File API + compile_commands.json
  build, not a synthetic fixture): `Target.source_files` (CMake File API,
  source-root-relative) and `CompileUnit.source` (`compile_db.py`, typically
  absolute — confirmed by hand: `/abs/path/src/demo.cpp` vs. `src/demo.cpp`
  for the identical file) do not share one path convention out of the box.**
  This does not affect the target-graph channel itself (it only ever reads
  `Target.source_files`, never joins against `CompileUnit`), but it does mean
  a real `abicheck_inputs` pack whose `SourceAbiTu.source` values were
  recorded in the *other* convention (e.g. absolute, matching how a
  clang-based wrapper naturally records them) will fail to match against a
  CMake-derived `attribution_path`'s relative keys — `normalize_source_path`
  only strips a leading `./` and normalizes separators, it does not resolve
  `..` or reconcile absolute-vs-relative. Fail-safe (an unmatched TU is
  excluded, never wrongly included), but less complete than a source-root-
  aware normalizer would be. Real path reconciliation belongs with D5's
  deferred pipeline-wiring work, once there's a concrete producer whose
  actual path conventions can be designed against, rather than guessed at
  here.

## Implementation plan

1. `abicheck/buildsource/link_attribution.py` — the pure algorithm (D1).
2. `abicheck/buildsource/adapters/make.py` — `_link_unit()` (D2).
3. `abicheck/buildsource/inputs_pack.py` — `ingest_inputs_pack`'s new
   optional filtering parameters (D3).
4. `abicheck/buildsource/build_output.py` — `attribution_path` field +
   `_inferred_evidence_projection_issues()` (D4).
5. Unit tests for all four, covering: target-graph attribution (direct +
   transitive static-lib fold), link-unit-graph attribution (direct +
   transitive static-lib fold), Make link-line scraping (shared/static/
   executable, `ar`, negative cases), ingest filtering (present/absent
   attribution, unknown-source exclusion), and the build-output validator's
   new accept/reject paths (mirroring the existing `"declared"` test
   coverage's shape).

## Validation

`pytest tests/test_link_attribution.py tests/test_make_adapter.py
tests/test_inputs_pack*.py tests/test_build_output.py -q`; `mypy
abicheck/buildsource/link_attribution.py`; `ruff check`;
`python scripts/check_ai_readiness.py` (no new errors).

## References

- ADR-047 §9/§11.1, D8 — the safe/full model split this closes the full half
  of.
- ADR-028 D3/D6 — post-build, non-executing adapter contract (Make's dry-run
  posture).
- ADR-029 D2/D6/D7 — `BuildEvidence`'s `LinkUnit` schema; Bazel's/Make's
  existing adapter design.
- ADR-035 D5 — Flow-2 `abicheck_inputs/` pack protocol `ingest_inputs_pack`
  extends here.
- `docs/contribute/plans/g30-github-actions-integration-model.md` — "P2 —
  Deeper architecture," the backlog entry this ADR resolves.
