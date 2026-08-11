<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`EnumType.underlying_type` is now populated on the castxml (and hybrid)
  header backend** — previously only the direct-clang backend read the
  compiler-resolved underlying integer type, so a castxml-produced enum
  silently kept the dataclass default `"int"` regardless of its real
  underlying type (a fixed `enum E : short`, or the implementation-chosen
  type for an unfixed enum). This made `tu_merge.py`'s cross-TU ODR
  agreement check for enums trivially pass on castxml input even when two
  translation units genuinely disagreed on an enum's underlying type. The
  whole-snapshot disk cache (`snapshot_cache._SNAPSHOT_CACHE_VERSION`) and
  the L4 source-ABI per-TU cache (`CASTXML_EXTRACTOR_VERSION`) are both
  bumped so an upgrading user's warm cache is re-extracted instead of
  replaying the old `"int"` default indefinitely.
- **The castxml L4 source-ABI extractor now stamps a `fact_set`/`coverage`
  identity (ADR-038 C.8)**, and structured-fact *content* comparisons
  (`generated_header_changed`, `public_typedef_target_changed`,
  `public_macro_value_changed`) are now gated on producer/producer_version
  agreement (`FactCompatibility.structured_content_comparable`), the same way
  opaque body/template hashes already were. Closes the residual gap the enum
  `underlying_type` fix above left open: a persisted L4 baseline written by an
  older castxml-producer version would otherwise diff an unchanged generated
  enum as changed purely from the extractor upgrade, since its `type_hash`
  now includes a previously-defaulted field. The castxml extractor previously
  never participated in this protocol at all, so this also newly reports its
  `macros`/`templates`/`inline_bodies`/`source_edges` coverage as
  `unsupported` (families it has never collected) rather than leaving them
  silently unreported. An asymmetric `fact_set` absence (only one side
  stamped one at all — the shape every already-persisted pre-castxml-0.2
  baseline hits) is now also treated as content-non-comparable, not silently
  forgiven the way a genuinely symmetric pre-C.8 pair is. The castxml
  extractor also now stamps a real `compiler_version` (its own resolved
  `castxml --version`, cached), and the `SOURCE_FACT_COVERAGE_INCOMPLETE`
  finding's description names which structured-content findings it
  suppresses instead of the previous (now-inaccurate) claim that
  content-change findings are unaffected by a fact-set mismatch.
  `FactCompatibility.structured_content_comparable` deliberately does NOT
  honor a matching `hash_recipe_id` override the way `opaque_hashes_comparable`
  does — that id is a declared statement about the opaque body/template
  hash canonicalization recipe specifically, and proves nothing about
  whether structured fact extraction (what a `type_hash` is built from)
  also stayed identical between two producer versions. The castxml
  `compiler_version` probe also now captures the bundled Clang's own
  identity line, not just the castxml release number — two castxml
  installs can share a release but bundle different Clang builds, and it's
  the bundled Clang that resolves a compiler-selected fact like an unfixed
  enum's underlying type.
- **The hybrid header-backend merge now backfills
  `RecordType.is_template_pattern`/`has_anonymous_aggregate_fields` from
  clang** onto a castxml-matched record — previously silently dropped for
  any declaration both backends saw. Both are plain booleans rather than an
  Optional tri-state, so the merge OR-merges them instead of using the
  existing null-check backfill pattern. Verified against a real compiled
  header that `is_template_pattern`'s backfill is empirically inert for the
  current producer pair (a clang template pattern never shares an identity
  with a castxml-matched concrete type) while `has_anonymous_aggregate_fields`
  is genuinely live — a real all-anonymous-union record's flag was
  previously silently dropped in hybrid mode even though castxml's own
  layout already corroborated it.
- **An opaque handle type (`struct Handle;` with no definition anywhere in
  the header set) is no longer silently absent from a direct-clang header
  snapshot.** `parse_types()` previously skipped every non-definition
  record entirely; it now emits an opaque `RecordType` stub
  (`is_opaque=True`, empty fields/bases/vtable) for a forward-declaration-
  only identity, matching the castxml backend's existing behavior. Also
  closes an adjacent gap: a type both forward-declared and defined in the
  same translation unit — confirmed against real clang 18 output that both
  land as separate AST nodes sharing one identity — now deterministically
  collapses to the definition regardless of declaration order, instead of
  relying on incidental per-node iteration order.
  `snapshot_cache._SNAPSHOT_CACHE_VERSION` bumped (an opaque handle type
  used to be missing from the snapshot entirely, not just wrong-valued).
  Two follow-up fixes on the same identity-grouping logic (Codex review):
  a `class Handle; struct Handle;` opaque redeclaration pair now
  canonicalizes `RecordType.kind` via `min(kind)` (mirroring
  `tu_merge._record_kinds_compatible`) instead of keeping whichever
  spelling appeared first, so reordering two equivalent, compiler-accepted
  forward decls can no longer flip the emitted kind and produce a false
  `SOURCE_LEVEL_KIND_CHANGED`; and a `[[deprecated]]` attribute attached
  to *any* redeclaration of an identity is now merged onto the emitted
  `RecordType`, instead of silently vanishing when an earlier,
  unattributed forward decl happens to win the kind tie-break. The
  merge preserves a *bare* `[[deprecated]]` (no message) too — its
  intentionally meaningful `""` marker is distinguished from "no
  attribute at all" rather than being treated as falsy and discarded
  (Codex review, second round).
- **The castxml L4 source-ABI extractor now folds its probed castxml/
  bundled-Clang identity into the D8 per-TU cache key**
  (`CastxmlSourceExtractor.cache_identity_extra()`, Codex review) —
  without this, a warm `SourceAbiCache` replayed a stale `SourceAbiTu`
  (stale enum facts and `compiler_version` included) after the castxml
  binary at the cached path was upgraded or swapped, since
  `CASTXML_EXTRACTOR_VERSION` alone doesn't change on a toolchain
  upgrade. Mirrors `ClangSourceExtractor.cache_identity_extra()`'s
  existing `--gcc-path` identity fold. The bundled-compiler banner regex
  now also recognizes an `LLVM version ...`-spelled banner, not just
  `clang version ...` (matching `dumper_castxml_probe`'s existing
  handling of the same spelling variance), so two installs differing
  only in that banner spelling no longer read as the same
  `compiler_version`/cache identity (Codex review, second round).
