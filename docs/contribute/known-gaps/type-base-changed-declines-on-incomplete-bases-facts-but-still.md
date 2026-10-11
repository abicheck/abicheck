---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# type_base_changed declines on incomplete bases facts but still fires on a PRESENT-but-empty capture gap

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** The vtable half is fixed. compare/base_class_diff.py:36-71 diff_bases now gates bases_fact/virtual_bases_fact through compare_facts and declines when a side is NOT_COLLECTED/FAILED/PARTIAL. A PRESENT-but-empty capture gap can still fabricate type_base_changed, as the entry describes.

A list-valued
`RecordType` field cannot express "not captured", so an empty-vs-non-empty
difference conflates a real change with one side's debug info simply not
covering the declaring TU. Confirmed for `vtable`: identical headers, no
DWARF vtable, and zero `_ZTV` symbols on either side still produced a
`BREAKING` `type_vtable_changed`, because `_diff_type_vtable` guarded on
`t_old.vtable == t_new.vtable` and nothing else. Fixed by requiring an
independent layout signal (a size change — the vptr a genuinely polymorphic
class gains — or a virtual-base change) before an empty↔non-empty
transition is reported; both-sides-captured differences are untouched, and
an unknown size on either side keeps the finding, since the suppression
needs positive evidence that layout held still rather than being a fallback
for missing information. This is the discipline `diff_vtable_layout.py`
(tri-state `None`, "degrading to B1's L0 view rather than fabricating a
break") and `diff_elf_layout.py` (compare only a `_ZTV` present on *both*
sides) already state in their own docstrings; the type-level detector had
neither. **Two things remain open.** (1) `_diff_type_bases`
(`set(t_old.bases) != set(t_new.bases)`, and its virtual-base half) has the
same unguarded shape and **stays that way — an attempted guard was written
and reverted before merge, and the reason is worth not rediscovering.**
Every layout-based premise for it is false: an *empty* base is invisible by
the empty-base optimization, and — the one that killed the attempt — a
*storage-contributing* base can be added without moving the derived class's
size at all when the class is over-aligned (verified against g++:
`struct alignas(8) D {}` and `struct alignas(8) D : B {}` with
`struct B { int y; }` are both 8 bytes, as are the `alignas(16)` pair at
16). So "size held still" proves nothing about a base list, in either
direction. Unlike the vtable case there is no independent evidence stream
to fall back on — `snapshot.functions` answers "did this class's virtuals
change" but nothing answers "did this class's bases change" except
`RecordType.bases` itself. Guarding it therefore needs evidence the model
does not currently carry (per-finding provenance, or a captured base-layout
fact such as `base_offsets` corroboration), not a cleverer reading of
`size_bits`. Until then a fabricated `type_base_changed` from a capture gap
is the accepted cost, because the alternative — suppressing a real
hierarchy change, which is sometimes the *only* breaking finding a
same-size base addition produces — is strictly worse. (2) The vtable guard is **narrower than
a first reading suggests, and its own docstring used to overclaim it.** The
class's virtual functions and its `RecordType.vtable` are two projections of
the same DWARF evidence (both trace to `DW_TAG_subprogram`), not independent
streams — so a translation unit whose coverage vanishes can take both, the
two sides' signature sets then differ, and the guard declines to suppress.
The false positive survives in that shape. That is the failure direction to
have (it leaves a pre-existing false positive standing rather than hiding a
real break), but it means the guard covers the *reported* case — identical
headers, no DWARF vtable, no `_ZTV` anywhere, virtuals absent from both
sides — and not every capture gap. Closing the rest needs artifact or
provenance evidence (`_ZTV` presence, per-finding providers) the type-level
detector does not receive today. (3) One accepted
false negative in the
fix itself: a class already polymorphic *through a base*, declaring no
virtuals of its own, that gains one — its vtable grows while its object
size does not, making it indistinguishable from capture noise without a
real polymorphism walk over both sides' base chains
(`diff_vtable_layout._is_polymorphic`) plus the per-finding provenance the
entry below describes. (4) A *pure* virtual reaches that same size backstop
for a different reason: it has no out-of-line definition, so
`dwarf_snapshot` drops its declaration-only DIE from `snapshot.functions`
while still counting it as a vtable child of the class, leaving both
owned-signature sets empty — and with `alignas` absorbing the new vptr the
size does not move either. Reproduced against g++
(`struct alignas(8) A { virtual void f() = 0; }` compiled alongside a
concrete derived class, which is what makes GCC emit A's complete DIE
rather than a `DW_AT_declaration` stub): old vtable `[]`, new vtable
`['_ZN1A1fEv']`, both 8 bytes, `_ZN1A1fEv` absent from the function map on
both sides. **An attempt to close this one was written, merged into the
branch, and then reverted — the reason is the most useful thing in this
entry.** The fix consulted `RecordType.vptr_offset_bits` as "the layout
descriptor's own witness, the only signal here that is not another
projection of the same subprogram DIEs." That claim was false, and
checkably so: **both** producers assign the field as `0 if vtable else
None` (`dwarf_snapshot.py`, `dumper_castxml.py`), so on every real DWARF
or CastXML snapshot `(old.vptr is None) != (new.vptr is None)` is
*identical to* the empty↔non-empty vtable transition being guarded. It was
therefore true by construction for every input that reached it, which did
not merely weaken the guard — it made the guard **inert**, restoring the
original capture-gap false positive in full (Codex review). Only the
optional `ABICHECK_CLANG_LAYOUT_TOOL` path computes a real vptr, and
nothing in the model distinguishes that value from the derived one, so the
field cannot be trusted here at all. Reverted; case (4) joins case (3) as
an accepted false negative. **The tests are the second lesson**: the
guard's unit tests built `RecordType`s with `vptr_offset_bits` left `None`
on both sides — a record no backend can emit — so the entire suite,
including the pre-existing test for the guard's whole purpose, passed
against a guard that suppressed nothing on real input. The helper now
derives the field the way the producers do, one test pins that derivation
in the producers' own source (so the reasoning fails loudly if a producer
ever computes a real vptr), and five of these tests fail against the
reverted revision. Worth recording for both (3) and (4) that the break is
**not** hidden: `diff_layout._check_vptr_introduced` fires independently
on the same `None → 0` transition and the verdict stays `BREAKING`
(verified end to end, and now pinned by its own test) — which is also why
the FP-rate corpus, a verdict-level gate, could not catch either the
original false positive or the inert-guard regression, and why the
regression coverage is a direct unit test on the predicate
(`tests/test_vtable_evidence_guard.py`) instead. Leaning on a sibling
detector remains uncomfortable, but the alternative on offer was a witness
that only appeared independent. Guarded by four FP-corpus cases under the
`evidence-absence` axis (one FP guard, three FN sentinels), the unit tests
above, and three Hypothesis properties in
`tests/test_detector_properties.py`.

**A named follow-on, found by a user rather than by any gate in this
repo, and worth being explicit about why: two detectors reading the
identical evidence gap and reaching two different severities is its own
bug class, distinct from either detector being individually wrong.**
`LAYOUT_UNVERIFIABLE` (`diff_layout.py`) answers "was there real layout
evidence for this type" from the exact same asymmetric-evidence condition
`_vtable_transition_is_evidenced` above declines to suppress on. Both
answers are individually correct and individually documented (this entry
is the vtable side's own justification) — but a type carrying both at
once reads as a hard BREAKING verdict sitting next to a finding that
already says the evidence for that type couldn't be verified either way,
reproducible with zero real ABI change (scanning a binary against a dump
of itself; reported against real oneDAL binaries, three types, two
libraries). None of this repo's existing quality gates were positioned to
catch it: the FP-rate/tier-accuracy gates are verdict-level (the overall
verdict was already correctly `BREAKING`, dominated by the vtable
finding, so nothing there looked wrong), and every existing test —
including the Hypothesis properties this entry already describes — is
scoped to one detector's own correctness in isolation, never to whether
two detectors' outputs are mutually *legible* when they land on the same
subject.

**The fix went through four designs before landing, and the last pivot is
the one worth reading closely — it generalizes past this one pair of
detectors.** (1) Demoting `TYPE_VTABLE_CHANGED` was tried first and
rejected immediately: a real last-virtual-method removal with a new-side
capture gap is indistinguishable from this same evidence gap, so
softening its severity risks a false negative on a genuine break. (2)
Folding the now-redundant `LAYOUT_UNVERIFIABLE` finding into
`redundant_changes` unconditionally was tried next, and review found four
real defects in it in turn: correlating on bare `Change.symbol` (two
distinct same-named records in different namespaces can share it, so
fold on `qualified_name`); folding on mere co-occurrence rather than a
marker recording *why* the stronger finding was kept (an
independently-evidenced `TYPE_VTABLE_CHANGED` — a real reorder, a real
size delta, a real virtual-base change — must never trigger the fold);
reusing `Change.modulation_reason`/`modulation_rule` for internal
signaling (those are a public, report-facing audit trail for an actual
verdict override — tagging an unmodulated finding with them is a false
audit entry a downstream consumer would read as real); and running the
fold *before* `FilterNonPublicSurface`/`ApplySuppression` had settled
both findings, which both hid the redundant finding from a suppression
rule targeting it directly and let it outlive its covering finding's
later exclusion. (3) Fixing all four and gating the fold on a
policy-resolved-verdict subsumption check (`checker.
_vtable_gap_finding_is_verdict_subsumed`) closed the `PolicyFile`-override
case, but review found the fix was still solving the wrong layer: **the
severity-scheme axis (`severity.SeverityConfig` — `abi_breaking`/
`potential_breaking`/etc., each independently `error`/`warning`/`info`)
is chosen by the CLI/reporter caller entirely *after* `compare()`
returns, so no compare()-time decision to remove a finding from
`changes` can ever be correct for it.** Concretely: a caller configuring
`abi_breaking=info` / `potential_breaking=error` wants
`LAYOUT_UNVERIFIABLE`'s error-level contribution to reach
`severity.compute_exit_code`, but that function reads `result.changes`
directly — if the fold already removed the finding because the
*policy* axis (a completely orthogonal gate, resolved before
`compare()` returns) ranked the covering `TYPE_VTABLE_CHANGED` as more
severe, the severity axis never gets a chance to see it, and this
reproduces with **zero policy override involved** (Codex review, fresh
evidence). The same review round also found the fold's pipeline
ordering relative to `DemoteUnreachableInternalChurn` (which runs later,
to demote a confirmed-unreachable internal-namespace type) could orphan
an already-made fold decision when the covering finding was itself
demoted afterward. (4) **The only structurally-safe fix was to stop
removing information from `changes` for this reason at all.**
`post_processing.AnnotateLayoutUnverifiableCoveredByVtableChanged`
leaves both findings exactly where they always were and only sets the
already-existing, generic `Change.correlated_change_kind` field (reused
from its original ADR-041 purpose, not a new one) on the redundant
`LAYOUT_UNVERIFIABLE` finding, pointing at `"type_vtable_changed"`.
`Change.vtable_covers_unverifiable_layout_gap` (set in
`diff_types._diff_type_vtable`) still records which `TYPE_VTABLE_CHANGED`
findings rest purely on the ambiguous evidence gap versus real,
independent evidence — that detection logic is exactly what survived all
four designs unchanged; only the *action* taken on it changed. This
design is immune by construction to every one of the defects above and
to any future one shaped like them, because nothing is ever hidden from
a consumer that reads `changes` — there is no "was this the right time
to remove it" question left to get wrong. **The general principle this
leaves behind: never remove a finding from a shared, multiply-consumed
list (`DiffResult.changes`) to resolve what is fundamentally a
*presentation* problem (two findings reading as contradictory) when that
list has independent downstream consumers — a legacy verdict, a
policy-file override, a severity-scheme exit code, a future pipeline
step — that this fix cannot fully enumerate and whose configuration is
chosen after the removal decision was made. Annotate; never remove.**
Regression coverage: `tests/test_vtable_severity.py`'s
`TestLayoutUnverifiableCorrelatedWithVtableChanged` (hand-picked example
cases, including one exercising the severity-axis gap directly via
`severity.compute_exit_code`) and a generalized Hypothesis property,
`test_layout_unverifiable_always_correlated_when_vtable_change_shares_its_gap`
in `tests/test_detector_properties.py`, checking the annotation holds
over arbitrarily generated classes and evidence-descriptor shapes.

**A whole-class structural fix was attempted mid-way through the fold
design (2) above, for the field-ordering half of it, and reverted — the
in-repo audit that justified it was the wrong audit.** (This sub-entry
predates the pivot to design (4) and is kept because the lesson is
independent of which design won.) The field-ordering bug (found twice
now: first on `AbiSnapshot` in PR #582, again on the new `Change` field
this fix originally added mid-list) was first "fixed" by making `Change`
keyword-only from `old_value` onward via the `dataclasses.KW_ONLY`
sentinel, on the strength of an AST-parse of every `Change(...)` call
site in this repo confirming every positional call anywhere passes
exactly the three required leading fields and never more. That audit
answers the wrong question for a type this codebase itself documents as
public API (`checker_types.py`'s own module docstring; CLAUDE.md:
"changing their public surface is a breaking change to the Python API —
coordinate it"): it proves this repo's own call sites are safe, and says
nothing about an external consumer who previously called `Change(kind,
symbol, description, old_value, new_value, ...)` positionally — for whom
the whole-class sentinel is exactly the same breaking shift the fix
exists to prevent, just moved to a boundary this repo cannot see or test
(Codex review, fresh evidence). Reverted in favor of the same per-field
`field(kw_only=True)` PR #582 already used for `AbiSnapshot`, applied
only to the one new field (`vtable_covers_unverifiable_layout_gap`) and
appended at the true end of the dataclass (not mid-list) so every
pre-existing field's position — and therefore every pre-existing
positional caller's behavior, in or out of this repo — is completely
unchanged. The lesson generalizes beyond this one field: for a type
documented as public API, "no in-repo caller breaks" is not the bar:
prefer the narrowest change that cannot break a caller this repo cannot
see, even when a broader structural fix looks more complete. The
`KW_ONLY` sentinel is still the right tool for a genuinely *internal*
dataclass with no external-construction contract (nothing here argues
against it in general) — the mistake was applying it to one that has
such a contract without checking for one first.
