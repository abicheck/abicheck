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
  against real clang output for both cases. Two follow-up review findings
  closed the same fix's remaining gaps: disambiguation now applies only to
  an `EntityId` that genuinely has more than one distinct `source_location`
  across the contributing fragments, so a single-TU manifest's occurrence
  IDs stay canonical (identical to a non-manifest normalization's) instead
  of always gaining a nonempty disambiguator; and a TU-local (`static`/
  anonymous-namespace) function or variable — a genuinely distinct
  declaration in every TU that defines one, even from an identical shared
  header at an identical location — is now disambiguated by its own
  `tu_name` unconditionally, mirroring `tu_merge._function_key`'s existing
  internal-linkage scoping.
