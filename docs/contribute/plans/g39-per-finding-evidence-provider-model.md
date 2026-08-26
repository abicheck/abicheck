# G39 — Per-finding evidence-provider model

**Status:** Proposed; not started.

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

### Phase 1 — wire the ~45 `Change(...)` construction sites (XL, phased itself)

Confirmed by grep before this plan was written: `Change(` construction sites
number in the low-to-mid 40s across `diff_symbols.py`, `diff_types.py`,
`diff_platform.py`, `diff_versioning.py`, `diff_vtable_layout.py`,
`diff_sycl.py`, `diff_stdlib_impl.py`, and `buildsource/*.py`'s L3-L5
detectors. **Not one flag day.** Ordered by risk, matching this file's own
"known gaps over risky reactive patches" discipline and the FP-rate/
mutation-score gate structure already in place:

1. **L0/L1-only detectors first** (`diff_symbols.py`'s symbol-table-driven
   findings, `diff_platform.py`'s ELF/PE/Mach-O-specific findings) — the
   provenance is a static fact of which module produced the `Change`, not a
   per-call derivation, so this slice is close to mechanical: one constant
   tuple per detector function, threaded through the existing `Change(...)`
   call.
2. **L2 header-derived detectors** (`diff_types.py`'s struct/enum/typedef
   findings) — provenance here genuinely varies per finding (which backend
   produced *this specific* `RecordType`, and was it DWARF-corroborated) —
   this is the harder, `AbiSnapshot`-inspecting slice the investigation
   above already partially scoped for one `ChangeKind` family (layout).
   Generalize that same reasoning (check the *specific* fields the finding
   rests on, not the snapshot's aggregate backend) across every `diff_types`
   detector, not just vtable/size/alignment.
3. **L3-L5 build/source detectors** (`buildsource/*.py`) — already carry
   `evidence_category`; extend rather than duplicate — a detector that
   already knows its own `evidence_category` value knows enough to also
   state a `l3:`/`l4:`/`l5:` `evidence_provenance` entry with no new
   information gathering, only a second field set from data already in
   scope at the call site.

Each slice, once wired, gets its own FP-rate/mutation-score gate re-run
before merging — never all ~45 sites behind one PR. The FP-rate/mutation-
score gates exist precisely because a previous incident in this exact area
(the reverted "linkage-blind removal" and "vptr-offset-bits" attempts,
`AGENTS.md`'s own record) showed field-level changes to shared detector
plumbing can silently reintroduce a false positive or false negative that
passes every hand-written example test.

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

`evidence_provenance` reaches JSON reports (report schema version bump,
`reporter.py`), SARIF (`sarif.py` — one `properties` bag entry, not a new
top-level SARIF concept), and the generated docs
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
(`checker_types.py`'s own `to_dict`/`from_dict`), SARIF, and every existing
consumer.

## Files & surfaces

- `abicheck/checker_types.py` — the new field.
- `abicheck/diff_symbols.py`, `diff_types.py`, `diff_platform.py`,
  `diff_versioning.py`, `diff_vtable_layout.py`, `diff_sycl.py`,
  `diff_stdlib_impl.py` — Phase 1's ~45 call sites.
- `abicheck/buildsource/*.py` — the L3-L5 detectors already carrying
  `evidence_category`.
- `abicheck/reporter.py`, `abicheck/sarif.py` — Phase 3.
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
