---
doc_type: contributor
level: advanced
lifecycle: active
---

# ABI/API knowledge and corpus — proving domain coverage, not case count

**Origin:** a review of the (now completed) [examples/catalog
split](examples-catalog-split.md) that separated three surfaces this
repository conflates when discussed loosely as "examples" or "docs":
`examples/` (task-oriented product workflows), `docs/learn/` (tool-neutral
ABI/API compatibility education), and `catalog/` (the calibration corpus).
The split fixed *where* things live and gave the calibration corpus a
taxonomy of its own cases (rule/scenario/variant/entity, ecosystem, evidence
tier). It did not answer a harder question the taxonomy alone cannot: does
that corpus, and the educational material that explains it, actually cover
the *space* of known ABI/API compatibility failure mechanisms — and does
abicheck detect every mechanism it claims evidence for? "197 cases" is a
count of what was written, not a coverage proof.

**Effort:** L (four phases, each independently useful; no code path,
detector, or default changes — this plan only produces a taxonomy, a
coverage matrix, and the paired-control cases and page cross-links that
matrix identifies as missing).

**Status:** Proposed; not started.

## The three surfaces, restated

This plan treats the following division (already substantially true in the
repository, see `examples/README.md`, `catalog/README.md`, and
`docs/AGENTS.md`'s two-track split) as settled, not as something to
redesign:

| Surface | Question it answers | Owner |
|---|---|---|
| `examples/` | "How do I do *task X* with abicheck?" | Product/user workflows |
| `docs/learn/` (educational track) | "How does ABI/API compatibility work, and why?" | Tool-neutral knowledge, useful without abicheck installed |
| `catalog/` | "What are the known ways ABI/API compatibility breaks, and does abicheck detect each one?" | Executable validation corpus |

`examples/` is genuinely a smaller, separate concern here: it changes when
the *product* workflow changes (a new CLI flag, a new default, a new report
shape), which is downstream of `vision.md` and the ADRs, not of this plan.
This plan is about the relationship between the other two — knowledge and
corpus — and about making that relationship checkable instead of assumed.

## Problem

The calibration corpus's own stated goal
(`catalog/README.md`) is calibration material: one case per compatibility
mechanism, driving the FP-rate, tier-accuracy, mutation, and full-catalog
gates. That is a true description of what the corpus *does* today, but it
is not a strong enough statement of what it is *for*. It supports exactly
the questions "does this corpus stay a fixed, countable set" and "do these
197 specific fixtures keep producing their pinned verdicts" — both
mechanical regression questions. It does not support the question a
maintainer or a reviewer actually wants answered: **out of everything that
can break ABI/API compatibility, how much of it is represented here, and
where are the gaps?**

Three consequences follow from not having an answer to that:

1. **No coverage denominator.** 197 is the numerator with no stated
   denominator. A case count can grow indefinitely without ever closing a
   gap, and can also look complete while a whole mechanism class (say,
   `noexcept`/exception-specification ABI changes, or a specific bitfield
   packing hazard) has no case at all.
2. **Knowledge and corpus can silently diverge.** `docs/learn/` already
   explains mechanisms in prose (vtable slot reordering, RTTI representation
   changes, symbol versioning, inline-namespace ABI stamps, and more — see
   the educational track's step 3, "How Breaks Happen"). Nothing currently
   checks that every mechanism a `docs/learn/` page asserts is real also has
   a `catalog/` case proving abicheck's behavior on it, or conversely that
   every `catalog/` case is explained by some educational page a reader can
   find.
3. **Detection claims are not distinguished from coverage claims.** A
   missing case for a mechanism could mean three different things today,
   indistinguishably: nobody has written the case yet (a corpus gap),
   abicheck cannot detect the mechanism with any evidence tier (a known
   product limitation, which belongs in `docs/learn/limitations.md` and
   `docs/contribute/known-gaps.md`), or the mechanism is fundamentally
   unobservable by any static tool (not a gap at all). Conflating these
   three under one silent absence is worse than reporting the true state of
   each.

## Non-goals

- **This is not a rewrite of `vision.md`'s product narrative.** A short,
  separate addition to `vision.md` records that abicheck is backed by this
  knowledge/corpus asset (see "vision.md" below) — the *systematic taxonomy
  and coverage-matrix work itself* stays here, not in the vision document,
  matching `docs/AGENTS.md`'s narrative-owner rule (`vision.md` is the
  narrative owner of product *direction*, not of a corpus's internal
  taxonomy).
- **No case is removed, renamed, or reclassified by this plan on its own.**
  Every finding this plan's phases produce (a documented-but-untested
  mechanism, a tested-but-unexplained case, a claimed-but-undetectable
  mechanism) is recorded as a gap with a disposition, the same "record
  before disposing" principle `AGENTS.md`'s product-decision-routing table
  already states for detected changes — it is not silently fixed by
  deleting the awkward row.
- **No change to what a "case" is.** This plan builds *on top of* the
  entity/rule/variant/scenario taxonomy the split already produced
  (`catalog/taxonomy.json`); it does not reopen that classification.
- **No new detector work is committed by this plan.** Phase 4 below may
  surface a mechanism abicheck cannot detect at any evidence tier; closing
  that is a new, separately scoped plan (or a `docs/contribute/known-gaps.md`
  entry if it is accepted as a permanent limitation), not something this
  plan's own phases implement.
- **This does not touch `examples/` (the product-workflow tree).** A
  workflow addition driven by a genuine new product capability is `AGENTS.md`'s
  ordinary "Adding a new ChangeKind"/workflow-plan path, unrelated to this
  plan's scope.

## Design

### Phase 1 — a normative ABI/API failure taxonomy

Write a taxonomy of the ABI/API compatibility failure *domain*, independent
of both `catalog/` and abicheck's own `ChangeKind` registry: a top-down
enumeration of known ways a compiled or source-level contract can break,
organized the way the field itself is organized, not the way the current
corpus happens to be organized. Top-level branches (each with named leaf
mechanisms, refined during the phase rather than fixed in advance):

1. Symbol identity (removal, rename, mangling change, linkage change,
   version-node change, visibility change, weak/strong binding change)
2. Function calling contract (parameter type/count, return type, calling
   convention, exception-specification/`noexcept` ABI, variadic changes)
3. Data layout (size, alignment, field offset, packing, enum
   representation, bitfields, unions)
4. C++ object model (base classes, virtual functions, vtable slots, RTTI,
   virtual inheritance, thunks)
5. Inline/template/source ABI (inline implementation changes, template
   instantiation, ODR, `constexpr`, macro-driven layout)
6. Export/public-surface contract (header-declared vs. exported vs.
   consumed surface divergence)
7. Dynamic linker contract (SONAME, symbol versioning, `DT_NEEDED`,
   `RPATH`/`RUNPATH`)
8. Dependency ABI (a transitively linked library's own break)
9. Toolchain/platform ABI (compiler ABI epochs, target triple, ABI-relevant
   flags)
10. Multi-library/product ABI (cross-component contracts within one
    release)
11. Source-level API compatibility (signature changes that break
    recompilation without necessarily breaking a prebuilt binary)
12. Header-only compatibility
13. Language/ecosystem-specific mechanisms (C, C++, CPython extension
    modules, SYCL, kernel `BTF`/`CTF`)

This taxonomy is deliberately *not* keyed by `ChangeKind` or by evidence
tier — those are abicheck's own vocabulary, mapped in Phase 3. It is keyed
by the domain, the way a reader with no abicheck installed would organize
the knowledge, because that independence is exactly what lets Phase 2/3
below detect a gap on either side rather than defining the taxonomy in
abicheck's own image and then trivially "covering" it. Store it as a
reviewable document (`docs/contribute/abi-api-failure-taxonomy.md` or a
structured sibling manifest analogous to `catalog/taxonomy.json`, decided
during the phase) with one row per leaf mechanism carrying at minimum: a
short description, applicable platforms/languages, and a stable id.

### Phase 2 — map existing knowledge and corpus onto the taxonomy

For every leaf mechanism from Phase 1, resolve three columns against what
already exists:

- **`learn_pages`** — which `docs/learn/` page(s) explain this mechanism
  (cross-referencing `docs/_meta/topics.yaml` where the mechanism is already
  a registered topic).
- **`catalog_cases`** — which `catalog/cases/case*` (via `rule_slug`/
  `entity`/`scenario_kind` from `catalog/taxonomy.json`) demonstrate it.
- **`detector`** — which abicheck `ChangeKind`(s)/detector module claims to
  observe it, and at which minimum evidence tier
  (`abicheck/model/change_catalog/*.py`, `scripts/evidence_tiers.py`).

Each column may legitimately be empty; Phase 2 only records the mapping, it
does not judge it yet. Where a mechanism maps to more than one case, keep
all of them (this is expected — it is exactly how a variant is supposed to
work) rather than collapsing to one.

### Phase 3 — classify every mechanism's coverage status

Assign each taxonomy leaf exactly one status, using values general enough to
distinguish a corpus gap from a product gap from a real absence (not an
open-ended free-text field — a fixed vocabulary is what makes the matrix a
gate rather than a spreadsheet):

| Status | Meaning |
|---|---|
| `COVERED` | Explained in `docs/learn/`, demonstrated by a case, and detected by abicheck at the evidence tier the case exercises. |
| `PARTIALLY_COVERED` | At least one of knowledge/case/detection exists, but not all three, or detection works only for a narrower sub-case than the mechanism as stated. |
| `KNOWN_UNDETECTABLE` | The mechanism is real and explained, but no static evidence abicheck can collect distinguishes it — a documented, accepted limitation (belongs in `docs/learn/limitations.md`/`docs/contribute/known-gaps.md`, cross-referenced here, not silently absent). |
| `NOT_IMPLEMENTED` | Detection is a real, tractable product gap — abicheck could detect this with evidence it doesn't yet collect or a detector that doesn't yet exist. Distinct from `KNOWN_UNDETECTABLE`: this is a backlog item, not an accepted limit. |
| `MISSING_CASE` | Detection exists and the mechanism is understood, but no `catalog/` case demonstrates it — a corpus gap, the most directly actionable status for this plan's own Phase 4. |
| `NOT_APPLICABLE` | The mechanism does not apply to abicheck's stated scope (`vision.md`'s "Scope and priorities" — e.g. a mechanism specific to a platform/language pairing abicheck does not target). |

Produce one generated report (mirroring the pattern of
`scripts/gen_catalog_coverage_report.py` → `docs/contribute/catalog-coverage.md`)
tabulating every leaf mechanism, its three Phase 2 columns, and its Phase 3
status. **Completeness against the taxonomy, not the raw case count,
becomes the headline metric this report states** — e.g. "136 of 143 known
mechanisms COVERED, 5 KNOWN_UNDETECTABLE, 2 MISSING_CASE" in place of "197
cases."

### Phase 4 — close `MISSING_CASE` gaps with paired positive/negative controls

For every `MISSING_CASE` finding, and opportunistically for an existing
`COVERED` mechanism that has a breaking case but no adjacent safe-variant
control, add the nearest-safe-transformation sibling alongside the existing
or new breaking case — the two together prove both directions:

- a breaking case (`X changed in a way that breaks compatibility`)
  demonstrates recall (no false negative);
- its nearest safe sibling (`the closest non-breaking transformation of the
  same construct`, e.g. a private-field addition next to the same
  addition on a public struct, or a non-virtual helper method next to a
  virtual-slot insertion) demonstrates precision (no false positive) on
  the mechanism the breaking case is proving.

This generalizes the pattern the corpus already uses ad hoc in scattered
cases into a systematic property of every mechanism this plan tracks, not a
one-off. Each new/extended case follows the existing case-authoring
contract (`catalog/CLAUDE.md`, paired `v1`/`v2`, a per-case `README.md`,
a `ground_truth.json` entry, taxonomy classification) — this phase adds
cases through the existing process, it does not invent a new one.

## Files & surfaces

- New: the Phase 1 taxonomy document (or manifest).
- New: a generator (`scripts/gen_abi_taxonomy_coverage.py` or similar,
  named during Phase 1) producing the Phase 3 coverage report, following
  the existing generated-doc contract (`docs/AGENTS.md`'s "Regenerating
  generated docs" section, `GENERATED_FILE_MARKERS` in
  `scripts/check_ai_readiness.py`).
- Extended: `catalog/taxonomy.json` or a sibling manifest, to carry each
  case's mapped taxonomy leaf id(s) (additive field, no existing field
  changes).
- New `catalog/cases/case*` entries from Phase 4 — through the ordinary
  case-authoring path, no different from any other new case.
- `docs/contribute/known-gaps.md` — gains entries for any `NOT_IMPLEMENTED`
  finding not already tracked there.
- `docs/learn/limitations.md` — gains cross-references for any
  `KNOWN_UNDETECTABLE` finding not already stated there.
- `vision.md` — a short, separate addition; see below.

## Tests

- A structural gate (extending `scripts/check_ai_readiness.py` or a
  standalone `scripts/check_abi_taxonomy_coverage.py`, decided during Phase
  1) that fails when a taxonomy leaf has no assigned status, mirroring how
  `changekind-partition` already requires every `ChangeKind` to sit in
  exactly one verdict bucket.
- `MISSING_CASE` count trends toward zero as Phase 4 lands; the gate does
  not require zero on landing (a `MISSING_CASE` entry with no plan can
  legitimately remain a tracked, visible gap rather than a blocking
  failure — matching this repository's general preference for an honest
  recorded gap over a manufactured closure).

## Effort & risk

L, four independently useful phases. Phase 1 is pure domain analysis (no
code); Phase 2/3 are read-mostly reconciliation against existing
manifests/registries; Phase 4 is ordinary case-authoring work, scoped by
whatever Phase 3 finds — its size is not knowable until Phase 3 completes,
which is why this plan does not commit to an XL estimate up front.

Risk: the taxonomy in Phase 1 is itself a judgment call with no external
authority to check it against (there is no canonical, machine-checkable
"list of all ABI/API failure mechanisms" to diff against, unlike e.g. the
`ChangeKind` registry). Treat it the way `AGENTS.md`'s canonical-identity
classification treats its own `UNVERIFIED` bucket: an explicit, reviewable
judgment call, not a claim of completeness the taxonomy cannot actually
support. Expect the taxonomy to be revised as gaps are found in later
phases, not frozen after Phase 1.

## Out of scope

- Rewriting or renumbering existing `catalog/` cases.
- Any change to `abicheck`'s detectors, evidence collection, or `ChangeKind`
  registry — a `NOT_IMPLEMENTED` finding is recorded, not fixed, by this
  plan.
- `examples/` product-workflow content.
- Vision-document product-direction changes beyond the short addition
  described below.
