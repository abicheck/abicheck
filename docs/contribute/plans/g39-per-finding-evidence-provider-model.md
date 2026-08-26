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

### Phase 1 — wire the finding-construction call sites (XL, phased itself)

**Corrected inventory** (an earlier draft of this plan cited "~45
`Change(...)` construction sites" — verified by grep before this revision
and found materially wrong; recorded here so it isn't re-derived
incorrectly a second time): direct `Change(...)` construction is rare —
about 14 sites total. The dominant path is `diff_helpers.make_change()`,
a shared factory called roughly 350 times across the detector modules,
which already forwards arbitrary keyword arguments straight to `Change(...)`
unchanged (`**change_kwargs: Any` in its signature). This has a real,
favorable consequence: **`make_change()` itself needs no signature
change** — any call site can already pass `evidence_provenance=(...)`
through today's `**change_kwargs`. The actual Phase 1 work is therefore
not "extend one factory, then wire ~45 sites downstream of it" — it's
"decide and pass the *right value* at every one of the ~350+14 real call
sites," which is a larger, not smaller, inventory than the original
estimate, spread across every detector module that emits findings, not
only `diff_symbols.py`/`diff_types.py`. Confirmed direct-construction call
sites (bypassing `make_change()`) span `bundle_models.py`, `checker.py`,
`internal_leak.py`, `pattern_verdicts.py`, `post_manifest.py`,
`post_processing.py`, `post_processing_reachability.py`, `stack_checker.py`,
`versioned_symbol_scheme.py`, `cli_buildsource_helpers.py`, and
`diff_type_spellings.py`, in addition to `diff_helpers.py`'s own factory
body — each needs individual attention, not just the `make_change()`
callers.

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
   constant.
2. **L2 header-derived detectors** (`diff_types.py`'s struct/enum/typedef
   findings, `diff_type_spellings.py`) — provenance here genuinely varies
   per finding (which backend produced *this specific* `RecordType`, and
   was it DWARF-corroborated) — this is the harder, `AbiSnapshot`-inspecting
   slice the investigation above already partially scoped for one
   `ChangeKind` family (layout). Generalize that same reasoning (check the
   *specific* fields the finding rests on, not the snapshot's aggregate
   backend) across every `diff_types` detector, not just vtable/size/
   alignment.
3. **L3-L5 build/source detectors** (`buildsource/*.py`) — already carry
   `evidence_category`; extend rather than duplicate — a detector that
   already knows its own `evidence_category` value knows enough to also
   state a `l3:`/`l4:`/`l5:` `evidence_provenance` entry with no new
   information gathering, only a second field set from data already in
   scope at the call site.
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

A `ChangeKind` with no entry fails CI — the exact mechanism that closes the
class of bug this plan's own "Problem" section names (a kind whose call site
was never revisited).

### Phase 3 — report/schema surface (S)

**Implementation location (ADR-061):** `report/` already exists as a real
package (`abicheck/report/document.py`'s `ReportDocument`,
`render_json.py`, `render_text.py` — created by the same ADR-061 migration
that produced this routing table) and is the named owner for "a report
field, report schema, or output format." `evidence_provenance` reaches the
canonical JSON path through `report/render_json.py`/`document.py` directly,
not through the legacy `reporter.py`, which remains a delegation facade
where it still fronts a documented public path. SARIF and JUnit rendering
had not migrated into `report/` at the time this plan was written (only
JSON and text renderers had) — reaches `sarif.py`'s existing `properties`
bag (one entry, not a new top-level SARIF concept) if that's still the live
SARIF renderer at implementation time, or `report/render_sarif.py` if that
migration has landed by then. Also reaches the generated docs
(`scripts/gen_detector_spec.py`'s matrix gains a column once every kind has
a real, non-`UNVERIFIED` classification — gated on Phase 2's completeness
test, so the docs generator cannot claim more coverage than actually
exists).

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

No new extraction anywhere. Every phase above threads a fact the producing
code already has in scope (which detector module ran, which snapshot field a
`RecordType`/`Function` value came from, whether `dwarf_layout_coherence`
backfilled it) into the existing `Change(...)` call — the same "provenance
plumbing, not a new evidence source" framing `AGENTS.md`'s own entry already
used to size this at "multi-day, not a quick fix": the *volume* of call
sites is the cost, not any single site's complexity.

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
