# ADR-060: Synthetic-Consumer Compile-Probe Layer — Deferred

**Date:** 2026-08-10
**Status:** Accepted — not implemented (decision to defer); no future phase is
currently scheduled to pick this up. A concrete driving case already exists
(`case111`, see below); revisit once a scoped synthesis-strategy design also
exists (see "Revisiting this decision" below).
**Decision maker:** Nikolay Petrov

## Context

[G31](../plans/g31-header-graph-default-on-followup.md) Phase D names a
"synthetic-consumer compile-probe layer" as an open item: actually compiling
a synthetic consumer against a library's old and new header sets and
observing real compiler diagnostics (errors, warnings, overload-resolution
ambiguity) as *corroborating evidence* for a compatibility finding. Phase D's
own text is explicit that if this turns out to be out of scope for the
initiative, "record that explicitly as an ADR rather than silently dropping
it" — the same discipline G28 Phase 5 used when deferring concepts/
`requires` handling to [G4](../plans/g4-header-ast-extractor.md). This ADR is
that record.

**What this would be, precisely.** For a given comparison, the layer would:
synthesize a small consumer translation unit that exercises the public
declarations a finding names (a function call, a type instantiation, a member
access), compile that same synthetic TU once against the old header set and
once against the new one, and read the compiler's own diagnostics
(succeeds / fails-to-compile / emits-a-new-warning) as a second, independent
signal alongside abicheck's own AST-derived detection.

**What it is deliberately not.** Four existing mechanisms sit close enough to
this idea that it's worth stating precisely why none of them already is it —
including one, `source_smoke.py`, that a review round on this ADR correctly
flagged as missing from an earlier draft's inventory and close enough to
change the framing below:

1. **`examples/*/app.c` / `app.cpp` runtime fixtures.** Every example case
   already carries a small consumer program, but it is compiled once, against
   one version, to produce a *ground-truth runtime behavior* (does it crash,
   what does it print) — not compiled against both old and new headers to
   observe *compile-time* diagnostics as corroboration for a specific
   finding. It answers "is this example's documented verdict correct," not
   "does the compiler agree with this specific detector."
2. **`abicheck/probe_harness.py`'s compile probe.** This exists to solve a
   different problem entirely: a header-only library has no `.so` to
   snapshot, so a synthetic consumer is compiled once (per configuration) to
   produce an object file that is then *fed into the normal snapshot
   pipeline* (DWARF/mangled-symbol extraction) as a substitute for a real
   binary. It never compiles the same probe against two header versions and
   never reads compiler diagnostics as a signal — its output is a snapshot,
   not a diagnostic.
3. **`contrib/abicheck-clang-plugin`.** A compile-time AST *facts* extractor
   (an alternative L2 fact source), not a diagnostic-corroboration mechanism
   — it augments what abicheck knows about a declaration, it doesn't compile
   a consumer against it.
4. **`abicheck/source_smoke.py`'s two-sided consumer compile/link check.**
   This is the closest existing relative — `run_source_smoke()` genuinely
   does compile the same consumer TU against a v1 and a v2 header/lib pair
   and reads the compiler's real success/failure as the check's own oracle.
   `case111_enumerable_thread_specific_lambda_ambiguity`
   (`catalog/ground_truth.json`) is exactly this ADR's proposed mechanism
   working end to end for one hand-built case: v1's `ets({})` compiles, v2's
   equivalent is genuinely ambiguous under real overload resolution, and the
   case is recorded with `detectability: none` — every snapshot-level
   evidence tier (L0–L5) reaches `COMPATIBLE`, only the compile-time probe
   proves the true `API_BREAK`. What `source_smoke` does **not** do is what
   distinguishes it from the layer this ADR is about: every `SourceSmokeSpec`
   is **hand-authored** — a human writes the exact `v1`/`v2` consumer source,
   the exact `replace` edits, and the exact expected outcome, one spec per
   example case. Nothing derives a probe automatically from a diff or a
   `Change`, and `run_source_smoke` is wired only into the examples
   ground-truth harness (`tests/validate_examples.py`,
   `scripts/evidence_tiers.py`) — never into `checker.compare()` or any
   report a real user's `compare`/`scan` invocation produces. It is a
   fixture-level *oracle* used to pin what the canonical verdict for a
   curated case should be, not a corroboration signal the tool computes for
   an arbitrary comparison. Section "Decision" below revises its reasoning
   in light of this case.

None of the four can be trivially extended into the general, per-comparison
compile-probe layer without a real design of its own — see the "Synthesis
strategy" item below for exactly what `source_smoke`'s hand-authoring leaves
unsolved.

## Decision

**Defer.** Do not build the synthetic-consumer compile-probe layer as part
of G31, and do not schedule it into a numbered follow-up phase speculatively.
Revisit once a scoped synthesis-strategy design exists (see "Revisiting this
decision" below) — a driving case is not the open question, see below.

### Why

- **A driving case exists, but it doesn't resolve the design question.**
  An earlier draft of this ADR claimed no concrete case motivates this
  layer; that was wrong, and a review round caught it directly against
  `catalog/ground_truth.json`. `case111` is a real, already-recorded proof
  that a compile-time probe catches an `API_BREAK` (constructor-overload
  ambiguity) every current evidence tier (L0–L5) misses —
  `known_detector_gap: "constructor_overload_ambiguity"` says so explicitly.
  What case111 does *not* supply is a *general* mechanism: its `source_smoke`
  spec is one human-authored consumer program, hand-crafted to reproduce one
  specific ambiguity, checked once as a fixture oracle — not a procedure for
  deriving the right consumer TU automatically from an arbitrary `Change` or
  public declaration the way a real compile-probe *layer* would need to.
  Restated precisely: this ADR's own first revisit criterion below
  ("a concrete case … where a compiler diagnostic is the only evidence
  source that would have produced the correct verdict") is **already
  satisfied**, today, by case111. The deferral does not rest on that
  criterion being unmet — it rests on the second one: nobody has designed
  *how* to generalize what case111's author did by hand into something the
  tool does automatically, for an arbitrary comparison, without either
  missing the exact call shape that would fail or probing every public
  declaration on every run. The "Synthesis strategy" item below is exactly
  that unsolved question, restated with case111 as its concrete existence
  proof rather than a hypothetical. This section is corrected accordingly;
  see "Revisiting this decision" for the updated criteria.
- **Non-trivial design surface, not a mechanical extension.** A real
  implementation needs answers to at least four questions none of the four
  adjacent mechanisms above answer for free:
  1. **Synthesis strategy** — how a synthetic consumer TU is derived per
     finding (or per public entry) robustly across both C and C++ (template
     arguments, overload sets, default arguments, macro-gated
     declarations), without either under-covering (missing the exact call
     shape that would fail) or over-generating (probing every public
     declaration on every comparison, which does not scale — G31 Phase D's
     own perf-gate work exists precisely because the *existing* always-on
     header graph's marginal cost was worth a dedicated regression gate;
     an unconditional compile-probe pass per comparison would be a much
     larger cost of the same shape). `case111`'s own `source_smoke` spec is
     the concrete illustration of how hard this specific sub-problem is: its
     author had to know, in advance, that `ets({})` (brace-init against the
     new factory-typed overload) was the one call shape that would turn
     ambiguous — a generic synthesizer would need to either enumerate
     representative call shapes per changed/added overload automatically
     (expensive, and still not guaranteed to hit the one shape that breaks)
     or accept that it only catches what a human already anticipated,
     which is a materially weaker claim than "corroborating evidence for an
     arbitrary finding."
  2. **Evidence-model placement** — whether this is a new evidence tier
     (alongside L0–L5, see `docs/learn/evidence-and-detectability.md`) or a
     corroboration-only signal folded into an existing tier's confidence,
     and how it interacts with the depth/evidence contract
     (`ADR-037` D5, `scan`'s `_check_scan_evidence_contract`) that already
     pins what each depth level promises.
  3. **Verdict mapping** — how a compiler's own diagnostic (a hard error, a
     new warning, a silently-different overload resolution with no
     diagnostic at all) maps onto a `ChangeKind`/severity, and how it
     interacts with ADR-049's contract-relevance evaluation (does a
     compile-probe failure feed `contract_evidence_refs`, or is it a
     wholly separate signal a report surfaces alongside a finding without
     participating in the gate).
  4. **Trust and sandboxing.** A synthesized consumer TU is compiled with
     real, resolved toolchain flags — the same class of trust-boundary
     surface the root `AGENTS.md`'s "Known gaps" section already documents
     at length for `profiles.<id>.compile.args` (the `_DANGEROUS_ARG_PREFIXES`
     denylist and its several review-round hardening passes). A new
     compile-invocation surface driven by synthesized, not user-authored,
     source text needs its own review against that same class of risk
     before it exists at all, not an assumption that reusing the existing
     denylist is sufficient.
  Each of the four is a real design decision, not a detail to fill in while
  coding — exactly the bar this repository's ADR process exists to clear
  before implementation starts (see
  `docs/contribute/adr/044-reachability-aware-suppression.md`'s own
  "Post-merge review rounds" note, cited by G31's Phase B entry as the same
  bar graph-identity changes need).
- **Cost is real and ongoing, not one-time.** Unlike a detector that runs
  once against already-extracted facts, this layer would invoke a real
  compiler binary per synthesized probe per comparison — the same
  `castxml`/`clang`/`g++` availability and cross-platform-toolchain
  concerns that already make G31 Phase D's own perf-regression gate
  optional/self-skipping when a compiler isn't present. A corroboration
  signal that silently degrades to "not run" on a large fraction of CI
  environments is a weak deliverable for the design cost above.

## Revisiting this decision

Reopen when **both** of the following hold — criterion 1 is already
satisfied (see below); criterion 2 is the actual blocker:

1. ~~A concrete case…~~ **Already satisfied**, by `case111` and its recorded
   `known_detector_gap: "constructor_overload_ambiguity"` — every existing
   evidence tier (L0–L5) genuinely cannot reach the correct `API_BREAK`
   verdict for it today (`detectability: none`), and no cheaper structural
   fix (extending `type_reachability.py`/`diff_cxx_rules.py`) is known to
   close it, since the failure mode is about downstream call-site overload
   resolution, not anything a snapshot alone encodes. A future proposal does
   not need to go looking for a first example — it already exists and should
   be the proposal's worked case. If additional cases accumulate in the
   FP-rate/per-tier-accuracy corpora or `examples/` with the same
   `known_detector_gap` shape, cite them too, but one is enough to satisfy
   this criterion.
2. **Not yet satisfied.** A scoped design answering the four questions above
   (synthesis strategy, evidence-model placement, verdict mapping, trust
   boundary) — written as its own plan doc or ADR, not folded into a future
   G31 phase as a drive-by addition. The synthesis-strategy question in
   particular must explain how to go from `case111`'s one hand-authored
   probe to something generated automatically for an arbitrary comparison,
   without simply enumerating every public declaration's call shapes on
   every run. The naming precedent from G31 Phase D
   (`PUBLIC_API_IMPACT_PROOF_PATH_CHANGED` and its siblings) already shows
   this needs Phase B's canonical-identity work in place first if the
   compile-probe result is to be linked to a specific graph proof path — so
   a future proposal should cross-reference
   [ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md)
   rather than treat the probe layer as independent of it.

## Consequences

- G31's plan doc (`docs/contribute/plans/g31-header-graph-default-on-followup.md`)
  is updated to mark this item resolved-via-deferral, linking here, rather
  than left as an open, unscoped bullet.
- No code, schema, or `ChangeKind` changes ship with this ADR — it is a
  scope decision only.
- `probe_harness.py`, the `examples/*/app.c`/`app.cpp` fixtures,
  `contrib/abicheck-clang-plugin`, and `source_smoke.py` are all unaffected
  and continue serving their existing, distinct purposes described above —
  in particular, `case111`'s `known_detector_gap` stays recorded as an open,
  tracked gap; this ADR does not resolve it, it only declines to build a
  general mechanism for it right now.

## Cross-references

- [G31 — Header-Graph Default-On: Follow-Up Phases B–D](../plans/g31-header-graph-default-on-followup.md)
  — the plan whose Phase D named this item.
- [ADR-048](048-canonical-entity-identity-and-graph-reconciliation.md) —
  canonical entity identity; a future compile-probe design should build on
  this rather than duplicate its identity resolution.
- [ADR-037](037-cli-interface-contract.md) — the depth/evidence contract
  (D5, the scan evidence-contract check) this layer would need to fit into
  if ever built.
- [ADR-044](044-reachability-aware-suppression.md) — the "post-merge review
  rounds" bar this decision applies to any future graph-identity-adjacent
  design.
- `docs/learn/evidence-and-detectability.md` — the canonical L0–L5 evidence
  model a compile-probe tier would need to be placed within.
