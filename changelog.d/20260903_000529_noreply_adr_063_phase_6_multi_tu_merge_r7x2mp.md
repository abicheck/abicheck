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
