### Added

- **CLI cleanup phase two, PR H**: `scan --artifact-set` gains audit-mode
  provider-ownership semantics. Two new findings, both scoped to what a
  single declared set (no old side) can prove: `bundle_duplicate_provider`
  (unconditional, `COMPATIBLE_WITH_RISK`) fires when the same default-bound
  symbol name is exported by 2+ members of the set -- an unversioned
  reference to it resolves to whichever library the dynamic linker's
  load-order/symbol-interposition rules pick first, not a declared
  contract; linker-synthesized per-object boilerplate (`_edata`/`_end`/...)
  is excluded so it doesn't fire on every real multi-library set.
  `bundle_manifest_entry_unsatisfied` (opt-in, `COMPATIBLE_WITH_RISK`) is
  new: `scan --artifact-set --manifest PATH` reuses `compare --manifest`'s
  own `InstantiationManifest` YAML/JSON schema unchanged, checking each
  entry's expected-provider ownership (`optional_provider: false` +
  `library:`) against this one declared set. "A symbol moved between
  sibling libraries" stays a `compare`-only finding
  (`bundle_provider_changed`) -- it needs an old side to confirm a move,
  which an audit doesn't have.

### Fixed

- `compare --manifest`'s own two-sided ownership check
  (`bundle_manifest_instantiation_removed`) now shares its matching logic
  with the new audit-mode check via one `_manifest_ownership_findings`
  helper, rather than a second, independently-maintained copy.
