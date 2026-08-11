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
  (Codex review, second round). The merge now also matches real clang's
  own diagnostic semantics for *conflicting* markers across
  redeclarations — verified empirically that `-Wdeprecated-declarations`
  reports whichever redeclaration's marker came LAST in source order, not
  the first — so a later marker always overwrites an earlier one instead
  of the merge's own first-wins default (Codex review, third round).
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
- **`check_fact_compatibility()` no longer forgives a fact-set-inconsistent
  mixed-producer pack the same way it forgives a genuinely pre-C.8 pair.**
  `rollup_fact_set()` collapses BOTH "every TU is silent" (a real pre-C.8
  absence) and "TUs disagree on fact_set" (a mixed-producer pack) to the
  identical `{}` — but only the former is the symmetric-absence case this
  gate's forward-compat forgiveness was designed for. A new
  `fact_set_rollup_is_inconsistent()` computes the distinguishing bit
  alongside the rollup; `link_source_abi()` stamps it onto
  `surface.coverage["fact_set_inconsistent"]`, and `check_fact_compatibility()`
  gained `old_inconsistent`/`new_inconsistent` keyword parameters so an
  inconsistent side's `{}` suppresses `structured_content_comparable`/
  `opaque_hashes_comparable`/`source_edges_comparable` the same way an
  asymmetric absence already does, instead of silently passing structured
  content changes through as trusted (Codex review, PR #719). Two more
  fixes in the same round: the surface-level `fact_set_inconsistent` read
  now requires the actual JSON boolean `True` rather than `bool(...)`
  truthiness, which misread the string `"false"` (from a hand-edited/
  forward-produced `source_abi.json`) as truthy; and the castxml
  `compiler_version` probe now reads the combined stdout+stderr
  transcript case-insensitively (mirroring `dumper_castxml_probe.py`'s
  own normalization), since a wrapper/build combination that writes its
  `--version` banner to stderr or capitalizes `CastXML` previously
  probed as an empty identity.
- **A public opaque-handle forward decl compatibly redeclared with a
  different class key in a private/transitive header no longer inherits
  that private redecl's `source_location`.** The `class Handle; struct
  Handle;` kind-canonicalization fix above picked one `_Decl` to supply
  BOTH the canonical kind and the emitted location together — but when
  the public and private declarations disagree on class key, the
  lexicographically-smaller one isn't necessarily the public one, so a
  genuinely public handle could silently read as `PRIVATE_HEADER`
  downstream and drop out of public-contract analysis. `parse_types()`
  now decides kind (`min(kind)` over every non-definition redeclaration)
  and location (the most-public declaration, ties keeping whichever was
  already kept) independently, mirroring the same location-preservation
  principle `tu_merge.py`'s own cross-TU merge already applies (Codex
  review, PR #719). A follow-up round on the same fix (Codex review)
  fixed a real regression it introduced: the canonicalized opaque kind
  was applied unconditionally to whichever record won, including a
  COMPLETE definition sharing an identity with a differently-keyed
  opaque redecl — silently hiding a real kind change on the definition
  itself (`class Foo;` unchanged, `class Foo {...};` → `struct Foo
  {...};`). `override_kind` is now only honored for a surviving opaque
  (non-definition) record; a definition's own kind always wins.
- **`check_fact_compatibility()`'s inconsistent-rollup fix (above) now
  also gates `structured_facts_comparable` (existence/removal
  detection), not just the recipe-dependent categories** (Codex review)
  — an inconsistent side's `{}` cannot establish absence either (a
  family missing only because a mixed-in producer variant never
  collects it looks identical to a real removal), so
  `generated_header_changed`/`public_typedef_removed`/
  `public_macro_removed`/`inline_function_removed`/
  `uninstantiated_template_removed` removal detection is now suppressed
  too when either side's fact_set is inconsistent. The pre-existing
  asymmetric-absence exemption (one side simply never stamped a
  fact_set) is untouched — that forward-compat contract predates this
  PR and isn't part of this gap.
- **The castxml `compiler_version` probe (`_castxml_tool_version()`) is
  now bound by the active scan `--budget` deadline**, not a bare
  `subprocess.run(timeout=5)` (Codex review) — it now goes through the
  same `run_bounded_for_extraction()` path every other subprocess this
  extractor runs already uses, so a stalled probe under a short
  remaining budget fails fast instead of silently eating up to 5s of it.
  A follow-up round (Codex review) closed a second gap in the same
  function: its `lru_cache` was keyed only on the binary *path*, so a
  long-lived process kept serving the old identity forever if the
  executable at that path was replaced in place (an in-place upgrade,
  or a swapped `PATH` entry) without the process restarting. A new
  `_executable_stat_key()` (mirroring `dumper_toolchain._executable_sha256`'s
  own stat-based cache key) is now folded into the `lru_cache` key by
  both real call sites, so a same-path executable swap gets a fresh
  probe instead of the stale memoized one. A third round (Codex review)
  fixed a real correctness gap the second round's own error handling
  introduced the surface for: `run_bounded_for_extraction()` folds a
  genuine scan `--budget` exhaustion into the same `SourceExtractionError`
  as an ordinary probe failure, erasing which one happened — this
  function's broad `except` clause was degrading BOTH to `""`, so a
  cache lookup keyed on that identity could proceed past an expired
  deadline with no further check in between. The probe now re-checks
  the deadline directly on any failure; a still-exhausted budget
  re-raises the real `deadline.DeadlineExceeded` (deliberately NOT
  wrapped into `SourceExtractionError`), matching the un-swallowed
  exception `_replay_cache_lookup()`'s own bare `deadline.check()`
  already lets through for this same phase.
- **Two more gaps closed in `check_fact_compatibility()`'s inconsistent-
  rollup handling (Codex review):** a matching `hash_recipe_id` on both
  sides no longer overrides an `old_inconsistent`/`new_inconsistent`
  flag — `same_recipe` is now also gated on `not inconsistent`, since a
  hand-authored or forward-produced `source_abi.json` can legally set
  `fact_set_inconsistent: true` while its `coverage.fact_set` block still
  carries non-empty, matching representative content on both sides (a
  shape `rollup_fact_set()` itself never produces, since it always
  collapses an inconsistent rollup to `{}`, but `check_fact_compatibility`
  takes `old_fact_set`/`new_fact_set` and the inconsistency flags as
  independent parameters); without the guard, a matching recipe id there
  would silently re-enable opaque-hash and source-edge comparisons for a
  pack whose own TUs disagreed on `fact_set`. Separately,
  `source_diff._diff_fact_coverage()`'s `has_signal` check now also
  counts a bare `fact_set_inconsistent: true` flag (both sides' rolled-up
  `fact_set`/`fact_family_states` can be empty even when the flag alone
  is the signal — again `rollup_fact_set()`'s own mixed-producer-pack
  shape) — previously that degraded shape produced `has_signal=False`,
  so `_diff_fact_coverage()` returned `[]` with no
  `SOURCE_FACT_COVERAGE_INCOMPLETE` explanation even while `compat`'s own
  inconsistency-driven suppression silently dropped structured/opaque/
  source-edge findings underneath it. A follow-up round (Codex review)
  closed the residual gap the same non-empty-matching-`fact_set` shape
  left open in `_diff_fact_coverage()` itself: even after `has_signal`
  correctly read `True`, `compat.issues` stayed empty because
  `check_fact_set_compatibility()` only ever compares the two rolled-up
  `fact_set` dicts it's given — it has no visibility into
  `old_inconsistent`/`new_inconsistent` at all, so two identical fact_sets
  produce no issue regardless of the inconsistency flag. `check_fact_
  compatibility()` now appends an explicit `fact_set_inconsistent`
  `FactSetIssue` whenever `old_inconsistent`/`new_inconsistent` is set, so
  `_diff_fact_coverage()`'s "only report when there's something to say"
  early return no longer fires silently for this shape.
- **`dumper_clang.py`'s opaque class/struct kind canonicalization no
  longer depends on which particular subset of legally-compatible
  redeclarations happens to be present in a given snapshot** (Codex
  review) — `min(kind)` over the *observed* redecl spellings was only
  stable when that observed set itself was stable, but C++ permits
  forward-declaring a type with one class-key (`struct H;`) and later
  adding a legal, semantics-preserving redeclaration with the other
  (`class H;`); adding or removing that second, compatible redecl between
  two otherwise-unchanged snapshots changed which spelling `min()` saw and
  could flip the emitted `RecordType.kind`, producing a false
  `SOURCE_LEVEL_KIND_CHANGED` for a header change that didn't alter the
  type's real kind at all. `class`/`struct` are now collapsed to one fixed
  spelling (`"struct"`) before folding into `opaque_kinds`, so the
  canonicalized value depends only on the identity's real kind category;
  `union` — a genuinely different, non-interchangeable type category
  (mirroring `tu_merge._record_kinds_compatible`'s existing rule) — is
  left untouched. A follow-up round (Codex review) found the unconditional
  fold itself was a *new* regression: an identity with only ONE observed
  opaque redeclaration (no ambiguity at all — e.g. just `class H;`, no
  competing `struct H;`) was still being unconditionally relabeled to the
  fixed `"struct"` spelling, so comparing it against a LATER snapshot
  where the same identity gains a same-key COMPLETE definition
  (`class H {};`, whose kind is always its own real, unmodified
  `_record_kind`) produced a false `SOURCE_LEVEL_KIND_CHANGED` purely
  because the opaque side had been fabricated to a different spelling than
  its real declared class-key. Fixed by tracking the full set of raw kinds
  observed per identity (`opaque_kind_sets`) rather than an eagerly-folded
  running value: an identity with exactly one distinct raw kind now keeps
  it UNCHANGED (matches any later same-key definition exactly); the fold
  to one fixed spelling only applies once genuine ambiguity is
  observed — two or more distinct raw kinds for the same identity in one
  snapshot. `_reduce_opaque_kind_set()` (new, in `dumper_clang_qualifiers.py`
  — `dumper_clang.py` sits at its 2000-line hard cap) documents the three
  cases explicitly.

- **`docs/reference/header-backend-capabilities.md`'s `EnumType.
  underlying_type` row now marks direct-clang `⚠️ Partial` instead of
  `✅ Yes`** (Codex review) — `_enum_underlying()` correctly reads the real
  compiler-selected type for a FIXED enum (`enum E : short`) via
  `fixedUnderlyingType`, but hard-codes the dataclass default `"int"` for
  an UNFIXED enum, since clang's AST JSON exposes no compiler-selected-
  underlying-type fact for that case at all — the true value can differ
  (e.g. `unsigned int`, chosen from the member value range, which is
  exactly the case castxml's own extraction now resolves correctly).
  Rendering direct-clang as fully capable collapsed this real precision
  gap; `scripts/backend_capabilities.py`'s row updated and
  `docs/reference/header-backend-capabilities.md` regenerated
  (`python scripts/gen_backend_capability_matrix.py`).

- **`CastxmlSourceExtractor.cache_identity_extra()` no longer collapses
  every unparseable/failed `--version` probe to the same uninformative
  `""`.** When the probed version transcript is empty (the binary ran
  but its banner didn't parse, or it failed outright), the D8 TU cache
  key's "extra" identity now falls back to the executable's own stat
  signature (`dev:ino:mtime_ns:size`) instead — so swapping to a
  DIFFERENT broken/unparseable castxml install at the same path still
  changes the cache key, rather than two distinct broken installs both
  reading as the identical `""` and letting a warm `SourceAbiCache`
  replay facts produced by the previous one (Codex review). A follow-up
  round (Codex review) closed the same gap for the *persisted* side:
  `fact_set.compiler_version` (`_stamp_fact_set_and_coverage`) was still
  being stamped with the version probe's bare `""` on the same failure,
  so two baselines collected against two different broken installs at
  the same path would compare as recipe-agreeing
  (`check_fact_compatibility` sees identical `compiler_version`), and an
  unchanged enum whose two bundled Clangs actually disagree on its
  underlying type could still slip through as `GENERATED_HEADER_CHANGED`
  with no `compiler_version_mismatch` warning to explain it.
  `compiler_version` is used only for opaque string-equality comparison
  and diagnostic display, never parsed as a real version string, so a
  `stat:...` fallback there is exactly as valid a value as the real
  probed identity — both call sites now share one
  `_castxml_identity_with_stat_fallback()` helper instead of the earlier
  version stamping only the cache key.
- **The castxml L4 source-ABI extractor's persisted `fact_set.
  compiler_version` now also folds in a stat signature of the resolved
  EMULATED compiler** (`cc_bin`, via `--castxml-cc-<id>`) — castxml shells
  out to it purely to discover built-in defines/include paths, so a header
  conditional on `__GNUC__`/`_MSC_VER` can extract differently once that
  compiler is upgraded at the same path, even though castxml itself and
  its own `--version` probe are unchanged. The equivalent D8 TU cache-key
  half (`cache_identity_extra()`) is a known gap — see `AGENTS.md` — since
  the resolved compiler varies per compile unit while that hook is a
  zero-arg, once-per-extractor-instance call.

### Known gaps (see `AGENTS.md`)

- Recorded, not fixed in this PR: `diff_filtering.py`'s opaque-type
  suppression (`_find_opaque_types()` and friends) keys by bare
  `RecordType.name`, not a qualified identity — pre-existing on both
  header backends, and newly reachable on the direct-clang backend by
  this PR's own opaque-handle-type fix (which previously silently
  dropped every forward-decl-only type on that backend instead of
  emitting a stub). Two distinct records sharing a bare name in
  different namespaces can collide in the same opaque set, silently
  suppressing a real structural-change finding on an unrelated,
  complete, public type. A correct fix needs qualified identity
  threaded through several call sites in `diff_filtering.py` with no
  existing test coverage for the collision case — a systematic,
  cross-cutting rework out of scope for this PR's time budget. See
  `AGENTS.md`'s Known gaps section for the full investigation.
- Recorded, not fixed: `dumper_clang.py`'s `parse_types()` identity key
  conflates a C/C++ tag-namespace name with an unrelated ordinary-namespace
  typedef name that happens to share the same spelling (e.g. `struct Foo;`
  plus a separate `typedef struct { int x; } Foo;`), silently dropping the
  unrelated opaque tag from the snapshot. Reproduced directly against the
  parser; not fixed here — see `AGENTS.md`'s Known gaps section.
- Recorded, not fixed: the castxml D8 TU cache key (`cache_identity_
  extra()`) does not fold in the resolved emulated compiler's identity
  (unlike the now-fixed persisted `fact_set.compiler_version` above),
  since that hook is a zero-arg, once-per-extractor-instance call while
  the resolved compiler varies per compile unit — needs a wider,
  per-instance-hook-signature change to `source_replay.py`'s shared
  cache-key infra. See `AGENTS.md`'s Known gaps section.
