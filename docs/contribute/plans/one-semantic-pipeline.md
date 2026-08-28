---
doc_type: contributor
level: advanced
lifecycle: active
---

# One Semantic Pipeline — unifying application, fact, identity, and outcome models

**ADR:** [ADR-063](../adr/063-one-semantic-pipeline.md) · Proposed; nothing in
this plan implemented yet. **Effort:** XL, multi-quarter, phased — do not
attempt as one PR. **Depends on / sequences with:** ADR-055, ADR-061,
ADR-062, ADR-042, ADR-046/048, ADR-049, ADR-050 (each partially implemented
already; see "Sequencing against in-flight ADRs" below).

## Problem

AGENTS.md's own "Known gaps" section documents, over dozens of numbered
findings, one recurring root cause: the same concept (an input, a config
value, a fact's availability, an entity's identity, a semantic result) is
represented more than once in the codebase, and the representations drift
out of agreement. ADR-063 states the target architecture — and, in its own
"Governing invariant" section, the one rule every phase here exists to
enforce: **one concept, one representation, everywhere it is used, never
two.** This document is the phased, file-level plan to get there without a
rewrite and without ever leaving two live implementations of the same
concept standing side by side for longer than one phase. That is not a
style preference either document treats as negotiable: a phase whose own
PR leaves the representation it was meant to replace still reachable by
any caller is an incomplete phase, not a phase with follow-up work, no
matter how much of the new representation it built.

Three constraints shape every phase below, taken directly from this
repository's own conventions (AGENTS.md, ADR-061's migration discipline)
and from the governing invariant above:

1. **Vertical slice, not flag day.** Each phase ships one consolidation,
   behavior-preserving, independently mergeable, independently revertible.
2. **Delete after consolidating — same PR or the very next one, never
   "eventually."** A phase is not done when the new path works; it is done
   when the old path it replaces is removed and nothing in the repository
   can still reach it. A phase that only adds is half a phase, and "half a
   phase, to be finished later" is exactly the accumulation pattern this
   plan exists to stop — it does not get counted as progress toward
   consolidation until the deletion half lands.
3. **Verify at the size of the change.** A phase touching the compare/scan
   hot path re-runs the FP-rate gate, the tier-accuracy gate, and the
   mutation-score gate for any module it touches; a phase touching
   persisted schema adds a v(N) migration test and a round-trip test at
   production scale (per this repo's "third-party-boundary tests" and
   "toolchain pins" conventions).

## Sequencing against in-flight ADRs

This plan does not start from zero. Four backing ADRs are already partially
implemented, and their current state determines what each phase below can
assume:

| Backing ADR | Current state | What this plan's phases assume |
|---|---|---|
| ADR-055 (typed request/result) | D1 implemented for `compare` only | Phase 1 extends the existing `CompareRequest`/`service_compare_pipeline.py` shape to `dump` (a real `DumpRequest`/`resolve_dump_request`/`execute_dump_request` pair, already built) and finishes routing `dump`'s own real execution onto it — `scan`'s `ScanRequest` already exists separately and its candidate resolution already converges on the shared execution primitive, so Phase 1 does not extend this shape to `scan`, only to `dump` |
| ADR-061 (responsibility packages) | Phases 0-1 implemented; Phase 5 (`model` package) begun | Phase 0/2/4/7 of this plan land inside the `model`/`compare`/`policy` packages ADR-061 already created; this plan does not create new top-level packages beyond what ADR-061 names |
| ADR-062 (storage v2) | Phase 0 primitives (`abicheck/storage/`: `FactStatus`/`FactAvailability`, occurrence-preserving identity, canonical encoding, version axes) implemented and **inert** — nothing wired to a producer/reader | Phase 0/5 of this plan is the *generalization* of these primitives into the domain layer; Phase 8 of this plan is the *wiring* ADR-062 Phase 1 still needs, done jointly rather than twice |
| ADR-042 (compatibility/gate separation) | Implemented for JSON/SARIF/`compare-release`; `workflows/aggregate/gate.py`/`fold.py` still decode exit codes inline | Phase 7 of this plan closes the `gate.py`/`fold.py` gap — not a redesign of ADR-042 itself. `junit_report.py`'s own inline `_is_failure` computation is **not** one of the gaps Phase 7 closes: that phase's own corrected design leaves it exactly as it is, since it is a legitimate per-render function of each call's own `SeverityConfig`/`relevant_ids`, not a property a finding carries — there is no `RunOutcome` field for it to read instead, so it stays inline by design, not as an unclosed gap |
| AGENTS.md "PR C" (dump/scan typed convergence) | `resolve_dump_request`/`execute_dump_request` split landed; `scan`'s candidate resolution already converges on the shared `workflows.artifact.execute._resolve_side_snapshot_impl` primitive that `execute_dump_request` itself calls internally (`resolve_dump_request` only validates evidence and builds a `ResolvedDumpRequest` — it never calls the primitive; `service_input_resolution` is only a delegating facade re-exporting this module's owner), but `dump`'s own real ELF/PE/Mach-O execution still runs the legacy path, blocked on two named items (castxml availability for parity testing, `--compile-db-filter` typed surface — now closed) | Phase 1 of this plan is exactly "finish PR C" for the `dump` half, not a new design |

## Phases

### Phase 0 — `Fact[T]` in the domain layer (builds on ADR-062 Phase 0)

**Goal.** A detector cannot observe a field's value without first observing
its availability. `None`/`[]`/a boolean flag stop being overloaded to mean
both "confirmed absent" and "not collected."

**Design.** `abicheck.storage.availability_status.FactStatus` is the leaf
vocabulary this phase reuses — but ADR-061's dependency direction is
`storage -> model`, so `Fact[T]` (living in `model/`) may not import
*from* `storage`. This phase therefore **relocates** `FactStatus`/
`Confidence`/the status-order tuples from `abicheck/storage/
availability_status.py` into `abicheck/model/availability.py` (a leaf
module, no dependency on anything but the standard library, matching that
module's own existing "none of it needs to know what a stored record or a
ledger looks like" framing), and `abicheck/storage/availability_status.py`
becomes a re-export shim for one release so existing `storage.*` imports
keep working. `FactAvailability` (the ledger record) stays in `storage/`,
since *it* legitimately depends on `model`, not the other way around.

Add `abicheck/model/fact.py`: a generic `Fact[T]` with **three** fields,
not two — `status: FactStatus`, `value: T | None`, and `diagnostics:
tuple[str, ...] = ()` — mirroring `storage.FactAvailability`'s own
existing shape, which already separates its value-bearing fields from
`diagnostics` for exactly this reason (see that record's own field
comments). A first draft of this phase described `Fact[T]` as just
`FactStatus` plus the `T` payload and let `Fact.failed(reason)` store its
`reason` there — review correctly caught that this cannot typecheck
(`reason` is a diagnostic string, not a value of type `T`) and would
either violate the declared generic type or silently drop the diagnostic
`FactAvailability`'s wire shape already preserves. With the third field:
`Fact.failed(reason)` is `Fact(status=FAILED, value=None,
diagnostics=(reason,))`; `Fact.unsupported()`/`Fact.not_applicable()`/
`Fact.not_collected()` take an optional `*diagnostics` the same way, for a
producer that wants to record *why* (e.g. which depth was requested)
without it becoming a smuggled value.

`FactStatus` has exactly six members (`PRESENT`, `PARTIAL`,
`NOT_COLLECTED`, `UNSUPPORTED`, `FAILED`, `NOT_APPLICABLE` — see that
module's own docstring) and deliberately has **no seventh "confirmed
absent" member**: per `PRESENT`'s own documented meaning ("the producer
ran, covered the requested scope, and established the facts — *including
establishing that a collection is legitimately empty*"), a confirmed
absence is `PRESENT` carrying an empty/`None` value, not a distinct
status. `Fact[T]`'s constructors are therefore `Fact.present(value)`
(value may legitimately be `None`/`[]` — that *is* confirmed absence),
`Fact.not_collected()`, `Fact.unsupported()`, `Fact.failed(reason)`,
`Fact.not_applicable()`, and `Fact.partial(value)`. There is no
`Fact.absent_confirmed()` — a draft of this plan proposed one and it was
corrected during review for contradicting the vocabulary it claims to
reuse unchanged; a caller wanting to assert absence calls
`Fact.present(None)` (or `Fact.present(())`/`Fact.present([])` for a
collection) explicitly, so the payload contract's only rule is that this
is the *one* legitimate way to spell "present, empty" — never a bare
sentinel construction readers could mistake for "not collected."
`Fact.value_or(default)` and `Fact.is_present` exist, but **`value_or` is
not a detector-safe way to read one** — a first draft of this phase
offered it as one of "the only two ways to read one without a full
`match`," which a reviewer correctly rejected: `old.vtable.value_or([])
!= new.vtable.value_or([])` collapses `NOT_COLLECTED`/`FAILED`/
`UNSUPPORTED` back into the same default as a confirmed empty value,
reintroducing by a different spelling the exact ambiguity this phase
exists to make unrepresentable. `value_or` is reserved for non-semantic
presentation code (a report renderer choosing a display fallback, where
collapsing "not collected" and "confirmed absent" to the same rendered
text is an acceptable UI simplification, not a detection decision);
*every* detector reads a `Fact[...]`-typed field only by inspecting
`.status`/pattern-matching the full `FactStatus` space. The
`check_ai_readiness.py` rule in this phase's acceptance criteria enforces
this distinction, not merely "was there a bare attribute access" —
`value_or` called from `diff_types.py`/`diff_layout.py` or any other
detector module is flagged exactly the same as a bare attribute read; the
rule's allowed callers are presentation modules only, not "anywhere
outside `model/fact.py`" as this phase's first draft stated it.
`Fact.__bool__` is explicitly **defined** to raise `TypeError("Fact[T]
has no truth value — read .is_present or .value_or(...)")` — plain
absence of `__bool__` leaves ordinary Python object truthiness in effect
(every `Fact[T]` instance would be truthy regardless of status), so the
no-implicit-truthiness invariant needs the raise, not silence.
**A second override was attempted for the same reason and reverted — a
first draft of this phase defined `Fact.__eq__`/`__ne__` to raise the
identical `TypeError`, and a later review round correctly rejected it.**
`Fact[T]` is itself a field on `RecordType`/`Function`/every other
fact-bearing dataclass, and a raising `__eq__` on a *field* poisons the
*containing* dataclass's own generated `__eq__` the instant comparison
reaches that field: two otherwise-identical `RecordType` instances (an
ordinary test assertion, a list/snapshot comparison, Phase 6's own
`CanonicalEntity` equality) would raise instead of comparing, which is a
far more disruptive failure than the narrow one this override was meant
to close. `Fact.__eq__` stays the plain dataclass-generated structural
comparison (`status`/`value`/`diagnostics` together) — correct for a
containing object's own equality, and exactly what `old.default !=
new.default` gets instead of a raise. The actual guard against that
specific misuse (comparing two `Fact[...]` values directly inside
*detector* logic, instead of unwrapping first) is the same mechanism
already enforcing the `.value_or()` rule: the `check_ai_readiness.py`
static AST check, widened to also flag a bare `Fact[...]`-typed field on
either side of `==`/`!=` inside a detector module — same file scope, same
enforcement layer, not a second runtime mechanism underneath the first.

**Scope for this phase (deliberately narrow).** Convert the fields
AGENTS.md's "Known gaps" names as actively causing fabricated
findings from absent evidence: `RecordType.vtable`/`vptr_offset_bits`
(the `type_vtable_changed` guard), `RecordType.bases` (the accepted-gap
`type_base_changed` entry — converting its *representation* first makes a
future evidence-based guard additive instead of another reinterpretation
of `None`), and `Param.is_va_list` (the reliability-flag entry) — plus
`RecordType.virtual_bases`, converted in this same Phase 0 PR alongside
`bases` rather than deferred (see the identical-producer/identical-
availability-conditions reasoning a few sections down), for five fields
total across four owning dataclasses. Every
other model field stays as-is in this phase — a blanket conversion is
Phase 5's job, after D7's registry exists to drive it mechanically.

**Where the `Fact[...]` value actually comes from (both directions —
fresh extraction and loading a legacy persisted snapshot).** This phase is
incomplete without both halves; a detector switched to read `Fact[...]`
with nothing populating it correctly would either suppress every existing
finding (if unpopulated defaults to `not_collected()`) or silently
recreate the exact ambiguity this phase exists to remove (if derived
naively from the existing raw value with no producer-aware distinction).
Concretely, this repository already has the mechanism this phase
generalizes, in the form of `AbiSnapshot`'s per-field, per-producer
reliability flags (`clang_vtable_facts_reliable`, `clang_va_list_facts_
reliable`, and their siblings for other fields not converted in this
phase) — each already encodes, in careful hand-written prose, exactly the
producer/schema-version distinction `Fact[...]` generalizes into a typed
value instead of a side boolean:
- **Fresh extraction** (`dumper_castxml.py`, `dumper_clang.py`/
  `dumper_clang_vtable.py`, `dwarf_snapshot.py`): each producer now
  constructs the field's value directly as a `Fact[...]` at parse time —
  `Fact.present(vtable_list)` when it actually reconstructed a vtable,
  `Fact.unsupported()` for a producer that has never populated this fact
  at all (castxml for `is_va_list`, per that field's own existing
  docstring), `Fact.not_collected()` when the run's evidence depth never
  reached that extractor. No snapshot-level reliability flag is needed for
  a freshly-built snapshot, since the per-field `Fact[...]` states it
  directly — this is the generalization's actual payoff, not an
  afterthought.

  **"Constructs the field's value directly at parse time" is not quite
  true for `vptr_offset_bits` on the DWARF backend, and a first draft of
  this phase missed the gap.** `dwarf_snapshot.py` runs a fixed-point
  resolution pass *after* every `RecordType` already exists
  (`rec.vptr_offset_bits = resolved`, at the sites resolving an inherited
  vptr offset through a virtual-primary-base fallback — confirmed by
  reading the real code, not assumed) — this has to run post-construction
  because it needs cross-references between already-built records a
  single object's own `__post_init__` cannot see. A `vptr_offset_bits_fact`
  constructed only inside `__post_init__`, at the point each `RecordType`
  is first built, would freeze at that record's *pre-resolution* state —
  typically `Fact.not_collected()`, since `vptr_offset_bits is None` is
  exactly the condition that put the record on this pass's own worklist —
  while the legacy `vptr_offset_bits` field goes on to hold the correctly
  resolved value a moment later. A migrated detector reading the `Fact`
  field would then see "not collected" for a record the legacy field
  (and, for a caller that didn't migrate, every existing behavior)
  correctly resolved — silently losing exactly the fact this conversion
  exists to make visible, for the one DWARF-specific case that resolves
  in two passes instead of one. Fixed by updating both representations at
  each of these fixed-point call sites, not by deferring `Fact`
  construction (which would mean every *other*, single-pass record
  waiting on a cross-record pass that in practice never touches it):
  `rec.vptr_offset_bits_fact = Fact.present(resolved)` alongside
  `rec.vptr_offset_bits = resolved` wherever this pass resolves a value,
  the same "both representations move together" discipline this phase's
  legacy-field-resync fix already establishes for the explicit-constructor
  direction, just applied to a producer-internal write instead of a
  caller-supplied one.
- **Loading a legacy, pre-`Fact[...]` persisted snapshot**
  (`serialization.py`): the existing reliability flag is read *once*, at
  load time, to reconstruct the correct `Fact[...]` value for that
  snapshot's schema version and producer — `clang_vtable_facts_reliable ==
  True` backfills `Fact.present(raw_vtable)`; `== False` backfills
  `Fact.not_collected()` (never `Fact.present([])` — the old field's
  "blanket empty" value on an unreliable snapshot is not a confirmed
  absence, exactly the "real but WRONG data" distinction that flag's own
  docstring already draws). **The reliability flags themselves become
  write-only after this phase, but are not deleted at Phase 10 — an
  earlier draft of this plan said they were, and that contradicts Phase
  8's own commitment.** ADR-062 Phase 1's v1-v25 import adapter is
  explicitly a *permanent* capability (`ProjectSnapshot` must always be
  able to import any snapshot version this project ever shipped, not only
  versions newer than some cutoff), and a pre-`Fact[...]` snapshot's
  reliability flags are the *only* evidence that lets the importer tell a
  trustworthy empty value apart from one that was never collected — once
  deleted, that information is gone from the input entirely, and no
  later code can reconstruct it. So `clang_vtable_facts_reliable`/
  `clang_va_list_facts_reliable` (and every sibling this pattern applies
  to) stay in `serialization.py`'s read path, and in the wire format, for
  exactly as long as a pre-`Fact[...]` schema version remains importable
  — which, per Phase 8's own commitment, is indefinitely. What *does*
  go away is the *domain*-side boolean field (`AbiSnapshot.clang_vtable_
  facts_reliable` as a live, queryable attribute on a freshly-built
  snapshot) — nothing in the current codebase reads it once every
  consumer reads `Fact[...]` instead, so the attribute itself is the one
  piece of this that is genuinely removable, not the wire-level decode
  logic that still has to run for a historical input.

  **`RecordType.bases` has no equivalent flag to read, and "every sibling
  this pattern applies to" above does not actually include it — a first
  draft of this phase implied it did, by backfilling `bases` the identical
  conditional way `vtable`/`is_va_list` backfill from their own reliability
  flags, and review correctly caught that `bases` has no such flag to
  condition on at all (confirmed by grep: no `*_bases_reliable`-shaped
  field exists anywhere in this codebase).** This is not an omission this
  phase introduces — AGENTS.md's own `type_base_changed` "Known gaps" entry
  already documents, at length, that no independent evidence signal exists
  for this field, and that the existing, live detector's accepted policy is
  to *always* treat a captured `bases` list as real ("the alternative —
  suppressing a real hierarchy change ... is strictly worse"). Backfilling
  a legacy snapshot's raw `bases` to `Fact.not_collected()` — the `False`-
  flag branch's behavior for `vtable`/`is_va_list` — would be a real,
  new regression here specifically: it would silently suppress every
  `type_base_changed` finding the existing, unconverted detector already
  produces today against every pre-`Fact[...]` snapshot, for a field whose
  status quo never suppressed on capture-gap grounds in the first place.
  Backfilling unconditionally to `Fact.present(raw_bases)` is therefore the
  *only* correct choice for this one field's legacy-loading path — not a
  weaker substitute for the flag-conditioned mechanism, but the literal
  zero-behavior-change preservation of what `bases` already does today,
  known limitation included. This is deliberately asymmetric with
  `vtable`/`is_va_list`'s own legacy-loading bullet above: those two
  fields have a real signal to condition on and use it; `bases` does not,
  and pretending otherwise by reusing the same conditional shape would
  fabricate a confidence neither the flag nor the field's own history
  supports. Closing the underlying gap — giving `bases` a real reliability
  signal, the way `vtable` already has one — is exactly the kind of
  cross-cutting data-model work AGENTS.md's own entry already named as a
  needed, not-yet-attempted follow-up; this phase converts the
  *representation*, which is what makes that follow-up additive instead of
  another reinterpretation of a raw `None`/empty value, and does not
  attempt the evidence-signal design itself.

**Writing a freshly-extracted snapshot back out needs its own fix, not
just a reader-side backfill.** `serialization.snapshot_to_dict()` calls
`asdict(snap)` on the *whole* `AbiSnapshot` — `dataclasses.asdict()`
recurses into every nested dataclass field, including a `Fact[...]`
instance, which it flattens into `{"status": FactStatus.PRESENT, "value":
..., "diagnostics": (...)}` with the `status` key holding the raw
`FactStatus` **enum member**, not a JSON-safe value — `json.dump()` raises
on an unrecognized type. This is not a new problem `Fact[...]` invents:
`snapshot_to_dict()` already has to do exactly this conversion for
`ElfMetadata`'s own enums today (its "Serialize ElfMetadata enums to
strings for JSON compatibility" post-`asdict()` pass, right below the
`asdict()` call itself) — this phase extends that same, already-
established pattern to every `Fact[...]`-typed field's `status`, writing
`status.value` (the plain string, e.g. `"present"`) instead of the enum
member, with the reverse conversion added to `serialization.
snapshot_from_dict()`'s per-field loaders. This is a genuine new key in
the serialized document for every converted field, so it is a real schema
change: `serialization.SCHEMA_VERSION` is bumped by one, following the
identical precedent each of the `clang_*_facts_reliable` flags already
used when it was introduced (schema v21 for vtable reliability, v23 for
`is_va_list`, ...) — this is not a new kind of schema decision, it is the
same kind this codebase already makes routinely for exactly this class of
field addition.

**Files.** `abicheck/model/availability.py` (new — the relocated
`FactStatus`/`Confidence`/order-tuple vocabulary); `abicheck/storage/
availability_status.py` (trimmed to a re-export shim); `abicheck/model/
fact.py` (new — `Fact[T]`); `abicheck/model/entities.py`'s `RecordType`
and `abicheck/model/declarations.py`'s `Param` dataclasses — **not
`model/snapshot.py`, which only imports both from their real owning
modules and defines neither; a first draft of this phase named the wrong
file, the same way Phase 5's Files section already correctly names the
two** (new `Fact[...]`-typed fields alongside the existing
ones, old field deprecated-but-present for one release to keep
`asdict`-based external consumers working). **The old field is not a
live `@property` deriving from the new one** — `dataclasses.asdict()`
only serializes declared dataclass fields, never properties, so making
the old field a property would silently *remove* it from every
`asdict`-based consumer's output instead of keeping it populated, the
opposite of this compatibility goal. Instead, every producer derives the
old field from the new `Fact[...]` value at the *same* construction call
— `vtable=fact.value_or([])` right next to `vtable_fact=fact` — so
there is exactly one write, not two independently-maintained ones that
could drift; the old field is never independently assigned raw producer
output again after this phase — with one named exception, below.

**"Removed in Phase 5's registry-driven sweep" names the wrong phase, and
a review round correctly found nothing anywhere actually does this
removal as stated — Phase 5's own Scope section converts a *different*,
disjoint set of fields (`RecordType.is_final`, `Function.
contract_attributes`, `Variable.alignment_bits`, and siblings Phase 0
left alone) and never touches `vtable`/`bases`/`vptr_offset_bits`/
`is_va_list` at all, and Phase 10's checklist has no row for this phase
either — only for the narrower domain-side `clang_*_facts_reliable`
boolean attributes.** Scheduled instead where it belongs: Phase 10's
checklist below gains its own row for this phase, removing the four
retained legacy attributes once the widened, repository-wide
legacy-attribute-read check this phase's own Acceptance criteria adds
(see below) reports zero remaining readers outside the compatibility
bridge's own `__post_init__` and serialization — the same "accounting
pass, not new design" bar every other Phase 10 row already uses, and the
same one-release retention window this phase's own "kept... to keep
`asdict`-based external consumers working" commitment already implies
rather than leaving open-ended. `dumper_castxml.py`/
`dumper_clang.py`/`dumper_clang_vtable.py`/`dwarf_snapshot.py` (each
producer constructs the `Fact[...]` value directly, per the design above);
`dumper_layout_backfill.py`'s `_backfilled_record()` — a *post-parse*
path, not a producer construction call, that `dataclasses.replace()`s an
already-built `RecordType` to overwrite `vtable`/`vptr_offset_bits` with
corroborating DWARF evidence after either header parser has already run.
The single-write rule above does not hold across this call: it must build
a new `Fact[...]` from the DWARF value first and derive the replaced
legacy fields from that `Fact`, the same order every producer uses, or the
backfilled `RecordType` ends up with a legacy field holding the
DWARF-corroborated value while its `Fact[...]` field still holds the
header parser's pre-backfill one — exactly the split-source drift this
phase exists to prevent, reached through a second call site rather than a
different producer.

**A second named exception: `RecordType`/`Param` are public API
dataclasses — AGENTS.md's own convention on this file's public types
means an external Python-API caller can construct one directly
(`RecordType(..., bases=["Base"])`, `Param(..., is_va_list=True)`),
bypassing every producer call site named above entirely, and a first
draft of this phase didn't account for that.** If the new `Fact[...]`
sibling field defaulted to `Fact.not_collected()` the way an ordinary
dataclass field default would, a direct caller supplying only the legacy
field gets a migrated detector reading "not collected" for a value the
caller explicitly gave it — silently discarding caller-supplied data this
phase must not break. Making the sibling field required instead breaks
every existing direct-construction call site outright, which is the
opposite failure. The fix: the new field's real default is `None` (not
`Fact.not_collected()` — a caller can still explicitly pass
`Fact.not_collected()` and have that honored, since `None` is reserved
purely as "nothing supplied," distinct from an explicit not-collected
claim), and `__post_init__` backfills it from the legacy field when still
`None` — `vtable_fact = Fact.present(self.vtable) if self.vtable_fact is
None else self.vtable_fact`, mirroring the shape `Param`'s own
`__post_init__`-based validation already uses elsewhere in this codebase
for exactly this kind of defaulting. **Precedence is explicit and
one-directional for which value wins, but — a later review round caught
this too — "wins" has to mean the legacy field is resynchronized from it,
not merely that the `Fact[...]` field ends up correct while the legacy
field is left stale.** `RecordType(vtable=["old"], vtable_fact=Fact.
present(["new"]))` is a real, constructible case (not a hypothetical): if
`__post_init__` only ever reads the legacy field to backfill the `Fact`
and never writes the legacy field back, a migrated detector reading
`vtable_fact` sees `["new"]` while `dataclasses.asdict()` and every
existing, unmigrated Python consumer reading `rec.vtable` directly still
sees `["old"]` — two disagreeing representations on the same object,
which is the exact defect this whole phase exists to eliminate,
reintroduced by the one compatibility path meant to prevent it. The
actual rule, corrected: whichever value is authoritative for a given
construction — the explicit `Fact[...]` when supplied, the backfilled one
derived from the legacy field otherwise — is written to **both** fields
before `__post_init__` returns, the same single-source-of-truth guarantee
every producer's own construction call already gives for free (Design
section, above) now given to a direct caller combining both forms too.
`RecordType(vtable=["old"], vtable_fact=Fact.present(["new"]))` ends
construction with `self.vtable == ["new"]`, not `["old"]` — the explicit
`Fact[...]` value also overwrites the legacy field, not only the other
way around.

**This bridge as just described is still wrong for the common case, and a
later review round caught it: `RecordType.bases`/`vtable` already default
to `[]` and `Param.is_va_list` already defaults to `False` — identical to
an explicitly-supplied confirmed-empty value.** `vtable_fact = Fact.
present(self.vtable) if self.vtable_fact is None else self.vtable_fact`
cannot tell "caller explicitly wrote `bases=[]`" apart from "caller wrote
`RecordType(...)` and never touched `bases` at all" — both leave
`self.bases == []` by the time `__post_init__` runs, and both would
backfill to `Fact.present([])`, falsely claiming "collected, confirmed
empty" for the ordinary case of a caller (most of this codebase's own
existing test fixtures, for a start) that never asserted anything about
the field at all. That is the identical unavailable-vs-empty collapse
Phase 0 exists to eliminate, reintroduced through the one compatibility
path meant to *preserve* callers, not create a new instance of the bug
for them. The fix needs an omission sentinel, not a truthiness check, and
the two field shapes (`list`-typed, `bool`-typed) need two different
mechanisms — **a first draft of this fix proposed one uniform mechanism
for both, and it is unimplementable for the boolean field**: Python has
exactly two `bool` instances, `True` and `False`; there is no third,
distinct `bool`-typed object a sentinel could be, so `self.is_va_list is
_OMITTED_IS_VA_LIST` can never be true for *any* caller-supplied value —
whichever of `True`/`False` the sentinel is defined to equal, that
identity check collides with a caller legitimately passing the same
value. **For `RecordType.bases`/`vtable` (`list`-typed), the mechanism
this section previously described cannot actually be implemented as a
direct field default — a dataclass field may not take a mutable object (a
`list`, `dict`, or `set` instance) as its own direct default at all;
Python's `dataclasses` module raises `ValueError: mutable default <class
'list'> for field ... is not allowed` the moment the class body executes,
before any instance is ever constructed.** A singleton list used as
`bases`' own direct default is exactly such an object, so `bases:
list[str] = _OMITTED_BASES` never reaches `__post_init__` at all — the
class itself fails to define.

**Two mechanisms were tried and rejected for both field shapes before
landing on the one that actually works, and the reasoning for rejecting
each is worth keeping, since a later round otherwise re-proposes one of
them.** (1) A bare `field(default_factory=...)` look-alike for the list
case does not repair the identity check: a `default_factory` runs fresh on
every omitted construction, so each omitted instance gets its *own*,
distinct empty-list object — never the one singleton
`self.bases is _OMITTED_BASES` needs to match. (2) Widening the declared
type to `bool | None`/`list[str] | None` (an earlier revision of this
section) makes the field constructible, but a later review round
correctly flagged it as a real breaking change for this bridge's own
stated purpose — "every reader still sees a plain `bool`/`list[str]`"
is true only *after* `__post_init__` runs, and AGENTS.md is explicit that
"changing \[a public dataclass's] public surface is a breaking change to
the Python API — coordinate it": a type-checked external caller reading
`Param.is_va_list`/`RecordType.bases` now has to handle `None` at the
static-type level for a value that can never actually be `None` at
runtime, which is exactly the kind of "the representation disagrees with
itself" defect the Governing Invariant singles out, just relocated from
the dataclass body to its type annotation.

**The mechanism that actually satisfies every constraint at once —
dataclass-constructible, identity-checkable, and *never widens the
declared field type* — wraps the existing private-sentinel idea in
`typing.cast()` at the point the sentinel is built, not at the point it is
compared.** A module-level singleton of a dedicated, non-`bool`/non-`list`
marker class (`_Omitted`, one instance) is constructed once and then
`cast()` to the field's own real type — `_OMITTED_IS_VA_LIST: bool =
cast(bool, _Omitted())`, `_OMITTED_BASES: list[str] = cast("list[str]",
_Omitted())` — which tells a type checker the sentinel *is* a `bool`/
`list[str]` (so the field's own declared type needs no union, no `None`,
no widening of any kind) while its actual runtime identity is a distinct,
non-`bool`/non-`list` object no caller-supplied value can ever equal by
identity. For the list-typed fields specifically, the mutable-default
`ValueError` is avoided the same way `field(default_factory=list)` already
avoids it today, just returning the *existing* singleton instead of a
fresh list each time — `field(default_factory=lambda: _OMITTED_BASES)` —
which is a legal `default_factory` (dataclasses only forbids a *direct*
mutable-typed default, not what a factory function returns) and, unlike
mechanism (1) above, returns the identical object on every omitted
construction, since the factory itself holds no state and always returns
the same module-level reference. `__post_init__` checks `self.bases is
_OMITTED_BASES`/`self.is_va_list is _OMITTED_IS_VA_LIST` (a `# type:
ignore[comparison-overlap]` is expected and correct here, since the
comparison is exactly the one case `cast()` told the type checker could
never be true — confirmed against this repository's own `mypy --strict`,
which accepts the construction with zero errors and reports the field's
type as exactly `bool`/`list[str]`, never a union), backfills
`Fact.not_collected()` for the true-omission case and `Fact.present(...)`
for an explicit value (including an explicit `[]`/`False`, still
distinguishable from omission since it is not the sentinel by identity),
then normalizes the field to an ordinary `False`/`[]` before returning —
so after construction every reader, `asdict()`-based or otherwise, sees
exactly the type the field has always declared, with no accepted
trade-off left to state. Verified directly (not merely reasoned about,
given how many rounds this exact design question has already gone
through): a minimal repro of this construction passes `mypy --strict`
with zero errors, reports `dataclasses.fields(...)`'s `type` as the
unwidened annotation, and round-trips correctly through `dataclasses.
asdict()` for both the omitted and explicit-value cases, including the
explicit-empty-list case staying distinct from omission.

**This choice has a real, named consequence for every *existing*
direct-construction call site in this codebase's own test suite, and a
later review round asked this plan to actually own that consequence
rather than only justify the choice that causes it.** Once a migrated
detector reads `.status` and skips rather than reports when it sees
`Fact.not_collected()` (the whole point of the migration), a pre-existing
test fixture built as `RecordType(name="Foo")` with no `bases`/
`bases_fact` — because that field didn't exist before this phase, not
because the test intended "unknown evidence" — now feeds that detector a
`not_collected` signal it never meant to assert, and a test that expects
a `TYPE_BASE_CHANGED`/`TYPE_VTABLE_CHANGED`/`PARAM_BECAME_VA_LIST`/
`PARAM_LOST_VA_LIST`-family finding from such a fixture can start failing
the moment its detector migrates — not the moment this phase lands the
sentinel itself, which is a no-op until a detector actually reads
`.status`. That ordering is what keeps this self-auditing *within* this
codebase: each detector's own migration PR (already its own Files/Tests
entry elsewhere in this plan) runs the existing suite and any fixture
whose real intent was "confirmed empty/non-variadic" surfaces as a loud,
specific test failure at that PR, which is fixed by making the fixture
say what it actually means — `RecordType(name="Foo", bases_fact=Fact.
present([]))` — not by reverting the detector's new, correct behavior.
Each detector-migration task in this plan's own Files sections gains this
as an explicit sub-task: audit and update every fixture the newly-migrated
detector's tests touch, in the same PR, rather than leaving a fixed
fixture as an unplanned follow-up discovered by a later, unrelated CI run.
**This is not self-auditing for an external Python-API caller who builds
an `AbiSnapshot` by direct construction outside this repo's own test
suite**, though — that caller has no test of this codebase's own to fail,
and their comparison now silently reads "no evidence" where it used to
read "confirmed empty," a genuine behavioral change with nothing to force
them to notice it. That is disclosed, not silently shipped: this phase's
changelog fragment (this repo's own `scriv create` convention, already
required for any change touching `abicheck/**/*.py`) states the behavior
change explicitly — direct construction of `RecordType`/`Param` without
the new sibling `Fact` field now represents unrecorded evidence, not a
confirmed empty/non-variadic value, to any `Fact`-aware consumer; a caller
that wants the old, confirmed-empty semantics passes the sibling field
explicitly (`bases_fact=Fact.present([])`) — which is exactly the
compatibility bridge already documented above, just invoked deliberately
instead of relied on implicitly.

**A third field shape needs a third mechanism, and a later review round
found it missing: `RecordType.vptr_offset_bits` is also converted by
this phase (named in the Scope section above) and is `int | None`,
already defaulting to `None` today — where `None` is already a real,
meaningful value ("no vptr observed"), not an unused slot the way `bool`'s
two values are both already spoken for.** Unlike `is_va_list`, `None`
cannot double as this field's omission marker — `RecordType()` (omitted)
and `RecordType(vptr_offset_bits=None)` (explicit: confirmed no vptr) must
backfill differently (`Fact.not_collected()` vs. `Fact.present(None)`),
but both already leave `self.vptr_offset_bits is None` with no way to
tell them apart, the identical ambiguity the `bases`/`is_va_list` fixes
above each close for their own field. `int` does not have `bool`'s
two-instance problem, though — it has the opposite problem from `list`
this field shares the fix with: a fresh private sentinel *object* (not a
literal value, and not reusing `None`) works exactly like the `list` case,
since nothing short of that exact object will ever compare identical to
it. The field's *actual* dataclass default becomes the identical
`cast()`-sentinel construction `bases`/`is_va_list` already use above —
`_OMITTED_VPTR_OFFSET_BITS = cast("int | None", _Omitted())`, never
exported — rather than the literal `None`. **This field's declared type
does not widen at all, because it has nothing to widen to** — `int | None`
was already its type before this phase, for the field's own, legitimate,
pre-existing reason (a real "no vptr observed" value), not introduced by
this mechanism the way a bare `bool`/`list[str]` field would otherwise
have needed one; `cast("int | None", ...)` merely tells the type checker
the sentinel already belongs to the union that was always there.
`__post_init__` checks `self.vptr_offset_bits is
_OMITTED_VPTR_OFFSET_BITS` (identity) to tell omission from an explicit,
confirmed-`None`, backfills `Fact.not_collected()` only for the
true-omission case and `Fact.present(self.vptr_offset_bits)` (`None`
included) for an explicit value, then normalizes the field to a real
`int | None` (`None` if it was the sentinel) before `__post_init__`
returns — the same post-condition the other two fields reach, by the
identical "a genuinely distinct object, made to type-check as the field's
own real type via `cast()`, can't collide with anything a caller passes"
mechanism, just applied to a field whose natural resting value happens to
coincide with Python's only singleton `None` the way `bool`'s natural
resting values coincide with its only two.

All three mechanisms end at the
identical post-condition (the legacy field is a plain, fully-populated
value after `__post_init__`, the sentinel never leaks to a reader, and the
field's own declared type never widens) — they differ only in how the
sentinel is shaped to type-check as the field's own real type: `cast()`
to the field's already-`Optional` type for `vptr_offset_bits` (nothing to
widen, the union predates this phase), and `cast()` to the field's
otherwise-unwidened `bool`/`list[str]` type for `is_va_list`/`bases`/
`vtable`, with the list-typed fields additionally routed through a
`default_factory` that returns the one singleton rather than a fresh
object, since a direct mutable-typed default is rejected outright by
`dataclasses` regardless of what mechanism the value itself uses.

Continuing the Files list: `serialization.py`
(`snapshot_to_dict()`'s `Fact[...]`-status-to-string encoding, extending
its existing ElfMetadata-enum-encoding pattern; `snapshot_from_dict()`'s
matching decode; `SCHEMA_VERSION` bump; and the legacy-schema backfill
path, reading the existing reliability flags exactly once on load);
`diff_layout.py`/`diff_types.py`'s vtable/base-list detectors, **and every
other semantic reader of the three converted fields, not only the two
primary detectors, and not only the ones living under `diff_*.py`** —
`diff_param_qualifiers._diff_param_va_list`
(`p_old.is_va_list`/`p_new.is_va_list`), `diff_vtable_layout.
_is_polymorphic` (`rec.vtable`/`rec.virtual_bases`), and `diff_cxx_rules`'s
base-walk helpers (`start.bases`/`rec.bases`) all read the raw field
directly today and were missing from a first draft of this file list —
each retains the exact unavailable-vs-empty ambiguity this phase exists
to close until it is migrated too.

**`internal_leak.py::_enqueue_record_children()` is a fourth such reader,
and a first draft of this paragraph's own closing sentence — "the AI-
readiness gate... checks every module under `diff_*.py`" — is exactly
why it was missed: `internal_leak.py` is not a `diff_*.py` module at
all, so a gate scoped to that glob would silently never see it.**
`_enqueue_record_children()` walks `rec.bases` (and, on the following
line, `rec.virtual_bases`) directly to decide whether
an internal type is reachable from a public one through inheritance, for
`INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`; reading the unconverted raw fields
here has the identical failure mode every other unmigrated reader has —
if `bases_fact` is `Fact.not_collected()` but the legacy field was
normalized to `[]` for backward compatibility, this walk sees no bases at
all, can miss the one public-inheritance path that makes the leak real,
and silently suppresses or demotes the finding. Fixed two ways, not one:
`internal_leak.py` is added to this phase's migration file list alongside
the `diff_*.py` modules above, and the AI-readiness gate's module scope
widens from "every module under `diff_*.py`" to an explicit allowlist of
every module this phase identifies as a semantic reader of the three
converted fields — a glob that happens to match today's known readers is
not the same invariant as "every known reader is checked," and the next
non-`diff_*.py` reader this plan's own drafting process misses should fail
the gate, not silently bypass it the way this one did.

**A further repo-wide check found the allowlist-by-hand-enumeration
itself is exactly the failure mode the previous paragraph just diagnosed,
reproduced across two more passes — and a review round correctly found
the prose narrating each pass separately had drifted into inconsistent
counts ("four more"/"five more" mixing module counts with call-site
counts) and inconsistent per-reader field lists (an earlier mention of
`internal_leak.py`/`surface_graph.py` named only `bases`, while both
actually read `virtual_bases` too, just on a separate line rather than
in one combined expression) and miscategorized two genuinely `diff_*.py`
modules as "outside" that scope. Replaced with one explicit table,
checked against the real source directly rather than re-described a
third time in prose:**

| Reader | Function | Fields read | In `diff_*.py`? |
|---|---|---|---|
| `contract_evidence_collect.py` | `build_type_graph()` | `bases`, `virtual_bases` | no |
| `diff_time64.py` | `_fold_record_tokens()` | `bases`, `virtual_bases` | **yes** |
| `diff_stdlib_impl.py` | `_public_by_value_type_closure()` | `bases`, `virtual_bases` | **yes** |
| `surface_graph.py` | `_build_type_refs()` | `bases`, `virtual_bases` (separate lines) | no |
| `internal_leak.py` | `_enqueue_record_children()` | `bases`, `virtual_bases` (separate lines) | no |
| `export_surface.py` | line 1167's unresolved-type scan | `bases`, `virtual_bases` | no |
| `surface.py` | two public-closure walks (lines 632, 782) | `bases`, `virtual_bases` | no |
| `type_reachability.py` | stdlib-reference text scan (lines 873-874) | `bases`, `virtual_bases` | no |
| `dumper_scoping.py` | dependency-retention text scan (lines 342-343) | `bases`, `virtual_bases` | no |

Nine distinct modules, ten call sites (`surface.py` contributes two).
`diff_time64.py`/`diff_stdlib_impl.py` *are* `diff_*.py` modules — the
original AI-readiness gate (scoped to that glob) already covers their
call sites; they needed no new gate-scope widening, only the same
Fact-aware migration every other row gets. The seven remaining rows are
outside `diff_*.py` and are exactly why the gate itself stops being a
glob at all (below). Every row's consequence follows one of two shapes:
a missing inheritance edge can make `export_surface.py`'s `exclusion_
is_provable` gate wrongly treat a type as out-of-contract instead of
failing closed (`contract_evidence_collect.py`/`export_surface.py`), or
it can silently suppress/omit a derived finding or shrink a resolved
surface the same way the vtable/base-list detectors already can
(every other row, including `surface.py`'s own reachability closure
feeding `compute_public_surface()` directly). All nine modules are added
to this phase's migration file list, and — since a fifth, then a tenth,
hand-missed call site is now a demonstrated, repeating pattern, not a
one-off — the
AI-readiness check itself stops being a hand-maintained module allowlist:
it becomes a real static scan for any direct attribute access naming
`bases`/`vtable`/`is_va_list`/`virtual_bases` (the fourth field sharing
this exact ambiguity, scheduled for conversion in this same phase below
— not itself one of the three fields the Design section above already
covers) on a value whose declared type resolves to `RecordType`/`Param`,
repository-wide, with all nine named modules above
becoming the check's own initial known-failures baseline (mirroring
`check_ai_readiness.py`'s existing `MYPY_ERROR_BASELINE`/
`LARGE_FILE_ALLOWLIST` pattern: a reviewed, shrinking allowlist of
*known* violations, not a permanent exemption list a new violation can
quietly join). **`RecordType.virtual_bases` was left out of this phase's
Design section with no concrete phase scheduled to pick it up, and a
review round correctly rejected that as a dead end: Phase 5's own
eligibility mechanism is keyed to an existing backend-reliability flag,
and this field has none, so leaving it for Phase 5 would mean it never
gets picked up by anything.** Checking the real producers settles where
it actually belongs: `dumper_clang.py`'s `_parse_bases()` and
`dwarf_snapshot.py`'s base-classification walk both populate `bases`/
`virtual_bases` from the *same* call, in the same pass, under the same
availability conditions — there is no separate collection step and no
separate reliability signal for one versus the other, so treating them as
two different phases' work would be artificial. `virtual_bases` is
therefore converted in this same Phase 0 PR, using the identical
sentinel-based `Fact[list[str]]` mechanism already fully specified for
`bases` above (same list-typed omission-sentinel construction, same
`__post_init__` bridge, same legacy-schema backfill reading whatever
signal `bases_fact` itself backfills from, since the two are produced
together) — not a new mechanism, the same one with a second field name.
Every one of the nine readers named above reads `bases`/
`virtual_bases` together in one loop body (`contract_evidence_collect.py`,
`diff_time64.py`, `diff_stdlib_impl.py`, `diff_cxx_rules`'s base-walk
helpers, `internal_leak.py`, `export_surface.py`, both `surface.py`
walks, `type_reachability.py`, `dumper_scoping.py`) and migrates both
fields in the same pass, not
`bases` now and `virtual_bases` in a still-later phase — the provenance
propagation this finding asked for is exactly "every semantic reader of
`virtual_bases` is already on this phase's own migration list for
`bases`," not a second, separate reader audit.

**Tests.** A direct test on `Fact[T]`'s actual comparison contract, added
before any detector migration depends on it: `if fact:` raises
`TypeError`; `fact_a == fact_b`/`fact_a != fact_b` do **not** raise —
they perform the plain structural comparison (`status`/`value`/
`diagnostics`), the same as any other dataclass — pinning the reverted
design directly (confirmed to fail against a version of `Fact` that
defines a raising `__eq__`/`__ne__`, which breaks this test as well as
every containing `RecordType`/`Function` comparison). A second test
confirms the containing-object guarantee this reversion exists to
protect: two separately-constructed but field-identical `RecordType`
instances (including equal `Fact[...]` sibling fields) compare equal via
the dataclass-generated `RecordType.__eq__`, without raising — confirmed
to fail against the raising design, which is the actual counterexample
that caused the reversion. A third test covers the real enforcement
point instead: a static AST check flags `fact_a == fact_b`/`fact_a !=
fact_b` (or against a literal) written directly inside a `diff_*.py`
detector module, the same mechanism and file scope as the existing
`.value_or()` rule.
Port the existing `tests/test_vtable_evidence_guard.py`
Hypothesis properties to assert over `Fact[...]` states directly, not only
derived booleans; add a property asserting no detector in `diff_types.py`/
`diff_layout.py` pattern-matches a `Fact[...]`-typed field without handling
every `FactStatus` variant (a static AST check, mirroring
`check_ai_readiness.py`'s own style, is preferable to a runtime check here
since the failure mode is a missing `case`, not a bad value). Add a direct
`serialization.py` round-trip test per converted field pinning the
backfill rule itself: a pre-conversion fixture snapshot with the
reliability flag `True` loads as `Fact.present(...)`, one with the flag
`False` loads as `Fact.not_collected()` — **not** `Fact.present([])`/
`Fact.present(False)` — since that exact confusion (a placeholder value
read as a confirmed fact) is the bug this phase exists to make
unrepresentable; a freshly-extracted snapshot round-trips through every
backend's real parser and never consults the legacy flag at all. Add a
second, direct test asserting `snapshot_to_dict()` on a freshly-built
snapshot never emits a raw `FactStatus` enum member anywhere in the
resulting `dict` (walk the tree and assert every value is a JSON-primitive
type) — confirmed to fail against the pre-fix `asdict()`-only path, which
is exactly the failure mode a reviewer caught in this design. A third test
covers the direct-construction compatibility bridge: `RecordType(...,
vtable=["f"])` with no `vtable_fact` given backfills to
`Fact.present(["f"])`; `RecordType(..., vtable=["f"],
vtable_fact=Fact.not_collected())` keeps the explicit `Fact` value rather
than the backfilled one, pinning the stated precedence directly; and a
bare `Param(is_va_list=True)` with no `is_va_list_fact` given backfills
the same way — each confirmed to fail against a version of the dataclass
that defaults the new field to `Fact.not_collected()` instead of `None`.
A fourth test pins the omission-marker fix directly, the exact
counterexample that caught the gap in the first design, across both
mechanisms: `RecordType()`
(nothing touched — the common case, not the nonempty/`True` cases the
third test above already covers) backfills `vtable_fact`/`bases_fact` to
`Fact.not_collected()`, never `Fact.present([])`; `RecordType(bases=[])`
(an explicit, confirmed-empty base list) backfills to `Fact.present([])`,
distinct from the previous case despite both leaving `self.bases == []`;
and a bare `Param()` backfills `is_va_list_fact` to `Fact.not_collected()`,
never `Fact.present(False)`, while `Param(is_va_list=False)` (an explicit,
confirmed-not-variadic value) backfills to `Fact.present(False)` — each
confirmed to fail against the truthiness-based (non-sentinel, non-`None`)
version of the bridge, which cannot tell the two cases apart for either
field shape. A fifth test pins the type itself, both statically and at
runtime: `Param.is_va_list`'s/`RecordType.bases`'s declared annotation
stays exactly `bool`/`list[str]` — no union, confirmed by running `mypy
--strict` against a fixture module using the compatibility bridge and
asserting zero errors, which also confirms `dataclasses.fields(...)`'s
recorded `type` is unwidened — and after construction (with or without
the argument) `self.is_va_list`/`self.bases` is always a plain `bool`/
`list[str]`, never the sentinel, confirming the cast sentinel normalizes
away before any reader, including `asdict()`-based serialization, can
observe it. A
sixth test pins `vptr_offset_bits`'s own third mechanism, the exact
counterexample that caught this field shape missing entirely: a bare
`RecordType()` backfills `vptr_offset_bits_fact` to `Fact.not_collected()`,
while `RecordType(vptr_offset_bits=None)` (an explicit, confirmed-no-vptr
value) backfills to `Fact.present(None)` — distinct from the previous
case despite both leaving `self.vptr_offset_bits is None` — confirmed to
fail against a version of the bridge that reuses `None` itself as both
the field's natural value and its omission marker. A seventh test pins
the legacy-field-resync fix: `RecordType(vtable=["old"],
vtable_fact=Fact.present(["new"]))` ends construction with `self.vtable ==
["new"]`, not `["old"]` — confirmed to fail against a version of the
bridge that lets the explicit `Fact[...]` value win for `vtable_fact`
itself while leaving `self.vtable` unsynchronized, which is the exact
two-disagreeing-representations counterexample this fix closes. An
eighth test pins the DWARF fixed-point-resolution fix directly, through
the real `dwarf_snapshot.py` resolution pass rather than a hand-built
`RecordType`: a base class with a real vtable and a derived class
inheriting its vptr through the virtual-primary-base fallback (the exact
shape that pass exists to resolve), asserting the derived record's
`vptr_offset_bits_fact` reads `Fact.present(resolved)` — not
`Fact.not_collected()` — after the pass runs, matching its own
now-resolved `vptr_offset_bits` value; confirmed to fail against a
version of the fix that updates only the legacy field at the resolution
site and leaves the `Fact` field frozen at its pre-resolution,
construction-time state.

**Acceptance criteria.** The three converted fields cannot be read by any
detector without explicit availability handling — enforced by a new
`check_ai_readiness.py` check flagging, inside `diff_*.py`/any detector
module — **`idioms.py` named explicitly here, since a first draft of this
criterion scoped itself to `diff_*.py` and missed it: it is semantic
detector logic reached by `pattern_verdicts.py` (ADR-027 D2.2's single-
snapshot anti-pattern recognition), reading `rec.vtable`/`rec.bases`
directly to emit polymorphic-type anti-pattern findings, not a
presentation or bridge module — the check's module-scope list is every
module producing a `Change`/finding from model data, not only the ones
named `diff_*.py`** — either a bare attribute read of a `Fact[...]`-typed field *or* a
`.value_or(...)` call on one (both collapse the status space the same
way); `.status`/pattern-match access is the only permitted form there.
`.value_or(...)` itself is not banned repository-wide — it stays legal in
presentation-only modules (`reporter.py`/`html_report.py`/`sarif.py` and
siblings), which is a real, narrower allowlist, not "anywhere outside
`model/fact.py`."

**The check as stated above cannot actually close this gap by itself — it
recognizes only reads of the *new*, `Fact[...]`-typed field names
(`vtable_fact`/`bases_fact`/`vptr_offset_bits_fact`/`is_va_list_fact`),
never a detector that keeps reading the *retained legacy* attribute
(`rec.vtable`, `rec.bases`, `rec.vptr_offset_bits`, `param.is_va_list`)
directly.** This phase's own compatibility bridge keeps those legacy
fields populated and normalized specifically so existing, unmigrated
callers keep working — but that same retention means a detector can
continue reading `rec.vtable` (unchanged, still a plain `list[str]`, still
passes every existing type check) and never touch the `Fact[...]` field or
the new check at all, bypassing availability handling entirely while
looking, to both a type checker and this AST check, exactly like a
correctly-migrated detector.

**Two different checks are in play here, and a first draft of this
criterion conflated their scopes — narrowing the legacy-attribute
widening to "only inside a detector module" silently abandoned the
repository-wide scope the reader-migration check above (lines 668–682)
already commits to, which is exactly the contradiction review caught:
`surface.py`/`export_surface.py`/`dumper_scoping.py`/`contract_evidence_
collect.py`/`internal_leak.py`/`type_reachability.py` are real,
documented semantic readers of `bases`/`vtable`/`virtual_bases`/
`is_va_list` — the whole reason they're on this phase's own migration
list and initial known-failures baseline — but none of them is a
`diff_*.py`-shaped detector module, so narrowing enforcement to
"detector module" would let any of them reintroduce a direct legacy-field
read after this phase ships with nothing catching it, recreating the
exact unavailable-vs-empty confusion the migration exists to close.**
The legacy-attribute-name widening (`rec.vtable`/`rec.bases`/
`rec.virtual_bases`/`rec.vptr_offset_bits`/`param.is_va_list`, on a value
whose declared type
resolves to `RecordType`/`Param`) therefore stays the *repository-wide*
scan already specified above — covering every one of the nine-plus named
semantic-reader modules, not only detector modules — with the
compatibility bridge's own `__post_init__` (which legitimately reads and
writes the legacy field to backfill/resync it) as the one named exemption,
and serialization/`asdict()`-based external consumers likewise exempt
since they read the dataclass generically, not by naming the field.
Only the *other* half of the check — a bare `Fact[...]`-typed field read
or a `.value_or(...)` call on one — keeps the narrower, detector-module-
only file-scope restriction, because that half's exemption (presentation
modules legitimately calling `.value_or(...)` to render a display string)
has no equivalent for a direct legacy-field read: nothing about rendering
a value for display justifies bypassing the availability-aware field
entirely when the legacy attribute is sitting right there with the same
information, unconverted. Full test suite green; FP-rate/
tier-accuracy gates unchanged (this phase changes representation, not
detector logic).

**Landed (first slice), not the whole phase — read this before assuming
the Design section above is fully implemented.** `abicheck/model/
availability.py` (relocated `FactStatus`/`Confidence`), `abicheck/model/
fact.py` (`Fact[T]`, the `cast()`-sentinel omission mechanism for all
three field shapes), `RecordType.bases_fact`/`virtual_bases_fact`/
`vtable_fact`/`vptr_offset_bits_fact`, and `Param.is_va_list_fact` are
real and tested (`tests/test_model_fact.py`,
`tests/test_serialization_roundtrip.py::TestFactFieldRoundTrip`).
`serialization.py` encodes/decodes the new fields and bumps
`SCHEMA_VERSION` to 26, backfilling a legacy snapshot correctly from the
existing `clang_vtable_facts_reliable`/`clang_va_list_facts_reliable`
flags (split into `storage/fact_codec.py`/`storage/enum_codec.py` — both
ADR-061 `storage`-layer leaf modules, depending on nothing beyond `model` —
to stay under the 2000-line file-size cap).

**Producer-side `Fact[...]` construction (second slice) — landed.**
`dumper_castxml.py`, `dumper_clang.py`, and `dwarf_snapshot.py` now each
construct `RecordType.bases_fact`/`virtual_bases_fact`/`vtable_fact`/
`vptr_offset_bits_fact` explicitly at parse time via a new shared helper,
`model.entities.record_layout_facts()` (spread into the `RecordType(...)`
call alongside the matching legacy keyword arguments) — every one of these
three producers genuinely resolves bases/virtual_bases/vtable/
vptr_offset_bits itself (real semantic analysis for castxml, an AST walk
for direct-clang, DWARF DIE traversal for the binary backend), opaque
records included, so this is a pure "state what was already true
explicitly" change with zero observable behavior difference on its own —
the omission bridge already derived the identical `Fact.present(...)` from
the always-supplied legacy value. DWARF's `vptr_offset_bits` is the one
field that may still be `None` pending `_finalize_vptr_offsets`'s own
later fixed-point pass; that pass already resyncs both representations via
`resolve_vptr_offset_bits()` from the prior slice, untouched here.

The one *real*, deliberate behavior change: `Param.is_va_list_fact` is now
`Fact.unsupported()` on both `dumper_castxml.py` and `dwarf_snapshot.py` —
neither producer can ever determine va_list-ness, on any run, which
`UNSUPPORTED` states plainly where the omission bridge's own
`NOT_COLLECTED` ("not collected this time") could not. `dumper_clang.py`'s
`is_va_list_fact` is `Fact.partial(...)`, not `Fact.present(...)` (Codex
review, second round): that backend genuinely evaluates
`_clang_param_is_va_list()` per parameter, but the check only covers
x86-64 System V and conservatively answers `False` — not a confirmed
negative — on any other target (the function's own documented
target-scoping residual — no per-snapshot record of which target ABI a
clang parse actually ran against — is unchanged; closing it still needs
the toolchain-identity probe named elsewhere in this repo's AGENTS.md).
`vptr_offset_bits_fact` is `Fact.partial(...)` on castxml/clang too (a
third finding, same review): both backends derive it from the same
`0`-if-polymorphic Itanium primary-base heuristic that the legacy
`vptr_offset_bits` field's own capability-matrix row already documents as
`PARTIAL` — it does not track a secondary vtable's placement under
multiple inheritance, so the `Fact[...]` sibling must not overclaim `FULL`
either. `scripts/backend_capabilities.py`'s `FACT_ROWS` (and its
`_is_placeholder_literal` scanner, taught to recognize a
`Fact.unsupported()`/`not_collected()`/`not_applicable()`/`failed()` call
as a placeholder rather than extraction, and to recurse into
`Fact.present()`/`partial()`'s own wrapped argument) were corrected to
match. New tests: `tests/test_castxml_fact_construction.py`,
`tests/test_dumper_clang_fact_construction.py`,
`tests/test_dwarf_fact_construction.py` — the `is_va_list_fact` change is
confirmed to fail against the pre-change code (`git stash`); the
representational-only assertions pass either way, since they pin
already-correct, now-explicit behavior rather than a regression.

**The widened, non-glob AI-readiness check — landed (third slice).**
`scripts/fact_field_readers.py` (registered by `check_ai_readiness.py` as
`fact-field-readers`) is the real, repo-wide static scan this Design
section names: an AST walk for a direct attribute *read* (`ast.Load`) of
`bases`/`virtual_bases`/`vtable`/`vptr_offset_bits`/`is_va_list` anywhere
under `abicheck/`, not a `diff_*.py` glob — because a glob is exactly what let this section's
own hand-enumeration repeatedly miss a real reader across several review
rounds (the fifth, then the tenth call site each surfaced only in a later
pass). Auditing the real tree while building this check (rather than
trusting that hand list) found **three more genuine semantic readers the
plan's own nine-reader table doesn't name**:
`buildsource/header_graph.py`'s inheritance-edge emitter (walks `rt.bases`
to build the L5 source graph's `INHERITS` edges), `buildsource/
source_extractors/base.py`'s `entity_from_record()` (folds `rec.bases`/
`rec.vtable` into the L4/L5 source-ABI-replay entity-identity fingerprint),
and `idioms.py`'s factory/non-virtual-destructor anti-pattern detectors
(`_recognise_factory`/`_is_virtual_dtor_present`/`_collect_base_targets`/
`_detect_non_virtual_dtor`, all reading `rec.vtable`/`rec.bases` directly)
— confirming this Design section's own conclusion in the most direct way
possible: a hand-maintained list is not the same invariant as "every known
reader is checked," and it takes exactly this kind of real scan to find
the readers a hand audit keeps missing.

The check's baseline (`KNOWN_UNMIGRATED_READERS`, allowlist-and-shrink,
`IMPORT_CYCLE_ALLOWLIST`'s own convention) currently records every known
reader site across the modules named above and throughout this Design
section (including `vptr_offset_bits` and every dynamic-read form later
review rounds added -- `getattr`/its aliases, `operator.attrgetter`,
`__getattribute__`, `MatchClass` keyword and positional patterns -- each
described in its own round below) — the exact count is deliberately not
restated here as a number (Codex review, fresh evidence: an earlier
revision of this paragraph copied the count by hand and went stale the
moment a later round changed it, the same drift risk every "stays at N"
review-note copy below carried too -- those now read "the baseline count
is unchanged" instead of a literal figure, for the identical reason).
`len(KNOWN_UNMIGRATED_READERS)` in the source is this baseline's own fact
owner, and the count is explicitly expected to shrink as readers migrate.
Each site is keyed `"<rel>::<qualname>::<attr>::
<outer-expr-text>::<expr-text>::<occurrence>"` -- `qualname` the enclosing
function (`<module>` for module-level code, `Class.method` for a method),
`outer-expr-text` the read's own outermost containing expression
(`_outermost_containing_expr`, climbing every enclosing `ast.expr` up to
the first statement boundary), `expr-text` the read's own exact bare
source text (`ast.get_source_segment`), `occurrence` a rank among reads
sharing all four of those. This is the *final* key shape, landed by the
"fingerprint the containing expression" round further below -- the count
and key shape in this paragraph previously described only the check's
*first* landed slice, silently contradicting the append-only review notes
below it once those notes moved past it (Codex review, fresh evidence);
readers auditing the gate should treat the review notes' own final state
as authoritative, and this paragraph is now kept in sync with it rather
than left as a stale first draft. That first draft keyed only
`"<rel>::<attr>::<occurrence>"`; a
Codex review round caught the real gap in that: an existing read migrated
or deleted and a different, unrelated read of the same attribute later
added to the same file would silently inherit the vacated occurrence
number and read as an already-reviewed site — scoping the counter to
`(qualname, attr)` needs the new read to land in the exact same function at
the exact same rank to collide, a real coincidence rather than a routine
edit's side effect.

**A second Codex round found the function-scoped key still collides.**
`diff_param_qualifiers.py`'s `if not p_old.is_va_list and p_new.is_va_list:`
has two DIFFERENT reads (`p_old.is_va_list`, `p_new.is_va_list`) on one
line, in the same function — a purely positional rank can't tell them
apart, or protect a future, unrelated third read from inheriting one's
rank once it's migrated away. Fixed by folding the read's own source text
into the key (`ast.get_source_segment`, scoping the occurrence counter to
`(qualname, attr, text)`) — a collision now needs the new read to be the
*textually identical* expression, not merely occupy the same rank in the
same function. Every key in the baseline below carries this shape.

`EXEMPT_FUNCTIONS` is the separate, non-shrinking set of specific
functions this check never flags: `RecordType.__post_init__`/
`Param.__post_init__` (the fields' own `__post_init__` omission-bridge
implementation) and two specific DWARF functions,
`dwarf_snapshot._DwarfSnapshotBuilder._finalize_vptr_offsets` and
`dumper_layout_backfill._backfilled_record`, which compute or combine the
raw legacy value itself rather than making a compatibility decision from
it. **Function-scoped, not module-scoped — a first draft exempted the
whole `dwarf_snapshot.py`/`dumper_layout_backfill.py` modules, and a
Codex review round found that was wrong**: both files hold a second kind
of function that genuinely *decides* something from these fields rather
than merely computing them --
`_DwarfSnapshotBuilder._filter_types_by_reachability` reads `bases`/
`virtual_bases` to decide which types survive into the exported snapshot
(dropping one on a false "no bases" reading is a real, silent
correctness loss, not a cosmetic one), and `dumper_layout_backfill.
_fields_corroborate` reads `bases`/`virtual_bases`/`vtable` to decide
whether two records structurally match across the header/DWARF backfill.
A whole-module exemption hid both from the scan entirely; narrowing to
function scope surfaced them as two more genuine, previously-invisible
readers, now real `KNOWN_UNMIGRATED_READERS` entries themselves (bringing
the total from the originally-reported 91 to 99).

**Two more real gaps found in the same review round, both fixed.** (1)
`RecordType.vptr_offset_bits` was originally left out of
`FACT_BRIDGED_ATTRS` on the theory that it was "already meaningfully
`None`... never itself ambiguous" before this phase — false: it carries
the identical `_OMITTED_VPTR_OFFSET_BITS` sentinel-based omission bridge
the other four fields use (`RecordType()` backfills `Fact.not_collected()`;
`RecordType(vptr_offset_bits=None)` backfills `Fact.present(None)` — two
different Facts for the same `None` legacy value), so a direct read has
the identical ambiguity. Adding it surfaced four more real reader sites
(`diff_layout.py`'s `_check_vptr_introduced`/`_has_layout_descriptor`).
(2) The scan matched only `ast.Attribute` nodes, missing a dynamic
`getattr(obj, "vtable", ...)` read with the attribute name as a literal
string constant — `diff_cpp_patterns._is_empty_record` reads `vtable`
exactly this way, invisible to the original scan. Fixed by also matching
a `getattr()` call whose second argument is a string-literal `ast.Constant`
naming one of the bridged attributes (a non-literal name stays out of
scope, the same no-type-inference limit already stated for the attribute
case). No type inference is attempted — verified empirically, by running
the scan against the whole package before writing the baseline, that
every hit recorded there is genuinely a `RecordType`/`Param` access
(attribute or `getattr`) with zero cross-class collisions. Tests:
`tests/test_fact_field_readers.py` (the real repository has zero unlisted
violations; every baseline/exempt entry still names something real; the
AST primitive's own contract — attrs detected including `vptr_offset_bits`,
`getattr` calls detected and a non-matching/dynamic name ignored, `Store`
ignored, per-function occurrence numbering, module/class qualname
derivation — pinned directly; and end-to-end cases against a throwaway
`abicheck/`-shaped tree confirming the check function itself, not only the
primitive, fires on a new violation, stays silent on a baselined one, and
respects a function-scoped exemption without leaking to a sibling function
in the same file).

**A fifth Codex review round found two more real gaps, both fixed.** (1)
The scan matched an `ast.Attribute` read and a `getattr()` call, but not a
structural-pattern-matching read: `case RecordType(bases=[]):` reads
`bases` via `ast.MatchClass.kwd_attrs` (a `list[str]`, paired positionally
with `kwd_patterns`), a node shape neither branch recognized — invisible
to the check even though it collapses unavailable and confirmed-empty the
same way a direct attribute read does. Fixed by matching a `MatchClass`
node's `kwd_attrs` against `FACT_BRIDGED_ATTRS` too, keyed by the matched
keyword pattern's own location and the whole class pattern's source text
(`RecordType(bases=[])`) as `expr-text`, since a `MatchClass` node carries
no location for a single keyword on its own. Verified empirically to have
zero existing hits — no `match`/`case` statement in the repository
currently patterns on any of these five fields, so the baseline count is unchanged. (2) The root `AGENTS.md`'s AI-readiness gate table (the canonical
verification contract every other check is listed in) had no row for
`fact-field-readers`, leaving it undiscoverable from that table — added
alongside `engine-cli-boundary`'s own row. New test:
`test_detects_a_structural_pattern_match_naming_a_bridged_attr`.

**A sixth Codex review round found two more real gaps in the same area,
both fixed.** (1) *Positional* class patterns: `case RecordType(_, _, _,
_, _, []):` reads a field via `cls.__match_args__` positionally, which
`MatchClass.kwd_attrs` (the previous fix's whole mechanism) doesn't cover
at all — an empty `kwd_attrs` list for a purely positional pattern, so
the loop finds nothing. Resolving *which* position maps to which field
would need real `__match_args__` introspection (the dataclass's own
declared field order) this pure-AST scan can't do — instead of leaving
this silently invisible, ANY non-empty positional pattern on a
`MatchClass` whose `cls` resolves by bare name to `RecordType`/`Param`
(`FACT_BRIDGED_CLASS_NAMES`, a new two-name constant) is reported with a
synthetic `<positional>` attr, on this module's own established
false-positive-over-false-negative principle. (2) A deeper, third-round
collision in the baseline key itself: two DIFFERENT call sites sharing
identical bare attribute text — `old_decision(rec.bases)` and, elsewhere
in the same function, `keep(rec.bases)` — still differed only by
occurrence ordinal even after the previous two rounds' fixes (function
scope, then bare-expression text), because the key never looked past the
attribute node itself to the *expression containing it*. Migrating
`old_decision` away and adding an unrelated third read anywhere in the
same function re-numbers the survivors in encounter order — `keep`
silently drops to rank 1 (harmless, it was already reviewed), but the new
read then lands on rank 2, silently inheriting `keep`'s own baseline
approval. Fixed with `_outermost_containing_expr()`: a one-pass parent
map, then climbing every enclosing `ast.expr` up to (but never past) the
first statement boundary. Deliberately the containing *expression*, not
the containing *statement* — a first attempt at this same fix climbed to
the nearest `ast.stmt` instead, which for `if not p_old.is_va_list and
p_new.is_va_list: changes.append(make_change(...))` pulled the *entire
if-body* into the key (a ~970-character entry that would silently break
on any unrelated edit inside that body) — caught before landing by
checking the actual generated key lengths, not just that the fix
compiled. Stopping at the expression boundary gives the whole boolean
test (`not p_old.is_va_list and p_new.is_va_list`, shared correctly by
both reads, still disambiguated between them by the existing bare-text
component) without the body. Key format is now `"<rel>::<qualname>::
<attr>::<outer-expr>::<expr-text>::<occurrence>"` — five components before
the ordinal, not three. Baseline regenerated directly from the real scan
(still the same number of entries, only the key shape changed) and re-verified at zero
unlisted violations. New tests: two different call sites with identical
bare text now getting distinct keys with no ordinal needed, a compound
statement's body confirmed excluded from the key, a positional pattern on
a bridged class detected, and one on an unrelated class ignored.

**A seventh Codex review round found the positional-pattern fix itself
was defeated by an import alias, fixed in the same PR.** `from
abicheck.model import RecordType as RT` then `case RT(_, _, _, _, _,
[]):` names the identical class, but a bare `node.cls.id in
FACT_BRIDGED_CLASS_NAMES` check rejects `"RT"` outright — invisible to
the positional-pattern fix despite being exactly the shape it exists to
catch. Fixed with `_imported_class_aliases()`: maps every local name an
`import`/`from ... import` statement binds one of `FACT_BRIDGED_CLASS_
NAMES` under (via `as`) back to its real name, whole-tree rather than
function-scoped (an import is visible for its entire enclosing scope
regardless of where a later pattern uses it) — the positional-pattern
check resolves through this map before testing membership. Verified
empirically: still zero existing hits, baseline count is unchanged. New tests:
a positional pattern through an import alias detected, one on an alias
of an unrelated class ignored.

**An eighth Codex review round found two more real gaps, both fixed in
the same PR -- the same two mechanisms, probed one alias mechanism
further each.** (1) `RT = RecordType` (a plain module-level assignment,
not an `import ... as`) then `case RT(_, _, _, _, _, []):` -- the
import-alias fix from the previous round only visited `Import`/
`ImportFrom` nodes, so a simple name assignment was invisible to it.
Fixed by extending `_imported_class_aliases()` to also collect a
single-target `name = OtherName` assignment as a candidate, resolved to
`FACT_BRIDGED_CLASS_NAMES` (directly, or transitively through an
already-resolved alias -- `RT2 = RT` chains too) the same fixed-point
way `fact_detector_misuse._fact_aliases` already chains local aliases.
(2) `import builtins; builtins.getattr(rec, "bases")` and `from builtins
import getattr as read_attr` are both the identical read as a bare
`getattr(rec, "bases")` call, but the existing check only matched an
`ast.Call` whose `func` was literally the bare `ast.Name` `"getattr"`.
Fixed with `_builtins_getattr_aliases()`: collects every local name bound
to the real `getattr` builtin (the bare name itself, plus any `from
builtins import getattr as X`) and every local name bound to the
`builtins` module itself (`import builtins`, `import builtins as b`),
and the `getattr`-detection branch now also matches a qualified call
(`<name>.getattr(...)`) whose base resolves to one of those module
names. Verified empirically: still zero existing hits, baseline count is unchanged. New tests: a local class-name alias detected and a negative control
for an unrelated class, a qualified `builtins.getattr` call detected, an
aliased `getattr` import detected, and a negative control for a call
qualified through an unrelated module.

**A ninth Codex review round found two more real gaps, both fixed in the
same PR -- the same two mechanisms probed one further indirection layer
each, matching the eighth round's own framing.** (1) `read_attr = getattr`
then `read_attr(rec, "bases")` is the identical dynamic read as a bare
`getattr(rec, "bases")` call, but `_builtins_getattr_aliases()` only ever
collected the bare name and a `from builtins import getattr as X` import
-- a plain assignment chain to the builtin callable was invisible, the
identical gap the eighth round's fix (1) already closed for a *class*
alias but left open for `getattr` itself. Fixed by extending
`_builtins_getattr_aliases()` with the same fixed-point assignment-chaining
`_imported_class_aliases` already does (`RT = RecordType`, `RT2 = RT`),
just for the builtin callable instead of a class name. (2) `rec.bases +=
inherited` updates a bridged field, but Python marks the target Attribute
node `ast.Store` even though the operation reads the field's existing
value first, to combine it with the right-hand side, before writing the
result back -- an ordinary `Store`/`Del` (a plain overwrite) genuinely
never reads, but an `AugAssign` target always does, and the existing
Load-only restriction missed this distinction entirely: the target
Attribute node is still visited independently by `ast.walk` (it's a child
of the `AugAssign`), but its `Store` context skipped the ordinary
attribute branch too. Fixed with a dedicated `ast.AugAssign` branch
matching the target's own attribute name, keyed on the target Attribute
node itself (not the whole `AugAssign` statement) so its site/text line up
with an ordinary attribute read at the same position. Verified
empirically: still zero existing hits, baseline count is unchanged. New tests: a
local `getattr` alias detected, a chained `getattr` alias detected, an
augmented assignment detected, and a negative control confirming an
ordinary `Store` overwrite is still not flagged.

**A tenth Codex review round found two more real gaps, both small,
bounded extensions of already-built mechanisms -- fixed, matching the
review-convergence status already posted on this PR (further exotic
indirection is documented as a known gap; a bounded combination of two
already-supported mechanisms is not).** (1) `RT: type = RecordType` then
`case RT(_, _, _, _, _, []):` -- an ordinary annotated assignment
(`ast.AnnAssign`), not the `ast.Assign` the class-alias collector already
matched. The same gap the eighth round's fix (1) closed for a plain
`RT = RecordType`, reached through a differently-typed AST node the
collector never visited. Fixed by adding an `ast.AnnAssign` branch to
`_imported_class_aliases()` alongside the existing `ast.Assign` one,
feeding the identical fixed-point chain. (2) `read_attr = builtins.
getattr` then `read_attr(rec, "bases")` -- combining two mechanisms this
file already supports independently (the qualified-call recognition from
round eight and the plain-assignment chaining from round nine) in a way
neither alone catches: the assignment's own value is `builtins.getattr`
(an `ast.Attribute`), not a bare `ast.Name`, so the existing candidate
collector -- which only ever matched an `ast.Name` value -- never added
it. Fixed with a second candidate list resolved once, after the walk
finishes collecting every `import builtins` occurrence (needed since,
unlike the plain-name candidates, this resolution needs the *complete*
`builtins_names` set before it can tell whether the qualifying name is
really the `builtins` module). Verified empirically: still zero existing
hits, baseline count is unchanged. New tests: an annotated class alias detected
through a positional pattern, and a qualified assignment to the
`getattr` builtin detected.

**An eleventh Codex review round found one more real gap, the same
small, bounded shape as round ten's fix (1) -- fixed.** `read_attr:
Callable[..., object] = getattr` then `read_attr(rec, "bases")` -- the
annotated-assignment spelling of the `getattr`-alias assignment, an
`ast.AnnAssign` the candidate collector never matched (it only ever
walked `ast.Assign`). The same gap round ten's fix (1) already closed
for `_imported_class_aliases()`, now closed for `_builtins_getattr_
aliases()` too, for both the plain-name and qualified-attribute value
shapes -- factored into one shared `_add_candidate()` helper so the
`ast.Assign` and `ast.AnnAssign` branches can't independently drift on
which value shapes each recognizes. Verified empirically: still zero
existing hits, baseline count is unchanged. New tests: an annotated `getattr`
alias detected, and the annotated form of the qualified spelling
(`read_attr: object = builtins.getattr`) detected too.

**A twelfth Codex review round found one more real gap, the class-alias
mirror of round eleven's `getattr` fix -- fixed.** `import abicheck.model
as model; RT = model.RecordType` then `case RT(_, _, _, _, _, []):` --
combining two already-supported forms (a qualified class reference,
already recognized when used directly as `model.RecordType(...)`-shaped
construction elsewhere in this file's own reasoning, and an assignment
alias) in a way neither alone catches: the assignment's own value is
`model.RecordType`, an `ast.Attribute`, not a bare `ast.Name`, so
`_imported_class_aliases()`'s candidate collector never resolved it.
Fixed by resolving a qualified-attribute RHS immediately whenever its own
`.attr` is one of `FACT_BRIDGED_CLASS_NAMES` -- matching the identical
name-only stance the `import ... as` branch already takes for its own
qualifying source module (never checked either way) -- factored into one
shared `_register_assign()` helper (mirroring round eleven's
`_add_candidate()` for `_builtins_getattr_aliases()`) so the `ast.Assign`
and `ast.AnnAssign` branches can't independently drift on which RHS
shapes each recognizes. Verified empirically: still zero existing hits,
baseline count is unchanged. New test: a qualified class alias detected through
a positional pattern.

**A thirteenth Codex review round found one more real gap, applying to
both alias resolvers at once -- fixed.** `RT = Alias = RecordType` (an
ordinary chained assignment) then `case RT(_, _, _, _, _, []):`, and the
identical shape for `_builtins_getattr_aliases()`: `read1 = read2 =
getattr` then `read1(rec, "bases")` -- both resolvers' `ast.Assign`
branches were still restricted to `len(node.targets) == 1`, so a chained
assignment (every target receiving the identical RHS, unlike a
tuple-unpacking target, which has no single value to attribute) was
excluded the same way the fifth-Codex-round-in-`fact_detector_misuse.py`
finding was for that module's own candidate collector. Fixed by looping
over every target in `node.targets` and registering each plain-`Name`
one, in both `_imported_class_aliases()` and `_builtins_getattr_
aliases()`. Verified empirically: still zero existing hits, baseline
count is unchanged. New tests: a chained class alias detected through a
positional pattern, and a chained `getattr` alias detected.

**A fourteenth Codex review round found one more real gap -- fixed.**
`import builtins; b = builtins` then `b.getattr(rec, "bases")` --
`builtins_names` was only ever populated from a real `import` statement
(`import builtins`/`import builtins as b`), never from a plain assignment
alias of an already-known one. Fixed by resolving `builtins_names` to a
fixed point too, reusing the identical `assign_candidates` list the
`getattr` alias chain already builds (it already captures every simple
Name-valued assignment, not just getattr-related ones) -- resolved
*before* the existing `qualified_candidates` step, so `b.getattr(...)`
is recognized through the now-expanded `builtins_names` as well.
Verified empirically: still zero existing hits, baseline count is unchanged.
New test: a call through an assigned alias of the `builtins` module.

**A fifteenth Codex review round found the scan still missed two more
standard dynamic-attribute-reading forms, distinct from `getattr`'s own
alias family the previous several rounds closed -- both fixed.**
`operator.attrgetter("bases")(rec)` and `object.__getattribute__(rec,
"bases")`/`rec.__getattribute__("bases")` read the identical legacy value
through different standard-library entry points -- `getattr()` is itself
defined in terms of `__getattribute__`, and `attrgetter` is the
callable-returning equivalent -- so unavailable evidence could again read
as confirmed-empty while the required gate passed. Fixed with a new
`_is_attrgetter_constructor_call()` helper (recognizing both the
qualified `operator.attrgetter(...)` spelling and the bare one reached
via `from operator import attrgetter`) plus two new branches matching a
`__getattribute__` call in both its bound (`rec.__getattribute__
("bases")`) and unbound (`object.__getattribute__(rec, "bases")`/`type.
__getattribute__(rec, "bases")`) forms. Both stay scoped to the same "no
type inference" limit as every other branch here: only a single,
literal-string field name is recognized -- `attrgetter("a.b")`'s dotted
chain, and a two-step `getter = attrgetter(...); getter(rec)` indirection
through an intermediate variable, are both out of scope, the identical
limit a non-literal `getattr()` default already accepts. Deliberately no
local-alias resolution for `attrgetter` itself, unlike `getattr`'s own
now-elaborate alias-chain tracking -- `attrgetter` returns a *callable*
assigned once and invoked later, which is exactly the two-step
indirection this scan already excludes for every other form, so building
the same machinery for it would recognize a shape this scan otherwise
deliberately doesn't. Verified empirically: still zero existing hits in
the real repository, baseline count is unchanged. Eight new tests: the
qualified and bare `attrgetter` forms, a dotted-name negative control, the
variable-indirection negative control, the bound and unbound
`__getattribute__` forms, and a non-matching-name negative control for
the latter.

**A sixteenth Codex review round found two more real gaps in the new
`attrgetter` recognition itself, both fixed.** (1) **An ordinary import
alias of either `operator` or `attrgetter` bypassed the gate.** `import
operator as op; op.attrgetter("bases")(rec)` and `from operator import
attrgetter as ag; ag("bases")(rec)` both read the identical legacy field
as the unaliased spellings, but the fifteenth round's matching checked
only the exact literal names `"operator"`/`"attrgetter"`. Fixed with a
new `_operator_attrgetter_aliases()` -- the identical import-alias
mechanism `_builtins_getattr_aliases()` already provides for `getattr`/
`builtins`, applied to this pair -- resolving `import operator as X` and
`from operator import attrgetter as X` into the two name sets `_is_
attrgetter_constructor_call()` now checks against, instead of two hard-
coded literals. Deliberately narrower than `_builtins_getattr_aliases()`'s
own multi-round evolution: no assignment-chain resolution (`op2 = op`,
`ag2 = ag`) was added, since neither finding asked for it and this stays
consistent with `attrgetter`'s own existing "no local-alias resolution"
stance for a *constructed getter value* -- only the two `import`-form
aliases this finding named are covered. (2) **Only the first argument to
`attrgetter` was inspected.** `attrgetter` accepts any number of
positional field names and reads every one of them --
`attrgetter("size_bits", "bases")(rec)` reads `bases` too, but the
fifteenth round's own code checked only `node.func.args[0]`, so a bridged
field named anywhere but first was invisible. Fixed by moving the whole
`attrgetter` case out of the single-attribute `elif` chain into its own
top-level handler (mirroring how `MatchClass` is already handled
separately, since it too can produce more than one match per node),
inspecting every literal, string-constant positional argument
independently and reporting each bridged one as its own site. Verified
empirically: still zero existing hits in the real repository, baseline
count is unchanged. Five new tests: the `import ... as`/`from ... import ... as`
alias forms, a multi-argument call with the bridged field in the second
position, a multi-argument call with two independently-bridged fields
(both reported), and a multi-argument negative control naming no bridged
field at all. A sixth, separate finding from the same round (a stale
"landed" paragraph in this Design section still describing the check's
*first* slice -- 99 sites, the pre-fingerprint key shape -- contradicting
the append-only review notes once they moved past it) is fixed directly
in that paragraph itself, not narrated as its own round here.

**A seventeenth Codex review round found two more real gaps, one in the
new `attrgetter` alias resolution and one a genuine false positive in the
existing `getattr` recognition -- both fixed, the second with a real
mid-fix correction.** (1) **A plain-assignment alias of `operator`/
`attrgetter` was still out of scope.** `import operator as op; op2 = op;
op2.attrgetter("bases")(rec)` and `from operator import attrgetter as ag;
ag2 = ag; ag2("bases")(rec)` read the identical legacy field as the
unaliased/singly-import-aliased spellings, but the previous round's own
docstring claimed "a `Call`-typed value has no simple assignment shape to
chain through" -- true, but irrelevant: `op`/`ag` are ordinary references
(a module, a builtin callable) *before* being called, chaining through a
plain assignment exactly the way `_builtins_getattr_aliases()`'s own
`getattr`/`builtins` resolution already does. Fixed by giving
`_operator_attrgetter_aliases()` the identical fixed-point assignment-
chaining pattern that function already uses. (2) **A local binding that
shadows the bare `getattr`/`builtins` names was never excluded from the
builtin match at all.** `def f(getattr, rec): return getattr(rec,
"bases")` -- an ordinary, unrelated function parameter reusing the name
`getattr` -- was unconditionally treated as the real builtin, blocking a
valid, unrelated change. Fixed with a new `_locally_bound_names()`,
consulted at each `getattr`/`builtins` match site. **The first revision of
that helper also covered ordinary `ast.Assign`/`ast.AnnAssign` targets,
not just parameters, and immediately broke six existing tests** --
`read_attr = getattr; read_attr(rec, "bases")` IS a genuine local
assignment target of the identical shape, but treating that assignment as
"shadowing" is backwards: it's *how* the existing alias-resolution
mechanism makes `read_attr` trustworthy in the first place, not a reason
to distrust it. Telling a real shadow (`getattr = some_unrelated_value`)
apart from a real alias assignment (`read_attr = getattr`) needs
per-assignment tracing of what each target's own value resolves to --
information `_builtins_getattr_aliases()`'s own internal `assign_
candidates` already computes but doesn't expose, and doing so scoped
per-function is a real, separate follow-up, not attempted here. Since a
parameter can never be an alias source in that same sense (nothing in a
function signature assigns FROM `getattr`), `_locally_bound_names()` was
narrowed to parameters only, closing the reported false positive with no
risk of this conflict -- and `def f(rec): getattr = some_other_thing;
getattr(rec, "bases")` (an ordinary reassignment shadow, not reported by
this round) is left as a documented, accepted residual false positive
rather than a silently reintroduced one, pinned by its own test. Verified
empirically: still zero existing hits in the real repository, baseline
count is unchanged, and `mypy`/`ruff` both stayed clean. Five new tests: the
two chained-alias `attrgetter`/`operator` forms, the shadowed-parameter
negative control, its sibling-function negative control (shadowing in one
function must not leak into another), and the residual-gap documentation
test.

**An eighteenth review round found one more real gap and one real test-
suite performance problem, both fixed.** (1) **The `attrgetter`
recognition never consulted the shadowing check the `getattr` branch
already got.** `def f(operator, rec): return operator.attrgetter("bases")
(rec)` -- an ordinary, unrelated parameter reusing the name `operator` --
was unconditionally treated as the real module, the identical false
positive the `getattr`-shadowing round fixed for that branch specifically,
left open here since this branch was added afterward and never wired to
`_locally_bound_names()`/`_shadowed()` at all. Fixed with a new
`_attrgetter_matched_name()` helper (re-deriving which of `_is_attrgetter_
constructor_call()`'s two recognized shapes actually matched, narrowly
typed so the call site doesn't need its own unchecked `ast.Attribute`/
`ast.Name` assumption -- mypy can't narrow `node.func.func`'s type through
a boolean `and`-chain the way an explicit `isinstance` inside a dedicated
function can) and a `not _shadowed(...)` guard on the same branch. (2)
**Three of this file's own tests each independently re-parsed and
re-scanned every `abicheck/**/*.py` file from scratch.** `test_no_
unlisted_violation_in_real_repo`/`test_baseline_entries_are_real_sites`/
`test_exempt_functions_are_real_sites` each ran their own full
`PKG.rglob("*.py")` walk -- ~14s apiece on a real measurement, ~42s total,
consuming close to this whole file's share of the documented 43-second
fast-suite budget by itself. Fixed with a new module-scoped `_repo_
reader_scan` pytest fixture running the walk exactly once; all three
assertions are pure derivations of the identical underlying scan data.
`test_no_unlisted_violation_in_real_repo`'s own derivation no longer
calls `check_fact_field_readers()` directly, but is provably equivalent
to it -- that function's own filtering is exactly the same two-step check
(`EXEMPT_FUNCTIONS`, then `KNOWN_UNMIGRATED_READERS` membership) applied
to the identical scan, confirmed by reading its source rather than
assumed; `check_fact_field_readers()`'s own Findings/message-building glue
stays independently, directly tested by this file's existing synthetic-
fixture tests (`test_check_reports_a_new_violation`/`test_check_is_
silent_for_clean_code`), so nothing lost real coverage. Verified
empirically: still zero existing hits, baseline count is unchanged, `mypy`/
`ruff` both stayed clean, and this file's own runtime dropped from ~26s
to ~9s for its (now 60, previously 57) tests. Three new tests: the
shadowed-`operator`-parameter and shadowed-bare-`attrgetter`-parameter
negative controls, and a sibling-function negative control (shadowing in
one function must not leak into another's genuine `attrgetter()` call).

**A nineteenth review round found the identical shadowing gap in the
unbound `__getattribute__` branch's sibling** -- the bound form
(`rec.__getattribute__("bases")`) has no builtin name to shadow (its
receiver is an arbitrary object, exactly like `rec.bases_fact` itself), but
the unbound form (`object.__getattribute__(rec, "bases")`) names the
builtin `object`/`type` the same way the `getattr`/`attrgetter` branches
name theirs, and it was the one branch of the four dynamic-reader forms
never wired to `_shadowed()`: `def f(object, rec): return object.
__getattribute__(rec, "bases")` -- an ordinary, unrelated parameter reusing
the name `object` -- was unconditionally treated as the builtin, the
identical false positive already fixed for `getattr` and (eighteenth round)
`attrgetter`. Fixed with a `not _shadowed(node, node.func.value.id)` guard
on the same condition, mirroring the other three branches exactly -- no new
helper needed here, since `node.func.value.id` is already narrowed to
`ast.Name` by the existing `isinstance` checks in the same `and`-chain, one
level shallower than the attrgetter branch's `node.func.func` access that
needed its own dedicated helper in the eighteenth round. Verified
empirically: still zero existing hits in the real repository, baseline
count is unchanged, `mypy`/`ruff` both stayed clean. Three new tests: the
shadowed-`object`-parameter and shadowed-`type`-parameter negative
controls, and a sibling-function negative control (shadowing in one
function must not leak into another's genuine unbound
`object.__getattribute__()` call).

**A twentieth review round found two more real gaps, both fixed.** (1)
**The unbound `__getattribute__` receiver check matched only the two
literal spellings `"object"`/`"type"`, missing an ordinary import
alias.** `from builtins import object as O; O.__getattribute__(rec,
"bases")` is the identical dynamic read as the unaliased spelling, but
was invisible to the scan entirely. Fixed with a new
`_unbound_getattribute_receiver_aliases()` helper (always includes the
bare `"object"`/`"type"`, plus any `from builtins import object as O`/
`from builtins import type as T`, plus a plain-assignment alias chain,
mirroring `_builtins_getattr_aliases()`'s own `getattr`-alias chaining
exactly), consulted in place of the literal two-element tuple; the
existing `_shadowed()` guard from the nineteenth round applies unchanged,
since it already checks whatever name was actually matched. (2)
**`_shadowed()` consulted only a call's own innermost qualname, never an
enclosing function's binding.** `def outer(getattr): def inner(rec):
return getattr(rec, "bases")` -- `inner` binds no parameter of its own
named `getattr`, but Python's ordinary closure rule still captures the
arbitrary callable `outer`'s own parameter holds, so this was
unconditionally treated as the real builtin despite being shadowed one
scope up. Fixed with a new `_lexical_function_parents()` helper -- a
standalone copy of `fact_detector_misuse.py`'s identical-purpose helper,
simplified to this module's own coarser, dot-joined qualname scheme (no
`#lineno` disambiguator, an existing, accepted characteristic of this
module's qualnames already) -- and widened `_shadowed()` to walk the
call's entire lexical scope chain, testing membership in each ancestor's
own `locally_bound` set in turn rather than only the innermost one.
Verified empirically: still zero existing hits in the real repository,
baseline count is unchanged, `mypy`/`ruff` both stayed clean. Four new tests:
the aliased-unbound-`__getattribute__` positive case and its
unrelated-name negative control, and the enclosing-parameter-shadow
exclusion and its sibling-function negative control.

**A further Codex review round found the `attrgetter` branch's own
baseline key never actually fingerprinted the read's containing
expression, unlike every other reader form.** `outer_text = ast.
get_source_segment(source, node) ...` used the attrgetter *call's own*
bare text for both the outer-expression and expr-text key slots, instead
of climbing to the read's real outermost containing expression via
`_expr_text()` the way the plain-attribute/`getattr`/`__getattribute__`
branches already do. `old_decision(attrgetter("bases")(rec))` and
`keep(attrgetter("bases")(rec))` -- two attrgetter reads with an
identical bare call but different containing expressions -- collapsed to
the exact same key, ending in occurrence `1` for both once sorted by
position; migrating the first reader while adding an unrelated new one
at the same rank would silently reuse the vacated key, the exact
collision `_outermost_containing_expr()` exists to close for every other
form (the very finding that motivated that helper's own creation
several rounds above -- this branch was simply never wired to it). Fixed
by computing `outer_text = _expr_text(node)` (the containing expression)
separately from `text` (the call's own bare source), matching the
six-part key's established `(qualname, attr, outer_text, text,
occurrence)` shape exactly. Verified empirically: still zero existing
hits in the real repository (no `attrgetter` read exists anywhere in
`abicheck/` today, so the baseline itself is unaffected by the key-shape
fix), baseline count is unchanged, `mypy`/`ruff` both stayed clean. New
test: two attrgetter reads sharing identical bare-call text inside
different containing expressions, pinning that they now get distinct
keys.

**A further Codex review round confirmed, with a concrete repro, a gap
`_locally_bound_names()`'s own docstring had already predicted but left
unattempted -- recorded here as an accepted known gap rather than a bug
fix, since a correct fix needs real per-position dataflow this module
does not have.** `def f(getattr, rec): getattr = builtins.getattr; return
getattr(rec, "bases")` -- a shadowing parameter later *rebound* to a
genuine alias source -- currently reports no site at all, even though the
call genuinely reads a bridged field through that rebound name (the
sibling `attrgetter`/`operator` shape has the identical gap). Correctly
distinguishing this from a genuine, still-shadowing parameter (`getattr =
some_unrelated_value`) needs order-aware tracing of which assignment is
actually in effect at the call's own position -- an order-*blind* "was
this name ever reassigned to a recognized alias anywhere in the scope"
check is unsound in the other direction, since `def f(getattr, rec):
result = getattr(rec, "bases"); getattr = builtins.getattr` calls
`getattr` *before* the rebind, while it still holds the arbitrary
parameter value, and would be wrongly excluded by that simpler check.
This module's presence/absence-only shadowing model has no notion of
"which binding is in effect here" at all; building one is a materially
larger change than every guard condition landed incrementally so far --
it took `fact_detector_misuse.py`'s own alias machinery upwards of twenty
review rounds to reach exactly this kind of order-sensitivity for a
structurally similar problem. Recorded directly in `_locally_bound_
names()`'s own docstring (extending the paragraph that already predicted
this shape) rather than fixed under review pressure: this is a false
*negative*, the direction this module's own "a false positive is far
cheaper than the false negative it closes" trade-off argues hardest
against silently accepting, but an incorrect, order-blind attempt risks
trading it for a new false positive on a genuine shadow -- not obviously
an improvement, and not the kind of bounded, single-condition extension
every other round in this section landed. Revisit with real per-position
tracing if this shape is found in practice.

**Two further Codex review rounds found two more real gaps, both fixed,
in the same area the previous several rounds had already been hardening.**
(1) **`_operator_attrgetter_aliases()` seeded `"attrgetter"`/`"operator"`
into their own alias sets unconditionally**, mirroring how
`_builtins_getattr_aliases()` correctly seeds the real builtin
`"getattr"` -- but `attrgetter`/`operator` are not builtins; nothing makes
either name mean the stdlib module/function without a real `import
operator`/`from operator import attrgetter` somewhere in the file. An
ordinary, unrelated local function or variable sharing either name --
`def attrgetter(rec, name): ...` with no import anywhere -- was therefore
wrongly recognized as the real constructor. Fixed by seeding both sets
empty and relying entirely on the module's own existing real-import scan
(the same one every `getattr`/`builtins` qualified-alias branch already
required) -- an asymmetry the fix's own docstring now states explicitly,
since a future reader could otherwise "fix" the asymmetry the wrong way
by re-adding the unconditional seed. New tests: an unrelated local
`attrgetter` function with no import, and its dotted-access sibling (an
unrelated `operator`-named local variable with its own `attrgetter`
method), both in `tests/test_fact_field_readers_wrapper_scoping.py`. Two
of this area's own pre-existing shadowing tests
(`test_ignores_attrgetter_shadowed_by_an_operator_parameter`/
`test_ignores_attrgetter_shadowed_by_a_bare_attrgetter_parameter`) had no
real import in their fixtures either, so under this fix they would have
passed for the wrong reason (no import at all, rather than the shadowing
guard) -- both were corrected to include a real module-level import, so
they keep testing the shadowing check specifically rather than silently
degrading into a second copy of the new import-requirement test.

(2) **`_outermost_containing_expr()` stopped climbing one level too early
at a keyword-argument value or a comprehension's own `for ... in ...`
clause**, since neither `ast.keyword` nor `ast.comprehension` is itself an
`ast.expr` -- the climb's own guard condition (`isinstance(parent,
ast.expr)`) correctly stops at any *ordinary* non-expression boundary
(a statement), but these two wrapper node types are not statements
either; they are transparent syntactic scaffolding a real containing
expression still continues through. Two real examples surfaced by
regenerating the baseline against the fix: `make_change(...,
old_value=str(t_old.bases), ...)` (a keyword argument) and
`{_topmost_scope_suffix(b) for b in header.bases + header.virtual_bases}`
(a comprehension's own iterable clause) both had their previously-recorded
`outer-expr-text` narrower than the real surrounding expression, silently
under-recording how much context a reader site's own key actually
carries. Fixed with a new `_TRANSPARENT_EXPR_WRAPPER_TYPES = (ast.keyword,
ast.comprehension)` tuple, checked alongside `ast.expr` in the climb's own
condition. This legitimately reshaped 19 pre-existing, already-reviewed
`KNOWN_UNMIGRATED_READERS` entries (same underlying sites, wider
`outer-expr-text`) -- the baseline was regenerated from a fresh real-repo
scan rather than hand-edited, with the count confirmed unchanged (104
before and after) and every reshaped key confirmed a 1:1 rename of an
existing site, not a new or missing violation. New tests, in
`tests/test_fact_field_readers_wrapper_scoping.py`: a keyword-argument
read climbing to its enclosing call, a comprehension read climbing to its
enclosing display, and two reads sharing identical attribute/expression
text but wrapped in different keyword arguments of different calls,
pinning that each climbs to its own enclosing call rather than the two
collapsing onto a shared inner boundary.

Both fixes' new tests were split into a new sibling file,
`tests/test_fact_field_readers_wrapper_scoping.py`, rather than appended
to `test_fact_field_readers.py` -- which was already within a few lines of
the architecture gate's 1200-line test-file cap once the two `test_
ignores_an_unrelated_*_with_no_import` and three climbing tests were
added -- mirroring how `tests/test_fact_detector_misuse_def_time_scope.py`
was split out of `test_fact_detector_misuse.py` for the identical reason.
Verified empirically: still zero existing `attrgetter`/`operator` false
positives introduced against the real repository (the baseline
regeneration's own 104-in/104-out count is the check), `mypy`/`ruff` both
stayed clean, and `python scripts/check_architecture.py` reports 0 errors
with both test files under the cap.

**A further Codex review round found a fresh repro for a residual this
module's own docstring had already named, explicitly, as an accepted,
narrow gap -- attempted a fix, and reverted it after the fix's own blast
radius and residual coverage didn't justify it.** `unmigrated_fact_reader_
sites`'s docstring already states: "A genuinely duplicated expression (the
identical containing expression appearing twice, with identical bare-read
text, in one function) still falls back to the ordinal, an accepted,
narrow residual." Codex's repro sharpens *why* that's a real risk, not
just a curiosity: `decide(rec.bases)` under `if cond:` and, separately,
under `else:` gets two baseline entries differing only by occurrence rank
(`...::1`, `...::2`); deleting the `if` branch's call and adding a
*different*, genuinely new `decide(rec.bases)` call somewhere else in the
function re-numbers the survivors from scratch, so the new call can land
on the vacated rank and silently inherit an unrelated site's approval.

Attempted a fix: `_branch_path()`, walking a read's ancestors up to its
enclosing function and recording which alternative of each `if`/`try`/
`match`/loop-`else` it sits in, added as a new key segment. A first cut
used a bare structural label (`"if"`/`"else"`) -- verified against the
exact repro and found **insufficient**: two separate, sibling `if`
statements in the same function both mark as plain `"if"` regardless of
which one, so Codex's own repro (the read moving from a deleted `else:`
to a *different* `if` statement elsewhere) still collided. Revised to
carry the branch's own *governing* text (`f"if:{test text}"`,
`f"except:{i}:{type text}"`, `f"case:{i}:{pattern text}"`) rather than a
bare label -- deliberately still not the *body* text
`_outermost_containing_expr`'s own docstring already rejected including
(a change anywhere inside the branch's own body would then reshape the
key for no reason); only the governing condition/type/pattern, which
changes only when the branch's own identity does. This version correctly
closed Codex's exact repro and a hand-built adversarial variant reusing
the identical condition-variable name across separate `if` statements,
verified empirically both ways.

**Reverted anyway, after actually running the full existing suite --
not because the mechanism was wrong, but because its true cost wasn't
visible until measured.** The new key segment is a real structural
change to the key *shape* for every entry, not just branching ones: a
non-branching read's `branch_marker` is `""`, but the key is still built
by `"::"`-joining a fixed number of components, so every one of the 40+
existing hand-written key-literal tests in this file failed --  not
because their logic was wrong, but because every expected key string now
needs an inserted empty `"::"` segment before the occurrence rank, and
the 104-entry `KNOWN_UNMIGRATED_READERS` baseline needs a full
regeneration for the same reason. That is a large, mechanical, but
genuinely invasive change for a gap this module's own docstring had
*already* scoped and accepted as narrow -- and even after paying that
cost, a residual remains (two sibling branches with byte-identical
governing text, e.g. two separately-written `if cond:` blocks with the
identical condition spelling and an identical duplicated call inside
each) still falls back to the ordinal, unchanged from before the
attempt. Weighed against this module's own established discipline
(documented elsewhere in this same file and in AGENTS.md's "attempted
twice, reverted twice" pattern): a fix that trades a large, invasive
change and a full baseline regeneration for a *narrower*, not
*eliminated*, version of an already-accepted, already-documented residual
is not a clear improvement, so it was reverted rather than shipped.
`git diff` after the revert is clean (`scripts/fact_field_readers.py`
byte-identical to before the attempt); the full existing test suite
(73 tests across this file and its wrapper-scoping sibling) passes
unchanged. The residual stays exactly as this module's own docstring
already described it before this round: an accepted, narrow gap, now
with a concretely verified (not merely hypothetical) repro on record here
and in the PR's own review thread.

**A further Codex review round found two more real gaps, both fixed.**
(1) **The attrgetter branch required the constructor call to be
*immediately* invoked** (`attrgetter("bases")(rec)`, matched via
`isinstance(node, ast.Call) and isinstance(node.func, ast.Call) and
_is_attrgetter_constructor_call(node.func, ...)`), missing the equally
common callback spelling entirely: `sorted(records,
key=operator.attrgetter("bases"))` and `map(attrgetter("bases"),
records)` both construct the identical getter, just hand it to another
function instead of calling it themselves -- the read still happens, on
whatever `sorted`/`map` eventually calls it with. Fixed by matching the
constructor call directly (`_is_attrgetter_constructor_call(node, ...)`,
no outer-call requirement at all) -- the same conservative-by-design
principle every other branch here already follows: a false positive
(a constructed-but-never-called getter) costs a reviewed baseline entry,
a false negative would be silent. This also closes, as a side effect
rather than a targeted fix, the previously-documented two-step
local-variable-indirection gap (`getter = attrgetter("bases");
getter(rec)`) -- `_is_attrgetter_constructor_call()`'s own docstring
previously stated this was deliberately out of scope, "the same
`x = attrgetter` equivalent of `_builtins_getattr_aliases()`" that
`attrgetter` doesn't get; both that docstring and `unmigrated_fact_
reader_sites()`'s own attrgetter-branch comment were corrected to
describe the new, broader match instead of the narrower one they no
longer describe. One consequence worth naming: `text` (the read's own
bare source, previously the *whole* double-call `attrgetter("bases")
(rec)`) is now just the constructor call's own text (`attrgetter(
"bases")`), since that call is what's actually matched -- `outer_text`
is unaffected (`_outermost_containing_expr()` still climbs through the
immediate outer call when there is one, since `ast.Call` is itself an
`ast.expr`). Every existing attrgetter test asserting an exact key
literal for the immediate-double-call shape needed updating to the
narrower `text` value; the real repository baseline is unaffected (still
zero existing attrgetter reads anywhere in `abicheck/`).

(2) **`_locally_bound_names()` only ever tracked a *parameter* as a
locally-bound name, never a nested `def`/`class` statement's own
*name*.** `def getattr(obj, name): return None` followed by `getattr(rec,
"bases")` -- an ordinary, unrelated function definition sharing the
builtin-looking name `getattr`, at module scope or nested inside the
calling function itself -- was still unconditionally treated as the real
builtin, since a def/class statement's own binding target (Python's
ordinary `STORE_NAME`/`STORE_FAST` rule, the identical rule
`fact_detector_misuse.py`'s own `_def_containing_qualnames` already
models for the sibling module) was invisible to this module's shadowing
check. Fixed by threading a `nearest_func` parameter through `_locally_
bound_names()`'s walk (mirroring `_lexical_function_parents`'s identical
concept -- class bodies stay transparent, the same simplified model this
whole module already uses) and recording each `def`/`class`'s own `name`
against whichever scope directly contains it, alongside its existing
parameter tracking.

Both fixes' new tests, plus the attrgetter-indirection test repurposed
from a negative to a positive control (its old premise -- "no local-alias
resolution for attrgetter" -- no longer holds), went into `tests/
test_fact_field_readers_wrapper_scoping.py` (room remained under the
architecture gate's 1200-line cap; one existing test was moved there from
`test_fact_field_readers.py` to keep the main file under the cap after
its own docstring updates grew it past 1200). Verified empirically: still
zero existing hits in the real repository, `mypy`/`ruff` both stayed
clean, and `python scripts/check_architecture.py` reports 0 errors with
both test files under the cap.

**A further Codex review round found a real regression in finding 4's own
fix, plus one more real gap in the module's line-based qualname model,
both fixed.**

**(1) The class-body-transparency fix used the wrong scope concept.**
Finding 4's fix threaded a `nearest_func` parameter, skipping class
layers the same way `_lexical_function_parents` deliberately does for
*closure* purposes -- but a method's own *name* does not bind into its
enclosing function/module namespace at all; it becomes a class attribute
(`C.getattr`), invisible to an ordinary bare-name lookup anywhere outside
the class body. Recording it against the skip-class `nearest_func` meant
`class C: def getattr(self, name): ...` made an *unrelated* function
elsewhere in the same module -- with no textual relationship to `C` at
all -- read as if it had a local `getattr` binding, silently excluding
its own genuine `getattr(rec, "bases")` call. Fixed by replacing
`nearest_func` with `binding_scope: str | None` -- the scope a bare name
actually *binds into*, as opposed to "the scope a closure looks up
through" -- `None` while directly inside a class body (nothing recorded
there at all, matching how `_shadowed()` never queries a class-body scope
either, since none of this module's qualname machinery models one), and
the function's own qualname once recursed into a function body (an
ordinary nested function's own name genuinely does bind into its
immediately enclosing function, unlike a method's into its class). New
tests: the reported case, and the identical class-body-transparency rule
for a nested `class` (not just a nested `def`) shadowing `attrgetter`.

**(2) A parameter default value/annotation was attributed to the
function's own body scope, not the enclosing scope it actually evaluates
in.** `def f(getattr, x=getattr(rec, "bases")): ...` -- Python evaluates
the default *before* `f`'s own parameters exist, so this call genuinely
reads the real builtin, but `_enclosing_qualnames()`'s `[child.lineno,
end]` range covers the function's own signature line too, so `_shadowed()`
saw `f`'s own (not-yet-bound) parameter `getattr` and wrongly excluded a
real read. A decorator needs no equivalent fix -- it sits on a line
strictly *before* `child.lineno`, already outside the function's own
range by construction. Fixed by registering each default/annotation's own
`[lineno, end_lineno]` range under the *current* (enclosing, pre-function)
qualname -- narrower than the function's own range in the ordinary case,
so the existing smallest-range-wins tie-break lets it correctly override
the function's own broader range for just those lines. A default/
annotation sharing a line with genuine function-*body* code (a one-liner
`def f(x=getattr(rec, "bases")): return x`) is a real, accepted residual
this line-based model can't distinguish further -- the same granularity
limit this function's own docstring already accepts throughout. New
tests: the reported case, a negative control confirming an ordinary body
call is still correctly shadowed, and a negative control confirming a
default nested inside a function that genuinely declares the shadowing
parameter is still correctly excluded (this fix only corrects the
function's own signature line being wrongly attributed to itself, not
shadowing in general).

Both fixes' new tests went into `tests/test_fact_field_readers_wrapper_
scoping.py` (room remained under the architecture gate's 1200-line cap).
Verified empirically: still zero existing hits in the real repository,
`mypy`/`ruff` both stayed clean.

**A further Codex review round found two more real gaps, both fixed.**

**(3) No branch recognized a mapping-based field read at all.**
`vars(rec)["bases"]`/`rec.__dict__["bases"]` both read the exact same
normalized legacy value `rec.bases` does, through the instance's own
`__dict__` mapping rather than attribute-lookup machinery -- invisible
to every existing branch (`ast.Attribute`, `getattr()`, `attrgetter()`,
`__getattribute__()`), none of which is an `ast.Subscript`. Added as a
new branch matching a `Subscript` in `ast.Load` context with a literal
string key in `FACT_BRIDGED_ATTRS`, whose `.value` is either a `vars(...)`
call (gated on `_shadowed()` for the `vars` spelling, an ordinary
bare-name call the same shadowing guard every other dynamic form already
gets) or a `.__dict__` attribute access (matched unconditionally -- an
attribute access, unlike a bare name, has nothing for a local binding to
shadow). A non-literal key (`vars(rec)[name]`) stays out of scope, the
same "no type inference" limit every other dynamic-read form here
already accepts. New tests: both positive forms, a shadowed-`vars`-
parameter negative control, a non-literal-key negative control, and an
unrelated-key negative control.

**(4) `_locally_bound_names()` never visited `ast.Import`/`ast.
ImportFrom` at all.** `from helper import getattr` then `getattr(rec,
"bases")` -- an ordinary import of an unrelated module's own `getattr`
symbol, reusing the builtin-looking bare name -- was still treated as
the real builtin, since an import statement's own binding was invisible
the same way a bare parameter/`def`/`class` name once was (findings 4-5
above, before those were fixed). Fixed by recording each imported name
(`alias.asname` if given, else the plain name, with the identical
first-`.`-segment split `fact_detector_misuse.py`'s own import branch
already applies for an unaliased dotted `import a.b.c`) against
whichever scope directly contains the import statement -- with a
deliberate carve-out for the specific imports this module already
recognizes elsewhere as a genuine alias *source* for a real builtin/
`operator` symbol: `from builtins import getattr`/`object`/`type`, `from
operator import attrgetter`, and a bare `import builtins`/`operator`.
Recording one of *those* as a local binding here too would have made
`_shadowed()` see it as shadowing itself, silently breaking the very
recognition it exists to enable -- e.g. `from operator import
attrgetter; attrgetter("bases")(rec)` would have stopped being
recognized at all, a real regression rather than an incomplete fix. Every
*other* import, including an aliased `from builtins import getattr as g`
(recognized under the alias `g`, excluded the identical way), still binds
and shadows normally. New tests: an unrelated module-scope import shadow,
its aliased-import variant, the identical shadow established inside a
function body instead of at module scope, a sibling-function negative
control (the shadow in one function must not suppress detection in an
unrelated one), and four positive controls confirming every one of the
five recognized-import carve-outs (`from builtins import getattr`, its
aliased spelling, `from operator import attrgetter`, and a bare `import
operator`) still resolves correctly.

Both fixes verified empirically: still zero existing hits in the real
repository, `mypy`/`ruff` both stayed clean. New tests in
`TestMappingBasedFieldReads` and `TestImportedNamesShadowBuiltinRecognition`
(`tests/test_fact_field_readers_wrapper_scoping.py`).

**A further Codex review round found one more real gap in the mapping-
subscript fix above.** `vars(rec).get("bases")`/`rec.__dict__.get(
"bases")` -- the `dict.get()` spelling of the identical mapping read --
were both still invisible, since neither is an `ast.Subscript`, the only
shape the previous fix's branch matched. Fixed by factoring the shared
"is this a mapping over the instance's own `__dict__`" check (`vars(
...)`/`.__dict__`) out into a new `_is_mapping_receiver()` helper, reused
by both the existing subscript branch and a new `.get()`-call branch --
so the two forms can't independently drift on what counts as a
recognized mapping receiver, the same lesson this file's own earlier
entries (the attrgetter-vs-`__getattribute__` alias sets, the vars/
`__dict__` shadowing rules) keep re-learning about duplicated conditions.
An optional second `.get()` argument (the default) is accepted but not
inspected, matching how `getattr()`'s own third argument is treated
elsewhere in this module. New tests: both mapping-receiver forms with
`.get()`, a call carrying an explicit default, a shadowed-`vars`-
parameter negative control, an unrelated-key negative control, and a
negative control confirming an ordinary `.get()` call on some unrelated
object (not `vars(...)`/`.__dict__`) is never flagged merely because its
argument happens to spell a bridged field name.

Verified empirically: still zero existing hits in the real repository,
`mypy`/`ruff` both stayed clean. New tests in `TestMappingGetFieldReads`
(`tests/test_fact_field_readers_wrapper_scoping.py`).

**A further Codex review round found one more real gap: `_is_mapping_
receiver()` only ever matched the bare literal spelling `vars`, not a
real alias of it.** `import builtins; builtins.vars(rec)["bases"]` (a
qualified call through a real `builtins` alias) and `read_map = vars;
read_map(rec).get("bases")` (a plain assignment alias) were both
invisible -- the identical gap `_builtins_getattr_aliases()` already
closed for `getattr` specifically, never extended to `vars`. Fixed with
a new, generalized `_builtins_symbol_aliases(tree, symbol,
builtins_names)`: the *symbol*-specific half of that same alias-
resolution mechanism (import-from, a plain-assignment chain resolved to
a fixed point, a qualified `X.symbol` chain), taking the caller's
already-resolved `builtins_names` as a parameter rather than re-deriving
it, so `vars` doesn't need a third hand-duplicated copy of the shared
`import builtins`/module-alias collection `_builtins_getattr_aliases()`
itself already owns. `_builtins_getattr_aliases()` itself was left
unchanged rather than refactored to share this helper -- it is already
hardened across five prior review rounds (see its own docstring), and
generalizing it risks reopening one of them for no benefit, since it
already returns exactly the `builtins_names` this function needs.

**A real regression caught before the tests even ran, by the tests
themselves rather than a fresh review round: an aliased import of `vars`
(`from builtins import vars as V`) was still wrongly excluded, for a
different reason than the one this fix targets.** `_locally_bound_names`'s
own recognized-import carve-out (added two rounds earlier for `getattr`/
`object`/`type`/`attrgetter`) had no entry for `vars` at all, so `from
builtins import vars as V` was itself recorded as an ordinary local
binding of `V` at module scope — making `_shadowed()` see `V` as shadowed
by its *own* import statement, the exact inversion the carve-out exists
to prevent. Fixed by adding `"vars"` to that carve-out's recognized-name
set, alongside the new alias-resolution fix above (not a separate,
unrelated bug — the same "this import is a recognized alias *source*,
not a shadow" principle, just missing one more entry).

Verified against the reported qualified-call and assigned-alias repros,
an imported-alias positive control, and three negative controls (a
`builtins`-shadowing parameter, an aliased-import self-shadow, and an
unrelated object's own `.vars()` method). Zero existing hits, `mypy`/
`ruff` both stayed clean. New tests in `TestVarsAliasesInMappingReads`
(`tests/test_fact_field_readers_wrapper_scoping.py`).

**A further Codex review round found one more real gap: `_operator_
attrgetter_aliases()`'s own assignment-chain resolution never recognized
a *qualified* RHS.** `import operator as op; ag = op.attrgetter;
ag("bases")(rec)` was invisible, since `_add_candidate()` only ever
matched a plain `ast.Name` value -- the identical gap `_builtins_getattr_
aliases()`'s own `qualified_candidates` mechanism already closes for
`read_attr = builtins.getattr`. Fixed the same way: a qualified
`X.attrgetter` assignment is collected into a new `qualified_candidates`
list during the same walk, resolved once the walk (and therefore
`operator_names`) is complete -- since, unlike the plain-name chain, this
needs the *complete* set to know whether `X` really is a resolved
`operator` alias. The resolution order matters: `operator_names`'s own
fixed point runs first, then the qualified resolution folds into
`attrgetter_names`, then `attrgetter_names`'s own plain-name fixed point
runs last -- so a name assigned from a qualified alias can itself be
chained further (`ag = op.attrgetter; ag2 = ag`).

Verified against the reported repro, a further-chained variant
(confirming the qualified resolution feeds back into the existing
plain-name chain), and a negative control for an unrelated object's own
`.attrgetter` attribute. Zero existing hits, `mypy`/`ruff` both stayed
clean, `fact_field_readers.py` at 1741 lines (well under the 2000-line
hard cap). New tests in `TestQualifiedAttrgetterAssignmentAliases`
(`tests/test_fact_field_readers_wrapper_scoping.py`).

**One more finding, from the next review round: the unbound-method
`object.__getattribute__(rec, "bases")` call branch only ever matched a
call made directly off `object`/`type`/an alias of either receiver -- it
had no notion of the *method itself* being lifted out to a plain name
first.** `read_attr = object.__getattribute__; read_attr(rec, "bases")`
performs the identical unbound-method read, but the call-matching branch
requires `node.func` to still be an `ast.Attribute` (`X.__getattribute__
(...)`) -- once the method is assigned to `read_attr`, `node.func` is an
`ast.Name`, and neither this branch nor `_unbound_getattribute_receiver_
aliases()` (which tracks aliases of the *receiver* `object`/`type`, not of
the method) had anything to recognize it. Fixed with a new
`_unbound_getattribute_method_aliases(tree, object_type_names)`, mirroring
`_builtins_symbol_aliases()`'s own qualified-candidate mechanism: a plain
assignment whose RHS is `X.__getattribute__` for some `X` already in the
caller's resolved `object_type_names` seeds the set, then an ordinary
plain-name chain resolves further aliases of that alias to a fixed point
-- so a receiver alias (`from builtins import object as O`) and a method
alias (`read_attr = O.__getattribute__`) compose correctly together. A new
call-matching branch was added alongside the existing unbound-receiver
branch, requiring the same `not _shadowed(...)`/two-args/literal-string-
argument/`FACT_BRIDGED_ATTRS` shape the existing branches already enforce.

Verified against the reported repro, a chained-alias variant, a
receiver-alias-composed-with-method-alias variant, the `type.
__getattribute__` sibling spelling, and two negative controls (a
shadowing parameter; a non-bridged attribute argument) via direct AST
reproduction before writing tests. Zero existing hits, `mypy`/`ruff` both
stayed clean, `fact_field_readers.py` at 1821 lines (well under the
2000-line hard cap, though headroom is narrowing -- the next finding in
this area should reassess whether a sibling module split is due before
adding more). New tests in `TestUnboundGetattributeMethodAliases`
(`tests/test_fact_field_readers_wrapper_scoping.py`).

**One more finding, from the next review round: the mapping-subscript
branch's `ast.Load`-only restriction missed the augmented-assignment
shape entirely, the identical gap the dedicated `ast.Attribute`-target
`AugAssign` branch already exists to close for the plain-attribute
form.** `rec.__dict__["bases"] += values` / `vars(rec)["bases"] += values`
both read the field's existing value before combining it with the
right-hand side, but Python marks an `AugAssign` target `ast.Store`
regardless of its shape -- the target `Subscript` node is still visited
independently by `ast.walk` (it's a child of the `AugAssign`), but its
`Store` context skips the ordinary, `Load`-only Subscript branch too, so
nothing caught it. Fixed with a new `ast.AugAssign` branch matching a
`Subscript` target with a literal string key naming a bridged attribute,
gated on `_is_mapping_receiver(node.target.value)` -- mirroring the
existing attribute-target `AugAssign` branch exactly, including keying
the finding on the target `Subscript` node itself (not the whole
`AugAssign` statement) so its site/text line up with an ordinary
subscript read at the same position.

Verified against both mapping spellings (`rec.__dict__[...]`/
`vars(rec)[...]`) and three negative controls (a non-bridged key; a
plain, non-augmented overwrite, which genuinely never reads and correctly
stays unflagged; a non-mapping receiver) via direct AST reproduction
before writing tests. Zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_field_readers.py` at 1845 lines -- still under the 2000-line hard
cap, but headroom has narrowed enough (155 lines) that the next finding
in this area should split a sibling module before adding more, per the
prior round's own note. New tests in
`TestAugmentedAssignmentThroughMappingReceivers` (`tests/
test_fact_field_readers_wrapper_scoping.py`).

**Two more findings from the next review round, plus the sibling-module
split the prior round's own note said was due.** (1) `_locally_bound_
names()`/`_enclosing_qualnames()` deliberately don't model an `ast.Lambda`
as its own scope at all (see either function's own docstring on why this
module's design stays coarser than `fact_detector_misuse.py`'s) -- a
lambda's body shares its *enclosing* function's qualname, so a lambda's
own parameter was never recorded as bound anywhere `_shadowed()`'s
qualname-based check could see. `lambda getattr, rec: getattr(rec,
"bases")` -- an ordinary, unrelated lambda parameter reusing the
builtin-looking name -- was still treated as the real builtin. Rather
than widening the qualname/scope machinery itself (a materially larger
change touching three functions' worth of established, narrower-by-design
modeling), `_shadowed()` now also walks the call's own true AST ancestry
(via the already-available `parents` map) checking every enclosing
`ast.Lambda`'s own parameters directly -- exact by construction, so it
can never misattribute a shadow to a call genuinely outside the lambda,
even one sharing the same line/qualname the coarser qualname model would
conflate them under (verified with a dedicated negative control: a
genuine `getattr` call textually outside the lambda, in the same
function, still gets caught). `_shadowed()`'s own parameter type widened
from `ast.Call` to `ast.expr`, since it only ever consults `.lineno` and
walks `parents` -- neither Call-specific -- and the new bare-`ast.Name`
use site from finding (2) needs the wider type too. (2)
`_is_mapping_receiver()` only ever matched `vars(rec)`/`X.__dict__`
directly at the point it inspects an expression -- `fields = vars(rec);
fields["bases"]` / `fields = rec.__dict__; fields.get("bases")` were both
invisible once the mapping was stored in an intermediate variable first,
the same "no alias tracking" gap this module's other alias helpers
already close for `getattr`/`vars`/`attrgetter` themselves, unclosed here
for their own *result*. Fixed with a new `_mapping_receiver_aliases()`
(the identical name-only, fixed-point assignment-chain pattern every
other alias helper in this module already uses), consulted by
`_is_mapping_receiver()`'s new final branch, gated on `_shadowed()` the
same way the direct `vars(rec)` form already is.

**The sibling-module split (Codex/CodeRabbit finding notwithstanding --
this one was self-imposed, per the prior round's own note, once the two
fixes above pushed the file to 1975 of 2000 lines, only 25 short of the
hard cap).** `scripts/fact_field_readers_scope.py` now holds
`_enclosing_qualnames`, `_parent_map`, `_TRANSPARENT_EXPR_WRAPPER_TYPES`,
`_outermost_containing_expr`, `_locally_bound_names`, and `_lexical_
function_parents` -- a mechanical extraction, not a redesign, mirroring
`fact_detector_misuse_scope.py`'s own identical split from the sibling
gate: every function moved unchanged, as one contiguous block, with a
matching sys.path guard (`fact_field_readers.py` importing the sibling
module whether run directly or loaded as `scripts.fact_field_readers` by
a test that never imports `check_ai_readiness.py` first) verified by
running `tests/test_fact_field_readers_wrapper_scoping.py` in isolation,
not just as part of the full suite -- the exact scenario a missing guard
would silently pass in a full run and fail only in isolation. Registered
in `scripts/CLAUDE.md`'s Inventory table.

Verified against both reported repros, a positive control for each (a
genuinely unrelated lambda/an unrelated dict alias must still be
caught/stay unflagged), a chained-mapping-alias variant, and negative
controls for shadowing and a non-bridged key, via direct AST reproduction
before writing tests -- including confirming the closure-shadow case
(`def outer(getattr): return (lambda rec: getattr(rec, "bases"))(None)`)
and a nested-lambda shadow both still resolve correctly with the new
ancestor-walk check running ahead of the pre-existing qualname-based one.
Zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_field_readers.py` back down to 1615 lines after the split (well
under the 2000-line hard cap, real headroom restored) plus the new 410-line
`fact_field_readers_scope.py`. New tests:
`TestLambdaParametersShadowDynamicReaders` and
`TestMappingReceiverAliasesResolveThroughLocalNames` in `tests/
test_fact_field_readers_wrapper_scoping.py`.

**Two more findings from the next review round, plus a second test-file
split once the second push crossed the 1200-line cap outright.** (1)
Neither the mapping-`.get()` branch nor the subscript branch matches
`vars(rec).__getitem__("bases")` (the explicit dunder-method spelling of
the identical subscript read) or `operator.getitem(vars(rec), "bases")`
(the standard-library callable spelling) -- both read the exact same
normalized legacy value already caught elsewhere. Fixed with two new
sibling branches: a `__getitem__` call on a mapping receiver, and a
`getitem` call through a real `operator` module alias (`operator_names`,
already resolved by `_operator_attrgetter_aliases()` for `attrgetter`'s
own qualified form -- reused as-is, not re-derived), gated on `_shadowed()`
the same way every other module-qualified call in this file already is.
(2) Every alias-collection helper in this module (`_builtins_getattr_
aliases`, `_operator_attrgetter_aliases`, `_unbound_getattribute_receiver_
aliases`, `_builtins_symbol_aliases`, `_mapping_receiver_aliases`) is
deliberately name-only and whole-tree, with no notion of *which* scope a
given alias was actually recognized in -- and `_locally_bound_names()`'s
own recognized-import carve-out (the mechanism that keeps a genuine alias
import like `from operator import attrgetter as ag` from being wrongly
treated as a shadow of itself) simply *omits* such an import from its
`bound` dict entirely, rather than recording anywhere that it was
recognized. `_shadowed()`'s outward closure walk had nothing to stop it
at the scope the alias was actually resolved in, so it kept walking past
that scope to search an *enclosing* one -- and if that enclosing scope
happened to bind the same bare name to something completely unrelated
(`from helper import ag` at module scope, an ordinary, unrecognized
import), the walk wrongly treated that unrelated binding as a shadow of
the genuinely-resolved inner alias. `from helper import ag` at module
scope, `from operator import attrgetter as ag` inside `f`, `ag("bases")
(rec)` inside `f` -- a real field read -- was invisible. Rather than
threading real per-scope alias resolution through all five of those
name-collection helpers (the kind of redesign `fact_detector_misuse.py`'s
own `_imported_fact_aliases()` docstring already declined for an
analogous reason -- see its "known gap" entry earlier in this doc),
`_locally_bound_names()` now returns a *second* dict alongside its
existing one: which names were recognized as a genuine alias source, per
scope, rather than discarding that information. `_shadowed()`'s walk now
checks this second dict at each scope it passes through and returns
`False` (unshadowed) the moment it finds the name recognized there,
before ever reaching an enclosing scope's own (potentially unrelated)
binding. This is a real, bounded fix rather than the declined redesign,
because it reuses the exact closure-walk mechanism `_shadowed()` already
has -- the only change is giving it one more signal to stop on, not
teaching every alias helper to understand scope.

Verified against both reported repros, an aliased-import variant of the
`operator.getitem` fix, and negative controls for each (a non-mapping
`__getitem__` receiver; a shadowed `operator` parameter; an unrelated
outer import with no inner recognized re-import, confirming the fix
doesn't widen recognition, only stops the walk early once a real alias is
found; a genuine parameter shadow, confirming ordinary shadowing is
unaffected; a closure through a real recognized alias from an *enclosing*
scope with no re-import of its own, confirming the pre-existing
closure-walk behavior survives) via direct AST reproduction before
writing tests. Adding these tests pushed `tests/
test_fact_field_readers_wrapper_scoping.py` to 1219 of its own 1200-line
cap -- over it outright -- so its tail (the five most recent test
classes: augmented assignment through mapping receivers, lambda parameter
shadowing, mapping-receiver aliases, explicit mapping-item readers,
per-scope dynamic-reader alias resolution) was split into a new sibling
file, `tests/test_fact_field_readers_later_fixes.py`, mirroring the
`test_fact_detector_misuse_alias_edge_cases.py` precedent on the sibling
gate -- mechanical extraction, every class moved unchanged, verified both
in combination and in isolation. Zero existing hits, `mypy`/`ruff` both
stayed clean, `fact_field_readers.py` at 1662 lines,
`fact_field_readers_scope.py` at 439 lines, the wrapper-scoping test file
back down to 936 lines, the new later-fixes test file at 315 lines -- all
well under their respective caps. New tests:
`TestExplicitMappingItemReaders` and `TestDynamicReaderAliasesResolvePer
LexicalScope`, both now in `tests/test_fact_field_readers_later_fixes.py`.

**One more finding from the next review round, on the same `operator.
getitem` shape just landed.** The new `getitem` call-matching branch
required an `ast.Attribute` callee (`operator.getitem(...)` through a
resolved `operator` module alias) -- `_operator_attrgetter_aliases()`
resolves `getitem`'s *own* bare-name import alias family nowhere at all,
unlike `attrgetter`, which already gets the identical import-seeded/
chained/qualified resolution via `attrgetter_names`. `from operator
import getitem as gi; gi(vars(rec), "bases")` was invisible. Fixed by
widening `_operator_attrgetter_aliases()` to also return a third set,
`getitem_names`, resolved by literally duplicating `attrgetter_names`'s
own three-stage mechanism (import-seeded from `from operator import
getitem [as X]`, a plain-assignment chain, and a qualified `X.getitem`
assignment once `operator_names` is known) against a separate `getitem`-
tagged qualified-candidate list, sharing this same function's
`operator_names`/`assign_candidates` collection so the two families can't
independently drift on what counts as a resolved `operator` alias. A new
sibling call-matching branch (bare `ast.Name` callee, `node.func.id in
getitem_names`) was added alongside the existing qualified-form branch.

**A second, self-found gap surfaced while first verifying this fix
empirically, before any external review flagged it: `_locally_bound_
names()`'s recognized-import carve-out had no entry for `from operator
import getitem`, unlike its sibling `attrgetter`.** Without that entry,
the new alias import was recorded as an *ordinary* local binding rather
than a recognized alias source -- making `_shadowed()` see the import
itself as shadowing its own later use, the exact inversion that carve-out
exists to prevent (the same class of bug the `vars` alias round earlier
in this file's own history already hit and fixed for an identical
reason). Caught before this even reached review, since the very first
empirical check of the direct, unaliased import form returned no site at
all -- confirming the value of reproducing every case via `python3 -c`
*before* writing tests, not just the one the review comment names. Fixed
by adding `"getitem"` alongside `"attrgetter"` in that carve-out's
recognized-name set.

Verified against the reported repro, an unaliased variant, a chained-
alias variant, and the qualified-assignment form (`gi = operator.
getitem`), plus negative controls (an unrelated local function with no
import; a shadowing parameter) -- all via direct AST reproduction before
writing tests, and re-verified after the carve-out fix confirmed both the
previously-broken direct-import forms and the previously-working chained/
qualified forms all resolve correctly together. Still zero existing hits,
`mypy`/`ruff` both stayed clean, `fact_field_readers.py` at 1720 lines
(well under the 2000-line hard cap). New tests:
`TestGetitemImportAliasesResolveTheBareCallableForm` in `tests/
test_fact_field_readers_later_fixes.py` (403 lines, well under its own
1200-line cap).

**Two more findings from the next review round.** (1) None of the alias
collectors ever recognized `ast.NamedExpr` -- only `ast.Assign`/
`ast.AnnAssign` -- so `(read := getattr)(rec, "bases")` and `(fields :=
vars(rec))["bases"]` were both invisible. Fixing this needed two separate
changes, not one, since the walrus repro packs two distinct gaps into one
expression: the *alias* itself (`read`, `fields`, real bindings a later,
ordinary reference should resolve through) needed a `NamedExpr` branch in
every alias-collecting function's own `ast.Assign`/`ast.AnnAssign` walk
(`_imported_class_aliases`, `_builtins_getattr_aliases`,
`_builtins_symbol_aliases`, `_unbound_getattribute_receiver_aliases`,
`_unbound_getattribute_method_aliases`, `_mapping_receiver_aliases`,
`_operator_attrgetter_aliases` -- all seven, applied mechanically since
every one of them shares the identical structural gap, not just the two
functions the finding itself named) -- and, separately, the walrus used
*directly* as the call's own callee or mapping receiver (as both reported
repros actually are) reads the field right there, in the very expression
that introduces the alias, which no amount of alias-table lookup can
catch since there is no later reference to look up. Fixed with a small,
targeted unwrap at the two sites the finding actually named: `_is_mapping_
receiver()` now unwraps a `NamedExpr` to its own `.value` before any of
its existing checks run (composing for free with every shape it already
recognizes), and the `getattr`-call-matching branch gained a third
alternative recognizing `node.func` as a `NamedExpr` whose own `.value` is
a known `getattr` name. (2) `_locally_bound_names()` modeled only a
parameter, a `def`/`class` name, and an import as a real local binding --
a `for` target, a `with ... as` target, an `except ... as` name, a
comprehension's own `for` target, and a `match` capture were all invisible
to it, so `for getattr in funcs: return getattr(rec, "bases")` (an
ordinary, unrelated loop variable reusing the builtin-looking name) was
still unconditionally flagged as a real read -- a false positive on
genuinely valid code, not a missed misuse. Fixed as one generalized
shadowing class, per the finding's own suggestion, rather than five
hand-rolled special cases: `_target_bound_names()` extracts every plain
name a `for`/`with` target binds (recursing through `Tuple`/`List`/
`Starred` nesting), and `_match_pattern_captures()` walks a `match`
pattern's own subtree for every `MatchAs`/`MatchStar`/`MatchMapping`
capture regardless of nesting depth. None of these five forms introduces
its own new scope (a `for`/`with`/`except`/`match` binds directly into
whatever function already contains it, and this module's own line-based
scope model already resolves a comprehension's `elt` to its enclosing
*function*, matching its established coarser granularity) -- so every
binding is recorded against the current `binding_scope` directly, the
same target a parameter already uses.

Verified against both reported repros for finding (1) plus a later-use
(non-inline) variant, a shadowed-parameter negative control, and an
unrelated-builtin negative control; and all five reported binding shapes
for finding (2) (`for`, tuple-unpacking `for`, `with ... as`,
`except ... as`, a comprehension target, a bare match capture, an
as-pattern match capture, and a capture nested inside a class pattern),
plus a positive control confirming an unrelated `for` loop doesn't
suppress a real read elsewhere in the same function -- all via direct AST
reproduction before writing tests. Still zero existing hits, `mypy`/
`ruff` both stayed clean, `fact_field_readers.py` at 1765 lines and
`fact_field_readers_scope.py` at 538 lines (both well under the 2000-line
hard cap). New tests: `TestNamedExprAliasesAndInlineCallsAreRecognized`
and `TestLexicalBindingFormsAreTreatedAsShadows`, both appended to `tests/
test_fact_field_readers_later_fixes.py` (558 lines, well under its own
1200-line cap).

**Two more findings from the next review round, plus one declined as a
re-raise of an already-decided, documented tradeoff.** (1) The lexical-
binding-forms fix's own comprehension-target handling (previous round)
was a real regression, caught by review: it recorded a comprehension's
`for` target against `_locally_bound_names()`'s coarser, function-only
qualname model -- correct for `for`/`with`/`except`/`match` (none of
which are block-scoped in Python), but a comprehension genuinely *does*
introduce its own new scope, so that recording shadowed every call
anywhere later in the *whole enclosing function*, not just calls
genuinely inside the comprehension. `[x for getattr in funcs]` followed
by an unrelated, later `getattr(rec, "bases")` in the same function was
wrongly suppressed. Fixed by reverting that one binding form out of
`_locally_bound_names()` entirely and handling it instead in
`_shadowed()`'s own real AST-ancestor walk -- the identical mechanism
already used for a `lambda` parameter's identical "not a scope this
module's coarser qualname model tracks" shape -- so a comprehension
target shadows exactly the calls nested inside it (`elt`, filters, later
generators), never anything outside it. The *outermost* generator's own
iterable is the one exception (mirroring `fact_detector_misuse_scope.py`'s
identical carve-out for the same construct): it evaluates in the scope
enclosing the comprehension, before the comprehension's own target
exists, so `[x for getattr in getattr(rec, "bases")]` must still be
flagged. Implementing this exactly needed one more structural fact about
the AST than the analogous fix in the sibling module: a comprehension's
`generators[0]` holds the `ast.comprehension` *clause* object, not its
`.iter` directly, so the ancestor walk tracks which clause object it just
ascended through (`outermost_iter_clause`) across the next hop, rather
than comparing the immediate child against `.iter` at the point the
`ListComp`/etc. itself is reached -- a first attempt compared against
`generators[0].iter` directly and silently never matched, since the
ancestor walk's immediate child at that point is always the clause
object. (2) A lambda's own default value expression evaluates at lambda-
*creation* time, in the enclosing scope, before the lambda's own
parameters exist at all -- the identical def-time-vs-body-time
distinction `_enclosing_qualnames`'s own default/annotation handling
already draws for a named `def` -- but `_shadowed()`'s lambda-ancestor
check unconditionally treated any call reached through a `Lambda`
ancestor as shadowed by its parameters, regardless of whether the call
came from the lambda's `body` or its `args` (default values). `lambda
getattr=getattr(rec, "bases"): getattr` read the real builtin in its
default, but was wrongly treated as shadowed. Fixed by checking `child is
node.body` at the point the walk reaches the `Lambda` ancestor -- exact
by construction (true only on the hop ascending directly out of the body
subtree, however deeply nested; false for every hop coming from `args`
instead) -- and only checking the lambda's own parameters when that holds.

(3) **Declined, citing an already-documented rationale rather than
re-implementing.** `_builtins_getattr_aliases()`'s own plain-assignment
alias resolution (`read = getattr`) is deliberately whole-tree/module-
wide, not per-function -- its own docstring already states this
explicitly ("Whole-tree, matching `_imported_class_aliases`'s own scope
for the identical reason"), and `_imported_class_aliases()`'s own
docstring gives the reason: "every mechanism here is almost always module
level, and scanning the whole tree is the same over-approximating-is-safe
stance this module already takes elsewhere." The finding reproduces
exactly the false-positive shape that stance already, deliberately
accepts: two unrelated functions each assigning the identical local name
`read` to two different values (`read = getattr` in one, `read = helper`
in another) both have their own `read(rec, "bases")` call flagged, since
the alias name is resolved once, globally. This is the identical class of
question already decided (and declined, twice, with the identical
citation) earlier in this same PR's review history for
`_imported_fact_aliases`'s own module-wide import-alias resolution (see
that thread's own "convergence note") -- a correct fix needs the same
kind of per-scope threading through the fixed-point alias resolution that
was already judged "a real, if narrow, redesign... not a follow-up to the
last one" there, not a bounded extension of an established mechanism.
Documented as a known, pre-existing, deliberately-accepted tradeoff
rather than re-litigated.

Verified against both reported repros for findings (1)/(2), a positive
control confirming each fix's *intended* shadowing case still works
(comprehension elt/filter/non-outermost-generator shadowing; a lambda
body genuinely shadowed by its own parameter), and the outermost-
generator-iterable exception itself for both a list comprehension and a
set comprehension, all via direct AST reproduction before writing tests.
Still zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_field_readers.py` at 1829 lines and `fact_field_readers_scope.py`
at 556 lines (both well under the 2000-line hard cap). New tests:
`TestComprehensionTargetShadowsOnlyWithinTheComprehension` and
`TestLambdaDefaultsEvaluateBeforeParameterShadows`, both appended to
`tests/test_fact_field_readers_later_fixes.py` (660 lines, well under its
own 1200-line cap).

**Two more findings from the next review round: one fixed, a bounded
extension of an established mechanism; one declined as needing a real
redesign rather than a bounded fix.** (1) `dict.__getitem__(vars(rec),
"bases")` -- the *unbound*-method spelling of the bound
`vars(rec).__getitem__("bases")` form already recognized, the identical
relationship `object.__getattribute__(rec, "bases")` already has to
`rec.__getattribute__("bases")` elsewhere in this module -- was invisible.
Fixed by resolving `dict`'s own alias family through `_builtins_symbol_
aliases()`'s already-generic mechanism (the same one `vars` itself already
uses -- `dict_names = _builtins_symbol_aliases(tree, "dict",
builtins_names)`, no new collector needed, since `dict` is a real,
always-in-scope builtin in the identical "no import required" category
`getattr` itself is in) and adding one new call-matching branch mirroring
the bound form's own shape, with the receiver check against `dict_names`
instead of `_is_mapping_receiver()`. **Proactively verified every sibling
alias form before writing tests, per this file's own established
discipline (a prior round's own self-found regression) rather than only
the one reported repro** -- the aliased-import form (`from builtins import
dict as D; D.__getitem__(...)`) initially produced no site at all, traced
to the identical class of bug caught earlier in this same file's history:
`_locally_bound_names()`'s recognized-import carve-out had no entry for
`"dict"` alongside its existing `"getattr"`/`"object"`/`"type"`/`"vars"`
tuple, so the import itself was recorded as an ordinary local binding
rather than a recognized alias source, making `_shadowed()` see the
import as shadowing its own later use. Fixed by adding `"dict"` to that
tuple, then re-verified every alias shape together (bare import, aliased
import, plain-assignment chain, qualified `builtins.dict`) rather than
re-testing only the one that had been broken.

(2) **Declined, documented as a known gap needing real redesign rather
than attempted under review pressure.** `class C: def getattr(self,
name): ...; value = getattr(rec, "bases")` -- a class-body-level call
(not inside a method) genuinely does see an earlier same-class-body
binding via ordinary sequential class-body execution, but
`_locally_bound_names()` passes `binding_scope=None` when descending into
a `ClassDef`, discarding every binding made anywhere in the class body,
not just a method's own name -- so this reads as an unshadowed real
`getattr` call and is wrongly flagged. Investigated a same-round fix and
found it structurally unsound, not merely inconvenient: this module's
`_enclosing_qualnames()` gives class-body-level code no distinct qualname
of its own at all (it inherits whichever qualname encloses the `class`
statement, confirmed directly -- the reported repro's own call resolved
to qualname `"<module>"`), which is exactly why `binding_scope=None` was
chosen for class bodies in the first place (a documented, deliberate
fix, see `_locally_bound_names()`'s own "That 'directly, syntactically
contains it' scope is NOT simply the nearest enclosing function" section):
recording a class-body binding (in particular a method's own name)
against the *same* qualname that also covers code genuinely *outside* and
*after* the class statement would leak it there too -- `class C: def
getattr(self, name): ...` followed by a real, unrelated
`getattr(rec, "bases")` **after** the class definition, at the same outer
scope, would then be wrongly excluded, reintroducing a worse version of
the exact bug that `None` was chosen to prevent. A correct fix needs a
genuinely distinct, *position-based* class-body scope (mirroring how a
comprehension's own scope was just handled above via `_shadowed()`'s
AST-ancestor walk rather than the qualname model) -- and, unlike the
comprehension case, get it *order-sensitive* too, since class-body
execution is sequential top-to-bottom, ordinary code (a call before the
shadowing `def` must still see the real builtin) -- exactly the
"materially larger change than the guard conditions this module has
added incrementally so far" `_locally_bound_names()`'s own docstring
already names as a known, deliberately-unattempted gap for a
structurally identical reordering problem. Recorded here rather than
attempted as a reactive same-round patch.

Verified against the reported repro, the aliased/bare-import/plain-
assignment/qualified alias forms, and negative controls (a `dict`
parameter shadow; an unrelated mapping-receiver argument) for finding
(1), via direct AST reproduction before writing tests. Still zero
existing hits, `mypy`/`ruff` both stayed clean, `fact_field_readers.py`
at 1859 lines and `fact_field_readers_scope.py` at 558 lines (both well
under the 2000-line hard cap). New tests:
`TestUnboundDictGetitemMappingReaders`, appended to `tests/
test_fact_field_readers_later_fixes.py` (728 lines, well under its own
1200-line cap).

**Still not landed**: no detector (`diff_layout.py`/`diff_types.py`/
`diff_param_qualifiers.py`/the reader set the check above now tracks
precisely) has actually been migrated to read `.status` — the check above
only *guards* the existing sites against a new, unreviewed one
joining them; it does not change what any of them do.
Migrating a detector now would add real complexity for zero behavior
change until every producer's own construction is at least this explicit
— landed as of the second slice for the five fields this phase scoped —
deferred deliberately, not silently, per this plan's own "vertical slice,
not flag day" discipline: each slice is a primitive the rest of Phase 0
builds on, landed and tested on its own rather than held until every
consumer migrates too.

**Three more `fact-field-readers` findings (same review round), all
bounded extensions of already-shipped alias/constructor-recognition
mechanisms — fixed, one after chasing a real self-inflicted regression
this same class of bug had already produced twice.** (1) `dict.get(vars
(rec), "bases")` — the unbound-method spelling of the already-recognized
bound `vars(rec).get("bases")` form, the identical relationship the
already-shipped unbound `dict.__getitem__` branch has to its own bound
sibling. A straightforward mirror of that branch, reusing `dict_names`
unchanged. (2) `operator.attrgetter("bases.foo")` — a dotted attrgetter
path. The existing docstring's blanket "no type inference" framing for a
dotted argument was overbroad: the *first* dotted component is read
directly off the literal string (`field.partition(".")`), no inference
needed, while a *later* component genuinely would need to know the
runtime type of what the first component reads — so only the first
component is matched, keeping the "no type inference" limit exactly
where it actually applies. Verified a multi-argument call
(`attrgetter("size_bits", "bases.foo")`) still reports only the real
`FACT_BRIDGED_ATTRS` hit, and that a bridged name appearing in a
*non-first* component (`attrgetter("foo.bases")`) stays correctly out of
scope. (3) `operator.itemgetter("bases")(vars(rec))` — the `attrgetter`-
shaped constructor spelling of a subscript read, previously entirely
untracked (no `itemgetter_names` alias family existed at all). Added by
widening `_operator_attrgetter_aliases()`'s return to a 4-tuple
(`itemgetter_names` resolved the identical import-seeded/chained/
qualified way `attrgetter_names`/`getitem_names` already are) and a new
`_is_itemgetter_constructor_call()`/`_itemgetter_matched_name()` pair
mirroring the attrgetter versions — but with one deliberate difference:
unlike `attrgetter`'s "match wherever constructed, regardless of how the
getter is later used" stance, `itemgetter` is matched only at the
*outer*, immediate call, gated on `_is_mapping_receiver()` the same way
every other subscript-reading form here already is. The reason the two
forms can't share one stance: `attrgetter("bases")(x)` reads `x.bases`
for *any* `x`, so there's no narrower receiver shape to require, while
`itemgetter("bases")(x)` reads `x["bases"]`, exactly as legitimate for an
arbitrary unrelated mapping as for an instance's own `vars()`/
`__dict__` — an ungated constructor-wide match would have been far
noisier than its `dict.get`/`operator.getitem` siblings.

**A real self-inflicted regression, caught by proactively verifying every
sibling alias form before writing tests rather than only the one repro —
the third instance of this exact bug class this session (a new alias
family missing its own entry in `_locally_bound_names()`'s recognized-
import carve-out tuple, `fact_field_readers_scope.py`).** The bare-name
forms (`from operator import itemgetter`, and its own `as` alias) both
returned `[]` — a genuine false negative, not merely an incomplete
positive check — because the carve-out tuple gating which imports are
treated as a real alias *source* rather than an ordinary shadowable local
binding still only listed `("attrgetter", "getitem")` for the `operator`
module; `itemgetter` was never added alongside them. Without the
carve-out entry, `_locally_bound_names()` recorded the bare `itemgetter`
import as an ordinary local binding, and `_shadowed()` then read it back
as shadowing itself — every bare-name itemgetter call was wrongly
excluded. Root-caused (not guessed) by adding temporary trace prints at
each condition in the new matching branch, isolating the exact point
where `_shadowed(...)` returned `True` for an unshadowed name, rather
than assuming which of the several new conditions was at fault. Fixed by
adding `itemgetter` to the tuple (now `("attrgetter", "getitem",
"itemgetter")`) and updating the docstring paragraph that enumerates the
carved-out spellings to match. Re-verified every sibling form afterward
(bare, aliased, `operator`-qualified, an aliased `operator` module, a
plain-assignment alias of the `operator` module, a qualified-assignment
alias) rather than stopping at the one repro that first surfaced the gap.

**A file-size consequence, handled the established way rather than
reactively.** Adding the itemgetter machinery pushed `fact_field_
readers.py` to 2019 lines — over the AI-readiness gate's 2000-line hard
cap. Rather than trim content, the `_operator_attrgetter_aliases()`/
`_is_attrgetter_constructor_call()`/`_attrgetter_matched_name()`/
`_is_itemgetter_constructor_call()`/`_itemgetter_matched_name()` block
(self-contained — no dependency on anything else in the module besides
bare `ast`) was moved to `fact_field_readers_scope.py` as a second,
later block, exactly mirroring how that sibling module's *first* block
was split out originally; the module's own docstring was extended to
record the second move. `fact_field_readers.py` dropped to 1701 lines,
`fact_field_readers_scope.py` grew to 896 — both comfortably under the
cap. Verified with the full existing 176-test suite plus 33 new
positive/negative-control tests (three new classes —
`TestUnboundDictGetMappingReaders`, `TestAttrgetterDottedPaths`,
`TestItemgetterMappingReaders` — appended to `tests/test_fact_field_
readers_later_fixes.py`, 974 lines, well under its own 1200-line cap),
`mypy`/`ruff format`/`ruff check` all clean on both touched scripts,
`check_architecture.py` and `check_ai_readiness.py` both at 0 errors, and
`check_docs_contract.py` unchanged at its two pre-existing warnings.

**One more finding on the same round's itemgetter fix: only a single
constructor argument was ever inspected.** `operator.itemgetter("foo",
"bases")(vars(rec))` returns a getter that reads *both* requested keys as
a tuple — real, documented `itemgetter` behavior, and the identical
multi-key shape `attrgetter`'s own recognition already handles — but the
new itemgetter branch's own `len(node.func.args) == 1` guard (and
`_is_itemgetter_constructor_call()`'s matching `len(node.args) == 1`)
silently missed a bridged key riding alongside an unrelated one. Fixed by
widening `_is_itemgetter_constructor_call()` to `len(node.args) >= 1`
(mirroring `_is_attrgetter_constructor_call()`'s identical bound) and
restructuring the itemgetter branch from the single-attribute elif chain
into its own top-level case — the same reason the `attrgetter` branch
itself is a top-level case rather than folded into that chain: it now
iterates every constructor argument and reports each bridged, literal
string-constant key independently, rather than fitting into a chain
shaped for exactly one `attr` per matched node.

Verified against the reported multi-key repro, both keys bridged
(reporting both independently), the bare (non-qualified) spelling,
a no-bridged-keys negative control, a non-mapping-receiver negative
control, and a shadowed-`operator`-parameter negative control, via direct
AST reproduction before writing tests. The full existing single-key
suite (dict.get, dotted attrgetter, single-key itemgetter, and every
other already-shipped reader form) re-verified unaffected. Still zero
existing hits, `mypy`/`ruff` both stayed clean, `fact_field_readers.py`
at 1733 lines and `fact_field_readers_scope.py` at 901 lines (both well
under the 2000-line hard cap). New tests:
`test_inspects_every_key_in_a_multi_key_getter`,
`test_reports_each_bridged_key_independently`,
`test_multi_key_bare_spelling_still_recognized`,
`test_ignores_a_multi_key_getter_with_no_bridged_keys`, appended to
`TestItemgetterMappingReaders` in `tests/test_fact_field_readers_later_
fixes.py` (1014 lines, well under its own 1200-line cap).

**Two more `fact-field-readers` findings (next review round), both real,
both bounded extensions of already-shipped mechanisms.** (1) `_shadowed()`'s
comprehension-generator handling blanket-checked *every* generator's own
target against a call reached through any generator's iterable -- correct
for the outermost generator (already excluded entirely, since it evaluates
before any target exists) but wrong for a *non*-outermost one: `[x for x in
xs for getattr in getattr(rec, "bases")]` -- the second generator's own
iterable evaluates *before that same generator's own target* is bound, the
identical binding-order rule the outermost generator's iterable already
gets, just one level less special-cased. Fixed by generalizing the single
`outermost_iter_clause` tracked during the ascent into
`comprehension_gen_clause`/`comprehension_via_iter`, identifying *which*
generator (by index, via `node.generators.index(...)`) and *which part* of
it (`.iter`, evaluating before that generator's own target; `.ifs`,
evaluating after) the call was reached through, then shadowing only
against the generators that are actually already bound at that point: none
for the outermost iterable, generators strictly before the current one for
a non-outermost iterable, the current generator inclusive for a filter, and
every generator for the comprehension's own final `elt`/`key`/`value`
(reached with no intervening generator clause at all). (2) `operator.
itemgetter("bases")(vars(rec))` was recognized only at the point of
immediate construction-and-call -- `get = operator.itemgetter("bases");
get(vars(rec))`, storing the constructed getter in a variable first before
calling it, is ordinary, common Python that was silently missed. Fixed
with a new `_itemgetter_alias_keys()`, tracking every local name bound via
a plain `ast.Assign` to the *result* of an itemgetter-constructor call,
mapped to that call's own literal keys -- deliberately not chased through
a *further* plain-name alias (`get2 = get`, the same "no type inference
beyond one hop" limit already accepted elsewhere in this module) and
dropped entirely for a name assigned more than once anywhere in the file
(ambiguous by the second assignment, so guessing which one a later call
used would risk fabricating a false positive rather than merely missing a
true one).

Both pre-existing tests that pinned the exact gaps these fixes close were
corrected rather than left pinning a now-fixed bug -- the fourth and fifth
instances of this exact pattern this plan has now recorded.
`test_still_shadows_within_a_non_outermost_generators_iterable` used a
repro where the shadowing target was the call's *own* generator's target
(self-shadowing, the bug), not an *earlier* generator's target (the
positive control its own docstring actually claimed to be testing) --
corrected to the earlier-target repro, with a new sibling test class,
`TestComprehensionGeneratorBindingOrder`, exhaustively covering all four
binding-order cases (a generator's own iterable, its own filter, the final
element expression, and a third generator confirming the rule generalizes
past two). `test_does_not_match_a_getter_constructed_but_not_immediately_
called` asserted `== []` for exactly the aliased-itemgetter repro this
round's second fix now correctly detects -- renamed and rewritten to
assert the real, single hit, with a new sibling test pinning the
one-hop-only chased-no-further limit explicitly rather than leaving it
implicit.

Verified against both reported repros, every binding-order permutation
listed above, the reassigned-variable and chained-second-name negative
controls for the itemgetter alias case, and every existing sibling form
(dict.get, dotted attrgetter, direct-call itemgetter, multi-key itemgetter)
re-verified unaffected, all via direct AST reproduction before writing
tests. Still zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_field_readers.py` at 1809 lines and `fact_field_readers_scope.py` at
966 lines (both well under the 2000-line hard cap). New tests:
`TestComprehensionGeneratorBindingOrder` (4 tests) and
`test_detects_a_getter_assigned_to_a_variable_before_being_called`/
`test_does_not_chase_an_aliased_getter_through_a_second_name`/
`test_ignores_a_reassigned_getter_variable` in
`TestItemgetterMappingReaders`, all in `tests/test_fact_field_readers_
later_fixes.py` (1113 lines, still under its own 1200-line cap).

A follow-up Codex review on the same commit found `_itemgetter_alias_keys()`
walked only `ast.Assign` -- `get: object = operator.itemgetter("bases")`
(an annotated assignment) and `(get := operator.itemgetter("bases"))`
(a named expression/walrus, bound and referenced later, not the immediate-
self-call shape) construct and bind the identical getter through a
different Python binding statement, and both were silently missed:
`unmigrated_fact_reader_sites()` returned no site for either form, so the
ERROR-level gate could be bypassed by simply spelling the same alias
through `AnnAssign`/`NamedExpr` instead of `Assign`. Fixed by generalizing
`_itemgetter_alias_keys()`'s walk to the identical three-branch shape
`_mapping_receiver_aliases()` (`fact_field_readers.py`) already uses for
this exact purpose -- a shared `_record(target, value)` helper called from
`ast.Assign`, `ast.AnnAssign`, and `ast.NamedExpr` branches, preserving the
existing ambiguity rule (a name assigned more than once anywhere in the
tree is dropped entirely, regardless of which binding forms produced the
multiple assignments).

Verified against both reported repros (AnnAssign, NamedExpr-then-later-call),
a multi-key variant of each, the bare (non-qualified) `itemgetter` spelling
combined with each, the original plain-`ast.Assign` case as a regression
check, and the reassigned-variable and non-mapping-receiver negative
controls for both new forms -- all via direct AST reproduction before
writing tests. One further shape was checked and found out of scope for
this finding: `(get := operator.itemgetter("bases"))(vars(rec))`, an
immediate self-call on the walrus expression itself (no later reference to
`get` at all), stays undetected -- distinct from the reported "store then
call later" pattern the review actually named, and from the already-handled
`operator.itemgetter("bases")(vars(rec))` immediate-construction-and-call
form (whose `func` is the `Call` node itself, not a `NamedExpr` wrapping
one); left as an unreported, narrower residual rather than folded into this
fix's scope. Still zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_field_readers_scope.py` at 989 lines (well under the 2000-line hard
cap). New tests split into a dedicated sibling file,
`tests/test_fact_field_readers_itemgetter_binding_forms.py` (9 tests, two
classes: `TestItemgetterConstructorAliasedThroughAnnAssign`/
`TestItemgetterConstructorAliasedThroughNamedExpr`), rather than appended to
`test_fact_field_readers_later_fixes.py`, which had only ~87 lines of
headroom left under its own 1200-line cap.

**The "left as an unreported, narrower residual" shape above turned out not
to stay a residual: a follow-up Codex review round named it directly and it
turned out to be a bounded, mechanical extension of an already-established
sibling mechanism, not exotic indirection worth declining.**
`(get := operator.itemgetter("bases"))(vars(rec))` -- the outer call's own
`func` is the `ast.NamedExpr` itself, not a `Call`, so neither the
immediate-construction-and-call branch (which requires `node.func` to
literally be the constructor `Call`) nor the plain-Name alias branch (which
requires `node.func` to be a bare `Name`) ever matched, and
`unmigrated_fact_reader_sites()` returned no site -- letting a bridged-field
read bypass the ERROR-level gate. The review pointed at the exact right
precedent to mirror: this module already handles the identical shape for
`getattr` (`(read := getattr)(rec, "bases")`, checked against the walrus's
own `.value` -- what is actually being called -- not its `.target`). Fixed
with a new branch matching `isinstance(node.func, ast.NamedExpr) and
isinstance(node.func.value, ast.Call) and _is_itemgetter_constructor_call
(node.func.value, ...)`, inspecting every constructor argument the same
multi-key way the immediate-construction-and-call branch already does, and
gated by `_shadowed()` against `_itemgetter_matched_name(node.func.value)`
-- the same "is the `itemgetter`/`operator` name itself locally shadowed at
this point" check the immediate-call branch already applies, just against
the walrus's wrapped call rather than `node.func` directly.

Verified against the reported repro, a multi-key variant, the bare
(non-qualified) `itemgetter` spelling, a no-bridged-keys negative control, a
non-mapping-receiver negative control, a shadowed-`operator`-parameter
negative control, and every existing sibling form re-verified unaffected
(the walrus-then-later-call form, the plain-`ast.Assign`/`AnnAssign` forms,
the immediate-construction-and-call form with no assignment at all, the
sibling `getattr` walrus-callee mechanism, and the still-undetected
two-hop-alias-chain residual staying correctly out of scope), all via direct
AST reproduction before writing tests. Still zero existing hits, `mypy`/
`ruff` both stayed clean, `fact_field_readers.py` at 1858 lines (well under
the 2000-line hard cap). New tests: `TestItemgetterConstructedAndCalledThrough
AWalrusCallee` (6 tests), appended to
`tests/test_fact_field_readers_itemgetter_binding_forms.py` (235 lines,
plenty of headroom under its own 1200-line cap -- the natural home for this
fix, unlike the two files already tight on headroom).

**Separately, this same round brought PR #921's branch up to date with
`main`, closing a CI `architecture` step failure (`abicheck/contract_
evidence.py:86: model -> policy is forbidden`, a `compare -> model ->
policy -> compare` dependency cycle) that was never this PR's own doing.**
The branch was 53 commits behind `main`; `main` had already merged a fix
for the identical cycle (PR #931,
`fix/contract-evidence-model-policy-cycle`) before this round started, so
merging `main` in (a clean merge, no conflicts) was sufficient -- no
independent architecture fix was needed on this branch. Verified with
`python scripts/check_architecture.py` reporting `0 error(s)` both on the
merge commit and, separately, on `main` itself before merging.

**A further Codex review round read the gate's own error message as
promising a recognition mechanism that does not exist, and was declined
as a detection-logic change while accepted as a wording fix.** The
finding: "when migrated code first checks `rec.bases_fact.status` and
then reads `rec.bases`, `unmigrated_fact_reader_sites()` still returns
the legacy read... the gate needs to recognize an applicable
sibling-status guard before reporting the legacy read (or change the
contract and diagnostic to require reading the Fact value instead)."
Investigated both halves of that either/or separately. Recognizing "a
preceding, applicable sibling-status guard" correctly needs genuine
control-flow analysis this scan deliberately doesn't have (a plain,
position-blind AST walk, per this module's own docstring) --
what counts as "preceding" (same branch? any earlier line in the same
function? does it need to dominate every path to the read?), what
counts as an "applicable" check (equality against a specific
`FactStatus` member? a truthiness test? membership?), and whether the
guard's branch is even the one containing the read, are all genuine
design questions with no existing precedent to model after -- and
`grep -rn "_fact\.status\b" abicheck/` confirms zero real call sites in
the codebase exercise this pattern today, matching the plan doc's own
already-recorded "Still not landed: no detector... has actually been
migrated to read `.status`" note two entries above. Building real
control-flow recognition for a pattern nothing in the tree uses yet,
under review pressure, risks exactly the "attractive nuisance" this
plan's own established discipline warns against -- a heuristic that
could produce false negatives (masking a genuinely unguarded read that
merely happens to share a function with an unrelated status check
elsewhere) for a benefit no real caller currently needs.

The second half -- "change the contract and diagnostic" -- was the real,
actionable gap: the message's previous wording ("either migrate this
reader to check .status first, or add its stable key to
KNOWN_UNMIGRATED_READERS") is genuinely ambiguous between "replace the
legacy-field read with a `Fact[...]`-sibling read" (the actual intended
migration path, which naturally stops matching this scan's own
attribute-read pattern once done, needing no new recognition logic at
all) and "add a status check immediately before the still-present
legacy read" (the reading Codex's finding took, which this scan can
never honor without the unbuilt control-flow analysis above). Tightened
the message to state the "no control-flow analysis, a preceding check
does not exempt the read" contract explicitly, so a developer reading
it cannot come away expecting a recognition mechanism the scan doesn't
have. Verified the new wording against a real preceding-`.status`-check
repro (confirming it still, correctly, fires) and against the existing
end-to-end violation-reporting test, and confirmed no existing test
pinned the old wording (`grep`-checked across every `test_fact_field_
readers*.py` file) before changing it. Still zero existing hits,
`mypy`/`ruff` both stayed clean, `fact_field_readers.py` at 1863 lines
(well under the 2000-line hard cap). New tests split into a dedicated
small sibling file, `tests/test_fact_field_readers_status_check_
diagnostic.py` (2 tests), rather than appended to
`test_fact_field_readers.py`, which had only ~24 lines of headroom left
under its own 1200-line cap.

**A second static gate, closing a gap this plan's own generated code left
open — landed.** `abicheck/model/fact.py`'s own `Fact` class docstring
states, in the present tense: "A detector reads a `Fact[...]`-typed field
only by inspecting `.status` ... The guard against comparing two
`Fact[...]` values directly inside detector logic (rather than unwrapping
first) is a static check, not a runtime one — see
`scripts/check_ai_readiness.py`'s `fact-detector-misuse` check." No such
check existed when that sentence was written in the first slice — a
docstring describing a gate the codebase doesn't have yet is exactly the
kind of drift AGENTS.md's `adr-status-sync`/`generated-file-ownership`
checks exist to catch for other artifact kinds, just not one either of
them scans for here. `scripts/fact_detector_misuse.py` (registered as
`fact-detector-misuse`, mirroring `fact_field_readers.py`'s own
extraction) is that check: an AST scan for an `==`/`!=` comparison where
either side is a `<attr>_fact` field access or a
`Fact(...)`/`Fact.<classmethod>(...)` constructor call — `Fact[T]`
deliberately doesn't override `__eq__` (poisoning the *containing*
dataclass's own generated equality would be worse), so this comparison
doesn't raise; it silently falls back to structural dataclass equality
over `status`/`value`/`diagnostics` together, which can answer True or
False without ever checking whether either side is `PRESENT` — exactly
the ambiguity `Fact[T]` exists to make unrepresentable, reintroduced by a
different spelling. Verified empirically (by running the real scan
against the whole package) to have zero existing hits under `abicheck/`
today — the only real matches for this pattern anywhere in the repo are
in `tests/`, asserting a constructed `Fact` equals an expected one, which
is legitimate assertion code outside this check's `abicheck/`-only scope
— so this check ships with **no baseline at all**, unlike
`fact-field-readers`'s allowlist-and-shrink `KNOWN_UNMIGRATED_READERS`:
any match is an unconditional error. Tests: `tests/
test_fact_detector_misuse.py` (the real repository has zero violations;
the AST primitive's own contract — both attribute-pair and
constructor-call shapes detected for `==` and `!=`, a chained comparison's
each adjacent pair caught independently, `is`/`is not` and an unrelated
attribute/method name ignored; and end-to-end cases confirming the check
function fires on a new violation and stays silent on `.status`-based
unwrapping).

**A Codex review round found a real gap in the name-only matching, fixed
in the same PR.** The scan recognized `x.bases_fact == y.bases_fact`
directly but not the same misuse laundered through an ordinary local
variable: `old_fact = old.bases_fact` followed by `old_fact == new_fact`
has two bare `ast.Name` operands, and a single node can't answer whether
a name is Fact-typed without knowing which function it belongs to — the
identical scope question `fact_field_readers.py`'s own `qualname`-keyed
baseline already had to solve. Fixed with `_fact_aliases()`: a
per-function `dict[qualname, set[name]]` built from two name-only
sources, a simple single-target assignment from a recognized Fact-typed
expression, and a function parameter whose own annotation is `Fact[...]`
(or bare `Fact`) — both scoped to their own enclosing function so an
alias in one function can't leak into an unrelated same-named local in a
sibling. Deliberately conservative in the *over*-approximating direction
(an aliased name is trusted for the whole function, not narrowed to the
lines after its assignment) — a false positive here is far cheaper than
the false negative it closes, and this check has no control-flow
analysis to narrow it correctly anyway. Verified empirically to have zero
existing hits, so the check still has no baseline. New tests: aliasing
through a local assignment, two `Fact[...]`-annotated parameters compared
directly, an alias not leaking across sibling functions, and an ordinary
non-Fact local left alone.

**A second Codex round found the alias tracking itself stopped one hop
too early, fixed in the same PR.** `first = rec.bases_fact; second =
first; second == other` launders the misuse through a *second* ordinary
assignment: `second`'s own RHS is the bare `ast.Name` `first`, which
`_is_fact_typed_expr` doesn't recognize (only an attribute access or a
constructor call), so a single pass over assignments stopped at `first`
and never learned `second` was an alias too. Fixed by resolving the
per-function alias set to a fixed point: collect every simple
single-target assignment as a `(name, value)` candidate first, then
repeatedly add any candidate whose value is either directly Fact-typed
*or* is itself a `Name` already known as an alias in that same function,
until a pass adds nothing new — bounded by construction (finitely many
candidates, each pass adds at least one or the loop stops). New test:
`test_detects_a_comparison_through_a_chained_alias`.

**A third Codex round found two more real gaps, both fixed in the same
PR.** (1) `old_fact: Fact[list[str]] = old.bases_fact` is an
`ast.AnnAssign`, a distinct node type the candidate collection (`ast.
Assign`-only) never matched at all — the ordinary annotated-assignment
spelling bypassed the gate entirely. Fixed by collecting `AnnAssign` too:
its own annotation is an unconditional signal on its own (mirroring the
function-parameter case — a bare `old_fact: Fact[...]` with no value is
still Fact-typed), and when it isn't Fact-typed but a value is present,
the assignment still joins the ordinary fixed-point candidate pool. (2)
`fact = rec.bases_fact` in an outer function, then `def inner(): return
fact == other` — `inner`'s own qualname has no assignment establishing
`fact`, but it's a real, visible closure variable there; a lookup scoped
strictly to the exact qualname missed it. Fixed by processing qualnames
in shallowest-first order (by dot-count — a real approximation of "outer
scopes before inner ones", not exact Python scoping since a class body
isn't actually a closure scope for its methods, but the same
over-approximating-is-safe direction this whole module already takes)
and seeding each qualname's known-alias set with its lexical parent's
already-resolved set before running the fixed point over its own
candidates. A real bug surfaced while implementing this: a qualname with
no candidates or annotations of its own (like `inner` in that example)
was never added to the `dict`, so its own lookup silently saw nothing
rather than its parent's set — fixed by unioning in every qualname
`_enclosing_qualnames` actually produced, not just ones with a candidate.
Re-verified the existing sibling-non-leakage guarantee still holds (an
alias in one function must not leak into an unrelated, non-nested sibling
sharing a parameter name) with a dedicated test alongside the two new
ones. Verified empirically: still zero existing hits under `abicheck/`
today. New tests: an annotated local assignment, a bare annotated local
with no value, a closure over an outer alias, and the sibling-leakage
negative control restated for this fix.

**A fourth Codex round found the closure-inheritance fix itself was
wrong for one real shape, fixed in the same PR.** That fix derived a
qualname's "lexical parent" by string-splitting the dotted qualname
(`rsplit(".", 1)`) — correct for a plain nested function, but wrong for a
class *nested inside* a function: `fact = rec.bases_fact` in an outer
function, then `class C: def method(self): return fact == other` still
closes `method` over `fact` in real Python (a class body isn't a closure
scope, but the function wrapping it still is), yet the dotted qualname
`"f.C.method"` splits to `"f.C"` — a synthetic scope no real function
owns, so it was never itself processed or seeded, silently breaking the
chain there. Fixed with `_lexical_function_parents()`: walks the tree
directly, tracking each function's nearest *enclosing function*
separately from the dotted-name prefix (skipping over any intervening
class layer), giving the real Python closure-scope chain instead of one
reconstructed from a string that conflates class and function nesting.
Qualnames are now processed in order of true scope-nesting depth (walking
this parent map) rather than dot-count, so a parent is still always
resolved before a child consults it. Verified empirically: still zero
existing hits. New test:
`test_detects_a_comparison_through_a_closure_over_a_class_nested_method`.

**A CodeRabbit finding on the same round: an import alias of `Fact`
itself, not just the fields on `RecordType`/`Param`, was invisible too --
fixed in the same PR.** `from abicheck.model.fact import Fact as F` then
`F.present(a) == F.present(b)` is the identical misuse as `Fact.
present(a) == Fact.present(b)`, but `_is_fact_typed_expr`/`_is_fact_
typed_annotation` both hard-coded the literal bare name `"Fact"`. Fixed
with `_imported_fact_aliases()` -- collects every local name bound to
`Fact` via `from ... import Fact as F` (matched by imported name alone,
not by source module, the same name-only stance `fact_field_readers.
py`'s own import-alias helpers already take) -- threaded as a new
`fact_names` parameter through both functions and every call site.
Verified empirically: still zero existing hits. New tests: an aliased
constructor-call comparison, an aliased annotation comparison, and a
negative control for an unrelated import merely sharing the alias name.

**A fifth Codex round found the closure-inheritance fix itself produces a
real false *positive*, not a missed detection -- fixed in the same PR.**
Every prior round in this section closed a *false-negative* gap (misuse
that should have been flagged but wasn't); this one is the opposite
direction, which this module's usual "a false positive here is cheaper
than a false negative" stance does not cover -- a false positive means
rejecting valid code as a hard CI error. `fact = rec.bases_fact` in an
outer function, then `def inner(fact, other): return fact == other` --
`inner`'s own `fact` parameter is an ordinary, unrelated local that merely
reuses the name; Python's scoping makes it local to the whole inner
function (shadowing the outer alias throughout, not just after some
reassignment point), but the closure-inheritance fix's `known |=
aliases.get(parent, set())` unconditionally unioned the parent's alias set
into every child scope, with no way to exclude a name the child rebinds
itself. Fixed by tracking `locally_bound`: every name a function binds on
its own -- every parameter (Fact-typed or not) and every simple assignment
target, collected in the same walk that already builds `aliases` and
`candidates` -- and subtracting it from the inherited set before seeding:
`known |= aliases.get(parent, set()) - locally_bound.get(qualname,
set())`. Verified empirically: the shadowed-parameter case now correctly
reports no misuse, the real (non-shadowed) closure case from the fourth
round is unaffected, and the real repository still reports zero
violations. New tests:
`test_ignores_a_parameter_that_shadows_an_outer_fact_alias` and
`test_ignores_a_reassigned_local_that_shadows_an_outer_fact_alias` (the
latter pinning that the shadowing rule applies to a plain reassignment,
not just a parameter, and that it covers the whole function body, not
only the lines after the reassignment).

**Review convergence reached on this PR; two further findings documented
as known gaps rather than fixed (Codex review, both after the above
fix).** Posted as a PR comment: six review rounds on this file have each
closed a real, bounded gap in the same "name-only, no type inference"
alias/closure-resolution machinery; going forward, a further round
finding yet another indirection on the same "is this expression
Fact-typed" question is the same inherent no-type-inference residual
`fact_field_readers.py`'s own docstring already accepts, not a specific
miss worth chasing indefinitely -- documented in the module rather than
fixed, per this repo's own review-convergence guidance. Two findings
landed exactly in that category and are recorded in
`_imported_fact_aliases`'s own docstring rather than fixed here: (1) the
import-alias set this function builds is module-wide rather than scoped
to the function the `ImportFrom` sits inside, so a (highly unusual)
per-function `from ... import Fact as F` could leak its alias name into
an unrelated sibling binding of the same short name; (2) only a bare `from
... import Fact as F` is recognized -- a module-qualified constructor call
(`fact_model.Fact.present(...)`) is invisible, since the constructor-call
match assumes `func.value` is a bare `ast.Name`, not an arbitrary
`ast.Attribute` chain. Both would need real per-scope import tracking or
arbitrary-attribute-chain resolution respectively -- their own scoped
redesigns, not follow-ups to the fixes already in this file.

**A further Codex review round found two more real false *positives* on
the shadowing fix's own machinery -- both fixed, since a false positive
here (rejecting valid code) is never treated as a convergence-eligible
finding regardless of how many rounds precede it.** (1) `locally_bound`
only ever recorded a bare `ast.Name` assignment target and a function's
own parameters, so `fact, other = pair` (ordinary tuple-unpacking) inside
a nested function shadowing an outer `fact = rec.bases_fact` alias was
never recorded as a local binding -- the nested function's own `fact`
still read as the *outer* alias, flagging a valid `fact == other`. Fixed
with `_bound_names()`, a small recursive helper unpacking `ast.Tuple`/
`ast.List`/`ast.Starred` targets, wired into `locally_bound` for
`ast.Assign` (all targets, not just the single-`Name` case `candidates`
stays restricted to), plus the other real Python local-binding forms the
same review comment named: a `for`/`async for` loop target, a `with`/
`async with` `as` target, and an `except ... as name:` handler name.
(2) `_enclosing_qualnames`/`_lexical_function_parents` both keyed a
function purely by its dotted name, so two functions sharing one bare
name -- the shape a `@typing.overload`-decorated stub and its real
implementation share -- collapsed onto the same qualname key, and
`_fact_aliases` merged their alias/candidate data: a stub's `x:
Fact[int]` parameter leaked into a same-named real implementation's own,
unrelated `x` local, flagging a valid `x == other`. Fixed by folding each
`def`'s own line number into its qualname (`f"{prefix}{child.name}#
{child.lineno}"`) in both helpers identically -- safe here in a way it
would not be in `fact_field_readers.py`'s identical-*looking* helper: this
module's qualname is purely internal (`fact_equality_misuse_sites` returns
only `(lineno, col_offset)`, never a qualname-derived key), so there is no
external format to keep stable. Verified empirically: still zero existing
hits in the real repository, and a positive control confirms two
independent same-named functions are still each checked for real misuse
on their own (neither silently swallows the other's genuine violation).
Six new tests: tuple-unpacking, `for`, `with`, and `except-as` shadowing,
plus a two-independent-same-name-functions-both-flagged positive control
and the `@overload`-shaped negative control itself.

**A further Codex review round found two more real false positives, both
fixed -- neither a lambda nor a comprehension, nor a class body,
introduced a scope of its own before this fix, so each leaked whatever
alias happened to be in scope around it straight through.** (1) A
class-body-level assignment (`fact = rec.bases_fact` written directly in
a class body, not inside a method) was attributed to whatever *function*
enclosed the class, since only a nested `FunctionDef` contributed a scope
range of its own -- a `ClassDef` contributed none. A class body is
actually its own namespace (an ordinary name assigned there is a class
attribute, visible only as `self.x`/`Class.x`, never as a bare name to
the enclosing function), so this let a class-body assignment masquerade
as a real local of the enclosing function, and an unrelated, later
`fact == 1` elsewhere in that function (a genuine module-global `fact`)
was flagged as misuse. Fixed by giving each `ClassDef` its own scope
range in `_enclosing_qualnames` (`f"{prefix}{child.name}#{child.lineno}
<class-body>"`), the identical mechanism a `FunctionDef` already gets --
a nested method's own, narrower range still overrides it for the
method's own lines. Nothing ever looks this qualname up as a lexical
*parent* (`_lexical_function_parents` only ever produces function-def-
derived keys), so it correctly has no effect on anything outside the
class body -- while a genuine misuse *within* the class body itself is
still caught, since the class body is now a real scope of its own rather
than a black hole. (2) A lambda parameter and a comprehension's own
`for` target were likewise never recorded as local bindings, since
neither introduced a scope of its own either -- `fact = rec.bases_fact`
outer, then `(lambda fact: fact == other)(1)` or `[fact == other for
fact in values]`, each shadow the outer alias with an unrelated local
exactly the way a nested `def`'s parameter or a `for`-loop's own target
already does, but the shadow went unrecognized. Fixed the identical way
a `def` already is: both `ast.Lambda` and each comprehension kind
(`ListComp`/`SetComp`/`DictComp`/`GeneratorExp`) now get their own scope
range in `_enclosing_qualnames` (disambiguated by line *and* column,
since several could share one line) and their own entry in
`_lexical_function_parents` -- a lambda/comprehension is a real Python
closure over its enclosing scope, exactly like a nested `def`, so it
becomes the new `nearest_func` for anything nested inside it too, not
just a leaf scope. `_fact_aliases`'s own binding-collection walk now
records a lambda's parameters (reusing the identical `FunctionDef`/
`AsyncFunctionDef` branch, since `ast.Lambda.args` shares the same
`ast.arguments` shape -- a lambda parameter can never carry an
annotation, so the Fact-typed-annotation check on it is always a
harmless no-op) and a comprehension's own `for` target(s) (via the
already-existing `_bound_names()` recursive-unpacking helper). Verified
empirically: still zero existing hits in the real repository, and three
positive controls confirm a genuine misuse inside a lambda, inside a
comprehension, and directly inside a class body are each still caught.
Six new tests: the class-body leak and its positive-control counterpart,
the lambda shadow and its positive control, the comprehension shadow and
its positive control.

**A further Codex review round found three more real gaps -- one a
structural correctness bug in the lambda/comprehension fix itself (a
false *negative*, not the false positives every round so far had been),
the other two small, bounded extensions -- all fixed.** (1) **Resolving a
scope by *line* alone, not by exact position, could hide a genuine
misuse, not merely flag a spurious one.** `fact = rec.bases_fact` outer,
then `(lambda fact: fact == other)(1); return fact == other` on one
line: the SECOND `fact == other` (the real outer alias, textually
sharing the lambda's line but not part of it at all) was silently
misattributed to the lambda's own shadowing scope, since the previous
fix's scope map was still keyed `dict[int, str]` -- one qualname per
physical *line* -- so whichever scope happened to be inserted last for
that line (the lambda) won for the *entire* line, including code that
was never part of the lambda's body. This is more serious than the
false positives every prior round in this section fixed: those rejected
valid code (a review cost); this one let a real `Fact[T]` misuse pass
silently, the exact failure this whole check exists to prevent. Fixed by
switching `_enclosing_qualnames` from a line-keyed `dict[int, str]` to a
list of exact `((start_line, start_col), (end_line, end_col), qualname)`
spans, resolved by a new `_qualname_at()` -- the smallest span whose
`(start, end)` lexicographically brackets a query `(lineno, col_offset)`
position, not merely the smallest span sharing its line (correct because
every span this module produces nests strictly inside its lexical
parent's own span, a laminar family, so comparing by `(end_line -
start_line, end_col - start_col)` -- line span first, column span only
as a same-line tiebreaker -- always picks the correctly-nested one).
Every one of the eight call sites that previously did `qualnames.get(node.
lineno, "<module>")` now does `_qualname_at((node.lineno, node.
col_offset), qualnames)`. Verified against the review's own exact repro:
the shadowed `fact == other` inside the lambda is correctly NOT flagged,
while the real outer `fact == other` on the same line now IS. (2) `first
= second = rec.bases_fact` -- a chained assignment gives every plain-name
target the identical RHS value, unlike a tuple-unpacking target, but the
candidate collector was restricted to exactly one target. Fixed by
looping over every target and registering a candidate for each
plain-`Name` one, all sharing the same RHS -- a tuple/list target among
the same chain still contributes nothing here (only `locally_bound`, via
the pre-existing `_bound_names()` path), since it still has no single
value of its own. (3) `(fact := rec.bases_fact) == other` -- an inline
assignment expression (`ast.NamedExpr`), invisible to `_is_fact_typed_
expr` (which never unwrapped it) and to the alias tracking (which only
ever walked plain assignment statements, never an expression embedded
inside a larger one). Fixed with two changes: `_is_fact_typed_expr` now
unwraps a `NamedExpr` to its own `.value`, exactly as Fact-typed as its
RHS; and `_fact_aliases`'s binding-collection walk registers the
walrus's target as an ordinary alias candidate too, so a later reuse of
the bound name (`if (fact := rec.bases_fact) is not None: return fact ==
other`) is also caught, not just an inline use at the assignment
expression's own site. Deliberately not chasing PEP 572's own
scope-hopping rule for a walrus used *inside* a comprehension (which
binds into the comprehension's *enclosing* scope, not its own) -- an
accepted, narrow residual, since `qualnames` has no notion of that rule
and the walrus alias is registered under whatever scope its own position
resolves to. Verified empirically: still zero existing hits in the real
repository. Four new tests: the exact same-line lambda/outer-alias repro
(with an explicit column check pinning which of the two identical-text
`fact == other` occurrences was reported), a chained assignment, an
inline walrus comparison, and a walrus alias reused on a later line.

**A further Codex review round found two more real gaps, both in the
walrus/parameter machinery this section had just built -- both fixed.**
(1) **The walrus fix's own "deliberately not chasing PEP 572's
scope-hopping rule" residual was, on inspection, backwards -- a real
false positive, not merely an accepted narrow gap.** The previous round
exempted *every* walrus target from `locally_bound` on the theory that
its scope-hopping rule always applies; that rule is real, but only fires
when the walrus sits *directly inside a comprehension* -- everywhere
else, a walrus binds to its immediately enclosing scope exactly like an
ordinary assignment. Unconditionally exempting it meant an entirely
ordinary, comprehension-free rebinding (`fact = rec.bases_fact` outer,
then `def inner(other): (fact := 1); return fact == other`) failed to
shadow the outer alias, flagging valid code. Fixed by hopping only the
genuine comprehension case out to its real PEP 572 binding scope (the
nearest enclosing *non*-comprehension scope, walking `lexical_parents` --
moved earlier in the function so this same walk can use it -- through
every nested comprehension layer, not just the innermost one) and
treating every other case as an ordinary local binding, added to
`locally_bound` the same as any other assignment target. (2) **A
parameter's own default value can be Fact-typed, and nothing examined
it.** `fact = rec.bases_fact; def inner(fact=fact): return fact ==
other` -- calling `inner()` with no override genuinely runs the
comparison against the outer Fact value (a default is evaluated once, in
the *enclosing* scope, at `def`/lambda time), but the parameter was
already unconditionally excluded from the inherited alias set regardless
of what its own default was. Fixed by pairing `args.defaults`/
`kw_defaults` with their parameters (positional defaults right-align
against `posonlyargs + args`; `kw_defaults` pairs positionally with
`kwonlyargs`, `None` for one with no default) and resolving each pending
default in a dedicated pass *after* the fixed point has already
stabilized every scope's own alias set -- a default expression must be
checked against its *enclosing* scope's final alias set, not the
function's own (which excludes this exact name by construction), so this
can't be folded into the same single fixed-point pass every other alias
source already uses. Verified empirically: still zero existing hits in
the real repository, and positive/negative controls confirm a walrus
alias used without shadowing, a walrus hopping out of a comprehension,
and an ordinary (non-Fact) parameter default all still behave correctly.
Five new tests: the walrus-shadow false positive and its walrus-hops-out
positive control, a default referencing an outer alias, a directly
Fact-typed default, and an ordinary-default negative control.

**A further Codex review round found two more real gaps -- one a false
positive in the same shadowing family as the class-body/lambda/
comprehension fixes above, the other a false negative in the same
"attributed to the wrong scope" family as the default-value fix just
above -- both fixed.** (1) **Structural pattern matching's own capture
forms were never recorded as local bindings.** `case fact:` (a bare
`ast.MatchAs` capture), `case [*fact]:` (`ast.MatchStar`), and `case
{**fact}:` (`ast.MatchMapping`'s own `rest`) are all real local bindings,
exactly like a `for` loop target or an `except ... as name:` handler, but
nothing collected any of them -- a nested function's own `case fact:`
failed to shadow an outer `fact = rec.bases_fact` alias, flagging a valid
`fact == other` comparison against the captured (arbitrary) matched
value. Fixed with a new `_match_pattern_names()`, recursively collecting
every bound name from a `pattern` (also descending into `MatchSequence`/
`MatchMapping`/`MatchClass`/`MatchOr`, since a capture can nest inside
any of them), wired into a new `ast.Match` branch in the binding-
collection walk -- `match`/`case` introduces no scope of its own in
Python, so every case's captures are attributed to the `match`
statement's own position. (2) **A walrus inside a parameter's own
default expression was attributed to the wrong scope.** `def inner(x=
(fact := rec.bases_fact)):` -- Python evaluates a default expression in
the *enclosing* scope (the same rule the previous round's default-value
fix already relies on), but the generic, position-based `NamedExpr`
handling resolves a query position to the *smallest* span containing it
-- and a default expression is textually part of the `def`/lambda's own
span, so the walrus was misattributed to `inner`'s own body scope, not
the scope its value is actually usable in. A later, genuinely outer
`fact == other` therefore saw no alias for `fact` at all. Fixed by
explicitly walking each default expression (`args.defaults`/
`kw_defaults`) inside the `FunctionDef`/`AsyncFunctionDef`/`Lambda`
branch that already handles defaults, registering any embedded
`NamedExpr` directly in the *enclosing* scope (`lexical_parents[
qualname]`) as an ordinary local binding -- and recording each one's
`id()` in a new `default_walrus_ids` set so the generic, position-based
`NamedExpr` branch skips it rather than also (mis)processing it a second
time. Verified empirically: still zero existing hits in the real
repository, and positive controls confirm a genuine misuse reached
through a match capture (no shadowing) is still caught. Five new tests:
`MatchAs`/`MatchStar`/`MatchMapping` shadowing, a positive control for a
real match-capture misuse, and the default-expression-walrus repro.

**A further Codex review round found three more real gaps, all in this
same shadowing/scope-attribution family, all fixed.** (1) **`nonlocal`/
`global` were treated identically to an ordinary local rebinding for
shadowing purposes -- wrong, since neither introduces a new local at
all.** `def outer(rec): fact = rec.bases_fact; def inner(): nonlocal
fact; hit = fact == other; fact = 1` -- the `fact = 1` reassignment inside
`inner` correctly makes `fact` a target the existing shadowing machinery
records in `locally_bound[inner]`, but `nonlocal fact` means this is the
*same* variable as the outer alias, not a fresh local one -- so the read
on the line before the reassignment should still see the outer alias and
be flagged, and it wasn't. The identical gap applies to `global`. Fixed
with a new `ast.Global`/`ast.Nonlocal` branch recording each declared
name against its own qualname, followed by a post-walk pass subtracting
every declared name from that same qualname's `locally_bound` set --
after every ordinary binding source has already been collected, so a
genuinely *different* local reassignment in the same function (not
declared nonlocal/global) is unaffected. (2) **A parameter default's own
alias never propagated to a function nested inside the one declaring the
default.** `def inner(rec, fact=rec.bases_fact): def nested(): return
fact == other` -- the previous round's default-value fix resolves
`pending_defaults` in one pass *after* the depth-ordered inheritance/
candidate fixed point has already run to completion, so `nested`'s own
inheritance from `inner` was computed before `inner`'s own default-derived
alias for `fact` existed at all, and `nested` never saw it. Fixed by
wrapping both the depth-ordered inheritance/candidate pass and the
pending-defaults resolution together in one outer fixed-point loop (`while
outer_changed: ...`), so a second iteration through inheritance sees
`inner`'s now-resolved `fact` and propagates it to `nested` exactly the
way an ordinary parent alias already would -- guaranteed to terminate,
since every alias set only ever grows over a finite universe of
`(qualname, name)` pairs. (3) **A class body's own top-level code did not
inherit from its lexically enclosing scope at all.** `def outer(rec):
fact = rec.bases_fact; class C: result = fact == other` -- a class body
is ordinary code that inherits from its enclosing scope like any other
(it's only invisible to a *method* defined inside it, since Python's LEGB
rule skips the class layer specifically for a nested function's own
closure), but `_lexical_function_parents()` had no entry for a class-body
qualname at all, so it inherited nothing and the read was missed. Fixed
by adding a `class_qualname -> nearest_func` entry to the same parents
dict the class-body walk already builds, without disturbing the existing,
correct method-skips-its-class-body behavior (a method gets its own,
independent qualname entry via the unchanged `FunctionDef` branch).
Verified empirically: still zero existing hits in the real repository,
and `mypy`/`ruff` both stayed clean (one incidental fix along the way --
the new pending-defaults loop variable had to be renamed off `target`,
since that name was already inferred as `ast.expr` from an unrelated
`for target in node.targets:` loop earlier in the same function, and
Python's lack of block scoping meant reusing it produced a real mypy type
error, not just a style nit). Nine new tests: the nonlocal/global
exemption and its negative control (an *undeclared* local reassignment
must still shadow), the nested-function default-propagation repro, the
class-body-inherits repro and its negative control (a class body with no
enclosing Fact alias), and a test pinning that a method still does not
see its own class body's local reassignment of the same name -- it sees
straight through to the enclosing function's real alias instead.

**A further Codex review round found three more real gaps in this same
family, all fixed.** (1) **A class body's own `LOAD_NAME` lookup is
statement-order-aware, unlike every other scope's `LOAD_FAST`, and the
previous round's shadowing fix didn't account for it.** `fact = rec.
bases_fact` outer, then `class C: hit = fact == other; fact = 1` --
Python resolves `fact` dynamically at each class-body statement against
whatever the class namespace holds *so far*, not statically pre-determined
by "is this name assigned anywhere in this class body" the way a
function's `LOAD_FAST` is -- so `hit`'s read, occurring *before* the
later `fact = 1` reassignment, genuinely sees the outer alias in real
Python, but the existing shadowing subtraction (whole-scope, position-
blind, correct for every other scope) excluded it entirely, missing a
real misuse. This module has no statement-order-aware lookup of its own
(building one would mean turning `_fact_aliases`'s per-scope answer into
a per-position one, a materially larger change), so the fix takes the
same over-approximating-is-safe direction the module already argues for
the opposite case (nonlocal/global): a class body's own local rebinding
now never shadows what it inherits at all, gated on the `<class-body>`
qualname suffix `_enclosing_qualnames` already produces. The accepted
cost is symmetric to the finding this closes: a class body that
reassigns `fact` to something ordinary *before* using it now reads as a
false positive -- the same "false positive is far cheaper than the false
negative it closes" trade-off this module states throughout, just landing
on the opposite scope this time. (2) **A nested `def`/`class` statement's
own *name* was never recorded as a local binding in the scope that
contains it.** `def fact(): ...` then `fact == other` in the same
containing scope -- Python's `LOAD_FAST`/`LOAD_NAME` resolves `fact` to
the function object just defined, an ordinary local, but the existing
branch only ever recorded the *nested* scope's own parameters, never the
definition's name in its *container* -- so an outer Fact alias of the
identical bare name was never shadowed, flagging valid code. Fixing this
correctly needed a new, dedicated helper rather than reusing the
position-based `_qualname_at` lookup every other binding site already
uses: a scope-introducing node's own span always starts at its own
`(lineno, col_offset)`, so querying that exact position resolves to the
node's *own* smallest containing span, not its parent's -- the wrong
answer for "what scope contains this definition." `_def_containing_
qualnames()` is a small, standalone recursive walk (mirroring `_lexical_
function_parents`'s own shape, deliberately *not* skipping a class
layer the way that function does, since a def/class statement's binding
target is a different question from the closure-scope chain), keyed by
position rather than qualname string, consulted only by this one new
`FunctionDef`/`AsyncFunctionDef`/`ClassDef` branch. (3) **`global fact`
was still routed through ordinary `lexical_parents` inheritance, which is
wrong for `global` specifically even though it's exactly right for
`nonlocal`.** `nonlocal fact` genuinely means "the nearest enclosing
*function's* own `fact`" -- precisely what `lexical_parents[qualname]`
already gives every other inherited name, so the previous round's fix was
correct for that half. `global fact` means "*module*-scope `fact`, full
stop" -- it must bypass every intervening function layer's own
inheritance entirely, even one with its own unrelated, non-Fact-typed
local of the identical bare name. Routing it through ordinary inheritance
was wrong in both directions at once: an intervening function's own
unrelated local could shadow (hide) a genuinely Fact-typed module-level
name, and, symmetrically, an intervening function's own genuinely
Fact-typed local of the same bare name could be wrongly attributed to an
unrelated module-level one. Fixed by tracking `global`-declared names in
a separate `global_declared` dict (alongside the existing, still-shared
`nonlocal_or_global` shadowing-exemption set) and adding a dedicated step
to the outer fixed-point loop: a `global`-declared name is excluded from
the ordinary parent-inheritance step entirely and instead resolved
directly against `aliases["<module>"]`. Verified empirically: still zero
existing hits in the real repository, and `mypy`/`ruff` both stayed clean.
Seven new tests: the class-body read-before-reassignment repro, the
nested-def and nested-class shadowing repros and their shared negative
control (a real misuse still caught with no nested definition present),
and the global-through-an-intervening-local repro with its reversed-
values negative control.

**A further Codex review round found two more real gaps, both fixed.**
(1) **A comprehension walrus was never actually marked as a local binding
in the scope PEP 572 hops it out to.** The scope-hop fix (several rounds
above) computes `binding_qualname` -- the walrus's real PEP 572 target
scope, hopping out of every enclosing comprehension layer -- correctly,
but the `locally_bound` mark itself was gated on `binding_qualname ==
walrus_qualname`, i.e. only when NO hop occurred. That's backwards
relative to that same fix's own stated intent ("every other case" gets
the identical `locally_bound` treatment): `[(fact := x) for x in values]`
sitting directly inside `inner`, with an outer `fact = rec.bases_fact`
alias, hops `fact`'s real binding out to `inner` itself under PEP 572 --
a genuine local of `inner` -- but with the mark skipped whenever a hop
happens, `inner`'s own inheritance step never learned this, and a later,
real `fact == other` in `inner` (reading the comprehension's own ordinary
int, not the outer Fact alias) was wrongly flagged. Fixed by marking
`locally_bound[binding_qualname]` unconditionally, regardless of whether
a hop occurred -- `binding_qualname` is already the correct target scope
either way, so the conditional never needed to exist. (2) **A `global`/
`nonlocal`-declared name's own *write* side was still attached to the
declaring function's own qualname, even though the read side (two rounds
above) was already correctly routed.** `def seed(rec): global fact; fact
= rec.bases_fact` genuinely writes *module*-scope `fact` -- but the
candidate this assignment produces was recorded under `seed`'s own
qualname regardless, so a sibling function reading the identical
module-level `fact` through ordinary inheritance (not its own `global`
declaration) never saw it as Fact-typed at all, missing a real misuse.
The identical gap applies to `nonlocal`. Fixed with a new
`_declared_target_scope()` helper and a dedicated post-walk pass
(alongside the existing shadowing-subtraction pass, for the identical
reason: a `global`/`nonlocal` statement can appear anywhere in its
function's body, even after the assignment it governs, so this can't be
decided candidate-by-candidate during the single forward walk) that
redirects every candidate *and* every direct alias recorded under a
declaring qualname to the scope the assignment actually writes to --
`<module>` for `global`, the nearest enclosing function for `nonlocal`.
Verified empirically: still zero existing hits in the real repository,
and `mypy`/`ruff` both stayed clean (the same "variable name already
inferred as a different type elsewhere in this function" mypy trap this
file's history has hit twice before required renaming two local variables
away from `target`/`declared`). Six new tests: the comprehension-walrus
shadowing repro and its negative control (a real misuse still caught with
no comprehension walrus present), the global-write-visible-to-a-sibling
repro and its negative control (an ordinary, undeclared local assignment
must not leak into the module's own alias set), and the identical
write-side routing test for `nonlocal`.

**A further Codex review round found two more real gaps, both fixed.**
(1) **The write-side routing fix's own candidate-move approach broke on a
same-writer-scope RHS indirection.** `global fact; local = rec.bases_fact;
fact = local` -- `fact`'s own candidate value is the bare `ast.Name`
`local`, which is only ever a real local of the *writer's own* scope
(`seed`), never of `<module>`. The previous round's fix moved the raw
`(name, value)` candidate tuple straight into the target scope's own
candidate list, where the inner fixed point then checked `local` against
the *target*'s own alias set -- `local` was never going to appear there,
silently breaking the exact RHS indirection this whole write-side fix
exists to close. Fixed by no longer moving candidates at all: a declared
assignment's candidate stays in its writer's own scope, resolving
normally there (so `local` resolves against `seed`'s own alias set, same
as any other same-function alias). A new step, run each outer-fixed-point
iteration right after a scope's own inner candidate resolution, then
checks whether a `global`/`nonlocal`-declared name has become confirmed
Fact-typed *within its writer's own scope this iteration* and, only then,
propagates it into the scope the assignment actually writes to -- so
`fact` (confirmed via `local`, within `seed`) propagates to `<module>`
the same outer iteration, with no separate resolution context to drift
out of sync with the writer's own. (2) **A comparison inside a
parameter's own default or annotation resolved against the function's
own body scope, not the enclosing scope it actually evaluates in.** `fact
= rec.bases_fact; def inner(fact=(fact == other)): ...` -- Python
evaluates a default at `def`-time in the *enclosing* scope (the same
binding-timing rule the earlier default-embedded-walrus fix already
relies on), but `fact_equality_misuse_sites()`'s own site-to-qualname
resolution is purely position-based, and the comparison's own position is
textually inside `inner`'s span -- so it was checked against `inner`'s
own alias set, where `inner`'s own parameter `fact` has already removed
the inherited alias (the shadowing fix, several rounds above), missing a
comparison that at the point it actually runs still reads the outer
alias. Fixed with a new `_default_and_annotation_scope_overrides()`,
walking every parameter default and annotation expression's own subtree
and mapping each descendant node's `id()` to the function's lexical
parent -- consulted by `fact_equality_misuse_sites()`'s own `ast.Compare`
handling before falling back to the ordinary position-based lookup.
Verified empirically: still zero existing hits in the real repository,
and `mypy`/`ruff` both stayed clean. Four new tests: the same-scope-RHS-
indirection repro for both `global` and `nonlocal`, the default-
comparison repro, and a negative control (a default comparison against
an unrelated, non-Fact value must not be flagged).

**A further Codex review round found two more real gaps, both fixed --
plus a file-size split this round's own new tests triggered.** (1) **The
`nonlocal` write-side routing fix always targeted the immediate lexical
parent, but `nonlocal` can skip *multiple* enclosing functions.** Python
resolves `nonlocal fact` to the nearest enclosing function scope that
actually binds the name itself, not merely the one right above -- `outer`
binds `fact`, `middle` (nested in `outer`) never touches it at all,
`setter` (nested in `middle`) does `nonlocal fact; fact = rec.bases_fact`
-- this genuinely writes `outer`'s `fact`, skipping `middle` entirely,
but the previous, immediate-parent-only routing published the write to
`middle` instead, where nothing reads it (a sibling of `middle`,
`reader`, never saw it). Fixed by walking outward from the immediate
parent, at each step checking whether that ancestor's own `locally_
bound` set actually contains the declared name -- `locally_bound` is
already exactly the right test, since it already excludes a name an
ancestor only holds via its *own* `nonlocal`/`global` declaration (the
shadowing-exemption subtraction from several rounds above), so the walk
naturally continues past an ancestor whose own binding is itself
borrowed from further out, the identical case Python's own resolution
skips. (2) **An import statement was never recorded as a local
binding at all.** `import json as fact` or `from pkg import item as fact`
inside a nested function shadows an inherited Fact alias exactly like any
other assignment form, but the binding collector had no `ast.Import`/
`ast.ImportFrom` branch, so a nested function's own import-bound `fact`
never shadowed an outer alias. Fixed with a new branch mirroring every
other binding-form branch's own shape, splitting an unaliased dotted
import (`import a.b.c`) on its first `.` to match Python's own
import-binding rule (only the top-level package name binds in the
importing scope without an explicit `as`). Verified empirically: still
zero existing hits in the real repository, and `mypy`/`ruff` both stayed
clean. Four new tests: the multi-level `nonlocal`-skip repro, both import
forms' shadowing repros, and a negative control (a real misuse still
caught with no import shadowing present).

**The four new tests above pushed `tests/test_fact_detector_misuse.py`
past the architecture gate's 1200-line test-file cap (1224 lines) --
split the same way `test_mutation_run_scoping.py` already did for an
identical reason (see `tests/CLAUDE.md`'s own note on that split).** The
whole `TestFactEqualityMisuseSites` class had grown into two genuinely
distinct halves: the core misuse-detection contract (direct attribute
reads, `getattr`/constructor-call recognition, alias chains, annotated
assignments) and, by far the larger half after fourteen-plus rounds of
scope-attribution findings, every shadowing/scope-resolution test
(parameter/local/comprehension/lambda/walrus/match-capture shadowing,
closure inheritance through nested functions and class bodies,
`nonlocal`/`global` read- and write-side routing, parameter-default/
annotation scope resolution, import-based shadowing). Split the second
half out into a new sibling file, `tests/test_fact_detector_misuse_
scoping.py` (`TestFactEqualityMisuseSitesScoping`), leaving the original
file at 273 lines and the new one at 997 -- both comfortably under the
cap, with zero test behavior change (all 82 tests, the same set as
before the split, still pass).

**A further Codex review round found four more real gaps, all fixed --
one in annotation recognition, three in scope resolution.** (1)
**`_is_fact_typed_annotation` only ever recognized a bare `Fact` or a
subscript whose own value was `Fact` -- an `Optional[Fact[T]]`/
`Union[Fact[T], None]`/PEP 604 `Fact[T] | None` wrapper hid the misuse
underneath it entirely.** `def f(value: Fact[int] | None, other): return
value == other` produced no finding at all. Fixed by making the function
genuinely recursive over the three wrapper shapes: an `ast.BinOp` with
`ast.BitOr` unwraps both operands (`X | None`), an `Optional[...]`
subscript unwraps its slice, and a `Union[...]` subscript unwraps every
element of its slice tuple (or the bare slice, for a single-argument
`Union[X]`) -- each via the same `fact_names`-keyed leaf check a bare
`Fact` already used, so `Fact[int]` nested arbitrarily deep inside any
combination of these still resolves. A new `_annotation_head_name()`
helper recognizes the wrapper's own name whether spelled bare
(`Optional[...]`) or module-qualified (`typing.Optional[...]`). (2)
**A default expression containing its own nested lambda/comprehension
scope had every one of its descendants force-attributed to the enclosing
scope, including nodes that actually evaluate *inside* that nested
scope.** With an outer `fact = rec.bases_fact`, `def f(cb=lambda fact:
fact == other): ...` was rejected even though the lambda's own parameter
`fact` genuinely shadows the outer alias for the comparison inside the
lambda's own body -- the blanket `ast.walk(subtree)` walked straight
through the lambda's own boundary, overriding the inner `Compare` node's
scope to the *outer* function's, discarding the shadowing the lambda's
own already-correct position-based resolution would have given it. Fixed
with a new `_iter_default_subtree()` walker: it still yields every
descendant (so the override map is unaffected for the common,
no-nested-scope case), but stops expanding past any node that itself
introduces a scope (`FunctionDef`/`AsyncFunctionDef`/`Lambda`/any
comprehension) -- that boundary node is still yielded (harmless, since
only an `ast.Compare` id is ever looked up), just never descended into,
leaving its own body to ordinary position-based resolution, which already
has a real span and qualname for it. (3) **`node.returns` -- a return
annotation -- was omitted from the scope-override traversal entirely,**
even though Python evaluates it in the defining (enclosing) scope exactly
like a parameter default or a parameter annotation: `fact = rec.
bases_fact; def inner(fact) -> (fact == other): ...` was missed, since
`inner`'s own parameter `fact` shadows the outer alias for the function
body, but the return annotation evaluates *before* that parameter even
exists. Fixed by appending `node.returns` (when present -- `ast.Lambda`
has no such attribute) to the same `subtrees` list defaults/parameter
annotations already populate. (4) **A method's own parameter default is
evaluated while its *containing class body* executes -- ordinary
`LOAD_NAME` class-body code, not a closure lookup -- but the
`pending_defaults` resolution pass looked up the default's evaluation
scope via `lexical_parents`, which intentionally skips the class layer
for the different question of a method *body*'s own free-variable
lookup.** `class C: fact = rec.bases_fact; def m(self, value=fact):
return value == other` produced no finding, since `value` was never
marked as a Fact alias -- `lexical_parents.get("C.m", ...)` skips straight
past `C`'s own class-body scope to whatever encloses the class, which
carries no `fact` alias in this shape at all. Fixed by resolving the
default's own evaluation scope through `_def_containing_qualnames`
instead (already computed once per call inside `_fact_aliases`, unlike
`_default_and_annotation_scope_overrides`'s own separate copy, since
`_fact_aliases` needed it first for the walrus-target-hop case several
rounds above) -- the syntactic containing scope, class layers included --
while the method *body*'s own `fact` parameter (with no default at all)
still correctly shadows in its body, verified by a dedicated negative
control, since `_fact_aliases`'s shadowing-subtraction mechanism the
class-skipping `lexical_parents` chain still governs is untouched by this
fix. `pending_defaults` grew a fourth tuple element (the `def`'s own
`(lineno, col_offset)`, needed since `_def_containing_qualnames` is
position-keyed, not qualname-keyed) to carry this through; `_default_and_
annotation_scope_overrides` itself was also switched from
`_lexical_function_parents` to `_def_containing_qualnames` for the
identical reason -- a comparison found *directly inside* a method's own
default (not just an aliased parameter later read in the body) needs the
same containing-class-scope answer. Verified empirically: still zero
existing hits in the real repository, and `mypy`/`ruff` both stayed
clean. Nine new tests: three annotation-wrapper positive cases plus a
negative control (an ordinary `int | None` parameter), the nested-lambda-
default exclusion plus its no-lambda negative control, the return-
annotation case, and the class-body-default case plus its
method-parameter-still-shadows negative control.

**A further Codex review round found the identical nested-scope-boundary
gap in a sibling collector, one function over.** `_default_and_
annotation_scope_overrides()`'s own `Compare`-attribution walk had just
been fixed (previous round) to stop at a nested lambda/comprehension
boundary via `_iter_default_subtree()`, but the *walrus-inside-a-default*
collector -- a separate loop, a few lines above in `_fact_aliases()`,
that publishes a default-embedded walrus target as an alias of the
enclosing scope -- still used a plain, unrestricted `ast.walk(default_
expr)`. `fact = 1; def configure(cb=lambda: (fact := rec.bases_fact)):
...` -- the lambda is only *created* at def-time in the enclosing scope;
the walrus inside its body binds `fact` in the *lambda's own* scope only
when the lambda is later called, never the enclosing one -- but the
unrestricted walk crossed that boundary anyway, wrongly publishing the
lambda-local walrus target as a real alias of the enclosing (here,
module) scope, so a later, genuinely unrelated `fact == other` read past
that point was rejected. Fixed by switching this collector to the same
`_iter_default_subtree()` walker the sibling fix already introduced --
one shared boundary-aware primitive for both consumers, rather than a
second copy of the same fix. Verified empirically: still zero existing
hits in the real repository, and `mypy`/`ruff` both stayed clean. Two new
tests: the nested-lambda-walrus exclusion and a negative control (a
walrus directly inside a default, no nested scope in the way, must still
be published to the enclosing scope exactly as before).

**A further Codex review round found three more real gaps in the same
containing-scope machinery, all fixed.** (1) **`_def_containing_
qualnames()` recorded a containing scope for a `def`/`class` statement
but never for a `Lambda`.** A lambda has no *name* to bind (unlike a
`def`/`class` statement, its introduction is an expression, not a
statement) -- but its own default values still evaluate at
lambda-creation time in whatever scope directly contains it, the
identical rule a method's own default already relies on this function
for. `fact = rec.bases_fact; cb = lambda x=fact: x == other` inside a
function had no entry to resolve against, so both the pending-default
alias resolution and the comparison-scope override silently fell back to
`"<module>"` regardless of the lambda's real containing scope. Fixed by
recording `containing[lambda.lineno, lambda.col_offset] = scope_qualname`
in the `Lambda` branch, mirroring the `FunctionDef`/`ClassDef` branches
exactly. (2) **A nested class's own base/keyword expressions were
attributed to the *inner* class's own body scope instead of the scope
that actually contains the `class` statement.** `class Outer: fact =
rec.bases_fact; class Inner(make_base(fact == other)): ...` -- a base
class or metaclass keyword executes while the `class Inner` statement
itself runs, in `Outer`'s own scope, never inside `Inner`'s own (not yet
even created) body -- but `_enclosing_qualnames` assigns the *entire*
`ClassDef` span, bases and keywords included, to the inner class-body
scope (correct for the body's own statements, wrong for the header that
precedes them), and `_default_and_annotation_scope_overrides()` had no
`ClassDef` branch at all to correct it. Fixed by extending that function
with a `ClassDef` branch: walk `node.bases`/`(kw.value for kw in
node.keywords)` through the same `_iter_default_subtree()` boundary-aware
walker the `FunctionDef`/`Lambda` branch already uses, overriding each to
the `ClassDef`'s own containing scope (from `def_containing`) rather than
its class-body scope. (3) **The walrus-inside-a-default collector's own
`enclosing` computation still used `lexical_parents`, not
`def_containing`, even after the sibling `pending_defaults` resolution
and `_default_and_annotation_scope_overrides()` were both already fixed
to use the latter (two rounds above).** A *method's* own default-embedded
walrus is evaluated while its containing *class body* executes -- ordinary
class-body code, not a closure lookup -- but `lexical_parents`
intentionally skips that class layer for the different question of a
method *body*'s own free-variable lookup: `fact = 1; class C: def
f(self, x=(fact := rec.bases_fact)): ...` wrongly bound the walrus target
to the *module's* `fact` (skipping straight past `C`'s own class-body
scope), so a later, genuinely unrelated module-level `fact == other`
read past that point was rejected, and the actual class-body-scoped
`fact` the walrus meant to bind was invisible to a use elsewhere in that
same class body. Fixed by switching this one remaining call site to
`def_containing.get((node.lineno, node.col_offset), "<module>")`,
matching its two siblings. Verified empirically: still zero existing
hits in the real repository, and `mypy`/`ruff` both stayed clean. Five
new tests: the lambda-default-inherits-containing-scope case, the
nested-class-header case, the method-default-walrus-binds-in-class-
namespace case and its positive control (the same walrus target visible
elsewhere in that class body).

**A further Codex review round found a structural bug in `_lexical_
function_parents()` itself, distinct from every containing-scope fix
above.** All of those rounds fixed *which primitive* (`lexical_parents`
vs. `def_containing`) a caller consults; this one is a bug in `lexical_
parents`'s own construction. `_lexical_function_parents()`'s recursive
descent switched to a `def`/`lambda`'s *new* qualname before visiting
*any* of its own children -- including its default values, parameter
annotations, return annotation, and decorators, all of which evaluate at
def/lambda-*creation* time, in whatever scope was active *before* that
new qualname takes over (the identical binding-timing rule `_fact_
aliases()`'s/`_default_and_annotation_scope_overrides()`'s own default/
annotation handling already relies on). `fact = rec.bases_fact` in `f`,
then `def g(fact, cb=[fact == other for _ in xs]): ...` -- the
comprehension executes while `g` is being *defined* (in `f`'s own scope,
before `g`'s own parameter `fact` even exists to shadow anything) and
genuinely closes over `f`'s alias, but was wrongly parented under `g`
itself, where `g`'s own same-named parameter incorrectly shadowed it.

Fixed by splitting the single recursive `visit()` into two cooperating
functions: `dispatch()` does the actual scope-introducing-node matching
(the original `visit()`'s `if isinstance(...)` chain, unchanged in
substance), while a new, thinner `visit()` just applies `dispatch()` to
every direct child of a node. This split matters because a `def`'s own
default/annotation/decorator expressions now need to be *re-dispatched*
(via a new `def_time_subtrees()` helper, mirroring `_default_and_
annotation_scope_overrides()`'s own `subtrees` construction almost
exactly) with the *old* `nearest_func`, while only the real function body
executes with the new one -- and a subtree re-dispatch needs the full
`dispatch()` match logic (in case the subtree is itself a bare `Lambda`/
comprehension), not a plain `visit()` that would skip straight into its
children. Generalized to `Lambda`'s own defaults too, not just `def`
(the identical Codex-reported repro reproduces for `g = lambda fact,
cb=[fact == other for _ in xs]: cb`), and to decorators (a decorator's
own arguments evaluate at def-time in the enclosing scope by the
identical rule, even though the reported finding didn't name that
shape) -- matching this repo's own "fix the cause, not the instance"
convention rather than patching only the literal reported repro.

**A real regression was caught and fixed while implementing this, not by
a separate review round: the first version of the split reused `visit()`
(not `dispatch()`) for a function's own body statements and a lambda's
own body expression, silently breaking the plain-closure case with no
default at all.** `visit(stmt, qualname + ".", qualname)` for each body
statement treats `stmt` as the node whose *children* get dispatched, not
as a dispatch candidate itself -- so a nested `def`/`class`/`Lambda`/
comprehension directly in the body (the ordinary, common shape every
closure-inheritance test in this whole file already exercises) was never
matched by `dispatch()` at all, and its own `parents[]` entry was never
recorded. Caught immediately by re-running the existing test suite before
adding new tests for this fix (`test_a_plain_closure_with_no_default_is_
still_caught` pins this exact regression), not by a fresh review round --
worth recording as the same "verify against the existing suite before
trusting a refactor, not just the new repro it was written for" discipline
this plan's own earlier rounds have needed more than once. Fixed by
calling `dispatch()`, not `visit()`, for both the `FunctionDef`/
`AsyncFunctionDef` body-statement loop and the `Lambda` body expression.
A second, smaller version of the same class of bug (`kw_defaults`
carrying a `None` element -- an ordinary "this keyword-only parameter has
no default" marker, not a bug -- reaching `dispatch()` unfiltered and
crashing on `ast.iter_child_nodes(None)`) was caught the same way, before
it ever reached CI, and fixed by filtering `None` out of `def_time_
subtrees()`'s own `kw_defaults` half, matching `_default_and_annotation_
scope_overrides()`'s own pre-existing `if subtree is None: continue`
guard for the identical list.

Verified empirically: still zero existing hits in the real repository,
and `mypy`/`ruff` both stayed clean. Six new tests, in a new sibling file
(`tests/test_fact_detector_misuse_def_time_scope.py` -- `test_fact_
detector_misuse_scoping.py` was already at the architecture gate's
1200-line cap, the same reason that file was split out of `test_fact_
detector_misuse.py` in the first place): the reported comprehension-
default-on-a-`def` case, the identical case on a `lambda`, a negative
control (a comprehension shadow genuinely inside the body, not a
default, must still be caught by the parameter), the plain-closure
regression guard, a keyword-only-default-after-a-no-default-keyword-arg
case (pinning the `kw_defaults`-filtering fix), and the decorator
generalization.

**A further Codex review round found `_def_containing_qualnames()` had
the identical unconditional-`visit(child, qualname + ".", qualname)` bug
`_lexical_function_parents()` above was just fixed for, in its own
separate walk over the same tree.** `_def_containing_qualnames()` answers
a related but distinct question (which scope a `def`/`class` *statement*
directly, syntactically binds into, and -- since a later slice -- which
scope a `Lambda` is directly, syntactically created in), used by the
pending-default alias resolution and the comparison-scope override
elsewhere in this file, but it had its own, separate recursive walk that
never received the `def_time_subtrees()`/`dispatch()`/`visit()` split
above. `fact = rec.bases_fact` in `f`, then `def g(fact, cb=lambda x=
fact: x == other): ...` -- the *inner* lambda's own `x=fact` default
evaluates while `g` is being defined, in `f`'s own scope, before `g`'s own
parameter `fact` even exists -- but the unconditional recursion recorded
the lambda's containing scope as `g` instead of `f`, so `g`'s own
same-named parameter `fact` incorrectly appeared to shadow the lambda's
`x=fact` default and the misuse went undetected. Fixed with the identical
split: a second `def_time_subtrees()` (duplicated rather than shared --
this module deliberately keeps each scoping helper local to the function
that uses it, matching this file's own existing convention of two
independent, structurally identical copies rather than a premature shared
abstraction) plus a `dispatch()`/`visit()` split for `_def_containing_
qualnames()`, re-visiting a def-time subtree under the *old*
`scope_qualname` while only the real body executes under the new one.
Verified empirically: still zero existing hits in the real repository,
and `mypy`/`ruff` both stayed clean. Two new tests, appended to the
existing `tests/test_fact_detector_misuse_def_time_scope.py` (room
remained under the architecture gate's 1200-line cap): the reported
nested-lambda-default-inside-another-`def`'s-default case, and a negative
control confirming a lambda genuinely created inside `g`'s own body (not
one of `g`'s own def-time subtrees) is still correctly parented under
`g`, so `g`'s own parameter still shadows the outer alias there.

**Two further Codex review rounds found two more real gaps in the same
area, both real Python scoping exceptions this scoping machinery had
never modeled, both fixed.**

**(1) A comprehension's own *outermost* generator's iterable evaluates in
the enclosing scope, not the comprehension's own.** This is a genuine
CPython semantic: a comprehension compiles to an implicit generator
function, and only the *first* `for`'s iterable is evaluated *before*
that function is even called (passed to it as an argument) -- the
element expression, every `if` filter, and every non-first `for`'s
iterable all run *inside* the comprehension's own new scope, after its
own target(s) have already bound. `fact = rec.bases_fact; [x for fact in
(fact == other,) for x in fact]` -- the comparison sits inside the first
generator's own iterable, which runs before `fact` (the comprehension's
own target) exists to shadow anything, so it still reads the *outer*
alias. All three scoping functions (`_enclosing_qualnames`,
`_lexical_function_parents`, `_def_containing_qualnames`) previously
attributed the *whole* comprehension -- outermost iterable included -- to
its own new scope uniformly, missing this. Notably, `_enclosing_
qualnames`'s own docstring had already predicted and explicitly accepted
this exact gap as "vanishingly rare, and not the shape any review round
has found" -- a review round then found it.

Fixed with the identical technique in all three functions, each adapted
to its own return shape: `_enclosing_qualnames` (which threads a
`qualname` string through its walk, not just a `prefix`, so it can name
what "the enclosing scope" actually *is*) registers a narrower,
independent span for the outermost iterable's own source range, tagged
with the *incoming* qualname rather than the comprehension's own new
one -- `_qualname_at`'s existing smallest-span-first resolution then
picks this override for any position inside that one iterable, while the
comprehension's own broader span still covers everything else. The
whole-node walk still revisits the same iterable afterward (a real,
accepted small waste, not a bug -- since it's the *identical* span, the
strict `<` comparison `_qualname_at` already uses never lets a
same-size, later-registered entry displace the correct, earlier one).
`_lexical_function_parents`/`_def_containing_qualnames` (which don't
build spans at all, only qualname-keyed maps) instead re-dispatch the
outermost iterable directly under the *old* `nearest_func`/
`scope_qualname`, mirroring exactly how each already re-dispatches a
`def`/`lambda`'s own def-time subtrees under the old scope. Verified
empirically: still zero existing hits in the real repository, and
`mypy`/`ruff` both stayed clean. New tests, in `tests/test_fact_detector_
misuse_def_time_scope.py`: the reported case, a negative control
confirming only the *first* generator is special (a genuine shadow in a
*second* generator's own iterable is still correctly excluded), and a
case confirming a real *closure* (not just a bare comparison) found in
the outermost iterable resolves correctly too.

**(2) A class's own base classes, keyword arguments, and decorators all
evaluate while the `class` statement itself executes, in whatever scope
directly contains it -- never inside the new class's own body -- and
`_def_containing_qualnames`'s `ClassDef` branch dispatched all of them
(bases, keywords, decorators, *and* body) uniformly under the new
class-body qualname.** An earlier round had already fixed the *direct*-
read case for `_default_and_annotation_scope_overrides`'s own separate
override map (`class Inner(make_base(fact == other)): ...`), but that fix
only widens which *qualname* a bare read resolves to when the read sits
*directly* in the header text -- it says nothing about a *nested closure*
(a `def`/`lambda`/comprehension/another `class`) found there, whose own
containing-scope entry is a completely different lookup, owned by
`_def_containing_qualnames` itself. `class Inner((lambda x=fact:
make_base(x == other))()): ...` -- the lambda's own default `x=fact`
evaluates in `Outer`'s namespace (wherever the `class Inner(...):`
statement itself lives), not `Inner`'s, but the unconditional dispatch
recorded the lambda's containing scope as `Inner<class-body>` instead,
so `x` was never recognized as the outer alias. Fixed by splitting the
`ClassDef` branch's dispatch the same way the `FunctionDef`/`Lambda`
branches already are: every base expression and keyword value (plus,
generalizing to a shape the reported repro didn't name but the identical
timing rule covers, every decorator) is re-dispatched under the
*incoming* `scope_qualname`, while only the class's own body statements
dispatch under the new `class_qualname`. This closes the gap for both a
direct read *and* a nested closure automatically, since a nested `def`/
`lambda` dispatched under `scope_qualname` now correctly records its own
`containing[]` entry against that same outer scope -- no separate change
to `_default_and_annotation_scope_overrides` was needed, since that
function already reads `def_containing` (this fix's own output) for every
`def`/`lambda`'s own position, whether inside a class header or not.
Verified empirically: still zero existing hits, `mypy`/`ruff` both stayed
clean. New tests: the reported base-expression case, a metaclass-keyword
case (generalizing beyond the one reported shape), and a negative control
confirming a comparison genuinely inside the class *body* is still
correctly attributed there -- shadowed via a nested method's own
parameter (a mechanism already known to work) rather than a class-body
*reassignment*, since class-body-level reassignment shadowing is a
separate, pre-existing, unrelated gap this module doesn't track at all
(confirmed unaffected by this fix, before and after, by direct
comparison against the pre-fix code).

**A further Codex review round found a real regression in the comprehension
fix above, plus one more real gap, both fixed.**

**(1) The comprehension fix's own "harmless, same span" reasoning was
correct for `_enclosing_qualnames`, but wrong for its two siblings.** The
first version of that fix dispatched the outermost iterable once, under
the old scope, then still finished with a blanket `visit(child, ...)`
over the *whole* comprehension -- reasoned as harmless, since the blanket
walk reaches the identical iterable a second time with the identical
span, and `_qualname_at`'s strict `size < best_size` tie-break never lets
a same-size later entry replace an earlier one. That reasoning holds for
`_enclosing_qualnames`'s own `spans` list, but `_lexical_function_
parents`/`_def_containing_qualnames` don't build a spans list at all --
they write straight into a plain `dict` (`parents[qualname] = ...`,
`containing[pos] = ...`), where a second write to the same key
*unconditionally overwrites* the first, no tie-break involved. `[x for
fact in (lambda y=fact: (y == other,))() for x in fact]` -- the lambda's
own default `y=fact` is correctly dispatched once, under `f`'s own scope,
by the fix's first half; the blanket re-walk then reaches the *same*
lambda a second time and overwrites its correct `containing[]`/`parents[]`
entry with the comprehension's own (wrong) scope, silently missing the
misuse. Fixed uniformly across all three functions by never re-walking
the outermost iterable at all: the comprehension's own body is now walked
explicitly, field by field (`elt`/`key`+`value` for a `DictComp`, each
generator's `target`/`ifs`, and every *non-first* generator's own `iter`),
instead of through a blanket walk that would reach the already-handled
iterable again. `_enclosing_qualnames` itself needed a genuine `dispatch`/
`visit` split for this (it never had one before, unlike its two siblings
-- see the def-time-subtree fix earlier in this section), since dispatching
a specific field like `elt` as its own candidate (it might itself be a bare
`Lambda`, as in `[lambda: x for x in y]`) needs a function that matches a
*given* node, not `visit`'s existing "match every child of a container"
shape. New tests: the reported lambda-in-outermost-iterable case, and a
`DictComp`-specific case confirming its `key`/`value` split is covered by
the same explicit-field dispatch.

**(2) `_fact_aliases()`'s candidate collection deliberately excluded every
tuple/list-unpacking assignment target**, reasoned as "has no single value
to attribute" -- true for the *general* case (`a, b = pair`, one opaque
value with no per-element sub-expression), but not for `old_fact, new_fact
= old.bases_fact, new.bases_fact`, where the RHS is *itself* a literal
tuple display of the identical length: each target element genuinely does
have its own value, the same way a chained assignment's every target
already does. Fixed with `_paired_unpacking_candidates()`, a recursive
helper matching a `Tuple`/`List` target against a structurally identical
`Tuple`/`List` value element-by-element (nesting through a further tuple
target the same way `_bound_names()` already does), returning *no*
candidates at all -- rather than a partial, best-effort pairing -- the
moment either side isn't a literal display, the lengths disagree, or
either side contains a `Starred` element (which captures an
arbitrary-length slice with no single corresponding RHS sub-expression to
pair it against). New tests: paired tuple unpacking, paired list
unpacking, nested paired unpacking, and two negative controls (the
pre-existing opaque-single-value case, still correctly excluded, and a
starred-target case confirming no candidate is derived for it without
spuriously flagging an unrelated comparison in the same function).

Both fixes' new tests went into `tests/test_fact_detector_misuse_def_
time_scope.py` (room remained under the architecture gate's 1200-line
cap). Verified empirically: still zero existing hits in the real
repository, and `mypy`/`ruff` both stayed clean.

**A further Codex review round found two more real gaps, both direct
siblings of already-fixed shapes above, both fixed.**

**(1) A walrus inside a class base or metaclass keyword expression had no
binding-side counterpart to the read-side fix already covering that same
header text.** `_default_and_annotation_scope_overrides()`'s `ClassDef`
branch already routes a bare `==`/`!=` comparison found directly in a
base/keyword expression to the scope containing the `class` statement --
but `_fact_aliases()`'s own walrus-binding handling (the branch that lets
`(fact := rec.bases_fact)` be *usable* later, not just recognized
inline) only ever special-cased a `FunctionDef`/`AsyncFunctionDef`/
`Lambda`'s own default/annotation subtrees, with no equivalent branch for
`ast.ClassDef` at all. `class C(make_base(fact := rec.vtable_fact)): ...`
therefore bound `fact` in the not-yet-created class-body scope (the
generic, position-based `NamedExpr` branch's default attribution), not
the scope actually executing the class header -- silently losing an
alias a later, genuinely outer `fact == other` needs. Fixed with the
identical mechanism the `FunctionDef`/`Lambda` branch already uses,
mirrored into a new `ast.ClassDef` branch: walk every base and keyword
value through `_iter_default_subtree()`, register any walrus found there
against `def_containing`'s own entry for the class statement, and mark
it via `default_walrus_ids` so the generic branch doesn't also
(mis)process it. One subtlety worth recording: `ast.ClassDef` already
matches an *earlier*, unrelated `elif` branch in the same walk (the
def/class own-name binding fix from an earlier round) -- placing the new
branch there would have made it unreachable dead code, since Python's
`elif` chain stops at the first match per node per loop iteration. The
new branch instead joins the *second*, independent `if`/`elif` chain the
`FunctionDef`/`Lambda` default-handling branch already starts (Python
allows a fresh `if` after an `elif` chain ends; the two chains run
sequentially, not exclusively, for a single node), confirmed correct by
tracing a `ClassDef` node's own execution path through both chains
directly rather than assuming placement.

**(2) A single `for`/comprehension loop target bound, one iteration at a
time, to every element of a literal `Tuple`/`List` display of
Fact-typed values had no propagation at all -- the loop-target
counterpart of `_paired_unpacking_candidates()`'s already-fixed
assignment-side handling.** `for fact in (old.bases_fact, new.bases_
fact): return fact == other` only ever recorded `fact` as an ordinary
local binding (shadowing an *outer* alias correctly), never as a
candidate in its own right, so the misuse this loop actually performs
went undetected. Unlike the paired-unpacking case -- where *distinct*
targets each pair with a *distinct* RHS element -- a loop target reuses
the *same* name across every iteration, so the alias only holds if
*every* element is definitively Fact-typed, not merely one of them
(`for x in (rec.bases_fact, some_other_call()):` must stay unflagged,
since `x` is only sometimes a Fact). Fixed by checking, for both `ast.
For`/`ast.AsyncFor` and a comprehension's own each-generator loop, that
the target is a bare `Name`, the iterable is a literal `Tuple`/`List`
display, and every element satisfies `_is_fact_typed_expr()` before
registering one representative element as a candidate (the fixed-point
resolver only needs one Fact-typed candidate value per name to mark it
known). Verified against the exact reported repro plus its comprehension
form (`[fact == other for fact in (old.bases_fact, new.bases_fact)]`),
a negative control with a mixed Fact/non-Fact tuple, a negative control
with a bare (non-literal) iterable, and a negative control confirming
the alias stays scoped to the function that actually established it
(an unrelated sibling function's own same-named parameter is unaffected).

Both fixes verified empirically: still zero existing hits in the real
repository, and `mypy`/`ruff` both stayed clean. New tests in `tests/
test_fact_detector_misuse_def_time_scope.py` (room remained under the
architecture gate's 1200-line cap): `TestClassHeaderWalrusContainingScope`
and `TestForLoopLiteralCollectionAliases`.

**A further Codex review round found one more real gap in the loop-target
fix above.** `_is_fact_typed_expr()` deliberately never resolves a bare
`ast.Name` -- answering "is this name a Fact" needs the whole-tree alias
fixed point, which doesn't exist yet during the single collection pass
that builds candidates -- so the "every element satisfies
`_is_fact_typed_expr()`" gate from the previous fix silently rejected an
otherwise-ordinary alias refactor: `old_fact = old.bases_fact` outer,
then `for fact in (old_fact,): fact == other`, is a real misuse (`fact`
genuinely holds `old_fact`'s value, which is genuinely Fact-typed), but
`old_fact` is a bare name, not a structural attribute/constructor
expression, so it never satisfied the gate at collection time.

The loop-target case needed something the existing `candidates`
mechanism can't express: `candidates` is *disjunctive* (a name becomes
known the moment ANY ONE of its recorded values resolves -- the right
model for "this name was assigned this value, or this other value, at
different points"), but a loop target bound to every element of one
tuple needs *every* element to resolve before the target itself does --
a *conjunctive* requirement. Fixed with a new, parallel structure,
`tuple_loop_candidates: dict[str, list[tuple[str, list[ast.expr]]]]`,
populated by the same collection-time gate as before except an element
is now accepted either because it's structurally Fact-typed (unchanged)
or because it's simply a bare `ast.Name` (deferred, not yet confirmed) --
an element that is neither still disqualifies the whole loop outright,
preserving the original conservative guarantee. Consulted inside the
same `while changed:` per-qualname fixed point `candidates` itself
already participates in: at each pass, a `tuple_loop_candidates` entry's
target becomes known once every one of its elements is either
structurally Fact-typed or a `Name` already present in `known` -- the
identical `isinstance(value, ast.Name) and value.id in known` resolution
`candidates`' own loop already performs for a single value, just
conjoined across a whole tuple instead of checked for one. Since `known`
persists across outer fixed-point passes and only ever grows, this
naturally converges the same way every other alias resolution in this
module does.

Verified against the exact reported repro, its comprehension form, a
mixed alias-and-direct-read tuple (both element kinds must each resolve
independently), a negative control where the bare-name element never
resolves to anything (must stay unflagged, confirming the deferred
element is only *eligible*, not automatically accepted), and a negative
control mixing a resolved alias with an unrelated non-Fact call
(confirming the conjunctive requirement holds regardless of which
specific element fails it) -- plus the full existing suite, confirming
the original tuple-of-direct-reads shape from the previous round is
unaffected. `mypy`/`ruff` both stayed clean, still zero existing hits in
the real repository. New tests:
`TestForLoopLiteralCollectionResolvesThroughExistingAliases` in `tests/
test_fact_detector_misuse_def_time_scope.py`.

**A CodeRabbit review round found one more real gap: `_fact_aliases()`'s
walrus-collection loop only ever walked `node.args.defaults`/
`kw_defaults`, never a parameter's own annotation or the `->` return
annotation.** `def inner(x: (fact := rec.bases_fact)): ...` -- absent
`from __future__ import annotations`, Python evaluates a parameter
annotation at the identical def-time, in the identical containing scope,
a default value does -- but this loop's own construction (mirroring only
half of `_default_and_annotation_scope_overrides()`'s already-wider
`subtrees` list, this module's read-side sibling function) never
included either, so a walrus there fell through to the generic,
position-based `NamedExpr` branch and was misattributed to the
function's own body scope, the identical failure mode every earlier
round in this section already fixed for defaults specifically. Fixed by
widening the loop's own subtree collection to mirror `_default_and_
annotation_scope_overrides()`'s exactly (every parameter's own
annotation, plus `returns` when present) -- unconditionally, regardless
of whether the module actually has `from __future__ import annotations`
in effect, matching that sibling function's own already-established,
deliberately conservative choice: a walrus that in fact never executes
under postponed evaluation registering a spurious alias is a false
positive, the safe direction this module accepts throughout, rather than
adding a second, narrower postponed-annotation-detection rule that would
only complicate this loop without closing a real gap in the safe
direction. Verified against the reported parameter-annotation repro, its
return-annotation sibling, and a negative control confirming the
existing nested-scope-boundary rule (`_iter_default_subtree()`, already
relied on for defaults) applies identically to a lambda nested inside an
annotation. Full existing suite unaffected. `mypy`/`ruff` both stayed
clean, still zero existing hits in the real repository. New tests:
`TestWalrusInAnnotationsContainingScope` in `tests/
test_fact_detector_misuse_def_time_scope.py`.

**A further Codex review round found two more real gaps in
`_is_fact_typed_expr()`'s and the loop-target machinery's own coverage,
both fixed.**

**(1) `_is_fact_typed_expr()` never unwrapped a conditional expression.**
`(old.bases_fact if condition else new.bases_fact) == other` -- and the
equivalent `fact = old.bases_fact if condition else new.bases_fact;
fact == other` -- both genuinely produce a Fact-typed result regardless
of which branch actually runs, but `ast.IfExp` had no branch in this
function at all, unlike the already-handled `ast.NamedExpr` unwrap.
Fixed with a new `ast.IfExp` branch requiring *both* `node.body` and
`node.orelse` to independently resolve as Fact-typed -- deliberately
narrower than `NamedExpr`'s unconditional unwrap, since an `IfExp`
genuinely produces one of two *different* values depending on
`condition`, so only the case where both are guaranteed Fact-typed
regardless of outcome is a real, unconditional Fact-typed result;
`old.bases_fact if condition else some_other_call()` must stay
unflagged, the identical every-branch-must-agree principle the
loop-target literal-collection fix earlier in this section already
applies to a tuple's own elements. Verified against both the inline and
assigned-then-compared repros, plus a negative control with one
non-Fact branch.

**(2) The loop-target literal-collection fix only ever handled a bare
`ast.Name` target -- a tuple-*unpacking* target was silently excluded
entirely.** `for fact, tag in ((old.bases_fact, "old"), (new.bases_fact,
"new")): fact == other` -- each target position genuinely has its own
per-iteration value, but neither the single-target `elif` branch nor
anything else in this loop matched a `Tuple`/`List` target at all. Fixed
with a new `elif` branch reusing `_paired_unpacking_candidates()` --
the identical elementwise pairing `ast.Assign`'s own unpacking handling
already relies on -- once per iteration element (each iteration element
is exactly the "one assignment's worth" of value that function already
knows how to pair against the loop's own, unchanging target shape). Any
single iteration element that isn't itself a literal display of matching
length (or that trips the starred-element exclusion
`_paired_unpacking_candidates()` already applies) disqualifies the
*whole* loop via an `all_iterations_paired` flag, rather than silently
pairing only some iterations -- the identical "no candidates at all over
a partial pairing" principle that function's own docstring already
states, extended across iterations instead of within one. Once every
iteration pairs successfully, each target name's own per-iteration
values are collected and registered together, subject to the identical
every-element-Fact-typed-or-deferred-name conjunctive requirement the
simple-target case already applies via `tuple_loop_candidates`. Verified
against the reported repro, a negative control confirming the sibling
unpacked position (`tag`, never Fact-typed) stays unflagged even though
`fact` in the same loop is, a negative control where one iteration
element fails to pair (disqualifying the whole loop, not just that
iteration), and a negative control for a starred unpacking target.

Both fixes verified empirically: still zero existing hits in the real
repository, `mypy`/`ruff` both stayed clean. New tests:
`TestConditionalExpressionsRecognizedWhenBothBranchesAreFactTyped` and
`TestForLoopUnpackingTargetsResolveElementwise` in `tests/
test_fact_detector_misuse_def_time_scope.py`.

**Split into a sibling module once these fixes pushed the file past the
AI-readiness `file-size` gate's own 2000-line hard cap.** Every review
round in this section added real, dense docstring explaining *why* --
the file-size check does not distinguish code from documentation, and
the growth was entirely legitimate (each finding needed its reasoning
recorded, per this file's own "known gaps over risky reactive patches"
and "fix the cause, not the instance" conventions), so the fix is a
mechanical extraction, not a diet. The eight lexical-scope-resolution
building blocks every alias-resolution function in this module builds on
(`_enclosing_qualnames`/`_qualname_at`/`_QualnameSpans`,
`_lexical_function_parents`, `_def_containing_qualnames`,
`_bound_names`, `_paired_unpacking_candidates`, `_match_pattern_names`)
moved, unchanged, into a new sibling leaf module,
`scripts/fact_detector_misuse_scope.py`, imported by
`fact_detector_misuse.py` via a sys.path guard mirroring
`check_ai_readiness.py`'s own identical one -- needed because a bare,
non-dotted `from fact_detector_misuse_scope import ...` only resolves
when `scripts/` itself is on `sys.path`, which is guaranteed when this
module is run directly (Python adds its own directory automatically) or
when `check_ai_readiness.py` was imported first in the same process (it
already inserts its own directory before importing `fact_detector_
misuse` the identical bare way) -- but not guaranteed for a test file
that imports `scripts.fact_detector_misuse` on its own, as two of this
module's own test files already do. Verified by running each of those
two test files in isolation (not just as part of the full suite), which
is exactly the scenario a missing guard would silently pass in a
full-suite run and fail only in isolation. `fact_detector_misuse.py`
dropped from 2058 to 1369 lines; the new module is 739. No behavior
change -- every moved function is bit-for-bit identical to its original,
confirmed by the full existing test suite passing unchanged (142 tests
across all three test files) and a fresh `mypy`/`ruff` pass on both
files. Registered in `scripts/CLAUDE.md`'s inventory table (the
`script-inventory` AI-readiness check's own requirement).

**A further Codex review round found four more real gaps, all fixed.**

**(1) `_is_fact_typed_expr()`'s own `IfExp` branch can't resolve a
bare-`Name` alias, since it has no access to a scope's `known` set.**
`old_fact = old.bases_fact; new_fact = new.bases_fact; fact = old_fact if
cond else new_fact; fact == other` is a real misuse -- both branches are
aliases the surrounding fixed point already confirmed Fact-typed -- but
the structural, scope-independent `_is_fact_typed_expr()` deliberately
never resolves a bare name at all. Fixed with a new
`_candidate_resolves_to_fact(value, fact_names, known)`: the
fixed-point-aware sibling every ordinary `candidates` entry already got
via its own inline `isinstance(value, ast.Name) and value.id in known`
check, generalized to recurse through an `IfExp`'s own two branches too
(each independently required to resolve -- structurally, as an
already-known alias, or itself another nested `IfExp` -- before the
conditional as a whole is trusted). Both the ordinary `candidates` loop
and `tuple_loop_candidates`' own per-element check now go through this
one function instead of duplicating the same inline check twice.

**(2) A comprehension's tuple-loop-target candidate always registered
every element against the comprehension's own scope, even the first
generator's own iterable, which actually evaluates in the *parent*
scope.** `fact = rec.bases_fact; [fact == other for fact in (fact,)]` --
the tuple element `fact` names the *outer* alias, but the comprehension's
own target is *also* `fact`, shadowing it in the comprehension's own
scope; checking the element there checks the shadowed name against
itself and never resolves. This needed a genuine data-model change, not
just a smarter check: `tuple_loop_candidates` now pairs each element with
its *own* resolution qualname (`list[tuple[ast.expr, str]]`, not a flat
`list[ast.expr]`) -- the resolved target name still becomes known in the
comprehension's own scope (where the actual read happens), but each
element is checked against `aliases.get(elt_qualname, set())`, which for
the first generator is the position `_qualname_at()` resolves via the
narrower override span `_enclosing_qualnames()` already registers for
that exact iterable, tagged with the comprehension's own incoming
(parent) qualname. A plain `for` loop and every generator after the
first pair every element with their own (unchanged) qualname, so this is
a strict widening, not a behavior change for the already-fixed cases.

**(3) The comprehension's own tuple-loop-target branch only ever matched
a bare `ast.Name` generator target -- the comprehension counterpart of
finding (2) two Codex rounds ago, applied to a plain `for` loop, was
never extended to a comprehension's own generator.** `[fact == other for
fact, tag in ((old.bases_fact, "old"),)]` was invisible. Fixed with the
identical `_paired_unpacking_candidates()`-per-iteration-element
machinery the `for`/`AsyncFor` branch already uses, applied per
generator -- paired with each element's own resolution qualname from
finding (2), and registered under the comprehension's own scope the
same way finding (2)'s fix already does.

**(4) A whole-subject `match` capture was recorded only as an ordinary
local binding, never as an alias of the match subject.** `match
rec.bases_fact: case fact: return fact == other` -- `case fact:` (a bare
capture) and `case SomeClass() as fact:` (an `as`-pattern) both bind the
*entire* subject unconditionally whenever that case matches, making the
captured name a real alias of `node.subject`, not merely an arbitrary
shadow the way a *nested* capture inside a larger structural pattern
(`case [x, y as fact]:`, capturing only a sub-part) correctly still is.
Both shapes are exactly `case.pattern` itself being an `ast.MatchAs` with
a real `name` -- Python's own grammar for a top-level capture/`as`-
pattern. Fixed by registering `(case.pattern.name, node.subject)` as an
ordinary candidate whenever `isinstance(case.pattern, ast.MatchAs) and
case.pattern.name is not None`, reusing the existing `candidates`
fixed point rather than adding a new mechanism.

Verified against each finding's own reported repro plus negative
controls (an unresolved conditional branch; an unrelated first-iterable
name; the sibling unpacked position staying unflagged; a nested
structural capture; a non-Fact match subject) and the full existing
suite, confirming every previously-fixed shape in this section is
unaffected. `mypy` caught two real variable-redefinition/type-narrowing
issues in the same pass (a same-named local reused with a different type
across the `for`-loop and comprehension branches) -- fixed by renaming,
not by suppressing. `ruff`/`mypy` both stayed clean, still zero existing
hits in the real repository, `fact_detector_misuse.py` at 1523 lines
(well under the 2000-line hard cap). New tests:
`TestConditionalExpressionResolvesThroughAliasBranches`,
`TestComprehensionFirstIterableResolvesAgainstTheParentScope`,
`TestComprehensionUnpackingTargetsResolveElementwise`, and
`TestWholeSubjectMatchCapturesAreAliases` in `tests/
test_fact_detector_misuse_def_time_scope.py`.

**A fifth finding, from the next review round: a direct conditional
comparison operand -- never assigned to an intermediate variable at all --
still bypassed alias resolution, even after finding (1) above fixed the
*assignment-candidate* half of the identical `IfExp` shape.**
`old_fact = rec.bases_fact; new_fact = rec.bases_fact; (old_fact if cond
else new_fact) == other` returned no misuse site. The two `IfExp` checks
already in the module are not the same check: `_is_fact_typed_expr()`'s own
`IfExp` branch (used by `_fact_aliases()`'s fixed point when *registering* a
candidate) requires both branches to be *structurally* Fact-typed on their
own -- it never resolves an alias `Name` in either branch, since alias
resolution is the fixed point's job, not this structural predicate's.
`fact_equality_misuse_sites()`'s own *terminal* `is_fact_typed()` closure --
the one that actually decides whether a raw `==`/`!=` operand counts,
applied after the whole fixed point has already converged -- had no `IfExp`
branch of its own at all: it fell through to `isinstance(node, ast.Name)`,
which a conditional expression never satisfies, so a direct conditional
operand could not be recognized regardless of what either branch resolved
to. Fixed by giving `is_fact_typed()` its own recursive `IfExp` branch,
mirroring `_candidate_resolves_to_fact()`'s reasoning but adapted to this
call site's available state: it checks each branch via `is_fact_typed()`
itself (so a nested conditional operand resolves too) against the
comparison's own `qualname`, reading the *final*, already-converged
`aliases` mapping rather than an in-progress fixed-point `known` set, since
by the time this terminal check runs `_fact_aliases()` has already finished.
Kept the same AND semantics every other `IfExp` handling in this module
already uses (`_is_fact_typed_expr`, `_candidate_resolves_to_fact`): both
branches must resolve, since a single-branch match cannot be told apart
from an ordinary conditional expression that merely happens to read one
Fact attribute among other unrelated locals -- a real, if narrow, accepted
false-negative direction, not new to this fix.

Verified against the reported repro, a nested-conditional variant, and two
negative controls (neither branch resolves; only one branch resolves) via
direct `python3 -c` reproduction before writing tests, plus the full
existing suite confirming every previously-fixed shape stays unaffected.
`ruff`/`mypy` both clean, still zero existing hits in the real repository,
`fact_detector_misuse.py` at 1529 lines (well under the 2000-line hard cap).
New tests:
`TestDirectConditionalOperandsResolveThroughAliasBranches` in `tests/
test_fact_detector_misuse_def_time_scope.py`.

**A CodeRabbit review round re-raised the postponed-annotations question
this module's own walrus-in-annotation collection loop already reasoned
through and deliberately declined to special-case -- investigated afresh,
the standing decision stands, not re-implemented.** The finding: `_fact_
aliases()` registers a walrus target found inside a parameter's own
annotation or the `->` return annotation as bound at def-time
unconditionally, even when the module carries `from __future__ import
annotations` (PEP 563), under which an annotation expression is never
evaluated at all -- it is stored as an unparsed string, so the walrus
inside it never actually executes and the name it would have bound is
never really available. This is not a new observation: the exact same
walrus-collection loop's own inline comment already states this trade-off
explicitly ("absent `from __future__ import annotations` ... unconditionally
walking here too matches that established, deliberately conservative
choice rather than adding a second, narrower rule: a walrus that in fact
never executes under postponed evaluation registering a spurious alias is
a false positive, the safe direction this whole module already accepts
throughout"). Re-verified the reasoning still holds rather than accepting
it on faith: (1) this repository's own `AGENTS.md` convention mandates
`from __future__ import annotations` in every scanned production file
(`abicheck/**/*.py`), so a correct implementation would need to gate on a
per-*module* fact (does the scanned file itself carry the future import)
threaded specifically into the annotation-embedded half of the walrus
walk -- default-expression walruses are unaffected by PEP 563 and must
keep binding eagerly regardless, so the two subtrees this loop currently
treats identically (`walrus_subtrees = [*node.args.defaults, *node.args.
kw_defaults]`, with annotations appended after) would need to split back
apart, undoing the very unification that closed the earlier annotation-
vs-default gap this class of finding exists to prevent reopening; (2) the
scenario itself -- a walrus operator inside a type annotation, assigning a
`Fact[...]`-typed value as a side effect of annotating a parameter -- is
adversarial, not a pattern any real detector code in this repository
would plausibly write, unlike every other alias shape this module has
special-cased so far (each traced to an ordinary, unremarkable refactor a
real contributor could genuinely perform); (3) the failure direction is
already the accepted one throughout this module -- an over-approximated
alias only ever risks flagging a comparison that, if the annotation truly
never executes, would itself raise `NameError` before reaching the
comparison, so the practical cost of not fixing it is a spurious ERROR on
code that cannot run as written, not a missed real misuse. Given the
fix's real complexity (a second, narrower rule threaded through one
specific subtree of an already-hardened, many-times-reviewed collection
loop) against a scenario with no plausible real-world occurrence, this is
documented as a known, deliberate, already-reasoned-through accepted
false-positive direction rather than implemented -- consistent with this
module's own established "the safe direction this whole module already
accepts throughout" principle, not a gap distinct from what the code
already states. No code change; replied to the review thread pointing at
the existing docstring's own reasoning.

**Two more findings from the same review round, both real, both bounded
extensions of the same recursive-resolver mechanism the direct-
conditional-operand fix just established.** (1) `_is_fact_typed_expr()`'s
own `NamedExpr` branch unwraps a walrus to its `.value` and checks it
structurally, but it deliberately never resolves a bare `Name` there (the
same limit every structural check in this module accepts -- alias
resolution needs scope, which a single node can't supply). Neither of the
two places that *can* supply scope had a matching `NamedExpr` branch of
their own: `_candidate_resolves_to_fact()` (the fixed-point-aware
resolver a candidate's own value is checked against) and `is_fact_typed()`
(`fact_equality_misuse_sites()`'s own terminal comparison-operand
predicate). `old_fact = rec.bases_fact; (copy := old_fact) == other` (a
direct comparison operand) and `old_fact = rec.bases_fact; fact = (copy
:= old_fact); fact == other` (assigned through an intermediate name
first) were both missed -- `_is_fact_typed_expr` correctly declined to
resolve the wrapped bare `old_fact`, and neither caller had anywhere else
to turn. Fixed by giving both functions the identical recursive
`NamedExpr` branch already established for `IfExp` one round earlier:
`_candidate_resolves_to_fact(value.value, fact_names, known)` and
`is_fact_typed(node.value, qualname)` respectively -- each simply
recurses into the walrus's own value through the same resolver, so a
nested walrus (`(a := (b := old_fact)) == other`) resolves too, and a
walrus wrapping a genuinely non-Fact name stays correctly unflagged. (2)
The parameter-default resolution loop's own inline check --
`_is_fact_typed_expr(default, fact_names) or (isinstance(default,
ast.Name) and default.id in aliases.get(parent, ()))` -- was a narrower,
duplicated re-implementation of exactly what `_candidate_resolves_to_fact()`
already generalizes: it had no `IfExp` (or now `NamedExpr`) branch of its
own, so a default composing two already-known aliases through a
conditional expression (`old = rec.bases_fact; new = rec.vtable_fact; def
inner(value=old if cond else new): return value == other`) was rejected
outright, even though both branches were already confirmed Fact-typed
aliases in the parent scope. Fixed by replacing the inline check with a
direct call to `_candidate_resolves_to_fact(default, fact_names,
aliases.get(parent, set()))` -- the identical resolver every other
candidate site already uses, so this loop can no longer independently
drift from what the rest of the module considers Fact-typed, and it
inherits the `NamedExpr` fix above for free.

Verified against all three reported repros (direct `NamedExpr` operand,
assigned-through `NamedExpr` alias, composed `IfExp` default), a nested-
`NamedExpr` variant, and two negative controls (a `NamedExpr` wrapping a
non-Fact name; a composed default with only one Fact-typed branch,
pinning the same AND semantics established for the direct-conditional-
operand fix) via direct `python3 -c` reproduction before writing tests.
`ruff`/`mypy` both stayed clean, still zero existing hits in the real
repository, `fact_detector_misuse.py` at 1533 lines (well under the
2000-line hard cap). New tests:
`TestNamedExpressionsResolveThroughAliasBranches` in `tests/
test_fact_detector_misuse_def_time_scope.py`.

**Two more findings from the next review round, plus a test-file split
the second push over the 1200-line cap.** (1) `_is_fact_typed_annotation()`'s
generic fallback branch (`return _is_fact_typed_annotation(annotation.value,
fact_names)`) is correct for a plain `Fact[int]` subscript -- `annotation.
value` is the `Fact` head name itself -- but wrong for `Annotated[Fact[int],
metadata]` (PEP 593): there, `annotation.value` is `Annotated`, not `Fact`,
and the real type lives in the subscript's own *slice* (its first element;
everything after is arbitrary metadata, never itself a type to check).
`def f(value: Annotated[Fact[int], "meta"], other): return value == other`
was invisible. Fixed with a dedicated `head == "Annotated"` branch,
mirroring the existing `Optional`/`Union` branches' own tuple-vs-bare-slice
handling: recurses into the slice's first element only (`elts[0]` when the
slice is a `Tuple`, else the bare slice), leaving every later metadata
element untouched. Composes correctly with the existing `Optional`
handling (`Annotated[Optional[Fact[int]], "meta"]`), since each branch
only ever recurses through the same function. (2) The `ast.Match` branch's
existing whole-subject-capture recognition (`case fact:`/`case SomeClass()
as fact:`) had no counterpart for an OR pattern where *every* alternative
independently captures the whole subject under the same name -- `case
(list() as fact) | (tuple() as fact): fact == other` was invisible, since
`case.pattern` there is `ast.MatchOr`, never itself an `ast.MatchAs`.
Python requires every alternative of an OR pattern to bind the identical
*set* of names (a `SyntaxError` otherwise), but not the identical binding
*shape* -- `case C(x=fact) | (D() as fact):` legally binds `fact` in both
branches, but only the second branch binds it to the whole subject, so
consistency of the bound *name* alone is not sufficient evidence. Fixed
by requiring every single alternative to itself be exactly `ast.MatchAs`
with the identical `.name` before treating it as an alias of `node.
subject` -- verified this correctly rejects the mixed-shape case above
while still accepting a bare-capture-mixed-with-an-as-pattern OR
(`case fact | (tuple() as fact):`) and a three-way OR, since each
alternative there is independently a whole-subject `MatchAs`.

Verified against both reported repros, a multi-metadata-item variant and
an `Annotated`+`Optional` composition for finding (1), a three-way OR and
a bare-capture-mixed-with-`MatchAs` OR for finding (2), and negative
controls for each (a non-Fact `Annotated` type; a non-Fact match subject;
the mixed-binding-shape OR pattern) via direct AST reproduction before
writing tests. Adding these tests pushed `tests/test_fact_detector_misuse_
def_time_scope.py` to 1198 of its own 1200-line test-file cap -- two lines
of headroom, effectively none -- so its tail (the four most recent
alias-resolution-edge-case test classes: direct conditional operands,
`NamedExpr`, `Annotated`, `MatchOr`) was split into a new sibling file,
`tests/test_fact_detector_misuse_alias_edge_cases.py`, mirroring how that
file was itself split out of `test_fact_detector_misuse_scoping.py` --
mechanical extraction, every class moved unchanged, verified both in
combination and in isolation. `ruff`/`mypy` both stayed clean, still zero
existing hits in the real repository, `fact_detector_misuse.py` at 1572
lines (well under the 2000-line hard cap), the def-time-scope test file
back down to 940 lines, the new edge-case test file at 299 lines. New
tests: `TestAnnotatedWrapperUnwrapsToItsFirstSliceElement` and
`TestMatchOrPropagatesWholeSubjectCaptures`, both now in `tests/
test_fact_detector_misuse_alias_edge_cases.py`.

**Two more findings from the next review round, both bounded extensions
of already-established mechanisms.** (1) None of `_is_fact_typed_expr()`,
`_candidate_resolves_to_fact()`, or `fact_equality_misuse_sites()`'s own
terminal `is_fact_typed()` predicate had a `BoolOp` branch, so `(old.
bases_fact or new.bases_fact) == other` was missed entirely, as was the
assignment-through-alias form. Python's `and`/`or` always return one of
their own operands verbatim -- never a synthesized `True`/`False`, only
ever short-circuiting to whichever operand its own truthiness picks -- so
if *every* operand of a `BoolOp` is guaranteed Fact-typed, the result is
too, regardless of which one runtime truthiness actually selects: the
identical every-operand-must-agree principle the `IfExp` branches already
established, generalized from two branches to `BoolOp.values`'s arbitrary
operand count (an `a or b or c` chain is one `BoolOp` node with three
values, not two nested ones). Fixed by giving all three functions the
identical recursive `BoolOp` branch, mirroring each one's own existing
`IfExp` branch exactly. (2) The comprehension-scope-hopping walrus
collection branch (an earlier round's own fix) registers a hopped
target's candidate under `binding_qualname` (the enclosing scope PEP 572
hops it out to), but paired it with `node.value` unconditionally checked
against that *same* scope's converged aliases at fixed-point time -- even
though the walrus's RHS is still textually written, and can only ever
resolve, in the comprehension's *own* scope. `[(captured := fact) for
fact in (rec.bases_fact,)]; captured == other` was missed: `fact` (the
comprehension's own `for`-bound target) is never a known alias of
`binding_qualname` (the enclosing function), only of the comprehension's
own scope. This is the identical "one shared qualname per entry can't
express both a binding scope and a resolution scope" problem `tuple_loop_
candidates`'s own per-element `(elt, elt_qualname)` pairing already
solves for a tuple's elements -- applied here to a single scalar walrus
value instead. Fixed with a new `cross_scope_candidates` dict, keyed by
the binding qualname exactly like `candidates`, but each entry additionally
carrying the value's own resolution qualname (`walrus_qualname`, only
populated when a real hop occurred -- the no-hop case still uses the
ordinary `candidates` dict unchanged); the fixed-point loop resolves each
entry's value against `aliases.get(value_qualname, set())` rather than
the current pass's `known` set, mirroring `tuple_loop_candidates`'s own
per-element resolution exactly. A walrus hopping out of *nested*
comprehensions (PEP 572 hops out of all of them, not just the innermost)
resolves correctly too, since `walrus_qualname` still names the
comprehension it was textually written in.

Verified against both reported repros, a three-way `BoolOp` chain, a
`BoolOp`-via-alias variant, alias-resolved `BoolOp` operands, a negative
control pinning the AND semantics (one non-Fact operand disqualifies the
whole expression) for finding (1); and a non-Fact-RHS negative control, a
double-hopped nested-comprehension variant, and a no-hop regression guard
for finding (2) -- all via direct AST reproduction before writing tests.
Zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_detector_misuse.py` at 1642 lines (well under the 2000-line hard
cap). New tests: `TestBoolOpResolvesWhenEveryOperandIsFactTyped` and
`TestComprehensionWalrusResolvesItsRhsInItsOwnScope`, both in `tests/
test_fact_detector_misuse_alias_edge_cases.py` (410 lines, well under its
own 1200-line cap).

**Two more findings from the next review round, both bounded extensions of
already-established mechanisms.** (1) `_iter_default_subtree()` -- the
helper `_default_and_annotation_scope_overrides()` uses to attribute
everything inside a `def`/`lambda`'s own default value or annotation to
its def-time (enclosing) scope -- stops descending the moment it reaches
any scope-introducing node, comprehensions included, since a comprehension
genuinely creates its own new scope for its `elt`/later generators. But a
comprehension's own *outermost* generator's iterable is the one
established exception to that rule (the identical carve-out
`_enclosing_qualnames`'s and `_lexical_function_parents`'s own
comprehension handling already give the same construct, documented in
their own docstrings): it evaluates in the scope enclosing the
comprehension, before the comprehension's own implicit function is even
called. `_iter_default_subtree()` never carried the same exception, so
`fact = rec.bases_fact; def g(fact, cb=[x for x in (fact == other,)]):
...` was silently missed -- the comparison sits inside exactly that
exempt iterable, but the walk stopped at the `ListComp` boundary before
ever reaching it. Fixed by giving `_iter_default_subtree()` the identical
one-generator carve-out: on reaching a comprehension, its outermost
iterable is pushed back onto the walk (still under the def-time scope this
whole function exists to attribute), and everything else in the
comprehension is left correctly opaque. The exception recurses through the
walk's own stack the same way it already does in the two sibling
functions, so a doubly-nested comprehension's own outermost iterable
(itself nested two hops from the `def`) resolves correctly too, with no
extra logic needed. (2) `_is_fact_typed_annotation()` had no case for a
*stringized* (quoted) annotation -- `def f(old_fact: "Fact[list[str]]",
other): return old_fact == other` -- a real, common spelling (required
under `from __future__ import annotations` for anything evaluated lazily,
and used ad hoc even without it to break an import cycle or reference a
not-yet-defined name), invisible to every existing shape check since it
parses as a bare `ast.Constant` string rather than any of the `Name`/
`Subscript`/`BinOp` shapes the function already recognized. Fixed by
parsing the string as an expression (`ast.parse(..., mode="eval")`) and
recursing into the parsed body through the same function -- so the fix
composes for free with every wrapper the function already handles
(`Optional[...]`, `Union[...]`, the `X | None` PEP 604 spelling,
`Annotated[...]`), rather than needing its own copy of that logic. A
string that isn't valid Python at all (unrelated malformed input, not a
forward-reference annotation) degrades to `False` rather than propagating
a `SyntaxError`, matching every other best-effort parse in this module.

Verified against both reported repros -- a doubly-nested comprehension
variant and a lambda-default-body negative control (a genuine, ordinary
scope boundary, unlike a comprehension's outermost iterable) for finding
(1); a stringized annotation wrapped in `Optional[...]`, one using the
PEP 604 `X | None` spelling, a malformed-string negative control, and an
unrelated non-Fact stringized-annotation negative control for finding
(2) -- all via direct AST reproduction before writing tests. Zero existing
hits, `mypy`/`ruff` both stayed clean, `fact_detector_misuse.py` at 1683
lines (well under the 2000-line hard cap). New tests:
`TestDefaultComprehensionOutermostIterableStaysDefTimeScoped` and
`TestStringizedFactAnnotationsAreRecognized`, both appended to `tests/
test_fact_detector_misuse_def_time_scope.py` (1062 lines, well under its
own 1200-line cap -- the natural home for both, since each is a def-time/
annotation-scope fix, not a plain alias-resolution one).

**One more finding from the next review round, a bounded extension of an
already-established mechanism.** The single-target `for`/comprehension
loop-binding branches (`tuple_loop_candidates`'s own collection: "one loop
target bound, one iteration at a time, to every element of a statically
known display") recognized only `ast.Tuple`/`ast.List`, so `for fact in
{old.bases_fact, new.bases_fact}: fact == other` (a set display) and `for
fact in {old.bases_fact: 1, new.bases_fact: 2}: fact == other` (a dict
display, iterated as its keys) were both invisible, as was the identical
gap in the duplicated comprehension-generator branch -- an ordinary choice
of container literal, not a different question from the `Tuple`/`List`
case already handled. Fixed with one shared `_static_display_elements()`
helper (`Tuple`/`List`/`Set` → `.elts`, `Dict` → `.keys`, `None` for
anything else or for a dict containing a `**expansion` -- a `None` entry
in `ast.Dict.keys`, whose own value could be anything, so the whole
display can't be treated as statically enumerable), used at both the
`for`-loop and comprehension single-target sites instead of duplicating
the three-way shape check at each.

Verified against both reported repros (set-display `for`, dict-keys
`for`), the identical pair for the comprehension form, a mixed-element
set negative control (only some elements Fact-typed -- must stay
unflagged, mirroring the existing tuple negative control), a `**expansion`
dict negative control, and an empty-display negative control (`{}`,
Python's only empty-display literal -- there is no bare empty-set syntax,
and `set()` is a call, correctly not recognized as a display at all), all
via direct AST reproduction before writing tests. Still zero existing
hits, `mypy`/`ruff` both stayed clean, `fact_detector_misuse.py` at 1714
lines (well under the 2000-line hard cap). New tests:
`TestStaticDisplayLoopTargetsRecognizeSetAndDictKeys` in `tests/
test_fact_detector_misuse_alias_edge_cases.py` (493 lines, well under its
own 1200-line cap).

**Three more findings from the next review round, all bounded extensions
of already-established mechanisms, plus one declined as a re-raise of an
already-decided, documented tradeoff.** (1) The tuple-*unpacking* loop/
comprehension branches (the destructured-target sibling of the set/dict-
display fix above) still gated on a hand-rolled `isinstance(node.iter,
(ast.Tuple, ast.List))`/`isinstance(generator.iter, (ast.Tuple, ast.List))`
of their own rather than reusing `_static_display_elements()` -- the
identical drift risk the simple-target case was already fixed for one
round earlier. `for fact, tag in {(rec.bases_fact, "old")}: fact == other`
(a set of tuples) was invisible, as was the identical dict-keys and
comprehension form. Fixed by reusing the already-computed `display_elts`/
`gen_display_elts` local (computed once per branch, shared by both the
simple-target and tuple-unpacking cases) instead of a second, independent
shape check. (2) None of the four loop/comprehension collection branches'
own element-admission gate (`_is_fact_typed_expr(elt, fact_names) or
isinstance(elt, ast.Name)`) recognized a composed expression
(`NamedExpr`/`IfExp`/`BoolOp`) as a candidate worth deferring to
fixed-point time, even though `_candidate_resolves_to_fact()` -- the
function that actually resolves a deferred candidate once `known`/
`aliases` are populated -- already has its own recursive branch for every
one of those three shapes. `old = rec1.bases_fact; new = rec2.
vtable_fact; for fact in (old if cond else new,): fact == other` was
rejected outright at collection time, since `old if cond else new` is
neither already Fact-typed (its own leaves aren't resolved as aliases
yet) nor a bare name. Fixed with one shared `_admissible_loop_element()`
gate (`_is_fact_typed_expr(...)` or one of `_DEFERRED_CANDIDATE_NODE_
TYPES` -- `Name`/`NamedExpr`/`IfExp`/`BoolOp`), replacing all four
independent copies of the old two-clause check at once, so a future
composed shape added to `_candidate_resolves_to_fact()` only needs
updating in this one admission set to reach every collection site. (3)
`Fact[int](...)`/`Fact[int].present(...)` -- a generic specialization of
`Fact`, which subscripting produces as a `_GenericAlias` whose own
`__call__`/attribute access delegates straight through to the real class
-- is still exactly `Fact` at runtime, but the callable is an
`ast.Subscript` (or an `ast.Attribute` whose `.value` is one), invisible
to the constructor-call recognition in `_is_fact_typed_expr()`, which
only ever unwrapped a bare `ast.Name`. Fixed with a single unwrap of an
`ast.Subscript` callee (and, separately, of an `ast.Attribute`'s own
`.value`) before the existing `Name`-in-`fact_names` checks, composing
for free with both existing shapes rather than needing a duplicate check.

(4) **Declined, citing an already-documented rationale rather than
re-implementing.** A module-qualified `Fact` *annotation* (`value: model.
Fact[int]`/`value: fact_module.Fact`) is the identical "no type
inference, match by import spelling" residual `_imported_fact_aliases()`'s
own docstring already documents and accepts for the module-qualified
*constructor-call* form (`fact_model.Fact.present(...)`) -- both trace to
the same root cause (`_is_fact_typed_annotation`'s/`_is_fact_typed_expr`'s
constructor-call branch each assuming a bare `ast.Name` rather than an
arbitrary `ast.Attribute` chain) and the same accepted-gap paragraph in
that docstring ("This module's own module docstring already accepts as
inherent to a pure-AST heuristic, not a specific miss worth chasing
indefinitely"). Documented as covering the annotation spelling too rather
than re-litigated as a fresh finding.

Verified against all three reported repros for findings (1)-(3) (the
destructured set/dict-keys forms and their comprehension duplicates; the
composed-`IfExp` loop element and its comprehension duplicate; both
generic-specialized constructor spellings), plus negative controls for
each (a destructured set with one non-Fact element; an `IfExp` with one
non-Fact branch) and a regression guard confirming the unspecialized
`Fact(...)` form still works, all via direct AST reproduction before
writing tests. Still zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_detector_misuse.py` at 1780 lines (well under the 2000-line hard
cap). New tests: `TestDestructuredLoopsRecognizeSetAndDictKeyDisplays`,
`TestComposedLoopElementsDeferToTheAliasFixedPoint`, and
`TestGenericSpecializedFactConstructorsAreRecognized`, all appended to
`tests/test_fact_detector_misuse_alias_edge_cases.py` (608 lines, well
under its own 1200-line cap).

**One more finding from the next review round, a bounded extension of an
established mechanism.** The `match` statement's whole-subject-capture
handling (a bare `case fact:`/`case SomeClass() as fact:`, or a
whole-subject `MatchOr`) only ever recognized `case.pattern` itself being
a top-level `MatchAs` -- a structural sequence pattern capturing a
*sub*-part of the subject, not the whole thing, was invisible: `match
(rec.bases_fact, tag): case (fact, _): return fact == other` -- `fact` is
definitively the subject tuple's first, Fact-typed element, but neither
existing branch applies (`case.pattern` is an `ast.MatchSequence`, not a
bare `MatchAs`/`MatchOr`-of-`MatchAs`). Fixed with a new
`_paired_match_sequence_candidates()` -- the `match`/`case` sibling of
`_paired_unpacking_candidates()` this module already uses for ordinary
tuple-unpacking assignment -- pairing a structural sequence pattern's own
captures against a statically-known `Tuple`/`List` subject's elements,
positionally, recursing through a nested sequence pattern matched against
a nested `Tuple`/`List` subject element the identical way its assignment-
unpacking sibling already nests. Deliberately **not** all-or-nothing the
way `_paired_unpacking_candidates()` is, though: a non-capturing
sub-pattern at one position (a wildcard `_`, a literal, a class pattern)
does not disqualify a real capture found at another position, since a
structural pattern routinely mixes captures with non-capturing
sub-patterns as completely ordinary code -- only a genuine shape mismatch
(a pattern/subject length disagreement, more than one `MatchStar`) makes
the whole pairing untrustworthy, since then no position can be
confidently attributed to the right subject element at all. A
`MatchStar`'s own captured name (`case (fact, *rest):`) binds a runtime
list, not a single value, and is deliberately not treated as a Fact-typed
candidate here (`_match_pattern_names()` still records it as an ordinary
local bound name, a real shadow, just not an alias source).

**Fixing this correctly required updating one existing test, not just
adding new ones.** `test_ignores_a_nested_capture_inside_a_structural_
pattern` (a pre-existing negative control, added for the whole-subject-
capture fix earlier in this module's history) used `match [rec.bases_fact,
1]: case [x, y]: return x == other` as its repro -- which its own
docstring correctly names as "must not be treated as an alias of the
[whole] subject," but its assertion (`== []`, no finding at all) also
happened to encode the *narrower*, not-yet-fixed gap this same round
closes: once elementwise pairing exists, `x` genuinely *is* `rec.
bases_fact` (the subject's first element), so `x == other` now correctly
IS a real misuse, and asserting `== []` there would pin the bug rather
than guard the fix. Reproducing the pre-fix suite confirmed this test
failed immediately, for exactly that reason, once the new branch landed.
Fixed by changing the test's own subject to a dynamic (non-statically-
known) one, `pair` rather than a literal display, which the elementwise
pairing correctly still returns no candidates for (subject isn't a
recognized static display) -- preserving the test's original, narrower
purpose (guard against the whole-subject-alias mechanism over-eagerly
matching a partial capture) without it silently drifting into pinning a
now-fixed gap.

Verified against the reported repro, a nested-sequence-pattern variant, a
leading-star and a trailing-star variant (confirming a captured position
binds to the correct, Python-semantics-accurate subject element on either
side of a `*rest`), a wildcard-position positive control (a non-capturing
sub-pattern elsewhere must not block a real capture), and negative
controls (a captured but genuinely non-Fact element; a dynamic subject; a
pattern/subject length mismatch), all via direct AST reproduction before
writing tests. Still zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_detector_misuse.py` at 1797 lines and `fact_detector_misuse_scope.py`
at 803 lines (both well under the 2000-line hard cap). New tests:
`TestStructuralSequencePatternCapturesPairWithTheSubject`, appended to
`tests/test_fact_detector_misuse_alias_edge_cases.py` (710 lines, well
under its own 1200-line cap).

**Two more findings on the same pairing machinery (7th review round),
both real, both bounded extensions of the mechanism already built for
the sequence-pattern fix above.** (1) `_paired_unpacking_candidates()`'s
own blanket "any `Starred` element anywhere disqualifies the whole
pairing" rule was one rule too many: a starred *target* element (`fact,
*rest = old.bases_fact, new.bases_fact, extra`) captures a runtime-length
slice with no single corresponding value, but the *fixed*-position
elements before and after it still line up unambiguously against the
value display's own (starless, so fixed-length) elements — the identical
before/after-the-star split `_paired_match_sequence_candidates()` already
uses for a `MatchStar`. A starred *value* element (`x = (*a, b)`) stays a
genuine disqualifier throughout, since a dynamic expansion's own length
isn't statically known. Fixed by splitting the target's own `Starred`
position (at most one, matching Python's own grammar) into before/after
slices and pairing each against the value's own front/back elements by
position, mirroring the sequence-pattern helper's shape exactly; the star
element itself never produces a candidate, matching that helper's
identical treatment of `MatchStar`'s own captured name. This reaches both
a plain assignment and a `for` loop's own unpacking target, since the
latter already reuses this same helper per iteration element. (2) The
sequence-pattern fix's own docstring explicitly scoped itself to
`MatchSequence` and left every `MatchMapping` pattern to the
whole-subject-capture handling, which never applies to a structural,
sub-part-capturing pattern — so `case {"fact": fact}:` against a literal
`{"fact": rec.bases_fact}` subject stayed an unrecognized capture. Fixed
with a new `_paired_match_mapping_candidates()`, the `MatchMapping`
sibling of `_paired_match_sequence_candidates()`: pairs a pattern's own
literal keys against a literal `Dict` subject's own literal keys
independently per key (unlike sequence pairing's positional,
whole-pairing-disqualifying shape, one key missing or unresolvable in the
subject contributes no candidate for *that* key without disqualifying
the others), requires every subject key to be a literal `ast.Constant`
with no `**expansion` entry, and recurses into a further sequence or
mapping sub-pattern the identical way the sequence helper already
recurses into a further sequence. `**rest` is deliberately not treated
as a candidate here either, mirroring `MatchStar`'s identical treatment.

**Two pre-existing tests pinned the exact gap fix (1) closes, and were
corrected rather than left pinning a now-fixed bug** (the same discipline
the sequence-pattern round's own `test_ignores_a_nested_capture_inside_a_
structural_pattern` correction already established): both
`TestElementwiseTupleUnpackingAliases::test_ignores_a_starred_target_
pairing` and `TestForLoopUnpackingTargetsResolveElementwise::test_ignores_
a_starred_unpacking_target` asserted `== []` for a starred-target
unpacking whose *fixed* position is now correctly Fact-typed — reproducing
the pre-fix suite confirmed both failed immediately once the new
before/after-the-star pairing landed, for exactly that reason. Each was
rewritten to isolate its own original, narrower purpose (confirming the
starred capture itself is never treated as a Fact alias — now checked via
a comparison involving the *starred* name specifically) from the new,
correctly-detected fixed-position finding, rather than asserting `== []`
against a case that is now a genuine, non-spurious hit.

Verified against the reported starred-target repro (leading and trailing
star, plain assignment and `for`-loop unpacking), a starred-*value*
regression control (still correctly excluded), and the mapping-pattern
repro plus its own negative controls (`**rest` not blocking a sibling
capture, a key absent from the subject, a dynamic non-`Dict` subject, a
non-literal subject key, a nested sequence pattern inside a mapping
pattern), all via direct AST reproduction before writing tests. Still
zero existing hits, `mypy`/`ruff` both stayed clean,
`fact_detector_misuse.py` at 1815 lines and `fact_detector_misuse_scope.py`
at 917 lines (both well under the 2000-line hard cap). New tests:
`TestStarredUnpackingTargetsStillPairFixedPositions` and
`TestStructuralMappingPatternCapturesPairWithTheSubject`, appended to
`tests/test_fact_detector_misuse_alias_edge_cases.py` (851 lines, well
under its own 1200-line cap).

**One more finding on the def-time scope-resolution machinery (8th review
round): decorator expressions were never given the def-time treatment
their siblings already have.** `_default_and_annotation_scope_overrides()`
already resolves a parameter default/annotation and a `ClassDef`'s own
base/keyword expression against the scope that directly, syntactically
contains the `def`/`class` statement, since Python evaluates each of
those *before* the function/class exists — but a decorator
(`@deco(fact == other)`) is evaluated the identical way, at the identical
time, and this function's own subtree collection had no `decorator_list`
entry for either shape at all. `fact = rec.bases_fact; @deco([x for x in
(fact == other,)]) def f(fact): ...` was silently missed:
`_enclosing_qualnames` assigns the whole `FunctionDef`'s decorator-
adjacent lines to `f`'s own body scope, where the parameter `fact` has
already shadowed the alias. Confirmed as a genuine asymmetry, not a fresh
question: `_lexical_function_parents`'s and `_def_containing_qualnames`'s
own `def_time_subtrees()`/`dispatch()` helpers *already* dispatch a
`def`'s or `class`'s `decorator_list` under the incoming (enclosing)
qualname — this function alone had never been extended to match. Fixed
by adding `decorator_list` to both the function/lambda branch's
`subtrees` (via `getattr`, since `ast.Lambda` carries none) and the
`ClassDef` branch's own base/keyword loop — the identical fix, applied to
the one remaining site that needed it.

Verified against the reported repro, its class-decorator sibling, a
regression control confirming the fix doesn't leak the decorator's own
resolution into the function's real body scope (a genuine parameter
still correctly shadows there), a non-Fact decorator negative control,
and both a nested-function and a method decorator (resolving against
their own real enclosing function/class-body scope respectively,
mirroring the identical class-body-vs-function distinction the existing
method-default handling already draws), all via direct AST reproduction
before writing tests. Still zero existing hits, `mypy`/`ruff` both stayed
clean, `fact_detector_misuse.py` at 1843 lines (well under the 2000-line
hard cap). New tests:
`TestDecoratorExpressionsResolveAgainstTheContainingScope`, appended to
`tests/test_fact_detector_misuse_alias_edge_cases.py` (946 lines, well
under its own 1200-line cap) rather than the more thematically obvious
`test_fact_detector_misuse_def_time_scope.py`, which had only ~105 lines
of headroom left under its own cap.

**One more finding on the whole-subject `match`-capture machinery (9th
review round): a `MatchAs` node can itself nest a *further* `MatchAs`,
not just a structural sub-pattern.** `case fact as alias:` parses as
`MatchAs(pattern=MatchAs(name="fact"), name="alias")` — a genuinely
*nested* `MatchAs`, distinct from `case SomeClass() as fact:`'s own
wrapped `MatchClass` (a real structural sub-pattern, correctly left
alone) — but the existing top-level-capture branch only ever registered
`case.pattern.name` (the outer `alias`), leaving the inner `fact` to
`_match_pattern_names()`'s ordinary local-shadow treatment even though
it is equally a real alias of the whole subject. Fixed with a new
`_matchas_chain_names()`, walking the chain of nested `MatchAs` nodes and
collecting every name along it — for the ordinary, non-chained case it
still returns exactly the single outer name, so this is a strict
generalization, not a behavior change for either existing shape. The
sibling `MatchOr` branch (added in an earlier round for `case (C() as
fact) | (D() as fact):`) had the identical gap one level up: its own
per-alternative check only compared the *outer* name across
alternatives, not the full nested chain, so a mismatched inner name
(`case (fact as alias) | (other_fact as alias):`, where only the
outer name `alias` is actually guaranteed identical by Python's own
same-name-set-across-alternatives grammar rule) could have been
trusted as safe when it isn't. Fixed by requiring every alternative's
*full* `_matchas_chain_names()` result to match the first alternative's,
term for term — the identical "only trust when every alternative is
structured identically" principle that branch's own docstring already
states, now applied at the right granularity.

Verified against the reported repro, the inner-name-alone and outer-
name-alone cases independently, both together in one comparison, the
existing single-level `as`-pattern regression control, an OR pattern
with identical nested chains (still trusted) and one with mismatched
inner names (correctly not trusted), and a negative control confirming
a nested capture inside a genuine structural sub-pattern (`case [x, y as
fact]:`) stays a sub-part capture, not a whole-subject one, via direct
AST reproduction before writing tests. Still zero existing hits,
`mypy`/`ruff` both stayed clean, `fact_detector_misuse.py` at 1862 lines
and `fact_detector_misuse_scope.py` at 945 lines (both well under the
2000-line hard cap). New tests:
`TestNestedMatchAsChainsPropagateWholeSubjectCaptures` (7 tests),
appended to `tests/test_fact_detector_misuse_alias_edge_cases.py` (1035
lines, still under its own 1200-line cap).

**Two more findings on the same commit (10th review round), both in the
structural-pairing per-position handling
(`_paired_match_sequence_candidates()`/`_paired_match_mapping_
candidates()`), not the whole-subject `MatchAs`/`MatchOr` branch the
previous round fixed.** (1) The per-position handling still only ever
extracted `sub_pattern.name` from a bare `MatchAs`, so a *chained*
`MatchAs` at a structural position (`case (fact as alias,): return fact
== other`) recorded only the outer `alias`, missing the inner `fact` --
the identical nested-`MatchAs` shape the previous round's fix already
closed at the whole-subject level, just unreached at the per-position
level. A structural sub-pattern *wrapped* by `MatchAs` at a position
(`case ((fact, _) as alias,):`) fell through every branch untouched
too, since the position's own top-level node is `MatchAs`, not
`MatchSequence`/`MatchMapping` directly. (2) The sequence-pairing
function accepted a *starred* subject (`match (*extras, rec.
bases_fact): case (_, fact):`) with no guard at all -- a dynamic
expansion of unknown length means no pattern position can be
confidently attributed to a known subject element, the identical rule
`_paired_unpacking_candidates()` already applies to a starred *value*
display, just missing on this structural-`match` sibling.

Fixed both per-position gaps with one new shared helper, `_paired_sub_
pattern_candidates()`, which both `_paired_match_sequence_candidates()`
and `_paired_match_mapping_candidates()` now delegate their per-position
step to instead of their own ad hoc `MatchAs`/`MatchSequence`/
`MatchMapping` branching: it unwinds a chained `MatchAs` via the
existing `_matchas_chain_names()` (recording every name along the chain
against the identical sub-subject), then finds the chain's innermost
non-`MatchAs` wrapped pattern -- skipping past every already-recorded
`MatchAs` layer so a name is never registered twice -- and recurses into
it structurally when it is itself a `MatchSequence`/`MatchMapping`.
Fixed the starred-subject gap with the same `any(isinstance(elt,
ast.Starred) ...)` guard `_paired_unpacking_candidates()`'s own value
display already uses, applied to `_paired_match_sequence_candidates()`'s
subject elements before any positional pairing is attempted (mapping
pairing is unaffected -- it pairs by literal key, not position, so a
starred *sequence* element has no analogue there).

Verified against both reported repros (chained `MatchAs` at a sequence
position, starred subject), the chained-`MatchAs` shape reproduced at a
mapping position too, a structural sub-pattern wrapped by `MatchAs`
(both the inner structural capture and the outer wrapping alias,
independently), and every existing sibling case re-verified unaffected
(a plain single-level `case fact:`, a sequence wildcard position, a
`MatchStar`'s own captured name staying an ordinary shadow rather than
an alias, a non-tuple/list subject, an unstarred subject, and a
length-mismatch subject), all via direct AST reproduction before
writing tests. Still zero existing hits, `mypy`/`ruff` both stayed
clean, `fact_detector_misuse_scope.py` at 996 lines (well under the
2000-line hard cap). New tests:
`TestNestedMatchAsChainsInsideStructuralPositions` (3 tests),
`TestStructuralSubPatternWrappedByMatchAsAtAPosition` (2 tests), and
`TestStarredSubjectDisqualifiesStructuralSequencePairing` (2 tests),
appended to `tests/test_fact_detector_misuse_alias_edge_cases.py` (1140
lines, still under its own 1200-line cap).

**Separately, this same round brought PR #929's branch up to date with
`main`, closing a CI `architecture` step failure (`abicheck/contract_
evidence.py:86: model -> policy is forbidden`, a `compare -> model ->
policy -> compare` dependency cycle) that this PR had already documented
as a pre-existing, unrelated base-branch regression (PR comments
5453660592/5453762855).** `main` had since merged a fix for the identical
cycle (PR #931, `fix/contract-evidence-model-policy-cycle`); the branch
was 48 commits behind, so merging `main` in (a clean merge, no conflicts)
was sufficient -- no independent architecture fix was needed on this
branch. Verified with `python scripts/check_architecture.py` reporting
`0 error(s)` on the merge commit.

**A further Codex review round found `_paired_sub_pattern_candidates()`
had no `MatchOr` branch, the identical OR-pattern shape the whole-`case.
pattern` level already recognizes, just unreached at the per-position
level.** `case ((C() as fact) | (D() as fact),):` deterministically
binds `fact` to the sequence position's own Fact-valued element
regardless of which alternative matched -- every alternative is a
top-level `MatchAs` capturing that one position's whole value under the
identical name -- but `_paired_sub_pattern_candidates()`'s `MatchAs`/
`MatchSequence`/`MatchMapping` branches don't match a `MatchOr` node at
all, so `unmigrated_fact_reader_sites()`'s `match_detector_misuse`
sibling (`fact_equality_misuse_sites()`) returned no site. Fixed by
extracting the existing whole-`case.pattern` OR-pattern trust rule
("every alternative is exactly `MatchAs` with the identical full nested
chain of names, via `_matchas_chain_names()`") into a new shared
`_trusted_matchor_chain_names()`, used by both the pre-existing
whole-subject branch (refactored to call it, not reimplement it, so the
already-correct case couldn't silently diverge from the newly-added
one) and a new `MatchOr` branch in `_paired_sub_pattern_candidates()`
itself -- deliberately not recursed into an alternative's own wrapped
structural sub-pattern, matching the whole-subject branch's identical
restriction, rather than widening the trust rule at the same time as
relocating it.

Verified against the reported repro, the identical shape reproduced at a
mapping position, three negative controls (mismatched alternative names,
one alternative binding a field rather than the whole value, a chained
`MatchAs` with mismatched inner names), and the whole-subject `MatchOr`
branch re-verified unaffected by the refactor (both its positive and
negative case), plus every other `_paired_sub_pattern_candidates()`
sibling form (plain `MatchAs` at a position, a chained `MatchAs`, a
structural sub-pattern wrapped by `MatchAs`, the starred-subject guard),
all via direct AST reproduction before writing tests. Still zero
existing hits, `mypy`/`ruff` both stayed clean, `fact_detector_misuse.py`
at 1842 lines and `fact_detector_misuse_scope.py` at 1057 lines (both
well under the 2000-line hard cap). New tests split into a dedicated
sibling file, `tests/test_fact_detector_misuse_matchor_structural_
positions.py` (7 tests, four classes:
`TestMatchOrAtAStructuralSequencePosition`/
`TestMatchOrAtAStructuralMappingPosition`/
`TestWholeSubjectMatchOrStillWorksAfterTheSharedHelperRefactor`), rather
than appended to `test_fact_detector_misuse_alias_edge_cases.py`, which
had only ~60 lines of headroom left under its own 1200-line cap.

**Three more findings on the same commit (a further Codex review round),
all bounded extensions -- one self-inflicted regression caught and fixed
before landing.**

(1) **`case (fact,) as whole:` -- a structural pattern *wrapped* by a
top-level `as`-pattern -- recorded only `whole`, never recursing into
its own wrapped `MatchSequence`.** The whole-`case.pattern` dispatch in
`fact_equality_misuse_sites()` treated `MatchAs` exclusively as a
whole-subject capture (an inline reimplementation of a *subset* of the
rules `_paired_sub_pattern_candidates()`'s own per-position handling
already states in full, including this exact wrapped-structural-pattern
case). Fixed by deleting the whole-`case.pattern` dispatch's own five
`if`/`elif` branches entirely and delegating the whole thing to
`_paired_sub_pattern_candidates(case.pattern, node.subject)` -- the same
shared primitive, reused whole rather than kept as two independently-
maintained subsets of the same rules that could (and, per this finding,
already had) silently diverge again. Verified every existing whole-
subject shape unaffected (bare capture, `SomeClass() as fact`, a chained
`MatchAs`, the whole-subject `MatchOr`, a structural sequence/mapping
sub-part, a bare wildcard contributing no candidate) plus the new
wrapped-structural shape at both a sequence and a mapping position, all
via direct AST reproduction before writing tests.

(2) **A literal display indexed at a statically known position/key
(`(rec.bases_fact,)[0]`, `{"x": rec.bases_fact}["x"]`) fell through
`_is_fact_typed_expr()` entirely -- `ast.Subscript` had no branch at
all.** Fixed with a new `Subscript` branch: a literal `ast.Tuple`/
`ast.List` indexed by a literal integer resolves to that element (a
`Starred` element anywhere disqualifies the whole display, the identical
rule `_paired_unpacking_candidates()` already applies to a starred value
display); a literal `ast.Dict` indexed by a literal key resolves to the
matching value (a `**expansion` entry disqualifies the whole display,
the identical rule). **A negative literal index (`[-1]`) needed its own
unwrap**: Python parses it as `ast.UnaryOp(op=ast.USub(), operand=
ast.Constant(...))`, not a bare `ast.Constant` -- caught by this fix's
own first round of empirical verification (the negative-index repro
failed against the initial implementation, before any test was written
for it) rather than shipping the gap silently. Composes for free with
every other recursive shape (`IfExp`/`BoolOp`/`NamedExpr`/a nested
resolved element), verified unaffected. **One residual, documented in
the function's own docstring rather than chased further**: this does
*not* recurse into the display itself when it is a further, resolvable
`Subscript` (`((rec.bases_fact,), 1)[0][0]`, two levels of indexing
before ever reaching a literal display) -- doubly-indirect subscript
chaining over a literal display has no real precedent in this codebase,
unlike the single-level form the reported finding actually named.

(3) **`def f(Fact, other): return Fact(1) == other` -- an ordinary
parameter reusing the constructor's own name -- was still treated as the
real constructor.** `_is_fact_typed_expr()`'s constructor-call
recognition is a pure, scope-blind lookup against a single whole-tree
`fact_names` set, with no shadow-awareness at all, unlike the shadowing
this module already applies to *alias* names via `_fact_aliases()`.
**The first fix attempt was wrong, caught before landing by direct
reproduction against a genuine import (this file's own "verify every
sibling form" discipline catching a self-inflicted bug, not just the
reported one):** reusing `_fact_aliases()`'s own broader internal
`locally_bound` set (returned as a new second value from that function)
seemed like the natural, already-computed answer -- but that set also
records every `ast.ImportFrom` binding, and a genuine `from abicheck.
model.fact import Fact` (the *correct*, ordinary way to bring the real
constructor into scope at all) is itself exactly such a binding.
Subtracting that broader set silently treated the legitimate import that
establishes `Fact` as though it *shadowed* `Fact`, disabling constructor
recognition module-wide the moment any file imported it normally --
confirmed by direct reproduction (`from abicheck.model.fact import
Fact; def f(other): return Fact(1) == other` stopped being detected at
all) before a single test was written, and reverted in full. Fixed
instead with a new, deliberately narrower `_locally_bound_parameter_
names()`, collecting *only* function/lambda parameter names (never
imports or assignment targets) per function's own qualname -- a
parameter is never a legitimate way to bind the real `Fact` class,
unlike an import, so this collector carries no equivalent risk. Also
needed closure-scope inheritance the parameter-only collector doesn't
give for free: `def outer(Fact): def inner(other): return Fact(1) ==
other` -- `inner`'s own parameter set is empty, since `Fact` is
`outer`'s parameter, but `inner` still genuinely closes over it -- so a
new `_shadowed_constructor_names()` walks the real lexical-function-
parent chain (`_lexical_function_parents()`, the identical closure-scope
chain `_fact_aliases()`'s own alias inheritance already walks), unioning
every ancestor's own bound-parameter set. Verified against the reported
repro, a genuine unshadowed import (both bare `Fact` and an aliased
`Fact as F`, both classmethod and plain-constructor spellings -- the
exact regression class the reverted first attempt introduced), a
sibling function with no shadowing parameter still detecting the real
constructor, single/double/class-nested closure inheritance of the
shadow, an unrelated attribute-field-access recognition path staying
unaffected by the shadow, and a shadowed imported-alias name too, all
via direct AST reproduction before writing tests.

Still zero existing hits across all three, `mypy`/`ruff` both stayed
clean, `fact_detector_misuse.py` at 1896 lines and `fact_detector_
misuse_scope.py` at 1109 lines (both well under the 2000-line hard cap).
New tests split into three dedicated sibling files (none of the
existing files had enough headroom left under their own 1200-line caps
to safely absorb this many new cases): `tests/test_fact_detector_
misuse_as_pattern_wraps_structural.py` (10 tests),
`tests/test_fact_detector_misuse_static_subscript.py` (20 tests), and
`tests/test_fact_detector_misuse_constructor_shadow.py` (11 tests, the
`TestGenuineImportStillRecognizedAfterTheFix` class specifically pinning
the self-inflicted regression the first attempt introduced, not just
the originally reported finding).

**A further Codex review round found two more real gaps in the same two
fixes, both closed -- and this round's own instruction was explicit
about not repeating the previous round's self-inflicted mistake: verify
against a realistic sibling case, not just a synthetic one, before
calling either fix done.**

(1) **`_locally_bound_parameter_names()` covered only parameters, so
every *other* local-binding form still shadowed nothing**: `Fact =
lambda x: x`, `for Fact in factories:`, and a comprehension target named
`Fact` were all still read as the real constructor and called, even
though Python resolves each to the local value, not `model.fact.Fact`.
Renamed and widened to `_locally_bound_constructor_shadow_names()`,
covering parameters, plain/annotated-assignment/walrus/`for`-loop/
comprehension targets (via `_bound_names()`, so nested tuple-unpacking
targets are covered too), and imports -- with the identical carve-out
the reverted first attempt's mistake already established, now applied
correctly instead of blanket-excluded: an `ast.ImportFrom` only counts
as a shadow when its own `alias.name` is *not* literally `"Fact"` -- the
exact structural test `_imported_fact_aliases()` itself uses to decide a
name belongs in `fact_names`, so the two functions cannot disagree.
`from x import Fact`/`from x import Fact as F` (any source module --
this module's own established "match by name alone" stance) are
correctly *not* shadows regardless of `x`; `from x import SomethingElse
as Fact` (renaming an unrelated import to the exact spelling `Fact`) *is*
one, since `_imported_fact_aliases()` never adds a name to `fact_names`
for that shape (it requires the *original* imported name to be `"Fact"`,
not the local alias) -- without the exclusion, a bare `ast.Import` is
never exempt at all, since `_imported_fact_aliases()` only ever
recognizes `ImportFrom`. Deliberately still narrower than `_fact_
aliases()`'s own general-binding walk in one respect, documented as an
accepted residual rather than reused outright: `match`/`case` pattern
captures and `with`/`except ... as` targets aren't collected, since each
would need the identical multi-round hardening `_fact_aliases()`'s own
history already applied to its *general* collection before it could be
trusted for this independent purpose too -- reusing that machinery
directly is exactly the coupling the first, reverted attempt already
showed is dangerous.

Verified against all three reported shapes, five more sibling forms
(annotated assignment, walrus, a renaming import, a bare `import ...
as Fact`, a nested tuple-unpacking target), and -- this time -- the
exact realistic sibling case the previous round's instruction named
explicitly: a genuine, unrenamed `from abicheck.model.fact import Fact`
(and its aliased `as F` form) confirmed still detected, a classmethod
constructor confirmed still detected, a sibling function with no local
shadow confirmed unaffected, and a shadow correctly *not* leaking past
its own function into an unrelated one -- all via direct AST
reproduction before writing a single test, closing the exact gap the
first attempt's own skipped step left open. No self-inflicted regression
this round.

(2) **The static-subscript fix's own selected element was still passed
through the purely structural `_is_fact_typed_expr()` alone**: `fact =
rec.bases_fact; (fact,)[0] == other` -- a bare alias inside an otherwise
statically resolvable display -- went unrecognized, since `_is_fact_
typed_expr()` deliberately never resolves a bare `ast.Name` (that needs
`known`/`aliases`, which a structural predicate alone doesn't have).
Fixed by extracting the resolution step itself into a new
`_static_subscript_element()` (mirroring `_static_display_elements()`'s
own "one extraction, several alias-aware/structural callers" shape,
carrying forward the identical negative-index/`Starred`/`**expansion`
rules the original fix already stated), then routing it through *both*
of this module's alias-aware resolvers -- a new `Subscript` branch in
`_candidate_resolves_to_fact()` (the fixed-point resolver, recursing
through itself with `known` in scope) and a new `Subscript` branch in
`fact_equality_misuse_sites()`'s own `is_fact_typed()` (the top-level
comparison-operand resolver, recursing through itself with `qualname` in
scope) -- instead of only ever landing back in the purely structural
`_is_fact_typed_expr()`, whose own Subscript branch was simplified to
call the shared extraction helper too.

Verified against both reported repros (tuple and dict displays), the
identical shape with the selected element further assigned to a new
local before comparison (exercising the fixed-point resolver
specifically, not just the top-level one), a chained alias inside a
subscript (`fact2 = fact`, composing the new Subscript recursion with
the fixed point's own existing alias-chain resolution), a negative-index
alias, a wrong-position alias staying correctly unrecognized, and every
pre-existing literal-display/non-literal-index/starred-display/non-alias
regression re-verified unaffected, all via direct AST reproduction
before writing tests.

Still zero existing hits across both, `mypy`/`ruff` both stayed clean,
`fact_detector_misuse.py` at 1959 lines (only ~41 lines of headroom left
under the 2000-line hard cap -- the very next finding in this area will
likely need a split into a sibling module) and `fact_detector_misuse_
scope.py` at 1170 lines (well under its own cap). New tests appended to
the two dedicated sibling files these two mechanisms already own (both
had ample headroom, unlike the script file itself):
`TestNonParameterShadowsSuppressRecognition` (9 tests) in
`tests/test_fact_detector_misuse_constructor_shadow.py`, and
`TestAliasResolutionInsideAStaticSubscript` (8 tests) in
`tests/test_fact_detector_misuse_static_subscript.py`.

**A further round tracked assignment aliases of the `Fact` constructor
itself** (Codex review, fresh evidence): `F = Fact` or `make_fact =
Fact.present`, followed by `F(1) == other`/`make_fact(1) == other`,
went unrecognized -- `F`/`make_fact` is a genuine, if local, name for
the identical constructor, not merely a shadow of it, but neither
`_is_fact_typed_expr()`'s scope-blind lookup nor the shadow-tracking
fix above had any notion of a local rebinding *extending* the
constructor set. Reproduced both sub-cases directly (0 hits, expected
1 each) before designing a fix.

Given `fact_detector_misuse.py`'s tight headroom (1959/2000 lines),
both new collectors -- `_constructor_alias_names()` (`F = Fact`: a
bound name behaves exactly like `Fact` itself, in both call and
further-attribute-access position, so it's *added* into the effective
`fact_names` set the same way a shadow is subtracted) and
`_constructor_method_alias_names()` (`make_fact = Fact.present`: an
*unbound classmethod reference*, tracked as its own, separate set,
recognized only against a direct `ast.Call` whose `func` is a bare
`ast.Name`, never composed into the general substitution set -- a
further attribute access off it, `make_fact.foo`, would be nonsensical)
-- went into `fact_detector_misuse_scope.py`, which had ample room. The
existing hand-rolled closure-scope-chain walk in `fact_detector_misuse.
py` (`_shadowed_constructor_names()`) was also generalized into a new
shared `_scope_chain_union()` helper (in the scope module) and reused
for both the shadow set and the two new alias sets, netting a small
reduction in the primary file's own line count that offset most of the
new wiring code -- both fixed-cap files stayed within budget without a
module split this round. Both `F = Fact` and `make_fact = Fact.present`
are recognized only via a plain single-target `ast.Assign` (no type
inference, "one hop only" -- a further transitive rename `G = F` is a
documented, accepted residual, the identical limit already accepted
elsewhere in this module's alias tracking).

**Proactive sibling verification for this round found a real false
positive of its own, before it shipped, not a reported finding**:
`def f(Fact, other): F = Fact; return F(1) == other` has a parameter
named `Fact` shadowing the real constructor for the whole function, so
`F = Fact` binds `F` to that *parameter's* runtime value, not to the
real constructor -- registering `F` as a constructor alias
unconditionally would have fabricated a misuse site out of an unrelated
local rebinding, and the identical hazard applies to the classmethod-
alias form. Fixed by threading `locally_bound_shadows`/`lexical_parents`
into both new collectors and skipping registration whenever the RHS
name is itself shadowed anywhere in its own closure-scope chain (via
the same `_scope_chain_union()` walk), including through a nested
closure over the shadowing outer scope.

Verified via direct AST reproduction against 20 cases before writing
any test: both reported sub-cases, a classmethod alias of a different
method, aliasing through an already-import-aliased name (both bare and
classmethod forms), nested-closure inheritance of both alias kinds, a
sibling function unaffected, a bare alias composing with a further
attribute call, the transitive (two-hop) alias correctly *not* chased,
both alias kinds correctly *not* recognized via tuple-unpack (not a
single target), two negative controls (aliasing an unrelated name,
aliasing a non-`Fact` attribute access), both pre-existing regressions
(bare import, classmethod constructor) still recognized, the
shadow-guard false positive fixed in both forms, and the shadow-guard
holding through a nested closure. `mypy`/`ruff` both stayed clean;
`fact_detector_misuse.py` at 1966 lines and `fact_detector_misuse_
scope.py` at 1300 lines, both still under their respective caps. New
tests: `TestConstructorAssignmentAliasesAreRecognized` (13 tests) and
`TestShadowedConstructorSuppressesItsOwnAlias` (3 tests), both appended
to `tests/test_fact_detector_misuse_constructor_shadow.py`.

**A further round found the enclosing-shadow walk itself was too blunt**
(Codex review, fresh evidence): `def outer(Fact): def inner(other):
global Fact; return Fact.present(1) == other` -- `inner`'s own `global
Fact` statement is Python's ordinary override of the closure rule for
that name: it routes *every* reference to `Fact` inside `inner` straight
to module scope, completely bypassing `outer`'s own shadowing parameter.
`_scope_chain_union()`'s unconditional walk had no notion of `global`
statements at all and still unioned `outer`'s shadow in, suppressing a
genuine misuse site of the real, unshadowed constructor. Reproduced
directly (0 hits, expected 1) before designing a fix.

Fixed with a new `_global_declared_names()` collector in
`fact_detector_misuse_scope.py` (maps each function's own qualname to
the names it declares via a direct `global` statement in its own body)
and an optional `global_names` parameter on `_scope_chain_union()`
itself: a name the *starting* qualname declares `global` is excluded
from every non-module ancestor's own contribution to the walk, while
still receiving whatever `"<module>"`'s own entry says, since the walk
reaches the module scope as its terminal node regardless. Threaded
through all three call sites that need it -- `is_fact_typed()`'s own
shadow-subtraction, and both `_constructor_alias_names()`'s and
`_constructor_method_alias_names()`'s own internal shadow checks (the
identical hazard applies to an alias sourced from a `global`-declared
name: `global Fact; F = Fact` must still register `F` as a real alias).
The alias-*addition* half of `is_fact_typed()`'s own union (constructor
aliases bound in an ancestor scope, reached via ordinary closure) is
correctly left unaffected -- `global` changes what a *name* resolves to
within the declaring scope, not whether a *different* scope's own local
binding remains visible to its own nested closures the normal way.

Verified via direct AST reproduction against 10 cases before writing
tests: the reported case, the bare-call form, both new alias
collectors' own shadow checks correctly bypassed too, a doubly-nested
case where only the innermost scope declares `global` (bypassing two
levels of shadowing parameter at once), a regression guard confirming
the shadow still suppresses recognition with the `global` statement
removed, a `global` declaration with no shadowing parameter left
completely unaffected, sibling-function isolation, `global` of an
*unrelated* name correctly not bypassing a genuine `Fact` shadow (the
bypass is keyed by name, not merely "a global statement exists in this
scope"), and the subtle case where the module-level `Fact` itself is
shadowed by an ordinary module-level assignment (`global` only means
"look at module scope" -- it doesn't mean the module-level binding is
the real constructor, so recognition correctly stays suppressed there
too). `mypy`/`ruff` both stayed clean; `fact_detector_misuse.py` at 1974
lines (headroom now tight -- ~26 lines left under the 2000-line hard
cap; the next finding in this area will very likely need a further
split) and `fact_detector_misuse_scope.py` at 1356 lines (still ample).
New tests: `TestGlobalDeclarationBypassesEnclosingShadow` (10 tests),
appended to `tests/test_fact_detector_misuse_constructor_shadow.py`.

**A further round found the shadow collector recorded only a `def`'s
own parameters, never a nested `def`/`class` statement's own name**
(Codex review, fresh evidence): `def outer(other): def Fact(x): return
x; return Fact(1) == other` was still read as the real constructor,
since nothing recorded the nested function's own name as a binding in
its containing scope -- the identical `STORE_NAME`/`STORE_FAST` rule an
ordinary assignment already gets. Reproduced directly for both the
`def Fact` and `class Fact` forms (0 hits each, expected 0 -- i.e. both
were false positives: the constructor was wrongly recognized) before
designing a fix.

Fixed entirely inside `fact_detector_misuse_scope.py`'s existing
`_locally_bound_constructor_shadow_names()`: resolves each `FunctionDef`/
`AsyncFunctionDef`/`ClassDef` node's own containing qualname via the
already-existing `_def_containing_qualnames()` helper (already used
elsewhere in this module for the identical "a def/class statement's own
name binds into whatever namespace textually contains it" question --
distinct from the closure-scope chain `_lexical_function_parents`
answers, since a def/class statement's binding target is never about
free-variable lookup) and records the definition's own `.name` there.
No change to `fact_detector_misuse.py` itself was needed at all --
deliberate, given that file's own tight remaining headroom (1974/2000
lines) after the previous two rounds.

Verified via direct AST reproduction against 10 cases before writing
tests: both reported forms, each correctly scoped to its own containing
function only (a sibling function unaffected), a further-nested closure
correctly inheriting the def-shadowed name, a module-level `def Fact`
also shadowing, both pre-existing regressions (bare import, classmethod
constructor) still recognized, an unrelated nested `def helper(...)`
correctly not affecting recognition, and the trickiest composition --
a sibling nested `def Fact` in the same enclosing function that would
otherwise shadow it, with an inner `global Fact` still correctly
bypassing straight to module scope, confirming the two mechanisms
(this round's def/class shadow and the previous round's global bypass)
don't interfere with each other. `mypy`/`ruff` both stayed clean;
`fact_detector_misuse.py` unchanged at 1974 lines and
`fact_detector_misuse_scope.py` at 1369 lines (still ample headroom).
New tests: `TestNestedDefinitionsShadowTheConstructorName` (10 tests),
appended to `tests/test_fact_detector_misuse_constructor_shadow.py`.

---

### Phase 1 — finish the `dump`/`scan` typed-API convergence (closes AGENTS.md "PR C")

**Goal.** `dump` and `compare`'s implicit-dump operand execute through the
same `resolve_dump_request`/`execute_dump_request` pair; `scan`'s candidate
resolution executes through the shared `workflows.artifact.execute.
_resolve_side_snapshot_impl` primitive that pair itself calls internally
(already landed, per AGENTS.md's own record — `scan` has no `DumpRequest`-
shaped input for `resolve_dump_request`/`execute_dump_request`'s own
signature to accept, so it converges one layer lower, not on that pair
verbatim; `service_input_resolution` is only a delegating facade
re-exporting this module's owner, per that facade's own docstring). No
entry point hand-rolls its own L2 seed, ADR-039 collector call, or AST
cache key.

**Design.** This is not new design — AGENTS.md's "PR C" note already
names the two blockers precisely and one is closed:

1. *(Closed, carried over from main)* `InputSpec.compile_db_filter` exists
   and is threaded through `resolve_dump_request`/`resolve_compare_request`.
2. *(Open — the actual work of this phase)* The default header backend
   (castxml) must be available in CI/dev environments capable of running
   this migration's parity tests; every measurement backing "PR C" so far
   is clang-only. Either (a) obtain a working castxml build for the
   parity-test lane (this plan's own investigation found conda-forge
   0.7.0 segfaulting inside `clang::ParseAST` in this environment — file
   that as its own upstream-castxml investigation, tracked separately, not
   blocking this phase's clang-only half), or (b) explicitly scope this
   phase's first landing to the clang backend and track the castxml
   parity gap as a named residual the same way AGENTS.md already does for
   every other castxml-unavailable finding in this file. **If (b) is
   taken, this phase's own Acceptance criteria below must be scoped to
   match, not left stating unqualified convergence** — `--ast-frontend`
   defaults to castxml, so landing the typed execution path for clang
   alone while the default production backend stays on the untouched
   legacy path means `dump`'s actual default invocation has not
   converged, and claiming "every build shape in the parity corpus" passes
   would silently overstate that for any reader who doesn't separately
   check which backend each shape ran under. Taking (b) makes this phase's
   real deliverable "the clang backend converges, verified; the castxml
   backend is a tracked, named incomplete prerequisite, not a residual
   detail" — restated explicitly in the Acceptance criteria below, not
   left implicit in this Design section alone.

Once unblocked: route `perform_elf_dump`/`handle_non_elf_dump` through
`execute_dump_request` — the one remaining routing step (this phase's
worklist is smaller than the Goal above might suggest: `scan_engine.
_build_new_snapshot` needs no further work here, since its own routing
onto `_resolve_side_snapshot_impl` already landed per AGENTS.md's own
record). Fold the legacy `-p`/`--compile-db` auto-match into the L3→L2
fold as the *sole* source of compile-database-derived context when the
fold applies (already decided and landed per AGENTS.md's "legacy-match
overlap" entry) rather than re-deciding it here.

**A third dump execution path exists, untouched by either of the two
above, and review caught this plan not naming it: the binary-less
`dump --sources`/`--build-info` branch (no SO_PATH), which calls
`cli_buildsource.dump_source_only()` — a pipeline that collects L3-L5
evidence into an otherwise-empty snapshot with no `resolve_input` call at
all, confirmed by reading the real code.** `execute_dump_request()`
already refuses this shape explicitly (`ValidationError` when `InputSpec.
path is None`), with its own docstring stating exactly why this plan must
not paper over: `InputSpec.path` was deliberately widened to `Path | None`
so the shape is *expressible* as a typed request (letting `--dry-run`
resolve one through `resolve_dump_request()`), but *executing* it is "a
genuinely different pipeline... and routing it through here is its own
slice, not part of making the model able to say it" — the exact words the
function's own comment already uses. Landing this phase's two named
routings (`perform_elf_dump`/`handle_non_elf_dump`, `scan_engine.
_build_new_snapshot`) while leaving `dump_source_only()` as a third,
independent assembler would leave exactly the outcome this phase's own
goal forbids — more than one real dump pipeline after the phase ships,
just one fewer than before. **Not migrated in this phase**: routing
`dump_source_only()`'s L3-L5-only collection through `execute_dump_
request()` needs the executor to support a snapshot with no binary-derived
L0-L2 facts at all, which is a real, separate design question (what does
"the executor's post-processing hooks" — ADR-039's collector, the G31
header-graph attach — even mean for a snapshot with no ELF/PE/Mach-O side
to attach them to?), not a drive-by extension of the two routings this
phase already does. Tracked explicitly as this phase's own named residual,
the same way AGENTS.md tracks its other incomplete-migration findings,
rather than silently left for a future reader to discover was never
covered.

**Files.** `abicheck/cli_dump_helpers.py` (`perform_elf_dump`/
`handle_non_elf_dump` → call `execute_dump_request` instead of `dumper.
dump()` directly, keeping every existing post-processing hook —
ADR-039 collector, G31 header-graph attach, clang-layout-tool attach — as
hooks the executor calls, not logic removed); `abicheck/
service_dump_pipeline.py` (the executor gains the hook points);
`abicheck/cli.py`'s `dump_cmd` (already builds a real `DumpRequest` per
AGENTS.md's record — this phase is where it starts being what actually
runs, not only what `--dry-run` renders).

**Tests.** `tests/test_dump_cli_typed_api_parity.py`'s existing
`_BUILD_SHAPES`/`xfail`-gated known-divergent-shape mechanism becomes the
acceptance gate: every shape currently marked `xfail` for a *named,
diagnosed* divergence must flip to passing, with no new shape added to the
divergent list. A shape that cannot be closed this phase is demoted to a
tracked AGENTS.md "Known gaps" entry with the same rigor the existing ones
carry (a real repro, a named mechanism, not a guess).

**Acceptance criteria.** `dump`'s CLI path and `execute_dump_request`
produce bit-for-bit identical snapshots (modulo timestamps/provenance) for
every build shape in the parity corpus **under the clang backend, and for
the binary-having (SO_PATH) case only** — this phase does not claim
convergence for `--ast-frontend castxml`, the
actual default, while option (b) above is in force, **nor for the
binary-less `dump --sources`/`--build-info` shape, which still executes
through `cli_buildsource.dump_source_only()`, a third pipeline this phase
explicitly does not migrate** (per the named residual above); a reader
checking this phase's own status must be able to see "clang + SO_PATH:
converged and verified; castxml: not yet verified; source-only: not
migrated at all" without inferring any of the three from the Design
section. `cli_dump_helpers.
render_dump_dry_run()` is deleted and `--dry-run` renders from the real
`ResolvedDumpRequest` for both backends (the dry-run path itself has no
castxml-specific execution to be blocked on). PR 3C (removing `dump
--build-query`/`--build-compile-db`, currently blocked on this per the
plan's own ordering rule) unblocks as a follow-on, not part of this phase,
and — per the same scoping — only once castxml parity closes too, not on
the strength of clang-only convergence alone, since `--build-query`/
`--build-compile-db` are reachable under either backend.

---

### Phase 2 — `EntityId`/`ScopePath` as the one identity primitive

**Goal.** Every place that currently computes identity from a string
(dict key, `name`, `qualified_name`, a synthetic ctor/dtor key) instead
computes it through one shared `EntityId` resolver — **"computed once"
here means one algorithm, called the same way everywhere, not a value
cached anywhere on the model; see the carrier note below for why, and for
where genuine per-snapshot caching actually lands.**

**Design.** `abicheck/model/identity.py`: `ScopePath` (an immutable tuple
of typed segments — `Namespace(name)`, `Record(name, access)`,
`InlineNamespace(name, version_tag)`, `Anonymous(kind, ordinal)`,
`LocalToFunction(owner)`) names only the *containing* scope, never the
leaf declaration itself.

**Each segment type states which of its own fields are identity and which
are payload — a bare `@dataclass(frozen=True)` would make every field
identity by default, which is wrong for at least one of the five.**
`Record(name, access)`'s `access` (public/protected/private) is carried
*on* the segment because a nested record's access is a real fact a
consumer may want, but it is not part of *where* the nesting scope is —
two snapshots of the same class with a member's access level changed
still name the identical containing scope, and `EntityId` is what diff
matching keys on (Phase 2's own stated purpose). Making `access` part of
`Record`'s equality/hash would turn an access-level change into a
spurious identity mismatch — the matcher would see "removed, then added"
at a different `EntityId` instead of "this declaration changed," for a
property this plan does not intend identity to track. `Record` therefore
defines `__eq__`/`__hash__` over `name` alone (`access` stays a plain,
non-identity field, the dataclass equivalent of `field(compare=False)`).
`Anonymous(kind, ordinal)`/`LocalToFunction(owner)` are the opposite case:
both fields *are* identity, deliberately, since nothing else disambiguates
two sibling anonymous structs or two same-named locals in one function —
an `ordinal`/`owner` that is dropped from identity would silently
re-introduce exactly the sibling-collision class this phase's own
`(ScopePath, kind, leaf_name, extra)` correction exists to close, one
level down. `ordinal` is a deterministic per-parent sequence
number assigned at parse time (the same position-in-the-scope-stack
counter `entry.scope`'s widening already has to track to build
`Anonymous` segments at all, not a second counter invented for this), not
a DWARF offset or other environment-sensitive value that could differ
between an otherwise-identical old/new pair — deterministic *within one
parse*, which is what makes it a legitimate disambiguator for two
anonymous siblings that coexist in the same snapshot.

**This is not the same claim as "stable across revisions," and a first
draft of this phase's wording did not distinguish the two — review
correctly caught that an ordinal is a within-parse index, not an
across-snapshot identity.** Inserting a new anonymous sibling before
existing ones (an ordinary, unremarkable source edit — a new anonymous
`union` added earlier in a header than existing ones) shifts every later
sibling's ordinal, which changes their `ScopePath` and therefore their
whole `EntityId` even though nothing about those later siblings' own
declarations changed — the old/new matcher would read every one of them
as "removed, then re-added at a new identity," exactly the false-positive
shape this plan's own diff-matching discipline exists to eliminate, not
introduce at a lower level. **No stable discriminator for this case is
adopted here, and none is asserted as if one were** — the two candidates
this codebase already has experience with are each independently
documented, in AGENTS.md's own "Known gaps," as unreliable for this exact
purpose: a source-location anchor (`file:line:col`, the same shape
AGENTS.md's "lambda-closure churn" entry already names as "per-translation-
unit and compiler-ordering dependent... a rebuilt consumer can fail to
resolve the symbol," reproduced there as a real false-positive source,
not merely a theoretical risk) and a structural/content fingerprint of the
anonymous scope's own members (circular here specifically — those
members' own identity is what `ScopePath` is being built to resolve, so
fingerprinting them to identify their *parent* scope has nothing yet to
fingerprint). Reconciliation semantics that treat an ordinal shift as a
rename rather than a removal-and-addition (matching on the *shifted*
sibling set's relative order, or deferring to a different signal when an
insertion is detected) are a real, separate design this plan does not
attempt to pick under continued review pressure for the third time in
this same section. Until designed, this is an accepted, documented
limitation of `Anonymous` identity specifically — the same "attempted
twice, reverted twice... accept the... limitation" discipline this
codebase's own AGENTS.md already establishes for comparably-shaped
identity problems (anonymous-type-marker collisions, the ctor/dtor
lambda-closure entries) — not a silent gap this plan is claiming away. `Namespace(name)`/
`InlineNamespace(name, version_tag)` are identity on every field
unconditionally — a namespace has no non-identity payload to exclude, and
an inline namespace's `version_tag` is exactly the dimension ADR-025's own
versioned-inline-namespace-alias handling already keys matching on, so
excluding it here would silently re-widen the `v1`/`v2`-shaped collision
that machinery exists to avoid. **`EntityId` therefore always carries the leaf
declaration's own name as an explicit component, for every kind — not
only for functions.** A first draft of this phase defined `EntityId` as
just `(ScopePath, kind)`, which collides any two sibling declarations of
the same kind in the same scope (`ns::A` and `ns::B`, two enums, two
variables, two typedefs — the function-overload collision this phase
already fixed once is one instance of this same shape, not a
function-specific special case, and fixing it only for functions left
every other kind exposed). The corrected shape is `EntityId = (ScopePath,
kind, leaf_name, extra)`, where `leaf_name` is the declaration's own
(unqualified) name for every kind, and `extra` is kind-specific and empty
for most kinds — `()` for a record/enum/typedef/constant, the mangled-name
discriminator described below for a variable specifically, and the
callable-signature discriminator described further below for a function
specifically (the one case a bare name is still insufficient, since two
overloads share both scope and name). `OccurrenceId` (an `EntityId` plus a
disambiguator for the already-documented "two declarations, one identity"
case ADR-062 Phase 0 already solves at the storage layer — reused here,
not reinvented). Generalizes ADR-046/048's source-graph identity (already
real, `USR`-based) by making `EntityId` the *single* identity both the
flat snapshot and the source graph reference, rather than two graphs with
their own identity schemes that happen to usually agree.

**A variable's `EntityId` carries its own mangled spelling in `extra` —
a bare `(ScopePath, "variable", leaf_name, ())` is not enough either, for
the identical reason AGENTS.md already states for the pre-existing
matcher this identity replaces: two exported variables sharing the same
scope and leaf name but differing mangled names (e.g. two distinct,
non-overloadable template-instantiation statics, or a declaration-vs-
definition spelling mismatch the mangler doesn't collapse) are two
different exports, not one — "variables enable no alias tier at all ...
a display-name join would hide a real removal" (AGENTS.md's own
`finding_identity.py`/`SymbolIdentityIndex` entry). A first draft of this
phase gave every non-function kind the identical empty `extra = ()`,
which collapses exactly that pair into one `EntityId` and would make
Phase 2's `diff_symbols.py` migration pair the wrong two variables (or
miss a real removal) wherever it currently relies on
`SymbolIdentityIndex`'s mangled-name-only matching. Fixed the same way
the function case is: `EntityId`'s variable variant is `(ScopePath,
"variable", leaf_name, extra=mangled_name)` when a mangled name exists
(the common case for any variable with external linkage), falling back to
`extra=()` only for the genuinely mangling-free case (no linker symbol at
all — e.g. a variable known only from a header declaration with no
corresponding binary evidence), mirroring the function fallback's own
scope for exactly the same reason rather than inventing a second rule.

**A function's `EntityId` carries a callable-signature discriminator —
`ScopePath` plus a bare name is not enough.** `f(int)` and `f(double)` share
the same `ScopePath` and the same `function` kind discriminator; without a
third component, `EntityId` collapses two genuinely distinct overloads into
one id, and since this phase directs diff matching and every other
semantic consumer to key on `EntityId` rather than re-deriving their own
fallback, `OccurrenceId`'s per-*record* disambiguator (built for the
unrelated "same identity, duplicate declaration" case) does not repair
this — it is not a per-overload discriminator. `EntityId` does not invent
a new scheme for this: it carries the existing tiered resolution
`finding_identity.resolve_function_identity`/`SymbolIdentityIndex` and
ADR-048's normalized identity already establish — mangled name first when
one exists (the common case, already globally unique per overload), and
only for the genuinely mangling-free case (a non-`extern "C"` function on
a DWARF-only snapshot) the same normalized-signature fallback tuple that
code already computes. **That fallback tuple includes the callable's own
qualified name, not only its parameter types and CV-qualifiers — a first
draft of this phase omitted the name**, which would have collapsed two
genuinely distinct functions with the same scope and the same parameter
types (`ns::f(int)` and `ns::g(int)`) into one `EntityId`, exactly the
collision class this phase exists to close rather than introduce. The
real primitive, `finding_identity.normalized_signature(qualified_name,
kind, param_types)`, already puts `qualified_name` first in its tuple for
precisely this reason ("two identically-declared overloads never
collide" — but that guarantee only holds because the qualified name is
*in* the tuple); this phase's fallback keeps that shape, it does not
narrow it. `EntityId`'s function variant is therefore `(ScopePath,
"function", leaf_name, extra=mangled_name | (param_types,
cv_qualifiers))` — `leaf_name` per the general shape above, `extra`
carrying exactly the signature discriminator a function additionally
needs — not a bare `(ScopePath, "function")` and not a signature tuple
with the name left out — generalizing the existing tiered primitive into
the one identity every consumer reads, rather than proposing a simpler one
that regresses what the codebase already gets
right.

**There is no new carrier field on `RecordType`/`Function`/any other model
dataclass in this phase, and consumers do not read a stored `EntityId` off
a declaration — both would be the wrong fix, and a first draft of this
phase left the question open rather than answering it, which review
correctly read as "nowhere for the promised identity to live."** The
resolver function (`model.identity.entity_id_for_record(rec)` and its
siblings for enum/typedef/function/variable/constant) derives `ScopePath`
from structural scope data the parsers already track internally during
the AST walk — **not, as an earlier draft of this note claimed, from
`RecordType.qualified_name`/`name` alone.** That claim does not survive
checking the real parser code: `entry.scope` (`dumper_clang.py`'s/
`dumper_castxml.py`'s own internal scope-tracking list, built up while
walking the AST, collapsed into `qualified_name` via `"::".join([*entry.
scope, name])` at the point a declaration is finalized) is a plain
`list[str]` of bare names, with no per-segment kind tag at all — it cannot
distinguish a record nested in a record from the same names nested in a
namespace, or an inline-namespace segment from an ordinary one, because
that distinction was never captured in the first place, not merely
discarded during string-joining. A resolver operating on `qualified_name`
alone is working from a representation that is *structurally* insufficient
for `ScopePath`, not one the resolver fails to parse correctly — no
amount of cleverness in `model/identity.py` recovers information the
parser itself never recorded. **The real fix reaches one layer further
back than the resolver: `entry.scope` itself is widened, in both
`dumper_clang.py` and `dumper_castxml.py`, from `list[str]` to a list of
typed segment records** — each push onto the scope stack (entering a
namespace, a record, an inline namespace, an anonymous scope, a
function-local scope during AST traversal) already has, *at that exact
point*, the one piece of information `qualified_name` alone throws away:
which AST node kind it is actually processing (a clang `NamespaceDecl`
vs. `CXXRecordDecl` vs. an inline-namespace-tagged `NamespaceDecl`; a
castxml `<Namespace>` vs. `<Struct>`/`<Class>` XML element), plus
whatever kind-specific data that node already carries (a record's access
specifier, an inline namespace's version tag). Recording that tag
*when the scope is entered*, rather than trying to reconstruct it later
from the flattened string, is what makes `ScopePath` constructible at
all — `model.identity.entity_id_for_record(rec)` and its siblings take
this typed scope list (not `qualified_name`) as their real input, with
`qualified_name`/`name` kept exactly as they are today for every
consumer that still wants the flat display spelling.

**This still leaves one real question this phase cannot paper over with a
third redesign: `_find_opaque_types`/`type_reachability.py`'s other
consumers run *after* parsing, against an already-built `AbiSnapshot` —
they have no access to the parser's local typed-scope list by the time
they run, only to `RecordType`'s own fields.** "Call the resolver
on demand" only works where the typed scope data the resolver needs is
still in scope, which is true during parsing and false for every
post-parse consumer this phase's own acceptance criteria require
migrating (`diff_filtering.py`'s ambiguity-tracking helpers, explicitly
named for deletion below). Two earlier framings of this section each
answered a different half of the real question and missed the other:
round 15's "no new carrier field, call the resolver on demand" is correct
for *where the computation happens* (parse time, not a cached field) but
silently assumed every consumer could reach that computation, which this
round's finding shows is false for any consumer running after parsing.
Resolving it for real needs one of two shapes, and this plan does not
pick one under continued review pressure a third time: (a) `EntityId`
actually is computed once, at parse time, and carried forward on the
model objects after all — which means Phase 2 does introduce a field
(`RecordType.entity_id`/equivalent per kind), contradicting this
section's earlier "no carrier" framing, with its own schema bump and
round-trip test; or (b) every post-parse consumer this phase lists for
migration is deferred to land *with* Phase 6 instead of before it, since
Phase 6's raw-fact capture is the one place in this plan's own sequencing
that already has the typed scope data (SemanticIR's `CanonicalEntity`
is built from it directly), making Phase 2 define the types and the
algorithm while Phase 6 is where real declarations actually get resolved
identities. This is named explicitly as Phase 2's own open design
question for its implementation PR to resolve, the same way this plan
has already done for the `SourceGraphSummary`-relocation and
`compare()`'s-own-public-surface-parameter questions elsewhere, rather than
asserting a fourth, unverified answer here.

**This choice is not contained to Phase 2 — it determines whether Phase
3, as sequenced below (after Phase 2, before Phase 6), is buildable at
all.** Phase 3's public-surface graph keys its `declaration`/`type` nodes
by `EntityId` (see that phase's own injective-key fix, above), which
needs a real, resolved `EntityId` for every declaration/type node the
graph builder visits. Under option (a), that identity is already sitting
on the model object by the time Phase 3 runs, same as every other field —
no conflict. Under option (b), no post-parse consumer has one yet
(resolution is deferred to Phase 6's `SemanticIR` assembly, which is
exactly why option (b) exists), and Phase 3's graph builder is a post-parse
consumer by construction — it walks an already-built `AbiSnapshot`, the
same position `type_reachability.py`'s other consumers are in per the
finding above. Under option (b), Phase 3 therefore cannot be built as
sequenced: either its identity-dependent parts move to land *with* or
after Phase 6 (the same deferral option (b) already applies to every
other post-parse consumer, generalized to this one), or the Phase 2
implementation PR resolves the open question as option (a) before Phase 3
starts. Not decided here, for the same reason the question itself is
left open above — but the dependency is stated explicitly so Phase 3's
own implementation PR does not discover it mid-flight.

This phase explicitly targets, and closes, the specific collision bugs
AGENTS.md's "Known gaps" records as already-found-and-patched-locally:
opaque-type suppression keyed by bare `RecordType.name`
(`diff_filtering._find_opaque_types`), the `dumper_clang.py` tag-vs-
ordinary-namespace typedef collision, and `type_reachability.py`'s
multi-round namespace-suffix/bare-alias collision history (eleven-plus
numbered findings in that one entry). Each of those local patches is
replaced by one `ScopePath`-based identity computation instead of being
kept as a parallel, narrower fix.

**This is not the first `EntityId`/`OccurrenceId` in the repository, and a
first draft of this phase treated it as one.** ADR-062 Phase 0 already
defined `storage/entity_ids.py`'s own `EntityId` (`kind: EntityKind`,
`qualified_name: str`, `discriminator: str`) and `OccurrenceId`, complete
with a packed `key` property and `to_dict()`/`from_dict()` — inert today
(ADR-062 Phase 1's writer/reader doesn't exist yet, per that ADR's own
status), but a real, already-reviewed module, not a stub. Landing `model/
identity.py`'s `(ScopePath, kind, leaf_name, extra)` shape as a second,
independent type would leave exactly two canonical identities once Phase 8
wires storage's writer/reader — the Governing Invariant's one forbidden
outcome. `EntityKind`/`ObservationKind` (genuinely domain vocabulary, not
a storage wire concern) relocate from `storage/entity_ids.py` into `model/
identity.py` alongside the new primitive, and `model.identity.EntityId`'s
`kind` field is typed as the relocated `EntityKind` enum rather than a bare
string literal, closing that mismatch too.

**Flattening `ScopePath`/`extra` into the existing bare
`qualified_name`/`discriminator` strings is not a lossless bridge, and a
first draft of this phase claimed it was without checking.** `ScopePath`
is a typed tuple of segment *kinds* (`Namespace`, `Record`,
`InlineNamespace`, `Anonymous`, `LocalToFunction`) — rendering it to one
string, the way a display spelling does, discards which kind each segment
was. Two domain `EntityId`s whose `ScopePath`s differ only in segment kind
(a record nested in a record vs. the same names nested in a namespace; an
inline-namespace segment vs. an ordinary one) can render to the identical
`qualified_name` string, so `from_dict()` reconstructing a domain
`EntityId` from that string cannot recover which one it was — a save/load
round trip can silently collapse two distinct domain identities into one,
or change which declarations a reloaded `EntityId` is considered equal to.
A one-way render for *display* purposes is fine; claiming it as the wire
DTO's reversible encoding is not. The actual fix: `storage/entity_ids.py`'s
`EntityId`/`OccurrenceId` DTOs gain a new wire-schema version (D8's "a
migration adapter per DTO version," applied here for the first time) whose
`to_dict()` encodes the `ScopePath` as an explicit list of typed segment
records — `{"kind": "namespace" | "record" | "inline_namespace" |
"anonymous" | "local_to_function", "name": str, ...}`, one entry per
segment, preserving exactly the structure `ScopePath` itself carries —
plus `leaf_name` and `extra` each kept as their own typed fields rather
than folded into one `discriminator` string. This is what makes
`to_dto()`/`from_dto()` an actual round trip rather than a one-way
projection: `from_dto()` reconstructs the identical `ScopePath`/`leaf_name`/
`extra` tuple `to_dto()` started from, with no string to parse back apart.
The old version-1 shape (`kind`/`qualified_name`/`discriminator`) stays
readable — a migration adapter maps a v1 document's bare strings into the
closest v2 domain `EntityId` it can (a single, untyped `Namespace` segment
per `::`-separated component, since v1 never recorded which kind a segment
was) — but a v1-loaded `EntityId` is documented as potentially not equal
to the v2 `EntityId` the same declaration would produce today; this is an
accepted, one-time migration-boundary gap, not a property the wire format
promises going forward. This is the identical domain/DTO split D8 already
establishes for Phase 8's storage writer, applied one phase earlier because
the domain type it wraps already existed before this phase, not invented
by it. `storage/identity.py`'s own re-export of both names is unaffected —
it already imports them from `storage/entity_ids.py`, and continues to.

**Files.** `abicheck/model/identity.py` (new, leaf — no dependency on
`checker_types`/`diff_*`, per ADR-063 D10; also receives the relocated
`EntityKind`/`ObservationKind` enums from `storage/entity_ids.py`, per the
note above). **The direction of reuse with
`finding_identity.resolve_function_identity` matters and a first draft of
this phase had it backwards**: `finding_identity.py` is comparison logic
that itself imports model entities and `checker_types`, so `model/
identity.py` calling *into* it would make the leaf module depend upward
on `compare/`-level code — reversing ADR-061's required `compare -> model`
direction and either failing the architecture gate outright or creating a
cycle the moment comparison code also starts consuming `EntityId`. The
corrected direction: the canonical signature-resolution *algorithm* itself
(mangled-name-primary, the normalized parameter-type/cv-qualifier fallback
tuple, the `extern "C"` exclusion) moves into `model/identity.py` as part
of `EntityId`'s own function-identity constructor, and
`finding_identity.resolve_function_identity` becomes a thin wrapper
delegating to it — `compare -> model` is the allowed edge, so `compare/`
(where `finding_identity.py` lives) depends on the leaf, never the
reverse. This is the same generalization direction every other phase in
this plan already takes (the algorithm moves to the primitive; the
original call site becomes the wrapper), corrected here to actually match
it rather than stating it backwards. `diff_filtering.py`'s
`_find_opaque_types`/`_find_by_value_types`/`_root_type_name` (consume
`EntityId` instead of bare `t.name`); `dumper_clang.py`/
`dumper_castxml.py`'s `parse_types()` (produce `ScopePath`-derived
identity, replacing the ad hoc `"::".join([*entry.scope, name])`);
`type_reachability.py` (its multiple ambiguity-tracking helpers —
`_spelling_index`, `_typedef_spelling_targets`, `_namespace_suffix_
spellings` — collapse into one `ScopePath`-based resolver, deleting the
bespoke string-suffix machinery once the new resolver's test coverage
matches or exceeds the existing eleven-plus regression cases);
`storage/entity_ids.py` (trimmed to the wire-DTO `EntityId`/`OccurrenceId`
pair plus the new `to_dto()`/`from_dto()` bridge to `model.identity.
EntityId`, per the relocation note above — `EntityKind`/`ObservationKind`
move out, the packed-key DTO shape stays).

**Tests.** Every existing regression test named in the "Known gaps"
collision-history entries above is kept (they pin real, previously-found
counterexamples) and re-pointed at the new `EntityId` resolver rather than
deleted — a primitive-level property suite
(`tests/test_entity_identity.py`, per AGENTS.md's "Primitive-level
property tests" convention) states the contract directly: two distinct
declarations in different namespaces never collide regardless of bare-name
overlap; a using-declaration's `EntityId` always resolves to its target's,
never a sibling; namespace-suffix stripping is symmetric and never merges
two records whose full `ScopePath`s differ; **and two distinct overloads
sharing one `ScopePath` (`f(int)` vs. `f(double)`, and separately `void
f()` vs. `void f() const`) always produce distinct `EntityId`s**, pinned
directly against the exact counterexample a reviewer raised for this
design, with an `extern "C"` sibling case confirming the deliberate
opposite rule (a changed parameter list there stays the same identity).
**The carrier-vs-no-carrier tests below are conditional on which option
the open design question (above) actually resolves to — a first draft of
this phase stated both as unconditional requirements, which is
self-contradictory: the no-new-field test forbids option (a) outright,
and the pure-function-of-existing-fields test cannot pass for option (b)
at all, since a post-parse `RecordType`/`Function`'s own flattened fields
are exactly what that open question found insufficient to reconstruct a
typed `ScopePath` from.** Whichever option this phase's implementation PR
selects gets exactly one of these two test shapes, not both: if option
(a) (a real `entity_id`-shaped field, populated at parse time) is chosen,
the test suite asserts that field is populated for every declaration kind
immediately after parsing, round-trips through `serialization.py`
unchanged, and a static check confirms the resolver function itself is
never called on an already-parsed `RecordType`/`Function` outside the
parser (since by that option's own design, the field is read thereafter,
never recomputed). If option (b) (resolution deferred to Phase 6's
raw-fact capture) is chosen, this phase's own test suite is narrower: it
pins the resolver's contract against the *raw facts* Phase 6's normalizer
will eventually supply (not against already-parsed model objects), and
the "calling the resolver twice on separately-constructed field-identical
objects produces equal `EntityId`s" property and the "no new `entity_id`-
shaped field" static check both apply only to *this* narrower scope —
Phase 6's own implementation is where the real, structurally-sufficient
input actually gets threaded through, tested there, not asserted
prematurely here against a model shape this phase already found
insufficient.
A separate test on the relocation covers `storage/entity_ids.py`'s new v2
wire schema: a primitive-level round-trip property test constructing
domain `EntityId`s across every `ScopePath` segment kind (including two
deliberately chosen so their *rendered* `qualified_name` strings would
collide under the old v1 flattening, pinning the exact counterexample this
finding raised) and asserting `from_dto(to_dto(entity)) == entity` — not
merely that some string comes back, but that the reconstructed domain
object is equal to the original, segment kinds included. A second test
covers the v1 migration adapter: every existing v1 fixture document still
loads without error, and the result is documented (in the test, not only
in prose) as a best-effort reconstruction rather than asserted equal to a
fresh v2 encoding of the same logical entity.

**Acceptance criteria.** `diff_filtering.py`/`type_reachability.py`'s
string-based ambiguity-tracking helpers are deleted, not kept alongside
the new resolver — **conditional on option (a) being the one this phase's
open design question resolves to, not unconditional.** A first draft of
this criterion deleted them regardless of which option the implementation
PR picks, which review correctly caught as unsatisfiable under option
(b): that option's own premise (stated above) is that no post-parse
consumer — `diff_filtering.py`/`type_reachability.py` included, named there
by name — has a resolved `EntityId` until Phase 6's `SemanticIR` assembly
runs. Deleting their only working implementation in *this* phase under
option (b) would leave them with neither the old mechanism nor a usable
replacement for the several phases in between — worse than the
double-reporting/collision bugs this phase exists to close. So: under
option (a), this criterion holds exactly as stated, in this phase. Under
option (b), the deletion moves to land together with Phase 6's own
migration of these same consumers (already named as deferred to that
phase above), and this phase's acceptance bar for them is narrowed to "the
new `model.identity` resolver exists and is correct," not "every
consumer has migrated onto it yet." Exactly one `EntityKind`/`ObservationKind` definition
exists in the repository after this phase, in `model/identity.py` —
`storage/entity_ids.py` imports rather than redefines them. FP-rate gate
shows no regression (a net-new suppressed finding from the identity
change is a Phase 2 bug, not acceptable drift).

---

### Phase 3 — public surface as a graph query over one evidence graph (D5)

**Goal.** `compute_public_surface()` answers "is this declaration public"
by traversing one authoritative evidence graph, not by independently
reconstructing include/reference/export relationships from the flat
snapshot a second time.

**Design.** Two things were wrong with this phase's first draft, both
caught by review, and both point to the same corrected design. First,
"is this declaration public" is a **relevance decision** — AGENTS.md's own
task-routing table assigns exactly that class of question ("decide
relevance, suppression, classification, severity, or gating") to
`policy/`, not to `compare/` ("match old/new entities or identify a raw
change"); putting the decision itself in `compare/` would make that
package own policy behavior. Second, and more fundamentally: this
repository **already has** a general-purpose, producer-agnostic node/edge
graph primitive with an evidence-preserving merge —
`buildsource.graph_facts.GraphNode`/`GraphEdge`/`GraphFact`/
`merge_graph_facts` (ADR-031 D2, ADR-046 D1/D2), currently used to build
the optional L5 source/build-evidence graph (`buildsource.source_graph.
SourceGraphSummary`, `NODE_KINDS`/`EDGE_KINDS`). A first draft of this
phase defined a *second*, parallel node/edge dataclass hierarchy in a new
`compare/surface_graph.py` for the public-surface graph — which is exactly
the "one concept, two representations" defect the Governing Invariant
forbids, and it would have left public-surface relevance and L5 impact
analysis (ADR-057's consumer graph, ADR-053's TU→link-unit→DSO
attribution) looking at the same declaration through two graphs that can
still disagree, the opposite of this phase's own stated goal.

The corrected design reuses the existing primitive rather than adding a
sibling:

- **Relocate the generic node/edge/merge primitive** (`GraphNode`,
  `GraphEdge`, `GraphFact`, `FactConflict`, `merge_graph_facts`) from
  `buildsource/graph_facts.py` into `abicheck/model/graph.py`. This is
  exactly what ADR-061's own task-routing table says belongs in `model/`
  ("add an ABI entity/value shared across stages") — the primitive itself
  is already producer-agnostic and was never actually specific to L5
  evidence; only its *vocabulary* (`NODE_KINDS`/`EDGE_KINDS`) and its
  *construction* from source/build evidence are. `buildsource/
  graph_facts.py`/`source_graph.py` import and re-export from the new
  location (mirroring the re-export shim `source_graph.py` already uses
  for its own split-out pieces), so every existing L5 caller is
  unaffected.
- **A third, pre-existing graph-shaped module already answers a
  public-surface question independently, and a first draft of this phase
  missed it entirely — `abicheck/surface_graph.py`'s `SurfaceGraph`/
  `build_surface_graph()`, with real production consumers in `idioms.py`,
  `pattern_verdicts.py`, and `diff_surface_metrics.py` (ADR-025's A1-A4
  surface-intelligence features).** Naming the new module
  `compare/surface_graph.py` below, without addressing this one, would
  leave two same-named-in-spirit graph modules in the codebase — exactly
  the outcome the Governing Invariant forbids. Worse than the name
  collision: `SurfaceGraph.public_roots()` computes "what's public" by
  filtering `Visibility.PUBLIC` directly off the flat snapshot, which is
  *not* the same answer `surface.py`'s reachability closure (and this
  phase's `PublicSurfaceQuery.resolve()`) computes — a declaration tagged
  `Visibility.PUBLIC` but unreachable through real header inclusion, or
  vice versa, is exactly the disagreement this phase exists to close for
  `compute_public_surface()`, and `SurfaceGraph` has had its own,
  independent version of that same risk the whole time.

  **Two things were wrong with the first fix for this, both caught by
  review, and both point at the same corrected shape.** First,
  `SurfaceGraph.public_roots()` calling `PublicSurfaceQuery.resolve()`
  directly would make `surface_graph.py` — a comparison/index-layer
  module (ADR-025's A1-A4 surface-intelligence substrate, the same role
  `idioms.py`/`pattern_verdicts.py`/`diff_surface_metrics.py` already play)
  — import `policy/public_surface.py`, reversing ADR-061's required
  `policy -> compare` direction (`policy/` is allowed to depend on
  `compare/`, never the reverse). Second, `PublicSurfaceQuery.resolve()`
  returns `frozenset[EntityId]` (per this phase's own primitive below),
  while `SurfaceGraph.public_roots()` is documented and consumed today as
  a `frozenset[str]` of symbol/mangled names — `pattern_verdicts.py`'s
  `_recognise_create_destroy()` passes each root straight into
  `re.Pattern[str].match()`, which a bare delegation would hand an
  `EntityId` object instead of a string and fail outright, not just
  disagree in content.

  The corrected shape fixes both at once by moving the decision, not the
  call: the workflow/compare orchestration code that already calls
  `PublicSurfaceQuery.resolve()` for `compute_public_surface()` (the same
  assembly step this phase's earlier bullets already describe) resolves
  the public `EntityId` set *once* and threads it down to
  `build_surface_graph`/`compute_surface_metrics` — `surface_graph.py`
  itself never imports `policy/public_surface.py`, it only receives an
  already-resolved answer, same direction as every other `policy ->
  compare` edge in this plan.

  **Threading it down means widening real call chains, not declaring the
  three consumers unaffected — a first draft of this phase claimed the
  latter and a reviewer checked the actual call sites and found it false.**
  `pattern_verdicts.py:196-197` calls `build_surface_graph(old)`/
  `build_surface_graph(new)` directly inside `apply_pattern_verdicts()`,
  and `surface_graph.py:371`'s own `compute_surface_metrics()` does the
  same — neither receives anything from a policy-layer caller today, and
  both are themselves reached from `checker.py`'s own explicit call sites
  (`_apply_pattern_verdicts_step`/`_apply_surface_metrics`, not the generic
  detector registry, so widening their signatures doesn't touch dispatch
  machinery).

  **A single *call's worth* of `public_entity_ids` is still the wrong
  shape for the two-snapshot *callers*, and a first draft of this fix
  threaded exactly one shared set to both — a review round correctly
  traced the real call sites and found `apply_pattern_verdicts()`/
  `compute_surface_metrics()` each build *two*
  separate graphs, one per side (`build_surface_graph(old)` and
  `build_surface_graph(new)`), not one.** Old and new can genuinely have
  different public reachability — a declaration added to, or removed from,
  the public-header set between versions is exactly the kind of change
  `compare()` exists to detect — so resolving one shared id set and handing
  it to both sides' graph builds classifies one side using the *other*
  side's surface, corrupting pattern modulation and surface-metric findings
  for precisely the changes that matter most (a declaration crossing the
  public/private line). **The old/new pair belongs on the two-snapshot
  callers, not on `build_surface_graph`/`compute_surface_metrics`
  themselves — a further review round correctly found this paragraph's
  own fix put the pair on the wrong functions: each of those two helpers
  operates on exactly one snapshot per call, so giving either of them
  *both* `old_public_entity_ids`/`new_public_entity_ids` leaves no
  unambiguous way to route the pair for a single-snapshot invocation.**
  `build_surface_graph`/`compute_surface_metrics` each gain exactly
  **one** optional parameter instead — `public_entity_ids:
  frozenset[EntityId] | None = None` — and the old/new pair lives only on
  their two-snapshot callers, `apply_pattern_verdicts()`/
  `compute_surface_metrics()`'s own caller, each passing its own side's set
  to its own matching call (`build_surface_graph(old, public_entity_ids=
  old_ids)`/`build_surface_graph(new, public_entity_ids=new_ids)`), never
  the same set to both. `checker.py`'s
  `_apply_pattern_verdicts_step`/`_apply_surface_metrics` both gain the
  two-snapshot pair — **received as an already-resolved `compare()`
  parameter from its caller, not computed by `compare()` itself; see the
  correction a few paragraphs down for why `checker.compare()` may not
  call `PublicSurfaceQuery.resolve()` directly (the same `compare ->
  policy` direction violation `surface_graph.py`'s own fix above already
  closed once, reappearing here at a second call site if `compare()`
  resolved this itself)** — and passed straight through to
  `apply_pattern_verdicts()`/
  `diff_surface_metrics()`, which pass the matching half of the pair
  through as the single `public_entity_ids` argument to `build_surface_
  graph()`/`compute_surface_metrics()`
  in turn. When both are `None` (the only case possible outside
  `compare()`'s own pipeline), `SurfaceGraph.public_roots()` falls
  back to its pre-existing `Visibility.PUBLIC` filter — an explicit,
  narrow, named residual for a caller this phase cannot reach, not a
  second silent implementation competing with the real one.

  **That residual turned out to be reachable from production after all —
  a review round found a second, documented entry point that calls
  `checker.compare()` directly and is never resolved, the exact gap the
  paragraph above claimed didn't exist.** `service.compare_snapshots()`
  is a real, documented Tier-2 production verb
  (its own docstring: "Thin wrapper over the Tier-1 core... so that
  *front-ends never call the core directly*") that forwards
  `pattern_verdicts`/`surface_metrics` straight into `compare()` with no
  resolved-ids parameter at all — confirmed by reading `service.py`'s
  `compare_snapshots()` directly, not assumed. Any caller reaching
  `compare()` through this path (not only `service_compare_pipeline.
  classify_compare_pair`'s typed-pipeline path) with `pattern_verdicts=
  True`/`surface_metrics=True` hits the `Visibility.PUBLIC` fallback named
  above, producing findings that can genuinely differ from the equivalent
  CLI/typed-pipeline comparison of the same two snapshots — not a
  theoretical caller this phase cannot reach, but the second of exactly
  two production routes into `compare()`. Fixed by giving
  `compare_snapshots()` the identical per-side resolution
  `classify_compare_pair` performs — calling the same `resolve_public_
  surface()` wrapper for `old`/`new` independently before invoking
  `compare()`, and passing the two resulting `frozenset[EntityId] | None`
  through as the same two parameters `compare()` itself gains — rather
  than inventing a second resolution path. `service.py` is not itself
  gated by the ADR-061 `compare -> policy` direction restriction (it is a
  flat, unmigrated module today, same as the other residuals this plan
  already tracks under its architecture-boundary notes), so this call is
  not a new violation; it is a second call site doing exactly what the
  workflow layer's own resolution call already does. Every documented
  production route into `compare()` — the typed pipeline and this direct
  Tier-2 verb — now resolves and passes real, side-specific ids; the
  `Visibility.PUBLIC` fallback is reachable only from a caller that
  imports `checker.compare()` directly, bypassing both documented
  entry points, which is exactly the ADR-037 D1/D10.1 violation the CLI-
  contract gate already exists to catch.

  **`PublicSurfaceQuery.
  resolve()`'s result is not already a function/variable-only set, and a
  first draft of this phase's mapping step assumed it was — `resolve()`
  traverses `declares` and type-reference edges, so it genuinely returns
  record/enum/typedef `EntityId`s too (a public function's return type, a
  public struct reachable from a public signature), which is correct for
  `compute_public_surface()`'s own purpose (deciding what's public at all,
  types included) but has no symbol/mangled-name spelling to map to for
  `SurfaceGraph.public_roots()`'s specific contract — that function's own
  docstring already states its root set is "`Visibility.PUBLIC` functions
  and variables," never types.** `SurfaceGraph.
  public_roots()` therefore filters the received `EntityId` set to
  `kind in (FUNCTION, VARIABLE)` *before* mapping — a type-kind id in the
  resolved set is simply not part of this particular root set and is
  dropped, not mapped-and-failed.

  **Mapping the resolved id back to its *mangled* spelling is the wrong
  target, and a review round correctly traced why: `public_roots()`'s
  existing contract is keyed on the plain declaration name, not the
  mangled one, and every one of its own consumers already depends on
  that.** Reading `surface_graph.py` directly: `public_roots()` is
  `frozenset(self._root_seed_types)`, and `_root_seed_types` is built by
  `_build_root_seed_types()` keyed on `fn.name`/`var.name` — the plain
  `Function`/`Variable` display name, deliberately (its own docstring:
  "C++ overloads share a demangled name... their seed sets are unioned"),
  never the mangled spelling. `reachable_types(root)` looks `root` up in
  that same dict directly. `idioms.py`'s `_recognise_create_destroy()`
  applies human-readable `create_*`/`destroy_*` regexes against these same
  keys, which only make sense against a plain name — a mangled Itanium
  symbol never matches those patterns at all. Returning `_Z...`-mangled
  spellings from the resolved-id mapping would therefore either silently
  disconnect every returned root from its own seed types (a `root` string
  `reachable_types`/`_root_seed_types` has never heard of) or suppress
  create/destroy pattern recognition outright — changing pattern-verdict
  and surface-metric findings, not merely an internal representation
  detail. Fixed by mapping each remaining `EntityId` back to the existing
  `Function.name`/`Variable.name` spelling instead — the exact key
  `_root_seed_types`/`reachable_types`/`idioms.py`'s regexes already use
  today, unchanged by this phase — not the mangled name; `EntityId`'s
  function variant carrying the mangled name in `extra` remains useful
  for identity/matching purposes elsewhere in this plan, just not as
  `public_roots()`'s own return value. This preserves its existing
  `frozenset[str]` return type
  and its existing consumers' string-based contract exactly. `SurfaceGraph`
  itself
  is not deleted or folded into `model/graph.py` — it answers a genuinely
  different question from the evidence graph (a snapshot-local
  declaration-reference index for surface-intelligence metrics, not a
  multi-evidence-source relevance graph), and conflating the two into one
  module would be the opposite error: forcing a real distinction into one
  representation. What must be one representation is *what counts as
  public*, not the index structure built for a different purpose on top
  of it, and not the direction that decision travels in.
- **Build the public-surface graph as instances of that same primitive**,
  not a new dataclass hierarchy — `abicheck/compare/surface_graph.py`
  (new) registers its own node/edge *kind vocabulary* (`header`,
  `translation_unit`, `declaration`, `type`, `symbol`, `target`; edge
  kinds `includes`, `declares`, `references`, `instantiates`, `exports`,
  `owned_by_target` — several of which already have an L5 analogue worth
  reusing directly rather than renaming for its own sake: `declares`
  *is* `SOURCE_DECLARES`, `exports` *is* `BINARY_EXPORTS_SYMBOL`,
  `owned_by_target` *is* `TARGET_HAS_SOURCE`/`TARGET_HAS_PUBLIC_HEADER`),
  built from facts the core L0-L2 extraction layer already produces (the
  header origin/scoping data `dumper_scoping.py` reads, the export-table
  data `export_surface.py` already computes for `contract=exports`, the
  declaration/reference data `type_reachability.py`/`surface.py` each
  independently reconstruct today) — available unconditionally, unlike
  `source_graph.py`'s graph, which only exists when L3-L5 evidence was
  collected. **Only `declaration`/`type` nodes are keyed by the `EntityId`
  Phase 2 established — not every node kind.** A first draft of this
  phase said "nodes are keyed by `EntityId`" without qualification, which
  overclaims: Phase 2's `EntityId` is specifically an ABI-declaration
  identity (record/enum/typedef/function/variable/constant); `header`,
  `translation_unit`, `symbol`, and `target` nodes are not ABI
  declarations and have no natural `EntityId` form — `GraphNode.id` in
  the real, existing `buildsource/graph_facts.py`/`source_graph.py` is
  already a plain string with its own per-kind URI scheme for exactly
  these non-declaration kinds (`header://`, `source://`, `target://`,
  `symbol://`, ...), which this phase reuses unchanged rather than
  replacing. **A `declaration`/`type` node's id must be an injective
  encoding of its `EntityId`, not the lossy flattened `qualified_name`
  string — a first draft of this phase used the flattened string
  directly, which is exactly the collision this plan's own Phase 2
  section warns against two sections above** ("Two domain `EntityId`s
  whose `ScopePath`s differ only in segment kind... can render to the
  identical `qualified_name` string"): `SourceGraphSummary.add_node()`
  merges any two registrations sharing one `id`, so a record nested in a
  record and the same names nested in a namespace would coalesce into one
  graph node, mixing their `GraphFact`s and corrupting public-surface
  reachability for both. **This key must be built from `model.identity`'s
  own *identity-only* encoding, not `storage/entity_ids.py`'s `to_dto()` —
  a first draft of this phase pointed the graph key at the same `{"kind":
  ..., "name": ..., ...}` segment records the storage DTO encodes, and
  review correctly caught that those two encodings answer different
  questions and must not share one function.** `to_dto()` is deliberately
  a *lossless, full-structure* round trip — it preserves `Record.access`
  as real payload, because storage wants to recover everything a
  `ScopePath` carries, access included. But `Record.__eq__`/`__hash__`
  (this phase's own ScopePath-identity section, above) deliberately
  *excludes* `access` from identity — two `EntityId`s differing only in a
  member's access level are the same identity by design, so that a real
  access-level change reads as "this declaration changed," not "removed,
  then added." A graph key built from the full-structure DTO encoding
  would give those two equal `EntityId`s two *different* node ids, which
  re-introduces this section's own target bug from the opposite direction:
  instead of two different scopes colliding into one node, one same scope
  would now silently split into two. The fix is a second, narrower
  function, `model.identity.canonical_key(entity_id) -> str`, built only
  from the fields each segment's own `__eq__`/`__hash__` already uses (so
  it is injective *on identity*, never on full structure) — used by both
  `model/graph.py`'s `GraphNode.id` for a `declaration`/`type` node and any
  other consumer that needs a collision-free, equality-consistent key
  (`kind`/`leaf_name`/`extra` plus each segment's identity-only fields),
  while `storage/entity_ids.py`'s `to_dto()` stays the separate,
  intentionally fuller encoding for its own persistence purpose —
  `GraphNode`'s own pre-existing `label: str` field (already documented as
  "human-readable name/path") is where the flattened, lossy `qualified_name`
  spelling belongs instead, exactly the role that field already plays for
  the existing URI-scheme node kinds below. Every other kind keeps the
  existing URI-scheme id (`header://`, `source://`, ...), which was never
  the display spelling and was never at risk of this collision.
  **`canonical_key(EntityId)` alone still collides for a real, ordinary
  case `EntityId`'s own precision was never built to resolve, and a
  reviewer correctly traced why: two internal-linkage (`static`)
  functions in different translation units sharing the same scope, leaf
  name, and signature** — e.g. two files each defining a file-local
  `static void helper()` — mangle to the *identical* Itanium symbol (a
  mangled name carries no file/TU component), so `EntityId`'s own
  function-kind `extra` (mangled name, or the normalized-signature
  fallback) cannot tell them apart either; both collapse to one
  `canonical_key`, and `SourceGraphSummary.add_node()` merges their facts
  and edges into a single node. This is not a new ambiguity this phase
  introduces — it is the identical one ADR-046/048's existing L5 source-
  graph identity (`buildsource/entity_identity.py`) was already built to
  resolve, by preferring a compiler-provided USR (which *does* encode
  enough context to disambiguate two same-named internal-linkage
  declarations) over a bare mangled name. Losing that resolution the
  moment declaration/type nodes key on `canonical_key(EntityId)` directly
  would be a real regression for exactly the nodes the L5 builder
  populates, not merely an unlikely edge case. **The fix reuses a
  mechanism this plan already designed for the adjacent "same identity,
  genuinely different declaration" shape, rather than inventing a fourth
  one**: `model/graph.py`'s `GraphNode.id` for a `declaration`/`type` node
  is `canonical_key(occurrence_id)` — `OccurrenceId`, not bare `EntityId`
  — where the disambiguator Phase 2 already defined for the ODR-duplicate/
  incomplete-declaration case is populated, for this case, from the same
  USR/TU-context signal `entity_identity.py` already prefers when the
  underlying evidence carries one (L5 source evidence, which is exactly
  when two internal-linkage declarations can coexist as distinct graph
  nodes in the first place — a pure L0-L2 binary/header-only snapshot has
  no TU-level view to distinguish them from either, the identical
  structural limit the flat `EntityId` layer already accepts). A
  declaration with a globally-unique identity at the `EntityId` level
  (the overwhelming common case — anything with external linkage, or an
  internal-linkage entity that merely happens not to collide) gets an
  empty disambiguator, so `canonical_key(occurrence_id)` reduces to
  exactly `canonical_key(entity_id)` for every node this finding doesn't
  apply to, with no behavior change for them.
  This phase is ordered after Phase 2 because the declaration/type half
  needs it — the same dependency Phase 6 (`SemanticIR`) has on Phase 2 —
  not because every node kind does.

  **Defining `canonical_key(occurrence_id)` does not, by itself, make the
  new public-surface builder's node ids agree with the existing L5
  builder's — and a review round correctly found this phase's text never
  actually closes that gap.** `canonical_key()` is specified above as
  `compare/surface_graph.py`'s own new encoding; meanwhile
  `buildsource/source_graph.py:1498,1533` and every sibling L5 module
  (`header_graph.py`, `call_graph.py`, `type_graph.py`,
  `override_graph.py`, `macro_graph.py`, `template_graph.py`,
  `callback_graph.py`, `graph_backends.py` — twelve call sites across
  eight files, by grep) still construct declaration/type node ids via
  `graph_facts._decl_node_id(identity)`/`_type_node_id(identity)`, which
  predate this phase and have their own independent `f"decl://
  {_normalize_graph_identity(identity)}"`/`f"type://{...}"` format. Two
  independently-written formats cannot be relied on to agree string-for-
  string for the same declaration merely because both are "collision-
  free" in isolation, and nothing in this phase's text migrates those
  twelve call sites — so handing both builders one shared
  `SourceGraphSummary` instance (the fix two bullets below) reconciles
  nothing: the public-surface builder's node for a given declaration and
  the L5 builder's node for the identical declaration land under two
  different ids, `add_node()`'s id-collision merge never triggers, and the
  two representations sit side by side in one container without ever
  reconciling.

  **A "move the function, have `canonical_key` delegate to it" fix was
  tried here and is itself wrong, for a reason a further review round
  caught precisely: relocating `_decl_node_id`/`_type_node_id` does not
  make their *inputs* equal, and the inputs are where the real
  incompatibility lives.** `canonical_key(entity_id)` exists specifically
  because a flattened qualified-name string is *not* injective on
  `EntityId` identity — two domain `EntityId`s whose `ScopePath`s differ
  only in segment *kind* (a record nested in a record vs. the same names
  nested in a namespace) can render to the identical flattened string,
  which is exactly the collision this phase's own `surface_graph.py` fix
  (two sections up) was built to avoid by keying on the segments'
  `__eq__`/`__hash__` fields directly, not on a flattened rendering.
  `_decl_node_id`/`_type_node_id`'s own normalization
  (`_normalize_graph_identity`) only ever strips a checkout-dependent
  absolute path out of an anonymous/lambda marker — confirmed by reading
  it — it carries no segment-kind information at all, because its input,
  `ent.identity()`, never had any: every one of the twelve L5 call sites
  computes a bare, already-flattened string with no `ScopePath`/kind
  breakdown behind it. So relocating the two functions and defining
  `canonical_key()` to call them does not produce one shared, injective
  encoding — it produces exactly one of two bad outcomes: either
  `canonical_key()`'s rendering stays flattened (matching the L5
  callers' ids, but reopening the segment-kind collision this same phase
  already closed for the public-surface builder's own nodes), or it stays
  segment-kind-aware (closing that collision, but then no longer matching
  what the unchanged L5 callers compute, so `add_node()`'s id-collision
  merge never triggers and nothing reconciles — the original finding's
  own failure mode, unsolved by the relocation).

  **Left as an explicit, scoped-out residual rather than attempted a
  third time under review pressure, matching this plan's own established
  discipline for a gap of this shape (the dump/scan typed-API convergence
  in AGENTS.md's "PR C" note is the same class of problem: real,
  cross-cutting, not a same-phase afterthought).** A correct fix needs
  one of: (a) migrating the twelve L5 call sites to construct a real
  `EntityId`/`OccurrenceId` from whatever scope/kind information their own
  producers (`SourceEntity`, clang AST nodes, USR strings) actually carry
  before flattening it away — a genuine, separate data-flow change to
  eight already-complex modules, not a drive-by edit; or (b) a lossless
  mapping recovering the lost segment-kind information from each
  producer's own provenance, which would need its own audit of what each
  of the twelve call sites' inputs actually preserve today. Until one of
  those lands, the two builders sharing one `SourceGraphSummary` instance
  (the assembly-step fix below) reconciles nodes only where the two
  encodings happen to coincide — unparameterized, unambiguous declarations
  with no real segment-kind collision, the common case — and a
  declaration that does hit the segment-kind collision keeps two separate
  nodes across the two builders, an accepted limitation for this phase
  rather than a silently-assumed-closed gap.
- **Sharing node ids alone does not merge two graphs — this phase adds the
  actual assembly step, not only a shared identity.** `merge_graph_facts`
  only folds the `GraphFact` list already attached to *one* node; it is
  not itself what combines two independently-built graph objects, and an
  earlier draft of this phase described the disagreement as closed on the
  strength of shared node ids alone, which a reviewer correctly rejected —
  two builders each producing their own, separate graph *object* can share
  every node id and still never actually merge, because nothing calls the
  merge. What actually merges two registrations today is
  `SourceGraphSummary.add_node()`/`add_edge()`: *within one
  `SourceGraphSummary` instance*, registering a second `GraphNode` under
  an id already present calls `merge_entity_facts`, which is what invokes
  `merge_graph_facts` underneath. So the real fix is an assembly step, not
  an identity claim: **both builders write into the same
  `SourceGraphSummary` instance for a given snapshot side.**
  `compare/surface_graph.py`'s public-surface builder and `source_graph.py`'s
  L5 builder (when L3-L5 evidence is present) are both given the *same*
  `SourceGraphSummary` object and both call its real `add_node`/`add_edge`
  — exactly the pattern `buildsource/header_graph.py`'s existing
  `build_header_only_graph()` already uses internally (`graph =
  SourceGraphSummary(); ...; graph.add_node(...)`), generalized here to
  two independent builders sharing one instance instead of one builder
  filling it alone. **Who constructs and threads that one instance matters
  for the same import-direction reason D5 already corrected once in this
  phase**: `compare/surface_graph.py` may not import `SourceGraphSummary`
  from `buildsource/` directly (`compare -> model` is the allowed edge;
  `buildsource/` is `extract`-layer, and `compare -> extract` is not), so
  the instance is constructed and handed to *both* builders by the
  orchestrating workflow code (`workflows/`, which is allowed to import
  `model`, `extract`, and `compare` alike) — each builder function receives
  the shared `SourceGraphSummary` as a parameter and only ever calls
  `.add_node`/`.add_edge` on it, never constructs or imports it itself.
  **`AbiSnapshot.build_source.source_graph` cannot be where this
  unconditional graph lives** — `build_source: BuildSourcePack | None` is
  itself `None` for an ordinary L0-L2 snapshot with no `--sources`/
  `--build-info`, which is the common case this phase's "available
  unconditionally" claim is specifically about; attaching the graph only
  under an optional evidence pack would mean fabricating a pack just to
  hold it, silently widening what `build_source is not None` means
  elsewhere in the codebase (it currently *means* "build/source evidence
  was collected," which several existing checks rely on). Fixed by a new,
  always-present field directly on `AbiSnapshot` — `surface_graph:
  SurfaceGraphLike | None = field(default=None, kw_only=True)` (`None`
  only for a snapshot this phase hasn't touched yet, e.g. an old loaded
  snapshot predating this field; always populated for a freshly-extracted
  one, regardless of whether `build_source` is set). This is the one
  shared instance both builders write into — when `build_source` evidence
  also exists, the L5 builder writes into the *same* `AbiSnapshot.
  surface_graph` instance rather than a separate graph attached under
  `build_source`, so there is exactly one graph-shaped field per snapshot
  after this phase, not a conditional one nested under an unrelated
  optional pack.

  A snapshot persisted before this field existed has `surface_graph is
  None`, and a query over the public surface must not treat that the same
  as "nothing is public" -- a first draft of this phase left the backfill
  unaddressed, which would have broken (or silently emptied) every existing
  baseline's public/export-surface queries the moment `compute_public_surface`
  stopped falling back to its own flat-snapshot traversal.

  **The fix is a lazy backfill, but it cannot live *inside*
  `PublicSurfaceQuery.resolve()` itself — a first draft of this paragraph
  placed it there without checking the resolver's own declared signature,
  and review correctly caught the contradiction: `resolve(graph,
  explicit_roots)` takes a pre-built `graph`, never the `AbiSnapshot` the
  backfill would need to build one from when that graph is `None`. A
  resolver that only ever receives `graph=None` for an old snapshot has
  nothing to backfill from — there is no snapshot reference in scope to
  read header origin/declaration/export-table data out of.** The backfill
  therefore runs one layer up, in a single shared helper every caller of
  `PublicSurfaceQuery.resolve()` routes through rather than each
  reimplementing its own `None` check — `policy.public_surface.
  resolve_public_surface(snapshot, explicit_roots)` (a thin wrapper, not a
  second query implementation): it reads `snapshot.surface_graph`,
  lazily builds one on the fly, in memory, using the flat `AbiSnapshot`
  fields that are actually available on an old snapshot (header origin,
  declarations, export-table data) when that field is `None` — and then
  calls `PublicSurfaceQuery.resolve()` with that graph, which stays exactly
  the graph-only traversal its signature already states.

  **That graph is a lossy approximation, not the real thing — a first
  draft of this paragraph claimed the backfill reuses "the exact same
  `compare/surface_graph.py` builder a fresh extraction already uses," and
  that claim contradicts Phase 2's own finding directly above.** Phase 2
  establishes that `EntityId`/`ScopePath` construction needs the *typed*
  scope-segment list the parsers track internally during the AST walk —
  which node kind each scope-stack entry actually is (namespace vs. record
  vs. inline namespace vs. anonymous scope), plus kind-specific data like a
  record's access specifier — and that `qualified_name`'s flattened
  `"::".join(...)` string is **structurally**, not merely
  implementation-incompletely, insufficient to reconstruct that list: the
  segment-kind tag was never captured in the string in the first place, so
  no amount of re-parsing `qualified_name` recovers it. An old snapshot
  predating this phase was written by a parser that only ever produced the
  flattened string — it has no typed scope list anywhere to read, on disk
  or in memory, because Phase 2's widening of `entry.scope` from
  `list[str]` to typed segment records is exactly the parser-side change
  that old snapshot's own extraction run never had. The "fresh extraction"
  builder this sentence originally pointed to is building `EntityId`-keyed
  nodes from that typed list **during parsing**; the backfill has no
  parsing step to draw it from, only the flat fields the old snapshot
  actually persisted. So the backfill cannot build a true `EntityId`-keyed
  graph for an old snapshot, full stop — it is not a gap in this
  wrapper's implementation to close later, it is the direct consequence of
  the fact the backfill's only inputs are exactly what Phase 2 already
  proved is insufficient.

  The honest fix is to build an **approximate** graph instead, keyed on the
  qualified-name string itself (optionally paired with `kind` to at least
  separate a record from a function sharing one bare name) rather than on
  a real `EntityId`, and to carry that distinction in the type system
  rather than leave it implicit: the backfill returns a graph over
  `EntityId`-shaped keys synthesized with an empty/best-effort `ScopePath`
  (every segment collapsed to a single untyped `Namespace`-kind entry, the
  closest-fitting existing segment type, rather than inventing a sixth
  segment kind solely to mean "unknown") — which is **exactly the same
  collision class `compute_public_surface()`'s/`export_surface.py`'s own
  pre-migration string-keyed traversal already has today** (two
  same-named declarations in different namespaces, or a record and a
  function sharing a bare name, collapsing onto one key) — so this backfill
  is a lateral move to the new query shape with the same known, already-
  accepted fidelity loss, not a regression and not a new capability. A
  fresh extraction under this phase never takes this path at all (its
  `surface_graph` is never `None`), so the approximation is reached only
  for a snapshot already using today's qualified-name-string semantics —
  it degrades to what that snapshot already had, nothing worse.

  **The approximation's effect on `resolve_public_domain()`'s own
  structured result — `resolvable`/`ambiguous_type_names`/
  `exact_type_identities` — is not automatic, and needs stating
  explicitly rather than left to be inferred from "it's the same
  collision class."** The backfilled graph's collapsed `ScopePath`
  segments mean two genuinely distinct declarations (same leaf name,
  different enclosing scope *kind* — a record nested in a record vs. the
  same names nested in a namespace, the exact distinction Phase 2's own
  widened `entry.scope` exists to preserve and this backfill has no way
  to recover) can merge onto one synthesized `EntityId` that a fresh
  extraction would have kept separate.

  **The first version of this rule could not actually detect the
  collision it names, and a review round caught why: "two or more
  distinct qualified-name+kind pairs onto one synthesized key" is not a
  condition that can ever hold — the synthesized key *is* a function of
  the qualified-name+kind pair, so two genuinely distinct pairs can never
  map onto the same key in the first place; by the time the backfill
  runs, a real collision (different original `ScopePath`, identical
  flattened spelling) has already reduced to one, not two, observable
  pairs.** The corrected, observable signal is different: not "distinct
  pairs merged," but "the same pair was produced by more than one
  separate flat declaration" — i.e. two or more entries in
  `snapshot.types`/`snapshot.functions`/etc. that already share an
  identical qualified-name+kind spelling before the backfill ever
  touches them, the same producer-side namespace-dropping collision
  class AGENTS.md's own `type_reachability.py`/opaque-type entries
  already document for this codebase's bare/partially-qualified-name
  matching. Any such duplicate lands the shared key in
  `ambiguous_type_names`. **But an *unduplicated* key is not, on that
  basis alone, promoted to `exact_type_identities` either — a second
  correction past the first fix's remaining gap.** A single observed flat
  entry for a given spelling is not proof that no collision occurred:
  this codebase's own upstream producers already first-wins-dedup by
  identity in several places (`model.py`'s `function_map`/`variable_map`/
  `type_by_name`), so two genuinely distinct declarations sharing a
  flattened spelling could already have been silently reduced to one
  surviving flat entry *before* this backfill ever sees the snapshot —
  leaving no duplicate for it to observe. The backfill therefore never
  promotes any of its own synthesized keys to `exact_type_identities` at
  all, duplicate-observed or not; a key is in `ambiguous_type_names` when
  a collision is actually observed, and in neither set otherwise (simply
  absent from the anti-hiding mechanism, not asserted safe) — strictly
  more conservative than the first draft's rule, and the only rule this
  backfill's own inputs can actually support. `resolvable` itself is
  unaffected by the approximation: it answers "does this snapshot have
  header-derived visibility at all" (a question the flat fields already
  answer on their own, independent of `EntityId` fidelity), not "is this
  graph's identity resolution trustworthy" — conflating the two would
  incorrectly downgrade a genuinely resolvable old snapshot's surface to
  the unscoped-everything fallback merely because it predates typed
  `ScopePath` data, which is a strictly worse outcome than the accepted
  ambiguity-tracking loss this paragraph already owns.

  **The regression test this fix needs is also corrected: a first draft
  paired a record and a function sharing one spelling, which `EntityId`'s
  own kind discriminator already keeps apart (they could never
  synthesize onto the same key to begin with), so that fixture exercised
  no real collision at all.** The actual regression test and the
  parity-test requirement below (Phase 3's own) instead fixture two
  *same-kind* declarations sharing one flattened spelling: two separate
  `RecordType` entries in `snapshot.types`, both with qualified name
  `Outer::Foo` (the realistic trigger being the producer-side
  namespace-dropping collision this codebase already documents
  elsewhere, not a hand-contrived input) — resolving through the
  backfill with the shared key landing in `ambiguous_type_names` and
  absent from `exact_type_identities`, confirmed to fail against a
  version of the backfill that either treats the collapsed key as
  unambiguous or promotes an unduplicated key to `exact_type_identities`
  on the strength of its single observed occurrence alone.
  The backfilled
  graph is not written back onto the loaded `snapshot` object (no silent,
  surprising mutation of a caller's loaded snapshot) -- a query against
  the same old snapshot pays the build cost each time, which is the
  correct tradeoff for what should be a rare path once fresh snapshots
  carry the field. `compute_public_surface(snapshot)` and any other direct
  caller call `resolve_public_surface(snapshot, ...)`, never
  `PublicSurfaceQuery.resolve()` directly, so the `None`-backfill and the
  graph-only resolver stay two separably-testable pieces rather than one
  function quietly doing both — and a workflow-layer caller that already
  holds a resolved, non-`None` graph (the common, fresh-extraction case)
  pays no extra indirection beyond the one wrapper call, with no fabricated
  pack to thread through either way.
  ADR-057/053's consumers still
  read the L3-L5-gated graph only when it exists, and migrating them onto
  querying through `PublicSurfaceQuery`'s shared instance directly is
  still explicitly **not** part of this phase (each stays its own later,
  separately-justified phase, per this plan's "don't attempt a change with
  no real caller" discipline) — but what changes this time is structural,
  not aspirational: there is one graph object per snapshot side after this
  phase, not two that merely happen to agree on node spelling.

  **The first of two items this relocation owes a real design is now
  resolved, not deferred a fourth time — a review round correctly found
  that `AbiSnapshot.surface_graph`'s own declared type above is not
  actionable while this stayed open, which is a different problem than
  "this would benefit from being decided eventually."** `SourceGraphSummary`
  itself — the container class with `add_node`/`add_edge`/
  `resolve_entities`, as opposed to the `GraphNode`/`GraphEdge` primitives
  Phase 3's own `model/graph.py` relocation already covers — still lives in
  `buildsource/source_graph.py` today, and does **not** relocate alongside
  them: its own imports (`buildsource.build_evidence.BuildEvidence`,
  `buildsource.entity_resolver.EntityResolver`) are genuine L3-L5
  build/source-evidence types, not model-layer primitives, so moving the
  whole class to `model/` would drag those two modules (and whatever they
  themselves depend on) into `model/` too — the same kind of inversion the
  `GraphNode`/`GraphEdge` relocation was careful to avoid by checking its
  own dependency closure first, just failed here by not checking
  `SourceGraphSummary`'s. The resolution is the protocol option this
  paragraph named but didn't choose: `model/graph.py` gains a narrow,
  structural `typing.Protocol` (e.g. `SurfaceGraphLike`) — **covering both
  the write side *and* the read side, not only `add_node`/`add_edge` as a
  first draft of this fix had it.** That first draft checked only what
  the Design section's *builders* call on the shared instance, and missed
  the one caller who actually needs to *read* the graph back:
  `PublicSurfaceQuery.resolve()`/`resolve_public_domain()` must traverse
  whatever `AbiSnapshot.surface_graph` already holds — closing reachability
  through `includes`/`declares`/`references`/`instantiates` edges is this
  phase's whole Goal — and a protocol exposing only two write-only methods
  gives that traversal nothing to read, forcing exactly the
  `buildsource.SourceGraphSummary`-importing cast this protocol exists to
  avoid. `SurfaceGraphLike` therefore also declares the two plain,
  already-existing attributes a traversal actually needs —
  `nodes: Sequence[GraphNode]`/`edges: Sequence[GraphEdge]` (`Sequence`,
  not `list`, since the protocol only ever needs read access, and widening
  to a broader container type is exactly what a `Protocol` is for) — plus
  `has_node(self, node_id: str) -> bool`, `SourceGraphSummary`'s own
  existing O(1) membership check a naive `node in self.nodes` linear scan
  would otherwise have to reimplement. All three already exist on
  `SourceGraphSummary` exactly as declared, so this widening needs no
  change to that class, only to the protocol's own declared surface.
  `AbiSnapshot.surface_graph: SurfaceGraphLike | None` in
  `model/snapshot.py` needs no import from `buildsource` at all —
  `SourceGraphSummary` already structurally satisfies the protocol (Python
  `Protocol`s check structurally, not by inheritance, so the existing class
  needs no base-class change either) — and every caller that actually needs
  `resolve_entities`/other `SourceGraphSummary`-specific methods narrows
  back from the protocol to the concrete type at its own call site.

  **That narrowing is an ordinary `isinstance(graph, SourceGraphSummary)`
  check against the concrete class, and a first draft of this paragraph
  mis-attributed why it works — `@runtime_checkable` has nothing to do
  with it.** `isinstance` against a concrete, imported class needs no
  decorator at all; that check works for any class, protocol-adjacent or
  not, with or without `SurfaceGraphLike` existing. What `@runtime_
  checkable` on `SurfaceGraphLike` actually enables is the *other*
  direction — `isinstance(x, SurfaceGraphLike)`, a structural check
  against the protocol itself, useful to a caller that wants to confirm
  something conforms to the read/write surface this protocol declares
  without needing (or having) the concrete `buildsource` import in scope
  at all. Narrowing to reach `resolve_entities` specifically is the
  concrete-class check, which needs the `buildsource.SourceGraphSummary`
  import regardless of the protocol's own `@runtime_checkable` status —
  a real, ordinary, localized import at that one call site, not a
  model-layer concern, and not something the protocol's decorator
  changes either way. `SurfaceGraphLike` stays `@runtime_checkable`
  anyway, for the structural-conformance case that decorator genuinely
  does enable, just not for the reason the first draft gave. The
  second item, below, is the one still left to the implementation PR, for
  the reason already stated — it depends on auditing and migrating real
  existing readers, not on a type-contract decision a planning document can
  make in the abstract. Second: moving the L5 graph's attachment point off
  `BuildSourcePack.source_graph` has real existing readers —
  `internal_leak.py`, `buildsource/crosscheck.py`, `buildsource/
  evidence_report.py`, `evidence_depth.py`, and `cli_graph.py` among them —
  each would observe
  no graph at all the moment the L5 builder stops writing to the old
  location, silently regressing impact/cross-check/assurance behavior
  that works today.

  **A review round correctly rejected leaving this as a pure "known gap
  for the implementation PR" — there is a concrete, low-risk fix available
  now, not just a later migration obligation, and not adopting it would
  leave five real readers observing `None` the moment this phase ships.**
  Rather than migrating every reader to `AbiSnapshot.surface_graph`
  directly in this same phase (a real, separate audit this phase does not
  have the implementation in front of it to safely perform, per the
  existing reasoning below), `BuildSourcePack.source_graph` is kept as a
  live **alias** to the same object, not left unpopulated: whenever
  `build_source` exists, the L5 builder's assignment `snapshot.
  surface_graph = built` is immediately followed by `build_source.
  source_graph = built` — the identical object, not a copy — so every
  existing reader keeps observing the real, current graph through its own
  already-working access path with zero code changes on their side, while
  `AbiSnapshot.surface_graph` is simultaneously the one new, unconditional
  field every *new* consumer (the public-surface graph builder, this
  phase's own query layer) reads from. This is not a second representation
  competing with the first — it is the single `SourceGraphSummary`
  instance reachable through two attribute paths, exactly preserving the
  "one object, not two that happen to agree" guarantee this phase's own
  assembly-step design already states, just exposed at both of its
  pre-existing and newly-added access points rather than only the new one.
  Migrating each of the five readers to stop going through the alias and
  read `AbiSnapshot.surface_graph` directly remains real, scoped,
  follow-up work — genuinely Phase 3's own implementation PR's to
  schedule and verify against each reader's existing tests, since that
  part is not itself safety-critical once the alias prevents the silent
  `None` regression — but it is no longer a precondition for this phase
  to ship without breaking existing behavior.

  **The in-memory alias does not, by itself, survive a save/load round
  trip — a review round correctly traced what actually happens on
  serialization and found the "one object, two attribute paths" guarantee
  breaks exactly there.** `serialization.snapshot_to_dict()` already
  encodes `BuildSourcePack.to_embedded_dict()` (which includes
  `source_graph`) for any snapshot carrying build-source evidence, and
  this phase's own `AbiSnapshot.surface_graph` field is additionally
  serialized at the top level (the schema-version-bump field named below)
  — so a snapshot with both populated writes the identical graph twice,
  as two independently-encoded blobs. On load, decoding each field
  separately reconstructs two distinct (if currently equal) `SourceGraph
  Summary` objects rather than rebinding one to the other — a real
  mutable-object alias that held in memory is gone the moment a snapshot
  is saved and reloaded, silently doubling on-disk size for a real,
  potentially large L5 graph and letting legacy and new readers diverge
  after deserialization if either is mutated afterward. Fixed by treating
  the write side the same way the in-memory assembly step already does:
  `snapshot_to_dict()` encodes `AbiSnapshot.surface_graph` once, and
  `BuildSourcePack.to_embedded_dict()` omits `source_graph` whenever the
  owning snapshot already has one (the ordinary case for every snapshot
  this phase's assembly step touches) rather than re-encoding the same
  object a second time. `snapshot_from_dict()` decodes the top-level
  `surface_graph` once and rebinds `build_source.source_graph` to that
  same decoded instance — restoring the alias on load, not just on
  construction.

  **Aliasing a legacy document's nested graph *forward* into
  `AbiSnapshot.surface_graph` — the direction this paragraph originally
  also specified, for a document written before this phase — is itself
  wrong, and a review round correctly traced the consequence: it
  silently defeats the approximate-backfill design two sections below.**
  `resolve_public_surface()`'s whole reason for existing is that a
  snapshot with `surface_graph is None` gets the lossy-but-designed-for-
  this-case approximate graph built from its flat fields; a snapshot with
  `surface_graph` already *non-`None`* skips that backfill and queries the
  graph directly. A pre-Phase-3 document's nested `build_source.
  source_graph` is an L3-L5 evidence graph that predates the public-
  surface builder entirely — it was never populated with the `includes`/
  `declares`/`references`/`exports` edges `PublicSurfaceQuery.resolve()`
  actually traverses, so aliasing it forward makes `surface_graph`
  non-`None` while still lacking exactly the edges the query needs,
  silently skipping the intentional approximate-backfill path in favor of
  querying a graph that resolves to a *smaller or empty* public surface
  than either the backfill or the pre-migration flat-snapshot traversal
  would have produced — worse than leaving it `None`, not equivalent to
  it. Fixed by not aliasing in this direction at all: for a legacy document
  (no top-level `surface_graph` key), `AbiSnapshot.surface_graph` stays
  `None` exactly as it would for any other snapshot predating this field,
  triggering `resolve_public_surface()`'s own designed fallback correctly;
  `build_source.source_graph` is decoded from its own nested key exactly as
  it always was, unaffected, so the five pre-existing L5 readers
  (`internal_leak.py`/`crosscheck.py`/`evidence_report.py`/
  `evidence_depth.py`/`cli_graph.py`) see the identical graph they always
  did. The "one object, two attribute paths" guarantee is therefore scoped
  to what this phase's own assembly step actually produces — a freshly
  extracted or freshly re-saved snapshot, whose `surface_graph` has
  genuinely been through the public-surface builder — not retroactively
  forced onto a document this phase never touched.
- **The relevance query** — `abicheck/policy/public_surface.py` (new):
  `PublicSurfaceQuery.resolve(graph, explicit_roots) -> frozenset[EntityId]`,
  a traversal from explicit public roots through `includes`/`declares`
  edges (closing the reachable-header surface) and `references`/
  `instantiates` edges (closing the reachable-type surface). `policy -> compare`
  is an already-allowed import edge under ADR-061, so `policy/` can consume
  the `compare/`-built graph directly; this is where `compute_public_
  surface()`'s actual decision logic — which declarations count as part of
  the public contract — lives after migration.

  **`resolve()`'s bare `frozenset[EntityId]` is not a complete replacement
  for today's `PublicSurface`, and a first draft of this bullet implied it
  was by never saying otherwise — review correctly read that silence as a
  real gap, not a simplification.** `surface.py`'s `PublicSurface` carries
  far more than membership: `resolvable`/`has_typed_roots`/`has_provenance`
  (three independently-meaningful "can this surface be trusted at all"
  signals — no header-derived visibility at all, an export-table-only
  surface with no typed roots to close a type closure from, no provenance
  because the snapshot wasn't dumped with a public-header set),
  `ambiguous_type_names`/`exact_type_identities` (which bare-name
  resolutions are trustworthy vs. collision-prone), and two origin indices
  (`origin_by_key`/`origin_by_qualified_key`) that `_hidden_friend_owner_
  effective_origin` and other callers read directly. `FilterNonPublicSurface`
  (`post_processing.py`) checks `surf_old.resolvable or surf_new.resolvable`
  *before* it will scope anything at all — collapsing that into "is this id
  in `resolve()`'s frozenset" erases the same distinction the `exports`
  domain fix two paragraphs above already had to preserve for `ExportSurface`:
  "not reached" vs. "reachability could not be established." An empty
  frozenset is indistinguishable from either, and reading the latter as the
  former would scope out every finding on a snapshot with no resolvable
  surface at all, instead of correctly falling back to "keep everything
  unscoped" the way `FilterNonPublicSurface` does today.

  Fixed the same way the `exports` domain already is: `resolve()` stays the
  bare-membership convenience method for a caller that genuinely only needs
  set membership (the new `type_reachability`-replacement query below, which
  never needed anything but membership), but the actual replacement for
  `compute_public_surface()`'s public-domain result is a second, structured
  method — `PublicSurfaceQuery.resolve_public_domain(graph, explicit_roots)
  -> PublicSurfaceResolution` — returning a result that carries the same
  `resolvable`/`has_typed_roots`/`has_provenance`/`ambiguous_type_names`/
  `exact_type_identities`/origin-index shape `PublicSurface` already does,
  computed from graph traversal state instead of `surface.py`'s own
  independent walk. `compute_public_surface(snapshot)` (via `resolve_
  public_surface()`, the backfill wrapper above) migrates to build this
  structured result from the query instead of running its own closure walk;
  its callers (`FilterNonPublicSurface`, `classify_change_surface`,
  contract evaluation's confirmed-type-match logic) keep reading the exact
  same field names they read today, unaffected by where the computation now
  happens.

  **The `contract=exports` domain does *not* collapse into this same
  bare-`frozenset[EntityId]`-returning `resolve()`, and a first draft of
  this phase claimed it did — review correctly found that claim
  incompatible with what `export_surface.py`'s real consumers actually
  need.** `ExportSurface` is not a membership set — it is a structured
  result carrying `resolvable: bool` and the `exclusion_is_provable`
  property, computed from several independent completeness conditions (no
  observed export table, no resolved root, an untyped root, an unaccounted
  export, an unresolved type edge — `export_surface.py`'s own documented
  fail-closed gate), and `contract_evaluation.py`/`contract_evidence_
  collect.py` consume exactly that structured state to decide whether a
  `PROVEN_OUT_OF_CONTRACT` classification is actually *safe* to make, not
  only whether a given `EntityId` is in some resolved set. Collapsing this
  into "is this id in the frozenset `resolve()` returns" erases the
  distinction between "not reached" and "reachability could not be
  established" — exactly the distinction `exclusion_is_provable` exists to
  keep, and losing it could let an incompletely-evidenced exclusion read as
  proven, or silently drop a real contract-coverage failure. The fix:
  `exports` queries the *same* shared graph this phase builds (the
  evidence stays unified, per the Governing Invariant), but through a
  second, differently-typed method — `PublicSurfaceQuery.
  resolve_export_domain(graph, ...) -> ExportSurface` (or an equivalent
  structured result preserving `resolvable`/`exclusion_is_provable`), not
  the bare-set `resolve()` — since this domain's consumers need the
  completeness state `resolve()`'s own return type has nowhere to carry.
  `export_surface.py`'s own closure-walk algorithm migrates to build this
  structured result from graph edges instead of its own independent scan;
  its result *shape* does not migrate into `resolve()`'s, and Phase 10's
  later deletion of `export_surface.py`'s independent closure walk (named
  below) means the walk, not the structured `ExportSurface` type or its
  consumers' own contract-evaluation logic, both of which stay exactly as
  they are, fed by the new query instead of the old scan.

`type_reachability.py`'s `directly_referenced_stdlib_types()` — itself a
relevance decision (it un-filters a record for suppression purposes) —
becomes a second, narrower query in `policy/public_surface.py` over the
same graph (a one-hop `references` filter) rather than its own independent
scan with its own ambiguity-tracking machinery — the machinery this phase
removes is exactly what Phase 2 already started removing for the identity
half of the same problem; this phase removes the *reachability* half.

**Files.** `abicheck/model/graph.py` (new — **the full dependency closure
the relocated types actually need, not only the five originally named
symbols**: `GraphNode`/`GraphEdge`/`GraphFact`/`FactConflict`/
`merge_graph_facts` plus `_normalize_if_decl_or_type`/`edge_relation_key`/
`ensure_facts_and_resolve` — a first draft of this phase named only the
first five, but `GraphNode.from_dict`/`GraphEdge.relation_key` call the
other three directly, so leaving them behind in `buildsource/graph_facts.py`
would either break those methods once that module is trimmed to a
re-export shim, or force `model/graph.py` to import back out to
`buildsource/` to reach them — the identical import-direction mistake
this phase already corrected once for `SourceGraphSummary` itself.
`ensure_facts_and_resolve`'s own identity normalization imports
`abicheck.name_classification`, which is safe to bring along — that
module has zero internal imports of its own (pure `re`-based string
utilities), so it is already a leaf and importing it from `model/`
introduces no cycle); `buildsource/
graph_facts.py`/`buildsource/source_graph.py` (trimmed to re-export from
the new location, `NODE_KINDS`/`EDGE_KINDS` and L5-specific construction
logic unchanged in place); `abicheck/compare/surface_graph.py` (new —
public-surface node/edge *kind vocabulary* and builder, using `model/
graph.py`'s primitive, not a new one); `abicheck/surface_graph.py` —
**each of `build_surface_graph()`/`compute_surface_metrics()` operates on
exactly one snapshot, and a review round correctly found an earlier draft
of this bullet gave each of them the two-snapshot `old_public_entity_ids`/
`new_public_entity_ids` pair, which neither function has any unambiguous
way to route for a single-snapshot call.** Each gains exactly **one**
optional parameter instead — `public_entity_ids: frozenset[EntityId] |
None = None` — and the pair lives only at the two-snapshot callers below,
each of which passes its own side's set to its own matching call:
`build_surface_graph(old, public_entity_ids=old_ids)`/
`build_surface_graph(new, public_entity_ids=new_ids)`. `SurfaceGraph.
public_roots()` maps a given set of ids back to the existing
`Function.name`/`Variable.name` declaration spelling — not a mangled/
symbol spelling, per the correction above (`_root_seed_types`/
`reachable_types`/`idioms.py`'s create/destroy regexes are all keyed on
this plain name already) — preserving its existing `frozenset[str]`
return type exactly — `surface_graph.py` itself still never imports
`policy/public_surface.py`, per the note above).

  **Threading `public_entity_ids` into `compute_surface_metrics()`'s own
  signature does not, by itself, make its *metrics* reflect the resolved
  surface, and a review round correctly traced why: every one of its
  *public-only* counts is computed straight from `Visibility.PUBLIC`, with
  no reference to the parameter at all.** Reading the real function:
  `public_functions`/`public_variables`/`exported_symbols`/
  `undocumented_export_ratio`/the per-header `exported_counts` tallies
  each sum `fn.visibility == Visibility.PUBLIC`/`var.visibility ==
  Visibility.PUBLIC` directly, and
  `public_types`/`public_enums` come from `_public_type_counts()`, which
  calls its *own*, entirely independent `compute_public_surface(snap)` —
  never the `public_entity_ids` its caller already resolved. Adding the
  parameter without touching any of these means `diff_surface_metrics()`'s
  `PUBLIC_SURFACE_GREW`/`SHRANK` findings (via `_public_decl_count()`,
  which sums exactly these fields) keep reflecting the legacy
  `Visibility.PUBLIC` definition regardless of what `public_entity_ids`
  says — a regression test proving the parameter was threaded through the
  call signature would pass while the emitted findings stayed unchanged,
  the exact gap this finding names. Fixed by having `compute_surface_
  metrics()` use `public_entity_ids`, when non-`None`, for every one of
  these *public-only* tallies: a function/variable counts as public when
  its own
  resolved `EntityId` is a member of the set (matching `build_
  surface_graph`'s own root-seeding rule, not a second definition), and
  `_public_type_counts()` takes the same `public_entity_ids` argument and
  counts record/enum membership directly from it instead of running its
  own independent `compute_public_surface()` resolution — the caller
  already resolved this once; a second, separate resolution inside
  `_public_type_counts()` is exactly the redundant-recomputation this
  phase's own design elsewhere avoids.

  **The per-header `declared_counts` tally is not one of these — a
  further review round correctly caught this finding's own first draft
  grouping it in with the visibility-filtered tallies.** `HeaderCoverage.
  declared` is explicitly the count of declarations physically defined in
  the header, incremented unconditionally for every function/variable/
  type/enum regardless of visibility — the denominator `HeaderCoverage.
  exported` (from `exported_counts`) is measured *against*. Filtering
  `declared_counts` to the resolved public set too would change that
  denominator and misreport header coverage, not fix it; only
  `exported_counts` (a public-only tally, alongside the other fields named
  above) is affected by `public_entity_ids`. When `public_entity_ids` is `None`
  (every call site outside `compare()`'s own pipeline), every one of these
  tallies keeps its exact current `Visibility.PUBLIC`-based behavior,
  `_public_type_counts()` included — the same explicit, narrow residual
  `public_roots()`'s own `Visibility.PUBLIC` fallback already states,
  extended to the metrics this function computes directly rather than
  through the graph — and `declared_counts` stays unfiltered in every
  case, `None` or not. `pattern_verdicts.py`
(`apply_pattern_verdicts()` gains the two-snapshot `old_public_entity_ids`/
`new_public_entity_ids` pair, each threaded through as the single
`public_entity_ids` argument to its matching `build_surface_graph()`
call); `diff_surface_
metrics.py` (`diff_surface_metrics()` gains the identical pair, each
threaded through the same way to its matching `compute_surface_metrics()`
call); `checker.py` (`_apply_pattern_verdicts_step`/`_apply_surface_metrics`
both gain the identical pair — received as an already-resolved parameter
from `compare()`'s own caller (`classify_compare_pair()`/`service.
compare_snapshots()`), never resolved by `compare()` itself; see
immediately below for why). **Neither
`checker.py` nor `compare()`
itself may call `PublicSurfaceQuery.resolve()` directly to populate it —
a first draft of this phase's text said exactly that ("the same
`PublicSurfaceQuery.resolve()` result `compare()` already computes"),
which is the identical `policy -> compare` direction violation this
phase's own `surface_graph.py` fix (above) already corrected once, just
reappearing at a second call site `compare()` itself.** `checker.compare()`
stays `compare/`-layer code with no import of `policy/public_surface.py`
anywhere in it; the actual
`resolve_public_surface()` call (the snapshot-aware wrapper around
`PublicSurfaceQuery.resolve()`, per the backfill fix below) moves to the
workflow layer that
already orchestrates `checker.compare()` for the typed pipeline —
`service_compare_pipeline.py`'s `classify_compare_pair` (or wherever the
Phase 4 `AnalysisPlan` resolution already runs, since both need the same
graph) resolves the ids once, which is the `workflows -> model, storage,
extract, compare, policy` edge ADR-061 already permits. **`service.
compare_snapshots()` — the second, documented Tier-2 production verb that
also calls `checker.compare()` directly, per the residual fix above —
performs the identical per-side `resolve_public_surface()` call before
forwarding into `compare()`, rather than being left to fall back to the
`Visibility.PUBLIC` default `classify_compare_pair`'s callers never hit.**

**Whether `compare()`'s own new parameter is optional (with a fallback)
or required was left as this phase's second open design question, and
a later review round resolved it by proving "required, no fallback" is
the wrong answer outright — it breaks real, existing in-pipeline
callers, not just `compare()`'s own external callers.** A repo-wide
check found `compute_public_surface()` called directly, with a bare
snapshot and no resolution in hand, from *inside* the detection
pipeline itself: `diff_stdlib_impl.py`/`surface_graph.py`'s own
`_public_type_counts()` call it with a single snapshot argument and no
`PipelineContext`/workflow caller anywhere in their own call chain to
have pre-resolved anything; `post_processing.py`'s `FilterNonPublic
Surface` and `contract_pipeline.py`'s evidence-collection stage already
call it too, but — tellingly — `contract_pipeline.py`'s own existing
code already shows the right shape for this: it reads `pp_ctx.surf_old`/
`.surf_new` (the cache `FilterNonPublicSurface` populates) *first*, and
falls back to an independent `compute_public_surface()` call only when
that cache is empty (a POST-manifest-only run, or `scope_to_public_
surface=False`, which never populates it). A "required, no fallback"
signature cannot serve any of these calls — none of them have a
pre-resolved `PublicSurfaceResolution` to pass, and `diff_stdlib_impl.py`/
`surface_graph.py` have no `PipelineContext` to read a cached one from
at all. Resolved instead the way `build_surface_graph()`/`compute_
surface_metrics()` already resolve the identical tension one section
up: `compute_public_surface()` keeps an **optional** `resolution:
PublicSurfaceResolution | None = None` parameter, not a required one —
when `None`, it calls the same lazy, snapshot-aware `resolve_public_
surface()` wrapper internally (the identical call a workflow-layer
caller would make, just made on the callee's behalf rather than the
caller's) instead of requiring every call site to pre-resolve. A caller
that already has a cached resolution in hand (`contract_pipeline.py`'s
`pp_ctx.surf_old`/`.surf_new` reuse, or `compare()`'s own pipeline once
it resolves once per snapshot up front) passes it explicitly purely as
an optimization avoiding redundant recomputation — never because the
function would otherwise fail to run.

**That internal fallback call is itself `surface.py` importing from
`policy/public_surface.py`, and a further review round correctly asked
whether this reintroduces the exact `compare -> policy` direction this
phase's own design elsewhere forbids.** Checked against the actual
enforcement, not assumed either way: `scripts/check_architecture.py`'s
package-boundary gate only evaluates a file once `_source_layer_for()`
resolves it to an already-migrated ADR-061 package — its own loop reads
`if source_layer is None: continue` before checking a single import.
`surface.py`, `diff_stdlib_impl.py`, `surface_graph.py`, `post_
processing.py`, and `contract_pipeline.py` are all still flat, top-level
modules today — none of them has been migrated into `abicheck/compare/`
(or any other ADR-061 package) by any phase in this plan — so
`source_layer` resolves to `None` for every one of them, and the gate
checks nothing about what they import, `policy/public_surface.py`
included. There is therefore no currently-enforced violation this design
introduces, and the `policy -> compare` directionality this phase
enforces elsewhere is specifically about code that *has* migrated into a
package — `policy/public_surface.py` itself, which the gate does check,
imports only `compare/` and `model/`, never backward. This is a real
residual nonetheless, not a closed question: the moment a *future* phase
migrates `surface.py` (or any of its callers) into `abicheck/compare/`,
this exact import becomes a real, gate-enforced violation, and that
future migration would need to either move `surface.py`'s `compute_
public_surface()` into `policy/` alongside the query it already calls,
or relocate the lazy-resolve fallback itself to whichever workflow-layer
caller triggers that migration — named here so that phase's own
implementation PR inherits the constraint explicitly rather than
discovering it as a fresh gate failure. `abicheck/policy/public_surface.py`
(new — `PublicSurfaceQuery`, migrated from `surface.py`'s existing
traversal logic); `surface.py` (`compute_public_surface(snapshot,
resolution: PublicSurfaceResolution | None = None)` — **not
`public_entity_ids: frozenset[EntityId]`, which a first draft of this
Files entry still said, predating (and left un-synced with) the
structured-result fix a few paragraphs above.** A bare frozenset is
exactly the membership-only collapse that fix exists to prevent:
`compute_public_surface()`'s own `PublicSurface` result carries
`resolvable`/`has_typed_roots`/`has_provenance`/`ambiguous_type_names`/
`exact_type_identities`/both origin indices, none of which a set of ids
can express, and `FilterNonPublicSurface`'s `surf_old.resolvable or
surf_new.resolvable` check (among others) runs *before*
`compute_public_surface()` has any membership set to offer at all — there
is no point reconstructing that state a second time from a bare id set
once a caller already computed it via `PublicSurfaceQuery.resolve_
public_domain()`. `compute_public_surface()` takes that structured
`PublicSurfaceResolution` directly (when given one) and projects it into
the existing `PublicSurface` field shape, so every existing reader of
`PublicSurface`'s own fields is unaffected by where the computation
happened. Every one of the four in-pipeline callers named above keeps
its existing call shape completely unchanged (`compute_public_surface
(snap)`/`compute_public_surface(ctx.old)`, no new argument required at
any of them) — this phase's acceptance bar for them is exactly that
they compile and behave identically with zero edits, not that they
thread a new parameter through. `surface.py`'s own pre-existing
traversal logic (the actual algorithm `PublicSurfaceQuery` migrates) is
still deleted per this phase's Acceptance criteria below — it is the
internal implementation that changes, not the public call shape every
existing caller already depends on. `dumper_scoping.py`/
`export_surface.py`/`type_reachability.py` (each becomes a graph *builder*
contributing nodes/edges in `compare/`, or a relevance *query* in
`policy/`, not an independent reachability algorithm); `abicheck/model/
snapshot.py` (new `AbiSnapshot.surface_graph: SurfaceGraphLike | None`
field, unconditional — not nested under `build_source`); the
`workflows/`-layer dump/compare orchestration code that already calls
`buildsource.header_graph.build_header_only_graph()`/attaches
`build_source.source_graph` (`service_header_graph_attach.py` and
siblings) gains the one line constructing a single `SourceGraphSummary`,
assigning it to `snapshot.surface_graph`, and threading that same
instance into both the public-surface builder and the L5 builder, per the
assembly-step design above. `abicheck/
workflows/consumer_graph.py` (ADR-057's consumer graph) and ADR-053's
TU→link-unit→DSO attribution are explicitly **not** migrated to query the
graph in this phase — each stays a candidate for a later, separate phase,
per this plan's "don't attempt a change with no real caller" discipline
(see AGENTS.md's "shape first, wiring later" gap and ADR-063 D7's
capability-lifecycle states) — but the moment this phase ships, the graph
they would eventually query is already the single, merged
`SourceGraphSummary` instance per the assembly step above, not a second
object they'd need their own migration to reconcile with.

**Tests.** Every existing `surface.py`/`type_reachability.py` regression
test (including the namespace-collision property suite Phase 2 already
restated for identity) is kept and re-targeted at
`PublicSurfaceQuery.resolve`'s output — this phase's acceptance bar is
that none of those tests need a *behavior* change, only a different call
path, and any test that does need a behavior change is a sign this phase
introduced a real regression, not a refactor. The existing L5 source-graph
test suite (`tests/test_source_graph*.py`/`tests/test_graph_facts.py` or
their current equivalents) is re-run unchanged against the relocated
`model/graph.py` primitive via `buildsource/graph_facts.py`'s re-export
shim, proving the relocation is behavior-preserving rather than asserted.
One new end-to-end regression is added specifically for the shared-assembly
claim above: a project with both a public header (no L3-L5 evidence
needed) and real `--sources`/`--build-info` evidence produces exactly one
`SourceGraphSummary` instance containing exactly one graph node for a
declaration both builders see, not two separate summary objects that
happen to agree on a node id — asserted by identity (`is`) on the summary
object each builder was handed, not only by comparing their outputs after
the fact, so a future regression that quietly goes back to constructing
two independent `SourceGraphSummary()` instances fails this test
immediately rather than only failing once two disagreeing facts happen to
surface. A second, separate regression locks down persistence: today's
writer never round-trips a graph through plain `asdict()` at all — the
existing `BuildSourcePack.to_embedded_dict()`/`SourceGraphSummary.
from_dict()` pair is a deliberate special case precisely because the
graph's canonical encoding isn't the dataclass default, reached today only
through `build_source`'s old attachment path. Moving the graph onto
`AbiSnapshot.surface_graph` directly needs the identical special-casing
added to `serialization.py`'s `snapshot_to_dict()`/`snapshot_from_dict()`
for the new field — without it, a saved-then-reloaded snapshot's
`surface_graph` comes back as `None` or a bare `dict`, not a
`SourceGraphSummary`, silently breaking `resolve_public_surface()`'s
snapshot-reading backfill on every persisted (as opposed to freshly-dumped)
snapshot. A populated-graph
save/load round-trip test (construct a snapshot with a real, non-empty
`surface_graph`, write it, read it back, assert the reloaded object is a
`SourceGraphSummary` with the same nodes/edges) is required by this phase,
not deferred to Phase 10's cleanup. A third regression covers the legacy
backfill: load an old-schema snapshot (`surface_graph=None`, constructed
the way a pre-this-phase snapshot would be) alongside a fresh one with a
real `surface_graph`, run `compute_public_surface`/`compare()` against
both, and assert the old snapshot's query result matches what
`resolve_public_surface()`'s lazy,
in-memory backfill produces rather
than crashing or returning an empty surface — and assert the loaded
snapshot object's own `surface_graph` attribute is still `None` afterward,
proving the backfill is genuinely not persisted back onto it. A fourth
regression covers `abicheck/surface_graph.py`'s own migration: every
existing `idioms.py`/`pattern_verdicts.py`/`diff_surface_metrics.py` unit
test calling `build_surface_graph()`/`compute_surface_metrics()` directly
is re-run unchanged (behavior-preserving, same as the `surface.py`
migration bar above) — these keep their existing call shape and the
`None`-triggered legacy fallback, since they have no policy-layer caller
in their chain. A fifth, new regression covers the threaded path itself:
a `checker.compare()` run over a fixture where `Visibility.PUBLIC` and
real reachability disagree, asserting the pattern-verdict/surface-metrics
findings `compare()` actually produces reflect the resolved `EntityId`
answer, not the legacy `Visibility.PUBLIC`-only one — confirmed by
patching two different call boundaries, not one, since the pair and the
singular value live on different functions after the single-snapshot-
helper correction (a review round correctly caught a version of this test
description that patched `build_surface_graph`/`compute_surface_metrics`
for the pair, which those functions no longer accept at all): asserting
`checker.py`'s `_apply_pattern_verdicts_step`/`_apply_surface_metrics`
call `apply_pattern_verdicts()`/`diff_surface_metrics()` with non-`None`
`old_public_entity_ids`/`new_public_entity_ids` when reached through
`compare()`, and separately asserting each of `build_surface_graph`/
`compute_surface_metrics` receives its own side's value as the singular
`public_entity_ids` argument — not only by comparing output, so a
future regression that silently stops threading either the pair or the
per-call singular value through `checker.py` fails this test even if it
happens not to change the specific fixture's output. A sixth regression pins the two-sided correction
itself, directly: a fixture where a declaration is public in `old` but
removed from the public-header set in `new` (or the reverse) — the one
shape a single shared id set would misclassify — asserting
`build_surface_graph(old)`/`build_surface_graph(new)` each receive their
own side's resolved ids, not the other side's, confirmed to fail against
a version of the threading that resolves one shared set and passes it to
both calls.
Separately, asserting
`SurfaceGraph.public_roots()` — still returning `frozenset[str]`, still
consumable by `re.Pattern.match()` with no caller change — agrees with
`PublicSurfaceQuery.resolve()`'s answer rather than the old
`Visibility.PUBLIC`-only one, confirmed to fail against the
pre-migration `SurfaceGraph` for this exact input; and a second case
asserting `surface_graph.py` imports nothing from `policy/`, enforced by
the same architecture-gate mechanism this plan already uses elsewhere for
a leaf module's import direction. A seventh regression pins the kind-filter
fix directly: a fixture where `PublicSurfaceQuery.resolve()`'s resolved
set genuinely includes a record/enum/typedef `EntityId` (a public
function's return type, reachable via a `declares`/type-reference edge)
alongside function/variable ids, asserting `SurfaceGraph.public_roots()`
still returns a clean `frozenset[str]` of only the function/variable
spellings — the type-kind id silently excluded from the root set, not
attempted-and-failed — confirmed to fail against a version of
`public_roots()` that maps every received id unconditionally.

**Acceptance criteria.** `surface.py`'s own traversal implementation and
`export_surface.py`'s independent closure walk are deleted, not kept
alongside the graph query (the actual removal happens in Phase 10's
checklist, but this phase's own PR is incomplete if it leaves both
implementations live past one release). No second node/edge dataclass
hierarchy exists anywhere in the repository after this phase —
`compare/surface_graph.py` constructs `model.graph.GraphNode`/`GraphEdge`
instances with its own kind vocabulary, the same way `buildsource/
source_graph.py` already does, never a parallel type. The new
`AbiSnapshot.surface_graph` field bumps `serialization.SCHEMA_VERSION`
the same way Phase 0's `Fact[...]` fields do (a third, independent bump
by this plan, on top of Phase 0's and Phase 7's — all three are additive
and bump the same pre-existing `AbiSnapshot`/report-schema counters, not
ADR-062's `ProjectSnapshot` schema). FP-rate gate shows no regression.

---

### Phase 4 — `AnalysisPlan`: pre-flight resolution, not mid-run discovery

**Goal.** An unsatisfiable request (an evidence requirement no resolved
collector/backend combination can produce) is rejected before extraction,
with a named reason, not discovered as a silent no-op mid-run.

**Design.** `abicheck/workflows/plan.py`: `AnalysisPlan` as a frozen
dataclass (operation, per-side `SidePlan`, requested depth, required
facts, requested toolchain/compile-context inputs) built by a new
`AnalysisPlanner.resolve(request) -> AnalysisPlan`, **raising
`PlanningError` on failure, not returning it — a first draft of this
signature wrote `-> AnalysisPlan | PlanningError`, a union return type
that directly contradicts the very next sentence's own "raise
`PlanningError`," and a review round correctly caught a contract this
plan states two different ways.** Chosen for the raise-not-return
direction to match this codebase's own existing idiom for exactly this
shape of failure (a request a resolver cannot satisfy at all, as opposed
to a resolved value with its own partial-failure fields) —
`DumpDepthNotSatisfiedError`/`ValidationError` and siblings are raised,
never returned alongside the success type in a union a caller must
narrow before using. `PlanningError` carries one entry per failed
requirement (`requested`, `why_unsupported`), modeled directly on the
`--build-target` + pre-captured `aquery` gap and the `-H` + unsupported-
collect-mode gap AGENTS.md already documents as *silent* failures — this
phase's acceptance test is exactly "these two scenarios now raise
`PlanningError` instead of silently dropping the request," and every
caller of `AnalysisPlanner.resolve()` can therefore treat a returned value
as always a usable `AnalysisPlan`, with no `isinstance`/union-narrowing
step of its own.

**"Resolved toolchain/compile context" is not a field this phase can
actually put in `AnalysisPlan`, and a review round correctly found this
plan already states why, one phase over, without connecting the two.**
`service_dump_pipeline.py`'s own `ResolvedDumpRequest` docstring (Phase 1,
already landed in this codebase) is explicit that the P0.3 L3→L2
compile-context fold cannot be determined without invoking it, and the
fold can raise `HeaderCompileContextAmbiguousError` on genuinely ambiguous
build evidence — which is exactly why that object deliberately excludes
the fold's result and the fold itself stays inside `execute_dump_request`,
never `resolve_dump_request`: running it during a side-effect-free resolve
step would be a real behavior change to `--dry-run`'s existing contract
(never raising on anything but a usage error), not an additive one. An
`AnalysisPlan` built during `resolve_compare_request`/`resolve_dump_request`
is bound by the identical constraint — it cannot carry the fold's actual
resolved compile context without either running the fold during resolution
(the same contract change `ResolvedDumpRequest`'s own design already
rejected) or leaving the field permanently unresolved, which is worse than
not stating it. Fixed by narrowing the field to what `AnalysisPlan` can
honestly carry: the *requested* toolchain/compile-context inputs (explicit
`--gcc-path`/`--ast-frontend`/language, and whatever `--build-info`/
`--sources` path was given) — the same inputs `ResolvedDumpRequest` itself
carries rather than the fold's output — not a resolved compile context.
This phase's own two named acceptance scenarios (`--build-target` +
pre-captured `aquery`; `-H` + unsupported collect mode) don't need the
fold's result either: both are about build-info/depth/collect-mode
compatibility, resolvable from the request's own inputs before any
compile-unit matching runs, so narrowing this field costs this phase
nothing it actually needed. `HeaderCompileContextAmbiguousError` itself
stays exactly where it already lives — raised from `execute_dump_request`,
not surfaced as a `PlanningError` — since catching it pre-flight would
require running the fold at resolve time, the one thing this phase cannot
do without reopening the behavior change `ResolvedDumpRequest`'s own
design already closed.

**`AnalysisPlan` deliberately does not carry resolved policy or the
surface contract, and a first draft of this phase's field list included
both — a reviewer correctly traced why neither belongs here.** This
phase's own Goal is extraction-feasibility pre-flight: rejecting a request
no resolved collector/backend combination can satisfy, *before* extraction
runs. Policy/pack overrides, contract mode, and severity configuration
answer a different question — how an already-extracted comparison's
findings are classified and scored — and for the native `compare`/`scan`
CLIs specifically, that question isn't even fully answered at the point
`resolve_compare_request`/`resolve_dump_request` return: `cli_compare_
receipt.resolve_and_apply()` (ADR-049 Phase 5) is a separate, Click-
dependent step that runs strictly *after* snapshot resolution
(`cli_compare_helpers.py`'s own `compare_cmd` calls `_resolve_compare_
snapshots()` first, `_resolve_evaluation_config()`/`resolve_and_apply()`
only afterward — confirmed by reading the real call order, not assumed),
since it depends on CLI-specific inputs (`--policy`/`--pack`/`--exit-
code-scheme`/a discovered `.abicheck.yml`) `AnalysisPlanner.resolve`'s own
request shape has no seam for. An `AnalysisPlan` that tried to carry a
"resolved policy" field populated at the earlier point would therefore be
stale or incomplete for exactly the front end D1 names first — recording a
policy the run does not actually score under, which is a worse defect than
not recording one at all. `AnalysisPlan` stays scoped to what its Goal
actually needs (evidence/extraction satisfiability, fully knowable before
any front-end-specific configuration seam runs); policy/pack/contract
resolution keeps its own existing timing, wherever a front end's own seam
for it already sits, and is not something this phase moves earlier or
threads through planning.

**ADR-063 D1's own scope is wider than `compare`/`dump`'s resolution
path alone, and a first draft of this phase didn't reach the rest of
it — D1 names the Action, `cli_project.py`, and bundle/release fan-out
explicitly as adapters that must stop orchestrating independently.**
Checking each against the real code narrows what's actually missing,
rather than treating all three as equally unconverged: `cli_compare_
release.py`'s `_run_compare_pair` already routes through `service.
run_compare` — ADR-037 D1's existing single Tier-2 chokepoint, confirmed
by that function's own docstring — so the release fan-out's *main* path
is not a second *implementation* of compare orchestration.

**That claim held only for `_run_compare_pair`, and a review round found
two more branches in the same file that it doesn't cover — `cli_compare_
release.py` is not uniformly converged, just its main path.**
`_collect_matrix_result()` (the `--probe-matrix-*` release-global
build-configuration feature) calls `service.compare_snapshots()` directly
over a pair of empty snapshots with `extra_changes` — the sanctioned Tier-2
chokepoint, not the disallowed Tier-1 `checker.compare()` core, so this
doesn't itself trip the `cli-contract` gate — but it's still not through
`service.run_compare`/`resolve_compare_request`, so it never constructs an
`AnalysisPlan` either and has no pre-flight check for its own inputs.
`_resolve_stranded_library()` (the `--bundle-facts-out` path's own
fallback for a library missing from the normal per-pair comparison) calls
`cli_resolve._resolve_input()` directly — the same Tier-2 resolution
`resolve_compare_request` itself calls, but reached independently, bypassing
the `AnalysisPlan`-producing wrapper around it, with its own bespoke ELF
fallback (`except Exception: ... AbiSnapshot(...)`) on top. Neither is
this phase's own Goal to migrate (an `AnalysisPlan` pre-flight check for a
probe-matrix build-config diff or a deliberately-degrading stranded-library
fallback is a real, separate design question, not a drive-by widening of
this phase's Files list) — named here explicitly instead, as a residual
this phase does **not** close: `cli_compare_release.py`'s release fan-out
is converged for its main per-pair comparison path only; these two
narrower branches remain outside the typed `AnalysisPlan` pipeline, a gap
for a future, separately-scoped pass to close rather than a silent
omission from this plan's own accounting. `bundle.py`'s `compare_bundle()`
takes already-computed `per_library_results` as an input rather than
calling `checker.compare`/`service.run_compare` itself, so it isn't an
orchestrator at all, just an aggregator over results the release fan-out
already produced; and the Action (`action/run.sh`) invokes the CLI as a
subprocess rather than importing `checker.compare`/`dumper.dump` in
Python, so once `cli.py` itself is the one pipeline (this plan's own
point), the Action inherits that for free as a CLI consumer. **What
*is* still missing, confirmed by reading the same code**: none of these
call sites construct an `AnalysisPlan` — `_run_compare_pair` builds and
resolves its own `CompareRequest`-shaped inputs without the pre-flight
`PlanningError` check this phase adds to `resolve_compare_request`, so a
release/bundle comparison can still hit the same silent-failure shape
(`--build-target` + pre-captured `aquery`, `-H` + unsupported collect
mode) this phase exists to close for a single-pair `compare` — the
existing Tier-2 chokepoint narrows the gap to "no second implementation,"
not "the same pre-flight guarantees." `cli_project.py`'s `project_plan_cmd`
is a narrower case again: it only *generates* `run-plan.json` (a document
`aggregate --run-plan` consumes later) and neither calls `compare`/`dump`
nor constructs an `AnalysisPlan` itself — closing its own gap (the
`--toolchain-bindings` identity-probe mismatch check it already performs
is a different, narrower pre-flight than `AnalysisPlan`'s) is not part of
this phase's scope, since it resolves a different question (build-output
coverage, not evidence-requirement satisfiability) and has no `compare`/
`dump` request to build a plan from at generation time.

**The claim two paragraphs up — that `_run_compare_pair` "builds and
resolves its own `CompareRequest`-shaped inputs without the pre-flight
`PlanningError` check" — was stale the moment a later review round (see
the Files section below) traced the real call chain and found the
opposite: `_run_compare_pair` already calls `service.run_compare`, which
calls `run_compare_request`, which calls `resolve_compare_request` — the
exact function this phase wires to construct an `AnalysisPlan`. A
release/bundle comparison therefore *does* get this phase's pre-flight
guarantee, for free, through the shared chokepoint, the same way every
other `service.run_compare()` caller does; it cannot "still hit the same
silent-failure shape" this phase exists to close, because it runs through
the identical resolver a single-pair `compare` does. Left uncorrected here,
this paragraph and the Files section's own correction below instruct an
implementer in opposite directions — build a second, independent plan for
`_run_compare_pair` versus don't, it already gets one — so this paragraph
is corrected rather than left standing: `_run_compare_pair` needs no
change and constructs no `AnalysisPlan` of its own; see the Files section
for the full reasoning.**

**Files.** `abicheck/workflows/plan.py` (new); `service_compare_pipeline.
resolve_compare_request`/`service_dump_pipeline.resolve_dump_request`
(construct `AnalysisPlan` as part of resolution — the extraction-
feasibility check only, per the Design section's own correction above; no
policy/pack resolution is constructed or reused here, since `AnalysisPlan`
carries none). **`cli_compare_release.py`'s `_run_compare_pair`
itself is *not* a Files entry, and a first draft of this phase's Files
list had it independently constructing and checking a second
`AnalysisPlan` before calling `service.run_compare` — a real mistake a
later review round caught.** `_run_compare_pair` already calls `service.
run_compare`, which itself calls `run_compare_request`, which calls
`resolve_compare_request` — the one function this phase just wired to
construct an `AnalysisPlan` as part of its own resolution. Every
`service.run_compare()` caller, `_run_compare_pair` included, already
reaches that check through the shared resolver; having the frontend build
a second, independent plan performs the same preflight twice and leaves
two copies of workflow orchestration that can silently drift apart the
moment planning gains a new probe or normalization step only one of them
gets. The release/bundle fan-out gets this phase's pre-flight guarantee
for free, through the exact chokepoint it already shares with single-pair
`compare` — no change to `cli_compare_release.py` itself is needed or
correct here. (A genuinely new capability — say, inspecting the resolved
plan *before* the comparison runs, which `_run_compare_pair` cannot do
through `service.run_compare`'s current signature — would need the
service API explicitly widened to return or accept a plan; that is a
real, separate change this phase does not need and does not attempt.)
`buildsource/adapters/bazel.py` (the `--build-
target` scoping gap gets its first real pre-flight check site here, per
its own AGENTS.md entry's recommended option 2 — reject, don't silently
scope-miss); `scripts/check_architecture.py`'s `cli-contract`/
`engine-cli-boundary` gates (widened to confirm every `service.
run_compare`/`run_compare_request` caller — not `_run_compare_pair`
independently — resolves an `AnalysisPlan` through the one shared path,
per ADR-063 D1's own statement that these gates are "widened to check
this directly").

**Tests.** Two direct regression tests reproducing the exact named gaps
from AGENTS.md (`--build-target` with pre-captured `--build-info`; `-H`
with an incompatible collect mode) — each asserting `PlanningError`, not
a warning or silent continuation. A third test reproduces the identical
`--build-target` gap through `compare-release`/bundle's own fan-out (not
only single-pair `compare`), confirming `_run_compare_pair` now raises the
same `PlanningError` for one library in a release — through the shared
`resolve_compare_request` path, not a second, frontend-local plan — rather
than silently scope-missing that one library while the rest of the
release proceeds.

**Acceptance criteria.** Both named silent-failure gaps in AGENTS.md close
as a side effect of this phase, not as separate fixes — if either needs a
bespoke patch instead of falling out of the planner, the planner's design
is incomplete and should not be landed yet. The release/bundle fan-out
gets the identical pre-flight guarantee a single-pair `compare` does, not
only the pre-existing single-chokepoint property. `cli_project.py`'s own
adapters and the Action remain out of this phase's direct scope for the
reasons stated above — named explicitly rather than left for a future
reader to rediscover by re-checking D1's adapter list against the Files
section.

---

### Phase 5 — the fact/capability registry (generalizes `change_registry.py`)

**Goal.** A new fact requires declaring the model field plus one registry
entry, not nine touched files spread across serialization, diff,
suppression, and hand-maintained docs.

**Design.** `abicheck/model/fact_registry.py`: `FactDefinition` (id, value
type, producing backends, persisted/identity-relevant/comparable/
suppressible/reportable flags, lifecycle state per ADR-063 D7). A codegen/
validation script (`scripts/gen_fact_capability_matrix.py`, mirroring
`scripts/gen_cli_reference.py`'s existing pattern) emits the backend
capability-matrix doc and a serialization-completeness check from this
registry; `scripts/check_ai_readiness.py` gains a `fact-registry-
completeness` check mirroring its existing `changekind-partition`/
`changekind-detector` checks, one level up.

**Scope.** This phase converts the *remaining* model fields Phase 0 left
alone into `Fact[T]` + a registry entry, mechanically, field by field —
each conversion is its own small commit (not one repository-wide diff),
so a regression is attributable to one field's conversion. This is
deliberately the availability-bearing subset of ADR-063 D7's stated
"every persisted, detected, or reported fact," per that decision's own
amendment scoping its initial realization this way — an ordinary,
always-present fact with no unavailable-vs-absent ambiguity has nothing
for this registry to resolve, and registering the full, unambiguous field
population is named there as a real but separately-justified future
extension, not this phase's own bar to clear. **"Remaining
model fields" means every availability-ambiguous field on every
fact-bearing model dataclass, not only the files named `model/*_facts.py`
— a first draft of this phase scoped itself to that filename pattern and
missed real candidates living elsewhere**: `RecordType.is_final` (`model/
entities.py`), `Function.contract_attributes`/`Variable.alignment_bits`
(`model/declarations.py`) are exactly the same "unavailable vs. genuinely
absent" ambiguity Phase 0 exists to close, and none of them live in a
`*_facts.py`-named file. The completeness check below must therefore scan
every dataclass field under `model/` eligible for this conversion, not
only fields already typed `Fact[T]` — a check that starts from "fields
already converted" is structurally blind to a raw field nobody has
touched yet, which is exactly how this phase could report complete while
the ambiguity it exists to close still exists.

**"Eligible" is not the same question as "which three annotation shapes
Phase 0 happened to use," and a first draft of this phase's check scanned
only `bool`/`list`/`int | None` fields — a review round correctly found
real, currently-unconverted counterexamples that shape excludes:
`Function.deprecated`/`TypeField.default` (`str | None`, each already
guarded by their own snapshot-level reliability flag) and `Variable.access`
(an enum, guarded by `castxml_var_access_facts_reliable`).** Both meet
this phase's own stated scope — "every availability-ambiguous field ...
documented as backend-dependent" — but neither is a bare `bool`/`list`/
`int | None` annotation, so a check enumerating those three shapes would
report Phase 5 complete while these (and any future field shaped like
them) stay raw, overloaded values with no availability distinction.

**"Does this field have a snapshot-level reliability flag" is itself too
narrow a key, and a further review round found it contradicts this
phase's own required examples two paragraphs above: `RecordType.
is_final`/`Function.contract_attributes`/`Variable.alignment_bits` — the
fields this phase's Scope section names specifically to prove eligibility
isn't limited to `*_facts.py` files — have no `*_facts_reliable` flag
covering any of them at all.** `is_final` is already `bool | None = None`,
documented tri-state at the *field* level (`True`/`False` = captured,
`None` = not captured) with no snapshot-level flag needed, since the
field's own optionality already carries the availability signal a
`bases`/`vtable`-shaped field needs a separate flag for. A scan keyed
exclusively on flag coverage would read all three of this phase's own
named examples as ineligible, which cannot be the intended rule for a
check this phase's own text introduces those three fields to motivate.
The actual scan key is therefore **field-based with an optional
availability source**, not flag-required: a field is eligible when it (a)
is guarded by a snapshot-level reliability flag (the `REFERENCE_FLAG_
COVERAGE`-tracked case below, covering `bases`/`vtable`/`is_va_list`-shaped
fields whose own natural resting value can't distinguish omission from
confirmed-empty), **or** (b) is already tri-state at the field's own
declared type (an `Optional`/sentinel shape whose `None`/sentinel value
already means "not captured," independent of any snapshot-level flag) —
`is_final`/`contract_attributes`/`alignment_bits` are case (b); `bases`/
`vtable`/`is_va_list` before their own Phase 0 conversion were case (a).
Both cases are scanned for, not only the flag-backed one — independent of
whether the field itself happens to be a `bool`, a `list`, an `int |
None`, a `str | None`, or an enum.

**"Sibling" overstates what this relationship actually is, and a first
draft of this correction implied a name-based lookup a reviewer correctly
found doesn't exist.** A `*_facts_reliable` flag is not a 1:1,
name-derivable sibling of the one field it guards — `fact_provenance.py`'s
own real logic shows the relationship is many-to-one and non-mechanical:
`clang_deprecation_facts_reliable` alone gates *two* fields
(`Function.deprecated` and `is_scoped`), and `clang_field_initializer_
facts_reliable` gates `TypeField.default`, a field whose own name shares
no substring with the flag's. No scan that tries to derive "which field(s)
does this flag cover" by matching names or scanning annotations can
reconstruct either relationship — and the registry lookup this check
otherwise leans on cannot find a field that was never converted or
registered in the first place, so the gap this finding closes (a field
nobody has touched yet) would survive a check keyed on an undefined
"sibling" relation exactly as it would have survived the original
three-shape enumeration. The fix is an explicit, hand-maintained inventory
— a `REFERENCE_FLAG_COVERAGE: dict[str, tuple[str, ...]]` (flag name →
every model field it guards, covering the already-known many-to-one cases
above) living alongside `fact_registry.py`, not derived at scan time — this
inventory covers case (a) (flag-backed) fields only, not case (b), since a
case (b) field's availability signal is its own declared type, not a flag
to look up — with the completeness check validated in *both* directions
per case: for case (a), every flag in the inventory names at least one
field that is either already converted or explicitly tracked as this
phase's remaining scope (an entry with no real field would be exactly the
kind of self-congratulatory registration this plan's own D7 completeness
principle forbids), and every case-(a) model field `model/`'s own
eligibility sweep finds appears under some flag in the inventory (a field
with real backend-dependent prose but no inventory entry is the "field
nobody has touched yet" case this whole correction exists to catch, now
caught by a table lookup instead of an unreliable name match); for case
(b), the sweep itself is the completeness check — every already-`Optional`/
sentinel-typed model field carrying a documented backend-dependence
comment (the same textual marker the sweep already looks for) is eligible
regardless of flag coverage, with no inventory entry required or expected.
Building the case-(a) inventory is this phase's own first concrete task,
not an assumption its design gets to take for granted — a field with no
flag, no tri-state declared type, and no other documented backend-dependence
is out of scope, the same way it was before this correction.

**Files.** `abicheck/model/fact_registry.py` (new); every fact-bearing
`model/` dataclass module with an eligible field — `model/*_facts.py`,
plus `model/entities.py` and `model/declarations.py` specifically (their
`is_final`/`contract_attributes`/`alignment_bits` fields named above, and
any sibling field matching the same shape found during the audit this
phase's first commit performs); `scripts/check_ai_readiness.py` (new
check); `scripts/gen_fact_capability_matrix.py` (new, generates what is
today a hand-maintained capability doc). **`serialization.py` for every
field converted, not only the registry and the model dataclass — a first
draft of this phase's touch list omitted it.** Every snapshot still loads
through `serialization.snapshot_from_dict()`'s explicit `Param`/
`RecordType`/other constructors (unchanged by the registry, which the
Design section above already states validates serialization
completeness but does not generate mappings); each field this phase
converts needs the identical encode/decode treatment Phase 0 already gave
its three — a `SCHEMA_VERSION` bump, the status-to-string encoding on
write, the matching decode on read, and a legacy-schema backfill path for
a pre-conversion snapshot. Without it, a persisted snapshot reloads the
newly-converted field as a plain dict (losing its `Fact[...]` type) or
drops the key outright — exactly the silent-regression shape Phase 0's
own round-trip tests were written to rule out, reintroduced here one
phase later for every field this one converts. The per-field touch list
a new fact needs (stated below) is a *fourth* item because of this, not
only the three already named.

**Tests.** `tests/test_fact_registry_completeness.py`: every `Fact[T]`-
typed model field has exactly one registry entry; **every registry
entry's declared producing backend is checked individually against a
real parser, not merely "at least one of them is real"** — a first draft
of this test accepted an entry naming one genuine producer plus any
number of nonexistent ones, which would let the generated capability
matrix falsely advertise a backend that never actually produces the fact.
The check also runs in the other direction: for each backend, every fact
that backend's own parser actually populates has a registry entry naming
that backend — an unregistered real producer is exactly the kind of
silent drift a registry meant to be the single source of truth cannot
tolerate either. A third direction closes the gap this finding raised:
the check also scans every dataclass field under `model/` for the
*eligible-but-unconverted* shape — a field named in the
`REFERENCE_FLAG_COVERAGE` inventory (per the Design section's own
corrected eligibility rule above) with no matching `Fact[...]` sibling,
not restricted to any particular annotation shape, and not derived from
the flag's own name by a match this codebase's real many-to-one flag/field
relationships don't support — and fails
if any exists once this phase claims completion — not only auditing the
fields the registry already knows about, so a field the conversion missed
entirely (not just one the registry forgot to register) fails this check
too. The regression fixture for this check includes at least one `str |
None` field (`Function.deprecated`/`TypeField.default`-shaped) and one
enum field (`Variable.access`-shaped) alongside the `bool`/`list`/`int |
None` cases, confirming the check catches a field the annotation-shape
enumeration would have missed, not only the three shapes Phase 0 happened
to use. A direct `serialization.py` round-trip test per converted field (the
same shape Phase 0's own tests already pin for its three fields) is
required for each field this phase converts — a freshly-extracted
snapshot round-trips through `snapshot_to_dict()`/`snapshot_from_dict()`
with the `Fact[...]` value and status intact, and the completeness check
above is additionally confirmed to fail (not pass vacuously) against a
converted field whose serialization pair was skipped, so the check
actually exercises the gap this finding raised rather than only the
registry-entry gap it was originally written for. Re-run the
full FP-rate/mutation-score gates once after this phase's field-by-field
conversion is complete (not per-field — the mechanical conversions don't
individually risk detector-logic drift, but the cumulative change to every
`Fact[T]`-typed field's representation is worth one full re-verification).

**Acceptance criteria.** PR #734's exact touch list (model, ELF dumper,
serialization, `Change`, diff, suppression, capability matrix, docs,
fixtures — nine files) shrinks, for a comparably-scoped new fact added
after this phase, to: the model dataclass field itself + one registry
entry + serialization encode/decode + parser + detector + test — **six
items, not five.** A first draft of this phase's acceptance criterion
named five and omitted serialization entirely, the same per-field,
hand-written `snapshot_to_dict()`/`snapshot_from_dict()` pair this
section's own `serialization.py` Files entry above just established is
still required per field (confirmed against the real code: the existing
`ElfMetadata`-enum encoding is per-path, hand-written, not a generic
tree walk a future field inherits for free) — leaving it out of the count
is the same class of gap as the already-caught "registry doesn't generate
the model field" omission below, just for a different file. **The
registry does not generate the
model field** — `FactDefinition` describes and validates an existing
`Fact[...]`-typed field on a `model/*_facts.py` dataclass; it is not a
schema from which that field is code-generated, so adding the field by
hand is still required and is explicitly counted in this acceptance
criterion rather than silently omitted from it, per this corrected draft
(a reviewer caught an earlier version of this criterion listing only four
items). **Nor does it generate the serialization encode/decode pair** —
the completeness check the Design section above adds only validates that
encode/decode exists for every `Fact[...]`-typed field, the same way it
validates registry entries, it does not write the per-field code itself.
Designing and validating real generation of the model field
itself from the registry — which would shrink the list further, to
registry entry + parser + detector + test — is out of scope for this
phase; it would need its own dataclass-field-codegen design (interacting
with `from __future__ import annotations`, `dataclasses.field(kw_only=
True)` placement, and the "new field appended last" convention every
public dataclass in this repo already follows) and is not attempted here.
The identical reasoning applies to generating the serialization pair
itself from the registry — also out of scope, also a separate, real
codegen design, not a follow-on to this phase's validator.
Demonstrate the stated (six-item) reduction directly — the phase's own PR
adds one new, real fact end-to-end as a worked example, including its
serialization round-trip, and states the
old-vs-new touch-list diff in its description.

**The six-item count is also an understatement for any fact flagged
`suppressible`/`reportable` — not just the Phase-8 persisted-fact case
below — and a first draft of this phase's worked example did not say so.**
A registry entry's `suppressible`/`reportable` flags are *validated* by
the completeness check (a flagged-suppressible fact whose `ChangeKind` has
no matching suppression-selector path, or a flagged-reportable fact whose
field is absent from the JSON schema, fails the check) — they are not
*generated* from, for the identical reason the registry does not generate
the model field, the serialization pair, or (post-Phase-8) the DTO
mapping: `suppression.py`'s selector grammar and `reporter.py`'s
schema/JSON emission are each their own real implementation, not a
derivable function of a boolean flag. Concretely, for a fact shaped like
AGENTS.md's own `elf_binding` entry (suppressible via a `binding: weak`
rule, reportable in the JSON `changes` list) the real touch list is eight
items, not six: model field + registry entry + serialization encode/
decode + parser + **suppression selector/matcher entry** + **report/
schema field** + detector + test — the two added items exist today
(`suppression.py`'s existing per-`ChangeKind` matchers, `reporter.py`'s
existing per-field JSON emission) and are unaffected by this phase; they
are simply not shrunk by it, and this plan states that explicitly rather
than letting a reader assume "suppressible"/"reportable" flags alone wire
those consumers up. Designing codegen that derives a selector/schema entry
from the registry's own flags is the identical kind of out-of-scope
follow-on as model-field/serialization/DTO-mapping generation above, not
attempted in any of the three phases.

**This six-item count holds only up to Phase 8; it gains one more item
once storage v2 lands, and this plan does not hide that.** Phase 8's own
design explicitly requires a distinct `to_dto()`/`from_dto()` mapping per
persisted field (that is the whole point of D8 — no `asdict`-based
mirror), and nothing in this registry generates that mapping either, for
the identical reason it does not generate the model field or the
serialization pair. So for a fact
added **after** Phase 8 ships, the real touch list is seven items — model
field + registry entry + serialization encode/decode + DTO mapping +
parser + detector + test — not
six, and this plan states that explicitly rather than letting the
six-item claim quietly go stale the moment Phase 8 lands. Registry-driven
DTO-mapping generation is the same kind of out-of-scope follow-on as
model-field/serialization generation above, not attempted in any of the
three phases.

---

### Phase 6 — canonical `SemanticIR` between backends and the checker

**Goal.** Type-spelling, scope, template-argument, anonymous/lambda, and
CV-qualification canonicalization happens once, not once per backend.

**Design.** `SemanticIR` is defined and tested *before* any parser is
narrowed to feed it — an earlier draft of this phase specified only the
normalizer function's signature (`normalize(raw) -> SemanticIR`) and
never the type itself, which would have let each backend's migration
converge on a different ad hoc shape behind the same name, defeating the
whole point of "one canonical IR." `abicheck/model/semantic_ir.py` (new):
`SemanticIR` is keyed by `OccurrenceId`, **not** collapsed to one entry
per `EntityId` — a first draft of this phase kept one entity per
`EntityId`, which would silently discard exactly the distinction Phase 2
introduces `OccurrenceId` to preserve: a complete definition and an
incomplete/ODR-duplicate declaration can legitimately share one
`EntityId` while carrying different availability, origin, or producer
facts, and a one-entry-per-identity map would overwrite or merge that
evidence away before comparison ever sees it. `SemanticIR.occurrences:
dict[OccurrenceId, CanonicalEntity]` holds every occurrence; a derived
`SemanticIR.canonical_entities() -> dict[EntityId, CanonicalEntity]`
projection (resolving which occurrence wins when a consumer genuinely
wants one canonical view, not every occurrence) is a separate, explicit
method for the callers that actually need that reduction, rather than
the only shape `SemanticIR` offers. **`CanonicalEntity` itself carries no
`ScopePath`/`EntityId` of its own — a first draft of this phase gave it a
resolved `ScopePath` field alongside the dict's own `OccurrenceId` key,
and a reviewer correctly flagged that as the exact "two independently
constructible representations of the same fact" shape the Governing
Invariant exists to forbid: `OccurrenceId.entity_id.scope_path` already
*is* the resolved scope, so a second, separately-settable copy on the
value means a normalizer or deserializer bug could produce a mapping
whose key names one scope and whose value reports another, with nothing
short of a dedicated equality test to catch the disagreement.** Identity
— `ScopePath` included — lives exclusively in the key; `CanonicalEntity`
holds only the non-identity payload: canonical type spelling,
template-argument list, and CV-qualification, independent of which
backend produced it, plus the `Fact[...]`-wrapped per-field availability
Phase 0 established, so a canonicalized entity can state "this backend
didn't produce this particular fact" rather than only "here is the
value." A caller that needs an entity's own scope reads it off the key it
was retrieved with (`occ_id.entity_id.scope_path`), never a field on the
value — a function that needs to hand a `CanonicalEntity` to another
caller without its key in scope returns the pair (`(OccurrenceId,
CanonicalEntity)` or equivalent), not a `CanonicalEntity` carrying a
second copy of what the key already states. This model file,
and a primitive-level test suite pinning its shape directly (construct a
few entities by hand, including two sharing one `EntityId` with
differing availability, and assert both the `OccurrenceId`→entity mapping
and the canonicalization rules independent of any real backend), land as
their own first step in this phase, before any of the following
narrowing work.

Only once `SemanticIR` itself is real does `abicheck/extract/
semantic_normalizer.py`'s `normalize(raw: RawCastXmlFacts | RawClangFacts
| RawDwarfFacts | ...) -> SemanticIR` have a concrete target to produce.
Each backend's existing parser (`dumper_castxml.py`, `dumper_clang.py`,
`dwarf_snapshot.py`, `pdb_metadata.py`) is narrowed to produce only its
own `RawXFacts` — today's `parse_types()`/`parse_typedefs()`-style
functions stop doing their own ad hoc namespace-joining, anonymous-marker
handling, and closure-identity stripping, and instead emit the backend's
literal output for the normalizer to canonicalize via the `EntityId`/
`ScopePath` primitives Phase 2 already built, converging on the one
`SemanticIR` shape just defined rather than each backend's own
reading of "canonical."

**Why this phase is ordered after Phase 2, not before.** Every
cross-backend disagreement AGENTS.md records in this area (the lambda-
closure-identity entries, the MSVC-vs-Itanium mangling-scheme entries, the
`Outer::Inner` partial-qualification entry) is a canonicalization
disagreement *about identity specifically* — Phase 2's `EntityId`/
`ScopePath` is the primitive this normalizer is built on, not a parallel
concern.

**Files.** `abicheck/model/semantic_ir.py` (new — `SemanticIR` itself,
landed and tested before any of the files below are touched);
`abicheck/extract/semantic_normalizer.py` (new);
`dumper_castxml.py`/`dumper_clang.py`/`dwarf_snapshot.py`/`pdb_metadata.py`
(narrowed to raw-fact production, each losing its own copy of
anonymous-marker/closure-identity/namespace-join logic as that logic moves
to the shared normalizer); `btf_metadata.py`/`ctf_metadata.py` (their own
`BtfType`/`CtfType`/`_TypeResolver` pairs narrowed the same way — included
per ADR-063 D9 on the same architectural grounds as the other
type-declaration-producing backends, even though neither has a specific
AGENTS.md incident motivating it yet); `name_classification.py` (its
`_ANONYMOUS_TYPE_MARKERS` and sibling helpers become the normalizer's,
used once); `dumper.py`/`dumper_manifest.py` (the assembly call sites —
call `semantic_normalizer.normalize()` on each backend's raw facts,
project into the existing `AbiSnapshot` field shapes, and attach the
`SemanticIR` itself on the new `semantic_ir` field, per the Design section
above). **`dumper.py`/`dumper_manifest.py` are not the only production
assembly call sites — `service.py` has two more of its own, each
independent, and a first draft of this phase's Files list named neither.**
`service.py`'s own BTF/CTF dispatch (around where it parses a raw BTF/CTF
blob and constructs an `AbiSnapshot` directly from `btf.to_dwarf_metadata()`/
`_typeinfo_functions(btf.func_protos)`/`dict(btf.typedefs)`, and the
identical CTF branch beside it) is a third production assembler —
narrowing `btf_metadata.py`/`ctf_metadata.py` to raw-fact production
without also routing *this* call site through `semantic_normalizer.
normalize()` would leave it assembling an `AbiSnapshot` from facts whose
shape just changed out from under it, breaking every BTF/CTF-backed
`dump`/`compare` rather than merely leaving `SemanticIR` inert.
`service.py`'s PE/PDB path is a fourth: it calls `pdb_model.
model_types_from_dwarf_metadata(dwarf_meta)` to convert PDB-derived DWARF
metadata into `RecordType`/`EnumType` objects before assembling the
snapshot — narrowing `pdb_metadata.py` alone, as the first draft's Files
list already named, leaves this second, PDB-model-specific conversion
step untouched and still producing the pre-normalization shape.

**A fifth site is not a raw-fact assembler at all, and a later review
round correctly found that this phase's own "attach `semantic_ir`
identically" treatment does not describe what it actually needs:
`dumper_hybrid.merge_snapshots()`, the `--ast-frontend hybrid` path.**
`service.py` recursively produces a CastXML snapshot and a Clang snapshot
and hands both to `merge_snapshots()`, which reconciles them via
`dataclasses.replace(castxml_snap, ...)` — a pairwise merge of two
*already-assembled* `AbiSnapshot`s, not a single backend's raw facts
going through the normalizer once. Each sub-snapshot, per this phase's own
per-backend wiring, already carries its own `semantic_ir` by the time
`merge_snapshots()` receives it — but that function's real logic only
reconciles the legacy `functions`/`types`/... projections (folding in
Clang-only entities and Clang-backfilled facts onto the CastXML base), and
has no step that does the equivalent reconciliation for `semantic_ir`
itself. Left as stated, the merged snapshot would keep the CastXML-only
`semantic_ir` unchanged while its own `functions`/`types` fields include
exactly the Clang-only and Clang-backfilled data that reconciliation adds
— the two representations disagreeing on a single, freshly-produced
snapshot, which is the Governing Invariant's one forbidden outcome, not a
legacy-compatibility accommodation. `merge_snapshots()` therefore needs
its own, fifth reconciliation step for `semantic_ir`.

**"Merging (or reconstructing) the two sub-snapshots' `SemanticIR.
occurrences` maps the same way the legacy fields already merge" is not
itself a rule, and a review round correctly pressed on what that
parenthetical was actually supposed to mean — it does not define how a
matching `EntityId` with a *different* `OccurrenceId` disambiguator is
reconciled, nor what happens when both backends produced a real,
disagreeing fact for the same entity.** The actual rule is the identical
base-plus-backfill-with-provenance discipline `merge_snapshots()`'s own
docstring already states for every other field it touches — "castxml
remains the base... only the facts documented in this module's docstring
are actually reconciled/backfilled," with `fact_provenance` recording
which backend's value won, per declaration, per fact — applied to
`occurrences` instead of invented fresh for it:

1. **Matching is keyed on `EntityId`, not the full `OccurrenceId`.** Both
   backends parse the identical headers, so the common case is two
   independently-derived `EntityId`s that are structurally identical (same
   `ScopePath`, same kind, same leaf name) with an empty disambiguator on
   both sides — exactly the "globally unique identity" case the graph-key
   fix elsewhere in this phase already reduces to plain `EntityId`
   equality for. Matching on the *bare* `EntityId` first, and only
   reading each side's own disambiguator afterward to decide *whether* a
   match is safe.

   **The safety check itself had a real bug, and a review round caught
   it precisely: "non-empty... on either side" is not the same condition
   as "both sides assert something, and they disagree" — and the
   difference breaks the ordinary case, not just an edge case.**
   `OccurrenceId`'s disambiguator carries no neutral, producer-independent
   value — it is populated from whichever USR/TU-context signal that
   *one* backend's own parse actually derived, and CastXML has no USR
   concept at all (`entity_identity.py`'s own rule: "an absent USR/mangled
   name degrades the tier, it is never guessed at"), so CastXML's side of
   an ordinary, genuinely-matching declaration is routinely empty while
   Clang's side is routinely non-empty — not because the two parsers
   disagree about identity, but because only one of them has a TU-context
   signal to report at all. The originally-stated rule ("a non-empty,
   disagreeing disambiguator on either side... left unmerged") reads that
   as a disagreement, which would leave the *common* hybrid case
   permanently unmerged — exactly the failure the reviewer traced: no
   Clang backfill ever reaches the base entity, and `semantic_ir`
   disagrees with the legacy fields `merge_snapshots()` already
   reconciles, on every ordinary declaration, not only a genuinely
   ambiguous one. The corrected rule needs **both** sides to carry a
   non-empty disambiguator before comparing them at all — an empty
   disambiguator on either side is "no additional signal from that
   backend," never itself a disagreement — and only withholds the merge
   when both are non-empty and unequal (the real TU-collision case this
   mechanism exists for: two backends that can *both* derive a TU-context
   signal and that signal genuinely differs).

   **That corrected rule is still not enough on its own, and a further
   review round found the gap it leaves: it silently assumes each
   `EntityId` maps to at most one `OccurrenceId` per side, which this
   same phase's own Design section explicitly says is false.** Phase 6's
   own text states `SemanticIR.occurrences` is "keyed by `OccurrenceId`,
   **not** collapsed to one entry" precisely because a real ODR-duplicate
   pair, or an incomplete-declaration/complete-definition pair, shares one
   `EntityId` across multiple occurrences on a single backend's own
   snapshot. "Match first by bare `EntityId`" is therefore not
   automatically a one-to-one match whenever either side's candidate set
   for that `EntityId` has more than one member — an arbitrary CastXML
   occurrence could be paired against the wrong Clang occurrence sharing
   the same identity (or vice versa), backfilling facts from a
   declaration that is not actually the same physical entity, or
   collapsing two genuinely distinct occurrences into one. Fixed by
   checking candidate-set size before attempting the disambiguator
   comparison above, not only after: for a given `EntityId`, if either
   side has more than one occurrence, matching is decided over the whole
   group at once, not pair by pair.

   **"Exactly one compatible pair survives all-pairs filtering" is the
   wrong test for "a unique matching exists," and a further review round
   gave the exact counterexample: when each side independently has
   *more than one* occurrence, a correct, unambiguous one-to-one pairing
   can still exist, and the all-pairs-filter rule rejects it anyway.**
   Two CastXML occurrences with disambiguators `{usr1, usr2}` against two
   Clang occurrences with disambiguators `{usr1, usr2}` have exactly one
   correct pairing (`usr1`↔`usr1`, `usr2`↔`usr2`) — but checking every
   cross pair against the disambiguator-safety rule leaves **two**
   agreeing pairs surviving (`usr1`↔`usr1` and `usr2`↔`usr2`), not one, so
   "exactly one pair remains" incorrectly reads this as ambiguous and
   unions the whole group unmerged, losing a real Clang backfill for both
   occurrences even though there was never any actual ambiguity. The
   correct test is uniqueness of a *complete matching* over the group, not
   uniqueness of a single surviving pair: group each side's occurrences by
   disambiguator value first — every non-empty disambiguator value present
   on *both* sides must name exactly one occurrence per side (two
   occurrences on one side sharing a non-empty disambiguator is itself a
   genuine ambiguity for that value, not resolvable by this rule at all) —
   pairing each such value 1:1.

   **The leftovers after that pass are not necessarily empty-disambiguator
   occurrences, and a further review round gave the exact counterexample:
   CastXML `{empty, usr1}` against Clang `{usr1, usr2}` pairs `usr1`↔`usr1`
   in the first pass, leaving CastXML's `empty` and Clang's `usr2` as the
   leftovers — one empty, one genuinely non-empty, neither claimed by the
   other side.** A one-sided non-empty disambiguator (present on one
   occurrence, simply absent because the other backend never derived one
   for its matching declaration) is not itself a disagreement — the same
   "no additional signal from that backend" rule the single-occurrence
   case already states — so treating every leftover as if it must be
   empty, and refusing to look at what it actually holds, would wrongly
   leave this pairing unmerged even though it is exactly as safe as the
   empty-vs-empty case. The leftover pass therefore applies the identical
   single-pair disambiguator-safety rule from above, not a
   narrower empty-only rule: when exactly one occurrence remains unmatched
   on each side, pair them unless *both* remaining disambiguators are
   non-empty and unequal (a real, two-sided disagreement — the one case
   this rule still refuses). Any `EntityId` for which this process
   leaves a non-empty disambiguator value claimed by more than one
   occurrence on either side in the first pass, leaves more than one
   leftover unmatched on either side after it, or leaves exactly one
   leftover per side whose disambiguators are both non-empty and disagree,
   has no unique matching — it is left entirely unmerged for that
   identity: every occurrence from both sides is unioned in verbatim as its
   own entry, under rule 3 below, rather than guessing at a pairing. This
   is the same fail-closed
   direction the disambiguator fix above already takes — an ambiguous
   group produces no merge rather than an arbitrary one — applied at the
   cardinality check that has to run before the pairwise comparison is
   even meaningful, not a new principle invented for it. A dedicated
   property test pins the uniquely-matchable multi-occurrence case
   directly (two same-sized groups whose non-empty disambiguators are a
   bijection, confirmed to merge both pairs correctly), the genuinely-
   ambiguous case (two occurrences sharing one non-empty disambiguator on
   one side, confirmed to leave the whole group unmerged), and the mixed
   one-sided-leftover case above (CastXML `{empty, usr1}` against Clang
   `{usr1, usr2}`, confirmed to merge both pairs — `usr1`↔`usr1` from the
   first pass, `empty`↔`usr2` from the leftover pass — rather than leaving
   the group unmerged) — the three shapes this finding's own two
   counterexamples and the original ambiguity-detection requirement each
   name.
2. **CastXML's `CanonicalEntity` is the base for every matched pair**,
   mirroring every other reconciled field in this function: Clang's
   matching occurrence backfills only the specific facts CastXML's own
   entity carries as `Fact.not_collected()`/`Fact.unsupported(...)` — it
   never overwrites a fact CastXML already resolved to `Fact.present(...)`,
   present-value disagreement included. A fact CastXML resolved and Clang
   *also* resolved, disagreeing, is not silently dropped by either
   direction: CastXML's value is kept (matching the base precedence every
   other field already uses), and the disagreement itself is recorded.

   **`fact_provenance` itself cannot carry that record, and a review round
   correctly found the reused-field claim doesn't survive checking the
   real type.** `AbiSnapshot.fact_provenance: dict[str, str]` stores only
   the *winning* producer's name (`"castxml"`/`"clang"`) per fact key —
   by design, per that field's own docstring, for every pre-existing
   legacy-field reconciliation this function already does — and has no
   slot for the losing backend's own value or for a conflict marker at
   all. Reusing it for `semantic_ir` reconciliation would silently lose
   exactly the information this step claims to preserve: once CastXML
   wins, a consumer reading `fact_provenance` sees `"castxml"` whether
   Clang agreed or actively disagreed, with no way to tell the two apart.
   Fixed with a new, additive field instead of widening the existing
   one (which every pre-existing `fact_provenance` reader already depends
   on staying `dict[str, str]`) — `AbiSnapshot.semantic_ir_conflicts:
   dict[str, str]`, valued with a `repr()` of the losing backend's
   discarded value
   (present only for a key where a real conflict occurred; absent
   otherwise, same "absence means no conflict" convention `fact_
   provenance` itself already uses).

   **Keying `semantic_ir_conflicts` identically to `fact_provenance`'s own
   fact keys is itself wrong for this specific field, and a further review
   round caught exactly why: `fact_provenance`'s keys
   (`func_fact_key(mangled, fact)`/`type_fact_key(name, fact)`/...,
   reading the real functions) name a *declaration*, not an *occurrence* —
   correct for every pre-existing legacy-field reconciliation, which never
   has more than one matched pair per identity, but this phase's own
   matching rule (two sections up) explicitly allows more than one matched
   pair to share one `EntityId` (the ODR-duplicate/incomplete-declaration
   case `SemanticIR.occurrences` exists to represent). Two different
   matched occurrence pairs sharing one `EntityId`, each with its own real
   conflict on the same fact name, would write the same declaration-keyed
   string twice — the second write silently discards the first conflict
   record, with no signal that two conflicts existed rather than one.**
   Fixed by keying `semantic_ir_conflicts` on the *occurrence*, not the
   declaration: each key is the matched pair's own `canonical_key
   (occurrence_id)` (Phase 3's collision-free occurrence rendering, already
   built for exactly this "more than one occurrence can share one
   `EntityId`" case) joined with the fact name, rather than reusing
   `fact_provenance`'s declaration-only key — so two conflicting pairs for
   the same `EntityId` occupy two distinct keys and neither can silently
   overwrite the other. `fact_provenance` itself is unchanged (its
   existing declaration-only key stays correct for the *legacy* fields it
   already reconciles, none of which has this multi-occurrence shape); a
   dedicated property test covers two matched occurrence pairs sharing one
   `EntityId`, each with its own independent conflicting fact, asserting
   both conflict records survive in `semantic_ir_conflicts` — confirmed to
   fail against a version keyed like `fact_provenance`, where the second
   pair's conflict silently overwrites the first's. `fact_provenance[key]
   == "castxml"` (declaration-keyed, for the legacy fields) plus the
   occurrence-keyed `semantic_ir_conflicts` together give a consumer both
   which backend won a given legacy field and, per occurrence, that a real
   disagreement, not mere agreement, produced a `semantic_ir` outcome.
3. **A Clang-only `EntityId` (no CastXML match at all) is unioned in
   verbatim**, exactly mirroring how a genuinely Clang-only function/type
   is appended rather than dropped in the existing legacy-field merge.

Added to the Files list below and to the parity-test requirement, since a
`--ast-frontend hybrid` `dump`/`compare` is a real, already-
documented production path this phase's own "every assembly call site"
bar already commits to covering, not a fifth site invented for this
finding. The BTF/CTF dispatch and PDB path are
updated the same way `dumper.py`/`dumper_manifest.py` are: call
`semantic_normalizer.normalize()` on the raw facts and project through the
existing `AbiSnapshot` field shapes, attaching `semantic_ir` identically
— `model/snapshot.py` (the new `AbiSnapshot.semantic_ir` field);
`pdb_model.py` (`model_types_from_dwarf_metadata` narrowed to raw-fact
production the same way `pdb_metadata.py` itself is, per the Design
section's own parser-narrowing rule, since it's a second conversion layer
for the identical backend, not a different one);
`dumper_hybrid.py` (`merge_snapshots()` gains the `semantic_ir`
reconciliation step named above, alongside its existing legacy-field
merge);
`serialization.py` (`SCHEMA_VERSION` bump, and a real encode/decode design
for `semantic_ir` — **not the bare "field's encode/decode" a first draft
of this phase left unspecified, which understates a genuine technical
blocker.** `SemanticIR.occurrences: dict[OccurrenceId, CanonicalEntity]`
is keyed by a dataclass, and `snapshot_to_dict()` calls whole-snapshot
`asdict(snap)` — `dataclasses.asdict()` recurses into a dict's *keys* the
same way it recurses into values, so an `OccurrenceId` key becomes a
nested dict before `json.dump()` ever runs, and a dict is unhashable —
`asdict()` itself raises constructing the converted mapping, for every
snapshot once `semantic_ir` is populated, not only on the eventual JSON
write. Flattening `OccurrenceId` into a string key (the same move Phase
2's `storage/entity_ids.py` finding rejected for `EntityId`'s own
`ScopePath`) would reintroduce the identical lossy-flattening defect for
the identical structural reason — an `OccurrenceId` carries an `EntityId`
carrying a `ScopePath`, so a string rendering can't be reversed any more
than `ScopePath` alone could. The fix follows the same shape Phase 2's
v2 DTO already established: `semantic_ir` is excluded from the plain
`asdict()` walk (the same special-casing `surface_graph` already needs,
per Phase 3's finding) and encoded as a **list of entries**, not a dict —
`{"occurrences": [{"occurrence": <dto>, "entity": <dto>} for each
occurrence_id, entity in self.occurrences.items()]}`.

**The encode/decode functions themselves do not live on the domain types —
a first draft of this phase put `to_dict()`/`from_dict()` directly on
`SemanticIR`/`OccurrenceId`/`EntityId`, and a reviewer correctly caught
that this reverses the dependency direction D8/ADR-061 already establish.**
`storage/entity_ids.py`'s v2 DTO conversion is owned by `storage/`
precisely so `model -> storage` never has to exist as an edge; a `model/
semantic_ir.py`-resident `to_dict()` that calls into `storage/entity_ids.
py`'s DTO functions would create exactly that edge, and reimplementing the
identical structured-segment encoding directly inside `model/` would
create the duplicate encoding this same paragraph already rejects one
paragraph up. The actual owner of this conversion is `serialization.py`
itself — the one module in this codebase already positioned to depend on
both `model` and `storage` — via new, free (not method) functions,
`encode_semantic_ir(semantic_ir) -> dict`/`decode_semantic_ir(data) ->
SemanticIR`, which call `storage.entity_ids.to_dto()`/`from_dto()` for each
`OccurrenceId`/`EntityId` and assemble/take apart the list-of-entries shape
above. `snapshot_to_dict()`/`snapshot_from_dict()` call these two
functions for the `semantic_ir` field instead of either a domain-type
method or a second, storage-importing branch living in `model/`.
`elf_metadata.py`/`pe_metadata.py`/`macho_metadata.py` are
explicitly **not** touched by this phase — see ADR-063 D9's own
"deliberately excluded, not an oversight" note: binary-symbol-table
extraction has no type spelling/scope/template-argument concern for this
normalizer to canonicalize in the first place.

Narrowing the parsers is not, by itself, a complete migration: `dumper.py`
and `dumper_manifest.py` are the production call sites that invoke
`parser.parse_functions()`/`parse_types()`/... today and assemble their
return values directly into `AbiSnapshot`'s `functions`/`types`/...
fields, and `checker.compare()` consumes that `AbiSnapshot` shape
unchanged. Once a parser method returns only `RawXFacts`, neither call
site has anywhere to route the normalizer through — `dumper.py`/
`dumper_manifest.py` (both named explicitly in the Files list below, not
only in this prose) are updated in this same phase to call
`semantic_normalizer.normalize()` on each backend's raw facts and project
the result back into the existing `AbiSnapshot` field shapes. **This
projection must not go through `SemanticIR.canonical_entities()` — a first
draft of this phase specified exactly that, and it silently reintroduces
the evidence loss `OccurrenceId`-keying exists to prevent, one step later
than the earlier fix closed it.** `canonical_entities()` is defined above
as "resolve which occurrence wins" — a genuine reduction, by design, for
a consumer that explicitly wants one canonical view. `AbiSnapshot.
functions`/`types`/... are **not** that consumer: they are the existing
list-shaped fields the unchanged checker reads today, and today's
assembly already puts a complete definition and an incomplete/ODR-
duplicate declaration sharing one `EntityId` into that list as two
separate entries — routing through `canonical_entities()` here would
collapse them to one, an order-dependent, unspecified-winner loss of
exactly the evidence `SemanticIR.occurrences` (plural, keyed by
`OccurrenceId`) was built to keep. The correct projection iterates
`SemanticIR.occurrences` directly — one `AbiSnapshot` list entry per
occurrence, the same cardinality today's assembly already produces — and
is pinned by this phase's own parity test (below) proving the legacy
fields' shape and count are unchanged for a fixture containing a real
ODR-duplicate pair, not only for the common one-occurrence-per-entity
case a less pointed test could pass by accident. `SemanticIR.
canonical_entities()` remains exactly what it already was: the reduction
method for a future `SemanticIR`-aware consumer that genuinely wants one
canonical view, reachable through `AbiSnapshot.semantic_ir` below, never
through the legacy fields. The adapter making `SemanticIR` itself
available to `compare()`/future `SemanticIR`-aware detectors, not only the
projected fields, is this same assembly step: `AbiSnapshot` gains a new,
optional `semantic_ir: SemanticIR | None` field, populated by `dumper.py`/
`dumper_manifest.py` alongside the projected fields — one assembly call
produces both the backward-compatible `AbiSnapshot` shape existing
detectors read and the canonical `SemanticIR` a future detector can read
instead.

**"Rather than two independent channels that could disagree" overclaims
what one shared assembly call actually guarantees, and a first draft of
this phase left it at that — review correctly pointed out that the
guarantee is one-time, not ongoing.** Both `AbiSnapshot.functions`/
`types`/... and `AbiSnapshot.semantic_ir` are ordinary, independently
mutable dataclass fields once construction returns — nothing stops a
direct Python caller, a post-processing pass, or a deserializer from
mutating or constructing one without the other afterward, and Phase 10
does not retire either representation (each is read by real consumers:
every existing detector reads the legacy fields, a future `SemanticIR`-
aware detector reads the new one). So after this phase, a snapshot that
went through any path other than the one assembly call this phase adds
*can* carry a legacy projection and a `SemanticIR` that disagree — the
one-time construction guarantee does not survive the object's own
mutability, and this plan should not claim it does. **Not made read-only
or derived-on-access in this phase, and that is a real, named limitation
rather than a silent gap**: a `@property`-derived legacy field was already
rejected twice elsewhere in this same plan (the `vtable`/`bases` `Fact`
bridge in Phase 0, the `CanonicalEntity`/`ScopePath` duplication in this
same phase, above) for the identical reason — `dataclasses.asdict()` walks
real fields, not properties, so deriving one from the other would rename
or drop a JSON key every existing `asdict`-based consumer reads today, the
exact compatibility break this phase's own "backward-compatible
`AbiSnapshot` shape" commitment exists to avoid. What this phase *does*
add: the end-to-end parity tests below exercise every real assembly call
site and would catch the one-time guarantee failing at construction, and
retiring the legacy fields (making them genuinely derived, or deleting
them outright) is explicitly deferred — not to an unscheduled "eventually,"
but to whichever future phase first has a real `SemanticIR`-only detector
population large enough that the legacy fields have no remaining reader,
the same retirement bar Phase 10's other removals already use elsewhere
in this plan. Until then, a caller that mutates one representation
directly and not the other is responsible for keeping them consistent
itself — this phase does not enforce it at every mutation/load boundary,
since doing so would mean exactly the derived-field redesign just rejected
above. This is
additive to `AbiSnapshot` (another `serialization.SCHEMA_VERSION` bump,
same shape as Phase 0/Phase 3's), not a replacement for the existing
fields, so `checker.compare()` itself needs no change in this phase —
every existing detector keeps reading `AbiSnapshot.functions`/`types`/...
exactly as it does today; only a detector written to consume `SemanticIR`
directly (none exist yet) would read the new field. Without this wiring,
landing the Files list above either breaks every `dump`/`compare`
invocation (the parsers stop returning what `dumper.py`/`service.py`
assemble from) or
leaves `SemanticIR` fully built and fully inert beside an unchanged
production pipeline — neither is an acceptable state to merge this phase
in. An end-to-end parity test (`dump`/`compare` over a real fixture,
before and after this phase, asserting identical `AbiSnapshot` output) is
required for **each of the five assembly call sites** — `dumper.py`,
`dumper_manifest.py`, `service.py`'s BTF/CTF dispatch, `service.py`'s
PDB path via `pdb_model.model_types_from_dwarf_metadata`, and
`dumper_hybrid.merge_snapshots()` — not only the
first two, alongside the per-backend unit tests below, to prove the
normalizer-mediated assembly path is behavior-preserving for the existing
pipeline rather than asserted — **and that fixture must include a real
ODR-duplicate or incomplete/complete declaration pair sharing one
`EntityId`, not only the common one-occurrence-per-entity case**, per the
`canonical_entities()` finding above: a fixture without that shape could
pass this parity test even with the wrong (collapsing) projection, since
the collapse is only observable when more than one occurrence exists for
some identity. A separate, direct test covers `semantic_ir`'s own
save/load round trip: construct a `SemanticIR` with multiple occurrences
sharing one `EntityId` (the same ODR-duplicate shape above), attach it to
an `AbiSnapshot`, write it via `snapshot_to_dict()`, read it back via
`snapshot_from_dict()`, and assert the reloaded `SemanticIR.occurrences`
has the same keys and values as the original — confirmed to fail against
a version of `snapshot_to_dict()` that still relies on plain `asdict()`
for this field (it raises before the assertion is even reached, per the
unhashable-key defect above) and against a version using a string-keyed
encoding (it loses the shared-`EntityId`, multiple-occurrence shape the
test specifically constructs). A dedicated test covers `dumper_hybrid.
merge_snapshots()`'s own `semantic_ir` reconciliation directly: a CastXML
sub-snapshot and a Clang sub-snapshot each carrying a real, distinct
`SemanticIR` (one entity backfilled from Clang-only facts, matching the
legacy-field reconciliation this function already performs), asserting
the merged snapshot's `semantic_ir` reflects that same Clang-only entity
— confirmed to fail against a version of `merge_snapshots()` that carries
the CastXML sub-snapshot's `semantic_ir` through unchanged, the exact
drift this finding caught.

**Tests.** Every existing per-backend regression test that currently
proves "backend X handles construct Y" is kept and re-targeted at the
normalizer's output for that backend's raw facts — this is a large,
mechanical re-pointing, not new test design, and is the natural place to
retire now-redundant backend-local duplicates of the same assertion (e.g.
two nearly-identical closure-identity tests, one per backend, collapsing
into one normalizer test parameterized over both backends' raw fixtures).

**Acceptance criteria.** **Not** "an identical `SemanticIR` regardless of
source backend" — backends genuinely differ in what evidence they can
produce (DWARF may see only emitted template instantiations where a
header AST sees uninstantiated declarations too; a given backend may be
structurally unable to produce a given fact at all, which is exactly
`Fact.unsupported()`'s job from Phase 0), and requiring bit-identical
output across backends could only be satisfied by discarding real
backend-specific evidence or fabricating a fact a backend never actually
observed — the opposite of what `Fact[T]` exists to prevent. The real bar
is narrower and is what this phase actually fixes: for the subset of
facts two backends **both** produce for a shared fixture, canonical
identity and spelling (`EntityId`/`ScopePath`, template-argument/
anonymous-marker/CV-qualification rendering) must agree exactly — a single
shared test fixture (one closure-parameterized template, one
partially-qualified nested type, one using-re-exported constant) asserts
that agreement on the intersection, and separately asserts each backend's
expected `FactStatus` for the facts only one of them can produce (e.g.
`dumper_castxml.py` genuinely reporting `Fact.unsupported()` for a fact
only the clang backend extracts) — stated as one parameterized test with
two assertions per fixture, not one assertion claiming full equality.

**If Phase 2's implementation PR resolves its own open option-(a)-vs-(b)
question (above) as option (b), this phase's Files/Tests/Acceptance
criteria above do not by themselves cover what that choice defers here —
a review round correctly found the dependency stated in Phase 2 has no
corresponding landing task in this phase, which would leave `diff_
filtering.py`'s/`type_reachability.py`'s deferred post-parse consumers
permanently unmigrated, completing neither D3 nor this phase's own
acceptance bar, with nothing in either phase's checklist to catch the
gap.** Stated here explicitly, conditional on that choice rather than
asserted as this phase's work unconditionally: under option (b), this
phase's own `SemanticIR` assembly is exactly where every declaration/type
first receives a real, resolved `EntityId` (`CanonicalEntity`'s own
identity, built from the typed scope data Phase 2 establishes), so the
Files list above additionally migrates `diff_filtering.py`'s ambiguity-
tracking helpers and `type_reachability.py`'s remaining post-parse
consumers (named in Phase 2's own finding) to read that resolved
`EntityId` off the assembled `SemanticIR` instead of re-deriving ambiguity
from bare qualified-name strings, with their existing bespoke trackers
deleted in the same PR (folding Phase 2's own Phase-3-deletion-checklist
row for these two modules into this phase's PR when, and only when,
option (b) is the one actually chosen).

**That covers only the two consumers Phase 2's own finding named —
Phase 3 itself is a third, and the larger one, and a further review
round found it still wasn't rescheduled anywhere.** Phase 2's own
"not contained to Phase 2" paragraph already states the consequence for
Phase 3 directly: under option (b), Phase 3's graph builder has no
resolved `EntityId` to key `declaration`/`type` nodes by, since it is a
post-parse consumer in the identical position `type_reachability.py`'s
other consumers are in, and that paragraph names "move Phase 3's
identity-dependent parts to land with or after Phase 6" as one of the two
ways to resolve it — but named the obligation without any phase's own
Files/Tests/Acceptance section actually carrying it out, leaving option
(b) a choice with no landing task for the larger of the two things it
defers. Under option (b), this phase's Files list gains Phase 3's own
identity-dependent work too, not only the two narrower consumers above:
the public-surface graph builder (`compare/surface_graph.py`'s node/edge
construction, keyed by `EntityId`/`canonical_key(occurrence_id)`),
`PublicSurfaceQuery.resolve()`/`resolve_public_domain()`/
`resolve_export_domain()` (which need that same graph to query), and the
`model/graph.py`/`AbiSnapshot.surface_graph` persistence work Phase 3
otherwise lands on its own — each moves to land with or after this phase
instead of before it, since `SemanticIR` assembly is the first point any
of them has a real `EntityId` to build from under this choice. Phase 3's
own Files/Tests/Acceptance sections stay written exactly as they are
(they are still the correct description of the work, option (a) or (b))
— what changes under option (b) is purely *when* that work lands, stated
here rather than asserted silently resolved by a partial Phase 6 entry
covering only the smaller of the two deferred obligations — not left as a dangling forward
reference two phases back with no phase left to claim it.

---

### Phase 7 — `RunOutcome` and the last inline exit-code computation

**Goal.** The multi-target *aggregate* path (`gate.py`/`fold.py`) stops
decoding and re-aggregating raw `exit_code` integers as semantic data;
every front end encodes `RunOutcome`'s independent axes exactly once, at
the boundary. `junit_report.py`'s own per-finding `_is_failure` is
explicitly **not** in this phase's scope — see the Design section's own
correction below for why a per-finding field on `Change` is the wrong
shape for what `_is_failure` answers, regardless of which layer would
stamp it.

**Design.** `abicheck/policy/outcome.py`: `RunOutcome` (compatibility,
assurance, gate, operational, lifecycle — each axis's *underlying concept*
is already real today as `Verdict`/`AnalysisAssurance`/various ad hoc
operational-status values/ADR-053's target lifecycle, just not yet one
object). **The `gate` axis is a new type, `PolicyGateDecision`, not a
reuse of the existing `severity.GateDecision`** — per ADR-063 D6's own
note: `severity.GateDecision` carries `exit_code: int`/`blocking: bool`/
`blocking_categories`, exactly the scheme-encoded data D6 bans from domain
objects, so `RunOutcome.gate` cannot simply *be* one. `PolicyGateDecision`
is an ordered, exit-code-free value (mirroring the `IssueCategory`
ordering `severity.compute_exit_code` already uses internally — `NONE <
ADDITION_QUALITY < POTENTIAL_BREAKING < ABI_BREAKING`) that the boundary
encoders convert to `severity.GateDecision`/a raw integer, never the
reverse. **`RunOutcome` is report-level, not per-finding — it does not
replace `junit_report.py`'s per-test-case classification, and this phase
does not attempt to make it.** `junit_report.py`'s `_is_failure` decides,
per `Change`, whether that individual finding fails its JUnit test case,
after contract evaluation, scoped-finding filtering, policy overrides, and
severity mapping have already run on it — exactly the per-change
granularity ADR-042 already records `_is_failure` as needing, and an
aggregate whole-report gate/compatibility value cannot answer "does
*this* change fail" for a report where only some category blocks. The fix
this phase makes is narrower than "read `RunOutcome` instead of `changes`",
and it does **not** reuse `Change.compatibility_decision` for this — a
first draft of this phase proposed exactly that and a reviewer correctly
rejected it: `compatibility_decision` is `None`/`NOT_EVALUATED` by design
on an ordinary, non-`--contract` run, and for a `--contract` run's
excluded findings, meaning "policy did not run on this finding," per
ADR-049 D1's own documented contract — reading it as a pass/fail signal
for JUnit would either read every ordinary breaking change as an
unclassified non-failure, or require universally populating a field whose
whole point is to distinguish "evaluated" from "not evaluated," silently
erasing that distinction from the existing JSON/SARIF output every
external consumer already relies on.

**No stored `Change` field at all — this is the resolution to the
stamping-layer question three prior rounds of this plan each tried to
answer a different way, and it closes by removing the thing being argued
over rather than picking a fourth layer to stamp it at.** Every prior
attempt (`compatibility_decision` reuse, a `checker.compare()`-stamped
field, an unspecified later-layer field) shared one assumption this round's
review finally named directly: that `_is_failure`'s per-finding answer is
a *property of the `Change`*, fixed once and read many times. It is not —
the identical `DiffResult` can legitimately be rendered twice with two
different `SeverityConfig`s and `relevant_ids` sets (an info-only render
and a strict-severity render of the same comparison, say), and
`_is_failure` is *supposed* to answer oppositely for the same `Change` in
each case. A single always-resolved field baked onto the shared `Change`
object can only ever be correct for the render context that stamped it;
every other render context reads a stale answer, which is a worse defect
than any of the three per-layer placements already rejected — it is wrong
by *design*, not by picking the wrong layer. The fix: `_is_failure` stays
exactly what it is today, a per-render function of `(change,
SeverityConfig, relevant_ids)` — `junit_report.py` keeps computing it
inline, unchanged, because "inline" was never the actual problem; the
problem this phase's Goal statement should have named is the *aggregate*
exit-code computation (`gate.py`/`fold.py`, covered below), not this
function. `RunOutcome` stays exactly what it always was in this phase —
a **report-level** aggregate, `compatibility_decision` keeps its existing
meaning and existing callers completely unchanged, and this phase adds
**zero** new fields to `Change`/`checker_types.py`. "Stops computing
inline" is corrected to mean only what Phase 7 actually closes: the
multi-target aggregate path below, not `junit_report.py`'s per-finding
logic, which this phase now leaves alone entirely.

**`junit_report.py` is not the only remaining inline exit-code consumer —
a first draft of this phase missed the multi-target aggregate path
entirely.** `abicheck/workflows/aggregate/gate.py`'s `GateInfo.
from_report_data`/`from_scan_report` decode a persisted report's raw `exit_code` integer back
into `blocking`/severity semantics, and `abicheck/workflows/aggregate/
fold.py`'s own `exit_code()` aggregates every target's gate by `max()`-ing
their integer codes and branches `blocking`/filtering directly on
`t.gate.exit_code`. This is exactly the PR #700 failure mode D6 targets —
an integer read and branched on as semantic data inside the system, not
only encoded once at a boundary — and it is **not** an instance this
phase can treat as "just another front-end encoder," because unlike the
CLI's own `_exit_with_severity_or_verdict`, `gate.py` is parsing a
persisted report **produced by a separate, possibly older, process** —
the raw `exit_code` integer is a genuine external wire contract at that
boundary, not internal domain data this phase controls end to end.

The fix is additive to the report schema, not a behavior change to what
already-published reports mean: the report JSON gains `RunOutcome`'s
structured axes (`compatibility`/`assurance`/`gate`/`operational`/
`lifecycle`) alongside the existing `exit_code` field — never replacing
it, since `exit_code` is the documented external contract
(`docs/reference/exit-codes.md`) and stays exactly as it is for every
external consumer. `GateInfo.from_report_data` reads the structured fields
when a report carries them, and falls back to decoding the legacy
`exit_code` only for a report that predates this change (the same
"read once, decode for legacy, never for fresh" backfill shape Phase 0
already established for `Fact[...]` against the old reliability flags —
this phase is the second place that exact shape applies, not a new
pattern).

**Reading `RunOutcome.gate` alone is not enough, and a first draft of
this phase's fold stopped there.** `PolicyGateDecision` (D6, above) only
orders *compatibility* categories (`NONE`/`ADDITION_QUALITY`/
`POTENTIAL_BREAKING`/`ABI_BREAKING`) — it has no slot for a `scan`
report's budget-overflow or not-comparable failures, which are exactly
why `scan`'s own legacy exit-code scheme is `0/2/4/5/6`, not `0/1/2/4`: 5
and 6 are real, independent blocking conditions today's raw-code fallback
(`gate.py::from_scan_report`'s existing discriminated-on-raw-code branch)
correctly keeps blocking, per that function's own docstring. `RunOutcome`
carries this as the separate `operational: OperationalStatus` axis for
exactly this reason.

**A later draft of this phase's fix routed the operational axis through a
new field on `fold.py`/`TargetReport`, reviewed and found to be the wrong
layer — `fold.py` does not need to change at all, and `TargetReport` does
not need a new field, because `GateInfo` is already the single
representation `fold.py`'s `exit_code()` reads (`max(t.gate.exit_code for
t in gated ...)`, plus `.blocking`/`.blocking_categories` elsewhere in the
same module) and the existing codebase already folds conditions outside
`PolicyGateDecision`'s compatibility categories into exactly that same
representation — `load.py`'s own loader already synthesizes a blocking
`GateInfo(blocking_categories=("operational_error",))`/`("not_comparable",)`
for a report that never arrived or carried an ADR-050 D2 `verdict: null`
result, and `from_scan_report`'s raw-code branch maps scan's 5/6 onto a
blocking `GateInfo` the identical way. The operational axis is simply a
third source feeding the one representation those two already populate,
not a second channel `fold.py` additionally has to consult.** The fix:
`gate.py`'s own readers — `GateInfo.from_report_data`/`from_scan_report` —
fold `RunOutcome.operational` into the `GateInfo` they return, for a
fresh report that carries the new structured fields: a blocking
`OperationalStatus` value (`BUDGET_OVERFLOW`/`NOT_COMPARABLE`/
`EVIDENCE_CONTRACT_ERROR`/`EXTRACTION_ERROR`, per ADR-063 D6's own
grounded definition of the type) is combined with
`PolicyGateDecision`'s own compatibility contribution by `max()` over the
exit-code scheme both already share — the same orthogonal-axes shape
ADR-049 Phase 7's contract-coverage axis already uses elsewhere in this
codebase for "two independent failure axes, neither allowed to mask the
other," resolved once, inside `gate.py`, rather than carried as two
values for every later consumer to remember to fold themselves.
`fold.py` is therefore **unchanged by this phase** beyond no longer being
fed a `GateInfo` whose `exit_code` came from decoding a raw integer for a
fresh report — it already aggregates whatever `GateInfo` it is handed,
which is exactly what makes this the right layer: every existing
consumer of `TargetReport.gate` (`fold.py`'s `blocking_targets`/
`coverage_blocking`/`exit_code()`, the CLI summary's `blocking_categories`
join) sees the operational axis automatically, with no second read path to
keep in sync.

**Files.** `abicheck/policy/outcome.py` (new — `RunOutcome` and the new,
exit-code-free `PolicyGateDecision` ordered type, per the Design section
above; `severity.GateDecision` itself is untouched, since it remains
exactly what the boundary encoders convert *to*). **`checker_types.py`/
`Change` gain nothing in this phase** — the Design section's own
correction above replaces the earlier `gate_classification`-field plan
entirely; `junit_report.py` is correspondingly **not** touched either,
since its `_is_failure` stays the unchanged, per-render function it
already is. `html_report.py`'s CI Gate card (already `RunOutcome`-shaped
per ADR-042 — confirm it reads the new object directly rather than a
precursor shape, closing ADR-036 Increment 3 as a side effect if it
hasn't landed separately by then); `abicheck/workflows/aggregate/gate.py`
(`GateInfo.from_report_data`/`from_scan_report` read structured
`RunOutcome.gate`/`.operational` fields first, folding both into the one
returned `GateInfo` by `max()` over the shared exit-code scheme; legacy
`exit_code` decoding becomes the named fallback path, not the only path).
**`abicheck/workflows/aggregate/fold.py` needs no change in this phase** —
per the Design section's own correction above, it already aggregates
whatever `GateInfo` each target's reader returns, so folding the
operational axis into `GateInfo` at the `gate.py` layer is what makes
`max()`-over-raw-integers disappear everywhere downstream at once, not a
second deletion this file list has to separately track.
The report-writing side of `reporter.py`/`aggregate.py` (emit the new
structured fields alongside the unchanged `exit_code`, and bump
`REPORT_SCHEMA_VERSION`/`AGGREGATE_SCHEMA_VERSION` — an additive schema
change needs its version bumped the same way every prior
`report_schema_version`-gated field addition already did, per that
constant's own changelog comments; a first draft of this phase named the
field addition without the version bump, schema-file edit, or
regeneration that addition requires); `abicheck/schemas/
compare_report.schema.json`/`aggregate_report.schema.json` (the new
fields — **the authoritative package schemas, not their
`docs/reference/schemas/v1/` mirror, which a first draft of this row
named instead.** `scripts/publish_schemas.py`'s own docstring states the
direction plainly — "the package copy is the source of truth" — copying
`abicheck/schemas/*.schema.json` onto the docs mirror, never the reverse;
editing the mirror directly would have its hand-added fields silently
overwritten the next time anyone runs the publisher, and in the meantime
leaves the actual schema package validates fresh reports against
unchanged, so a freshly-generated report carrying `RunOutcome` fields
would fail validation against its own schema rather than the mirror
merely drifting). Edit the two package schema files, then run
`scripts/publish_schemas.py` to regenerate the
`docs/reference/schemas/v1/` mirror from them — never the other order.

**Three more writers are not report-reading fallback paths but
*synthetic* report builders, each stamping `REPORT_SCHEMA_VERSION`
directly and independently of `reporter.py`/`aggregate.py` — a gap a first
draft of this phase's Files list didn't catch, since these aren't shaped
like the other named writers.** `buildsource/check_report.py`'s
`build_operational_error_report()`/`build_bootstrap_report()`/
`build_new_target_report()` each hand-build a report `dict` from scratch
for exactly the three non-`EXISTING` cases `RunOutcome.lifecycle`/
`.operational` exist to represent (`EXTRACTION_ERROR`, `BOOTSTRAP`, and
`NEW_TARGET` respectively) — once `REPORT_SCHEMA_VERSION` is bumped, these
three would stamp the new version number on a document that still omits
the very structured axes that version bump is *for*, for precisely the
cases those axes were built to cover. Each gains the identical structured-
field emission the other writers in this phase add — `build_operational_
error_report()` emits `RunOutcome.operational = EXTRACTION_ERROR`;
`build_bootstrap_report()`/`build_new_target_report()` emit
`RunOutcome.lifecycle = BOOTSTRAP`/`NEW_TARGET` respectively — alongside
their existing legacy sentinel fields, unchanged. None of the three
ever computed a real compatibility verdict, which is exactly why
ADR-063 D6's `compatibility` field is `CompatibilityVerdict | None`
rather than required: all three construct their `RunOutcome` with
`compatibility=None`, the honest "no comparison ran" value, rather
than inventing one. `scan_engine.
ScanOutcome.to_dict()` (a separate, independent report writer a first
draft of this phase's file list missed entirely — not a sibling of
`reporter.py`'s compare-report writer, and `gate.py`'s `GateInfo.
from_scan_report` is the matching separate *reader* already in this
phase's file list, so leaving the writer unmigrated would mean every
freshly-generated `scan` report still lacks the structured fields and
keeps forcing `from_scan_report` onto the legacy-decode path D6 means to
reserve for genuinely old reports, not new ones). **`scan_engine.
ScanOutcome` is not the only scan-report writer — a later review round
found two more, independent of it and of each other, missed by the first
fix in turn: `service_scan.py`'s `ScanResult.to_dict()` (the typed-API
single-binary scan result) and `ScanSetResult.to_dict()` (the
`--artifact-set` sibling, ADR-056) each build their own dict directly —
`verdict`/`exit_code` as raw fields, no call into `ScanOutcome` or any
shared writer — so migrating `scan_engine.ScanOutcome` alone would leave
exactly these two typed-API paths (and `ScanArtifactResult.to_dict()`,
which wraps `ScanResult.to_dict()`'s output unchanged) emitting the old,
unstructured shape while stamping the newly-bumped `SCAN_SCHEMA_VERSION`
— a document claiming a schema version it doesn't actually carry the
fields of, which is a worse state than not bumping the version at all.**
Both gain the identical structured `RunOutcome`-axis fields
`ScanOutcome.to_dict()` adds, alongside their existing `verdict`/
`exit_code` fields (additive, same as every other writer in this phase).
`scan` reports carry
their own independent `SCAN_SCHEMA_VERSION` (`schemas.py`) — genuinely
separate from `REPORT_SCHEMA_VERSION`/`AGGREGATE_SCHEMA_VERSION`, not
another name for one of them, since `scan_engine.py`/`service_scan.py`
stamp it on their own report shape. Emitting the new structured fields
from this writer needs `SCAN_SCHEMA_VERSION` bumped too, for the identical
reason the compare/aggregate counters are bumped above — a freshly
regenerated `scan` report with the new fields but an unbumped
`scan_schema_version` reads as the old schema to any version-aware
consumer and defeats the whole point of versioning the additive change.

**Two more writers, found after the synthetic-writer correction above,
are the ones this phase exists for most directly and were still missing:
the two paths that actually produce a `verdict: null` NOT_COMPARABLE
report.** `report/not_comparable.py`'s `not_comparable_document()` — the
ADR-050 D2 comparability-refusal document `checker.compare`'s own gate
raises before any `DiffResult` exists to build a report from — and
`cli_compare_release.py`'s per-library refusal branch (the release
fan-out's own inline `report_schema_version: REPORT_SCHEMA_VERSION`
construction for the identical refusal, independent of the shared
document builder) both stamp `REPORT_SCHEMA_VERSION` directly with no
`RunOutcome` fields at all. Once this phase's schema bump lands, a freshly
produced refusal report from either path claims the current schema while
omitting `RunOutcome.operational = NOT_COMPARABLE` — the exact axis this
whole phase exists to make structured-first — forcing `GateInfo.
from_report_data`'s new reader back onto the legacy-decode fallback this
phase means to reserve for genuinely old reports, on a report that isn't
old at all. Both gain `RunOutcome.operational = NOT_COMPARABLE` alongside
their existing `verdict: null`/`reason` fields; `not_comparable_document()`
takes the value as an explicit parameter the same way it already does for
`report_schema_version` (per that function's own stated reason — a report
schema version it does not itself own — which applies identically to a
`RunOutcome` axis it likewise must not hardcode), and `cli_compare_
release.py`'s refusal branch passes it through the identical way it
already threads `report_schema_version`.

**Making `not_comparable_document()`'s new parameter required breaks a
third path neither of the two writers above is the caller for, and a
review round correctly traced the real call chain to find it: the
*normal*, single-pair `compare --format json` refusal.** `cli_compare_
helpers.py`'s own comparability-gate handler calls `render_not_comparable_
json()` — not `not_comparable_document()` directly — which itself calls
`not_comparable_document()` one layer down; neither
`render_not_comparable_json()`'s own signature nor its one real call site
in `cli_compare_helpers.py` gained the new parameter, so an ordinary
`compare` invocation that hits a profile/scope mismatch (ADR-050 D1/D2,
the most common way a user actually reaches this refusal path, not only
the release fan-out) would either raise `TypeError` on the now-required
argument or — if the parameter were merely added without being threaded
here — silently keep stamping the current schema version with no
`RunOutcome.operational` at all, the identical gap this paragraph exists
to close, just one call deeper than where the first fix stopped.
`render_not_comparable_json()` gains the identical parameter, threaded
straight through to `not_comparable_document()` the same way it already
threads `report_schema_version`; `cli_compare_helpers.py`'s own call site
passes `operational=NOT_COMPARABLE` explicitly, the same value the
release fan-out's own refusal branch now passes. Added to this phase's
writer inventory and to the schema-version parity tests alongside the
other writers above — three real producers of this document now, not
two, each verified through its own actual call chain rather than only at
the shared builder's own signature.

**A fourth writer-adjacent call site needs this phase's attention, and it
is not a new writer — it is an existing *neutralizer*, missed by the
first draft because its own subject is mutating an already-written
report, not producing one: `buildsource/check_report.py`'s
`_neutralize_gate()`.** `check-project.yml`'s `gate-mode: advisory` path
zeroes a report's legacy `severity`/`exit_code` contribution in place
(that function's own docstring: "Only `advisory` reports are rewritten
this way"), and its own accumulated review history already lists three
prior rounds of exactly this shape of bug — zeroing only the top-level
field left a nested `diff.severity` block, or the orthogonal
contract-coverage contribution, still driving the trailing `aggregate`
job to a nonzero exit, each caught and fixed in turn. `RunOutcome.gate` is
a **fourth** axis this function does not know about yet, and `GateInfo.
from_report_data`'s own new structured-field-first reading (this phase's
own change, a few paragraphs above) is exactly what makes the omission
land: once a fresh report's structured fields are preferred over the
legacy ones this function *does* zero, an unchanged, still-blocking
`RunOutcome.gate` value overrides the neutralization entirely, and an
explicitly `advisory` check blocks the trailing aggregate anyway — the
identical failure mode this function's own history keeps rediscovering,
reached through the one axis this phase adds rather than one of the three
it already covers. `_neutralize_gate()` gains the identical treatment for
that one axis: zero `RunOutcome.gate`'s own blocking contribution the same
way it already zeroes `severity`/`exit_code`/the nested scan-shaped block/
the coverage contribution — one more axis added to a function whose whole
job is "every *compatibility-policy* axis that can block, zeroed for
advisory."

**`RunOutcome.operational` is deliberately excluded from that
treatment, and a first draft of this paragraph said to zero it
alongside `.gate` — review correctly caught that as reproducing the exact
class of bug this function exists to prevent, just on the opposite axis.**
`check_report.final_exit_code()`'s own docstring states the invariant in
so many words: "Operational errors... always fail the job regardless of
`gate-mode`... resolve-baseline's failure taxonomy is never silently
degraded to a passing/neutral outcome either," and its implementation
returns `1` on `operational_error` unconditionally, *before* it even
branches on `gate_mode`. `RunOutcome.operational` (`BUDGET_OVERFLOW`/
`NOT_COMPARABLE`/`EVIDENCE_CONTRACT_ERROR`/`EXTRACTION_ERROR`) is exactly
this same signal in the new structured shape — an analysis that never
produced a real compatibility verdict at all, as opposed to one that did
and scored it `ABI_BREAKING`. Zeroing it under `advisory` would let a scan
that hit a budget overflow or a hard evidence-contract error read as a
clean, non-blocking pass once a consumer prefers the structured fields —
silently degrading exactly the failure taxonomy `final_exit_code()` says
must never be degraded, and confirming the reviewer's point that `.gate`
and `.operational` are not one axis that happens to share a dataclass:
`.gate` is the thing `advisory` is for deferring, `.operational` is the
thing no gate-mode may ever defer.

**A fifth mutator needs the identical structured-field treatment, found
alongside `_neutralize_gate()` but pulling in the opposite direction — it
*escalates* rather than neutralizes, and the same
structured-field-preferred reading can silently erase an escalation the
same way it could silently keep a neutralized gate blocking.**
`buildsource/check_report.py`'s `_escalate_removed_library_severity()` is
`augment_report()`'s own fold for `--fail-on-removed-library`'s exit 8 —
the one case, per that function's own docstring, where the composite
Action's real process exit code can diverge from what the report body
itself persisted, since `compare_release_cmd`'s `_exit_compare_release`
applies exit 8 "in preference to the severity code." It writes only the
legacy `severity` dict (`exit_code`/`blocking`/`blocking_categories`) —
`augment_report()` has no `RunOutcome.gate` field to write yet at all, so
once `GateInfo.from_report_data` is reading the structured fields first,
a report this exact function escalated reads `RunOutcome.gate = NONE`
(never populated, defaulting to non-blocking) while its legacy `severity`
block correctly reads `blocking: true` — a deferred aggregate job reading
the preferred, structured field sees no escalation at all and passes a
release whose removed-library gate the caller explicitly asked for,
silently, with no warning and no test currently covering this exact path.
`_escalate_removed_library_severity()` gains the matching structured
write — `RunOutcome.gate = ABI_BREAKING` (the same tier its existing
legacy write already encodes: "a whole library disappearing is
unambiguously an ABI break," per that function's own docstring) — folded
in at the identical call site `augment_report()` already has
(`analysis_exit_code == _REMOVED_LIBRARY_EXIT_CODE`), alongside the
existing severity write, never replacing it. A dedicated regression
reproduces the exit-8 path end to end through `augment_report()` itself
(not a hand-built report dict) and asserts `GateInfo.from_report_data`
reads a blocking result from the escalated report under every gate mode
except `advisory` — confirmed to fail against a version of
`_escalate_removed_library_severity()` that writes only the legacy block,
which is exactly today's code.

**Tests.** `tests/test_junit_report.py`'s existing suite needs no changes
at all — `_is_failure` is untouched, so this is the test that proves the
Design section's own correction is real: if any of these tests needed to
change, this phase would have reintroduced a `Change`-level field by
another name. A
second parity test for the aggregate path: `fold.py`'s `exit_code()`/
`blocking` output is unchanged for every existing `tests/test_aggregate.py`
fixture, run twice — once against a report carrying only the legacy
`exit_code` field (proving the fallback decode path reproduces today's
behavior exactly) and once against a report regenerated with the new
structured fields (proving the new path agrees with the old one on every
existing fixture, not only on fixtures written after this phase).
**Explicitly included in that fixture set, not left to be covered
incidentally: the two `scan`-specific exit codes `PolicyGateDecision`
alone cannot represent** — a fresh `scan` report carrying exit 5 (budget
overflow) and one carrying exit 6 (not-comparable), each constructed with
the new structured `RunOutcome` fields, asserted to still produce a
blocking `GateInfo` from `gate.py`'s own reader and to still aggregate as
blocking through `fold.py`'s unchanged `exit_code()` — confirmed to fail
against a `gate.py` reader that folds only `RunOutcome.gate` into the
returned `GateInfo` and ignores `.operational` entirely, which is the
exact regression this finding caught (`fold.py` itself needs no
corresponding test change, since it is not touched by this phase). A third
parity test covers the writers this phase adds — all three of them, not
only `scan_engine.ScanOutcome`: a freshly-generated `scan` report
(`ScanOutcome.to_dict()`), a freshly-run typed-API `ScanResult.to_dict()`,
and a freshly-run `--artifact-set` `ScanSetResult.to_dict()` each carry
the new structured fields, and `GateInfo.from_scan_report()` reading any
of the three fresh reports takes the structured-field path, not the
legacy-decode fallback — confirmed by
asserting which path actually ran (not only that the output matches),
since a test that only checks the output could pass with the writer
changed and the reader still silently falling back. A parity test for
`_neutralize_gate()` pins the new-axis gap directly, and deliberately
pins the two axes to *opposite* outcomes rather than asserting one blanket
"non-blocking" result — asserting the same thing for both would silently
reproduce the `.operational` bug this same finding's own fix exists to
prevent, just inside the test instead of the implementation. Case one: a
fresh report carrying a blocking `RunOutcome.gate` value (no operational
failure), run through `_neutralize_gate()` under `gate-mode: advisory`,
then read back through the same `GateInfo.from_report_data`/
`from_scan_report` this phase's reader changes use — asserting the
aggregate sees a non-blocking result, confirmed to fail against a version
of `_neutralize_gate()` that zeroes only the pre-existing legacy axes and
leaves `.gate` untouched, reproducing the exact "advisory check blocks the
trailing aggregate anyway" failure mode this function's own prior review
rounds already fixed three times for other axes. Case two: a fresh report
carrying a non-blocking `RunOutcome.gate` alongside a real
`RunOutcome.operational` failure (e.g. `EVIDENCE_CONTRACT_ERROR`), run
through the identical `_neutralize_gate()`/`gate-mode: advisory` path —
asserting the aggregate still sees a **blocking** result, confirmed to
fail against a version of `_neutralize_gate()` that zeroes `.operational`
alongside `.gate` (this finding's own rejected first draft), which is
precisely what `final_exit_code()`'s "operational errors always fail the
job regardless of gate-mode" invariant forbids. A fourth test
validates every regenerated fixture report against the regenerated
`docs/reference/schemas/v1/compare_report.schema.json`/
`aggregate_report.schema.json` (the same validation
`scripts/verify.py`'s `fair-metadata` step already runs for generated
files), so the new fields are provably reflected in the published schema
mirror, not only in the Python writer. A fifth test covers the three
synthetic builders directly: calling each of `build_operational_error_
report()`/`build_bootstrap_report()`/`build_new_target_report()` and
asserting the returned document carries the correct, non-`EXISTING`
structured axis (`RunOutcome.operational`/`.lifecycle` respectively) —
confirmed to fail against a version of each builder that stamps the
bumped `REPORT_SCHEMA_VERSION` without adding the matching structured
field, the exact "claims a schema version it doesn't carry the fields of"
defect this phase's own `ScanOutcome`/`ScanResult`/`ScanSetResult`
migration above is already written to avoid.

**Acceptance criteria.** Zero remaining inline exit-code/severity
computation outside the one designated encoder per front end — enforced
by a new `check_ai_readiness.py` check (`no-inline-gate-computation`,
WARN) flagging a severity/exit-code literal compared against `Change`
data, or a `.gate`/`.operational`-shaped `RunOutcome` axis decoded by
`max()`/comparison against a raw integer, outside `policy/outcome.py` and
the per-front-end encoders (the widened check is what actually closes the
gap the first draft's narrower, `Change`-only check left open: `gate.py`'s
decode of a persisted report's raw `exit_code` never touches `Change` at
all, so a check scoped to `Change` comparisons alone would never have
flagged it). `fold.py`'s own `max(t.gate.exit_code for t in gated ...)`
is **not** a violation this check should flag — per the Design section's
own correction above, `GateInfo.exit_code` is by that point already the
*output* of `gate.py`'s structured decode (both axes already folded by
`max()` over the shared exit-code scheme), not a raw integer read back
off the persisted report a second time; the check distinguishes the two
by scope (`gate.py` and `policy/outcome.py` are where a `RunOutcome` axis
may be decoded from raw fields at all) rather than by forbidding every
`max()` over a `.exit_code` attribute outright, which would also flag
`fold.py`'s own legitimate aggregation-by-`max()` over many targets'
already-decoded gates. Stated explicitly, matching ADR-063 D6's own
restated encoder list: `gate.py` reads structured `RunOutcome.gate`/
`.operational` fields first and folds both into the one `GateInfo` it
returns (legacy `exit_code` decoding as the named fallback, never the
only path for a fresh report); `fold.py` aggregates those already-folded
`GateInfo` values across targets, unchanged by this phase; and
`fold.py::exit_code()` is the one place that aggregated value converts to
the integer `aggregate`'s own JSON output and process exit code need —
two decode/encode steps (`gate.py` in, `fold.py::exit_code()` out), not
three, because folding the operational axis into `GateInfo` at the read
boundary means there is no longer a third, separate step left over to
name.

**The acceptance check above cannot actually establish the "zero remaining
inline exit-code/severity computation" bar it states, because it never
touches `action/run.sh` at all — a review round correctly found this
phase's Files list never migrates it, even though ADR-063 D6 already names
"the Action's own encoder" as one of exactly four boundary encoders this
decision covers.** `check_ai_readiness.py`'s `no-inline-gate-computation`
check is a Python AST walk; `action/run.sh` is bash, structurally invisible
to it. Reading the real script confirms the gap is not cosmetic:
`_severity_gate_exit()` still reads `severity.exit_code` from the JSON
report directly, and the main `case $ABICHECK_EXIT in ...)` blocks (the
`scan`/`dump`/`deps` paths) reconstruct which message/annotation to emit
from the bare process exit integer, not from the report's own structured
`RunOutcome.gate`/`.operational` fields — exactly the "semantic decision
computed by branching on an integer exit code" shape D6's own "no domain
or workflow code" rule targets, just in a language this phase's tooling
cannot enforce against. ADR-063 D6's framing — "the Action's own encoder...
already owns exactly this conversion... this is a new input type for an
existing function" — overstates what landing Phase 7 alone actually
changes for the Action: nothing in this phase's Files list touches
`action/run.sh`, so its raw-exit-code branching is exactly as unmigrated
after this phase as before it. **Not migrated in this phase, named here as
an explicit, scoped exception rather than a silent gap in this phase's own
accounting** — matching this plan's own established pattern for a real,
separately-justified residual (the binary-less `dump --sources` path in
Phase 1; the two `cli_compare_release.py` branches in Phase 4): a correct
migration means rewriting `action/run.sh`'s several hundred lines of
`case`/annotation logic to read `RunOutcome.gate`/`.operational` from the
JSON report via `jq` instead of branching on `$ABICHECK_EXIT`, re-verified
against every one of its existing GH Action annotation/step-summary
behaviors end to end — a real, large, separately-scoped rewrite of a
shell script this phase's Python-only tooling cannot even test-cover, not
a drive-by addition to this phase's Files list. Until that future phase
lands, `action/run.sh` stays a raw-exit-code consumer for its own
messaging logic, and the "zero remaining inline exit-code/severity
computation" acceptance bar above is scoped to the Python front ends
(`cli.py`, `service.py`, `aggregate`) this phase's own check can actually
see.

---

### Phase 8 — wire storage v2's writer/reader to the domain layer (closes ADR-062 Phase 1, jointly with D8)

**Goal.** ADR-062 Phase 0's primitives stop being inert. A real
`ProjectSnapshot` can be written and read, using `Fact[T]`/`EntityId` from
Phases 0/2 as its domain representation rather than a second identity/
availability scheme invented at the storage layer.

**Design.** This phase is ADR-062 Phase 1 (the v1-v25 import adapter, the
directory-backed `ObjectStore`, folding baseline sets/`BundleFacts` into
sections) **executed with this plan's D8 constraint already in force**:
every DTO is a distinct, versioned class from the domain `SemanticIR`/
`Fact[T]`/`EntityId` objects, with an explicit `to_dto()`/`from_dto()`
(never `asdict`/a 500-line mirror deserializer) and a migration adapter per
DTO version. Doing this jointly with ADR-062 Phase 1 (rather than landing
Phase 1 first, unconstrained, and retrofitting D8 after) avoids writing a
throwaway first version of the writer/reader.

**Files.** `abicheck/storage/package.py` (already has the object model —
`PackageManifest`/`VariantRef`/`ArtifactRef`/`ObjectRef`/`ObjectStore`;
this phase adds the directory-backed implementation and the writer);
`abicheck/storage/dto.py` (new — the `SnapshotDTO`/`ProjectSnapshotDTO`
classes D8 requires); `serialization.py` (the legacy `asdict`-adjacent
`snapshot_from_dict` path is the one this phase's D8 constraint exists to
prevent from growing a `ProjectSnapshot`-shaped sibling).

**Tests.** Per ADR-062's own validation-corpus plan, plus a D8-specific
test: renaming an internal domain field (a synthetic identity key, a
reordered dataclass field) must not change any persisted DTO's bytes —
stated as a property test generating domain-object mutations outside the
DTO's own declared field set and asserting the serialized bytes are
unchanged.

**Acceptance criteria.** Matches ADR-062 Phase 1's own acceptance
criteria (see that ADR and the `storage-format-v2.md` plan) plus: zero
direct `asdict`/mirror-deserializer call sites for any `ProjectSnapshot`-
related type, enforced by the same AI-readiness-style check this plan's
earlier phases already establish as the pattern.

---

### Phase 9 — selector/suppression/reclassification consolidation (D10)

**Goal.** `suppression.py` and `reclassify.py` share one selector-matching
primitive instead of two independent grammars kept in sync by hand, and
`reclassify.py`'s `importlib.import_module` workaround for an import cycle
is removed because the cycle it works around no longer exists.

**Design.** `abicheck/policy/selectors.py` (new, leaf — zero dependency on
`checker_types`, `suppression.py`, `reclassify.py`, or `reporter`, per
ADR-063 D10): the selector grammar (`symbol`/`symbol_pattern`/
`type_pattern`/`member_name`/`namespace`/`entity_namespace`/
`cause_namespace`/`source_location`/`change_kind`/`binding`/`finding_id`/
`expires`) — **`binding` and `finding_id` are listed explicitly here
because a first draft of this phase omitted both, in two different ways.**
`binding` is shared by both `Suppression` and `ReclassifyRule` (ELF
symbol-linkage matching, `Suppression._matches_binding`), and existing
tests cover weak/global binding rules. `finding_id` is narrower —
`Suppression.finding_id` only, not `ReclassifyRule`, matched via
`_matches_finding_id()` against `finding_identity.
report_canonical_finding_id(change)` — but it is a standalone-sufficient
selector (an exact match on the producer-agnostic canonical finding
identity needs no other field to narrow it), so it is still part of the
shared grammar `suppression.py`'s matcher must keep evaluating even though
`reclassify.py` never uses it; the shared module's leaf-matcher contract
covers the union of both classes' fields, not their intersection, and a
consumer that doesn't use a given field simply never sets it.

**The `finding_id` matcher itself must not call `finding_identity.
report_canonical_finding_id` from inside the leaf module — a first draft
of this phase's Files section did exactly that, and it is the same
upward-dependency mistake Phase 2 caught and corrected for
`model/identity.py`, recreated here.** `finding_identity.py` is
comparison-layer logic that imports `checker_types`/model entities to
compute its answer, so `policy/selectors.py` calling into it would depend
upward on `compare/`-level code — precisely the edge this leaf module's
own "zero dependency on `checker_types`/`suppression.py`/`reclassify.py`/
`reporter`" contract exists to forbid, and the existing architecture-gate
check this phase adds (see Files below) would not even catch it, since
that check's denylist names those four modules specifically, not
`finding_identity.py`. The fix follows the same shape Phase 2 already
established for exactly this situation: the leaf matcher never computes
the canonical finding id itself — it only compares a string. **The
caller** (`Suppression.selector_matches()`/`suppression.py`, which already
imports `finding_identity.py` today and is comparison-layer code, not a
leaf) computes `report_canonical_finding_id(change)` once and passes the
resulting string into the shared matcher alongside the `Change`, so
`policy/selectors.py`'s `finding_id` check is a plain string-equality
comparison against an already-computed value, with no import of
`finding_identity.py` anywhere in the leaf module. Dropping
either from the shared grammar would either lose a supported selector
outright or leave its matching logic as a second, un-consolidated
implementation sitting next to the new leaf module — exactly what this
phase exists to remove, not reintroduce. The corrected list above is every
selector field either class currently supports, not a subset — extracted
from `suppression.Suppression`'s existing `selector_matches()` — already
the real, shared logic `reclassify.py` calls today, just reached through
the import-cycle workaround rather than a dependency-free module. Once the
grammar itself lives in a leaf package with no edge back to `checker_types`
or `policy_file`, `reclassify.py` can import it **statically** — the cycle
`policy_file -> reclassify -> suppression -> checker_types -> policy_file`
that `reclassify.py`'s own docstring names as the reason for the
`importlib.import_module` workaround no longer exists, because neither
`reclassify.py` nor `suppression.py` needs to import the *other* anymore —
both import the shared leaf instead. `Suppression`/`ReclassifyRule` keep
their own, distinct *outcomes* (delete the finding vs. reclassify its
verdict) — D10 consolidates the matching grammar, not the two rule types'
different actions, which remain genuinely different decisions and are not
an instance of the "one concept, two representations" problem this plan
otherwise targets.

**Files.** `abicheck/policy/selectors.py` (new — includes the
`finding_id` matcher as a plain string-equality comparison against an
already-computed canonical id, per the Design section above; the leaf
itself never imports `finding_identity.py`); `suppression.py`
(`Suppression.selector_matches()` becomes a thin wrapper calling the
shared matcher — computing `finding_identity.report_canonical_finding_id`
itself, as it already does today via `_matches_finding_id()`, and passing
the resulting string into the shared matcher — or is removed in favor of
direct calls, whichever keeps `Suppression`'s own public method surface,
including its existing `parse_finding_id`-based construction-time
validation, intact for existing callers);
`reclassify.py` (drops the `importlib.import_module` workaround and its
own docstring's cycle justification, replaced by a static import of
`policy/selectors.py`); `scripts/check_architecture.py`'s import-direction
gate (ADR-061) gains a check that `policy/selectors.py` itself imports
nothing from `policy_file.py`/`checker_types.py`/`suppression.py`/
`reclassify.py`/`finding_identity.py` — `finding_identity.py` is added to
the denylist explicitly, not assumed covered by the other four, since it
is exactly the module a first draft of this phase tried to import from
the leaf and the module name alone gives no hint it belongs on this list
unless named — so a future change cannot silently reintroduce the same
cycle through the new leaf module.

**Tests.** Every existing `suppression.py`/`reclassify.py` selector test is
kept and re-targeted at the shared matcher — this phase's acceptance bar
is that no selector-matching *behavior* changes, only where the grammar
lives. A new test asserts `reclassify.py` contains no
`importlib.import_module` call at all (confirmed to fail against the
pre-phase code, which has exactly one, per that module's own docstring),
and `scripts/check_architecture.py`'s widened gate is exercised directly
against a deliberately-reintroduced cyclic import in a throwaway fixture
module to confirm it actually fails closed.

**Acceptance criteria.** `reclassify.py`'s `importlib.import_module`
workaround is deleted, not kept "for safety" alongside the static import —
per the Governing Invariant, a workaround for a cycle that no longer
exists is itself a stale second path. `suppression.py`'s selector grammar
is the shared leaf module's, not a second copy. FP-rate/mutation-score
gates show no regression (this phase moves matching logic, it does not
change what matches).

---

### Phase 10 — delete the superseded representations

**Goal.** Every phase above is only complete once its "before" state is
removed, not left as a second path. This phase is the accounting pass,
not new design.

**Checklist (one row per phase, each a real PR removing code):**

- Phase 0: the *domain-side* `AbiSnapshot.clang_*_facts_reliable` boolean
  attributes are removed once every consumer reads the `Fact[...]` field
  instead. **Not removed, ever, per Phase 0's own corrected design**: the
  wire-level decode of those same keys for a pre-`Fact[...]` persisted
  snapshot — `serialization.py`'s legacy-schema backfill path is a
  permanent reader, the same way every other schema-version branch in
  that module is, for as long as ADR-062's v1-v25 import adapter promises
  to keep importing that version at all. **A second, separate row for the
  same phase**: the retained legacy compatibility-bridge attributes
  themselves — `RecordType.vtable`/`bases`/`virtual_bases`/
  `vptr_offset_bits`, `Param.is_va_list` — are removed from the public
  dataclasses once the widened, repository-wide legacy-attribute-read
  check Phase 0's own Acceptance criteria adds reports zero remaining
  readers outside `__post_init__`/serialization, closing the "kept for
  one release" window
  that section's own design states rather than leaving it open-ended; a
  first draft of this plan said this removal happened in Phase 5, which
  never touches these four fields at all (see Phase 0's own corrected
  text above).
- Phase 1: `cli_dump_helpers.render_dump_dry_run()`'s independent
  resolution logic; the legacy `-p`/`--compile-db` auto-match's standalone
  code path once the fold fully subsumes it (already partly done per
  AGENTS.md's "legacy-match overlap" record — this is closing the
  remainder). **Not in this accounting, and not silently assumed closed
  by it**: `cli_buildsource.dump_source_only()`, the binary-less
  `dump --sources`/`--build-info` path — per ADR-063 D1's own named
  exception, it remains a third, independent dump assembler with no phase
  in this plan scheduled to migrate or retire it; closing it is a real,
  separately-justified future phase, not a residual of Phase 10's cleanup.
- Phase 2: `diff_filtering.py`/`type_reachability.py`'s bespoke string-
  suffix ambiguity trackers.
- Phase 3: `surface.py`'s pre-graph traversal implementation and
  `export_surface.py`'s independent closure walk, once
  `PublicSurfaceQuery.resolve` is the only path either one calls; the
  original, in-place copies of `GraphNode`/`GraphEdge`/`GraphFact`/
  `FactConflict`/`merge_graph_facts` in `buildsource/graph_facts.py` once
  every caller reads them from `model/graph.py` instead of the re-export
  shim. **A second, separate row for the same phase**: `BuildSourcePack.
  source_graph`'s own live-alias mechanism is removed once the five named
  readers (`internal_leak.py`, `buildsource/crosscheck.py`, `buildsource/
  evidence_report.py`, `evidence_depth.py`, `cli_graph.py`) are migrated to
  read `AbiSnapshot.surface_graph` directly — a review round correctly
  found the alias's own Phase 3 text named this migration as "real, scoped,
  follow-up work" with no phase ever actually scheduled to do it, and no
  Phase 10 row removing the alias once it was; left as stated, the two
  attribute paths for one mutable graph persist indefinitely, which is
  exactly the kind of drift risk ("a later assignment to either path alone
  could make old and new consumers diverge again") the alias's own
  one-object guarantee was built to avoid, not accept permanently. This
  row's migration is the same five-reader audit Phase 3's own text already
  deferred, made concrete instead of left open-ended: migrate each reader
  (verified against its own existing tests, per Phase 3's own reasoning for
  why this wasn't attempted in that phase), then delete the in-memory
  alias assignment in the L5 builder — the one piece this row can actually
  remove once every reader stops going through it.

  **"Delete the legacy-document aliasing fallback in `snapshot_from_dict()`"
  is no longer this row's to do — that fallback was itself retracted
  earlier in this same phase (see the correction above), and a further
  review round correctly found the deeper problem this row's first draft
  didn't address: migrating the five readers to read only `AbiSnapshot.
  surface_graph` makes historical L3-L5 evidence silently disappear from
  them, not merely redundant.** `surface_graph` is deliberately never
  populated for a pre-Phase-3 snapshot (the retracted-aliasing fix's whole
  point — aliasing the legacy L3-L5-only graph in there breaks
  `resolve_public_surface()`), so a reader that reads *only*
  `AbiSnapshot.surface_graph` sees nothing for exactly the old snapshots
  this row's migration is supposed to leave working. The migration is
  therefore not a hard cutover to a single field: each of the five readers
  keeps a fallback to `build_source.source_graph` for a snapshot where
  `surface_graph` is `None` but `build_source` is present — `graph =
  snap.surface_graph or (snap.build_source.source_graph if snap.
  build_source else None)`, read `AbiSnapshot.surface_graph` first (the
  canonical location for a fresh snapshot, including one re-saved through
  this phase's own assembly step) and fall back to the legacy nested field
  only when it's absent. This acceptance check changes accordingly: "every
  reader prefers `AbiSnapshot.surface_graph`" is what `git grep` can
  confirm mechanically; "no pre-Phase-3 baseline silently loses L3-L5
  evidence" is confirmed by a direct regression test loading a real
  pre-Phase-3 fixture (`surface_graph` absent, `build_source.source_graph`
  present) through each migrated reader and asserting its output is
  unchanged from before the migration.
- Phase 4: **no row, by design, not by omission** — `AnalysisPlan`/
  `AnalysisPlanner.resolve()` is net-new pre-flight validation, not a
  second implementation of something this plan is consolidating onto one
  representation. The defect it closes is a silent no-op (an unsatisfiable
  request dropping mid-run with no diagnostic), not a duplicate
  representation with an old copy left over to delete once migration
  finishes — there is no prior `PlanningError`-equivalent code path for
  this checklist to retire. Verified instead by Phase 4's own
  already-stated acceptance test: the `--build-target` + pre-captured
  `aquery` gap and the `-H` + unsupported-collect-mode gap each raise
  `PlanningError` rather than silently dropping the request, confirmed on
  the same two scenarios AGENTS.md already documents as today's silent
  failures.
- Phase 5: any hand-maintained capability-matrix doc section the
  generator now produces.
- Phase 6: each backend parser's own copy of anonymous-marker/closure-
  identity/namespace-join logic.

  **This row is deliberately narrower than this phase's "before" state in
  full, and a review round correctly found that gap worth naming rather
  than leaving this row read as covering it — this section's own Goal
  above says "every phase above is only complete once its 'before' state
  is removed," and Phase 6's own text is explicit that the legacy
  `functions`/`types`/... projection is *not* removed by this phase, with
  no later row in this checklist removing it either.** Phase 6's own
  text already states why: retiring the legacy fields is deferred "not to
  an unscheduled 'eventually,' but to whichever future phase first has a
  real `SemanticIR`-only detector population large enough that the legacy
  fields have no remaining reader" — but no phase in this plan is that
  phase, so the condition is real and named, not yet satisfied by
  anything scheduled here. This is the same shape as Phase 4's own "no
  row, by design, not by omission" entry above, stated explicitly for the
  same reason: the legacy-field retirement is not a superseded
  representation with an old copy sitting idle to delete (every existing
  detector still reads it, genuinely, not merely for compatibility), it is
  a migration with a real prerequisite — a detector population large
  enough to retire the fallback — that this plan's own Phase 6 deliberately
  does not attempt, per that phase's own "validating both 'is the IR
  correct' and 'does every detector still behave identically once reading
  from it' in one unreviewable pass" reasoning. Migrating the checker's
  detectors onto `SemanticIR` and retiring the legacy projection is
  therefore real, scheduled, separately-justified future work — named here
  as a residual this checklist does not close, not a silent gap in its own
  accounting.
- Phase 7: `gate.py`'s raw-`exit_code`-decode-as-the-only-path for a
  *fresh* report (replaced outright by the structured-`RunOutcome`-first
  read in the same phase, not left running alongside it for new reports —
  `fold.py` itself needs no corresponding row, since it was never the
  file doing the raw decode). **Not removed, ever,
  per Phase 7's own corrected design**: `junit_report.py`'s own
  `_is_failure` computation — its answer is a per-render function of each
  call's own `SeverityConfig`/`relevant_ids`, not a property a finding
  carries, so there is nothing for it to be superseded by and deleting it
  would remove real, still-needed behavior rather than a second path.
  `gate.py`'s
  legacy-`exit_code`-only decode fallback is a second **permanent**
  exception, not a temporary one to be deleted once every front end has
  migrated — a review round correctly caught a first draft's "removed
  once no such report needs to be read anymore (every front end emits the
  structured fields from this phase onward)" for conflating two different
  conditions: which *front ends* currently write is not the same fact as
  whether any pre-Phase-7 report *still exists to be read*, and a
  persisted report (a CI artifact, an archived aggregate result) can
  outlive every writer that produced it by years. This exception is
  therefore kept exactly the way Phase 0's `serialization.py`
  legacy-schema backfill is kept ("not removed, ever... a permanent
  reader, the same way every other schema-version branch in that module
  is, for as long as ADR-062's v1-v25 import adapter promises to keep
  importing that version at all") — the same reasoning applies here
  verbatim, and matches ADR-063 D6's own stated promise that this
  fallback "keeps every already-published report decodable rather than
  orphaned by this decision," which is a permanent commitment, not one
  scoped to "until every front end migrates." Not kept as a second
  *current* representation — every front end emits the structured fields
  from this phase onward, so the fallback is reached only for a report
  that predates it — but never scheduled for removal on any writer-
  migration timeline.
- Phase 8: any remaining legacy baseline-set/`BundleFacts`-only code path
  once the `ProjectSnapshot` import adapter covers it — per ADR-062's own
  phasing, not accelerated here.
- Phase 9: `reclassify.py`'s `importlib.import_module` workaround and its
  own now-stale cycle-justification docstring.

**Acceptance criteria.** For each row: a `git grep` for the removed
pattern/function name returns nothing outside test fixtures/changelog
history.

**That grep must be scoped per row to the specific symbol the row names,
never a blanket pattern — a first draft of this section left that
implicit, and a review round correctly found the gap: two of these
rows explicitly say the old path is *not* removed, and a loosely-
chosen grep pattern for a sibling row can still match that
intentionally-retained code.** Phase 0's row removes the domain-side
`clang_*_facts_reliable` boolean attributes but explicitly keeps
`serialization.py`'s legacy-schema backfill reading those same wire
keys; Phase 7's row removes `gate.py`'s raw-`exit_code`-decode-as-
the-only-path but explicitly keeps its legacy-`exit_code`-only decode
fallback for a pre-Phase-7 report. A grep for a pattern as generic as
`facts_reliable` or `exit_code` would match both the removed call site
and the retained one, so each row's acceptance check names the exact
removed symbol (e.g. the domain-side attribute access on `AbiSnapshot`
itself, not the wire-key string the backfill still reads; the
raw-decode-as-primary-path function, not the fallback branch that
survives it) — not a substring a retained sibling could also contain.
Each of the two rows with a deliberately-retained path additionally
gets its own **positive** assertion, run alongside the row's negative
grep rather than instead of it: the retained backfill/fallback symbol
(`serialization.py`'s legacy-schema decode path; `gate.py`'s
legacy-`exit_code`-only fallback) is confirmed still present and still
reachable from a pre-migration-schema input, so this checklist cannot
be satisfied by accidentally deleting the compatibility path those
rows were written to keep.

This checklist is re-run, and re-verified, at the end of the
*last* phase landed in a given release cycle — not deferred to "eventually."

## What this plan deliberately does not attempt

- **No new root CLI command or public API surface.** Every new type in
  this plan (`Fact[T]`, `EntityId`, `AnalysisPlan`, `RunOutcome`) is
  internal until a specific phase's own PR explicitly promotes it, per
  ADR-063's own "Explicitly not done by this ADR" section.
- **No *`ProjectSnapshot`/storage-v2* schema version bump beyond what
  ADR-062 already plans — this is narrower than "no schema bump at all,"
  and an earlier draft of this section stated it too broadly, contradicting
  Phase 0's own design a few hundred lines earlier.** Phase 8 follows
  ADR-062's own phase boundaries for the `ProjectSnapshot`/DTO schema
  specifically; it adds no independent migration *there*. This plan does,
  deliberately, bump two schema versions ADR-062 does not own: Phase 0
  bumps `serialization.SCHEMA_VERSION` (the pre-existing `AbiSnapshot`
  schema — the same counter every prior `clang_*_facts_reliable` flag
  addition already bumped, v21/v23/etc.), and Phase 7 adds new report-JSON
  fields alongside the unchanged `exit_code`. Both are real,
  intentional, additive migrations to formats that predate and are
  independent of ADR-062's `ProjectSnapshot` — not a contradiction of this
  bullet, which is scoped to the one schema ADR-062 actually owns.
- **No attempt to resolve every AGENTS.md "Known gaps" entry.** Several
  entries there are accepted, permanent limitations (e.g. the reverted
  linkage-blind-removal attempts, the `type_base_changed` evidence gap
  with no independent signal) that this plan's primitives make *easier to
  close later* (Phase 0's `Fact[T]` on `RecordType.bases`, specifically)
  but does not itself close — closing them needs the evidence this plan
  doesn't add (consumer-side evidence, a captured base-layout fact), which
  is out of scope here and remains a tracked gap.
- **No toolchain-identity-probe implementation.** AGENTS.md names this gap
  independently (castxml/clang invoked without validating the resolved
  compiler matches the real build); Phase 6's `SemanticNormalizer` makes a
  future probe's result easier to thread through uniformly, but does not
  implement the probe itself.

## Effort and risk summary

| Phase | Effort | Primary risk |
|---|---|---|
| 0 | M | Converting the wrong three fields first (pick fields with an *active* fabricated-finding incident, not merely "many `None` checks") |
| 1 | L | castxml unavailability blocking the parity-test half; mitigated by explicit clang-only first landing |
| 2 | L | Identity collision regressions are exactly the bug class this phase targets — the property-test suite is the real acceptance bar, not code review alone |
| 3 | XL | Two distinct risks, not one: migrating `surface.py`/`export_surface.py`'s traversal without changing what counts as public (the kept-test-behavior acceptance bar), *and* relocating `buildsource/graph_facts.py`'s `GraphNode`/`GraphEdge`/`merge_graph_facts` into `model/` without disturbing the existing L5 source-graph suite — a wider blast radius than the phase's first draft assumed, which is exactly why review caught the parallel-graph-hierarchy defect in that draft before it shipped |
| 4 | M | Planner rejecting a request current behavior silently accepted — must ship with a migration note in `CHANGELOG.md`/docs, not only a changelog fragment, since it is a user-visible behavior change (a previously-silent no-op becomes an error) |
| 5 | L (mechanical, field-by-field) | Scope creep — cap each commit to one field |
| 6 | XL | Largest blast radius in this plan (every backend parser); sequence last among the "hard" phases, after 0/2/3 give it primitives to build on |
| 7 | M | Two independent exit-code consumers, not one: JUnit output is consumed by external CI systems (`_is_failure` stays per-finding, parity testing not redesign), and the multi-target aggregate path (`workflows/aggregate/gate.py`/`fold.py`) was missed entirely in this phase's first draft — its report-schema addition needs the legacy-decode fallback proven bit-for-bit equivalent on every existing fixture, not just new ones |
| 8 | XL | Shared with ADR-062's own Phase 1 risk profile; do not duplicate that ADR's own risk analysis here, defer to it |
| 9 | S | Low technical risk (extracting already-shared logic into a leaf module), but skippable-looking — it has no dependency on any other phase, which makes it easy to defer indefinitely rather than land; don't let "independent" read as "optional" |
| 10 | S per row, continuous | The easiest phase to skip under time pressure — explicitly called out as required, not optional, per ADR-063's decision drivers |
