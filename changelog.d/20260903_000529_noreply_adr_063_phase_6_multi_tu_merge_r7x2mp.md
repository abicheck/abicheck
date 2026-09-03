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
