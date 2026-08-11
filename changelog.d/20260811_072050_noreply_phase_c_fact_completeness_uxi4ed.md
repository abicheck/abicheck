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
  silently unreported.
