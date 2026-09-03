### Fixed

- **ADR-063 Phase 6's `--dump-manifest` multi-TU occurrence-detail gap is
  now closed.** `tu_merge.merge_fragments` collapses same-identity
  declarations across translation units into one representative entry
  before a naive normalization pass would ever see them, which silently
  discarded a genuine cross-TU declaration split (e.g. a public header's
  forward declaration plus a private header's full definition of the same
  type) down to a single `SemanticIR` occurrence. The fix normalizes each
  contributing TU's own raw, pre-merge fragment independently
  (`extract/manifest_semantic_ir.py`'s new `manifest_semantic_ir`) and
  disambiguates each resulting `OccurrenceId` by the declaration's own
  `source_location` (`extract/semantic_normalizer.normalize_header_ast`'s
  new `disambiguate_by_source_location` parameter) — reusing a field
  `RecordType`/`EnumType`/`Function`/`Variable` already carry from both
  header-AST backends, rather than inventing a new signal. A genuine split
  reports two different `file:line` locations and survives as two
  occurrences; the far more common case — many TUs `#include` the
  identical, unmodified header — reports the identical `file:line` from
  every including TU and correctly collapses to one. Verified end-to-end
  against real clang output for both cases. Several follow-up review
  findings closed the same fix's remaining gaps: disambiguation now
  applies only to an `EntityId` whose declarations span more than one
  distinct *cross-fragment* location-set — a single fragment's own
  multiple locations (e.g. a declaration followed by its own definition
  in the same TU) never trigger it by themselves, so a single-TU
  manifest's occurrence IDs stay canonical (identical to a non-manifest
  normalization's); a TU-local (`static`/anonymous-namespace) function or
  variable is disambiguated by combining its own `tu_name` with its
  location, classified per fragment rather than globally by `EntityId`
  (a plain-C function's own identity construction does not encode
  static-vs-external linkage, so a global classification could wrongly
  TU-scope a genuinely external occurrence sharing a collided identity
  with an unrelated internal one) and mirroring `tu_merge._function_key`'s
  existing internal-linkage scoping.

  A further review round found three more gaps in the same fix, all now
  closed: (1) an externally-linked entity observed with the *same*
  multi-location location-set in every contributing TU (e.g. a shared
  header's prototype immediately followed by its own definition) was
  still being collapsed to one occurrence, discarding a real ODR-distinct
  declaration — `manifest_semantic_ir` now only blanks an entity's
  disambiguator when its one agreed-upon location set has exactly one
  member, keeping a multi-location entity's own per-location
  disambiguators intact so redundant cross-TU observations still fold
  together while distinct declarations do not; (2) a plain-C (or
  `extern "C"`) file-scope `static` variable's mangled spelling equals its
  bare name, carrying no Itanium linkage marker at all, so it had no
  signal distinguishing it from a same-named `extern` variable across
  translation units — `Variable` now carries an `is_static` field
  (mirroring `Function.is_static`, populated by both header-AST backends;
  schema v43) that `tu_merge._variable_key`/`manifest_semantic_ir`'s
  locally-linked classification fall back to when the mangled name
  carries no marker; (3) `dumper_scoping`'s dependency-header scoping
  filtered `SemanticIR` occurrences only by whole-`EntityId` membership,
  so a kept identity's own excluded system-header occurrence (reached via
  an unrelated TU) leaked into a default-scoped snapshot even though its
  flat counterpart was correctly dropped — `dumper_scoping` now also
  checks each occurrence's own disambiguator-derived header origin
  (`occurrence_dependency_scope.py`'s new
  `occurrence_survives_dependency_scope`), split into its own leaf module
  to keep `dumper_scoping.py` under its `architecture/debt.yaml`
  no-growth baseline.

  A second review round found three further gaps, all now closed: (1)
  `_multi_location_non_ambiguous_entity_ids`'s location-set-size check
  could not tell a single fragment's own prototype-then-definition pair
  apart from the genuine cross-fragment case above -- both reduce to one
  agreed-upon location set of size 2 -- so a `--dump-manifest` run over
  exactly ONE translation unit kept two occurrences where the equivalent
  non-manifest single-TU path collapses to one, forking persisted
  occurrence IDs solely because a manifest was used; the fix adds a raw
  per-entity fragment-count gate (computed in the same one-pass helper,
  now `_per_entity_location_sets`'s second return value) so only a
  genuinely multi-fragment agreement (>= 2 TUs) keeps its per-location
  split, while a lone fragment's multi-location entity now falls through
  to the ordinary blank-disambiguator collapse; (2) `Variable.is_static`
  was appended as a plain positional field -- `Function.is_static`'s own
  precedent -- but the model's own "append new fields at the end,
  keyword-only where a default is needed" contract calls for keyword-only
  on a new defaulted field, so it is now `field(default=False,
  kw_only=True)` (every real call site already passes it by keyword, so
  this is not a behavior change); (3) `occurrence_dependency_scope.py`
  moved from the flat package root to `extract/`, its owning package
  (ADR-061's task-routing table), and its per-occurrence dependency check
  dropped every occurrence of a *kept* identity whenever all of that
  identity's own occurrences happened to live under a dependency header --
  exactly the case `scope_snapshot_excluding_dependencies` already
  retains deliberately (a dependency type directly named by a kept public
  declaration), leaving the retained flat entity with zero surviving
  `SemanticIR` evidence; the new `scoped_occurrences_excluding_dependencies`
  excludes a dependency occurrence only when a *different*, non-dependency
  occurrence of the same identity also survives to stand in for it, via a
  first pass that records which kept identities have at least one
  non-dependency occurrence at all.

  Added `tests/test_dumper_scoping_occurrence_dependency.py`, direct unit
  tests for `scoped_occurrences_excluding_dependencies` covering all four
  of its branches (non-scoped kind, excluded identity, kept identity with
  only dependency occurrences, kept identity with a mix) -- closing a real
  patch-coverage gap the review round's own fix left, split into its own
  file since `test_dumper_scoping.py` has no `architecture/debt.yaml`
  adoption-debt entry and is at the AI-readiness `new-test-size` cap.

  A third review round found `_variable_key`'s new `Variable.is_static`
  fallback (item 2 in the second round above) had the identical class of
  gap `_function_key`'s own pre-existing, documented "Second known,
  accepted limitation" already named for template methods: `mangled ==
  name` is not proof of plain-C linkage -- clang's header AST also has no
  `mangledName` for a STATIC member (function or data) of an
  *uninstantiated* class template, since mangling a member requires a
  concrete instantiation that does not exist yet (confirmed empirically:
  `template<class T> struct A { static int x; };`/`static void run(T);`
  both parse with no `mangledName` for their member). That previously made
  `_variable_key` wrongly TU-qualify an ordinarily externally-linked
  static member as internal linkage, and would have done the identical
  thing for a static member FUNCTION once `_function_key`'s own
  `is_static` fallback saw the same shape. New
  `extract/headers/scope_segments.entity_is_record_member` checks whether
  an `EntityId`'s own scope ends in a `Record` segment -- populated from
  the real AST scope walk regardless of whether mangling succeeded, unlike
  `is_static` -- and both `_function_key`/`_variable_key` (and their
  `manifest_semantic_ir` mirrors) now withhold trust in the `is_static`
  fallback for a record member. This closes the newly-reported static-
  data-member gap and, as a side effect, the static-member-function
  variant of `_function_key`'s own older, previously-accepted-as-
  unfixable limitation from PR #635 round 12 (the sibling NON-static
  method cross-template bare-name collision that limitation also named
  remains open -- `is_static` is already `False` there, so this fix does
  not reach it; still needs the return-type-independent identity that
  limitation's own text describes). Verified against real clang output
  for both the static-data-member and static-member-function shapes.
