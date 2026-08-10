### Added

- **A public template instantiation's own type arguments and emitted symbols
  are now graph facts.** `abicheck/buildsource/template_graph.py` is a
  third, independent `clang -ast-dump=json` pass (alongside the existing
  call and type graph passes), closing the "public template → concrete
  instantiation → internal specialization → emitted exported symbol" chain
  the pre-existing passes can't: they only ever see a template's *pattern*,
  never a specific instantiation's arguments. New `template_decl`/
  `template_instantiation` graph nodes and `DECL_INSTANTIATES_TEMPLATE`/
  `TEMPLATE_USES_TYPE`/`INSTANTIATION_EMITS_SYMBOL` edges, driven by
  `inline_graph_fold.fold_template_graph` whenever the call/type graph
  passes run. A resolved template argument (e.g. `Wrapper<internal::Detail>`)
  joins onto the same `record_type`/`enum_type` node `type_graph.py` would
  use for the identical qualified name, via clang's own `decl` cross-
  reference on the `TemplateArgument` node — exact identification, not a
  textual heuristic, and it resolves straight through a `using`/typedef
  alias argument to the real declaration. A class instantiation's own
  instantiated member functions' mangled names join onto an existing
  `binary_symbol` node only (ADR-057 D1's join-by-shared-node-id rule,
  reapplied) — an instantiated-but-never-exported member mints no node.
  `TEMPLATE_USES_DECL`/`INSTANTIATION_MAPS_TO_EXPORT`/
  `DECL_USES_DEFAULT_TEMPLATE_ARG`/`CONSTRAINT_DEPENDS_ON_DECL` remain
  reserved, unpopulated vocabulary — see the module's own docstring for why
  each is deferred. This closes the first of G29 Phase 5's five named open
  graph families (object/archive link provenance was the other closed one);
  virtual dispatch, macro/config, and callback/function-pointer remain
  open. No new `ChangeKind`, no report schema change, no verdict/exit-code
  effect.

### Fixed

- `source_graph.NODE_KINDS`/`EDGE_KINDS`'s object/link-provenance vocabulary
  (`object_file`/`archive_member`/`static_library`/…,
  `ARCHIVE_CONTAINS_OBJECT`/`OBJECT_DEFINES_SYMBOL`/…) moved out of
  `source_graph.py`'s own inline declarations into
  `graph_facts.LINK_PROVENANCE_NODE_KINDS`/`LINK_PROVENANCE_EDGE_KINDS`,
  unioned in the same way the consumer/use-case/template vocabulary already
  is — a pure relocation (no behavior change) needed to keep
  `source_graph.py` under its 2000-line hard cap while adding the template
  vocabulary above.

  Four more fixes from a review round on `template_graph.py` itself: a
  `ClassTemplateSpecializationDecl` used as a *nested* template argument
  (`Outer<Wrapper<int>>` vs. `Outer<Wrapper<double>>`) resolved its argument's
  `target_qname` to the identical bare, unparameterized primary-template name
  for both — empirically confirmed against real clang AST output — so the
  two collided onto one graph node instead of staying distinct; the resolver
  now recurses through the target's own template arguments the same way the
  top-level per-instantiation walk already disambiguates itself. A resolved
  argument's `TemplateArgUse` now also carries the target's own raw clang
  decl kind, so `augment_graph_with_templates` mints the correct `enum_type`/
  `typedef`/`record_type` graph node for it instead of defaulting every
  resolved argument to `record_type` regardless of what it actually is (the
  `_type_node_kind` helper existed but was never called).
  `ClangTemplateGraphExtractor.extract_from_build`'s cross-TU dedup now
  merges a repeated `(kind, template_qname, label)` instantiation instead of
  keeping whichever TU happened to run first — mirrors `type_graph.py`'s own
  `_merge_type_edges` for the identical richness gap (one TU may resolve an
  argument's `target_qname` or reach an instantiated member another TU's
  translation unit never used). And a dead `out` parameter threaded through
  `_walk_class_templates` (never appended to — the function only ever
  *registers* class-template membership) was removed, along with a
  `_MEMBER_FUNCTION_KINDS`-equivalent tuple duplicated inline in
  `_walk_function_templates`.

  Separately, a real macOS CI failure surfaced a pre-existing test gap
  (unrelated to any code path this feature touches): `test_archive_graph.py`'s
  real-`ar` round-trip used an uninitialized global (`int gamma_sym;`) to
  exercise a data-symbol export — a tentative definition becomes a COMMON
  symbol, and whether a platform's `ranlib`/`ar` indexes a COMMON symbol the
  same way as an ordinary defined one is toolchain-specific (macOS's system
  `ar` omits it). The test now initializes the value, sidestepping the
  ambiguity rather than asserting a platform-dependent fact the test was
  never actually trying to verify.

  A second review round found three more real gaps, all empirically
  confirmed against real clang AST output before fixing: two distinct
  overloads of the same function template (`f<T>(T)` vs. `f<T>(T,T)`), both
  instantiated with identical template arguments, produce the identical
  *label* (built only from template arguments, never arity/signature) — so
  `template_instantiation_node_id` collapsed both onto one graph node,
  attributing both overloads' emitted symbols to a single instantiation
  identity; it now additionally keys a function-kind instantiation by its
  own unique mangled name, which always differs between overloads even when
  the label doesn't. A class template specialization's own nested member
  function template (e.g. `Holder::apply`) needs the specialization's own
  name added to scope, or two *unrelated* classes sharing a member-template
  name in the same enclosing scope collapse onto one `template_decl` node as
  if they instantiated the same template — confirmed with two sibling class
  templates (`Holder`/`Wrapper`) each declaring their own `apply` member,
  both resolving to the identical bare `api::apply` before this fix. And on
  Darwin, clang's AST reports a mangled name with the platform's extra
  Mach-O leading underscore still attached (`__Z...`), the same
  `call_graph.py`/`type_graph.py` quirk their own `_normalize_mangled`
  already strips — left unstripped here, every `INSTANTIATION_EMITS_SYMBOL`
  edge silently failed to join on Mach-O; this module now carries its own
  independent copy of the same one-line fix.

  A third review round, on `archive_graph.py`: `ar` permits two members
  sharing one name in the same archive (e.g. `ar rc lib.a sub1/util.o
  sub2/util.o`) — `archive_member_node_id` keyed only on `(archive label,
  member name)`, so both members collapsed onto one graph node, and every
  symbol the index attributed to either member attached to that single node
  instead of its own. `ArSymbolRef` now also carries the defining member's
  own `header_offset` (the join key the symbol-index parsers already had in
  hand), and both `archive_member_node_id` and the per-archive member lookup
  key on it, disambiguating without inventing new state. Also:
  `FileReader.__init__` now reads size via `os.fstat` on the already-open
  descriptor instead of a fresh `path.stat()` (which re-resolves the path a
  second time, and whose failure would leak the just-opened handle since a
  raising `__init__` never reaches `__exit__`); `inline.py`'s
  `fold_archive_graph` import moved from eager/top-level into the same
  deferred-import block as the other three fold passes, for one consistent
  import pattern; two stale code comments (`_walk_class_templates` claiming
  recursion it never performs; this fragment's own now-corrected "last
  of"/"first of" wording contradiction against the archive-graph fragment
  covering the same PR) were corrected; and three real-`ar` integration
  tests that guarded only on `ar`'s presence, then unconditionally invoked
  `gcc`, now guard on both (a runner with binutils but no `gcc` would error
  instead of skip).

  A fourth review round found three more real gaps. `cli_buildsource_helpers.
  _collect_source_graph` (the out-of-band `collect --source-abi --source-
  graph summary` path) folded only three of the four clang-backed passes
  `inline._build_inline_graph`'s own `with_call_graph` block runs together,
  and never called `fold_archive_graph` at all — an otherwise-equivalent
  collected pack silently carried no template nodes/edges/coverage stamp,
  and its `static_library` nodes got no archive-member/symbol-definition
  edges regardless of whether `--source-abi` was given; both passes are now
  folded the same way the inline path already does. `source_graph.
  _augment_with_source_abi` minted a `binary_symbol` node only from
  `source_decl_to_binary_symbol` — `source_link.py` accounts for a real
  export under several other mappings too (a template-instantiation export
  matched only through an erased-pattern attribution, a synthesized
  vtable/typeinfo/thunk, an allocator interposer, a genuinely undocumented/
  leaked export), each keyed *by* the symbol; omitting them left no node for
  archive_graph.py's/template_graph.py's own join-only-onto-an-existing-node
  rule to find, so a real archive member defining an undocumented export, or
  a template instantiation's own emitted member matched only through this
  attribution tier, silently went unjoined — all four mappings are now
  seeded. And an *explicit* function-template specialization
  (`template<> int foo<int>(int)`) exhibits the identical detachment quirk
  this module's docstring already documents for class templates — an
  unmangled stub nested under the `FunctionTemplateDecl`, full mangled
  content detached as a top-level sibling sharing the stub's id — but only
  the class-template path had the two-pass id-indexed join needed to
  resolve it; `_walk_function_templates` now has the same join
  (`_collect_full_function_defs`), verified against real clang AST output.

  A fifth review round found two more real gaps, both empirically confirmed.
  `_template_arg_use()` only checked a `TemplateArgument`'s *direct* children
  for clang's `decl` cross-reference, but a pointer/reference/array/cv-
  qualified argument nests it one or more wrapper levels deep instead —
  `Box<internal::Detail *>` produces `TemplateArgument -> PointerType ->
  RecordType -> decl`, and `Box<const internal::Detail &>` nests two levels
  (`LValueReferenceType -> QualType -> RecordType -> decl`) — verified
  against real clang AST output for pointer, reference, and array wrapping
  alike. `_template_arg_use` now recurses depth-first through a node's own
  `inner` subtree (`_first_decl_id`), pre-order so the outermost decl still
  wins for a nested specialization argument. Separately, `archive_graph.py`'s
  `archive_member_node_id` disambiguated two same-named members of one
  archive by the defining member's raw `header_offset` — but that offset
  shifts for *every later* member whenever an *earlier* member's own size
  changes, even when the later member's own content is byte-identical,
  false-positively reporting an untouched member (and its edges) as
  removed-and-re-added on a version-over-version structural graph diff. Now
  keyed by a stable, zero-based *occurrence index* among same-named members
  in file order instead of the offset itself.

  A sixth round found and fixed a real, pre-existing bug this PR's own new
  Windows integration test was the first to exercise end-to-end: every
  normalized `BuildEvidenceCompileUnit` field (`source`, `directory`,
  `include_paths`, ...) is persisted with its home-directory prefix redacted
  to `~` (ADR-032 D7's `RedactionPolicy`), and `subprocess` never expands `~`
  (no shell) — so replaying a redacted path straight into a real `clang`
  invocation makes it fail to find the file. `call_graph.py`'s
  `_safe_clang_args_from_compile_unit` (shared by all three clang-backed L5
  passes: call/type/template graph) built its argv — including the source
  positional — directly from these redacted fields, and each extractor's
  `_extract_from_compile_unit` passed the equally-redacted `cu.directory` as
  `cwd`, with no un-redaction anywhere on this path. Confirmed on real
  Windows CI: the new `test_collect_source_graph_folds_template_graph_pass`
  puts its temp source under the runner's own home directory
  (`C:\Users\...\AppData\Local\Temp\...`), which redaction rewrites to
  `~\AppData\...` — degrading call/type/template graph collection uniformly
  (all three showed up `degraded_passes`) while the sibling `include_graph`
  pass, which already un-redacts its own argv/cwd
  (`ClangIncludeExtractor.extract_from_build`, same pattern already used by
  `preprocessor_scan.py`/`archive_graph.py`), succeeded. Fixed by
  un-redacting every argv token and the `cwd` the same way, via a new shared
  `call_graph._replay_cwd` helper.

  A seventh round found three more real gaps, each empirically confirmed.
  A `ClassTemplateSpecializationDecl`'s own *nested* declarations (e.g.
  `Wrapper<int>::Nested`) scoped under the specialization's bare,
  unparameterized name — two distinct specializations' nested types
  (`Wrapper<int>::Nested` vs. `Wrapper<double>::Nested`) both indexed as the
  identical bare `"Wrapper::Nested"` and collided onto one type node;
  `_index_type_decls` now builds the scope name from the specialization's own
  `TemplateArgument` spellings (mirroring `_instantiation_label`) instead.
  Separately, every `TemplateInstantiation.file` came back empty for nearly
  every real instantiation: clang emits `loc.file` only on the very *first*
  node with a location in a TU (confirmed against real clang output — a
  two-declaration single-file TU records `loc.file` on the first top-level
  declaration only), and the previous code called the stateless `_node_file`
  directly at each instantiation site instead of threading the sticky file
  forward the way `type_graph._index_declared_entities` already does for the
  identical clang quirk. `_index_type_decls` now threads `cur_file` through
  its own walk (returning the updated value, same contract as its
  `type_graph.py` counterpart) and records an `id -> file` index every
  instantiation site reads instead of calling `_node_file` directly. And
  `inline._build_inline_graph`'s own `has_build` gate counted only
  `compile_units`/`targets`, not `link_units` — a link-only
  input (e.g. a Make transcript linking prebuilt objects against a static
  archive, no compile actions of its own) made the function return `None`
  before ever calling `build_source_graph()`, which folds `link_units`
  unconditionally regardless; `link_units` now counts toward `has_build` too.

  An eighth round found a real gap in variadic-template handling, confirmed
  against real clang AST output: a parameter pack argument is *itself* one
  `TemplateArgument` node (`isPack: true`, no `type`/`value` of its own)
  whose real per-element arguments are nested one level deeper in its own
  `inner` — `Pack<int>` and `Pack<double>` both produce this pack-wrapper
  shape, and the previous code treated the wrapper as a plain, unspellable
  argument and dropped the whole pack, collapsing both instantiations onto
  the identical, argument-less label `"Pack"`. A new `_flatten_template_args`
  helper (used everywhere a decl's direct `TemplateArgument` children are
  collected) recurses into a pack wrapper's own `inner` instead.

  A ninth round found two more real gaps, both empirically confirmed. A
  **template-template argument** (a template passed as a template argument,
  e.g. `Use<A>`/`Use<B>` for `template <template <typename> class C> struct
  Use;`) produces a completely bare `TemplateArgument` node — no `type`,
  `value`, `isPack`, or `inner` at all: clang's `-ast-dump=json` serializes
  zero identifying information for it, so `Use<A>` and `Use<B>` are
  indistinguishable from the dump alone. The previous code silently dropped
  this one opaque argument (same as any other unmodeled shape), but that
  still let both instantiations reduce to the identical, argument-less label
  `"Use"` and collide onto one shared graph-node identity, merging their
  real, distinct emitted-symbol/type-dependency edges — a correctness bug,
  not merely an incomplete args list. `_flatten_template_args` now returns
  `None` (not an empty/partial list) when any argument is opaque, and every
  caller treats `None` as "skip this instantiation entirely" instead of
  recording a wrong, merged one. Separately, `inline_graph_fold.
  _default_archive_search_roots` derived search roots only from
  compile-unit directories — for **link-only** evidence (no `compile_units`
  at all, the exact Make-transcript scenario the previous round's
  `has_build` fix newly reaches) it returned no roots whatsoever, so a
  relative archive link-input could still never be found even though the
  archive pass now runs for it. `LinkUnit` gains an additive `directory`
  field (defensive `.get()` parsing, no `BUILD_EVIDENCE_VERSION` bump
  needed, mirroring `CompileUnit.directory`), populated only by
  `adapters/make.py` — the one build system with no absolute-path-carrying
  target graph to lean on instead — and `_default_archive_search_roots` now
  also tries each link unit's own directory.

  A tenth round found two more real gaps and confirmed one deliberate,
  pre-existing tradeoff needed no change. `template_decl_node_id()` keyed
  a function template's own abstract declaration node by qname alone
  (`template_decl://f`) — so two overloaded function templates sharing one
  name (`f<T>(T)` vs. `f<T>(T,T)`) still collapsed their `DECL_INSTANTIATES_
  TEMPLATE` edges onto one shared node even after the earlier instantiation-
  id fix separated their own instantiation nodes. Verified against real
  clang output: each overload's own pattern `FunctionDecl` carries a
  distinct printed signature (`"T (T)"` vs. `"T (T, T)"`) — a new
  `TemplateInstantiation.template_signature` field (function-kind only)
  threads it through as a discriminator. Separately, `archive_graph.py`'s
  Mach-O leading-underscore join fallback was gated on `index_kind ==
  "bsd"` alone (the previous round's fix) — but a BSD symbol-index format
  doesn't prove Mach-O either: a real `llvm-ar --format=bsd` produces a
  valid `__.SYMDEF`-indexed archive around ordinary ELF objects too
  (empirically reproduced: `llvm-ar --format=bsd rcs lib.a a.o` over a real
  ELF `a.o`). `ArchiveContents` gains an `object_magic` field (the first
  regular member's own leading 4 bytes, read for free during the existing
  parse); the fallback now requires *both* a BSD index and genuine Mach-O
  member magic (`_MACHO_MAGICS`, an independent copy of `macho_metadata`'s
  own constant). And a review comment on `call_graph.py`'s redaction fix
  (two rounds back) — that blanket-expanding every reconstructed argv token
  including `-D`/`-U` macro values could corrupt a rare user-authored
  literal macro value that itself starts with `~` — was investigated and
  found to be an already-accepted, already-documented tradeoff: `source_
  extractors/castxml.py`'s own `extract()` has blanket-expanded its entire
  command line the identical way since an earlier Codex review (#335),
  accepting exactly this narrow risk as the cost of correctly replaying the
  far more common case (a genuinely redacted home-path macro that must
  expand or replay fails to find the right header). Documented in place
  rather than narrowed, for consistency with that established precedent.

  An eleventh review round found one more real, empirically-confirmed gap,
  but one that falls squarely inside a scope this module's own docstring
  already defers rather than warranting new code: two C++20 *constrained*
  function-template overloads sharing both a qualified name and an
  identical function type (`requires integral<T> void f(T)` vs. `requires
  floating_point<T> void f(T)` both print the pattern signature `"void
  (T)"`) still collide onto one `template_decl` node under the new
  signature discriminator, since the constraint text itself lives in a
  separate `ConceptSpecializationExpr` AST subsystem the discriminator
  never reads. Their own instantiations correctly stay distinct regardless
  (the Itanium requires-clause mangling differs per constraint), so only
  the coarser declaration-identity edge is affected. Documented as an
  explicit consequence of the module's pre-existing, deliberate C++20-
  concepts deferral rather than a one-line extension into that subsystem.

  A twelfth review round found three more real gaps in `archive_graph.py`,
  all empirically confirmed, plus one defensive hardening in
  `template_graph.py` with no observed failure. A BSD `#1/<len>` extended
  member name declaring a length longer than the member's own `size` — a
  shape that never occurs in a real archive, since the name is a strict
  prefix of the member's own data — was silently clamped via `min(len,
  size)`, fabricating a truncated name out of what is actually the
  member's real content instead of reporting the corruption; it now
  resolves to `None` (unresolvable), matching how every other malformed-
  name case in the same function already degrades. `_is_bsd_index_name`
  matched the `__.SYMDEF` magic by unbounded prefix, so an ordinary,
  legally-named member merely starting with those bytes
  (`__.SYMDEFECT.o`) was misrouted into the BSD ranlib-index decoder and
  raised `ArchiveFormatError` for an otherwise valid archive; it now
  matches only the bare magic name or that name followed by a literal
  space (the real " SORTED" variant, truncated or not), closing the false
  positive without reintroducing the truncated-name misses the original
  exact-string allowlist had. And the Mach-O underscore-stripping join
  fallback required *both* `index_kind == "bsd"` and genuine Mach-O
  `object_magic` — but empirically, a real Mach-O object cross-compiled
  with `clang --target=x86_64-apple-darwin` and archived via `llvm-ar
  --format=gnu` produces a genuinely GNU-encoded index around real Mach-O
  member content, so the conjunction silently dropped every Mach-O archive
  built with a non-BSD `ar`; the fallback now gates on `object_magic`
  alone, since it is already the strictly stronger, direct signal.
  Separately, `_function_template_pattern_signature` returned the first
  function-shaped child's printed type regardless of whether it carried a
  `mangledName`, relying implicitly on clang always emitting the unmangled
  pattern child first — verified true across five distinct real-clang AST
  shapes (a plain overload pair, multiple instantiations, an explicit
  specialization's detached stub, a forward-declared-then-defined
  template, an out-of-line class-member template), and structurally
  guaranteed by C++'s own requirement that a primary template be declared
  before anything instantiates or specializes it. No collision was
  observed or reproduced; the function now explicitly skips a
  `mangledName`-carrying child anyway, removing the implicit ordering
  dependency rather than merely relying on it.

  A thirteenth review round found one more real gap, fixed, plus one
  empirically-confirmed limitation, deferred and documented in place. An
  instantiated class template's *static data member* carries its mangled
  name on a direct `VarDecl` child (e.g. `template <typename T> struct Box
  { static T value; };` explicitly instantiated as `Box<int>` puts
  `_ZN3BoxIiE5valueE` on a `VarDecl`), not one of the member-function
  kinds `_member_symbols` scanned — so this genuinely emitted symbol was
  silently dropped from `emitted_symbols` entirely, verified against real
  clang AST output; `_member_symbols` now also scans `VarDecl` children (a
  non-static field is never mistaken for one, since it has no linkage and
  clang never gives it a `mangledName`, so the existing truthiness guard
  already excludes it). Separately, `archive_graph.py`'s `object_magic`
  field — read from `members[0].data_offset` — is empty, not wrong, for a
  *thin* archive's first regular member: a thin archive's regular members
  are bodiless (their real data lives in an external file this parser has
  no path to open), so that offset lands on whatever immediately follows
  the header rather than real object content, confirmed empirically with a
  real `llvm-ar --format=gnu rcT` thin archive over a real Mach-O object
  (`object_magic` comes back `b""`). The Mach-O underscore-stripping join
  fallback therefore silently misses a real Mach-O thin archive's
  underscore-prefixed symbols — a missed edge, never a wrong one, since an
  ASCII archive header can never accidentally match a 4-byte binary Mach-O
  magic sequence. A real fix needs resolving each thin member's own
  external file path and reading its magic from there — new, non-trivial
  plumbing this parser doesn't have today — so it is deferred the same way
  this module already defers its other documented thin-archive gaps,
  rather than attempted as a drive-by extension of this read.

  A fourteenth review round found one more real gap in `template_graph.py`,
  fixed and empirically confirmed both against real clang AST output and a
  real compiled object's own symbol table: clang's AST `mangledName` on a
  constructor/destructor always reports only the complete-object variant
  (`C1`/`D1`), never its siblings, but a real Mach-O/ELF binary separately
  exports `C2` alongside every `C1`, and `D0`/`D2` alongside every (virtual)
  `D1`, as genuinely distinct symbol-table entries — confirmed with both a
  trivial and a parameterized/virtual constructor/destructor via `nm` on a
  real compiled object. `_member_symbols` previously only recorded the
  AST-reported spelling, so an instantiation's own `C2`/`D0`/`D2` export
  (when the graph already carries a `binary_symbol` node for it) received
  no `INSTANTIATION_EMITS_SYMBOL` edge. A new `_ctor_dtor_symbol_variants`
  helper locates the `C1E`/`D1E` marker — always immediately followed by
  the nested-name-closing `E`, verified with and without constructor
  parameters — and substitutes each sibling code in its place; this is a
  textual substitution rather than a full Itanium parse, but it is safe by
  construction under this module's existing join-only-onto-an-existing-node
  discipline (ADR-057 D1): a garbled derived string from a pathological
  class/argument name embedding a literal `C1E`/`D1E` substring earlier in
  the mangling would simply fail to match any real `binary_symbol` node,
  never produce a wrong edge, since Itanium manglings are unique by
  construction. `C3` (the allocating constructor) is deliberately omitted —
  not observed emitted by clang/GCC in either test, unlike `C1`/`C2` which
  were always both present.

  A fifteenth review round found two more real, empirically-confirmed
  gaps. First, the fourteenth round's own `_ctor_dtor_symbol_variants`
  fix had a real correctness bug: it located the ctor/dtor marker with a
  naive `mangled.find("C1E"/"D1E")` substring search, and a class
  literally named `C1Evil<int>` mangles to `_ZN6C1EvilIiEC1Ev` — the
  first `"C1E"` match is inside the *class name*'s own length-prefixed
  encoding (`6C1Evil`), not the real ctor code that follows it, so the
  derivation produced `_ZN6C2EvilIiEC1Ev`, which happens to be the real
  constructor mangling of the genuinely different class `C2Evil<int>` if
  that class is also exported — a false positive, not merely a missed
  edge, since the module's usual join-only-onto-an-existing-node safety
  net doesn't catch a corrupted string that coincidentally *is* a real,
  different symbol. Fixed with a new, structurally correct
  `diff_cxx_rules.itanium_ctor_dtor_marker_span()` (reusing the same
  length-prefix-aware nested-name walk `itanium_scope_components()`
  already uses, so a length-prefixed identifier is always skipped as one
  whole unit rather than scanned character-by-character for a
  coincidental match) instead of the naive search; `_member_symbols` now
  normalizes the Mach-O prefix once up front, since the new locator's
  offset arithmetic requires a single `"_Z..."` prefix. Second, a nested
  specialization whose own argument is opaque to the JSON AST parser (a
  template-template argument) previously fell back to its bare,
  unparameterized primary-template name when resolving an *outer*
  argument naming it — `Outer<Use<A>>` and `Outer<Use<B>>`, where
  `Use<A>`/`Use<B>` both take an opaque template-template argument, both
  resolved their outer argument's `target_qname` to the identical
  ambiguous `"Use"`, so both emitted a `TEMPLATE_USES_TYPE` edge to the
  *same* graph node, falsely merging dependencies on two genuinely
  distinct specializations (confirmed empirically: the outer
  instantiation *nodes* themselves stayed correctly distinct, only the
  argument's own target was wrong). `_resolve_specialization_qname` now
  returns unresolved (`None`, no edge) in this case instead of the
  ambiguous bare name, matching this module's usual "never guess"
  discipline.

  A sixteenth review round found one more real, empirically-confirmed gap
  in `_walk_function_templates`'s own member-template scoping. Two
  *explicit* class-template specializations each independently declaring
  their own same-named, same-signature member template previously
  collided the same way the earlier "member function template nested in a
  class specialization" fix addressed for *implicit* instantiations, but
  for a structurally different reason this module's own established
  design deliberately didn't cover: `template<> struct H<int> {
  template<typename U> void f(U); };` and `template<> struct H<double> {
  template<typename U> void f(U); };` each write their *own* `f` from
  scratch — `H` has no generic member `f` on its primary template at all
  — so `H<int>::f` and `H<double>::f` are genuinely unrelated
  declarations that merely share a spelling and signature, unlike
  `Holder<int>::apply`/`Holder<double>::apply` (an implicit instantiation
  of one shared primary-pattern member, which the existing code correctly
  keeps merged onto one `template_decl` node — the design decision this
  round's fix had to preserve, not override). Both still resolved to the
  identical bare qname `"H::f"` and, sharing the same printed pattern
  signature, the same `template_decl` id — confirmed empirically against
  real clang AST output, including the same explicit-specialization
  detachment shape (an empty stub nested under the `ClassTemplateDecl`,
  full content detached to a top-level sibling sharing the stub's id)
  this module's docstring already documents for class-level resolution.
  `_walk_function_templates` now threads a `via_primary_template` flag
  set only when recursing directly from a `ClassTemplateDecl`'s own
  children (an implicit instantiation, or a stub with no member content
  to find anyway); reached any other way (a detached top-level sibling —
  always an explicit specialization, per that same detachment shape), the
  specialization's own template arguments now disambiguate its scope name
  the same way a nested type argument already does
  (`_specialization_scope_name`), degrading to the bare name when those
  arguments aren't resolvable, this module's usual conservative fallback.

  A seventeenth review round found the sixteenth round's own
  `via_primary_template` fix was itself wrong, confirmed empirically
  against real clang AST output: the flag was set from *structural
  position* alone (reached directly from a `ClassTemplateDecl`'s own
  children vs. reached from a detached top-level sibling), on the
  assumption that only an explicit specialization ever detaches to a
  top-level sibling. That assumption doesn't hold — an *explicit
  instantiation definition* (`template struct Holder<int>;`, forcing
  instantiation of the primary template's own shared member body, as
  opposed to `template<> struct H<int> { ... };`, which writes an
  independent body) detaches in the identical structural shape (empty stub
  nested under the `ClassTemplateDecl`, full content a top-level sibling
  sharing the stub's id) while its members remain the *same* declaration
  as any other instantiation's. The position-only heuristic disambiguated
  it anyway, wrongly splitting `Holder<int>::apply<int>` off from
  `Holder<double>::apply<double>` (an ordinary implicit instantiation of
  the same shared member) onto two different `template_decl` nodes instead
  of keeping them merged. Fixed by replacing the structural-position
  signal with a source-identity one: clang's AST `loc.offset` is identical
  across every AST node that represents the same source declaration (a
  copied/instantiated/shared member), and differs for one that was
  independently written. `_walk_class_templates` now also records, per
  class template, its primary pattern's own member function-templates'
  `loc.offset` (`_primary_pattern_member_locs`); `_walk_function_templates`
  compares each specialization's own member's `loc.offset` against that
  recorded value — a match means "same declaration as the primary
  pattern's", so the bare name is kept; a mismatch (or no recorded primary
  member at all, the `H`/explicit-specialization case) means independently
  authored content, so the specialization's own arguments disambiguate the
  scope name as before. This subsumes the sixteenth round's fix rather
  than special-casing on top of it: an explicit specialization's member
  never shares the primary pattern's `loc`, so it still disambiguates,
  while an explicit-instantiation-definition's member now correctly
  doesn't.

  An eighteenth round found and fixed two more real gaps, both in
  `diff_cxx_rules.py`, plus one confirmed-false CodeRabbit docstring
  finding. First, a genuine correctness bug shared by every caller of
  `_parse_source_name_component` (`itanium_scope_components`,
  `itanium_ctor_dtor_marker_span`, and therefore `owner_class_of` and
  `template_graph._ctor_dtor_symbol_variants` transitively): the helper
  checked for a directly-attached template-argument list (`I…E`) *before*
  checking for a GNU ABI tag (`B<source-name>`), but real GCC mangles a
  tagged, templated name in the opposite order -- name, then tag, then
  template-args -- confirmed empirically by compiling `template
  <typename T> struct __attribute__((abi_tag("tag"))) C { C(); ~C(); };`
  instantiated as `C<int>` and reading its real symbols via `nm`/
  `c++filt`: `_ZN1CB3tagIiEC1Ev`. Checking template-args first meant the
  component parser stopped at the tag, left `"IiE"` completely
  unconsumed, and every caller of it failed outright (returned `None`) on
  this real, non-synthetic case -- not merely mis-grouped it. Fixed by
  swapping the check order (tags before template-args), matching the real
  mangling grammar. Second, the docstring on `itanium_ctor_dtor_marker_span`
  claiming the caller must pre-normalize a Mach-O `__Z`-prefixed *mangled*
  before calling (else the returned span misaligns by one character) was
  itself wrong, confirmed empirically (CodeRabbit finding): the function's
  offset arithmetic (`offset = len(mangled) - len(s)`) is computed against
  the caller's own untouched `mangled` -- `_itanium_strip_prefix` only
  reassigns its own local variable internally, never the caller's -- so
  `__ZN1CC1Ev` and `_ZN1CC1Ev` both correctly locate their own `"C1"` text
  today, no pre-normalization required. Corrected the docstring (and the
  two matching, equally-wrong claims in `template_graph.py`'s
  `_ctor_dtor_symbol_variants` docstring and the `_member_symbols` comment
  that cited it) rather than the code, since the code was already correct.

  A nineteenth round found and fixed one more real gap in
  `archive_graph.py`, plus documented one confirmed-real, deliberately
  deferred gap in `template_graph.py`. `_bsd_symbol_index` fell back to
  `strings[str_off:]` (the string table's own remaining bytes) whenever a
  symbol name's declared offset had no NUL terminator within the table —
  silently accepting a truncated/corrupted run of bytes as a complete
  symbol name instead of treating the missing terminator as the
  corruption signal it is. Confirmed empirically against a real
  `llvm-ar --format=bsd` archive's raw bytes that every string-table
  entry, including the very last one, is genuinely NUL-terminated in a
  well-formed archive — so a missing terminator is unambiguously
  malformed, not a legitimate "runs to the end" case. Fixed to skip the
  entry instead, matching this parser's existing policy of degrading an
  individually-unresolvable entry rather than surfacing corrupted data
  (Codex review, fresh evidence). Separately, confirmed and documented (not
  fixed) that a polymorphic class template instantiation's vtable/typeinfo
  symbols (`_ZTV`/`_ZTI`/`_ZTS`) are never AST decl children with their own
  `mangledName` the way an ordinary member function is — they're
  synthesized by codegen from the class's own identity — so
  `_member_symbols`'s child-scanning walk structurally cannot discover
  them regardless of which decl kinds it recognizes; a real fix needs both
  a polymorphism/vague-linkage model this module doesn't have and a new
  position-returning structural Itanium class-name splitter neither this
  module nor `diff_cxx_rules.py` currently exposes — each its own scoped
  follow-up, documented in the module's own docstring rather than
  attempted as a drive-by extension here.

  A twentieth round found and fixed two more real gaps, both confirmed
  empirically against real compiled/hand-assembled fixtures. First,
  `_primary_pattern_member_locs` keyed its `member name -> loc.offset`
  dict by name alone -- when a class template's primary pattern itself
  *overloads* a member-template name (`template <typename U> U f(U);`
  alongside `template <typename U> U f(U, U);`, both named `f`), the
  name-only dict kept only the last-registered overload's offset, so
  every earlier overload's own instantiated member failed the
  shared-member check and was wrongly disambiguated. Confirmed against a
  real `clang -ast-dump=json` dump: two overloads of `f` on `Holder`'s
  primary pattern, each instantiated through both `Holder<int>` and
  `Holder<double>`, split the first overload into separate
  `Holder<int>::f`/`Holder<double>::f` nodes for what both dumps show is
  the identical syntactic overload. Fixed by keying on `(name, pattern
  signature)` instead, reusing the same `_function_template_pattern_
  signature` discriminator `template_decl_node_id` already uses to keep
  two such overloads' own declaration nodes distinct (Codex review, fresh
  evidence). Second, `augment_graph_with_archives`'s Mach-O leading-
  underscore join fallback gated on `ArchiveContents.object_magic` --
  the *first* regular member's own magic bytes -- applied uniformly to
  every symbol in the archive regardless of which member actually defines
  it. A mixed-format archive (an ELF object and a Mach-O object archived
  together, e.g. a cross-toolchain build) has members whose own format
  genuinely differs member-to-member: if the first member is Mach-O but a
  later one is ELF, the later member's own already-correctly-spelled
  `_foo` symbol gets the stripped-underscore treatment applied anyway and
  can false-join onto an unrelated exported `foo` (confirmed with a
  hand-assembled two-member archive reproducing exactly this). `ArMember`
  now carries its own `object_magic` (populated once at construction,
  first four bytes of that specific member's own data), and the fallback
  gates on the *referenced symbol's own defining member's* magic instead
  of the archive-wide value.

  A twenty-first round found and fixed two more real gaps, both confirmed
  empirically. First, `source_graph.py`'s `LinkUnit` input classification
  (`inp.endswith(_STATIC_LIBRARY_SUFFIXES)`) was case-sensitive, so a
  legitimately uppercase-suffixed Windows archive input (`FOO.LIB`, common
  on a case-insensitive filesystem) was silently misclassified as a plain
  `object_file` instead of `static_library` -- `adapters/make.py`'s own
  link-input filter already lowercases before the identical suffix check,
  but this classification site didn't, so the archive never became a
  `static_library` node and `archive_graph.py`'s pass (which only ever
  looks at that node kind) never saw it at all: no extractor row, no
  member nodes, no `OBJECT_DEFINES_SYMBOL` edges (Codex review, fresh
  evidence). Fixed by lowercasing before the compare, matching the Make
  adapter. Second, an `auto` non-type template parameter (`template <auto
  V> struct A;`) whose deduced type is a scoped enum produces a bare
  `{"value": N}` `TemplateArgument` node in clang's `-ast-dump=json` --
  confirmed against real clang 18 output -- with no `type` field
  distinguishing the argument's *deduced* type at all, so `A<E1::X>` and
  `A<E2::X>` (two different enum types sharing the same underlying value)
  both reduced to the identical label `"A<1>"` and collided onto one
  `template_instantiation` graph node, merging their genuinely distinct
  emitted-symbol edges. Fixed by detecting a class template's own `auto`
  NTTP parameter (`_class_template_has_auto_nttp`) and treating a bare
  `{"value": N}` argument on such a template the same way an opaque
  template-template argument is already treated: skip the whole
  instantiation rather than record a wrong, merged identity. Deliberately
  scoped to the `auto` case only -- an ordinary, non-`auto` NTTP
  (`template <int V>`) carries no such ambiguity, since its type is fixed
  and known from the parameter declaration itself, so a blanket "any bare
  value" check would have needlessly discarded the far more common,
  unambiguous case too.

  A twenty-second round found and fixed six more real gaps, all confirmed
  empirically against real compiled/hand-assembled fixtures, plus resolved
  a real merge conflict against `main`'s concurrent `override_graph.py`
  (ADR-041 P2 item 1) landing. First, the merge: `main` refactored
  `inline._build_inline_graph`'s four Clang-backed fold calls into one
  `inline_graph_fold.fold_semantic_graphs()` wrapper and added
  `fold_override_graph`, occupying the same call sites this branch's
  `fold_template_graph`/`fold_archive_graph` (G29 Phase 5) independently
  added. Resolved by keeping both new fold functions, extending
  `fold_semantic_graphs` to run all five Clang-backed passes (call, type,
  override, template, include), and keeping `fold_archive_graph` as the
  unconditional, non-clang pass alongside it.

  Then, six Codex-confirmed real gaps. (1) `archive_graph.py`: a `/`/`//`/
  `__.SYMDEF` special-member header could declare an arbitrarily large
  size, read via a single unbounded `reader.read(data_offset, size)` call
  before any content validation — over a *sparse* file whose reported
  logical size can cheaply match an inflated header claim (`truncate
  --size=10G evil.a` costs near-zero real disk), an ordinary dump attempt
  could try to materialize gigabytes as one Python `bytes` object. Fixed
  with a new `_MAX_SPECIAL_MEMBER_BYTES` (1 GiB) ceiling checked before
  each of the three full-body reads, and a smaller `_MAX_BSD_NAME_BYTES`
  (64 KiB) ceiling on a BSD `#1/<len>` extended-name length (reachable on
  *any* member, not just an index, since a member's own `size` is equally
  attacker/corruption-controlled). (2) `archive_graph.py`: a `__.SYMDEF`
  string-table offset pointing into the *middle* of another entry's name
  (e.g. offset 1 into `"xfoo\0"`) decoded as the plausible-looking but
  spoofed `"foo"` — real ranlib output never does this (an entry's offset
  always names a string's start), so a malformed/hostile one is now
  rejected unless `str_off == 0` or immediately follows a NUL. (3)
  `diff_cxx_rules.py`: the ABI-tag ordering fix flattened a tag into the
  identity as bare `name + "B" + tag`, so `C[abi_tag("tag")]<int>` and an
  unrelated, plainly-named `CBtag<int>` both flattened to the identical
  `"CBtagIiE"` — confirmed against two real compiled symbols
  (`_ZN1CB3tagIiE1fEv` vs. `_ZN5CBtagIiE1fEv`). Now delimited as
  `"[abi:tag]"`, which no real C++ identifier can contain. (4)
  `template_graph.py`: the sticky-file mechanism (`id_to_file.setdefault`)
  let a loc-less header stub permanently lock a specialization's `file` to
  the *header*, even when the detached full definition carries its own
  explicit `.cpp` location — confirmed via a real clang dump of a header-
  declared template explicitly specialized in a `.cpp`. Now an explicit
  own `loc.file` always overwrites a prior (inherited) mapping for the
  same id. (5) `template_graph.py`: a namespace-scoped explicit
  instantiation written with a *qualified* name outside its namespace
  (`template struct api::Holder<int>;`) detaches its full content as a
  direct `TranslationUnitDecl` child, not nested inside the namespace at
  all — confirmed against real clang 18 output — so the member-scoping
  fix's own structural-scope computation produced the bare `"Holder"`
  instead of `"api::Holder"`, wrongly splitting a shared member. Fixed by
  preferring the id-keyed `id_to_template_qname` registration (set
  correctly from the stub, always reached nested inside its own
  `ClassTemplateDecl`) over this walk's own structural scope, for both the
  member-locs lookup and the scope prefix used to build child qnames. (6)
  `template_graph.py`: an out-of-line member-template *definition*
  (`template <class T> void C::f(T) {}`) detaches as a separate, top-level
  `FunctionTemplateDecl` whose own children resolve (via the existing
  id-keyed join) to the same instantiated symbols the correctly-scoped
  in-class declaration already captures — confirmed against real clang
  output — so without a fix the graph carried the instantiation twice,
  once correctly under `"C::f"` and once wrongly under a bare `"f"` that
  could coincidentally merge with an unrelated global template. Fixed by
  resolving the node's `parentDeclContextId` (emitted by clang precisely
  on this node shape, confirmed absent on an ordinary in-class
  declaration) through `id_to_qname` and preferring it unconditionally
  when present.

  Two more findings were investigated and deliberately documented rather
  than fixed: extending the `auto`-NTTP class-instantiation guard to
  function templates (CodeRabbit nitpick) is unnecessary in practice — a
  function instantiation's own graph-node identity already keys on its
  mangled name, not the ambiguous label, so the only residual effect is a
  cosmetic label collision between two instantiations that (being
  ambiguous same-valued `auto` NTTPs of one template) correctly share one
  `template_decl` node anyway; documented in
  `_class_template_has_auto_nttp`'s own docstring rather than adding an
  unverified parallel check.

  A twenty-third round found and fixed one more real gap in
  `template_graph.py`, confirmed empirically. `_function_template_pattern_
  signature`'s discriminator relied solely on clang's printed function
  type (e.g. `"void ()"`), but two function templates can share both a
  qualified name and an identical *function* signature while differing
  only in their own *template* parameter list -- `template <class T> void
  f()` and `template <class T, class U> void f()` both print the identical
  `"void ()"`, since the function parameter list is genuinely empty for
  both and clang's printer never reflects the template parameter list in
  that spelling at all (Codex review, fresh evidence). The pattern-type
  discriminator alone therefore still collapsed both onto one shared
  `template_decl` node despite their own instantiations correctly staying
  separate (distinct mangled names), falsely making every
  `DECL_INSTANTIATES_TEMPLATE` edge from either one point at a declaration
  the other didn't actually come from. Fixed by additionally counting each
  `FunctionTemplateDecl`'s own leading template-parameter-declaration
  kinds (`TemplateTypeParmDecl`/`NonTypeTemplateParmDecl`/
  `TemplateTemplateParmDecl`) and folding that into the discriminator
  alongside the existing pattern-type spelling. `tests/test_template_graph.py`
  crossed the 2000-line AI-readiness hard cap while adding this
  regression test; split identity-discriminator tests into a new sibling
  `tests/test_template_graph_identity.py` rather than further growing the
  parent file, per this repo's own file-size guidance.

  A twenty-fourth round found and fixed two more real gaps, both confirmed
  empirically. First, `template_graph.py`'s newly added template-parameter
  discriminator counted only each parameter's structural *kind*, not a
  non-type template parameter's own declared *type* -- `template <E1 N>
  void f()` and `template <E2 N> void f()` (two distinct enum types) both
  counted as one bare `"NonTypeTemplateParmDecl"`, colliding the identical
  way the parameter-list fix itself was meant to prevent (Codex review,
  second round, fresh evidence, confirmed via a real compiled/dumped pair:
  `f<E1::A>`/`f<E2::A>` reduced to the identical
  `"NonTypeTemplateParmDecl|void ()"` signature). Fixed by folding the
  NTTP's own `type.qualType` (always present, reliably differs) into its
  parameter descriptor. `TemplateTypeParmDecl`/`TemplateTemplateParmDecl`
  deliberately stay kind-only -- no confirmed real-world collision source
  found for either, so no unverified extension was added preemptively.
  Second, `archive_graph.py`'s `archive_member_node_id` disambiguated two
  same-named members by appending `#<discriminator>` to the raw member
  name -- but `#` is a legal `ar` member-name character, so a uniquely-
  named member literally called `"foo#1"` (discriminator 0) and an
  ordinary member named `"foo"` at occurrence index 1 (discriminator 1)
  both produced the identical id `"archive_member://<archive>::foo#1"`,
  merging two genuinely distinct members onto one graph node (Codex
  review, third round, fresh evidence). Fixed by length-prefixing the
  member name in the id (the same technique this module already trusts
  for Itanium source-name parsing), applied uniformly regardless of
  whether the discriminator is zero -- a plain, unprefixed "common case"
  format would still have been reachable from the other direction, by an
  unusual literal member name that happens to look like a length-prefixed
  entry.

  A twenty-fifth round found and fixed one more real gap in
  `archive_graph.py`, confirmed by inspection of the caller/callee
  contract. `augment_graph_with_archives`'s own docstring promises "never
  raises" for every per-archive failure (ADR-028 D3), and its `try/except`
  around `read_archive(path)` caught `ArchiveFormatError`/`OSError` --
  but not `MemoryError`, which the `_MAX_SPECIAL_MEMBER_BYTES` cap added
  in the twenty-second round still permits as a single 1 GiB allocation,
  large enough to exhaust memory on a constrained host (CodeRabbit
  review). Left uncaught, that would abort the whole L5 graph fold over
  one archive instead of just skipping it. Fixed by adding an
  `except MemoryError:` clause alongside the existing two, degrading to a
  diagnostic the same way any other unreadable archive already does.
