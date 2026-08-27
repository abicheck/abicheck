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
| ADR-055 (typed request/result) | D1 implemented for `compare` only | Phase 1 extends the existing `CompareRequest`/`service_compare_pipeline.py` shape to `dump`/`scan`, it does not invent a new shape |
| ADR-061 (responsibility packages) | Phases 0-1 implemented; Phase 5 (`model` package) begun | Phase 0/2/4/7 of this plan land inside the `model`/`compare`/`policy` packages ADR-061 already created; this plan does not create new top-level packages beyond what ADR-061 names |
| ADR-062 (storage v2) | Phase 0 primitives (`abicheck/storage/`: `FactStatus`/`FactAvailability`, occurrence-preserving identity, canonical encoding, version axes) implemented and **inert** — nothing wired to a producer/reader | Phase 0/5 of this plan is the *generalization* of these primitives into the domain layer; Phase 8 of this plan is the *wiring* ADR-062 Phase 1 still needs, done jointly rather than twice |
| ADR-042 (compatibility/gate separation) | Implemented for JSON/SARIF/`compare-release`; `junit_report.py` and `workflows/aggregate/gate.py`/`fold.py` still compute/decode exit codes inline | Phase 7 of this plan closes both remaining gaps (not only the `junit_report.py` one a first draft of this plan named) — neither is a redesign of ADR-042 itself |
| AGENTS.md "PR C" (dump/scan typed convergence) | `resolve_dump_request`/`execute_dump_request` split landed; real `dump`/`scan` execution still on the legacy path, blocked on two named items (castxml availability for parity testing, `--compile-db-filter` typed surface — now closed) | Phase 1 of this plan is exactly "finish PR C," not a new design |

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

**Scope for this phase (deliberately narrow).** Convert exactly the three
fields AGENTS.md's "Known gaps" names as actively causing fabricated
findings from absent evidence: `RecordType.vtable`/`vptr_offset_bits`
(the `type_vtable_changed` guard), `RecordType.bases` (the accepted-gap
`type_base_changed` entry — converting its *representation* first makes a
future evidence-based guard additive instead of another reinterpretation
of `None`), and `Param.is_va_list` (the reliability-flag entry). Every
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
output again after this phase — with one named exception, below. (Removed
in Phase 5's registry-driven sweep, not here.) `dumper_castxml.py`/
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
one-directional**: an explicitly-supplied `Fact[...]` field always wins
regardless of what the legacy field says (it's the newer, authoritative
channel); the legacy field only ever backfills the `Fact[...]` field when
the latter was never supplied, never the other way around — so a caller
combining both consistently sees the `Fact[...]` value it gave, and a
caller giving only the legacy value sees it faithfully reconstructed
rather than silently demoted to not-collected.

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
value. For `RecordType.bases`/`vtable` (`list`-typed), the mechanism from
before stands unchanged: the field's *actual* dataclass default becomes a
private, identity-checkable empty-list singleton (`_OMITTED_BASES`/
`_OMITTED_VTABLE`, never exported) rather than a literal `[]` —
`__post_init__` checks `self.bases is _OMITTED_BASES` (identity, not
equality — an explicitly passed *different*, even equal-valued, empty
list is a distinct object) to tell omission from explicit confirmed-empty,
backfills `Fact.not_collected()` only for the true-omission case and
`Fact.present(self.bases)` for an explicit value (empty or not), then
normalizes the field to an ordinary `[]` before `__post_init__` returns —
a `list` is mutable and has real per-construction identity, so this works
exactly as stated. For `Param.is_va_list` (`bool`-typed), the field's
declared type widens to `bool | None`, default `None` — `None` is the
omission marker (distinct from both `True` and `False`, not a third
`bool`), `__post_init__` checks `self.is_va_list is None` the same way,
backfills identically, and then normalizes the field to a real `bool`
(`False` if it was `None`) before returning — so after construction every
reader still sees a plain `bool`, never `None`; only the *constructor's*
accepted input type genuinely widens, which is what "an explicit union
and normalization" means concretely here. Both mechanisms end at the
identical post-condition (the legacy field is a plain, fully-populated
value after `__post_init__`, the sentinel/`None` never leaks to a reader)
— they differ only in which type each field's omission marker can
actually be, which is the one extra degree of freedom `list` has that
`bool` doesn't.

Continuing the Files list: `serialization.py`
(`snapshot_to_dict()`'s `Fact[...]`-status-to-string encoding, extending
its existing ElfMetadata-enum-encoding pattern; `snapshot_from_dict()`'s
matching decode; `SCHEMA_VERSION` bump; and the legacy-schema backfill
path, reading the existing reliability flags exactly once on load);
`diff_layout.py`/`diff_types.py`'s vtable/base-list detectors, **and every
other semantic reader of the three converted fields, not only the two
primary detectors** — `diff_param_qualifiers._diff_param_va_list`
(`p_old.is_va_list`/`p_new.is_va_list`), `diff_vtable_layout.
_is_polymorphic` (`rec.vtable`/`rec.virtual_bases`), and `diff_cxx_rules`'s
base-walk helpers (`start.bases`/`rec.bases`) all read the raw field
directly today and were missing from a first draft of this file list —
each retains the exact unavailable-vs-empty ambiguity this phase exists
to close until it is migrated too. The AI-readiness gate this phase adds
checks every module under `diff_*.py`, not only the two named here, so
the full set is enforced mechanically once written, but the file list
itself must name all of them as phase-0 work, not assume the gate alone
will surface the rest as a later, unplanned fixup.

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
field shape. A fifth test pins the type itself: `Param.is_va_list`'s
declared annotation is `bool | None`, and after construction (with or
without the argument) `self.is_va_list` is always a plain `bool` — never
`None` — confirming the widened constructor input normalizes away before
any reader, including `asdict()`-based serialization, can observe it.

**Acceptance criteria.** The three converted fields cannot be read by any
detector without explicit availability handling — enforced by a new
`check_ai_readiness.py` check flagging, inside `diff_*.py`/any detector
module, either a bare attribute read of a `Fact[...]`-typed field *or* a
`.value_or(...)` call on one (both collapse the status space the same
way); `.status`/pattern-match access is the only permitted form there.
`.value_or(...)` itself is not banned repository-wide — it stays legal in
presentation-only modules (`reporter.py`/`html_report.py`/`sarif.py` and
siblings), which is a real, narrower allowlist, not "anywhere outside
`model/fact.py`." Full test suite green; FP-rate/
tier-accuracy gates unchanged (this phase changes representation, not
detector logic).

---

### Phase 1 — finish the `dump`/`scan` typed-API convergence (closes AGENTS.md "PR C")

**Goal.** `dump`, `scan`, and `compare`'s implicit-dump operand execute
through the same `resolve_dump_request`/`execute_dump_request` pair; no
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
`execute_dump_request`, and `scan_engine._build_new_snapshot` through the
already-landed `_resolve_side_snapshot_impl` call (this step is smaller
than it looks — the candidate-resolver convergence already landed per
AGENTS.md's own record; what remains is the `dump` CLI's real execution
path). Fold the legacy `-p`/`--compile-db` auto-match into the L3→L2 fold
as the *sole* source of compile-database-derived context when the fold
applies (already decided and landed per AGENTS.md's "legacy-match
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
leaf declaration itself. **`EntityId` therefore always carries the leaf
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
for most kinds — `()` for a record/enum/typedef/variable/constant, and
the callable-signature discriminator described below for a function
specifically (the one case a bare name is still insufficient, since two
overloads share both scope and name). `OccurrenceId` (an `EntityId` plus a
disambiguator for the already-documented "two declarations, one identity"
case ADR-062 Phase 0 already solves at the storage layer — reused here,
not reinvented). Generalizes ADR-046/048's source-graph identity (already
real, `USR`-based) by making `EntityId` the *single* identity both the
flat snapshot and the source graph reference, rather than two graphs with
their own identity schemes that happen to usually agree.

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
`gate_classification`-stamping-layer questions elsewhere, rather than
asserting a fourth, unverified answer here.

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
the new resolver. Exactly one `EntityKind`/`ObservationKind` definition
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
  `idiom.py`/`pattern_verdicts.py`/`diff_surface_metrics.py` already play)
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
  machinery). `build_surface_graph`/`compute_surface_metrics` both gain an
  **optional** `public_entity_ids: frozenset[EntityId] | None = None`
  parameter — optional so a test or script calling either function
  directly, with no policy-layer caller in the chain at all, does not
  break — and `checker.py`'s `_apply_pattern_verdicts_step`/
  `_apply_surface_metrics` both gain the identical parameter, threaded in
  from `compare()`'s own `PublicSurfaceQuery.resolve()` call (the same
  resolved set `compute_public_surface()` already uses) and passed straight
  through to `apply_pattern_verdicts()`/`diff_surface_metrics()`, which
  pass it straight through to `build_surface_graph()`/
  `compute_surface_metrics()` in turn. When `None` (the only case possible
  outside `compare()`'s own pipeline), `SurfaceGraph.public_roots()` falls
  back to its pre-existing `Visibility.PUBLIC` filter — an explicit,
  narrow, named residual for a caller this phase cannot reach, not a
  second silent implementation competing with the real one; every call
  reachable from `checker.compare()` itself — which is what `idioms.py`/
  `pattern_verdicts.py`/`diff_surface_metrics.py` actually run under in
  production — receives the real, resolved ids. **`PublicSurfaceQuery.
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
  dropped, not mapped-and-failed — then maps each remaining `EntityId`
  back to the snapshot's own
  symbol/mangled-name spelling (via the `Function`/`Variable` the identity
  already resolves to — `EntityId`'s function variant carries the mangled
  name directly in `extra` when one exists, the same primitive Phase 2
  already built), preserving its existing `frozenset[str]` return type
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
  replacing. So: a `declaration`/`type` node's id is `EntityId`-derived
  (rendered to the same string form `model/graph.py`'s `GraphNode.id`
  already expects); every other kind keeps the existing URI-scheme id.
  This phase is ordered after Phase 2 because the declaration/type half
  needs it — the same dependency Phase 6 (`SemanticIR`) has on Phase 2 —
  not because every node kind does.
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
  SourceGraphSummary | None = field(default=None, kw_only=True)` (`None`
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
  None`, and `PublicSurfaceQuery.resolve()` must not treat that the same as
  "nothing is public" -- a first draft of this phase left the backfill
  unaddressed, which would have broken (or silently emptied) every existing
  baseline's public/export-surface queries the moment `compute_public_surface`
  stopped falling back to its own flat-snapshot traversal. The fix is a
  lazy backfill inside `PublicSurfaceQuery.resolve()` itself, not a
  migration step on load: when `snapshot.surface_graph is None`,
  `resolve()` builds one on the fly, in memory, using the exact same
  `compare/surface_graph.py` builder a fresh extraction already uses --
  it needs only the flat `AbiSnapshot` fields that builder already reads
  (header origin, declarations, export-table data), none of which are
  themselves new or missing on an old snapshot; only the *pre-built graph*
  is missing, not the evidence it would be built from. The backfilled
  graph is not written back onto the loaded `snapshot` object (no silent,
  surprising mutation of a caller's loaded snapshot) -- a query against
  the same old snapshot pays the build cost each time, which is the
  correct tradeoff for what should be a rare path once fresh snapshots
  carry the field. `compute_public_surface(snapshot)` and any other direct
  caller read `snapshot.surface_graph` when present, or get the
  lazily-built equivalent transparently through `PublicSurfaceQuery.
  resolve()`, with no fabricated pack to thread through either way.
  ADR-057/053's consumers still
  read the L3-L5-gated graph only when it exists, and migrating them onto
  querying through `PublicSurfaceQuery`'s shared instance directly is
  still explicitly **not** part of this phase (each stays its own later,
  separately-justified phase, per this plan's "don't attempt a change with
  no real caller" discipline) — but what changes this time is structural,
  not aspirational: there is one graph object per snapshot side after this
  phase, not two that merely happen to agree on node spelling.

  **Two items this relocation still owes a real design, named here rather
  than asserted as solved — this plan is stopping at naming them, not
  attempting a fourth consecutive re-design of the same field in the same
  review cycle.** First: `SourceGraphSummary` itself — the container class
  with `add_node`/`add_edge`/`resolve_entities`, as opposed to the
  `GraphNode`/`GraphEdge` primitives Phase 3's own `model/graph.py`
  relocation already covers — still lives in `buildsource/source_graph.py`
  today. Typing `AbiSnapshot.surface_graph: SourceGraphSummary | None` from
  `model/snapshot.py` needs that container available to `model/` too, the
  same direction question already answered once for the primitives
  themselves; whether `SourceGraphSummary` relocates alongside them, or
  `AbiSnapshot` instead holds a narrower model-layer protocol/interface
  `SourceGraphSummary` satisfies, is a real design choice this document
  does not resolve. Second: moving the L5 graph's attachment point off
  `BuildSourcePack.source_graph` has real existing readers —
  `internal_leak.py`, `buildsource/crosscheck.py`, `buildsource/
  evidence_report.py`, `evidence_depth.py` among them — each would observe
  no graph at all the moment the L5 builder stops writing to the old
  location, silently regressing impact/cross-check/assurance behavior
  that works today. Both are real, scoped, answerable questions — not
  reasons to abandon the `surface_graph` design — but they are Phase 3's
  own implementation PR's work to resolve with the actual code in front
  of it (a real migration of every named reader, verified against its own
  existing tests), not this planning document's.
- **The relevance query** — `abicheck/policy/public_surface.py` (new):
  `PublicSurfaceQuery.resolve(graph, explicit_roots) -> frozenset[EntityId]`,
  a traversal from explicit public roots through `includes`/`declares`
  edges (closing the reachable-header surface) and `references`/
  `instantiates` edges (closing the reachable-type surface), with
  `exports` edges answering the `contract=exports` domain from the *same*
  graph instead of `export_surface.py`'s separate walk. `policy -> compare`
  is an already-allowed import edge under ADR-061, so `policy/` can consume
  the `compare/`-built graph directly; this is where `compute_public_
  surface()`'s actual decision logic — which declarations count as part of
  the public contract — lives after migration.

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
graph.py`'s primitive, not a new one); `abicheck/surface_graph.py`
(`build_surface_graph()`/`compute_surface_metrics()` each gain an optional
`public_entity_ids: frozenset[EntityId] | None = None` parameter, and
`SurfaceGraph.public_roots()` maps a given set of ids back to their
mangled/symbol-name spelling, preserving its existing `frozenset[str]`
return type exactly — `surface_graph.py` itself still never imports
`policy/public_surface.py`, per the note above); `pattern_verdicts.py`
(`apply_pattern_verdicts()` gains the identical optional parameter,
threaded straight through to `build_surface_graph()`); `diff_surface_
metrics.py` (`diff_surface_metrics()` gains the identical optional
parameter, threaded straight through to `compute_surface_metrics()`);
`checker.py` (`_apply_pattern_verdicts_step`/`_apply_surface_metrics` both
gain the identical parameter, populated from the same `PublicSurfaceQuery.
resolve()` result `compare()` already computes for `compute_public_surface()`,
and thread it into the two functions above — this is what makes every
call reachable from `compare()`'s own pipeline receive the real, resolved
ids rather than the `None`-triggered legacy fallback); `abicheck/policy/public_surface.py`
(new — `PublicSurfaceQuery`, migrated from `surface.py`'s existing
traversal logic); `surface.py` (`compute_public_surface()` becomes a thin
wrapper calling `PublicSurfaceQuery.resolve`); `dumper_scoping.py`/
`export_surface.py`/`type_reachability.py` (each becomes a graph *builder*
contributing nodes/edges in `compare/`, or a relevance *query* in
`policy/`, not an independent reachability algorithm); `abicheck/model/
snapshot.py` (new `AbiSnapshot.surface_graph: SourceGraphSummary | None`
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
`SourceGraphSummary`, silently breaking `PublicSurfaceQuery.resolve()` on
every persisted (as opposed to freshly-dumped) snapshot. A populated-graph
save/load round-trip test (construct a snapshot with a real, non-empty
`surface_graph`, write it, read it back, assert the reloaded object is a
`SourceGraphSummary` with the same nodes/edges) is required by this phase,
not deferred to Phase 10's cleanup. A third regression covers the legacy
backfill: load an old-schema snapshot (`surface_graph=None`, constructed
the way a pre-this-phase snapshot would be) alongside a fresh one with a
real `surface_graph`, run `compute_public_surface`/`compare()` against
both, and assert the old snapshot's query result matches what the lazy,
in-memory backfill inside `PublicSurfaceQuery.resolve()` produces rather
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
patching `build_surface_graph`/`compute_surface_metrics` to assert they
were called with a non-`None` `public_entity_ids` when reached through
`compare()`, not only by comparing output, so a future regression that
silently stops threading the parameter through `checker.py` fails this
test even if it happens not to change the specific fixture's output.
Separately, asserting
`SurfaceGraph.public_roots()` — still returning `frozenset[str]`, still
consumable by `re.Pattern.match()` with no caller change — agrees with
`PublicSurfaceQuery.resolve()`'s answer rather than the old
`Visibility.PUBLIC`-only one, confirmed to fail against the
pre-migration `SurfaceGraph` for this exact input; and a second case
asserting `surface_graph.py` imports nothing from `policy/`, enforced by
the same architecture-gate mechanism this plan already uses elsewhere for
a leaf module's import direction. A sixth regression pins the kind-filter
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
facts, resolved toolchain/compile context, resolved policy, surface
contract) built by a new `AnalysisPlanner.resolve(request) ->
AnalysisPlan | PlanningError`. `PlanningError` carries one entry per failed
requirement (`requested`, `why_unsupported`), modeled directly on the
`--build-target` + pre-captured `aquery` gap and the `-H` + unsupported-
collect-mode gap AGENTS.md already documents as *silent* failures — this
phase's acceptance test is exactly "these two scenarios now raise
`PlanningError` instead of silently dropping the request."

**ADR-063 D1's own scope is wider than `compare`/`dump`'s resolution
path alone, and a first draft of this phase didn't reach the rest of
it — D1 names the Action, `cli_project.py`, and bundle/release fan-out
explicitly as adapters that must stop orchestrating independently.**
Checking each against the real code narrows what's actually missing,
rather than treating all three as equally unconverged: `cli_compare_
release.py`'s `_run_compare_pair` already routes through `service.
run_compare` — ADR-037 D1's existing single Tier-2 chokepoint, confirmed
by that function's own docstring — so the release fan-out is not a second
*implementation* of compare orchestration; `bundle.py`'s `compare_bundle()`
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

**Files.** `abicheck/workflows/plan.py` (new); `service_compare_pipeline.
resolve_compare_request`/`service_dump_pipeline.resolve_dump_request`
(construct `AnalysisPlan` as part of resolution, reusing — not
duplicating — ADR-049's `compatibility_evaluation_resolver.resolve_field`
for the policy half). **`cli_compare_release.py`'s `_run_compare_pair`
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
so a regression is attributable to one field's conversion. **"Remaining
model fields" means every availability-ambiguous field on every
fact-bearing model dataclass, not only the files named `model/*_facts.py`
— a first draft of this phase scoped itself to that filename pattern and
missed real candidates living elsewhere**: `RecordType.is_final` (`model/
entities.py`), `Function.contract_attributes`/`Variable.alignment_bits`
(`model/declarations.py`) are exactly the same "unavailable vs. genuinely
absent" ambiguity Phase 0 exists to close, and none of them live in a
`*_facts.py`-named file. The completeness check below must therefore scan
every dataclass field under `model/` eligible for this conversion
(bool/list/int-or-None fields documented as backend-dependent), not only
fields already typed `Fact[T]` — a check that starts from "fields already
converted" is structurally blind to a raw field nobody has touched yet,
which is exactly how this phase could report complete while the ambiguity
it exists to close still exists.

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
*eligible-but-unconverted* shape (raw `bool`/`list`/`int | None` with no
matching `Fact[...]` sibling, documented as backend-dependent) and fails
if any exists once this phase claims completion — not only auditing the
fields the registry already knows about, so a field the conversion missed
entirely (not just one the registry forgot to register) fails this check
too. A direct `serialization.py` round-trip test per converted field (the
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
the only shape `SemanticIR` offers. Each `CanonicalEntity` carries its
resolved `ScopePath`, canonical type spelling, template-argument list,
and CV-qualification, independent of which backend produced it, plus the
`Fact[...]`-wrapped per-field availability Phase 0 established, so a
canonicalized entity can state "this backend didn't produce this
particular fact" rather than only "here is the value." This model file,
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
step untouched and still producing the pre-normalization shape. Both are
updated the same way `dumper.py`/`dumper_manifest.py` are: call
`semantic_normalizer.normalize()` on the raw facts and project through the
existing `AbiSnapshot` field shapes, attaching `semantic_ir` identically
— `model/snapshot.py` (the new `AbiSnapshot.semantic_ir` field);
`pdb_model.py` (`model_types_from_dwarf_metadata` narrowed to raw-fact
production the same way `pdb_metadata.py` itself is, per the Design
section's own parser-narrowing rule, since it's a second conversion layer
for the identical backend, not a different one);
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
per Phase 3's finding) and encoded by its own `SemanticIR.to_dict()` as a
**list of entries**, not a dict — `{"occurrences": [{"occurrence":
occurrence_id.to_dict(), "entity": entity.to_dict()} for occurrence_id,
entity in self.occurrences.items()]}` — with `SemanticIR.from_dict()`
rebuilding the `dict[OccurrenceId, CanonicalEntity]` from that list on
load. `OccurrenceId`/`EntityId`'s own `to_dict()`/`from_dict()` reuse the
identical structured-segment encoding Phase 2's `storage/entity_ids.py`
v2 DTO already defines for `ScopePath`, rather than a third, independently
invented structural encoding for the same type.)
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
instead, rather than two independent channels that could disagree. This is
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
required for **each of the four assembly call sites** — `dumper.py`,
`dumper_manifest.py`, `service.py`'s BTF/CTF dispatch, and `service.py`'s
PDB path via `pdb_model.model_types_from_dwarf_metadata` — not only the
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
test specifically constructs).

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

---

### Phase 7 — `RunOutcome` and the last inline exit-code computation

**Goal.** `junit_report.py` stops computing an exit code inline; every
front end encodes `RunOutcome`'s independent axes exactly once, at the
boundary.

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
external consumer already relies on. Instead, each `Change` gains a new,
separate field — `gate_classification` (or similarly named; always
resolved, never `None`, independent of whether contract evaluation ran).

**Where it is stamped is not a new computation — it is meant to be
`_is_failure`'s own existing logic, moved to run once instead of on every
read — but exactly which layer does the stamping is an open question this
plan states honestly rather than asserting a third time.** `junit_report.
_is_failure` already calls the real, single canonical per-finding verdict
— `DiffResult._effective_verdict_for_change(change)` — which already
honours `PolicyFile` overrides, the A4 per-finding `effective_verdict`
(ADR-027), and frozen-namespace escalation guards; an earlier draft of
this phase claimed `checker.compare()`'s own result-assembly step as the
stamping point, which review correctly found incomplete twice over:
`compare()` does not receive the renderer's `SeverityConfig` (a
CLI/front-end-level concern today, resolved after `compare()` returns,
not an input to it) or the `relevant_ids` scoping `--used-by`/
`--required-symbol` produce, and `_effective_verdict_for_change` itself
does not replicate `_is_failure`'s own preceding `is_evaluated`/
scoped-id gate — so a value stamped purely inside `compare()` would not
actually reproduce `_is_failure`'s current behavior for a demoted
severity preset, a contract-excluded finding, or a scoped run. Closing
this needs either moving the stamping call to wherever severity/scoping
are already resolved (a later policy/workflow layer `compare()` itself
does not reach, which may mean this field cannot be a plain `Change`
attribute stamped once at all, but something resolved per-render-context
instead) or threading those two missing inputs into `compare()`'s own
signature — a real design decision with real tradeoffs on both sides,
left to Phase 7's own implementation PR rather than guessed at a third
time here. `compatibility_decision` keeps its existing meaning and
existing callers completely unchanged either way. `RunOutcome` is what
the report's own top-level `compatibility_decision` summary still renders
from, unchanged. "Stops computing inline" means `_is_failure` stops
*calling* the resolution logic itself once that logic has a real, settled
stamping point — not that the resolution logic is new, or that it starts
asking the aggregate report outcome a per-test-case question it cannot
answer.

**`junit_report.py` is not the only remaining inline exit-code consumer —
a first draft of this phase missed the multi-target aggregate path
entirely.** `abicheck/workflows/aggregate/gate.py`'s `TargetGate.
from_report` decodes a persisted report's raw `exit_code` integer back
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
external consumer. `TargetGate.from_report` reads the structured fields
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
exactly this reason — but a fold that reads only `.gate` for a *fresh*
report, once the legacy-decode fallback stops running for it, silently
drops that axis: a fresh `scan` report hitting budget overflow would
carry `gate = NONE` (no compatibility category fired) and an
`operational` status recording the overflow, and a fold that never
inspects `operational` at all would aggregate it as passing — the opposite
of what the pre-existing raw-code path already gets right. The fix:
`fold.py`'s aggregation reads *both* axes and folds each into the
aggregate's blocking decision independently — `PolicyGateDecision`'s own
ordering for the compatibility contribution, plus a defined blocking set
over `OperationalStatus` (budget overflow, not-comparable/evidence-
contract-error, and any sibling operational failure this phase's
`RunOutcome` construction populates) for the operational contribution —
combined with the same orthogonal `max()`-style fold ADR-049 Phase 7's
contract-coverage axis already uses elsewhere in this codebase for
exactly this "two independent failure axes, neither allowed to mask the
other" shape, not a single combined ordering that could let one axis's
`NONE` silently override the other's real failure. `fold.py`'s own
aggregation then operates on both folded values (never `max()` over raw
integers) for every report new enough to carry them, and its
`exit_code()` method becomes the *one* place — the aggregate's own
external encoder, mirroring the CLI's `_exit_with_severity_or_verdict` —
that turns the aggregated `RunOutcome` back into the integer `aggregate`'s
own JSON output and process exit code still need.

**Files.** `abicheck/policy/outcome.py` (new — `RunOutcome` and the new,
exit-code-free `PolicyGateDecision` ordered type, per the Design section
above; `severity.GateDecision` itself is untouched, since it remains
exactly what the boundary encoders convert *to*); `checker_types.py` (new
`Change.gate_classification` field, kw_only, appended last — `Change` is
public API); the actual stamping call site — `checker.py` if the
`SeverityConfig`/`relevant_ids` gap above is closed by widening
`compare()`'s own inputs, or the CLI/workflow layer that already holds
both today if not; this phase's own implementation PR resolves which,
per the open-question note above, rather than this plan naming one
prematurely a third time; `junit_report.py` (the first remaining inline
exit-code
computation ADR-042 already named as unfinished); `html_report.py`'s CI Gate card (already `RunOutcome`-shaped
per ADR-042 — confirm it reads the new object directly rather than a
precursor shape, closing ADR-036 Increment 3 as a side effect if it
hasn't landed separately by then); `abicheck/workflows/aggregate/gate.py`
(`TargetGate.from_report` reads structured `RunOutcome` fields first,
legacy `exit_code` decoding becomes the named fallback path, not the only
path); `abicheck/workflows/aggregate/fold.py` (`exit_code()`'s aggregation
reads `RunOutcome.gate` per target, `max()`-over-raw-integers deleted);
the report-writing side of `reporter.py`/`aggregate.py` (emit the new
structured fields alongside the unchanged `exit_code`, and bump
`REPORT_SCHEMA_VERSION`/`AGGREGATE_SCHEMA_VERSION` — an additive schema
change needs its version bumped the same way every prior
`report_schema_version`-gated field addition already did, per that
constant's own changelog comments; a first draft of this phase named the
field addition without the version bump, schema-file edit, or
regeneration that addition requires); `docs/reference/schemas/v1/
compare_report.schema.json`/`aggregate_report.schema.json` (the new
fields, regenerated via `scripts/publish_schemas.py`, not hand-edited);
`scan_engine.
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

**Tests.** A parity test asserting `junit_report.py`'s exit-relevant
output (failure count, failure classification) is unchanged for the
existing `tests/test_junit_report.py` fixtures before and after the
rewrite — this is a refactor, not a behavior change, and needs to prove
that explicitly given JUnit output is consumed by external CI systems. A
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
the new structured `RunOutcome` fields, asserted to still aggregate as
blocking through the new operational-axis fold — confirmed to fail
against a fold that reads only `RunOutcome.gate` and ignores
`.operational` entirely, which is the exact regression this finding
caught. A third
parity test covers the writers this phase adds — all three of them, not
only `scan_engine.ScanOutcome`: a freshly-generated `scan` report
(`ScanOutcome.to_dict()`), a freshly-run typed-API `ScanResult.to_dict()`,
and a freshly-run `--artifact-set` `ScanSetResult.to_dict()` each carry
the new structured fields, and `GateInfo.from_scan_report()` reading any
of the three fresh reports takes the structured-field path, not the
legacy-decode fallback — confirmed by
asserting which path actually ran (not only that the output matches),
since a test that only checks the output could pass with the writer
changed and the reader still silently falling back. A fourth test
validates every regenerated fixture report against the regenerated
`docs/reference/schemas/v1/compare_report.schema.json`/
`aggregate_report.schema.json` (the same validation
`scripts/verify.py`'s `fair-metadata` step already runs for generated
files), so the new fields are provably reflected in the published schema
mirror, not only in the Python writer.

**Acceptance criteria.** Zero remaining inline exit-code/severity
computation outside the one designated encoder per front end — enforced
by a new `check_ai_readiness.py` check (`no-inline-gate-computation`,
WARN) flagging a severity/exit-code literal compared against `Change`
data, or a `max()`/comparison over a `.exit_code` attribute, outside
`policy/outcome.py` and the per-front-end encoders (the widened check is
what actually closes the gap the first draft's narrower, `Change`-only
check left open: `fold.py`'s `max()` over `TargetGate.exit_code` never
touches `Change` at all, so a check scoped to `Change` comparisons alone
would never have flagged it). Stated explicitly, matching ADR-063 D6's
own restated encoder list: `gate.py` reads structured `RunOutcome` fields
first (legacy `exit_code` decoding as the named fallback, never the only
path for a fresh report); `fold.py`'s aggregation orders and `max()`s
`PolicyGateDecision` values **and independently folds `OperationalStatus`'s
own blocking set, per the finding above** — never raw integers, and never
the compatibility axis alone; and `fold.py::exit_code()`
is the one place that aggregated value converts to the integer `aggregate`'s
own JSON output and process exit code need — three steps, not one
function call, because `aggregate` has a multi-target pipeline the CLI's
single-report `_exit_with_severity_or_verdict` doesn't.

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
  to keep importing that version at all.
- Phase 1: `cli_dump_helpers.render_dump_dry_run()`'s independent
  resolution logic; the legacy `-p`/`--compile-db` auto-match's standalone
  code path once the fold fully subsumes it (already partly done per
  AGENTS.md's "legacy-match overlap" record — this is closing the
  remainder).
- Phase 2: `diff_filtering.py`/`type_reachability.py`'s bespoke string-
  suffix ambiguity trackers.
- Phase 3: `surface.py`'s pre-graph traversal implementation and
  `export_surface.py`'s independent closure walk, once
  `PublicSurfaceQuery.resolve` is the only path either one calls; the
  original, in-place copies of `GraphNode`/`GraphEdge`/`GraphFact`/
  `FactConflict`/`merge_graph_facts` in `buildsource/graph_facts.py` once
  every caller reads them from `model/graph.py` instead of the re-export
  shim.
- Phase 5: any hand-maintained capability-matrix doc section the
  generator now produces.
- Phase 6: each backend parser's own copy of anonymous-marker/closure-
  identity/namespace-join logic.
- Phase 7: `junit_report.py`'s pre-rewrite inline computation (deleted,
  not `# deprecated` and kept); `fold.py`'s `max()`-over-raw-`exit_code`
  aggregation (replaced outright by `RunOutcome.gate`-ordering aggregation
  in the same phase, not left running alongside it). `gate.py`'s
  legacy-`exit_code`-only decode fallback is the one exception to "delete
  in the same PR": it stays, the same way Phase 0's reliability-flag
  backfill stays, only so a report predating this phase's schema addition
  still reads correctly — removed once no such report needs to be read
  anymore (every front end emits the structured fields from this phase
  onward), not before, and not kept as a second *current* representation.
- Phase 8: any remaining legacy baseline-set/`BundleFacts`-only code path
  once the `ProjectSnapshot` import adapter covers it — per ADR-062's own
  phasing, not accelerated here.
- Phase 9: `reclassify.py`'s `importlib.import_module` workaround and its
  own now-stale cycle-justification docstring.

**Acceptance criteria.** For each row: a `git grep` for the removed
pattern/function name returns nothing outside test fixtures/changelog
history. This checklist is re-run, and re-verified, at the end of the
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
