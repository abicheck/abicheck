### Fixed

- **Report no longer presents public vtable/RTTI breaks as internal churn.**
  The Markdown surface-breakdown note claimed RTTI and internal-namespace
  findings were "typically ... not public-API breaks" and labelled the
  remainder the "genuine public-surface" count. Both inputs are name-shape
  observations, not contract membership: a vtable change on a public,
  user-derivable class is a genuine public ABI break (e.g. adding virtual
  functions to a public base shifts the dispatch slots of a derived class's
  own virtuals). The note now reports the counts, says what they do and do
  not establish, and points at `--contract` for real contract classification.
- **A symbol's parameter types no longer decide its namespace.**
  `name_classification.symbol_origin` scanned the whole mangled string for an
  internal-namespace component, so any public function taking an argument from
  a `detail`/`impl`/`internal` namespace was classified as internal churn —
  `svs::consume(const svs::detail::Token&)` mangles to
  `_ZN3svs7consumeERKNS_6detail5TokenE`, whose `6detail` belongs to the
  parameter. Classification now resolves the *owning* scope through the
  structural Itanium parser (`model.mangled_name`), with a nested-name-region
  fallback for shapes it does not model (constructors, operators). An entity
  merely *named* like an internal namespace is no longer treated as being in
  one.

### Changed

- **`v0` is no longer assumed to be an experimental namespace.** A version
  segment is not a stability promise, and inline-versioned public APIs
  (`namespace v0 { … }`) are a widespread way to spell a library's *current
  public* API — for those projects the default reported every removal from the
  whole public API as an experimental-API removal. Projects that do mean `v0`
  that way now state it via the new `experimental_namespaces:` policy key,
  which restores the previous behaviour exactly.

### Added

- **`experimental_namespaces:` policy-file key**, the counterpart of
  `internal_namespaces:` for the `experimental::` graduation convention.
  Threaded through `PipelineContext.experimental_namespaces` to
  `DetectNamespacePatterns`.
