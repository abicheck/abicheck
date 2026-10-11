---
doc_type: contributor
audience:
  - contributor
level: advanced
lifecycle: active
generated: false
---

# Type reachability (direct vs. transitive stdlib references) — computed and wired into `diff_types.py`'s RecordType-based detectors; enum/typedef paths remain unwired

Index: [Known gaps](../known-gaps.md)

**Status (re-verified 2026-10-09 at `456989f`): PARTIAL.** type_reachability.py is wired into the RecordType detectors (diff_types_surface.py and others), but the diff_types.py:1113-1118 enum paths still call _is_non_abi_surface_type(e.name) directly, as the entry says.

`abicheck/type_reachability.py`
(`directly_referenced_stdlib_types()`) computes, from a snapshot alone,
which `std::`/`__gnu_cxx::`/etc. record types are directly referenced by
a non-stdlib function's signature or a non-stdlib type's own field — as
opposed to only reachable via deep template-instantiation internals
(`std::string::_Alloc_hider`, `std::_Rb_tree_node_base`) that
`is_non_abi_surface_type`'s existing whole-name-prefix filter already
correctly excludes as toolchain-artifact churn either way. A Codex review
round found and fixed a real correctness gap in the computational claim:
candidate identification originally matched only `RecordType.name`, but
castxml/direct-clang populate the bare leaf there and the
namespace-qualified spelling separately in `qualified_name` (`model.py`,
`dumper_clang.py`) — so `name` alone never carries a `std::` prefix for
those two backends and the helper silently found nothing on any real
castxml/clang-produced snapshot. Fixed by identifying candidates via
`qualified_name or name`. That fix alone was still insufficient, confirmed
by dumping a real compiled `std::vector<int>` parameter end to end:
`Function.return_type`/`Param.type` spell the outer type **bare**
(`"vector<int, std::allocator<int> >"`) even when the matching
`RecordType`'s identity is fully qualified
(`"std::vector<int, std::allocator<int> >"`), across *all three* backends
(DWARF bakes the qualified form straight into `name` with no separate
field; castxml/clang keep `name` bare and `qualified_name` separate) — so
a pure full-identity substring match still couldn't connect the two.
Fixed by also generating a namespace-prefix-stripped spelling per
candidate and matching against either form. **Since resolved** (a later
pass, user-requested): a signature spelled with a typedef alias
(`std::string`, `std::wstring`, ...) names the alias, not the real
underlying class (`std::basic_string<char, ...>`) that owns the
`RecordType` entry — no current model field maps one back to the other
directly, but `snapshot.typedefs` does carry the alias → target mapping.
Verified empirically against a real DWARF-dumped `std::string`
parameter: `snapshot.typedefs["std::string"]` resolves to the bare
`"basic_string<char, std::char_traits<char>, std::allocator<char> >"`,
while the owning `RecordType.name` is the fully-qualified
`"std::__cxx11::basic_string<char, std::char_traits<char>,
std::allocator<char> >"` — libstdc++ wraps its own post-C++11 dual-ABI
types in an inline namespace (`__cxx11::`) the exact same way libc++
wraps its whole standard library (`__1::`/`__ndk1::`, already handled),
so that inline-namespace-stripping list gained a third entry. The
typedef *key* itself needed the identical bare-vs-qualified treatment
already applied to `RecordType` identities (the DWARF backend spells the
signature with the bare form `"string"`, never the qualified typedef key
`"std::string"`), so `_typedef_spelling_targets()` builds a
`spelling -> target` index covering both the literal key and its
namespace-stripped bare form (dropped instead of recorded when
ambiguous, same false-negative-over-false-positive principle as the
`RecordType` spelling index), and `_scan()` now follows a matched
typedef alias to its target the same way `surface.py`'s own
reachability closure does. What this does *not* cover: a stdlib alias
the producing backend never emitted into `snapshot.typedefs` at all (no
empirical case of this found across the three backends so far, but
nothing guarantees one couldn't exist) — that residual case degrades
silently back to "not directly referenced," the same conservative
false-negative default this whole module already uses throughout.

**Two more real gaps found and fixed in the same pass** (Codex review,
fresh evidence): (1) the non-stdlib bare-alias fallback derived a
record's unqualified spelling via `identity.rsplit("::", 1)`, which
splits inside a *template argument's own* qualified name rather than at
the outer namespace boundary — for `"api::Wrapper<dep::Tag>"`, the
lexically last `"::"` belongs to the template argument `dep::Tag`, not
the outer namespace path, so the old code derived the corrupted bare
form `"Tag>"` instead of `"Wrapper<dep::Tag>"`, and a real dumper
backend's bare signature spelling for that wrapper then never matched
anything. Fixed with a new `_bare_type_name()` that tracks `<`/`>`
nesting depth and only treats a `"::"` at depth zero as a namespace
separator. (2) stdlib and non-stdlib spellings were matched via one
*combined* compiled pattern in a single non-overlapping `finditer()`
pass — when a non-stdlib record's own identity embeds a stdlib type's
spelling verbatim (e.g. a template instantiation `"Wrapper<std::string>"`
registered as its own record identity), and a public signature names
that wrapper's full identity exactly, the combined pattern's
longest-first alternation matches the whole wrapper span first,
consuming it — since regex matches never overlap, the nested
`"std::string"` substring inside that same span was never independently
found, even though it is directly present in the public signature text.
Fixed by splitting `_spelling_index()` into two independent indices
(stdlib vs. non-stdlib/record) with two independently compiled patterns
scanned separately over each declaration, so one pattern's match can
never mask the other's.

**A third real gap found in the same pass** (Codex review, fresh
evidence): both the stdlib-stripping collision guard (in
`_spelling_index`) and the typedef-key stripping collision guard (in
`_typedef_spelling_targets`) checked a stripped spelling only against
*full* non-stdlib record identities, not against the bare
(namespace-unqualified) alias a real backend actually spells that record
with. A non-stdlib record like `api::vector<int>` is spelled bare as
`"vector<int>"` — the same bare spelling `std::vector<int>` reduces to
after namespace-stripping — so a signature naming the unrelated user
type by its bare spelling incorrectly marked the real `std::vector<int>`
as directly referenced too; the identical gap existed one level up for
`api::string`/`"std::string"`'s typedef key. Fixed with a new
`_non_stdlib_signature_spellings()` helper (full identity plus bare
alias — deliberately keeping an ambiguous bare alias that
`_spelling_index`'s own `record_index` drops, since it's still a real
spelling *some* non-stdlib record can be named by) shared by both
collision guards.

**A fourth finding pointed one level deeper, into shared infrastructure
this module calls rather than into `type_reachability.py` itself**
(Codex review, fresh evidence): `diff_cxx_rules.owner_class_of()` — the
helper this module's owner-class seeding reuses, also used by
`diff_symbols.py`'s owner-based move detection, `diff_cxx_rules.py`'s
own member-move heuristics, and `surface.py`'s reachability closure —
mis-parses a public conversion operator's owner when the operator's own
target type is namespace-qualified. Confirmed against a real compiled
and demangled symbol: `struct Foo { operator ns::Bar() const; };`
demangles to `"Foo::operator ns::Bar() const"`, and abicheck's own
`Function.name` (after its existing signature-stripping step) is exactly
`"Foo::operator ns::Bar"`. The old naive `rsplit("::", 1)` split at the
*lexically last* `"::"` — which belongs to the operator's own qualified
target (`ns::Bar`), not the owner/member boundary — producing the
corrupted owner `"Foo::operator ns"` instead of `"Foo"`, so a public
conversion operator to a qualified type would never seed its owner
class, potentially hiding a genuine layout break in one of the owner's
fields. Fixed in `owner_class_of()` itself (not duplicated locally) by
locating the literal `"::operator "` marker — present only for a
conversion-to-named-type operator, never for a symbol operator like
`operator+`/`operator[]`, which has no target type to separate from the
keyword with a space — and splitting there when present, falling back to
the previous behavior otherwise. Fixing the shared helper directly
(rather than working around it only in `type_reachability.py`) also
corrects the same latent mis-parse for its other three callers, since
none of them could have been relying on the old behavior's output for
this input shape without already being wrong.

**A fifth finding, on the same owner-seeding feature, investigated and
deliberately not implemented this pass:** a public method whose dumper
backend recorded only a bare member name (CastXML's convention — "the
bare `bar` rather than `C::bar`", per `owner_class_of()`'s own
docstring) on a class-template specialization falls through to
`owner_class_of()`'s mangled-name fallback
(`itanium_scope_components`), which — confirmed empirically
(`itanium_scope_components("_ZN3FooIiE3barEv")` returns
`["FooIiE", "bar"]`) — deliberately keeps the **raw, undemangled**
Itanium template-argument encoding (`"FooIiE"`) rather than the spelled
form (`"Foo<int>"`) a real `RecordType` identity actually uses; that
design choice is itself intentional and documented in
`itanium_scope_components`'s own docstring ("the raw template-argument
encoding is kept so distinct specializations stay distinct"), since its
other callers use it for grouping/distinguishing specializations, not
for matching against demangled model spellings. `type_reachability.py`'s
owner-seeding then feeds this raw string into `_scan()`, which correctly
finds no match (a silent false negative — the same
false-negative-over-false-positive default this whole module already
uses throughout, not a new failure mode). A real fix has two paths, both
rejected as out of scope for a drive-by extension here: (1) making
`owner_class_of()` itself resolve raw template encodings to spelled
form would mean invoking the real demangler (`demangle.py`'s
`demangle()`, which shells out to `c++filt`/`cxxfilt` on a cache miss)
from a hot path every one of its four callers shares, directly
contradicting `itanium_scope_components`'s own stated design rationale
("avoids any dependency on an external demangler ... so this works
identically on Linux, macOS, and Windows and never shells out"); (2) a
narrower, local-only translation in `type_reachability.py` (demangle
just `fn.mangled` when `owner_class_of()` took the mangled-fallback
path, then re-derive the owner from the *demangled* qualified name)
would need a genuinely new depth-aware "class::member" boundary splitter
for demangled text — not a reuse of `_bare_type_name` (which strips a
*leading* namespace qualifier, the opposite half of this problem) — and
would have to correctly compose with the already-fragile
`"::operator "` marker special-case from the fourth finding above (a
demangled conversion operator on a qualified template specialization
could combine both edge cases at once), which is exactly the kind of
compounding-edge-case complexity this file's own docstring already
flags as needing "its own scoped follow-up," not a reactive patch.

**Two more real gaps found and fixed in the same pass** (Codex review,
fresh evidence): (1) A real backend does not always spell a nested type
as either the fully-qualified identity or the fully-bare leaf —
confirmed empirically via `clang -ast-dump` on `namespace api { struct
Outer { struct Inner {}; }; Outer::Inner g(); }`: direct-clang prints
the return type as exactly `"Outer::Inner"`, dropping the enclosing
namespace (`api::`) while keeping the class-nesting qualifier
(`Outer::`). Neither the full-identity match nor the single
fully-bare-leaf match (`_bare_type_name`) covered this partial
qualification. Generalized `_bare_type_name` into
`_namespace_suffix_spellings()`, returning every suffix obtainable by
dropping some prefix of the scope chain at each depth-zero `"::"`
boundary, and updated all three call sites to register every suffix
(same ambiguity-drop collision guard extended to each). (2)
CastXML/direct-clang record a function or namespace-scope variable's
own display name bare (e.g. `"touch"`, never
`"__gnu_cxx::Node::touch"` or `"std::touch"`), so the existing
`name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)` guard cannot catch a
retained, seemingly-public declaration that is actually part of the
standard library itself — verified with two real Itanium
mangled-symbol repros (a namespace-scope stdlib variable and a stdlib
free function) that both incorrectly marked `std::string` as directly
referenced before the fix. Fixed by also checking the declaration's
recovered qualified name (`diff_cxx_rules.itanium_qualified_name`, from
`mangled`) against the stdlib prefixes for both functions and
variables — which subsumes the narrower owner-only check from the
fourth finding above (a stdlib-prefixed owner always makes the full
qualified name stdlib-prefixed too, but not vice versa: a stdlib
namespace's own direct free function/variable is a single mangled
scope component, so `owner_class_of` returns a bare `"std"` with no
trailing `"::"`, never matching the `"std::"` prefix string), so the
now-redundant owner-only guard was removed.

**A sixth finding found a different shape of gap again: an owner-seeding
correctness bug, not a missing-spelling one.** `owner_class_of()`
derives its result by chopping the trailing `"::"`-component off *any*
already-qualified declaration name or mangled-symbol scope chain, with
no way to tell — from the string alone — whether what remains is really
an enclosing *class* or just an enclosing *namespace* (Codex review,
fresh evidence, confirmed with a minimal repro): a public namespace
function `api::run()` makes `owner_class_of` return the bare namespace
fragment `"api"`, which the general suffix-matching mechanism
(`_namespace_suffix_spellings`, added for the first finding above) could
then coincidentally match against an unrelated internal record's own
bare-suffix spelling (e.g. `other::api`), wrongly walking that record's
fields and unfiltering its layout churn. Fixed by seeding an owner only
on an *exact* match against a non-stdlib record's full identity —
bypassing `_spelling_index`'s `record_index`/suffix mechanism entirely
for this specific seed, rather than routing it through `_scan()`. This
is safe rather than a regression risk: unlike a genuine signature type
spelling (which a backend can legitimately partially-qualify, per the
first finding), `owner_class_of`'s result is always either the complete,
exact scope chain of a real class (both its already-qualified-name path
and its mangled-decomposition fallback reconstruct the *full* chain,
never a partially-elided one — DWARF always bakes the complete
namespace/class path into a qualified name, and Itanium mangling always
encodes the complete nested-name unambiguously) or, when the function
isn't actually a method, namespace noise — so restricting to exact
matching loses no real case while closing the false-positive collision.
While verifying this fix through the full `compare()` pipeline (not just
the unit level), the same class of bug was found to independently exist
in `surface.py`'s `compute_public_surface()` — its own, separate
`owner_class_of`-based seeding (`_seed_public_roots`) feeds the raw
owner through `_type_identifiers()` into `seed_types`, and
`_walk_type_closure()`'s `record_by_name` lookup is *itself* keyed by
bare-tail aliases (an intentional, correct mechanism for genuine type
references — "a short alias reached inside its own namespace resolves
to the namespaced record"), so the identical `"api"` vs. `other::api`
collision reproduces there too, confirmed with the same minimal repro
(`compute_public_surface` marks `"api"` — and therefore `other::api` —
public). **Deliberately not fixed in this pass**: `surface.py` is a
different, foundational module (the public-surface-scoping gate every
other detector in the codebase depends on) that this PR never otherwise
touches, and unlike the narrow `type_reachability.py` seeding path, its
`record_by_name` bare-tail lookup is a *shared* mechanism relied on by
every other seed type too — restricting it for the owner case
specifically needs its own careful, independently-verified design (which
seed paths may legitimately need the ambiguous-tail lookup and which
must not), not a same-PR drive-by extension of an unrelated finding.

**Two more ambiguity-tracking gaps found in the same collision guards**
(Codex review, fresh evidence, both confirmed with minimal repros): (1)
when two non-stdlib records had identities `"Inner"` and `"api::Inner"`,
`_spelling_index`'s derived-suffix collection only counted contributors
to the *derived* suffix `"Inner"` (from `"api::Inner"`) — the unrelated
global `"Inner"` identity never contributes to that same tracking
structure (it's already a full identity, not a derived suffix), so the
ambiguity count saw only one contributor and merged `"api::Inner"`
straight into the pre-existing full-identity entry for the global
`"Inner"`. Fixed by also treating a derived suffix that collides with a
*different* record's own full identity as ambiguous. (2)
`_typedef_spelling_targets` gave an *exact* pre-existing typedef key
automatic priority over a derived suffix from a different key, rather
than tracking both through the same ambiguity-counting structure: when
`snapshot.typedefs` held both a global `"Alias" -> "std::…"` and a
qualified `"api::Alias" -> "Foo"`, a declaration inside `api` can
legitimately spell the latter as bare `"Alias"` too — silently
preferring the pre-existing exact key could resolve it to the wrong
one. Fixed by unifying exact keys and derived suffixes into one
target-set-per-spelling structure, resolving a spelling only when every
contributing source agrees on exactly one target.

**A follow-up review round on the same fix found the removal above was
necessary but not sufficient.** Refusing to *merge* `"api::Inner"`'s
candidates into the pre-existing `record_index["Inner"]` entry still
left that entry pointing at the unrelated global `"Inner"` record
(Codex review, fresh evidence, confirmed with a minimal repro):
direct-clang's own "drop the enclosing namespace" convention (the same
mechanism `_namespace_suffix_spellings` models for the `Outer::Inner`
finding above) means a signature declared *inside* namespace `api` can
spell `api::Inner` bare as `"Inner"` too — not just a partially-qualified
form. A public `api::f()` returning (bare-spelled) `api::Inner` would
then have its `std::` field misattributed to the *unrelated* global
`Inner`'s own field instead of correctly failing to resolve. Fixed by
removing the colliding spelling from `record_index` entirely
(`record_index.pop(bare, None)`) rather than merely refusing to add the
other record's candidates to it — since the bare spelling is genuinely
ambiguous between both records, leaving it resolved to either one
(including the "already there by default" one) is the wrong outcome,
not just an incomplete fix.

**A separate, deeper finding on typedef keys, investigated and
deliberately not implemented this pass:** direct-clang's own
`parse_typedefs()` (`dumper_clang.py`) stores a typedef's bare
`node["name"]` as the `snapshot.typedefs` key — never the scope-joined
qualified form `_qualified()` uses for every other decl kind — so a
namespaced alias loses its namespace at the point the snapshot is
produced, not merely at the point this module reads it. Confirmed
empirically via a real `clang -ast-dump` on `namespace api { struct Foo
{}; using Alias = Foo; } api::Alias make();`: the `TypeAliasDecl`'s own
name is bare `Alias`, while the function's return type is printed fully
qualified `"api::Alias"` (a typedef reference is always spelled
qualified by clang's printer, unlike a plain class reference) — meaning
`snapshot.typedefs` ends up with `{"Alias": "Foo"}` while the real
signature spells `"api::Alias"`, the exact inverse of the
qualified-key/bare-signature shape `_typedef_spelling_targets` was built
to handle. Since suffix-stripping only ever produces a *shorter*
candidate from a key, it can never reconstruct a *longer*, more-qualified
spelling from an already-bare key — there is no string-level fix
possible in this module for this direction, only two heavier ones, both
out of scope for a drive-by extension here: (1) fixing
`dumper_clang.py`'s `parse_typedefs()` to store the qualified key
instead — a genuine, separate producer-side bug, but one whose blast
radius reaches every other consumer of `snapshot.typedefs` (typedef
diffing, `surface.py`'s own typedef-following in `_walk_type_closure`),
each needing its own re-verification against the FP-rate/mutation-score
gates before trusting a changed key shape; (2) a local reverse-namespace
guesser in this module (re-attaching every namespace prefix seen among
the snapshot's own record identities to a bare typedef key and hoping
one matches) — pure speculation with no way to verify which, if any,
namespace a given bare key actually belongs to, and a real risk of
fabricating new false-positive matches rather than closing a
false-negative gap. Left as a silent false negative — the same
conservative default this module already uses throughout.

**A seventh finding pointed at a platform-specific mangled-name quirk,
silently disabling the mangled-scope-recovery guard on every Mach-O
snapshot.** Confirmed via `dumper_clang.py`'s own `_visibility()`
docstring: clang's `mangledName` carries an extra platform leading
underscore on macOS (`"__ZN3lib3addEii"`, not the plain Itanium
`"_ZN3lib3addEii"`), and empirically: `itanium_scope_components(
"__ZSt5touchv")` returned `None` before this fix, since
`_itanium_strip_prefix()` only recognized the bare `"_Z"` prefix
(Codex review, fresh evidence). Since every declaration's stdlib-scope
check in this module (and `owner_class_of()`'s mangled fallback) relies
on this recovery, a bare-named stdlib declaration on macOS bypassed the
guard *entirely* — not just in the one edge case a synthetic unit test
would reach, but for every symbol on that platform. Fixed in the shared
`diff_cxx_rules.py` parser (benefiting all four of its callers, not
just this module) by stripping the extra leading underscore before the
Itanium-prefix check, mirroring `dumper_clang.py`'s own
`_symbol_candidates()` de-prefixing approach for the identical quirk.

**An eighth finding pointed at a different mangling scheme entirely, not
a variant of the same Itanium quirk.** A `clang-cl` (or any
`--target=*-windows-msvc`) direct-clang snapshot records a method's bare
AST name — the same unqualified-leaf convention CastXML uses — while
`mangledName` is mangled in the proprietary Microsoft C++ ABI scheme, not
Itanium (Codex review, fresh evidence). `owner_class_of()`'s mangled-name
fallback only ever recognized the Itanium `_Z`/`__Z` prefix, so this
owner seed stayed `None` on every MSVC-mangled bare-named method,
regardless of the Mach-O fix above (a different, unrelated prefix
convention, not fixed by it). Confirmed empirically by compiling real
headers with `clang --target=x86_64-pc-windows-msvc -fms-compatibility
-Xclang -ast-dump=json`: `Foo::run()` mangles to `?run@Foo@@QEAAXXZ`
(scope components written *innermost first*, `@`-separated, terminated
by the first `@@` — the reverse order and terminator convention Itanium
uses, confirmed against nested-namespace, single-letter-class-name, and
global-free-function cases too). Fixed with a new, genuinely separate
`msvc_scope_components()`/`msvc_qualified_name()` pair in
`diff_cxx_rules.py` (not a branch inside the Itanium parser, since the
two schemes share no structure beyond both being length/separator-based),
tried as a second fallback in `owner_class_of()` after Itanium — the two
prefixes (`_Z`/`__Z` vs. `?`) are mutually exclusive, so trying both in
sequence is unambiguous and free on the common Itanium path. Deliberately
conservative, mirroring `itanium_scope_components`'s own "model the
simple cases, return `None` for the rest" contract, confirmed unmodelled
against the same real compiler output: special member functions and
operators (`??0` ctor, `??1`/`??_D` dtor, `??4` `operator=`, ...) mangle
with a *second* `?` immediately after the first, so the leaf/scope split
does not apply and is rejected outright; template classes/functions
(`?$Name@Args@`) embed the template-argument encoding inside the same
`@`-delimited region as the scope chain, and an argument token is
indistinguishable from a scope token by simple splitting, so any
component starting with `?` (the template marker `?$` or the anonymous-
namespace marker `?A`) is rejected; a bare-digit component is a
name-backreference into MSVC's per-symbol substitution table, not a
literal identifier (no real C++ identifier is all-digits, so this is an
unambiguous, lossless signal to bail — verified this does *not*
misfire on a genuine single-letter class name like `struct A`, which
mangles as a component that is a letter, never a bare digit). Also wired
the same new fallback into `type_reachability.py`'s two direct
`itanium_qualified_name()` call sites (the free-function/variable
stdlib-namespace guards, not just the owner-seeding path the review
comment named) — same root cause, same one-line fix, verified against a
`std::`-namespaced MSVC-mangled free function that would otherwise have
bypassed the guard identically to the Mach-O case above.

**A ninth finding pointed at an asymmetry in the typedef-spelling
ambiguity guard, not a mangling gap.** `_typedef_spelling_targets()`
registers every *derived* candidate spelling (a stdlib-stripped or
namespace-suffix form of a typedef key) only after checking it against
`_non_stdlib_signature_spellings()` — but the typedef's own *exact* key
was registered unconditionally, with no equivalent guard (Codex review,
fresh evidence). The already-documented direct-clang typedef-scope-loss
gap above (`parse_typedefs()` storing only the bare `node["name"]`) means
an exact key like `"Alias"` can itself collide with an unrelated
non-stdlib record's own bare signature spelling — e.g. a global `struct
Alias {};` sharing the same name as a namespaced `namespace api { using
Alias = std::string; }` whose `api::` the producer already dropped.
Confirmed empirically: `directly_referenced_stdlib_types()` incorrectly
returned `{"std::string"}` for a public function taking the unrelated
`Alias` record by value, purely because of the same-named, unrelated
typedef. Fixed by applying the identical `non_stdlib_spellings` guard to
the exact-key registration, matching how a colliding derived candidate is
already skipped — the spelling belongs to the real record, so the
typedef contributes nothing for it, rather than competing through the
ambiguity-resolution machinery.

**A tenth finding closed the conversion-operator half of the owner-
seeding gap the earlier `"::operator "`-marker fix only partly covered.**
That earlier fix handled a *display-name* conversion operator whose own
qualification embeds `"::"` (e.g. DWARF's `"Foo::operator ns::Bar"`), but
a direct-clang snapshot stores a conversion operator's AST name bare —
`"operator Bar"`, no owning-class prefix at all, confirmed via a real
`clang -ast-dump` — so `owner_class_of()`'s display-name branch never
applies (there is no `"::"` to find), and it falls through to the
mangled-name fallback (Codex review, fresh evidence). That fallback had
no coverage for conversion operators either:
`itanium_scope_components()`'s underlying component parser deliberately
excludes the Itanium `cv` (conversion-to-*T*) code from
`_ITANIUM_OPERATORS` — correctly, for that set's own purpose of grouping
operator *overloads* by a fixed 2-char code, since every conversion
operator carries a different target type and is never an overload of
another one — but treating `cv` as entirely unparseable meant hitting it
aborted the *whole* scope-recovery attempt, discarding the class name
already parsed before it. Confirmed empirically: `_ZNK3FoocvN2ns3BarEEv`
(`Foo::operator ns::Bar() const`) made `itanium_scope_components()`
return `None` outright, and `owner_class_of()` therefore returned `None`
instead of `"Foo"`. Fixed by recognizing `cv` as a distinct, opaque leaf
component (`"{op:cv}"`) in `_parse_operator_component()` — separately
from `_ITANIUM_OPERATORS`, since the overload-grouping semantics
correctly stay excluded — and forcing `_step_next_component()`'s `done`
flag to `True` immediately upon seeing it, regardless of nesting: the
conversion operator's own leaf is always the last component, and the
target-type encoding immediately following `cv` (e.g. `N2ns3BarE` for
`ns::Bar`) is a full, arbitrary Itanium `<type>` production — a much
larger grammar than this structural parser attempts elsewhere — but
recovering the *scope prefix* never needs that type parsed at all, only
a signal to stop before attempting it. Regression tests added: direct
parser-level cases in `TestItaniumScopeParser`/`TestMsvcScopeParser`'s
sibling `diff_cxx_rules` test file, plus an end-to-end
`directly_referenced_stdlib_types` test confirming a `Foo`-owning
conversion operator's embedded `std::string` field is no longer
filtered.

**An eleventh finding pointed at a masking mechanism the earlier
cross-index split didn't fully close.** Splitting `_spelling_index()`
into independent `stdlib_index`/`record_index` patterns (an earlier
fix) solved masking *between* the two indices — a non-stdlib wrapper's
identity embedding a stdlib type's spelling verbatim. It did not solve
the identical masking *within* either index (Codex review, fresh
evidence): `.finditer()` only returns non-overlapping matches, so when
one candidate's registered spelling is itself a substring of another
candidate's spelling *in the same index* (e.g. `"std::string"` inside
`"std::vector<std::string>"`, both stdlib; or a non-stdlib `"Inner"`
inside `"Wrapper<Inner>"`), the longest-first alternation matches the
outer candidate first, consumes the whole span, and the search
continues from the end of that match — so the inner one, though
directly present in the text, is never independently reported.
Confirmed empirically for both the stdlib and non-stdlib cases (and, on
further investigation while fixing this, the identical mechanism in
`typedef_pattern`'s typedef-key matching too — a third, independently
confirmed instance of the same root cause). Fixed with a single new
helper, `_finditer_allow_nested()`, used at all three call sites: for
every match found, it recurses into `text[m.start()+1 : m.end()]` — a
strictly narrower window, so recursion terminates — to catch a shorter
candidate embedded anywhere inside it, at any nesting depth, not just
one level. Kept as one shared helper rather than three inline copies
since all three loops have the exact same masking mechanism. Verified
against the existing large-corpus performance regression guard
(`test_many_unreferenced_stdlib_candidates_scan_efficiently`) to confirm
this doesn't reintroduce the quadratic candidate-by-candidate cost the
single-pattern rewrite was originally built to eliminate — the extra
recursive search only runs when a match is actually found (rare in the
common case), bounded by nesting depth, not candidate count.

**A twelfth finding closed a narrower gap in the conversion-operator
owner fix itself (tenth finding, above).** The `"::operator "`-marker
fix only detects a conversion operator when an *owner* precedes the
marker; a bare-recorded conversion operator (no owning-class prefix at
all, per the tenth finding) can still carry a *qualified target* with
its own `"::"` — e.g. `"operator ns::Bar"`, no `"Foo::"` prefix — and
for that shape neither the marker (there's no owner text before
`"operator"`) nor the previous unqualified-bare check applied, so the
naive `rsplit` fallback still ran and returned junk like `"operator
ns"` (CodeRabbit review). Confirmed empirically: constructing exactly
this input shape reproduced the bad `"operator ns"` result before the
fix. Fixed by checking for the `"operator "` prefix the same way the
already-fixed unqualified case is detected, falling through to
mangled-name recovery for both shapes uniformly.

**A thirteenth finding pointed at a robustness gap in the eleventh
finding's own fix, not a new correctness bug.** `_finditer_allow_nested()`
(the nested-match helper from the eleventh finding) recursed one Python
call per nesting level to search each match's own span for a further
embedded candidate (Codex review, fresh evidence). For a genuinely deep
chain of registered spellings each nested one inside the next —
plausible for template-metaprogramming-heavy C++ under a compiler's
configured template-instantiation depth (GCC/Clang both default well
into the hundreds, and it is routinely raised higher for real
metaprogramming-heavy code) — that per-level recursion follows the C++
template depth 1:1. Confirmed empirically: 1,000 successively nested
registered candidate spellings raised `RecursionError` under Python's
default 1,000-frame recursion limit, aborting the whole comparison
rather than degrading gracefully. Fixed by converting the recursive
search into an explicit stack — each entry is still a strictly narrower
window than the match that produced it, so the search still always
terminates, just without consuming Python's call stack to do it, so no
amount of nesting depth can overflow it.

**A fourteenth finding was a genuine regression the tenth finding's own
fix introduced, caught before merge.** Recognizing the Itanium `cv` code
as an opaque leaf component (tenth finding) used a single fixed
placeholder label (`"{op:cv}"`) regardless of the conversion's actual
target type. `diff_types._overload_group_key()` chains
`itanium_qualified_name()` — which now runs this label onto the scope
prefix — to decide whether two declarations are genuine overloads of one
another for `_diff_overload_additions()`'s KDE-policy check (Codex
review, fresh evidence). A fixed placeholder made *every* conversion
operator on a class produce the same qualified name regardless of
target — e.g. both `operator int()` and `operator double()` on the same
class reduced to `"Foo::{op:cv}"` — collapsing two conversion operators
that are never overloads of each other (each is a distinct, unambiguous
conversion function; there is no shared `&Foo::operator T` that becomes
ambiguous) into one group. Confirmed empirically:
`_diff_overload_additions()` fired a false `OVERLOAD_ADDED` for adding
`operator double()` alongside an existing `operator int()` before this
fix. Fixed by embedding the raw, un-decoded remainder of the mangled
string after `cv` into the label itself, instead of a fixed placeholder
— Itanium mangling is deterministic, so the same target always
reproduces the identical remainder (keeping genuine re-declarations in
the same group) while distinct targets always mangle differently
(keeping them in distinct groups), without this parser needing to
actually decode the arbitrary Itanium `<type>` grammar the remainder
encodes. Owner recovery (`owner_class_of()`, which only ever consumes
`comps[:-1]`, dropping the leaf entirely) is unaffected either way.

**A fifteenth finding pointed at a data-model assumption this module's
own new code introduced without verifying, contradicted by an existing
sibling.** `directly_referenced_stdlib_types()` built `non_stdlib_records`
as a plain `dict[str, RecordType]` keyed by identity — when
`snapshot.types` contains multiple entries sharing the same identity
(e.g. a complete definition alongside an ODR-duplicate or incomplete
declaration), a later entry silently overwrote an earlier one, so a
public signature reaching that identity walked only the survivor (Codex
review, fresh evidence). `surface.py`'s own `record_by_name` index —
the established reference this module has mirrored throughout every
finding above — already anticipates exactly this by keying on a *list*
of records per identity (`dict[str, list[RecordType]]`) and walking
every one (`for rec_node in rec_nodes: ...`), not a single winner; this
module's new dict introduced a real regression relative to that already-
correct sibling pattern, not a hypothetical edge case. Confirmed
empirically both orderings (the complete definition first, and the
complete definition last): whichever entry didn't survive the dict
overwrite, if it carried a `std::` field the survivor lacked, that field
was silently missed. Fixed by changing `non_stdlib_records` to
`dict[str, list[RecordType]]` (appending instead of overwriting) and
walking every record for a reached identity in the worklist loop,
checking each one's own `origin` independently (a private-origin
duplicate still excludes only itself, not a public-origin sibling
sharing the same identity) — exactly mirroring `surface.py`'s own
per-record walk.

**Wiring (this pass):** `diff_types.py`'s single choke-point gate,
`_is_abi_surface_type()`, now accepts a `directly_referenced` set (built
once per detector via `_directly_referenced(old, new)`) and un-filters a
std:: record that set names, instead of blanket-filtering every std::
record regardless of direct use. Because every RecordType-based
struct/union/field/kind/reserved detector in that file already shares this
one gate function, wiring it there once covers all of them uniformly —
not 9 independent, individually-drifting call sites. While wiring this in,
the FP-rate corpus's own new cases (`stdlib-direct-reference` category)
surfaced a second, *pre-existing* correctness gap in the gate's std::
check itself (independent of `directly_referenced`): it filtered using
`_is_non_abi_surface_type(t.name, ...)`, i.e. bare `t.name` only, the exact
same bare-vs-qualified split as the `type_reachability.py` fix above — so
a real castxml/clang-produced std:: record (bare `name`, qualified
`qualified_name`) was **never actually filtered as std:: at all**,
independent of whether anything referenced it. Fixed in the same gate by
keying the std:: prefix check on `qualified_name or name` (the anonymous-
type-marker half of the check still uses bare `name`, unaffected).
`diff_platform.py`/`diff_symbols.py`/`diff_vtable_layout.py`/
`diff_stdlib_impl.py`/`diff_layout.py`/`diff_filtering.py`/
`diff_type_spellings.py`, plus `diff_types.py`'s own enum/typedef paths
(which call `is_non_abi_surface_type`/`is_abi_surface_type_name` directly
on enum/typedef names, not through `_is_abi_surface_type`), remain
unwired and carry the identical bare-name gap — each needs its own
individually-verified follow-up (FP-rate/mutation-score gates), not a
drive-by extension of this pass's RecordType-scoped fix.
