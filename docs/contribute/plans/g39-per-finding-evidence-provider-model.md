---
doc_type: contributor
level: expert
lifecycle: active
generated: false
---

# G39 — Per-finding evidence-provider model

**Status:** Proposed; not started.

**Routing note (ADR-061):** every implementation location this plan names is
qualified against the root `AGENTS.md`'s "Task routing and dependency
direction" table (`AGENTS.md`, sourced from
[ADR-061](../adr/061-responsibility-package-architecture.md)) — route new
code to the responsibility-package owner current at implementation time, not
to the flat legacy module this plan cites for orientation. See "Design" and
"Files & surfaces" below for the specific mapping.

Split out of [G38](g38-bundle-facts-model-and-multibuild-comparability.md)'s
"Out of scope" section and the root `AGENTS.md`'s "Evidence-provider model"
known-gap entry — both explicitly deferred this as its own, separately-scoped
project rather than a side effect of either G38 or the layout-finding
investigation that first raised it. This document is that separate scope: a
design, not an implementation. No code changes ship with this plan.

## Problem

`checker_types.Change` carries two provenance-adjacent fields today, and
neither answers the question this plan is about:

- `evidence_category: str | None` (ADR-033 D9) — coarse, two-valued
  (`"build_context"` / `"source_only"`), set only for L3+ findings, and its
  job is metrics bucketing (retained-finding counts per evidence bucket), not
  per-finding provenance.
- `contract_evidence_refs: tuple[str, ...] | None` (ADR-049) — real
  provenance, but scoped narrowly: populated only when a run is given
  `--contract`, and its entries reference *contract-domain evidence
  providers* (`public_header`, `export_table`, the `post_manifest`/
  `forced_public_symbols` overlays) — i.e. "what evidence justified this
  finding's *relevance* classification", not "which extractor/tier
  *produced* this finding's *content*". A run without `--contract` — the
  overwhelming majority of invocations — carries `None` here regardless of
  how the finding was actually produced.

Neither field can answer: for one specific `Change`, was it produced from L0
symbol-table evidence alone, corroborated against L1 DWARF, derived from an
L2 castxml or clang header parse, or does it rest on L3 build evidence? A
consumer — a policy file, a report reader, a future confidence-scoring
step — cannot distinguish "this `TYPE_SIZE_CHANGED` finding is grounded in
DWARF-corroborated layout" from "this one is a castxml-only computation with
no DWARF to check it against" without re-deriving that fact from the
snapshot's own `dwarf_layout_coherence`/`ast_frontend` fields and guessing
which one applied to *this* finding specifically — fragile, and already
shown to be wrong at least once (see `AGENTS.md`'s "Findings emitted from
absent evidence" entry: the vtable false positive traced to exactly this
class of missing per-finding evidence signal, closed there only for one
`ChangeKind` via a narrow, kind-specific guard rather than a general
mechanism).

**What was already investigated and found not to generalize the way a first
reading suggests** (`AGENTS.md`'s own entry, restated here for this plan's
own record so it isn't re-litigated): the risk that a header-only
(castxml/clang) finding could read as artifact-proven merely because *some
other* part of the same report had binary evidence does not reproduce for
layout findings specifically, because `RecordType.size_bits`/
`alignment_bits` are either DWARF-backfilled-and-corroborated (direct-clang)
or self-consistently computed by castxml's own bundled compiler (a prior,
intentional design decision, not a gap). That investigation is *evidence the
per-kind story is subtler than "L2 findings are always weaker than L1/L0
ones"* — which is itself an argument for a real per-finding model over a
per-report or per-kind heuristic: the correct provenance genuinely varies
finding-by-finding within one `ChangeKind`, not just kind-by-kind.

## Goal & acceptance criteria

A `Change` can name, without re-deriving it, which evidence tier(s) actually
produced and corroborated it — precise enough that a consumer no longer has
to guess from surrounding snapshot metadata, general enough that it doesn't
need a bespoke field per `ChangeKind` the way the vtable guard's
`vtable_covers_unverifiable_layout_gap` field does today.

### Phase 0 — data model (S)

**Implementation location (ADR-061):** `Change` is a shared domain value
used across every later stage (comparison, policy, reporting) — the root
`AGENTS.md`'s routing table places exactly this class of change under
`model/` ("Add an ABI entity/value shared across stages"). At the time this
plan was written, `Change` still lives in `checker_types.py` and `model/`
has not yet absorbed it (ADR-061 Phases 2-4, which include this migration,
are "in progress," per the ADR's own status line). This plan does **not**
perform that migration as a side effect: add the field wherever `Change` is
canonically defined *at implementation time* — `model/findings.py` if that
migration has already landed by then, `checker_types.py` otherwise. Landing
this field is not itself a reason to force the `model/` migration forward.

Add one new, additive field to `Change`, following the existing
`contract_evidence_refs` precedent exactly (flat `tuple[str, ...] | None`,
`field(default=None, kw_only=True)`, lazy-imported enum for its value
vocabulary to avoid a circular import the same way `ContractRelevance` is
avoided at module scope):

```python
evidence_provenance: tuple[str, ...] | None = field(default=None, kw_only=True)
```

Each entry is a stable, namespaced provider-id string, not a bespoke enum —
mirroring `contract_evidence_refs`' own string-ref shape rather than
inventing a second typed vocabulary next to `contract_relevance_types.py`'s
existing one. Provisional vocabulary (finalized in Phase 1, once real call
sites are wired and the actual granularity needed is known — do **not**
freeze this before that):

| Prefix | Meaning | Existing analogue |
|---|---|---|
| `l0:elf_symtab` | ELF `.dynsym`/export table alone | `evidence_tiers.py`'s L0 |
| `l1:dwarf` | DWARF debug info | L1 |
| `l2:castxml` / `l2:clang` | Header-AST backend, named specifically (not just "L2") since the two backends have measurably different fact completeness — see `docs/reference/header-backend-capabilities.md` | L2 |
| `l3:build_context` | ADR-039 build-context collector / L3→L2 fold | L3 |
| `l4:source_replay` | L4 source-ABI replay | L4 |
| `l5:source_graph` | L5 source/consumer graph | L5 |
| `corroborated:dwarf` | An L2 fact that was cross-checked against DWARF (`dwarf_layout_coherence`-style backfill) | — new |

`None` means "not yet computed for this finding" (every pre-Phase-1 call
site), distinguished from `()` meaning "computed, and genuinely no provider
claims this finding" (should not occur in practice once Phase 1 completes,
but the distinction matters for the completeness gate in Phase 3 the same
way it already matters for `contract_evidence_refs`).

**A flat, unqualified tuple cannot distinguish which *side* a provider
applies to, and that distinction is load-bearing (Codex review, fresh
evidence).** A changed fact's `Change` spans two `AbiSnapshot`s (old and
new), and their evidence can genuinely differ — an old-side `RecordType`
DWARF-corroborated per this plan's own layout-finding investigation, paired
with a new-side one that is not (a real, not hypothetical, shape: a header
changed in a way DWARF debug info for the *new* build doesn't yet cover,
or vice versa). An unordered union like `("l2:clang", "corroborated:dwarf")`
cannot say *which* side the corroboration belongs to — a consumer reading
it would have no way to avoid treating the whole finding as corroborated
when only one side actually is, silently defeating the confidence
distinction this field exists to provide. **Resolution: every entry in the
vocabulary above is side-scoped by a mandatory `old:`/`new:`/`both:` prefix
layer** ahead of the tier prefix — e.g. `old:l2:clang`, `new:corroborated:dwarf`,
or `both:l0:elf_symtab` for a provider whose evidence is genuinely identical
on both sides (the common case for e.g. a symbol-table-only detector, where
"old" and "new" mean the same extraction mechanism ran on each side, not
that the *content* matched). This is additive to the table's existing
prefixes, not a redesign of them — `evidence_provenance = ("both:l0:elf_symtab",)`
is the typical L0/L1 slice-1 value, and only the harder L2+ slices need the
`old:`/`new:` split in practice. Phase 1's own wiring for each slice states,
per detector, whether `both:` suffices or the per-side split is required —
recorded there rather than guessed here, since (mirroring this plan's own
"XL, phased" discipline) the answer genuinely varies by detector and
shouldn't be frozen before real call sites are examined.

**The `old:`/`new:`/`both:` vocabulary is mandatory-prefix but not
exhaustive over every finding shape -- a fourth, `current:` scope is
needed for genuinely unary findings (Codex review, fresh evidence,
confirmed by reading real call sites rather than assumed from the
prefix table alone).** `buildsource/crosscheck.py`'s
`_check_header_build_context_mismatch`/`_check_public_to_internal_
dependency` (and their sibling `_check_*` functions in that module) each
take exactly one `snapshot: AbiSnapshot` parameter -- there is no old/new
pair at all, so neither `old:`/`new:` (which name a *side* of a
comparison) nor `both:` (explicitly defined above as "the mechanism runs
on both sides" -- itself presupposing two sides) can truthfully describe
where this evidence came from. Forcing one of the three onto a
single-snapshot check would either mislabel it (claiming a `both:` a
consumer would read as "ran on both sides of a comparison," when only one
snapshot was ever examined) or violate the "mandatory prefix" contract
Phase 1 otherwise enforces. Resolution: a fourth scope, `current:`,
reserved specifically for a `_change()`/`Change(...)` construction site
whose function signature takes one `AbiSnapshot`, not a
`(old, new)` pair -- e.g. `current:l5:source_graph` for
`_check_public_to_internal_dependency`'s L5 reachability evidence. Phase
1's own per-call-site wiring decides, from the function signature at each
site, whether `old:`/`new:`/`both:` or `current:` applies -- the same
"recorded there rather than guessed here" discipline the paragraph above
already establishes for the `both:`-suffices question.

### Phase 1 — wire the finding-construction call sites (XL, phased itself)

**No hand-copied count — this plan has already gotten one wrong twice
(Codex review, three rounds; per `AGENTS.md`'s own "don't hand-copy a
table, count, or version number that already has a fact owner elsewhere"
rule, which this section now follows instead of fighting).** An earlier
draft cited "~45 `Change(...)` construction sites"; a later revision
"corrected" that to "about 14 sites" plus "~350" `make_change()` calls —
also wrong, confirmed by a fresh `git grep -n "Change(" -- 'abicheck/*.py'
'abicheck/buildsource/*.py'` at implementation time finding several dozen
direct-construction sites in `buildsource/` alone (`build_diff.py`,
`crosscheck_base.py`, `evidence_policy.py`, `graph_reconcile.py`,
`source_diff.py`, `source_graph_findings.py`, ...), none of which the
prior revision's file list named. A hand-copied count in a plan document
goes stale the moment any PR adds, removes, or refactors a detector, and
this plan's own history is now direct proof of that. **Derive the real
inventory at implementation time instead of trusting any number written
here**: `git grep -n "Change(" -- 'abicheck/*.py' 'abicheck/buildsource/*.py'`
for direct constructions (filter out the `class Change` definition itself,
type annotations, and `diff_helpers.py`'s own factory body), and
`git grep -n "make_change(" -- 'abicheck/*.py' 'abicheck/buildsource/*.py'`
for factory calls. The dominant path is still `diff_helpers.make_change()`,
which already forwards arbitrary keyword arguments straight to
`Change(...)` unchanged (`**change_kwargs: Any` in its signature) — a real,
favorable, count-independent consequence: **`make_change()` itself needs
no signature change**, any call site can already pass
`evidence_provenance=(...)` through today's `**change_kwargs`. The actual
Phase 1 work is therefore not "extend one factory, then wire N sites
downstream of it" — it's "decide and pass the *right value* at every real
call site, both `make_change()`-routed and direct," spread across every
detector module that emits findings (`diff_*.py` **and** `buildsource/*.py`
in full, not a partial file list) — a large, XL-effort inventory whichever
way it's counted, which is this section's actual, count-independent point.
**A completeness gate over construction paths themselves** (the P2 finding
this round of review also raised) is a real alternative to a static count
and is worth a follow-up investigation — e.g. an AST-based
`check_ai_readiness.py`-style check that every `Change(...)`/`make_change(...)`
call site is covered by *some* test asserting on `evidence_provenance` once
Phase 1 completes — but designing that gate is its own scoped piece of
work, not attempted in this already-corrected-twice section.

**Implementation location (ADR-061):** detector wiring is `compare/`
territory ("Match old/new entities or identify a raw change"). As with
Phase 0, this plan targets whichever module is canonical for a given
detector *at implementation time* — the flat `diff_*.py`/`buildsource/*.py`
modules named below if `compare/`'s detector migration (ADR-061 D2's
`compare/detectors/{symbols,types,cpp,platform,build,source}.py`) has not
yet reached that detector, the `compare/` package otherwise. `buildsource/*`
is a distinct, already-`AGENTS.md`-scoped package (L3-L5 build/source
evidence) and stays where it is regardless of the `compare/` migration —
see `abicheck/buildsource/CLAUDE.md`.

**Not one flag day.** Ordered by risk, matching this file's own "known gaps
over risky reactive patches" discipline and the FP-rate/mutation-score gate
structure already in place:

1. **L0/L1-only detectors first** (`diff_platform.py`'s ELF/PE/Mach-O-specific
   findings, and the direct-construction sites in `checker.py`/
   `versioned_symbol_scheme.py` that are similarly static) — the provenance
   is a static fact of which module produced the `Change`, not a per-call
   derivation, so this slice is close to mechanical: one constant tuple per
   detector function, passed through the existing `make_change(...)`/
   `Change(...)` call. **`diff_symbols.py` does NOT belong in this
   mechanical sub-slice** despite being symbol-table-driven on its face
   (Codex review, verified against the code): `_public_functions()` falls
   back to the complete header/DWARF function set when live ELF evidence is
   absent, and separately still returns synthetic CastXML-only
   constructors/destructors that never match any exported symbol — so the
   same removal/addition call site can be driven by L0, L1, or L2 evidence
   depending on which producer's facts a given `Function` actually rests on,
   not by which module ran. Treat `diff_symbols.py` as its own slice,
   ordered after this one and before slice 2, deriving provenance per
   finding from the participating `Function`/`Variable` record's own
   evidence (which source populated it) rather than assuming a module-level
   constant. **On a `--ast-frontend hybrid` snapshot, "the participating
   record" is not a single producer either (Codex review, verified against
   the code): `fact_provenance.py` (G28 Phase 3) exists precisely because a
   hybrid snapshot's merge (`dumper_hybrid.merge_snapshots`) can backfill
   individual facts on one `Function`/`Variable` from clang while the rest
   of that same record came from castxml — `AbiSnapshot.fact_provenance` is
   keyed per-fact (`func_fact_key`/`var_fact_key`/`field_fact_key`, e.g.
   `"func:<mangled>:<fact>"`), not per-declaration.** So for a hybrid
   snapshot this slice must query `fact_provenance` for the *specific* fact
   a given detector call compares (e.g. a parameter-default or deprecation
   change), via `is_castxml_backed_fact`/`both_castxml_backed_fact` or a
   sibling per-fact lookup, rather than reading one provenance value off the
   record as a whole — the same "specific fields, not aggregate backend"
   principle slice 2 below already states for `diff_types.py`, extended to
   `diff_symbols.py`'s own record-level facts.
2. **L2 header-derived detectors** (`diff_types.py`'s struct/enum/typedef
   findings, `diff_type_spellings.py`) — provenance here genuinely varies
   per finding (which backend produced *this specific* `RecordType`, and
   was it DWARF-corroborated) — this is the harder, `AbiSnapshot`-inspecting
   slice the investigation above already partially scoped for one
   `ChangeKind` family (layout). Generalize that same reasoning (check the
   *specific* fields the finding rests on, not the snapshot's aggregate
   backend) across every `diff_types` detector, not just vtable/size/
   alignment. **A real prerequisite this slice cannot skip (Codex review,
   verified against the code): the snapshot model does not currently
   persist per-record backfill provenance for this slice to read.**
   `dumper_layout_backfill.resolve_snapshot_layout_coherence()` returns
   only `(coherence.status, coherence.mismatched)` — `DwarfLayoutCoherence
   .matched` (the per-type names that *were* successfully DWARF-backfilled)
   is computed and then discarded, and `RecordType` itself carries no
   producer/backfill marker a detector could read per-instance. Without
   that fact, a type-level detector in this slice can only infer from the
   snapshot's *aggregate* `dwarf_layout_coherence` status (`"matched"` /
   `"partial"` / ...) — exactly the report-level-metadata guess this slice
   exists to replace with real per-finding provenance. Closing this needs
   a small, additive extraction/model change *before* this slice's detector
   wiring starts: thread `DwarfLayoutCoherence.matched` (or an equivalent
   per-record marker) onto the record itself, or onto a snapshot-level
   lookup the detector can consult per `RecordType.qualified_name`. Scope
   that model change as its own reviewed sub-step of this slice, not a
   simultaneous side effect of the detector wiring itself.
3. **L3-L5 build/source detectors** (`buildsource/*.py`) — **not** simply
   "already carry `evidence_category`; extend rather than duplicate" as an
   earlier draft of this plan said (Codex review, verified against the
   code: `evidence_category` is a coarse binary tag,
   `"source_only"`/`"build_context"` only — `evidence_policy.
   tag_evidence_category` and the two direct call sites in
   `crosscheck_coherence.py`/`diff_reconcile.py` are the only producers,
   and it cannot express a finding that rests on more than one evidence
   *kind*, e.g. `exported_not_public` (binary exports + L2 header AST) or
   `header_build_context_mismatch` (L2 header AST + L3 build context)).
   `buildsource/crosscheck.py`'s `run_crosschecks()` already records the
   real, specific sources for every one of its checks in each
   `_CheckOutput.providers` list — a sequence of `PROVIDER_*` constants
   (`PROVIDER_BINARY_EXPORTS`, `PROVIDER_PUBLIC_HEADER_AST`,
   `PROVIDER_BUILD_CONFIG`, `PROVIDER_SOURCE_INDEX`), one entry per evidence
   kind the check actually consulted, already present at every one of this
   file's `_check_*` call sites. This slice's wiring derives
   `evidence_provenance` from that `providers` list, not from
   `evidence_category` — the richer, already-collected fact, not the coarser
   tag layered on top of a subset of it.

   **`PROVIDER_*` is not itself tier-bearing, and the list cannot be
   translated to a prefix by a fixed `PROVIDER_* -> l*:` lookup table
   (Codex review, fresh evidence, verified against the code).** The
   parenthetical originally here claimed each `PROVIDER_*` constant "maps
   cleanly to an `l0:`/`l2:`/`l3:`/`l4:` prefix" — false for
   `PROVIDER_SOURCE_INDEX`, which several distinct `_check_*` functions
   share despite consuming genuinely different evidence *tiers*:
   `_check_odr_type_variant` reads `snapshot.build_source.source_abi` (the
   L4 source-replay surface — its own docstring says so explicitly) while
   `_check_public_to_internal_dependency` reads
   `snapshot.build_source.source_graph` (the L5 source/consumer graph) —
   both stamp the identical `providers = [PROVIDER_SOURCE_INDEX]`.
   Translating the provider list directly into a prefix, as the previous
   wording of this bullet directed, would derive the same tier string for
   both and silently mislabel the L5 finding as L4 (or vice versa) — the
   `current:l5:source_graph` example already given for
   `_check_public_to_internal_dependency` earlier in this document (see
   the `old:`/`new:`/`both:`/`current:` scoping section above) is the
   correct value for that check specifically, and is *not* recoverable from
   `providers` alone. The fix is not a richer `PROVIDER_*` enum (that would
   re-litigate `crosscheck_base.py`'s own provider-agreement-matrix
   contract, which several other consumers beyond this plan already
   depend on) — it is that this slice's wiring must key the `l*:` prefix
   off **the emitting `_check_*` function's own known tier**, not off the
   `PROVIDER_*` identity alone. Concretely: build the mapping as one entry
   per `_check_*` function (`_check_odr_type_variant ->
   "l4:source_replay"`, `_check_public_to_internal_dependency ->
   "l5:source_graph"`, and so on for every check in this module), derived
   by reading each function's own body to see which `snapshot.build_source`
   field it actually consults — the same per-call-site reading discipline
   Phase 1's other slices already use — rather than assuming the
   `PROVIDER_*` constant on `_CheckOutput.providers` is itself sufficient.
   A `PROVIDER_*` constant that turns out to name only one tier across every
   one of its call sites (e.g. `PROVIDER_BUILD_CONFIG` naming only L3) may
   still resolve to a single fixed prefix in practice — the point is that
   this must be verified per constant, function by function, not assumed
   uniformly from the constant's name.
4. **Cross-cutting post-processing and roll-up emitters**
   (`post_processing.py`, `post_processing_reachability.py`,
   `pattern_verdicts.py`, `internal_leak.py`, `bundle_models.py`,
   `post_manifest.py`, `cli_buildsource_helpers.py`) — these often
   construct a `Change` by *transforming* an existing one (a roll-up, a
   suppression-adjacent rewrite) rather than deriving fresh evidence; the
   right default here is usually "carry the source finding's own
   `evidence_provenance` forward," verified per emitter rather than assumed
   uniformly.

Each slice, once wired, gets its own FP-rate/mutation-score gate re-run
before merging — never the whole inventory behind one PR. The FP-rate/
mutation-score gates exist precisely because a previous incident in this
exact area (the reverted "linkage-blind removal" and "vptr-offset-bits"
attempts, `AGENTS.md`'s own record) showed field-level changes to shared
detector plumbing can silently reintroduce a false positive or false
negative that passes every hand-written example test.

### Phase 2 — completeness gate (M)

A new test, `test_evidence_provenance_completeness.py`, mirroring
`tests/canonical_identity_contract.py`'s "every `ChangeKind` must be
classified" pattern exactly (the same pattern this repo adopted specifically
*because* an earlier per-`ChangeKind` classification silently missed three
entries — PR #753 → #759, cited in the root `AGENTS.md`'s "Adding a new
ChangeKind" section). Every `ChangeKind` must appear in one of:

- `PROVENANCE_STATIC` — kinds whose provenance is a constant of the
  producing detector (most L0/L1 kinds);
- `PROVENANCE_PER_FINDING` — kinds whose provenance must be computed per
  instance (most L2+ kinds);
- `PROVENANCE_UNVERIFIED` — an explicit backlog bucket, not a silent gap,
  identical in spirit to `UNVERIFIED` in the canonical-identity contract.

A `ChangeKind` with no entry fails CI — the same "no silent omission"
mechanism `canonical_identity_contract.UNVERIFIED` already established for
identity classification, applied here to provenance classification instead.

**What this gate does and does not prove (Codex review, fresh evidence):**
it proves every `ChangeKind` is classified into one of the three buckets —
an *enum-partition* completeness, mirroring the #753 → #759 incident's own
lesson (a missing entry is silent everywhere else, so make the enum itself
un-skippable). It does **not** prove a kind's real producer(s) actually
behave the way its bucket claims — a kind moved to `PROVENANCE_STATIC`/
`PROVENANCE_PER_FINDING` whose producer forgot to actually set
`evidence_provenance`, or whose *second*, independent producer path (e.g. a
kind emitted from both a `diff_symbols.py` path and an unrelated
`diff_platform.py` path) never got wired at all, still passes this gate
outright, since it checks bucket *membership*, not producer *behavior*. That
gap is why the "completeness gate over construction paths" idea two
paragraphs above is called out as real, separate, not-yet-designed
follow-up work rather than something this phase already closes — the two
sections must not be read as contradicting each other: Phase 2's gate closes
the enum-omission failure mode specifically, and is deliberately silent on
the construction-path failure mode, which needs its own mechanism this plan
does not yet specify.

### Phase 3 — report/schema surface (S)

**Implementation location (ADR-061), corrected against the actual code
(Codex review — an earlier revision of this section named `report/
render_json.py`/`document.py` as the projection point, which is wrong):**
`report/document.py`'s `ReportDocument.from_mapping`/`to_mapping` only
freeze/thaw an *already-built* mapping, and `render_json.py` only calls
`json.dumps(document.to_mapping())` on it — neither one projects a `Change`
into a dict. That projection is still done entirely by legacy `reporter.py`
call sites: `_change_to_dict` (the main per-change dict builder), its
sibling `_leaf_entry` (leaf-mode's own, deliberately separate dict builder
for type-change leaves — see its own docstring for why it doesn't route
through `_change_to_dict`), and `cli_scan_baseline._baseline_finding_dicts`
(scan's own, separate projection). **This phase must cover all three, not
just one**, or `evidence_provenance` reaches full-mode JSON while silently
staying absent from leaf-mode and `scan --against` output. Add the field to
each builder directly (mirroring how `contract_evidence_refs` already
appears in `_change_to_dict` today — `_leaf_entry`/`_baseline_finding_dicts`
would need it added fresh, since neither currently carries
`contract_evidence_refs` either, confirmed by reading both). If `report/`'s
own migration has absorbed one or more of these builders by implementation
time, wire the field there instead — but the sub-task is "cover every
current `Change`→dict projection," not "extend whichever one happens to be
literally named `report/`."

**Four more `Change`→dict projections exist beyond those three, and this
phase must cover them too (Codex review, fresh evidence, confirmed by
reading `reporter.py` directly): `_out_of_surface_entry`, `_add_reconciled`,
`_filtered_internal_entry`, and `_suppressed_change_entry`.** These are the
audit-ledger serializers `_add_contract_decision_fields`'s own docstring
already names as the reason that helper exists (see that docstring's own
"a demoted/suppressed/reconciled finding" language) — each builds a
compact, independent dict from a real `Change` object for a finding that
was *excluded* from the main `changes` list (out-of-surface, ADR-039
reconciled, filtered-internal, or suppressed), and none of the four routes
through `_change_to_dict`/`_leaf_entry`/`_baseline_finding_dicts` to pick up
`evidence_provenance` incidentally. Implementing Phase 3 literally against
only the three builders named above would leave `evidence_provenance`
present on every *kept* finding while silently absent from every demoted
one — exactly backwards from where a report reader most needs to see the
evidence a policy or suppression decision rested on: an audit trail that
shows a finding was suppressed but not what evidence justified suppressing
it defeats the auditability this whole model exists to provide. Add
`evidence_provenance` to all four the same way `_add_contract_decision_fields`
already stamps `finding_id`/`canonical_finding_id`/the contract-decision
fields on each of them — a single shared helper call at each site, not four
independent implementations — and extend the schema-version-bump/topic-
registration steps above to cover these four projections' own output shape
(the `surface_scope.out_of_surface_changes`/`scope.filtered_internal_changes`/
reconciliation-ledger/suppressed-changes JSON blocks), not just the three
originally named.

**A new public field on an already-published report format is a real
schema change, not a cosmetic addition (Codex review — a prior revision of
this phase named all three builders but never said this)**:
`_change_to_dict`/`_leaf_entry` feed `abicheck/schemas/__init__.py`'s
`REPORT_SCHEMA_VERSION` (do not hand-copy its current value here — see
`docs/AGENTS.md`'s rule against hand-copying a volatile count), which must
gain at least a MINOR bump, and
`abicheck/schemas/compare_report.schema.json` must add `evidence_provenance`
to its `Change`-object definition — mirrored, per the repo's existing
convention for that file, into `docs/reference/schemas/v1/
compare_report.schema.json` (`scripts/publish_schemas.py`) in the same PR.
`site/` is build output (mkdocs-generated, gitignored — confirmed no
`site/` path is tracked in this repo and `publish_schemas.py` only writes
`docs/reference/schemas/v1/`), so there is no `site/reference/schemas/v1/`
tracked file to update; it regenerates from the `docs/` mirror on the next
`mkdocs build`.
`_baseline_finding_dicts` feeds `SCAN_SCHEMA_VERSION` (also in
`abicheck/schemas/__init__.py`, same "don't hand-copy the current value"
rule) the identical way — that format has
no separate published `.schema.json` mirror today (confirmed: no
`scan_report.schema.json` exists under `abicheck/schemas/` or
`docs/reference/schemas/v1/`), so only the version constant itself needs
bumping for that builder, not a schema file that doesn't exist. Landing
`evidence_provenance` in any of these three dict builders under an
*unchanged* schema version would let a new field appear to consumers doing
schema-version-gated feature detection as if it had always been there.

**Docs-ownership registration (`docs/AGENTS.md`'s topic-ownership
contract):** `evidence_provenance` is a new public-facing report field, so
this phase must register it in `docs/_meta/topics.yaml` in the same PR, not
defer it. The natural home is the existing `evidence-model` topic
(`canonical_page: learn/evidence-and-detectability.md`) — the field is
squarely evidence-tier vocabulary, not a new topic, and `docs/AGENTS.md`
already says not to add a fourth page to that deliberately three-page trio.
Extend its `fact_sources` list with the modules that actually produce and
render the field (the detector modules Phase 1 wires, and whichever of
`_change_to_dict`/`_leaf_entry`/`_baseline_finding_dicts` or their `report/`
successors carry it per Phase 3) rather than creating a separate topic; add
a short description of the field to `learn/evidence-and-detectability.md`
itself as part of this phase's PR.

SARIF and JUnit rendering had not migrated into `report/` at the time this
plan was written (only JSON and text renderers had) — reaches `sarif.py`'s
existing `properties` bag (one entry, not a new top-level SARIF concept) if
that's still the live SARIF renderer at implementation time, or `report/
render_sarif.py` if that migration has landed by then. Also reaches the
generated docs (`scripts/gen_detector_spec.py`'s matrix gains a column once
every kind has a real, non-`UNVERIFIED` classification — gated on Phase 2's
completeness test, so the docs generator cannot claim more coverage than
actually exists).

### Phase 4 — one real consumer (M, optional/stretch)

Explicitly **not required** for this plan's acceptance criteria, named here
so a future PR doesn't have to re-derive the target: once real, per-finding
values exist, `evidence_status_for_result`'s report-level `ARTIFACT_PROVEN`
→ `UNATTRIBUTED` downgrade (the mechanism `AGENTS.md`'s entry investigated
and found too coarse) can be re-scoped from "was the *whole comparison*
header-only" to "was *this finding* header-only, uncorroborated" — a
strictly more precise version of the same signal, using data this plan
produces rather than requiring new extraction.

## Design

Almost no new extraction. Every phase above threads a fact the producing
code already has in scope (which detector module ran, which snapshot field a
`RecordType`/`Function` value came from) into the existing `Change(...)`
call — the same "provenance plumbing, not a new evidence source" framing
`AGENTS.md`'s own entry already used to size this at "multi-day, not a quick
fix": the *volume* of call sites is the cost, not any single site's
complexity. **One real exception, stated in full in Phase 1 step 2 above and
repeated here so this section doesn't contradict it**: the L2 header-derived
slice cannot read per-record DWARF-backfill provenance today —
`DwarfLayoutCoherence.matched` is computed and discarded, and `RecordType`
carries no per-instance producer/backfill marker — so that one slice needs a
small, additive extraction/model change (thread `.matched`, or an equivalent
per-record marker, onto the record or a snapshot-level lookup) *before* its
detector wiring starts, scoped as its own reviewed sub-step, not folded
silently into "no new extraction."

Deliberately reuses `contract_evidence_refs`' shape (flat string-tuple, not
a nested dataclass) rather than introducing a second typed provenance model
alongside `contract_relevance_types.py` — a `Change` gains one more optional
tuple field, not a new sub-object graph to keep in sync with serialization
(wherever `Change.to_dict`/`from_dict` canonically live at implementation
time), SARIF/JSON rendering, and every existing consumer.

## Files & surfaces

Named per their flat, pre-migration location, since that is what exists
today; each bullet's own ADR-061 target package is the actual implementation
owner once that migration reaches it — see the phase-by-phase routing notes
above, which this list intentionally does not re-duplicate.

- `abicheck/checker_types.py` (→ `model/findings.py`) — the new field.
- `abicheck/diff_helpers.py`'s `make_change()` (→ `compare/`) — no signature
  change needed; named here because its `**change_kwargs` forwarding is
  exactly why most call sites need no factory-level change, only a passed
  value.
- `abicheck/diff_symbols.py`, `diff_types.py`, `diff_platform.py`,
  `diff_versioning.py`, `diff_vtable_layout.py`, `diff_sycl.py`,
  `diff_stdlib_impl.py`, `diff_type_spellings.py` (→ `compare/detectors/*`)
  — Phase 1's `make_change()`-routed call sites.
- `abicheck/checker.py`, `internal_leak.py`, `pattern_verdicts.py`,
  `post_manifest.py`, `post_processing.py`, `post_processing_reachability.py`,
  `stack_checker.py`, `versioned_symbol_scheme.py`,
  `cli_buildsource_helpers.py`, `bundle_models.py` (→ `compare/`/
  `workflows/`, module-dependent) — Phase 1's direct-construction sites.
- `abicheck/buildsource/*.py` (stays `buildsource/`, per its own scoped
  `AGENTS.md` — not part of the `compare/` migration) — the L3-L5 detectors
  already carrying `evidence_category`.
- `abicheck/report/render_json.py`/`document.py` (already `report/`),
  `abicheck/reporter.py`/`sarif.py` as long as either remains the live
  legacy renderer for its format — Phase 3.
- `tests/test_evidence_provenance_completeness.py` — Phase 2 (new).
- `scripts/gen_detector_spec.py`, `docs/reference/detector-spec.md` — Phase 3.

## Tests

- Phase 2's completeness gate (above) — the primary regression backstop,
  same shape as `test_canonical_finding_id_completeness.py`.
- Per-slice: existing detector-oracle tests (`test_detector_oracle.py`) gain
  an `evidence_provenance` assertion alongside their existing `ChangeKind`/
  verdict assertions, for every mutation the oracle already covers — no new
  mutation catalogue, just a wider assertion on the existing one.
- A property test (`test_detector_properties.py`, `slow`) stating the
  general invariant: for any generated snapshot pair, every emitted
  `Change.evidence_provenance` is non-`None` once Phase 1 completes for that
  kind's producing detector (mirrors the property-test discipline
  `AGENTS.md`'s "Primitive-level property tests" section already
  establishes for reusable primitives — this is the same idea applied to a
  cross-cutting field rather than a merge primitive).

## Example fixtures

None new required — every existing detector-oracle/example fixture already
exercises the call sites this plan wires; the assertions widen, the fixtures
don't need to.

## Effort & risk

**XL**, genuinely multi-day per the investigation this plan formalizes, and
explicitly *not* a single PR — Phase 1 alone is bounded at "one detector
module's slice per PR, gated on that slice's own FP-rate/mutation-score
re-run" specifically to keep each PR's blast radius reviewable, per the
`AGENTS.md` incident history this document already cites twice. Risk is
**medium-high** on the L2 slice specifically (Phase 1 step 2) — the same
class of subtlety the layout-finding investigation surfaced (correct
provenance varies per-instance, not just per-kind) is most present there.

## Out of scope

- **Phase 4's `evidence_status_for_result` re-scoping** — real, but a
  separate change to a separate, already-shipped consumer; not required to
  call this plan's own acceptance criteria met.
- **A UI/report-rendering redesign** around the new field — Phase 3 adds it
  to existing surfaces (JSON, SARIF) unchanged in shape otherwise.
- **`abicheck/compat/cli.py`'s ABICC-compatible surface** — that format has
  its own, externally-defined schema (ABICC parity) with no slot for this;
  not extended here.
- **Retroactively re-verifying every closed `AGENTS.md` known-gap entry**
  this plan's provenance model could theoretically help diagnose faster in
  the future (the toolchain-identity-probe gap, the linkage-blind-removal
  gap) — those stay exactly as documented; this plan does not attempt to
  close them as a side effect.
