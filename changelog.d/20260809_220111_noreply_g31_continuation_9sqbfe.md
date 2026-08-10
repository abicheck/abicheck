<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **The direct-clang (`--ast-frontend clang`) L2 header backend now
  populates `TypeField.default` (the default member initializer)** —
  G31 Phase C's last remaining fact-completeness gap that backend can
  close (vptr *placement* still cannot; the member-initializer *value*
  now can). Previously castxml-only, which left
  `FIELD_DEFAULT_INITIALIZER_REMOVED`/`_CHANGED` silently dead on a
  `--ast-frontend clang` run.
- **`FIELD_DEFAULT_INITIALIZER_REMOVED`/`_CHANGED` now gate on a
  SAME-producer check** (mirroring `Param.default`'s own
  `PARAM_DEFAULT_VALUE_*` gate) instead of "castxml on both sides": the
  two backends' initializer VALUE representations are still not
  cross-comparable (castxml keeps the verbatim source expression, clang
  falls back to a literal/structural fingerprint), so a castxml-vs-clang
  pair is correctly declined while a clang-vs-clang (or castxml-vs-castxml)
  pair now compares for real.
- **Snapshot schema bumped to v20** to gate the clang-side field-default
  extraction above: a snapshot serialized on the `--ast-frontend clang`
  header path under an older schema version never actually extracted
  `TypeField.default`, so reloading it now correctly marks the fact
  unreliable (`AbiSnapshot.clang_field_initializer_facts_reliable`,
  mirroring `clang_deprecation_facts_reliable`'s v19 pattern) instead of
  treating a stale `None` as a trustworthy "no initializer" answer.
- **Qualified the hybrid merge's field `default` provenance key** by
  namespace, matching `deprecated`'s existing qualification: since a
  clang-only field's `default` provenance is now stamped too (previously
  only `deprecated` was), two distinct types sharing only a bare leaf name
  in different namespaces could otherwise collide in the shared provenance
  dict. A hybrid baseline persisted before this fix still reads correctly
  via the existing bare-key fallback.
- **Bumped the whole-snapshot disk cache version** (`snapshot_cache.py`,
  v7 → v8): an upgrading user's warm `--ast-frontend clang`/`hybrid` cache
  entry would otherwise keep replaying the pre-upgrade snapshot (missing
  the newly-extracted field-default facts, or stale bare-keyed
  `fact_provenance` for a hybrid entry) until the entry expired or was
  manually cleared.
- **Fixed a false `FIELD_DEFAULT_INITIALIZER_REMOVED` against a legacy
  pre-v20 clang snapshot** (Codex review, fresh evidence): the new
  same-producer gate's "producer unknown → permissive" fallback couldn't
  tell a POSITIVELY known-unreliable value (the legacy snapshot's
  unconditional `None`, real but wrong) apart from genuinely-never-recorded
  provenance, so comparing a fresh clang snapshot's real initializer
  against an unchanged, persisted pre-v20 clang baseline reported a
  spurious removal purely from the schema upgrade. The gate now declines
  the comparison outright whenever either side is positively known
  unreliable, while staying permissive for truly unset provenance.
- **Extended that same fix to cover a legacy pre-v20 hybrid snapshot**
  (Codex review, fresh evidence, second round): a hybrid merge's
  clang-only-appended record types never had `default` provenance stamped
  at all under the old merge code (only `deprecated` was), so an ABSENT
  provenance entry for one of those fields on a legacy hybrid snapshot is
  real-but-WRONG legacy data too, not genuinely unrecorded — the same
  reliability marker (`clang_field_initializer_facts_reliable`) now also
  covers the `"hybrid"` producer. A MATCHED field's own recorded
  provenance entry (always unconditionally stamped `"castxml"`, regardless
  of schema version) stays trusted either way — only an absence on a
  legacy hybrid snapshot is treated as unreliable.
- **Fixed a missed `FIELD_DEFAULT_INITIALIZER_CHANGED` when a field
  initializer is a declaration reference or function call** (Codex review,
  fresh evidence): the direct-clang backend's structural fingerprint
  (`_canonical_expr`) dropped a `DeclRefExpr`'s `referencedDecl` sibling
  entirely, so `int x = DEFAULT_A;` vs. `int x = DEFAULT_B;` (or `one()`
  vs. `two()`) fingerprinted identically — verified against real Clang 18
  output before fixing. Since `Param.default` shares this same helper, this
  also closes the identical gap in `PARAM_DEFAULT_VALUE_CHANGED` for a
  clang-parsed default argument. The referenced declaration's stable
  `kind`/`name`/`type` are now folded into the fingerprint (never its raw
  memory-address `id`, which stays excluded per this function's existing
  build-stability contract); the same declaration referenced twice still
  fingerprints identically.
- **Extended that fix to distinguish same-named declarations in different
  scopes** (Codex review, fresh evidence, second round): a `referencedDecl`
  stub's `kind`/`name`/`type` alone still collide for `a::VALUE` vs.
  `b::VALUE` (or `a::same()` vs. `b::same()`) — a `DeclRefExpr`'s compact
  stub carries only the bare, unqualified name, verified against real Clang
  17/18 output. A new `dumper_clang._index_decl_id_qualified_names()` maps
  every declaration's clang `id` to its scope-qualified name in one pass
  over the AST root, letting the fingerprint resolve a referenced
  declaration's *scope* without ever hashing its unstable, per-build
  memory-address `id` directly. Falls back to the bare-identity behavior
  above when a reference's `id` isn't found (e.g. a builtin).
- **Fixed the id-index above being built eagerly for every field, even one
  with no initializer at all** (Codex review, fresh evidence): the index
  build was a plain function-call argument, so Python evaluated it before
  `_field_initializer_value` got a chance to reject the field via
  `hasInClassInitializer` — the first field processed in nearly every
  direct-clang dump paid the one-time whole-AST index walk for nothing. Now
  gated on `hasInClassInitializer` first, short-circuiting the same way the
  sibling `Param.default` call site already did.
- **Fixed the id-index above colliding across distinct template
  specializations** (Codex review, fresh evidence, second round): `A<int>`
  and `A<long>` are separate `ClassTemplateSpecializationDecl` nodes that
  both expose only the bare primary-template name `"A"` — verified against
  real Clang 17 output that this node carries no template-argument spelling
  at all, and that kind is absent from the existing namespace/class
  scope-tracking rule. A representative member's own MANGLED name (which
  does encode the arguments, e.g. `_ZN1AIiE5VALUEE` vs. `_ZN1AIlE5VALUEE`)
  is now used to disambiguate — build-stable, unlike the specialization
  node's own memory-address `id`.
- **Fixed a missed `FIELD_DEFAULT_INITIALIZER_CHANGED` for a `sizeof`/
  `alignof` operand type change** (Codex review, fresh evidence): a
  `UnaryExprOrTypeTraitExpr` stores its TYPE operand exclusively in
  `argType` — its own `type` key is just the trait's result type (always
  `unsigned long` for `sizeof`, identical regardless of operand) — so
  `sizeof(int)` and `sizeof(long long)` fingerprinted identically before
  this. Verified against real Clang 18 output before fixing.
- **Fixed the template-specialization disambiguator above being unstable
  to unrelated member insertions** (Codex review, fresh evidence, third
  round): it previously used whichever direct child happened to be FIRST
  with a mangled name, so inserting a new, unrelated member earlier in the
  same specialization changed an already-unrelated declaration's computed
  fingerprint purely from that insertion — verified against real Clang 17
  output. Now derives the disambiguator from only the SCOPE portion of a
  representative member's mangled name (via the existing
  `diff_cxx_rules.itanium_scope_components`, dropping the member's own
  trailing leaf component), which is identical regardless of which member
  of the same specialization contributed it.
- **Fixed the direct-clang initializer fingerprint above embedding a
  source location for an anonymous type** (Codex review, fresh evidence,
  fourth round): clang spells an anonymous enum/struct/union/class's type
  as `"(unnamed <kind> at <file>:<line>:<col>)"` — an absolute path and
  line number baked directly into the type spelling. Verified against real
  Clang 17 output that parsing identical source from two different
  checkout paths, or merely inserting a blank line before an anonymous
  `enum { VALUE = 3 };`, produced two different `TypeField.default`
  fingerprints for an unrelated, unchanged initializer referencing it. The
  location is now normalized to a fixed placeholder before hashing (the
  "unnamed `<kind>`" portion itself is kept, still distinguishing an
  anonymous type from a named one).
- **Extended the same location-normalization to lambda closure types**
  (Codex review, fresh evidence, fourth round): a lambda's type is spelled
  `"(lambda at <file>:<line>:<col>)"` — a different, non-"unnamed"-prefixed
  shape than the anonymous-tag pattern above, so the earlier fix's regex
  didn't match it. Verified against real Clang 17 output that this spelling
  recurs throughout an entire `std::function<...>`-wrapped lambda's
  template instantiation chain, so every occurrence is now normalized, not
  just the first.
- **Fixed a possible false `PARAM_DEFAULT_VALUE_CHANGED` when upgrading
  past this PR** (Codex review, fresh evidence, P1): unlike a literal
  default's plain value, a non-literal default's clang-side representation
  is a structural fingerprint whose exact algorithm changed across this
  PR's several fixes above (referenced-declaration identity/scope, `sizeof`
  operand types, anonymous-type/lambda location normalization) — none of
  which the already-shipped `Param.default` fact had ever needed before.
  A persisted pre-v20 clang snapshot's fingerprint for an UNCHANGED
  non-literal default argument could therefore differ from a freshly-dumped
  one purely from that algorithm shift, not a real edit. The comparison now
  declines specifically for fingerprint-shaped (`"expr:"`-prefixed) values
  on a pre-v20 clang side — a literal default's value, and default
  presence/absence detection (`PARAM_DEFAULT_VALUE_REMOVED`), are both
  unaffected and continue comparing normally regardless of schema version.
  The new gate lives in a new leaf module,
  `abicheck/diff_symbols_param_defaults.py`, split out to keep
  `diff_symbols.py` under its line-count cap.
- **Extended the pre-v20 `Param.default` gate above to a legacy hybrid
  snapshot too** (Codex review, fresh evidence): the gate only recognized a
  snapshot whose top-level `ast_producer` was exactly `"clang"`, missing a
  hybrid snapshot's clang-only-appended function (`dumper_hybrid.py`'s
  merge stamps that specific function's `param_defaults` provenance as
  `"clang"` unconditionally) — the same false `PARAM_DEFAULT_VALUE_CHANGED`
  risk, just reached through a different producer path. The gate now
  consults the caller's already-resolved PER-FUNCTION producer instead of
  the snapshot-level one, correctly covering both shapes uniformly.
- **Fixed the referenced-declaration index still being built eagerly for a
  plain literal default** (Codex review, fresh evidence): gating the build
  on `hasInClassInitializer`/`_param_has_default` alone (an earlier fix)
  still triggered it for e.g. `int timeout = 30;`, which never reaches the
  point that actually needs it. The index is now threaded as a lazy,
  memoized callable and invoked only at the exact point
  `_canonical_expr` resolves a real `referencedDecl`.
- **Documented a known, deliberately-deferred gap**: an MSVC
  (`clang-cl`/`--target=*-windows-msvc`) direct-clang snapshot's template
  specialization members mangle with a scheme neither existing scope
  parser (`itanium_scope_components` nor `diff_cxx_rules.
  msvc_scope_components`, the latter verified by its own design to reject
  template-argument encoding) can decode — `A<int>::VALUE` vs.
  `A<long>::VALUE` on such a snapshot still collide to the bare `"A"`. A
  real fix needs new MSVC template-argument decoding with no existing
  caller to verify it against yet; pinned with a regression test rather
  than guessed at.
- **Split `dumper_clang.py`'s initializer/default-argument fingerprinting
  into a new leaf module**, `abicheck/dumper_clang_expr.py`: the fingerprint
  chain grew substantially across this PR's several review rounds and kept
  pushing the parent file over its 2000-line hard cap. Behavior-preserving
  (every fingerprint byte-for-byte identical before/after, verified against
  every case in this PR's own test suite) — existing test imports needed no
  changes, since the moved names are re-exported back through
  `dumper_clang`'s own namespace.
- **Fixed a false `FIELD_DEFAULT_INITIALIZER_CHANGED` when one side's header
  producer predates provenance tracking entirely** (Codex review, fresh
  evidence): `fact_provenance.same_producer_backed_fact_qualified`'s
  "producer unknown → permissive" fallback let a legacy castxml snapshot
  (real verbatim source-expression text, but no recorded `ast_producer`)
  compare against a fresh, confirmed direct-clang snapshot's structural
  fingerprint for the SAME, unchanged field — reading as a false change
  purely from the representation mismatch. This detector's predecessor gate
  (`both_castxml_backed_fact`) required POSITIVELY confirmed castxml
  provenance on both sides; the fix restores that same both-positively-known
  invariant (generalized to any matching known producer, not just castxml).
  `Param.default`'s own separate, inline gate is unaffected — it stays
  deliberately permissive on an unknown producer, a pre-existing behavior
  this PR didn't change.
- **Fixed `PARAM_DEFAULT_VALUE_CHANGED`'s fingerprint-reliability gate
  checking the WRONG side's reliability** (Codex review, fresh evidence):
  the gate declined a comparison whenever EITHER value looked
  fingerprint-shaped (`"expr:"`-prefixed), then checked BOTH sides'
  reliability — so a pre-v20 clang snapshot storing a LITERAL default
  (never touching the unstable fingerprint algorithm at all) could suppress
  a genuine change on the OTHER side purely because its own, irrelevant
  reliability flag was unset. Now checked per side: a side's reliability
  only matters when THAT side's own value is fingerprint-shaped. Renamed
  the leaf module the two gate functions live in from
  `diff_symbols_param_defaults.py` to `diff_default_value_reliability.py`
  (and the functions from `param_default_*` to `default_value_*`), since
  both are now shared verbatim between `Param.default` and
  `TypeField.default`'s own value comparison instead of being Param-only.
- **Fixed a missed `FIELD_DEFAULT_INITIALIZER_CHANGED`/
  `PARAM_DEFAULT_VALUE_CHANGED` when a default references distinct
  specializations of the same function/variable template** (Codex review,
  fresh evidence, fourth round): `f<int>()`/`f<long>()` both report a
  `FunctionDecl` named `"f"`, and `V<int>`/`V<long>` both report a
  `VarTemplateSpecializationDecl` named `"V"` — no template-argument
  spelling on the node itself, verified against real Clang 17 output — so
  the referenced-declaration index resolved both to the same qualified
  name, and a default changing from one specialization to the other
  fingerprinted identically. Each specialization DOES carry its own
  build-stable `mangledName` directly (unlike a class-template
  specialization, which has none of its own), so it's now used as the
  index value outright whenever the node is a function/variable template
  specialization.
- **Documented a second known, deliberately-deferred gap** (Codex review,
  fresh evidence, fourth round): a template-DEPENDENT initializer operand
  inside an uninstantiated class template pattern (e.g. `T::template
  value<1>()` vs. `T::template value<2>()`) is a
  `DependentScopeDeclRefExpr` that clang's `-ast-dump=json` prints with no
  name, value, or children at all — verified against real Clang 17 output
  that both instances reduce to the byte-identical structural form, so a
  default changing between two dependent operands like this is silently
  missed. A real fix needs either re-invoking clang in a different dump
  mode or reading the raw source text at the node's range offsets, neither
  a narrow extension of this pure AST-JSON-only fingerprint chain; pinned
  with a regression test rather than guessed at.
- **Fixed a false `PARAM_DEFAULT_VALUE_CHANGED` against a direct-clang
  snapshot persisted BEFORE `ast_producer` provenance was tracked at all**
  (Codex review, fresh evidence, third round): such a snapshot's
  per-declaration producer resolves to `None` (unknown), not `"clang"`, but
  its real value can still be an `"expr:"`-shaped fingerprint from that
  same unstable pre-v20 `_canonical_expr` algorithm. The reliability gate's
  strict `producer == "clang"` equality check let this producer-less
  legacy fingerprint compare directly against a freshly-stabilized one.
  Since the caller already restricts every check here to a value that
  starts with `"expr:"` — and only the direct-clang backend ever produces
  that prefix — an unresolved producer is now treated the same as a
  known-clang one for this check (only `"castxml"` is excluded, since it
  never produces that prefix at all).
- **Documented a third known, deliberately-deferred fingerprint gap**
  (Codex review, fresh evidence): `offsetof(A, x)` vs. `offsetof(A, y)` —
  clang's `OffsetOfExpr` node carries no `inner` children and no key
  identifying which member the offset walk selects, verified against real
  Clang 18 output (both `-ast-dump=json` and the plain `-ast-dump` text
  form) that both calls reduce to the byte-identical structural form.
  Pinned with a regression test rather than guessed at.
- **Fixed a missed `FIELD_DEFAULT_INITIALIZER_CHANGED`/
  `PARAM_DEFAULT_VALUE_CHANGED` for a `new`-expression allocation-function
  change** (Codex review, fresh evidence): `new S()` vs. `::new S()` when
  `S` declares its own `operator new` previously fingerprinted identically
  — clang's `CXXNewExpr` node's `isGlobal`/`operatorNewDecl`/
  `operatorDeleteDecl` keys, which are what distinguish a class-member
  allocation from a global one, were dropped by `_canonical_expr`'s
  whitelist entirely, verified against real Clang 18 output before fixing.
  `operatorNewDecl`/`operatorDeleteDecl` now reduce through the same
  stable `kind`/`name`/`type`(/`qualified_name`-via-id-index) stub
  `referencedDecl` already used, factored into a shared `_decl_stub()`
  helper.
- **Fixed a missed `FIELD_DEFAULT_INITIALIZER_CHANGED`/
  `PARAM_DEFAULT_VALUE_CHANGED` for a value- vs. default-initialization
  change** (Codex review, fresh evidence): `new S` vs. `new S()` previously
  fingerprinted identically too — `CXXNewExpr.initStyle` (absent vs.
  `"call"`) and the nested `CXXConstructExpr`'s `zeroing` (absent vs.
  `true`, since value-initialization zero-initializes `S`'s scalar members
  first) were both dropped by `_canonical_expr`'s whitelist, verified
  against real Clang 18 output before fixing. Both are now kept verbatim.
