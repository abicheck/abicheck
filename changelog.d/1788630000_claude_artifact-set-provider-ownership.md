### Added

- **CLI cleanup phase two, PR H**: `scan --artifact-set` gains audit-mode
  provider-ownership semantics. Two new findings, both scoped to what a
  single declared set (no old side) can prove: `bundle_duplicate_provider`
  (unconditional, `COMPATIBLE_WITH_RISK`) fires when the same default-bound
  symbol name is exported by 2+ members of the set as a **strong**
  (`STB_GLOBAL`) definition -- an unversioned reference to it resolves to
  whichever library the dynamic linker's load-order/symbol-interposition
  rules pick first, not a declared contract; linker-synthesized per-object
  boilerplate (`_edata`/`_end`/...) and ordinary C++ vague linkage (a weak
  `STB_WEAK`/`STB_GNU_UNIQUE` inline-function/template-instantiation
  COMDAT copy every DSO using it emits identically, already deduplicated
  by the dynamic linker at load time) are excluded so it doesn't fire on
  every real multi-library C++ set.
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
- **Security**: a manifest entry naming a bare symbol was previously
  considered satisfied by a definition that exists only as a non-default
  versioned export (e.g. `foo@V1`, never `foo@@V1`) -- a shape an
  unversioned consumer cannot actually link against. Both
  `compare --manifest` and the new `scan --artifact-set --manifest` now
  require a *default* (`is_default`) definition before treating a
  symbol/pattern/template promise as satisfied, closing a false-negative
  path an attacker-crafted ELF could otherwise exploit to make a broken
  ownership contract read as `COMPATIBLE` with zero findings.
- **Security**: `load_manifest` and `run_scan_set` (the typed-API entry
  point a caller can reach with a directly-constructed
  `ScanRequest(bundle_manifest=...)`, bypassing `load_manifest` entirely)
  both now reject a `provides:` entry that sets `optional_provider: false`
  without also naming a `library` -- every wrong-provider check downstream
  is itself gated on the entry having a declared library, so this shape
  previously loaded silently and behaved exactly like the
  always-permissive `optional_provider: true` default, letting any
  matching library satisfy what was declared a required, named-provider
  promise. The check lives in one shared `_validate_manifest_entries`
  helper, called from both entry points -- but deliberately *not* from
  `_parse_manifest_entry` itself, which is also reused by
  `manifest_entry_from_dict` to round-trip an already-persisted
  `BundleFacts` manifest; rejecting there would have broken backward
  compatibility with facts written before this check existed. The same
  helper also validates a `ManifestEntry`'s shape directly (exactly one of
  `symbol`/`pattern`/`template` set, and a non-empty `instantiations` for
  a template entry) -- invariants `load_manifest`'s raw-dict parsing
  already guaranteed, but that a directly-constructed
  `ScanRequest(bundle_manifest=...)` could bypass entirely: a bare
  `ManifestEntry()` previously expanded to zero match targets and reported
  no unsatisfied entry at all, and an entry with two selectors set at once
  silently dropped one promise.
- **Security**: expected-provider matching (`optional_provider: false` +
  `library:`) now also recognizes a manifest entry naming the literal
  on-disk filename of a provider (e.g. a versioned real file behind a dev
  symlink) rather than only its discovery key or `DT_SONAME` -- reusing
  `ResolutionGraph.soname_to_name`, the same symlink/hard-link alias
  reverse map `_compute_resolution_graph` already builds for `DT_NEEDED`
  resolution, instead of a second, narrower alias lookup that previously
  missed this shape and could misreport a correctly-provided symbol as a
  wrong-provider violation.
- `_match_entry`'s per-target manifest-matching loop now calls
  `deadline.check()`, so a large pattern/template manifest can no longer
  overrun a small `--budget` well past the point `run_scan_set` would
  otherwise report the overflow.
