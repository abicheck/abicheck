---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Source-read licence: enforced at the one consumer, not repo-wide (2026-09-12)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** Both deliberate residuals are still open: scripts/check_ai_readiness.py has no source-read licence gate (grep 'licen' finds only license-header checks), and there are no per-input content digests. The third residual is recorded as closed.


The contract — *a path recorded in a snapshot is provenance, not a licence to
re-read the current filesystem for historical facts* — is stated in
`abicheck/buildsource/source_inputs.py` and enforced in the one place that
had violated it: `workflows/pattern_preprocessor_scan.py`, plus the two
primitives beneath it (`buildsource/pattern_facts.py`'s
`find_pattern_facts`, which now refuses to stat an unlicensed root, and
`buildsource/preprocessor_facts.py`, which is simply not invoked for an
unlicensed side since `clang -E` resolves `#include`s against the filesystem
it runs on).

Two residuals are deliberately open, both recorded on the
`evidence.stored_snapshot_rederivation` bug class in
`tests/regressions/manifest.py`:

1. **No mechanical gate against a future violator.** Nothing stops a new
   consumer from reading `AbiSnapshot.source_header` (or a compile unit's
   `source`) and opening it without resolving a licence first — nor a new front
   end from calling `dumper.dump` directly and forgetting to *grant* one. Both
   halves of that already bit once: the ABICC-compatible CLI was found doing
   exactly the second (PR #1236's review round), after `service.run_dump` had
   already been fixed for the first. The shape of
   the fix is known — an AST scan in `scripts/check_ai_readiness.py`, the way
   `fact-field-readers` guards `Fact[T]` reads, with an allowlist of the
   reader sites that legitimately hold a licence — but it is a gate of its
   own, not part of this fix, and today there is exactly one such consumer to
   guard.

2. **The licence is object-level, not content-verified.** A header-derived
   live extraction grants it for the whole run; it does not digest each source
   input and re-check that digest at read time. The consequence is the conservative
   direction (a stored snapshot declines to re-derive even when the tree on
   disk genuinely *is* the one it was dumped from), which is why it is a gap
   rather than a defect. Closing it properly means persisting per-input
   content digests, i.e. an `AbiSnapshot` schema bump inside the ADR-050
   comparability contract plus a codec and migration — the "persist the facts
   at dump time" half of the original two-option brief. That is the route to
   take if stored-versus-stored pattern/preprocessor evolution ever needs to
   be answerable rather than honestly declined; do not instead widen the
   licence, which would reintroduce exactly the defect above.

**Closed, and how (one licence per evidence source).** A third residual used
to sit here: the licence required *header-derived* provenance
(`extraction_read_source_inputs`), so a dump that collected only L3 build
evidence (`--sources` with no `-H`) reported its source-derived facts as not
evaluated even though it genuinely had read its compile units, because nothing
at the grant point separated that from a *loaded* pack whose paths are as
historical as a stored snapshot's. The same coarseness had a worse, opposite
failure the same review round found: a live header dump merged with a
pre-captured `--build-info` pack was granted one snapshot-wide licence off the
header AST, and the pattern/preprocessor scans then read the *pack's* recorded
compile-unit paths from whatever occupies them on this runner — the original
fabrication, reached through the other evidence source (PR #1236, Codex P2).

Both directions are one defect: a side holds up to two source-evidence sources
with independent provenance, and one licence cannot be correct for both. So the
licence is now resolved per source — declared headers via
`snapshot_source_licence`, an embedded build pack via `build_evidence_licence`,
which asks the pack itself (`BuildSourcePack.live_source_evidence`, stamped by
`buildsource/embed.py` only for an inline collection performed in this run and,
like the snapshot flag, never serialized). A partially-licensed side reports
what it read and keeps the unlicensed roots in the expected-input account as
`not_licensed` gaps, so no *absence* is established from the half that was read.
What remains true is that the grant is *conditional*, which is the point for a
DWARF-only dump whose `DW_AT_decl_file` paths name the build machine's tree:
that side never opened them, so it gets no header licence.

Note also the *shape* of the accepted trade-off in the third fix: the
evolution fold now decides each identity from what is established for that
identity, which means `persistent` is reported from two observations even
when both sides had coverage gaps elsewhere. An earlier revision withheld it
globally. That is not a regression of the earlier fix: an incomplete scan
cannot un-see a hit, so two observations are the strongest premise the fold
has, while `introduced`/`resolved` each assert an *absence* and still require
the relevant side's sufficiency. The lexical scan cannot, however, attribute
an unread file to a particular `PatternKind`, so a gap in any declared root
leaves *every* kind's absence unestablished on that side — a per-kind answer
would need per-kind input attribution the scanner does not have, and is
recorded as a gap on `coverage.discovery_derived_completeness`.
