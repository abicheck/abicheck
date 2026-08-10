# ADR-059: Synthetic-Consumer Compile-Probe Layer — Deferred

**Date:** 2026-08-10
**Status:** Accepted (decision to defer) — not implemented; no future phase is
currently scheduled to pick this up. Revisit only once a concrete gap in
existing evidence tiers motivates it (see "Revisiting this decision" below).
**Decision maker:** pending

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

**What it is deliberately not.** Three existing mechanisms sit close enough
to this idea that it's worth stating why none of them already is it:

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

None of the three can be trivially extended into the compile-probe layer
without a real design of its own.

## Decision

**Defer.** Do not build the synthetic-consumer compile-probe layer as part
of G31, and do not schedule it into a numbered follow-up phase speculatively.
Revisit only when a concrete driving case exists (see below).

### Why

- **No driving gap today.** Nothing in the FP-rate corpus
  (`scripts/check_fp_rate.py`) or the per-tier accuracy gate
  (`scripts/check_tier_accuracy.py`) currently shows a false positive or
  false negative that a real compile — as opposed to a deeper AST/DWARF
  fact — would have caught. The class of case where a compiler's own
  overload-resolution/template-instantiation diagnostic is the *only*
  reliable signal (a public template whose ill-formedness depends on a
  caller's own argument types, SFINAE-driven API surface, or macro-gated
  declarations that only resolve one way per translation unit) is real but
  narrow, and abicheck has no example case today demonstrating a specific
  finding this layer would have caught and the existing L0–L5 evidence
  tiers would have missed. Building speculative infrastructure ahead of a
  concrete failure case is exactly the trap `docs/contribute/plans/`'s
  existing "deferred entirely" sections (see the root `AGENTS.md`'s "Known
  gaps" trend-database and devcontainer entries) already warn against for
  this codebase.
- **Non-trivial design surface, not a mechanical extension.** A real
  implementation needs answers to at least four questions none of the three
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
     larger cost of the same shape).
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
  before implementation starts (see `docs/contribute/adr/044-reachability-
  aware-suppression.md`'s own "Post-merge review rounds" note, cited by
  G31's Phase B entry as the same bar graph-identity changes need).
- **Cost is real and ongoing, not one-time.** Unlike a detector that runs
  once against already-extracted facts, this layer would invoke a real
  compiler binary per synthesized probe per comparison — the same
  `castxml`/`clang`/`g++` availability and cross-platform-toolchain
  concerns that already make G31 Phase D's own perf-regression gate
  optional/self-skipping when a compiler isn't present. A corroboration
  signal that silently degrades to "not run" on a large fraction of CI
  environments is a weak deliverable for the design cost above.

## Revisiting this decision

Reopen only when **both** of the following exist:

1. A concrete case — added to the FP-rate or per-tier-accuracy corpus, or a
   real example under `examples/` — where a compiler diagnostic is the only
   evidence source that would have produced the correct verdict, and every
   existing evidence tier (L0–L5) genuinely cannot be extended to close the
   same gap more cheaply (e.g. by improving `type_reachability.py`'s or
   `diff_cxx_rules.py`'s own structural analysis, which is usually cheaper
   than invoking a compiler).
2. A scoped design answering the four questions above (synthesis strategy,
   evidence-model placement, verdict mapping, trust boundary) — written as
   its own plan doc or ADR, not folded into a future G31 phase as a
   drive-by addition. The naming precedent from G31 Phase D
   (`PUBLIC_API_IMPACT_PROOF_PATH_CHANGED` and its siblings) already shows
   this needs Phase B's canonical-identity work in place first if the
   compile-probe result is to be linked to a specific graph proof path — so
   a future proposal should cross-reference [ADR-048](048-canonical-entity-
   identity-and-graph-reconciliation.md) rather than treat the probe layer
   as independent of it.

## Consequences

- G31's plan doc (`docs/contribute/plans/g31-header-graph-default-on-
  followup.md`) is updated to mark this item resolved-via-deferral, linking
  here, rather than left as an open, unscoped bullet.
- No code, schema, or `ChangeKind` changes ship with this ADR — it is a
  scope decision only.
- `probe_harness.py`, the `examples/*/app.c`/`app.cpp` fixtures, and
  `contrib/abicheck-clang-plugin` are unaffected and continue serving their
  existing, distinct purposes described above.

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
