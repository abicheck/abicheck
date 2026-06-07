# ADR-027: API Surface Intelligence — Structure Metrics, Idiom Detection, Cross-Library Reasoning, and Pattern-Aware Verdicts

**Date:** 2026-06-06
**Status:** Accepted
**Decision maker:** Nikolay Petrov

> **Implementation status (2026-06-07).** All phases have shipped:
> Phase 0 (`surface_graph.py`), Phase 1 (`surface-report` / A1 metrics),
> Phase 2 (`idioms.py` recognisers + the four D2.2 anti-pattern `ChangeKind`s),
> Phase 3 (A4 pattern-aware verdicts: `pattern_verdicts.py`, the per-finding
> `effective_verdict` override threaded through every classification site,
> `--pattern-verdicts` / `--explain-patterns`, the `pattern_modulations`
> ledger), Phase 4 (A3 cross-library: the `BUNDLE_INTRA_TYPE_CHANGED`
> reachability filter in `bundle.py` — a cross-DSO type change a consumer uses
> only via internal/non-exported symbols is demoted to risk via a per-finding
> `BundleFinding.effective_verdict`, propagated onto the lowered `Change` so the
> bundle verdict and the `compare-release` exit code honour it), and Phase 5
> (the D1.2 metric-drift `ChangeKind`s behind `--surface-metrics`). Idiom
> evidence is recomputed at diff time from the persisted declaration graph
> rather than serialized as bare tag names (D2.4 intent), so no schema bump was
> required; A3 uses ELF exported-symbol membership as the consumer public-surface
> proxy (the bundle path carries ELF metadata, not full type graphs).
> **Still deferred by design:** flipping `--pattern-verdicts` to default-on,
> which the ADR gates on FP-rate + parity stability across a release cycle.

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

## Decision

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
```

> **OUT_PARAM is deliberately *not* a recognised idiom.** Detecting that a
> pointer/reference parameter is genuinely *written through* requires body/IR
> evidence (write effects), which the header/declaration graph does not carry —
> a non-`const` pointer like `int lookup(Foo *key)` is input-only. Inferring it
> from declaration facts alone would mis-tag ordinary pointer parameters, so it
> is omitted from the modulating recognisers; if a purely *descriptive*
> `may_out_param` hint is ever wanted it must be marked as such and must **not**
> be allowed to drive verdict modulation. The `idioms.py` implementation omits
> it accordingly.

Each recogniser is intentionally conservative: it tags only when the graph
evidence is unambiguous, and records *why* (the edges that matched) for the
ledger. Recognition uses facts already in the model — `ParamKind`,
`pointer_depth` (`model.py:Param`), field types, `RecordType.is_opaque` /
incomplete markers, base-class lists, vtables.

**Worked example — opaque pointer.** Pointer-only *usage* is **not enough** to
call a type opaque: if `T`'s full definition is visible in a public header, a
caller can `sizeof(T)`, stack-allocate it, embed it, or read its layout from
inline/header code regardless of how the *exported* functions pass it — so a
size/field change is still ABI-breaking. The recogniser therefore tags `T` as
`OPAQUE_POINTER` only when **all** of:

1. `T`'s complete definition is **not visible in the public include closure** —
   i.e. when the supplied public headers are preprocessed, `T` is only ever
   *incomplete* (forward-declared), never completed by any transitive
   `#include`. The reliable signal is `RecordType.is_opaque` as observed by the
   parser on the public-header translation unit: if a public header (even
   transitively) pulls in the full definition, castxml sees `T` complete and
   this condition is **false**. **Provenance classification alone is *not*
   sufficient** — a `PRIVATE_HEADER` origin only means "outside the explicitly
   supplied public set", but ADR-024 notes castxml parses transitively-included
   private headers, and a user compiling the public header sees that definition
   too (`sizeof(T)`, inline layout). So a complete `T` reachable through a
   public header is observable and must **not** be treated as opaque, regardless
   of which header file its definition physically sits in. This is the
   load-bearing condition: it proves callers cannot allocate or observe the
   layout.
2. every public function that references `T` does so only through
   `pointer_depth >= 1` (never by value), and
3. `T` exposes no public data members in the surface closure.

The payoff is A4: a size/field change to a type callers provably cannot see or
embed is **not** an ABI break, so it is demoted with reason
`opaque-by-construction` — but only when condition (1) holds on **both**
snapshots. A type whose definition *becomes* visible (or that gains a by-value
public use) has *lost* opaqueness, which is itself a real change → emit
`OPAQUE_INVARIANT_BROKEN` (D2.2), never a silent demotion.

**PIMPL is *not* the same as opaque-pointer, and is treated differently.** A
PIMPL wrapper is a **complete** public type: callers can `sizeof` it, embed it,
or stack-allocate it, so its **own** layout (its size and its single
impl-pointer field) is part of the ABI and a change to it is a real break. Only
the *pointee* — the private/incomplete `struct Impl` behind the pointer — is
hidden from callers. The recogniser therefore records, for a `PIMPL` type, both
the wrapper's own layout signature and the identity of the hidden pointee, so
A4's `PIMPL pointee-only` rule (D4.1) can demote a change to the pointee while
keeping any change to the wrapper itself breaking. Conflating the two (demoting
a wrapper that gains a second member) would hide a genuine layout break — the
explicit failure mode this split avoids.

#### D2.2 Anti-pattern detectors (new ChangeKinds)

Anti-patterns are graph properties that are *findings in their own right*,
independent of any diff (single-snapshot) or as transitions (diff-time). They
extend the existing leak family in `internal_leak.py` rather than starting a
new subsystem:

| ChangeKind | Category | Condition |
|------------|----------|-----------|
| `PUBLIC_API_EXPOSES_STL_BY_VALUE` | `RISK` | Public function takes/returns a `std::` type by value across the boundary (notoriously ABI-fragile across toolchains; ties into ADR-020a build context). |
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

Idiom tags and inferred conventions are persisted on the snapshot behind an
`ADR-015` schema bump. Crucially, the persisted form is **structured evidence,
not bare tag names** — a later `--pattern-verdicts` / `--explain-patterns` run
loaded from a `.abi.json` must be able to enforce D2.1 confidence thresholds,
prove the both-snapshots anti-hiding guards (D4.1), and populate the ledger's
`edges_matched` (D4.3) entirely from what was saved. So:

```python
@dataclass
class IdiomTag:
    idiom: Idiom
    confidence: Confidence            # so D4.1 thresholds survive serialization
    evidence: list[str]               # the matched edges/reasons → ledger edges_matched
    # idiom-specific proof needed by the both-snapshots guards:
    layout_signature: str | None = None   # OPAQUE/PIMPL wrapper's own layout (D4.1 PIMPL guard)
    hidden_pointee: str | None = None      # PIMPL impl pointee identity
    definition_hidden: bool = False        # T incomplete in the public include closure (D2.1 cond.1)

# AbiSnapshot.idioms: dict[str, list[IdiomTag]]   # declaration name → tags
# AbiSnapshot.conventions: ...
```

A tag with only its name would let a loaded run know a declaration *was*
`OPAQUE_POINTER` but not at what confidence, nor whether the definition-hidden
condition held — so it could neither apply the tier/threshold gates nor show the
evidence. Persisting the `IdiomTag` record closes that gap and keeps the diff
stage source-agnostic (it reads evidence, never re-derives it). Older snapshots
without the field degrade to "no idiom evidence" → A4 modulation simply doesn't
fire (safe default). Dump without idiom analysis (`--no-idioms`) leaves it empty.

---

### A3 — Cross-library / product-structure reasoning

ADR-023 (bundle-aware) and ADR-006/008 (package / full-stack) already model a
*product* as a set of binaries with a **symbol-level** dependency graph
(`needed_libs`, undefined symbols, `appcompat.py`), and `abicheck/bundle.py`
already **emits cross-library findings** — `BUNDLE_INTRA_DEP_REMOVED`,
`BUNDLE_INTRA_DEP_SIGNATURE_CHANGED`, and `BUNDLE_INTRA_TYPE_CHANGED` (the last
already covering cross-DSO `TYPE_SIZE_CHANGED`/`TYPE_FIELD_*`/`TYPE_VTABLE_CHANGED`
between sibling libraries). **A3 does not add a parallel detector or new
`CROSS_LIBRARY_*` kinds** — that would duplicate reporting and churn the enum /
`doc-count-sync`. Instead A3 *tightens* the existing `bundle.py` detectors with
the **type-level reachability** the `SurfaceGraph` makes available, and adds at
most one genuinely-new surface-consistency kind. It introduces no new package
model.

#### D3.1 Product surface graph

When a comparison runs over a package / multi-binary bundle (the
`compare-release` / bundle path), build a **product-level** index: the union
of per-library `SurfaceGraph`s plus the inter-library edges already resolved
by the appcompat/bundle layer (which exported symbol in `libA` satisfies which
undefined symbol in `libB`).

#### D3.2 Tightening the existing bundle detectors (no new break kinds)

`bundle.py` already detects sibling symbol removals
(`BUNDLE_INTRA_DEP_REMOVED`), signature drift on consumed symbols
(`BUNDLE_INTRA_DEP_SIGNATURE_CHANGED`), and cross-DSO type layout changes
(`BUNDLE_INTRA_TYPE_CHANGED`). A3's contribution is **precision, not new
kinds**: today `BUNDLE_INTRA_TYPE_CHANGED` fires whenever a type shared across
two DSOs changes layout, even if the consumer never exposes that type on its
*own* public surface. The `SurfaceGraph` lets us add a **reachability filter**:

| Existing kind | A3 refinement (reuse, don't replace) |
|---------------|--------------------------------------|
| `BUNDLE_INTRA_TYPE_CHANGED` | Only emit (or emit at full confidence vs. reduced) when the changed type is **reachable from the consumer library's own public surface** via its `SurfaceGraph`; a layout change to a type the consumer uses only internally is demoted, not dropped (same ledger contract as A4). |
| `BUNDLE_INTRA_DEP_REMOVED` / `BUNDLE_INTRA_DEP_SIGNATURE_CHANGED` | Unchanged in *what* they fire on; A3 only enriches the finding with the `(producer → consumer)` reachability path for the report. |

**The demotion must reach the bundle verdict, not just the per-library one.**
`BundleFinding` (`bundle.py`) is a *separate* type from `Change`: its
`to_change()` builds a fresh `Change` carrying only kind/symbol/description, and
`BundleDiffResult.bundle_verdict` runs `compute_verdict()` over those lowered
changes. So a reachability demotion expressed only on the per-library `Change`
path would be **dropped** on the bundle path — `bundle_verdict` would still see
the raw `BUNDLE_INTRA_TYPE_CHANGED` as breaking. The D4.1 override mechanism
therefore extends to the bundle path identically: `BundleFinding` gains the same
`effective_verdict` / `modulation_reason` / `modulation_rule` fields,
`to_change()` propagates them onto the lowered `Change`, and `bundle_verdict`
(plus the `compare-release` JSON/SARIF and its exit-code path) classifies via
the shared `effective_category(...)` helper — never bare `compute_verdict()` on
the raw kind. The demoted finding stays in `bundle_findings` (disclosed in the
report), re-categorised in place, never dropped.

The type→consumer match still leans on shared `source_header`, which is
inherently fuzzy — provenance paths are build-time absolute paths matched on
segments (`provenance.py` documents this), so two libraries built in different
trees may spell the same header differently. The reachability filter therefore
treats a header match as *corroborating* evidence layered on the type's
fully-qualified name + layout signature (the primary key), never the sole
trigger; `--product`/bundle gating bounds the blast radius. Dedicated bundle
fixtures with divergent build-path prefixes pin this (§A3 validation).

The **one** potentially-new kind A3 needs is a surface-consistency check —
*"a public header declares an API that no shipped library in the product
exports"* (or two libraries export the same symbol with divergent signatures).
This is not expressed by any current `BUNDLE_*` kind
(`BUNDLE_LIBRARY_REMOVED`/`_ADDED`, `BUNDLE_PROVIDER_CHANGED`,
`BUNDLE_SONAME_SKEW`, `BUNDLE_INTRA_*` are all about *linkage* between shipped
libraries, not header-vs-shipped consistency). If, on implementation, it cannot
be folded into an existing kind, add a single `PRODUCT_SURFACE_INCONSISTENT`
(`RISK`) following the 4-step ChangeKind procedure; otherwise reuse the closest
existing kind. Either way, **no `CROSS_LIBRARY_*` family is introduced.**

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

Each rule takes a `Change` + both `SurfaceGraph`s and may adjust the finding's
**own** `confidence` (a *new* per-finding `Change.confidence` field — see the
data-model table; this is distinct from the existing verdict-level
`DiffResult.confidence`) and/or change its **effective category** (see the
mechanism below), **always** writing a `modulation_reason` and the rule id:

| Rule | Effect | Guard (anti-hiding) |
|------|--------|---------------------|
| **Opaque-pointer layout** | `TYPE_SIZE_CHANGED` / `TYPE_FIELD_*` on an `OPAQUE_POINTER` type whose **complete definition is not reachable through the public include closure** (incomplete when the public headers are preprocessed — D2.1 condition 1) → demote to compatible, reason `opaque-by-construction`. | The definition-hidden condition must hold on **both** snapshots; if the definition *became* visible or a by-value public use appears, opaqueness was lost → emit `OPAQUE_INVARIANT_BROKEN` (D2.2) instead — never silent. A type whose full definition is reachable via a public header — even a *transitively-included* private one — is observable (`sizeof`/inline) and is **never** demoted, regardless of provenance classification. |
| **PIMPL pointee-only** | A layout change to the *private/incomplete impl pointee* of a `PIMPL` type → demote, reason `pimpl-impl-hidden`. | **Strictly scoped:** the public wrapper is itself a complete type callers can `sizeof`/embed/stack-allocate, so a change to the **wrapper's own** layout (its size, or its single impl-pointer field) is **never** demoted — it stays breaking. Demotion fires only when the wrapper's own layout is byte-identical across both snapshots **and** only the hidden pointee changed. A wrapper gaining a second data member is a real break (and likely also `OPAQUE_INVARIANT_BROKEN`). |
| **Versioned-addition** | A near-duplicate symbol matching the inferred version scheme (D2.3) → treat as managed addition, not accidental churn. | Only suppresses the *noise* classification; the addition is still reported as `FUNC_ADDED`. |
| **Anti-pattern raise** | A change *on* a `POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR` / STL-by-value surface → raise confidence / annotate elevated risk. | Pure raise; cannot hide. |
| **Confidence floor by tier** | Modulation that *demotes* is only permitted at `HEADER_AWARE` evidence tier (idioms need the AST). At `ELF_ONLY`/`DWARF_AWARE`, demotion is disabled; the finding stands. | Demotion requires the evidence that justified it. |

**Mechanism — per-finding effective category (the missing link).** Today a
finding's category is derived *purely from its `kind`*: the
`DiffResult.breaking`/`source_breaks`/`compatible`/`risk` properties filter
`c.kind in <set>` against `_effective_kind_sets()`, and the existing
`policy_file.overrides` path can only move a **whole `ChangeKind`** between
sets, policy-wide — it cannot demote *one* `TYPE_SIZE_CHANGED` finding while
leaving its siblings breaking. So a modulation that merely sets a confidence
field would **not** change reports or the exit code; the opaque-layout demotion
would be cosmetic. This ADR therefore adds a **per-finding override**:

- New field `Change.effective_verdict: Verdict | None = None` (default `None` =
  "classify by `kind`", i.e. today's behaviour exactly).
- A single shared helper — `effective_category(change, kind_sets) -> Verdict`
  (returns `change.effective_verdict` when set, else derives the category from
  `change.kind ∈ kind_sets`) — becomes the **one** place category is decided.
  **Every** site that today buckets by `c.kind in <set>` must route through it,
  not just the `DiffResult` properties. Concretely that is: the four
  `DiffResult.breaking`/`source_breaks`/`compatible`/`risk` properties and
  `compute_verdict()` (exit code); **`reporter.py`** — `_change_to_dict`, the
  `filtered_summary` counts, and the type/non-type category splits (all
  currently keyed on `c.kind in eff_breaking`, etc.); and **`severity.py`** —
  `categorize_changes` and `compute_exit_code`, which classify with kind sets
  for the severity-aware exit codes. If any one of these is missed, a demoted
  `TYPE_SIZE_CHANGED` could still serialize or count as `breaking` and emit exit
  code 4 under `--severity-*` options — so honouring the override is a
  **completeness requirement across all classification sites**, enforced by the
  validation matrix below (a demoted finding must read compatible in *every*
  output: text, JSON `changes` + `filtered_summary`, SARIF, JUnit, and both
  exit-code paths). This is the per-finding analogue of the existing kind-level
  `_effective_kind_sets()` move, evaluated *after* it.
- Precedence / anti-hiding: the existing `frozen_namespace_violation` guard
  (`checker_types.Change`) and any policy that blocks downgrades take
  precedence — a pattern demotion can **never** override a frozen-namespace
  break. A demotion that would lower an `abi_breaking` finding requires the
  idiom to hold on both snapshots, is gated to `HEADER_AWARE`, and is logged at
  WARN in the `pattern_modulations` ledger (D4.3). The demoted finding stays in
  `DiffResult.changes` (visible in every report) with its `effective_verdict`,
  `modulation_reason`, and `modulation_rule` recorded — it is re-categorised in
  place, never moved to a hidden list or silently dropped.

This keeps the demotion **auditable and reversible** (`--no-pattern-verdicts`
restores pure kind-based classification) and avoids doubling the `ChangeKind`
enum with `*_OPAQUE` compatible-variant kinds (see Alternatives).

#### D4.2 Idiom-aware rename detection

`binary_fingerprint.py` detects renames via size + code-hash, gated by
uniqueness and `_plausible_rename`; a confirmed rename **suppresses** the paired
`FUNC_REMOVED`/`FUNC_ADDED` as redundant. A4 adds a **type-signature
fingerprint** (the parameter/return *type-reference closure*) as *one more
corroborating signal*, **never a standalone matcher** — because a type closure
is emphatically **not unique** (a library may have many `int(void)` accessors),
so pairing on it alone could marry an unrelated removal to an unrelated
addition and, via that suppression, **hide a real breaking removal** as a
compatible rename. The guards are therefore:

- **Uniqueness required.** The fingerprint may only promote a pair when the
  closure is unique on *both* sides — exactly one removed and one added function
  carry it. Any ambiguity (≥2 candidates either side) ⇒ no rename.
- **Corroboration required.** It is additive evidence layered on the existing
  gates (size proximity / code-hash / name similarity via `_plausible_rename`),
  not a replacement for them — it raises a *borderline* pair's confidence, it
  does not manufacture a pair from type-equality alone.
- **Never suppress a break on weak evidence.** When the *only* signal is the
  type fingerprint (no size/hash/name corroboration), the pair is emitted as a
  low-confidence rename **hint** and the `FUNC_REMOVED`/`FUNC_ADDED` are **kept
  unsuppressed** — so a genuine removal can never be downgraded to compatible by
  a speculative rename. Suppression stays reserved for the existing
  size/hash-corroborated path.

Emitted as the existing rename ChangeKind (no new kind); the fingerprint only
ever *raises recall on already-plausible pairs*, bounded by the anti-hiding
guard above and the FP-rate gate (§Validation).

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
| `model.py` | `AbiSnapshot.idioms: dict[str, list[IdiomTag]]` (structured evidence — `idiom` + `confidence` + matched `evidence` + the opaque/PIMPL proof fields, **not** bare tag names, so loaded snapshots can enforce thresholds/guards and populate `edges_matched` — D2.4), `.conventions`; new `IdiomTag` dataclass; helper `RecordType` opaque/handle flags if not already derivable. | Additive; schema bump (ADR-015). Old snapshots → empty → safe no-op. |
| `checker_types.py` | `Change.confidence: Confidence` (reusing `checker_policy.Confidence`, default `HIGH`) — per-finding trust, distinct from verdict-level `DiffResult.confidence`; `Change.effective_verdict: Verdict \| None = None` — per-finding category override (default `None` = classify by `kind`); plus `Change.modulation_reason: str \| None`, `.modulation_rule: str \| None`. Add the shared `effective_category(change, kind_sets)` helper (D4.1 mechanism). | Additive dataclass fields with safe defaults; classification is a no-op while every `effective_verdict` is `None` (`--no-pattern-verdicts` / pre-Phase-3). |
| `checker_policy.py` / `reporter.py` / `severity.py` | **Behavioural change:** every kind-based classification site must route through `effective_category(...)` instead of bare `c.kind in <set>` — `compute_verdict()`; `reporter._change_to_dict` + `filtered_summary` + type/non-type splits; `severity.categorize_changes` + `compute_exit_code`. | No-op while no finding carries an override; otherwise demoted findings read compatible in **all** outputs and both exit-code paths (enforced by the cross-output validation matrix). |
| `bundle.py` | `BundleFinding` gains the same `effective_verdict` / `modulation_reason` / `modulation_rule` fields; `to_change()` propagates them onto the lowered `Change`; `BundleDiffResult.bundle_verdict` and the `compare-release` JSON/SARIF + exit-code paths classify via `effective_category(...)`, not bare `compute_verdict()` on the raw kind (D3.2). | Additive fields, default `None` → no-op for existing bundle runs; required so an A3 reachability demotion actually reaches the product verdict rather than being dropped at `to_change()`. |
| `checker_policy.py` | New `ChangeKind`s from A1.2 (metric drift) and A2.2 (anti-patterns), each placed in exactly one of `BREAKING/API_BREAK/COMPATIBLE/RISK` (import-time partition assertion enforces it). **A3 adds none** beyond at most one optional `PRODUCT_SURFACE_INCONSISTENT` — it reuses the existing `BUNDLE_INTRA_*` kinds (D3.2). | Enum grows modestly; follow the 4-step `/CLAUDE.md` procedure. |
| `surface.py` | Extract reachability helper into `surface_graph.py`, import back. | Internal refactor, no behaviour change. |
| New modules | `surface_graph.py`, `idioms.py`, `pattern_verdicts.py`, `cli_surface.py`. | Each *targeted* at < 600 lines; the AI-readiness file-size gate warns at 1500 / errors at 2000, so `idioms.py` (7 recognisers + convention inference) and `pattern_verdicts.py` (4 rules + ledger) should be split (e.g. one recogniser-registry module + a rules module) before they approach the soft limit, the same way `diff_platform.py` spun out `diff_platform_templates.py`. |
| CLI | `surface-report` command; `--surface-metrics`, `--idioms/--no-idioms`, `--pattern-verdicts/--no-pattern-verdicts`, `--explain-patterns`, `--product` flags. | Opt-in; defaults preserve current behaviour except `--pattern-verdicts` (see phasing — default-on only after validation). |

All new ChangeKinds must also satisfy the AI-readiness gates: partition
(ERROR), produced-somewhere (`changekind-detector` WARN), documented in
`docs/` (`changekind-docs` WARN), and headline-count sync (`doc-count-sync`
ERROR). Because `doc-count-sync` is an **ERROR** gate keyed off
`len(ChangeKind)`, the implementing PR for each phase must bump the ChangeKind
headline count **in the same commit** that adds the enum values — across this
multi-phase rollout it is the easiest gate to trip by adding a `ChangeKind`
in one PR and forgetting the doc count.

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
   - **PIMPL wrapper vs pointee:** a change to the *impl pointee* is demoted,
     but a change to the **wrapper's own** layout (gaining a member, the
     impl-pointer field changing) stays breaking — assert both directions on
     the same fixture (D4.1 `PIMPL pointee-only` guard).
   - Demotion is refused below `HEADER_AWARE` tier.
   - **Cross-output completeness:** for one demoted finding, assert it reads
     `compatible` in **every** sink — text report, JSON `changes` *and*
     `filtered_summary`, SARIF, JUnit — and contributes to **neither**
     exit-code path (`compute_verdict` and the severity-aware
     `severity.compute_exit_code`). This is the regression guard that every
     `c.kind in <set>` site was migrated to `effective_category(...)`.
3. **Property-based** (`slow`, hypothesis, extends
   `tests/test_detector_properties.py`):
   - *Modulation subset:* the pattern-aware finding set, projected back to
     categories, removes/demotes only — never invents a break.
   - *Determinism / order-independence* of graph construction and idiom tags.
   - *Idempotence:* re-running modulation on its own output is a fixed point.
4. **Cross-library** (A3): bundle fixtures where a removal in one `.so` is
   consumed by a sibling; assert the **existing** `BUNDLE_INTRA_DEP_REMOVED`
   still fires (now enriched with the producer→consumer reachability path), and
   that the A3 reachability filter demotes a `BUNDLE_INTRA_TYPE_CHANGED` on a
   type the consumer uses only internally while keeping it for a type on the
   consumer's public surface. No `CROSS_LIBRARY_*` kind is asserted (none is
   introduced).
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
| Modulate verdicts inline inside each detector | Scatters pattern logic across the `diff_*` detector modules; couples detection to inference. Chosen: a single post-processing pass with a ledger, mirroring `FilterNonPublicSurface`. |
| Require libclang (richer AST) for idioms | Heavyweight, violates the lightweight-core posture; castxml + DWARF already expose pointer-depth, fields, bases, vtables — enough for the conservative recognisers here. libclang (G4) would *extend* recall later, not gate this. |
| Add a parallel `CROSS_LIBRARY_*` ChangeKind family for product breaks | Rejected: `bundle.py` already emits `BUNDLE_INTRA_DEP_REMOVED`/`_SIGNATURE_CHANGED`/`_TYPE_CHANGED` for exactly these producer→consumer scenarios, so a parallel family means duplicate reporting + enum/`doc-count-sync` churn. A3 instead **reuses and tightens** those kinds with the `SurfaceGraph` reachability filter (D3.2). |
| Demote by re-tagging to compatible *variant* `ChangeKind`s (e.g. `TYPE_SIZE_CHANGED_OPAQUE`) instead of a per-finding override | This is how `*_ELF_ONLY` variants already work, so it was the obvious first idea. Rejected: it would roughly **double** the layout/field `ChangeKind` family (one compatible twin per demotable kind), inflate the `doc-count-sync` headline count, and bury the original kind so reports lose "what actually changed." The per-finding `effective_verdict` override (D4.1) re-categorises **in place**, keeps the original `kind` for the reader, and needs no new enum values. |
| Demote by moving findings to a separate ledger list (à la ADR-024 `out_of_surface_changes`) | Works for *scoping* (the finding genuinely isn't about the public surface), but here the finding **is** about the public surface — it's still a real, reportable change, just ABI-compatible *for this idiom*. Keeping it in `changes` with a downgraded `effective_verdict` is more honest than hiding it in a side list. |

---

## Consequences

**Positive:** fewer false positives on idiomatic ABI-stable patterns
(opaque/PIMPL); new real breaks caught (lost opaqueness, handle changes) and
**fewer false ones** from cross-library diffs (reachability-filtered
`BUNDLE_INTRA_*` findings, reusing the existing bundle kinds rather than adding
parallel ones); a descriptive `surface-report` for API hygiene and release
notes; a single product verdict for multi-binary releases; better rename
recall — all from data already captured, with no new required dependency and no
runtime analysis. Every pattern-driven decision is attributed and reversible.

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
- ADR-020a — Build-Context Aware Header Extraction (STL-by-value risk depends on it)
- ADR-023 — Bundle-Aware Multi-Binary Analysis (A3 extends its dependency graph to types)
- ADR-024 — Public ABI Surface Resolution (the demote-don't-delete contract and the
  reachability closure A4 reuses; `FilterNonPublicSurface` is the structural template
  for `pattern_verdicts.py`)
- Plan G4 — libclang header-AST extractor (future recall extension for idioms)
- `abicheck/surface.py`, `abicheck/internal_leak.py`, `abicheck/binary_fingerprint.py`,
  `abicheck/provenance.py`, `abicheck/model.py` (`ScopeOrigin`), `abicheck/checker_types.py`
