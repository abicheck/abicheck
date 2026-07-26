### Added

- **`release_recommendation` gains `possible_impact`** (schema 2.22): a new,
  always-non-null string field carrying the bump abicheck would recommend if
  its evidence were sufficient to confirm one. `version_bump` stays `null`
  when `state` is `"unavailable"` (the honesty fix from earlier in this PR),
  but that previously left the still-plausible bump readable only from
  free-text `rationale` prose — `possible_impact` gives automation a
  machine-readable answer to "what would this be if confirmed?" without
  reintroducing the blind-trust risk `version_bump: null` exists to prevent
  (an automated release action must still gate on `version_bump`/`state`,
  never on `possible_impact` alone).
- **`aggregate --format json`/text output gains a `profile_matrix`** (schema
  1.1): a project running the same target under multiple toolchain profiles
  (`profiles:` in `.abicheck.yml`, `check_id` shaped
  `target@profile#baseline_channel@requested_depth` per ADR-047 §7) previously
  saw unrelated-looking `target_id` rows in `targets[]` with nothing tying
  them back to the same logical target. `TargetReport` gains `profile_id`/
  `base_target` (parsed from a `check_id`-shaped `target_id`, via the new
  `parse_check_id()`), and `AggregateResult.profile_matrix` groups every
  target sharing a `base_target` into one entry naming which profiles are
  `affected_profiles` (verdict neither `NO_CHANGE` nor `COMPATIBLE`) versus
  clean — e.g. "libfoo is broken under linux-gcc14/linux-clang20 but fine
  under windows-msvc" instead of three opaque rows. Empty (and every target's
  `profile_id` absent) for a `target_id` with no profile encoding — the
  common single-profile case — so this is purely additive. Grouping combines
  *all* of a profile's checks worst-verdict-wins rather than keying by
  `profile_id` alone, since a run plan can carry more than one check for the
  same target+profile at different baseline channels/requested depths — a
  naive last-write-wins would let a later, cleaner check silently overwrite
  an earlier, breaking one for that profile (Codex review). Each
  `profile_matrix` entry also gains `incomplete_profiles`: when one of a
  profile's checks is unavailable while another did report, the unavailable
  one is surfaced here instead of being silently dropped — a completed,
  compatible `headers`-depth check plus a missing required `build`-depth
  check for the same profile is an incomplete-coverage gap, not "this
  profile is clean" (Codex review). `incomplete_profiles` only counts an
  unavailable *required* check — an unavailable optional one does not, so
  this stays consistent with `AggregateResult.coverage` (also required-only;
  Codex review). `affected_profiles` also now considers a report's own gate
  (`severity.blocking`), not just its verdict — a `COMPATIBLE` report can
  still carry a policy-blocking gate (e.g. an `addition: error` policy), and
  a profile in that state is not "clean" just because nothing broke (Codex
  review). Each entry also gains `unanalyzed_profiles`: a profile with zero
  analyzed checks at all (every check for it unavailable, whether required
  or optional) is never described as "clean" — it was simply never checked,
  so claiming otherwise would assert confidence the result doesn't have
  (Codex review). The "affected" text branch also now surfaces
  `unanalyzed_profiles` when they coexist with an affected sibling profile
  on the same target — a profile with a real break and a profile with no
  analyzed result at all were both being folded into one "checked on X, Y"
  clause, wrongly implying the unanalyzed one produced a result too (Codex
  review).
- **`abicheck/type_reachability.py`**: a new, additive building block for
  status-review item 3 ("direct vs. transitive type reachability").
  `directly_referenced_stdlib_types()` computes, from a snapshot alone,
  which `std::`/`__gnu_cxx::`/etc. record types are directly referenced by a
  **public**, non-stdlib function's signature or a non-stdlib type's own
  field — as opposed to only reachable via deep template-instantiation
  internals (`std::string::_Alloc_hider`, `std::_Rb_tree_node_base`) that the
  existing whole-name-prefix filter (`is_non_abi_surface_type`) already
  correctly treats as toolchain-artifact churn either way. A
  `Visibility.HIDDEN`/`ELF_ONLY` function's signature does not count as a
  reference (Codex review): such functions are retained in real snapshots
  for cross-reference purposes but are not part of the public ABI surface
  this helper models, and treating them the same as a public function would
  turn an internal implementation signature into a stdlib ABI dependency
  that isn't real. A function's `origin` (`ScopeOrigin.PRIVATE_HEADER`/
  `SYSTEM_HEADER`/`GENERATED`) is checked alongside `visibility` for the
  same reason (Codex review, fresh evidence): public-header scoping can
  retain a function whose `visibility` is still `PUBLIC` but whose defining
  header sits outside the public set — linkage and origin are independent
  axes (ADR-024 D1), so this excludes it too, using the same
  `_NON_PUBLIC_ORIGINS` set `idioms.py` already established. The record-field
  scan gets the identical `origin` check (Codex review, fresh evidence): it
  had bypassed `_NON_PUBLIC_ORIGINS` entirely even though `RecordType`
  carries the same provenance axis as `Function` — a non-stdlib record kept
  only from a private/system/generated header must not make its own field
  types count as reachability roots either. Candidate identification is
  also fixed to use `qualified_name or name` instead of `name` alone
  (Codex review, fresh evidence): castxml/direct-clang populate the bare
  leaf in `name` and the "std::"-prefixed spelling separately in
  `qualified_name`, so `name` alone never carries the prefix for those two
  backends and the helper silently found nothing on any real
  castxml/clang-produced snapshot. That alone was still insufficient,
  confirmed by dumping a real compiled `std::vector<int>` parameter
  end-to-end: `Function.return_type`/`Param.type` spell the outer type
  bare (`"vector<int, std::allocator<int> >"`) even when the matching
  `RecordType`'s identity is fully qualified, across all three backends —
  fixed by also generating a namespace-prefix-stripped spelling per
  candidate and matching against either form. Still not fixed (out of
  scope for this pass, documented in `AGENTS.md`'s "Known gaps"): a
  signature spelled with a typedef alias (`std::string`, `std::wstring`)
  names the alias, not the real underlying class that owns the
  `RecordType` entry, and no current model field maps one back to the
  other — that needs a dedicated typedef-alias-resolution layer.
- **`type_reachability.py` is now wired into `diff_types.py`'s
  RecordType-based detectors** (struct/union size, alignment, fields,
  bases, vtable, kind, reserved fields, qualifiers, renames, deprecation):
  `_is_abi_surface_type()`, the single gate function every one of those
  detectors already shares, now accepts a `directly_referenced` set
  (`_directly_referenced(old, new)`, computed once per detector) and
  un-filters a std:: record that set names, instead of blanket-filtering
  every std:: record regardless of direct use — e.g. a public function
  taking `std::vector<int>` by value now correctly reports a layout change
  to that vector as `TYPE_SIZE_CHANGED`/`BREAKING`, where it was previously
  silently dropped as toolchain noise. Because one gate function covers
  every caller, this closes the gap for all of them at once rather than 9
  independently-drifting call sites. While wiring this in, the FP-rate
  corpus's own new cases surfaced a second, pre-existing bug in the gate's
  std:: check itself: it filtered using `t.name` alone (the same
  bare-vs-qualified split just fixed in `type_reachability.py`), so a real
  castxml/clang-produced std:: record was never actually recognized as
  std:: at all, independent of `directly_referenced` — fixed by keying the
  std:: prefix check on `qualified_name or name` too. `diff_platform.py`/
  `diff_symbols.py`/`diff_vtable_layout.py`/`diff_stdlib_impl.py`/
  `diff_layout.py`/`diff_filtering.py`/`diff_type_spellings.py`, plus
  `diff_types.py`'s own enum/typedef paths, remain unwired and carry the
  identical gap — each needs its own individually-verified follow-up
  (FP-rate/mutation-score gates), documented in `AGENTS.md`'s "Known gaps".
  New FP-rate gate corpus cases (`stdlib-direct-reference` category):
  `public_stdlib_type_used_directly_layout_changed` (real-break/FN
  sentinel) and `stdlib_type_unreferenced_stays_filtered` (internal-noise/
  FP sentinel) — both pass at baseline 0/0.
- **`type_reachability.py` hardened three more ways** (Codex review, fresh
  evidence, found while reviewing the `diff_types.py` wiring above):
  (1) **Spelling collision**: the namespace-prefix-stripped fallback
  spelling could coincidentally equal an unrelated, genuinely non-stdlib
  type's own bare name (e.g. a library's own top-level `vector<int, ...>`)
  — a signature naming that unrelated user type was misread as a direct
  stdlib reference, risking a false-positive attribution of unrelated
  layout churn to the stdlib candidate. Fixed by excluding a stripped
  spelling that collides with any real non-stdlib record's own identity
  from the match set for that candidate — missing the stdlib candidate in
  that rare case is far safer than misattributing an unrelated break.
  (2) **libc++ inline namespaces**: libc++/Android-NDK-libc++ wrap the
  whole standard library in an invisible inline namespace
  (`std::__1::`/`std::__ndk1::`) that shows up in a record's qualified
  name but never in a bare backend signature spelling — stripping only the
  `std::` prefix left `__1::vector<int>`, which still couldn't match.
  Fixed by also stripping a recognized inline-namespace marker right after
  the namespace prefix. (3) **Performance**: the per-candidate substring
  scan was O(candidates × declarations) — a synthetic snapshot with 1,000
  functions and 1,000 unreferenced stdlib records took over a second in a
  single call (confirmed locally; Codex additionally measured ~21s across
  the 9 independent `diff_types.py` call sites in one comparison). Fixed
  by compiling one alternation pattern per snapshot and scanning each
  declaration's type string once, turning the cost into O(declarations)
  independent of candidate count (~16x faster on the synthetic worst case,
  ~47x faster end-to-end through a full `compare()` call). The remaining
  9x redundant computation across `diff_types.py`'s independent call sites
  was deliberately left alone rather than added to a shared cache keyed on
  snapshot object identity — `AbiSnapshot` isn't hashable and an `id()`-
  based cache risks a silently-wrong result if two different snapshot
  objects across separate `compare()` calls in a long-running process ever
  reused the same id() after garbage collection, a correctness risk judged
  worse than the now-small remaining performance cost (~0.45s end-to-end
  on the synthetic worst case, verified). A future pass could eliminate it
  safely by threading a shared per-comparison context through the detector
  registry — a bigger, separate structural change.
- **`type_reachability.py`'s record-field scan now requires actual
  reachability from a public root** (Codex review, fresh evidence): the
  previous version scanned *every* non-stdlib record's fields
  unconditionally (excluding only confirmed-private-origin ones) rather
  than restricting to records the public surface actually reaches — a
  purely internal record a DWARF-only snapshot retains with the default
  `ScopeOrigin.UNKNOWN` (the common case, since origin classification is
  opt-in) could make an unrelated stdlib type look directly referenced
  just by existing in the snapshot with a matching-spelled field. Fixed
  with a proper worklist-based closure: a non-stdlib record's fields are
  only walked once that record is itself confirmed reachable, either by
  direct mention in a public, non-hidden, non-private-origin function's
  own signature, or transitively through another already-reachable
  record's fields. New FP-rate corpus case
  `internal_record_field_stdlib_churn_stays_filtered` guards this
  specifically (distinct from `stdlib_type_unreferenced_stays_filtered`,
  which has no owning record at all).

  A second finding from the same review round — gating signature
  reachability on pointer-vs-by-value use (a `std::vector<int> *`
  parameter shouldn't unfilter `std::vector<int>`'s layout) — was
  investigated and **not implemented**: it would regress this codebase's
  own established, ADR-024 §D3-documented anti-hiding position.
  `surface.py`'s own reachability closure deliberately does *not* apply
  pointer-vs-value precision either, since "a pointer-reached type whose
  full definition is public is still layout-observable (a consumer can
  dereference/allocate it by value), so demoting it at this stage would
  hide a real break" — the safe half of that precision (a pointer-only-
  reached *opaque* handle) is already delivered downstream by the
  existing opaque-size-change filter
  (`diff_filtering._filter_opaque_size_changes`, gated on
  `RecordType.is_opaque`), confirmed by reading that filter directly.
  Applying the suggested pointer-depth gate here would create exactly the
  false-negative risk that filter's own design note warns against, for a
  precision axis this module was never meant to duplicate.
- **`type_reachability.py` reachability closure gains two more fixes**
  (Codex review, fresh evidence): (1) a public *method*'s own owner
  class/struct is now seeded as reachable too — a public member like
  `void Foo::run()` never repeats `Foo` in its own return/parameter types,
  so without also consulting `diff_cxx_rules.owner_class_of()` the
  reachability closure never queued `Foo` at all, silently missing a
  genuine layout break in one of `Foo`'s fields. (2) a non-stdlib record's
  bare-trailing-segment alias (e.g. `Inner` for `api::Inner`) is now
  dropped rather than recorded when it is ambiguous — shared by two or
  more *distinct* non-stdlib records (e.g. `api::Inner` and
  `detail::Inner` both reducing to bare `Inner`) — since queuing every
  colliding record would let a signature naming one of them wrongly walk
  an unrelated internal record's fields too, misattributing its own
  implementation-only churn as publicly reachable.
- **`type_reachability.py`'s reachability closure now also seeds public
  variables and resolves typedef aliases** (Codex review, fresh evidence):
  (1) an exported public `Variable` (e.g. `Foo global`) is now a
  reachability root the same way a public function is — the closure
  previously only walked `snapshot.functions`, so a public global variable
  of a non-stdlib record type never seeded that record at all. (2) a
  signature/field type string spelled with a user-defined typedef alias
  (e.g. a public function returning `Alias` where `snapshot.typedefs` maps
  `"Alias"` to `"Foo"`) is now resolved to its target and scanned in turn,
  mirroring `surface.py`'s own reachability closure — the alias's target
  is a plain type-string substitution already present in
  `snapshot.typedefs`, distinct from the still-unresolved, harder gap of a
  signature spelled with a *stdlib* alias like `std::string` (which names
  a compiler-internal alias with no reverse mapping back to the owning
  `RecordType` in any current model field).
- **`type_reachability.py`'s reachability closure now also follows base
  classes** (Codex review, fresh evidence): a public `Derived` inheriting
  a non-stdlib `Base` whose own field is a stdlib record was never
  reached, since the worklist expansion only followed `rec.fields`, not
  `rec.bases`/`rec.virtual_bases`. Fixed by enqueuing both direct and
  virtual bases the same way `surface.py`'s own reachability closure does
  — virtual inheritance still embeds the base subobject + vtable path, so
  both are ABI-reachable through the derived type.
- **`type_reachability.py` resolves the previously-documented typedef-
  aliased-stdlib-type gap** (user-requested): a signature spelled with a
  typedef alias (`std::string`, `std::wstring`, ...) names the alias, not
  the real underlying class (`std::basic_string<char, ...>`) that owns the
  `RecordType` entry. Verified empirically against a real DWARF-dumped
  `std::string` parameter: `snapshot.typedefs["std::string"]` resolves to
  the bare `"basic_string<char, std::char_traits<char>,
  std::allocator<char> >"`, while the owning `RecordType.name` is the
  fully-qualified `"std::__cxx11::basic_string<char, std::char_traits<char>,
  std::allocator<char> >"` — libstdc++ wraps its own post-C++11 dual-ABI
  types in an inline namespace (`__cxx11::`) the same way libc++ wraps its
  whole standard library (`__1::`/`__ndk1::`, already handled), so that
  list gained a third entry. The typedef *key* itself also needed the same
  bare-vs-qualified treatment already applied to `RecordType` identities
  (the DWARF backend spells the signature bare — `"string"`, never the
  qualified key `"std::string"`), so a new `_typedef_spelling_targets()`
  builds a `spelling -> target` index covering both the literal key and
  its namespace-stripped bare form (an ambiguous stripped spelling is
  dropped rather than recorded, same principle as the `RecordType`
  spelling index), and the scan now follows a matched typedef alias to its
  target the same way `surface.py`'s own reachability closure does. New
  FP-rate corpus case `public_std_string_typedef_alias_layout_changed`
  (real-break/FN sentinel) passes at baseline 0/0.
- **`type_reachability.py`'s bare-alias derivation and single-pattern
  scan each had one more real gap** (Codex review, fresh evidence): (1)
  the non-stdlib bare-alias fallback derived a record's unqualified
  spelling via `identity.rsplit("::", 1)`, which splits inside a *template
  argument's own* qualified name rather than at the outer namespace
  boundary — for `"api::Wrapper<dep::Tag>"`, the lexically last `"::"`
  belongs to the template argument `dep::Tag`, not the outer namespace
  path, so the old code derived the corrupted bare form `"Tag>"` instead
  of `"Wrapper<dep::Tag>"`, and a real dumper backend's bare signature
  spelling for that wrapper then never matched anything. Fixed with a new
  `_bare_type_name()` that tracks `<`/`>` nesting depth and only treats a
  `"::"` at depth zero as a namespace separator. (2) stdlib and non-stdlib
  spellings were matched via one *combined* compiled pattern in a single
  non-overlapping `finditer()` pass — when a non-stdlib record's own
  identity embeds a stdlib type's spelling verbatim (e.g. a template
  instantiation `"Wrapper<std::string>"` registered as its own record
  identity), and a public signature names that wrapper's full identity
  exactly, the combined pattern's longest-first alternation matches the
  whole wrapper span first, consuming it — since regex matches never
  overlap, the nested `"std::string"` substring inside that same span was
  never independently found, even though it is directly present in the
  public signature text. Fixed by splitting `_spelling_index()` into two
  independent indices (stdlib vs. non-stdlib/record) with two
  independently compiled patterns scanned separately over each
  declaration, so one pattern's match can never mask the other's. New
  regression tests cover both: an end-to-end case resolving a record via
  its qualified-template-argument bare alias, and a wrapper-with-no-fields
  case that can only pass via the independent nested-spelling match (not
  the field-walk fallback).
- **`type_reachability.py`'s collision guards missed a bare-alias case on
  both the stdlib-stripping and typedef-stripping paths** (Codex review,
  fresh evidence): both guards checked a stripped/typedef spelling only
  against *full* non-stdlib record identities, not against the bare
  (namespace-unqualified) alias a real dumper backend actually spells that
  record with in a signature. A non-stdlib record like `api::vector<int>`
  is spelled bare as `"vector<int>"`, the same bare spelling
  `std::vector<int>` reduces to after namespace-stripping — since
  `"vector<int>"` never equals the full identity `"api::vector<int>"`, the
  old check let a signature naming the unrelated user type also mark the
  real `std::vector<int>` as directly referenced. The identical gap
  existed one level up on the typedef-key stripping path: `api::string`
  spelled bare as `"string"` collides with what the `"std::string"`
  typedef key strips to. Fixed with a new `_non_stdlib_signature_spellings()`
  helper (full identity plus bare alias, deliberately keeping an ambiguous
  bare alias that `_spelling_index`'s own `record_index` drops — it's
  still a real spelling *some* non-stdlib record can be named by) used by
  both `_spelling_index`'s stdlib-stripping loop and
  `_typedef_spelling_targets`'s typedef-stripping loop. New regression
  tests cover the collision set itself and both end-to-end paths
  (bare-aliased non-stdlib type vs. an unrelated stdlib record with the
  same bare spelling, through both the direct-record and typedef-alias
  routes).
- **`diff_cxx_rules.owner_class_of()` mis-parsed a public conversion
  operator's owner when the operator's own target type is
  namespace-qualified** (Codex review, fresh evidence, found via
  `type_reachability.py`'s new owner-seeding call site but affecting every
  caller of this shared helper — `diff_symbols.py`'s owner-based move
  detection, `diff_cxx_rules.py`'s own member-move heuristics, and
  `surface.py`'s reachability closure). Confirmed against a real compiled
  and demangled symbol: `struct Foo { operator ns::Bar() const; };`
  demangles to `"Foo::operator ns::Bar() const"`, and after the existing
  signature-stripping step abicheck's own `Function.name` is exactly
  `"Foo::operator ns::Bar"`. The old naive `rsplit("::", 1)` split at the
  *lexically last* `"::"` — which belongs to the operator's own qualified
  target (`ns::Bar`), not the owner/member boundary — producing the
  corrupted owner `"Foo::operator ns"` instead of `"Foo"`. Fixed by
  locating the literal `"::operator "` marker (present only for a
  conversion-to-named-type operator, never for a symbol operator like
  `operator+`/`operator[]`, which has no target type to separate from the
  keyword with a space) and splitting there when present, falling back to
  the previous behavior otherwise. New regression tests cover the direct
  unit behavior (including a namespace-nested owner, to guard against
  over-correcting) and the end-to-end `type_reachability.py` seeding path.
- **`type_reachability.py`'s typedef-key bare-aliasing only covered
  stdlib-namespaced keys** (Codex review, fresh evidence): the DWARF
  backend stores a namespace-qualified typedef key like `"api::Alias"`
  while a public declaration's own type string spells the bare `"Alias"`
  — the same bare-vs-qualified split this module already handles
  everywhere else, but `_typedef_spelling_targets()` only derived a bare
  form via `_stripped_signature_spelling()` (stdlib-namespace-prefix-only),
  so a non-stdlib qualified typedef key never got a bare alias at all —
  silently missing a stdlib field reachable through that typedef's target.
  Fixed by also deriving a candidate via `_bare_type_name()` (already
  correct for qualified template arguments) for every typedef key, not
  just stdlib-namespaced ones — reusing the existing collision guard
  unchanged. New regression tests cover the end-to-end resolution, the
  existing collision guard applied to this new candidate source, and a
  guard against registering a spurious duplicate entry for an
  already-bare key.
- **`type_reachability.py`'s owner-class seeding could mark a purely
  internal stdlib class as directly referenced** (Codex review, fresh
  evidence): CastXML/direct-clang record a method's own `Function.name`
  bare (e.g. `"touch"`, never `"__gnu_cxx::Node::touch"`), so the existing
  `fn.name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)` guard cannot catch
  a retained, seemingly-public method whose *owner* is itself a
  stdlib-internal class (e.g. libstdc++'s `__gnu_cxx::Node`) —
  `owner_class_of()` correctly recovers that qualified owner via its
  mangled-name fallback, and handing it straight to `_scan()` without a
  second check marked the stdlib class itself as directly referenced,
  unfiltering purely internal toolchain churn as a public break. Fixed by
  also checking the recovered owner against
  `STDLIB_TYPE_NAMESPACE_PREFIXES` before scanning it, mirroring the
  existing `fn.name` guard. New regression tests (positive and negative)
  plus a new FP-rate corpus case
  (`stdlib_internal_owner_method_stays_filtered`, `stdlib-direct-reference`
  category) reproducing the exact scenario; gate stays 0 FP/0 FN (32
  cases).
- **`type_reachability.py` gains two more fixes, closing a class of
  partially-qualified backend spellings and a class of bare-named stdlib
  declarations** (Codex review, fresh evidence): (1) A real backend does
  not always spell a nested type as either the fully-qualified identity
  or the fully-bare leaf — confirmed empirically via `clang -ast-dump` on
  `namespace api { struct Outer { struct Inner {}; }; Outer::Inner g();
  }`: direct-clang prints the return type as exactly `"Outer::Inner"`,
  dropping the enclosing namespace (`api::`) while keeping the
  class-nesting qualifier (`Outer::`). Neither the previous full-identity
  match nor the single fully-bare-leaf match (`_bare_type_name`) covered
  this partial qualification. Generalized `_bare_type_name` into
  `_namespace_suffix_spellings()`, which returns every suffix obtainable
  by dropping some prefix of the scope chain at each depth-zero `"::"`
  boundary (not just the innermost one), and updated all three call sites
  (`_spelling_index`'s record index, `_non_stdlib_signature_spellings`,
  `_typedef_spelling_targets`) to register every suffix instead of only
  the fully-bare one, with the same ambiguity-drop collision guard
  extended to every suffix. `_bare_type_name` itself is kept (now a thin
  wrapper returning the innermost suffix) since it's still independently
  tested and used for its narrower semantics. (2) CastXML/direct-clang
  record a function or namespace-scope variable's own display name bare
  (e.g. `"touch"`, never `"__gnu_cxx::Node::touch"` or `"std::touch"`), so
  the existing `name.startswith(STDLIB_TYPE_NAMESPACE_PREFIXES)` guard
  cannot catch a retained, seemingly-public declaration that is actually
  part of the standard library itself — verified with two real Itanium
  mangled-symbol repros (`_ZN3std5touchE` for a namespace-scope stdlib
  variable, `_ZN3std5touchEv` for a stdlib free function) that both
  incorrectly marked `std::string` as directly referenced before the fix.
  Fixed by also checking the declaration's recovered qualified name
  (`diff_cxx_rules.itanium_qualified_name`, from `mangled`) against the
  stdlib prefixes for both functions and variables. This subsumes (and
  replaces) the narrower owner-only check added earlier in this PR: since
  a stdlib-prefixed owner always makes the full qualified name
  stdlib-prefixed too, but not vice versa (a stdlib namespace's own direct
  free function/variable is a single mangled scope component, so
  `owner_class_of` returns a bare `"std"` with no trailing `"::"`, which
  never matches the `"std::"` prefix string), the broader check is
  strictly more correct and the redundant owner-only guard was removed.
  New regression tests cover both fixes (positive, negative, and
  ambiguity-guard cases); full suite green, 100% module coverage, FP-rate
  gate stays 0 FP/0 FN.
- **`type_reachability.py`'s owner-seeding gains a correctness fix, not
  just a missing-spelling one** (Codex review, fresh evidence, confirmed
  with a minimal repro): `owner_class_of()` derives its result by chopping
  the trailing `"::"`-component off any already-qualified declaration name
  or mangled-symbol scope chain, with no way to tell whether what remains
  is really an enclosing class or just an enclosing namespace — a public
  namespace function `api::run()` makes `owner_class_of` return the bare
  namespace fragment `"api"`, which the general suffix-matching mechanism
  could then coincidentally match against an unrelated internal record's
  own bare-suffix spelling (`other::api`), wrongly walking its fields and
  unfiltering its layout churn. Fixed by seeding an owner only on an exact
  match against a non-stdlib record's full identity, bypassing the
  suffix-matching mechanism entirely for this seed — safe because,
  unlike a genuine signature type spelling, `owner_class_of`'s result is
  always either the complete, exact scope chain of a real class or
  namespace noise, never a partially-elided one. While verifying this fix
  through the full `compare()` pipeline, the identical bug pattern was
  found to independently exist in `surface.py`'s own, separate
  `owner_class_of`-based public-surface seeding — deliberately left
  unfixed here and documented in `AGENTS.md`'s "Known gaps", since
  `surface.py` is a different, foundational module this PR doesn't
  otherwise touch and its bare-tail lookup mechanism is shared by every
  other seed type, needing its own careful, independently-verified design.
- **GCC/Clang's Itanium `St` substitution for the `std::` scope prefix is
  now recognized** (Codex review, fresh evidence, confirmed against two
  real GCC-compiled symbols): the Itanium ABI mandates the 2-character
  substitution `St` for the *first* occurrence of `std::` in a mangled
  name — `namespace std { void touch() {} }` compiles to the bare
  `_ZSt5touchv` (no `N…E` nested-name wrapper at all), while `namespace
  std { namespace detail { void foo() {} } }` compiles to
  `_ZNSt6detail3fooEv` (`St` right after the `N` marker). Neither form was
  previously recognized by `itanium_scope_components()`, so a bare-named
  stdlib function/variable using this (extremely common) substitution
  bypassed the mangled-scope-recovery guard added earlier in this PR.
  Fixed in the shared `diff_cxx_rules.py` parser (benefiting all of its
  callers, not just `type_reachability.py`) by recognizing `St` as the
  first scope component when it appears at the very start of parsing —
  deliberately not attempting general Itanium substitution-table
  resolution for the other substitution abbreviations (`Sa`/`Sb`/`Ss`/
  `Si`/`So`/`Sd`), which stand for a complete template type rather than a
  scope prefix and are irrelevant to this need. New regression tests
  cover both the standalone and nested `St` forms for both functions and
  variables, plus the owner-seeding exact-match fix (positive and
  negative cases). Full suite green, 100% module coverage, FP-rate gate
  stays 0 FP/0 FN.
- **Two more ambiguity-tracking gaps found in `type_reachability.py`'s
  collision guards** (Codex review, fresh evidence, both confirmed with
  minimal repros): (1) when two non-stdlib records had identities
  `"Inner"` and `"api::Inner"`, `_spelling_index`'s derived-suffix
  collection only counted contributors to the *derived* suffix `"Inner"`
  (from `"api::Inner"`) — since the unrelated global `"Inner"` identity
  never contributes to that same tracking structure (it's already a full
  identity, not a derived suffix), the ambiguity count saw only one
  contributor and merged `"api::Inner"` straight into the pre-existing
  full-identity entry for the global `"Inner"`. A public signature
  spelling the global type as bare `"Inner"` then also queued the
  unrelated `"api::Inner"` and its `std::` field. Fixed by also treating a
  derived suffix that collides with a *different* record's own full
  identity as ambiguous. (2) `_typedef_spelling_targets` gave an *exact*
  pre-existing typedef key automatic priority over a derived suffix from
  a different key, rather than tracking both through the same
  ambiguity-counting structure: when `snapshot.typedefs` held both a
  global `"Alias" -> "std::…"` and a qualified `"api::Alias" -> "Foo"`, a
  declaration inside `api` can legitimately spell the latter as bare
  `"Alias"` too — the bare spelling is genuinely ambiguous between the
  two real typedefs, and silently preferring the pre-existing exact key
  could resolve it to the wrong one (hiding a real non-stdlib reference or
  fabricating a stdlib one). Fixed by unifying exact keys and derived
  suffixes into one target-set-per-spelling structure, resolving a
  spelling only when every contributing source agrees on exactly one
  target. New regression tests cover both fixes (positive repro plus a
  guard that an agreeing exact-key/derived-suffix pair is still kept).
  Full suite green, 100% module coverage, FP-rate gate stays 0 FP/0 FN.
