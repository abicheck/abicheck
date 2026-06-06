# ADR-025: API Surface Intelligence — Structure Metrics, Idiom Detection, Cross-Library Reasoning, and Pattern-Aware Verdicts

**Date:** 2026-06-06
**Status:** Proposed
**Decision maker:** Nikolay Petrov

---

## Context

Today abicheck uses parsed headers in a **vertical** way: castxml
(`dumper_castxml.py`) turns headers into per-declaration records;
`provenance.py` tags each declaration with a `source_header` and a
`ScopeOrigin` (`PUBLIC_HEADER` / `PRIVATE_HEADER` / `SYSTEM_HEADER` /
`GENERATED` / `EXPORT_ONLY` / `UNKNOWN`, `model.py:333`); and that feeds
**per-symbol** diffing plus the public-surface scoping shipped in ADR-024.
The unit of reasoning is a single symbol or type compared against its twin.

That leaves a large amount of *already-captured* information unused. The
snapshot is in fact a **typed declaration graph** — functions referencing
parameter/return types, types referencing field/base/typedef types, all
carrying header provenance and visibility. ADR-024's `surface.py` already
walks the reachability closure over that graph; `internal_leak.py` already
does public→private reachability. The graph is there; we only ever query it
one edge at a time.

This ADR proposes treating the declaration graph as a first-class object and
extracting **horizontal** intelligence from it — structure, idioms,
cross-library relationships — and feeding that intelligence back into
verdicts. Four capabilities, deliberately specified together because they
share one substrate (the declaration graph + provenance) and one new internal
module (`abicheck/surface_graph.py`):

| # | Aspect | Decision unlocked |
|---|--------|-------------------|
| **A1** | **Surface structure & metrics** (single snapshot) | "What *is* this API, and is its public surface coherent?" — coverage, cohesion, undocumented-export detection. |
| **A2** | **Idiom & anti-pattern detection** (graph patterns) | "Which break rules actually apply here?" — opaque handles, PIMPL, factories, ABI anti-patterns. |
| **A3** | **Cross-library / product-structure reasoning** | "Does a change in `libA` break `libB` *in the same product*?" — transitive breaks the per-library view misses. |
| **A4** | **Pattern-aware verdicts** (diff-time) | Idiom/structure evidence *modulates* confidence and severity, and improves rename detection — turning new knowledge into better calls, not just more findings. |

### Why now / why together

- The enabling data (typed graph + provenance + reachability) shipped with
  ADR-024. These four capabilities are the *return on that investment*.
- They are **non-goal-respecting**: all static, offline, pure-Python; no new
  required dependency; no runtime instrumentation (per `goals.md` non-goals).
- They are additive to Goal 2 ("close gaps + extend") and Goal 5 (the break
  encyclopedia gains idiom-aware verdicts), and complement Goal 3 of ADR-023
  (bundle-aware multi-binary) by adding *type-level* cross-library reasoning
  on top of its *symbol-level* dependency graph.

### The risk we must design against (carried from ADR-024)

Inferred patterns are heuristics. An idiom guess that downgrades a real break
is exactly the silent-deletion failure ADR-024 was built to prevent. The
governing constraint is identical and inherited verbatim:

> **Pattern inference may *demote with a disclosed reason* or *raise* a
> finding; it may never silently delete one.** Every modulation is recorded,
> attributed to the rule that made it, and reversible via a flag.

---

## Decision (proposed)

### D0. Shared substrate — `abicheck/surface_graph.py` (new)

A single read-only module builds an indexed view over an `AbiSnapshot` that
all four capabilities consume. It owns no detection logic; it is the query
layer the existing one-edge-at-a-time call sites lack.

```python
# abicheck/surface_graph.py  (new, target < 600 lines)
@dataclass(frozen=True)
class SurfaceGraph:
    snapshot: AbiSnapshot
    # name → declaration, built once
    functions_by_name: Mapping[str, Function]
    types_by_name: Mapping[str, RecordType]
    # adjacency: type name → set of type names it references
    type_refs: Mapping[str, frozenset[str]]
    # inverse: type name → public roots that reach it (memoised closure)
    reached_by: Mapping[str, frozenset[str]]
    # provenance index: header path → declarations defined there
    by_header: Mapping[str, frozenset[str]]

    def public_roots(self) -> frozenset[str]: ...
    def reachable_types(self, root: str) -> frozenset[str]: ...
    def fan_in(self, type_name: str) -> int: ...
    def fan_out(self, type_name: str) -> int: ...
```

Construction reuses the closure walk already implemented in `surface.py`
(extract the private reachability helper into `surface_graph.py` and have
`surface.py` import it — no behavioural change, removes duplication). The
graph is **deterministic and order-stable** (sorted adjacency) so every
downstream metric is reproducible and cache-keyable.

---

### A1 — Surface structure & metrics

#### D1.1 Single-snapshot `surface-report`

A new read-only command, `abicheck surface-report <lib> [--header ...]`,
emits structural facts about *one* library's public surface (no diff). Home:
a new `abicheck/cli_surface.py` sibling module (per the "Adding a new
top-level command" recipe in `/CLAUDE.md`), registering on `main`.

Computed from the `SurfaceGraph`:

| Metric | Definition | Decision it informs |
|--------|------------|---------------------|
| **Header→symbol coverage** | For each public header, count of declarations that resolve to an exported symbol vs. declared-but-not-exported. | "These 12 declarations in `api.h` are documented but not shipped." |
| **Undocumented exports** | Exported symbols with `origin == EXPORT_ONLY` (no header declaration). | "37% of your exported surface has no public header — accidental ABI." |
| **Fan-in / fan-out per type** | `SurfaceGraph.fan_in/fan_out`. | Flags a "god type" every API touches (high blast radius if it changes). |
| **Header cohesion clusters** | Connected components of the type-reference graph restricted to one header's declarations. | Detects a header that is really N unrelated modules, or one that pulls in everything. |
| **Surface size** | Counts of public functions / types / enums / variables, with the EvidenceTier they were resolved at. | A trendable baseline (see D1.2). |

These are **reported, never enforced** by default — A1 is descriptive. The
output is text + a machine-readable `--format json` object (`surface_metrics`)
so it can be diffed externally or fed to A4.

#### D1.2 Metric drift (opt-in, diff-time)

When two snapshots are compared, the same metrics are computed for old and
new and the *deltas* surfaced under a new informational change family. These
are **COMPATIBLE_KINDS** (never breaking on their own):

- `PUBLIC_SURFACE_GREW` / `PUBLIC_SURFACE_SHRANK` — net public-declaration
  count delta (additions/removals are already detected per-symbol; this is the
  *aggregate* signal, useful for CI dashboards and release notes).
- `UNDOCUMENTED_EXPORT_RATIO_INCREASED` — the EXPORT_ONLY fraction rose
  (a packaging-hygiene regression: someone exported a symbol without a header).

Both are emitted only with `--surface-metrics` (off by default) so existing
output is unchanged.

---

### A2 — Idiom & anti-pattern detection

#### D2.1 Idiom recognisers (`abicheck/idioms.py`, new)

A registry of pure, deterministic recognisers over the `SurfaceGraph`, each
mapping a declaration (or pair) to an `Idiom` tag with a confidence. They run
at **dump time** and persist onto the snapshot (schema bump, see D2.4) so the
classification is auditable and the diff stage stays source-agnostic.

```python
class Idiom(str, Enum):
    OPAQUE_POINTER   = "opaque_pointer"    # type only ever crossed by pointer; never by value
    PIMPL            = "pimpl"             # public type whose only data member is a pointer to a private/incomplete type
    HANDLE           = "handle"            # typedef of void* / forward-declared struct ptr used as a token
    FACTORY          = "factory"           # exported fn returning a pointer to an abstract/base type
    CREATE_DESTROY   = "create_destroy"    # paired create_X / destroy_X (or _new/_free) lifecycle fns
    CALLBACK_ABI     = "callback_abi"      # function-pointer-typed parameter/field (ABI-sensitive)
    OUT_PARAM        = "out_param"         # non-const pointer/ref parameter written through
```

Each recogniser is intentionally conservative: it tags only when the graph
evidence is unambiguous, and records *why* (the edges that matched) for the
ledger. Recognition uses facts already in the model — `ParamKind`,
`pointer_depth` (`model.py:Param`), field types, `RecordType.is_opaque` /
incomplete markers, base-class lists, vtables.

**Worked example — opaque pointer.** A `RecordType T` is `OPAQUE_POINTER`
iff: every public function that references `T` does so only through
`pointer_depth >= 1` parameters/returns (never by value), **and** `T` has no
public data members in the surface closure. The payoff is A4: a *size or field
change* to a type that is provably only crossed by pointer is **not** an ABI
break for callers (they never embed it), so the finding is demoted with reason
`opaque-by-construction` — but only when the idiom holds on **both** old and
new snapshots (a type that *stops* being opaque is itself a real change, see
D2.2).

#### D2.2 Anti-pattern detectors (new ChangeKinds)

Anti-patterns are graph properties that are *findings in their own right*,
independent of any diff (single-snapshot) or as transitions (diff-time). They
extend the existing leak family in `internal_leak.py` rather than starting a
new subsystem:

| ChangeKind | Category | Condition |
|------------|----------|-----------|
| `PUBLIC_API_EXPOSES_STL_BY_VALUE` | `RISK` | Public function takes/returns a `std::` type by value across the boundary (notoriously ABI-fragile across toolchains; ties into ADR-020 build context). |
| `POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR` | `RISK` | A type with virtual methods (has vtable) used as a `FACTORY` return / base, but no virtual destructor — `delete` through base is UB. |
| `OPAQUE_INVARIANT_BROKEN` | `BREAKING` | A type that was `OPAQUE_POINTER`/`PIMPL` in old gains a by-value public use in new (the opaqueness guarantee that callers relied on is gone). |
| `HANDLE_TYPE_CHANGED` | `BREAKING` | A `HANDLE` typedef's underlying token type changed in a way callers can observe. |

Single-snapshot anti-patterns (the `RISK` ones) are reported by
`surface-report` (A1) and, at diff time, only when *newly introduced* (old
clean → new dirty), so we never nag about pre-existing debt on every run.

#### D2.3 Naming-convention & versioning inference

A lightweight inference pass (`idioms.py::infer_conventions`) derives the
project's *own* scheme from the public surface, then uses it to reduce
false positives in A4:

- **Symbol prefix / namespace** — the dominant common prefix or top-level
  namespace (e.g. `foo_`, `Foo::`). Used to recognise that `foo_v2_open`
  next to `foo_open` is an **intentional versioned addition**, not an
  accidental near-duplicate.
- **Inline-namespace / abi_tag versioning** — already parsed
  (`diff_abi_tags.py`, inline-namespace handling); this pass *aggregates* it
  into a per-snapshot "versioning style" so A4 can treat a coordinated
  `v1`→`v2` inline-namespace bump as a managed transition rather than a wall
  of symbol churn.

Inference is descriptive metadata only; it never changes a verdict by itself,
only feeds A4's modulation with a disclosed rationale.

#### D2.4 Persistence

Idiom tags and inferred conventions are persisted on the snapshot
(`AbiSnapshot.idioms: dict[str, list[str]]`, `AbiSnapshot.conventions`) behind
an `ADR-015` schema bump. Older snapshots without the fields degrade to
"no idiom evidence" → A4 modulation simply doesn't fire (safe default). Dump
without idiom analysis (`--no-idioms`) leaves the fields empty.

---

### A3 — Cross-library / product-structure reasoning

ADR-023 (bundle-aware) and ADR-006/008 (package / full-stack) already model a
*product* as a set of binaries with a **symbol-level** dependency graph
(`needed_libs`, undefined symbols, `appcompat.py`). A3 adds **type-level**
and **surface-level** cross-library reasoning on top of that existing graph —
it does not introduce a new package model.

#### D3.1 Product surface graph

When a comparison runs over a package / multi-binary bundle (the
`compare-release` / bundle path), build a **product-level** index: the union
of per-library `SurfaceGraph`s plus the inter-library edges already resolved
by the appcompat/bundle layer (which exported symbol in `libA` satisfies which
undefined symbol in `libB`).

#### D3.2 Cross-library transitive break detection (new ChangeKinds)

Today each `.so` is diffed in isolation, so a removal in `libA` that `libB`
*in the same product* depends on is invisible to the per-library verdict. With
the product graph:

| ChangeKind | Category | Condition |
|------------|----------|-----------|
| `CROSS_LIBRARY_SYMBOL_BREAK` | `BREAKING` | A symbol removed/changed-incompatibly in `libA` is in the resolved `needed` set of a sibling `libB` in the product. |
| `CROSS_LIBRARY_TYPE_LAYOUT_BREAK` | `BREAKING` | A public type changed layout in `libA` and is reachable from `libB`'s public surface through a shared header (provenance-matched `source_header`). |
| `PRODUCT_SURFACE_INCONSISTENT` | `RISK` | A header declares an API but no shipped library in the product exports it (or two libraries export the *same* symbol with divergent signatures). |

These are emitted **in addition to** the per-library findings, attributed to
the `(producer, consumer)` library pair so the report shows the propagation
path. They are gated on `--product`/bundle mode and never fire in
single-library comparisons (no false product edges).

#### D3.3 SDK-level verdict roll-up

A product comparison currently yields *N* independent verdicts the user must
mentally merge. A3 adds a single **product verdict** computed over the
dependency DAG: the worst per-library verdict, *plus* any cross-library break
from D3.2, with the propagation path attached. Exit-code contract: the product
command returns the max of the contributing exit codes (consistent with the
existing `compare` contract in `/CLAUDE.md` → "Exit codes"). Per-library
verdicts remain available in the detailed/JSON output — the roll-up is an
overlay, not a replacement.

---

### A4 — Pattern-aware verdicts (the payoff)

A4 is where A1–A3 stop being reports and start changing *decisions*. It is a
**post-processing modulation pass** (`abicheck/pattern_verdicts.py`, new)
that runs after detectors produce `Change` objects and before policy
classification — structurally the same insertion point and the same
"demote/raise with a ledger" contract as ADR-024's `FilterNonPublicSurface`.

#### D4.1 Modulation rules

Each rule takes a `Change` + both `SurfaceGraph`s and may adjust `confidence`
(`checker_types.Change.confidence`) and/or move the finding between
categories, **always** writing a `modulation_reason` and the rule id:

| Rule | Effect | Guard (anti-hiding) |
|------|--------|---------------------|
| **Opaque-aware layout** | `TYPE_SIZE_CHANGED` / `TYPE_FIELD_*` on an `OPAQUE_POINTER`/`PIMPL` type → demote to compatible, reason `opaque-by-construction`. | Idiom must hold on **both** snapshots; if opaqueness was *lost*, emit `OPAQUE_INVARIANT_BROKEN` (D2.2) instead — never silent. |
| **Versioned-addition** | A near-duplicate symbol matching the inferred version scheme (D2.3) → treat as managed addition, not accidental churn. | Only suppresses the *noise* classification; the addition is still reported as `FUNC_ADDED`. |
| **Anti-pattern raise** | A change *on* a `POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR` / STL-by-value surface → raise confidence / annotate elevated risk. | Pure raise; cannot hide. |
| **Confidence floor by tier** | Modulation that *demotes* is only permitted at `HEADER_AWARE` evidence tier (idioms need the AST). At `ELF_ONLY`/`DWARF_AWARE`, demotion is disabled; the finding stands. | Demotion requires the evidence that justified it. |

#### D4.2 Idiom-aware rename detection

`binary_fingerprint.py` detects renames via code-hash on stripped binaries.
A4 adds a **type-signature fingerprint**: two functions whose
parameter/return *type-reference closure* is identical (modulo the renamed
symbol) across old/new are rename candidates even when the code hash differs
(e.g. recompiled with different flags). This raises rename-detection recall
without new false positives, because it requires structural type-graph
equality, not just name similarity. Emitted as the existing rename
ChangeKind with elevated confidence, not a new kind.

#### D4.3 Auditability (inherited contract)

Every modulation is disclosed exactly like the ADR-024 surface ledger:

- A `pattern_modulations` array in JSON / SARIF: `{symbol, original_category,
  new_category, rule_id, reason, evidence_tier, edges_matched}`.
- `--no-pattern-verdicts` disables all modulation (findings as raw detectors
  produced them) for diffing/debugging.
- `--explain-patterns` prints, per modulated finding, the idiom evidence that
  drove the call.
- A demotion that would move an `abi_breaking` finding to compatible requires
  the idiom to hold on both snapshots **and** is logged at WARN in the ledger
  — break-demotion is never quiet (mirrors ADR-024 §D5.4).

---

## Data-model & API surface changes

| Surface | Change | Compatibility |
|---------|--------|---------------|
| `model.py` | `AbiSnapshot.idioms`, `.conventions`; helper `RecordType` opaque/handle flags if not already derivable. | Additive; schema bump (ADR-015). Old snapshots → empty → safe no-op. |
| `checker_types.py` | `Change.modulation_reason: str \| None`, `.modulation_rule: str \| None`. | Additive dataclass fields with defaults. |
| `checker_policy.py` | New `ChangeKind`s (A1.2, A2.2, A3.2) each placed in exactly one of `BREAKING/API_BREAK/COMPATIBLE/RISK` (import-time partition assertion enforces it). | Enum grows; follow the 4-step `/CLAUDE.md` procedure. |
| `surface.py` | Extract reachability helper into `surface_graph.py`, import back. | Internal refactor, no behaviour change. |
| New modules | `surface_graph.py`, `idioms.py`, `pattern_verdicts.py`, `cli_surface.py`. | All < 600 lines (AI-readiness file-size gate). |
| CLI | `surface-report` command; `--surface-metrics`, `--idioms/--no-idioms`, `--pattern-verdicts/--no-pattern-verdicts`, `--explain-patterns`, `--product` flags. | Opt-in; defaults preserve current behaviour except `--pattern-verdicts` (see phasing — default-on only after validation). |

All new ChangeKinds must also satisfy the AI-readiness gates: partition
(ERROR), produced-somewhere (`changekind-detector` WARN), documented in
`docs/` (`changekind-docs` WARN), and headline-count sync (`doc-count-sync`
ERROR — update the ChangeKind count wherever it is asserted).

---

## Validation & testing strategy

The credibility bar is the same as ADR-024: prove the patterns neither
over- nor under-fire, and that modulation can never hide a real break.

1. **Idiom golden corpus.** New `examples/caseXXX_*` fixtures, one per idiom
   and anti-pattern (opaque pointer, PIMPL, handle, factory, create/destroy,
   STL-by-value, non-virtual-dtor base), each with a `README.md` and a
   `ground_truth.json` entry (AI-readiness `examples-ground-truth` ERROR
   gate). Assert the recogniser tags exactly the expected declarations.
2. **Anti-hiding negative tests (most important).**
   - A real layout break on a **non**-opaque public type still fires at full
     severity (modulation must not touch it).
   - A type that *loses* opaqueness emits `OPAQUE_INVARIANT_BROKEN`, not a
     silent demotion.
   - Demotion is refused below `HEADER_AWARE` tier.
3. **Property-based** (`slow`, hypothesis, extends
   `tests/test_detector_properties.py`):
   - *Modulation subset:* the pattern-aware finding set, projected back to
     categories, removes/demotes only — never invents a break.
   - *Determinism / order-independence* of graph construction and idiom tags.
   - *Idempotence:* re-running modulation on its own output is a fixed point.
4. **Cross-library** (A3): bundle fixtures where a removal in one `.so` is
   consumed by a sibling; assert `CROSS_LIBRARY_SYMBOL_BREAK` fires with the
   correct producer→consumer path, and does **not** fire in single-library
   mode.
5. **FP-rate gate.** Extend the labelled corpus in `scripts/check_fp_rate.py`
   (and `tests/test_fp_rate_gate.py`) with idiom cases: opaque-pointer layout
   changes must stay non-breaking; non-opaque ones must stay breaking. Both
   baselines remain 0.
6. **Mutation testing.** Add `idioms.py`, `pattern_verdicts.py`,
   `surface_graph.py` to the `mutmut` target set in
   `scripts/check_mutation_score.py` so the modulation logic is held to the
   same survivor baseline as the detector core.
7. **Metric stability** (A1): `surface-report` JSON is snapshot-tested under
   the `golden` marker so metric definitions don't drift silently.

---

## Implementation phasing

| Phase | Scope | Gate to advance |
|-------|-------|-----------------|
| **0** | `surface_graph.py` substrate (D0) + refactor `surface.py` to use it. No user-visible change. | Existing suite green; no behavioural diff. |
| **1 (A1)** | `surface-report` command + single-snapshot metrics (D1.1). Descriptive only. | Golden metric snapshots; docs page. |
| **2 (A2)** | Idiom recognisers + anti-pattern ChangeKinds (D2), persisted on snapshot (schema bump). Reported, not yet modulating. | Idiom golden corpus passes; partition/docs gates green. |
| **3 (A4)** | Pattern-aware modulation (D4) **opt-in** (`--pattern-verdicts`). Ledger + `--explain-patterns`. | All anti-hiding negative tests + FP-rate gate green. |
| **4 (A3)** | Cross-library reasoning + product roll-up (D3), gated on bundle/`--product` mode. | Bundle fixtures; no single-library regressions. |
| **5** | Metric-drift kinds (D1.2); flip `--pattern-verdicts` to default-on once the FP-rate corpus and parity lanes validate it (with `--no-pattern-verdicts` opt-out), exactly as ADR-024 flipped `header-scoped`. | FP-rate + parity stable across a release cycle. |

Each phase ships independently and leaves the tool fully working; nothing
before Phase 5 changes a default verdict.

---

## Alternatives considered

| Option | Why not |
|--------|---------|
| Keep per-symbol-only analysis (status quo) | Leaves the declaration graph, idioms, and cross-library edges unused; the four decisions above remain unmakeable. |
| **Hard** idiom-based suppression (drop opaque-type findings) | Repeats the libabigail `--headers-dir` mistake ADR-024 rejected — loses auditability and can hide a lost-opaqueness break. Chosen: demote + disclose. |
| Modulate verdicts inline inside each detector | Scatters pattern logic across 30 `diff_*` modules; couples detection to inference. Chosen: a single post-processing pass with a ledger, mirroring `FilterNonPublicSurface`. |
| Require libclang (richer AST) for idioms | Heavyweight, violates the lightweight-core posture; castxml + DWARF already expose pointer-depth, fields, bases, vtables — enough for the conservative recognisers here. libclang (G4) would *extend* recall later, not gate this. |
| Push cross-library logic into ADR-023 bundle layer only | ADR-023 is symbol-level; A3 needs the *type-level* reachability graph this ADR introduces. A3 builds **on** ADR-023's edges rather than duplicating them. |

---

## Consequences

**Positive:** fewer false positives on idiomatic ABI-stable patterns
(opaque/PIMPL); new real breaks caught (cross-library propagation, lost
opaqueness, handle changes); a descriptive `surface-report` for API hygiene
and release notes; a single product verdict for multi-binary releases; better
rename recall — all from data already captured, with no new required
dependency and no runtime analysis. Every pattern-driven decision is
attributed and reversible.

**Negative / risks:** idiom recognisers are heuristics — kept conservative and
gated to `HEADER_AWARE` for any *demotion*, with the anti-hiding negative-test
suite and FP-rate gate as the safety net; a schema bump and snapshot-cache key
change (idiom fields participate in the key); four new modules and several new
ChangeKinds to keep within the AI-readiness structural gates; cross-library
accuracy depends on correct product-edge resolution (inherited from
ADR-023/006), so A3 is gated to explicit bundle/product mode to avoid inventing
edges in the common single-library case.

## References

- ADR-006 — Package-Level Comparison (product model A3 builds on)
- ADR-008 — Full-Stack Dependency Validation (symbol-level cross-library edges)
- ADR-011 — Change Classification Taxonomy (where the new ChangeKinds live)
- ADR-015 — Snapshot Serialization (schema bump for idiom/convention fields)
- ADR-016 — Three-Tier Visibility Model
- ADR-020 — Build-Context Aware Header Extraction (STL-by-value risk depends on it)
- ADR-023 — Bundle-Aware Multi-Binary Analysis (A3 extends its dependency graph to types)
- ADR-024 — Public ABI Surface Resolution (the demote-don't-delete contract and the
  reachability closure A4 reuses; `FilterNonPublicSurface` is the structural template
  for `pattern_verdicts.py`)
- Plan G4 — libclang header-AST extractor (future recall extension for idioms)
- `abicheck/surface.py`, `abicheck/internal_leak.py`, `abicheck/binary_fingerprint.py`,
  `abicheck/provenance.py`, `abicheck/model.py` (`ScopeOrigin`), `abicheck/checker_types.py`
