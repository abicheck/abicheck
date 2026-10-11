---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# A using-declaration re-exporting a namespace-scope constant produces two `AbiSnapshot.constants` entries for one declaration on the castxml backend — reported against real oneDAL headers. A per-snapshot, name-shape-based dedup was implemented, reviewed, found unsound in two independent ways, and reverted rather than shipped broken (Codex review, three rounds)

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): OPEN.** The entry's own later verification (known-gaps.md ~4030-4068) found both backends now drop the alias spelling (a false negative), so the alias-identity gap remains open. There is no UsingShadowDecl threading in the code.

`cpu.hpp` opens a plain (non-inline)
`namespace v1 { constexpr ... cpu_feature_map = ...; }` and later does
`using v1::cpu_feature_map;` inside the enclosing `detail` namespace —
an ordinary C++ re-export pattern, not versioned-inline-namespace ABI
tagging (`inline namespace v1 {}`, which is the shape
`diff_namespaces.py`'s `_paired_stable_indices`/
`EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT` machinery already merges
aliases for — that machinery is function/type-scoped, cross-*snapshot*
(old vs. new), and keys on `experimental`/`preview`/`v0` segments
specifically, so it neither targets nor would catch this same-snapshot
pattern even if it applied to constants). castxml's `<Variable>` XML
records the using-declaration as a second full element rather than a
reference to the original, so `dumper_castxml.py`'s
`_iter_public_constants()` (the shared source for `parse_constants()`/
`parse_constant_headers()`) emits both `detail::v1::cpu_feature_map`
and `detail::cpu_feature_map` as independent entries in
`AbiSnapshot.constants` with the same value — distinct qualified-name
*keys*, not a key collision, so this is invisible to `model.py`'s
existing first-wins duplicate-name dedup (`function_map`/
`variable_map`/`type_by_name` all dedup by identity — mangled name or
bare type name — under which a using-shadow naturally collapses onto
its target; `AbiSnapshot.constants` has no mangled-name equivalent).
Reported at real scale, not a one-off: 98 alias groups across 267
constants in the reporting library's own dump. The same castxml
behavior duplicates other declaration kinds too, with two different
outcomes: 1,477 of 6,009 mangled function names were duplicated the
same way, but that half is **not a *double-reporting* bug** —
`model.py`'s mangled-name-keyed `function_map`/`variable_map` already
collapse a using-shadow function or variable onto its target for free
for the ABI/mangled-symbol question (a using-declaration doesn't change
what a symbol mangles to), so this is not a repeat of the constants
over-reporting problem above. **It is a separate, undocumented
false-negative gap on the source/API side, narrower in scope than the
`std::`-specific `STD_REEXPORT_REMOVED` detector below (Codex review,
fresh evidence):** because the diff is keyed on the unchanged mangled
symbol, a release that removes only the `using` re-export of a
library's *own* function or variable — not a `std::` name, which is
the one case `STD_REEXPORT_REMOVED` already covers — while keeping the
real declaration and its export produces no finding at all, even
though a consumer that named the alias-qualified spelling no longer
compiles. This is the identical shape of gap the clang-side note below
documents for constants (an unchanged underlying identity hides a real,
source-visible alias removal), just reached from the opposite side
(castxml capturing the alias correctly here, the *diff* layer being the
one blind to it) — not attempted in this pass, and would need
`detect_std_reexport_removed`'s general shape (matching declared
qualified names, not `std::`-specific) extended to a library's own
namespaces, which is its own scoped detector design, not a drive-by
extension of this entry. A duplicated bare *type* name (`range`
alongside `v1::range`) is a **separate, still-open** gap from the
type-dedup entry already documented above
(opaque-type suppression keyed by bare `RecordType.name` colliding
*across namespaces* on an accidentally-shared bare name) —
`range`/`v1::range` are two distinct qualified spellings of what may be
the same using-re-exported record, so today's exact-bare-name
first-wins dedup does not merge them either, and would need its own,
differently-shaped fix (threaded through `RecordType` identity, not
`AbiSnapshot.constants`), not attempted here.

**Confirmed independently that the duplicate-entry shape is castxml-only
— but the direct-clang L2 backend is not clean either, it just fails in
the opposite direction.** Verified directly against real Clang 18
(`-Xclang -ast-dump=json`) on a minimal repro of the same shape: a
using-declaration lowers to a `UsingDecl` node carrying the qualified
target name (`v1::cpu_feature_map`) but no `init`/value of its own,
immediately followed by an **implicit**, **unnamed** `UsingShadowDecl`
(`"isImplicit": true`, no `name` key at all — confirmed by inspecting
the emitted JSON directly, not inferred). **That `UsingShadowDecl` node
does carry real target identity** — a `target` object with the
underlying `VarDecl`'s own `id`, `kind`, `name`, and `type` (confirmed
by inspecting the real JSON: `{"id": "0x...", "kind": "UsingShadowDecl",
"isImplicit": true, "target": {"id": "0x...", "kind": "VarDecl", "name":
"cpu_feature_map", "type": {"qualType": "const int"}}}`) — so this is
identity Clang exposes and `dumper_clang.py` simply never reads, not
identity Clang lacks (Codex review, fresh evidence; an earlier revision
of this entry claimed the latter, which was wrong). `_categorize()` has
no branch matching either `UsingDecl` or `UsingShadowDecl` — its
`VarDecl` branch requires both `kind == "VarDecl"` and a non-empty
`name`, neither of which either using-related node satisfies — so both
are silently dropped, `target` included, and the re-exported spelling
never enters `AbiSnapshot.constants` at all on that backend.
This is *complementary* missing coverage, not a clean bill of health:
a release that removes only the `using v1::cpu_feature_map;` re-export
while keeping the real `v1::cpu_feature_map` definition breaks every
consumer of the re-exported spelling, but both the old and new
Clang-derived snapshots contain only the one underlying key —
`_diff_constants()` has nothing to compare and emits no
`CONSTANT_REMOVED`, a silent false negative. Castxml's two qualified
keys, over-reporting as they are for the case this entry is about, would
at least detect that removal correctly. So the two backends fail in
opposite directions on the same underlying gap (no alias-identity
evidence for constants) — over-reporting on castxml, under-reporting on
clang. Not verified against a live castxml run (no castxml binary
available in this environment to reproduce the XML shape directly) —
the castxml-side mechanism above is taken from the original report, not
independently re-derived from castxml's own output. **Update (G31 Phase
C, later pass): now independently re-derived, against a real
conda-forge castxml 0.7.0 build, and the "opposite directions" framing
in this paragraph does not hold — see the "Option (b) closed" note at
the end of this entry, below, for the reproduction and its
implications.**

**Attempted fix, reverted (three review rounds):** a
`qualified_name_segments.dedup_versioned_namespace_alias_items()`
helper collapsed one spelling onto the other within one already-
collected `_iter_public_constants()` item list, gated on BOTH (1) the
two qualified names differing by exactly one versioned-inline-namespace
*segment* (`version_strip_segments` — the same, already-trusted
structural check `diff_namespaces.py` already relies on for its own
alias merging) AND (2) the two values being byte-identical. Deliberately
narrower than the plain value-equality merge this same module's own
docstring already rejected after three earlier review rounds for a
*different*, cross-snapshot reason (that heuristic had no name evidence
at all and could merge unrelated same-valued declarations that never
even coexist together) — this attempt was same-snapshot only and added
a real structural name-shape gate. It was not enough. Two more rounds of
review found it unsound from two independent directions, and a third
round (after the second was "fixed") found the second fix was *also*
wrong, at which point it was reverted rather than iterated a fourth
time:

1. **Round 1 (P1):** the first revision kept the shorter, version-
   stripped alias and dropped the version-qualified original —
   reasoned as "the spelling a consumer of the `using` re-export
   actually writes." This actively fabricated findings: a release that
   adds or removes only the re-export (the real declaration unchanged)
   went from one snapshot keeping `detail::v1::x` (no alias present,
   nothing to strip) to the other collapsing down to `detail::x`
   instead — two different surviving keys for one unchanged
   declaration, which `_diff_constants` read as a spurious
   `CONSTANT_REMOVED` + `CONSTANT_ADDED` pair. In other words: this
   revision manufactured exactly the kind of double-reporting it was
   written to eliminate, just shaped as fabricated add/remove instead
   of a duplicated `CONSTANT_CHANGED` — arguably worse, since a
   fabricated `CONSTANT_REMOVED` reads as BREAKING.
2. **Round 2 (P1 again, on the "fix" for round 1):** reversing the
   direction — always keep the version-*qualified* spelling, always
   drop the alias — looked sound (invariant to whether either side
   happens to carry the re-export) and was reviewed as fixing round 1.
   A further round then produced a concrete counterexample proving the
   fixed direction was *also* wrong in general: `namespace detail {
   constexpr int x = 42; namespace v1 { using detail::x; } }` is
   legal C++ where a using-declaration imports a name **into** a
   versioned-looking namespace rather than out of one — here
   `detail::x` (the shorter spelling) is the real declaration and
   `detail::v1::x` (the longer, version-qualified spelling) is the
   alias, the exact reverse of what round 1's fix assumed universally.
   "Always keep the longer/qualified spelling" reproduces round 1's
   failure mode for this reversed input shape instead. **This is the
   structural finding that ended the attempt**: qualified-name *shape*
   alone (segment count, or which segment looks version-tagged) cannot
   determine which of two spellings is the real declaration and which
   is the using-introduced alias — a using-declaration can legally go
   in either direction relative to a versioned-looking namespace
   segment, and `_iter_public_constants()`'s current
   `(qualified_name, value, declaring_header)` output carries no
   signal (declaration order, an `artificial`-style marker, a
   using-shadow/target back-reference) that distinguishes the two
   directions. No fixed, name-shape-based rule can be sound for both.
3. **Round 3 (P2, on the value-equality gate itself, independent of
   direction):** even a correctly-directed rule would still merge two
   genuinely independent declarations that happen to form a
   version-alias-shaped name pair AND happen to share a value at
   extraction time (e.g. `namespace detail { namespace v1 { constexpr
   int x = 42; } constexpr int x = 42; }` — two unrelated `x`s, no
   `using` anywhere) — the same class of coincidence-driven risk this
   module's docstring already flags for the cross-snapshot merge it
   rejects, now shown to reach the narrower same-snapshot case too.

Given round 2 proves no per-snapshot, name-shape-based direction rule
can be sound, and a genuinely correct fix needs real using-shadow/
target identity evidence rather than name shape — continuing to patch
the heuristic's direction a third time was exactly the "one more
counterexample" pattern this file's own linkage-blind-removal and
type-identity entries above already warn against repeating. Reverted in
full (code, tests, changelog fragment) rather than shipped with a
known-unsound direction, per this file's own "known gaps over risky
reactive patches" convention — and per the same "attempted twice,
reverted twice" discipline already established here: a false BREAKING
finding fabricated by an unsound heuristic is a worse failure mode than
the pre-existing double-reporting it would have fixed, since the
original bug is merely noisy while a wrong-direction fabricated
`CONSTANT_REMOVED` blocks a release for nothing.

**What "real identity evidence" actually means differs by backend, and
the two must not be conflated (Codex review, fresh evidence corrected
an earlier draft of this entry that did conflate them).** On
direct-clang, the identity evidence demonstrably EXISTS today and is
simply unused: `UsingShadowDecl.target` (see above) names the exact
underlying `VarDecl` by AST id, so a sound clang-side fix is a real,
scoped option — capture the shadow, resolve `target.id` back to the
already-visited `VarDecl`, and re-register the alias under the
using-declaration's own enclosing-scope name, carrying its target's
identity forward rather than guessing from name shape. That is new
extraction work (`_categorize()` doesn't currently visit
`UsingShadowDecl` at all) and was not attempted in this pass, but it is
a materially different, better-founded starting point than the
name-shape heuristic this entry's earlier rounds tried and reverted. On
castxml, whether an equivalent target back-reference exists in its
`<Variable>` XML is genuinely **unknown** — not confirmed absent, not
confirmed present — since no castxml binary was available in this
environment to inspect real output for this construct; the reported
castxml duplication mechanism throughout this entry is taken from the
original report, not independently re-derived. A correct, full fix
needs one of: (a) the clang-side `target.id` threading described above,
landed and verified against the FP-rate/mutation-score gates before
trusting it; (b) inspecting real castxml XML for this construct to
determine whether it exposes anything equivalent, which this
environment cannot do; or (c) a genuinely different architecture that
resolves the ambiguity at diff time with both sides' full data available
simultaneously (mirroring how `diff_namespaces.py`'s
`_paired_stable_indices` jointly builds paired OLD/NEW indices for
functions/types, gated on real identity) rather than destructively
dropping information from one side's snapshot in isolation — a
materially larger, cross-cutting change on its own, not a follow-up
patch to the same per-snapshot helper.

**Option (b) closed (G31 Phase C, real conda-forge castxml build):** a
real, policy-conformant castxml (0.7.0, `conda-forge`, within the
`>=0.6.11,<0.8.0` range `castxml_policy.py` enforces, bundled Clang
20.1.8) is now available and was used to reproduce this construct
directly through the exact invocation shape `_build_castxml_command`
emits (`--castxml-cc-gnu (g++ -x c)`/`--castxml-cc-gnu g++`, matching
real header-dump usage) — **the originally-hypothesized castxml
duplication mechanism does not reproduce.** For `namespace detail {
namespace v1 { constexpr int cpu_feature_map = 42; } using
v1::cpu_feature_map; }`, real castxml emits exactly **one** `<Variable>`
element (`context="_13"`, i.e. `v1` — never `detail`), and that single
element's id is listed in *both* `detail`'s and `v1`'s `<Namespace
members="...">` attribute — a shared reference, not two elements. There
is no `<Using...>`-shaped XML tag in castxml's schema at all (checked
against castxml's own `--help`), and forcing the value to be ODR-used
(`&cpu_feature_map` from an inline function) does not change this.
Since `dumper_castxml.py`'s `_variable_els`/`_iter_public_constants()`
iterate the flat, once-per-XML-element id map (`_build_id_map`), not
either namespace's `members` list, they see this construct exactly once
too — `_qualified_name()` resolves it to `detail::v1::cpu_feature_map`
only; the alias spelling `detail::cpu_feature_map` never enters
`AbiSnapshot.constants` on this castxml version, at all. The identical
single-element behavior was independently confirmed for the sibling
type-dedup case this entry cross-references (`struct range` in `v1`,
`using v1::range;` in `detail`): one `<Struct>` element, shared between
both namespaces' `members` lists, never a duplicate. **What this means
for the entry above:** the "castxml over-reports (duplicate keys),
clang under-reports (missing alias)" asymmetry this entry documented
was written from an unverified report and does not hold for the
currently-supported castxml version range — for this exact construct,
both backends now demonstrably fail the *same* way (silent false
negative: the alias spelling is absent from the snapshot entirely,
matching the clang-side finding already confirmed above). This does not
retroactively invalidate the original report (98 alias groups across
267 constants, real oneDAL headers) — that scan may have used a
different castxml build, or the real duplication may come from a
different source construct entirely (e.g. two independent, textually
separate definitions sharing a value, rather than a language-level
using-declaration/using-directive) that this pass did not have the
original headers to reproduce against. It does mean: (1) the three
reverted name-shape-heuristic attempts documented above were correctly
reverted regardless of this finding (their unsoundness was proven by
counterexample, independent of whether castxml duplicates), so nothing
here reopens that question; (2) a future fix attempt should design for
the *false-negative* alias-identity gap confirmed symmetric on both
backends (option (a)'s `UsingShadowDecl.target` clang-side threading is
now the best-founded starting point, since it is real, already-present
AST identity, not a heuristic), rather than a castxml-side dedup this
evidence no longer motivates; (3) closing this for real still needs the
original large-scale report's exact source construct identified before
trusting any fix against it — not attempted here, since the oneDAL
headers that produced the original 98/267 count are not available in
this environment either.
